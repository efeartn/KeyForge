"""Minimalist Craft Obsidian & Emerald design system, cached fonts, DPI helpers, and native window styling."""

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

# Craft Obsidian / Deep Slate Surface Hierarchy
BG_ROOT = "#090C10"
BG_PANEL = "#0E1319"
BG_ELEVATED = "#151D27"
BG_CARD = "#10161E"
BG_CARD_HOVER = "#161E29"
BG_CARD_ACTIVE = "#0F271E"
BG_INPUT = "#0A0E13"

BORDER_SUBTLE = "#1B2430"
BORDER_CONTROL = "#222D3D"
BORDER_FOCUS = "#2EE59D"

# Typography Hierarchy (Crisp High-Legibility)
TEXT_PRIMARY = "#F1F5F9"
TEXT_SECONDARY = "#8B98A9"
TEXT_MUTED = "#5E6B7C"
CTA_TEXT = "#051910"

# Cohesive Mint-Emerald + Calm Slate Accent System
ACCENT_PRIMARY = "#2EE59D"
ACCENT_PRIMARY_HOVER = "#24CF8B"
ACCENT_SUCCESS = "#34D399"
ACCENT_SUCCESS_HOVER = "#22B57E"
CTA_SUCCESS = "#2EE59D"
CTA_SUCCESS_HOVER = "#24CF8B"
SEGMENT_ACTIVE = "#1A222E"
SEGMENT_ACTIVE_HOVER = "#212B3A"
ACCENT_WARNING = "#F59E0B"
ACCENT_WARNING_HOVER = "#D97706"
ACCENT_DANGER = "#EF4444"
ACCENT_DANGER_HOVER = "#DC2626"
ACCENT_PURPLE = "#A78BFA"
ACCENT_PURPLE_HOVER = "#8B5CF6"
ACCENT_CYAN = "#38BDF8"

# Restrained Hairline-Bordered Neutral & Danger Controls
BTN_NEUTRAL = "#131922"
BTN_NEUTRAL_HOVER = "#1A222E"
BTN_SUBTLE = "#0F141C"
BTN_SUBTLE_HOVER = "#171F2B"
BTN_DANGER_SUBTLE = "#201115"
BTN_DANGER_SUBTLE_TEXT = "#F87171"
BTN_DANGER_BORDER = "#3B1A22"

BANNER_UPDATE_BG = "#0C241B"
BANNER_UPDATE_BORDER = "#1C5A41"
BANNER_UPDATE_FG = "#6EE7B7"

BANNER_ERR_BG = "#2A1215"
BANNER_ERR_BORDER = "#692026"
BANNER_ERR_FG = "#FCA5A5"

BANNER_WARN_BG = "#291E0D"
BANNER_WARN_BORDER = "#6E4B15"
BANNER_WARN_FG = "#FDE68A"

BANNER_INFO_BG = "#0F1E30"
BANNER_INFO_BORDER = "#1F426B"
BANNER_INFO_FG = "#93C5FD"

BADGE_OK_BG = "#0E2920"
BADGE_OK_FG = "#34D399"
BADGE_WARN_BG = "#261E0D"
BADGE_WARN_FG = "#FBBF24"
BADGE_CRIT_BG = "#2D1215"
BADGE_CRIT_FG = "#FCA5A5"

# Per-Action Soft Translucent Pill Identity: (badge_bg, badge_fg, stripe_accent)
ACTION_VISUAL_STYLE: dict[ActionType, tuple[str, str, str]] = {
    ActionType.KEY_PRESS: ("#0E2920", "#34D399", "#34D399"),  # Emerald Mint
    ActionType.KEY_DOWN: ("#0E2433", "#38BDF8", "#38BDF8"),  # Sky Blue
    ActionType.KEY_UP: ("#18202B", "#94A3B8", "#94A3B8"),  # Neutral Slate
    ActionType.MOUSE_CLICK: ("#0E2A26", "#2DD4BF", "#2DD4BF"),  # Teal
    ActionType.MOUSE_MOVE: ("#102436", "#60A5FA", "#60A5FA"),  # Soft Sapphire
    ActionType.MOUSE_SCROLL: ("#1E1A36", "#A78BFA", "#A78BFA"),  # Muted Indigo
    ActionType.WAIT: ("#271F0E", "#FBBF24", "#FBBF24"),  # Warm Amber
    ActionType.REPEAT_BLOCK: ("#2C1625", "#F472B6", "#F472B6"),  # Soft Rose
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
        "micro": ctk.CTkFont(family=ui_family, size=10, weight="normal"),
        "micro_bold": ctk.CTkFont(family=ui_family, size=10, weight="bold"),
        "mono": ctk.CTkFont(family=mono_family, size=12, weight="normal"),
        "mono_bold": ctk.CTkFont(family=mono_family, size=12, weight="bold"),
        "mono_small": ctk.CTkFont(family=mono_family, size=11, weight="normal"),
        "mono_small_bold": ctk.CTkFont(family=mono_family, size=11, weight="bold"),
        "mono_metric": ctk.CTkFont(family=mono_family, size=14, weight="bold"),
        "step_idx": ctk.CTkFont(family=mono_family, size=11, weight="bold"),
        "step_badge": ctk.CTkFont(family=ui_family, size=10, weight="bold"),
        "step_title": ctk.CTkFont(family=ui_family, size=13, weight="bold"),
        "step_sub": ctk.CTkFont(family=ui_family, size=11, weight="normal"),
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


_CACHED_BRAND_PIL_IMAGE: Image.Image | None = None


def load_brand_ctk_image(size: tuple[int, int] = (32, 32)) -> ctk.CTkImage | None:
    """Load the bundled KeyForge icon as a high-DPI CTkImage using a cached PIL source."""
    global _CACHED_BRAND_PIL_IMAGE
    if _CACHED_BRAND_PIL_IMAGE is None:
        png_path = get_icon_png_path()
        if not png_path.exists():
            return None
        with contextlib.suppress(Exception):
            with Image.open(png_path) as opened:
                _CACHED_BRAND_PIL_IMAGE = opened.convert("RGBA").copy()
    if _CACHED_BRAND_PIL_IMAGE is None:
        return None
    with contextlib.suppress(Exception):
        return ctk.CTkImage(light_image=_CACHED_BRAND_PIL_IMAGE, dark_image=_CACHED_BRAND_PIL_IMAGE, size=size)
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


def cancel_all_tcl_after_timers(window: Any) -> None:
    """Cancel all pending Tcl ``after`` callbacks directly without breaking child ``destroy()``."""
    with contextlib.suppress(Exception):
        raw_ids = window.tk.call("after", "info")
        if isinstance(raw_ids, str):
            timer_ids = tuple(raw_ids.split())
        else:
            timer_ids = tuple(raw_ids)
        for timer_id in timer_ids:
            with contextlib.suppress(Exception):
                window.tk.call("after", "cancel", timer_id)
