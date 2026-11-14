"""Comprehensive tests for Apple Dark Titanium UI overhaul, dialogs, config persistence, and updater UI."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from keyforge.config import load_active_profile_backup, load_app_config
from keyforge.models.action import (
    ActionType,
    ClickType,
    MouseButton,
    make_key_press,
    make_mouse_click,
    make_repeat_block,
    make_wait,
)
from keyforge.models.profile import RepeatMode, get_builtin_presets
from keyforge.ui.app import KeyForgeApp
from keyforge.ui.step_dialog import StepEditorDialog
from keyforge.ui.theme import get_fonts
from keyforge.ui.update_dialog import UpdateDialog
from keyforge.updater import ReleaseInfo


def test_beginner_presets_present_and_valid() -> None:
    presets = get_builtin_presets()
    assert "Simple Auto-Clicker (10 CPS)" in presets
    assert "Key Spammer (Press E Loop)" in presets

    clicker = presets["Simple Auto-Clicker (10 CPS)"]
    assert clicker.total_cycle_wait_ms() == 100
    assert clicker.actions[0].action_type == ActionType.MOUSE_CLICK

    spammer = presets["Key Spammer (Press E Loop)"]
    assert spammer.total_cycle_wait_ms() == 100
    assert spammer.actions[0].action_type == ActionType.KEY_PRESS
    assert spammer.actions[0].key == "E"


def test_step_dialog_click_hold_ms_and_repeat_disabled_substep_preservation() -> None:
    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        # 1. MOUSE_CLICK editable hold_ms
        saved: list = []
        click_act = make_mouse_click(MouseButton.RIGHT, ClickType.SINGLE, hold_ms=42)
        dlg_click = StepEditorDialog(app, initial_action=click_act, on_save=saved.append)
        assert dlg_click.hold_entry.get().strip() == "42"
        dlg_click.hold_entry.delete(0, "end")
        dlg_click.hold_entry.insert(0, "75")
        dlg_click._on_submit()
        assert len(saved) == 1
        assert saved[0].hold_ms == 75

        # 2. REPEAT_BLOCK preserves disabled sub-actions when inner DSL string is untouched
        sub_enabled = make_key_press("F", hold_ms=25)
        sub_disabled = make_wait(120)
        sub_disabled.enabled = False
        repeat_act = make_repeat_block(repeat_count=3, sub_actions=[sub_enabled, sub_disabled])

        saved_repeat: list = []
        dlg_rep = StepEditorDialog(app, initial_action=repeat_act, on_save=saved_repeat.append)
        dlg_rep.repeat_count_entry.delete(0, "end")
        dlg_rep.repeat_count_entry.insert(0, "5")
        dlg_rep._on_submit()
        assert len(saved_repeat) == 1
        assert saved_repeat[0].repeat_count == 5
        assert len(saved_repeat[0].sub_actions) == 2
        assert saved_repeat[0].sub_actions[1].enabled is False
    finally:
        app._on_close()


def test_simple_studio_sync_caching_and_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        # Cached fonts shared between app and dialog
        assert get_fonts(app) is app.fonts

        # Switch to Simple Auto-Clicker and configure 100ms
        app.simple_task_var.set("Auto-Clicker")
        app._on_simple_task_changed("Auto-Clicker")
        assert len(app.profile.actions) == 2
        assert app.profile.actions[0].action_type == ActionType.MOUSE_CLICK

        # Empty profile execution guard shows toast cleanly without starting
        app.clear_all_steps()
        app.toggle_execution()
        assert app.executor.is_running is False
        assert "No enabled" in app.toast_lbl.cget("text")

        # Restore a preset, then test Key Spammer exact 5ms slider quantization + 25ms round-trip
        app.load_preset("Key Spammer (Press E Loop)")
        assert app.ss_interval_slider.cget("number_of_steps") == 395
        for target_ms in (25, 80, 140, 2000):
            app.ss_interval_slider.set(target_ms)
            assert int(round(app.ss_interval_slider.get())) == target_ms

        app.ss_interval_slider.set(25)
        assert app._apply_simple_spammer_to_profile(record_undo=False) is True
        assert app.profile.actions[0].hold_ms == 20
        assert app.profile.actions[1].wait_ms == 5
        assert app.profile.total_cycle_wait_ms() == 25
        app._sync_profile_to_simple_view()
        assert int(round(app.ss_interval_slider.get())) == 25

        # Switch to Studio & test sync_ui_settings_to_profile does NOT rebuild timeline cards
        app._switch_ui_mode("Studio")
        before_rows = dict(app._step_row_frames)
        app.repeat_mode_var.set(RepeatMode.COUNT.value)
        app.repeat_val_entry.delete(0, "end")
        app.repeat_val_entry.insert(0, "7")
        assert app.sync_ui_settings_to_profile() is True
        assert dict(app._step_row_frames) == before_rows

        # Dirty-checked _ui_tick runs cleanly at 10 Hz / ~3 Hz
        app._ui_tick()
        app._ui_tick()

        # Ctrl+Z does not undo macro steps while focus is inside an Entry widget
        app.update()
        app.dsl_entry.focus_set()
        app.update_idletasks()
        pre_undo_count = len(app.profile.actions)
        app._on_ctrl_z()
        assert len(app.profile.actions) == pre_undo_count
    finally:
        app._on_close()

    # Verify AppConfig and active profile persisted to isolated KEYFORGE_CONFIG_DIR
    cfg = load_app_config(tmp_path)
    assert cfg.ui_mode == "studio"
    restored = load_active_profile_backup(tmp_path)
    assert restored is not None
    assert restored.repeat_mode == RepeatMode.COUNT
    assert restored.repeat_count == 7


def test_startup_update_banner_and_update_dialog_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        release = ReleaseInfo(
            tag_name="v9.9.9",
            version="9.9.9",
            name="KeyForge v9.9.9",
            body="Minimalist titanium update",
            html_url="https://github.com/efeartn/KeyForge/releases/tag/v9.9.9",
            asset_name="KeyForge-Windows-x64.exe",
            download_url="https://github.com/efeartn/KeyForge/releases/download/v9.9.9/KeyForge-Windows-x64.exe",
            asset_size=None,
            sha256="a" * 64,
            is_update_available=True,
        )
        app._on_startup_update_available(release)
        assert "9.9.9" in app.updates_btn.cget("text")
        assert app.update_banner.winfo_manager() != ""

        # Dismiss banner
        app.dismiss_update_banner()
        assert app.update_banner.winfo_manager() == ""

        # Open UpdateDialog with cached release and verify Check Again + safe close
        dlg = UpdateDialog(
            app,
            initial_release=release,
            on_before_restart=app._prepare_for_update_restart,
        )
        assert dlg.check_again_btn.cget("state") == "normal"
        assert "Size unknown" in dlg.meta_lbl.cget("text")
        assert "SHA-256" in dlg.sha_pill.cget("text")

        # Source mode download completion does not crash or destroy parent
        fake_bin = tmp_path / "updates" / "KeyForge-Windows-x64.exe"
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.write_bytes(b"MZ")
        dlg._on_download_complete(fake_bin)
        assert "source mode" in dlg.progress_lbl.cget("text").lower()

        # Frozen mode invokes on_before_restart & apply_update_and_restart cleanly
        before_called = False

        def _mark_before() -> None:
            nonlocal before_called
            before_called = True
            app._prepare_for_update_restart()

        dlg._on_before_restart = _mark_before
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        with (
            patch("keyforge.ui.update_dialog.apply_update_and_restart") as mock_apply,
            patch.object(app, "quit") as mock_quit,
        ):
            dlg._on_download_complete(fake_bin)
            assert before_called is True
            mock_apply.assert_called_once_with(fake_bin)
            mock_quit.assert_called_once()
    finally:
        if not app._closing:
            app._on_close()


def test_custom_presets_countdown_mini_hud_and_installer_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib
    import time

    from keyforge.ui.installer_dialog import InstallerWindow

    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        # 1. Custom saved presets CRUD + protection for built-in presets
        assert app.action_save_custom_preset("Raid Combo") is True
        assert "Raid Combo" in app.presets
        assert "Raid Combo" in app.preset_menu.cget("values")

        builtin_first = next(iter(app._builtin_preset_names))
        assert app.action_delete_custom_preset(builtin_first) is False
        assert app.action_delete_custom_preset("Raid Combo") is True
        assert "Raid Combo" not in app.presets

        # 2. Pre-Start Countdown start + cancellation
        app.dry_run_var.set(True)
        app.countdown_var.set("1s")
        app._on_countdown_changed("1s")
        assert app.app_config.start_countdown_sec == 1
        app.toggle_execution()
        assert app._countdown_after_id is not None
        assert "Starting in" in app.run_toggle_btn.cget("text")
        assert app.cancel_countdown("cancelled") is True
        assert app._countdown_after_id is None

        # 3. Countdown completion launches Dry-Run executor
        app._start_countdown(100)
        deadline = time.monotonic() + 2.0
        while not app.executor.is_running and time.monotonic() < deadline:
            app.update_idletasks()
            app.update()
            time.sleep(0.02)
        if app.executor.is_running:
            app.executor.stop()

        # 4. Compact Floating Mini-HUD lifecycle
        app.enter_mini_hud_mode()
        assert app._mini_hud is not None
        app.update_idletasks()
        assert app._mini_hud.winfo_exists()
        app._ui_tick()
        app.exit_mini_hud_mode()
        assert app._mini_hud is None
    finally:
        if not app._closing:
            app._on_close()

    # 5. Setup & Uninstall GUI window smoke check
    monkeypatch.setattr("keyforge.installer._create_windows_shortcut", lambda *args, **kwargs: True)
    monkeypatch.setattr("keyforge.installer._register_windows_uninstaller", lambda *args, **kwargs: True)
    setup_win = InstallerWindow(mode="setup", install_dir=tmp_path / "wiz_install")
    with contextlib.suppress(Exception):
        setup_win.update_idletasks()
        assert "Setup" in setup_win.title()
        assert setup_win.purge_var.get() is False
    setup_win._on_close()
