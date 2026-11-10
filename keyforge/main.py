"""CLI and Desktop GUI entry point for KeyForge."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from keyforge import __version__
from keyforge.engine.backend import DryRunBackend
from keyforge.engine.executor import MacroExecutor
from keyforge.engine.win32_input import Win32InputBackend
from keyforge.models.dsl import format_actions_to_dsl, parse_quick_expression
from keyforge.models.profile import MacroProfile, RepeatMode, create_default_profile
from keyforge.security.auditor import audit_profile
from keyforge.security.integrity import load_profile_secure, save_profile_secure


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="keyforge",
        description=(
            "KeyForge — A modern, secure, FOSS visual keyboard & mouse macro builder and auto-clicker for Windows."
        ),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"KeyForge v{__version__}",
    )
    parser.add_argument(
        "--expr",
        type=str,
        default=None,
        help="Run a Quick-Forge expression directly (e.g. 'Press E -> Wait 200ms -> Click -> Press 1').",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Path to a .json / .kfmacro profile file to execute or audit.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of repeat cycles when running via --expr (default: 1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution using DryRunBackend without sending real OS inputs.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Run static security & operational safety audit on the specified profile or expression.",
    )
    parser.add_argument(
        "--export-default",
        type=str,
        default=None,
        help="Export the default signed starter profile to the given file path and exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main entrypoint supporting both Desktop GUI and headless CLI modes."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # 1. Export default profile
    if args.export_default:
        profile = create_default_profile()
        target = save_profile_secure(profile, args.export_default)
        print(f"Exported signed default profile to: {target}")
        return 0

    # 2. Headless CLI mode (--expr, --profile, or --audit)
    if args.expr or args.profile or args.audit:
        try:
            if args.profile:
                profile, sig_ok = load_profile_secure(args.profile, require_signature=False)
                sig_status = "SHA-256 Verified" if sig_ok else "Unsigned (Schema Validated)"
                print(f"Loaded Profile: {profile.name} [{sig_status}]")
            elif args.expr:
                steps = parse_quick_expression(args.expr)
                profile = MacroProfile(
                    name="CLI Expression Macro",
                    description=args.expr,
                    repeat_mode=RepeatMode.COUNT,
                    repeat_count=max(1, args.repeat),
                    inter_cycle_delay_ms=10,
                    actions=steps,
                )
            else:
                profile = create_default_profile()

            report = audit_profile(profile)
            print(f"Security Audit: {report.badge_summary()}")
            for finding in report.findings:
                print(f"  - [{finding.code}] ({finding.severity.value.upper()}) {finding.title}: {finding.description}")

            if args.audit:
                return 1 if report.has_critical else 0

            backend = DryRunBackend() if args.dry_run else Win32InputBackend()
            time_scale = 0.01 if args.dry_run else 1.0
            executor = MacroExecutor(backend=backend, time_scale=time_scale)

            print(f"Executing: {format_actions_to_dsl(profile.actions)}")
            telem = executor.run_blocking(profile)
            print(
                f"Completed: state={telem.state.value}, cycles={telem.cycles_completed}, "
                f"actions={telem.actions_executed}, elapsed={telem.elapsed_seconds:.3f}s"
            )
            return 0 if telem.state.value == "idle" else 1
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    # 3. Default: Launch Desktop GUI
    from keyforge.ui.app import KeyForgeApp

    app = KeyForgeApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
