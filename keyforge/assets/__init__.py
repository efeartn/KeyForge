"""Bundled graphical assets and cross-platform PyInstaller-aware icon path resolvers."""

from __future__ import annotations

import sys
from pathlib import Path


def get_assets_dir() -> Path:
    """Return the directory containing bundled KeyForge graphical assets."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        frozen_dir = Path(meipass) / "keyforge" / "assets"
        if frozen_dir.exists():
            return frozen_dir
    return Path(__file__).resolve().parent


def get_asset_path(filename: str) -> Path:
    """Resolve an asset file by name inside the KeyForge assets bundle."""
    return get_assets_dir() / filename


def get_icon_png_path() -> Path:
    """Return the path to the 512x512 RGBA PNG application icon."""
    return get_asset_path("icon.png")


def get_icon_ico_path() -> Path:
    """Return the path to the multi-resolution Windows .ico application icon."""
    return get_asset_path("icon.ico")
