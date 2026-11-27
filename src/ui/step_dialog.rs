//! Modal dialog for creating or editing a single MacroAction step.

use egui::{Color32, Context, RichText, Rounding, Stroke};

use crate::engine::platform::{get_platform_capabilities, platform_get_cursor_pos};
use crate::models::action::{
    new_action_id, ActionType, ClickType, MacroAction, MouseButton, MoveMode,
};
use crate::models::dsl::{format_actions_to_dsl, parse_quick_expression};
use crate::models::keymap::{format_egui_key_combo, list_common_keys};
use crate::ui::theme::{
    styled_button, ACCENT_DANGER, ACCENT_WARNING, BG_CARD, BG_ELEVATED, BG_INPUT, BORDER_CONTROL,
    BORDER_SUBTLE, BTN_NEUTRAL, CTA_SUCCESS, CTA_TEXT, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY,
};

fn action_type_label(t: ActionType) -> &'static str {
    match t {
        ActionType::KeyPress => "Key Press (Tap)",
        ActionType::KeyDown => "Key Hold Down",
        ActionType::KeyUp => "Key Release Up",
        ActionType::MouseClick => "Mouse Click",
        ActionType::MouseMove => "Move Cursor",
        ActionType::MouseScroll => "Mouse Scroll",
        ActionType::Wait => "Wait / Delay",
        ActionType::RepeatBlock => "Repeat Block",
    }
}

fn mouse_button_label(b: MouseButton) -> &'static str {
    match b {
        MouseButton::Left => "Left Button",
        MouseButton::Right => "Right Button",
        MouseButton::Middle => "Middle Button",
        MouseButton::X1 => "Side Back (X1)",
        MouseButton::X2 => "Side Forward (X2)",
    }
}

fn click_type_label(c: ClickType) -> &'static str {
    match c {
        ClickType::Single => "Single Click",
        ClickType::Double => "Double Click",
        ClickType::Down => "Hold Button Down",
        ClickType::Up => "Release Button Up",
    }
}

fn move_mode_label(m: MoveMode) -> &'static str {
    match m {
        MoveMode::Absolute => "Absolute (Screen XY)",
        MoveMode::Relative => "Relative (Offset dX/dY)",
    }
}

pub enum StepDialogOutcome {
    Open,
    Cancelled,
    Saved {
        edit_id: Option<String>,
        action: Box<MacroAction>,
    },
}

pub struct StepEditorState {
    pub edit_id: Option<String>,
    pub enabled: bool,
    pub action_type: ActionType,
    pub label: String,
    pub key: String,
    pub hold_ms_str: String,
    pub wait_ms_str: String,
    pub jitter_ms_str: String,
    pub button: MouseButton,
    pub click_type: ClickType,
    pub x_str: String,
    pub y_str: String,
    pub move_mode: MoveMode,
    pub scroll_dx_str: String,
    pub scroll_dy_str: String,
    pub repeat_count_str: String,
    pub repeat_dsl: String,
    pub initial_repeat_dsl: Option<String>,
    pub initial_repeat_subs: Vec<MacroAction>,
    pub capturing_key: bool,
    pub error: String,
}

impl StepEditorState {
    pub fn for_new() -> Self {
        Self {
            edit_id: None,
            enabled: true,
            action_type: ActionType::KeyPress,
            label: String::new(),
            key: "E".to_string(),
            hold_ms_str: "25".to_string(),
            wait_ms_str: "200".to_string(),
            jitter_ms_str: "0".to_string(),
            button: MouseButton::Left,
            click_type: ClickType::Single,
            x_str: String::new(),
            y_str: String::new(),
            move_mode: MoveMode::Absolute,
            scroll_dx_str: "0".to_string(),
            scroll_dy_str: "-3".to_string(),
            repeat_count_str: "3".to_string(),
            repeat_dsl: "Press F -> Wait 120ms".to_string(),
            initial_repeat_dsl: None,
            initial_repeat_subs: Vec::new(),
            capturing_key: false,
            error: String::new(),
        }
    }

    pub fn from_action(act: &MacroAction) -> Self {
        let mut s = Self::for_new();
        s.edit_id = Some(act.id.clone());
        s.enabled = act.enabled;
        s.action_type = act.action_type;
        s.label = act.label.clone();
        s.key = act.key.clone();
        s.hold_ms_str = act.hold_ms.to_string();
        s.wait_ms_str = act.wait_ms.to_string();
        s.jitter_ms_str = act.jitter_ms.to_string();
        s.button = act.button;
        s.click_type = act.click_type;
        if let (Some(cx), Some(cy)) = (act.x, act.y) {
            s.x_str = cx.to_string();
            s.y_str = cy.to_string();
        } else if act.action_type == ActionType::MouseMove {
            s.x_str = "500".to_string();
            s.y_str = "500".to_string();
        }
        s.move_mode = act.move_mode;
        s.scroll_dx_str = act.scroll_dx.to_string();
        s.scroll_dy_str = act.scroll_dy.to_string();
        s.repeat_count_str = act.repeat_count.to_string();
        if act.action_type == ActionType::RepeatBlock {
            let dsl = format_actions_to_dsl(&act.sub_actions);
            s.repeat_dsl = dsl.clone();
            s.initial_repeat_dsl = Some(dsl);
            s.initial_repeat_subs = act.sub_actions.clone();
        }
        s
    }

    fn build_validated(&self) -> Result<MacroAction, String> {
        let mut act = MacroAction::new(self.action_type);
        act.id = self.edit_id.clone().unwrap_or_else(new_action_id);
        act.enabled = self.enabled;
        act.label = self.label.trim().to_string();

        match self.action_type {
            ActionType::KeyPress => {
                act.key = self.key.trim().to_string();
                act.hold_ms = self
                    .hold_ms_str
                    .trim()
                    .parse()
                    .map_err(|_| "Hold duration must be an integer (ms).".to_string())?;
            }
            ActionType::KeyDown | ActionType::KeyUp => {
                act.key = self.key.trim().to_string();
            }
            ActionType::Wait => {
                act.wait_ms = self
                    .wait_ms_str
                    .trim()
                    .parse()
                    .map_err(|_| "Wait duration must be an integer (ms).".to_string())?;
                let j = self.jitter_ms_str.trim();
                act.jitter_ms = if j.is_empty() {
                    0
                } else {
                    j.parse()
                        .map_err(|_| "Random jitter must be an integer (ms).".to_string())?
                };
            }
            ActionType::MouseClick => {
                act.button = self.button;
                act.click_type = self.click_type;
                let h = self.hold_ms_str.trim();
                act.hold_ms = if h.is_empty() {
                    25
                } else {
                    h.parse()
                        .map_err(|_| "Hold duration must be an integer (ms).".to_string())?
                };
                let xr = self.x_str.trim();
                let yr = self.y_str.trim();
                act.x = if xr.is_empty() {
                    None
                } else {
                    Some(
                        xr.parse()
                            .map_err(|_| "X coordinate must be an integer.".to_string())?,
                    )
                };
                act.y = if yr.is_empty() {
                    None
                } else {
                    Some(
                        yr.parse()
                            .map_err(|_| "Y coordinate must be an integer.".to_string())?,
                    )
                };
            }
            ActionType::MouseMove => {
                act.move_mode = self.move_mode;
                act.x = Some(
                    self.x_str
                        .trim()
                        .parse()
                        .map_err(|_| "X coordinate must be an integer.".to_string())?,
                );
                act.y = Some(
                    self.y_str
                        .trim()
                        .parse()
                        .map_err(|_| "Y coordinate must be an integer.".to_string())?,
                );
            }
            ActionType::MouseScroll => {
                let dy = self.scroll_dy_str.trim();
                let dx = self.scroll_dx_str.trim();
                act.scroll_dy = if dy.is_empty() {
                    0
                } else {
                    dy.parse()
                        .map_err(|_| "Vertical ticks must be an integer.".to_string())?
                };
                act.scroll_dx = if dx.is_empty() {
                    0
                } else {
                    dx.parse()
                        .map_err(|_| "Horizontal ticks must be an integer.".to_string())?
                };
            }
            ActionType::RepeatBlock => {
                act.repeat_count = self
                    .repeat_count_str
                    .trim()
                    .parse()
                    .map_err(|_| "Repeat count must be an integer.".to_string())?;
                let dsl_text = self.repeat_dsl.trim();
                if self.edit_id.is_some()
                    && self
                        .initial_repeat_dsl
                        .as_deref()
                        .is_some_and(|orig| orig == dsl_text)
                {
                    act.sub_actions = self.initial_repeat_subs.clone();
                } else {
                    act.sub_actions = parse_quick_expression(dsl_text)?;
                }
            }
        }

        act.validate()?;
        Ok(act)
    }

    pub fn show(&mut self, ctx: &Context) -> StepDialogOutcome {
        let mut outcome = StepDialogOutcome::Open;

        if self.capturing_key {
            let mut captured: Option<String> = None;
            let mut cancel = false;
            ctx.input(|i| {
                for ev in &i.events {
                    if let egui::Event::Key {
                        key,
                        pressed: true,
                        modifiers,
                        ..
                    } = ev
                    {
                        if *key == egui::Key::Escape && !modifiers.any() {
                            cancel = true;
                        } else if let Some(combo) = format_egui_key_combo(*key, *modifiers) {
                            captured = Some(combo);
                        }
                    }
                }
            });
            if cancel {
                self.capturing_key = false;
            } else if let Some(c) = captured {
                self.key = c;
                self.capturing_key = false;
            }
        }

        if ctx.input(|i| i.modifiers.command && i.key_pressed(egui::Key::Enter)) {
            match self.build_validated() {
                Ok(action) => {
                    return StepDialogOutcome::Saved {
                        edit_id: self.edit_id.clone(),
                        action: Box::new(action),
                    };
                }
                Err(e) => self.error = e,
            }
        }

        let title = if self.edit_id.is_some() {
            "Edit Macro Step"
        } else {
            "Add Macro Step"
        };

        let mut close_clicked = false;
        egui::Window::new(title)
            .collapsible(false)
            .resizable(false)
            .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
            .fixed_size([480.0, 400.0])
            .frame(
                egui::Frame::window(&ctx.style())
                    .fill(crate::ui::theme::BG_PANEL)
                    .stroke(Stroke::new(1.0_f32, BORDER_CONTROL))
                    .rounding(Rounding::same(12.0))
                    .inner_margin(egui::Margin::same(16.0)),
            )
            .show(ctx, |ui| {
                ui.label(
                    RichText::new(if self.edit_id.is_some() {
                        "Edit Action Step"
                    } else {
                        "New Action Step"
                    })
                    .size(16.0)
                    .strong()
                    .color(TEXT_PRIMARY),
                );
                ui.add_space(8.0);

                egui::Frame::none()
                    .fill(BG_CARD)
                    .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                    .rounding(Rounding::same(10.0))
                    .inner_margin(egui::Margin::same(12.0))
                    .show(ui, |ui| {
                        egui::Grid::new("step_dialog_grid")
                            .num_columns(2)
                            .spacing([12.0, 8.0])
                            .show(ui, |ui| {
                                ui.label(RichText::new("Action Type:").color(TEXT_SECONDARY));
                                egui::ComboBox::from_id_salt("step_type_combo")
                                    .width(240.0)
                                    .selected_text(action_type_label(self.action_type))
                                    .show_ui(ui, |ui| {
                                        for t in [
                                            ActionType::KeyPress,
                                            ActionType::KeyDown,
                                            ActionType::KeyUp,
                                            ActionType::MouseClick,
                                            ActionType::MouseMove,
                                            ActionType::MouseScroll,
                                            ActionType::Wait,
                                            ActionType::RepeatBlock,
                                        ] {
                                            if ui
                                                .selectable_value(
                                                    &mut self.action_type,
                                                    t,
                                                    action_type_label(t),
                                                )
                                                .clicked()
                                            {
                                                self.capturing_key = false;
                                                self.error.clear();
                                                if t == ActionType::MouseMove
                                                    && self.x_str.is_empty()
                                                {
                                                    self.x_str = "500".to_string();
                                                    self.y_str = "500".to_string();
                                                }
                                            }
                                        }
                                    });
                                ui.end_row();

                                ui.label(RichText::new("Step Comment:").color(TEXT_SECONDARY));
                                ui.add(
                                    egui::TextEdit::singleline(&mut self.label)
                                        .desired_width(240.0)
                                        .hint_text("Optional note (e.g. Interact / Reload)"),
                                );
                                ui.end_row();

                                match self.action_type {
                                    ActionType::KeyPress | ActionType::KeyDown | ActionType::KeyUp => {
                                        ui.label(RichText::new("Key / Combo:").color(TEXT_SECONDARY));
                                        ui.horizontal(|ui| {
                                            ui.add(
                                                egui::TextEdit::singleline(&mut self.key)
                                                    .desired_width(110.0),
                                            );
                                            egui::ComboBox::from_id_salt("step_key_preset")
                                                .width(68.0)
                                                .selected_text("Pick")
                                                .show_ui(ui, |ui| {
                                                    for k in list_common_keys() {
                                                        if ui.selectable_label(self.key == k, k).clicked() {
                                                            self.key = k.to_string();
                                                        }
                                                    }
                                                });
                                            let cap_lbl = if self.capturing_key {
                                                "Press..."
                                            } else {
                                                "Bind Key"
                                            };
                                            let cap_bg = if self.capturing_key {
                                                ACCENT_WARNING
                                            } else {
                                                BTN_NEUTRAL
                                            };
                                            let cap_fg = if self.capturing_key {
                                                Color32::from_rgb(0x09, 0x0A, 0x0D)
                                            } else {
                                                TEXT_PRIMARY
                                            };
                                            if styled_button(ui, cap_lbl, cap_bg, cap_fg, BORDER_CONTROL)
                                                .clicked()
                                            {
                                                self.capturing_key = !self.capturing_key;
                                            }
                                        });
                                        ui.end_row();

                                        if self.action_type == ActionType::KeyPress {
                                            ui.label(
                                                RichText::new("Hold Duration (ms):")
                                                    .color(TEXT_SECONDARY),
                                            );
                                            ui.add(
                                                egui::TextEdit::singleline(&mut self.hold_ms_str)
                                                    .desired_width(110.0),
                                            );
                                            ui.end_row();
                                        }
                                    }
                                    ActionType::Wait => {
                                        ui.label(
                                            RichText::new("Wait Duration (ms):").color(TEXT_SECONDARY),
                                        );
                                        ui.add(
                                            egui::TextEdit::singleline(&mut self.wait_ms_str)
                                                .desired_width(130.0),
                                        );
                                        ui.end_row();

                                        ui.label(
                                            RichText::new("Random Jitter (\u{00B1}ms):")
                                                .color(TEXT_SECONDARY),
                                        );
                                        ui.add(
                                            egui::TextEdit::singleline(&mut self.jitter_ms_str)
                                                .desired_width(130.0),
                                        );
                                        ui.end_row();
                                    }
                                    ActionType::MouseClick => {
                                        ui.label(RichText::new("Mouse Button:").color(TEXT_SECONDARY));
                                        egui::ComboBox::from_id_salt("step_mouse_btn")
                                            .width(190.0)
                                            .selected_text(mouse_button_label(self.button))
                                            .show_ui(ui, |ui| {
                                                for b in [
                                                    MouseButton::Left,
                                                    MouseButton::Right,
                                                    MouseButton::Middle,
                                                    MouseButton::X1,
                                                    MouseButton::X2,
                                                ] {
                                                    ui.selectable_value(
                                                        &mut self.button,
                                                        b,
                                                        mouse_button_label(b),
                                                    );
                                                }
                                            });
                                        ui.end_row();

                                        ui.label(RichText::new("Click Mode:").color(TEXT_SECONDARY));
                                        egui::ComboBox::from_id_salt("step_click_mode")
                                            .width(190.0)
                                            .selected_text(click_type_label(self.click_type))
                                            .show_ui(ui, |ui| {
                                                for c in [
                                                    ClickType::Single,
                                                    ClickType::Double,
                                                    ClickType::Down,
                                                    ClickType::Up,
                                                ] {
                                                    ui.selectable_value(
                                                        &mut self.click_type,
                                                        c,
                                                        click_type_label(c),
                                                    );
                                                }
                                            });
                                        ui.end_row();

                                        ui.label(
                                            RichText::new("Hold Duration (ms):")
                                                .color(TEXT_SECONDARY),
                                        );
                                        ui.add(
                                            egui::TextEdit::singleline(&mut self.hold_ms_str)
                                                .desired_width(100.0),
                                        );
                                        ui.end_row();

                                        ui.label(
                                            RichText::new("Coordinates (X, Y):")
                                                .color(TEXT_SECONDARY),
                                        );
                                        ui.horizontal(|ui| {
                                            ui.add(
                                                egui::TextEdit::singleline(&mut self.x_str)
                                                    .desired_width(68.0)
                                                    .hint_text("Curr X"),
                                            );
                                            ui.add(
                                                egui::TextEdit::singleline(&mut self.y_str)
                                                    .desired_width(68.0)
                                                    .hint_text("Curr Y"),
                                            );
                                            if styled_button(
                                                ui,
                                                "Pick XY",
                                                BTN_NEUTRAL,
                                                TEXT_PRIMARY,
                                                BORDER_CONTROL,
                                            )
                                            .clicked()
                                                && get_platform_capabilities().can_poll_cursor
                                            {
                                                let (cx, cy) = platform_get_cursor_pos();
                                                self.x_str = cx.to_string();
                                                self.y_str = cy.to_string();
                                            }
                                        });
                                        ui.end_row();
                                    }
                                    ActionType::MouseMove => {
                                        ui.label(
                                            RichText::new("Movement Mode:").color(TEXT_SECONDARY),
                                        );
                                        egui::ComboBox::from_id_salt("step_move_mode")
                                            .width(210.0)
                                            .selected_text(move_mode_label(self.move_mode))
                                            .show_ui(ui, |ui| {
                                                for m in [MoveMode::Absolute, MoveMode::Relative] {
                                                    ui.selectable_value(
                                                        &mut self.move_mode,
                                                        m,
                                                        move_mode_label(m),
                                                    );
                                                }
                                            });
                                        ui.end_row();

                                        ui.label(
                                            RichText::new("Target X / Y:").color(TEXT_SECONDARY),
                                        );
                                        ui.horizontal(|ui| {
                                            ui.add(
                                                egui::TextEdit::singleline(&mut self.x_str)
                                                    .desired_width(72.0),
                                            );
                                            ui.add(
                                                egui::TextEdit::singleline(&mut self.y_str)
                                                    .desired_width(72.0),
                                            );
                                            if styled_button(
                                                ui,
                                                "Pick XY",
                                                BTN_NEUTRAL,
                                                TEXT_PRIMARY,
                                                BORDER_CONTROL,
                                            )
                                            .clicked()
                                                && get_platform_capabilities().can_poll_cursor
                                            {
                                                let (cx, cy) = platform_get_cursor_pos();
                                                self.x_str = cx.to_string();
                                                self.y_str = cy.to_string();
                                            }
                                        });
                                        ui.end_row();
                                    }
                                    ActionType::MouseScroll => {
                                        ui.label(
                                            RichText::new("Vertical Ticks (+Up/-Dn):")
                                                .color(TEXT_SECONDARY),
                                        );
                                        ui.add(
                                            egui::TextEdit::singleline(&mut self.scroll_dy_str)
                                                .desired_width(100.0),
                                        );
                                        ui.end_row();

                                        ui.label(
                                            RichText::new("Horizontal Ticks:")
                                                .color(TEXT_SECONDARY),
                                        );
                                        ui.add(
                                            egui::TextEdit::singleline(&mut self.scroll_dx_str)
                                                .desired_width(100.0),
                                        );
                                        ui.end_row();
                                    }
                                    ActionType::RepeatBlock => {
                                        ui.label(
                                            RichText::new("Repeat Count:").color(TEXT_SECONDARY),
                                        );
                                        ui.add(
                                            egui::TextEdit::singleline(&mut self.repeat_count_str)
                                                .desired_width(110.0),
                                        );
                                        ui.end_row();

                                        ui.label(
                                            RichText::new("Inner Steps DSL:")
                                                .color(TEXT_SECONDARY),
                                        );
                                        ui.add(
                                            egui::TextEdit::singleline(&mut self.repeat_dsl)
                                                .desired_width(240.0)
                                                .font(egui::TextStyle::Monospace),
                                        );
                                        ui.end_row();
                                    }
                                }
                            });

                        if self.action_type == ActionType::Wait {
                            ui.add_space(6.0);
                            ui.label(
                                RichText::new("Tip: Adding 5\u{2013}20ms jitter makes repeats behave like natural human timing.")
                                    .size(11.0)
                                    .color(TEXT_SECONDARY),
                            );
                        } else if self.action_type == ActionType::MouseClick {
                            ui.add_space(6.0);
                            ui.label(
                                RichText::new("Leave X and Y blank to click at the current live cursor position.")
                                    .size(11.0)
                                    .color(TEXT_SECONDARY),
                            );
                        } else if self.action_type == ActionType::RepeatBlock {
                            let disabled_cnt = self
                                .initial_repeat_subs
                                .iter()
                                .filter(|s| !s.enabled)
                                .count();
                            if disabled_cnt > 0 {
                                ui.add_space(6.0);
                                ui.label(
                                    RichText::new(format!(
                                        "Note: {disabled_cnt} disabled inner step(s) are preserved unless Inner Steps text is edited."
                                    ))
                                    .size(11.0)
                                    .color(ACCENT_WARNING),
                                );
                            }
                        }
                    });

                if !self.error.is_empty() {
                    ui.add_space(6.0);
                    ui.label(
                        RichText::new(format!("Validation Error: {}", self.error))
                            .size(11.5)
                            .color(ACCENT_DANGER),
                    );
                }

                ui.add_space(10.0);
                ui.horizontal(|ui| {
                    let hint = if cfg!(target_os = "macos") {
                        "\u{2318}+Enter to save"
                    } else {
                        "Ctrl+Enter to save"
                    };
                    ui.label(RichText::new(hint).size(11.0).color(TEXT_MUTED));
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if styled_button(ui, "Save Step", CTA_SUCCESS, CTA_TEXT, CTA_SUCCESS)
                            .clicked()
                        {
                            match self.build_validated() {
                                Ok(action) => {
                                    outcome = StepDialogOutcome::Saved {
                                        edit_id: self.edit_id.clone(),
                                        action: Box::new(action),
                                    };
                                }
                                Err(e) => self.error = e,
                            }
                        }
                        if styled_button(ui, "Cancel", BTN_NEUTRAL, TEXT_PRIMARY, BORDER_CONTROL)
                            .clicked()
                        {
                            close_clicked = true;
                        }
                    });
                });
                let _ = BG_ELEVATED;
                let _ = BG_INPUT;
            });

        if close_clicked || ctx.input(|i| i.key_pressed(egui::Key::Escape)) && !self.capturing_key {
            return StepDialogOutcome::Cancelled;
        }
        outcome
    }
}
