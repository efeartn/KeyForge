"""Minimalist Apple-inspired design language, color palette, badge styles, and icon utilities for KeyForge."""

from __future__ import annotations

import contextlib
import ctypes
import sys
import tkinter as tk
from typing import Any

import customtkinter as ctk
from PIL import Image

from keyforge.assets import get_icon_ico_path, get_icon_png_path
from keyforge.models.action import ActionType

# Apple macOS / Human Interface Guidelines Surface Hierarchy (Space Black & Dark Titanium)
BG_ROOT = "#0C0E12"
BG_PANEL = "#15181F"
BG_ELEVATED = "#1B1F28"
BG_CARD = "#191D26"
BG_CARD_HOVER = "#222733"
BG_CARD_ACTIVE = "#162C4E"
BG_INPUT = "#0E1116"

BORDER_SUBTLE = "#262C38"
BORDER_FOCUS = "#0A84FF"

# Typography Hierarchy (Crisp High-Legibility)
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#98A2B3"
TEXT_MUTED = "#667085"

# Semantic Accents (Apple System Colors)
ACCENT_PRIMARY = "#0A84FF"  # Apple Blue
ACCENT_PRIMARY_HOVER = "#0071E3"
ACCENT_SUCCESS = "#30D158"  # Apple Green
ACCENT_SUCCESS_HOVER = "#24A143"
ACCENT_WARNING = "#FF9F0A"  # Apple Orange
ACCENT_WARNING_HOVER = "#D98200"
ACCENT_DANGER = "#FF453A"  # Apple Red
ACCENT_DANGER_HOVER = "#D93025"
ACCENT_PURPLE = "#BF5AF2"  # Apple Purple
ACCENT_PURPLE_HOVER = "#9F37D6"
ACCENT_CYAN = "#64D2FF"  # Apple Teal/Cyan

# Per-Action Type Visual Identity: (badge_bg, badge_fg, stripe_accent)
ACTION_VISUAL_STYLE: dict[ActionType, tuple[str, str, str]] = {
    ActionType.KEY_PRESS: ("#13233F", "#82B4FF", "#0A84FF"),  # System Blue
    ActionType.KEY_DOWN: ("#182F52", "#A8CBFF", "#5AC8FA"),  # Sky Blue
    ActionType.KEY_UP: ("#1C212A", "#D0D5DD", "#98A2B3"),  # Slate Titanium
    ActionType.MOUSE_CLICK: ("#102C1E", "#7EE7A6", "#30D158"),  # Mint Green
    ActionType.MOUSE_MOVE: ("#112938", "#7DD3FC", "#64D2FF"),  # Teal Cyan
    ActionType.MOUSE_SCROLL: ("#2C173E", "#E9D5FF", "#BF5AF2"),  # Violet
    ActionType.WAIT: ("#33210C", "#FDE68A", "#FF9F0A"),  # Amber
    ActionType.REPEAT_BLOCK: ("#36142A", "#FBCFE8", "#FF375F"),  # Rose Fuchsia
}


def get_badge_colors(action_type: ActionType) -> tuple[str, str]:
    """Return (background_hex, foreground_hex) for an ActionType badge."""
    bg, fg, _ = ACTION_VISUAL_STYLE.get(action_type, ("#1E293B", "#E2E8F0", "#3B82F6"))
    return (bg, fg)


def get_stripe_color(action_type: ActionType) -> str:
    """Return the left accent stripe hex color for an ActionType."""
    _, _, stripe = ACTION_VISUAL_STYLE.get(action_type, ("#1E293B", "#E2E8F0", "#3B82F6"))
    return stripe


def apply_window_icon(window: Any) -> bool:
    """Apply the bundled KeyForge application icon to a Tk / CTk window across Windows, macOS, and Linux."""
    applied = False
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KeyForge.Studio.App.1")

    ico_path = get_icon_ico_path()
    png_path = get_icon_png_path()

    if png_path.exists():
        with contextlib.suppress(Exception):
            photo = tk.PhotoImage(master=window, file=str(png_path))
            window._kf_icon_photo_ref = photo
            window.wm_iconphoto(True, photo)
            applied = True

    if sys.platform == "win32" and ico_path.exists():
        with contextlib.suppress(Exception):
            window.iconbitmap(default=str(ico_path))
            window.after(150, lambda: _safe_set_iconbitmap(window, str(ico_path)))
            applied = True

    return applied


def _safe_set_iconbitmap(window: Any, ico_str: str) -> None:
    with contextlib.suppress(Exception):
        if window.winfo_exists():
            window.iconbitmap(ico_str)


def load_brand_ctk_image(size: tuple[int, int] = (36, 36)) -> ctk.CTkImage | None:
    """Load the bundled KeyForge icon as a high-DPI CTkImage for header embedding."""
    png_path = get_icon_png_path()
    if not png_path.exists():
        return None
    with contextlib.suppress(Exception):
        pil_img = Image.open(png_path)
        return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)
    return None
