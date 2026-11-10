"""Modern Obsidian & Neon Studio color palette, badge styles, and icon utilities for KeyForge UI."""

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

# Core Obsidian & Slate Surface Hierarchy
BG_ROOT = "#070A10"
BG_PANEL = "#0F1521"
BG_ELEVATED = "#141C2B"
BG_CARD = "#172033"
BG_CARD_HOVER = "#1E2942"
BG_CARD_ACTIVE = "#1E3A5F"
BG_INPUT = "#0A0E17"

BORDER_SUBTLE = "#223049"
BORDER_FOCUS = "#3B82F6"

# Typography Hierarchy
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#94A3B8"
TEXT_MUTED = "#64748B"

# Semantic & Brand Accents
ACCENT_PRIMARY = "#3B82F6"
ACCENT_PRIMARY_HOVER = "#2563EB"
ACCENT_SUCCESS = "#10B981"
ACCENT_SUCCESS_HOVER = "#059669"
ACCENT_WARNING = "#F59E0B"
ACCENT_WARNING_HOVER = "#D97706"
ACCENT_DANGER = "#EF4444"
ACCENT_DANGER_HOVER = "#DC2626"
ACCENT_PURPLE = "#8B5CF6"
ACCENT_PURPLE_HOVER = "#7C3AED"
ACCENT_CYAN = "#06B6D4"

# Per-Action Type Visual Identity: (badge_bg, badge_fg, stripe_accent)
ACTION_VISUAL_STYLE: dict[ActionType, tuple[str, str, str]] = {
    ActionType.KEY_PRESS: ("#172554", "#93C5FD", "#3B82F6"),  # Electric Blue
    ActionType.KEY_DOWN: ("#1E3A8A", "#BFDBFE", "#60A5FA"),  # Ice Blue
    ActionType.KEY_UP: ("#1E293B", "#CBD5E1", "#94A3B8"),  # Slate Silver
    ActionType.MOUSE_CLICK: ("#064E3B", "#6EE7B7", "#10B981"),  # Emerald Mint
    ActionType.MOUSE_MOVE: ("#164E63", "#67E8F9", "#06B6D4"),  # Cyber Cyan
    ActionType.MOUSE_SCROLL: ("#3B0764", "#D8B4FE", "#A855F7"),  # Neon Violet
    ActionType.WAIT: ("#78350F", "#FCD34D", "#F59E0B"),  # Amber Gold
    ActionType.REPEAT_BLOCK: ("#701A75", "#F0ABFC", "#EC4899"),  # Fuchsia Loop
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
