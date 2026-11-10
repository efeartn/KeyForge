"""Cross-platform PyInstaller build script for Windows (.exe), macOS, and Linux standalone binaries."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path


def get_binary_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        return "KeyForge-Windows-x64"
    if system == "darwin":
        arch = "arm64" if "arm" in machine or "aarch64" in machine else "x64"
        return f"KeyForge-macOS-{arch}"
    return "KeyForge-Linux-x64"


def build_executable() -> int:
    root = Path(__file__).resolve().parent.parent
    entry = root / "keyforge" / "main.py"
    assets_dir = root / "keyforge" / "assets"
    icon_ico = assets_dir / "icon.ico"
    icon_png = assets_dir / "icon.png"
    dist_dir = root / "dist"
    bin_name = get_binary_name()

    sep = ";" if os.name == "nt" else ":"
    add_data_arg = f"{assets_dir}{sep}keyforge/assets"
    icon_file = icon_ico if sys.platform == "win32" else icon_png

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        bin_name,
        "--collect-all",
        "customtkinter",
        "--add-data",
        add_data_arg,
    ]
    if icon_file.exists():
        cmd.extend(["--icon", str(icon_file)])
    cmd.append(str(entry))

    print("Running PyInstaller:", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(root))  # noqa: S603
    if rc != 0:
        return rc

    # Locate built binary and generate SHA-256 checksum file
    suffix = ".exe" if sys.platform == "win32" else ""
    built_binary = dist_dir / f"{bin_name}{suffix}"
    if built_binary.exists():
        digest = hashlib.sha256(built_binary.read_bytes()).hexdigest()
        checksum_line = f"{digest}  {built_binary.name}\n"
        checksum_file = dist_dir / f"{bin_name}.sha256.txt"
        checksum_file.write_text(checksum_line, encoding="utf-8")
        print(f"Built artifact: {built_binary} ({built_binary.stat().st_size} bytes)")
        print(f"SHA-256: {checksum_line.strip()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(build_executable())
