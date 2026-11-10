"""Windowless Desktop GUI Launcher for KeyForge Studio (runs via pythonw.exe with no console window)."""

from __future__ import annotations

from keyforge.main import main

if __name__ == "__main__":
    raise SystemExit(main([]))
