"""Live keyboard & mouse macro recorder with key-tap coalescing and synthetic input filtering."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from keyforge.engine.backend import KEYFORGE_EXTRA_INFO
from keyforge.engine.platform_input import (
    VK_LBUTTON,
    VK_MBUTTON,
    VK_RBUTTON,
    VK_XBUTTON1,
    VK_XBUTTON2,
    platform_get_cursor_pos,
    platform_get_pressed_states,
    platform_supports_key_polling,
)
from keyforge.models.action import (
    ActionType,
    ClickType,
    MacroAction,
    MouseButton,
    make_key_down,
    make_key_press,
    make_key_up,
    make_mouse_click,
    make_mouse_scroll,
    make_wait,
)
from keyforge.models.keymap import VK_TO_KEY, parse_key_combo
from keyforge.security.validator import MAX_TOTAL_STEPS

# Mouse Virtual-Key mapping for state polling
_MOUSE_VK_MAP: dict[int, MouseButton] = {
    VK_LBUTTON: MouseButton.LEFT,
    VK_RBUTTON: MouseButton.RIGHT,
    VK_MBUTTON: MouseButton.MIDDLE,
    VK_XBUTTON1: MouseButton.X1,
    VK_XBUTTON2: MouseButton.X2,
}


@dataclass(slots=True)
class RecorderConfig:
    """Configuration options for live macro recording."""

    record_delays: bool = True
    min_delay_ms: int = 20
    merge_key_taps: bool = True
    capture_click_coordinates: bool = True
    max_recorded_steps: int = 1000


class MacroRecorder:
    """Records user keyboard and mouse actions into editable MacroAction sequences."""

    def __init__(
        self,
        config: RecorderConfig | None = None,
        on_step_recorded: Callable[[MacroAction], None] | None = None,
    ) -> None:
        self.config = config or RecorderConfig()
        self._on_step_recorded = on_step_recorded

        self._lock = threading.RLock()
        self._is_recording = False
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None

        self._actions: list[MacroAction] = []
        self._last_event_ts: float | None = None
        self._pending_key_down_ts: dict[str, float] = {}
        self._pending_mouse_down_ts: dict[MouseButton, tuple[float, int | None, int | None]] = {}
        self._ignored_single_vks: set[int] = set()
        self._ignored_combos: list[tuple[int, ...]] = []
        self._depressed_norm_vks: set[int] = set()

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._is_recording

    @property
    def recorded_actions(self) -> list[MacroAction]:
        with self._lock:
            return list(self._actions)

    def set_ignored_hotkeys(self, *hotkey_combos: str) -> None:
        """Configure hotkey combinations that should be ignored by the recorder."""
        with self._lock:
            self._ignored_single_vks.clear()
            self._ignored_combos.clear()
            for combo in hotkey_combos:
                try:
                    keys = parse_key_combo(combo)
                except ValueError:
                    continue
                if len(keys) == 1:
                    self._ignored_single_vks.add(keys[0].vk_code)
                elif len(keys) > 1:
                    self._ignored_combos.append(tuple(k.vk_code for k in keys))

    def _matches_ignored_combo(self, trigger_norm_vk: int) -> bool:
        mod_family = {
            0x11: (0x11, 0xA3),
            0x10: (0x10, 0xA1),
            0x12: (0x12, 0xA5),
            0x5B: (0x5B, 0x5C),
        }
        for combo_vks in self._ignored_combos:
            if combo_vks[-1] != trigger_norm_vk:
                continue
            if all(
                any(v in self._depressed_norm_vks for v in mod_family.get(mod_vk, (mod_vk,)))
                for mod_vk in combo_vks[:-1]
            ):
                return True
        return False

    def start(self, enable_os_poller: bool = True) -> None:
        """Begin a new recording session."""
        with self._lock:
            if self._is_recording:
                return
            self._actions.clear()
            self._last_event_ts = time.perf_counter()
            self._pending_key_down_ts.clear()
            self._pending_mouse_down_ts.clear()
            self._depressed_norm_vks.clear()
            self._stop_event.clear()
            self._is_recording = True

            if enable_os_poller and platform_supports_key_polling():
                self._poll_thread = threading.Thread(
                    target=self._os_poll_loop,
                    name="KeyForge-Recorder",
                    daemon=True,
                )
                self._poll_thread.start()

    def stop(self) -> list[MacroAction]:
        """Stop recording and return the finalized list of MacroAction steps."""
        thread_to_join: threading.Thread | None = None
        with self._lock:
            if not self._is_recording:
                return list(self._actions)
            self._is_recording = False
            self._stop_event.set()
            thread_to_join = self._poll_thread
            self._poll_thread = None

        if thread_to_join is not None and thread_to_join is not threading.current_thread():
            thread_to_join.join(timeout=1.0)

        with self._lock:
            return list(self._actions)

    def feed_key_event(
        self,
        vk_code: int,
        is_down: bool,
        timestamp_s: float | None = None,
        extra_info: int = 0,
    ) -> None:
        """Process a raw keyboard transition event."""
        if extra_info == KEYFORGE_EXTRA_INFO:
            return

        now = timestamp_s if timestamp_s is not None else time.perf_counter()
        with self._lock:
            if not self._is_recording:
                return
            key_info = VK_TO_KEY.get(vk_code)
            if key_info is None:
                return
            norm_vk = key_info.vk_code
            if is_down:
                self._depressed_norm_vks.add(norm_vk)
            else:
                self._depressed_norm_vks.discard(norm_vk)
            if vk_code in self._ignored_single_vks or norm_vk in self._ignored_single_vks:
                return
            if is_down and self._matches_ignored_combo(norm_vk):
                return
            cap = min(self.config.max_recorded_steps, MAX_TOTAL_STEPS)
            key_name = key_info.canonical_name

            if is_down:
                if len(self._actions) >= cap:
                    return
                if key_name in self._pending_key_down_ts:
                    # Ignore OS auto-repeat while key is already depressed
                    return
                self._maybe_append_wait(now, cap)
                self._pending_key_down_ts[key_name] = now
                self._append_action(make_key_down(key_name), cap)
                self._last_event_ts = now
            else:
                down_ts = self._pending_key_down_ts.pop(key_name, None)
                if down_ts is None:
                    # Ignore orphan key-up for a key that was depressed before recording started
                    return
                hold_ms = max(10, int(round((now - down_ts) * 1000)))
                if (
                    self.config.merge_key_taps
                    and self._actions
                    and self._actions[-1].action_type == ActionType.KEY_DOWN
                    and self._actions[-1].key == key_name
                    and hold_ms <= 2000
                ):
                    # Coalesce KEY_DOWN + KEY_UP in place even if len(self._actions) == cap
                    self._actions[-1] = make_key_press(key=key_name, hold_ms=hold_ms)
                    self._notify_step(self._actions[-1])
                else:
                    if len(self._actions) >= cap:
                        return
                    self._maybe_append_wait(now, cap)
                    self._append_action(make_key_up(key_name), cap)
                self._last_event_ts = now

    def feed_mouse_button_event(
        self,
        button: MouseButton,
        is_down: bool,
        x: int | None = None,
        y: int | None = None,
        timestamp_s: float | None = None,
        extra_info: int = 0,
    ) -> None:
        """Process a raw mouse button press/release event."""
        if extra_info == KEYFORGE_EXTRA_INFO:
            return

        now = timestamp_s if timestamp_s is not None else time.perf_counter()
        with self._lock:
            if not self._is_recording:
                return
            cap = min(self.config.max_recorded_steps, MAX_TOTAL_STEPS)

            coord_x = x if self.config.capture_click_coordinates else None
            coord_y = y if self.config.capture_click_coordinates else None

            if is_down:
                if len(self._actions) >= cap:
                    return
                self._maybe_append_wait(now, cap)
                self._pending_mouse_down_ts[button] = (now, coord_x, coord_y)
                self._append_action(
                    make_mouse_click(
                        button=button,
                        click_type=ClickType.DOWN,
                        x=coord_x,
                        y=coord_y,
                    ),
                    cap,
                )
                self._last_event_ts = now
            else:
                down_info = self._pending_mouse_down_ts.pop(button, None)
                if down_info is None:
                    return
                hold_ms = max(10, int(round((now - down_info[0]) * 1000)))
                if (
                    self.config.merge_key_taps
                    and self._actions
                    and self._actions[-1].action_type == ActionType.MOUSE_CLICK
                    and self._actions[-1].button == button
                    and self._actions[-1].click_type == ClickType.DOWN
                    and hold_ms <= 2000
                ):
                    down_x = down_info[1]
                    down_y = down_info[2]
                    self._actions[-1] = make_mouse_click(
                        button=button,
                        click_type=ClickType.SINGLE,
                        x=down_x,
                        y=down_y,
                        hold_ms=hold_ms,
                    )
                    self._notify_step(self._actions[-1])
                else:
                    if len(self._actions) >= cap:
                        return
                    self._maybe_append_wait(now, cap)
                    self._append_action(
                        make_mouse_click(
                            button=button,
                            click_type=ClickType.UP,
                            x=coord_x,
                            y=coord_y,
                        ),
                        cap,
                    )
                self._last_event_ts = now

    def feed_mouse_scroll_event(
        self,
        scroll_dy: int = 0,
        scroll_dx: int = 0,
        timestamp_s: float | None = None,
        extra_info: int = 0,
    ) -> None:
        """Process a mouse wheel scroll event."""
        if extra_info == KEYFORGE_EXTRA_INFO or (scroll_dx == 0 and scroll_dy == 0):
            return

        now = timestamp_s if timestamp_s is not None else time.perf_counter()
        with self._lock:
            if not self._is_recording:
                return
            cap = min(self.config.max_recorded_steps, MAX_TOTAL_STEPS)
            if len(self._actions) >= cap:
                return
            self._maybe_append_wait(now, cap)
            self._append_action(make_mouse_scroll(scroll_dy=scroll_dy, scroll_dx=scroll_dx), cap)
            self._last_event_ts = now

    def _maybe_append_wait(self, now: float, cap: int) -> None:
        if not self.config.record_delays or self._last_event_ts is None:
            return
        if not self._actions:
            # Avoid adding leading delay before the very first recorded action
            return
        # Reserve at least 1 slot for the actual action that follows this WAIT
        if len(self._actions) + 1 >= cap:
            return
        delta_ms = int(round((now - self._last_event_ts) * 1000))
        if delta_ms >= self.config.min_delay_ms:
            self._append_action(make_wait(wait_ms=min(delta_ms, 60_000)), cap)

    def _append_action(self, action: MacroAction, cap: int) -> None:
        if len(self._actions) < cap:
            self._actions.append(action)
            self._notify_step(action)

    def _notify_step(self, action: MacroAction) -> None:
        if self._on_step_recorded is not None:
            with contextlib.suppress(Exception):
                self._on_step_recorded(action)

    def _os_poll_loop(self) -> None:
        """200 Hz non-blocking key/mouse state transition poller."""
        monitored_key_vks = [vk for vk in VK_TO_KEY if vk not in (0x10, 0x11, 0x12)]
        monitored_mouse_vks = list(_MOUSE_VK_MAP.keys())
        prev_key_down, prev_mouse_down = platform_get_pressed_states(monitored_key_vks, monitored_mouse_vks)

        while not self._stop_event.wait(timeout=0.005):
            now = time.perf_counter()
            curr_keys, curr_mouse = platform_get_pressed_states(monitored_key_vks, monitored_mouse_vks)
            for vk in monitored_key_vks:
                is_down = curr_keys.get(vk, False)
                if is_down != prev_key_down.get(vk, False):
                    prev_key_down[vk] = is_down
                    self.feed_key_event(vk, is_down=is_down, timestamp_s=now)

            cursor_pos: tuple[int, int] | None = None
            for vk, btn in _MOUSE_VK_MAP.items():
                is_down = curr_mouse.get(vk, False)
                if is_down != prev_mouse_down.get(vk, False):
                    prev_mouse_down[vk] = is_down
                    if cursor_pos is None:
                        cursor_pos = platform_get_cursor_pos()
                    cx, cy = cursor_pos
                    self.feed_mouse_button_event(
                        btn,
                        is_down=is_down,
                        x=cx,
                        y=cy,
                        timestamp_s=now,
                    )
