"""Always-on-top compact floating Mini-HUD pill window for KeyForge."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from keyforge.ui.theme import (
    ACCENT_DANGER,
    ACCENT_DANGER_HOVER,
    ACCENT_SUCCESS,
    BG_CARD,
    BG_PANEL,
    BORDER_SUBTLE,
    BTN_NEUTRAL,
    BTN_NEUTRAL_HOVER,
    CTA_SUCCESS,
    CTA_SUCCESS_HOVER,
    TEXT_SECONDARY,
    apply_window_icon,
    get_fonts,
    get_logical_workarea,
)

if TYPE_CHECKING:
    from keyforge.ui.app import KeyForgeApp


class MiniHudWindow(ctk.CTkToplevel):
    """Compact always-on-top control bar with live macro state, Start/Stop, Panic, and Expand."""

    def __init__(self, master: KeyForgeApp) -> None:
        super().__init__(master)
        self.title("KeyForge HUD")
        self.resizable(False, False)
        self.configure(fg_color=BG_PANEL)

        self._master_app = master
        self._fonts = get_fonts(master)
        self._closed = False
        self._last_state: tuple[Any, ...] | None = None

        apply_window_icon(self)
        with contextlib.suppress(Exception):
            self.attributes("-topmost", True)

        self._position_hud()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._master_app.exit_mini_hud_mode)

    def _position_hud(self) -> None:
        work_left, work_top, work_w, _work_h, scale = get_logical_workarea(self._master_app)
        hud_w, hud_h = 410, 64
        x_px = work_left + max(16, int((work_w - hud_w - 24) * scale))
        y_px = work_top + max(16, int(20 * scale))
        self.geometry(f"{hud_w}x{hud_h}+{x_px}+{y_px}")

    def _build_ui(self) -> None:
        pill = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        pill.pack(fill="both", expand=True, padx=6, pady=6)

        info_col = ctk.CTkFrame(pill, fg_color="transparent")
        info_col.pack(side="left", padx=(10, 8), pady=6)

        self.status_lbl = ctk.CTkLabel(
            info_col,
            text="● READY",
            font=self._fonts["small_bold"],
            text_color=ACCENT_SUCCESS,
            anchor="w",
        )
        self.status_lbl.pack(anchor="w")

        self.cycles_lbl = ctk.CTkLabel(
            info_col,
            text="0 cycles",
            font=self._fonts["mono_small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self.cycles_lbl.pack(anchor="w")

        self.expand_btn = ctk.CTkButton(
            pill,
            text="Expand",
            width=64,
            height=30,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self._fonts["small_bold"],
            command=self._master_app.exit_mini_hud_mode,
        )
        self.expand_btn.pack(side="right", padx=(4, 8), pady=10)

        self.panic_btn = ctk.CTkButton(
            pill,
            text="Panic",
            width=72,
            height=30,
            fg_color=ACCENT_DANGER,
            hover_color=ACCENT_DANGER_HOVER,
            font=self._fonts["small_bold"],
            command=self._master_app.trigger_panic_stop,
        )
        self.panic_btn.pack(side="right", padx=4, pady=10)

        self.run_btn = ctk.CTkButton(
            pill,
            text="▶ Start",
            width=78,
            height=30,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            font=self._fonts["small_bold"],
            command=self._master_app.toggle_execution,
        )
        self.run_btn.pack(side="right", padx=(8, 4), pady=10)

    def update_state(
        self,
        state_text: str,
        state_color: str,
        cycles_text: str,
        is_active: bool,
        start_hk: str,
        panic_hk: str,
    ) -> None:
        """Update Mini-HUD labels and buttons only when the rendered values change."""
        if self._closed:
            return
        signature = (state_text, state_color, cycles_text, is_active, start_hk, panic_hk)
        if signature == self._last_state:
            return
        self._last_state = signature

        self.status_lbl.configure(text=state_text, text_color=state_color)
        self.cycles_lbl.configure(text=cycles_text)

        if is_active:
            self.run_btn.configure(
                text=f"■ Stop ({start_hk})",
                fg_color=ACCENT_DANGER,
                hover_color=ACCENT_DANGER_HOVER,
            )
        else:
            self.run_btn.configure(
                text=f"▶ Start ({start_hk})",
                fg_color=CTA_SUCCESS,
                hover_color=CTA_SUCCESS_HOVER,
            )
        self.panic_btn.configure(text=f"Panic ({panic_hk})")

    def close_hud(self) -> None:
        """Cancel pending icon timer and destroy the HUD window cleanly."""
        if self._closed:
            return
        self._closed = True
        aid = getattr(self, "_kf_icon_after_id", None)
        if aid is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(aid)
            self._kf_icon_after_id = None
        with contextlib.suppress(Exception):
            self.destroy()
