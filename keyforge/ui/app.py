"""Main CustomTkinter desktop GUI application for KeyForge."""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from keyforge import __version__
from keyforge.engine.backend import DryRunBackend
from keyforge.engine.executor import ExecutionTelemetry, ExecutorState, MacroExecutor
from keyforge.engine.hotkeys import GlobalHotkeyManager
from keyforge.engine.recorder import MacroRecorder, RecorderConfig
from keyforge.engine.win32_input import Win32InputBackend
from keyforge.models.action import (
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
from keyforge.security.integrity import load_profile_secure, save_profile_secure
from keyforge.ui.step_dialog import StepEditorDialog
from keyforge.ui.theme import (
    ACCENT_DANGER,
    ACCENT_DANGER_HOVER,
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    ACCENT_PURPLE,
    ACCENT_SUCCESS,
    ACCENT_SUCCESS_HOVER,
    ACCENT_WARNING,
    BG_CARD,
    BG_CARD_ACTIVE,
    BG_INPUT,
    BG_PANEL,
    BG_ROOT,
    BORDER_SUBTLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    get_badge_colors,
)


class KeyForgeApp(ctk.CTk):
    """KeyForge Desktop Application Window & Controller."""

    def __init__(self, start_hotkey_listener: bool = True) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"KeyForge v{__version__} — FOSS Visual Macro Builder & Auto-Clicker")
        self.geometry("1220x780")
        self.minsize(1060, 680)
        self.configure(fg_color=BG_ROOT)

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

        # Build UI Layout
        self._build_top_bar()
        self._build_quick_forge_bar()
        self._build_main_workspace()

        # Initial Render & Audit
        self.refresh_sequence_view()
        self.sync_profile_settings_to_ui()
        self.run_security_audit()
        self._log_event(
            f"KeyForge v{__version__} initialized. Hardware Panic Kill-Switch armed on [{self.profile.panic_hotkey}]."
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
        bar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0, height=60)
        bar.pack(fill="x", padx=0, pady=0)

        title_lbl = ctk.CTkLabel(
            bar,
            text="⚡ KEYFORGE",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        title_lbl.pack(side="left", padx=(20, 6), pady=12)

        ver_badge = ctk.CTkLabel(
            bar,
            text=f"v{__version__} FOSS",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#1E3A8A",
            text_color="#93C5FD",
            corner_radius=6,
            padx=8,
            pady=2,
        )
        ver_badge.pack(side="left", padx=(0, 18), pady=12)

        ctk.CTkLabel(bar, text="Preset:", text_color=TEXT_SECONDARY).pack(side="left", padx=(4, 6))
        self.preset_var = ctk.StringVar(value=self.profile.name)
        self.preset_menu = ctk.CTkOptionMenu(
            bar,
            variable=self.preset_var,
            values=list(self.presets.keys()),
            command=self.load_preset,
            width=260,
            fg_color=BG_CARD,
        )
        self.preset_menu.pack(side="left", padx=4)

        ctk.CTkButton(
            bar,
            text="New",
            width=64,
            fg_color=BG_CARD,
            hover_color="#28354A",
            command=self.action_new_profile,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            bar,
            text="Open JSON",
            width=92,
            fg_color=BG_CARD,
            hover_color="#28354A",
            command=self.action_open_profile_dialog,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            bar,
            text="Save JSON",
            width=92,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            command=self.action_save_profile_dialog,
        ).pack(side="left", padx=4)

        # Emergency Panic Button in Top Bar
        self.top_panic_btn = ctk.CTkButton(
            bar,
            text=f"⚠ PANIC STOP ({self.profile.panic_hotkey})",
            fg_color=ACCENT_DANGER,
            hover_color=ACCENT_DANGER_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            width=155,
            command=self.trigger_panic_stop,
        )
        self.top_panic_btn.pack(side="right", padx=(10, 20), pady=12)

        # Security Audit Status Pill
        self.audit_badge = ctk.CTkLabel(
            bar,
            text="SECURITY AUDIT: PASSED",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#064E3B",
            text_color="#6EE7B7",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.audit_badge.pack(side="right", padx=6, pady=12)

    def _build_quick_forge_bar(self) -> None:
        qbar = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        qbar.pack(fill="x", padx=16, pady=(10, 6))

        ctk.CTkLabel(
            qbar,
            text="Quick-Forge Recipe:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=(14, 8), pady=10)

        self.dsl_entry = ctk.CTkEntry(
            qbar,
            placeholder_text="e.g. Press E -> Wait 200ms -> Click -> Press 1",
            fg_color=BG_INPUT,
            height=34,
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.dsl_entry.pack(side="left", fill="x", expand=True, padx=6, pady=10)
        self.dsl_entry.insert(0, format_actions_to_dsl(self.profile.actions))

        ctk.CTkButton(
            qbar,
            text="Replace Sequence",
            width=130,
            height=32,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            command=lambda: self.apply_quick_expression(append=False),
        ).pack(side="left", padx=4, pady=10)

        ctk.CTkButton(
            qbar,
            text="Append Steps",
            width=110,
            height=32,
            fg_color=ACCENT_PURPLE,
            hover_color="#7C3AED",
            command=lambda: self.apply_quick_expression(append=True),
        ).pack(side="left", padx=(4, 14), pady=10)

    def _build_main_workspace(self) -> None:
        workspace = ctk.CTkFrame(self, fg_color="transparent")
        workspace.pack(fill="both", expand=True, padx=16, pady=(4, 14))
        workspace.grid_columnconfigure(0, weight=3)
        workspace.grid_columnconfigure(1, weight=2)
        workspace.grid_rowconfigure(0, weight=1)

        # Left Column: Visual Action Sequence Builder
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
            text="Visual Action Sequence Builder",
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

        self.sequence_scroll = ctk.CTkScrollableFrame(
            left_panel,
            fg_color=BG_ROOT,
            corner_radius=8,
        )
        self.sequence_scroll.pack(fill="both", expand=True, padx=14, pady=6)

        # Quick-Add Step Toolbar
        toolbar = ctk.CTkFrame(left_panel, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=(6, 12))

        ctk.CTkButton(
            toolbar,
            text="+ Key",
            width=72,
            height=32,
            fg_color="#1E3A8A",
            hover_color="#1D4ED8",
            command=lambda: self.add_step(make_key_press("E", 25)),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            toolbar,
            text="+ Wait 200ms",
            width=100,
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
            width=118,
            height=32,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
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

        # Right Column: Execution, Recorder, and Security Audit Console
        right_panel = ctk.CTkFrame(workspace, fg_color="transparent")
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._build_execution_card(right_panel)
        self._build_recorder_card(right_panel)
        self._build_telemetry_and_security_card(right_panel)

    def _build_execution_card(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        card.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            card,
            text="Execution & Loop Engine",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=16, pady=(12, 8))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 8))

        self.run_toggle_btn = ctk.CTkButton(
            btn_row,
            text=f"▶ START MACRO ({self.profile.start_stop_hotkey})",
            fg_color=ACCENT_SUCCESS,
            hover_color=ACCENT_SUCCESS_HOVER,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.toggle_execution,
        )
        self.run_toggle_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.dry_run_var = ctk.BooleanVar(value=False)
        self.dry_run_switch = ctk.CTkSwitch(
            btn_row,
            text="Dry-Run Sim",
            variable=self.dry_run_var,
            font=ctk.CTkFont(size=12),
        )
        self.dry_run_switch.pack(side="right", padx=4)

        # Repeat Mode Row
        rep_row = ctk.CTkFrame(card, fg_color="transparent")
        rep_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(rep_row, text="Repeat Mode:", width=115, anchor="w").pack(side="left")
        self.repeat_mode_var = ctk.StringVar(value=self.profile.repeat_mode.value)
        self.repeat_mode_menu = ctk.CTkOptionMenu(
            rep_row,
            variable=self.repeat_mode_var,
            values=[m.value for m in RepeatMode],
            command=self._on_repeat_settings_changed,
            width=140,
            fg_color=BG_CARD,
        )
        self.repeat_mode_menu.pack(side="left", padx=4)

        ctk.CTkLabel(rep_row, text="Count/Sec:", width=75, anchor="e").pack(side="left", padx=4)
        self.repeat_val_entry = ctk.CTkEntry(rep_row, width=75, fg_color=BG_INPUT)
        self.repeat_val_entry.insert(0, str(self.profile.repeat_count))
        self.repeat_val_entry.pack(side="left", padx=4)

        # Inter-Cycle Delay & Hotkey Row
        delay_row = ctk.CTkFrame(card, fg_color="transparent")
        delay_row.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(delay_row, text="Cycle Delay (ms):", width=115, anchor="w").pack(side="left")
        self.cycle_delay_entry = ctk.CTkEntry(delay_row, width=85, fg_color=BG_INPUT)
        self.cycle_delay_entry.insert(0, str(self.profile.inter_cycle_delay_ms))
        self.cycle_delay_entry.pack(side="left", padx=4)

        ctk.CTkButton(
            delay_row,
            text="Apply Settings",
            width=110,
            height=28,
            fg_color=BG_CARD,
            hover_color="#28354A",
            command=self.sync_ui_settings_to_profile,
        ).pack(side="right", padx=4)

    def _build_recorder_card(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        card.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            card,
            text="Live Input Recorder",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=16, pady=(12, 8))

        rec_row = ctk.CTkFrame(card, fg_color="transparent")
        rec_row.pack(fill="x", padx=16, pady=(0, 8))

        self.rec_toggle_btn = ctk.CTkButton(
            rec_row,
            text=f"● RECORD LIVE INPUTS ({self.profile.record_hotkey})",
            fg_color="#991B1B",
            hover_color="#B91C1C",
            height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.toggle_recording,
        )
        self.rec_toggle_btn.pack(fill="x", expand=True)

        opts_row = ctk.CTkFrame(card, fg_color="transparent")
        opts_row.pack(fill="x", padx=16, pady=(2, 12))

        self.rec_delays_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opts_row,
            text="Record Delays",
            variable=self.rec_delays_var,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 10))

        self.rec_merge_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opts_row,
            text="Merge Tap",
            variable=self.rec_merge_var,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 10))

        self.rec_coords_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            opts_row,
            text="Lock Click XY",
            variable=self.rec_coords_var,
            checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(size=12),
        ).pack(side="left")

    def _build_telemetry_and_security_card(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color=BG_PANEL,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        card.pack(fill="both", expand=True)

        ctk.CTkLabel(
            card,
            text="Live Telemetry & Security Audit Console",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=16, pady=(12, 6))

        stats_grid = ctk.CTkFrame(card, fg_color=BG_CARD, corner_radius=8)
        stats_grid.pack(fill="x", padx=16, pady=4)

        self.status_lbl = ctk.CTkLabel(
            stats_grid,
            text="STATE: IDLE",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT_SUCCESS,
        )
        self.status_lbl.pack(side="left", padx=10, pady=8)

        self.cycles_lbl = ctk.CTkLabel(
            stats_grid,
            text="Cycles: 0",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_PRIMARY,
        )
        self.cycles_lbl.pack(side="left", padx=10, pady=8)

        self.actions_lbl = ctk.CTkLabel(
            stats_grid,
            text="Actions: 0",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_PRIMARY,
        )
        self.actions_lbl.pack(side="left", padx=10, pady=8)

        self.cursor_lbl = ctk.CTkLabel(
            stats_grid,
            text="XY: (0, 0)",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=TEXT_SECONDARY,
        )
        self.cursor_lbl.pack(side="right", padx=10, pady=8)

        self.console_box = ctk.CTkTextbox(
            card,
            fg_color=BG_INPUT,
            text_color="#CBD5E1",
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8,
        )
        self.console_box.pack(fill="both", expand=True, padx=16, pady=(6, 14))

    # -------------------------------------------------------------------------
    # Sequence Builder Operations
    # -------------------------------------------------------------------------

    def refresh_sequence_view(self) -> None:
        """Rebuild the visual step cards in the sequence scroll container."""
        for child in self.sequence_scroll.winfo_children():
            child.destroy()
        self._step_row_frames.clear()

        actions = self.profile.actions
        self.step_count_lbl.configure(
            text=f"{len(actions)} step(s) | Cycle Wait: {self.profile.total_cycle_wait_ms()}ms"
        )

        if not actions:
            empty_lbl = ctk.CTkLabel(
                self.sequence_scroll,
                text=(
                    "No steps in macro sequence.\n"
                    "Use the Quick-Forge Recipe bar above or click '+ Key', '+ Wait 200ms', '+ Click' below."
                ),
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=13),
                justify="center",
            )
            empty_lbl.pack(pady=60)
            return

        for idx, act in enumerate(actions):
            is_active = act.id == self._active_step_id
            row_bg = BG_CARD_ACTIVE if is_active else BG_CARD
            row = ctk.CTkFrame(self.sequence_scroll, fg_color=row_bg, corner_radius=8, height=44)
            row.pack(fill="x", pady=3, padx=2)
            self._step_row_frames[act.id] = row

            # Step Index
            ctk.CTkLabel(
                row,
                text=f"#{idx + 1}",
                width=34,
                font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                text_color=TEXT_SECONDARY,
            ).pack(side="left", padx=(8, 2), pady=8)

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
                corner_radius=5,
                width=64,
                font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(side="left", padx=6, pady=8)

            # Step Description
            desc_color = TEXT_PRIMARY if act.enabled else TEXT_MUTED
            ctk.CTkLabel(
                row,
                text=act.describe(),
                text_color=desc_color,
                font=ctk.CTkFont(size=13),
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=6)

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

    def _sync_dsl_entry_from_steps(self) -> None:
        self.dsl_entry.delete(0, "end")
        self.dsl_entry.insert(0, format_actions_to_dsl(self.profile.actions))

    def _on_sequence_mutated(self) -> None:
        self._sync_dsl_entry_from_steps()
        self.refresh_sequence_view()
        self.run_security_audit()

    def _on_toggle_step_enabled(self, action: MacroAction, enabled: bool) -> None:
        action.enabled = enabled
        self._on_sequence_mutated()

    def add_step(self, action: MacroAction) -> None:
        self.profile.actions.append(action)
        self._on_sequence_mutated()
        self._log_event(f"Added step: {action.describe()}")

    def delete_step(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions):
            removed = self.profile.actions.pop(index)
            self._on_sequence_mutated()
            self._log_event(f"Deleted step #{index + 1}: {removed.describe()}")

    def duplicate_step(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions):
            cloned = self.profile.actions[index].clone()
            self.profile.actions.insert(index + 1, cloned)
            self._on_sequence_mutated()
            self._log_event(f"Duplicated step #{index + 1}: {cloned.describe()}")

    def move_step_up(self, index: int) -> None:
        if 0 < index < len(self.profile.actions):
            acts = self.profile.actions
            acts[index - 1], acts[index] = acts[index], acts[index - 1]
            self._on_sequence_mutated()

    def move_step_down(self, index: int) -> None:
        if 0 <= index < len(self.profile.actions) - 1:
            acts = self.profile.actions
            acts[index + 1], acts[index] = acts[index], acts[index + 1]
            self._on_sequence_mutated()

    def clear_all_steps(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
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
            self.profile.actions[index] = updated
            self._on_sequence_mutated()
            self._log_event(f"Updated step #{index + 1}: {updated.describe()}")

        StepEditorDialog(self, initial_action=current, on_save=_save_edited)

    def apply_quick_expression(self, append: bool = False) -> bool:
        """Parse the Quick-Forge recipe entry and replace or append steps."""
        expr = self.dsl_entry.get().strip()
        try:
            parsed_steps = parse_quick_expression(expr)
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
            self.profile.validate()
            self.run_security_audit()
            self.refresh_sequence_view()
            self._log_event(
                f"Updated loop settings: mode={mode.value}, cycle_delay={self.profile.inter_cycle_delay_ms}ms."
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
            mode_label = "DRY-RUN SIMULATION" if use_dry_run else "LIVE WIN32"
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
        self.profile = MacroProfile.from_payload_dict(preset.to_payload_dict())
        self.sync_profile_settings_to_ui()
        self._on_sequence_mutated()
        self._log_event(f"Loaded preset profile: {self.profile.name}")

    def action_new_profile(self) -> None:
        if self.executor.is_running:
            self.executor.stop()
        self.profile = MacroProfile(
            name="Custom Macro Profile",
            description="Created in KeyForge Visual Builder",
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
        summary = report.badge_summary()
        if report.has_critical:
            self.audit_badge.configure(text=summary, fg_color="#7F1D1D", text_color="#FCA5A5")
        elif report.has_warnings:
            self.audit_badge.configure(text=summary, fg_color="#78350F", text_color="#FCD34D")
        else:
            self.audit_badge.configure(text=summary, fg_color="#064E3B", text_color="#6EE7B7")

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

        state_text = f"STATE: {telem.state.value.upper()}"
        state_color = {
            ExecutorState.IDLE: ACCENT_SUCCESS,
            ExecutorState.RUNNING: ACCENT_PRIMARY,
            ExecutorState.STOPPING: ACCENT_WARNING,
            ExecutorState.PANIC_ABORTED: ACCENT_DANGER,
            ExecutorState.ERROR: ACCENT_DANGER,
        }[telem.state]
        self.status_lbl.configure(text=state_text, text_color=state_color)
        self.cycles_lbl.configure(text=f"Cycles: {telem.cycles_completed}")
        self.actions_lbl.configure(text=f"Actions: {telem.actions_executed} ({telem.elapsed_seconds:.1f}s)")

        cx, cy = self.win32_backend.get_cursor_pos()
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
