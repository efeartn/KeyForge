//! Standalone Setup & Uninstall wizard window for KeyForge (`eframe/egui`).

use std::path::PathBuf;
use std::process::{Command, Stdio};

use egui::{Color32, Context, RichText, Rounding, Stroke, ViewportCommand};

use crate::config::get_config_dir;
use crate::installer::{get_default_install_dir, install_keyforge, uninstall_keyforge};
use crate::ui::theme::{
    apply_theme, apply_windows_window_branding, card_frame, load_brand_texture,
    load_window_icon_data, micro_heading, styled_button, ACCENT_DANGER, ACCENT_PRIMARY,
    ACCENT_SUCCESS, BG_INPUT, BG_PANEL, BORDER_CONTROL, BORDER_SUBTLE, BTN_NEUTRAL, CTA_SUCCESS,
    CTA_TEXT, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY,
};
use crate::CODENAME;
use crate::VERSION;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallerMode {
    Setup,
    Uninstall,
}

pub struct InstallerWindow {
    mode: InstallerMode,
    target_dir: PathBuf,
    desktop_shortcut: bool,
    start_menu_shortcut: bool,
    launch_after: bool,
    purge_data: bool,
    finished: bool,
    status_text: String,
    status_color: Color32,
    brand_tex: Option<egui::TextureHandle>,
    branded_window: bool,
}

impl InstallerWindow {
    pub fn new(mode: InstallerMode, install_dir: Option<PathBuf>) -> Self {
        let target_dir = install_dir.unwrap_or_else(get_default_install_dir);
        let status_text = if mode == InstallerMode::Setup {
            "Ready to install.".to_string()
        } else {
            "Ready to uninstall.".to_string()
        };
        Self {
            mode,
            target_dir,
            desktop_shortcut: true,
            start_menu_shortcut: true,
            launch_after: true,
            purge_data: false,
            finished: false,
            status_text,
            status_color: TEXT_SECONDARY,
            brand_tex: None,
            branded_window: false,
        }
    }

    pub fn run(mode: InstallerMode, install_dir: Option<PathBuf>) -> eframe::Result<()> {
        let title = if mode == InstallerMode::Uninstall {
            format!("Uninstall KeyForge v{VERSION} \"{CODENAME}\"")
        } else {
            format!("KeyForge v{VERSION} \"{CODENAME}\" Setup")
        };
        let mut vp = egui::ViewportBuilder::default()
            .with_inner_size([540.0, 440.0])
            .with_resizable(false);
        if let Some(icon) = load_window_icon_data() {
            vp = vp.with_icon(icon);
        }
        let options = eframe::NativeOptions {
            viewport: vp,
            ..Default::default()
        };
        eframe::run_native(
            &title,
            options,
            Box::new(move |cc| {
                apply_theme(&cc.egui_ctx);
                Ok(Box::new(Self::new(mode, install_dir)))
            }),
        )
    }

    fn execute_action(&mut self, ctx: &Context) {
        if self.finished {
            ctx.send_viewport_cmd(ViewportCommand::Close);
            return;
        }
        match self.mode {
            InstallerMode::Setup => {
                match install_keyforge(
                    Some(&self.target_dir),
                    None,
                    self.desktop_shortcut,
                    self.start_menu_shortcut,
                    true,
                ) {
                    Ok(manifest) => {
                        self.finished = true;
                        self.status_text = format!(
                            "Installed KeyForge v{VERSION} to {}",
                            self.target_dir.display()
                        );
                        self.status_color = ACCENT_SUCCESS;
                        if self.launch_after && manifest.installed_exe.is_file() {
                            let _ = Command::new(&manifest.installed_exe)
                                .stdin(Stdio::null())
                                .stdout(Stdio::null())
                                .stderr(Stdio::null())
                                .spawn();
                            ctx.send_viewport_cmd(ViewportCommand::Close);
                        }
                    }
                    Err(e) => {
                        self.status_text = format!("Operation failed: {e}");
                        self.status_color = ACCENT_DANGER;
                    }
                }
            }
            InstallerMode::Uninstall => {
                let _res = uninstall_keyforge(Some(&self.target_dir), self.purge_data, true);
                self.finished = true;
                self.status_text = if self.purge_data {
                    "All KeyForge files and user configs removed.".to_string()
                } else {
                    "KeyForge uninstalled. Saved macros and configs were preserved.".to_string()
                };
                self.status_color = ACCENT_SUCCESS;
            }
        }
    }
}

impl eframe::App for InstallerWindow {
    fn update(&mut self, ctx: &Context, _frame: &mut eframe::Frame) {
        if !self.branded_window {
            self.branded_window = true;
            apply_windows_window_branding();
        }
        if self.brand_tex.is_none() {
            self.brand_tex = load_brand_texture(ctx);
        }

        egui::CentralPanel::default()
            .frame(
                egui::Frame::none()
                    .fill(BG_PANEL)
                    .inner_margin(egui::Margin::same(22.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    if let Some(tex) = &self.brand_tex {
                        ui.image((tex.id(), egui::vec2(38.0, 38.0)));
                        ui.add_space(6.0);
                    }
                    ui.vertical(|ui| {
                        let heading = if self.mode == InstallerMode::Setup {
                            format!("KeyForge v{VERSION} \"{CODENAME}\" Setup")
                        } else {
                            format!("Uninstall KeyForge v{VERSION} \"{CODENAME}\"")
                        };
                        let subtitle = if self.mode == InstallerMode::Setup {
                            "Install KeyForge on this computer while preserving existing configs & macros"
                        } else {
                            "Remove KeyForge binaries, shortcuts, and app registration"
                        };
                        ui.label(
                            RichText::new(heading)
                                .size(19.0)
                                .strong()
                                .color(TEXT_PRIMARY),
                        );
                        ui.label(
                            RichText::new(subtitle)
                                .size(11.5)
                                .color(TEXT_SECONDARY),
                        );
                    });
                });

                ui.add_space(14.0);

                // Location card
                card_frame().show(ui, |ui| {
                    micro_heading(
                        ui,
                        if self.mode == InstallerMode::Setup {
                            "DESTINATION FOLDER"
                        } else {
                            "INSTALLED LOCATION"
                        },
                    );
                    ui.add_space(4.0);
                    egui::Frame::none()
                        .fill(BG_INPUT)
                        .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                        .rounding(Rounding::same(8.0))
                        .inner_margin(egui::Margin::symmetric(10.0, 7.0))
                        .show(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.add(
                                    egui::Label::new(
                                        RichText::new(self.target_dir.to_string_lossy().as_ref())
                                            .monospace()
                                            .size(11.5)
                                            .color(TEXT_PRIMARY),
                                    )
                                    .truncate(),
                                );
                                if self.mode == InstallerMode::Setup {
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            if styled_button(
                                                ui,
                                                "Browse...",
                                                BTN_NEUTRAL,
                                                TEXT_PRIMARY,
                                                BORDER_CONTROL,
                                            )
                                            .clicked()
                                            {
                                                if let Some(chosen) = rfd::FileDialog::new()
                                                    .set_title("Select Installation Folder")
                                                    .set_directory(&self.target_dir)
                                                    .pick_folder()
                                                {
                                                    self.target_dir = chosen;
                                                }
                                            }
                                        },
                                    );
                                }
                            });
                        });
                });

                ui.add_space(10.0);

                // Options card
                card_frame().show(ui, |ui| {
                    let config_dir = get_config_dir(None);
                    if self.mode == InstallerMode::Setup {
                        ui.checkbox(&mut self.desktop_shortcut, "Create Desktop shortcut");
                        let menu_lbl = if cfg!(target_os = "windows") {
                            "Create Start Menu shortcut"
                        } else if cfg!(target_os = "linux") {
                            "Create Application Menu launcher (.desktop)"
                        } else {
                            "Create Applications shortcut"
                        };
                        ui.checkbox(&mut self.start_menu_shortcut, menu_lbl);
                        ui.checkbox(
                            &mut self.launch_after,
                            "Launch KeyForge when setup finishes",
                        );
                        ui.add_space(6.0);
                        ui.add(
                            egui::Label::new(
                                RichText::new(format!(
                                    "Configs & saved macros in {} are automatically preserved.",
                                    config_dir.display()
                                ))
                                .size(11.0)
                                .color(TEXT_MUTED),
                            )
                            .wrap(),
                        );
                    } else {
                        ui.checkbox(
                            &mut self.purge_data,
                            RichText::new(
                                "Also delete my saved macros, custom presets & configuration",
                            )
                            .strong(),
                        );
                        ui.add_space(6.0);
                        ui.add(
                            egui::Label::new(
                                RichText::new(format!(
                                    "Left unchecked, your saved data in {} remains safe for future reinstalls.",
                                    config_dir.display()
                                ))
                                .size(11.0)
                                .color(TEXT_MUTED),
                            )
                            .wrap(),
                        );
                    }
                });

                ui.add_space(12.0);
                ui.label(
                    RichText::new(&self.status_text)
                        .size(11.5)
                        .color(self.status_color),
                );
                ui.add_space(10.0);

                ui.horizontal(|ui| {
                    if styled_button(
                        ui,
                        if self.finished { "Close" } else { "Cancel" },
                        BTN_NEUTRAL,
                        TEXT_PRIMARY,
                        BORDER_CONTROL,
                    )
                    .clicked()
                    {
                        ctx.send_viewport_cmd(ViewportCommand::Close);
                    }

                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let (btn_txt, btn_fill, btn_fg) = if self.finished {
                            let lbl = if self.mode == InstallerMode::Setup {
                                "Finish"
                            } else {
                                "Done"
                            };
                            (lbl.to_string(), ACCENT_PRIMARY, CTA_TEXT)
                        } else if self.mode == InstallerMode::Setup {
                            (
                                format!("Install KeyForge v{VERSION}"),
                                CTA_SUCCESS,
                                CTA_TEXT,
                            )
                        } else {
                            (
                                "Uninstall KeyForge".to_string(),
                                ACCENT_DANGER,
                                Color32::WHITE,
                            )
                        };
                        if styled_button(ui, &btn_txt, btn_fill, btn_fg, btn_fill).clicked() {
                            self.execute_action(ctx);
                        }
                    });
                });
            });
    }
}
