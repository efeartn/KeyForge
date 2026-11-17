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
from keyforge.models.keymap import (
    finalize_modifier_only_capture,
    format_captured_key_combo,
    list_common_keys,
)
from keyforge.ui.theme import (
    ACCENT_DANGER,
    ACCENT_WARNING,
    ACCENT_WARNING_HOVER,
    BG_CARD,
    BG_ELEVATED,
    BG_INPUT,
    BG_PANEL,
    BORDER_CONTROL,
    BORDER_SUBTLE,
    BTN_NEUTRAL,
    BTN_NEUTRAL_HOVER,
    CTA_SUCCESS,
    CTA_SUCCESS_HOVER,
    CTA_TEXT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    apply_window_icon,
    center_window_on_parent,
    get_fonts,
)

_ACTION_TYPE_LABELS: dict[ActionType, str] = {
    ActionType.KEY_PRESS: "Key Press (Tap)",
    ActionType.KEY_DOWN: "Key Hold Down",
    ActionType.KEY_UP: "Key Release Up",
    ActionType.MOUSE_CLICK: "Mouse Click",
    ActionType.MOUSE_MOVE: "Move Cursor",
    ActionType.MOUSE_SCROLL: "Mouse Scroll",
    ActionType.WAIT: "Wait / Delay",
    ActionType.REPEAT_BLOCK: "Repeat Block",
}
_LABEL_TO_ACTION_TYPE: dict[str, ActionType] = {v: k for k, v in _ACTION_TYPE_LABELS.items()}

_MOUSE_BUTTON_LABELS: dict[MouseButton, str] = {
    MouseButton.LEFT: "Left Button",
    MouseButton.RIGHT: "Right Button",
    MouseButton.MIDDLE: "Middle Button",
    MouseButton.X1: "Side Back (X1)",
    MouseButton.X2: "Side Forward (X2)",
}
_LABEL_TO_MOUSE_BUTTON: dict[str, MouseButton] = {v: k for k, v in _MOUSE_BUTTON_LABELS.items()}

_CLICK_TYPE_LABELS: dict[ClickType, str] = {
    ClickType.SINGLE: "Single Click",
    ClickType.DOUBLE: "Double Click",
    ClickType.DOWN: "Hold Button Down",
    ClickType.UP: "Release Button Up",
}
_LABEL_TO_CLICK_TYPE: dict[str, ClickType] = {v: k for k, v in _CLICK_TYPE_LABELS.items()}

_MOVE_MODE_LABELS: dict[MoveMode, str] = {
    MoveMode.ABSOLUTE: "Absolute (Screen XY)",
    MoveMode.RELATIVE: "Relative (Offset dX/dY)",
}
_LABEL_TO_MOVE_MODE: dict[str, MoveMode] = {v: k for k, v in _MOVE_MODE_LABELS.items()}


def _parse_action_type(value: str) -> ActionType:
    if value in _LABEL_TO_ACTION_TYPE:
        return _LABEL_TO_ACTION_TYPE[value]
    return ActionType(value)


def _parse_mouse_button(value: str) -> MouseButton:
    if value in _LABEL_TO_MOUSE_BUTTON:
        return _LABEL_TO_MOUSE_BUTTON[value]
    return MouseButton(value)


def _parse_click_type(value: str) -> ClickType:
    if value in _LABEL_TO_CLICK_TYPE:
        return _LABEL_TO_CLICK_TYPE[value]
    return ClickType(value)


def _parse_move_mode(value: str) -> MoveMode:
    if value in _LABEL_TO_MOVE_MODE:
        return _LABEL_TO_MOVE_MODE[value]
    return MoveMode(value)


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
        self.resizable(False, False)
        self.configure(fg_color=BG_PANEL)

        self._fonts = get_fonts(master)
        self._initial = initial_action
        self._on_save = on_save
        self.result_action: MacroAction | None = None
        self._initial_repeat_dsl: str | None = None
        self._initial_repeat_subs: list[MacroAction] = []
        self._capturing_key: bool = False
        self._pending_modifier_keysym: str | None = None

        center_window_on_parent(self, master, 500, 475)
        apply_window_icon(self)

        self.transient(master)
        with contextlib.suppress(Exception):
            self.grab_set()

        self.bind("<Escape>", self._on_escape_pressed)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Use Ctrl+Enter rather than global <Return> so CTkComboBox input/selection never misfires.
        self.bind("<Control-Return>", lambda _e: self._on_submit())

        self._build_ui()
        if initial_action is not None:
            self._populate_from_action(initial_action)
        else:
            self._on_type_changed(_ACTION_TYPE_LABELS[ActionType.KEY_PRESS])

    def _build_ui(self) -> None:
        header = ctk.CTkLabel(
            self,
            text="Edit Action Step" if self._initial else "New Action Step",
            font=self._fonts["title"],
            text_color=TEXT_PRIMARY,
        )
        header.pack(padx=20, pady=(14, 8), anchor="w")

        form = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        form.pack(fill="both", expand=True, padx=20, pady=(0, 6))

        # Action Type
        row_type = ctk.CTkFrame(form, fg_color="transparent")
        row_type.pack(fill="x", padx=16, pady=(12, 5))
        ctk.CTkLabel(row_type, text="Action Type:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
        self.type_var = ctk.StringVar(value=_ACTION_TYPE_LABELS[ActionType.KEY_PRESS])
        self.type_menu = ctk.CTkOptionMenu(
            row_type,
            variable=self.type_var,
            values=[_ACTION_TYPE_LABELS[t] for t in ActionType],
            command=self._on_type_changed,
            width=260,
            fg_color=BG_ELEVATED,
            button_color=BTN_NEUTRAL,
            button_hover_color=BTN_NEUTRAL_HOVER,
            font=self._fonts["body"],
        )
        self.type_menu.pack(side="left", padx=8)

        # Optional Label
        row_label = ctk.CTkFrame(form, fg_color="transparent")
        row_label.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(row_label, text="Step Comment:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
        self.label_entry = ctk.CTkEntry(
            row_label,
            placeholder_text="Optional note (e.g. Interact / Reload)",
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            width=260,
            font=self._fonts["body"],
        )
        self.label_entry.pack(side="left", padx=8)

        # Dynamic container for type-specific fields
        self.dynamic_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.dynamic_frame.pack(fill="both", expand=True, padx=16, pady=(6, 10))

        # Error banner
        self.error_label = ctk.CTkLabel(
            self,
            text="",
            text_color=ACCENT_DANGER,
            font=self._fonts["small"],
            wraplength=455,
        )
        self.error_label.pack(padx=20, pady=(2, 2))

        # Footer buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(4, 14))

        ctk.CTkLabel(
            btn_row,
            text="Ctrl+Enter to save",
            font=self._fonts["caption"],
            text_color=TEXT_MUTED,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            border_width=1,
            border_color=BORDER_CONTROL,
            text_color=TEXT_PRIMARY,
            width=104,
            height=32,
            font=self._fonts["body"],
            command=self._on_close,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_row,
            text="Save Step",
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            text_color=CTA_TEXT,
            width=132,
            height=32,
            font=self._fonts["body_bold"],
            command=self._on_submit,
        ).pack(side="right")

    def _clear_dynamic(self) -> None:
        for child in self.dynamic_frame.winfo_children():
            child.destroy()

    def _on_escape_pressed(self, _event: object = None) -> None:
        if self._capturing_key:
            self.stop_key_capture()
            return
        self._on_close()

    def start_key_capture(self) -> None:
        """Arm single-key/combo listening inside the step editor."""
        if not hasattr(self, "bind_key_btn"):
            return
        if self._capturing_key:
            self.stop_key_capture()
            return
        self._capturing_key = True
        self._pending_modifier_keysym = None
        self.bind_key_btn.configure(
            text="Press key...",
            fg_color=ACCENT_WARNING,
            hover_color=ACCENT_WARNING_HOVER,
            border_color=ACCENT_WARNING,
            text_color="#090A0D",
        )
        with contextlib.suppress(Exception):
            self.bind_key_btn.focus_set()
        self.bind("<KeyPress>", self._on_capture_keypress)
        self.bind("<KeyRelease>", self._on_capture_keyrelease)

    def stop_key_capture(self) -> None:
        self._capturing_key = False
        self._pending_modifier_keysym = None
        with contextlib.suppress(Exception):
            self.unbind("<KeyPress>")
            self.unbind("<KeyRelease>")
        if hasattr(self, "bind_key_btn") and self.bind_key_btn.winfo_exists():
            self.bind_key_btn.configure(
                text="Bind Key",
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                border_color=BORDER_CONTROL,
                text_color=TEXT_PRIMARY,
            )

    def apply_captured_key(self, combo_str: str) -> None:
        if hasattr(self, "key_combo"):
            self.key_combo.set(combo_str)
        self.stop_key_capture()

    def _on_capture_keypress(self, event: object) -> str | None:
        if not self._capturing_key:
            return None
        keysym = str(getattr(event, "keysym", "") or "")
        state = int(getattr(event, "state", 0) or 0)
        keycode = int(getattr(event, "keycode", 0) or 0)
        combo = format_captured_key_combo(keysym, state=state, keycode=keycode)
        if combo is None:
            if finalize_modifier_only_capture(keysym, keycode=keycode):
                self._pending_modifier_keysym = keysym
            return "break"
        self.apply_captured_key(combo)
        return "break"

    def _on_capture_keyrelease(self, event: object) -> str | None:
        if not self._capturing_key:
            return None
        keysym = str(getattr(event, "keysym", "") or "")
        keycode = int(getattr(event, "keycode", 0) or 0)
        if self._pending_modifier_keysym and keysym == self._pending_modifier_keysym:
            mod_name = finalize_modifier_only_capture(keysym, keycode=keycode)
            if mod_name:
                self.apply_captured_key(mod_name)
                return "break"
        return None

    def _on_type_changed(self, selected_type: str) -> None:
        self.stop_key_capture()
        self._clear_dynamic()
        self._initial_repeat_dsl = None
        self._initial_repeat_subs = []
        self.error_label.configure(text="")
        atype = _parse_action_type(selected_type)
        self.type_var.set(_ACTION_TYPE_LABELS[atype])

        if atype in (ActionType.KEY_PRESS, ActionType.KEY_DOWN, ActionType.KEY_UP):
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=5)
            ctk.CTkLabel(r1, text="Key / Combo:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
            self.key_combo = ctk.CTkComboBox(
                r1,
                values=list_common_keys(),
                width=168,
                fg_color=BG_INPUT,
                border_color=BORDER_SUBTLE,
                button_color=BTN_NEUTRAL,
                button_hover_color=BTN_NEUTRAL_HOVER,
                font=self._fonts["body"],
            )
            self.key_combo.set("E")
            self.key_combo.pack(side="left", padx=(8, 6))

            self.bind_key_btn = ctk.CTkButton(
                r1,
                text="Bind Key",
                width=86,
                height=28,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                border_width=1,
                border_color=BORDER_CONTROL,
                text_color=TEXT_PRIMARY,
                font=self._fonts["small_bold"],
                command=self.start_key_capture,
            )
            self.bind_key_btn.pack(side="left")

            if atype == ActionType.KEY_PRESS:
                r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
                r2.pack(fill="x", pady=5)
                ctk.CTkLabel(r2, text="Hold Duration (ms):", width=130, anchor="w", font=self._fonts["body"]).pack(
                    side="left"
                )
                self.hold_entry = ctk.CTkEntry(
                    r2, width=120, fg_color=BG_INPUT, border_color=BORDER_SUBTLE, font=self._fonts["body"]
                )
                self.hold_entry.insert(0, "25")
                self.hold_entry.pack(side="left", padx=8)

        elif atype == ActionType.WAIT:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=5)
            ctk.CTkLabel(r1, text="Wait Duration (ms):", width=130, anchor="w", font=self._fonts["body"]).pack(
                side="left"
            )
            self.wait_entry = ctk.CTkEntry(
                r1, width=140, fg_color=BG_INPUT, border_color=BORDER_SUBTLE, font=self._fonts["body"]
            )
            self.wait_entry.insert(0, "200")
            self.wait_entry.pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=5)
            ctk.CTkLabel(r2, text="Random Jitter (±ms):", width=130, anchor="w", font=self._fonts["body"]).pack(
                side="left"
            )
            self.jitter_entry = ctk.CTkEntry(
                r2, width=140, fg_color=BG_INPUT, border_color=BORDER_SUBTLE, font=self._fonts["body"]
            )
            self.jitter_entry.insert(0, "0")
            self.jitter_entry.pack(side="left", padx=8)
            ctk.CTkLabel(
                self.dynamic_frame,
                text="Tip: Adding 5–20ms jitter makes repeats behave like natural human timing.",
                text_color=TEXT_SECONDARY,
                font=self._fonts["small"],
            ).pack(anchor="w", pady=(4, 0))

        elif atype == ActionType.MOUSE_CLICK:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=4)
            ctk.CTkLabel(r1, text="Mouse Button:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
            self.btn_var = ctk.StringVar(value=_MOUSE_BUTTON_LABELS[MouseButton.LEFT])
            ctk.CTkOptionMenu(
                r1,
                variable=self.btn_var,
                values=[_MOUSE_BUTTON_LABELS[b] for b in MouseButton],
                width=180,
                fg_color=BG_ELEVATED,
                button_color=BTN_NEUTRAL,
                button_hover_color=BTN_NEUTRAL_HOVER,
                font=self._fonts["body"],
            ).pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=4)
            ctk.CTkLabel(r2, text="Click Mode:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
            self.click_type_var = ctk.StringVar(value=_CLICK_TYPE_LABELS[ClickType.SINGLE])
            ctk.CTkOptionMenu(
                r2,
                variable=self.click_type_var,
                values=[_CLICK_TYPE_LABELS[c] for c in ClickType],
                width=180,
                fg_color=BG_ELEVATED,
                button_color=BTN_NEUTRAL,
                button_hover_color=BTN_NEUTRAL_HOVER,
                font=self._fonts["body"],
            ).pack(side="left", padx=8)

            r_hold = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r_hold.pack(fill="x", pady=4)
            ctk.CTkLabel(r_hold, text="Hold Duration (ms):", width=130, anchor="w", font=self._fonts["body"]).pack(
                side="left"
            )
            self.hold_entry = ctk.CTkEntry(
                r_hold, width=100, fg_color=BG_INPUT, border_color=BORDER_SUBTLE, font=self._fonts["body"]
            )
            self.hold_entry.insert(0, "25")
            self.hold_entry.pack(side="left", padx=8)

            r3 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r3.pack(fill="x", pady=4)
            ctk.CTkLabel(r3, text="Coordinates (X, Y):", width=130, anchor="w", font=self._fonts["body"]).pack(
                side="left"
            )
            self.x_entry = ctk.CTkEntry(
                r3, placeholder_text="Curr X", width=76, fg_color=BG_INPUT, border_color=BORDER_SUBTLE
            )
            self.x_entry.pack(side="left", padx=(8, 4))
            self.y_entry = ctk.CTkEntry(
                r3, placeholder_text="Curr Y", width=76, fg_color=BG_INPUT, border_color=BORDER_SUBTLE
            )
            self.y_entry.pack(side="left", padx=4)
            ctk.CTkButton(
                r3,
                text="Pick XY",
                width=82,
                height=28,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                border_width=1,
                border_color=BORDER_CONTROL,
                text_color=TEXT_PRIMARY,
                font=self._fonts["small_bold"],
                command=self._capture_current_xy,
            ).pack(side="left", padx=6)
            ctk.CTkLabel(
                self.dynamic_frame,
                text="Leave X and Y blank to click at the current live cursor position.",
                text_color=TEXT_SECONDARY,
                font=self._fonts["small"],
            ).pack(anchor="w", pady=(2, 0))

        elif atype == ActionType.MOUSE_MOVE:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=5)
            ctk.CTkLabel(r1, text="Movement Mode:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
            self.move_mode_var = ctk.StringVar(value=_MOVE_MODE_LABELS[MoveMode.ABSOLUTE])
            ctk.CTkOptionMenu(
                r1,
                variable=self.move_mode_var,
                values=[_MOVE_MODE_LABELS[m] for m in MoveMode],
                width=210,
                fg_color=BG_ELEVATED,
                button_color=BTN_NEUTRAL,
                button_hover_color=BTN_NEUTRAL_HOVER,
                font=self._fonts["body"],
            ).pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=5)
            ctk.CTkLabel(r2, text="Target X / Y:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
            self.x_entry = ctk.CTkEntry(r2, width=76, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
            self.x_entry.insert(0, "500")
            self.x_entry.pack(side="left", padx=(8, 4))
            self.y_entry = ctk.CTkEntry(r2, width=76, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
            self.y_entry.insert(0, "500")
            self.y_entry.pack(side="left", padx=4)
            ctk.CTkButton(
                r2,
                text="Pick XY",
                width=82,
                height=28,
                fg_color=BTN_NEUTRAL,
                hover_color=BTN_NEUTRAL_HOVER,
                border_width=1,
                border_color=BORDER_CONTROL,
                text_color=TEXT_PRIMARY,
                font=self._fonts["small_bold"],
                command=self._capture_current_xy,
            ).pack(side="left", padx=6)

        elif atype == ActionType.MOUSE_SCROLL:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=5)
            ctk.CTkLabel(r1, text="Vertical Ticks (+Up/-Dn):", width=155, anchor="w", font=self._fonts["body"]).pack(
                side="left"
            )
            self.scroll_dy_entry = ctk.CTkEntry(r1, width=100, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
            self.scroll_dy_entry.insert(0, "-3")
            self.scroll_dy_entry.pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=5)
            ctk.CTkLabel(r2, text="Horizontal Ticks:", width=155, anchor="w", font=self._fonts["body"]).pack(
                side="left"
            )
            self.scroll_dx_entry = ctk.CTkEntry(r2, width=100, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
            self.scroll_dx_entry.insert(0, "0")
            self.scroll_dx_entry.pack(side="left", padx=8)

        elif atype == ActionType.REPEAT_BLOCK:
            r1 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r1.pack(fill="x", pady=5)
            ctk.CTkLabel(r1, text="Repeat Count:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
            self.repeat_count_entry = ctk.CTkEntry(r1, width=120, fg_color=BG_INPUT, border_color=BORDER_SUBTLE)
            self.repeat_count_entry.insert(0, "3")
            self.repeat_count_entry.pack(side="left", padx=8)

            r2 = ctk.CTkFrame(self.dynamic_frame, fg_color="transparent")
            r2.pack(fill="x", pady=5)
            ctk.CTkLabel(r2, text="Inner Steps DSL:", width=130, anchor="w", font=self._fonts["body"]).pack(side="left")
            self.repeat_dsl_entry = ctk.CTkEntry(
                r2, width=260, fg_color=BG_INPUT, border_color=BORDER_SUBTLE, font=self._fonts["mono_small"]
            )
            self.repeat_dsl_entry.insert(0, "Press F -> Wait 120ms")
            self.repeat_dsl_entry.pack(side="left", padx=8)

            self.repeat_note_lbl = ctk.CTkLabel(
                self.dynamic_frame,
                text="",
                text_color=ACCENT_WARNING,
                font=self._fonts["small"],
                wraplength=415,
                justify="left",
            )
            self.repeat_note_lbl.pack(anchor="w", pady=(4, 0))

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
        self.type_var.set(_ACTION_TYPE_LABELS[act.action_type])
        self._on_type_changed(_ACTION_TYPE_LABELS[act.action_type])
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
            self.btn_var.set(_MOUSE_BUTTON_LABELS[act.button])
            self.click_type_var.set(_CLICK_TYPE_LABELS[act.click_type])
            self.hold_entry.delete(0, "end")
            self.hold_entry.insert(0, str(act.hold_ms))
            self.x_entry.delete(0, "end")
            self.y_entry.delete(0, "end")
            if act.x is not None and act.y is not None:
                self.x_entry.insert(0, str(act.x))
                self.y_entry.insert(0, str(act.y))
        elif atype == ActionType.MOUSE_MOVE:
            self.move_mode_var.set(_MOVE_MODE_LABELS[act.move_mode])
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
            formatted_dsl = format_actions_to_dsl(act.sub_actions)
            self._initial_repeat_dsl = formatted_dsl
            self._initial_repeat_subs = [MacroAction.from_dict(s.to_dict()) for s in act.sub_actions]
            self.repeat_dsl_entry.delete(0, "end")
            self.repeat_dsl_entry.insert(0, formatted_dsl)
            disabled_cnt = sum(1 for s in act.sub_actions if not s.enabled)
            if disabled_cnt > 0 and hasattr(self, "repeat_note_lbl"):
                self.repeat_note_lbl.configure(
                    text=(
                        f"Note: {disabled_cnt} disabled inner step(s) are hidden from DSL "
                        "and will be preserved unless you edit the Inner Steps text."
                    )
                )

    def _on_submit(self) -> None:
        self.stop_key_capture()
        try:
            atype = _parse_action_type(self.type_var.get())
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
                kwargs["button"] = _parse_mouse_button(self.btn_var.get())
                kwargs["click_type"] = _parse_click_type(self.click_type_var.get())
                kwargs["hold_ms"] = int(self.hold_entry.get().strip() or "25")
                x_raw = self.x_entry.get().strip()
                y_raw = self.y_entry.get().strip()
                kwargs["x"] = int(x_raw) if x_raw else None
                kwargs["y"] = int(y_raw) if y_raw else None
            elif atype == ActionType.MOUSE_MOVE:
                kwargs["move_mode"] = _parse_move_mode(self.move_mode_var.get())
                kwargs["x"] = int(self.x_entry.get().strip())
                kwargs["y"] = int(self.y_entry.get().strip())
            elif atype == ActionType.MOUSE_SCROLL:
                kwargs["scroll_dy"] = int(self.scroll_dy_entry.get().strip() or "0")
                kwargs["scroll_dx"] = int(self.scroll_dx_entry.get().strip() or "0")
            elif atype == ActionType.REPEAT_BLOCK:
                kwargs["repeat_count"] = int(self.repeat_count_entry.get().strip())
                dsl_text = self.repeat_dsl_entry.get().strip()
                if (
                    self._initial is not None
                    and self._initial.action_type == ActionType.REPEAT_BLOCK
                    and self._initial_repeat_dsl is not None
                    and dsl_text == self._initial_repeat_dsl
                ):
                    kwargs["sub_actions"] = [MacroAction.from_dict(s.to_dict()) for s in self._initial_repeat_subs]
                else:
                    kwargs["sub_actions"] = parse_quick_expression(dsl_text)

            new_action = MacroAction(**kwargs)
            self.result_action = new_action
            if self._on_save is not None:
                self._on_save(new_action)
            self._on_close()
        except Exception as exc:
            self.error_label.configure(text=f"Validation Error: {exc}")

    def _on_close(self) -> None:
        self.stop_key_capture()
        aid = getattr(self, "_kf_icon_after_id", None)
        if aid is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(aid)
            self._kf_icon_after_id = None
        with contextlib.suppress(Exception):
            self.grab_release()
        with contextlib.suppress(Exception):
            self.destroy()
