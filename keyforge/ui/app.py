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
    delete_custom_preset,
    load_active_profile_backup,
    load_app_config,
    load_custom_presets,
    save_active_profile_backup,
    save_app_config,
    save_custom_preset,
)
from keyforge.engine.backend import DryRunBackend
from keyforge.engine.executor import ExecutionTelemetry, ExecutorState, MacroExecutor
from keyforge.engine.hotkeys import GlobalHotkeyManager
from keyforge.engine.platform_input import (
    create_os_input_backend,
    get_platform_capabilities,
    platform_supports_key_polling,
)
from keyforge.engine.recorder import MacroRecorder, RecorderConfig
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
from keyforge.models.keymap import (
    finalize_modifier_only_capture,
    format_captured_key_combo,
    list_common_keys,
    normalize_key_combo,
)
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
from keyforge.ui.mini_hud import MiniHudWindow
from keyforge.ui.step_dialog import StepEditorDialog
from keyforge.ui.theme import (
    ACCENT_DANGER,
    ACCENT_DANGER_HOVER,
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    ACCENT_SUCCESS,
    ACCENT_WARNING,
    ACCENT_WARNING_HOVER,
    BADGE_CRIT_BG,
    BADGE_CRIT_FG,
    BADGE_OK_BG,
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
    BORDER_CONTROL,
    BORDER_FOCUS,
    BORDER_SUBTLE,
    BTN_DANGER_BORDER,
    BTN_DANGER_SUBTLE,
    BTN_DANGER_SUBTLE_TEXT,
    BTN_NEUTRAL,
    BTN_NEUTRAL_HOVER,
    BTN_SUBTLE,
    BTN_SUBTLE_HOVER,
    CTA_SUCCESS,
    CTA_SUCCESS_HOVER,
    CTA_TEXT,
    SEGMENT_ACTIVE,
    SEGMENT_ACTIVE_HOVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    apply_window_icon,
    cancel_all_tcl_after_timers,
    center_window_on_parent,
    get_badge_colors,
    get_fonts,
    get_geometry_coord_scale,
    get_logical_workarea,
    load_brand_ctk_image,
)
from keyforge.ui.update_dialog import UpdateDialog
from keyforge.updater import DEFAULT_REPO, ReleaseInfo, fetch_latest_release

MAX_UNDO_HISTORY = 40
MAX_CONSOLE_LINES = 200
MAX_STEP_ROW_POOL = 64
_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$")

_SIMPLE_REPEAT_LABELS = {
    RepeatMode.INFINITE: "Forever",
    RepeatMode.COUNT: "Count",
    RepeatMode.DURATION: "Duration",
}
_LABEL_TO_REPEAT_MODE = {v: k for k, v in _SIMPLE_REPEAT_LABELS.items()}

_STUDIO_REPEAT_LABELS = {
    RepeatMode.COUNT: "Fixed Count",
    RepeatMode.INFINITE: "Infinite",
    RepeatMode.DURATION: "Duration",
}
_LABEL_TO_STUDIO_REPEAT = {
    "Fixed Count": RepeatMode.COUNT,
    "Infinite": RepeatMode.INFINITE,
    "Duration": RepeatMode.DURATION,
    RepeatMode.COUNT.value: RepeatMode.COUNT,
    RepeatMode.INFINITE.value: RepeatMode.INFINITE,
    RepeatMode.DURATION.value: RepeatMode.DURATION,
}

_SIMPLE_TASK_LABELS = {
    "spammer": "Key Repeater",
    "sequence": "Quick Sequence",
}
_LABEL_TO_SIMPLE_TASK = {
    "Key Repeater": "spammer",
    "Key Spammer": "spammer",
    "Quick Sequence": "sequence",
}


def _parse_repeat_mode(value: str) -> RepeatMode:
    if value in _LABEL_TO_STUDIO_REPEAT:
        return _LABEL_TO_STUDIO_REPEAT[value]
    return RepeatMode(value)


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
        self._builtin_preset_names: set[str] = set()
        self.presets: dict[str, MacroProfile] = {}
        self._reload_presets_catalog()
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
        self.os_backend = create_os_input_backend()
        self.win32_backend = self.os_backend  # Backward-compatible attribute alias
        self._platform_caps = get_platform_capabilities(self.os_backend)
        self.dry_run_backend = DryRunBackend()
        self.executor = MacroExecutor(backend=self.os_backend)
        self.recorder = MacroRecorder(config=RecorderConfig())
        self.hotkey_manager = GlobalHotkeyManager()

        # Internal UI & Performance State
        self._closing = False
        self._hotkey_listener_enabled = bool(start_hotkey_listener and platform_supports_key_polling())
        self._active_step_id: str | None = None
        self._selected_step_index: int | None = 0 if self.profile.actions else None
        self._visible_step_indices: list[int] = []
        self._step_row_frames: dict[str, ctk.CTkFrame] = {}
        self._step_slots_by_id: dict[str, dict[str, Any]] = {}
        self._step_row_pool: list[dict[str, Any]] = []
        self._empty_sequence_lbl: ctk.CTkLabel | None = None
        self._console_line_count: int = 0
        self._key_capture_target: str | None = None
        self._key_capture_pending_mod: str | None = None
        self._ui_tick_after_id: str | None = None
        self._startup_update_after_id: str | None = None
        self._startup_update_poll_after_id: str | None = None
        self._hotkey_poll_after_id: str | None = None
        self._filter_after_id: str | None = None
        self._simple_slider_after_id: str | None = None
        self._toast_after_id: str | None = None
        self._countdown_remaining_ms: int = 0
        self._countdown_after_id: str | None = None
        self._mini_hud: MiniHudWindow | None = None
        self._filter_query: str = ""
        self._step_type_filter: str = "all"
        self._step_filter_buttons: dict[str, ctk.CTkButton] = {}
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
        self.repeat_mode_var = ctk.StringVar(value=_STUDIO_REPEAT_LABELS.get(self.profile.repeat_mode, "Infinite"))
        self.simple_repeat_mode_var = ctk.StringVar(
            value=_SIMPLE_REPEAT_LABELS.get(self.profile.repeat_mode, "Forever")
        )
        self.preset_var = ctk.StringVar(value=self.profile.name)
        self.countdown_var = ctk.StringVar(value=f"{self.app_config.start_countdown_sec}s")

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
        self.bind("<Control-o>", lambda _e: self.action_open_profile_dialog())
        self.bind("<Control-n>", lambda _e: self.action_new_profile())
        self.bind("<Control-d>", self._on_ctrl_d)
        if sys.platform == "darwin":
            self.bind("<Command-z>", self._on_ctrl_z)
            self.bind("<Command-Shift-Z>", self._on_ctrl_y)
            self.bind("<Command-y>", self._on_ctrl_y)
            self.bind("<Command-s>", lambda _e: self.action_save_profile_dialog())
            self.bind("<Command-o>", lambda _e: self.action_open_profile_dialog())
            self.bind("<Command-n>", lambda _e: self.action_new_profile())
            self.bind("<Command-d>", self._on_ctrl_d)
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<BackSpace>", self._on_delete_key)
        self.bind("<space>", self._on_space_key)
        self.bind("<Alt-Up>", self._on_alt_up)
        self.bind("<Alt-Down>", self._on_alt_down)
        self.bind("<Up>", self._on_arrow_up)
        self.bind("<Down>", self._on_arrow_down)
        self.bind("<KeyPress-slash>", self._on_slash_key)
        self.bind("<Escape>", self._on_main_escape)

        # Initial Render & Audit
        self.refresh_sequence_view()
        self.sync_profile_settings_to_ui()
        self._sync_profile_to_simple_view()
        self.run_security_audit()
        self._log_event(
            f"KeyForge v{__version__} ({_platform_badge_label()} · {self._platform_caps.backend_name}) ready. "
            f"Panic Stop armed on [{self.profile.panic_hotkey}]."
        )

        # Global Hotkeys
        self._rebind_hotkeys()
        if self._hotkey_listener_enabled:
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
        coord_scale = get_geometry_coord_scale(scale)
        avail_w = max(640, work_w - 40)
        avail_h = max(400, work_h - 48)
        target_w = min(1140, avail_w)
        target_h = min(720, avail_h)
        min_w = min(960, target_w)
        min_h = min(660, target_h)
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
                    if _is_point_on_screen(self, x_px, y_px, round(w * coord_scale), round(h * coord_scale)):
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
            self.app_config.simple_spammer = {
                "key": self.ss_key_combo.get().strip() or "E",
                "interval_ms": int(round(self.ss_interval_slider.get())),
                "jitter_enabled": bool(self.ss_jitter_var.get()),
            }

    # -------------------------------------------------------------------------
    # Top Bar, Banners & Status Bar
    # -------------------------------------------------------------------------

    def _build_top_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0, height=50)
        bar.pack(side="top", fill="x", padx=0, pady=0)
        bar.pack_propagate(False)

        ctk.CTkFrame(self, fg_color=BORDER_SUBTLE, height=1, corner_radius=0).pack(side="top", fill="x")

        # Left Brand Group (intentionally clean, mirroring minimal desktop craft headers)
        self._brand_icon_img = load_brand_ctk_image((26, 26))
        if self._brand_icon_img is not None:
            ctk.CTkLabel(bar, text="", image=self._brand_icon_img).pack(side="left", padx=(16, 8), pady=12)

        ctk.CTkLabel(
            bar,
            text="KeyForge",
            font=self.fonts["brand"],
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=(0, 8), pady=12)

        ctk.CTkLabel(
            bar,
            text=f"v{__version__}",
            font=self.fonts["micro_bold"],
            fg_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            corner_radius=6,
            padx=7,
            pady=2,
        ).pack(side="left", padx=(0, 12), pady=13)

        # Right Action Cluster (fits with ample margin even at 920px minimum width)
        self.top_panic_btn = ctk.CTkButton(
            bar,
            text=f"Panic ({self.profile.panic_hotkey})",
            fg_color=BTN_DANGER_SUBTLE,
            hover_color=ACCENT_DANGER,
            border_width=1,
            border_color=BTN_DANGER_BORDER,
            text_color=BTN_DANGER_SUBTLE_TEXT,
            font=self.fonts["small_bold"],
            height=28,
            width=0,
            corner_radius=7,
            command=self.trigger_panic_stop,
        )
        self.top_panic_btn.pack(side="right", padx=(4, 14), pady=11)

        self.mini_hud_btn = ctk.CTkButton(
            bar,
            text="Mini HUD",
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            height=28,
            width=74,
            corner_radius=7,
            command=self.toggle_mini_hud_mode,
        )
        self.mini_hud_btn.pack(side="right", padx=3, pady=11)

        self.updates_btn = ctk.CTkButton(
            bar,
            text="Updates",
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            height=28,
            width=76,
            corner_radius=7,
            command=self.open_update_dialog,
        )
        self.updates_btn.pack(side="right", padx=3, pady=11)

        ctk.CTkFrame(bar, fg_color=BORDER_SUBTLE, width=1, height=18).pack(side="right", padx=6, pady=16)

        for label, width, cmd in (
            ("Save", 48, self.action_save_profile_dialog),
            ("Open", 48, self.action_open_profile_dialog),
            ("New", 44, self.action_new_profile),
        ):
            ctk.CTkButton(
                bar,
                text=label,
                width=width,
                height=28,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                border_width=1,
                border_color=BORDER_CONTROL,
                text_color=TEXT_SECONDARY,
                corner_radius=7,
                font=self.fonts["small_bold"],
                command=cmd,
            ).pack(side="right", padx=2, pady=11)

        ctk.CTkFrame(bar, fg_color=BORDER_SUBTLE, width=1, height=18).pack(side="right", padx=6, pady=16)

        # Simple | Studio Calm Slate Switcher
        self.mode_switcher = ctk.CTkSegmentedButton(
            bar,
            values=["Simple", "Studio"],
            variable=self.ui_mode_var,
            command=self._on_ui_mode_changed,
            width=136,
            height=28,
            corner_radius=7,
            fg_color=BG_INPUT,
            selected_color=SEGMENT_ACTIVE,
            selected_hover_color=SEGMENT_ACTIVE_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
        )
        self.mode_switcher.pack(side="right", padx=2, pady=11)

    def _build_banners(self) -> None:
        """Build collapsible Update Banner and Transient Inline Notification Strip."""
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
            text_color=CTA_TEXT,
            corner_radius=6,
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

        dup_hint = "⌘D Dup" if sys.platform == "darwin" else "Ctrl+D Dup"
        ctk.CTkLabel(
            sbar,
            text=f"/ Filter  ·  Del Delete  ·  {dup_hint}  ·  Space Toggle  ·  Alt+↑/↓ Move  ·  Dbl-Click Edit",
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
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        l_top = ctk.CTkFrame(left_card, fg_color="transparent")
        l_top.pack(fill="x", padx=18, pady=(14, 8))
        ctk.CTkLabel(
            l_top,
            text="ACTION MODE",
            font=self.fonts["micro_bold"],
            text_color=TEXT_MUTED,
        ).pack(anchor="w")
        ctk.CTkLabel(
            l_top,
            text="Choose Action",
            font=self.fonts["title"],
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(2, 0))

        initial_task_label = _SIMPLE_TASK_LABELS.get(self.app_config.simple_task, "Key Repeater")
        self.simple_task_var = ctk.StringVar(value=initial_task_label)
        self.simple_task_seg = ctk.CTkSegmentedButton(
            left_card,
            values=["Key Repeater", "Quick Sequence"],
            variable=self.simple_task_var,
            command=self._on_simple_task_changed,
            height=34,
            corner_radius=8,
            fg_color=BG_INPUT,
            selected_color=SEGMENT_ACTIVE,
            selected_hover_color=SEGMENT_ACTIVE_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            font=self.fonts["body_bold"],
        )
        self.simple_task_seg.pack(fill="x", padx=18, pady=(0, 12))

        self.simple_task_body = ctk.CTkFrame(left_card, fg_color="transparent")
        self.simple_task_body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.ss_panel = ctk.CTkFrame(self.simple_task_body, fg_color="transparent")
        self.sq_panel = ctk.CTkFrame(self.simple_task_body, fg_color="transparent")

        self._build_simple_spammer_panel(self.ss_panel)
        self._build_simple_sequence_panel(self.sq_panel)

        # Right Card: Control Center
        right_card = ctk.CTkFrame(
            container,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        right_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._build_simple_control_center(right_card)

    def _build_simple_spammer_panel(self, panel: ctk.CTkFrame) -> None:
        cfg = self.app_config.simple_spammer

        r_key = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        r_key.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            r_key, text="KEY OR SHORTCUT TO REPEAT", font=self.fonts["micro_bold"], text_color=TEXT_MUTED
        ).pack(anchor="w", padx=14, pady=(12, 6))
        key_row = ctk.CTkFrame(r_key, fg_color="transparent")
        key_row.pack(fill="x", padx=14, pady=(0, 12))

        self.ss_key_combo = ctk.CTkComboBox(
            key_row,
            values=list_common_keys(),
            width=180,
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
        self.ss_key_combo.bind("<Return>", lambda _e: self._apply_simple_spammer_to_profile())

        self.ss_bind_key_btn = ctk.CTkButton(
            key_row,
            text="Bind Key",
            width=84,
            height=32,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=lambda: self.start_main_key_capture("simple_spammer"),
        )
        self.ss_bind_key_btn.pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            key_row,
            text="Apply",
            width=68,
            height=32,
            fg_color=SEGMENT_ACTIVE,
            hover_color=SEGMENT_ACTIVE_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=ACCENT_SUCCESS,
            font=self.fonts["small_bold"],
            command=self._apply_simple_spammer_to_profile,
        ).pack(side="left", padx=(6, 0))

        # Key Interval Slider
        r_spd = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        r_spd.pack(fill="x")
        spd_hdr = ctk.CTkFrame(r_spd, fg_color="transparent")
        spd_hdr.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(spd_hdr, text="REPEAT INTERVAL", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(
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
            fg_color=BG_INPUT,
            button_color=ACCENT_PRIMARY,
            button_hover_color=ACCENT_PRIMARY_HOVER,
            progress_color=ACCENT_PRIMARY,
            command=self._on_ss_slider_moved,
        )
        self.ss_interval_slider.set(init_ms)
        self.ss_interval_slider.pack(fill="x", padx=14, pady=(2, 8))

        chips_row = ctk.CTkFrame(r_spd, fg_color="transparent")
        chips_row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(chips_row, text="Presets:", font=self.fonts["small"], text_color=TEXT_MUTED).pack(
            side="left", padx=(0, 6)
        )
        for ms in (25, 50, 100, 250, 500):
            ctk.CTkButton(
                chips_row,
                text=f"{ms}ms",
                width=48,
                height=24,
                fg_color=BTN_SUBTLE,
                hover_color=BTN_SUBTLE_HOVER,
                border_width=1,
                border_color=BORDER_SUBTLE,
                text_color=TEXT_SECONDARY,
                font=self.fonts["small"],
                command=lambda target_ms=ms: self._set_simple_spammer_interval(target_ms),
            ).pack(side="left", padx=2)

        self.ss_jitter_var = ctk.BooleanVar(value=bool(cfg.get("jitter_enabled", True)))
        ctk.CTkSwitch(
            r_spd,
            text="Humanized timing variance (±10%)",
            variable=self.ss_jitter_var,
            progress_color=CTA_SUCCESS,
            button_color=TEXT_PRIMARY,
            button_hover_color="#FFFFFF",
            font=self.fonts["small"],
            command=self._apply_simple_spammer_to_profile,
        ).pack(anchor="w", padx=14, pady=(0, 12))

    def _build_simple_sequence_panel(self, panel: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        card.pack(fill="both", expand=True)

        ctk.CTkLabel(
            card,
            text="ACTIVE MACRO SEQUENCE",
            font=self.fonts["micro_bold"],
            text_color=TEXT_MUTED,
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
            text="QUICK RECIPE DSL",
            font=self.fonts["micro_bold"],
            text_color=TEXT_MUTED,
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
            fg_color=SEGMENT_ACTIVE,
            hover_color=SEGMENT_ACTIVE_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=ACCENT_SUCCESS,
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
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=lambda: self._switch_ui_mode("Studio"),
        ).pack(side="right")

        ctk.CTkLabel(
            card,
            text="Syntax: Press KEY -> Wait MS ±JITTER -> Click -> Move X,Y -> Scroll DY -> Repeat N { ... }",
            font=self.fonts["micro"],
            text_color=TEXT_MUTED,
            anchor="w",
            wraplength=440,
            justify="left",
        ).pack(fill="x", padx=14, pady=(0, 12))

    def _build_simple_control_center(self, card: ctk.CTkFrame) -> None:
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkLabel(
            hdr,
            text="CONTROL CENTER",
            font=self.fonts["micro_bold"],
            text_color=TEXT_MUTED,
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr,
            text="Execution & Timing",
            font=self.fonts["title"],
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(2, 0))

        # Status Card
        status_box = ctk.CTkFrame(card, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        status_box.pack(fill="x", padx=16, pady=(0, 10))

        self.simple_status_lbl = ctk.CTkLabel(
            status_box,
            text="● Ready",
            font=self.fonts["hero"],
            text_color=ACCENT_SUCCESS,
        )
        self.simple_status_lbl.pack(anchor="w", padx=14, pady=(10, 1))

        self.simple_telem_lbl = ctk.CTkLabel(
            status_box,
            text="0 cycles  •  0 actions  •  0.0s",
            font=self.fonts["mono_small"],
            text_color=TEXT_SECONDARY,
        )
        self.simple_telem_lbl.pack(anchor="w", padx=14, pady=(0, 10))

        # Primary Start/Stop & Panic CTAs
        self.simple_run_btn = ctk.CTkButton(
            card,
            text=f"▶ Start Macro ({self.profile.start_stop_hotkey})",
            height=40,
            corner_radius=8,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            text_color=CTA_TEXT,
            font=self.fonts["cta"],
            command=self.toggle_execution,
        )
        self.simple_run_btn.pack(fill="x", padx=16, pady=(0, 6))

        self.simple_panic_btn = ctk.CTkButton(
            card,
            text=f"Panic Stop ({self.profile.panic_hotkey})",
            height=32,
            corner_radius=8,
            fg_color=BTN_DANGER_SUBTLE,
            hover_color=ACCENT_DANGER,
            border_width=1,
            border_color=BTN_DANGER_BORDER,
            text_color=BTN_DANGER_SUBTLE_TEXT,
            font=self.fonts["body_bold"],
            command=self.trigger_panic_stop,
        )
        self.simple_panic_btn.pack(fill="x", padx=16, pady=(0, 10))

        # Repeat Mode Row
        rep_box = ctk.CTkFrame(card, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        rep_box.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(rep_box, text="REPEAT MODE", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkSegmentedButton(
            rep_box,
            values=["Forever", "Count", "Duration"],
            variable=self.simple_repeat_mode_var,
            command=self._on_simple_repeat_mode_changed,
            height=28,
            fg_color=BG_INPUT,
            selected_color=SEGMENT_ACTIVE,
            selected_hover_color=SEGMENT_ACTIVE_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
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
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small"],
            command=self._on_simple_repeat_val_commit,
        ).pack(side="left")

        self._simple_dry_run_switch = ctk.CTkSwitch(
            rep_box,
            text="Dry-Run Simulation (safe preview, no OS input)",
            variable=self.dry_run_var,
            progress_color=CTA_SUCCESS,
            button_color=TEXT_PRIMARY,
            button_hover_color="#FFFFFF",
            font=self.fonts["small"],
            command=self._on_dry_run_toggled,
        )
        self._simple_dry_run_switch.pack(anchor="w", padx=12, pady=(2, 6))

        cd_row = ctk.CTkFrame(rep_box, fg_color="transparent")
        cd_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(cd_row, text="Start Delay:", font=self.fonts["small"], text_color=TEXT_SECONDARY).pack(side="left")
        self.simple_countdown_seg = ctk.CTkSegmentedButton(
            cd_row,
            values=["0s", "1s", "3s", "5s"],
            variable=self.countdown_var,
            command=self._on_countdown_changed,
            height=24,
            fg_color=BG_INPUT,
            selected_color=SEGMENT_ACTIVE,
            selected_hover_color=SEGMENT_ACTIVE_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
        )
        self.simple_countdown_seg.pack(side="right", fill="x", expand=True, padx=(10, 0))

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
            font=self.fonts["mono_small"],
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
            font=self.fonts["mono_small"],
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
            font=self.fonts["mono_small"],
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
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            checkmark_color=CTA_TEXT,
            border_color=BORDER_CONTROL,
            border_width=1,
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
        """Build the 4-card architectural hero strip (Profile, Telemetry, Hotkeys, Run Control)."""
        hero = ctk.CTkFrame(parent, fg_color="transparent")
        hero.pack(fill="x", padx=14, pady=(8, 6))
        hero.grid_columnconfigure((0, 1, 2), weight=10, uniform="kf_hero")
        hero.grid_columnconfigure(3, weight=13)
        hero.grid_rowconfigure(0, weight=1)

        # Card 1: Profile & Security Audit
        c1 = ctk.CTkFrame(hero, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        hdr1 = ctk.CTkFrame(c1, fg_color="transparent")
        hdr1.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(hdr1, text="PROFILE", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(side="left")

        self.audit_badge = ctk.CTkButton(
            hdr1,
            text="Verified",
            font=self.fonts["micro_bold"],
            fg_color=BG_ELEVATED,
            hover_color=BTN_NEUTRAL_HOVER,
            text_color=ACCENT_SUCCESS,
            corner_radius=5,
            height=20,
            width=0,
            border_width=1,
            border_color=BORDER_SUBTLE,
            command=self._focus_security_audit,
        )
        self.audit_badge.pack(side="right")

        self.preset_menu = ctk.CTkOptionMenu(
            c1,
            variable=self.preset_var,
            values=list(self.presets.keys()),
            command=self.load_preset,
            height=29,
            fg_color=BG_INPUT,
            button_color=BTN_NEUTRAL,
            button_hover_color=BTN_NEUTRAL_HOVER,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
        )
        self.preset_menu.pack(fill="x", padx=12, pady=(0, 6))

        links1 = ctk.CTkFrame(c1, fg_color="transparent")
        links1.pack(fill="x", padx=10, pady=(0, 8))
        self.save_preset_btn = ctk.CTkButton(
            links1,
            text="+ Save Preset",
            width=0,
            height=20,
            fg_color="transparent",
            hover_color=BTN_SUBTLE_HOVER,
            text_color=ACCENT_SUCCESS,
            font=self.fonts["small_bold"],
            command=self.action_save_custom_preset,
        )
        self.save_preset_btn.pack(side="left")

        self.delete_preset_btn = ctk.CTkButton(
            links1,
            text="Delete",
            width=0,
            height=20,
            fg_color="transparent",
            hover_color=BTN_DANGER_SUBTLE,
            text_color=TEXT_MUTED,
            font=self.fonts["small"],
            command=self.action_delete_custom_preset,
        )
        self.delete_preset_btn.pack(side="right")

        # Card 2: Execution Telemetry & Progress
        c2 = ctk.CTkFrame(hero, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c2.grid(row=0, column=1, sticky="nsew", padx=3)

        hdr2 = ctk.CTkFrame(c2, fg_color="transparent")
        hdr2.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(hdr2, text="ENGINE", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(side="left")
        self.mode_sub_lbl = ctk.CTkLabel(
            hdr2,
            text="Live Input",
            font=self.fonts["micro_bold"],
            fg_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            corner_radius=5,
            padx=7,
            pady=1,
        )
        self.mode_sub_lbl.pack(side="right")

        self.status_lbl = ctk.CTkLabel(
            c2, text="● Ready", font=self.fonts["heading"], text_color=ACCENT_SUCCESS, anchor="w"
        )
        self.status_lbl.pack(fill="x", padx=12, pady=(2, 2))

        self.cycles_lbl = ctk.CTkLabel(
            c2, text="0 Cycles | 0 Actions", font=self.fonts["small_bold"], text_color=TEXT_PRIMARY, anchor="w"
        )
        self.cycles_lbl.pack(fill="x", padx=12, pady=(0, 1))

        self.actions_lbl = ctk.CTkLabel(
            c2,
            text=f"Cycle: {self._cached_cycle_wait_ms}ms • 0.0s",
            font=self.fonts["mono_small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self.actions_lbl.pack(fill="x", padx=12, pady=(0, 4))

        self.cycle_progress = ctk.CTkProgressBar(
            c2, height=3, corner_radius=1, fg_color=BG_ELEVATED, progress_color=BG_ELEVATED
        )
        self.cycle_progress.set(0.0)
        self.cycle_progress.pack(fill="x", padx=12, pady=(0, 7))

        # Card 3: Hotkeys, Cursor & Safe Simulation Toggle
        c3 = ctk.CTkFrame(hero, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c3.grid(row=0, column=2, sticky="nsew", padx=3)

        hdr3 = ctk.CTkFrame(c3, fg_color="transparent")
        hdr3.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(hdr3, text="HOTKEYS", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(side="left")
        self.cursor_lbl = ctk.CTkLabel(
            hdr3, text="XY: (0, 0)", font=self.fonts["mono_small"], text_color=TEXT_SECONDARY
        )
        self.cursor_lbl.pack(side="right")

        self.hotkey_sub_lbl = ctk.CTkLabel(
            c3,
            text=(
                f"Run: {self.profile.start_stop_hotkey} • "
                f"Rec: {self.profile.record_hotkey} • "
                f"Panic: {self.profile.panic_hotkey}"
            ),
            font=self.fonts["small_bold"],
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.hotkey_sub_lbl.pack(fill="x", padx=12, pady=(2, 8))

        self.dry_run_switch = ctk.CTkSwitch(
            c3,
            text="Dry-Run Simulation",
            variable=self.dry_run_var,
            switch_width=34,
            switch_height=18,
            progress_color=CTA_SUCCESS,
            button_color=TEXT_PRIMARY,
            button_hover_color="#FFFFFF",
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
            command=self._on_dry_run_toggled,
        )
        self.dry_run_switch.pack(anchor="w", padx=12, pady=(0, 8))

        # Card 4: Delay Preset & Primary Mint-Emerald CTA
        c4 = ctk.CTkFrame(hero, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        c4.grid(row=0, column=3, sticky="nsew", padx=(6, 0))

        hdr4 = ctk.CTkFrame(c4, fg_color="transparent")
        hdr4.pack(fill="x", padx=12, pady=(8, 6))
        ctk.CTkLabel(hdr4, text="DELAY", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(side="left")

        self.studio_countdown_seg = ctk.CTkSegmentedButton(
            hdr4,
            values=["0s", "1s", "3s", "5s"],
            variable=self.countdown_var,
            command=self._on_countdown_changed,
            height=22,
            corner_radius=6,
            fg_color=BG_INPUT,
            selected_color=SEGMENT_ACTIVE,
            selected_hover_color=SEGMENT_ACTIVE_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            font=self.fonts["micro_bold"],
        )
        self.studio_countdown_seg.pack(side="right")

        self.run_toggle_btn = ctk.CTkButton(
            c4,
            text=f"▶ Start Macro ({self.profile.start_stop_hotkey})",
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            text_color=CTA_TEXT,
            height=35,
            corner_radius=8,
            font=self.fonts["cta"],
            command=self.toggle_execution,
        )
        self.run_toggle_btn.pack(fill="x", padx=10, pady=(0, 6))

        self._hero_summary_lbl = ctk.CTkLabel(
            c4,
            text=(
                f"{_STUDIO_REPEAT_LABELS.get(self.profile.repeat_mode, 'Infinite')}  ·  "
                f"{self._cached_enabled_count}/{len(self.profile.actions)} steps  ·  "
                f"{self._cached_cycle_wait_ms}ms"
            ),
            font=self.fonts["micro"],
            text_color=TEXT_MUTED,
            anchor="center",
        )
        self._hero_summary_lbl.pack(fill="x", padx=10, pady=(0, 6))

    def _build_quick_forge_bar(self, parent: ctk.CTkFrame) -> None:
        """Build the unified Category Filter Pills + Search + Batch Action toolbar on canvas."""
        fbar = ctk.CTkFrame(parent, fg_color="transparent")
        fbar.pack(fill="x", padx=14, pady=(0, 6))

        pills_frame = ctk.CTkFrame(fbar, fg_color=BG_PANEL, corner_radius=8, border_width=1, border_color=BORDER_SUBTLE)
        pills_frame.pack(side="left", padx=(0, 8))

        self._step_filter_buttons.clear()
        for key, label in (("all", "All Steps"), ("key", "Keys"), ("wait", "Waits"), ("mouse", "Mouse")):
            is_active = key == self._step_type_filter
            btn = ctk.CTkButton(
                pills_frame,
                text=label,
                width=0,
                height=26,
                corner_radius=6,
                fg_color=SEGMENT_ACTIVE if is_active else "transparent",
                hover_color=SEGMENT_ACTIVE_HOVER if is_active else BG_ELEVATED,
                border_width=1 if is_active else 0,
                border_color=BORDER_CONTROL,
                text_color=TEXT_PRIMARY if is_active else TEXT_SECONDARY,
                font=self.fonts["small_bold"] if is_active else self.fonts["small"],
                command=lambda k=key: self._set_step_type_filter(k),
            )
            btn.pack(side="left", padx=2, pady=2)
            self._step_filter_buttons[key] = btn

        self.filter_entry = ctk.CTkEntry(
            fbar,
            placeholder_text="Filter steps... (/)",
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            border_width=1,
            corner_radius=8,
            height=30,
            width=140,
            font=self.fonts["small"],
        )
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.filter_entry.bind("<KeyRelease>", self._on_filter_changed)

        for label, width, cmd in (
            ("Undo", 46, self.undo_step_change),
            ("Redo", 46, self.redo_step_change),
            ("2×", 34, lambda: self.scale_wait_times(0.5)),
            ("0.5×", 40, lambda: self.scale_wait_times(2.0)),
            ("All On", 50, lambda: self.set_all_steps_enabled(True)),
            ("All Off", 52, lambda: self.set_all_steps_enabled(False)),
            ("Flip", 42, self.reverse_steps),
        ):
            ctk.CTkButton(
                fbar,
                text=label,
                width=width,
                height=28,
                corner_radius=7,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                border_width=1,
                border_color=BORDER_SUBTLE,
                text_color=TEXT_SECONDARY,
                font=self.fonts["small"],
                command=cmd,
            ).pack(side="left", padx=2)

        self.step_count_lbl = ctk.CTkLabel(fbar, text="0 steps", text_color=TEXT_MUTED, font=self.fonts["mono_small"])
        self.step_count_lbl.pack(side="right", padx=(10, 2))

    def _build_main_workspace(self, parent: ctk.CTkFrame) -> None:
        workspace = ctk.CTkFrame(parent, fg_color="transparent")
        workspace.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        workspace.grid_columnconfigure(0, weight=7)
        workspace.grid_columnconfigure(1, weight=3, minsize=315)
        workspace.grid_rowconfigure(0, weight=1)

        # Left Column: Compact Sequence DSL Card + Seamless Timeline Canvas + Quick-Add Bar
        left_panel = ctk.CTkFrame(workspace, fg_color="transparent")
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        qbar = ctk.CTkFrame(left_panel, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        qbar.pack(fill="x", pady=(0, 6))

        top_row = ctk.CTkFrame(qbar, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(7, 4))

        ctk.CTkLabel(
            top_row,
            text="DSL",
            font=self.fonts["micro_bold"],
            fg_color=BADGE_OK_BG,
            text_color=ACCENT_SUCCESS,
            corner_radius=5,
            padx=7,
            pady=2,
        ).pack(side="left", padx=(0, 8))

        self.dsl_entry = ctk.CTkEntry(
            top_row,
            placeholder_text="e.g. Press E -> Wait 200ms -> Click -> Press 1",
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            height=28,
            font=self.fonts["mono_small"],
        )
        self.dsl_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.dsl_entry.insert(0, format_actions_to_dsl(self.profile.actions))
        self.dsl_entry.bind("<Return>", lambda _e: self.apply_quick_expression(append=False))

        ctk.CTkButton(
            top_row,
            text="Replace",
            width=70,
            height=28,
            corner_radius=7,
            fg_color=SEGMENT_ACTIVE,
            hover_color=SEGMENT_ACTIVE_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=ACCENT_SUCCESS,
            font=self.fonts["small_bold"],
            command=lambda: self.apply_quick_expression(append=False),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            top_row,
            text="+ Append",
            width=72,
            height=28,
            corner_radius=7,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=lambda: self.apply_quick_expression(append=True),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            top_row,
            text="Copy",
            width=52,
            height=28,
            corner_radius=7,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_SECONDARY,
            font=self.fonts["small"],
            command=self.copy_recipe_to_clipboard,
        ).pack(side="left", padx=(2, 0))

        chips_row = ctk.CTkFrame(qbar, fg_color="transparent")
        chips_row.pack(fill="x", padx=10, pady=(0, 7))
        ctk.CTkLabel(chips_row, text="TEMPLATES", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(
            side="left", padx=(0, 8)
        )

        templates = [
            ("E · Click · 1", "Press E -> Wait 200ms -> Click -> Press 1"),
            ("Copy & Paste", "Press Ctrl+C -> Wait 80ms -> Press Tab -> Wait 80ms -> Press Ctrl+V"),
            ("3× Repeat", "Repeat 3 { Press F -> Wait 120ms ±15ms } -> Click"),
            ("Shift + Click", "KeyDown Shift -> Click -> Wait 100ms -> KeyUp Shift"),
        ]
        for label, expr in templates:
            ctk.CTkButton(
                chips_row,
                text=label,
                height=22,
                width=0,
                corner_radius=6,
                fg_color=BTN_SUBTLE,
                hover_color=BTN_SUBTLE_HOVER,
                border_width=1,
                border_color=BORDER_SUBTLE,
                text_color=TEXT_SECONDARY,
                font=self.fonts["small"],
                command=lambda e=expr: self._apply_template_chip(e),
            ).pack(side="left", padx=2)

        # Seamless Timeline Canvas (no sunken black box pit)
        self.sequence_scroll = ctk.CTkScrollableFrame(
            left_panel,
            fg_color=BG_ROOT,
            corner_radius=0,
            scrollbar_button_color=BORDER_SUBTLE,
            scrollbar_button_hover_color=BORDER_CONTROL,
        )
        self.sequence_scroll.pack(fill="both", expand=True, pady=(0, 4))

        # Quick-Add Footer Toolbar
        toolbar = ctk.CTkFrame(left_panel, fg_color="transparent")
        toolbar.pack(fill="x", pady=(2, 2))

        for label, width, cmd in (
            ("+ Key", 56, lambda: self.add_step(make_key_press("E", 25))),
            ("+ Wait", 60, lambda: self.add_step(make_wait(200, 10))),
            ("+ Click", 62, lambda: self.add_step(make_mouse_click(MouseButton.LEFT, ClickType.SINGLE))),
            ("+ Move", 62, lambda: self.add_step(make_mouse_move(500, 500, MoveMode.ABSOLUTE))),
            ("+ Scroll", 64, lambda: self.add_step(make_mouse_scroll(scroll_dy=-3))),
        ):
            ctk.CTkButton(
                toolbar,
                text=label,
                width=width,
                height=28,
                corner_radius=7,
                fg_color=BG_PANEL,
                hover_color=BTN_NEUTRAL_HOVER,
                border_width=1,
                border_color=BORDER_SUBTLE,
                text_color=TEXT_PRIMARY,
                font=self.fonts["small_bold"],
                command=cmd,
            ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            toolbar,
            text="+ Custom...",
            width=86,
            height=28,
            corner_radius=7,
            fg_color=SEGMENT_ACTIVE,
            hover_color=SEGMENT_ACTIVE_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=ACCENT_SUCCESS,
            font=self.fonts["small_bold"],
            command=self.open_add_step_dialog,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            toolbar,
            text="Clear",
            width=52,
            height=28,
            corner_radius=7,
            fg_color="transparent",
            hover_color=BTN_DANGER_SUBTLE,
            border_width=1,
            border_color=BTN_DANGER_BORDER,
            text_color=BTN_DANGER_SUBTLE_TEXT,
            font=self.fonts["small"],
            command=self.clear_all_steps,
        ).pack(side="right", padx=0)

        # Right Slim Inspector Card
        right_panel = ctk.CTkFrame(
            workspace, fg_color=BG_PANEL, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 0))
        self._build_inspector_tabs(right_panel)

    def _build_inspector_tabs(self, parent: ctk.CTkFrame) -> None:
        self.inspector_tabs = ctk.CTkTabview(
            parent,
            fg_color=BG_PANEL,
            segmented_button_fg_color=BG_INPUT,
            segmented_button_selected_color=SEGMENT_ACTIVE,
            segmented_button_selected_hover_color=SEGMENT_ACTIVE_HOVER,
            segmented_button_unselected_color=BG_INPUT,
            segmented_button_unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            corner_radius=10,
            command=self._on_inspector_tab_changed,
        )
        self.inspector_tabs.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        tab_exec = self.inspector_tabs.add("Loop & Keys")
        tab_rec = self.inspector_tabs.add("Recorder")
        tab_sec = self.inspector_tabs.add("Security")
        tab_log = self.inspector_tabs.add("Console")

        self._build_loop_and_hotkeys_tab(tab_exec)
        self._build_recorder_tab(tab_rec)
        self._build_security_tab(tab_sec)
        self._build_console_tab(tab_log)

    def _build_loop_and_hotkeys_tab(self, tab: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(tab, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        ctk.CTkLabel(card, text="REPEAT MODE & TIMING", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=12, pady=(8, 4)
        )

        self.repeat_mode_menu = ctk.CTkSegmentedButton(
            card,
            values=[_STUDIO_REPEAT_LABELS[m] for m in RepeatMode],
            variable=self.repeat_mode_var,
            command=self._on_repeat_settings_changed,
            height=26,
            fg_color=BG_INPUT,
            selected_color=SEGMENT_ACTIVE,
            selected_hover_color=SEGMENT_ACTIVE_HOVER,
            unselected_color=BG_INPUT,
            unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
        )
        self.repeat_mode_menu.pack(fill="x", padx=12, pady=(0, 6))

        self._studio_repeat_val_row = ctk.CTkFrame(card, fg_color="transparent")
        self._studio_repeat_val_lbl = ctk.CTkLabel(
            self._studio_repeat_val_row, text="Repeat Count:", anchor="w", font=self.fonts["small"]
        )
        self._studio_repeat_val_lbl.pack(side="left")
        self.repeat_val_entry = ctk.CTkEntry(
            self._studio_repeat_val_row, width=92, height=24, fg_color=BG_INPUT, border_color=BORDER_SUBTLE
        )
        self.repeat_val_entry.insert(0, str(self.profile.repeat_count))
        self.repeat_val_entry.bind("<Return>", lambda _e: self.sync_ui_settings_to_profile())
        self.repeat_val_entry.pack(side="right", padx=2)

        self._studio_cycle_delay_row = ctk.CTkFrame(card, fg_color="transparent")
        self._studio_cycle_delay_row.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(
            self._studio_cycle_delay_row, text="Inter-Cycle Delay (ms):", anchor="w", font=self.fonts["small"]
        ).pack(side="left")
        self.cycle_delay_entry = ctk.CTkEntry(
            self._studio_cycle_delay_row, width=92, height=24, fg_color=BG_INPUT, border_color=BORDER_SUBTLE
        )
        self.cycle_delay_entry.insert(0, str(self.profile.inter_cycle_delay_ms))
        self.cycle_delay_entry.bind("<Return>", lambda _e: self.sync_ui_settings_to_profile())
        self.cycle_delay_entry.pack(side="right", padx=2)

        self._update_studio_repeat_val_visibility(self.profile.repeat_mode)

        ctk.CTkLabel(card, text="GLOBAL HOTKEY BINDINGS", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=12, pady=(8, 4)
        )

        hk1 = ctk.CTkFrame(card, fg_color="transparent")
        hk1.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(hk1, text="Start / Stop Hotkey:", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.hk_start_bind_btn = ctk.CTkButton(
            hk1,
            text="Bind",
            width=46,
            height=24,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=lambda: self.start_main_key_capture("hk_start"),
        )
        self.hk_start_bind_btn.pack(side="right", padx=(4, 2))
        self.hk_start_entry = ctk.CTkEntry(hk1, width=72, height=24, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.hk_start_entry.insert(0, self.profile.start_stop_hotkey)
        self.hk_start_entry.bind("<Return>", lambda _e: self.sync_ui_settings_to_profile())
        self.hk_start_entry.pack(side="right", padx=2)

        hk2 = ctk.CTkFrame(card, fg_color="transparent")
        hk2.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(hk2, text="Live Recorder Hotkey:", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.hk_rec_bind_btn = ctk.CTkButton(
            hk2,
            text="Bind",
            width=46,
            height=24,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=lambda: self.start_main_key_capture("hk_rec"),
        )
        self.hk_rec_bind_btn.pack(side="right", padx=(4, 2))
        self.hk_rec_entry = ctk.CTkEntry(hk2, width=72, height=24, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.hk_rec_entry.insert(0, self.profile.record_hotkey)
        self.hk_rec_entry.bind("<Return>", lambda _e: self.sync_ui_settings_to_profile())
        self.hk_rec_entry.pack(side="right", padx=2)

        hk3 = ctk.CTkFrame(card, fg_color="transparent")
        hk3.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(hk3, text="Panic Stop Hotkey:", anchor="w", font=self.fonts["small"]).pack(side="left")
        self.hk_panic_bind_btn = ctk.CTkButton(
            hk3,
            text="Bind",
            width=46,
            height=24,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=lambda: self.start_main_key_capture("hk_panic"),
        )
        self.hk_panic_bind_btn.pack(side="right", padx=(4, 2))
        self.hk_panic_entry = ctk.CTkEntry(hk3, width=72, height=24, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
        self.hk_panic_entry.insert(0, self.profile.panic_hotkey)
        self.hk_panic_entry.bind("<Return>", lambda _e: self.sync_ui_settings_to_profile())
        self.hk_panic_entry.pack(side="right", padx=2)

        ctk.CTkButton(
            card,
            text="Apply Loop & Hotkey Settings",
            height=28,
            corner_radius=7,
            fg_color=SEGMENT_ACTIVE,
            hover_color=SEGMENT_ACTIVE_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=ACCENT_SUCCESS,
            font=self.fonts["small_bold"],
            command=self.sync_ui_settings_to_profile,
        ).pack(fill="x", padx=12, pady=(8, 8))

    def _build_recorder_tab(self, tab: ctk.CTkFrame) -> None:
        rec_cfg = self.app_config.recorder_settings
        card = ctk.CTkFrame(tab, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        card.pack(fill="both", expand=True, padx=2, pady=4)

        ctk.CTkLabel(card, text="LIVE INPUT RECORDER", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(
            anchor="w", padx=12, pady=(12, 8)
        )

        self.rec_toggle_btn = ctk.CTkButton(
            card,
            text=f"● Record Live Inputs ({self.profile.record_hotkey})",
            fg_color=BTN_DANGER_SUBTLE,
            hover_color=ACCENT_DANGER,
            border_width=1,
            border_color=BTN_DANGER_BORDER,
            text_color=BTN_DANGER_SUBTLE_TEXT,
            height=34,
            corner_radius=8,
            font=self.fonts["body_bold"],
            command=self.toggle_recording,
        )
        self.rec_toggle_btn.pack(fill="x", padx=12, pady=(0, 10))

        self.rec_delays_var = ctk.BooleanVar(value=bool(rec_cfg.get("record_delays", True)))
        ctk.CTkCheckBox(
            card,
            text="Record delays between actions (>= 20ms)",
            variable=self.rec_delays_var,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            checkmark_color=CTA_TEXT,
            border_color=BORDER_CONTROL,
            border_width=1,
            font=self.fonts["small"],
        ).pack(anchor="w", padx=14, pady=4)

        self.rec_merge_var = ctk.BooleanVar(value=bool(rec_cfg.get("merge_key_taps", True)))
        ctk.CTkCheckBox(
            card,
            text="Coalesce KeyDown + KeyUp into Press",
            variable=self.rec_merge_var,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            checkmark_color=CTA_TEXT,
            border_color=BORDER_CONTROL,
            border_width=1,
            font=self.fonts["small"],
        ).pack(anchor="w", padx=14, pady=4)

        self.rec_coords_var = ctk.BooleanVar(value=bool(rec_cfg.get("capture_click_coordinates", False)))
        ctk.CTkCheckBox(
            card,
            text="Lock clicks to exact (X, Y) coordinates",
            variable=self.rec_coords_var,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            checkmark_color=CTA_TEXT,
            border_color=BORDER_CONTROL,
            border_width=1,
            font=self.fonts["small"],
        ).pack(anchor="w", padx=14, pady=4)

        ctk.CTkLabel(
            card,
            text="Recorded steps filter out KeyForge synthetic playback inputs and your control hotkeys.",
            text_color=TEXT_SECONDARY,
            font=self.fonts["small"],
            wraplength=280,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(12, 8))

    def _build_security_tab(self, tab: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(tab, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_SUBTLE)
        card.pack(fill="both", expand=True, padx=2, pady=4)

        ctk.CTkLabel(
            card, text="CRYPTOGRAPHIC INTEGRITY & AUDIT", font=self.fonts["micro_bold"], text_color=TEXT_MUTED
        ).pack(anchor="w", padx=12, pady=(12, 6))

        self.sec_hash_lbl = ctk.CTkLabel(
            card, text="SHA-256: deferred until viewed", font=self.fonts["mono_small"], text_color=ACCENT_SUCCESS
        )
        self.sec_hash_lbl.pack(anchor="w", padx=12, pady=2)

        self.sec_budget_lbl = ctk.CTkLabel(
            card,
            text="Complexity Budget: 0 / 5,000 steps | 0 / 100,000 ops",
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
        )
        self.sec_budget_lbl.pack(anchor="w", padx=12, pady=(0, 8))

        self.sec_report_box = ctk.CTkTextbox(
            card,
            fg_color=BG_INPUT,
            border_width=1,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            font=self.fonts["mono_small"],
            corner_radius=8,
            state="disabled",
        )
        self.sec_report_box.pack(fill="both", expand=True, padx=12, pady=(2, 10))

    def _build_console_tab(self, tab: ctk.CTkFrame) -> None:
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=2, pady=(4, 4))
        ctk.CTkLabel(top, text="EVENT & TELEMETRY LOG", font=self.fonts["micro_bold"], text_color=TEXT_MUTED).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            top,
            text="Clear",
            width=56,
            height=24,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_SECONDARY,
            font=self.fonts["small"],
            command=self._clear_console_log,
        ).pack(side="right", padx=2)

        self.console_box = ctk.CTkTextbox(
            tab,
            fg_color=BG_INPUT,
            border_width=1,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_SECONDARY,
            font=self.fonts["mono_small"],
            corner_radius=8,
            state="disabled",
        )
        self.console_box.pack(fill="both", expand=True, padx=2, pady=(2, 4))

    def _clear_console_log(self) -> None:
        self.console_box.configure(state="normal")
        self.console_box.delete("1.0", "end")
        self.console_box.configure(state="disabled")
        self._console_line_count = 0

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
        self.stop_main_key_capture()
        if selected_mode == "Studio" and self.simple_frame.winfo_manager():
            task_key = _LABEL_TO_SIMPLE_TASK.get(self.simple_task_var.get(), "sequence")
            if task_key == "spammer":
                self._apply_simple_spammer_to_profile(record_undo=False)
            if self.profile.repeat_mode != RepeatMode.INFINITE:
                raw_val = self.simple_repeat_val_entry.get().strip()
                if raw_val:
                    self.repeat_val_entry.delete(0, "end")
                    self.repeat_val_entry.insert(0, raw_val)
                    self.sync_ui_settings_to_profile()
        elif selected_mode == "Simple" and self.studio_frame.winfo_manager():
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
        self.stop_main_key_capture()
        task_key = _LABEL_TO_SIMPLE_TASK.get(selected_label, "sequence")
        self.app_config.simple_task = task_key
        self._show_simple_task_subpanel(task_key)

        if task_key == "spammer":
            self._apply_simple_spammer_to_profile()
        else:
            self._refresh_simple_sequence_summary()
        self._save_persistent_state()

    def _show_simple_task_subpanel(self, task_key: str) -> None:
        for panel in (self.ss_panel, self.sq_panel):
            panel.pack_forget()
        if task_key == "spammer":
            self.ss_panel.pack(fill="both", expand=True)
        else:
            self.sq_panel.pack(fill="both", expand=True)

    def _on_ss_slider_moved(self, val: float) -> None:
        ms = max(25, int(round(val)))
        self.ss_interval_lbl.configure(text=f"{ms} ms ({1000.0 / ms:.1f} / sec)")
        self._schedule_simple_slider_commit("spammer")

    def _set_simple_spammer_interval(self, interval_ms: int) -> None:
        ms = max(25, min(2000, int(interval_ms)))
        self.ss_interval_slider.set(ms)
        self.ss_interval_lbl.configure(text=f"{ms} ms ({1000.0 / ms:.1f} / sec)")
        self._apply_simple_spammer_to_profile()

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
        self._apply_simple_spammer_to_profile(record_undo=False)

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
        self.repeat_mode_var.set(_STUDIO_REPEAT_LABELS.get(mode, "Infinite"))
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
        self._update_studio_repeat_val_visibility(mode)
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
                self.simple_repeat_val_row.pack(fill="x", padx=12, pady=(0, 8), before=self._simple_dry_run_switch)

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
    # Interactive Click-to-Bind Key Capture & Step Keyboard Workflow
    # -------------------------------------------------------------------------

    def _capture_button_for_target(self, target: str) -> tuple[ctk.CTkButton | None, str]:
        mapping = {
            "simple_spammer": (getattr(self, "ss_bind_key_btn", None), "Bind Key"),
            "hk_start": (getattr(self, "hk_start_bind_btn", None), "Bind"),
            "hk_rec": (getattr(self, "hk_rec_bind_btn", None), "Bind"),
            "hk_panic": (getattr(self, "hk_panic_bind_btn", None), "Bind"),
        }
        return mapping.get(target, (None, "Bind"))

    def start_main_key_capture(self, target: str) -> None:
        """Listen for the next key press/combo and assign it to ``target``."""
        if self._closing:
            return
        if self._key_capture_target == target:
            self.stop_main_key_capture()
            return
        self.stop_main_key_capture()
        btn, _ = self._capture_button_for_target(target)
        if btn is None:
            return
        self._key_capture_target = target
        self._key_capture_pending_mod = None
        btn.configure(
            text="Press...",
            fg_color=ACCENT_WARNING,
            hover_color=ACCENT_WARNING_HOVER,
            border_color=ACCENT_WARNING,
            text_color="#090A0D",
        )
        with contextlib.suppress(Exception):
            btn.focus_set()
        self.bind("<KeyPress>", self._on_main_capture_keypress)
        self.bind("<KeyRelease>", self._on_main_capture_keyrelease)

    def stop_main_key_capture(self) -> None:
        target = self._key_capture_target
        self._key_capture_target = None
        self._key_capture_pending_mod = None
        with contextlib.suppress(Exception):
            self.unbind("<KeyPress>")
            self.unbind("<KeyRelease>")
        if target is not None:
            btn, rest_label = self._capture_button_for_target(target)
            if btn is not None and btn.winfo_exists():
                btn.configure(
                    text=rest_label,
                    fg_color=BTN_NEUTRAL,
                    hover_color=BTN_NEUTRAL_HOVER,
                    border_color=BORDER_CONTROL,
                    text_color=TEXT_PRIMARY,
                )

    def apply_main_captured_key(self, target: str, combo_str: str) -> None:
        """Apply a captured key/combo directly to the target control."""
        self.stop_main_key_capture()
        if target == "simple_spammer":
            self.ss_key_combo.set(combo_str)
            self._apply_simple_spammer_to_profile()
            self.show_toast(f"Bound Key Repeater to [{combo_str}].", level="success")
        elif target == "hk_start":
            self.hk_start_entry.delete(0, "end")
            self.hk_start_entry.insert(0, combo_str)
            if self.sync_ui_settings_to_profile():
                self.show_toast(f"Start / Stop hotkey set to [{combo_str}].", level="success")
        elif target == "hk_rec":
            self.hk_rec_entry.delete(0, "end")
            self.hk_rec_entry.insert(0, combo_str)
            if self.sync_ui_settings_to_profile():
                self.show_toast(f"Recorder hotkey set to [{combo_str}].", level="success")
        elif target == "hk_panic":
            self.hk_panic_entry.delete(0, "end")
            self.hk_panic_entry.insert(0, combo_str)
            if self.sync_ui_settings_to_profile():
                self.show_toast(f"Panic Stop hotkey set to [{combo_str}].", level="success")

    def _on_main_capture_keypress(self, event: object) -> str | None:
        if self._key_capture_target is None:
            return None
        keysym = str(getattr(event, "keysym", "") or "")
        state = int(getattr(event, "state", 0) or 0)
        keycode = int(getattr(event, "keycode", 0) or 0)
        combo = format_captured_key_combo(keysym, state=state, keycode=keycode)
        if combo is None:
            if finalize_modifier_only_capture(keysym, keycode=keycode):
                self._key_capture_pending_mod = keysym
            return "break"
        target = self._key_capture_target
        self.apply_main_captured_key(target, combo)
        return "break"

    def _on_main_capture_keyrelease(self, event: object) -> str | None:
        if self._key_capture_target is None:
            return None
        keysym = str(getattr(event, "keysym", "") or "")
        keycode = int(getattr(event, "keycode", 0) or 0)
        if self._key_capture_pending_mod and keysym == self._key_capture_pending_mod:
            mod_name = finalize_modifier_only_capture(keysym, keycode=keycode)
            if mod_name:
                target = self._key_capture_target
                self.apply_main_captured_key(target, mod_name)
                return "break"
        return None

    def _on_slash_key(self, event: object = None) -> str | None:
        if not self._can_handle_step_shortcut(event):
            return None
        self.filter_entry.focus_set()
        return "break"

    def _on_main_escape(self, _event: object = None) -> str | None:
        if self._key_capture_target is not None:
            self.stop_main_key_capture()
            return "break"
        return None

    # -------------------------------------------------------------------------
    # Undo / Redo & Sequence Builder Operations
    # -------------------------------------------------------------------------

    def _is_text_input_focused(self, event: object = None) -> bool:
        target = getattr(event, "widget", None)
        if isinstance(target, (tk.Entry, tk.Text)):
            return True
        focused = self.focus_get()
        return isinstance(focused, (tk.Entry, tk.Text))

    def _can_handle_step_shortcut(self, event: object = None) -> bool:
        if self._closing or self.grab_current() is not None:
            return False
        if self._key_capture_target is not None:
            return False
        if self.ui_mode_var.get() != "Studio":
            return False
        return not self._is_text_input_focused(event)

    def _on_ctrl_z(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if self._is_text_input_focused(event):
            return None
        self.undo_step_change()
        return None

    def _on_ctrl_y(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if self._is_text_input_focused(event):
            return None
        self.redo_step_change()
        return None

    def _on_ctrl_d(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if not self._can_handle_step_shortcut(event):
            return None
        self.duplicate_selected_step()
        return "break"

    def _on_delete_key(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if not self._can_handle_step_shortcut(event):
            return None
        self.delete_selected_step()
        return "break"

    def _on_space_key(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if not self._can_handle_step_shortcut(event):
            return None
        self.toggle_selected_step_enabled()
        return "break"

    def _on_alt_up(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if not self._can_handle_step_shortcut(event):
            return None
        self.move_selected_step_up()
        return "break"

    def _on_alt_down(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if not self._can_handle_step_shortcut(event):
            return None
        self.move_selected_step_down()
        return "break"

    def _on_arrow_up(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if not self._can_handle_step_shortcut(event) or not self.profile.actions:
            return None
        has_filter = bool(self._filter_query or self._step_type_filter != "all")
        nav_indices = self._visible_step_indices if has_filter else list(range(len(self.profile.actions)))
        if not nav_indices:
            return None
        if self._selected_step_index in nav_indices:
            pos = nav_indices.index(self._selected_step_index)
            idx = nav_indices[max(0, pos - 1)]
        else:
            idx = nav_indices[-1]
        self.select_step(idx)
        return "break"

    def _on_arrow_down(self, event: object = None) -> str | None:
        if self._key_capture_target is not None:
            return self._on_main_capture_keypress(event)
        if not self._can_handle_step_shortcut(event) or not self.profile.actions:
            return None
        has_filter = bool(self._filter_query or self._step_type_filter != "all")
        nav_indices = self._visible_step_indices if has_filter else list(range(len(self.profile.actions)))
        if not nav_indices:
            return None
        if self._selected_step_index in nav_indices:
            pos = nav_indices.index(self._selected_step_index)
            idx = nav_indices[min(len(nav_indices) - 1, pos + 1)]
        else:
            idx = nav_indices[0]
        self.select_step(idx)
        return "break"

    def select_step(self, index: int | None) -> int | None:
        """Select a step index in the Studio timeline and update border highlights."""
        actions = self.profile.actions
        if not actions or index is None:
            self._selected_step_index = None
        else:
            self._selected_step_index = max(0, min(index, len(actions) - 1))

        selected_id = (
            actions[self._selected_step_index].id
            if self._selected_step_index is not None and 0 <= self._selected_step_index < len(actions)
            else None
        )
        for slot in self._step_row_pool:
            aid = slot["action_id"]
            if aid in self._step_row_frames:
                new_border = BORDER_FOCUS if aid == selected_id else BORDER_SUBTLE
                if slot["border_color"] != new_border:
                    slot["border_color"] = new_border
                    frame = slot["row"]
                    with contextlib.suppress(Exception):
                        if frame.winfo_exists():
                            frame.configure(border_color=new_border)
        return self._selected_step_index

    def _selected_step_is_actionable(self) -> bool:
        if self._selected_step_index is None or not (0 <= self._selected_step_index < len(self.profile.actions)):
            return False
        if self._filter_query or self._step_type_filter != "all":
            return self._selected_step_index in self._visible_step_indices
        return True

    def delete_selected_step(self) -> bool:
        if not self._selected_step_is_actionable() or self._selected_step_index is None:
            return False
        self.delete_step(self._selected_step_index)
        return True

    def duplicate_selected_step(self) -> bool:
        if not self._selected_step_is_actionable() or self._selected_step_index is None:
            return False
        self.duplicate_step(self._selected_step_index)
        return True

    def toggle_selected_step_enabled(self) -> bool:
        if not self._selected_step_is_actionable() or self._selected_step_index is None:
            return False
        act = self.profile.actions[self._selected_step_index]
        self._on_toggle_step_enabled(act, not act.enabled)
        return True

    def move_selected_step_up(self) -> bool:
        if (
            not self._selected_step_is_actionable()
            or self._selected_step_index is None
            or self._selected_step_index <= 0
        ):
            return False
        self.move_step_up(self._selected_step_index)
        return True

    def move_selected_step_down(self) -> bool:
        if (
            not self._selected_step_is_actionable()
            or self._selected_step_index is None
            or self._selected_step_index >= len(self.profile.actions) - 1
        ):
            return False
        self.move_step_down(self._selected_step_index)
        return True

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
    def _matches_step_type_filter(act: MacroAction, filter_key: str) -> bool:
        if filter_key == "key":
            return act.action_type in (ActionType.KEY_PRESS, ActionType.KEY_DOWN, ActionType.KEY_UP)
        if filter_key == "wait":
            return act.action_type in (ActionType.WAIT, ActionType.REPEAT_BLOCK)
        if filter_key == "mouse":
            return act.action_type in (ActionType.MOUSE_CLICK, ActionType.MOUSE_MOVE, ActionType.MOUSE_SCROLL)
        return True

    def _set_step_type_filter(self, filter_key: str) -> None:
        if filter_key not in ("all", "key", "wait", "mouse"):
            filter_key = "all"
        if self._step_type_filter == filter_key:
            return
        self._step_type_filter = filter_key
        for key, btn in self._step_filter_buttons.items():
            is_active = key == filter_key
            btn.configure(
                fg_color=SEGMENT_ACTIVE if is_active else "transparent",
                hover_color=SEGMENT_ACTIVE_HOVER if is_active else BG_ELEVATED,
                border_width=1 if is_active else 0,
                text_color=TEXT_PRIMARY if is_active else TEXT_SECONDARY,
                font=self.fonts["small_bold"] if is_active else self.fonts["small"],
            )
        self.refresh_sequence_view()

    def _reset_step_filters(self) -> None:
        self._filter_query = ""
        if hasattr(self, "filter_entry") and self.filter_entry.get():
            self.filter_entry.delete(0, "end")
        if self._step_type_filter != "all":
            self._set_step_type_filter("all")

    @staticmethod
    def _step_headline(act: MacroAction) -> str:
        if act.action_type == ActionType.KEY_PRESS:
            raw = f"Press [{act.key}]"
        elif act.action_type == ActionType.KEY_DOWN:
            raw = f"Hold Key [{act.key}]"
        elif act.action_type == ActionType.KEY_UP:
            raw = f"Release Key [{act.key}]"
        elif act.action_type == ActionType.MOUSE_CLICK:
            btn = act.button.value.capitalize()
            ctype = {
                ClickType.SINGLE: "Click",
                ClickType.DOUBLE: "Double-Click",
                ClickType.DOWN: "Button Down",
                ClickType.UP: "Button Up",
            }[act.click_type]
            raw = f"{btn} {ctype}"
        elif act.action_type == ActionType.MOUSE_MOVE:
            if act.move_mode == MoveMode.RELATIVE:
                raw = f"Move ({act.x:+d}, {act.y:+d})"
            else:
                raw = f"Move to ({act.x}, {act.y})"
        elif act.action_type == ActionType.MOUSE_SCROLL:
            if act.scroll_dy != 0:
                direction = "Up" if act.scroll_dy > 0 else "Down"
                raw = f"Scroll {direction} {abs(act.scroll_dy)}"
            else:
                direction = "Right" if act.scroll_dx > 0 else "Left"
                raw = f"Scroll {direction} {abs(act.scroll_dx)}"
        elif act.action_type == ActionType.WAIT:
            raw = f"Wait {act.wait_ms}ms"
        elif act.action_type == ActionType.REPEAT_BLOCK:
            raw = f"Repeat {act.repeat_count}× ({len(act.sub_actions)} steps)"
        else:
            raw = act.describe()
        return f"{raw[:43]}…" if len(raw) > 44 else raw

    @staticmethod
    def _step_subtitle(act: MacroAction) -> str:
        if act.action_type == ActionType.KEY_PRESS:
            detail = f"{act.hold_ms}ms hold" if act.label else f"Tap • {act.hold_ms}ms hold"
        elif act.action_type == ActionType.KEY_DOWN:
            detail = "Hold key down"
        elif act.action_type == ActionType.KEY_UP:
            detail = "Release key up"
        elif act.action_type == ActionType.WAIT:
            if act.jitter_ms > 0:
                lo = max(1, act.wait_ms - act.jitter_ms)
                hi = act.wait_ms + act.jitter_ms
                detail = f"{lo}–{hi}ms" if act.label else f"Randomized {lo}–{hi}ms"
            else:
                detail = "Exact delay"
        elif act.action_type == ActionType.MOUSE_CLICK:
            target = f"Fixed ({act.x}, {act.y})" if act.x is not None else "At cursor"
            detail = target if (act.label and act.hold_ms == 25) else f"{target} • {act.hold_ms}ms hold"
        elif act.action_type == ActionType.MOUSE_MOVE:
            detail = f"{act.move_mode.value.capitalize()} coordinates"
        elif act.action_type == ActionType.MOUSE_SCROLL:
            detail = f"Wheel dY={act.scroll_dy}, dX={act.scroll_dx}"
        elif act.action_type == ActionType.REPEAT_BLOCK:
            inner = " -> ".join(s.describe() for s in act.sub_actions[:2])
            detail = f"{inner}" if inner else f"{len(act.sub_actions)} inner steps"
        else:
            detail = ""
        text = f"{act.label} • {detail}" if act.label and detail else (act.label or detail)
        return f"{text[:59]}…" if len(text) > 60 else text

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
            text=f"{self._cached_enabled_count}/{total} active · {self._cached_cycle_wait_ms}ms"
        )
        if hasattr(self, "_hero_summary_lbl"):
            mode_label = _STUDIO_REPEAT_LABELS.get(self.profile.repeat_mode, "Infinite")
            steps_part = f"{self._cached_enabled_count}/{total} steps"
            self._hero_summary_lbl.configure(text=f"{mode_label}  ·  {steps_part}  ·  {self._cached_cycle_wait_ms}ms")

    def _create_step_row_slot(self) -> dict[str, Any]:
        """Allocate a reusable Studio timeline card with spacious Resolv-style hierarchy."""
        row = ctk.CTkFrame(
            self.sequence_scroll,
            fg_color=BG_CARD,
            corner_radius=10,
            height=58,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        row.pack_propagate(False)

        slot: dict[str, Any] = {
            "index": 0,
            "action_id": "",
            "action_ref": None,
            "render_key": None,
            "fg_color": BG_CARD,
            "border_color": BORDER_SUBTLE,
            "row": row,
        }

        # Right fixed controls packed FIRST so titles/badges never squeeze action pills.
        ctk.CTkButton(
            row,
            text="✕",
            width=24,
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color=BTN_DANGER_SUBTLE,
            text_color=TEXT_MUTED,
            font=self.fonts["small_bold"],
            command=lambda s=slot: self.delete_step(s["index"]),
        ).pack(side="right", padx=(1, 10))

        ctk.CTkButton(
            row,
            text="Dup",
            width=32,
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color=BTN_NEUTRAL_HOVER,
            text_color=TEXT_MUTED,
            font=self.fonts["small"],
            command=lambda s=slot: self.duplicate_step(s["index"]),
        ).pack(side="right", padx=1)

        ctk.CTkButton(
            row,
            text="▼",
            width=24,
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color=BTN_NEUTRAL_HOVER,
            text_color=TEXT_MUTED,
            font=self.fonts["small"],
            command=lambda s=slot: self.move_step_down(s["index"]),
        ).pack(side="right", padx=1)

        ctk.CTkButton(
            row,
            text="▲",
            width=24,
            height=26,
            corner_radius=6,
            fg_color="transparent",
            hover_color=BTN_NEUTRAL_HOVER,
            text_color=TEXT_MUTED,
            font=self.fonts["small"],
            command=lambda s=slot: self.move_step_up(s["index"]),
        ).pack(side="right", padx=1)

        ctk.CTkButton(
            row,
            text="Edit",
            width=46,
            height=26,
            corner_radius=6,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            font=self.fonts["small_bold"],
            command=lambda s=slot: self.open_edit_step_dialog(s["index"]),
        ).pack(side="right", padx=(6, 4))

        metric_lbl = ctk.CTkLabel(
            row,
            text="",
            width=84,
            height=24,
            anchor="center",
            font=self.fonts["step_metric"],
            fg_color=BG_INPUT,
            text_color=TEXT_SECONDARY,
            corner_radius=6,
            padx=6,
            pady=2,
        )
        metric_lbl.pack(side="right", padx=(6, 2))

        # Left toggle + index + inline title/badge + subtitle
        en_var = ctk.BooleanVar(value=True)
        en_chk = ctk.CTkCheckBox(
            row,
            text="",
            variable=en_var,
            width=22,
            checkbox_width=18,
            checkbox_height=18,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            checkmark_color=CTA_TEXT,
            border_color=BORDER_CONTROL,
            border_width=1,
            command=lambda s=slot: (
                self._on_toggle_step_enabled(s["action_ref"], s["en_var"].get())
                if s["action_ref"] is not None
                else None
            ),
        )
        en_chk.pack(side="left", padx=(12, 4))

        idx_lbl = ctk.CTkLabel(
            row,
            text="#01",
            width=26,
            font=self.fonts["step_idx"],
            text_color=TEXT_MUTED,
        )
        idx_lbl.pack(side="left", padx=(0, 8))

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", pady=7)

        title_row = ctk.CTkFrame(text_col, fg_color="transparent")
        title_row.pack(anchor="w")

        title_lbl = ctk.CTkLabel(
            title_row,
            text="",
            text_color=TEXT_PRIMARY,
            height=20,
            font=self.fonts["step_title"],
            anchor="w",
        )
        title_lbl.pack(side="left")

        badge_lbl = ctk.CTkLabel(
            title_row,
            text="KEY",
            fg_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            corner_radius=5,
            padx=7,
            pady=1,
            height=19,
            font=self.fonts["step_badge"],
        )
        badge_lbl.pack(side="left", padx=(8, 0))

        sub_lbl = ctk.CTkLabel(
            text_col,
            text="",
            text_color=TEXT_SECONDARY,
            height=16,
            font=self.fonts["step_sub"],
            anchor="w",
        )
        sub_lbl.pack(anchor="w", pady=(1, 0))

        # Architectural hairline track spanning empty gap between text block and right metrics
        bridge_line = ctk.CTkFrame(row, fg_color=BORDER_SUBTLE, height=1, corner_radius=0)
        bridge_line.pack(side="left", fill="x", expand=True, padx=(14, 14))

        for target_widget in (
            row,
            idx_lbl,
            badge_lbl,
            text_col,
            title_row,
            title_lbl,
            sub_lbl,
            bridge_line,
            metric_lbl,
        ):
            target_widget.bind("<Button-1>", lambda _e, s=slot: self._on_row_clicked(s["index"]))
        row.bind("<Enter>", lambda _e, s=slot: self._on_row_hover(s, True))
        row.bind("<Leave>", lambda _e, s=slot: self._on_row_hover(s, False))
        for dbl_widget in (row, title_row, title_lbl, sub_lbl, bridge_line):
            dbl_widget.bind("<Double-Button-1>", lambda _e, s=slot: self.open_edit_step_dialog(s["index"]))

        slot.update(
            {
                "idx_lbl": idx_lbl,
                "en_var": en_var,
                "en_chk": en_chk,
                "badge_lbl": badge_lbl,
                "metric_lbl": metric_lbl,
                "text_col": text_col,
                "title_row": title_row,
                "title_lbl": title_lbl,
                "sub_lbl": sub_lbl,
                "bridge_line": bridge_line,
            }
        )
        return slot

    def refresh_sequence_view(self) -> None:
        """Update timeline step cards via a warm widget pool to minimize CPU & RAM churn."""
        actions = self.profile.actions
        self._update_cached_cycle_metrics()

        visible: list[tuple[int, MacroAction]] = []
        if actions:
            if self._selected_step_index is not None:
                self._selected_step_index = max(0, min(self._selected_step_index, len(actions) - 1))
            q = self._filter_query
            type_filter = self._step_type_filter
            for idx, act in enumerate(actions):
                if not self._matches_step_type_filter(act, type_filter):
                    continue
                if q and q not in f"{act.badge_text()} {act.describe()} {act.label}".lower():
                    continue
                visible.append((idx, act))
        else:
            self._selected_step_index = None

        self._step_row_frames.clear()
        self._step_slots_by_id.clear()
        self._visible_step_indices = [idx for idx, _act in visible]
        if not visible:
            for slot in self._step_row_pool:
                if slot["row"].winfo_manager():
                    slot["row"].pack_forget()
            if self._empty_sequence_lbl is None:
                self._empty_sequence_lbl = ctk.CTkLabel(
                    self.sequence_scroll,
                    text="",
                    text_color=TEXT_MUTED,
                    font=self.fonts["body"],
                    justify="center",
                )
            has_filter = bool(self._filter_query or self._step_type_filter != "all")
            empty_msg = (
                "No steps match the current filter."
                if actions and has_filter
                else "No steps in macro sequence.\nSelect a Template above or click '+ Key', '+ Wait', '+ Click' below."
            )
            self._empty_sequence_lbl.configure(text=empty_msg)
            if not self._empty_sequence_lbl.winfo_manager():
                self._empty_sequence_lbl.pack(pady=52)
            return

        if self._empty_sequence_lbl is not None and self._empty_sequence_lbl.winfo_manager():
            self._empty_sequence_lbl.pack_forget()

        needed = len(visible)
        for slot in self._step_row_pool[needed:]:
            if slot["row"].winfo_manager():
                slot["row"].pack_forget()

        # Release surplus cached rows beyond pool limit so huge temporary macros don't bloat idle RAM.
        while len(self._step_row_pool) > max(needed, MAX_STEP_ROW_POOL):
            extra_slot = self._step_row_pool.pop()
            with contextlib.suppress(Exception):
                extra_slot["row"].destroy()

        while len(self._step_row_pool) < needed:
            self._step_row_pool.append(self._create_step_row_slot())

        for pos, (idx, act) in enumerate(visible):
            slot = self._step_row_pool[pos]
            slot["index"] = idx
            slot["action_id"] = act.id
            slot["action_ref"] = act

            row = slot["row"]
            self._step_row_frames[act.id] = row
            self._step_slots_by_id[act.id] = slot

            is_active = act.id == self._active_step_id
            is_selected = idx == self._selected_step_index
            row_bg = BG_CARD_ACTIVE if is_active else BG_CARD
            border_col = BORDER_FOCUS if is_selected else BORDER_SUBTLE

            title_text = self._step_headline(act)
            sub_text = self._step_subtitle(act)
            metric_text = self._step_metric_pill(act)
            render_key = (
                idx,
                act.id,
                act.enabled,
                act.action_type,
                title_text,
                sub_text,
                metric_text,
            )

            if slot["fg_color"] != row_bg or slot["border_color"] != border_col:
                slot["fg_color"] = row_bg
                slot["border_color"] = border_col
                row.configure(fg_color=row_bg, border_color=border_col)

            if slot["render_key"] != render_key:
                slot["render_key"] = render_key
                slot["idx_lbl"].configure(text=f"#{idx + 1:02d}")
                if slot["en_var"].get() != act.enabled:
                    slot["en_var"].set(act.enabled)
                badge_bg, badge_fg = get_badge_colors(act.action_type)
                slot["badge_lbl"].configure(text=act.badge_text(), fg_color=badge_bg, text_color=badge_fg)
                slot["metric_lbl"].configure(text=metric_text)
                slot["title_lbl"].configure(
                    text=title_text,
                    text_color=TEXT_PRIMARY if act.enabled else TEXT_MUTED,
                )
                sub_lbl = slot["sub_lbl"]
                if sub_text:
                    sub_lbl.configure(text=sub_text)
                    if not sub_lbl.winfo_manager():
                        sub_lbl.pack(anchor="w", pady=(1, 0))
                elif sub_lbl.winfo_manager():
                    sub_lbl.pack_forget()

            if not row.winfo_manager():
                row.pack(fill="x", pady=3, padx=0)

    def _on_row_clicked(self, index: int) -> None:
        self.focus_set()
        self.select_step(index)

    def _on_row_hover(self, slot: dict[str, Any], entering: bool) -> None:
        row = slot["row"]
        if slot["action_id"] == self._active_step_id:
            target_fg = BG_CARD_ACTIVE
        else:
            target_fg = BG_CARD_HOVER if entering else BG_CARD
        if slot["fg_color"] != target_fg:
            slot["fg_color"] = target_fg
            row.configure(fg_color=target_fg)

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
        self._selected_step_index = len(self.profile.actions) - 1
        self._on_sequence_mutated()
        self._log_event(f"Added step: {action.describe()}")

    def delete_step(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions):
            self._record_undo_snapshot()
            removed = self.profile.actions.pop(index)
            if not self.profile.actions:
                self._selected_step_index = None
            else:
                self._selected_step_index = min(index, len(self.profile.actions) - 1)
            self._on_sequence_mutated()
            self._log_event(f"Deleted step #{index + 1}: {removed.describe()}")

    def duplicate_step(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions):
            self._record_undo_snapshot()
            cloned = self.profile.actions[index].clone()
            self.profile.actions.insert(index + 1, cloned)
            self._selected_step_index = index + 1
            self._on_sequence_mutated()
            self._log_event(f"Duplicated step #{index + 1}: {cloned.describe()}")

    def move_step_up(self, index: int) -> None:
        if 0 < index < len(self.profile.actions):
            self._record_undo_snapshot()
            acts = self.profile.actions
            acts[index - 1], acts[index] = acts[index], acts[index - 1]
            self._selected_step_index = index - 1
            self._on_sequence_mutated()

    def move_step_down(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions) - 1:
            self._record_undo_snapshot()
            acts = self.profile.actions
            acts[index + 1], acts[index] = acts[index], acts[index + 1]
            self._selected_step_index = index + 1
            self._on_sequence_mutated()

    def clear_all_steps(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
        self._record_undo_snapshot()
        self.profile.actions.clear()
        self._selected_step_index = None
        self._on_sequence_mutated()
        self._log_event("Cleared all steps from sequence.")

    def open_add_step_dialog(self) -> None:
        StepEditorDialog(self, initial_action=None, on_save=self.add_step)

    def open_edit_step_dialog(self, index: int) -> None:
        if not (0 <= index < len(self.profile.actions)):
            return
        current = self.profile.actions[index]
        target_id = current.id

        def _save_edited(updated: MacroAction) -> None:
            target_idx = next((i for i, act in enumerate(self.profile.actions) if act.id == target_id), None)
            if target_idx is None:
                self.show_toast("Edited step was removed before saving.", level="warning")
                return
            self._record_undo_snapshot()
            self.profile.actions[target_idx] = updated
            self._on_sequence_mutated()
            self._log_event(f"Updated step #{target_idx + 1}: {updated.describe()}")

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
                f"Run: {self.profile.start_stop_hotkey} • "
                f"Rec: {self.profile.record_hotkey} • "
                f"Panic: {self.profile.panic_hotkey}"
            )
        )
        self.top_panic_btn.configure(text=f"Panic ({self.profile.panic_hotkey})")
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

    def _update_studio_repeat_val_visibility(self, mode: RepeatMode) -> None:
        if not hasattr(self, "_studio_repeat_val_row"):
            return
        if mode == RepeatMode.INFINITE:
            self._studio_repeat_val_row.pack_forget()
        else:
            lbl_text = "Repeat Count:" if mode == RepeatMode.COUNT else "Duration (s):"
            self._studio_repeat_val_lbl.configure(text=lbl_text)
            if not self._studio_repeat_val_row.winfo_manager():
                self._studio_repeat_val_row.pack(fill="x", padx=12, pady=3, before=self._studio_cycle_delay_row)

    def sync_profile_settings_to_ui(self) -> None:
        self.repeat_mode_var.set(_STUDIO_REPEAT_LABELS.get(self.profile.repeat_mode, "Infinite"))
        self._update_studio_repeat_val_visibility(self.profile.repeat_mode)
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
            mode = _parse_repeat_mode(self.repeat_mode_var.get())
            self.repeat_mode_var.set(_STUDIO_REPEAT_LABELS.get(mode, "Infinite"))
            self._update_studio_repeat_val_visibility(mode)
            self.profile.repeat_mode = mode

            # Read repeat value from Simple Mode entry only when Simple Mode is actively displayed
            use_simple_entry = bool(self.simple_frame.winfo_manager()) and mode != RepeatMode.INFINITE
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
        mode = _parse_repeat_mode(selected)
        self.repeat_mode_var.set(_STUDIO_REPEAT_LABELS.get(mode, "Infinite"))
        self.simple_repeat_mode_var.set(_SIMPLE_REPEAT_LABELS.get(mode, "Forever"))
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
        self._update_studio_repeat_val_visibility(mode)
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
        if self._countdown_after_id is not None or self._countdown_remaining_ms > 0:
            self.cancel_countdown("Start countdown cancelled.")
            return

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

        # Commit any pending Simple Mode text input before starting
        if self.ui_mode_var.get() == "Simple":
            task_key = _LABEL_TO_SIMPLE_TASK.get(self.simple_task_var.get(), "sequence")
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
        if self.app_config.start_countdown_sec > 0:
            self._start_countdown(self.app_config.start_countdown_sec * 1000)
            return

        self._launch_executor_now()

    def _on_countdown_changed(self, selected: str) -> None:
        with contextlib.suppress(ValueError):
            sec = max(0, min(10, int(str(selected).strip().rstrip("s"))))
            self.app_config.start_countdown_sec = sec
            self.countdown_var.set(f"{sec}s")

    def cancel_countdown(self, log_reason: str | None = None) -> bool:
        had_countdown = self._countdown_after_id is not None or self._countdown_remaining_ms > 0
        if self._countdown_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._countdown_after_id)
            self._countdown_after_id = None
        self._countdown_remaining_ms = 0
        if had_countdown and log_reason:
            self._log_event(log_reason)
        self._update_run_button_ui()
        return had_countdown

    def _start_countdown(self, total_ms: int) -> None:
        self.cancel_countdown()
        self._countdown_remaining_ms = max(100, int(total_ms))
        self._log_event(f"Starting macro in {self._countdown_remaining_ms / 1000.0:.1f}s...")
        self._update_run_button_ui()
        with contextlib.suppress(Exception):
            if self.winfo_exists():
                self._countdown_after_id = self.after(100, self._tick_countdown)

    def _tick_countdown(self) -> None:
        self._countdown_after_id = None
        if self._closing:
            return
        self._countdown_remaining_ms = max(0, self._countdown_remaining_ms - 100)
        if self._countdown_remaining_ms <= 0:
            self._launch_executor_now()
            return
        self._update_run_button_ui()
        with contextlib.suppress(Exception):
            if self.winfo_exists():
                self._countdown_after_id = self.after(100, self._tick_countdown)

    def _launch_executor_now(self) -> None:
        self._countdown_remaining_ms = 0
        use_dry_run = self.dry_run_var.get()
        if use_dry_run:
            self.dry_run_backend.clear()
            self.executor.backend = self.dry_run_backend
        else:
            self.executor.backend = self.os_backend
            caps = get_platform_capabilities(self.os_backend)
            self._platform_caps = caps
            if not caps.can_inject and caps.notice:
                self.show_toast(caps.notice, level="warning", duration_ms=8000)
                self._log_event(f"[{caps.backend_name}] {caps.notice}")

        started = self.executor.start(self.profile)
        if started:
            self.hotkey_manager.set_active_mode(True)
            mode_label = "DRY-RUN SIMULATION" if use_dry_run else self._platform_caps.backend_name.upper()
            self._log_event(
                f"Started macro [{self.profile.name}] in {mode_label} mode ({self.profile.repeat_mode.value})."
            )
        self._update_run_button_ui()

    def trigger_panic_stop(self) -> None:
        """Immediate hardware panic kill-switch handler."""
        self.cancel_countdown()
        if self.recorder.is_recording:
            self.recorder.stop()
            self._refresh_hotkey_displays()
        self.executor.panic_abort()
        self.hotkey_manager.set_active_mode(False)
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
            self.hotkey_manager.set_active_mode(self.executor.is_running)
            self.rec_toggle_btn.configure(
                text=f"● Record Live Inputs ({self.profile.record_hotkey})",
                fg_color=BTN_DANGER_SUBTLE,
                hover_color=ACCENT_DANGER,
                border_color=BTN_DANGER_BORDER,
                text_color=BTN_DANGER_SUBTLE_TEXT,
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
            caps = get_platform_capabilities(self.os_backend)
            self._platform_caps = caps
            if not caps.can_poll_keys and caps.notice:
                self.show_toast(caps.notice, level="warning", duration_ms=8000)
                self._log_event(f"[{caps.backend_name}] {caps.notice}")
            elif caps.requires_permission and caps.notice:
                self.show_toast(caps.notice, level="warning", duration_ms=8000)
                self._log_event(f"[{caps.backend_name}] {caps.notice}")
            self.recorder.config.record_delays = self.rec_delays_var.get()
            self.recorder.config.merge_key_taps = self.rec_merge_var.get()
            self.recorder.config.capture_click_coordinates = self.rec_coords_var.get()
            self.recorder.set_ignored_hotkeys(
                self.profile.record_hotkey,
                self.profile.panic_hotkey,
                self.profile.start_stop_hotkey,
            )
            self.recorder.start(enable_os_poller=True)
            self.hotkey_manager.set_active_mode(True)
            self.rec_toggle_btn.configure(
                text=f"⏹ Stop Recording ({self.profile.record_hotkey})",
                fg_color=ACCENT_WARNING,
                hover_color=ACCENT_WARNING_HOVER,
                border_color=ACCENT_WARNING,
                text_color=CTA_TEXT,
            )
            self._log_event("Live Recording started... Press keys or click mouse.")

    # -------------------------------------------------------------------------
    # Presets, Profile I/O & Security Audit
    # -------------------------------------------------------------------------

    def _reload_presets_catalog(self) -> None:
        builtins = get_builtin_presets()
        self._builtin_preset_names = set(builtins.keys())
        customs = load_custom_presets()
        merged = dict(builtins)
        merged.update(customs)
        self.presets = merged
        if hasattr(self, "preset_menu"):
            self.preset_menu.configure(values=list(self.presets.keys()))

    def action_save_custom_preset(self, preset_name: str | None = None) -> bool:
        self.stop_main_key_capture()
        if self.ui_mode_var.get() == "Simple":
            task_key = _LABEL_TO_SIMPLE_TASK.get(self.simple_task_var.get(), "sequence")
            if task_key == "spammer" and not self._apply_simple_spammer_to_profile(record_undo=False):
                return False
        elif not self.sync_ui_settings_to_profile():
            return False

        chosen = preset_name
        if chosen is None:
            current_name = self.profile.name.strip()
            auto_named = (
                not current_name
                or current_name in self._builtin_preset_names
                or current_name == "Custom Profile"
                or current_name.startswith(("Auto-Clicker", "Key Spammer", "Untitled"))
            )
            if not auto_named:
                chosen = current_name
            else:
                dlg = ctk.CTkInputDialog(
                    text="Enter a name for your custom preset:",
                    title="Save Custom Preset",
                )
                chosen = dlg.get_input()

        if chosen is None or not chosen.strip():
            if preset_name is not None:
                self.show_toast("Preset name cannot be empty.", level="warning")
            return False

        cleaned_name = chosen.strip()[:100]
        self.profile.name = cleaned_name
        save_custom_preset(self.profile)
        self._reload_presets_catalog()
        self.preset_var.set(cleaned_name)
        self._log_event(f"Saved custom preset: {cleaned_name}")
        self.show_toast(f"Saved preset '{cleaned_name}'.", level="success")
        return True

    def action_delete_custom_preset(self, preset_name: str | None = None) -> bool:
        target = (preset_name or self.preset_var.get()).strip()
        if not target:
            return False
        if target in self._builtin_preset_names:
            self.show_toast("Built-in presets cannot be deleted.", level="warning")
            return False
        if not delete_custom_preset(target):
            self.show_toast(f"Custom preset '{target}' not found.", level="warning")
            return False
        self._reload_presets_catalog()
        if self.presets:
            first_key = next(iter(self.presets.keys()))
            self.preset_var.set(first_key)
        self._log_event(f"Deleted custom preset: {target}")
        self.show_toast(f"Deleted custom preset '{target}'.", level="info")
        return True

    def load_preset(self, preset_name: str) -> None:
        if self.executor.is_running:
            self.executor.stop()
        preset = self.presets.get(preset_name)
        if preset is None:
            return
        self._reset_step_filters()
        self._record_undo_snapshot()
        self.profile = MacroProfile.from_payload_dict(preset.to_payload_dict())
        self.sync_profile_settings_to_ui()
        self._rebind_hotkeys()
        self._on_sequence_mutated()
        self._log_event(f"Loaded preset profile: {self.profile.name}")

    def action_new_profile(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
        self._reset_step_filters()
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
        self._reset_step_filters()
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
            filetypes=[("KeyForge Macro", ("*.json", "*.kfmacro")), ("All JSON", "*.json")],
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
        current_tab = self.inspector_tabs.get()
        if self._security_tab_dirty and current_tab == "Security":
            self._refresh_security_tab_details()
        elif current_tab == "Console":
            with contextlib.suppress(Exception):
                self.console_box.see("end")

    def _refresh_security_tab_details(self) -> None:
        report = self._last_audit_report or audit_profile(self.profile)
        with contextlib.suppress(Exception):
            digest = compute_payload_sha256(self.profile.to_payload_dict())
            nodes, ops = validate_profile_complexity(self.profile)
            self.sec_hash_lbl.configure(text=f"SHA-256: {digest[:24]}...{digest[-8:]}")
            self.sec_budget_lbl.configure(
                text=f"Complexity Budget: {nodes} / 5,000 steps | {ops} / 100,000 expanded ops"
            )
            self.sec_report_box.configure(state="normal")
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
            self.sec_report_box.configure(state="disabled")
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
                text="Verified",
                fg_color=BG_ELEVATED,
                text_color=ACCENT_SUCCESS,
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
            border_color=CTA_SUCCESS,
            text_color=CTA_TEXT,
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
                border_color=BORDER_CONTROL,
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
        close_backend = getattr(self.os_backend, "close", None)
        if callable(close_backend):
            with contextlib.suppress(Exception):
                close_backend()

    # -------------------------------------------------------------------------
    # Compact Floating Mini-HUD Mode
    # -------------------------------------------------------------------------

    def enter_mini_hud_mode(self) -> None:
        """Minimize the full Studio/Simple window and show an always-on-top floating HUD pill."""
        if self._closing:
            return
        if self._mini_hud is not None and not self._mini_hud._closed:
            with contextlib.suppress(Exception):
                if self._mini_hud.winfo_exists():
                    self._mini_hud.lift()
                    return
        self.withdraw()
        self._mini_hud = MiniHudWindow(self)
        self._sync_mini_hud()

    def exit_mini_hud_mode(self) -> None:
        """Close the floating Mini-HUD pill and restore the main KeyForge window."""
        if self._mini_hud is not None:
            self._mini_hud.close_hud()
            self._mini_hud = None
        if not self._closing:
            with contextlib.suppress(Exception):
                if self.winfo_exists():
                    self.deiconify()
                    self.lift()

    def toggle_mini_hud_mode(self) -> None:
        """Toggle between full desktop mode and the always-on-top Mini-HUD pill."""
        if self._mini_hud is not None and not self._mini_hud._closed:
            self.exit_mini_hud_mode()
        else:
            self.enter_mini_hud_mode()

    def _sync_mini_hud(self) -> None:
        if self._mini_hud is None or self._mini_hud._closed:
            return
        telem: ExecutionTelemetry = self.executor.telemetry
        counting_down = self._countdown_after_id is not None or self._countdown_remaining_ms > 0
        if self.executor.is_running:
            state_text = "● RUNNING"
            state_color = ACCENT_PRIMARY
            is_active = True
        elif counting_down:
            state_text = f"⏳ {self._countdown_remaining_ms / 1000.0:.1f}s"
            state_color = ACCENT_WARNING
            is_active = True
        elif telem.state == ExecutorState.PANIC_ABORTED:
            state_text = "● ABORTED"
            state_color = ACCENT_DANGER
            is_active = False
        elif telem.state == ExecutorState.ERROR:
            state_text = "● ERROR"
            state_color = ACCENT_DANGER
            is_active = False
        else:
            state_text = "● READY"
            state_color = ACCENT_SUCCESS
            is_active = False

        cycles_text = f"{telem.cycles_completed} cycles"
        self._mini_hud.update_state(
            state_text=state_text,
            state_color=state_color,
            cycles_text=cycles_text,
            is_active=is_active,
            start_hk=self.profile.start_stop_hotkey,
            panic_hk=self.profile.panic_hotkey,
        )

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
                is_active = self.executor.is_running or self.recorder.is_recording
                is_minimized = False
                with contextlib.suppress(Exception):
                    is_minimized = self.state() == "iconic"
                delay_ms = 50 if is_active else (300 if is_minimized else 150)
                self._hotkey_poll_after_id = self.after(delay_ms, self._poll_hotkey_queue)

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
        counting_down = not running and (self._countdown_after_id is not None or self._countdown_remaining_ms > 0)
        countdown_step = self._countdown_remaining_ms if counting_down else 0
        key_state = (running, counting_down, countdown_step, self.profile.start_stop_hotkey)
        if self._tick_cache.get("run_btn") != key_state:
            self._tick_cache["run_btn"] = key_state
            if running:
                self.run_toggle_btn.configure(
                    text=f"⏹ Stop Macro ({self.profile.start_stop_hotkey})",
                    fg_color=ACCENT_DANGER,
                    hover_color=ACCENT_DANGER_HOVER,
                    text_color="#FFFFFF",
                )
            elif counting_down:
                rem_s = countdown_step / 1000.0
                self.run_toggle_btn.configure(
                    text=f"⏳ Starting in {rem_s:.1f}s ({self.profile.start_stop_hotkey})",
                    fg_color=ACCENT_WARNING,
                    hover_color=ACCENT_WARNING_HOVER,
                    text_color=CTA_TEXT,
                )
            else:
                self.run_toggle_btn.configure(
                    text=f"▶ Start Macro ({self.profile.start_stop_hotkey})",
                    fg_color=CTA_SUCCESS,
                    hover_color=CTA_SUCCESS_HOVER,
                    text_color=CTA_TEXT,
                )

        if hasattr(self, "simple_run_btn") and self._tick_cache.get("simple_run_btn") != key_state:
            self._tick_cache["simple_run_btn"] = key_state
            if running:
                self.simple_run_btn.configure(
                    text=f"⏹ Stop Macro ({self.profile.start_stop_hotkey})",
                    fg_color=ACCENT_DANGER,
                    hover_color=ACCENT_DANGER_HOVER,
                    text_color="#FFFFFF",
                )
            elif counting_down:
                rem_s = countdown_step / 1000.0
                self.simple_run_btn.configure(
                    text=f"⏳ Starting in {rem_s:.1f}s... ({self.profile.start_stop_hotkey})",
                    fg_color=ACCENT_WARNING,
                    hover_color=ACCENT_WARNING_HOVER,
                    text_color=CTA_TEXT,
                )
            else:
                self.simple_run_btn.configure(
                    text=f"▶ Start Macro ({self.profile.start_stop_hotkey})",
                    fg_color=CTA_SUCCESS,
                    hover_color=CTA_SUCCESS_HOVER,
                    text_color=CTA_TEXT,
                )
        self._sync_mini_hud()

    def _schedule_ui_tick(self) -> None:
        if self._closing:
            return
        with contextlib.suppress(Exception):
            if not self.winfo_exists():
                return
        is_running = self._ui_tick()
        is_minimized = False
        with contextlib.suppress(Exception):
            is_minimized = self.state() == "iconic"
        if is_running:
            interval_ms = 100
        elif is_minimized and self._mini_hud is None:
            interval_ms = 1000
        else:
            interval_ms = 500
        self._ui_tick_after_id = self.after(interval_ms, self._schedule_ui_tick)

    def _ui_tick(self) -> bool:
        self._drain_hotkey_queue()
        self._drain_startup_update_queue()
        telem: ExecutionTelemetry = self.executor.telemetry
        is_running = self.executor.is_running or telem.state in (ExecutorState.RUNNING, ExecutorState.STOPPING)
        self.hotkey_manager.set_active_mode(is_running or self.recorder.is_recording)
        self._update_run_button_ui()

        if telem.state == ExecutorState.ERROR and telem.last_error and telem.last_error != self._last_telem_error:
            self._last_telem_error = telem.last_error
            self._log_event(f"[Execution Error] {telem.last_error}")
            self.show_toast(f"Execution Error: {telem.last_error}", level="error")

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

        if self._tick_cache.get("state") != (simple_status_text, state_color):
            self._tick_cache["state"] = (simple_status_text, state_color)
            self.status_lbl.configure(text=simple_status_text, text_color=state_color)
            self.simple_status_lbl.configure(text=simple_status_text, text_color=state_color)

        is_dry = bool(self.dry_run_var.get())
        if is_dry:
            mode_badge = ("Dry-Run", BADGE_WARN_BG, BADGE_WARN_FG)
        elif not self._platform_caps.can_inject:
            label = "Access Needed" if self._platform_caps.requires_permission else "Preview Only"
            mode_badge = (label, BADGE_WARN_BG, BADGE_WARN_FG)
        else:
            mode_badge = ("Live Input", BG_ELEVATED, TEXT_SECONDARY)

        if self._tick_cache.get("mode_badge") != mode_badge:
            self._tick_cache["mode_badge"] = mode_badge
            self.mode_sub_lbl.configure(
                text=mode_badge[0],
                fg_color=mode_badge[1],
                text_color=mode_badge[2],
            )

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
            self.cycle_progress.configure(progress_color=ACCENT_PRIMARY if ratio > 0 else BG_ELEVATED)
            self.cycle_progress.set(ratio)

        actions_text = f"Cycle: {self._cached_cycle_wait_ms}ms • {telem.elapsed_seconds:.1f}s"
        if self._tick_cache.get("actions_text") != actions_text:
            self._tick_cache["actions_text"] = actions_text
            self.actions_lbl.configure(text=actions_text)

        # Skip cursor polling when minimized, in Mini-HUD, in Simple mode, or unfocused at idle
        is_minimized = False
        with contextlib.suppress(Exception):
            is_minimized = self.state() == "iconic"

        has_focus = False
        if not is_minimized and self.ui_mode_var.get() == "Studio" and self._mini_hud is None:
            with contextlib.suppress(Exception):
                has_focus = is_running or (self.focus_displayof() is not None)

        if has_focus:
            if self._platform_caps.can_poll_cursor:
                cx, cy = self.os_backend.get_cursor_pos()
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
            if prev_id and prev_id in self._step_slots_by_id:
                prev_slot = self._step_slots_by_id[prev_id]
                if prev_slot["fg_color"] != BG_CARD:
                    prev_slot["fg_color"] = BG_CARD
                    prev_slot["row"].configure(fg_color=BG_CARD)
            if self._active_step_id and self._active_step_id in self._step_slots_by_id:
                curr_slot = self._step_slots_by_id[self._active_step_id]
                if curr_slot["fg_color"] != BG_CARD_ACTIVE:
                    curr_slot["fg_color"] = BG_CARD_ACTIVE
                    curr_slot["row"].configure(fg_color=BG_CARD_ACTIVE)

        self._sync_mini_hud()
        return is_running

    def _log_event(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self.console_box.configure(state="normal")
        self.console_box.insert("end", line)
        self._console_line_count += 1
        if self._console_line_count > MAX_CONSOLE_LINES:
            self.console_box.delete("1.0", "26.0")
            self._console_line_count = max(0, self._console_line_count - 25)
        self.console_box.configure(state="disabled")
        if self.ui_mode_var.get() == "Studio" and self.inspector_tabs.get() == "Console":
            self.console_box.see("end")
        if hasattr(self, "footer_status_lbl"):
            self.footer_status_lbl.configure(text=f"● [{ts}] {message}")

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._startup_update_checking = False
        self.stop_main_key_capture()

        if self._update_dialog is not None:
            with contextlib.suppress(Exception):
                if not self._update_dialog._closed:
                    self._update_dialog._on_close()
            self._update_dialog = None

        if self._mini_hud is not None:
            with contextlib.suppress(Exception):
                self._mini_hud.close_hud()
            self._mini_hud = None

        for after_attr in (
            "_ui_tick_after_id",
            "_startup_update_after_id",
            "_startup_update_poll_after_id",
            "_hotkey_poll_after_id",
            "_filter_after_id",
            "_simple_slider_after_id",
            "_toast_after_id",
            "_countdown_after_id",
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
        close_backend = getattr(self.os_backend, "close", None)
        if callable(close_backend):
            with contextlib.suppress(Exception):
                close_backend()
        cancel_all_tcl_after_timers(self)
        with contextlib.suppress(Exception):
            self.destroy()
        with contextlib.suppress(Exception):
            if getattr(tk, "_default_root", None) is self:
                tk._default_root = None
        with contextlib.suppress(Exception):
            if not ctk.ScalingTracker.window_widgets_dict:
                ctk.ScalingTracker.update_loop_running = False
