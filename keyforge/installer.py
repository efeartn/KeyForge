"""Cross-platform installation, shortcut management, and uninstallation helpers for KeyForge."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from keyforge import __version__
from keyforge.config import get_config_dir

_UNINSTALL_REG_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\KeyForge"


def get_default_install_dir() -> Path:
    """Return the default per-user installation directory for KeyForge."""
    env_dir = os.environ.get("KEYFORGE_INSTALL_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()

    if sys.platform == "win32" or os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else (Path.home() / "AppData" / "Local")
        return base / "Programs" / "KeyForge"

    return Path.home() / ".local" / "share" / "KeyForge"


def is_running_as_setup_installer(argv0: str | None = None) -> bool:
    """Return True if launched from a compiled standalone Setup installer binary."""
    if not getattr(sys, "frozen", False):
        return False
    candidate = argv0 or sys.executable
    return "setup" in Path(candidate).stem.lower()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _resolve_powershell_executable() -> str:
    if os.name != "nt":
        return "powershell.exe"
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(sys_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"


def _get_windows_shortcut_paths() -> tuple[Path, Path]:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "KeyForge.lnk"
    app_data = os.environ.get("APPDATA", "").strip()
    roaming = Path(app_data) if app_data else (Path.home() / "AppData" / "Roaming")
    start_menu = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "KeyForge.lnk"
    return desktop, start_menu


def _create_windows_shortcut(shortcut_path: Path, target_exe: Path, description: str) -> bool:
    if os.name != "nt":
        return False
    try:
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        ps_script = "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "$ws = New-Object -ComObject WScript.Shell",
                f"$sc = $ws.CreateShortcut({_ps_quote(str(shortcut_path))})",
                f"$sc.TargetPath = {_ps_quote(str(target_exe))}",
                f"$sc.WorkingDirectory = {_ps_quote(str(target_exe.parent))}",
                f"$sc.IconLocation = {_ps_quote(f'{target_exe},0')}",
                f"$sc.Description = {_ps_quote(description)}",
                "$sc.Save()",
            ]
        )
        encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        res = subprocess.run(  # noqa: S603
            [
                _resolve_powershell_executable(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            timeout=15,
            creationflags=flags,
            check=False,
        )
        return res.returncode == 0 and shortcut_path.exists()
    except Exception:
        return False


def _register_windows_uninstaller(target_dir: Path, installed_exe: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REG_SUBKEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "KeyForge")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, __version__)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "KeyForge Open Source")
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(installed_exe))
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(target_dir))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{installed_exe}" --uninstall')
            winreg.SetValueEx(
                key,
                "QuietUninstallString",
                0,
                winreg.REG_SZ,
                f'"{installed_exe}" --uninstall --silent',
            )
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        return True
    except Exception:
        return False


def _unregister_windows_uninstaller() -> None:
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REG_SUBKEY)


def _resolve_source_executable(source_exe: Path | None) -> Path:
    if source_exe is not None:
        return Path(source_exe)
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    dist_exe = Path(__file__).resolve().parent.parent / "dist" / "KeyForge-Windows-x64.exe"
    if dist_exe.is_file():
        return dist_exe
    return Path(sys.executable)


def _spawn_windows_dir_cleanup(target_dir: Path) -> None:
    ps_script = "\n".join(
        [
            "$ErrorActionPreference = 'SilentlyContinue'",
            "Start-Sleep -Milliseconds 1200",
            f"$dir = {_ps_quote(str(target_dir))}",
            "for ($i = 0; $i -lt 30; $i++) {",
            "    if (-not (Test-Path -LiteralPath $dir)) { break }",
            "    Remove-Item -LiteralPath $dir -Recurse -Force",
            "    if (-not (Test-Path -LiteralPath $dir)) { break }",
            "    Start-Sleep -Milliseconds 300",
            "}",
        ]
    )
    encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
    creation_flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )
    with contextlib.suppress(Exception):
        subprocess.Popen(  # noqa: S603
            [
                _resolve_powershell_executable(),
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )


def install_keyforge(
    install_dir: Path | None = None,
    source_exe: Path | None = None,
    create_desktop_shortcut: bool = True,
    create_start_menu_shortcut: bool = True,
    register_uninstaller: bool = True,
) -> dict[str, Any]:
    """Install KeyForge standalone binary into ``install_dir`` and register shortcuts/uninstaller."""
    target_dir = Path(install_dir).expanduser() if install_dir is not None else get_default_install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    exe_name = "KeyForge.exe" if os.name == "nt" else "KeyForge"
    installed_exe = target_dir / exe_name
    resolved_source = _resolve_source_executable(source_exe)

    if resolved_source.is_file():
        if resolved_source.resolve() != installed_exe.resolve():
            shutil.copy2(resolved_source, installed_exe)
    elif not installed_exe.exists():
        installed_exe.write_bytes(b"")

    desktop_lnk, start_menu_lnk = _get_windows_shortcut_paths()
    desktop_created: str | None = None
    start_menu_created: str | None = None

    if os.name == "nt":
        if create_desktop_shortcut and _create_windows_shortcut(
            desktop_lnk, installed_exe, f"KeyForge v{__version__} Macro Studio"
        ):
            desktop_created = str(desktop_lnk)
        if create_start_menu_shortcut and _create_windows_shortcut(
            start_menu_lnk, installed_exe, f"KeyForge v{__version__} Macro Studio"
        ):
            start_menu_created = str(start_menu_lnk)
        if register_uninstaller:
            _register_windows_uninstaller(target_dir, installed_exe)

    manifest: dict[str, Any] = {
        "version": __version__,
        "installed_exe": str(installed_exe),
        "desktop_shortcut": desktop_created,
        "start_menu_shortcut": start_menu_created,
        "config_dir": str(get_config_dir()),
    }
    manifest_path = target_dir / "install_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def uninstall_keyforge(
    install_dir: Path | None = None,
    purge_user_data: bool = False,
    spawn_self_delete: bool = True,
) -> dict[str, Any]:
    """Uninstall KeyForge binaries/shortcuts/registry, keeping user data intact by default."""
    target_dir = Path(install_dir).expanduser() if install_dir is not None else get_default_install_dir()
    manifest_path = target_dir / "install_manifest.json"

    shortcut_candidates: list[Path] = []
    if manifest_path.is_file():
        with contextlib.suppress(Exception):
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("desktop_shortcut", "start_menu_shortcut"):
                    raw = data.get(key)
                    if isinstance(raw, str) and raw.strip():
                        shortcut_candidates.append(Path(raw))

    if os.name == "nt":
        desktop_lnk, start_menu_lnk = _get_windows_shortcut_paths()
        shortcut_candidates.extend([desktop_lnk, start_menu_lnk])

    for lnk in shortcut_candidates:
        with contextlib.suppress(Exception):
            if lnk.exists() or lnk.is_symlink():
                lnk.unlink()

    _unregister_windows_uninstaller()

    if purge_user_data:
        config_dir = get_config_dir()
        if config_dir.exists():
            shutil.rmtree(config_dir, ignore_errors=True)

    running_from_target = False
    if getattr(sys, "frozen", False):
        with contextlib.suppress(Exception):
            running_from_target = Path(sys.executable).resolve().parent == target_dir.resolve()

    if running_from_target and spawn_self_delete and os.name == "nt":
        _spawn_windows_dir_cleanup(target_dir)
    elif target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    return {
        "uninstalled_dir": str(target_dir),
        "purged_user_data": bool(purge_user_data),
    }
