//! Modal Software Update dialog for kTap.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};

use egui::{Context, RichText, Rounding, Stroke};

use crate::config::get_config_dir;
use crate::ui::theme::{
    styled_button, ACCENT_DANGER, ACCENT_SUCCESS, ACCENT_WARNING, BG_CARD, BG_ELEVATED, BG_PANEL,
    BORDER_CONTROL, BORDER_SUBTLE, BTN_NEUTRAL, CTA_SUCCESS, CTA_TEXT, TEXT_MUTED, TEXT_PRIMARY,
    TEXT_SECONDARY,
};
use crate::updater::{
    apply_update_and_restart, download_update_asset, fetch_latest_release, ReleaseInfo,
    DEFAULT_REPO,
};
use crate::{codename_for_version, CODENAME, VERSION};

enum UpdateMsg {
    CheckOk(Box<ReleaseInfo>),
    CheckErr(String),
    Progress(u64, Option<u64>),
    DownloadOk(PathBuf),
    DownloadErr(String),
    Cancelled,
}

pub enum UpdateDialogAction {
    Open,
    Close,
    Checked(Box<ReleaseInfo>),
    Restarting,
}

pub struct UpdateDialogState {
    release: Option<ReleaseInfo>,
    is_checking: bool,
    is_downloading: bool,
    cancel_flag: Arc<AtomicBool>,
    progress_frac: f32,
    status_title: String,
    meta_text: String,
    progress_text: String,
    notes_text: String,
    error_text: String,
    rx: mpsc::Receiver<UpdateMsg>,
    tx: mpsc::Sender<UpdateMsg>,
}

impl UpdateDialogState {
    pub fn new(initial_release: Option<ReleaseInfo>) -> Self {
        let (tx, rx) = mpsc::channel();
        let mut s = Self {
            release: initial_release.clone(),
            is_checking: false,
            is_downloading: false,
            cancel_flag: Arc::new(AtomicBool::new(false)),
            progress_frac: 0.0,
            status_title: format!("kTap v{VERSION} \"{CODENAME}\""),
            meta_text: "Checking GitHub Releases...".to_string(),
            progress_text: String::new(),
            notes_text: String::new(),
            error_text: String::new(),
            rx,
            tx,
        };
        if let Some(rel) = initial_release {
            s.apply_release(&rel);
        } else {
            s.start_check();
        }
        s
    }

    pub fn is_busy(&self) -> bool {
        self.is_checking || self.is_downloading
    }

    fn apply_release(&mut self, rel: &ReleaseInfo) {
        self.error_text.clear();
        self.notes_text = if rel.body.trim().is_empty() {
            "No release notes provided.".to_string()
        } else {
            rel.body.clone()
        };

        let rel_codename_suffix = codename_for_version(&rel.version)
            .map(|c| format!(" \"{c}\""))
            .unwrap_or_default();
        if rel.is_update_available {
            self.status_title =
                format!("Update Available: v{}{}", rel.version, rel_codename_suffix);
            let sha_hint = rel
                .sha256
                .as_ref()
                .map(|s| format!("SHA-256: {}...", &s[..16.min(s.len())]))
                .unwrap_or_else(|| "SHA-256: not published".to_string());
            let size_hint = rel
                .asset_size
                .map(|sz| format!("{:.1} MB", sz as f64 / (1024.0 * 1024.0)))
                .unwrap_or_else(|| "size unknown".to_string());
            self.meta_text = format!(
                "{} \u{2022} {size_hint} \u{2022} {sha_hint}",
                rel.asset_name.as_deref().unwrap_or("Platform binary")
            );
            if rel.download_url.is_some() {
                self.progress_text =
                    "All configs & macros will be preserved automatically.".to_string();
            } else {
                self.progress_text =
                    "No matching binary asset found for this OS. Use Release Page.".to_string();
            }
        } else {
            self.status_title = format!("You're Up to Date (v{VERSION} \"{CODENAME}\")");
            self.meta_text = format!(
                "Latest release on GitHub: v{}{}",
                rel.version, rel_codename_suffix
            );
            self.progress_text = "kTap is running the latest version.".to_string();
        }
    }

    fn start_check(&mut self) {
        if self.is_checking || self.is_downloading {
            return;
        }
        self.is_checking = true;
        self.error_text.clear();
        self.status_title = "Checking for Updates...".to_string();
        self.progress_text = format!("Querying GitHub Releases ({DEFAULT_REPO})...");
        let tx = self.tx.clone();
        std::thread::Builder::new()
            .name("kTap-UpdateCheck".to_string())
            .spawn(
                move || match fetch_latest_release(Some(VERSION), Some(DEFAULT_REPO), 8.0) {
                    Ok(rel) => {
                        let _ = tx.send(UpdateMsg::CheckOk(Box::new(rel)));
                    }
                    Err(err) => {
                        let _ = tx.send(UpdateMsg::CheckErr(err.to_string()));
                    }
                },
            )
            .ok();
    }

    fn start_download(&mut self) {
        let Some(rel) = self.release.clone() else {
            return;
        };
        if self.is_downloading || self.is_checking || rel.download_url.is_none() {
            return;
        }
        self.is_downloading = true;
        self.cancel_flag.store(false, Ordering::SeqCst);
        self.progress_frac = 0.0;
        self.error_text.clear();
        self.progress_text = "Starting secure HTTPS download...".to_string();

        let updates_dir = get_config_dir(None).join("updates");
        let filename = rel
            .asset_name
            .clone()
            .unwrap_or_else(|| format!("kTap-v{}.bin", rel.version));
        let mut target_path = updates_dir.join(filename);
        if let Ok(cur_exe) = std::env::current_exe() {
            let same =
                std::fs::canonicalize(&cur_exe).ok() == std::fs::canonicalize(&target_path).ok();
            if same || cur_exe == target_path {
                let ext = target_path
                    .extension()
                    .and_then(|s| s.to_str())
                    .map(|s| format!(".{s}"))
                    .unwrap_or_else(|| ".bin".to_string());
                target_path = updates_dir.join(format!("kTap-update-v{}{ext}", rel.version));
            }
        }

        let tx = self.tx.clone();
        let cancel_flag = Arc::clone(&self.cancel_flag);
        std::thread::Builder::new()
            .name("kTap-UpdateDownload".to_string())
            .spawn(move || {
                let require_sha = rel.sha256.is_some();
                let res = download_update_asset(
                    &rel,
                    &target_path,
                    Some(|dl: u64, total: Option<u64>| {
                        let _ = tx.send(UpdateMsg::Progress(dl, total));
                    }),
                    Some(|| cancel_flag.load(Ordering::SeqCst)),
                    65_536,
                    30.0,
                    require_sha,
                );
                match res {
                    Ok(path) => {
                        let _ = tx.send(UpdateMsg::DownloadOk(path));
                    }
                    Err(e) if e.reason == "cancelled" => {
                        let _ = tx.send(UpdateMsg::Cancelled);
                    }
                    Err(e) => {
                        let _ = tx.send(UpdateMsg::DownloadErr(e.to_string()));
                    }
                }
            })
            .ok();
    }

    pub fn show(&mut self, ctx: &Context) -> UpdateDialogAction {
        let mut action = UpdateDialogAction::Open;

        while let Ok(msg) = self.rx.try_recv() {
            match msg {
                UpdateMsg::CheckOk(rel) => {
                    self.is_checking = false;
                    self.release = Some((*rel).clone());
                    self.apply_release(&rel);
                    action = UpdateDialogAction::Checked(rel);
                }
                UpdateMsg::CheckErr(err) => {
                    self.is_checking = false;
                    self.status_title = "Update Check Failed".to_string();
                    self.error_text = err;
                }
                UpdateMsg::Progress(dl, total) => {
                    if let Some(tot) = total {
                        if tot > 0 {
                            self.progress_frac = (dl as f32 / tot as f32).clamp(0.0, 1.0);
                            self.progress_text = format!(
                                "Downloading {:.1} / {:.1} MB ({:.0}%)",
                                dl as f64 / (1024.0 * 1024.0),
                                tot as f64 / (1024.0 * 1024.0),
                                self.progress_frac * 100.0
                            );
                        }
                    } else {
                        self.progress_text =
                            format!("Downloading {:.1} MB...", dl as f64 / (1024.0 * 1024.0));
                    }
                }
                UpdateMsg::DownloadOk(path) => {
                    self.is_downloading = false;
                    self.progress_frac = 1.0;
                    self.progress_text =
                        "SHA-256 verified. Applying update and restarting...".to_string();
                    match apply_update_and_restart(&path, false) {
                        Ok(_) => return UpdateDialogAction::Restarting,
                        Err(err) => {
                            self.error_text = format!("Failed to apply update: {err}");
                        }
                    }
                }
                UpdateMsg::DownloadErr(err) => {
                    self.is_downloading = false;
                    self.error_text = err;
                }
                UpdateMsg::Cancelled => {
                    self.is_downloading = false;
                    self.progress_frac = 0.0;
                    self.progress_text = "Download cancelled.".to_string();
                }
            }
        }

        let mut close_req = false;
        egui::Window::new("Software Update")
            .collapsible(false)
            .resizable(false)
            .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
            .fixed_size([520.0, 390.0])
            .frame(
                egui::Frame::window(&ctx.style())
                    .fill(BG_PANEL)
                    .stroke(Stroke::new(1.0_f32, BORDER_CONTROL))
                    .rounding(Rounding::same(12.0))
                    .inner_margin(egui::Margin::same(16.0)),
            )
            .show(ctx, |ui| {
                let title_color = if !self.error_text.is_empty() {
                    ACCENT_DANGER
                } else if self.release.as_ref().is_some_and(|r| r.is_update_available) {
                    ACCENT_SUCCESS
                } else {
                    TEXT_PRIMARY
                };
                ui.label(
                    RichText::new(&self.status_title)
                        .size(16.0)
                        .strong()
                        .color(title_color),
                );
                ui.label(
                    RichText::new(&self.meta_text)
                        .size(11.5)
                        .color(TEXT_SECONDARY),
                );
                ui.add_space(8.0);

                egui::Frame::none()
                    .fill(BG_CARD)
                    .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                    .rounding(Rounding::same(10.0))
                    .inner_margin(egui::Margin::same(10.0))
                    .show(ui, |ui| {
                        egui::ScrollArea::vertical()
                            .max_height(160.0)
                            .show(ui, |ui| {
                                ui.add(
                                    egui::Label::new(
                                        RichText::new(&self.notes_text)
                                            .size(12.0)
                                            .color(TEXT_SECONDARY),
                                    )
                                    .wrap(),
                                );
                            });
                    });

                ui.add_space(8.0);
                if self.is_downloading || self.progress_frac > 0.0 {
                    ui.add(egui::ProgressBar::new(self.progress_frac).fill(ACCENT_SUCCESS));
                }
                if !self.progress_text.is_empty() {
                    ui.label(RichText::new(&self.progress_text).size(11.5).color(
                        if self.is_downloading {
                            TEXT_PRIMARY
                        } else {
                            TEXT_SECONDARY
                        },
                    ));
                }
                if !self.error_text.is_empty() {
                    ui.label(
                        RichText::new(&self.error_text)
                            .size(11.5)
                            .color(ACCENT_DANGER),
                    );
                }

                ui.add_space(10.0);
                ui.horizontal(|ui| {
                    if styled_button(ui, "Check Again", BTN_NEUTRAL, TEXT_PRIMARY, BORDER_CONTROL)
                        .clicked()
                    {
                        self.start_check();
                    }
                    if let Some(url) = self
                        .release
                        .as_ref()
                        .map(|r| r.html_url.as_str())
                        .filter(|u| !u.is_empty())
                    {
                        if styled_button(
                            ui,
                            "Release Page \u{2197}",
                            BTN_NEUTRAL,
                            TEXT_SECONDARY,
                            BORDER_CONTROL,
                        )
                        .clicked()
                        {
                            let _ = open::that(url);
                        }
                    }

                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let can_install = !self.is_checking
                            && !self.is_downloading
                            && self
                                .release
                                .as_ref()
                                .is_some_and(|r| r.is_update_available && r.download_url.is_some());
                        let install_lbl = if self.is_downloading {
                            "Downloading..."
                        } else if self.release.as_ref().is_some_and(|r| r.is_update_available) {
                            "Update Now"
                        } else {
                            "Up to Date"
                        };
                        let (fill, fg, stroke) = if can_install {
                            (CTA_SUCCESS, CTA_TEXT, CTA_SUCCESS)
                        } else {
                            (BG_ELEVATED, TEXT_MUTED, BORDER_SUBTLE)
                        };
                        if ui
                            .add_enabled(
                                can_install,
                                egui::Button::new(
                                    RichText::new(install_lbl).color(fg).strong().size(12.0),
                                )
                                .fill(fill)
                                .stroke(Stroke::new(1.0_f32, stroke))
                                .rounding(Rounding::same(7.0)),
                            )
                            .clicked()
                        {
                            self.start_download();
                        }

                        let cancel_lbl = if self.is_downloading {
                            "Cancel Download"
                        } else {
                            "Close"
                        };
                        if styled_button(ui, cancel_lbl, BTN_NEUTRAL, TEXT_PRIMARY, BORDER_CONTROL)
                            .clicked()
                        {
                            if self.is_downloading {
                                self.cancel_flag.store(true, Ordering::SeqCst);
                            } else {
                                close_req = true;
                            }
                        }
                    });
                });
                let _ = ACCENT_WARNING;
            });

        if close_req || ctx.input(|i| i.key_pressed(egui::Key::Escape)) && !self.is_downloading {
            return UpdateDialogAction::Close;
        }
        action
    }
}
