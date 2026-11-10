"""Tests for Win32 ctypes structures, MacroExecutor, Panic Kill-Switch, MacroRecorder, and Hotkeys."""

from __future__ import annotations

import ctypes
import struct
import threading
import time

from keyforge.engine.backend import KEYFORGE_EXTRA_INFO, DryRunBackend
from keyforge.engine.executor import ExecutorState, MacroExecutor
from keyforge.engine.hotkeys import GlobalHotkeyManager
from keyforge.engine.recorder import MacroRecorder, RecorderConfig
from keyforge.engine.win32_input import INPUT, KEYBDINPUT, MOUSEINPUT
from keyforge.models.action import (
    ActionType,
    ClickType,
    MouseButton,
    MoveMode,
    make_key_down,
    make_key_press,
    make_mouse_click,
    make_mouse_move,
    make_mouse_scroll,
    make_repeat_block,
    make_wait,
)
from keyforge.models.dsl import parse_quick_expression
from keyforge.models.profile import MacroProfile, RepeatMode


def test_win32_ctypes_struct_alignment() -> None:
    """Verify 64-bit Windows SendInput struct sizes so SendInput never fails with ERROR_INVALID_PARAMETER."""
    ptr_bits = struct.calcsize("P") * 8
    if ptr_bits == 64:
        assert ctypes.sizeof(MOUSEINPUT) == 32
        assert ctypes.sizeof(KEYBDINPUT) == 24
        assert ctypes.sizeof(INPUT) == 40
    assert KEYFORGE_EXTRA_INFO == 0x4B465247


def test_executor_runs_flagship_macro_deterministically() -> None:
    backend = DryRunBackend(initial_cursor=(500, 500))
    executor = MacroExecutor(backend=backend, time_scale=0.0, rng_seed=42)

    actions = parse_quick_expression("Press E -> Wait 200ms -> Click -> Press 1")
    profile = MacroProfile(
        name="Test Loop",
        repeat_mode=RepeatMode.COUNT,
        repeat_count=3,
        inter_cycle_delay_ms=10,
        actions=actions,
    )

    telem = executor.run_blocking(profile)
    assert telem.state == ExecutorState.IDLE
    assert telem.cycles_completed == 3
    assert telem.actions_executed == 12  # 4 steps * 3 cycles

    # Each cycle dispatches: key_down(E), key_up(E), mouse_down(left), mouse_up(left), key_down(1), key_up(1)
    assert len(backend.events) == 18
    cycle_1 = [(e.kind, e.target) for e in backend.events[:6]]
    assert cycle_1 == [
        ("key_down", "E"),
        ("key_up", "E"),
        ("mouse_down", "left"),
        ("mouse_up", "left"),
        ("key_down", "1"),
        ("key_up", "1"),
    ]
    assert len(backend.currently_held_keys) == 0
    assert len(backend.currently_held_buttons) == 0


def test_executor_modifier_combos_and_mouse_actions() -> None:
    backend = DryRunBackend(initial_cursor=(100, 100))
    executor = MacroExecutor(backend=backend, time_scale=0.0)

    profile = MacroProfile(
        name="Complex Actions",
        repeat_mode=RepeatMode.COUNT,
        repeat_count=1,
        inter_cycle_delay_ms=0,
        actions=[
            make_key_press("Ctrl+Shift+E", hold_ms=10),
            make_mouse_move(300, 400, MoveMode.ABSOLUTE),
            make_mouse_move(20, -10, MoveMode.RELATIVE),
            make_mouse_scroll(scroll_dy=-2, scroll_dx=1),
            make_repeat_block(2, [make_key_press("Space", hold_ms=0)]),
        ],
    )

    executor.run_blocking(profile)
    kinds_targets = [(e.kind, e.target) for e in backend.events]
    # Verify Ctrl+Shift+E presses in order and releases in reverse order
    assert kinds_targets[:6] == [
        ("key_down", "Ctrl"),
        ("key_down", "Shift"),
        ("key_down", "E"),
        ("key_up", "E"),
        ("key_up", "Shift"),
        ("key_up", "Ctrl"),
    ]
    assert backend.get_cursor_pos() == (320, 390)


def test_panic_kill_switch_releases_all_stuck_inputs() -> None:
    """Verify EmergencyInputSanitizer releases held keys and mouse buttons on panic_abort()."""
    backend = DryRunBackend()
    executor = MacroExecutor(backend=backend, time_scale=1.0)

    # Profile that holds Shift and Left Mouse button down, then waits 60 seconds
    profile = MacroProfile(
        name="Hold Forever",
        repeat_mode=RepeatMode.INFINITE,
        inter_cycle_delay_ms=100,
        actions=[
            make_key_down("Shift"),
            make_mouse_click(MouseButton.LEFT, ClickType.DOWN),
            make_wait(60_000),
        ],
    )

    started = executor.start(profile)
    assert started is True

    # Wait briefly until Shift and Left Mouse are depressed
    deadline = time.perf_counter() + 2.0
    while time.perf_counter() < deadline:
        if "Shift" in backend.currently_held_keys and MouseButton.LEFT in backend.currently_held_buttons:
            break
        time.sleep(0.01)

    assert "Shift" in backend.currently_held_keys
    assert MouseButton.LEFT in backend.currently_held_buttons

    # Trigger Panic Kill-Switch!
    t0 = time.perf_counter()
    executor.panic_abort()
    abort_latency_ms = (time.perf_counter() - t0) * 1000.0

    # Wait up to 200ms for worker thread cleanup
    time.sleep(0.05)

    assert abort_latency_ms < 100.0
    assert executor.telemetry.state == ExecutorState.PANIC_ABORTED
    # Critical security guarantee: NO keys or mouse buttons remain stuck down!
    assert len(backend.currently_held_keys) == 0
    assert len(backend.currently_held_buttons) == 0


def test_recorder_coalesces_taps_and_ignores_synthetic_events() -> None:
    recorder = MacroRecorder(
        config=RecorderConfig(
            record_delays=True,
            min_delay_ms=20,
            merge_key_taps=True,
            capture_click_coordinates=True,
        )
    )
    recorder.set_ignored_hotkeys("F7", "F12")
    recorder.start(enable_os_poller=False)

    # 1. Ignored hotkey F7 (VK 0x76) should be filtered out
    recorder.feed_key_event(0x76, is_down=True, timestamp_s=1.000)
    recorder.feed_key_event(0x76, is_down=False, timestamp_s=1.030)

    # 2. Synthetic event from KeyForge itself (KEYFORGE_EXTRA_INFO) must be filtered out
    recorder.feed_key_event(0x45, is_down=True, timestamp_s=1.050, extra_info=KEYFORGE_EXTRA_INFO)
    recorder.feed_key_event(0x45, is_down=False, timestamp_s=1.080, extra_info=KEYFORGE_EXTRA_INFO)

    # 3. Genuine user tap on 'E' (0x45) held for 35ms
    recorder.feed_key_event(0x45, is_down=True, timestamp_s=2.000)
    recorder.feed_key_event(0x45, is_down=False, timestamp_s=2.035)

    # 4. Wait 200ms then Left Click at (640, 480) held for 25ms
    recorder.feed_mouse_button_event(MouseButton.LEFT, is_down=True, x=640, y=480, timestamp_s=2.235)
    recorder.feed_mouse_button_event(MouseButton.LEFT, is_down=False, x=640, y=480, timestamp_s=2.260)

    # 5. Scroll wheel
    recorder.feed_mouse_scroll_event(scroll_dy=-3, timestamp_s=2.300)

    recorded = recorder.stop()
    assert len(recorded) == 5
    assert recorded[0].action_type == ActionType.KEY_PRESS
    assert recorded[0].key == "E"
    assert recorded[0].hold_ms == 35

    assert recorded[1].action_type == ActionType.WAIT
    assert recorded[1].wait_ms == 200

    assert recorded[2].action_type == ActionType.MOUSE_CLICK
    assert recorded[2].button == MouseButton.LEFT
    assert recorded[2].click_type == ClickType.SINGLE
    assert (recorded[2].x, recorded[2].y) == (640, 480)

    assert recorded[3].action_type == ActionType.WAIT
    assert recorded[3].wait_ms == 40

    assert recorded[4].action_type == ActionType.MOUSE_SCROLL
    assert recorded[4].scroll_dy == -3


def test_global_hotkey_manager_registration() -> None:
    mgr = GlobalHotkeyManager()
    fired = threading.Event()
    mgr.register("panic", "F12", fired.set)

    assert mgr.trigger_for_test("panic") is True
    assert fired.is_set() is True

    mgr.unregister("panic")
    assert mgr.trigger_for_test("panic") is False


def test_win32_input_backend_dispatches_signed_inputs(monkeypatch) -> None:
    from keyforge.engine import win32_input
    from keyforge.models.keymap import resolve_single_key

    dispatched: list[INPUT] = []

    def fake_send_single_input(inp: INPUT) -> int:
        # Copy struct contents for inspection
        copy_inp = INPUT.from_buffer_copy(inp)
        dispatched.append(copy_inp)
        return 1

    monkeypatch.setattr(win32_input, "_send_single_input", fake_send_single_input)
    backend = win32_input.Win32InputBackend()

    # 1. Key down & up (including extended key 'Right')
    key_right = resolve_single_key("Right")
    backend.key_down(key_right)
    backend.key_up(key_right)
    assert len(dispatched) == 2
    assert dispatched[0].type == win32_input.INPUT_KEYBOARD
    assert dispatched[0].ki.wVk == key_right.vk_code
    assert dispatched[0].ki.dwExtraInfo == KEYFORGE_EXTRA_INFO
    assert bool(dispatched[0].ki.dwFlags & win32_input.KEYEVENTF_EXTENDEDKEY)
    assert bool(dispatched[1].ki.dwFlags & win32_input.KEYEVENTF_KEYUP)

    # 2. Mouse buttons (Left, Right, Middle, X1, X2)
    for btn in (
        MouseButton.LEFT,
        MouseButton.RIGHT,
        MouseButton.MIDDLE,
        MouseButton.X1,
        MouseButton.X2,
    ):
        backend.mouse_down(btn)
        backend.mouse_up(btn)

    # 3. Relative move & scroll
    backend.move_cursor(10, -5, relative=True)
    backend.scroll(dx=1, dy=-2)
    assert all(inp.mi.dwExtraInfo == KEYFORGE_EXTRA_INFO for inp in dispatched[2:])
    cx, cy = backend.get_cursor_pos()
    assert isinstance(cx, int) and isinstance(cy, int)


def test_recorder_coalesces_on_final_cap_slot_and_ignores_orphan_keyup() -> None:
    recorder = MacroRecorder(
        config=RecorderConfig(
            record_delays=True,
            min_delay_ms=10,
            merge_key_taps=True,
            max_recorded_steps=1,
        )
    )
    recorder.start(enable_os_poller=False)

    # Orphan key-up for a key that was never pressed during recording must be ignored
    recorder.feed_key_event(0x41, is_down=False, timestamp_s=1.000)
    assert len(recorder.recorded_actions) == 0

    # Key-down fills slot 1/1; subsequent key-up must still coalesce into KEY_PRESS in place!
    recorder.feed_key_event(0x45, is_down=True, timestamp_s=1.100)
    recorder.feed_key_event(0x45, is_down=False, timestamp_s=1.140)
    recorded = recorder.stop()
    assert len(recorded) == 1
    assert recorded[0].action_type == ActionType.KEY_PRESS
    assert recorded[0].key == "E"
    assert recorded[0].hold_ms == 40
