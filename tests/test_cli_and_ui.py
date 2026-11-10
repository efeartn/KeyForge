"""Tests for KeyForge CLI and Desktop GUI controller operations."""

from __future__ import annotations

from pathlib import Path

from keyforge.main import main
from keyforge.models.action import make_wait


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
    finally:
        app._on_close()
