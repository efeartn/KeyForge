"""Tests for KeyForge CLI, version parity, and Desktop GUI controller operations."""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from keyforge import __version__
from keyforge.assets import get_icon_ico_path, get_icon_png_path
from keyforge.main import main
from keyforge.models.action import (
    ActionType,
    ClickType,
    MouseButton,
    make_mouse_click,
    make_wait,
)
from keyforge.models.profile import RepeatMode
from keyforge.ui.theme import BTN_NEUTRAL, CTA_SUCCESS
from keyforge.updater import USER_AGENT, ReleaseInfo, UpdateCheckError


def test_bundled_app_icon_assets() -> None:
    png_path = get_icon_png_path()
    ico_path = get_icon_ico_path()
    assert png_path.exists()
    assert ico_path.exists()

    with Image.open(png_path) as png_img:
        assert png_img.size == (512, 512)
        assert png_img.mode == "RGBA"

    with Image.open(ico_path) as ico_img:
        assert ico_img.format == "ICO"


def test_cli_export_audit_and_dry_run(tmp_path: Path) -> None:
    profile_path = tmp_path / "cli_default.kfmacro.json"

    # 1. Export default signed profile
    rc_export = main(["--export-default", str(profile_path)])
    assert rc_export == 0
    assert profile_path.exists()

    # 2. Audit exported profile
    rc_audit = main(["--profile", str(profile_path), "--audit"])
    assert rc_audit == 0

    # 3. Run expression with --repeat 2 in dry-run mode
    rc_expr = main(
        [
            "--expr",
            "Press E -> Wait 200ms -> Click -> Press 1",
            "--repeat",
            "2",
            "--dry-run",
        ]
    )
    assert rc_expr == 0

    # 4. Critical security audit returns non-zero exit code
    rc_crit = main(["--expr", "Press Win+R -> Wait 100ms", "--audit"])
    assert rc_crit == 1

    # 5. Verify windowless (--noconsole / pythonw) execution when sys.stdout/stderr are None
    import sys

    orig_out, orig_err = sys.stdout, sys.stderr
    try:
        sys.stdout = None  # type: ignore[assignment]
        sys.stderr = None  # type: ignore[assignment]
        rc_noconsole = main(["--expr", "Press E -> Wait 50ms", "--repeat", "1", "--dry-run"])
        assert rc_noconsole == 0
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err


def test_gui_controller_lifecycle(tmp_path: Path) -> None:
    from keyforge.ui.app import KeyForgeApp

    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        app.update_idletasks()
        assert len(app.profile.actions) == 5

        # Test Quick-Forge expression replacement
        app.dsl_entry.delete(0, "end")
        app.dsl_entry.insert(0, "Press E -> Wait 200ms -> Click -> Press 1")
        assert app.apply_quick_expression(append=False) is True
        assert len(app.profile.actions) == 4

        # Test step manipulation (add, duplicate, move up/down, delete)
        app.add_step(make_wait(50))
        assert len(app.profile.actions) == 5
        app.move_step_up(4)
        assert app.profile.actions[3].wait_ms == 50
        app.move_step_down(3)
        assert app.profile.actions[4].wait_ms == 50
        app.duplicate_step(0)
        assert len(app.profile.actions) == 6
        app.delete_step(0)
        assert len(app.profile.actions) == 5

        # Test saving and reloading signed profile from UI controller
        save_file = tmp_path / "ui_saved.kfmacro.json"
        app.save_profile_to_path(save_file)
        app.clear_all_steps()
        assert len(app.profile.actions) == 0

        app.load_profile_from_path(save_file)
        assert len(app.profile.actions) == 5

        # Test Dry-Run execution and Panic Kill-Switch through UI controller
        app.dry_run_var.set(True)
        app.toggle_execution()
        app.trigger_panic_stop()
        assert app.executor.is_running is False

        # Test StepEditorDialog across all ActionTypes
        from keyforge.models.action import ActionType
        from keyforge.ui.step_dialog import StepEditorDialog

        saved_from_dialog = []
        dlg = StepEditorDialog(
            app,
            initial_action=app.profile.actions[0],
            on_save=saved_from_dialog.append,
        )
        for atype in ActionType:
            dlg._on_type_changed(atype.value)
        dlg._populate_from_action(app.profile.actions[0])
        dlg._on_submit()
        assert len(saved_from_dialog) == 1

        # Test new Studio UI features: template chips, filter, bulk enable/disable, reverse, undo/redo, tempo scaling
        assert app.icon_applied is True
        app._apply_template_chip("Press E -> Wait 200ms -> Click -> Press 1")
        assert len(app.profile.actions) == 4
        app.scale_wait_times(0.5)
        assert app.profile.actions[1].wait_ms == 100
        app.scale_wait_times(2.0)
        assert app.profile.actions[1].wait_ms == 200
        app.set_all_steps_enabled(False)
        assert all(not a.enabled for a in app.profile.actions)
        app.set_all_steps_enabled(True)
        assert all(a.enabled for a in app.profile.actions)
        app.reverse_steps()
        assert app.profile.actions[0].key == "1"
        assert app.undo_step_change() is True
        assert app.profile.actions[0].key == "E"
        assert app.redo_step_change() is True
        assert app.profile.actions[0].key == "1"
        app.filter_entry.insert(0, "Wait")
        app._on_filter_changed()
    finally:
        app._on_close()
        assert app._closing is True
        for timer_attr in (
            "_ui_tick_after_id",
            "_startup_update_after_id",
            "_startup_update_poll_after_id",
            "_hotkey_poll_after_id",
            "_filter_after_id",
            "_simple_slider_after_id",
            "_toast_after_id",
            "_countdown_after_id",
        ):
            assert getattr(app, timer_attr) is None
        assert getattr(app, "_kf_icon_after_id", None) is None


def _run_mainloop_until(app: Any, predicate: Any, timeout_ms: int = 3000) -> bool:
    """Drive ``app.mainloop()`` until ``predicate()`` holds or ``timeout_ms`` elapses."""
    if predicate():
        return True

    exited = False
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    pending_ids: list[str] = []

    def _poll() -> None:
        if exited:
            return
        if predicate() or time.monotonic() >= deadline:
            app.quit()
            # Re-post quit() in case CustomTkinter runs a nested update() loop that swallows one quit().
            pending_ids.append(app.after(20, _poll))
            return
        pending_ids.append(app.after(15, _poll))

    pending_ids.append(app.after(15, _poll))
    try:
        app.mainloop()
    finally:
        exited = True
        for aid in pending_ids:
            with contextlib.suppress(Exception):
                app.after_cancel(aid)
    return bool(predicate())


def test_version_parity_and_cli_version_flag() -> None:
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    pyproject_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project_version = pyproject_data["project"]["version"]

    assert __version__ == "1.3.0"
    assert __version__ == project_version
    assert USER_AGENT == f"KeyForge/{__version__}"

    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "keyforge", "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert proc.stdout.strip() == f"KeyForge v{__version__}"


def test_simple_and_studio_bidirectional_profile_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    from keyforge.ui.app import KeyForgeApp

    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        app.update_idletasks()
        assert app.ui_mode_var.get() == "Simple"

        # 1. Configure Key Spammer + Count repeat in Simple Mode -> switch to Studio Mode
        app.simple_task_var.set("Key Spammer")
        app._on_simple_task_changed("Key Spammer")
        assert app.ss_panel.winfo_manager() != ""

        app.ss_key_combo.set("F")
        app.ss_interval_slider.set(75)
        app.ss_jitter_var.set(False)
        app._apply_simple_spammer_to_profile()

        app.simple_repeat_mode_var.set("Count")
        app._on_simple_repeat_mode_changed("Count")
        app.simple_repeat_val_entry.delete(0, "end")
        app.simple_repeat_val_entry.insert(0, "4")
        app._on_simple_repeat_val_commit()

        app._switch_ui_mode("Studio")
        assert app.studio_frame.winfo_manager() != ""
        assert app.simple_frame.winfo_manager() == ""
        assert app.profile.actions[0].action_type == ActionType.KEY_PRESS
        assert app.profile.actions[0].key == "F"
        assert app.profile.actions[1].wait_ms == 50
        assert app.profile.repeat_mode == RepeatMode.COUNT
        assert app.profile.repeat_count == 4
        assert app.repeat_val_entry.get().strip() == "4"

        # 2. Edit a multi-step sequence + Duration repeat in Studio -> switch back to Simple (infers Quick Sequence)
        app.dsl_entry.delete(0, "end")
        app.dsl_entry.insert(0, "Press E -> Wait 150ms -> Click -> Press 1")
        assert app.apply_quick_expression(append=False) is True
        app.repeat_mode_var.set(RepeatMode.DURATION.value)
        app.repeat_val_entry.delete(0, "end")
        app.repeat_val_entry.insert(0, "12")
        assert app.sync_ui_settings_to_profile() is True

        app._switch_ui_mode("Simple")
        assert app.simple_frame.winfo_manager() != ""
        assert app.sq_panel.winfo_manager() != ""
        assert app.simple_task_var.get() == "Quick Sequence"
        assert app.sq_dsl_entry.get().strip() == "Press E -> Wait 150ms -> Click -> Press 1"
        assert app.simple_repeat_mode_var.get() == "Duration"
        assert float(app.simple_repeat_val_entry.get().strip()) == 12.0

        # 3. Author a 2-step clicker pattern in Studio -> switch to Simple (infers Auto-Clicker + Fixed XY)
        app._switch_ui_mode("Studio")
        app.profile.actions = [
            make_mouse_click(MouseButton.RIGHT, ClickType.DOUBLE, x=320, y=240, hold_ms=15),
            make_wait(185, jitter_ms=10),
        ]
        app._on_sequence_mutated(sync_simple=False)
        app._switch_ui_mode("Simple")
        assert app.sc_panel.winfo_manager() != ""
        assert app.sc_button_var.get() == "Right"
        assert app.sc_click_type_var.get() == "Double"
        assert app.sc_loc_mode_var.get() == "Fixed XY"
        assert app.sc_x_entry.get().strip() == "320"
        assert app.sc_y_entry.get().strip() == "240"
        assert app.sc_jitter_var.get() is True
        assert int(round(app.sc_interval_slider.get())) == 200
    finally:
        app._on_close()


def test_debounced_filter_and_simple_slider_timers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    from keyforge.ui.app import KeyForgeApp

    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        # 1. Rapid Simple Auto-Clicker slider moves cancel previous timer and flush cleanly
        with patch.object(app, "after_cancel", wraps=app.after_cancel) as spy_cancel:
            app.sc_interval_slider.set(120)
            app._on_sc_slider_moved(120)
            first_id = app._simple_slider_after_id
            assert first_id is not None

            app.sc_interval_slider.set(160)
            app._on_sc_slider_moved(160)
            app.sc_interval_slider.set(250)
            app._on_sc_slider_moved(250)

            assert spy_cancel.call_count == 2
            assert app._simple_slider_after_id is not None
            assert app.sc_interval_lbl.cget("text") == "250 ms (4.0 CPS)"

        # Flush clicker timer manually & verify timer ID cleared + profile updated
        app._commit_simple_slider_change("clicker")
        assert app._simple_slider_after_id is None
        assert app.profile.actions[0].action_type == ActionType.MOUSE_CLICK
        assert app.profile.actions[0].hold_ms + app.profile.actions[1].wait_ms == 250

        # 2. Rapid Key Spammer slider moves execute real debounced after(80, ...) callback once
        with patch.object(app, "after_cancel", wraps=app.after_cancel) as spy_cancel:
            app.ss_key_combo.set("Q")
            app.ss_interval_slider.set(95)
            app._on_ss_slider_moved(95)
            app.ss_interval_slider.set(125)
            app._on_ss_slider_moved(125)
            assert spy_cancel.call_count == 1
            assert app._simple_slider_after_id is not None

        assert _run_mainloop_until(app, lambda: app._simple_slider_after_id is None, timeout_ms=2000) is True
        assert app.profile.actions[0].action_type == ActionType.KEY_PRESS
        assert app.profile.actions[0].key == "Q"
        assert app.profile.actions[0].hold_ms + app.profile.actions[1].wait_ms == 125

        # 3. Studio filter debouncing cancels prior timers and renders matching rows once
        app._switch_ui_mode("Studio")
        app._apply_template_chip("Press E -> Wait 200ms -> Click -> Press 1")
        assert len(app._step_row_frames) == 4

        with patch.object(app, "after_cancel", wraps=app.after_cancel) as spy_cancel:
            app.filter_entry.delete(0, "end")
            app.filter_entry.insert(0, "w")
            app._on_filter_changed(event=object())
            app.filter_entry.insert("end", "ait")
            app._on_filter_changed(event=object())
            assert spy_cancel.call_count == 1
            assert app._filter_after_id is not None

        assert _run_mainloop_until(app, lambda: app._filter_after_id is None, timeout_ms=2000) is True
        assert app._filter_query == "wait"
        assert len(app._step_row_frames) == 1

        # 4. Pending timers are cancelled on close & debounced callbacks no-op safely when closing
        app._on_sc_slider_moved(300)
        app._on_filter_changed(event=object())
        assert app._simple_slider_after_id is not None
        assert app._filter_after_id is not None

        app._on_close()
        assert app._simple_slider_after_id is None
        assert app._filter_after_id is None
        app._commit_simple_slider_change("clicker")
        app._apply_filter_debounced()
    finally:
        if not app._closing:
            app._on_close()


def test_dirty_checked_ui_tick_avoids_redundant_widget_configures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    from keyforge.ui.app import KeyForgeApp

    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        app._ui_tick()

        with (
            patch.object(app.status_lbl, "configure", wraps=app.status_lbl.configure) as spy_status,
            patch.object(app.simple_status_lbl, "configure", wraps=app.simple_status_lbl.configure) as spy_simple_st,
            patch.object(app.cycles_lbl, "configure", wraps=app.cycles_lbl.configure) as spy_cycles,
            patch.object(app.simple_telem_lbl, "configure", wraps=app.simple_telem_lbl.configure) as spy_simple_tel,
            patch.object(app.mode_sub_lbl, "configure", wraps=app.mode_sub_lbl.configure) as spy_mode,
            patch.object(app.actions_lbl, "configure", wraps=app.actions_lbl.configure) as spy_actions,
            patch.object(app.run_toggle_btn, "configure", wraps=app.run_toggle_btn.configure) as spy_run_btn,
            patch.object(app.simple_run_btn, "configure", wraps=app.simple_run_btn.configure) as spy_simple_run,
        ):
            # Identical state -> dirty-checking must emit ZERO redundant widget .configure calls
            app._ui_tick()
            for spy in (
                spy_status,
                spy_simple_st,
                spy_cycles,
                spy_simple_tel,
                spy_mode,
                spy_actions,
                spy_run_btn,
                spy_simple_run,
            ):
                assert spy.call_count == 0

            # Mutating dry_run_var updates ONLY mode_sub_lbl
            app.dry_run_var.set(True)
            app._ui_tick()
            assert spy_mode.call_count == 1
            assert "Dry-Run Simulation" in app.mode_sub_lbl.cget("text")
            assert spy_status.call_count == 0
            assert spy_cycles.call_count == 0
            assert spy_run_btn.call_count == 0
    finally:
        app._on_close()


def test_startup_update_worker_and_closing_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    from keyforge.ui.app import KeyForgeApp

    newer_release = ReleaseInfo(
        tag_name="v9.9.9",
        version="9.9.9",
        name="KeyForge v9.9.9",
        body="New release notes",
        html_url="https://github.com/efeartn/KeyForge/releases/tag/v9.9.9",
        asset_name="KeyForge-Windows-x64.exe",
        download_url="https://github.com/efeartn/KeyForge/releases/download/v9.9.9/KeyForge-Windows-x64.exe",
        sha256="a" * 64,
        is_update_available=True,
    )
    uptodate_release = ReleaseInfo(
        tag_name=f"v{__version__}",
        version=__version__,
        is_update_available=False,
    )

    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        # 1. Network error inside _startup_update_worker touches ZERO widget methods and shows no banner
        monkeypatch.setattr(
            "keyforge.ui.app.fetch_latest_release",
            lambda **kwargs: (_ for _ in ()).throw(UpdateCheckError("offline", reason="offline")),
        )
        with (
            patch.object(app, "after", side_effect=AssertionError("worker called app.after")) as spy_app_after,
            patch.object(
                app, "winfo_exists", side_effect=AssertionError("worker called app.winfo_exists")
            ) as spy_app_exists,
        ):
            t_err = threading.Thread(target=app._startup_update_worker, daemon=True)
            t_err.start()
            t_err.join(timeout=3.0)
            assert not t_err.is_alive()
            app._enqueue_hotkey_action("start_stop")
            app._enqueue_panic_hotkey()
            assert spy_app_after.call_count == 0
            assert spy_app_exists.call_count == 0

        app._drain_startup_update_queue()
        app.update()
        assert app._latest_release is None
        assert app.update_banner.winfo_manager() == ""

        # 2. Newer ReleaseInfo via real _check_startup_updates thread highlights UI and packs banner
        monkeypatch.setattr("keyforge.ui.app.fetch_latest_release", lambda **kwargs: newer_release)
        with (
            patch.object(
                app,
                "winfo_exists",
                wraps=app.winfo_exists,
            ),
        ):
            app._check_startup_updates()
            assert _run_mainloop_until(app, lambda: app._latest_release is not None, timeout_ms=2500) is True
        assert "9.9.9" in app.updates_btn.cget("text")
        assert app.updates_btn.cget("fg_color") == CTA_SUCCESS
        assert "9.9.9" in app.simple_update_link.cget("text")
        assert app.update_banner.winfo_manager() != ""

        # 3. Hotkey worker callback off main thread only pushes to _hotkey_queue; main thread drains safely
        with (
            patch.object(app, "after", side_effect=AssertionError("hotkey worker called app.after")) as spy_hk_after,
            patch.object(
                app, "winfo_exists", side_effect=AssertionError("hotkey worker called app.winfo_exists")
            ) as spy_hk_exists,
        ):
            t_hk = threading.Thread(target=lambda: app._enqueue_hotkey_action("start_stop"), daemon=True)
            t_hk.start()
            t_hk.join(timeout=3.0)
            assert not t_hk.is_alive()
            assert spy_hk_after.call_count == 0
            assert spy_hk_exists.call_count == 0
        with patch.object(app, "toggle_execution") as mock_toggle:
            app._drain_hotkey_queue()
            assert mock_toggle.call_count >= 1

        # 4. Subsequent up-to-date check resets updates_btn & hides banner
        app._on_release_checked(uptodate_release)
        assert app.updates_btn.cget("text") == "Updates"
        assert app.updates_btn.cget("fg_color") == BTN_NEUTRAL
        assert app.update_banner.winfo_manager() == ""

        # 5. Closing mid-flight ignores late queued updates cleanly and cancels poll timers
        app._on_close()
        assert app._startup_update_poll_after_id is None
        assert app._hotkey_poll_after_id is None
        app._latest_release = None
        app._startup_update_queue.put(newer_release)
        app._drain_startup_update_queue()
        assert app._latest_release is None
    finally:
        if not app._closing:
            app._on_close()


def test_update_dialog_close_mid_download_and_error_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    from keyforge.ui.app import KeyForgeApp
    from keyforge.ui.update_dialog import UpdateDialog

    release = ReleaseInfo(
        tag_name="v9.9.9",
        version="9.9.9",
        name="KeyForge v9.9.9",
        body="Update test release",
        html_url="https://github.com/efeartn/KeyForge/releases/tag/v9.9.9",
        asset_name="KeyForge-Windows-x64.exe",
        download_url="https://github.com/efeartn/KeyForge/releases/download/v9.9.9/KeyForge-Windows-x64.exe",
        asset_size=12,
        sha256="c" * 64,
        is_update_available=True,
    )

    class _StreamingStubResp:
        def __init__(self, chunk1_seen: threading.Event, allow_next: threading.Event, url: str) -> None:
            self._chunk1_seen = chunk1_seen
            self._allow_next = allow_next
            self._url = url
            self._calls = 0
            self.headers = {"Content-Length": "12"}

        def read(self, amt: int = -1) -> bytes:
            self._calls += 1
            if self._calls == 1:
                return b"CHUNK1"
            if self._calls == 2:
                self._chunk1_seen.set()
                self._allow_next.wait(timeout=3.0)
                return b"CHUNK2"
            return b""

        def geturl(self) -> str:
            return self._url

        def close(self) -> None:
            pass

    thread_errors: list[BaseException] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: thread_errors.append(args.exc_value))

    app = KeyForgeApp(start_hotkey_listener=False)
    try:
        # 1. Check Again error handling renders friendly failure UI
        dlg_err = UpdateDialog(app, initial_release=release)
        monkeypatch.setattr(
            "keyforge.ui.update_dialog.fetch_latest_release",
            lambda **kwargs: (_ for _ in ()).throw(
                UpdateCheckError("Rate limit exceeded", reason="rate_limited", status_code=429)
            ),
        )
        dlg_err.check_for_updates()
        assert _run_mainloop_until(app, lambda: not dlg_err._is_checking, timeout_ms=2500) is True
        assert "Update Check Failed" in dlg_err.status_title_lbl.cget("text")
        assert dlg_err.sha_pill.cget("text") == "Check Error"
        dlg_err._on_close()

        # 2. Cancel button mid-download aborts cleanly & removes .downloading
        dlg_cancel = UpdateDialog(app, initial_release=release)
        c1_seen = threading.Event()
        allow_c = threading.Event()
        cancel_clicked = False
        monkeypatch.setattr(
            "keyforge.updater._open_https",
            lambda url, timeout=30.0: (_StreamingStubResp(c1_seen, allow_c, url), url),
        )
        dlg_cancel.start_download_and_install()

        def _drive_cancel() -> bool:
            nonlocal cancel_clicked
            if not cancel_clicked and c1_seen.is_set():
                cancel_clicked = True
                dlg_cancel._on_secondary_clicked()
                assert dlg_cancel._cancel_requested is True
                allow_c.set()
            return cancel_clicked and not dlg_cancel._is_downloading

        assert _run_mainloop_until(app, _drive_cancel, timeout_ms=3000) is True
        assert dlg_cancel.progress_lbl.cget("text") == "Download cancelled."
        assert dlg_cancel.secondary_btn.cget("text") == "Close"
        assert not (tmp_path / "updates" / "KeyForge-Windows-x64.exe.downloading").exists()
        dlg_cancel._on_close()

        # 3. Closing UpdateDialog mid-download causes ZERO TclError and cleans .downloading
        dlg_close = UpdateDialog(app, initial_release=release)
        c2_seen = threading.Event()
        allow_close = threading.Event()
        closed_mid_download = False
        dl_threads: list[threading.Thread] = []
        monkeypatch.setattr(
            "keyforge.updater._open_https",
            lambda url, timeout=30.0: (_StreamingStubResp(c2_seen, allow_close, url), url),
        )
        dlg_close.start_download_and_install()

        def _drive_close_mid_download() -> bool:
            nonlocal closed_mid_download
            if not closed_mid_download and c2_seen.is_set():
                closed_mid_download = True
                dl_threads.extend(t for t in threading.enumerate() if t.name == "KeyForge-UpdateDownload")
                dlg_close._on_close()
                allow_close.set()
            return closed_mid_download and all(not t.is_alive() for t in dl_threads)

        assert _run_mainloop_until(app, _drive_close_mid_download, timeout_ms=3000) is True
        app.update()

        assert dlg_close._closed is True
        assert dlg_close._cancel_requested is True
        assert dlg_close._poll_after_id is None
        assert not (tmp_path / "updates" / "KeyForge-Windows-x64.exe").exists()
        assert not (tmp_path / "updates" / "KeyForge-Windows-x64.exe.downloading").exists()

        # 4. Background workers in UpdateDialog perform ZERO direct widget/after/winfo calls
        dlg_iso = UpdateDialog(app, initial_release=release)
        monkeypatch.setattr("keyforge.ui.update_dialog.fetch_latest_release", lambda **kwargs: release)
        fake_dl_path = tmp_path / "updates" / "KeyForge-Windows-x64.exe"

        def _fake_download(*, progress_callback: Any, **kwargs: Any) -> Path:
            progress_callback(512, 1024)
            fake_dl_path.parent.mkdir(parents=True, exist_ok=True)
            fake_dl_path.write_bytes(b"MZ")
            return fake_dl_path

        monkeypatch.setattr("keyforge.ui.update_dialog.download_update_asset", _fake_download)
        with (
            patch.object(dlg_iso, "after", side_effect=AssertionError("worker called dlg.after")) as spy_dlg_after,
            patch.object(
                dlg_iso, "winfo_exists", side_effect=AssertionError("worker called dlg.winfo_exists")
            ) as spy_dlg_exists,
            patch.object(app, "after", side_effect=AssertionError("worker called app.after")) as spy_parent_after,
            patch.object(
                app, "winfo_exists", side_effect=AssertionError("worker called app.winfo_exists")
            ) as spy_parent_exists,
        ):
            t_chk = threading.Thread(target=dlg_iso._check_worker, daemon=True)
            t_chk.start()
            t_chk.join(timeout=3.0)
            t_dl = threading.Thread(target=dlg_iso._download_worker, args=(release, fake_dl_path), daemon=True)
            t_dl.start()
            t_dl.join(timeout=3.0)
            assert not t_chk.is_alive()
            assert not t_dl.is_alive()
            assert spy_dlg_after.call_count == 0
            assert spy_dlg_exists.call_count == 0
            assert spy_parent_after.call_count == 0
            assert spy_parent_exists.call_count == 0

        dlg_iso._drain_ui_queue()
        assert dlg_iso._downloaded_path == fake_dl_path
        dlg_iso._on_close()
        assert dlg_iso._poll_after_id is None
        assert thread_errors == []
    finally:
        app._on_close()
