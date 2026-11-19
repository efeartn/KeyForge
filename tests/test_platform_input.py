"""Tests for cross-platform macOS/Linux input backends, POSIX installer/updater, and UI bugfix regressions."""

from __future__ import annotations

import contextlib
import ctypes
import os
import stat
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from keyforge import __version__
from keyforge.engine.backend import KEYFORGE_EXTRA_INFO
from keyforge.engine.hotkeys import GlobalHotkeyManager
from keyforge.engine.platform_input import (
    VK_LBUTTON,
    VK_RBUTTON,
    VK_TO_MAC_KEYCODE,
    VK_TO_X11_KEYSYM,
    CGPoint,
    LinuxX11InputBackend,
    MacOSInputBackend,
    get_platform_capabilities,
)
from keyforge.engine.recorder import MacroRecorder, RecorderConfig
from keyforge.installer import get_default_install_dir, install_keyforge, uninstall_keyforge
from keyforge.models.action import ActionType, ClickType, MouseButton, make_mouse_click
from keyforge.models.keymap import CANONICAL_KEYS, format_captured_key_combo, normalize_key_combo, resolve_single_key
from keyforge.models.profile import RepeatMode
from keyforge.ui.app import KeyForgeApp
from keyforge.ui.theme import BG_CARD, BG_CARD_ACTIVE, BG_ELEVATED, TEXT_MUTED, center_window_on_parent
from keyforge.ui.update_dialog import UpdateDialog
from keyforge.updater import ReleaseInfo, apply_update_and_restart


def test_vk_tables_cover_common_keys_modifiers_arrows_and_fkeys() -> None:
    expected_keys = (
        "Ctrl",
        "RCtrl",
        "Shift",
        "RShift",
        "Alt",
        "RAlt",
        "Win",
        "RWin",
        "Enter",
        "Space",
        "Tab",
        "Esc",
        "Backspace",
        "Delete",
        "Up",
        "Down",
        "Left",
        "Right",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        *(f"F{i}" for i in range(1, 13)),
        *"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    )
    for name in expected_keys:
        vk = CANONICAL_KEYS[name].vk_code
        assert vk in VK_TO_MAC_KEYCODE, f"Missing macOS keycode for {name} (VK 0x{vk:02X})"
        assert vk in VK_TO_X11_KEYSYM, f"Missing X11 keysym for {name} (VK 0x{vk:02X})"


def test_macos_input_backend_coregraphics_events_and_cfrelease() -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    created_handles: list[int] = []
    released_handles: list[int] = []
    trusted_state = {"trusted": True}

    class _FakeCG:
        def __init__(self) -> None:
            self._next = 100

        def _alloc(self) -> int:
            self._next += 1
            created_handles.append(self._next)
            return self._next

        def CGEventCreate(self, src: Any) -> int:
            calls.append(("CGEventCreate", (src,)))
            return self._alloc()

        def CGEventGetLocation(self, evt: int) -> CGPoint:
            calls.append(("CGEventGetLocation", (evt,)))
            return CGPoint(320.4, 240.6)

        def CGEventCreateKeyboardEvent(self, src: Any, keycode: int, key_down: bool) -> int:
            calls.append(("CGEventCreateKeyboardEvent", (keycode, key_down)))
            return self._alloc()

        def CGEventCreateMouseEvent(self, src: Any, ev_type: int, pt: CGPoint, btn: int) -> int:
            calls.append(("CGEventCreateMouseEvent", (ev_type, pt.x, pt.y, btn)))
            return self._alloc()

        def CGEventCreateScrollWheelEvent2(
            self,
            src: Any,
            units: int,
            wheel_count: int,
            w1: int,
            w2: int,
            w3: int,
        ) -> int:
            calls.append(("CGEventCreateScrollWheelEvent2", (units, wheel_count, w1, w2, w3)))
            return self._alloc()

        def CGEventSetFlags(self, evt: int, flags: int) -> None:
            calls.append(("CGEventSetFlags", (evt, flags)))

        def CGEventSetIntegerValueField(self, evt: int, field: int, value: int) -> None:
            calls.append(("CGEventSetIntegerValueField", (evt, field, value)))

        def CGEventPost(self, tap: int, evt: int) -> None:
            calls.append(("CGEventPost", (tap, evt)))

        def CGEventSourceKeyState(self, state_id: int, keycode: int) -> bool:
            return keycode == VK_TO_MAC_KEYCODE[resolve_single_key("E").vk_code]

        def CGEventSourceButtonState(self, state_id: int, button: int) -> bool:
            return button == 0

    class _FakeCF:
        def CFRelease(self, ref: int) -> None:
            released_handles.append(ref)

    class _FakeAppServices:
        def AXIsProcessTrusted(self) -> bool:
            return trusted_state["trusted"]

    backend = MacOSInputBackend(load_frameworks=False)
    backend._cg = _FakeCG()
    backend._cf = _FakeCF()
    backend._app_services = _FakeAppServices()
    backend._available = True

    assert backend.get_cursor_pos() == (320, 241)
    backend.key_down(resolve_single_key("Cmd"))
    backend.key_down(resolve_single_key("C"))
    backend.key_up(resolve_single_key("C"))
    backend.key_up(resolve_single_key("Cmd"))

    backend.move_cursor(500, 600, relative=False)
    backend.mouse_down(MouseButton.RIGHT)
    backend.move_cursor(10, -5, relative=True)
    backend.mouse_up(MouseButton.RIGHT)
    backend.scroll(dx=1, dy=-3)

    assert backend.is_key_down(resolve_single_key("E").vk_code) is True
    assert backend.is_mouse_down(VK_LBUTTON) is True
    assert backend.is_mouse_down(VK_RBUTTON) is False

    # Every created CGEventRef must be released via CFRelease to prevent native memory leaks
    assert len(created_handles) > 0
    assert created_handles == released_handles

    # Non-variadic CGEventCreateScrollWheelEvent2 was used and KEYFORGE_EXTRA_INFO stamped
    assert any(name == "CGEventCreateScrollWheelEvent2" for name, _ in calls)
    assert any(name == "CGEventSetIntegerValueField" and args[2] == KEYFORGE_EXTRA_INFO for name, args in calls)
    assert any(name == "CGEventSetFlags" and (args[1] & 0x00100000) for name, args in calls)

    # Capabilities respect AXIsProcessTrusted()
    with patch("keyforge.engine.platform_input.sys.platform", "darwin"):
        caps_ok = get_platform_capabilities(backend)
        assert caps_ok.can_inject is True
        assert caps_ok.requires_permission is False

        trusted_state["trusted"] = False
        caps_denied = get_platform_capabilities(backend)
        assert caps_denied.can_inject is False
        assert caps_denied.requires_permission is True
        assert "Accessibility" in caps_denied.notice


def test_linux_x11_backend_mocked_and_wayland_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    e_vk = resolve_single_key("E").vk_code
    e_keycode = 26

    class _FakeX11:
        def XKeysymToKeycode(self, disp: int, keysym: int) -> int:
            return e_keycode if keysym == VK_TO_X11_KEYSYM[e_vk] else 38

        def XFlush(self, disp: int) -> int:
            calls.append(("XFlush", (disp,)))
            return 1

        def XQueryKeymap(self, disp: int, buf: Any) -> int:
            raw = bytearray(32)
            raw[e_keycode >> 3] |= 1 << (e_keycode & 7)
            ctypes.memmove(buf, bytes(raw), 32)
            return 1

        def XQueryPointer(self, disp: int, root: int, r_ret, c_ret, rx, ry, wx, wy, mask) -> int:
            rx._obj.value = 840
            ry._obj.value = 420
            mask._obj.value = 1 << 8  # Button1Mask (Left mouse held)
            return 1

        def XCloseDisplay(self, disp: int) -> int:
            calls.append(("XCloseDisplay", (disp,)))
            return 0

    class _FakeXTest:
        def XTestFakeKeyEvent(self, disp: int, kc: int, is_press: int, delay: int) -> int:
            calls.append(("XTestFakeKeyEvent", (kc, is_press)))
            return 1

        def XTestFakeButtonEvent(self, disp: int, btn: int, is_press: int, delay: int) -> int:
            calls.append(("XTestFakeButtonEvent", (btn, is_press)))
            return 1

        def XTestFakeMotionEvent(self, disp: int, screen: int, x: int, y: int, delay: int) -> int:
            calls.append(("XTestFakeMotionEvent", (screen, x, y)))
            return 1

        def XTestFakeRelativeMotionEvent(self, disp: int, dx: int, dy: int, delay: int) -> int:
            calls.append(("XTestFakeRelativeMotionEvent", (dx, dy)))
            return 1

    backend = LinuxX11InputBackend(connect_display=False)
    backend._x11 = _FakeX11()
    backend._xtst = _FakeXTest()
    backend._display = 0xDEAD
    backend._root_window = 0x1
    backend._available = True

    backend.key_down(resolve_single_key("E"))
    backend.key_up(resolve_single_key("E"))
    backend.mouse_down(MouseButton.LEFT)
    backend.mouse_up(MouseButton.LEFT)
    backend.move_cursor(120, 240, relative=False)
    backend.move_cursor(10, -5, relative=True)
    backend.scroll(dx=1, dy=-2)

    assert backend.get_cursor_pos() == (840, 420)
    keys_map, mouse_map = backend.get_pressed_states([e_vk, resolve_single_key("A").vk_code], [VK_LBUTTON, VK_RBUTTON])
    assert keys_map[e_vk] is True
    assert mouse_map[VK_LBUTTON] is True
    assert mouse_map[VK_RBUTTON] is False

    assert ("XTestFakeKeyEvent", (e_keycode, 1)) in calls
    assert ("XTestFakeKeyEvent", (e_keycode, 0)) in calls
    assert ("XTestFakeButtonEvent", (1, 1)) in calls
    assert ("XTestFakeMotionEvent", (0, 120, 240)) in calls
    assert ("XTestFakeRelativeMotionEvent", (10, -5)) in calls
    assert ("XTestFakeButtonEvent", (5, 1)) in calls  # scroll down

    backend.close()
    assert ("XCloseDisplay", (0xDEAD,)) in calls
    assert backend.available is False

    # Graceful Wayland without X11 fallback
    unavailable = LinuxX11InputBackend(connect_display=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    caps_wayland = get_platform_capabilities(unavailable)
    assert caps_wayland.can_inject is False
    assert caps_wayland.can_poll_keys is False
    assert "Wayland" in caps_wayland.backend_name


@pytest.mark.skipif(
    not (sys.platform == "linux" and os.environ.get("DISPLAY")),
    reason="Live X11/XTest integration test runs under Linux DISPLAY/Xvfb",
)
def test_linux_live_x11_xvfb_roundtrip() -> None:
    backend = LinuxX11InputBackend()
    try:
        assert backend.available is True
        backend.move_cursor(120, 140, relative=False)
        cx, cy = backend.get_cursor_pos()
        assert isinstance(cx, int) and isinstance(cy, int)
        backend.key_down(resolve_single_key("E"))
        backend.key_up(resolve_single_key("E"))
        keys_map, mouse_map = backend.get_pressed_states([resolve_single_key("E").vk_code], [VK_LBUTTON])
        assert resolve_single_key("E").vk_code in keys_map
        assert VK_LBUTTON in mouse_map
    finally:
        backend.close()


def test_hotkeys_adaptive_polling_and_recorder_combo_ignore(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. GlobalHotkeyManager adaptive idle/active intervals & no-thread start when unsupported
    mgr = GlobalHotkeyManager(poll_interval_s=0.020, idle_poll_interval_s=0.050)
    assert mgr.current_poll_interval_s == pytest.approx(0.050)
    mgr.set_active_mode(True)
    assert mgr.current_poll_interval_s == pytest.approx(0.020)
    mgr.set_active_mode(False)
    assert mgr.current_poll_interval_s == pytest.approx(0.050)

    monkeypatch.setattr("keyforge.engine.hotkeys.platform_supports_key_polling", lambda: False)
    mgr.register("panic", "F12", lambda: None)
    mgr.start()
    assert mgr._thread is None
    assert mgr.trigger_for_test("panic") is True

    # 2. MacroRecorder: Ctrl+F6 ignored hotkey must NOT suppress recording Ctrl+C
    rec = MacroRecorder(config=RecorderConfig(record_delays=False, merge_key_taps=True))
    rec.set_ignored_hotkeys("F7", "Ctrl+F6")
    rec.start(enable_os_poller=False)

    ctrl_vk = CANONICAL_KEYS["Ctrl"].vk_code
    c_vk = CANONICAL_KEYS["C"].vk_code
    f6_vk = CANONICAL_KEYS["F6"].vk_code
    f7_vk = CANONICAL_KEYS["F7"].vk_code

    # User holds Ctrl + taps C -> Ctrl Down, Press C, Ctrl Up
    rec.feed_key_event(ctrl_vk, is_down=True, timestamp_s=1.000)
    rec.feed_key_event(c_vk, is_down=True, timestamp_s=1.020)
    rec.feed_key_event(c_vk, is_down=False, timestamp_s=1.045)
    # Triggering Ctrl+F6 while Ctrl is held suppresses F6 completely, and standalone F7 is ignored
    rec.feed_key_event(f6_vk, is_down=True, timestamp_s=1.080)
    rec.feed_key_event(f6_vk, is_down=False, timestamp_s=1.100)
    rec.feed_key_event(f7_vk, is_down=True, timestamp_s=1.120)
    rec.feed_key_event(f7_vk, is_down=False, timestamp_s=1.140)
    rec.feed_key_event(ctrl_vk, is_down=False, timestamp_s=3.200)

    recorded = rec.stop()
    assert [a.action_type for a in recorded] == [ActionType.KEY_DOWN, ActionType.KEY_PRESS, ActionType.KEY_UP]
    assert [a.key for a in recorded] == ["Ctrl", "C", "Ctrl"]


def test_macos_keymap_aliases_and_retina_workarea_positioning() -> None:
    assert normalize_key_combo("cmd + c") == "Win+C"
    assert normalize_key_combo("option + tab") == "Alt+Tab"
    assert format_captured_key_combo("c", state=0x0008, platform_name="darwin") == "Win+C"
    assert format_captured_key_combo("Tab", state=0x0010, platform_name="darwin") == "Alt+Tab"
    assert format_captured_key_combo("e", state=0x0005, platform_name="darwin") == "Ctrl+Shift+E"

    class _FakeWin:
        def __init__(self) -> None:
            self.geo = ""

        def winfo_screenwidth(self) -> int:
            return 1512

        def winfo_screenheight(self) -> int:
            return 982

        def geometry(self, val: str) -> None:
            self.geo = val

    fake_win = _FakeWin()
    with (
        patch("keyforge.ui.theme.sys.platform", "darwin"),
        patch("keyforge.ui.theme.ctk.ScalingTracker.get_window_scaling", return_value=2.0),
    ):
        w, h = center_window_on_parent(fake_win, None, 540, 540)
        assert (w, h) == (540, 540)
        pos_part = fake_win.geo.split("+", 1)[1]
        x_str, y_str = pos_part.split("+")
        assert 400 <= int(x_str) <= 600
        assert 150 <= int(y_str) <= 300


def test_posix_installer_permissions_shortcuts_and_updater_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    (fake_home / "Desktop").mkdir(parents=True)
    xdg_data = tmp_path / "xdg_data"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.delenv("KEYFORGE_INSTALL_DIR", raising=False)
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path / "cfg"))

    # 1. macOS default install directory resolves to ~/Applications/KeyForge
    monkeypatch.setattr(sys, "platform", "darwin")
    assert get_default_install_dir() == fake_home / "Applications" / "KeyForge"

    # 2. Linux install writes executable binary, icon, Desktop + Application menu .desktop launchers, and cleans up
    monkeypatch.setattr(sys, "platform", "linux")
    assert get_default_install_dir() == fake_home / ".local" / "share" / "KeyForge"

    src_bin = tmp_path / "KeyForge-Linux-x64"
    src_bin.write_bytes(b"\x7fELF-FAKE-BIN")
    install_dir = tmp_path / "linux_install"

    chmod_calls: list[int] = []
    orig_chmod = Path.chmod

    def _spy_chmod(self_path: Path, mode: int) -> None:
        chmod_calls.append(mode)
        orig_chmod(self_path, mode)

    with patch.object(Path, "chmod", autospec=True, side_effect=_spy_chmod):
        manifest = install_keyforge(
            install_dir=install_dir,
            source_exe=src_bin,
            create_desktop_shortcut=True,
            create_start_menu_shortcut=True,
        )
    installed_bin = Path(manifest["installed_exe"])
    assert installed_bin.is_file()
    assert 0o755 in chmod_calls
    if os.name != "nt":
        assert bool(installed_bin.stat().st_mode & stat.S_IXUSR)
    desktop_file = Path(manifest["desktop_shortcut"])
    menu_file = Path(manifest["start_menu_shortcut"])
    assert desktop_file.is_file()
    assert menu_file.is_file()
    assert "[Desktop Entry]" in menu_file.read_text(encoding="utf-8")

    uninstall_keyforge(install_dir=install_dir, purge_user_data=False, spawn_self_delete=False)
    assert not install_dir.exists()
    assert not desktop_file.exists()
    assert not menu_file.exists()

    # 3. POSIX updater apply_update_and_restart removes staging downloaded file after swap
    updates_dir = tmp_path / "updates"
    updates_dir.mkdir()
    new_bin = updates_dir / "KeyForge-Linux-x64"
    new_bin.write_bytes(b"NEW-BINARY")
    target_bin = tmp_path / "bin" / "KeyForge"
    target_bin.parent.mkdir()
    target_bin.write_bytes(b"OLD-BINARY")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(target_bin))
    with patch("keyforge.updater.subprocess.Popen") as mock_popen:
        relaunch = apply_update_and_restart(new_bin, dry_run=False)
    assert relaunch == [str(target_bin)]
    assert target_bin.read_bytes() == b"NEW-BINARY"
    assert not new_bin.exists()
    mock_popen.assert_called_once()


def test_ui_bugfix_regressions_filter_highlight_readonly_and_update_btn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        app._switch_ui_mode("Studio")

        # 1. Read-only console and security audit textboxes
        assert app.console_box.cget("state") == "disabled"
        assert app.sec_report_box.cget("state") == "disabled"

        # 2. Step subtitle polish keeps key/wait contract and avoids mid-word truncation on flagship click
        click_step = make_mouse_click(MouseButton.LEFT, ClickType.SINGLE, label="Primary Attack / Confirm", hold_ms=25)
        assert KeyForgeApp._step_subtitle(click_step) == "Primary Attack / Confirm • At cursor"

        # 3. Filtered navigation & delete safety + preset load filter reset
        app._apply_template_chip("Press E -> Wait 200ms -> Click -> Press 1")
        app.select_step(0)
        app._set_step_type_filter("wait")
        assert app._visible_step_indices == [1]
        # Selected step #0 is filtered out -> Delete must NOT delete hidden step
        assert app.delete_selected_step() is False
        assert len(app.profile.actions) == 4
        # Arrow down snaps onto the visible filtered step
        app._on_arrow_down()
        assert app._selected_step_index == 1
        assert app._selected_step_is_actionable() is True
        # Loading a preset resets active category/text filters
        app.filter_entry.insert(0, "xyz")
        app._on_filter_changed()
        app.load_preset("Key Spammer (Press E Loop)")
        assert app._step_type_filter == "all"
        assert app._filter_query == ""
        assert len(app._step_row_frames) == 2

        # 4. Active step highlight syncs slot["fg_color"]
        step0_id = app.profile.actions[0].id
        app.executor._telemetry.current_action_id = step0_id
        app._ui_tick()
        assert app._step_slots_by_id[step0_id]["fg_color"] == BG_CARD_ACTIVE
        app.executor._telemetry.current_action_id = None
        app._ui_tick()
        assert app._step_slots_by_id[step0_id]["fg_color"] == BG_CARD

        # 5. Studio -> Simple mode switch commits edited Studio repeat count
        app._on_repeat_settings_changed("Fixed Count")
        app.repeat_val_entry.delete(0, "end")
        app.repeat_val_entry.insert(0, "9")
        app._switch_ui_mode("Simple")
        assert app.profile.repeat_mode == RepeatMode.COUNT
        assert app.profile.repeat_count == 9
        assert app.simple_repeat_val_entry.get().strip() == "9"

        # 6. Disabled "Up to Date" CTA in UpdateDialog uses muted surface rather than vivid green
        up_to_date_rel = ReleaseInfo(
            tag_name=f"v{__version__}",
            version=__version__,
            name=f"KeyForge v{__version__}",
            is_update_available=False,
        )
        dlg = UpdateDialog(app, initial_release=up_to_date_rel)
        try:
            assert dlg.install_btn.cget("state") == "disabled"
            assert dlg.install_btn.cget("text") == "Up to Date"
            assert dlg.install_btn.cget("fg_color") == BG_ELEVATED
            assert dlg.install_btn.cget("text_color") == TEXT_MUTED
        finally:
            dlg._on_close()
    finally:
        with contextlib.suppress(Exception):
            app.executor._telemetry.current_action_id = None
        app._on_close()
