"""Modern Obsidian & Neon Studio Desktop GUI Application for KeyForge (Windows, macOS, Linux)."""

from __future__ import annotations

import contextlib
import sys
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk

from keyforge import __version__
from keyforge.engine.backend import DryRunBackend
from keyforge.engine.executor import ExecutionTelemetry, ExecutorState, MacroExecutor
from keyforge.engine.hotkeys import GlobalHotkeyManager
from keyforge.engine.recorder import MacroRecorder, RecorderConfig
from keyforge.engine.win32_input import IS_WINDOWS, Win32InputBackend
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
    ACCENT_PURPLE,
    ACCENT_PURPLE_HOVER,
    ACCENT_SUCCESS,
    ACCENT_SUCCESS_HOVER,
    ACCENT_WARNING,
    BG_CARD,
    BG_CARD_ACTIVE,
    BG_CARD_HOVER,
    BG_ELEVATED,
    BG_INPUT,
    BG_PANEL,
    BG_ROOT,
    BORDER_SUBTLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    apply_window_icon,
    get_badge_colors,
    get_stripe_color,
    load_brand_ctk_image,
)

MAX_UNDO_HISTORY = 40


def _platform_badge_label() -> str:
    if sys.platform == "win32":
        return "WIN32 NATIVE"
    if sys.platform == "darwin":
        return "MACOS"
    return "LINUX"


class KeyForgeApp(ctk.CTk):
    """KeyForge Modern Studio Desktop Application Window & Controller."""

    def __init__(self, start_hotkey_listener: bool = True) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"KeyForge Studio v{__version__} — FOSS Visual Macro Builder & Auto-Clicker")
        self.geometry("1300x860")
        self.minsize(1120, 740)
        self.configure(fg_color=BG_ROOT)
        self.icon_applied: bool = apply_window_icon(self)

        # Core State & Engines
        self.presets: dict[str, MacroProfile] = get_builtin_presets()
        self.profile: MacroProfile = create_default_profile()

        self.win32_backend = Win32InputBackend()
        self.dry_run_backend = DryRunBackend()
        self.executor = MacroExecutor(backend=self.win32_backend)
        self.recorder = MacroRecorder(config=RecorderConfig())
        self.hotkey_manager = GlobalHotkeyManager()

        self._active_step_id: str | None = None
        self._step_row_frames: dict[str, ctk.CTkFrame] = {}
        self._ui_tick_after_id: str | None = None
        self._filter_query: str = ""
        self._undo_stack: list[list[dict[str, Any]]] = []
        self._redo_stack: list[list[dict[str, Any]]] = []

        # Build Modern Studio Layout
        self._build_top_bar()
        self._build_bento_metrics_strip()
        self._build_quick_forge_bar()
        self._build_status_bar()
        self._build_main_workspace()

        # Bind Studio Keyboard Shortcuts
        self.bind("<Control-z>", lambda _e: self.undo_step_change())
        self.bind("<Control-y>", lambda _e: self.redo_step_change())
        self.bind("<Control-s>", lambda _e: self.action_save_profile_dialog())

        # Initial Render & Audit
        self.refresh_sequence_view()
        self.sync_profile_settings_to_ui()
        self.run_security_audit()
        self._log_event(
            f"KeyForge Studio v{__version__} ({_platform_badge_label()}) ready. "
            f"Panic Kill-Switch armed on [{self.profile.panic_hotkey}]."
        )

        # Configure Global Hotkeys
        self._rebind_hotkeys()
        if start_hotkey_listener:
            self.hotkey_manager.start()

        # Start periodic UI telemetry refresh (20 Hz)
        self._schedule_ui_tick()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------------------------------------------------------
    # UI Construction
    # -------------------------------------------------------------------------

    def _build_top_bar(self) -> None:
        bar = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=0,
            height=64,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        bar.pack(fill="x", padx=0, pady=0)

        # Brand App Icon + Title Stack
        self._brand_icon_img = load_brand_ctk_image((38, 38))
        if self._brand_icon_img is not None:
            icon_lbl = ctk.CTkLabel(bar, text="", image=self._brand_icon_img)
            icon_lbl.pack(side="left", padx=(18, 8), pady=10)
        else:
            ctk.CTkLabel(
                bar,
                text="⚡",
                font=ctk.CTkFont(size=22, weight="bold"),
                text_color=ACCENT_PRIMARY,
            ).pack(side="left", padx=(18, 6), pady=10)

        brand_stack = ctk.CTkFrame(bar, fg_color="transparent")
        brand_stack.pack(side="left", padx=(0, 10), pady=8)

        ctk.CTkLabel(
            brand_stack,
            text="KEYFORGE STUDIO",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            brand_stack,
            text="Security-Hardened Visual Macro Suite",
            font=ctk.CTkFont(size=10),
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            bar,
            text=f"v{__version__} • {_platform_badge_label()}",
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#172554",
            text_color="#93C5FD",
            corner_radius=6,
            padx=8,
            pady=2,
        ).pack(side="left", padx=(0, 12), pady=12)

        # Preset & File Actions
        ctk.CTkLabel(
            bar,
            text="Preset:",
            text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(2, 5))

        self.preset_var = ctk.StringVar(value=self.profile.name)
        self.preset_menu = ctk.CTkOptionMenu(
            bar,
            variable=self.preset_var,
            values=list(self.presets.keys()),
            command=self.load_preset,
            width=195,
            height=30,
            fg_color=BG_CARD,
            button_color=BG_ELEVATED,
        )
        self.preset_menu.pack(side="left", padx=3)

        ctk.CTkButton(
            bar,
            text="New",
            width=50,
            height=30,
            fg_color=BG_CARD,
            hover_color="#28354A",
            command=self.action_new_profile,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            bar,
            text="Open",
            width=58,
            height=30,
            fg_color=BG_CARD,
            hover_color="#28354A",
            command=self.action_open_profile_dialog,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            bar,
            text="Save",
            width=58,
            height=30,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.action_save_profile_dialog,
        ).pack(side="left", padx=2)

        # Emergency Panic Button in Top Bar
        self.top_panic_btn = ctk.CTkButton(
            bar,
            text=f"⚠ PANIC STOP ({self.profile.panic_hotkey})",
            fg_color=ACCENT_DANGER,
            hover_color=ACCENT_DANGER_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            height=32,
            width=156,
            command=self.trigger_panic_stop,
        )
        self.top_panic_btn.pack(side="right", padx=(8, 16), pady=12)

        # Security Audit Status Pill
        self.audit_badge = ctk.CTkLabel(
            bar,
            text="🛡 SECURITY AUDIT: PASSED",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#064E3B",
            text_color="#6EE7B7",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.audit_badge.pack(side="right", padx=4, pady=12)

    def _build_bento_metrics_strip(self) -> None:
        """Build the 4-card Bento KPI Telemetry strip below the header."""
        strip = ctk.CTkFrame(self, fg_color="transparent")
        strip.pack(fill="x", padx=16, pady=(10, 4))
        for col in range(4):
            strip.grid_columnconfigure(col, weight=1, uniform="bento")

        # Card 1: Engine Status
        c1 = ctk.CTkFrame(
            strip,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(
            c1,
            text="ENGINE STATUS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TEXT_MUTED,
        ).pack(anchor="w", padx=14, pady=(8, 0))
        self.status_lbl = ctk.CTkLabel(
            c1,
            text="● IDLE",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ACCENT_SUCCESS,
        )
        self.status_lbl.pack(anchor="w", padx=14, pady=(0, 2))
        self.mode_sub_lbl = ctk.CTkLabel(
            c1,
            text="Mode: Live OS Injection",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SECONDARY,
        )
        self.mode_sub_lbl.pack(anchor="w", padx=14, pady=(0, 8))

        # Card 2: Execution Cycles & Progress Bar
        c2 = ctk.CTkFrame(
            strip,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        c2.grid(row=0, column=1, sticky="nsew", padx=6)
        ctk.CTkLabel(
            c2,
            text="EXECUTION PROGRESS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TEXT_MUTED,
        ).pack(anchor="w", padx=14, pady=(8, 0))
        self.cycles_lbl = ctk.CTkLabel(
            c2,
            text="0 Cycles | 0 Actions",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        self.cycles_lbl.pack(anchor="w", padx=14, pady=(0, 4))
        self.cycle_progress = ctk.CTkProgressBar(
            c2,
            height=6,
            fg_color=BG_INPUT,
            progress_color=ACCENT_PRIMARY,
        )
        self.cycle_progress.set(0.0)
        self.cycle_progress.pack(fill="x", padx=14, pady=(2, 10))

        # Card 3: Sequence Timing & Rate
        c3 = ctk.CTkFrame(
            strip,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        c3.grid(row=0, column=2, sticky="nsew", padx=6)
        ctk.CTkLabel(
            c3,
            text="SEQUENCE TIMING & THROUGHPUT",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TEXT_MUTED,
        ).pack(anchor="w", padx=14, pady=(8, 0))
        self.actions_lbl = ctk.CTkLabel(
            c3,
            text="Cycle: 450ms | Elapsed: 0.0s",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=ACCENT_CYAN,
        )
        self.actions_lbl.pack(anchor="w", padx=14, pady=(0, 2))
        self.rate_sub_lbl = ctk.CTkLabel(
            c3,
            text="Governor: 5ms Anti-Flood Floor Armed",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SECONDARY,
        )
        self.rate_sub_lbl.pack(anchor="w", padx=14, pady=(0, 8))

        # Card 4: Live Cursor & Global Hotkeys
        c4 = ctk.CTkFrame(
            strip,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        c4.grid(row=0, column=3, sticky="nsew", padx=(6, 0))
        ctk.CTkLabel(
            c4,
            text="LIVE CURSOR & HOTKEYS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TEXT_MUTED,
        ).pack(anchor="w", padx=14, pady=(8, 0))
        self.cursor_lbl = ctk.CTkLabel(
            c4,
            text="XY: (0, 0)",
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        self.cursor_lbl.pack(anchor="w", padx=14, pady=(0, 2))
        self.hotkey_sub_lbl = ctk.CTkLabel(
            c4,
            text=(
                f"Run: {self.profile.start_stop_hotkey} | "
                f"Rec: {self.profile.record_hotkey} | "
                f"Panic: {self.profile.panic_hotkey}"
            ),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SECONDARY,
        )
        self.hotkey_sub_lbl.pack(anchor="w", padx=14, pady=(0, 8))

    def _build_quick_forge_bar(self) -> None:
        qbar = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        qbar.pack(fill="x", padx=16, pady=6)

        top_row = ctk.CTkFrame(qbar, fg_color="transparent")
        top_row.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(
            top_row,
            text=">_ Quick-Forge Recipe:",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color=ACCENT_PRIMARY,
        ).pack(side="left", padx=(4, 8))

        self.dsl_entry = ctk.CTkEntry(
            top_row,
            placeholder_text="e.g. Press E -> Wait 200ms -> Click -> Press 1",
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            height=34,
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.dsl_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.dsl_entry.insert(0, format_actions_to_dsl(self.profile.actions))
        self.dsl_entry.bind("<Return>", lambda _e: self.apply_quick_expression(append=False))

        ctk.CTkButton(
            top_row,
            text="⚡ Replace Sequence",
            width=142,
            height=32,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda: self.apply_quick_expression(append=False),
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            top_row,
            text="+ Append",
            width=90,
            height=32,
            fg_color=ACCENT_PURPLE,
            hover_color=ACCENT_PURPLE_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda: self.apply_quick_expression(append=True),
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            top_row,
            text="Copy Recipe",
            width=96,
            height=32,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=12),
            command=self.copy_recipe_to_clipboard,
        ).pack(side="left", padx=(4, 2))

        # Quick-Insert Template Chips Row
        chips_row = ctk.CTkFrame(qbar, fg_color="transparent")
        chips_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            chips_row,
            text="Quick Templates:",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack(side="left", padx=(0, 8))

        templates = [
            ("⚡ E -> 200ms -> Click -> 1", "Press E -> Wait 200ms -> Click -> Press 1"),
            ("🖱 20 CPS Humanized Clicker", "Click -> Wait 45ms ±10ms"),
            ("🔁 3x Loot Repeat Block", "Repeat 3 { Press F -> Wait 120ms ±15ms } -> Click"),
            ("⌨ Hold Shift + Attack", "KeyDown Shift -> Click -> Wait 100ms -> KeyUp Shift"),
        ]
        for label, expr in templates:
            ctk.CTkButton(
                chips_row,
                text=label,
                height=22,
                fg_color=BG_ELEVATED,
                hover_color="#28354A",
                text_color=TEXT_SECONDARY,
                font=ctk.CTkFont(size=11),
                command=lambda e=expr: self._apply_template_chip(e),
            ).pack(side="left", padx=3)

    def _build_status_bar(self) -> None:
        """Build the docked bottom Studio Status & Keyboard Shortcuts Bar."""
        sbar = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=0,
            height=28,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        sbar.pack(side="bottom", fill="x", padx=0, pady=0)

        self.footer_status_lbl = ctk.CTkLabel(
            sbar,
            text=f"● Ready — KeyForge Studio v{__version__}",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SECONDARY,
        )
        self.footer_status_lbl.pack(side="left", padx=16, pady=3)

        ctk.CTkLabel(
            sbar,
            text="Shortcuts: Ctrl+Z Undo  •  Ctrl+Y Redo  •  Ctrl+S Save  •  Double-Click Step to Edit",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack(side="right", padx=16, pady=3)

    def _apply_template_chip(self, expression: str) -> None:
        self.dsl_entry.delete(0, "end")
        self.dsl_entry.insert(0, expression)
        self.apply_quick_expression(append=False)

    def copy_recipe_to_clipboard(self) -> None:
        recipe = format_actions_to_dsl(self.profile.actions)
        self.clipboard_clear()
        self.clipboard_append(recipe)
        self._log_event(f"Copied Quick-Forge recipe to clipboard ({len(recipe)} chars).")

    def _build_main_workspace(self) -> None:
        workspace = ctk.CTkFrame(self, fg_color="transparent")
        workspace.pack(fill="both", expand=True, padx=16, pady=(2, 10))
        workspace.grid_columnconfigure(0, weight=7)
        workspace.grid_columnconfigure(1, weight=5)
        workspace.grid_rowconfigure(0, weight=1)

        # Left Column: Visual Action Sequence Timeline Builder
        left_panel = ctk.CTkFrame(
            workspace,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        l_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        l_header.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(
            l_header,
            text="Visual Action Sequence Timeline",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        self.step_count_lbl = ctk.CTkLabel(
            l_header,
            text="0 steps",
            text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(size=12),
        )
        self.step_count_lbl.pack(side="right")

        # Filter, Undo/Redo, Speed Scaling & Bulk Operations Sub-Bar
        filter_bar = ctk.CTkFrame(left_panel, fg_color="transparent")
        filter_bar.pack(fill="x", padx=14, pady=(0, 4))

        self.filter_entry = ctk.CTkEntry(
            filter_bar,
            placeholder_text="🔍 Filter steps by key, type, or comment...",
            fg_color=BG_INPUT,
            height=28,
            width=210,
            font=ctk.CTkFont(size=12),
        )
        self.filter_entry.pack(side="left", padx=(2, 6))
        self.filter_entry.bind("<KeyRelease>", self._on_filter_changed)

        self.undo_btn = ctk.CTkButton(
            filter_bar,
            text="↶ Undo",
            width=58,
            height=26,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=11),
            command=self.undo_step_change,
        )
        self.undo_btn.pack(side="left", padx=2)

        self.redo_btn = ctk.CTkButton(
            filter_bar,
            text="↷ Redo",
            width=58,
            height=26,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=11),
            command=self.redo_step_change,
        )
        self.redo_btn.pack(side="left", padx=2)

        ctk.CTkButton(
            filter_bar,
            text="⚡ 2x Faster",
            width=74,
            height=26,
            fg_color="#172554",
            hover_color="#1D4ED8",
            text_color="#93C5FD",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda: self.scale_wait_times(0.5),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            filter_bar,
            text="🐢 0.5x Slower",
            width=82,
            height=26,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=11),
            command=lambda: self.scale_wait_times(2.0),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            filter_bar,
            text="Enable All",
            width=68,
            height=26,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=11),
            command=lambda: self.set_all_steps_enabled(True),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            filter_bar,
            text="Disable All",
            width=68,
            height=26,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=11),
            command=lambda: self.set_all_steps_enabled(False),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            filter_bar,
            text="Reverse",
            width=60,
            height=26,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=11),
            command=self.reverse_steps,
        ).pack(side="left", padx=2)

        # Scrollable Timeline Container
        self.sequence_scroll = ctk.CTkScrollableFrame(
            left_panel,
            fg_color=BG_ROOT,
            corner_radius=10,
        )
        self.sequence_scroll.pack(fill="both", expand=True, padx=14, pady=6)

        # Quick-Add Step Toolbar
        toolbar = ctk.CTkFrame(left_panel, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=(4, 12))

        ctk.CTkButton(
            toolbar,
            text="+ Key",
            width=72,
            height=32,
            fg_color="#172554",
            hover_color="#1D4ED8",
            command=lambda: self.add_step(make_key_press("E", 25)),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            toolbar,
            text="+ Wait 200ms",
            width=102,
            height=32,
            fg_color="#78350F",
            hover_color="#92400E",
            command=lambda: self.add_step(make_wait(200, 10)),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            toolbar,
            text="+ Click",
            width=76,
            height=32,
            fg_color="#064E3B",
            hover_color="#047857",
            command=lambda: self.add_step(make_mouse_click(MouseButton.LEFT, ClickType.SINGLE)),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            toolbar,
            text="+ Move",
            width=74,
            height=32,
            fg_color="#164E63",
            hover_color="#0E7490",
            command=lambda: self.add_step(make_mouse_move(500, 500, MoveMode.ABSOLUTE)),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            toolbar,
            text="+ Scroll",
            width=76,
            height=32,
            fg_color="#3B0764",
            hover_color="#581C87",
            command=lambda: self.add_step(make_mouse_scroll(scroll_dy=-3)),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            toolbar,
            text="+ Custom Step...",
            width=120,
            height=32,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.open_add_step_dialog,
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            toolbar,
            text="Clear",
            width=64,
            height=32,
            fg_color="#334155",
            hover_color=ACCENT_DANGER,
            command=self.clear_all_steps,
        ).pack(side="right", padx=3)

        # Right Column: Modern Tabbed Studio Inspector + Always-Visible Primary CTA
        right_panel = ctk.CTkFrame(
            workspace,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._build_primary_execution_dock(right_panel)
        self._build_inspector_tabs(right_panel)

    def _build_primary_execution_dock(self, parent: ctk.CTkFrame) -> None:
        """Build the always-accessible Master Execution Dock at the top of the right panel."""
        dock = ctk.CTkFrame(parent, fg_color=BG_ELEVATED, corner_radius=10)
        dock.pack(fill="x", padx=14, pady=(14, 8))

        btn_row = ctk.CTkFrame(dock, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=10)

        self.run_toggle_btn = ctk.CTkButton(
            btn_row,
            text=f"▶ START MACRO ({self.profile.start_stop_hotkey})",
            fg_color=ACCENT_SUCCESS,
            hover_color=ACCENT_SUCCESS_HOVER,
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.toggle_execution,
        )
        self.run_toggle_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.dry_run_var = ctk.BooleanVar(value=False)
        self.dry_run_switch = ctk.CTkSwitch(
            btn_row,
            text="Dry-Run Sim",
            variable=self.dry_run_var,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.dry_run_switch.pack(side="right", padx=4)

    def _build_inspector_tabs(self, parent: ctk.CTkFrame) -> None:
        """Build the 4-tab Inspector (`Loop & Hotkeys`, `Recorder`, `Security`, `Console`)."""
        self.inspector_tabs = ctk.CTkTabview(
            parent,
            fg_color=BG_ROOT,
            segmented_button_fg_color=BG_ELEVATED,
            segmented_button_selected_color=ACCENT_PRIMARY,
            corner_radius=10,
        )
        self.inspector_tabs.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        tab_exec = self.inspector_tabs.add("⚙ Loop & Keys")
        tab_rec = self.inspector_tabs.add("⏺ Recorder")
        tab_sec = self.inspector_tabs.add("🛡 Security")
        tab_log = self.inspector_tabs.add(">_ Console")

        self._build_loop_and_hotkeys_tab(tab_exec)
        self._build_recorder_tab(tab_rec)
        self._build_security_tab(tab_sec)
        self._build_console_tab(tab_log)

    def _build_loop_and_hotkeys_tab(self, tab: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(tab, fg_color=BG_PANEL, corner_radius=10)
        card.pack(fill="both", expand=True, padx=6, pady=6)

        ctk.CTkLabel(
            card,
            text="Repeat Mode & Loop Timing",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=14, pady=(12, 6))

        self.repeat_mode_var = ctk.StringVar(value=self.profile.repeat_mode.value)
        self.repeat_mode_menu = ctk.CTkSegmentedButton(
            card,
            values=[m.value for m in RepeatMode],
            variable=self.repeat_mode_var,
            command=self._on_repeat_settings_changed,
            selected_color=ACCENT_PRIMARY,
        )
        self.repeat_mode_menu.pack(fill="x", padx=14, pady=6)

        r1 = ctk.CTkFrame(card, fg_color="transparent")
        r1.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(r1, text="Repeat Count / Duration (s):", width=185, anchor="w").pack(side="left")
        self.repeat_val_entry = ctk.CTkEntry(r1, width=100, fg_color=BG_INPUT)
        self.repeat_val_entry.insert(0, str(self.profile.repeat_count))
        self.repeat_val_entry.pack(side="right", padx=4)

        r2 = ctk.CTkFrame(card, fg_color="transparent")
        r2.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(r2, text="Inter-Cycle Delay (ms):", width=185, anchor="w").pack(side="left")
        self.cycle_delay_entry = ctk.CTkEntry(r2, width=100, fg_color=BG_INPUT)
        self.cycle_delay_entry.insert(0, str(self.profile.inter_cycle_delay_ms))
        self.cycle_delay_entry.pack(side="right", padx=4)

        # Hotkey Customizer Section
        ctk.CTkLabel(
            card,
            text="Global Hotkey Bindings",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=14, pady=(14, 4))

        hk1 = ctk.CTkFrame(card, fg_color="transparent")
        hk1.pack(fill="x", padx=14, pady=3)
        ctk.CTkLabel(hk1, text="Start / Stop Hotkey:", width=160, anchor="w").pack(side="left")
        self.hk_start_entry = ctk.CTkEntry(hk1, width=100, fg_color=BG_INPUT)
        self.hk_start_entry.insert(0, self.profile.start_stop_hotkey)
        self.hk_start_entry.pack(side="right", padx=4)

        hk2 = ctk.CTkFrame(card, fg_color="transparent")
        hk2.pack(fill="x", padx=14, pady=3)
        ctk.CTkLabel(hk2, text="Live Recorder Hotkey:", width=160, anchor="w").pack(side="left")
        self.hk_rec_entry = ctk.CTkEntry(hk2, width=100, fg_color=BG_INPUT)
        self.hk_rec_entry.insert(0, self.profile.record_hotkey)
        self.hk_rec_entry.pack(side="right", padx=4)

        hk3 = ctk.CTkFrame(card, fg_color="transparent")
        hk3.pack(fill="x", padx=14, pady=3)
        ctk.CTkLabel(hk3, text="Panic Kill-Switch Hotkey:", width=160, anchor="w").pack(side="left")
        self.hk_panic_entry = ctk.CTkEntry(hk3, width=100, fg_color=BG_INPUT)
        self.hk_panic_entry.insert(0, self.profile.panic_hotkey)
        self.hk_panic_entry.pack(side="right", padx=4)

        ctk.CTkButton(
            card,
            text="✓ Apply Loop & Hotkey Settings",
            height=34,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.sync_ui_settings_to_profile,
        ).pack(fill="x", padx=14, pady=(12, 10))

    def _build_recorder_tab(self, tab: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(tab, fg_color=BG_PANEL, corner_radius=10)
        card.pack(fill="both", expand=True, padx=6, pady=6)

        ctk.CTkLabel(
            card,
            text="Live Input Recorder",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=14, pady=(12, 6))

        self.rec_toggle_btn = ctk.CTkButton(
            card,
            text=f"● RECORD LIVE INPUTS ({self.profile.record_hotkey})",
            fg_color="#991B1B",
            hover_color="#B91C1C",
            height=40,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.toggle_recording,
        )
        self.rec_toggle_btn.pack(fill="x", padx=14, pady=8)

        self.rec_delays_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            card,
            text="Record delays between actions (>= 20ms)",
            variable=self.rec_delays_var,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=6)

        self.rec_merge_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            card,
            text="Coalesce KeyDown + KeyUp into single Press step",
            variable=self.rec_merge_var,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=6)

        self.rec_coords_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            card,
            text="Lock recorded mouse clicks to exact (X, Y) coordinates",
            variable=self.rec_coords_var,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=6)

        ctk.CTkLabel(
            card,
            text=(
                "Security Note: Synthetic inputs stamped with KeyForge's 0x4B465247 "
                "signature and your control hotkeys are automatically filtered out."
            ),
            text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(size=11),
            wraplength=360,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(12, 8))

    def _build_security_tab(self, tab: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(tab, fg_color=BG_PANEL, corner_radius=10)
        card.pack(fill="both", expand=True, padx=6, pady=6)

        ctk.CTkLabel(
            card,
            text="Cryptographic Integrity & Safety Auditor",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=14, pady=(12, 4))

        self.sec_hash_lbl = ctk.CTkLabel(
            card,
            text="SHA-256: computing...",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=ACCENT_CYAN,
        )
        self.sec_hash_lbl.pack(anchor="w", padx=14, pady=2)

        self.sec_budget_lbl = ctk.CTkLabel(
            card,
            text="Complexity Budget: 0 / 5,000 steps | 0 / 100,000 ops",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SECONDARY,
        )
        self.sec_budget_lbl.pack(anchor="w", padx=14, pady=(0, 6))

        self.sec_report_box = ctk.CTkTextbox(
            card,
            fg_color=BG_INPUT,
            text_color="#E2E8F0",
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8,
        )
        self.sec_report_box.pack(fill="both", expand=True, padx=14, pady=(4, 12))

    def _build_console_tab(self, tab: ctk.CTkFrame) -> None:
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(
            top,
            text="Live Event & Telemetry Log",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            top,
            text="Clear Log",
            width=74,
            height=24,
            fg_color=BG_CARD,
            hover_color="#28354A",
            font=ctk.CTkFont(size=11),
            command=lambda: self.console_box.delete("1.0", "end"),
        ).pack(side="right", padx=4)

        self.console_box = ctk.CTkTextbox(
            tab,
            fg_color=BG_INPUT,
            text_color="#CBD5E1",
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8,
        )
        self.console_box.pack(fill="both", expand=True, padx=6, pady=(4, 6))

    # -------------------------------------------------------------------------
    # Undo / Redo & Sequence Builder Operations
    # -------------------------------------------------------------------------

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
        """Scale all Wait step durations (and jitter) by factor (e.g. 0.5 for 2x speed, 2.0 for 0.5x speed)."""
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

    def _on_filter_changed(self, _event: object = None) -> None:
        self._filter_query = self.filter_entry.get().strip().lower()
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
        """Return secondary technical metadata for a timeline step card."""
        if act.action_type == ActionType.KEY_PRESS:
            return f"Hold: {act.hold_ms}ms | Mode: Tap"
        if act.action_type in (ActionType.KEY_DOWN, ActionType.KEY_UP):
            return "Explicit State Transition | Auto-Sanitized on Stop"
        if act.action_type == ActionType.WAIT:
            if act.jitter_ms > 0:
                lo = max(1, act.wait_ms - act.jitter_ms)
                hi = act.wait_ms + act.jitter_ms
                return f"Humanized Range: {lo}ms – {hi}ms"
            return "Deterministic Fixed Delay"
        if act.action_type == ActionType.MOUSE_CLICK:
            target = f"Fixed ({act.x}, {act.y})" if act.x is not None else "Dynamic Live Cursor"
            return f"Hold: {act.hold_ms}ms | Target: {target}"
        if act.action_type == ActionType.MOUSE_MOVE:
            return f"Coordinate Space: {act.move_mode.value.capitalize()}"
        if act.action_type == ActionType.MOUSE_SCROLL:
            return f"Wheel Delta: dY={act.scroll_dy}, dX={act.scroll_dx}"
        if act.action_type == ActionType.REPEAT_BLOCK:
            return f"{len(act.sub_actions)} nested inner step(s) × {act.repeat_count} iterations"
        return ""

    @staticmethod
    def _step_metric_pill(act: MacroAction) -> str:
        """Return compact monospace metric pill text for right-column alignment."""
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

    def refresh_sequence_view(self) -> None:
        """Rebuild the modern timeline step cards in the sequence scroll container."""
        for child in self.sequence_scroll.winfo_children():
            child.destroy()
        self._step_row_frames.clear()

        actions = self.profile.actions
        enabled_count = sum(1 for a in actions if a.enabled)
        wait_total = self.profile.total_cycle_wait_ms()
        self.step_count_lbl.configure(text=f"{enabled_count}/{len(actions)} active | Cycle: {wait_total}ms")

        if not actions:
            empty_lbl = ctk.CTkLabel(
                self.sequence_scroll,
                text=(
                    "No steps in macro sequence.\n"
                    "Select a Quick Template chip above or click '+ Key', '+ Wait 200ms', '+ Click' below."
                ),
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=13),
                justify="center",
            )
            empty_lbl.pack(pady=60)
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
                height=52,
                border_width=1,
                border_color=BORDER_SUBTLE,
            )
            row.pack_propagate(False)
            row.pack(fill="x", pady=3, padx=2)
            self._step_row_frames[act.id] = row

            # Colored Left Accent Stripe
            stripe_color = get_stripe_color(act.action_type)
            stripe = ctk.CTkFrame(row, fg_color=stripe_color, width=5, height=42, corner_radius=3)
            stripe.pack(side="left", fill="y", padx=(2, 6), pady=4)

            # Step Index Pill
            ctk.CTkLabel(
                row,
                text=f"#{idx + 1:02d}",
                width=34,
                font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                text_color=TEXT_SECONDARY,
            ).pack(side="left", padx=(2, 2))

            # Enable Toggle
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

            # Action Type Badge
            badge_bg, badge_fg = get_badge_colors(act.action_type)
            ctk.CTkLabel(
                row,
                text=act.badge_text(),
                fg_color=badge_bg,
                text_color=badge_fg,
                corner_radius=6,
                width=62,
                height=24,
                font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(side="left", padx=6)

            # Primary Title + Secondary Subtitle Stack
            text_col = ctk.CTkFrame(row, fg_color="transparent", height=40)
            text_col.pack(side="left", fill="x", expand=True, padx=6, pady=4)

            desc_color = TEXT_PRIMARY if act.enabled else TEXT_MUTED
            title_lbl = ctk.CTkLabel(
                text_col,
                text=act.describe(),
                text_color=desc_color,
                height=18,
                font=ctk.CTkFont(size=13, weight="bold"),
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
                    font=ctk.CTkFont(size=10),
                    anchor="w",
                )
                sub_lbl.pack(fill="x", anchor="w")
                sub_lbl.bind("<Double-Button-1>", lambda _e, i=idx: self.open_edit_step_dialog(i))

            # Bind hover & double-click edit on step row
            row.bind("<Enter>", lambda _e, r=row, aid=act.id: self._on_row_hover(r, aid, True))
            row.bind("<Leave>", lambda _e, r=row, aid=act.id: self._on_row_hover(r, aid, False))
            row.bind("<Double-Button-1>", lambda _e, i=idx: self.open_edit_step_dialog(i))
            title_lbl.bind("<Double-Button-1>", lambda _e, i=idx: self.open_edit_step_dialog(i))

            # Row Controls (Delete, Duplicate, Edit, Down, Up)
            ctk.CTkButton(
                row,
                text="✕",
                width=28,
                height=26,
                fg_color="#3F1D24",
                hover_color=ACCENT_DANGER,
                command=lambda i=idx: self.delete_step(i),
            ).pack(side="right", padx=(2, 8))

            ctk.CTkButton(
                row,
                text="Dup",
                width=38,
                height=26,
                fg_color="#1E293B",
                hover_color="#334155",
                command=lambda i=idx: self.duplicate_step(i),
            ).pack(side="right", padx=2)

            ctk.CTkButton(
                row,
                text="Edit",
                width=42,
                height=26,
                fg_color="#1E3A8A",
                hover_color=ACCENT_PRIMARY,
                command=lambda i=idx: self.open_edit_step_dialog(i),
            ).pack(side="right", padx=2)

            ctk.CTkButton(
                row,
                text="▼",
                width=28,
                height=26,
                fg_color="#1E293B",
                hover_color="#334155",
                command=lambda i=idx: self.move_step_down(i),
            ).pack(side="right", padx=2)

            ctk.CTkButton(
                row,
                text="▲",
                width=28,
                height=26,
                fg_color="#1E293B",
                hover_color="#334155",
                command=lambda i=idx: self.move_step_up(i),
            ).pack(side="right", padx=2)

            # Right-aligned Monospace Metric Pill
            ctk.CTkLabel(
                row,
                text=self._step_metric_pill(act),
                font=ctk.CTkFont(family="Consolas", size=11),
                fg_color=BG_INPUT,
                text_color=TEXT_SECONDARY,
                corner_radius=5,
                padx=8,
                pady=2,
            ).pack(side="right", padx=(4, 6))

    def _on_row_hover(self, row: ctk.CTkFrame, action_id: str, entering: bool) -> None:
        if action_id == self._active_step_id:
            row.configure(fg_color=BG_CARD_ACTIVE)
        else:
            row.configure(fg_color=BG_CARD_HOVER if entering else BG_CARD)

    def _sync_dsl_entry_from_steps(self) -> None:
        self.dsl_entry.delete(0, "end")
        self.dsl_entry.insert(0, format_actions_to_dsl(self.profile.actions))

    def _on_sequence_mutated(self) -> None:
        self._sync_dsl_entry_from_steps()
        self.refresh_sequence_view()
        self.run_security_audit()

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
            self._log_event(f"[DSL Error] {exc}")
            return False

    # -------------------------------------------------------------------------
    # Execution, Recording & Panic Controls
    # -------------------------------------------------------------------------

    def sync_profile_settings_to_ui(self) -> None:
        self.repeat_mode_var.set(self.profile.repeat_mode.value)
        self.repeat_val_entry.delete(0, "end")
        if self.profile.repeat_mode == RepeatMode.DURATION:
            self.repeat_val_entry.insert(0, str(self.profile.repeat_duration_s))
        else:
            self.repeat_val_entry.insert(0, str(self.profile.repeat_count))
        self.cycle_delay_entry.delete(0, "end")
        self.cycle_delay_entry.insert(0, str(self.profile.inter_cycle_delay_ms))

        self.hk_start_entry.delete(0, "end")
        self.hk_start_entry.insert(0, self.profile.start_stop_hotkey)
        self.hk_rec_entry.delete(0, "end")
        self.hk_rec_entry.insert(0, self.profile.record_hotkey)
        self.hk_panic_entry.delete(0, "end")
        self.hk_panic_entry.insert(0, self.profile.panic_hotkey)

    def sync_ui_settings_to_profile(self) -> bool:
        try:
            mode = RepeatMode(self.repeat_mode_var.get())
            self.profile.repeat_mode = mode
            raw_val = self.repeat_val_entry.get().strip()
            if mode == RepeatMode.DURATION:
                self.profile.repeat_duration_s = float(raw_val)
            elif mode == RepeatMode.COUNT:
                self.profile.repeat_count = int(float(raw_val))

            self.profile.inter_cycle_delay_ms = int(self.cycle_delay_entry.get().strip())
            self.profile.start_stop_hotkey = self.hk_start_entry.get().strip() or "F6"
            self.profile.record_hotkey = self.hk_rec_entry.get().strip() or "F7"
            self.profile.panic_hotkey = self.hk_panic_entry.get().strip() or "F12"
            self.profile.validate()
            self._rebind_hotkeys()
            self.hotkey_sub_lbl.configure(
                text=(
                    f"Run: {self.profile.start_stop_hotkey} | "
                    f"Rec: {self.profile.record_hotkey} | "
                    f"Panic: {self.profile.panic_hotkey}"
                )
            )
            self.top_panic_btn.configure(text=f"⚠ PANIC KILL-SWITCH ({self.profile.panic_hotkey})")
            self.run_security_audit()
            self.refresh_sequence_view()
            self._log_event(
                f"Applied settings: mode={mode.value}, delay={self.profile.inter_cycle_delay_ms}ms, "
                f"hotkeys=[{self.profile.start_stop_hotkey}/{self.profile.record_hotkey}/{self.profile.panic_hotkey}]."
            )
            return True
        except Exception as exc:
            self._log_event(f"[Settings Error] {exc}")
            return False

    def _on_repeat_settings_changed(self, _selected: str) -> None:
        self.sync_ui_settings_to_profile()

    def toggle_execution(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
            self._log_event("Macro execution stopped.")
            self._update_run_button_ui()
            return

        if self.recorder.is_recording:
            self._log_event("Cannot start macro while Live Recorder is active.")
            return

        if not self.sync_ui_settings_to_profile():
            return

        use_dry_run = self.dry_run_var.get()
        if use_dry_run:
            self.dry_run_backend.clear()
            self.executor.backend = self.dry_run_backend
        else:
            self.executor.backend = self.win32_backend

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
        self.executor.panic_abort()
        self._update_run_button_ui()
        self._log_event("⚠ PANIC KILL-SWITCH TRIGGERED! Execution aborted and all held keys/buttons released.")

    def toggle_recording(self) -> None:
        if self.executor.is_running:
            self._log_event("Stop macro execution before starting Live Recorder.")
            return

        if self.recorder.is_recording:
            recorded = self.recorder.stop()
            self.rec_toggle_btn.configure(
                text=f"● RECORD LIVE INPUTS ({self.profile.record_hotkey})",
                fg_color="#991B1B",
            )
            if recorded:
                self._record_undo_snapshot()
                self.profile.actions = recorded
                self._on_sequence_mutated()
                self._log_event(f"Live Recording complete: captured {len(recorded)} step(s).")
            else:
                self._log_event("Live Recording stopped (0 inputs captured).")
        else:
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
                text=f"⏹ STOP RECORDING ({self.profile.record_hotkey})",
                fg_color=ACCENT_WARNING,
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
        self._on_sequence_mutated()
        self._log_event(f"Loaded preset profile: {self.profile.name}")

    def action_new_profile(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
        self._record_undo_snapshot()
        self.profile = MacroProfile(
            name="Custom Macro Profile",
            description="Created in KeyForge Studio",
            repeat_mode=RepeatMode.COUNT,
            repeat_count=5,
            inter_cycle_delay_ms=100,
            actions=[],
        )
        self.sync_profile_settings_to_ui()
        self._on_sequence_mutated()
        self._log_event("Created new blank profile.")

    def save_profile_to_path(self, path: str | Path) -> Path:
        self.sync_ui_settings_to_profile()
        saved_path = save_profile_secure(self.profile, path)
        self._log_event(f"Saved SHA-256 signed profile to: {saved_path.name}")
        return saved_path

    def load_profile_from_path(self, path: str | Path) -> MacroProfile:
        if self.executor.is_running:
            self.executor.stop()
        loaded_profile, sig_verified = load_profile_secure(path, require_signature=False)
        self._record_undo_snapshot()
        self.profile = loaded_profile
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
                self.save_profile_to_path(file_path)
            except Exception as exc:
                self._log_event(f"[Save Error] {exc}")

    def action_open_profile_dialog(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Open KeyForge Macro Profile",
            filetypes=[("KeyForge Macro JSON", "*.json *.kfmacro")],
        )
        if file_path:
            try:
                self.load_profile_from_path(file_path)
            except Exception as exc:
                self._log_event(f"[Load Security Error] {exc}")

    def run_security_audit(self) -> AuditReport:
        report = audit_profile(self.profile)
        summary = f"🛡 {report.badge_summary()}"
        if report.has_critical:
            self.audit_badge.configure(text=summary, fg_color="#7F1D1D", text_color="#FCA5A5")
        elif report.has_warnings:
            self.audit_badge.configure(text=summary, fg_color="#78350F", text_color="#FCD34D")
        else:
            self.audit_badge.configure(text=summary, fg_color="#064E3B", text_color="#6EE7B7")

        # Update Security Inspector Tab
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

        for finding in report.findings:
            if finding.severity in (AuditSeverity.WARNING, AuditSeverity.CRITICAL):
                self._log_event(f"[{finding.code}] {finding.title}: {finding.description}")

        return report

    # -------------------------------------------------------------------------
    # Background Hotkey & Telemetry Loop
    # -------------------------------------------------------------------------

    def _rebind_hotkeys(self) -> None:
        self.hotkey_manager.clear()
        self.hotkey_manager.register(
            "start_stop",
            self.profile.start_stop_hotkey,
            lambda: self.after(0, self.toggle_execution),
        )
        self.hotkey_manager.register(
            "panic",
            self.profile.panic_hotkey,
            lambda: self.after(0, self.trigger_panic_stop),
        )
        self.hotkey_manager.register(
            "record",
            self.profile.record_hotkey,
            lambda: self.after(0, self.toggle_recording),
        )

    def _update_run_button_ui(self) -> None:
        if self.executor.is_running:
            self.run_toggle_btn.configure(
                text=f"⏹ STOP MACRO ({self.profile.start_stop_hotkey})",
                fg_color=ACCENT_DANGER,
                hover_color=ACCENT_DANGER_HOVER,
            )
        else:
            self.run_toggle_btn.configure(
                text=f"▶ START MACRO ({self.profile.start_stop_hotkey})",
                fg_color=ACCENT_SUCCESS,
                hover_color=ACCENT_SUCCESS_HOVER,
            )

    def _schedule_ui_tick(self) -> None:
        if not self.winfo_exists():
            return
        self._ui_tick()
        self._ui_tick_after_id = self.after(50, self._schedule_ui_tick)

    def _ui_tick(self) -> None:
        telem: ExecutionTelemetry = self.executor.telemetry
        self._update_run_button_ui()

        state_text = f"● {telem.state.value.upper()}"
        state_color = {
            ExecutorState.IDLE: ACCENT_SUCCESS,
            ExecutorState.RUNNING: ACCENT_PRIMARY,
            ExecutorState.STOPPING: ACCENT_WARNING,
            ExecutorState.PANIC_ABORTED: ACCENT_DANGER,
            ExecutorState.ERROR: ACCENT_DANGER,
        }[telem.state]
        self.status_lbl.configure(text=state_text, text_color=state_color)

        mode_str = "Dry-Run Simulation" if self.dry_run_var.get() else "Live OS Injection"
        self.mode_sub_lbl.configure(text=f"Mode: {mode_str}")

        self.cycles_lbl.configure(text=f"{telem.cycles_completed} Cycles | {telem.actions_executed} Actions")

        # Update progress bar
        if self.profile.repeat_mode == RepeatMode.COUNT and self.profile.repeat_count > 0:
            ratio = min(1.0, telem.cycles_completed / float(self.profile.repeat_count))
            self.cycle_progress.set(ratio)
        elif self.profile.repeat_mode == RepeatMode.DURATION and self.profile.repeat_duration_s > 0:
            ratio = min(1.0, telem.elapsed_seconds / float(self.profile.repeat_duration_s))
            self.cycle_progress.set(ratio)
        else:
            self.cycle_progress.set(1.0 if telem.state == ExecutorState.RUNNING else 0.0)

        wait_ms = self.profile.total_cycle_wait_ms()
        self.actions_lbl.configure(text=f"Cycle: {wait_ms}ms | Elapsed: {telem.elapsed_seconds:.1f}s")

        if IS_WINDOWS:
            cx, cy = self.win32_backend.get_cursor_pos()
        else:
            cx, cy = self.winfo_pointerx(), self.winfo_pointery()
        self.cursor_lbl.configure(text=f"XY: ({cx}, {cy})")

        # Highlight currently executing step card
        if telem.current_action_id != self._active_step_id:
            prev_id = self._active_step_id
            self._active_step_id = telem.current_action_id
            if prev_id and prev_id in self._step_row_frames:
                self._step_row_frames[prev_id].configure(fg_color=BG_CARD)
            if self._active_step_id and self._active_step_id in self._step_row_frames:
                self._step_row_frames[self._active_step_id].configure(fg_color=BG_CARD_ACTIVE)

    def _log_event(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self.console_box.insert("end", line)
        self.console_box.see("end")
        if hasattr(self, "footer_status_lbl"):
            self.footer_status_lbl.configure(text=f"● [{ts}] {message}")

    def _on_close(self) -> None:
        if self._ui_tick_after_id is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._ui_tick_after_id)
            self._ui_tick_after_id = None
        self.hotkey_manager.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
        if self.executor.is_running:
            self.executor.panic_abort()
        self.destroy()
