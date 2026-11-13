"""Apple-minimalist Dark Titanium Desktop GUI Application for KeyForge (Windows, macOS, Linux)."""

from __future__ import annotations

import contextlib
import ctypes
import os
import queue
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk

from keyforge import __version__
from keyforge.config import (
    AppConfig,
    load_active_profile_backup,
    load_app_config,
    save_active_profile_backup,
    save_app_config,
)
from keyforge.engine.backend import DryRunBackend
from keyforge.engine.executor import ExecutionTelemetry, ExecutorState, MacroExecutor
from keyforge.engine.hotkeys import GlobalHotkeyManager
from keyforge.engine.recorder import MacroRecorder, RecorderConfig
from keyforge.engine.win32_input import IS_WINDOWS, Win32InputBackend, get_system_cursor_pos
from keyforge.models.action import (
    ActionType,
    ClickType,
    MacroAction,
    MouseButton,
    MoveMode,
    make_key_press,
    make_mouse_click,
    make_mouse_move,
    make_mouse_scroll,
    make_wait,
)
from keyforge.models.dsl import format_actions_to_dsl, parse_quick_expression
from keyforge.models.keymap import list_common_keys, normalize_key_combo
from keyforge.models.profile import (
    MacroProfile,
    RepeatMode,
    create_default_profile,
    get_builtin_presets,
)
from keyforge.security.auditor import AuditReport, AuditSeverity, audit_profile
from keyforge.security.integrity import (
    compute_payload_sha256,
    load_profile_secure,
    save_profile_secure,
)
from keyforge.security.validator import validate_profile_complexity
from keyforge.ui.step_dialog import StepEditorDialog
from keyforge.ui.theme import (
    ACCENT_CYAN,
    ACCENT_DANGER,
    ACCENT_DANGER_HOVER,
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    ACCENT_SUCCESS,
    ACCENT_WARNING,
    BADGE_CRIT_BG,
    BADGE_CRIT_FG,
    BADGE_OK_BG,
    BADGE_OK_FG,
    BADGE_WARN_BG,
    BADGE_WARN_FG,
    BANNER_ERR_BG,
    BANNER_ERR_BORDER,
    BANNER_ERR_FG,
    BANNER_INFO_BG,
    BANNER_INFO_BORDER,
    BANNER_INFO_FG,
    BANNER_UPDATE_BG,
    BANNER_UPDATE_BORDER,
    BANNER_UPDATE_FG,
    BANNER_WARN_BG,
    BANNER_WARN_BORDER,
    BANNER_WARN_FG,
    BG_CARD,
    BG_CARD_ACTIVE,
    BG_CARD_HOVER,
    BG_ELEVATED,
    BG_INPUT,
    BG_PANEL,
    BG_ROOT,
    BORDER_SUBTLE,
    BTN_NEUTRAL,
    BTN_NEUTRAL_HOVER,
    BTN_SUBTLE,
    BTN_SUBTLE_HOVER,
    CTA_SUCCESS,
    CTA_SUCCESS_HOVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    apply_window_icon,
    center_window_on_parent,
    get_badge_colors,
    get_fonts,
    get_logical_workarea,
    get_stripe_color,
    load_brand_ctk_image,
)
from keyforge.ui.update_dialog import UpdateDialog
from keyforge.updater import DEFAULT_REPO, ReleaseInfo, fetch_latest_release

MAX_UNDO_HISTORY = 40
_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$")

_SIMPLE_REPEAT_LABELS = {
    RepeatMode.INFINITE: "Forever",
    RepeatMode.COUNT: "Count",
    RepeatMode.DURATION: "Duration",
}
_LABEL_TO_REPEAT_MODE = {v: k for k, v in _SIMPLE_REPEAT_LABELS.items()}

_SIMPLE_TASK_LABELS = {
    "clicker": "Auto-Clicker",
    "spammer": "Key Spammer",
    "sequence": "Quick Sequence",
}
_LABEL_TO_SIMPLE_TASK = {v: k for k, v in _SIMPLE_TASK_LABELS.items()}


def _platform_badge_label() -> str:
    if sys.platform == "win32":
        return "WIN32"
    if sys.platform == "darwin":
        return "MACOS"
    return "LINUX"


def _is_point_on_screen(window: Any, x_px: int, y_px: int, w_px: int, h_px: int) -> bool:
    """Verify at least the window titlebar region is accessible on an active display."""
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            user32 = ctypes.windll.user32
            vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            return (x_px + w_px - 100 >= vx) and (x_px <= vx + vw - 100) and (y_px >= vy - 8) and (y_px <= vy + vh - 60)

    sw = max(640, int(window.winfo_screenwidth()))
    sh = max(480, int(window.winfo_screenheight()))
    return (x_px + w_px - 100 >= 0) and (x_px <= sw - 100) and (y_px >= 0) and (y_px <= sh - 60)


class KeyForgeApp(ctk.CTk):
    """KeyForge Dark Titanium Desktop Application Window & Controller."""

    def __init__(self, start_hotkey_listener: bool = True) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"KeyForge v{__version__}")
        self.configure(fg_color=BG_ROOT)
        self.icon_applied: bool = apply_window_icon(self)
        self.fonts = get_fonts(self)

        # Load persisted configuration & active profile
        self.app_config: AppConfig = load_app_config()
        self.presets: dict[str, MacroProfile] = get_builtin_presets()
        restored_profile = load_active_profile_backup() if self.app_config.auto_save_active_profile else None
        if restored_profile is not None:
            self.profile: MacroProfile = restored_profile
        else:
            self.profile = create_default_profile()
            with contextlib.suppress(ValueError):
                candidate_hk = (
                    self.app_config.start_stop_hotkey,
                    self.app_config.record_hotkey,
                    self.app_config.panic_hotkey,
                )
                if len(set(candidate_hk)) == 3:
                    self.profile.start_stop_hotkey = self.app_config.start_stop_hotkey
                    self.profile.record_hotkey = self.app_config.record_hotkey
                    self.profile.panic_hotkey = self.app_config.panic_hotkey
                    self.profile.validate()

        # Core Engines
        self.win32_backend = Win32InputBackend()
        self.dry_run_backend = DryRunBackend()
        self.executor = MacroExecutor(backend=self.win32_backend)
        self.recorder = MacroRecorder(config=RecorderConfig())
        self.hotkey_manager = GlobalHotkeyManager()

        # Internal UI & Performance State
        self._closing = False
        self._hotkey_listener_enabled = bool(start_hotkey_listener)
        self._active_step_id: str | None = None
        self._step_row_frames: dict[str, ctk.CTkFrame] = {}
        self._ui_tick_after_id: str | None = None
        self._startup_update_after_id: str | None = None
        self._startup_update_poll_after_id: str | None = None
        self._hotkey_poll_after_id: str | None = None
        self._filter_after_id: str | None = None
        self._simple_slider_after_id: str | None = None
        self._toast_after_id: str | None = None
        self._filter_query: str = ""
        self._undo_stack: list[list[dict[str, Any]]] = []
        self._redo_stack: list[list[dict[str, Any]]] = []
        self._tick_cache: dict[str, Any] = {}
        self._last_telem_error: str | None = None
        self._cached_cycle_wait_ms: int = self.profile.total_cycle_wait_ms()
        self._cached_enabled_count: int = sum(1 for a in self.profile.actions if a.enabled)
        self._security_tab_dirty: bool = True
        self._last_audit_report: AuditReport | None = None
        self._last_logged_finding_codes: tuple[str, ...] = ()
        self._suppress_simple_sync: bool = False

        # Auto-Update & Thread-Safe Event Queues
        self._latest_release: ReleaseInfo | None = None
        self._update_dialog: UpdateDialog | None = None
        self._update_banner_dismissed: bool = False
        self._startup_update_checking: bool = False
        self._startup_update_queue: queue.SimpleQueue[ReleaseInfo | None] = queue.SimpleQueue()
        self._hotkey_queue: queue.SimpleQueue[str] = queue.SimpleQueue()

        # Shared Observable Vars
        self.ui_mode_var = ctk.StringVar(value="Simple" if self.app_config.ui_mode == "simple" else "Studio")
        self.dry_run_var = ctk.BooleanVar(value=self.app_config.dry_run)
        self.check_updates_var = ctk.BooleanVar(value=self.app_config.check_updates_on_startup)
        self.repeat_mode_var = ctk.StringVar(value=self.profile.repeat_mode.value)
        self.simple_repeat_mode_var = ctk.StringVar(
            value=_SIMPLE_REPEAT_LABELS.get(self.profile.repeat_mode, "Forever")
        )
        self.preset_var = ctk.StringVar(value=self.profile.name)

        # Configure responsive DPI-clamped startup size & minsize
        self._configure_initial_geometry()

        # Build Layout Hierarchy
        self._build_top_bar()
        self._build_status_bar()
        self._build_banners()

        self._workspace_host = ctk.CTkFrame(self, fg_color="transparent")
        self._workspace_host.pack(side="top", fill="both", expand=True)

        self.simple_frame = ctk.CTkFrame(self._workspace_host, fg_color="transparent")
        self.studio_frame = ctk.CTkFrame(self._workspace_host, fg_color="transparent")

        self._build_simple_workspace(self.simple_frame)
        self._build_studio_workspace(self.studio_frame)
        self._apply_ui_mode_layout()

        # Safe Keyboard Shortcuts (never hijack text editing inside Entry/Text)
        self.bind("<Control-z>", self._on_ctrl_z)
        self.bind("<Control-y>", self._on_ctrl_y)
        self.bind("<Control-Shift-Z>", self._on_ctrl_y)
        self.bind("<Control-s>", lambda _e: self.action_save_profile_dialog())

        # Initial Render & Audit
        self.refresh_sequence_view()
        self.sync_profile_settings_to_ui()
        self._sync_profile_to_simple_view()
        self.run_security_audit()
        self._log_event(
            f"KeyForge v{__version__} ({_platform_badge_label()}) ready. "
            f"Panic Stop armed on [{self.profile.panic_hotkey}]."
        )

        # Global Hotkeys
        self._rebind_hotkeys()
        if start_hotkey_listener:
            self.hotkey_manager.start()
            self._schedule_hotkey_poll()

        # Adaptive Telemetry Tick + Async Startup Update Check
        self._schedule_ui_tick()
        if self.app_config.check_updates_on_startup and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._startup_update_after_id = self.after(300, self._check_startup_updates)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------------------------------------------------------
    # Window Sizing & Persistence
    # -------------------------------------------------------------------------

    def _configure_initial_geometry(self) -> None:
        work_left, work_top, work_w, work_h, scale = get_logical_workarea(self)
        avail_w = max(640, work_w - 40)
        avail_h = max(400, work_h - 48)
        target_w = min(1080, avail_w)
        target_h = min(700, avail_h)
        min_w = min(920, target_w)
        min_h = min(580, target_h)
        self.minsize(min_w, min_h)

        saved_geo = self.app_config.window_geometry
        if saved_geo:
            match = _GEOMETRY_RE.match(saved_geo.strip())
            if match:
                raw_w = int(match.group(1))
                raw_h = int(match.group(2))
                w = max(min_w, min(raw_w, max(min_w, work_w - 16)))
                h = max(min_h, min(raw_h, max(min_h, work_h - 32)))
                if match.group(3) is not None and match.group(4) is not None:
                    x_px = int(match.group(3))
                    y_px = int(match.group(4))
                    if _is_point_on_screen(self, x_px, y_px, round(w * scale), round(h * scale)):
                        self.geometry(f"{w}x{h}+{x_px}+{y_px}")
                        return
                center_window_on_parent(self, None, w, h)
                return

        center_window_on_parent(self, None, target_w, target_h)

    def _save_persistent_state(self) -> None:
        with contextlib.suppress(Exception):
            if self.state() == "normal":
                self.app_config.window_geometry = self.geometry()

        self.app_config.ui_mode = "simple" if self.ui_mode_var.get() == "Simple" else "studio"
        self.app_config.simple_task = _LABEL_TO_SIMPLE_TASK.get(self.simple_task_var.get(), "sequence")
        self.app_config.dry_run = bool(self.dry_run_var.get())
        self.app_config.check_updates_on_startup = bool(self.check_updates_var.get())
        self.app_config.start_stop_hotkey = self.profile.start_stop_hotkey
        self.app_config.record_hotkey = self.profile.record_hotkey
        self.app_config.panic_hotkey = self.profile.panic_hotkey
        self.app_config.recorder_settings = {
            "record_delays": bool(self.rec_delays_var.get()),
            "merge_key_taps": bool(self.rec_merge_var.get()),
            "capture_click_coordinates": bool(self.rec_coords_var.get()),
        }
        self._capture_simple_controls_to_config()

        with contextlib.suppress(Exception):
            save_app_config(self.app_config)
        if self.app_config.auto_save_active_profile:
            with contextlib.suppress(Exception):
                save_active_profile_backup(self.profile)

    def _capture_simple_controls_to_config(self) -> None:
        with contextlib.suppress(Exception):
            x_val = int(self.sc_x_entry.get().strip() or "0")
            y_val = int(self.sc_y_entry.get().strip() or "0")
            self.app_config.simple_clicker = {
                "button": self.sc_button_var.get(),
                "click_type": self.sc_click_type_var.get(),
                "interval_ms": int(round(self.sc_interval_slider.get())),
                "jitter_enabled": bool(self.sc_jitter_var.get()),
                "loc_mode": "fixed" if self.sc_loc_mode_var.get() == "Fixed XY" else "cursor",
                "x": x_val,
                "y": y_val,
            }
        with contextlib.suppress(Exception):
            self.app_config.simple_spammer = {
                "key": self.ss_key_combo.get().strip() or "E",
                "interval_ms": int(round(self.ss_interval_slider.get())),
                "jitter_enabled": bool(self.ss_jitter_var.get()),
            }

    # -------------------------------------------------------------------------
    # Top Bar, Banners & Status Bar
    # -------------------------------------------------------------------------

    def _build_top_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0, height=54)
        bar.pack(side="top", fill="x", padx=0, pady=0)
        bar.pack_propagate(False)

        ctk.CTkFrame(self, fg_color=BORDER_SUBTLE, height=1, corner_radius=0).pack(side="top", fill="x")

        # Left Brand Group
        self._brand_icon_img = load_brand_ctk_image((28, 28))
        if self._brand_icon_img is not None:
            ctk.CTkLabel(bar, text="", image=self._brand_icon_img).pack(side="left", padx=(14, 8), pady=12)

        ctk.CTkLabel(
            bar,
            text="KeyForge",
            font=self.fonts["brand"],
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=(0, 6), pady=12)

        ctk.CTkLabel(
            bar,
            text=f"v{__version__}",
            font=self.fonts["caption_bold"],
            fg_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            corner_radius=6,
            padx=7,
            pady=2,
        ).pack(side="left", padx=(0, 10), pady=14)

        # Simple | Studio Mode Switcher
        self.mode_switcher = ctk.CTkSegmentedButton(
            bar,
            values=["Simple", "Studio"],
            variable=self.ui_mode_var,
            command=self._on_ui_mode_changed,
            width=124,
            height=28,
            fg_color=BG_ROOT,
            selected_color=ACCENT_PRIMARY,
            selected_hover_color=ACCENT_PRIMARY_HOVER,
            unselected_color=BG_ELEVATED,
            unselected_hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small_bold"],
        )
        self.mode_switcher.pack(side="left", padx=(0, 10), pady=13)

        # Presets & File Actions
        self.preset_menu = ctk.CTkOptionMenu(
            bar,
            variable=self.preset_var,
            values=list(self.presets.keys()),
            command=self.load_preset,
            width=165,
            height=28,
            fg_color=BG_ELEVATED,
            button_color=BTN_NEUTRAL,
            button_hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small"],
        )
        self.preset_menu.pack(side="left", padx=(0, 6), pady=13)

        for label, width, cmd in (
            ("New", 44, self.action_new_profile),
            ("Open", 48, self.action_open_profile_dialog),
            ("Save", 48, self.action_save_profile_dialog),
        ):
            ctk.CTkButton(
                bar,
                text=label,
                width=width,
                height=28,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                font=self.fonts["small_bold"],
                command=cmd,
            ).pack(side="left", padx=2, pady=13)

        # Right Action Group (Panic -> Updates -> Security Badge)
        self.top_panic_btn = ctk.CTkButton(
            bar,
            text=f"Panic Stop ({self.profile.panic_hotkey})",
            fg_color=ACCENT_DANGER,
            hover_color=ACCENT_DANGER_HOVER,
            font=self.fonts["small_bold"],
            height=30,
            width=0,
            command=self.trigger_panic_stop,
        )
        self.top_panic_btn.pack(side="right", padx=(6, 14), pady=12)

        self.updates_btn = ctk.CTkButton(
            bar,
            text="Updates",
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            height=30,
            width=0,
            command=self.open_update_dialog,
        )
        self.updates_btn.pack(side="right", padx=4, pady=12)

        self.audit_badge = ctk.CTkButton(
            bar,
            text="Audit OK",
            font=self.fonts["small_bold"],
            fg_color=BADGE_OK_BG,
            hover_color=BG_ELEVATED,
            text_color=BADGE_OK_FG,
            corner_radius=6,
            height=26,
            width=0,
            command=self._focus_security_audit,
        )
        self.audit_badge.pack(side="right", padx=4, pady=14)

    def _build_banners(self) -> None:
        """Build collapsible Update Banner and Transient Inline Notification Strip."""
        # Update Available Banner (visible in both Simple & Studio modes)
        self.update_banner = ctk.CTkFrame(
            self,
            fg_color=BANNER_UPDATE_BG,
            border_width=1,
            border_color=BANNER_UPDATE_BORDER,
            corner_radius=10,
        )
        self.update_banner_lbl = ctk.CTkLabel(
            self.update_banner,
            text="",
            font=self.fonts["small_bold"],
            text_color=BANNER_UPDATE_FG,
            anchor="w",
        )
        self.update_banner_lbl.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=7)

        ctk.CTkButton(
            self.update_banner,
            text="Dismiss",
            width=66,
            height=24,
            fg_color="transparent",
            hover_color=BTN_NEUTRAL,
            text_color=TEXT_SECONDARY,
            font=self.fonts["small"],
            command=self.dismiss_update_banner,
        ).pack(side="right", padx=(4, 10), pady=6)

        ctk.CTkButton(
            self.update_banner,
            text="Update Now",
            width=96,
            height=24,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=self.open_update_dialog,
        ).pack(side="right", padx=4, pady=6)

        # Transient Inline Notification / Error Toast Strip
        self.toast_banner = ctk.CTkFrame(
            self,
            fg_color=BANNER_INFO_BG,
            border_width=1,
            border_color=BANNER_INFO_BORDER,
            corner_radius=10,
        )
        self.toast_lbl = ctk.CTkLabel(
            self.toast_banner,
            text="",
            font=self.fonts["small_bold"],
            text_color=BANNER_INFO_FG,
            anchor="w",
        )
        self.toast_lbl.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=6)

        ctk.CTkButton(
            self.toast_banner,
            text="✕",
            width=26,
            height=22,
            fg_color="transparent",
            hover_color=BTN_NEUTRAL,
            text_color=TEXT_SECONDARY,
            font=self.fonts["small_bold"],
            command=self.dismiss_toast,
        ).pack(side="right", padx=(4, 10), pady=5)

    def _build_status_bar(self) -> None:
        """Build the docked bottom Status & Keyboard Shortcuts Bar."""
        sbar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0, height=26)
        sbar.pack(side="bottom", fill="x", padx=0, pady=0)
        sbar.pack_propagate(False)

        ctk.CTkFrame(self, fg_color=BORDER_SUBTLE, height=1, corner_radius=0).pack(side="bottom", fill="x")

        self.footer_status_lbl = ctk.CTkLabel(
            sbar,
            text=f"● Ready — KeyForge v{__version__}",
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
        )
        self.footer_status_lbl.pack(side="left", padx=14, pady=2)

        ctk.CTkLabel(
            sbar,
            text="Ctrl+Z Undo  •  Ctrl+Y Redo  •  Ctrl+S Save  •  Double-Click Step to Edit",
            font=self.fonts["small"],
            text_color=TEXT_MUTED,
        ).pack(side="right", padx=14, pady=2)

    def show_toast(self, message: str, level: str = "info", duration_ms: int | None = None) -> None:
        """Display a transient inline notification strip visible in both Simple and Studio modes."""
        if self._closing:
            return
        styles = {
            "error": (BANNER_ERR_BG, BANNER_ERR_BORDER, BANNER_ERR_FG, 8000),
            "warning": (BANNER_WARN_BG, BANNER_WARN_BORDER, BANNER_WARN_FG, 7000),
            "info": (BANNER_INFO_BG, BANNER_INFO_BORDER, BANNER_INFO_FG, 4500),
            "success": (BANNER_UPDATE_BG, BANNER_UPDATE_BORDER, BANNER_UPDATE_FG, 4500),
        }
        bg, border, fg, default_ms = styles.get(level, styles["info"])
        self.toast_banner.configure(fg_color=bg, border_color=border)
        self.toast_lbl.configure(text=message, text_color=fg)
        if not self.toast_banner.winfo_manager():
            self.toast_banner.pack(side="top", fill="x", padx=16, pady=(6, 0), before=self._workspace_host)

        if self._toast_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._toast_after_id)
            self._toast_after_id = None

        timeout = default_ms if duration_ms is None else duration_ms
        if timeout > 0:
            self._toast_after_id = self.after(timeout, self.dismiss_toast)

    def dismiss_toast(self) -> None:
        if self._toast_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._toast_after_id)
            self._toast_after_id = None
        if self.toast_banner.winfo_manager():
            self.toast_banner.pack_forget()

    def dismiss_update_banner(self) -> None:
        self._update_banner_dismissed = True
        if self.update_banner.winfo_manager():
            self.update_banner.pack_forget()

    # -------------------------------------------------------------------------
    # Simple Mode Workspace
    # -------------------------------------------------------------------------

    def _build_simple_workspace(self, parent: ctk.CTkFrame) -> None:
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=12)
        container.grid_columnconfigure(0, weight=11)
        container.grid_columnconfigure(1, weight=9)
        container.grid_rowconfigure(0, weight=1)

        # Left Card: Task Builder
        left_card = ctk.CTkFrame(
            container,
            fg_color=BG_PANEL,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        l_top = ctk.CTkFrame(left_card, fg_color="transparent")
        l_top.pack(fill="x", padx=18, pady=(16, 10))
        ctk.CTkLabel(
            l_top,
            text="Choose Action",
            font=self.fonts["title"],
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        initial_task_label = _SIMPLE_TASK_LABELS.get(self.app_config.simple_task, "Auto-Clicker")
        self.simple_task_var = ctk.StringVar(value=initial_task_label)
        self.simple_task_seg = ctk.CTkSegmentedButton(
            left_card,
            values=["Auto-Clicker", "Key Spammer", "Quick Sequence"],
            variable=self.simple_task_var,
            command=self._on_simple_task_changed,
            height=34,
            fg_color=BG_ROOT,
            selected_color=ACCENT_PRIMARY,
            selected_hover_color=ACCENT_PRIMARY_HOVER,
            unselected_color=BG_ELEVATED,
            unselected_hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["body_bold"],
        )
        self.simple_task_seg.pack(fill="x", padx=18, pady=(0, 12))

        self.simple_task_body = ctk.CTkFrame(left_card, fg_color="transparent")
        self.simple_task_body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.sc_panel = ctk.CTkFrame(self.simple_task_body, fg_color="transparent")
        self.ss_panel = ctk.CTkFrame(self.simple_task_body, fg_color="transparent")
        self.sq_panel = ctk.CTkFrame(self.simple_task_body, fg_color="transparent")

        self._build_simple_clicker_panel(self.sc_panel)
        self._build_simple_spammer_panel(self.ss_panel)
        self._build_simple_sequence_panel(self.sq_panel)

        # Right Card: Control Center
        right_card = ctk.CTkFrame(
            container,
            fg_color=BG_PANEL,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        right_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._build_simple_control_center(right_card)

    def _build_simple_clicker_panel(self, panel: ctk.CTkFrame) -> None:
        cfg = self.app_config.simple_clicker

        # Mouse Button & Click Type
        r_btn = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        r_btn.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(r_btn, text="Mouse Button", font=self.fonts["small_bold"], text_color=TEXT_SECONDARY).pack(
            anchor="w", padx=14, pady=(10, 6)
        )
        self.sc_button_var = ctk.StringVar(value=str(cfg.get("button", "Left")))
        ctk.CTkSegmentedButton(
            r_btn,
            values=["Left", "Right", "Middle"],
            variable=self.sc_button_var,
            command=lambda _v: self._apply_simple_clicker_to_profile(),
            height=30,
            fg_color=BG_ROOT,
            selected_color=ACCENT_PRIMARY,
            unselected_color=BG_ELEVATED,
            font=self.fonts["body"],
        ).pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkLabel(r_btn, text="Click Mode", font=self.fonts["small_bold"], text_color=TEXT_SECONDARY).pack(
            anchor="w", padx=14, pady=(2, 6)
        )
        self.sc_click_type_var = ctk.StringVar(value=str(cfg.get("click_type", "Single")))
        ctk.CTkSegmentedButton(
            r_btn,
            values=["Single", "Double"],
            variable=self.sc_click_type_var,
            command=lambda _v: self._apply_simple_clicker_to_profile(),
            height=30,
            fg_color=BG_ROOT,
            selected_color=ACCENT_PRIMARY,
            unselected_color=BG_ELEVATED,
            font=self.fonts["body"],
        ).pack(fill="x", padx=14, pady=(0, 12))

        # Click Speed / Interval Slider
        r_spd = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        r_spd.pack(fill="x", pady=(0, 10))
        spd_hdr = ctk.CTkFrame(r_spd, fg_color="transparent")
        spd_hdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(spd_hdr, text="Click Interval", font=self.fonts["small_bold"], text_color=TEXT_SECONDARY).pack(
            side="left"
        )
        init_ms = max(20, min(2000, int(cfg.get("interval_ms", 100))))
        self.sc_interval_lbl = ctk.CTkLabel(
            spd_hdr,
            text=f"{init_ms} ms ({1000.0 / init_ms:.1f} CPS)",
            font=self.fonts["mono_bold"],
            text_color=ACCENT_PRIMARY,
        )
        self.sc_interval_lbl.pack(side="right")

        self.sc_interval_slider = ctk.CTkSlider(
            r_spd,
            from_=20,
            to=2000,
            number_of_steps=198,
            button_color=ACCENT_PRIMARY,
            progress_color=ACCENT_PRIMARY,
            command=self._on_sc_slider_moved,
        )
        self.sc_interval_slider.set(init_ms)
        self.sc_interval_slider.pack(fill="x", padx=14, pady=(2, 8))

        self.sc_jitter_var = ctk.BooleanVar(value=bool(cfg.get("jitter_enabled", True)))
        ctk.CTkSwitch(
            r_spd,
            text="Humanized timing variance (±10%)",
            variable=self.sc_jitter_var,
            font=self.fonts["small"],
            command=self._apply_simple_clicker_to_profile,
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # Target Position
        r_loc = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        r_loc.pack(fill="x")
        loc_top = ctk.CTkFrame(r_loc, fg_color="transparent")
        loc_top.pack(fill="x", padx=14, pady=(10, 8))
        ctk.CTkLabel(loc_top, text="Click Location", font=self.fonts["small_bold"], text_color=TEXT_SECONDARY).pack(
            side="left"
        )
        init_loc = "Fixed XY" if str(cfg.get("loc_mode", "cursor")).lower() == "fixed" else "At Cursor"
        self.sc_loc_mode_var = ctk.StringVar(value=init_loc)
        ctk.CTkSegmentedButton(
            loc_top,
            values=["At Cursor", "Fixed XY"],
            variable=self.sc_loc_mode_var,
            command=self._on_sc_loc_mode_changed,
            width=160,
            height=26,
            fg_color=BG_ROOT,
            selected_color=ACCENT_PRIMARY,
            unselected_color=BG_ELEVATED,
            font=self.fonts["small"],
        ).pack(side="right")

        self.sc_xy_row = ctk.CTkFrame(r_loc, fg_color="transparent")
        self.sc_x_entry = ctk.CTkEntry(
            self.sc_xy_row, width=72, height=28, fg_color=BG_INPUT, border_color=BORDER_SUBTLE
        )
        self.sc_x_entry.insert(0, str(cfg.get("x", 0)))
        self.sc_y_entry = ctk.CTkEntry(
            self.sc_xy_row, width=72, height=28, fg_color=BG_INPUT, border_color=BORDER_SUBTLE
        )
        self.sc_y_entry.insert(0, str(cfg.get("y", 0)))
        ctk.CTkLabel(self.sc_xy_row, text="X:", font=self.fonts["small"]).pack(side="left", padx=(0, 4))
        self.sc_x_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(self.sc_xy_row, text="Y:", font=self.fonts["small"]).pack(side="left", padx=(0, 4))
        self.sc_y_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            self.sc_xy_row,
            text="Pick Cursor",
            width=88,
            height=28,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small_bold"],
            command=self._pick_simple_cursor_xy,
        ).pack(side="left")
        ctk.CTkButton(
            self.sc_xy_row,
            text="Set",
            width=48,
            height=28,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=self.fonts["small_bold"],
            command=self._apply_simple_clicker_to_profile,
        ).pack(side="right")

        if init_loc == "Fixed XY":
            self.sc_xy_row.pack(fill="x", padx=14, pady=(0, 10))

    def _build_simple_spammer_panel(self, panel: ctk.CTkFrame) -> None:
        cfg = self.app_config.simple_spammer

        r_key = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        r_key.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(r_key, text="Target Key or Combo", font=self.fonts["small_bold"], text_color=TEXT_SECONDARY).pack(
            anchor="w", padx=14, pady=(12, 6)
        )
        key_row = ctk.CTkFrame(r_key, fg_color="transparent")
        key_row.pack(fill="x", padx=14, pady=(0, 12))

        self.ss_key_combo = ctk.CTkComboBox(
            key_row,
            values=list_common_keys(),
            width=190,
            height=32,
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            button_color=BTN_NEUTRAL,
            button_hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["body_bold"],
            command=lambda _v: self._apply_simple_spammer_to_profile(),
        )
        self.ss_key_combo.set(str(cfg.get("key", "E")))
        self.ss_key_combo.pack(side="left")

        ctk.CTkButton(
            key_row,
            text="Apply Key",
            width=92,
            height=32,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small_bold"],
            command=self._apply_simple_spammer_to_profile,
        ).pack(side="left", padx=(10, 0))

        # Key Interval Slider
        r_spd = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        r_spd.pack(fill="x")
        spd_hdr = ctk.CTkFrame(r_spd, fg_color="transparent")
        spd_hdr.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(spd_hdr, text="Repeat Interval", font=self.fonts["small_bold"], text_color=TEXT_SECONDARY).pack(
            side="left"
        )
        init_ms = max(25, min(2000, int(cfg.get("interval_ms", 100))))
        self.ss_interval_lbl = ctk.CTkLabel(
            spd_hdr,
            text=f"{init_ms} ms ({1000.0 / init_ms:.1f} / sec)",
            font=self.fonts["mono_bold"],
            text_color=ACCENT_PRIMARY,
        )
        self.ss_interval_lbl.pack(side="right")

        self.ss_interval_slider = ctk.CTkSlider(
            r_spd,
            from_=25,
            to=2000,
            number_of_steps=395,
            button_color=ACCENT_PRIMARY,
            progress_color=ACCENT_PRIMARY,
            command=self._on_ss_slider_moved,
        )
        self.ss_interval_slider.set(init_ms)
        self.ss_interval_slider.pack(fill="x", padx=14, pady=(2, 10))

        self.ss_jitter_var = ctk.BooleanVar(value=bool(cfg.get("jitter_enabled", True)))
        ctk.CTkSwitch(
            r_spd,
            text="Humanized timing variance (±10%)",
            variable=self.ss_jitter_var,
            font=self.fonts["small"],
            command=self._apply_simple_spammer_to_profile,
        ).pack(anchor="w", padx=14, pady=(0, 12))

    def _build_simple_sequence_panel(self, panel: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        card.pack(fill="both", expand=True)

        ctk.CTkLabel(
            card,
            text="Active Macro Sequence",
            font=self.fonts["subheading"],
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=14, pady=(12, 4))

        self.sq_summary_lbl = ctk.CTkLabel(
            card,
            text="",
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
            justify="left",
        )
        self.sq_summary_lbl.pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkLabel(
            card,
            text="Quick Recipe",
            font=self.fonts["small_bold"],
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", padx=14, pady=(0, 4))

        self.sq_dsl_entry = ctk.CTkEntry(
            card,
            placeholder_text="Press E -> Wait 200ms -> Click -> Press 1",
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            height=34,
            font=self.fonts["mono_small"],
        )
        self.sq_dsl_entry.pack(fill="x", padx=14, pady=(0, 10))
        self.sq_dsl_entry.bind("<Return>", lambda _e: self._apply_simple_dsl_recipe())

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 12))

        ctk.CTkButton(
            btn_row,
            text="Apply Recipe",
            width=118,
            height=30,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=self.fonts["small_bold"],
            command=self._apply_simple_dsl_recipe,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row,
            text="Edit Steps in Studio →",
            width=156,
            height=30,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small_bold"],
            command=lambda: self._switch_ui_mode("Studio"),
        ).pack(side="right")

    def _build_simple_control_center(self, card: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            card,
            text="Control Center",
            font=self.fonts["title"],
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=16, pady=(12, 6))

        # Status Card
        status_box = ctk.CTkFrame(card, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        status_box.pack(fill="x", padx=16, pady=(0, 8))

        self.simple_status_lbl = ctk.CTkLabel(
            status_box,
            text="● Ready",
            font=self.fonts["hero"],
            text_color=ACCENT_SUCCESS,
        )
        self.simple_status_lbl.pack(anchor="w", padx=12, pady=(8, 1))

        self.simple_telem_lbl = ctk.CTkLabel(
            status_box,
            text="0 cycles  •  0 actions  •  0.0s",
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
        )
        self.simple_telem_lbl.pack(anchor="w", padx=12, pady=(0, 8))

        # Primary Start/Stop & Panic CTAs
        self.simple_run_btn = ctk.CTkButton(
            card,
            text=f"▶ Start Macro ({self.profile.start_stop_hotkey})",
            height=42,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            font=self.fonts["cta"],
            command=self.toggle_execution,
        )
        self.simple_run_btn.pack(fill="x", padx=16, pady=(0, 6))

        self.simple_panic_btn = ctk.CTkButton(
            card,
            text=f"Panic Stop ({self.profile.panic_hotkey})",
            height=34,
            fg_color=ACCENT_DANGER,
            hover_color=ACCENT_DANGER_HOVER,
            font=self.fonts["body_bold"],
            command=self.trigger_panic_stop,
        )
        self.simple_panic_btn.pack(fill="x", padx=16, pady=(0, 10))

        # Repeat Mode Row
        rep_box = ctk.CTkFrame(card, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        rep_box.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(rep_box, text="Repeat Mode", font=self.fonts["small_bold"], text_color=TEXT_SECONDARY).pack(
            anchor="w", padx=12, pady=(8, 4)
        )
        ctk.CTkSegmentedButton(
            rep_box,
            values=["Forever", "Count", "Duration"],
            variable=self.simple_repeat_mode_var,
            command=self._on_simple_repeat_mode_changed,
            height=28,
            fg_color=BG_ROOT,
            selected_color=ACCENT_PRIMARY,
            unselected_color=BG_ELEVATED,
            font=self.fonts["small_bold"],
        ).pack(fill="x", padx=12, pady=(0, 6))

        self.simple_repeat_val_row = ctk.CTkFrame(rep_box, fg_color="transparent")
        self.simple_repeat_val_lbl = ctk.CTkLabel(
            self.simple_repeat_val_row, text="Times:", font=self.fonts["small"], text_color=TEXT_SECONDARY
        )
        self.simple_repeat_val_lbl.pack(side="left")
        self.simple_repeat_val_entry = ctk.CTkEntry(
            self.simple_repeat_val_row,
            width=80,
            height=26,
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            font=self.fonts["small"],
        )
        self.simple_repeat_val_entry.pack(side="left", padx=8)
        self.simple_repeat_val_entry.bind("<Return>", lambda _e: self._on_simple_repeat_val_commit())
        ctk.CTkButton(
            self.simple_repeat_val_row,
            text="Apply",
            width=58,
            height=26,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small"],
            command=self._on_simple_repeat_val_commit,
        ).pack(side="left")

        ctk.CTkSwitch(
            rep_box,
            text="Dry-Run Simulation (safe preview, no OS clicks)",
            variable=self.dry_run_var,
            font=self.fonts["small"],
            command=self._on_dry_run_toggled,
        ).pack(anchor="w", padx=12, pady=(2, 8))

        # Hotkey Summary Pills
        hk_row = ctk.CTkFrame(card, fg_color="transparent")
        hk_row.pack(fill="x", padx=16, pady=(0, 6))
        self.simple_hk_start_pill = ctk.CTkLabel(
            hk_row,
            text=f"Start: {self.profile.start_stop_hotkey}",
            fg_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            corner_radius=6,
            padx=8,
            pady=2,
            font=self.fonts["small"],
        )
        self.simple_hk_start_pill.pack(side="left", padx=(0, 6))

        self.simple_hk_rec_pill = ctk.CTkLabel(
            hk_row,
            text=f"Rec: {self.profile.record_hotkey}",
            fg_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            corner_radius=6,
            padx=8,
            pady=2,
            font=self.fonts["small"],
        )
        self.simple_hk_rec_pill.pack(side="left", padx=(0, 6))

        self.simple_hk_panic_pill = ctk.CTkLabel(
            hk_row,
            text=f"Panic: {self.profile.panic_hotkey}",
            fg_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            corner_radius=6,
            padx=8,
            pady=2,
            font=self.fonts["small"],
        )
        self.simple_hk_panic_pill.pack(side="left")

        # Update Status Link + Startup Check Toggle
        upd_row = ctk.CTkFrame(card, fg_color="transparent")
        upd_row.pack(fill="x", padx=16, pady=(2, 10), side="bottom")

        self.simple_update_link = ctk.CTkButton(
            upd_row,
            text=f"✓ KeyForge v{__version__} • Check Updates",
            fg_color="transparent",
            hover_color=BTN_NEUTRAL,
            text_color=TEXT_SECONDARY,
            font=self.fonts["small"],
            anchor="w",
            height=22,
            width=0,
            command=self.open_update_dialog,
        )
        self.simple_update_link.pack(anchor="w")

        ctk.CTkCheckBox(
            upd_row,
            text="Check for updates on startup",
            variable=self.check_updates_var,
            font=self.fonts["small"],
            checkbox_width=16,
            checkbox_height=16,
            command=self._on_check_updates_toggled,
        ).pack(anchor="w", pady=(3, 0))

    # -------------------------------------------------------------------------
    # Studio Mode Workspace
    # -------------------------------------------------------------------------

    def _build_studio_workspace(self, parent: ctk.CTkFrame) -> None:
        self._build_bento_metrics_strip(parent)
        self._build_quick_forge_bar(parent)
        self._build_main_workspace(parent)

    def _build_bento_metrics_strip(self, parent: ctk.CTkFrame) -> None:
        strip = ctk.CTkFrame(parent, fg_color="transparent")
        strip.pack(fill="x", padx=14, pady=(6, 3))
        for col in range(4):
            strip.grid_columnconfigure(col, weight=1, uniform="bento")

        # Card 1: Engine Status
        c1 = ctk.CTkFrame(strip, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ctk.CTkLabel(c1, text="ENGINE STATUS", font=self.fonts["caption_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=10, pady=(5, 0)
        )
        self.status_lbl = ctk.CTkLabel(c1, text="● IDLE", font=self.fonts["metric"], text_color=ACCENT_SUCCESS)
        self.status_lbl.pack(anchor="w", padx=10, pady=(0, 0))
        self.mode_sub_lbl = ctk.CTkLabel(
            c1, text="Mode: Live OS Injection", font=self.fonts["small"], text_color=TEXT_SECONDARY
        )
        self.mode_sub_lbl.pack(anchor="w", padx=10, pady=(0, 5))

        # Card 2: Execution Progress
        c2 = ctk.CTkFrame(strip, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c2.grid(row=0, column=1, sticky="nsew", padx=4)
        ctk.CTkLabel(c2, text="EXECUTION PROGRESS", font=self.fonts["caption_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=10, pady=(5, 0)
        )
        self.cycles_lbl = ctk.CTkLabel(
            c2, text="0 Cycles | 0 Actions", font=self.fonts["metric"], text_color=TEXT_PRIMARY
        )
        self.cycles_lbl.pack(anchor="w", padx=10, pady=(0, 2))
        self.cycle_progress = ctk.CTkProgressBar(c2, height=6, fg_color=BG_INPUT, progress_color=ACCENT_PRIMARY)
        self.cycle_progress.set(0.0)
        self.cycle_progress.pack(fill="x", padx=10, pady=(2, 7))

        # Card 3: Timing & Throughput
        c3 = ctk.CTkFrame(strip, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c3.grid(row=0, column=2, sticky="nsew", padx=4)
        ctk.CTkLabel(c3, text="SEQUENCE TIMING", font=self.fonts["caption_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=10, pady=(5, 0)
        )
        self.actions_lbl = ctk.CTkLabel(
            c3, text="Cycle: 450ms | Elapsed: 0.0s", font=self.fonts["heading"], text_color=ACCENT_CYAN
        )
        self.actions_lbl.pack(anchor="w", padx=10, pady=(0, 0))
        self.rate_sub_lbl = ctk.CTkLabel(
            c3, text="Governor: 5ms Anti-Flood Floor", font=self.fonts["small"], text_color=TEXT_SECONDARY
        )
        self.rate_sub_lbl.pack(anchor="w", padx=10, pady=(0, 5))

        # Card 4: Cursor & Hotkeys
        c4 = ctk.CTkFrame(strip, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c4.grid(row=0, column=3, sticky="nsew", padx=(4, 0))
        ctk.CTkLabel(c4, text="CURSOR & HOTKEYS", font=self.fonts["caption_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=10, pady=(5, 0)
        )
        self.cursor_lbl = ctk.CTkLabel(c4, text="XY: (0, 0)", font=self.fonts["mono_metric"], text_color=TEXT_PRIMARY)
        self.cursor_lbl.pack(anchor="w", padx=10, pady=(0, 0))
        self.hotkey_sub_lbl = ctk.CTkLabel(
            c4,
            text=(
                f"Run: {self.profile.start_stop_hotkey} | "
                f"Rec: {self.profile.record_hotkey} | "
                f"Panic: {self.profile.panic_hotkey}"
            ),
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
        )
        self.hotkey_sub_lbl.pack(anchor="w", padx=10, pady=(0, 5))

    def _build_quick_forge_bar(self, parent: ctk.CTkFrame) -> None:
        qbar = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        qbar.pack(fill="x", padx=14, pady=(2, 4))

        top_row = ctk.CTkFrame(qbar, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(6, 3))

        ctk.CTkLabel(
            top_row,
            text="Quick-Forge:",
            font=self.fonts["mono_bold"],
            text_color=ACCENT_PRIMARY,
        ).pack(side="left", padx=(2, 6))

        self.dsl_entry = ctk.CTkEntry(
            top_row,
            placeholder_text="e.g. Press E -> Wait 200ms -> Click -> Press 1",
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            height=28,
            font=self.fonts["mono"],
        )
        self.dsl_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.dsl_entry.insert(0, format_actions_to_dsl(self.profile.actions))
        self.dsl_entry.bind("<Return>", lambda _e: self.apply_quick_expression(append=False))

        ctk.CTkButton(
            top_row,
            text="Replace",
            width=80,
            height=28,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=self.fonts["small_bold"],
            command=lambda: self.apply_quick_expression(append=False),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            top_row,
            text="+ Append",
            width=76,
            height=28,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small_bold"],
            command=lambda: self.apply_quick_expression(append=True),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            top_row,
            text="Copy",
            width=58,
            height=28,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small"],
            command=self.copy_recipe_to_clipboard,
        ).pack(side="left", padx=(3, 0))

        # Quick Templates Row
        chips_row = ctk.CTkFrame(qbar, fg_color="transparent")
        chips_row.pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkLabel(chips_row, text="Templates:", font=self.fonts["small"], text_color=TEXT_MUTED).pack(
            side="left", padx=(2, 6)
        )

        templates = [
            ("E -> 200ms -> Click -> 1", "Press E -> Wait 200ms -> Click -> Press 1"),
            ("20 CPS Clicker", "Click -> Wait 45ms ±10ms"),
            ("3x Repeat Block", "Repeat 3 { Press F -> Wait 120ms ±15ms } -> Click"),
            ("Hold Shift + Click", "KeyDown Shift -> Click -> Wait 100ms -> KeyUp Shift"),
        ]
        for label, expr in templates:
            ctk.CTkButton(
                chips_row,
                text=label,
                height=21,
                width=0,
                fg_color=BTN_SUBTLE,
                hover_color=BTN_SUBTLE_HOVER,
                text_color=TEXT_SECONDARY,
                font=self.fonts["small"],
                command=lambda e=expr: self._apply_template_chip(e),
            ).pack(side="left", padx=3)

    def _build_main_workspace(self, parent: ctk.CTkFrame) -> None:
        workspace = ctk.CTkFrame(parent, fg_color="transparent")
        workspace.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        workspace.grid_columnconfigure(0, weight=3)
        workspace.grid_columnconfigure(1, weight=2)
        workspace.grid_rowconfigure(0, weight=1)

        # Left Panel: Sequence Timeline
        left_panel = ctk.CTkFrame(
            workspace, fg_color=BG_PANEL, corner_radius=12, border_width=1, border_color=BORDER_SUBTLE
        )
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        l_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        l_header.pack(fill="x", padx=12, pady=(8, 3))
        ctk.CTkLabel(
            l_header, text="Action Sequence Timeline", font=self.fonts["heading"], text_color=TEXT_PRIMARY
        ).pack(side="left")
        self.step_count_lbl = ctk.CTkLabel(
            l_header, text="0 steps", text_color=TEXT_SECONDARY, font=self.fonts["small"]
        )
        self.step_count_lbl.pack(side="right")

        # Filter & Batch Controls (compact fit down to 920px window width)
        filter_bar = ctk.CTkFrame(left_panel, fg_color="transparent")
        filter_bar.pack(fill="x", padx=10, pady=(0, 4))

        self.filter_entry = ctk.CTkEntry(
            filter_bar,
            placeholder_text="Filter steps...",
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            height=26,
            width=90,
            font=self.fonts["small"],
        )
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.filter_entry.bind("<KeyRelease>", self._on_filter_changed)

        for label, width, cmd in (
            ("Undo", 44, self.undo_step_change),
            ("Redo", 44, self.redo_step_change),
            ("2x", 32, lambda: self.scale_wait_times(0.5)),
            ("0.5x", 38, lambda: self.scale_wait_times(2.0)),
            ("All On", 48, lambda: self.set_all_steps_enabled(True)),
            ("All Off", 48, lambda: self.set_all_steps_enabled(False)),
            ("Flip", 40, self.reverse_steps),
        ):
            ctk.CTkButton(
                filter_bar,
                text=label,
                width=width,
                height=26,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                text_color=TEXT_SECONDARY,
                font=self.fonts["small"],
                command=cmd,
            ).pack(side="left", padx=1)

        self.sequence_scroll = ctk.CTkScrollableFrame(left_panel, fg_color=BG_ROOT, corner_radius=10)
        self.sequence_scroll.pack(fill="both", expand=True, padx=10, pady=3)

        # Quick-Add Step Toolbar (compact fit down to 920px window width)
        toolbar = ctk.CTkFrame(left_panel, fg_color="transparent")
        toolbar.pack(fill="x", padx=10, pady=(3, 8))

        for label, width, cmd in (
            ("+ Key", 54, lambda: self.add_step(make_key_press("E", 25))),
            ("+ Wait", 58, lambda: self.add_step(make_wait(200, 10))),
            ("+ Click", 58, lambda: self.add_step(make_mouse_click(MouseButton.LEFT, ClickType.SINGLE))),
            ("+ Move", 58, lambda: self.add_step(make_mouse_move(500, 500, MoveMode.ABSOLUTE))),
            ("+ Scroll", 60, lambda: self.add_step(make_mouse_scroll(scroll_dy=-3))),
        ):
            ctk.CTkButton(
                toolbar,
                text=label,
                width=width,
                height=28,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                font=self.fonts["small_bold"],
                command=cmd,
            ).pack(side="left", padx=2)

        ctk.CTkButton(
            toolbar,
            text="+ Custom...",
            width=84,
            height=28,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=self.fonts["small_bold"],
            command=self.open_add_step_dialog,
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            toolbar,
            text="Clear",
            width=48,
            height=28,
            fg_color=BTN_NEUTRAL,
            hover_color=ACCENT_DANGER,
            font=self.fonts["small"],
            command=self.clear_all_steps,
        ).pack(side="right", padx=1)

        # Right Panel: Execution Dock & Tabbed Inspector
        right_panel = ctk.CTkFrame(
            workspace, fg_color=BG_PANEL, corner_radius=12, border_width=1, border_color=BORDER_SUBTLE
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_primary_execution_dock(right_panel)
        self._build_inspector_tabs(right_panel)

    def _build_primary_execution_dock(self, parent: ctk.CTkFrame) -> None:
        dock = ctk.CTkFrame(parent, fg_color=BG_ELEVATED, corner_radius=10)
        dock.pack(fill="x", padx=10, pady=(10, 4))

        btn_row = ctk.CTkFrame(dock, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=6)

        self.run_toggle_btn = ctk.CTkButton(
            btn_row,
            text=f"▶ START MACRO ({self.profile.start_stop_hotkey})",
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            height=34,
            font=self.fonts["cta"],
            command=self.toggle_execution,
        )
        self.run_toggle_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.dry_run_switch = ctk.CTkSwitch(
            btn_row,
            text="Dry-Run Sim",
            variable=self.dry_run_var,
            font=self.fonts["small_bold"],
            command=self._on_dry_run_toggled,
        )
        self.dry_run_switch.pack(side="right", padx=2)

    def _build_inspector_tabs(self, parent: ctk.CTkFrame) -> None:
        self.inspector_tabs = ctk.CTkTabview(
            parent,
            fg_color=BG_ROOT,
            segmented_button_fg_color=BG_ELEVATED,
            segmented_button_selected_color=ACCENT_PRIMARY,
            segmented_button_unselected_color=BG_ELEVATED,
            corner_radius=10,
            command=self._on_inspector_tab_changed,
        )
        self.inspector_tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tab_exec = self.inspector_tabs.add("Loop & Keys")
        tab_rec = self.inspector_tabs.add("Recorder")
        tab_sec = self.inspector_tabs.add("Security")
        tab_log = self.inspector_tabs.add("Console")

        self._build_loop_and_hotkeys_tab(tab_exec)
        self._build_recorder_tab(tab_rec)
        self._build_security_tab(tab_sec)
        self._build_console_tab(tab_log)

    def _build_loop_and_hotkeys_tab(self, tab: ctk.CTkFrame) -> None:
        card = ctk.CTkScrollableFrame(tab, fg_color=BG_PANEL, corner_radius=10)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        ctk.CTkLabel(card, text="Repeat Mode & Timing", font=self.fonts["subheading"], text_color=TEXT_PRIMARY).pack(
            anchor="w", padx=10, pady=(6, 4)
        )

        self.repeat_mode_menu = ctk.CTkSegmentedButton(
            card,
            values=[m.value for m in RepeatMode],
            variable=self.repeat_mode_var,
            command=self._on_repeat_settings_changed,
            height=26,
            selected_color=ACCENT_PRIMARY,
            font=self.fonts["small_bold"],
        )
        self.repeat_mode_menu.pack(fill="x", padx=10, pady=2)

        r1 = ctk.CTkFrame(card, fg_color="transparent")
        r1.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(r1, text="Repeat Count / Duration (s):", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.repeat_val_entry = ctk.CTkEntry(r1, width=84, height=26, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.repeat_val_entry.insert(0, str(self.profile.repeat_count))
        self.repeat_val_entry.pack(side="right", padx=2)

        r2 = ctk.CTkFrame(card, fg_color="transparent")
        r2.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(r2, text="Inter-Cycle Delay (ms):", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.cycle_delay_entry = ctk.CTkEntry(r2, width=84, height=26, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.cycle_delay_entry.insert(0, str(self.profile.inter_cycle_delay_ms))
        self.cycle_delay_entry.pack(side="right", padx=2)

        ctk.CTkLabel(card, text="Global Hotkey Bindings", font=self.fonts["subheading"], text_color=TEXT_PRIMARY).pack(
            anchor="w", padx=10, pady=(8, 2)
        )

        hk1 = ctk.CTkFrame(card, fg_color="transparent")
        hk1.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(hk1, text="Start / Stop Hotkey:", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.hk_start_entry = ctk.CTkEntry(hk1, width=84, height=26, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.hk_start_entry.insert(0, self.profile.start_stop_hotkey)
        self.hk_start_entry.pack(side="right", padx=2)

        hk2 = ctk.CTkFrame(card, fg_color="transparent")
        hk2.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(hk2, text="Live Recorder Hotkey:", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.hk_rec_entry = ctk.CTkEntry(hk2, width=84, height=26, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.hk_rec_entry.insert(0, self.profile.record_hotkey)
        self.hk_rec_entry.pack(side="right", padx=2)

        hk3 = ctk.CTkFrame(card, fg_color="transparent")
        hk3.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(hk3, text="Panic Stop Hotkey:", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.hk_panic_entry = ctk.CTkEntry(hk3, width=84, height=26, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.hk_panic_entry.insert(0, self.profile.panic_hotkey)
        self.hk_panic_entry.pack(side="right", padx=2)

        ctk.CTkButton(
            card,
            text="Apply Loop & Hotkey Settings",
            height=30,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=self.fonts["small_bold"],
            command=self.sync_ui_settings_to_profile,
        ).pack(fill="x", padx=10, pady=(8, 6))

    def _build_recorder_tab(self, tab: ctk.CTkFrame) -> None:
        rec_cfg = self.app_config.recorder_settings
        card = ctk.CTkScrollableFrame(tab, fg_color=BG_PANEL, corner_radius=10)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        ctk.CTkLabel(card, text="Live Input Recorder", font=self.fonts["heading"], text_color=TEXT_PRIMARY).pack(
            anchor="w", padx=10, pady=(6, 4)
        )

        self.rec_toggle_btn = ctk.CTkButton(
            card,
            text=f"● Record Live Inputs ({self.profile.record_hotkey})",
            fg_color=ACCENT_DANGER,
            hover_color=ACCENT_DANGER_HOVER,
            height=34,
            font=self.fonts["body_bold"],
            command=self.toggle_recording,
        )
        self.rec_toggle_btn.pack(fill="x", padx=10, pady=4)

        self.rec_delays_var = ctk.BooleanVar(value=bool(rec_cfg.get("record_delays", True)))
        ctk.CTkCheckBox(
            card,
            text="Record delays between actions (>= 20ms)",
            variable=self.rec_delays_var,
            font=self.fonts["small"],
        ).pack(anchor="w", padx=12, pady=4)

        self.rec_merge_var = ctk.BooleanVar(value=bool(rec_cfg.get("merge_key_taps", True)))
        ctk.CTkCheckBox(
            card,
            text="Coalesce KeyDown + KeyUp into Press",
            variable=self.rec_merge_var,
            font=self.fonts["small"],
        ).pack(anchor="w", padx=12, pady=4)

        self.rec_coords_var = ctk.BooleanVar(value=bool(rec_cfg.get("capture_click_coordinates", False)))
        ctk.CTkCheckBox(
            card,
            text="Lock clicks to exact (X, Y) coordinates",
            variable=self.rec_coords_var,
            font=self.fonts["small"],
        ).pack(anchor="w", padx=12, pady=4)

        ctk.CTkLabel(
            card,
            text=(
                "Synthetic inputs stamped with KeyForge's 0x4B465247 signature and your "
                "control hotkeys are automatically filtered out."
            ),
            text_color=TEXT_SECONDARY,
            font=self.fonts["small"],
            wraplength=300,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(6, 6))

    def _build_security_tab(self, tab: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(tab, fg_color=BG_PANEL, corner_radius=10)
        card.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(
            card, text="Cryptographic Integrity & Safety Audit", font=self.fonts["subheading"], text_color=TEXT_PRIMARY
        ).pack(anchor="w", padx=12, pady=(10, 4))

        self.sec_hash_lbl = ctk.CTkLabel(
            card, text="SHA-256: deferred until viewed", font=self.fonts["mono_small"], text_color=ACCENT_CYAN
        )
        self.sec_hash_lbl.pack(anchor="w", padx=12, pady=2)

        self.sec_budget_lbl = ctk.CTkLabel(
            card,
            text="Complexity Budget: 0 / 5,000 steps | 0 / 100,000 ops",
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
        )
        self.sec_budget_lbl.pack(anchor="w", padx=12, pady=(0, 6))

        self.sec_report_box = ctk.CTkTextbox(
            card,
            fg_color=BG_INPUT,
            text_color=TEXT_PRIMARY,
            font=self.fonts["mono_small"],
            corner_radius=8,
        )
        self.sec_report_box.pack(fill="both", expand=True, padx=12, pady=(2, 10))

    def _build_console_tab(self, tab: ctk.CTkFrame) -> None:
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkLabel(top, text="Event & Telemetry Log", font=self.fonts["subheading"], text_color=TEXT_PRIMARY).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            top,
            text="Clear",
            width=58,
            height=24,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["small"],
            command=lambda: self.console_box.delete("1.0", "end"),
        ).pack(side="right", padx=4)

        self.console_box = ctk.CTkTextbox(
            tab, fg_color=BG_INPUT, text_color=TEXT_SECONDARY, font=self.fonts["mono_small"], corner_radius=8
        )
        self.console_box.pack(fill="both", expand=True, padx=4, pady=(2, 4))

    # -------------------------------------------------------------------------
    # Simple <-> Studio Mode & Bidirectional Sync
    # -------------------------------------------------------------------------

    def _apply_ui_mode_layout(self) -> None:
        is_simple = self.ui_mode_var.get() == "Simple"
        if is_simple:
            self.studio_frame.pack_forget()
            self.simple_frame.pack(fill="both", expand=True)
        else:
            self.simple_frame.pack_forget()
            self.studio_frame.pack(fill="both", expand=True)

    def _switch_ui_mode(self, mode_label: str) -> None:
        self.ui_mode_var.set(mode_label)
        self._on_ui_mode_changed(mode_label)

    def _on_ui_mode_changed(self, selected_mode: str) -> None:
        if selected_mode == "Studio" and self.simple_frame.winfo_manager():
            task_key = _LABEL_TO_SIMPLE_TASK.get(self.simple_task_var.get(), "sequence")
            if task_key == "clicker":
                self._apply_simple_clicker_to_profile(record_undo=False)
            elif task_key == "spammer":
                self._apply_simple_spammer_to_profile(record_undo=False)
            if self.profile.repeat_mode != RepeatMode.INFINITE:
                raw_val = self.simple_repeat_val_entry.get().strip()
                if raw_val:
                    self.repeat_val_entry.delete(0, "end")
                    self.repeat_val_entry.insert(0, raw_val)
                    self.sync_ui_settings_to_profile()

        self.app_config.ui_mode = "simple" if selected_mode == "Simple" else "studio"
        self._apply_ui_mode_layout()
        if selected_mode == "Simple":
            self._sync_profile_to_simple_view()
        else:
            self.sync_profile_settings_to_ui()
            if self._security_tab_dirty and self.inspector_tabs.get() == "Security":
                self._refresh_security_tab_details()
        self._save_persistent_state()

    def _on_simple_task_changed(self, selected_label: str) -> None:
        if self._suppress_simple_sync:
            return
        task_key = _LABEL_TO_SIMPLE_TASK.get(selected_label, "sequence")
        self.app_config.simple_task = task_key
        self._show_simple_task_subpanel(task_key)

        if task_key == "clicker":
            self._apply_simple_clicker_to_profile()
        elif task_key == "spammer":
            self._apply_simple_spammer_to_profile()
        else:
            self._refresh_simple_sequence_summary()
        self._save_persistent_state()

    def _show_simple_task_subpanel(self, task_key: str) -> None:
        for panel in (self.sc_panel, self.ss_panel, self.sq_panel):
            panel.pack_forget()
        if task_key == "clicker":
            self.sc_panel.pack(fill="both", expand=True)
        elif task_key == "spammer":
            self.ss_panel.pack(fill="both", expand=True)
        else:
            self.sq_panel.pack(fill="both", expand=True)

    def _on_sc_slider_moved(self, val: float) -> None:
        ms = max(20, int(round(val)))
        self.sc_interval_lbl.configure(text=f"{ms} ms ({1000.0 / ms:.1f} CPS)")
        self._schedule_simple_slider_commit("clicker")

    def _on_ss_slider_moved(self, val: float) -> None:
        ms = max(25, int(round(val)))
        self.ss_interval_lbl.configure(text=f"{ms} ms ({1000.0 / ms:.1f} / sec)")
        self._schedule_simple_slider_commit("spammer")

    def _schedule_simple_slider_commit(self, target: str) -> None:
        if self._simple_slider_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._simple_slider_after_id)
        self._simple_slider_after_id = self.after(
            80,
            lambda t=target: self._commit_simple_slider_change(t),
        )

    def _commit_simple_slider_change(self, target: str) -> None:
        if self._simple_slider_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._simple_slider_after_id)
            self._simple_slider_after_id = None
        if self._closing:
            return
        if target == "clicker":
            self._apply_simple_clicker_to_profile(record_undo=False)
        else:
            self._apply_simple_spammer_to_profile(record_undo=False)

    def _on_sc_loc_mode_changed(self, selected: str) -> None:
        if selected == "Fixed XY":
            self.sc_xy_row.pack(fill="x", padx=14, pady=(0, 10))
        else:
            self.sc_xy_row.pack_forget()
        self._apply_simple_clicker_to_profile()

    def _pick_simple_cursor_xy(self) -> None:
        if IS_WINDOWS:
            cx, cy = get_system_cursor_pos()
        else:
            cx, cy = self.winfo_pointerx(), self.winfo_pointery()
        self.sc_x_entry.delete(0, "end")
        self.sc_x_entry.insert(0, str(cx))
        self.sc_y_entry.delete(0, "end")
        self.sc_y_entry.insert(0, str(cy))
        self._apply_simple_clicker_to_profile()

    def _apply_simple_clicker_to_profile(self, record_undo: bool = True) -> bool:
        try:
            btn = MouseButton(self.sc_button_var.get().strip().lower())
            ctype = ClickType(self.sc_click_type_var.get().strip().lower())
            interval_ms = max(20, min(2000, int(round(self.sc_interval_slider.get()))))
            hold_ms = 15
            wait_ms = max(5, interval_ms - hold_ms)
            jitter_ms = max(1, min(15, round(interval_ms * 0.1))) if self.sc_jitter_var.get() else 0

            x: int | None = None
            y: int | None = None
            if self.sc_loc_mode_var.get() == "Fixed XY":
                x = int(self.sc_x_entry.get().strip() or "0")
                y = int(self.sc_y_entry.get().strip() or "0")

            actions = [
                make_mouse_click(btn, ctype, x=x, y=y, hold_ms=hold_ms, label="Auto Click"),
                make_wait(wait_ms, jitter_ms=jitter_ms, label="Click Interval"),
            ]
            if record_undo:
                self._record_undo_snapshot()
            self.profile.name = f"Auto-Clicker ({btn.value.capitalize()} {1000.0 / interval_ms:.1f} CPS)"
            self.profile.inter_cycle_delay_ms = 0
            self.profile.actions = actions
            self.profile.validate()
            self.cycle_delay_entry.delete(0, "end")
            self.cycle_delay_entry.insert(0, "0")
            self._capture_simple_controls_to_config()
            self._on_sequence_mutated(sync_simple=False)
            self._refresh_simple_sequence_summary()
            return True
        except Exception as exc:
            self.show_toast(f"Auto-Clicker setting error: {exc}", level="error")
            return False

    def _apply_simple_spammer_to_profile(self, record_undo: bool = True) -> bool:
        try:
            raw_key = self.ss_key_combo.get().strip()
            key_norm = normalize_key_combo(raw_key)
            self.ss_key_combo.set(key_norm)

            interval_ms = max(25, min(2000, int(round(self.ss_interval_slider.get()))))
            hold_ms = min(25, interval_ms - 5)
            wait_ms = max(5, interval_ms - hold_ms)
            jitter_ms = max(1, min(15, round(interval_ms * 0.1))) if self.ss_jitter_var.get() else 0

            actions = [
                make_key_press(key_norm, hold_ms=hold_ms, label=f"Tap {key_norm}"),
                make_wait(wait_ms, jitter_ms=jitter_ms, label="Repeat Interval"),
            ]
            if record_undo:
                self._record_undo_snapshot()
            self.profile.name = f"Key Spammer ({key_norm})"
            self.profile.inter_cycle_delay_ms = 0
            self.profile.actions = actions
            self.profile.validate()
            self.cycle_delay_entry.delete(0, "end")
            self.cycle_delay_entry.insert(0, "0")
            self._capture_simple_controls_to_config()
            self._on_sequence_mutated(sync_simple=False)
            self._refresh_simple_sequence_summary()
            return True
        except Exception as exc:
            self.show_toast(f"Invalid target key: {exc}", level="error")
            return False

    def _apply_simple_dsl_recipe(self) -> None:
        expr = self.sq_dsl_entry.get().strip()
        self.dsl_entry.delete(0, "end")
        self.dsl_entry.insert(0, expr)
        if self.apply_quick_expression(append=False):
            self.show_toast("Sequence recipe updated.", level="success")

    def _on_simple_repeat_mode_changed(self, selected_label: str) -> None:
        mode = _LABEL_TO_REPEAT_MODE.get(selected_label, RepeatMode.INFINITE)
        self.repeat_mode_var.set(mode.value)
        if mode == RepeatMode.DURATION:
            self.repeat_val_entry.delete(0, "end")
            self.repeat_val_entry.insert(0, str(self.profile.repeat_duration_s))
            self.simple_repeat_val_entry.delete(0, "end")
            self.simple_repeat_val_entry.insert(0, str(self.profile.repeat_duration_s))
        elif mode == RepeatMode.COUNT:
            self.repeat_val_entry.delete(0, "end")
            self.repeat_val_entry.insert(0, str(self.profile.repeat_count))
            self.simple_repeat_val_entry.delete(0, "end")
            self.simple_repeat_val_entry.insert(0, str(self.profile.repeat_count))
        self._update_simple_repeat_val_visibility(mode)
        self.sync_ui_settings_to_profile()

    def _on_simple_repeat_val_commit(self) -> None:
        raw = self.simple_repeat_val_entry.get().strip()
        self.repeat_val_entry.delete(0, "end")
        self.repeat_val_entry.insert(0, raw)
        if self.sync_ui_settings_to_profile():
            self._save_persistent_state()

    def _update_simple_repeat_val_visibility(self, mode: RepeatMode) -> None:
        if mode == RepeatMode.INFINITE:
            self.simple_repeat_val_row.pack_forget()
        else:
            lbl_text = "Times:" if mode == RepeatMode.COUNT else "Seconds:"
            self.simple_repeat_val_lbl.configure(text=lbl_text)
            if not self.simple_repeat_val_row.winfo_manager():
                self.simple_repeat_val_row.pack(fill="x", padx=14, pady=(0, 8))

    def _on_dry_run_toggled(self) -> None:
        self.app_config.dry_run = bool(self.dry_run_var.get())
        self._save_persistent_state()

    def _on_check_updates_toggled(self) -> None:
        self.app_config.check_updates_on_startup = bool(self.check_updates_var.get())
        self._save_persistent_state()

    def _refresh_simple_sequence_summary(self) -> None:
        enabled = sum(1 for a in self.profile.actions if a.enabled)
        total = len(self.profile.actions)
        wait_ms = self._cached_cycle_wait_ms
        self.sq_summary_lbl.configure(
            text=f"{self.profile.name}\n{enabled}/{total} active steps  •  {wait_ms}ms per cycle"
        )
        dsl_text = format_actions_to_dsl(self.profile.actions)
        if self.sq_dsl_entry.get() != dsl_text:
            self.sq_dsl_entry.delete(0, "end")
            self.sq_dsl_entry.insert(0, dsl_text)

    def _sync_profile_to_simple_view(self) -> None:
        """Infer the matching Simple Mode panel from ``self.profile`` so Simple & Studio never desync."""
        self._suppress_simple_sync = True
        try:
            self.simple_repeat_mode_var.set(_SIMPLE_REPEAT_LABELS.get(self.profile.repeat_mode, "Forever"))
            self.simple_repeat_val_entry.delete(0, "end")
            val = (
                str(self.profile.repeat_duration_s)
                if self.profile.repeat_mode == RepeatMode.DURATION
                else str(self.profile.repeat_count)
            )
            self.simple_repeat_val_entry.insert(0, val)
            self._update_simple_repeat_val_visibility(self.profile.repeat_mode)
            self._refresh_hotkey_displays()
            self._refresh_simple_sequence_summary()

            acts = self.profile.actions
            task_key = "sequence"
            if (
                len(acts) == 2
                and acts[0].enabled
                and acts[1].enabled
                and acts[0].action_type == ActionType.MOUSE_CLICK
                and acts[1].action_type == ActionType.WAIT
                and acts[0].button in (MouseButton.LEFT, MouseButton.RIGHT, MouseButton.MIDDLE)
                and acts[0].click_type in (ClickType.SINGLE, ClickType.DOUBLE)
            ):
                task_key = "clicker"
                click_act, wait_act = acts[0], acts[1]
                self.sc_button_var.set(click_act.button.value.capitalize())
                self.sc_click_type_var.set(click_act.click_type.value.capitalize())
                total_ms = max(20, min(2000, click_act.hold_ms + wait_act.wait_ms))
                self.sc_interval_slider.set(total_ms)
                self.sc_interval_lbl.configure(text=f"{total_ms} ms ({1000.0 / total_ms:.1f} CPS)")
                self.sc_jitter_var.set(wait_act.jitter_ms > 0)
                if click_act.x is not None and click_act.y is not None:
                    self.sc_loc_mode_var.set("Fixed XY")
                    self.sc_x_entry.delete(0, "end")
                    self.sc_x_entry.insert(0, str(click_act.x))
                    self.sc_y_entry.delete(0, "end")
                    self.sc_y_entry.insert(0, str(click_act.y))
                    if not self.sc_xy_row.winfo_manager():
                        self.sc_xy_row.pack(fill="x", padx=14, pady=(0, 10))
                else:
                    self.sc_loc_mode_var.set("At Cursor")
                    self.sc_xy_row.pack_forget()
            elif (
                len(acts) == 2
                and acts[0].enabled
                and acts[1].enabled
                and acts[0].action_type == ActionType.KEY_PRESS
                and acts[1].action_type == ActionType.WAIT
            ):
                task_key = "spammer"
                key_act, wait_act = acts[0], acts[1]
                self.ss_key_combo.set(key_act.key)
                total_ms = max(25, min(2000, key_act.hold_ms + wait_act.wait_ms))
                self.ss_interval_slider.set(total_ms)
                self.ss_interval_lbl.configure(text=f"{total_ms} ms ({1000.0 / total_ms:.1f} / sec)")
                self.ss_jitter_var.set(wait_act.jitter_ms > 0)

            self.app_config.simple_task = task_key
            self.simple_task_var.set(_SIMPLE_TASK_LABELS[task_key])
            self._show_simple_task_subpanel(task_key)
        finally:
            self._suppress_simple_sync = False

    # -------------------------------------------------------------------------
    # Undo / Redo & Sequence Builder Operations
    # -------------------------------------------------------------------------

    def _is_text_input_focused(self, event: object = None) -> bool:
        target = getattr(event, "widget", None)
        if isinstance(target, (tk.Entry, tk.Text)):
            return True
        focused = self.focus_get()
        return isinstance(focused, (tk.Entry, tk.Text))

    def _on_ctrl_z(self, event: object = None) -> None:
        if self._is_text_input_focused(event):
            return
        self.undo_step_change()

    def _on_ctrl_y(self, event: object = None) -> None:
        if self._is_text_input_focused(event):
            return
        self.redo_step_change()

    def _snapshot_actions(self) -> list[dict[str, Any]]:
        return [act.to_dict() for act in self.profile.actions]

    def _record_undo_snapshot(self) -> None:
        self._undo_stack.append(self._snapshot_actions())
        if len(self._undo_stack) > MAX_UNDO_HISTORY:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo_step_change(self) -> bool:
        if not self._undo_stack:
            self._log_event("Undo history is empty.")
            return False
        self._redo_stack.append(self._snapshot_actions())
        prev_state = self._undo_stack.pop()
        self.profile.actions = [MacroAction.from_dict(d) for d in prev_state]
        self._on_sequence_mutated()
        self._log_event(f"Undid last change ({len(self.profile.actions)} steps active).")
        return True

    def redo_step_change(self) -> bool:
        if not self._redo_stack:
            self._log_event("Redo history is empty.")
            return False
        self._undo_stack.append(self._snapshot_actions())
        next_state = self._redo_stack.pop()
        self.profile.actions = [MacroAction.from_dict(d) for d in next_state]
        self._on_sequence_mutated()
        self._log_event(f"Redid change ({len(self.profile.actions)} steps active).")
        return True

    def scale_wait_times(self, factor: float) -> None:
        """Scale all Wait step durations (and jitter) by ``factor``."""
        if not self.profile.actions:
            return
        self._record_undo_snapshot()

        def _scale_list(actions: list[MacroAction]) -> int:
            scaled = 0
            for act in actions:
                if act.action_type == ActionType.WAIT:
                    act.wait_ms = max(5, min(3_600_000, int(round(act.wait_ms * factor))))
                    act.jitter_ms = max(0, min(60_000, int(round(act.jitter_ms * factor))))
                    scaled += 1
                elif act.action_type == ActionType.REPEAT_BLOCK:
                    scaled += _scale_list(act.sub_actions)
            return scaled

        count = _scale_list(self.profile.actions)
        self._on_sequence_mutated()
        speed_desc = "2x faster" if factor < 1.0 else "0.5x slower"
        self._log_event(f"Scaled {count} wait step(s) ({speed_desc}).")

    def _on_filter_changed(self, event: object = None) -> None:
        if event is None:
            # Immediate synchronous filter when invoked directly by controller/tests
            self._apply_filter_debounced()
            return
        if self._filter_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._filter_after_id)
        self._filter_after_id = self.after(120, self._apply_filter_debounced)

    def _apply_filter_debounced(self) -> None:
        if self._filter_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        if self._closing:
            return
        new_query = self.filter_entry.get().strip().lower()
        if new_query == self._filter_query and len(self._step_row_frames) > 0:
            return
        self._filter_query = new_query
        self.refresh_sequence_view()

    def set_all_steps_enabled(self, enabled: bool) -> None:
        self._record_undo_snapshot()
        for act in self.profile.actions:
            act.enabled = enabled
        self._on_sequence_mutated()

    def reverse_steps(self) -> None:
        self._record_undo_snapshot()
        self.profile.actions.reverse()
        self._on_sequence_mutated()
        self._log_event("Reversed step sequence order.")

    @staticmethod
    def _step_subtitle(act: MacroAction) -> str:
        if act.action_type == ActionType.KEY_PRESS:
            return f"Hold: {act.hold_ms}ms | Mode: Tap"
        if act.action_type in (ActionType.KEY_DOWN, ActionType.KEY_UP):
            return "Explicit State Transition | Auto-Sanitized on Stop"
        if act.action_type == ActionType.WAIT:
            if act.jitter_ms > 0:
                lo = max(1, act.wait_ms - act.jitter_ms)
                hi = act.wait_ms + act.jitter_ms
                return f"Humanized Range: {lo}ms – {hi}ms"
            return "Deterministic Delay"
        if act.action_type == ActionType.MOUSE_CLICK:
            target = f"Fixed ({act.x}, {act.y})" if act.x is not None else "Dynamic Live Cursor"
            return f"Hold: {act.hold_ms}ms | Target: {target}"
        if act.action_type == ActionType.MOUSE_MOVE:
            return f"Coordinate Space: {act.move_mode.value.capitalize()}"
        if act.action_type == ActionType.MOUSE_SCROLL:
            return f"Wheel Delta: dY={act.scroll_dy}, dX={act.scroll_dx}"
        if act.action_type == ActionType.REPEAT_BLOCK:
            return f"{len(act.sub_actions)} inner step(s) × {act.repeat_count} iterations"
        return ""

    @staticmethod
    def _step_metric_pill(act: MacroAction) -> str:
        if act.action_type == ActionType.WAIT:
            return f"{act.wait_ms}ms ±{act.jitter_ms}" if act.jitter_ms > 0 else f"{act.wait_ms}ms"
        if act.action_type == ActionType.KEY_PRESS:
            return f"{act.hold_ms}ms tap"
        if act.action_type == ActionType.MOUSE_CLICK:
            return f"({act.x},{act.y})" if act.x is not None else "Cursor"
        if act.action_type == ActionType.MOUSE_MOVE:
            return f"({act.x},{act.y})"
        if act.action_type == ActionType.MOUSE_SCROLL:
            return f"dY {act.scroll_dy:+d}"
        if act.action_type == ActionType.REPEAT_BLOCK:
            return f"{act.repeat_count}× loop"
        return "State"

    def _update_cached_cycle_metrics(self) -> None:
        self._cached_enabled_count = sum(1 for a in self.profile.actions if a.enabled)
        self._cached_cycle_wait_ms = self.profile.total_cycle_wait_ms()
        total = len(self.profile.actions)
        self.step_count_lbl.configure(
            text=f"{self._cached_enabled_count}/{total} active | Cycle: {self._cached_cycle_wait_ms}ms"
        )

    def refresh_sequence_view(self) -> None:
        """Rebuild timeline step cards using cached fonts and non-clipping right-first layout."""
        for child in self.sequence_scroll.winfo_children():
            child.destroy()
        self._step_row_frames.clear()

        actions = self.profile.actions
        self._update_cached_cycle_metrics()

        if not actions:
            ctk.CTkLabel(
                self.sequence_scroll,
                text=(
                    "No steps in macro sequence.\nSelect a Template above or click '+ Key', '+ Wait', '+ Click' below."
                ),
                text_color=TEXT_MUTED,
                font=self.fonts["body"],
                justify="center",
            ).pack(pady=52)
            return

        for idx, act in enumerate(actions):
            if self._filter_query:
                haystack = f"{act.badge_text()} {act.describe()} {act.label}".lower()
                if self._filter_query not in haystack:
                    continue

            is_active = act.id == self._active_step_id
            row_bg = BG_CARD_ACTIVE if is_active else BG_CARD
            row = ctk.CTkFrame(
                self.sequence_scroll,
                fg_color=row_bg,
                corner_radius=8,
                height=50,
                border_width=1,
                border_color=BORDER_SUBTLE,
            )
            row.pack_propagate(False)
            row.pack(fill="x", pady=3, padx=2)
            self._step_row_frames[act.id] = row

            # 1. Left fixed elements
            stripe = ctk.CTkFrame(row, fg_color=get_stripe_color(act.action_type), width=4, height=40, corner_radius=2)
            stripe.pack(side="left", fill="y", padx=(2, 6), pady=4)

            ctk.CTkLabel(
                row,
                text=f"#{idx + 1:02d}",
                width=32,
                font=self.fonts["step_idx"],
                text_color=TEXT_SECONDARY,
            ).pack(side="left", padx=(0, 2))

            en_var = ctk.BooleanVar(value=act.enabled)
            ctk.CTkCheckBox(
                row,
                text="",
                variable=en_var,
                width=22,
                checkbox_width=18,
                checkbox_height=18,
                command=lambda a=act, v=en_var: self._on_toggle_step_enabled(a, v.get()),
            ).pack(side="left", padx=2)

            badge_bg, badge_fg = get_badge_colors(act.action_type)
            ctk.CTkLabel(
                row,
                text=act.badge_text(),
                fg_color=badge_bg,
                text_color=badge_fg,
                corner_radius=6,
                width=58,
                height=24,
                font=self.fonts["step_badge"],
            ).pack(side="left", padx=5)

            # 2. Right fixed controls packed BEFORE text_col so long titles NEVER clip action buttons!
            ctk.CTkButton(
                row,
                text="✕",
                width=26,
                height=26,
                fg_color=BTN_SUBTLE,
                hover_color=ACCENT_DANGER,
                font=self.fonts["small_bold"],
                command=lambda i=idx: self.delete_step(i),
            ).pack(side="right", padx=(2, 8))

            ctk.CTkButton(
                row,
                text="Dup",
                width=36,
                height=26,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                font=self.fonts["small"],
                command=lambda i=idx: self.duplicate_step(i),
            ).pack(side="right", padx=2)

            ctk.CTkButton(
                row,
                text="Edit",
                width=40,
                height=26,
                fg_color=BTN_NEUTRAL,
                hover_color=ACCENT_PRIMARY,
                font=self.fonts["small_bold"],
                command=lambda i=idx: self.open_edit_step_dialog(i),
            ).pack(side="right", padx=2)

            ctk.CTkButton(
                row,
                text="▼",
                width=26,
                height=26,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                font=self.fonts["small"],
                command=lambda i=idx: self.move_step_down(i),
            ).pack(side="right", padx=2)

            ctk.CTkButton(
                row,
                text="▲",
                width=26,
                height=26,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                font=self.fonts["small"],
                command=lambda i=idx: self.move_step_up(i),
            ).pack(side="right", padx=2)

            ctk.CTkLabel(
                row,
                text=self._step_metric_pill(act),
                font=self.fonts["step_metric"],
                fg_color=BG_INPUT,
                text_color=TEXT_SECONDARY,
                corner_radius=5,
                padx=7,
                pady=2,
            ).pack(side="right", padx=(4, 6))

            # 3. Expandable Title + Subtitle Column
            text_col = ctk.CTkFrame(row, fg_color="transparent", height=38)
            text_col.pack(side="left", fill="x", expand=True, padx=4, pady=4)

            desc_raw = act.describe()
            desc_text = f"{desc_raw[:88]}…" if len(desc_raw) > 89 else desc_raw
            title_lbl = ctk.CTkLabel(
                text_col,
                text=desc_text,
                text_color=TEXT_PRIMARY if act.enabled else TEXT_MUTED,
                height=18,
                font=self.fonts["step_title"],
                anchor="w",
            )
            title_lbl.pack(fill="x", anchor="w")

            sub_text = self._step_subtitle(act)
            if sub_text:
                sub_lbl = ctk.CTkLabel(
                    text_col,
                    text=sub_text,
                    text_color=TEXT_SECONDARY,
                    height=14,
                    font=self.fonts["step_sub"],
                    anchor="w",
                )
                sub_lbl.pack(fill="x", anchor="w")
                sub_lbl.bind("<Double-Button-1>", lambda _e, i=idx: self.open_edit_step_dialog(i))

            row.bind("<Enter>", lambda _e, r=row, aid=act.id: self._on_row_hover(r, aid, True))
            row.bind("<Leave>", lambda _e, r=row, aid=act.id: self._on_row_hover(r, aid, False))
            row.bind("<Double-Button-1>", lambda _e, i=idx: self.open_edit_step_dialog(i))
            title_lbl.bind("<Double-Button-1>", lambda _e, i=idx: self.open_edit_step_dialog(i))

    def _on_row_hover(self, row: ctk.CTkFrame, action_id: str, entering: bool) -> None:
        if action_id == self._active_step_id:
            row.configure(fg_color=BG_CARD_ACTIVE)
        else:
            row.configure(fg_color=BG_CARD_HOVER if entering else BG_CARD)

    def _sync_dsl_entry_from_steps(self) -> None:
        dsl = format_actions_to_dsl(self.profile.actions)
        self.dsl_entry.delete(0, "end")
        self.dsl_entry.insert(0, dsl)
        if hasattr(self, "sq_dsl_entry"):
            self.sq_dsl_entry.delete(0, "end")
            self.sq_dsl_entry.insert(0, dsl)

    def _on_sequence_mutated(self, sync_simple: bool = True) -> None:
        self._sync_dsl_entry_from_steps()
        self.preset_var.set(self.profile.name if self.profile.name in self.presets else "Custom Profile")
        self.refresh_sequence_view()
        self.run_security_audit()
        if sync_simple:
            self._sync_profile_to_simple_view()

    def _on_toggle_step_enabled(self, action: MacroAction, enabled: bool) -> None:
        self._record_undo_snapshot()
        action.enabled = enabled
        self._on_sequence_mutated()

    def add_step(self, action: MacroAction) -> None:
        self._record_undo_snapshot()
        self.profile.actions.append(action)
        self._on_sequence_mutated()
        self._log_event(f"Added step: {action.describe()}")

    def delete_step(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions):
            self._record_undo_snapshot()
            removed = self.profile.actions.pop(index)
            self._on_sequence_mutated()
            self._log_event(f"Deleted step #{index + 1}: {removed.describe()}")

    def duplicate_step(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions):
            self._record_undo_snapshot()
            cloned = self.profile.actions[index].clone()
            self.profile.actions.insert(index + 1, cloned)
            self._on_sequence_mutated()
            self._log_event(f"Duplicated step #{index + 1}: {cloned.describe()}")

    def move_step_up(self, index: int) -> None:
        if 0 < index < len(self.profile.actions):
            self._record_undo_snapshot()
            acts = self.profile.actions
            acts[index - 1], acts[index] = acts[index], acts[index - 1]
            self._on_sequence_mutated()

    def move_step_down(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions) - 1:
            self._record_undo_snapshot()
            acts = self.profile.actions
            acts[index + 1], acts[index] = acts[index], acts[index + 1]
            self._on_sequence_mutated()

    def clear_all_steps(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
        self._record_undo_snapshot()
        self.profile.actions.clear()
        self._on_sequence_mutated()
        self._log_event("Cleared all steps from sequence.")

    def open_add_step_dialog(self) -> None:
        StepEditorDialog(self, initial_action=None, on_save=self.add_step)

    def open_edit_step_dialog(self, index: int) -> None:
        if not (0 <= index < len(self.profile.actions)):
            return
        current = self.profile.actions[index]

        def _save_edited(updated: MacroAction) -> None:
            self._record_undo_snapshot()
            self.profile.actions[index] = updated
            self._on_sequence_mutated()
            self._log_event(f"Updated step #{index + 1}: {updated.describe()}")

        StepEditorDialog(self, initial_action=current, on_save=_save_edited)

    def apply_quick_expression(self, append: bool = False) -> bool:
        """Parse the Quick-Forge recipe entry and replace or append steps."""
        expr = self.dsl_entry.get().strip()
        try:
            parsed_steps = parse_quick_expression(expr)
            self._record_undo_snapshot()
            if append:
                self.profile.actions.extend(parsed_steps)
            else:
                self.profile.actions = parsed_steps
            self._on_sequence_mutated()
            mode_str = "Appended" if append else "Forged"
            self._log_event(f"{mode_str} {len(parsed_steps)} step(s) from Quick-Forge recipe.")
            return True
        except Exception as exc:
            msg = f"DSL Error: {exc}"
            self._log_event(f"[DSL Error] {exc}")
            self.show_toast(msg, level="error")
            return False

    def _apply_template_chip(self, expression: str) -> None:
        self.dsl_entry.delete(0, "end")
        self.dsl_entry.insert(0, expression)
        self.apply_quick_expression(append=False)

    def copy_recipe_to_clipboard(self) -> None:
        recipe = format_actions_to_dsl(self.profile.actions)
        self.clipboard_clear()
        self.clipboard_append(recipe)
        self.show_toast(f"Copied recipe to clipboard ({len(recipe)} chars).", level="info")
        self._log_event(f"Copied Quick-Forge recipe to clipboard ({len(recipe)} chars).")

    # -------------------------------------------------------------------------
    # Execution, Recording & Panic Controls
    # -------------------------------------------------------------------------

    def _refresh_hotkey_displays(self) -> None:
        self.hotkey_sub_lbl.configure(
            text=(
                f"Run: {self.profile.start_stop_hotkey} | "
                f"Rec: {self.profile.record_hotkey} | "
                f"Panic: {self.profile.panic_hotkey}"
            )
        )
        self.top_panic_btn.configure(text=f"Panic Stop ({self.profile.panic_hotkey})")
        if hasattr(self, "simple_panic_btn"):
            self.simple_panic_btn.configure(text=f"Panic Stop ({self.profile.panic_hotkey})")
        if hasattr(self, "simple_hk_start_pill"):
            self.simple_hk_start_pill.configure(text=f"Start: {self.profile.start_stop_hotkey}")
            self.simple_hk_rec_pill.configure(text=f"Rec: {self.profile.record_hotkey}")
            self.simple_hk_panic_pill.configure(text=f"Panic: {self.profile.panic_hotkey}")
        if not self.recorder.is_recording:
            self.rec_toggle_btn.configure(text=f"● Record Live Inputs ({self.profile.record_hotkey})")
        else:
            self.rec_toggle_btn.configure(text=f"⏹ Stop Recording ({self.profile.record_hotkey})")
        self._tick_cache.pop("run_btn", None)
        self._tick_cache.pop("simple_run_btn", None)
        self._update_run_button_ui()

    def sync_profile_settings_to_ui(self) -> None:
        self.repeat_mode_var.set(self.profile.repeat_mode.value)
        val_str = (
            str(self.profile.repeat_duration_s)
            if self.profile.repeat_mode == RepeatMode.DURATION
            else str(self.profile.repeat_count)
        )
        self.repeat_val_entry.delete(0, "end")
        self.repeat_val_entry.insert(0, val_str)
        self.cycle_delay_entry.delete(0, "end")
        self.cycle_delay_entry.insert(0, str(self.profile.inter_cycle_delay_ms))

        self.hk_start_entry.delete(0, "end")
        self.hk_start_entry.insert(0, self.profile.start_stop_hotkey)
        self.hk_rec_entry.delete(0, "end")
        self.hk_rec_entry.insert(0, self.profile.record_hotkey)
        self.hk_panic_entry.delete(0, "end")
        self.hk_panic_entry.insert(0, self.profile.panic_hotkey)

        self._refresh_hotkey_displays()

    def sync_ui_settings_to_profile(self) -> bool:
        try:
            mode = RepeatMode(self.repeat_mode_var.get())
            self.profile.repeat_mode = mode

            # Read repeat value from Simple Mode entry if Simple Mode is currently active and focused/visible
            use_simple_entry = self.ui_mode_var.get() == "Simple" and mode != RepeatMode.INFINITE
            raw_val = (
                self.simple_repeat_val_entry.get().strip() if use_simple_entry else self.repeat_val_entry.get().strip()
            )
            if mode == RepeatMode.DURATION:
                self.profile.repeat_duration_s = float(raw_val)
            elif mode == RepeatMode.COUNT:
                self.profile.repeat_count = int(float(raw_val))

            # Keep both repeat entries synchronized cleanly
            synced_val = (
                str(self.profile.repeat_duration_s) if mode == RepeatMode.DURATION else str(self.profile.repeat_count)
            )
            if self.repeat_val_entry.get().strip() != synced_val:
                self.repeat_val_entry.delete(0, "end")
                self.repeat_val_entry.insert(0, synced_val)
            if self.simple_repeat_val_entry.get().strip() != synced_val:
                self.simple_repeat_val_entry.delete(0, "end")
                self.simple_repeat_val_entry.insert(0, synced_val)

            self.profile.inter_cycle_delay_ms = int(self.cycle_delay_entry.get().strip())
            self.profile.start_stop_hotkey = self.hk_start_entry.get().strip() or "F6"
            self.profile.record_hotkey = self.hk_rec_entry.get().strip() or "F7"
            self.profile.panic_hotkey = self.hk_panic_entry.get().strip() or "F12"
            self.profile.validate()

            self._rebind_hotkeys()
            self._refresh_hotkey_displays()
            self._update_cached_cycle_metrics()
            self.run_security_audit()
            self._refresh_simple_sequence_summary()
            self._log_event(
                f"Applied settings: mode={mode.value}, delay={self.profile.inter_cycle_delay_ms}ms, "
                f"hotkeys=[{self.profile.start_stop_hotkey}/{self.profile.record_hotkey}/{self.profile.panic_hotkey}]."
            )
            return True
        except Exception as exc:
            self._log_event(f"[Settings Error] {exc}")
            self.show_toast(f"Settings Error: {exc}", level="error")
            return False

    def _on_repeat_settings_changed(self, selected: str) -> None:
        mode = RepeatMode(selected)
        self.simple_repeat_mode_var.set(_SIMPLE_REPEAT_LABELS.get(mode, "Forever"))
        self._update_simple_repeat_val_visibility(mode)
        self.sync_ui_settings_to_profile()

    def _on_global_start_stop_hotkey(self) -> None:
        if self.grab_current() is not None:
            return
        self.toggle_execution()

    def _on_global_record_hotkey(self) -> None:
        if self.grab_current() is not None:
            return
        self.toggle_recording()

    def toggle_execution(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
            self._log_event("Macro execution stopped.")
            self._update_run_button_ui()
            return

        if self.recorder.is_recording:
            msg = "Cannot start macro while Live Recorder is active."
            self._log_event(msg)
            self.show_toast(msg, level="warning")
            return

        # Commit any pending Simple Mode text input (e.g., key combo or XY) before starting
        if self.ui_mode_var.get() == "Simple":
            task_key = _LABEL_TO_SIMPLE_TASK.get(self.simple_task_var.get(), "sequence")
            if task_key == "clicker" and not self._apply_simple_clicker_to_profile(record_undo=False):
                return
            if task_key == "spammer" and not self._apply_simple_spammer_to_profile(record_undo=False):
                return

        if not any(a.enabled for a in self.profile.actions):
            msg = "No enabled macro steps to execute. Add or enable at least one step."
            self._log_event(msg)
            self.show_toast(msg, level="warning")
            return

        if not self.sync_ui_settings_to_profile():
            return

        self._last_telem_error = None
        use_dry_run = self.dry_run_var.get()
        if use_dry_run:
            self.dry_run_backend.clear()
            self.executor.backend = self.dry_run_backend
        else:
            self.executor.backend = self.win32_backend
            if not IS_WINDOWS:
                self.show_toast(
                    "Non-Windows OS: native SendInput is Windows-only (use Dry-Run Sim to preview steps).",
                    level="warning",
                )

        started = self.executor.start(self.profile)
        if started:
            mode_label = "DRY-RUN SIMULATION" if use_dry_run else "LIVE OS"
            self._log_event(
                f"Started macro [{self.profile.name}] in {mode_label} mode ({self.profile.repeat_mode.value})."
            )
        self._update_run_button_ui()

    def trigger_panic_stop(self) -> None:
        """Immediate hardware panic kill-switch handler."""
        if self.recorder.is_recording:
            self.recorder.stop()
            self._refresh_hotkey_displays()
        self.executor.panic_abort()
        self._update_run_button_ui()
        self._log_event("PANIC STOP TRIGGERED! Execution aborted and all held keys/buttons released.")
        self.show_toast("Panic Stop triggered — all inputs released.", level="warning")

    def toggle_recording(self) -> None:
        if self.executor.is_running:
            msg = "Stop macro execution before starting Live Recorder."
            self._log_event(msg)
            self.show_toast(msg, level="warning")
            return

        if self.recorder.is_recording:
            recorded = self.recorder.stop()
            self.rec_toggle_btn.configure(
                text=f"● Record Live Inputs ({self.profile.record_hotkey})",
                fg_color=ACCENT_DANGER,
                hover_color=ACCENT_DANGER_HOVER,
            )
            if recorded:
                self._record_undo_snapshot()
                self.profile.actions = recorded
                self._on_sequence_mutated()
                self._log_event(f"Live Recording complete: captured {len(recorded)} step(s).")
                self.show_toast(f"Captured {len(recorded)} recorded step(s).", level="success")
            else:
                self._log_event("Live Recording stopped (0 inputs captured).")
        else:
            if not IS_WINDOWS:
                self.show_toast("Live OS input recording requires Windows.", level="warning")
            self.recorder.config.record_delays = self.rec_delays_var.get()
            self.recorder.config.merge_key_taps = self.rec_merge_var.get()
            self.recorder.config.capture_click_coordinates = self.rec_coords_var.get()
            self.recorder.set_ignored_hotkeys(
                self.profile.record_hotkey,
                self.profile.panic_hotkey,
                self.profile.start_stop_hotkey,
            )
            self.recorder.start(enable_os_poller=True)
            self.rec_toggle_btn.configure(
                text=f"⏹ Stop Recording ({self.profile.record_hotkey})",
                fg_color=ACCENT_WARNING,
                hover_color=ACCENT_WARNING,
            )
            self._log_event("Live Recording started... Press keys or click mouse.")

    # -------------------------------------------------------------------------
    # Presets, Profile I/O & Security Audit
    # -------------------------------------------------------------------------

    def load_preset(self, preset_name: str) -> None:
        if self.executor.is_running:
            self.executor.stop()
        preset = self.presets.get(preset_name)
        if preset is None:
            return
        self._record_undo_snapshot()
        self.profile = MacroProfile.from_payload_dict(preset.to_payload_dict())
        self.sync_profile_settings_to_ui()
        self._rebind_hotkeys()
        self._on_sequence_mutated()
        self._log_event(f"Loaded preset profile: {self.profile.name}")

    def action_new_profile(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
        self._record_undo_snapshot()
        self.profile = MacroProfile(
            name="Custom Macro Profile",
            description="Created in KeyForge",
            repeat_mode=RepeatMode.COUNT,
            repeat_count=5,
            inter_cycle_delay_ms=100,
            actions=[],
        )
        self.sync_profile_settings_to_ui()
        self._rebind_hotkeys()
        self._on_sequence_mutated()
        self._log_event("Created new blank profile.")

    def save_profile_to_path(self, path: str | Path) -> Path:
        self.sync_ui_settings_to_profile()
        saved_path = save_profile_secure(self.profile, path)
        self.app_config.last_profile_path = str(saved_path)
        self._save_persistent_state()
        self._log_event(f"Saved SHA-256 signed profile to: {saved_path.name}")
        return saved_path

    def load_profile_from_path(self, path: str | Path) -> MacroProfile:
        if self.executor.is_running:
            self.executor.stop()
        loaded_profile, sig_verified = load_profile_secure(path, require_signature=False)
        self._record_undo_snapshot()
        self.profile = loaded_profile
        self.app_config.last_profile_path = str(Path(path))
        self.sync_profile_settings_to_ui()
        self._rebind_hotkeys()
        self._on_sequence_mutated()
        sig_msg = "SHA-256 Verified" if sig_verified else "Unsigned JSON (Schema Validated)"
        self._log_event(f"Loaded profile [{loaded_profile.name}] ({sig_msg}).")
        return loaded_profile

    def action_save_profile_dialog(self) -> None:
        file_path = filedialog.asksaveasfilename(
            title="Save KeyForge Macro Profile",
            defaultextension=".json",
            initialfile="my_macro.kfmacro.json",
            filetypes=[("KeyForge Macro JSON", "*.json"), ("KeyForge Macro", "*.kfmacro")],
        )
        if file_path:
            try:
                saved = self.save_profile_to_path(file_path)
                self.show_toast(f"Saved profile: {saved.name}", level="success")
            except Exception as exc:
                self._log_event(f"[Save Error] {exc}")
                self.show_toast(f"Save Error: {exc}", level="error")

    def action_open_profile_dialog(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Open KeyForge Macro Profile",
            filetypes=[("KeyForge Macro JSON", "*.json *.kfmacro")],
        )
        if file_path:
            try:
                loaded = self.load_profile_from_path(file_path)
                self.show_toast(f"Loaded profile: {loaded.name}", level="success")
            except Exception as exc:
                self._log_event(f"[Load Security Error] {exc}")
                self.show_toast(f"Load Error: {exc}", level="error")

    def _focus_security_audit(self) -> None:
        if self.ui_mode_var.get() != "Studio":
            self._switch_ui_mode("Studio")
        self.inspector_tabs.set("Security")
        self._refresh_security_tab_details()

    def _on_inspector_tab_changed(self) -> None:
        if self._security_tab_dirty and self.inspector_tabs.get() == "Security":
            self._refresh_security_tab_details()

    def _refresh_security_tab_details(self) -> None:
        report = self._last_audit_report or audit_profile(self.profile)
        with contextlib.suppress(Exception):
            digest = compute_payload_sha256(self.profile.to_payload_dict())
            nodes, ops = validate_profile_complexity(self.profile)
            self.sec_hash_lbl.configure(text=f"SHA-256: {digest[:24]}...{digest[-8:]}")
            self.sec_budget_lbl.configure(
                text=f"Complexity Budget: {nodes} / 5,000 steps | {ops} / 100,000 expanded ops"
            )
            self.sec_report_box.delete("1.0", "end")
            if report.is_clean:
                self.sec_report_box.insert(
                    "end",
                    "✓ STATIC SECURITY AUDIT PASSED\n\n"
                    "• Zero arbitrary code execution vectors\n"
                    "• Safe cycle wait timing verified\n"
                    "• All key/mouse down states balanced\n"
                    "• No suspicious shell shortcuts (Win+R / Win+X) detected\n",
                )
            else:
                for f in report.findings:
                    self.sec_report_box.insert(
                        "end",
                        f"[{f.code}] ({f.severity.value.upper()}) {f.title}\n"
                        f"  Detail: {f.description}\n"
                        f"  Fix:    {f.remediation}\n\n",
                    )
            self._security_tab_dirty = False

    def run_security_audit(self) -> AuditReport:
        report = audit_profile(self.profile)
        self._last_audit_report = report
        self._security_tab_dirty = True

        crit = sum(1 for f in report.findings if f.severity == AuditSeverity.CRITICAL)
        warn = sum(1 for f in report.findings if f.severity == AuditSeverity.WARNING)
        if crit > 0:
            self.audit_badge.configure(
                text=f"Alert: {crit} Crit",
                fg_color=BADGE_CRIT_BG,
                text_color=BADGE_CRIT_FG,
            )
        elif warn > 0:
            self.audit_badge.configure(
                text=f"Notice ({warn})",
                fg_color=BADGE_WARN_BG,
                text_color=BADGE_WARN_FG,
            )
        else:
            self.audit_badge.configure(
                text="Audit OK",
                fg_color=BADGE_OK_BG,
                text_color=BADGE_OK_FG,
            )

        # Defer heavy SHA-256 hashing & textbox rebuild unless Security tab is currently visible
        if self.ui_mode_var.get() == "Studio" and self.inspector_tabs.get() == "Security":
            self._refresh_security_tab_details()

        active_codes = tuple(
            f.code for f in report.findings if f.severity in (AuditSeverity.WARNING, AuditSeverity.CRITICAL)
        )
        if active_codes != self._last_logged_finding_codes:
            self._last_logged_finding_codes = active_codes
            for finding in report.findings:
                if finding.severity in (AuditSeverity.WARNING, AuditSeverity.CRITICAL):
                    self._log_event(f"[{finding.code}] {finding.title}: {finding.description}")

        return report

    # -------------------------------------------------------------------------
    # Auto-Update Integration (Queue-Based Main-Thread Drain)
    # -------------------------------------------------------------------------

    def _check_startup_updates(self) -> None:
        self._startup_update_after_id = None
        if self._closing or self._startup_update_checking:
            return
        self._startup_update_checking = True
        self._schedule_startup_update_poll()
        threading.Thread(
            target=self._startup_update_worker,
            name="KeyForge-StartupUpdate",
            daemon=True,
        ).start()

    def _startup_update_worker(self) -> None:
        result: ReleaseInfo | None = None
        try:
            rel = fetch_latest_release(current_version=__version__, repo=DEFAULT_REPO, timeout=8.0)
            if rel.is_update_available:
                result = rel
        except Exception:
            result = None
        finally:
            if not self._closing:
                self._startup_update_queue.put(result)

    def _schedule_startup_update_poll(self) -> None:
        if self._closing or self._startup_update_poll_after_id is not None:
            return
        with contextlib.suppress(Exception):
            if self.winfo_exists():
                self._startup_update_poll_after_id = self.after(40, self._poll_startup_update_queue)

    def _poll_startup_update_queue(self) -> None:
        self._startup_update_poll_after_id = None
        if self._closing:
            return
        with contextlib.suppress(Exception):
            if not self.winfo_exists():
                return
        self._drain_startup_update_queue()
        if self._startup_update_checking and not self._closing:
            self._schedule_startup_update_poll()

    def _drain_startup_update_queue(self) -> None:
        if self._closing:
            while True:
                try:
                    self._startup_update_queue.get_nowait()
                except queue.Empty:
                    break
            self._startup_update_checking = False
            return
        with contextlib.suppress(Exception):
            if not self.winfo_exists():
                return
        while True:
            try:
                release = self._startup_update_queue.get_nowait()
            except queue.Empty:
                break
            self._startup_update_checking = False
            if release is not None and release.is_update_available:
                self._on_startup_update_available(release)

    def _on_startup_update_available(self, release: ReleaseInfo) -> None:
        self._latest_release = release
        if not release.is_update_available:
            return

        self.updates_btn.configure(
            text=f"✨ Update v{release.version}",
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            text_color=TEXT_PRIMARY,
        )
        if hasattr(self, "simple_update_link"):
            self.simple_update_link.configure(
                text=f"✨ Update v{release.version} available — Install",
                text_color=ACCENT_SUCCESS,
            )
        if not self._update_banner_dismissed:
            self.update_banner_lbl.configure(
                text=(
                    f"✨ KeyForge v{release.version} is available — "
                    "Update cleanly while keeping all your configs & macros"
                )
            )
            if not self.update_banner.winfo_manager():
                self.update_banner.pack(side="top", fill="x", padx=16, pady=(6, 0), before=self._workspace_host)

        self._log_event(f"Update available: KeyForge v{release.version} ({release.tag_name}).")

    def _on_release_checked(self, release: ReleaseInfo) -> None:
        self._latest_release = release
        if release.is_update_available:
            self._on_startup_update_available(release)
        else:
            self.updates_btn.configure(
                text="Updates",
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                text_color=TEXT_PRIMARY,
            )
            if hasattr(self, "simple_update_link"):
                self.simple_update_link.configure(
                    text=f"✓ Up to date (v{__version__})",
                    text_color=TEXT_SECONDARY,
                )
            if self.update_banner.winfo_manager():
                self.update_banner.pack_forget()

    def open_update_dialog(self) -> None:
        if self._update_dialog is not None:
            with contextlib.suppress(Exception):
                if self._update_dialog.winfo_exists():
                    self._update_dialog.lift()
                    self._update_dialog.focus_force()
                    return
        self._update_dialog = UpdateDialog(
            self,
            initial_release=self._latest_release,
            on_release_checked=self._on_release_checked,
            on_before_restart=self._prepare_for_update_restart,
        )

    def _prepare_for_update_restart(self) -> None:
        """Persist config & active profile and release all background hooks before hot-swap restart."""
        self._save_persistent_state()
        self.hotkey_manager.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
        if self.executor.is_running:
            self.executor.panic_abort()

    # -------------------------------------------------------------------------
    # Background Hotkey & Dirty-Checked Telemetry Loop
    # -------------------------------------------------------------------------

    def _enqueue_hotkey_action(self, action_name: str) -> None:
        if not self._closing:
            self._hotkey_queue.put(action_name)

    def _enqueue_panic_hotkey(self) -> None:
        if self._closing:
            return
        # Immediately abort executor on the background hotkey thread (<5ms latency),
        # then post UI state/toast updates strictly to the Tk main thread.
        self.executor.panic_abort()
        self._hotkey_queue.put("panic")

    def _rebind_hotkeys(self) -> None:
        self.hotkey_manager.clear()
        self.hotkey_manager.register(
            "start_stop",
            self.profile.start_stop_hotkey,
            lambda: self._enqueue_hotkey_action("start_stop"),
        )
        self.hotkey_manager.register(
            "panic",
            self.profile.panic_hotkey,
            self._enqueue_panic_hotkey,
        )
        self.hotkey_manager.register(
            "record",
            self.profile.record_hotkey,
            lambda: self._enqueue_hotkey_action("record"),
        )

    def _schedule_hotkey_poll(self) -> None:
        if self._closing or not self._hotkey_listener_enabled or self._hotkey_poll_after_id is not None:
            return
        with contextlib.suppress(Exception):
            if self.winfo_exists():
                self._hotkey_poll_after_id = self.after(40, self._poll_hotkey_queue)

    def _poll_hotkey_queue(self) -> None:
        self._hotkey_poll_after_id = None
        if self._closing:
            return
        with contextlib.suppress(Exception):
            if not self.winfo_exists():
                return
        self._drain_hotkey_queue()
        if not self._closing and self._hotkey_listener_enabled:
            self._schedule_hotkey_poll()

    def _drain_hotkey_queue(self) -> None:
        if self._closing:
            while True:
                try:
                    self._hotkey_queue.get_nowait()
                except queue.Empty:
                    break
            return
        with contextlib.suppress(Exception):
            if not self.winfo_exists():
                return
        while True:
            try:
                action_name = self._hotkey_queue.get_nowait()
            except queue.Empty:
                break
            if action_name == "start_stop":
                self._on_global_start_stop_hotkey()
            elif action_name == "panic":
                self.trigger_panic_stop()
            elif action_name == "record":
                self._on_global_record_hotkey()

    def _update_run_button_ui(self) -> None:
        running = self.executor.is_running
        key_state = (running, self.profile.start_stop_hotkey)
        if self._tick_cache.get("run_btn") != key_state:
            self._tick_cache["run_btn"] = key_state
            if running:
                self.run_toggle_btn.configure(
                    text=f"⏹ STOP MACRO ({self.profile.start_stop_hotkey})",
                    fg_color=ACCENT_DANGER,
                    hover_color=ACCENT_DANGER_HOVER,
                )
            else:
                self.run_toggle_btn.configure(
                    text=f"▶ START MACRO ({self.profile.start_stop_hotkey})",
                    fg_color=CTA_SUCCESS,
                    hover_color=CTA_SUCCESS_HOVER,
                )

        if hasattr(self, "simple_run_btn") and self._tick_cache.get("simple_run_btn") != key_state:
            self._tick_cache["simple_run_btn"] = key_state
            if running:
                self.simple_run_btn.configure(
                    text=f"⏹ Stop Macro ({self.profile.start_stop_hotkey})",
                    fg_color=ACCENT_DANGER,
                    hover_color=ACCENT_DANGER_HOVER,
                )
            else:
                self.simple_run_btn.configure(
                    text=f"▶ Start Macro ({self.profile.start_stop_hotkey})",
                    fg_color=CTA_SUCCESS,
                    hover_color=CTA_SUCCESS_HOVER,
                )

    def _schedule_ui_tick(self) -> None:
        if self._closing:
            return
        with contextlib.suppress(Exception):
            if not self.winfo_exists():
                return
        is_running = self._ui_tick()
        interval_ms = 100 if is_running else 350
        self._ui_tick_after_id = self.after(interval_ms, self._schedule_ui_tick)

    def _ui_tick(self) -> bool:
        self._drain_hotkey_queue()
        self._drain_startup_update_queue()
        telem: ExecutionTelemetry = self.executor.telemetry
        is_running = self.executor.is_running or telem.state in (ExecutorState.RUNNING, ExecutorState.STOPPING)
        self._update_run_button_ui()

        if telem.state == ExecutorState.ERROR and telem.last_error and telem.last_error != self._last_telem_error:
            self._last_telem_error = telem.last_error
            self._log_event(f"[Execution Error] {telem.last_error}")
            self.show_toast(f"Execution Error: {telem.last_error}", level="error")

        state_text = f"● {telem.state.value.upper()}"
        state_color = {
            ExecutorState.IDLE: ACCENT_SUCCESS,
            ExecutorState.RUNNING: ACCENT_PRIMARY,
            ExecutorState.STOPPING: ACCENT_WARNING,
            ExecutorState.PANIC_ABORTED: ACCENT_DANGER,
            ExecutorState.ERROR: ACCENT_DANGER,
        }[telem.state]

        simple_status_text = {
            ExecutorState.IDLE: "● Ready",
            ExecutorState.RUNNING: "● Running",
            ExecutorState.STOPPING: "● Stopping",
            ExecutorState.PANIC_ABORTED: "● Stopped (Panic)",
            ExecutorState.ERROR: "● Error",
        }[telem.state]

        if self._tick_cache.get("state") != (state_text, state_color):
            self._tick_cache["state"] = (state_text, state_color)
            self.status_lbl.configure(text=state_text, text_color=state_color)
            self.simple_status_lbl.configure(text=simple_status_text, text_color=state_color)

        mode_text = f"Mode: {'Dry-Run Simulation' if self.dry_run_var.get() else 'Live OS Injection'}"
        if self._tick_cache.get("mode_text") != mode_text:
            self._tick_cache["mode_text"] = mode_text
            self.mode_sub_lbl.configure(text=mode_text)

        cycles_text = f"{telem.cycles_completed} Cycles | {telem.actions_executed} Actions"
        if self._tick_cache.get("cycles_text") != cycles_text:
            self._tick_cache["cycles_text"] = cycles_text
            self.cycles_lbl.configure(text=cycles_text)

        simple_telem = (
            f"{telem.cycles_completed} cycles  •  {telem.actions_executed} actions  •  {telem.elapsed_seconds:.1f}s"
        )
        if self._tick_cache.get("simple_telem") != simple_telem:
            self._tick_cache["simple_telem"] = simple_telem
            self.simple_telem_lbl.configure(text=simple_telem)

        if self.profile.repeat_mode == RepeatMode.COUNT and self.profile.repeat_count > 0:
            ratio = min(1.0, telem.cycles_completed / float(self.profile.repeat_count))
        elif self.profile.repeat_mode == RepeatMode.DURATION and self.profile.repeat_duration_s > 0:
            ratio = min(1.0, telem.elapsed_seconds / float(self.profile.repeat_duration_s))
        else:
            ratio = 1.0 if telem.state == ExecutorState.RUNNING else 0.0

        ratio_q = round(ratio, 3)
        if self._tick_cache.get("progress") != ratio_q:
            self._tick_cache["progress"] = ratio_q
            self.cycle_progress.set(ratio)

        actions_text = f"Cycle: {self._cached_cycle_wait_ms}ms | Elapsed: {telem.elapsed_seconds:.1f}s"
        if self._tick_cache.get("actions_text") != actions_text:
            self._tick_cache["actions_text"] = actions_text
            self.actions_lbl.configure(text=actions_text)

        # Skip cursor polling when minimized or when Studio Mode isn't visible
        is_minimized = False
        with contextlib.suppress(Exception):
            is_minimized = self.state() == "iconic"

        if not is_minimized and self.ui_mode_var.get() == "Studio":
            if IS_WINDOWS:
                cx, cy = self.win32_backend.get_cursor_pos()
            else:
                cx, cy = self.winfo_pointerx(), self.winfo_pointery()
            cursor_text = f"XY: ({cx}, {cy})"
            if self._tick_cache.get("cursor_text") != cursor_text:
                self._tick_cache["cursor_text"] = cursor_text
                self.cursor_lbl.configure(text=cursor_text)

        # Highlight currently executing step card only when changed
        if telem.current_action_id != self._active_step_id:
            prev_id = self._active_step_id
            self._active_step_id = telem.current_action_id
            if prev_id and prev_id in self._step_row_frames:
                self._step_row_frames[prev_id].configure(fg_color=BG_CARD)
            if self._active_step_id and self._active_step_id in self._step_row_frames:
                self._step_row_frames[self._active_step_id].configure(fg_color=BG_CARD_ACTIVE)

        return is_running

    def _log_event(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self.console_box.insert("end", line)
        self.console_box.see("end")
        if hasattr(self, "footer_status_lbl"):
            self.footer_status_lbl.configure(text=f"● [{ts}] {message}")

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._startup_update_checking = False

        if self._update_dialog is not None:
            with contextlib.suppress(Exception):
                if not self._update_dialog._closed:
                    self._update_dialog._on_close()
            self._update_dialog = None

        for after_attr in (
            "_ui_tick_after_id",
            "_startup_update_after_id",
            "_startup_update_poll_after_id",
            "_hotkey_poll_after_id",
            "_filter_after_id",
            "_simple_slider_after_id",
            "_toast_after_id",
            "_kf_icon_after_id",
        ):
            after_id = getattr(self, after_attr, None)
            if after_id is not None:
                with contextlib.suppress(Exception):
                    self.after_cancel(after_id)
                setattr(self, after_attr, None)

        self._drain_startup_update_queue()
        self._drain_hotkey_queue()

        self._save_persistent_state()
        self.hotkey_manager.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
        if self.executor.is_running:
            self.executor.panic_abort()
        with contextlib.suppress(Exception):
            self.destroy()
        with contextlib.suppress(Exception):
            if not ctk.ScalingTracker.window_widgets_dict:
                ctk.ScalingTracker.update_loop_running = False
