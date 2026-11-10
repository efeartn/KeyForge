"""Thread-safe macro execution engine with Panic Kill-Switch and EmergencyInputSanitizer."""

from __future__ import annotations

import contextlib
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from keyforge.engine.backend import InputBackend
from keyforge.models.action import ActionType, ClickType, MacroAction, MouseButton, MoveMode
from keyforge.models.keymap import KeyInfo, parse_key_combo, resolve_single_key
from keyforge.models.profile import MacroProfile, RepeatMode
from keyforge.security.validator import MIN_SAFE_CYCLE_DELAY_MS, validate_profile_complexity


class ExecutorState(StrEnum):
    """Lifecycle states of the MacroExecutor."""

    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    PANIC_ABORTED = "panic_aborted"
    ERROR = "error"


@dataclass(slots=True)
class ExecutionTelemetry:
    """Snapshot of live execution metrics."""

    state: ExecutorState = ExecutorState.IDLE
    cycles_completed: int = 0
    actions_executed: int = 0
    current_action_id: str | None = None
    elapsed_seconds: float = 0.0
    last_error: str | None = None


class MacroExecutor:
    """Executes MacroProfiles with sub-millisecond cancelability and guaranteed input release."""

    def __init__(
        self,
        backend: InputBackend,
        time_scale: float = 1.0,
        rng_seed: int | None = None,
        on_telemetry: Callable[[ExecutionTelemetry], None] | None = None,
    ) -> None:
        self._backend = backend
        self._time_scale = max(0.0, float(time_scale))
        self._rng = random.Random(rng_seed)
        self._on_telemetry = on_telemetry

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._panic_flag = False
        self._worker_thread: threading.Thread | None = None

        self._held_keys: dict[int, KeyInfo] = {}
        self._held_buttons: set[MouseButton] = set()
        self._telemetry = ExecutionTelemetry()
        self._start_perf: float = 0.0

    @property
    def backend(self) -> InputBackend:
        return self._backend

    @backend.setter
    def backend(self, new_backend: InputBackend) -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("Cannot swap input backend while macro is running.")
            self._backend = new_backend

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._telemetry.state in (ExecutorState.RUNNING, ExecutorState.STOPPING) or (
                self._worker_thread is not None and self._worker_thread.is_alive()
            )

    @property
    def telemetry(self) -> ExecutionTelemetry:
        with self._lock:
            elapsed = (
                time.perf_counter() - self._start_perf
                if self._start_perf > 0 and self.is_running
                else self._telemetry.elapsed_seconds
            )
            return ExecutionTelemetry(
                state=self._telemetry.state,
                cycles_completed=self._telemetry.cycles_completed,
                actions_executed=self._telemetry.actions_executed,
                current_action_id=self._telemetry.current_action_id,
                elapsed_seconds=elapsed,
                last_error=self._telemetry.last_error,
            )

    def start(self, profile: MacroProfile) -> bool:
        """Start executing an isolated snapshot of the given MacroProfile on a background daemon thread."""
        with self._lock:
            if self.is_running:
                return False
            validate_profile_complexity(profile)
            profile_snapshot = MacroProfile.from_payload_dict(profile.to_payload_dict())
            self._stop_event.clear()
            self._panic_flag = False
            self._start_perf = time.perf_counter()
            self._telemetry = ExecutionTelemetry(state=ExecutorState.RUNNING)

            thread = threading.Thread(
                target=self._run_worker,
                args=(profile_snapshot,),
                name="KeyForge-Executor",
                daemon=True,
            )
            self._worker_thread = thread
            thread.start()
        self._emit_telemetry()
        return True

    def run_blocking(self, profile: MacroProfile) -> ExecutionTelemetry:
        """Execute a MacroProfile synchronously on the calling thread (for CLI and tests)."""
        with self._lock:
            if self.is_running:
                raise RuntimeError("Executor is already running.")
            validate_profile_complexity(profile)
            self._stop_event.clear()
            self._panic_flag = False
            self._start_perf = time.perf_counter()
            self._telemetry = ExecutionTelemetry(state=ExecutorState.RUNNING)
            self._emit_telemetry()

        self._run_worker(profile)
        return self.telemetry

    def stop(self, timeout: float = 2.0) -> None:
        """Request graceful stop of the active macro and wait for worker completion."""
        thread_to_join: threading.Thread | None = None
        with self._lock:
            if not self.is_running:
                return
            self._telemetry.state = ExecutorState.STOPPING
            self._stop_event.set()
            thread_to_join = self._worker_thread
            self._emit_telemetry()

        if thread_to_join is not None and thread_to_join is not threading.current_thread():
            thread_to_join.join(timeout=timeout)

    def panic_abort(self, timeout: float = 0.2) -> None:
        """Hardware Panic Kill-Switch: immediately abort execution and release all held inputs."""
        thread_to_join: threading.Thread | None = None
        with self._lock:
            self._panic_flag = True
            self._stop_event.set()
            self._release_all_held_inputs()
            if self._telemetry.state != ExecutorState.IDLE:
                self._telemetry.state = ExecutorState.PANIC_ABORTED
            thread_to_join = self._worker_thread
        self._emit_telemetry()
        if thread_to_join is not None and thread_to_join is not threading.current_thread():
            thread_to_join.join(timeout=timeout)

    def _run_worker(self, profile: MacroProfile) -> None:
        enabled_actions = [a for a in profile.actions if a.enabled]
        try:
            if not enabled_actions:
                return

            cycle_index = 0
            while not self._stop_event.is_set():
                # Check repeat mode termination condition before starting cycle
                if profile.repeat_mode == RepeatMode.COUNT and cycle_index >= profile.repeat_count:
                    break
                if profile.repeat_mode == RepeatMode.DURATION:
                    elapsed = time.perf_counter() - self._start_perf
                    if elapsed >= profile.repeat_duration_s:
                        break

                cycle_start = time.perf_counter()
                self._execute_action_list(enabled_actions, profile)

                if self._stop_event.is_set():
                    break

                with self._lock:
                    cycle_index += 1
                    self._telemetry.cycles_completed = cycle_index
                    self._telemetry.elapsed_seconds = time.perf_counter() - self._start_perf
                    self._emit_telemetry()

                # Check if we have more cycles to run
                has_more_cycles = True
                if profile.repeat_mode == RepeatMode.COUNT and cycle_index >= profile.repeat_count:
                    has_more_cycles = False
                elif profile.repeat_mode == RepeatMode.DURATION:
                    if (time.perf_counter() - self._start_perf) >= profile.repeat_duration_s:
                        has_more_cycles = False

                if not has_more_cycles:
                    break

                # Inter-cycle delay + OS input queue flood protection floor
                inter_delay_ms = profile.inter_cycle_delay_ms
                cycle_elapsed_ms = (time.perf_counter() - cycle_start) * 1000.0
                if inter_delay_ms <= 0 and cycle_elapsed_ms < MIN_SAFE_CYCLE_DELAY_MS:
                    inter_delay_ms = MIN_SAFE_CYCLE_DELAY_MS

                if inter_delay_ms > 0:
                    self._interruptible_wait(inter_delay_ms, jitter_ms=0)

        except Exception as exc:
            with self._lock:
                self._telemetry.state = ExecutorState.ERROR
                self._telemetry.last_error = str(exc)
        finally:
            with self._lock:
                self._release_all_held_inputs()
                self._telemetry.current_action_id = None
                if self._start_perf > 0:
                    self._telemetry.elapsed_seconds = time.perf_counter() - self._start_perf
                if self._panic_flag:
                    self._telemetry.state = ExecutorState.PANIC_ABORTED
                elif self._telemetry.state != ExecutorState.ERROR:
                    self._telemetry.state = ExecutorState.IDLE
                self._worker_thread = None
                self._emit_telemetry()

    def _execute_action_list(self, actions: list[MacroAction], profile: MacroProfile) -> None:
        for action in actions:
            if self._stop_event.is_set():
                return
            if not action.enabled:
                continue
            if profile.repeat_mode == RepeatMode.DURATION:
                if (time.perf_counter() - self._start_perf) >= profile.repeat_duration_s:
                    self._stop_event.set()
                    return

            with self._lock:
                self._telemetry.current_action_id = action.id
                self._emit_telemetry()

            self._execute_single_action(action, profile)

    def _execute_single_action(self, action: MacroAction, profile: MacroProfile) -> None:
        atype = action.action_type

        if atype == ActionType.KEY_PRESS:
            keys = parse_key_combo(action.key)
            for k in keys:
                if self._stop_event.is_set():
                    return
                self._safe_key_down(k)
            if action.hold_ms > 0:
                self._interruptible_wait(action.hold_ms, jitter_ms=0)
            for k in reversed(keys):
                self._safe_key_up(k)
            self._increment_actions()

        elif atype == ActionType.KEY_DOWN:
            k = resolve_single_key(action.key)
            self._safe_key_down(k)
            self._increment_actions()

        elif atype == ActionType.KEY_UP:
            k = resolve_single_key(action.key)
            self._safe_key_up(k)
            self._increment_actions()

        elif atype == ActionType.MOUSE_CLICK:
            if action.x is not None and action.y is not None:
                self._backend.move_cursor(action.x, action.y, relative=False)

            btn = action.button
            ctype = action.click_type
            if ctype == ClickType.SINGLE:
                self._safe_mouse_down(btn)
                if action.hold_ms > 0:
                    self._interruptible_wait(action.hold_ms, jitter_ms=0)
                self._safe_mouse_up(btn)
            elif ctype == ClickType.DOUBLE:
                for idx in range(2):
                    if self._stop_event.is_set():
                        break
                    self._safe_mouse_down(btn)
                    if action.hold_ms > 0:
                        self._interruptible_wait(action.hold_ms, jitter_ms=0)
                    self._safe_mouse_up(btn)
                    if idx == 0 and not self._stop_event.is_set():
                        self._interruptible_wait(40, jitter_ms=0)
            elif ctype == ClickType.DOWN:
                self._safe_mouse_down(btn)
            elif ctype == ClickType.UP:
                self._safe_mouse_up(btn)
            self._increment_actions()

        elif atype == ActionType.MOUSE_MOVE:
            if action.x is not None and action.y is not None:
                is_rel = action.move_mode == MoveMode.RELATIVE
                self._backend.move_cursor(action.x, action.y, relative=is_rel)
                self._increment_actions()

        elif atype == ActionType.MOUSE_SCROLL:
            self._backend.scroll(dx=action.scroll_dx, dy=action.scroll_dy)
            self._increment_actions()

        elif atype == ActionType.WAIT:
            self._interruptible_wait(action.wait_ms, jitter_ms=action.jitter_ms)
            self._increment_actions()

        elif atype == ActionType.REPEAT_BLOCK:
            enabled_subs = [s for s in action.sub_actions if s.enabled]
            for _ in range(action.repeat_count):
                if self._stop_event.is_set():
                    break
                self._execute_action_list(enabled_subs, profile)

    def _safe_key_down(self, key: KeyInfo) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._held_keys[key.vk_code] = key
            self._backend.key_down(key)

    def _safe_key_up(self, key: KeyInfo) -> None:
        with self._lock:
            self._held_keys.pop(key.vk_code, None)
            self._backend.key_up(key)

    def _safe_mouse_down(self, button: MouseButton) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._held_buttons.add(button)
            self._backend.mouse_down(button)

    def _safe_mouse_up(self, button: MouseButton) -> None:
        with self._lock:
            self._held_buttons.discard(button)
            self._backend.mouse_up(button)

    def _release_all_held_inputs(self) -> None:
        """EmergencyInputSanitizer: unconditionally release any depressed keys or mouse buttons."""
        with self._lock:
            held_keys_list = list(self._held_keys.values())
            self._held_keys.clear()
            for key in reversed(held_keys_list):
                with contextlib.suppress(Exception):
                    self._backend.key_up(key)

            held_buttons_list = list(self._held_buttons)
            self._held_buttons.clear()
            for btn in held_buttons_list:
                with contextlib.suppress(Exception):
                    self._backend.mouse_up(btn)

    def _interruptible_wait(self, wait_ms: int, jitter_ms: int = 0) -> None:
        """Wait for wait_ms (±jitter_ms) while remaining immediately responsive to stop_event."""
        effective_ms = float(wait_ms)
        if jitter_ms > 0:
            delta = self._rng.uniform(-float(jitter_ms), float(jitter_ms))
            effective_ms = max(1.0, effective_ms + delta)

        duration_s = (effective_ms / 1000.0) * self._time_scale
        if duration_s <= 0.0:
            return

        self._stop_event.wait(timeout=duration_s)

    def _increment_actions(self) -> None:
        with self._lock:
            self._telemetry.actions_executed += 1
            if self._start_perf > 0:
                self._telemetry.elapsed_seconds = time.perf_counter() - self._start_perf
            self._emit_telemetry()

    def _emit_telemetry(self) -> None:
        if self._on_telemetry is not None:
            snapshot = self.telemetry
            with contextlib.suppress(Exception):
                self._on_telemetry(snapshot)
