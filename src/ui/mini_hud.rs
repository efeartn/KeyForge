//! Always-on-top compact floating Mini-HUD viewport pill for kTap.

use egui::{Color32, Context, RichText, Rounding, Stroke, ViewportBuilder, ViewportId};

use crate::engine::executor::{ExecutionTelemetry, ExecutorState};
use crate::ui::theme::{
    styled_button, ACCENT_DANGER, ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, BG_CARD,
    BG_PANEL, BORDER_CONTROL, BORDER_SUBTLE, BTN_DANGER_BORDER, BTN_DANGER_SUBTLE,
    BTN_DANGER_SUBTLE_TEXT, BTN_NEUTRAL, CTA_SUCCESS, CTA_TEXT, TEXT_PRIMARY, TEXT_SECONDARY,
};

pub enum MiniHudAction {
    None,
    ToggleExecution,
    PanicStop,
    Expand,
}

pub fn show_mini_hud(
    ctx: &Context,
    telem: &ExecutionTelemetry,
    is_running: bool,
    countdown_rem_s: Option<f32>,
    start_hk: &str,
    panic_hk: &str,
) -> MiniHudAction {
    let mut result = MiniHudAction::None;

    let vp_id = ViewportId::from_hash_of("ktap_mini_hud");
    let builder = ViewportBuilder::default()
        .with_title("kTap HUD")
        .with_inner_size([432.0, 70.0])
        .with_resizable(false)
        .with_always_on_top();

    ctx.show_viewport_immediate(vp_id, builder, |hud_ctx, _class| {
        if hud_ctx.input(|i| i.viewport().close_requested()) {
            result = MiniHudAction::Expand;
        }

        let (state_text, state_color, is_active) = if is_running {
            ("\u{25CF} RUNNING".to_string(), ACCENT_PRIMARY, true)
        } else if let Some(rem) = countdown_rem_s {
            (format!("\u{23F3} {rem:.1}s"), ACCENT_WARNING, true)
        } else {
            match telem.state {
                ExecutorState::PanicAborted => {
                    ("\u{25CF} ABORTED".to_string(), ACCENT_DANGER, false)
                }
                ExecutorState::Error => ("\u{25CF} ERROR".to_string(), ACCENT_DANGER, false),
                _ => ("\u{25CF} READY".to_string(), ACCENT_SUCCESS, false),
            }
        };

        egui::CentralPanel::default()
            .frame(
                egui::Frame::none()
                    .fill(BG_PANEL)
                    .inner_margin(egui::Margin::same(6.0)),
            )
            .show(hud_ctx, |ui| {
                egui::Frame::none()
                    .fill(BG_CARD)
                    .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                    .rounding(Rounding::same(10.0))
                    .inner_margin(egui::Margin::symmetric(10.0, 6.0))
                    .show(ui, |ui| {
                        ui.horizontal_centered(|ui| {
                            ui.vertical(|ui| {
                                ui.label(
                                    RichText::new(state_text)
                                        .size(12.0)
                                        .strong()
                                        .color(state_color),
                                );
                                ui.label(
                                    RichText::new(format!("{} cycles", telem.cycles_completed))
                                        .monospace()
                                        .size(11.0)
                                        .color(TEXT_SECONDARY),
                                );
                            });

                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if styled_button(
                                        ui,
                                        "Expand",
                                        BTN_NEUTRAL,
                                        TEXT_PRIMARY,
                                        BORDER_CONTROL,
                                    )
                                    .clicked()
                                    {
                                        result = MiniHudAction::Expand;
                                    }

                                    let panic_txt = format!("Panic ({panic_hk})");
                                    if styled_button(
                                        ui,
                                        &panic_txt,
                                        BTN_DANGER_SUBTLE,
                                        BTN_DANGER_SUBTLE_TEXT,
                                        BTN_DANGER_BORDER,
                                    )
                                    .clicked()
                                    {
                                        result = MiniHudAction::PanicStop;
                                    }

                                    let (run_txt, run_fill, run_fg) = if is_active {
                                        (
                                            format!("\u{25A0} Stop ({start_hk})"),
                                            ACCENT_DANGER,
                                            Color32::WHITE,
                                        )
                                    } else {
                                        (
                                            format!("\u{25B6} Start ({start_hk})"),
                                            CTA_SUCCESS,
                                            CTA_TEXT,
                                        )
                                    };
                                    if styled_button(ui, &run_txt, run_fill, run_fg, run_fill)
                                        .clicked()
                                    {
                                        result = MiniHudAction::ToggleExecution;
                                    }
                                },
                            );
                        });
                    });
            });
    });

    result
}
