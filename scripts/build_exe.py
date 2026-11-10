"""Build script to package KeyForge as a standalone single-file Windows executable (.exe)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def build_executable() -> int:
    root = Path(__file__).resolve().parent.parent
    entry = root / "keyforge" / "main.py"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        "KeyForge",
        "--collect-all",
        "customtkinter",
        str(entry),
    ]
    print("Running PyInstaller:", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(root))


if __name__ == "__main__":
    raise SystemExit(build_executable())
