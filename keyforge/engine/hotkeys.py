"""Global hotkey listener with instant Panic Kill-Switch detection."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

from keyforge.engine.win32_input import get_async_key_state
from keyforge.models.keymap import KeyInfo, parse_key_combo


class GlobalHotkeyManager:
    """Monitors global key combinations (Start/Stop, Record, Panic Stop) in the background."""

    def __init__(self, poll_interval_s: float = 0.015) -> None:
        self._poll_interval_s = max(0.005, float(poll_interval_s))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._bindings: dict[str, tuple[list[KeyInfo], Callable[[], None]]] = {}
        self._was_active: dict[str, bool] = {}

    def register(self, name: str, combo: str, callback: Callable[[], None]) -> None:
        """Register or update a named hotkey binding (e.g. name='panic', combo='F12')."""
        keys = parse_key_combo(combo)
        with self._lock:
            self._bindings[name] = (keys, callback)
            self._was_active[name] = False

    def unregister(self, name: str) -> None:
        """Remove a named hotkey binding."""
        with self._lock:
            self._bindings.pop(name, None)
            self._was_active.pop(name, None)

    def clear(self) -> None:
        """Remove all hotkey bindings."""
        with self._lock:
            self._bindings.clear()
            self._was_active.clear()

    def start(self) -> None:
        """Start the background Win32 hotkey polling daemon thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="KeyForge-Hotkeys",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the background hotkey polling thread."""
        thread_to_join: threading.Thread | None = None
        with self._lock:
            self._stop_event.set()
            thread_to_join = self._thread
            self._thread = None
        if thread_to_join is not None and thread_to_join is not threading.current_thread():
            thread_to_join.join(timeout=1.0)

    def trigger_for_test(self, name: str) -> bool:
        """Manually invoke a registered hotkey callback for deterministic unit testing."""
        with self._lock:
            entry = self._bindings.get(name)
        if entry is None:
            return False
        _, callback = entry
        callback()
        return True

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(timeout=self._poll_interval_s):
            callbacks_to_fire: list[Callable[[], None]] = []
            with self._lock:
                for name, (keys, callback) in self._bindings.items():
                    all_pressed = all(get_async_key_state(k.vk_code) for k in keys)
                    was_pressed = self._was_active.get(name, False)
                    if all_pressed and not was_pressed:
                        self._was_active[name] = True
                        callbacks_to_fire.append(callback)
                    elif not all_pressed and was_pressed:
                        self._was_active[name] = False

            for cb in callbacks_to_fire:
                with contextlib.suppress(Exception):
                    cb()
