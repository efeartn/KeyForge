//! Minimalist Craft Obsidian & Mint Emerald design system, embedded icons, and OS window styling.

use std::sync::Arc;

use egui::{Color32, Context, FontId, Rounding, Stroke, Vec2};

use crate::installer::EMBEDDED_ICON_PNG;
use crate::models::action::ActionType;

// Craft Obsidian / Deep Slate Surface Hierarchy
pub const BG_ROOT: Color32 = Color32::from_rgb(0x09, 0x0C, 0x10);
pub const BG_PANEL: Color32 = Color32::from_rgb(0x0E, 0x13, 0x19);
pub const BG_ELEVATED: Color32 = Color32::from_rgb(0x15, 0x1D, 0x27);
pub const BG_CARD: Color32 = Color32::from_rgb(0x10, 0x16, 0x1E);
pub const BG_CARD_HOVER: Color32 = Color32::from_rgb(0x16, 0x1E, 0x29);
pub const BG_CARD_ACTIVE: Color32 = Color32::from_rgb(0x0F, 0x27, 0x1E);
pub const BG_INPUT: Color32 = Color32::from_rgb(0x0A, 0x0E, 0x13);

pub const BORDER_SUBTLE: Color32 = Color32::from_rgb(0x1B, 0x24, 0x30);
pub const BORDER_CONTROL: Color32 = Color32::from_rgb(0x22, 0x2D, 0x3D);
pub const BORDER_FOCUS: Color32 = Color32::from_rgb(0x2E, 0xE5, 0x9D);

// Typography Hierarchy
pub const TEXT_PRIMARY: Color32 = Color32::from_rgb(0xF1, 0xF5, 0xF9);
pub const TEXT_SECONDARY: Color32 = Color32::from_rgb(0x8B, 0x98, 0xA9);
pub const TEXT_MUTED: Color32 = Color32::from_rgb(0x5E, 0x6B, 0x7C);
pub const CTA_TEXT: Color32 = Color32::from_rgb(0x05, 0x19, 0x10);

// Cohesive Mint-Emerald + Calm Slate Accent System
pub const ACCENT_PRIMARY: Color32 = Color32::from_rgb(0x2E, 0xE5, 0x9D);
pub const ACCENT_PRIMARY_HOVER: Color32 = Color32::from_rgb(0x24, 0xCF, 0x8B);
pub const ACCENT_SUCCESS: Color32 = Color32::from_rgb(0x34, 0xD3, 0x99);
pub const CTA_SUCCESS: Color32 = Color32::from_rgb(0x2E, 0xE5, 0x9D);
pub const CTA_SUCCESS_HOVER: Color32 = Color32::from_rgb(0x24, 0xCF, 0x8B);
pub const SEGMENT_ACTIVE: Color32 = Color32::from_rgb(0x1A, 0x22, 0x2E);
pub const SEGMENT_ACTIVE_HOVER: Color32 = Color32::from_rgb(0x21, 0x2B, 0x3A);
pub const ACCENT_WARNING: Color32 = Color32::from_rgb(0xF5, 0x9E, 0x0B);
pub const ACCENT_DANGER: Color32 = Color32::from_rgb(0xEF, 0x44, 0x44);
pub const ACCENT_DANGER_HOVER: Color32 = Color32::from_rgb(0xDC, 0x26, 0x26);

// Hairline-Bordered Neutral & Danger Controls
pub const BTN_NEUTRAL: Color32 = Color32::from_rgb(0x13, 0x19, 0x22);
pub const BTN_NEUTRAL_HOVER: Color32 = Color32::from_rgb(0x1A, 0x22, 0x2E);
pub const BTN_SUBTLE: Color32 = Color32::from_rgb(0x0F, 0x14, 0x1C);
pub const BTN_DANGER_SUBTLE: Color32 = Color32::from_rgb(0x20, 0x11, 0x15);
pub const BTN_DANGER_SUBTLE_TEXT: Color32 = Color32::from_rgb(0xF8, 0x71, 0x71);
pub const BTN_DANGER_BORDER: Color32 = Color32::from_rgb(0x3B, 0x1A, 0x22);

pub const BANNER_UPDATE_BG: Color32 = Color32::from_rgb(0x0C, 0x24, 0x1B);
pub const BANNER_UPDATE_BORDER: Color32 = Color32::from_rgb(0x1C, 0x5A, 0x41);
pub const BANNER_UPDATE_FG: Color32 = Color32::from_rgb(0x6E, 0xE7, 0xB7);

pub const BANNER_ERR_BG: Color32 = Color32::from_rgb(0x2A, 0x12, 0x15);
pub const BANNER_ERR_BORDER: Color32 = Color32::from_rgb(0x69, 0x20, 0x26);
pub const BANNER_ERR_FG: Color32 = Color32::from_rgb(0xFC, 0xA5, 0xA5);

pub const BANNER_WARN_BG: Color32 = Color32::from_rgb(0x29, 0x1E, 0x0D);
pub const BANNER_WARN_BORDER: Color32 = Color32::from_rgb(0x6E, 0x4B, 0x15);
pub const BANNER_WARN_FG: Color32 = Color32::from_rgb(0xFD, 0xE6, 0x8A);

pub const BANNER_INFO_BG: Color32 = Color32::from_rgb(0x0F, 0x1E, 0x30);
pub const BANNER_INFO_BORDER: Color32 = Color32::from_rgb(0x1F, 0x42, 0x6B);
pub const BANNER_INFO_FG: Color32 = Color32::from_rgb(0x93, 0xC5, 0xFD);

pub const BADGE_OK_BG: Color32 = Color32::from_rgb(0x0E, 0x29, 0x20);
pub const BADGE_WARN_BG: Color32 = Color32::from_rgb(0x26, 0x1E, 0x0D);
pub const BADGE_WARN_FG: Color32 = Color32::from_rgb(0xFB, 0xBF, 0x24);
pub const BADGE_CRIT_BG: Color32 = Color32::from_rgb(0x2D, 0x12, 0x15);
pub const BADGE_CRIT_FG: Color32 = Color32::from_rgb(0xFC, 0xA5, 0xA5);

/// Per-action pill palette `(badge_bg, badge_fg, left_stripe)`.
pub fn action_visual_style(action_type: ActionType) -> (Color32, Color32, Color32) {
    match action_type {
        ActionType::KeyPress => (
            Color32::from_rgb(0x0E, 0x29, 0x20),
            Color32::from_rgb(0x34, 0xD3, 0x99),
            Color32::from_rgb(0x34, 0xD3, 0x99),
        ),
        ActionType::KeyDown => (
            Color32::from_rgb(0x0E, 0x24, 0x33),
            Color32::from_rgb(0x38, 0xBD, 0xF8),
            Color32::from_rgb(0x38, 0xBD, 0xF8),
        ),
        ActionType::KeyUp => (
            Color32::from_rgb(0x18, 0x20, 0x2B),
            Color32::from_rgb(0x94, 0xA3, 0xB8),
            Color32::from_rgb(0x94, 0xA3, 0xB8),
        ),
        ActionType::MouseClick => (
            Color32::from_rgb(0x0E, 0x2A, 0x26),
            Color32::from_rgb(0x2D, 0xD4, 0xBF),
            Color32::from_rgb(0x2D, 0xD4, 0xBF),
        ),
        ActionType::MouseMove => (
            Color32::from_rgb(0x10, 0x24, 0x36),
            Color32::from_rgb(0x60, 0xA5, 0xFA),
            Color32::from_rgb(0x60, 0xA5, 0xFA),
        ),
        ActionType::MouseScroll => (
            Color32::from_rgb(0x1E, 0x1A, 0x36),
            Color32::from_rgb(0xA7, 0x8B, 0xFA),
            Color32::from_rgb(0xA7, 0x8B, 0xFA),
        ),
        ActionType::Wait => (
            Color32::from_rgb(0x27, 0x1F, 0x0E),
            Color32::from_rgb(0xFB, 0xBF, 0x24),
            Color32::from_rgb(0xFB, 0xBF, 0x24),
        ),
        ActionType::RepeatBlock => (
            Color32::from_rgb(0x2C, 0x16, 0x25),
            Color32::from_rgb(0xF4, 0x72, 0xB6),
            Color32::from_rgb(0xF4, 0x72, 0xB6),
        ),
    }
}

pub fn apply_theme(ctx: &Context) {
    let mut style = (*ctx.style()).clone();
    let mut visuals = egui::Visuals::dark();

    visuals.dark_mode = true;
    visuals.panel_fill = BG_ROOT;
    visuals.window_fill = BG_PANEL;
    visuals.window_stroke = Stroke::new(1.0_f32, BORDER_SUBTLE);
    visuals.window_rounding = Rounding::same(12.0);
    visuals.extreme_bg_color = BG_INPUT;
    visuals.faint_bg_color = BG_ELEVATED;
    visuals.code_bg_color = BG_INPUT;
    visuals.override_text_color = Some(TEXT_PRIMARY);
    visuals.selection.bg_fill = Color32::from_rgba_unmultiplied(0x2E, 0xE5, 0x9D, 46);
    visuals.selection.stroke = Stroke::new(1.0_f32, ACCENT_PRIMARY);

    visuals.widgets.noninteractive.bg_fill = BG_PANEL;
    visuals.widgets.noninteractive.bg_stroke = Stroke::new(1.0_f32, BORDER_SUBTLE);
    visuals.widgets.noninteractive.fg_stroke = Stroke::new(1.0_f32, TEXT_PRIMARY);
    visuals.widgets.noninteractive.rounding = Rounding::same(8.0);

    visuals.widgets.inactive.bg_fill = BTN_NEUTRAL;
    visuals.widgets.inactive.weak_bg_fill = BTN_NEUTRAL;
    visuals.widgets.inactive.bg_stroke = Stroke::new(1.0_f32, BORDER_CONTROL);
    visuals.widgets.inactive.fg_stroke = Stroke::new(1.0_f32, TEXT_PRIMARY);
    visuals.widgets.inactive.rounding = Rounding::same(7.0);

    visuals.widgets.hovered.bg_fill = BTN_NEUTRAL_HOVER;
    visuals.widgets.hovered.weak_bg_fill = BTN_NEUTRAL_HOVER;
    visuals.widgets.hovered.bg_stroke = Stroke::new(1.0_f32, BORDER_CONTROL);
    visuals.widgets.hovered.fg_stroke = Stroke::new(1.0_f32, TEXT_PRIMARY);
    visuals.widgets.hovered.rounding = Rounding::same(7.0);

    visuals.widgets.active.bg_fill = SEGMENT_ACTIVE;
    visuals.widgets.active.weak_bg_fill = SEGMENT_ACTIVE;
    visuals.widgets.active.bg_stroke = Stroke::new(1.0_f32, ACCENT_PRIMARY);
    visuals.widgets.active.fg_stroke = Stroke::new(1.0_f32, TEXT_PRIMARY);
    visuals.widgets.active.rounding = Rounding::same(7.0);

    visuals.widgets.open.bg_fill = BG_ELEVATED;
    visuals.widgets.open.bg_stroke = Stroke::new(1.0_f32, BORDER_CONTROL);
    visuals.widgets.open.fg_stroke = Stroke::new(1.0_f32, TEXT_PRIMARY);
    visuals.widgets.open.rounding = Rounding::same(7.0);

    style.visuals = visuals;
    style.spacing.item_spacing = Vec2::new(8.0, 6.0);
    style.spacing.button_padding = Vec2::new(10.0, 5.0);
    style.spacing.scroll.bar_width = 7.0;
    ctx.set_style(style);
}

pub fn load_window_icon_data() -> Option<Arc<egui::IconData>> {
    let img = image::load_from_memory(EMBEDDED_ICON_PNG).ok()?;
    let resized = img.resize(64, 64, image::imageops::FilterType::Lanczos3);
    let rgba = resized.to_rgba8();
    let (w, h) = rgba.dimensions();
    Some(Arc::new(egui::IconData {
        rgba: rgba.into_raw(),
        width: w,
        height: h,
    }))
}

pub fn load_brand_texture(ctx: &Context) -> Option<egui::TextureHandle> {
    let img = image::load_from_memory(EMBEDDED_ICON_PNG).ok()?;
    let resized = img.resize(64, 64, image::imageops::FilterType::Lanczos3);
    let rgba = resized.to_rgba8();
    let size = [rgba.width() as usize, rgba.height() as usize];
    let color_image = egui::ColorImage::from_rgba_unmultiplied(size, rgba.as_raw());
    Some(ctx.load_texture("keyforge_brand", color_image, egui::TextureOptions::LINEAR))
}

pub fn apply_windows_window_branding() {
    #[cfg(target_os = "windows")]
    unsafe {
        use windows_sys::Win32::Graphics::Dwm::DwmSetWindowAttribute;
        use windows_sys::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID;
        use windows_sys::Win32::UI::WindowsAndMessaging::GetForegroundWindow;

        let app_id: Vec<u16> = "KeyForge.Studio.App.1\0".encode_utf16().collect();
        let _ = SetCurrentProcessExplicitAppUserModelID(app_id.as_ptr());

        let hwnd = GetForegroundWindow();
        if !hwnd.is_null() {
            let dark: i32 = 1;
            let res = DwmSetWindowAttribute(
                hwnd,
                20,
                &dark as *const i32 as *const std::ffi::c_void,
                std::mem::size_of::<i32>() as u32,
            );
            if res != 0 {
                let _ = DwmSetWindowAttribute(
                    hwnd,
                    19,
                    &dark as *const i32 as *const std::ffi::c_void,
                    std::mem::size_of::<i32>() as u32,
                );
            }
        }
    }
}

pub fn panel_frame() -> egui::Frame {
    egui::Frame::none()
        .fill(BG_PANEL)
        .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
        .rounding(Rounding::same(10.0))
        .inner_margin(egui::Margin::same(12.0))
}

pub fn card_frame() -> egui::Frame {
    egui::Frame::none()
        .fill(BG_CARD)
        .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
        .rounding(Rounding::same(10.0))
        .inner_margin(egui::Margin::same(12.0))
}

pub fn micro_heading(ui: &mut egui::Ui, text: &str) {
    ui.label(
        egui::RichText::new(text)
            .font(FontId::proportional(10.0))
            .strong()
            .color(TEXT_MUTED),
    );
}

pub fn styled_button(
    ui: &mut egui::Ui,
    text: &str,
    fill: Color32,
    text_color: Color32,
    border: Color32,
) -> egui::Response {
    ui.add(
        egui::Button::new(
            egui::RichText::new(text)
                .color(text_color)
                .strong()
                .size(12.0),
        )
        .fill(fill)
        .stroke(Stroke::new(1.0_f32, border))
        .rounding(Rounding::same(7.0)),
    )
}

pub fn ghost_button(ui: &mut egui::Ui, text: &str, text_color: Color32) -> egui::Response {
    ui.add(
        egui::Button::new(egui::RichText::new(text).color(text_color).size(11.5))
            .fill(Color32::TRANSPARENT)
            .stroke(Stroke::NONE)
            .rounding(Rounding::same(6.0)),
    )
}

pub fn pill_badge(ui: &mut egui::Ui, text: &str, bg: Color32, fg: Color32) {
    egui::Frame::none()
        .fill(bg)
        .rounding(Rounding::same(6.0))
        .inner_margin(egui::Margin::symmetric(7.0, 2.0))
        .show(ui, |ui| {
            ui.label(egui::RichText::new(text).size(10.0).strong().color(fg));
        });
}
