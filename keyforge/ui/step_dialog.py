"""Modal dialog for creating or editing a single MacroAction step (Cross-Platform)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable

import customtkinter as ctk

from keyforge.engine.win32_input import IS_WINDOWS, get_system_cursor_pos
from keyforge.models.action import (
    ActionType,
    ClickType,
    MacroAction,
    MouseButton,
    MoveMode,
)
from keyforge.models.dsl import format_actions_to_dsl, parse_quick_expression
from keyforge.models.keymap import list_common_keys
from keyforge.ui.theme import (
    ACCENT_DANGER,
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    ACCENT_SUCCESS,
    BG_CARD,
    BG_INPUT,
    BG_PANEL,
    BORDER_SUBTLE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class StepEditorDialog(ctk.CTkToplevel):
    """Modal editor dialog for configuring any MacroAction."""

    def __init__(
        self,
        master: ctk.CTk,
        initial_action: MacroAction | None = None,
        on_save: Callable[[MacroAction], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Edit Macro Step" if initial_action else "Add Macro Step")
        self.geometry("520x540")
        self.resizable(False, False)
        self.configure(fg_color=BG_PANEL)

        self._initial = initial_action
        self._on_save = on_save
        self.result_action: MacroAction | None = None

        self.transient(master)
        with contextlib.suppress(Exception):
            self.grab_set()

        self._build_ui()
        if initial_action is not None:
            self._populate_from_action(initial_action)
        else:
            self._on_type_changed(ActionType.KEY_PRESS.value)

    def _build_ui(self) -> None:
        header = ctk.CTkLabel(
            self,
            text="⚡ Configure Macro Action Step",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        header.pack(padx=20, pady=(16, 10), anchor="w")

        form = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        form.pack(fill="both", expand=True, padx=20, pady=6)

        # Action Type
        row_type = ctk.CTkFrame(form, fg_color="transparent")
        row_type.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkLabel(row_type, text="Action Type:", width=130, anchor="w").pack(side="left")
        self.type_var = ctk.StringVar(value=ActionType.KEY_PRESS.value)
        self.type_menu = ctk.CTkOptionMenu(
            row_type,
            variable=self.type_var,
            values=[t.value for t in ActionType],
            command=self._on_type_changed,
            width=260,
        )
        self.type_menu.pack(side="left", padx=8)

        # Optional Label
        row_label = ctk.CTkFrame(form, fg_color="transparent")
        row_label.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(row_label, text="Step Comment:", width=130, anchor="w").pack(side="left")
        self.label_entry = ctk.CTkEntry(
            row_label,
            placeholder_text="Optional note (e.g. Interact / Reload)",
            fg_color=BG_INPUT,
            width=260,
        )
        self.label_entry.pack(side="left", padx=8)

        # Dynamic container for type-specific fields
        self.dynamic_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.dynamic_frame.pack(fill="both", expand=True, padx=16, pady=8)

        # Error banner
        self.error_label = ctk.CTkLabel(
            self,
            text="",
            text_color=ACCENT_DANGER,
            font=ctk.CTkFont(size=12),
            wraplength=460,
        )
        self.error_label.pack(padx=20, pady=(2, 2))

        # Footer buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(6, 16))

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            fg_color="#334155",
            hover_color="#475569",
            width=110,
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_row,
            text="Save Step",
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_PRIMARY_HOVER,
            width=140,
            font=ctk.CTkFont(weight="bold"),
            command=self._on_submit,
        ).pack(side="right")

    def _clear_dynamic(self) -> None:
        for child in self.dynamic_frame.winfo_children():
            child.destroy()

    def _on_type_changed(self, selected_type: str) -> None:
        self._clear_dynamic()
        self.error_label.configure(text="")
        atype = ActionType(selected_type)

        if atype in (ActionType.KEY_PRESS, ActionType.KEY_DOWN, ActionType.KEY_UP):
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=6)
            ctk.CTkLabel(r1, text="Key / Combo:", width=130, anchor="w").pack(side="left")
            self.key_combo = ctk.CTkComboBox(
                r1,
                values=list_common_keys(),
                width=190,
                fg_color=BG_INPUT,
            )
            self.key_combo.set("E")
            self.key_combo.pack(side="left", padx=8)

            if atype == ActionType.KEY_PRESS:
                r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
                r2.pack(fill="x", pady=6)
                ctk.CTkLabel(r2, text="Hold Duration (ms):", width=130, anchor="w").pack(side="left")
                self.hold_entry = ctk.CTkEntry(r2, width=120, fg_color=BG_INPUT)
                self.hold_entry.insert(0, "25")
                self.hold_entry.pack(side="left", padx=8)

        elif atype == ActionType.WAIT:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=6)
            ctk.CTkLabel(r1, text="Wait Duration (ms):", width=130, anchor="w").pack(side="left")
            self.wait_entry = ctk.CTkEntry(r1, width=140, fg_color=BG_INPUT)
            self.wait_entry.insert(0, "200")
            self.wait_entry.pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=6)
            ctk.CTkLabel(r2, text="Random Jitter (±ms):", width=130, anchor="w").pack(side="left")
            self.jitter_entry = ctk.CTkEntry(r2, width=140, fg_color=BG_INPUT)
            self.jitter_entry.insert(0, "0")
            self.jitter_entry.pack(side="left", padx=8)
            ctk.CTkLabel(
                self.dynamic_frame,
                text="Tip: Adding 5–20ms jitter makes repeats behave like natural human timing.",
                text_color=TEXT_SECONDARY,
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", pady=(4, 0))

        elif atype == ActionType.MOUSE_CLICK:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=6)
            ctk.CTkLabel(r1, text="Mouse Button:", width=130, anchor="w").pack(side="left")
            self.btn_var = ctk.StringVar(value=MouseButton.LEFT.value)
            ctk.CTkOptionMenu(
                r1,
                variable=self.btn_var,
                values=[b.value for b in MouseButton],
                width=160,
            ).pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=6)
            ctk.CTkLabel(r2, text="Click Mode:", width=130, anchor="w").pack(side="left")
            self.click_type_var = ctk.StringVar(value=ClickType.SINGLE.value)
            ctk.CTkOptionMenu(
                r2,
                variable=self.click_type_var,
                values=[c.value for c in ClickType],
                width=160,
            ).pack(side="left", padx=8)

            r3 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r3.pack(fill="x", pady=6)
            ctk.CTkLabel(r3, text="Coordinates (X, Y):", width=130, anchor="w").pack(side="left")
            self.x_entry = ctk.CTkEntry(r3, placeholder_text="Curr X", width=80, fg_color=BG_INPUT)
            self.x_entry.pack(side="left", padx=(8, 4))
            self.y_entry = ctk.CTkEntry(r3, placeholder_text="Curr Y", width=80, fg_color=BG_INPUT)
            self.y_entry.pack(side="left", padx=4)
            ctk.CTkButton(
                r3,
                text="Pick Current XY",
                width=105,
                fg_color=ACCENT_SUCCESS,
                command=self._capture_current_xy,
            ).pack(side="left", padx=6)
            ctk.CTkLabel(
                self.dynamic_frame,
                text="Leave X and Y blank to click at the current live cursor position.",
                text_color=TEXT_SECONDARY,
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", pady=(2, 0))

        elif atype == ActionType.MOUSE_MOVE:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=6)
            ctk.CTkLabel(r1, text="Movement Mode:", width=130, anchor="w").pack(side="left")
            self.move_mode_var = ctk.StringVar(value=MoveMode.ABSOLUTE.value)
            ctk.CTkOptionMenu(
                r1,
                variable=self.move_mode_var,
                values=[m.value for m in MoveMode],
                width=160,
            ).pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=6)
            ctk.CTkLabel(r2, text="Target X / Y:", width=130, anchor="w").pack(side="left")
            self.x_entry = ctk.CTkEntry(r2, width=80, fg_color=BG_INPUT)
            self.x_entry.insert(0, "500")
            self.x_entry.pack(side="left", padx=(8, 4))
            self.y_entry = ctk.CTkEntry(r2, width=80, fg_color=BG_INPUT)
            self.y_entry.insert(0, "500")
            self.y_entry.pack(side="left", padx=4)
            ctk.CTkButton(
                r2,
                text="Pick Current XY",
                width=105,
                fg_color=ACCENT_SUCCESS,
                command=self._capture_current_xy,
            ).pack(side="left", padx=6)

        elif atype == ActionType.MOUSE_SCROLL:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=6)
            ctk.CTkLabel(r1, text="Vertical Ticks (+Up/-Dn):", width=155, anchor="w").pack(side="left")
            self.scroll_dy_entry = ctk.CTkEntry(r1, width=100, fg_color=BG_INPUT)
            self.scroll_dy_entry.insert(0, "-3")
            self.scroll_dy_entry.pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=6)
            ctk.CTkLabel(r2, text="Horizontal Ticks:", width=155, anchor="w").pack(side="left")
            self.scroll_dx_entry = ctk.CTkEntry(r2, width=100, fg_color=BG_INPUT)
            self.scroll_dx_entry.insert(0, "0")
            self.scroll_dx_entry.pack(side="left", padx=8)

        elif atype == ActionType.REPEAT_BLOCK:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=6)
            ctk.CTkLabel(r1, text="Repeat Count:", width=130, anchor="w").pack(side="left")
            self.repeat_count_entry = ctk.CTkEntry(r1, width=120, fg_color=BG_INPUT)
            self.repeat_count_entry.insert(0, "3")
            self.repeat_count_entry.pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=6)
            ctk.CTkLabel(r2, text="Inner Steps DSL:", width=130, anchor="w").pack(side="left")
            self.repeat_dsl_entry = ctk.CTkEntry(r2, width=260, fg_color=BG_INPUT)
            self.repeat_dsl_entry.insert(0, "Press F -> Wait 120ms")
            self.repeat_dsl_entry.pack(side="left", padx=8)

    def _capture_current_xy(self) -> None:
        if IS_WINDOWS:
            cx, cy = get_system_cursor_pos()
        else:
            cx, cy = self.winfo_pointerx(), self.winfo_pointery()
        if hasattr(self, "x_entry") and hasattr(self, "y_entry"):
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(cx))
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(cy))

    def _populate_from_action(self, act: MacroAction) -> None:
        self.type_var.set(act.action_type.value)
        self._on_type_changed(act.action_type.value)
        self.label_entry.delete(0, "end")
        self.label_entry.insert(0, act.label)

        atype = act.action_type
        if atype in (ActionType.KEY_PRESS, ActionType.KEY_DOWN, ActionType.KEY_UP):
            self.key_combo.set(act.key)
            if atype == ActionType.KEY_PRESS:
                self.hold_entry.delete(0, "end")
                self.hold_entry.insert(0, str(act.hold_ms))
        elif atype == ActionType.WAIT:
            self.wait_entry.delete(0, "end")
            self.wait_entry.insert(0, str(act.wait_ms))
            self.jitter_entry.delete(0, "end")
            self.jitter_entry.insert(0, str(act.jitter_ms))
        elif atype == ActionType.MOUSE_CLICK:
            self.btn_var.set(act.button.value)
            self.click_type_var.set(act.click_type.value)
            self.x_entry.delete(0, "end")
            self.y_entry.delete(0, "end")
            if act.x is not None and act.y is not None:
                self.x_entry.insert(0, str(act.x))
                self.y_entry.insert(0, str(act.y))
        elif atype == ActionType.MOUSE_MOVE:
            self.move_mode_var.set(act.move_mode.value)
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(act.x or 0))
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(act.y or 0))
        elif atype == ActionType.MOUSE_SCROLL:
            self.scroll_dy_entry.delete(0, "end")
            self.scroll_dy_entry.insert(0, str(act.scroll_dy))
            self.scroll_dx_entry.delete(0, "end")
            self.scroll_dx_entry.insert(0, str(act.scroll_dx))
        elif atype == ActionType.REPEAT_BLOCK:
            self.repeat_count_entry.delete(0, "end")
            self.repeat_count_entry.insert(0, str(act.repeat_count))
            self.repeat_dsl_entry.delete(0, "end")
            self.repeat_dsl_entry.insert(0, format_actions_to_dsl(act.sub_actions))

    def _on_submit(self) -> None:
        try:
            atype = ActionType(self.type_var.get())
            label = self.label_entry.get().strip()
            action_id = self._initial.id if self._initial else None
            enabled = self._initial.enabled if self._initial else True

            kwargs: dict = {
                "action_type": atype,
                "label": label,
                "enabled": enabled,
            }
            if action_id:
                kwargs["id"] = action_id

            if atype in (ActionType.KEY_PRESS, ActionType.KEY_DOWN, ActionType.KEY_UP):
                kwargs["key"] = self.key_combo.get().strip()
                if atype == ActionType.KEY_PRESS:
                    kwargs["hold_ms"] = int(self.hold_entry.get().strip())
            elif atype == ActionType.WAIT:
                kwargs["wait_ms"] = int(self.wait_entry.get().strip())
                kwargs["jitter_ms"] = int(self.jitter_entry.get().strip() or "0")
            elif atype == ActionType.MOUSE_CLICK:
                kwargs["button"] = MouseButton(self.btn_var.get())
                kwargs["click_type"] = ClickType(self.click_type_var.get())
                if self._initial is not None and self._initial.action_type == ActionType.MOUSE_CLICK:
                    kwargs["hold_ms"] = self._initial.hold_ms
                x_raw = self.x_entry.get().strip()
                y_raw = self.y_entry.get().strip()
                kwargs["x"] = int(x_raw) if x_raw else None
                kwargs["y"] = int(y_raw) if y_raw else None
            elif atype == ActionType.MOUSE_MOVE:
                kwargs["move_mode"] = MoveMode(self.move_mode_var.get())
                kwargs["x"] = int(self.x_entry.get().strip())
                kwargs["y"] = int(self.y_entry.get().strip())
            elif atype == ActionType.MOUSE_SCROLL:
                kwargs["scroll_dy"] = int(self.scroll_dy_entry.get().strip() or "0")
                kwargs["scroll_dx"] = int(self.scroll_dx_entry.get().strip() or "0")
            elif atype == ActionType.REPEAT_BLOCK:
                kwargs["repeat_count"] = int(self.repeat_count_entry.get().strip())
                dsl_text = self.repeat_dsl_entry.get().strip()
                kwargs["sub_actions"] = parse_quick_expression(dsl_text)

            new_action = MacroAction(**kwargs)
            self.result_action = new_action
            if self._on_save is not None:
                self._on_save(new_action)
            self.destroy()
        except Exception as exc:
            self.error_label.configure(text=f"Validation Error: {exc}")
