//! Pure Rust `eframe/egui` Craft Obsidian & Mint Emerald Desktop GUI for kTap.

use std::collections::BTreeMap;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use chrono::Local;
use egui::{Color32, Context, RichText, Rounding, Stroke, Vec2, ViewportCommand};

use crate::config::{
    delete_custom_preset, load_active_profile_backup, load_app_config, load_custom_presets,
    save_active_profile_backup, save_app_config, save_custom_preset, AppConfig,
};
use crate::engine::backend::{DryRunBackend, InputBackend};
use crate::engine::executor::{ExecutionTelemetry, ExecutorState, MacroExecutor};
use crate::engine::hotkeys::GlobalHotkeyManager;
use crate::engine::platform::{
    create_os_input_backend, get_platform_capabilities, platform_badge_label,
    platform_get_cursor_pos, platform_supports_key_polling, wayland_udev_setup_command,
    PlatformInputCapabilities,
};
use crate::engine::recorder::{MacroRecorder, RecorderConfig};
use crate::models::action::{
    make_key_press, make_mouse_click, make_mouse_move, make_mouse_scroll, make_wait, ActionType,
    ClickType, MacroAction, MouseButton, MoveMode,
};
use crate::models::dsl::{format_actions_to_dsl, parse_quick_expression};
use crate::models::keymap::{format_egui_key_combo, list_common_keys, normalize_key_combo};
use crate::models::profile::{
    create_default_profile, get_builtin_presets, MacroProfile, RepeatMode,
};
use crate::security::auditor::{audit_profile, AuditReport, AuditSeverity};
use crate::security::integrity::{
    compute_payload_sha256, load_profile_secure, save_profile_secure,
};
use crate::security::validator::validate_profile_complexity;
use crate::ui::mini_hud::{show_mini_hud, MiniHudAction};
use crate::ui::step_dialog::{StepDialogOutcome, StepEditorState};
use crate::ui::theme::{
    action_visual_style, apply_theme, apply_windows_window_branding, card_frame, ghost_button,
    load_brand_texture, load_window_icon_data, micro_heading, panel_frame, pill_badge,
    styled_button, ACCENT_DANGER, ACCENT_PRIMARY, ACCENT_SUCCESS, ACCENT_WARNING, BADGE_CRIT_BG,
    BADGE_CRIT_FG, BADGE_OK_BG, BADGE_WARN_BG, BADGE_WARN_FG, BANNER_ERR_BG, BANNER_ERR_BORDER,
    BANNER_ERR_FG, BANNER_INFO_BG, BANNER_INFO_BORDER, BANNER_INFO_FG, BANNER_UPDATE_BG,
    BANNER_UPDATE_BORDER, BANNER_UPDATE_FG, BANNER_WARN_BG, BANNER_WARN_BORDER, BANNER_WARN_FG,
    BG_CARD, BG_CARD_ACTIVE, BG_CARD_HOVER, BG_ELEVATED, BG_INPUT, BG_PANEL, BG_ROOT,
    BORDER_CONTROL, BORDER_FOCUS, BORDER_SUBTLE, BTN_DANGER_BORDER, BTN_DANGER_SUBTLE,
    BTN_DANGER_SUBTLE_TEXT, BTN_NEUTRAL, BTN_SUBTLE, CTA_SUCCESS, CTA_TEXT, SEGMENT_ACTIVE,
    TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY,
};
use crate::ui::update_dialog::{UpdateDialogAction, UpdateDialogState};
use crate::updater::{fetch_latest_release, ReleaseInfo};
use crate::{full_version_label, VERSION};

const MAX_UNDO_HISTORY: usize = 40;
const MAX_CONSOLE_LINES: usize = 200;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UiMode {
    Simple,
    Studio,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SimpleTask {
    Spammer,
    Sequence,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StepFilter {
    All,
    Key,
    Wait,
    Mouse,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InspectorTab {
    LoopKeys,
    Recorder,
    Security,
    Console,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ToastLevel {
    Info,
    Success,
    Warning,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum KeyCaptureTarget {
    SimpleSpammer,
    HotkeyStart,
    HotkeyRec,
    HotkeyPanic,
}

struct ToastState {
    message: String,
    level: ToastLevel,
    expires_at: Instant,
}

pub struct KTapApp {
    app_config: AppConfig,
    profile: MacroProfile,
    presets: Vec<MacroProfile>,
    builtin_names: std::collections::HashSet<String>,

    os_backend: Arc<dyn InputBackend>,
    dry_run_backend: Arc<DryRunBackend>,
    platform_caps: PlatformInputCapabilities,
    executor: MacroExecutor,
    recorder: MacroRecorder,
    hotkey_manager: GlobalHotkeyManager,
    hotkey_rx: mpsc::Receiver<&'static str>,
    hotkey_tx: mpsc::Sender<&'static str>,
    egui_ctx_ref: Arc<Mutex<Option<Context>>>,

    ui_mode: UiMode,
    simple_task: SimpleTask,
    dry_run: bool,
    check_updates_on_startup: bool,
    countdown_sec: i64,
    countdown_deadline: Option<Instant>,
    mini_hud_active: bool,

    // Simple Mode editable controls
    ss_key: String,
    ss_interval_ms: i64,
    ss_jitter_enabled: bool,
    ss_slider_dirty_at: Option<Instant>,

    // Shared & Studio editable buffers
    repeat_val_str: String,
    cycle_delay_str: String,
    hk_start_str: String,
    hk_rec_str: String,
    hk_panic_str: String,
    dsl_input: String,
    filter_query: String,
    focus_filter_requested: bool,
    step_filter: StepFilter,
    inspector_tab: InspectorTab,
    selected_step_index: Option<usize>,

    // Recorder options
    rec_delays: bool,
    rec_merge_taps: bool,
    rec_capture_coords: bool,

    // Undo / Redo
    undo_stack: Vec<Vec<MacroAction>>,
    redo_stack: Vec<Vec<MacroAction>>,

    // Cached cycle & audit metrics
    cached_enabled_count: usize,
    cached_cycle_wait_ms: i64,
    last_audit: AuditReport,
    cached_sha256: String,
    cached_nodes: usize,
    cached_ops: usize,
    security_dirty: bool,
    last_logged_finding_codes: Vec<String>,
    last_telem_error: Option<String>,

    // Telemetry & Console
    cursor_xy: (i64, i64),
    console_lines: Vec<String>,
    footer_status: String,
    toast: Option<ToastState>,
    key_capture_target: Option<KeyCaptureTarget>,

    // Modals & Updates
    step_dialog: Option<StepEditorState>,
    update_dialog: Option<UpdateDialogState>,
    save_preset_modal: Option<String>,
    latest_release: Option<ReleaseInfo>,
    update_banner_dismissed: bool,
    platform_notice_dismissed: bool,
    startup_update_checking: bool,
    startup_update_rx: mpsc::Receiver<Option<ReleaseInfo>>,

    // Visuals
    brand_tex: Option<egui::TextureHandle>,
    branded_window: bool,
    last_cursor_poll: Instant,
}

impl KTapApp {
    pub fn run() -> eframe::Result<()> {
        let mut vp = egui::ViewportBuilder::default()
            .with_inner_size([1140.0, 720.0])
            .with_min_inner_size([920.0, 620.0]);
        if let Some(icon) = load_window_icon_data() {
            vp = vp.with_icon(icon);
        }
        let options = eframe::NativeOptions {
            viewport: vp,
            ..Default::default()
        };
        let title = format!("kTap {}", full_version_label());
        eframe::run_native(
            &title,
            options,
            Box::new(|cc| {
                apply_theme(&cc.egui_ctx);
                Ok(Box::new(Self::new(cc.egui_ctx.clone(), true)))
            }),
        )
    }

    pub fn new(ctx: Context, check_updates: bool) -> Self {
        let app_config = load_app_config(None);
        let (presets, builtin_names) = Self::build_presets_catalog();

        let mut profile = if app_config.auto_save_active_profile {
            load_active_profile_backup(None).unwrap_or_else(create_default_profile)
        } else {
            let mut p = create_default_profile();
            p.start_stop_hotkey = app_config.start_stop_hotkey.clone();
            p.record_hotkey = app_config.record_hotkey.clone();
            p.panic_hotkey = app_config.panic_hotkey.clone();
            let _ = p.validate();
            p
        };
        let _ = profile.validate();

        let os_backend = create_os_input_backend();
        let dry_run_backend = Arc::new(DryRunBackend::default());
        let platform_caps = get_platform_capabilities();
        let executor = MacroExecutor::new(Arc::clone(&os_backend), 1.0, None);
        let recorder = MacroRecorder::new(RecorderConfig::default());
        let hotkey_manager = GlobalHotkeyManager::default();
        let (hotkey_tx, hotkey_rx) = mpsc::channel();
        let egui_ctx_ref = Arc::new(Mutex::new(Some(ctx)));

        let ui_mode = if app_config.ui_mode == "studio" {
            UiMode::Studio
        } else {
            UiMode::Simple
        };
        let simple_task = if app_config.simple_task == "sequence" {
            SimpleTask::Sequence
        } else {
            SimpleTask::Spammer
        };

        let dsl_input = format_actions_to_dsl(&profile.actions);
        let repeat_val_str = if profile.repeat_mode == RepeatMode::Duration {
            profile.repeat_duration_s.to_string()
        } else {
            profile.repeat_count.to_string()
        };
        let cycle_delay_str = profile.inter_cycle_delay_ms.to_string();
        let hk_start_str = profile.start_stop_hotkey.clone();
        let hk_rec_str = profile.record_hotkey.clone();
        let hk_panic_str = profile.panic_hotkey.clone();

        let (startup_tx, startup_update_rx) = mpsc::channel();
        let start_update_check = check_updates && app_config.check_updates_on_startup;

        let mut app = Self {
            ss_key: app_config.simple_spammer.key.clone(),
            cached_enabled_count: profile.actions.iter().filter(|a| a.enabled).count(),
            cached_cycle_wait_ms: profile.total_cycle_wait_ms(),
            ss_interval_ms: app_config.simple_spammer.interval_ms.clamp(25, 2000),
            ss_jitter_enabled: app_config.simple_spammer.jitter_enabled,
            ss_slider_dirty_at: None,
            dry_run: app_config.dry_run,
            check_updates_on_startup: app_config.check_updates_on_startup,
            countdown_sec: app_config.start_countdown_sec.clamp(0, 10),
            countdown_deadline: None,
            mini_hud_active: false,
            rec_delays: app_config.recorder_settings.record_delays,
            rec_merge_taps: app_config.recorder_settings.merge_key_taps,
            rec_capture_coords: app_config.recorder_settings.capture_click_coordinates,
            app_config,
            profile,
            presets,
            builtin_names,
            os_backend,
            dry_run_backend,
            platform_caps,
            executor,
            recorder,
            hotkey_manager,
            hotkey_rx,
            hotkey_tx,
            egui_ctx_ref,
            ui_mode,
            simple_task,
            repeat_val_str,
            cycle_delay_str,
            hk_start_str,
            hk_rec_str,
            hk_panic_str,
            dsl_input,
            filter_query: String::new(),
            focus_filter_requested: false,
            step_filter: StepFilter::All,
            inspector_tab: InspectorTab::LoopKeys,
            selected_step_index: None,
            undo_stack: Vec::new(),
            redo_stack: Vec::new(),
            last_audit: AuditReport::default(),
            cached_sha256: String::new(),
            cached_nodes: 0,
            cached_ops: 0,
            security_dirty: true,
            last_logged_finding_codes: Vec::new(),
            last_telem_error: None,
            cursor_xy: (0, 0),
            console_lines: Vec::new(),
            footer_status: format!("\u{2022} Ready \u{2014} kTap {}", full_version_label()),
            toast: None,
            key_capture_target: None,
            step_dialog: None,
            update_dialog: None,
            save_preset_modal: None,
            latest_release: None,
            update_banner_dismissed: false,
            platform_notice_dismissed: false,
            startup_update_checking: start_update_check,
            startup_update_rx,
            brand_tex: None,
            branded_window: false,
            last_cursor_poll: Instant::now() - Duration::from_secs(10),
        };

        if !app.profile.actions.is_empty() {
            app.selected_step_index = Some(0);
        }
        app.sync_profile_to_simple_spammer_fields();
        app.run_security_audit();
        app.log_event(&format!(
            "kTap {} ({} \u{00B7} {}) ready. Panic Stop armed on [{}].",
            full_version_label(),
            platform_badge_label(),
            app.platform_caps.backend_name,
            app.profile.panic_hotkey
        ));

        app.rebind_hotkeys();
        if platform_supports_key_polling() {
            app.hotkey_manager.start();
        }

        if start_update_check {
            std::thread::Builder::new()
                .name("kTap-StartupUpdate".to_string())
                .spawn(move || {
                    let found = fetch_latest_release(Some(VERSION), None, 8.0)
                        .ok()
                        .filter(|r| r.is_update_available);
                    let _ = startup_tx.send(found);
                })
                .ok();
        }

        app
    }

    fn build_presets_catalog() -> (Vec<MacroProfile>, std::collections::HashSet<String>) {
        let builtins = get_builtin_presets();
        let builtin_names = builtins.iter().map(|p| p.name.clone()).collect();
        let mut by_name: BTreeMap<String, MacroProfile> = BTreeMap::new();
        let mut ordered = Vec::new();
        for p in builtins {
            by_name.insert(p.name.clone(), p.clone());
            ordered.push(p);
        }
        for (name, custom) in load_custom_presets(None) {
            if let Some(pos) = ordered.iter().position(|p| p.name == name) {
                ordered[pos] = custom;
            } else {
                ordered.push(custom);
            }
        }
        (ordered, builtin_names)
    }

    fn reload_presets_catalog(&mut self) {
        let (p, b) = Self::build_presets_catalog();
        self.presets = p;
        self.builtin_names = b;
    }

    fn show_toast(&mut self, msg: impl Into<String>, level: ToastLevel, secs: u64) {
        self.toast = Some(ToastState {
            message: msg.into(),
            level,
            expires_at: Instant::now() + Duration::from_secs(secs),
        });
    }

    fn log_event(&mut self, msg: &str) {
        let ts = Local::now().format("%H:%M:%S").to_string();
        self.console_lines.push(format!("[{ts}] {msg}"));
        if self.console_lines.len() > MAX_CONSOLE_LINES {
            let excess = self.console_lines.len() - MAX_CONSOLE_LINES;
            self.console_lines.drain(0..excess);
        }
        self.footer_status = format!("\u{2022} [{ts}] {msg}");
    }

    fn save_persistent_state(&mut self) {
        self.app_config.ui_mode = match self.ui_mode {
            UiMode::Simple => "simple".to_string(),
            UiMode::Studio => "studio".to_string(),
        };
        self.app_config.simple_task = match self.simple_task {
            SimpleTask::Spammer => "spammer".to_string(),
            SimpleTask::Sequence => "sequence".to_string(),
        };
        self.app_config.dry_run = self.dry_run;
        self.app_config.check_updates_on_startup = self.check_updates_on_startup;
        self.app_config.start_countdown_sec = self.countdown_sec;
        self.app_config.start_stop_hotkey = self.profile.start_stop_hotkey.clone();
        self.app_config.record_hotkey = self.profile.record_hotkey.clone();
        self.app_config.panic_hotkey = self.profile.panic_hotkey.clone();
        self.app_config.simple_spammer.key = self.ss_key.trim().to_string();
        self.app_config.simple_spammer.interval_ms = self.ss_interval_ms;
        self.app_config.simple_spammer.jitter_enabled = self.ss_jitter_enabled;
        self.app_config.recorder_settings.record_delays = self.rec_delays;
        self.app_config.recorder_settings.merge_key_taps = self.rec_merge_taps;
        self.app_config.recorder_settings.capture_click_coordinates = self.rec_capture_coords;

        let _ = save_app_config(&self.app_config, None);
        if self.app_config.auto_save_active_profile {
            let _ = save_active_profile_backup(&mut self.profile, None);
        }
    }

    fn rebind_hotkeys(&self) {
        self.hotkey_manager.clear();

        let tx1 = self.hotkey_tx.clone();
        let ctx1 = Arc::clone(&self.egui_ctx_ref);
        let _ = self.hotkey_manager.register(
            "start_stop",
            &self.profile.start_stop_hotkey,
            move || {
                let _ = tx1.send("start_stop");
                if let Some(c) = ctx1.lock().expect("ctx lock").as_ref() {
                    c.request_repaint();
                }
            },
        );

        let tx2 = self.hotkey_tx.clone();
        let ctx2 = Arc::clone(&self.egui_ctx_ref);
        let _ = self
            .hotkey_manager
            .register("record", &self.profile.record_hotkey, move || {
                let _ = tx2.send("record");
                if let Some(c) = ctx2.lock().expect("ctx lock").as_ref() {
                    c.request_repaint();
                }
            });

        // Panic runs synchronously on the hotkey thread so stuck inputs release in <5ms
        let tx3 = self.hotkey_tx.clone();
        let ctx3 = Arc::clone(&self.egui_ctx_ref);
        let _ = self
            .hotkey_manager
            .register("panic", &self.profile.panic_hotkey, move || {
                let _ = tx3.send("panic");
                if let Some(c) = ctx3.lock().expect("ctx lock").as_ref() {
                    c.request_repaint();
                }
            });
    }

    fn sync_profile_to_simple_spammer_fields(&mut self) {
        let acts = &self.profile.actions;
        if acts.len() == 2
            && acts[0].enabled
            && acts[1].enabled
            && acts[0].action_type == ActionType::KeyPress
            && acts[1].action_type == ActionType::Wait
        {
            self.simple_task = SimpleTask::Spammer;
            self.ss_key = acts[0].key.clone();
            self.ss_interval_ms = (acts[0].hold_ms + acts[1].wait_ms).clamp(25, 2000);
            self.ss_jitter_enabled = acts[1].jitter_ms > 0;
        } else {
            self.simple_task = SimpleTask::Sequence;
        }
    }

    fn sync_profile_settings_to_ui(&mut self) {
        self.repeat_val_str = if self.profile.repeat_mode == RepeatMode::Duration {
            self.profile.repeat_duration_s.to_string()
        } else {
            self.profile.repeat_count.to_string()
        };
        self.cycle_delay_str = self.profile.inter_cycle_delay_ms.to_string();
        self.hk_start_str = self.profile.start_stop_hotkey.clone();
        self.hk_rec_str = self.profile.record_hotkey.clone();
        self.hk_panic_str = self.profile.panic_hotkey.clone();
    }

    fn sync_ui_settings_to_profile(&mut self) -> bool {
        let parse_res: Result<(), String> = (|| {
            match self.profile.repeat_mode {
                RepeatMode::Duration => {
                    let dur: f64 =
                        self.repeat_val_str.trim().parse().map_err(|_| {
                            "Repeat duration must be a number (seconds).".to_string()
                        })?;
                    self.profile.repeat_duration_s = dur;
                }
                RepeatMode::Count => {
                    let val: f64 = self
                        .repeat_val_str
                        .trim()
                        .parse()
                        .map_err(|_| "Repeat count must be an integer.".to_string())?;
                    self.profile.repeat_count = val as i64;
                }
                RepeatMode::Infinite => {}
            }
            self.profile.inter_cycle_delay_ms = self
                .cycle_delay_str
                .trim()
                .parse()
                .map_err(|_| "Inter-cycle delay must be an integer (ms).".to_string())?;

            let s = self.hk_start_str.trim();
            let r = self.hk_rec_str.trim();
            let p = self.hk_panic_str.trim();
            self.profile.start_stop_hotkey = if s.is_empty() { "F6" } else { s }.to_string();
            self.profile.record_hotkey = if r.is_empty() { "F7" } else { r }.to_string();
            self.profile.panic_hotkey = if p.is_empty() { "F12" } else { p }.to_string();
            self.profile.validate()?;
            Ok(())
        })();

        match parse_res {
            Ok(()) => {
                self.sync_profile_settings_to_ui();
                self.rebind_hotkeys();
                self.run_security_audit();
                true
            }
            Err(err) => {
                self.log_event(&format!("[Settings Error] {err}"));
                self.show_toast(format!("Settings Error: {err}"), ToastLevel::Error, 6);
                false
            }
        }
    }

    fn apply_simple_spammer_to_profile(&mut self, record_undo: bool) -> bool {
        let key_norm = match normalize_key_combo(&self.ss_key) {
            Ok(k) => k,
            Err(e) => {
                self.show_toast(format!("Invalid target key: {e}"), ToastLevel::Error, 5);
                return false;
            }
        };
        self.ss_key = key_norm.clone();
        let interval = self.ss_interval_ms.clamp(25, 2000);
        let hold_ms = 25.min(interval - 5);
        let wait_ms = (interval - hold_ms).max(5);
        let jitter_ms = if self.ss_jitter_enabled {
            ((interval as f64 * 0.1).round() as i64).clamp(1, 15)
        } else {
            0
        };

        let Ok(act1) = make_key_press(&key_norm, hold_ms, &format!("Tap {key_norm}")) else {
            return false;
        };
        let Ok(act2) = make_wait(wait_ms, jitter_ms, "Repeat Interval") else {
            return false;
        };

        if record_undo {
            self.record_undo();
        }
        self.profile.name = format!("Key Spammer ({key_norm})");
        self.profile.inter_cycle_delay_ms = 0;
        self.cycle_delay_str = "0".to_string();
        self.profile.actions = vec![act1, act2];
        let _ = self.profile.validate();
        self.refresh_cached_metrics();
        self.dsl_input = format_actions_to_dsl(&self.profile.actions);
        self.run_security_audit();
        true
    }

    fn refresh_cached_metrics(&mut self) {
        self.cached_enabled_count = self.profile.actions.iter().filter(|a| a.enabled).count();
        self.cached_cycle_wait_ms = self.profile.total_cycle_wait_ms();
    }

    fn record_undo(&mut self) {
        self.undo_stack.push(self.profile.actions.clone());
        if self.undo_stack.len() > MAX_UNDO_HISTORY {
            self.undo_stack.remove(0);
        }
        self.redo_stack.clear();
    }

    fn on_sequence_mutated(&mut self, sync_simple: bool) {
        self.refresh_cached_metrics();
        self.dsl_input = format_actions_to_dsl(&self.profile.actions);
        self.run_security_audit();
        if sync_simple {
            self.sync_profile_to_simple_spammer_fields();
        }
    }

    fn undo(&mut self) {
        if let Some(prev) = self.undo_stack.pop() {
            self.redo_stack.push(self.profile.actions.clone());
            self.profile.actions = prev;
            self.on_sequence_mutated(true);
            self.log_event(&format!(
                "Undid last change ({} steps active).",
                self.profile.actions.len()
            ));
        } else {
            self.log_event("Undo history is empty.");
        }
    }

    fn redo(&mut self) {
        if let Some(next) = self.redo_stack.pop() {
            self.undo_stack.push(self.profile.actions.clone());
            self.profile.actions = next;
            self.on_sequence_mutated(true);
            self.log_event(&format!(
                "Redid change ({} steps active).",
                self.profile.actions.len()
            ));
        } else {
            self.log_event("Redo history is empty.");
        }
    }

    fn scale_wait_times(&mut self, factor: f64) {
        if self.profile.actions.is_empty() {
            return;
        }
        self.record_undo();
        fn scale_list(actions: &mut [MacroAction], factor: f64) -> usize {
            let mut count = 0;
            for act in actions {
                if act.action_type == ActionType::Wait {
                    act.wait_ms =
                        ((act.wait_ms as f64 * factor).round() as i64).clamp(5, 3_600_000);
                    act.jitter_ms =
                        ((act.jitter_ms as f64 * factor).round() as i64).clamp(0, 60_000);
                    count += 1;
                } else if act.action_type == ActionType::RepeatBlock {
                    count += scale_list(&mut act.sub_actions, factor);
                }
            }
            count
        }
        let n = scale_list(&mut self.profile.actions, factor);
        self.on_sequence_mutated(true);
        let desc = if factor < 1.0 {
            "2x faster"
        } else {
            "0.5x slower"
        };
        self.log_event(&format!("Scaled {n} wait step(s) ({desc})."));
    }

    fn run_security_audit(&mut self) {
        let report = audit_profile(&self.profile);
        self.last_audit = report.clone();
        self.security_dirty = true;
        if self.ui_mode == UiMode::Studio && self.inspector_tab == InspectorTab::Security {
            self.refresh_security_details();
        }
        let active_codes: Vec<String> = report
            .findings
            .iter()
            .filter(|f| matches!(f.severity, AuditSeverity::Warning | AuditSeverity::Critical))
            .map(|f| f.code.clone())
            .collect();
        if active_codes != self.last_logged_finding_codes {
            self.last_logged_finding_codes = active_codes;
            for f in &report.findings {
                if matches!(f.severity, AuditSeverity::Warning | AuditSeverity::Critical) {
                    self.log_event(&format!("[{}] {}: {}", f.code, f.title, f.description));
                }
            }
        }
    }

    fn refresh_security_details(&mut self) {
        if let Ok(digest) = compute_payload_sha256(&self.profile.to_payload_value()) {
            self.cached_sha256 = digest;
        }
        if let Ok((n, ops)) = validate_profile_complexity(&mut self.profile) {
            self.cached_nodes = n;
            self.cached_ops = ops;
        }
        self.security_dirty = false;
    }

    fn apply_dsl(&mut self, append: bool) -> bool {
        match parse_quick_expression(self.dsl_input.trim()) {
            Ok(mut parsed) => {
                self.record_undo();
                if append {
                    self.profile.actions.append(&mut parsed);
                } else {
                    self.profile.actions = parsed;
                }
                self.on_sequence_mutated(true);
                let verb = if append { "Appended" } else { "Forged" };
                self.log_event(&format!("{verb} steps from Quick-Forge recipe."));
                true
            }
            Err(err) => {
                self.log_event(&format!("[DSL Error] {err}"));
                self.show_toast(format!("DSL Error: {err}"), ToastLevel::Error, 6);
                false
            }
        }
    }

    fn toggle_execution(&mut self) {
        if self.countdown_deadline.is_some() {
            self.countdown_deadline = None;
            self.log_event("Start countdown cancelled.");
            return;
        }
        if self.executor.is_running() {
            self.executor.stop(Duration::from_secs(2));
            self.hotkey_manager.set_active_mode(false);
            self.log_event("Macro execution stopped.");
            return;
        }
        if self.recorder.is_recording() {
            self.show_toast(
                "Cannot start macro while Live Recorder is active.",
                ToastLevel::Warning,
                4,
            );
            return;
        }
        if self.ui_mode == UiMode::Simple
            && self.simple_task == SimpleTask::Spammer
            && !self.apply_simple_spammer_to_profile(false)
        {
            return;
        }
        if !self.profile.actions.iter().any(|a| a.enabled) {
            self.show_toast(
                "No enabled macro steps to execute. Add or enable at least one step.",
                ToastLevel::Warning,
                4,
            );
            return;
        }
        if !self.sync_ui_settings_to_profile() {
            return;
        }

        self.last_telem_error = None;
        if self.countdown_sec > 0 {
            self.countdown_deadline =
                Some(Instant::now() + Duration::from_secs(self.countdown_sec as u64));
            self.log_event(&format!("Starting macro in {}s...", self.countdown_sec));
        } else {
            self.launch_executor_now();
        }
    }

    fn launch_executor_now(&mut self) {
        self.countdown_deadline = None;
        if self.dry_run {
            self.dry_run_backend.clear();
            let b: Arc<dyn InputBackend> = self.dry_run_backend.clone();
            let _ = self.executor.set_backend(b);
        } else {
            let _ = self.executor.set_backend(Arc::clone(&self.os_backend));
            self.platform_caps = get_platform_capabilities();
            if !self.platform_caps.can_inject && !self.platform_caps.notice.is_empty() {
                let notice = self.platform_caps.notice.clone();
                self.show_toast(&notice, ToastLevel::Warning, 7);
                self.log_event(&format!("[{}] {notice}", self.platform_caps.backend_name));
            }
        }

        match self.executor.start(self.profile.clone()) {
            Ok(true) => {
                self.hotkey_manager.set_active_mode(true);
                let mode_lbl = if self.dry_run {
                    "DRY-RUN SIMULATION"
                } else {
                    self.platform_caps.backend_name
                };
                self.log_event(&format!(
                    "Started macro [{}] in {mode_lbl} mode ({}).",
                    self.profile.name,
                    self.profile.repeat_mode.as_str()
                ));
            }
            Ok(false) => {}
            Err(err) => {
                self.show_toast(format!("Start Error: {err}"), ToastLevel::Error, 6);
                self.log_event(&format!("[Start Error] {err}"));
            }
        }
    }

    fn trigger_panic_stop(&mut self) {
        self.countdown_deadline = None;
        if self.recorder.is_recording() {
            let _ = self.recorder.stop();
        }
        self.executor.panic_abort(Duration::from_millis(200));
        self.hotkey_manager.set_active_mode(false);
        self.log_event("PANIC STOP TRIGGERED! Execution aborted and all held inputs released.");
        self.show_toast(
            "Panic Stop triggered \u{2014} all inputs released.",
            ToastLevel::Warning,
            5,
        );
    }

    fn toggle_recording(&mut self) {
        if self.executor.is_running() {
            self.show_toast(
                "Stop macro execution before starting Live Recorder.",
                ToastLevel::Warning,
                4,
            );
            return;
        }
        if self.recorder.is_recording() {
            let recorded = self.recorder.stop();
            self.hotkey_manager.set_active_mode(false);
            if !recorded.is_empty() {
                let n = recorded.len();
                self.record_undo();
                self.profile.actions = recorded;
                self.on_sequence_mutated(true);
                self.log_event(&format!("Live Recording complete: captured {n} step(s)."));
                self.show_toast(
                    format!("Captured {n} recorded step(s)."),
                    ToastLevel::Success,
                    4,
                );
            } else {
                self.log_event("Live Recording stopped (0 inputs captured).");
            }
        } else {
            self.platform_caps = get_platform_capabilities();
            if (!self.platform_caps.can_poll_keys || self.platform_caps.requires_permission)
                && !self.platform_caps.notice.is_empty()
            {
                let notice = self.platform_caps.notice.clone();
                self.show_toast(&notice, ToastLevel::Warning, 7);
                self.log_event(&format!("[{}] {notice}", self.platform_caps.backend_name));
            }
            self.recorder.set_config(RecorderConfig {
                record_delays: self.rec_delays,
                merge_key_taps: self.rec_merge_taps,
                capture_click_coordinates: self.rec_capture_coords,
                ..RecorderConfig::default()
            });
            self.recorder.set_ignored_hotkeys(&[
                &self.profile.record_hotkey,
                &self.profile.panic_hotkey,
                &self.profile.start_stop_hotkey,
            ]);
            self.recorder.start(true);
            self.hotkey_manager.set_active_mode(true);
            self.log_event("Live Recording started... Press keys or click mouse.");
        }
    }

    fn open_profile_dialog(&mut self) {
        if let Some(path) = rfd::FileDialog::new()
            .set_title("Open kTap Macro Profile")
            .add_filter("kTap / KeyForge Macro", &["json", "ktap", "kfmacro"])
            .pick_file()
        {
            if self.executor.is_running() {
                self.executor.stop(Duration::from_secs(2));
            }
            match load_profile_secure(&path, false) {
                Ok((loaded, sig_ok)) => {
                    self.filter_query.clear();
                    self.step_filter = StepFilter::All;
                    self.record_undo();
                    self.profile = loaded;
                    self.app_config.last_profile_path = Some(path.to_string_lossy().to_string());
                    self.sync_profile_settings_to_ui();
                    self.rebind_hotkeys();
                    self.on_sequence_mutated(true);
                    let sig = if sig_ok {
                        "SHA-256 Verified"
                    } else {
                        "Unsigned JSON (Schema Validated)"
                    };
                    self.log_event(&format!("Loaded profile [{}] ({sig}).", self.profile.name));
                    self.show_toast(
                        format!("Loaded profile: {}", self.profile.name),
                        ToastLevel::Success,
                        4,
                    );
                }
                Err(err) => {
                    self.log_event(&format!("[Load Error] {err}"));
                    self.show_toast(format!("Load Error: {err}"), ToastLevel::Error, 6);
                }
            }
        }
    }

    fn save_profile_dialog(&mut self) {
        if let Some(path) = rfd::FileDialog::new()
            .set_title("Save kTap Macro Profile")
            .set_file_name("my_macro.ktap.json")
            .add_filter("kTap Macro JSON", &["json"])
            .save_file()
        {
            self.sync_ui_settings_to_profile();
            match save_profile_secure(&mut self.profile, &path) {
                Ok(saved) => {
                    self.app_config.last_profile_path = Some(saved.to_string_lossy().to_string());
                    self.save_persistent_state();
                    let fname = saved
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("profile");
                    self.log_event(&format!("Saved SHA-256 signed profile to: {fname}"));
                    self.show_toast(format!("Saved profile: {fname}"), ToastLevel::Success, 4);
                }
                Err(err) => {
                    self.log_event(&format!("[Save Error] {err}"));
                    self.show_toast(format!("Save Error: {err}"), ToastLevel::Error, 6);
                }
            }
        }
    }

    fn new_profile(&mut self) {
        if self.executor.is_running() {
            self.executor.stop(Duration::from_secs(2));
        }
        self.filter_query.clear();
        self.step_filter = StepFilter::All;
        self.record_undo();
        self.profile = MacroProfile {
            name: "Custom Macro Profile".to_string(),
            description: "Created in kTap".to_string(),
            repeat_mode: RepeatMode::Count,
            repeat_count: 5,
            inter_cycle_delay_ms: 100,
            actions: Vec::new(),
            ..MacroProfile::default()
        };
        self.sync_profile_settings_to_ui();
        self.rebind_hotkeys();
        self.on_sequence_mutated(true);
        self.log_event("Created new blank profile.");
    }

    fn visible_step_indices(&self) -> Vec<usize> {
        let trimmed = self.filter_query.trim();
        let q = if trimmed.is_empty() {
            String::new()
        } else {
            trimmed.to_ascii_lowercase()
        };
        self.profile
            .actions
            .iter()
            .enumerate()
            .filter(|(_, act)| {
                let type_ok = match self.step_filter {
                    StepFilter::All => true,
                    StepFilter::Key => matches!(
                        act.action_type,
                        ActionType::KeyPress | ActionType::KeyDown | ActionType::KeyUp
                    ),
                    StepFilter::Wait => {
                        matches!(act.action_type, ActionType::Wait | ActionType::RepeatBlock)
                    }
                    StepFilter::Mouse => matches!(
                        act.action_type,
                        ActionType::MouseClick | ActionType::MouseMove | ActionType::MouseScroll
                    ),
                };
                if !type_ok {
                    return false;
                }
                if q.is_empty() {
                    return true;
                }
                act.badge_text().to_ascii_lowercase().contains(&q)
                    || act.label.to_ascii_lowercase().contains(&q)
                    || act.describe().to_ascii_lowercase().contains(&q)
            })
            .map(|(i, _)| i)
            .collect()
    }

    fn step_headline(act: &MacroAction) -> String {
        let raw = match act.action_type {
            ActionType::KeyPress => format!("Press [{}]", act.key),
            ActionType::KeyDown => format!("Hold Key [{}]", act.key),
            ActionType::KeyUp => format!("Release Key [{}]", act.key),
            ActionType::MouseClick => {
                let kind = match act.click_type {
                    ClickType::Single => "Click",
                    ClickType::Double => "Double-Click",
                    ClickType::Down => "Button Down",
                    ClickType::Up => "Button Up",
                };
                format!("{} {kind}", act.button.capitalize())
            }
            ActionType::MouseMove => {
                let (x, y) = (act.x.unwrap_or(0), act.y.unwrap_or(0));
                if act.move_mode == MoveMode::Relative {
                    format!("Move ({x:+}, {y:+})")
                } else {
                    format!("Move to ({x}, {y})")
                }
            }
            ActionType::MouseScroll => {
                if act.scroll_dy != 0 {
                    let dir = if act.scroll_dy > 0 { "Up" } else { "Down" };
                    format!("Scroll {dir} {}", act.scroll_dy.abs())
                } else {
                    let dir = if act.scroll_dx > 0 { "Right" } else { "Left" };
                    format!("Scroll {dir} {}", act.scroll_dx.abs())
                }
            }
            ActionType::Wait => format!("Wait {}ms", act.wait_ms),
            ActionType::RepeatBlock => {
                format!(
                    "Repeat {}\u{00D7} ({} steps)",
                    act.repeat_count,
                    act.sub_actions.len()
                )
            }
        };
        if raw.chars().count() > 44 {
            let cut: String = raw.chars().take(43).collect();
            format!("{cut}\u{2026}")
        } else {
            raw
        }
    }

    fn step_subtitle(act: &MacroAction) -> String {
        let detail = match act.action_type {
            ActionType::KeyPress => {
                if act.label.is_empty() {
                    format!("Tap \u{2022} {}ms hold", act.hold_ms)
                } else {
                    format!("{}ms hold", act.hold_ms)
                }
            }
            ActionType::KeyDown => "Hold key down".to_string(),
            ActionType::KeyUp => "Release key up".to_string(),
            ActionType::Wait => {
                if act.jitter_ms > 0 {
                    let lo = (act.wait_ms - act.jitter_ms).max(1);
                    let hi = act.wait_ms + act.jitter_ms;
                    if act.label.is_empty() {
                        format!("Randomized {lo}\u{2013}{hi}ms")
                    } else {
                        format!("{lo}\u{2013}{hi}ms")
                    }
                } else {
                    "Exact delay".to_string()
                }
            }
            ActionType::MouseClick => {
                let target = match (act.x, act.y) {
                    (Some(x), Some(y)) => format!("Fixed ({x}, {y})"),
                    _ => "At cursor".to_string(),
                };
                if !act.label.is_empty() && act.hold_ms == 25 {
                    target
                } else {
                    format!("{target} \u{2022} {}ms hold", act.hold_ms)
                }
            }
            ActionType::MouseMove => format!("{:?} coordinates", act.move_mode),
            ActionType::MouseScroll => {
                format!("Wheel dY={}, dX={}", act.scroll_dy, act.scroll_dx)
            }
            ActionType::RepeatBlock => act
                .sub_actions
                .iter()
                .take(2)
                .map(MacroAction::describe)
                .collect::<Vec<_>>()
                .join(" -> "),
        };
        let combined = if !act.label.is_empty() && !detail.is_empty() {
            format!("{} \u{2022} {detail}", act.label)
        } else if !act.label.is_empty() {
            act.label.clone()
        } else {
            detail
        };
        if combined.chars().count() > 60 {
            let cut: String = combined.chars().take(59).collect();
            format!("{cut}\u{2026}")
        } else {
            combined
        }
    }

    fn step_metric_pill(act: &MacroAction) -> String {
        match act.action_type {
            ActionType::Wait => {
                if act.jitter_ms > 0 {
                    format!("{}ms \u{00B1}{}", act.wait_ms, act.jitter_ms)
                } else {
                    format!("{}ms", act.wait_ms)
                }
            }
            ActionType::KeyPress => format!("{}ms tap", act.hold_ms),
            ActionType::MouseClick => match (act.x, act.y) {
                (Some(x), Some(y)) => format!("({x},{y})"),
                _ => "Cursor".to_string(),
            },
            ActionType::MouseMove => format!("({},{})", act.x.unwrap_or(0), act.y.unwrap_or(0)),
            ActionType::MouseScroll => format!("dY {:+}", act.scroll_dy),
            ActionType::RepeatBlock => format!("{}\u{00D7} loop", act.repeat_count),
            _ => "State".to_string(),
        }
    }

    fn handle_background_events(&mut self, ctx: &Context) {
        while let Ok(action) = self.hotkey_rx.try_recv() {
            match action {
                "start_stop" => {
                    if self.step_dialog.is_none() && self.update_dialog.is_none() {
                        self.toggle_execution();
                    }
                }
                "record" => {
                    if self.step_dialog.is_none() && self.update_dialog.is_none() {
                        self.toggle_recording();
                    }
                }
                "panic" => {
                    self.trigger_panic_stop();
                }
                _ => {}
            }
        }

        while let Ok(maybe_rel) = self.startup_update_rx.try_recv() {
            self.startup_update_checking = false;
            if let Some(rel) = maybe_rel {
                if rel.is_update_available {
                    self.log_event(&format!(
                        "Update available: kTap v{} ({}).",
                        rel.version, rel.tag_name
                    ));
                    self.latest_release = Some(rel);
                }
            }
        }

        if let Some(deadline) = self.countdown_deadline {
            if Instant::now() >= deadline {
                self.launch_executor_now();
            }
        }

        if let Some(dirty_at) = self.ss_slider_dirty_at {
            if dirty_at.elapsed() >= Duration::from_millis(80) {
                self.ss_slider_dirty_at = None;
                self.apply_simple_spammer_to_profile(false);
            }
        }

        if let Some(ref toast) = self.toast {
            if Instant::now() >= toast.expires_at {
                self.toast = None;
            }
        }

        if let Some(target) = self.key_capture_target {
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
                self.key_capture_target = None;
            } else if let Some(combo) = captured {
                self.key_capture_target = None;
                match target {
                    KeyCaptureTarget::SimpleSpammer => {
                        self.ss_key = combo.clone();
                        self.apply_simple_spammer_to_profile(true);
                        self.show_toast(
                            format!("Bound Key Repeater to [{combo}]."),
                            ToastLevel::Success,
                            3,
                        );
                    }
                    KeyCaptureTarget::HotkeyStart => {
                        self.hk_start_str = combo.clone();
                        if self.sync_ui_settings_to_profile() {
                            self.show_toast(
                                format!("Start / Stop hotkey set to [{combo}]."),
                                ToastLevel::Success,
                                3,
                            );
                        }
                    }
                    KeyCaptureTarget::HotkeyRec => {
                        self.hk_rec_str = combo.clone();
                        if self.sync_ui_settings_to_profile() {
                            self.show_toast(
                                format!("Recorder hotkey set to [{combo}]."),
                                ToastLevel::Success,
                                3,
                            );
                        }
                    }
                    KeyCaptureTarget::HotkeyPanic => {
                        self.hk_panic_str = combo.clone();
                        if self.sync_ui_settings_to_profile() {
                            self.show_toast(
                                format!("Panic Stop hotkey set to [{combo}]."),
                                ToastLevel::Success,
                                3,
                            );
                        }
                    }
                }
            }
        }
    }

    fn handle_shortcuts(&mut self, ctx: &Context, visible: &[usize]) {
        if self.key_capture_target.is_some()
            || self.step_dialog.is_some()
            || self.update_dialog.is_some()
            || self.save_preset_modal.is_some()
            || ctx.wants_keyboard_input()
        {
            return;
        }

        let cmd = ctx.input(|i| i.modifiers.command);
        let shift = ctx.input(|i| i.modifiers.shift);
        let alt = ctx.input(|i| i.modifiers.alt);

        if cmd && ctx.input(|i| i.key_pressed(egui::Key::Z)) && !shift {
            self.undo();
            return;
        }
        if (cmd && ctx.input(|i| i.key_pressed(egui::Key::Y)))
            || (cmd && shift && ctx.input(|i| i.key_pressed(egui::Key::Z)))
        {
            self.redo();
            return;
        }
        if cmd && ctx.input(|i| i.key_pressed(egui::Key::S)) {
            self.save_profile_dialog();
            return;
        }
        if cmd && ctx.input(|i| i.key_pressed(egui::Key::O)) {
            self.open_profile_dialog();
            return;
        }
        if cmd && ctx.input(|i| i.key_pressed(egui::Key::N)) {
            self.new_profile();
            return;
        }

        if self.ui_mode != UiMode::Studio {
            return;
        }

        if ctx.input(|i| i.key_pressed(egui::Key::Slash)) {
            self.focus_filter_requested = true;
            return;
        }

        let actionable = self
            .selected_step_index
            .is_some_and(|idx| idx < self.profile.actions.len() && visible.contains(&idx));

        if cmd && ctx.input(|i| i.key_pressed(egui::Key::D)) && actionable {
            let idx = self.selected_step_index.unwrap();
            self.record_undo();
            let cloned = self.profile.actions[idx].clone_with_new_id();
            self.profile.actions.insert(idx + 1, cloned);
            self.selected_step_index = Some(idx + 1);
            self.on_sequence_mutated(true);
            return;
        }

        if (ctx.input(|i| i.key_pressed(egui::Key::Delete))
            || ctx.input(|i| i.key_pressed(egui::Key::Backspace)))
            && actionable
        {
            let idx = self.selected_step_index.unwrap();
            self.record_undo();
            self.profile.actions.remove(idx);
            self.selected_step_index = if self.profile.actions.is_empty() {
                None
            } else {
                Some(idx.min(self.profile.actions.len() - 1))
            };
            self.on_sequence_mutated(true);
            return;
        }

        if ctx.input(|i| i.key_pressed(egui::Key::Space)) && actionable {
            let idx = self.selected_step_index.unwrap();
            self.record_undo();
            self.profile.actions[idx].enabled = !self.profile.actions[idx].enabled;
            self.on_sequence_mutated(true);
            return;
        }

        if alt && ctx.input(|i| i.key_pressed(egui::Key::ArrowUp)) && actionable {
            let idx = self.selected_step_index.unwrap();
            if idx > 0 {
                self.record_undo();
                self.profile.actions.swap(idx - 1, idx);
                self.selected_step_index = Some(idx - 1);
                self.on_sequence_mutated(true);
            }
            return;
        }

        if alt && ctx.input(|i| i.key_pressed(egui::Key::ArrowDown)) && actionable {
            let idx = self.selected_step_index.unwrap();
            if idx + 1 < self.profile.actions.len() {
                self.record_undo();
                self.profile.actions.swap(idx, idx + 1);
                self.selected_step_index = Some(idx + 1);
                self.on_sequence_mutated(true);
            }
            return;
        }

        if !alt && ctx.input(|i| i.key_pressed(egui::Key::ArrowUp)) && !visible.is_empty() {
            self.selected_step_index = match self
                .selected_step_index
                .and_then(|sel| visible.iter().position(|&v| v == sel))
            {
                Some(pos) => Some(visible[pos.saturating_sub(1)]),
                None => visible.last().copied(),
            };
        } else if !alt && ctx.input(|i| i.key_pressed(egui::Key::ArrowDown)) && !visible.is_empty()
        {
            self.selected_step_index = match self
                .selected_step_index
                .and_then(|sel| visible.iter().position(|&v| v == sel))
            {
                Some(pos) => Some(visible[(pos + 1).min(visible.len() - 1)]),
                None => visible.first().copied(),
            };
        }
    }

    fn draw_top_bar(&mut self, ctx: &Context) {
        egui::TopBottomPanel::top("kf_topbar")
            .frame(
                egui::Frame::none()
                    .fill(BG_PANEL)
                    .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                    .inner_margin(egui::Margin::symmetric(14.0, 10.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    if let Some(tex) = &self.brand_tex {
                        ui.image((tex.id(), egui::vec2(26.0, 26.0)));
                    }
                    ui.label(
                        RichText::new("kTap")
                            .size(16.0)
                            .strong()
                            .color(TEXT_PRIMARY),
                    );
                    pill_badge(ui, &full_version_label(), BG_ELEVATED, TEXT_SECONDARY);
                    pill_badge(ui, platform_badge_label(), BG_ELEVATED, ACCENT_PRIMARY);

                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if styled_button(
                            ui,
                            &format!("Panic ({})", self.profile.panic_hotkey),
                            BTN_DANGER_SUBTLE,
                            BTN_DANGER_SUBTLE_TEXT,
                            BTN_DANGER_BORDER,
                        )
                        .on_hover_text(
                            "Immediately abort execution and release all held keys/buttons",
                        )
                        .clicked()
                        {
                            self.trigger_panic_stop();
                        }

                        if styled_button(ui, "Mini HUD", BTN_NEUTRAL, TEXT_PRIMARY, BORDER_CONTROL)
                            .on_hover_text("Minimize window and show floating compact control pill")
                            .clicked()
                        {
                            self.mini_hud_active = !self.mini_hud_active;
                            ctx.send_viewport_cmd(ViewportCommand::Minimized(self.mini_hud_active));
                        }

                        let (upd_txt, upd_fill, upd_fg, upd_border) = if let Some(rel) = self
                            .latest_release
                            .as_ref()
                            .filter(|r| r.is_update_available)
                        {
                            (
                                format!("Update v{}", rel.version),
                                CTA_SUCCESS,
                                CTA_TEXT,
                                CTA_SUCCESS,
                            )
                        } else {
                            (
                                "Updates".to_string(),
                                BTN_NEUTRAL,
                                TEXT_PRIMARY,
                                BORDER_CONTROL,
                            )
                        };
                        if styled_button(ui, &upd_txt, upd_fill, upd_fg, upd_border).clicked() {
                            self.update_dialog =
                                Some(UpdateDialogState::new(self.latest_release.clone()));
                        }

                        ui.separator();

                        if styled_button(ui, "Save", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_CONTROL)
                            .clicked()
                        {
                            self.save_profile_dialog();
                        }
                        if styled_button(ui, "Open", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_CONTROL)
                            .clicked()
                        {
                            self.open_profile_dialog();
                        }
                        if styled_button(ui, "New", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_CONTROL)
                            .clicked()
                        {
                            self.new_profile();
                        }

                        ui.separator();

                        egui::Frame::none()
                            .fill(BG_INPUT)
                            .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                            .rounding(Rounding::same(7.0))
                            .inner_margin(egui::Margin::same(2.0))
                            .show(ui, |ui| {
                                ui.horizontal(|ui| {
                                    for (mode, label) in
                                        [(UiMode::Studio, "Studio"), (UiMode::Simple, "Simple")]
                                    {
                                        let active = self.ui_mode == mode;
                                        let fill = if active {
                                            SEGMENT_ACTIVE
                                        } else {
                                            Color32::TRANSPARENT
                                        };
                                        let fg = if active { TEXT_PRIMARY } else { TEXT_SECONDARY };
                                        if ui
                                            .add(
                                                egui::Button::new(
                                                    RichText::new(label)
                                                        .size(11.5)
                                                        .strong()
                                                        .color(fg),
                                                )
                                                .fill(fill)
                                                .stroke(Stroke::NONE)
                                                .rounding(Rounding::same(6.0)),
                                            )
                                            .clicked()
                                            && self.ui_mode != mode
                                        {
                                            if mode == UiMode::Studio
                                                && self.simple_task == SimpleTask::Spammer
                                            {
                                                self.apply_simple_spammer_to_profile(false);
                                            }
                                            self.sync_ui_settings_to_profile();
                                            self.ui_mode = mode;
                                            if mode == UiMode::Simple {
                                                self.sync_profile_to_simple_spammer_fields();
                                            }
                                            self.save_persistent_state();
                                        }
                                    }
                                });
                            });
                    });
                });
            });
    }

    fn draw_status_bar(&self, ctx: &Context) {
        egui::TopBottomPanel::bottom("kf_statusbar")
            .frame(
                egui::Frame::none()
                    .fill(BG_PANEL)
                    .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                    .inner_margin(egui::Margin::symmetric(14.0, 5.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.label(
                        RichText::new(&self.footer_status)
                            .size(11.0)
                            .color(TEXT_SECONDARY),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let dup = if cfg!(target_os = "macos") {
                            "\u{2318}D Dup"
                        } else {
                            "Ctrl+D Dup"
                        };
                        ui.label(
                            RichText::new(format!(
                                "/ Filter \u{00B7} Del Delete \u{00B7} {dup} \u{00B7} Space Toggle \u{00B7} Alt+Up/Dn Move \u{00B7} Dbl-Click Edit"
                            ))
                            .size(11.0)
                            .color(TEXT_MUTED),
                        );
                    });
                });
            });
    }

    fn draw_banners(&mut self, ui: &mut egui::Ui) {
        if !self.update_banner_dismissed {
            if let Some(rel) = self
                .latest_release
                .as_ref()
                .filter(|r| r.is_update_available)
                .cloned()
            {
                egui::Frame::none()
                    .fill(BANNER_UPDATE_BG)
                    .stroke(Stroke::new(1.0_f32, BANNER_UPDATE_BORDER))
                    .rounding(Rounding::same(10.0))
                    .inner_margin(egui::Margin::symmetric(14.0, 7.0))
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.label(
                                RichText::new(format!(
                                    "kTap v{} is available \u{2014} Update cleanly while keeping all your configs & macros",
                                    rel.version
                                ))
                                .size(11.5)
                                .strong()
                                .color(BANNER_UPDATE_FG),
                            );
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                if ghost_button(ui, "Dismiss", TEXT_SECONDARY).clicked() {
                                    self.update_banner_dismissed = true;
                                }
                                if styled_button(
                                    ui,
                                    "Update Now",
                                    CTA_SUCCESS,
                                    CTA_TEXT,
                                    CTA_SUCCESS,
                                )
                                .clicked()
                                {
                                    self.update_dialog =
                                        Some(UpdateDialogState::new(Some(rel.clone())));
                                }
                            });
                        });
                    });
                ui.add_space(6.0);
            }
        }

        if !self.platform_notice_dismissed && !self.platform_caps.notice.trim().is_empty() {
            let (bg, border, fg) = if self.platform_caps.requires_permission {
                (BANNER_WARN_BG, BANNER_WARN_BORDER, BANNER_WARN_FG)
            } else {
                (BANNER_INFO_BG, BANNER_INFO_BORDER, BANNER_INFO_FG)
            };
            let notice = self.platform_caps.notice.clone();
            let show_wayland_copy =
                self.platform_caps.platform == "linux" && self.platform_caps.requires_permission;
            egui::Frame::none()
                .fill(bg)
                .stroke(Stroke::new(1.0_f32, border))
                .rounding(Rounding::same(10.0))
                .inner_margin(egui::Margin::symmetric(14.0, 6.0))
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.add(
                            egui::Label::new(RichText::new(&notice).size(11.0).color(fg)).wrap(),
                        );
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ghost_button(ui, "\u{00D7}", TEXT_SECONDARY).clicked() {
                                self.platform_notice_dismissed = true;
                            }
                            if show_wayland_copy
                                && styled_button(
                                    ui,
                                    "Copy Setup Cmd",
                                    BTN_NEUTRAL,
                                    TEXT_PRIMARY,
                                    BORDER_CONTROL,
                                )
                                .clicked()
                            {
                                ui.ctx().copy_text(wayland_udev_setup_command().to_string());
                                self.show_toast(
                                    "Copied Linux Wayland /dev/uinput setup command to clipboard.",
                                    ToastLevel::Success,
                                    5,
                                );
                            }
                        });
                    });
                });
            ui.add_space(6.0);
        }

        if let Some(toast) = self.toast.as_ref().map(|t| (t.message.clone(), t.level)) {
            let (bg, border, fg) = match toast.1 {
                ToastLevel::Error => (BANNER_ERR_BG, BANNER_ERR_BORDER, BANNER_ERR_FG),
                ToastLevel::Warning => (BANNER_WARN_BG, BANNER_WARN_BORDER, BANNER_WARN_FG),
                ToastLevel::Info => (BANNER_INFO_BG, BANNER_INFO_BORDER, BANNER_INFO_FG),
                ToastLevel::Success => (BANNER_UPDATE_BG, BANNER_UPDATE_BORDER, BANNER_UPDATE_FG),
            };
            egui::Frame::none()
                .fill(bg)
                .stroke(Stroke::new(1.0_f32, border))
                .rounding(Rounding::same(10.0))
                .inner_margin(egui::Margin::symmetric(14.0, 6.0))
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.label(RichText::new(toast.0).size(11.5).strong().color(fg));
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ghost_button(ui, "\u{00D7}", TEXT_SECONDARY).clicked() {
                                self.toast = None;
                            }
                        });
                    });
                });
            ui.add_space(6.0);
        }
    }

    fn draw_simple_mode(
        &mut self,
        ui: &mut egui::Ui,
        telem: &ExecutionTelemetry,
        is_running: bool,
    ) {
        let total_w = ui.available_width();
        let left_w = ((total_w - 14.0) * 0.55).max(320.0);
        let right_w = (total_w - 14.0 - left_w).max(300.0);
        let avail_h = ui.available_height();

        ui.horizontal_top(|ui| {
            // Left Card: Task Builder
            ui.allocate_ui_with_layout(
                Vec2::new(left_w, avail_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    panel_frame().show(ui, |ui| {
                        ui.set_min_height((avail_h - 24.0).max(200.0));
                        micro_heading(ui, "ACTION MODE");
                        ui.label(
                            RichText::new("Choose Action")
                                .size(15.5)
                                .strong()
                                .color(TEXT_PRIMARY),
                        );
                        ui.add_space(6.0);

                        egui::Frame::none()
                            .fill(BG_INPUT)
                            .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                            .rounding(Rounding::same(8.0))
                            .inner_margin(egui::Margin::same(3.0))
                            .show(ui, |ui| {
                                ui.columns(2, |cols| {
                                    for (idx, (task, lbl)) in [
                                        (SimpleTask::Spammer, "Key Repeater"),
                                        (SimpleTask::Sequence, "Quick Sequence"),
                                    ]
                                    .into_iter()
                                    .enumerate()
                                    {
                                        let active = self.simple_task == task;
                                        let fill = if active {
                                            SEGMENT_ACTIVE
                                        } else {
                                            Color32::TRANSPARENT
                                        };
                                        if cols[idx]
                                            .add_sized(
                                                [cols[idx].available_width(), 28.0],
                                                egui::Button::new(
                                                    RichText::new(lbl)
                                                        .size(12.0)
                                                        .strong()
                                                        .color(TEXT_PRIMARY),
                                                )
                                                .fill(fill)
                                                .stroke(Stroke::NONE)
                                                .rounding(Rounding::same(6.0)),
                                            )
                                            .clicked()
                                            && self.simple_task != task
                                        {
                                            self.simple_task = task;
                                            if task == SimpleTask::Spammer {
                                                self.apply_simple_spammer_to_profile(true);
                                            }
                                            self.save_persistent_state();
                                        }
                                    }
                                });
                            });

                        ui.add_space(10.0);

                        if self.simple_task == SimpleTask::Spammer {
                            card_frame().show(ui, |ui| {
                                micro_heading(ui, "KEY OR SHORTCUT TO REPEAT");
                                ui.add_space(6.0);
                                ui.horizontal(|ui| {
                                    ui.add(
                                        egui::TextEdit::singleline(&mut self.ss_key)
                                            .desired_width(130.0),
                                    );
                                    egui::ComboBox::from_id_salt("ss_key_combo")
                                        .width(68.0)
                                        .selected_text("Pick")
                                        .show_ui(ui, |ui| {
                                            for k in list_common_keys() {
                                                if ui
                                                    .selectable_label(self.ss_key == k, k)
                                                    .clicked()
                                                {
                                                    self.ss_key = k.to_string();
                                                    self.apply_simple_spammer_to_profile(true);
                                                }
                                            }
                                        });
                                    let cap_on = self.key_capture_target
                                        == Some(KeyCaptureTarget::SimpleSpammer);
                                    if styled_button(
                                        ui,
                                        if cap_on { "Press..." } else { "Bind Key" },
                                        if cap_on { ACCENT_WARNING } else { BTN_NEUTRAL },
                                        if cap_on {
                                            Color32::from_rgb(0x09, 0x0A, 0x0D)
                                        } else {
                                            TEXT_PRIMARY
                                        },
                                        BORDER_CONTROL,
                                    )
                                    .clicked()
                                    {
                                        self.key_capture_target = if cap_on {
                                            None
                                        } else {
                                            Some(KeyCaptureTarget::SimpleSpammer)
                                        };
                                    }
                                    if styled_button(
                                        ui,
                                        "Apply",
                                        SEGMENT_ACTIVE,
                                        ACCENT_SUCCESS,
                                        BORDER_CONTROL,
                                    )
                                    .clicked()
                                    {
                                        self.apply_simple_spammer_to_profile(true);
                                    }
                                });
                            });

                            ui.add_space(10.0);

                            card_frame().show(ui, |ui| {
                                ui.horizontal(|ui| {
                                    micro_heading(ui, "REPEAT INTERVAL");
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            let ms = self.ss_interval_ms.max(25);
                                            ui.label(
                                                RichText::new(format!(
                                                    "{ms} ms ({:.1} / sec)",
                                                    1000.0 / (ms as f64)
                                                ))
                                                .monospace()
                                                .strong()
                                                .size(12.5)
                                                .color(ACCENT_PRIMARY),
                                            );
                                        },
                                    );
                                });
                                ui.add_space(6.0);
                                let mut slider_val = self.ss_interval_ms as f64;
                                if ui
                                    .add(
                                        egui::Slider::new(&mut slider_val, 25.0..=2000.0)
                                            .step_by(5.0)
                                            .show_value(false),
                                    )
                                    .changed()
                                {
                                    self.ss_interval_ms = slider_val.round() as i64;
                                    self.ss_slider_dirty_at = Some(Instant::now());
                                }
                                ui.add_space(6.0);
                                ui.horizontal(|ui| {
                                    ui.label(
                                        RichText::new("Presets:").size(11.0).color(TEXT_MUTED),
                                    );
                                    for ms in [25, 50, 100, 250, 500] {
                                        let active_ms = self.ss_interval_ms == ms;
                                        if styled_button(
                                            ui,
                                            &format!("{ms}ms"),
                                            if active_ms { SEGMENT_ACTIVE } else { BTN_SUBTLE },
                                            if active_ms {
                                                ACCENT_PRIMARY
                                            } else {
                                                TEXT_SECONDARY
                                            },
                                            if active_ms {
                                                ACCENT_PRIMARY
                                            } else {
                                                BORDER_SUBTLE
                                            },
                                        )
                                        .clicked()
                                        {
                                            self.ss_interval_ms = ms;
                                            self.apply_simple_spammer_to_profile(true);
                                        }
                                    }
                                });
                                ui.add_space(6.0);
                                if ui
                                    .checkbox(
                                        &mut self.ss_jitter_enabled,
                                        "Humanized timing variance (\u{00B1}10%)",
                                    )
                                    .changed()
                                {
                                    self.apply_simple_spammer_to_profile(true);
                                }
                            });
                        } else {
                            card_frame().show(ui, |ui| {
                                micro_heading(ui, "ACTIVE MACRO SEQUENCE");
                                ui.label(
                                    RichText::new(format!(
                                        "{}\n{}/{} active steps \u{2022} {}ms per cycle",
                                        self.profile.name,
                                        self.cached_enabled_count,
                                        self.profile.actions.len(),
                                        self.cached_cycle_wait_ms
                                    ))
                                    .size(11.5)
                                    .color(TEXT_SECONDARY),
                                );
                                ui.add_space(10.0);
                                micro_heading(ui, "QUICK RECIPE DSL");
                                let enter_hit = ui
                                    .add(
                                        egui::TextEdit::singleline(&mut self.dsl_input)
                                            .desired_width(ui.available_width())
                                            .font(egui::TextStyle::Monospace)
                                            .hint_text("Press E -> Wait 200ms -> Click -> Press 1"),
                                    )
                                    .lost_focus()
                                    && ui.input(|i| i.key_pressed(egui::Key::Enter));
                                if enter_hit && self.apply_dsl(false) {
                                    self.show_toast(
                                        "Sequence recipe updated.",
                                        ToastLevel::Success,
                                        3,
                                    );
                                }
                                ui.add_space(8.0);
                                ui.horizontal(|ui| {
                                    if styled_button(
                                        ui,
                                        "Apply Recipe",
                                        SEGMENT_ACTIVE,
                                        ACCENT_SUCCESS,
                                        BORDER_CONTROL,
                                    )
                                    .clicked()
                                        && self.apply_dsl(false)
                                    {
                                        self.show_toast(
                                            "Sequence recipe updated.",
                                            ToastLevel::Success,
                                            3,
                                        );
                                    }
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            if styled_button(
                                                ui,
                                                "Edit Steps in Studio \u{2192}",
                                                BTN_NEUTRAL,
                                                TEXT_PRIMARY,
                                                BORDER_CONTROL,
                                            )
                                            .clicked()
                                            {
                                                self.ui_mode = UiMode::Studio;
                                                self.save_persistent_state();
                                            }
                                        },
                                    );
                                });
                                ui.add_space(8.0);
                                ui.add(
                                    egui::Label::new(
                                        RichText::new("Syntax: Press KEY -> Wait MS \u{00B1}JITTER -> Click -> Move X,Y -> Scroll DY -> Repeat N { ... }")
                                            .size(10.5)
                                            .color(TEXT_MUTED),
                                    )
                                    .wrap(),
                                );
                            });
                        }
                    });
                },
            );

            ui.add_space(8.0);

            // Right Card: Control Center
            ui.allocate_ui_with_layout(
                Vec2::new(right_w, avail_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    panel_frame().show(ui, |ui| {
                        ui.set_min_height((avail_h - 24.0).max(200.0));
                        micro_heading(ui, "CONTROL CENTER");
                        ui.label(
                            RichText::new("Execution & Timing")
                                .size(15.5)
                                .strong()
                                .color(TEXT_PRIMARY),
                        );
                        ui.add_space(6.0);

                        let (st_text, st_color) = Self::status_badge_style(telem.state);
                        card_frame().show(ui, |ui| {
                            ui.label(RichText::new(st_text).size(20.0).strong().color(st_color));
                            ui.label(
                                RichText::new(format!(
                                    "{} cycles \u{2022} {} actions \u{2022} {:.1}s",
                                    telem.cycles_completed,
                                    telem.actions_executed,
                                    telem.elapsed_seconds
                                ))
                                .monospace()
                                .size(11.5)
                                .color(TEXT_SECONDARY),
                            );
                        });

                        ui.add_space(8.0);
                        self.draw_primary_cta(ui, is_running, 40.0);
                        ui.add_space(6.0);
                        if ui
                            .add_sized(
                                [ui.available_width(), 32.0],
                                egui::Button::new(
                                    RichText::new(format!(
                                        "Panic Stop ({})",
                                        self.profile.panic_hotkey
                                    ))
                                    .strong()
                                    .color(BTN_DANGER_SUBTLE_TEXT),
                                )
                                .fill(BTN_DANGER_SUBTLE)
                                .stroke(Stroke::new(1.0_f32, BTN_DANGER_BORDER))
                                .rounding(Rounding::same(8.0)),
                            )
                            .clicked()
                        {
                            self.trigger_panic_stop();
                        }

                        ui.add_space(8.0);
                        card_frame().show(ui, |ui| {
                            micro_heading(ui, "REPEAT MODE");
                            ui.add_space(4.0);
                            egui::Frame::none()
                                .fill(BG_INPUT)
                                .rounding(Rounding::same(7.0))
                                .inner_margin(egui::Margin::same(2.0))
                                .show(ui, |ui| {
                                    ui.columns(3, |cols| {
                                        for (i, (m, lbl)) in [
                                            (RepeatMode::Infinite, "Forever"),
                                            (RepeatMode::Count, "Count"),
                                            (RepeatMode::Duration, "Duration"),
                                        ]
                                        .into_iter()
                                        .enumerate()
                                        {
                                            let active = self.profile.repeat_mode == m;
                                            let fill = if active {
                                                SEGMENT_ACTIVE
                                            } else {
                                                Color32::TRANSPARENT
                                            };
                                            if cols[i]
                                                .add_sized(
                                                    [cols[i].available_width(), 24.0],
                                                    egui::Button::new(
                                                        RichText::new(lbl)
                                                            .size(11.5)
                                                            .strong()
                                                            .color(if active {
                                                                TEXT_PRIMARY
                                                            } else {
                                                                TEXT_SECONDARY
                                                            }),
                                                    )
                                                    .fill(fill)
                                                    .stroke(Stroke::NONE)
                                                    .rounding(Rounding::same(6.0)),
                                                )
                                                .clicked()
                                                && self.profile.repeat_mode != m
                                            {
                                                self.profile.repeat_mode = m;
                                                self.sync_profile_settings_to_ui();
                                                self.sync_ui_settings_to_profile();
                                                self.save_persistent_state();
                                            }
                                        }
                                    });
                                });

                            if self.profile.repeat_mode != RepeatMode::Infinite {
                                ui.add_space(6.0);
                                ui.horizontal(|ui| {
                                    let lbl = if self.profile.repeat_mode == RepeatMode::Count {
                                        "Times:"
                                    } else {
                                        "Seconds:"
                                    };
                                    ui.label(RichText::new(lbl).size(11.5).color(TEXT_SECONDARY));
                                    let enter = ui
                                        .add(
                                            egui::TextEdit::singleline(&mut self.repeat_val_str)
                                                .desired_width(80.0),
                                        )
                                        .lost_focus()
                                        && ui.input(|i| i.key_pressed(egui::Key::Enter));
                                    if (enter
                                        || styled_button(
                                            ui,
                                            "Apply",
                                            BTN_NEUTRAL,
                                            TEXT_PRIMARY,
                                            BORDER_CONTROL,
                                        )
                                        .clicked())
                                        && self.sync_ui_settings_to_profile()
                                    {
                                        self.save_persistent_state();
                                    }
                                });
                            }

                            ui.add_space(6.0);
                            if ui
                                .checkbox(
                                    &mut self.dry_run,
                                    "Dry-Run Simulation (safe preview, no OS input)",
                                )
                                .changed()
                            {
                                self.save_persistent_state();
                            }

                            ui.add_space(4.0);
                            ui.horizontal(|ui| {
                                ui.label(
                                    RichText::new("Start Delay:")
                                        .size(11.5)
                                        .color(TEXT_SECONDARY),
                                );
                                for sec in [0, 1, 3, 5] {
                                    let active = self.countdown_sec == sec;
                                    if styled_button(
                                        ui,
                                        &format!("{sec}s"),
                                        if active { SEGMENT_ACTIVE } else { BG_INPUT },
                                        TEXT_PRIMARY,
                                        BORDER_SUBTLE,
                                    )
                                    .clicked()
                                    {
                                        self.countdown_sec = sec;
                                        self.save_persistent_state();
                                    }
                                }
                            });
                        });

                        ui.add_space(6.0);
                        ui.horizontal(|ui| {
                            for pill in [
                                format!("Start: {}", self.profile.start_stop_hotkey),
                                format!("Rec: {}", self.profile.record_hotkey),
                                format!("Panic: {}", self.profile.panic_hotkey),
                            ] {
                                egui::Frame::none()
                                    .fill(BG_ELEVATED)
                                    .rounding(Rounding::same(6.0))
                                    .inner_margin(egui::Margin::symmetric(8.0, 2.0))
                                    .show(ui, |ui| {
                                        ui.label(
                                            RichText::new(pill)
                                                .monospace()
                                                .size(11.0)
                                                .color(TEXT_SECONDARY),
                                        );
                                    });
                            }
                        });

                        ui.add_space(8.0);
                        let (upd_lbl, upd_col) = if let Some(rel) =
                            self.latest_release.as_ref().filter(|r| r.is_update_available)
                        {
                            (
                                format!("Update v{} available \u{2014} Install", rel.version),
                                ACCENT_SUCCESS,
                            )
                        } else {
                            (
                                format!("kTap {} \u{2022} Check Updates", full_version_label()),
                                TEXT_SECONDARY,
                            )
                        };
                        if ui
                            .add(
                                egui::Button::new(RichText::new(upd_lbl).size(11.5).color(upd_col))
                                    .fill(Color32::TRANSPARENT)
                                    .stroke(Stroke::NONE),
                            )
                            .clicked()
                        {
                            self.update_dialog =
                                Some(UpdateDialogState::new(self.latest_release.clone()));
                        }
                        if ui
                            .checkbox(&mut self.check_updates_on_startup, "Check for updates on startup")
                            .changed()
                        {
                            self.save_persistent_state();
                        }
                    });
                },
            );
        });
    }

    fn status_badge_style(state: ExecutorState) -> (&'static str, Color32) {
        match state {
            ExecutorState::Idle => ("\u{2022} Ready", ACCENT_SUCCESS),
            ExecutorState::Running => ("\u{2022} Running", ACCENT_PRIMARY),
            ExecutorState::Stopping => ("\u{2022} Stopping", ACCENT_WARNING),
            ExecutorState::PanicAborted => ("\u{2022} Stopped (Panic)", ACCENT_DANGER),
            ExecutorState::Error => ("\u{2022} Error", ACCENT_DANGER),
        }
    }

    fn draw_primary_cta(&mut self, ui: &mut egui::Ui, is_running: bool, height: f32) {
        let (text, fill, fg) = if is_running {
            (
                format!("\u{25A0} Stop Macro ({})", self.profile.start_stop_hotkey),
                ACCENT_DANGER,
                Color32::WHITE,
            )
        } else if let Some(deadline) = self.countdown_deadline {
            let rem = deadline
                .saturating_duration_since(Instant::now())
                .as_secs_f32()
                .max(0.1);
            (
                format!("Starting in {rem:.1}s ({})", self.profile.start_stop_hotkey),
                ACCENT_WARNING,
                CTA_TEXT,
            )
        } else {
            (
                format!("\u{25B6} Start Macro ({})", self.profile.start_stop_hotkey),
                CTA_SUCCESS,
                CTA_TEXT,
            )
        };
        if ui
            .add_sized(
                [ui.available_width(), height],
                egui::Button::new(RichText::new(text).size(13.5).strong().color(fg))
                    .fill(fill)
                    .stroke(Stroke::NONE)
                    .rounding(Rounding::same(8.0)),
            )
            .clicked()
        {
            self.toggle_execution();
        }
    }

    fn draw_studio_hero_strip(
        &mut self,
        ui: &mut egui::Ui,
        telem: &ExecutionTelemetry,
        is_running: bool,
    ) {
        let total_w = ui.available_width();
        let card_h = 104.0;
        let gap = 8.0;
        let w = ((total_w - gap * 3.0) / 4.25).max(160.0);
        let cta_w = (total_w - gap * 3.0 - w * 3.0).max(200.0);
        let inner_w = (w - 24.0).max(120.0);
        let cta_inner_w = (cta_w - 24.0).max(160.0);

        ui.horizontal_top(|ui| {
            ui.spacing_mut().item_spacing.x = gap;
            // Card 1: Profile & Security Badge
            ui.allocate_ui_with_layout(
                Vec2::new(w, card_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    ui.set_width(w);
                    ui.set_max_width(w);
                    panel_frame().show(ui, |ui| {
                        ui.set_width(inner_w);
                        ui.set_min_height(card_h - 24.0);
                        ui.horizontal(|ui| {
                            micro_heading(ui, "PROFILE");
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    let (badge_txt, badge_bg, badge_fg) =
                                        if self.last_audit.has_critical() {
                                            (
                                                format!(
                                                    "Alert: {}",
                                                    self.last_audit
                                                        .findings
                                                        .iter()
                                                        .filter(|f| f.severity
                                                            == AuditSeverity::Critical)
                                                        .count()
                                                ),
                                                BADGE_CRIT_BG,
                                                BADGE_CRIT_FG,
                                            )
                                        } else if self.last_audit.has_warnings() {
                                            (
                                                format!(
                                                    "Notice ({})",
                                                    self.last_audit
                                                        .findings
                                                        .iter()
                                                        .filter(|f| f.severity
                                                            == AuditSeverity::Warning)
                                                        .count()
                                                ),
                                                BADGE_WARN_BG,
                                                BADGE_WARN_FG,
                                            )
                                        } else {
                                            ("Verified".to_string(), BG_ELEVATED, ACCENT_SUCCESS)
                                        };
                                    if ui
                                        .add(
                                            egui::Button::new(
                                                RichText::new(badge_txt)
                                                    .size(10.0)
                                                    .strong()
                                                    .color(badge_fg),
                                            )
                                            .fill(badge_bg)
                                            .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                                            .rounding(Rounding::same(5.0)),
                                        )
                                        .clicked()
                                    {
                                        self.inspector_tab = InspectorTab::Security;
                                        self.refresh_security_details();
                                    }
                                },
                            );
                        });

                        ui.add_space(4.0);
                        let mut chosen_preset: Option<MacroProfile> = None;
                        egui::ComboBox::from_id_salt("studio_preset_combo")
                            .width(inner_w)
                            .selected_text(RichText::new(&self.profile.name).size(11.5).strong())
                            .show_ui(ui, |ui| {
                                for p in &self.presets {
                                    if ui
                                        .selectable_label(self.profile.name == p.name, &p.name)
                                        .clicked()
                                    {
                                        chosen_preset = Some(p.clone());
                                    }
                                }
                            });
                        if let Some(p) = chosen_preset {
                            if self.executor.is_running() {
                                self.executor.stop(Duration::from_secs(2));
                            }
                            self.filter_query.clear();
                            self.step_filter = StepFilter::All;
                            self.record_undo();
                            self.profile = p;
                            self.sync_profile_settings_to_ui();
                            self.rebind_hotkeys();
                            self.on_sequence_mutated(true);
                            self.log_event(&format!(
                                "Loaded preset profile: {}",
                                self.profile.name
                            ));
                        }

                        ui.add_space(4.0);
                        ui.horizontal(|ui| {
                            if ghost_button(ui, "+ Save Preset", ACCENT_SUCCESS).clicked() {
                                let cur = self.profile.name.trim().to_string();
                                let auto = cur.is_empty()
                                    || self.builtin_names.contains(&cur)
                                    || cur == "Custom Profile"
                                    || cur.starts_with("Key Spammer")
                                    || cur.starts_with("Untitled");
                                if auto {
                                    self.save_preset_modal = Some(String::new());
                                } else if self.save_custom_preset_named(&cur) {
                                    // saved directly
                                }
                            }

                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ghost_button(ui, "Delete", TEXT_MUTED).clicked() {
                                        let name = self.profile.name.clone();
                                        if self.builtin_names.contains(&name) {
                                            self.show_toast(
                                                "Built-in presets cannot be deleted.",
                                                ToastLevel::Warning,
                                                3,
                                            );
                                        } else if delete_custom_preset(&name, None) {
                                            self.reload_presets_catalog();
                                            self.show_toast(
                                                format!("Deleted custom preset '{name}'."),
                                                ToastLevel::Info,
                                                3,
                                            );
                                        }
                                    }
                                },
                            );
                        });
                    });
                },
            );

            // Card 2: Execution Telemetry & Progress
            ui.allocate_ui_with_layout(
                Vec2::new(w, card_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    ui.set_width(w);
                    ui.set_max_width(w);
                    panel_frame().show(ui, |ui| {
                        ui.set_width(inner_w);
                        ui.set_min_height(card_h - 24.0);
                        ui.horizontal(|ui| {
                            micro_heading(ui, "ENGINE");
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    let (m_txt, m_bg, m_fg) = if self.dry_run {
                                        ("Dry-Run", BADGE_WARN_BG, BADGE_WARN_FG)
                                    } else if !self.platform_caps.can_inject {
                                        (
                                            if self.platform_caps.requires_permission {
                                                "Access Needed"
                                            } else {
                                                "Preview Only"
                                            },
                                            BADGE_WARN_BG,
                                            BADGE_WARN_FG,
                                        )
                                    } else {
                                        ("Live Input", BG_ELEVATED, TEXT_SECONDARY)
                                    };
                                    egui::Frame::none()
                                        .fill(m_bg)
                                        .rounding(Rounding::same(5.0))
                                        .inner_margin(egui::Margin::symmetric(6.0, 1.0))
                                        .show(ui, |ui| {
                                            ui.label(
                                                RichText::new(m_txt)
                                                    .size(10.0)
                                                    .strong()
                                                    .color(m_fg),
                                            );
                                        });
                                },
                            );
                        });
                        let (st_text, st_color) = Self::status_badge_style(telem.state);
                        ui.label(RichText::new(st_text).size(14.0).strong().color(st_color));
                        ui.label(
                            RichText::new(format!(
                                "{} Cycles | {} Actions",
                                telem.cycles_completed, telem.actions_executed
                            ))
                            .size(11.5)
                            .strong()
                            .color(TEXT_PRIMARY),
                        );
                        ui.label(
                            RichText::new(format!(
                                "Cycle: {}ms \u{2022} {:.1}s",
                                self.cached_cycle_wait_ms, telem.elapsed_seconds
                            ))
                            .monospace()
                            .size(11.0)
                            .color(TEXT_SECONDARY),
                        );
                        let ratio = match self.profile.repeat_mode {
                            RepeatMode::Count if self.profile.repeat_count > 0 => {
                                (telem.cycles_completed as f32 / self.profile.repeat_count as f32)
                                    .clamp(0.0, 1.0)
                            }
                            RepeatMode::Duration if self.profile.repeat_duration_s > 0.0 => {
                                (telem.elapsed_seconds as f32
                                    / self.profile.repeat_duration_s as f32)
                                    .clamp(0.0, 1.0)
                            }
                            _ => {
                                if is_running {
                                    1.0
                                } else {
                                    0.0
                                }
                            }
                        };
                        ui.add(egui::ProgressBar::new(ratio).desired_height(3.0).fill(
                            if ratio > 0.0 {
                                ACCENT_PRIMARY
                            } else {
                                BG_ELEVATED
                            },
                        ));
                    });
                },
            );

            // Card 3: Hotkeys & Cursor
            ui.allocate_ui_with_layout(
                Vec2::new(w, card_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    ui.set_width(w);
                    ui.set_max_width(w);
                    panel_frame().show(ui, |ui| {
                        ui.set_width(inner_w);
                        ui.set_min_height(card_h - 24.0);
                        ui.horizontal(|ui| {
                            micro_heading(ui, "HOTKEYS");
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    ui.label(
                                        RichText::new(format!(
                                            "XY: ({}, {})",
                                            self.cursor_xy.0, self.cursor_xy.1
                                        ))
                                        .monospace()
                                        .size(11.0)
                                        .color(TEXT_SECONDARY),
                                    );
                                },
                            );
                        });
                        ui.add_space(4.0);
                        ui.label(
                            RichText::new(format!(
                                "Run: {} \u{2022} Rec: {} \u{2022} Panic: {}",
                                self.profile.start_stop_hotkey,
                                self.profile.record_hotkey,
                                self.profile.panic_hotkey
                            ))
                            .size(11.5)
                            .strong()
                            .color(TEXT_PRIMARY),
                        );
                        ui.add_space(6.0);
                        if ui
                            .checkbox(&mut self.dry_run, "Dry-Run Simulation")
                            .changed()
                        {
                            self.save_persistent_state();
                        }
                    });
                },
            );

            // Card 4: Delay & Primary CTA
            ui.allocate_ui_with_layout(
                Vec2::new(cta_w, card_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    ui.set_width(cta_w);
                    ui.set_max_width(cta_w);
                    panel_frame().show(ui, |ui| {
                        ui.set_width(cta_inner_w);
                        ui.set_min_height(card_h - 24.0);
                        ui.horizontal(|ui| {
                            micro_heading(ui, "DELAY");
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    for sec in [5, 3, 1, 0] {
                                        let active = self.countdown_sec == sec;
                                        if styled_button(
                                            ui,
                                            &format!("{sec}s"),
                                            if active { SEGMENT_ACTIVE } else { BG_INPUT },
                                            TEXT_PRIMARY,
                                            BORDER_SUBTLE,
                                        )
                                        .clicked()
                                        {
                                            self.countdown_sec = sec;
                                            self.save_persistent_state();
                                        }
                                    }
                                },
                            );
                        });
                        ui.add_space(4.0);
                        self.draw_primary_cta(ui, is_running, 34.0);
                        let mode_str = match self.profile.repeat_mode {
                            RepeatMode::Count => "Fixed Count",
                            RepeatMode::Infinite => "Infinite",
                            RepeatMode::Duration => "Duration",
                        };
                        ui.centered_and_justified(|ui| {
                            ui.label(
                                RichText::new(format!(
                                    "{mode_str} \u{00B7} {}/{} steps \u{00B7} {}ms",
                                    self.cached_enabled_count,
                                    self.profile.actions.len(),
                                    self.cached_cycle_wait_ms
                                ))
                                .size(10.5)
                                .color(TEXT_MUTED),
                            );
                        });
                    });
                },
            );
        });
    }

    fn save_custom_preset_named(&mut self, name: &str) -> bool {
        let cleaned = name.trim();
        if cleaned.is_empty() {
            self.show_toast("Preset name cannot be empty.", ToastLevel::Warning, 3);
            return false;
        }
        if self.ui_mode == UiMode::Simple && self.simple_task == SimpleTask::Spammer {
            if !self.apply_simple_spammer_to_profile(false) {
                return false;
            }
        } else if !self.sync_ui_settings_to_profile() {
            return false;
        }
        let capped: String = cleaned.chars().take(100).collect();
        self.profile.name = capped.clone();
        match save_custom_preset(&mut self.profile, None) {
            Ok(_) => {
                self.reload_presets_catalog();
                self.log_event(&format!("Saved custom preset: {capped}"));
                self.show_toast(format!("Saved preset '{capped}'."), ToastLevel::Success, 4);
                true
            }
            Err(e) => {
                self.show_toast(format!("Save Preset Error: {e}"), ToastLevel::Error, 5);
                false
            }
        }
    }

    fn draw_studio_workspace(
        &mut self,
        ui: &mut egui::Ui,
        telem: &ExecutionTelemetry,
        is_running: bool,
        visible: &[usize],
    ) {
        self.draw_studio_hero_strip(ui, telem, is_running);
        ui.add_space(6.0);

        // Filter & Batch Action Bar
        ui.horizontal(|ui| {
            egui::Frame::none()
                .fill(BG_PANEL)
                .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                .rounding(Rounding::same(8.0))
                .inner_margin(egui::Margin::same(3.0))
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        for (flt, lbl) in [
                            (StepFilter::All, "All Steps"),
                            (StepFilter::Key, "Keys"),
                            (StepFilter::Wait, "Waits"),
                            (StepFilter::Mouse, "Mouse"),
                        ] {
                            let active = self.step_filter == flt;
                            if ui
                                .add(
                                    egui::Button::new(
                                        RichText::new(lbl).size(11.5).color(if active {
                                            TEXT_PRIMARY
                                        } else {
                                            TEXT_SECONDARY
                                        }),
                                    )
                                    .fill(if active {
                                        SEGMENT_ACTIVE
                                    } else {
                                        Color32::TRANSPARENT
                                    })
                                    .stroke(if active {
                                        Stroke::new(1.0_f32, BORDER_CONTROL)
                                    } else {
                                        Stroke::NONE
                                    })
                                    .rounding(Rounding::same(6.0)),
                                )
                                .clicked()
                            {
                                self.step_filter = flt;
                            }
                        }
                    });
                });

            let filter_resp = ui.add(
                egui::TextEdit::singleline(&mut self.filter_query)
                    .desired_width(150.0)
                    .hint_text("Filter steps... (/)"),
            );
            if self.focus_filter_requested {
                self.focus_filter_requested = false;
                filter_resp.request_focus();
            }
            if !self.filter_query.is_empty() {
                let clear_clicked = ghost_button(ui, "\u{00D7}", TEXT_MUTED)
                    .on_hover_text("Clear filter (Esc)")
                    .clicked();
                let clear_esc =
                    filter_resp.has_focus() && ui.input(|i| i.key_pressed(egui::Key::Escape));
                if clear_clicked || clear_esc {
                    self.filter_query.clear();
                }
            }

            let undo_len = self.undo_stack.len();
            ui.add_enabled_ui(undo_len > 0, |ui| {
                if styled_button(ui, "Undo", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_SUBTLE)
                    .on_hover_text(format!("Undo ({undo_len} available)"))
                    .clicked()
                {
                    self.undo();
                }
            });
            let redo_len = self.redo_stack.len();
            ui.add_enabled_ui(redo_len > 0, |ui| {
                if styled_button(ui, "Redo", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_SUBTLE)
                    .on_hover_text(format!("Redo ({redo_len} available)"))
                    .clicked()
                {
                    self.redo();
                }
            });
            let has_actions = !self.profile.actions.is_empty();
            ui.add_enabled_ui(has_actions, |ui| {
                if styled_button(ui, "2\u{00D7}", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_SUBTLE)
                    .on_hover_text("Halve all wait durations (2\u{00D7} faster)")
                    .clicked()
                {
                    self.scale_wait_times(0.5);
                }
                if styled_button(
                    ui,
                    "0.5\u{00D7}",
                    BTN_NEUTRAL,
                    TEXT_SECONDARY,
                    BORDER_SUBTLE,
                )
                .on_hover_text("Double all wait durations (2\u{00D7} slower)")
                .clicked()
                {
                    self.scale_wait_times(2.0);
                }
                if styled_button(ui, "All On", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_SUBTLE)
                    .on_hover_text("Enable all steps in sequence")
                    .clicked()
                {
                    self.record_undo();
                    for a in &mut self.profile.actions {
                        a.enabled = true;
                    }
                    self.on_sequence_mutated(true);
                }
                if styled_button(ui, "All Off", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_SUBTLE)
                    .on_hover_text("Disable all steps in sequence")
                    .clicked()
                {
                    self.record_undo();
                    for a in &mut self.profile.actions {
                        a.enabled = false;
                    }
                    self.on_sequence_mutated(true);
                }
                if styled_button(ui, "Flip", BTN_NEUTRAL, TEXT_SECONDARY, BORDER_SUBTLE)
                    .on_hover_text("Reverse step sequence order")
                    .clicked()
                {
                    self.record_undo();
                    self.profile.actions.reverse();
                    self.on_sequence_mutated(true);
                    self.log_event("Reversed step sequence order.");
                }
            });

            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.label(
                    RichText::new(format!(
                        "{}/{} active \u{00B7} {}ms",
                        self.cached_enabled_count,
                        self.profile.actions.len(),
                        self.cached_cycle_wait_ms
                    ))
                    .monospace()
                    .size(11.0)
                    .color(TEXT_MUTED),
                );
            });
        });

        ui.add_space(6.0);

        // Two-Column Main Workspace
        let total_w = ui.available_width();
        let right_w = 335.0_f32.min((total_w * 0.34).max(300.0));
        let left_w = (total_w - right_w - 10.0).max(400.0);
        let avail_h = ui.available_height();

        ui.horizontal_top(|ui| {
            // Left: DSL Card + Virtualized Timeline + Quick-Add Footer
            ui.allocate_ui_with_layout(
                Vec2::new(left_w, avail_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    ui.set_width(left_w);
                    ui.set_max_width(left_w);
                    // Quick-Forge DSL Card
                    panel_frame().show(ui, |ui| {
                        ui.horizontal(|ui| {
                            egui::Frame::none()
                                .fill(BADGE_OK_BG)
                                .rounding(Rounding::same(5.0))
                                .inner_margin(egui::Margin::symmetric(7.0, 2.0))
                                .show(ui, |ui| {
                                    ui.label(
                                        RichText::new("DSL")
                                            .size(10.0)
                                            .strong()
                                            .color(ACCENT_SUCCESS),
                                    );
                                });
                            let entry_w = (ui.available_width() - 215.0).max(120.0);
                            let enter = ui
                                .add(
                                    egui::TextEdit::singleline(&mut self.dsl_input)
                                        .desired_width(entry_w)
                                        .font(egui::TextStyle::Monospace)
                                        .hint_text("e.g. Press E -> Wait 200ms -> Click -> Press 1"),
                                )
                                .lost_focus()
                                && ui.input(|i| i.key_pressed(egui::Key::Enter));
                            if enter
                                || styled_button(
                                    ui,
                                    "Replace",
                                    SEGMENT_ACTIVE,
                                    ACCENT_SUCCESS,
                                    BORDER_CONTROL,
                                )
                                .clicked()
                            {
                                self.apply_dsl(false);
                            }
                            if styled_button(
                                ui,
                                "+ Append",
                                BTN_NEUTRAL,
                                TEXT_PRIMARY,
                                BORDER_CONTROL,
                            )
                            .clicked()
                            {
                                self.apply_dsl(true);
                            }
                            if styled_button(
                                ui,
                                "Copy",
                                BTN_NEUTRAL,
                                TEXT_SECONDARY,
                                BORDER_CONTROL,
                            )
                            .clicked()
                            {
                                let recipe = format_actions_to_dsl(&self.profile.actions);
                                let len = recipe.len();
                                ui.ctx().copy_text(recipe);
                                self.show_toast(
                                    format!("Copied recipe to clipboard ({len} chars)."),
                                    ToastLevel::Info,
                                    3,
                                );
                            }
                        });

                        ui.add_space(4.0);
                        ui.horizontal(|ui| {
                            micro_heading(ui, "TEMPLATES");
                            for (lbl, expr) in [
                                ("E \u{00B7} Click \u{00B7} 1", "Press E -> Wait 200ms -> Click -> Press 1"),
                                (
                                    "Copy & Paste",
                                    "Press Ctrl+C -> Wait 80ms -> Press Tab -> Wait 80ms -> Press Ctrl+V",
                                ),
                                ("3\u{00D7} Repeat", "Repeat 3 { Press F -> Wait 120ms \u{00B1}15ms } -> Click"),
                                ("Shift + Click", "KeyDown Shift -> Click -> Wait 100ms -> KeyUp Shift"),
                            ] {
                                if styled_button(
                                    ui,
                                    lbl,
                                    BTN_SUBTLE,
                                    TEXT_SECONDARY,
                                    BORDER_SUBTLE,
                                )
                                .clicked()
                                {
                                    self.dsl_input = expr.to_string();
                                    self.apply_dsl(false);
                                }
                            }
                        });
                    });

                    ui.add_space(6.0);

                    // Scrollable Virtualized Step Timeline
                    let list_h = (ui.available_height() - 40.0).max(120.0);
                    ui.allocate_ui(Vec2::new(left_w, list_h), |ui| {
                        if visible.is_empty() {
                            let has_filter =
                                !self.filter_query.trim().is_empty() || self.step_filter != StepFilter::All;
                            let msg = if !self.profile.actions.is_empty() && has_filter {
                                "No steps match the current filter."
                            } else {
                                "No steps in macro sequence.\nSelect a Template above or click '+ Key', '+ Wait', '+ Click' below."
                            };
                            ui.centered_and_justified(|ui| {
                                ui.label(RichText::new(msg).color(TEXT_MUTED).size(12.5));
                            });
                        } else {
                            let mut pending_toggle: Option<(usize, bool)> = None;
                            let mut pending_select: Option<usize> = None;
                            let mut pending_edit: Option<usize> = None;
                            let mut pending_up: Option<usize> = None;
                            let mut pending_down: Option<usize> = None;
                            let mut pending_dup: Option<usize> = None;
                            let mut pending_del: Option<usize> = None;

                            egui::ScrollArea::vertical()
                                .id_salt("kf_timeline_scroll")
                                .auto_shrink([false, false])
                                .show_rows(ui, 60.0, visible.len(), |ui, range| {
                                    for &idx in &visible[range] {
                                        let Some(act) = self.profile.actions.get(idx) else {
                                            continue;
                                        };
                                        let is_active = telem
                                            .current_action_id
                                            .as_deref()
                                            .is_some_and(|id| id == act.id);
                                        let is_selected = self.selected_step_index == Some(idx);
                                        let (badge_bg, badge_fg, stripe) =
                                            action_visual_style(act.action_type);

                                        let hover_id = ui.make_persistent_id(("step_card_hover", idx));
                                        let was_hovered =
                                            ui.ctx().data(|d| d.get_temp::<bool>(hover_id).unwrap_or(false));
                                        let row_bg = if is_active {
                                            BG_CARD_ACTIVE
                                        } else if was_hovered {
                                            BG_CARD_HOVER
                                        } else {
                                            BG_CARD
                                        };
                                        let border = if is_active {
                                            ACCENT_PRIMARY
                                        } else if is_selected {
                                            BORDER_FOCUS
                                        } else {
                                            BORDER_SUBTLE
                                        };

                                        let resp = egui::Frame::none()
                                            .fill(row_bg)
                                            .stroke(Stroke::new(1.0_f32, border))
                                            .rounding(Rounding::same(10.0))
                                            .inner_margin(egui::Margin::symmetric(10.0, 8.0))
                                            .show(ui, |ui| {
                                                ui.horizontal(|ui| {
                                                    // Left accent stripe pill
                                                    let (stripe_rect, _) = ui.allocate_exact_size(
                                                        Vec2::new(3.0, 36.0),
                                                        egui::Sense::hover(),
                                                    );
                                                    ui.painter().rect_filled(
                                                        stripe_rect,
                                                        Rounding::same(2.0),
                                                        stripe,
                                                    );

                                                    let mut en = act.enabled;
                                                    if ui.checkbox(&mut en, "").changed() {
                                                        pending_toggle = Some((idx, en));
                                                    }

                                                    ui.label(
                                                        RichText::new(format!("#{:02}", idx + 1))
                                                            .monospace()
                                                            .strong()
                                                            .size(11.0)
                                                            .color(TEXT_MUTED),
                                                    );

                                                    ui.vertical(|ui| {
                                                        ui.horizontal(|ui| {
                                                            ui.label(
                                                                RichText::new(Self::step_headline(
                                                                    act,
                                                                ))
                                                                .strong()
                                                                .size(12.5)
                                                                .color(if act.enabled {
                                                                    TEXT_PRIMARY
                                                                } else {
                                                                    TEXT_MUTED
                                                                }),
                                                            );
                                                            egui::Frame::none()
                                                                .fill(badge_bg)
                                                                .rounding(Rounding::same(5.0))
                                                                .inner_margin(
                                                                    egui::Margin::symmetric(6.0, 1.0),
                                                                )
                                                                .show(ui, |ui| {
                                                                    ui.label(
                                                                        RichText::new(
                                                                            act.badge_text(),
                                                                        )
                                                                        .size(10.0)
                                                                        .strong()
                                                                        .color(badge_fg),
                                                                    );
                                                                });
                                                        });
                                                        let sub = Self::step_subtitle(act);
                                                        if !sub.is_empty() {
                                                            ui.label(
                                                                RichText::new(sub)
                                                                    .size(11.0)
                                                                    .color(TEXT_SECONDARY),
                                                            );
                                                        }
                                                    });

                                                    ui.with_layout(
                                                        egui::Layout::right_to_left(
                                                            egui::Align::Center,
                                                        ),
                                                        |ui| {
                                                            if ghost_button(
                                                                ui,
                                                                "\u{00D7}",
                                                                TEXT_MUTED,
                                                            )
                                                            .clicked()
                                                            {
                                                                pending_del = Some(idx);
                                                            }
                                                            if ghost_button(ui, "Dup", TEXT_MUTED)
                                                                .clicked()
                                                            {
                                                                pending_dup = Some(idx);
                                                            }
                                                            if ghost_button(
                                                                ui,
                                                                "Dn",
                                                                TEXT_MUTED,
                                                            )
                                                            .clicked()
                                                            {
                                                                pending_down = Some(idx);
                                                            }
                                                            if ghost_button(
                                                                ui,
                                                                "Up",
                                                                TEXT_MUTED,
                                                            )
                                                            .clicked()
                                                            {
                                                                pending_up = Some(idx);
                                                            }
                                                            if styled_button(
                                                                ui,
                                                                "Edit",
                                                                BTN_NEUTRAL,
                                                                TEXT_PRIMARY,
                                                                BORDER_CONTROL,
                                                            )
                                                            .clicked()
                                                            {
                                                                pending_edit = Some(idx);
                                                            }
                                                            egui::Frame::none()
                                                                .fill(BG_INPUT)
                                                                .rounding(Rounding::same(6.0))
                                                                .inner_margin(
                                                                    egui::Margin::symmetric(8.0, 3.0),
                                                                )
                                                                .show(ui, |ui| {
                                                                    ui.label(
                                                                        RichText::new(
                                                                            Self::step_metric_pill(
                                                                                act,
                                                                            ),
                                                                        )
                                                                        .monospace()
                                                                        .size(11.0)
                                                                        .color(TEXT_SECONDARY),
                                                                    );
                                                                });
                                                        },
                                                    );
                                                });
                                            })
                                            .response;

                                        let hovered_now = resp.hovered()
                                            || ui.rect_contains_pointer(resp.rect);
                                        ui.ctx().data_mut(|d| d.insert_temp(hover_id, hovered_now));

                                        let interact = ui.interact(
                                            resp.rect,
                                            ui.id().with(("step_row", idx)),
                                            egui::Sense::click(),
                                        );
                                        if interact.double_clicked() {
                                            pending_edit = Some(idx);
                                        } else if interact.clicked() {
                                            pending_select = Some(idx);
                                        }
                                        ui.add_space(4.0);
                                    }
                                });

                            if let Some(idx) = pending_select {
                                self.selected_step_index = Some(idx);
                            }
                            if let Some((idx, en)) = pending_toggle {
                                self.record_undo();
                                self.profile.actions[idx].enabled = en;
                                self.on_sequence_mutated(true);
                            }
                            if let Some(idx) = pending_up {
                                if idx > 0 {
                                    self.record_undo();
                                    self.profile.actions.swap(idx - 1, idx);
                                    self.selected_step_index = Some(idx - 1);
                                    self.on_sequence_mutated(true);
                                }
                            }
                            if let Some(idx) = pending_down {
                                if idx + 1 < self.profile.actions.len() {
                                    self.record_undo();
                                    self.profile.actions.swap(idx, idx + 1);
                                    self.selected_step_index = Some(idx + 1);
                                    self.on_sequence_mutated(true);
                                }
                            }
                            if let Some(idx) = pending_dup {
                                self.record_undo();
                                let cloned = self.profile.actions[idx].clone_with_new_id();
                                self.profile.actions.insert(idx + 1, cloned);
                                self.selected_step_index = Some(idx + 1);
                                self.on_sequence_mutated(true);
                            }
                            if let Some(idx) = pending_del {
                                self.record_undo();
                                let removed = self.profile.actions.remove(idx);
                                self.selected_step_index = if self.profile.actions.is_empty() {
                                    None
                                } else {
                                    Some(idx.min(self.profile.actions.len() - 1))
                                };
                                self.on_sequence_mutated(true);
                                self.log_event(&format!(
                                    "Deleted step #{}: {}",
                                    idx + 1,
                                    removed.describe()
                                ));
                            }
                            if let Some(idx) = pending_edit {
                                if let Some(act) = self.profile.actions.get(idx) {
                                    self.step_dialog = Some(StepEditorState::from_action(act));
                                }
                            }
                        }
                    });

                    // Quick-Add Footer Bar
                    ui.horizontal(|ui| {
                        let quick_adds: Vec<(&str, Result<MacroAction, String>)> = vec![
                            ("+ Key", make_key_press("E", 25, "")),
                            ("+ Wait", make_wait(200, 10, "")),
                            (
                                "+ Click",
                                make_mouse_click(
                                    MouseButton::Left,
                                    ClickType::Single,
                                    None,
                                    None,
                                    25,
                                    "",
                                ),
                            ),
                            ("+ Move", make_mouse_move(500, 500, MoveMode::Absolute, "")),
                            ("+ Scroll", make_mouse_scroll(-3, 0, "")),
                        ];
                        for (lbl, res_act) in quick_adds {
                            if styled_button(ui, lbl, BG_PANEL, TEXT_PRIMARY, BORDER_SUBTLE)
                                .clicked()
                            {
                                if let Ok(act) = res_act {
                                    self.record_undo();
                                    let desc = act.describe();
                                    self.profile.actions.push(act);
                                    self.selected_step_index =
                                        Some(self.profile.actions.len() - 1);
                                    self.on_sequence_mutated(true);
                                    self.log_event(&format!("Added step: {desc}"));
                                }
                            }
                        }
                        if styled_button(
                            ui,
                            "+ Custom...",
                            SEGMENT_ACTIVE,
                            ACCENT_SUCCESS,
                            BORDER_CONTROL,
                        )
                        .clicked()
                        {
                            self.step_dialog = Some(StepEditorState::for_new());
                        }
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if styled_button(
                                ui,
                                "Clear",
                                Color32::TRANSPARENT,
                                BTN_DANGER_SUBTLE_TEXT,
                                BTN_DANGER_BORDER,
                            )
                            .clicked()
                            {
                                if self.executor.is_running() {
                                    self.executor.stop(Duration::from_secs(2));
                                }
                                self.record_undo();
                                self.profile.actions.clear();
                                self.selected_step_index = None;
                                self.on_sequence_mutated(true);
                                self.log_event("Cleared all steps from sequence.");
                            }
                        });
                    });
                },
            );

            ui.add_space(6.0);

            // Right Slim Inspector Card (4 Tabs)
            ui.allocate_ui_with_layout(
                Vec2::new(right_w, avail_h),
                egui::Layout::top_down(egui::Align::LEFT),
                |ui| {
                    ui.set_width(right_w);
                    ui.set_max_width(right_w);
                    panel_frame().show(ui, |ui| {
                        ui.set_width((right_w - 24.0).max(200.0));
                        ui.set_min_height((avail_h - 24.0).max(200.0));
                        egui::Frame::none()
                            .fill(BG_INPUT)
                            .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                            .rounding(Rounding::same(8.0))
                            .inner_margin(egui::Margin::same(3.0))
                            .show(ui, |ui| {
                                ui.columns(4, |cols| {
                                    for (i, (tab, lbl)) in [
                                        (InspectorTab::LoopKeys, "Loop"),
                                        (InspectorTab::Recorder, "Recorder"),
                                        (InspectorTab::Security, "Security"),
                                        (InspectorTab::Console, "Console"),
                                    ]
                                    .into_iter()
                                    .enumerate()
                                    {
                                        let active = self.inspector_tab == tab;
                                        if cols[i]
                                            .add_sized(
                                                [cols[i].available_width(), 24.0],
                                                egui::Button::new(
                                                    RichText::new(lbl).size(11.0).strong().color(
                                                        if active {
                                                            TEXT_PRIMARY
                                                        } else {
                                                            TEXT_SECONDARY
                                                        },
                                                    ),
                                                )
                                                .fill(if active {
                                                    SEGMENT_ACTIVE
                                                } else {
                                                    Color32::TRANSPARENT
                                                })
                                                .stroke(Stroke::NONE)
                                                .rounding(Rounding::same(6.0)),
                                            )
                                            .clicked()
                                        {
                                            self.inspector_tab = tab;
                                            if tab == InspectorTab::Security && self.security_dirty {
                                                self.refresh_security_details();
                                            }
                                        }
                                    }
                                });
                            });

                        ui.add_space(8.0);
                        match self.inspector_tab {
                            InspectorTab::LoopKeys => {
                                card_frame().show(ui, |ui| {
                                    micro_heading(ui, "REPEAT MODE & TIMING");
                                    ui.add_space(4.0);
                                    egui::Frame::none()
                                        .fill(BG_INPUT)
                                        .rounding(Rounding::same(7.0))
                                        .inner_margin(egui::Margin::same(2.0))
                                        .show(ui, |ui| {
                                            ui.columns(3, |cols| {
                                                for (i, (m, lbl)) in [
                                                    (RepeatMode::Count, "Fixed"),
                                                    (RepeatMode::Infinite, "Infinite"),
                                                    (RepeatMode::Duration, "Duration"),
                                                ]
                                                .into_iter()
                                                .enumerate()
                                                {
                                                    let active = self.profile.repeat_mode == m;
                                                    if cols[i]
                                                        .add_sized(
                                                            [cols[i].available_width(), 24.0],
                                                            egui::Button::new(
                                                                RichText::new(lbl)
                                                                    .size(11.0)
                                                                    .strong()
                                                                    .color(TEXT_PRIMARY),
                                                            )
                                                            .fill(if active {
                                                                SEGMENT_ACTIVE
                                                            } else {
                                                                Color32::TRANSPARENT
                                                            })
                                                            .stroke(Stroke::NONE),
                                                        )
                                                        .clicked()
                                                        && self.profile.repeat_mode != m
                                                    {
                                                        self.profile.repeat_mode = m;
                                                        self.sync_profile_settings_to_ui();
                                                        self.sync_ui_settings_to_profile();
                                                    }
                                                }
                                            });
                                        });

                                    ui.add_space(6.0);
                                    egui::Grid::new("loop_grid")
                                        .num_columns(2)
                                        .spacing([8.0, 6.0])
                                        .show(ui, |ui| {
                                            if self.profile.repeat_mode != RepeatMode::Infinite {
                                                let lbl = if self.profile.repeat_mode
                                                    == RepeatMode::Count
                                                {
                                                    "Repeat Count:"
                                                } else {
                                                    "Duration (s):"
                                                };
                                                ui.label(
                                                    RichText::new(lbl)
                                                        .size(11.5)
                                                        .color(TEXT_SECONDARY),
                                                );
                                                ui.add(
                                                    egui::TextEdit::singleline(
                                                        &mut self.repeat_val_str,
                                                    )
                                                    .desired_width(90.0),
                                                );
                                                ui.end_row();
                                            }
                                            ui.label(
                                                RichText::new("Inter-Cycle Delay (ms):")
                                                    .size(11.5)
                                                    .color(TEXT_SECONDARY),
                                            );
                                            ui.add(
                                                egui::TextEdit::singleline(
                                                    &mut self.cycle_delay_str,
                                                )
                                                .desired_width(90.0),
                                            );
                                            ui.end_row();
                                        });

                                    ui.add_space(10.0);
                                    micro_heading(ui, "GLOBAL HOTKEY BINDINGS");
                                    ui.add_space(4.0);

                                    for (lbl, target, val) in [
                                        (
                                            "Start / Stop:",
                                            KeyCaptureTarget::HotkeyStart,
                                            &mut self.hk_start_str,
                                        ),
                                        (
                                            "Recorder:",
                                            KeyCaptureTarget::HotkeyRec,
                                            &mut self.hk_rec_str,
                                        ),
                                        (
                                            "Panic Stop:",
                                            KeyCaptureTarget::HotkeyPanic,
                                            &mut self.hk_panic_str,
                                        ),
                                    ] {
                                        ui.horizontal(|ui| {
                                            ui.label(
                                                RichText::new(lbl)
                                                    .size(11.5)
                                                    .color(TEXT_SECONDARY),
                                            );
                                            ui.with_layout(
                                                egui::Layout::right_to_left(egui::Align::Center),
                                                |ui| {
                                                    let cap_on =
                                                        self.key_capture_target == Some(target);
                                                    if styled_button(
                                                        ui,
                                                        if cap_on { "Press..." } else { "Bind" },
                                                        if cap_on {
                                                            ACCENT_WARNING
                                                        } else {
                                                            BTN_NEUTRAL
                                                        },
                                                        if cap_on {
                                                            Color32::from_rgb(0x09, 0x0A, 0x0D)
                                                        } else {
                                                            TEXT_PRIMARY
                                                        },
                                                        BORDER_CONTROL,
                                                    )
                                                    .clicked()
                                                    {
                                                        self.key_capture_target = if cap_on {
                                                            None
                                                        } else {
                                                            Some(target)
                                                        };
                                                    }
                                                    ui.add(
                                                        egui::TextEdit::singleline(val)
                                                            .desired_width(72.0),
                                                    );
                                                },
                                            );
                                        });
                                    }

                                    ui.add_space(8.0);
                                    if ui
                                        .add_sized(
                                            [ui.available_width(), 28.0],
                                            egui::Button::new(
                                                RichText::new("Apply Loop & Hotkey Settings")
                                                    .size(11.5)
                                                    .strong()
                                                    .color(ACCENT_SUCCESS),
                                            )
                                            .fill(SEGMENT_ACTIVE)
                                            .stroke(Stroke::new(1.0_f32, BORDER_CONTROL))
                                            .rounding(Rounding::same(7.0)),
                                        )
                                        .clicked()
                                    {
                                        self.sync_ui_settings_to_profile();
                                        self.save_persistent_state();
                                    }
                                });
                            }
                            InspectorTab::Recorder => {
                                card_frame().show(ui, |ui| {
                                    micro_heading(ui, "LIVE INPUT RECORDER");
                                    ui.add_space(6.0);
                                    let is_rec = self.recorder.is_recording();
                                    let (txt, fill, fg, border) = if is_rec {
                                        (
                                            format!(
                                                "\u{25A0} Stop Recording ({})",
                                                self.profile.record_hotkey
                                            ),
                                            ACCENT_WARNING,
                                            CTA_TEXT,
                                            ACCENT_WARNING,
                                        )
                                    } else {
                                        (
                                            format!(
                                                "\u{2022} Record Live Inputs ({})",
                                                self.profile.record_hotkey
                                            ),
                                            BTN_DANGER_SUBTLE,
                                            BTN_DANGER_SUBTLE_TEXT,
                                            BTN_DANGER_BORDER,
                                        )
                                    };
                                    if ui
                                        .add_sized(
                                            [ui.available_width(), 34.0],
                                            egui::Button::new(
                                                RichText::new(txt).size(12.0).strong().color(fg),
                                            )
                                            .fill(fill)
                                            .stroke(Stroke::new(1.0_f32, border))
                                            .rounding(Rounding::same(8.0)),
                                        )
                                        .clicked()
                                    {
                                        self.toggle_recording();
                                    }
                                    ui.add_space(8.0);
                                    ui.checkbox(
                                        &mut self.rec_delays,
                                        "Record delays between actions (>= 20ms)",
                                    );
                                    ui.checkbox(
                                        &mut self.rec_merge_taps,
                                        "Coalesce KeyDown + KeyUp into Press",
                                    );
                                    ui.checkbox(
                                        &mut self.rec_capture_coords,
                                        "Lock clicks to exact (X, Y) coordinates",
                                    );
                                    ui.add_space(8.0);
                                    ui.add(
                                        egui::Label::new(
                                            RichText::new("Recorded steps filter out kTap synthetic playback inputs and your control hotkeys.")
                                                .size(11.0)
                                                .color(TEXT_SECONDARY),
                                        )
                                        .wrap(),
                                    );
                                });
                            }
                            InspectorTab::Security => {
                                card_frame().show(ui, |ui| {
                                    micro_heading(ui, "CRYPTOGRAPHIC INTEGRITY & AUDIT");
                                    ui.add_space(4.0);
                                    let sha_short = if self.cached_sha256.len() >= 32 {
                                        format!(
                                            "{}...{}",
                                            &self.cached_sha256[..20],
                                            &self.cached_sha256[self.cached_sha256.len() - 8..]
                                        )
                                    } else {
                                        self.cached_sha256.clone()
                                    };
                                    ui.label(
                                        RichText::new(format!("SHA-256: {sha_short}"))
                                            .monospace()
                                            .size(11.0)
                                            .color(ACCENT_SUCCESS),
                                    );
                                    ui.label(
                                        RichText::new(format!(
                                            "Complexity: {} / 5,000 steps | {} / 100,000 ops",
                                            self.cached_nodes, self.cached_ops
                                        ))
                                        .size(11.0)
                                        .color(TEXT_SECONDARY),
                                    );
                                    ui.add_space(6.0);
                                    egui::Frame::none()
                                        .fill(BG_INPUT)
                                        .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                                        .rounding(Rounding::same(8.0))
                                        .inner_margin(egui::Margin::same(8.0))
                                        .show(ui, |ui| {
                                            egui::ScrollArea::vertical()
                                                .max_height((avail_h - 190.0).max(120.0))
                                                .show(ui, |ui| {
                                                    let report_str = if self.last_audit.is_clean() {
                                                        "STATIC SECURITY AUDIT PASSED\n\n\
                                                         \u{2022} Zero arbitrary code execution vectors\n\
                                                         \u{2022} Safe cycle wait timing verified\n\
                                                         \u{2022} All key/mouse down states balanced\n\
                                                         \u{2022} No suspicious shell shortcuts detected"
                                                            .to_string()
                                                    } else {
                                                        self.last_audit
                                                            .findings
                                                            .iter()
                                                            .map(|f| {
                                                                format!(
                                                                    "[{}] ({}) {}\n  Detail: {}\n  Fix:    {}",
                                                                    f.code,
                                                                    f.severity.as_str().to_uppercase(),
                                                                    f.title,
                                                                    f.description,
                                                                    f.remediation
                                                                )
                                                            })
                                                            .collect::<Vec<_>>()
                                                            .join("\n\n")
                                                    };
                                                    ui.add(
                                                        egui::Label::new(
                                                            RichText::new(report_str)
                                                                .monospace()
                                                                .size(11.0)
                                                                .color(TEXT_PRIMARY),
                                                        )
                                                        .wrap(),
                                                    );
                                                });
                                        });
                                });
                            }
                            InspectorTab::Console => {
                                ui.horizontal(|ui| {
                                    micro_heading(ui, "EVENT & TELEMETRY LOG");
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            if styled_button(
                                                ui,
                                                "Clear",
                                                BTN_NEUTRAL,
                                                TEXT_SECONDARY,
                                                BORDER_CONTROL,
                                            )
                                            .clicked()
                                            {
                                                self.console_lines.clear();
                                            }
                                        },
                                    );
                                });
                                ui.add_space(4.0);
                                egui::Frame::none()
                                    .fill(BG_INPUT)
                                    .stroke(Stroke::new(1.0_f32, BORDER_SUBTLE))
                                    .rounding(Rounding::same(8.0))
                                    .inner_margin(egui::Margin::same(8.0))
                                    .show(ui, |ui| {
                                        egui::ScrollArea::vertical()
                                            .stick_to_bottom(true)
                                            .max_height((avail_h - 120.0).max(140.0))
                                            .show(ui, |ui| {
                                                let log_text = self.console_lines.join("\n");
                                                ui.add(
                                                    egui::Label::new(
                                                        RichText::new(log_text)
                                                            .monospace()
                                                            .size(10.5)
                                                            .color(TEXT_SECONDARY),
                                                    )
                                                    .wrap(),
                                                );
                                            });
                                    });
                            }
                        }
                    });
                },
            );
        });
    }
}

impl eframe::App for KTapApp {
    fn update(&mut self, ctx: &Context, _frame: &mut eframe::Frame) {
        if !self.branded_window {
            self.branded_window = true;
            apply_windows_window_branding();
        }
        if self.brand_tex.is_none() {
            self.brand_tex = load_brand_texture(ctx);
        }

        self.handle_background_events(ctx);

        let telem = self.executor.telemetry();
        let is_running = self.executor.is_running()
            || matches!(
                telem.state,
                ExecutorState::Running | ExecutorState::Stopping
            );
        self.hotkey_manager
            .set_active_mode(is_running || self.recorder.is_recording());

        if telem.state == ExecutorState::Error {
            if let Some(ref err) = telem.last_error {
                if self.last_telem_error.as_ref() != Some(err) {
                    let msg = err.clone();
                    self.last_telem_error = Some(msg.clone());
                    self.log_event(&format!("[Execution Error] {msg}"));
                    self.show_toast(format!("Execution Error: {msg}"), ToastLevel::Error, 6);
                }
            }
        }

        // Poll OS cursor periodically only in Studio mode when visible
        let is_focused = ctx.input(|i| i.focused);
        if self.ui_mode == UiMode::Studio
            && !self.mini_hud_active
            && (is_running || is_focused)
            && self.last_cursor_poll.elapsed() >= Duration::from_millis(120)
        {
            self.last_cursor_poll = Instant::now();
            if self.platform_caps.can_poll_cursor {
                self.cursor_xy = platform_get_cursor_pos();
            }
        }

        let visible = self.visible_step_indices();
        self.handle_shortcuts(ctx, &visible);

        // Mini-HUD viewport if toggled on
        if self.mini_hud_active && ctx.input(|i| i.viewport().minimized == Some(false) && i.focused)
        {
            self.mini_hud_active = false;
        }
        if self.mini_hud_active {
            let cd_s = self.countdown_deadline.map(|d| {
                d.saturating_duration_since(Instant::now())
                    .as_secs_f32()
                    .max(0.1)
            });
            match show_mini_hud(
                ctx,
                &telem,
                is_running,
                cd_s,
                &self.profile.start_stop_hotkey,
                &self.profile.panic_hotkey,
            ) {
                MiniHudAction::None => {}
                MiniHudAction::ToggleExecution => self.toggle_execution(),
                MiniHudAction::PanicStop => self.trigger_panic_stop(),
                MiniHudAction::Expand => {
                    self.mini_hud_active = false;
                    ctx.send_viewport_cmd(ViewportCommand::Minimized(false));
                    ctx.send_viewport_cmd(ViewportCommand::Focus);
                }
            }
        }

        self.draw_top_bar(ctx);
        self.draw_status_bar(ctx);

        egui::CentralPanel::default()
            .frame(
                egui::Frame::none()
                    .fill(BG_ROOT)
                    .inner_margin(egui::Margin::symmetric(14.0, 10.0)),
            )
            .show(ctx, |ui| {
                self.draw_banners(ui);
                match self.ui_mode {
                    UiMode::Simple => self.draw_simple_mode(ui, &telem, is_running),
                    UiMode::Studio => self.draw_studio_workspace(ui, &telem, is_running, &visible),
                }
            });

        // Step Editor Modal
        if let Some(mut dlg) = self.step_dialog.take() {
            match dlg.show(ctx) {
                StepDialogOutcome::Open => self.step_dialog = Some(dlg),
                StepDialogOutcome::Cancelled => {}
                StepDialogOutcome::Saved { edit_id, action } => {
                    self.record_undo();
                    let desc = action.describe();
                    if let Some(eid) = edit_id {
                        if let Some(pos) = self.profile.actions.iter().position(|a| a.id == eid) {
                            self.profile.actions[pos] = *action;
                            self.log_event(&format!("Updated step #{}: {desc}", pos + 1));
                        } else {
                            self.show_toast(
                                "Edited step was removed before saving.",
                                ToastLevel::Warning,
                                4,
                            );
                        }
                    } else {
                        self.profile.actions.push(*action);
                        self.selected_step_index = Some(self.profile.actions.len() - 1);
                        self.log_event(&format!("Added step: {desc}"));
                    }
                    self.on_sequence_mutated(true);
                }
            }
        }

        // Software Update Modal
        if let Some(mut upd) = self.update_dialog.take() {
            match upd.show(ctx) {
                UpdateDialogAction::Open => self.update_dialog = Some(upd),
                UpdateDialogAction::Close => {}
                UpdateDialogAction::Checked(rel) => {
                    self.latest_release = Some(*rel);
                    self.update_dialog = Some(upd);
                }
                UpdateDialogAction::Restarting => {
                    self.save_persistent_state();
                    self.hotkey_manager.stop();
                    if self.recorder.is_recording() {
                        let _ = self.recorder.stop();
                    }
                    if self.executor.is_running() {
                        self.executor.panic_abort(Duration::from_millis(200));
                    }
                    self.os_backend.close();
                    ctx.send_viewport_cmd(ViewportCommand::Close);
                }
            }
        }

        // Save Preset Modal
        if let Some(mut name_buf) = self.save_preset_modal.take() {
            let mut keep_open = true;
            egui::Window::new("Save Custom Preset")
                .collapsible(false)
                .resizable(false)
                .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
                .fixed_size([360.0, 120.0])
                .show(ctx, |ui| {
                    ui.label("Enter a name for your custom preset:");
                    let enter = ui
                        .add(
                            egui::TextEdit::singleline(&mut name_buf)
                                .desired_width(ui.available_width()),
                        )
                        .lost_focus()
                        && ui.input(|i| i.key_pressed(egui::Key::Enter));
                    ui.add_space(8.0);
                    ui.horizontal(|ui| {
                        if styled_button(ui, "Cancel", BTN_NEUTRAL, TEXT_PRIMARY, BORDER_CONTROL)
                            .clicked()
                        {
                            keep_open = false;
                        }
                        if enter
                            || styled_button(ui, "Save", CTA_SUCCESS, CTA_TEXT, CTA_SUCCESS)
                                .clicked()
                        {
                            let saved = self.save_custom_preset_named(&name_buf);
                            if saved {
                                keep_open = false;
                            }
                        }
                    });
                });
            if keep_open {
                self.save_preset_modal = Some(name_buf);
            }
        }

        // Adaptive repaint schedule for low idle CPU & instant active telemetry
        let high_freq = is_running
            || self.recorder.is_recording()
            || self.countdown_deadline.is_some()
            || self.startup_update_checking
            || self.update_dialog.as_ref().is_some_and(|d| d.is_busy())
            || self.ss_slider_dirty_at.is_some();
        if high_freq {
            ctx.request_repaint_after(Duration::from_millis(50));
        } else if self.mini_hud_active {
            ctx.request_repaint_after(Duration::from_millis(120));
        } else {
            ctx.request_repaint_after(Duration::from_millis(1000));
        }
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.save_persistent_state();
        self.hotkey_manager.stop();
        if self.recorder.is_recording() {
            let _ = self.recorder.stop();
        }
        if self.executor.is_running() {
            self.executor.panic_abort(Duration::from_millis(200));
        }
        self.os_backend.close();
    }
}

#[cfg(feature = "screenshots")]
impl KTapApp {
    pub fn capture_screenshots(out_dir: std::path::PathBuf) -> eframe::Result<()> {
        let mut vp = egui::ViewportBuilder::default()
            .with_inner_size([1240.0, 800.0])
            .with_min_inner_size([320.0, 64.0]);
        if let Some(icon) = load_window_icon_data() {
            vp = vp.with_icon(icon);
        }
        let options = eframe::NativeOptions {
            viewport: vp,
            ..Default::default()
        };

        #[derive(Clone, Copy)]
        enum Scene {
            Studio,
            Security,
            Simple,
            StepInspector,
            Updater,
            MiniHud,
        }

        struct ScreenshotRunner {
            app: KTapApp,
            out_dir: std::path::PathBuf,
            step_idx: usize,
            wait_frames: u32,
            awaiting_shot: bool,
        }

        impl ScreenshotRunner {
            const SCENES: [Scene; 6] = [
                Scene::Studio,
                Scene::Security,
                Scene::Simple,
                Scene::StepInspector,
                Scene::Updater,
                Scene::MiniHud,
            ];

            fn filename(scene: Scene) -> &'static str {
                match scene {
                    Scene::Studio => "studio_mode.png",
                    Scene::Security => "security_audit.png",
                    Scene::Simple => "simple_mode.png",
                    Scene::StepInspector => "step_inspector.png",
                    Scene::Updater => "updater_modal.png",
                    Scene::MiniHud => "mini_hud.png",
                }
            }

            fn configure_scene(&mut self, ctx: &Context, scene: Scene) {
                self.app.step_dialog = None;
                self.app.update_dialog = None;
                self.app.mini_hud_active = false;
                self.app.selected_step_index = None;
                match scene {
                    Scene::Studio => {
                        ctx.send_viewport_cmd(ViewportCommand::Decorations(true));
                        ctx.send_viewport_cmd(ViewportCommand::InnerSize(egui::vec2(
                            1240.0, 800.0,
                        )));
                        self.app.ui_mode = UiMode::Studio;
                        self.app.inspector_tab = InspectorTab::LoopKeys;
                        self.app.selected_step_index = Some(0);
                    }
                    Scene::Security => {
                        ctx.send_viewport_cmd(ViewportCommand::Decorations(true));
                        ctx.send_viewport_cmd(ViewportCommand::InnerSize(egui::vec2(
                            1240.0, 800.0,
                        )));
                        self.app.ui_mode = UiMode::Studio;
                        self.app.inspector_tab = InspectorTab::Security;
                        self.app.refresh_security_details();
                    }
                    Scene::Simple => {
                        ctx.send_viewport_cmd(ViewportCommand::Decorations(true));
                        ctx.send_viewport_cmd(ViewportCommand::InnerSize(egui::vec2(
                            1120.0, 760.0,
                        )));
                        self.app.ui_mode = UiMode::Simple;
                        self.app.simple_task = SimpleTask::Spammer;
                        self.app.ss_interval_ms = 50;
                        self.app.apply_simple_spammer_to_profile(false);
                    }
                    Scene::StepInspector => {
                        ctx.send_viewport_cmd(ViewportCommand::Decorations(true));
                        ctx.send_viewport_cmd(ViewportCommand::InnerSize(egui::vec2(960.0, 640.0)));
                        self.app.ui_mode = UiMode::Studio;
                        self.app.inspector_tab = InspectorTab::LoopKeys;
                        if let Some(act) = self.app.profile.actions.first() {
                            self.app.step_dialog = Some(StepEditorState::from_action(act));
                        }
                    }
                    Scene::Updater => {
                        ctx.send_viewport_cmd(ViewportCommand::Decorations(true));
                        ctx.send_viewport_cmd(ViewportCommand::InnerSize(egui::vec2(960.0, 620.0)));
                        self.app.ui_mode = UiMode::Studio;
                        self.app.update_dialog = Some(UpdateDialogState::new(Some(ReleaseInfo {
                            tag_name: "v1.0.0".to_string(),
                            version: "1.0.0".to_string(),
                            name: "1.0.0".to_string(),
                            body: "kTap 1.0.0 — Initial native Rust release with Simple & Studio modes, Linux Wayland + X11, macOS, Windows engines, SHA-256 signed profiles, and verified auto-updater.".to_string(),
                            html_url: "https://github.com/efeartn/kTap/releases/tag/v1.0.0".to_string(),
                            published_at: "2026-11-28T00:00:00Z".to_string(),
                            prerelease: false,
                            asset_name: Some("kTap-Windows-x64.exe".to_string()),
                            download_url: Some("https://github.com/efeartn/kTap/releases/download/v1.0.0/kTap-Windows-x64.exe".to_string()),
                            asset_size: Some(5_847_552),
                            sha256: Some("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855".to_string()),
                            checksum_url: None,
                            is_update_available: false,
                        })));
                    }
                    Scene::MiniHud => {
                        ctx.send_viewport_cmd(ViewportCommand::Decorations(false));
                        ctx.send_viewport_cmd(ViewportCommand::InnerSize(egui::vec2(432.0, 70.0)));
                    }
                }
                self.wait_frames = 0;
                self.awaiting_shot = false;
            }
        }

        impl eframe::App for ScreenshotRunner {
            fn update(&mut self, ctx: &Context, frame: &mut eframe::Frame) {
                if self.step_idx >= Self::SCENES.len() {
                    ctx.send_viewport_cmd(ViewportCommand::Close);
                    return;
                }
                let captured = ctx.input(|i| {
                    i.events.iter().find_map(|e| match e {
                        egui::Event::Screenshot { image, .. } => Some(image.clone()),
                        _ => None,
                    })
                });
                if let Some(img) = captured {
                    let scene = Self::SCENES[self.step_idx];
                    let path = self.out_dir.join(Self::filename(scene));
                    crate::ui::theme::save_screenshot_image(&img, &path);
                    self.step_idx += 1;
                    self.awaiting_shot = false;
                    if self.step_idx >= Self::SCENES.len() {
                        ctx.send_viewport_cmd(ViewportCommand::Close);
                        return;
                    }
                    self.configure_scene(ctx, Self::SCENES[self.step_idx]);
                }

                let scene = Self::SCENES[self.step_idx];
                if matches!(scene, Scene::MiniHud) {
                    let telem = self.app.executor.telemetry();
                    let _ = crate::ui::mini_hud::draw_mini_hud_contents(
                        ctx,
                        &telem,
                        false,
                        None,
                        &self.app.profile.start_stop_hotkey,
                        &self.app.profile.panic_hotkey,
                    );
                } else {
                    self.app.update(ctx, frame);
                }

                self.wait_frames += 1;
                if self.wait_frames >= 8 && !self.awaiting_shot {
                    self.awaiting_shot = true;
                    ctx.send_viewport_cmd(ViewportCommand::Screenshot);
                }
                ctx.request_repaint();
            }

            fn on_exit(&mut self, gl: Option<&eframe::glow::Context>) {
                self.app.on_exit(gl);
            }
        }

        let title = format!("kTap {}", full_version_label());
        eframe::run_native(
            &title,
            options,
            Box::new(move |cc| {
                apply_theme(&cc.egui_ctx);
                let mut runner = ScreenshotRunner {
                    app: KTapApp::new(cc.egui_ctx.clone(), false),
                    out_dir,
                    step_idx: 0,
                    wait_frames: 0,
                    awaiting_shot: false,
                };
                runner.configure_scene(&cc.egui_ctx, ScreenshotRunner::SCENES[0]);
                Ok(Box::new(runner))
            }),
        )
    }
}
