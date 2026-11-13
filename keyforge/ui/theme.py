"""Minimalist Apple Dark Titanium design language, cached fonts, DPI helpers, and native window styling."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import sys
import tkinter as tk
import tkinter.font as tkfont
from typing import Any

import customtkinter as ctk
from PIL import Image

from keyforge.assets import get_icon_ico_path, get_icon_png_path
from keyforge.models.action import ActionType

# Apple macOS / Human Interface Guidelines Surface Hierarchy (Space Black & Dark Titanium)
BG_ROOT = "#0C0E12"
BG_PANEL = "#15181F"
BG_ELEVATED = "#1C212B"
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
CTA_SUCCESS = "#248A3D"  # Accessible Dark-Mode Filled Green CTA
CTA_SUCCESS_HOVER = "#1E7A34"
ACCENT_WARNING = "#FF9F0A"  # Apple Orange
ACCENT_WARNING_HOVER = "#D98200"
ACCENT_DANGER = "#FF453A"  # Apple Red
ACCENT_DANGER_HOVER = "#D93025"
ACCENT_PURPLE = "#BF5AF2"  # Apple Purple
ACCENT_PURPLE_HOVER = "#9F37D6"
ACCENT_CYAN = "#64D2FF"  # Apple Teal/Cyan

# Calm Apple Neutral Surfaces & Status Banners
BTN_NEUTRAL = "#232936"
BTN_NEUTRAL_HOVER = "#2E3646"
BTN_SUBTLE = "#1E232E"
BTN_SUBTLE_HOVER = "#2A3140"

BANNER_UPDATE_BG = "#0F291E"
BANNER_UPDATE_BORDER = "#1D6B44"
BANNER_UPDATE_FG = "#6EE7B7"

BANNER_ERR_BG = "#331618"
BANNER_ERR_BORDER = "#7F2326"
BANNER_ERR_FG = "#FCA5A5"

BANNER_WARN_BG = "#2D210F"
BANNER_WARN_BORDER = "#785014"
BANNER_WARN_FG = "#FDE68A"

BANNER_INFO_BG = "#11223A"
BANNER_INFO_BORDER = "#1E4B85"
BANNER_INFO_FG = "#93C5FD"

BADGE_OK_BG = "#102C1E"
BADGE_OK_FG = "#7EE7A6"
BADGE_WARN_BG = "#33210C"
BADGE_WARN_FG = "#FDE68A"
BADGE_CRIT_BG = "#3D1414"
BADGE_CRIT_FG = "#FF9E99"

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


def _resolve_root_widget(widget: Any) -> Any:
    """Walk widget masters to find the root Tk/CTk window owning font objects."""
    curr = widget
    while getattr(curr, "master", None) is not None:
        curr = curr.master
    return curr


def _pick_font_families(root: Any) -> tuple[str, str]:
    """Pick crisp platform-native UI sans-serif and monospace font families."""
    family_map: dict[str, str] = {}
    with contextlib.suppress(Exception):
        family_map = {f.lower(): f for f in tkfont.families(root)}

    if sys.platform == "win32":
        ui_candidates = ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI")
        mono_candidates = ("Cascadia Mono", "Consolas")
    elif sys.platform == "darwin":
        ui_candidates = ("SF Pro Text", "SF Pro", "Helvetica Neue", ".AppleSystemUIFont")
        mono_candidates = ("SF Mono", "Menlo", "Monaco")
    else:
        ui_candidates = ("Inter", "Noto Sans", "Ubuntu", "DejaVu Sans")
        mono_candidates = ("JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono")

    ui_family = next((family_map[c.lower()] for c in ui_candidates if c.lower() in family_map), ui_candidates[-1])
    mono_family = next((family_map[c.lower()] for c in mono_candidates if c.lower() in family_map), mono_candidates[-1])
    return ui_family, mono_family


def get_fonts(root: Any) -> dict[str, ctk.CTkFont]:
    """Return a cached dictionary of platform-native CTkFont objects bound to ``root``."""
    owner = _resolve_root_widget(root)
    cached: dict[str, ctk.CTkFont] | None = getattr(owner, "_kf_fonts_cache", None)
    if cached is not None:
        return cached

    ui_family, mono_family = _pick_font_families(owner)
    fonts: dict[str, ctk.CTkFont] = {
        "hero": ctk.CTkFont(family=ui_family, size=20, weight="bold"),
        "brand": ctk.CTkFont(family=ui_family, size=16, weight="bold"),
        "title": ctk.CTkFont(family=ui_family, size=15, weight="bold"),
        "heading": ctk.CTkFont(family=ui_family, size=14, weight="bold"),
        "subheading": ctk.CTkFont(family=ui_family, size=13, weight="bold"),
        "cta": ctk.CTkFont(family=ui_family, size=14, weight="bold"),
        "metric": ctk.CTkFont(family=ui_family, size=15, weight="bold"),
        "body": ctk.CTkFont(family=ui_family, size=12, weight="normal"),
        "body_bold": ctk.CTkFont(family=ui_family, size=12, weight="bold"),
        "small": ctk.CTkFont(family=ui_family, size=11, weight="normal"),
        "small_bold": ctk.CTkFont(family=ui_family, size=11, weight="bold"),
        "caption": ctk.CTkFont(family=ui_family, size=10, weight="normal"),
        "caption_bold": ctk.CTkFont(family=ui_family, size=10, weight="bold"),
        "mono": ctk.CTkFont(family=mono_family, size=12, weight="normal"),
        "mono_bold": ctk.CTkFont(family=mono_family, size=12, weight="bold"),
        "mono_small": ctk.CTkFont(family=mono_family, size=11, weight="normal"),
        "mono_small_bold": ctk.CTkFont(family=mono_family, size=11, weight="bold"),
        "mono_metric": ctk.CTkFont(family=mono_family, size=14, weight="bold"),
        "step_idx": ctk.CTkFont(family=mono_family, size=12, weight="bold"),
        "step_badge": ctk.CTkFont(family=ui_family, size=11, weight="bold"),
        "step_title": ctk.CTkFont(family=ui_family, size=13, weight="bold"),
        "step_sub": ctk.CTkFont(family=ui_family, size=10, weight="normal"),
        "step_metric": ctk.CTkFont(family=mono_family, size=11, weight="normal"),
    }
    owner._kf_fonts_cache = fonts
    return fonts


def _apply_windows_dark_titlebar(window: Any) -> None:
    """Enable native Windows 10/11 immersive dark titlebar via dwmapi.DwmSetWindowAttribute."""
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        dark_val = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10 20H1+ / Windows 11)
        # 19 = DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_val), ctypes.sizeof(dark_val))
        if res != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(dark_val), ctypes.sizeof(dark_val))


def apply_window_icon(window: Any) -> bool:
    """Apply the bundled KeyForge icon and native dark titlebar across Windows, macOS, and Linux."""
    applied = False
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KeyForge.Studio.App.1")
        _apply_windows_dark_titlebar(window)

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
            window._kf_icon_after_id = window.after(150, lambda: _safe_set_iconbitmap(window, str(ico_path)))
            applied = True

    return applied


def _safe_set_iconbitmap(window: Any, ico_str: str) -> None:
    with contextlib.suppress(Exception):
        window._kf_icon_after_id = None
        if window.winfo_exists():
            window.iconbitmap(ico_str)
            _apply_windows_dark_titlebar(window)


def load_brand_ctk_image(size: tuple[int, int] = (32, 32)) -> ctk.CTkImage | None:
    """Load the bundled KeyForge icon as a high-DPI CTkImage for header embedding."""
    png_path = get_icon_png_path()
    if not png_path.exists():
        return None
    with contextlib.suppress(Exception):
        pil_img = Image.open(png_path)
        return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)
    return None


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


def get_logical_workarea(window: Any) -> tuple[int, int, int, int, float]:
    """Return ``(work_left_px, work_top_px, work_w_logical, work_h_logical, scale)``."""
    scale = 1.0
    with contextlib.suppress(Exception):
        scale = max(1.0, float(ctk.ScalingTracker.get_window_scaling(window)))

    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
            hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            if hmon:
                mi = _MONITORINFO()
                mi.cbSize = ctypes.sizeof(_MONITORINFO)
                if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                    rc = mi.rcWork
                    w_log = max(640, int((rc.right - rc.left) / scale))
                    h_log = max(440, int((rc.bottom - rc.top) / scale))
                    return (int(rc.left), int(rc.top), w_log, h_log, scale)

        with contextlib.suppress(Exception):
            rc = ctypes.wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):
                w_log = max(640, int((rc.right - rc.left) / scale))
                h_log = max(440, int((rc.bottom - rc.top) / scale))
                return (int(rc.left), int(rc.top), w_log, h_log, scale)

    sw = max(640, int(window.winfo_screenwidth()))
    sh = max(480, int(window.winfo_screenheight()))
    # Reserve small margin for OS menu bar / taskbar on macOS & Linux
    return (0, 0, sw, max(440, sh - 36), scale)


def center_window_on_parent(
    window: Any,
    parent: Any,
    logical_w: int,
    logical_h: int,
) -> tuple[int, int]:
    """Size ``window`` clamped to the monitor workarea and center it on ``parent``."""
    ref = parent if parent is not None else window
    work_left, work_top, work_w, work_h, scale = get_logical_workarea(ref)

    w = min(logical_w, max(360, work_w - 24))
    h = min(logical_h, max(320, work_h - 40))

    px_w = round(w * scale)
    px_h = round(h * scale)
    work_right = work_left + round(work_w * scale)
    work_bottom = work_top + round(work_h * scale)

    x_px = work_left + max(0, (round(work_w * scale) - px_w) // 2)
    y_px = work_top + max(0, (round(work_h * scale) - px_h) // 2)

    if parent is not None:
        with contextlib.suppress(Exception):
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            if pw >= 120 and ph >= 120:
                x_px = parent.winfo_rootx() + (pw - px_w) // 2
                y_px = parent.winfo_rooty() + (ph - px_h) // 2

    x_px = max(work_left + 8, min(x_px, work_right - px_w - 8))
    y_px = max(work_top + 8, min(y_px, work_bottom - px_h - 8))

    window.geometry(f"{w}x{h}+{x_px}+{y_px}")
    return (w, h)
