//! Crash-safe platform configuration persistence and active profile backup for KeyForge.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::models::keymap::normalize_key_combo;
use crate::models::profile::MacroProfile;
use crate::security::integrity::{
    atomic_write_bytes, load_profile_secure, parse_and_verify_envelope, save_profile_secure,
};
use crate::security::validator::{SecurityValidationError, MAX_PROFILE_FILE_BYTES};

pub const CONFIG_FILENAME: &str = "config.json";
pub const CONFIG_BACKUP_FILENAME: &str = "config.json.bak";
pub const ACTIVE_PROFILE_FILENAME: &str = "active_profile.kfmacro.json";
pub const ACTIVE_PROFILE_BACKUP_FILENAME: &str = "active_profile.kfmacro.json.bak";
pub const PRESETS_DIRNAME: &str = "presets";
pub const MAX_CONFIG_FILE_BYTES: usize = 1_048_576;

pub fn is_test_binary_env() -> bool {
    if cfg!(test) {
        return true;
    }
    if std::env::var("KEYFORGE_TEST_ISOLATE")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
    {
        return true;
    }
    if let Ok(exe) = std::env::current_exe() {
        let norm = exe.to_string_lossy().replace('\\', "/");
        let stem = exe.file_stem().and_then(|s| s.to_str()).unwrap_or_default();
        if stem.starts_with("integration_tests")
            || (norm.contains("/deps/") && norm.contains("/target/"))
            || (norm.contains("/build/")
                && norm.contains("/out/")
                && !stem.eq_ignore_ascii_case("keyforge"))
        {
            return true;
        }
    }
    false
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SimpleClickerConfig {
    pub button: String,
    pub click_type: String,
    pub interval_ms: i64,
    pub jitter_enabled: bool,
    pub loc_mode: String,
    pub x: i64,
    pub y: i64,
}

impl Default for SimpleClickerConfig {
    fn default() -> Self {
        Self {
            button: "Left".to_string(),
            click_type: "Single".to_string(),
            interval_ms: 100,
            jitter_enabled: true,
            loc_mode: "cursor".to_string(),
            x: 0,
            y: 0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SimpleSpammerConfig {
    pub key: String,
    pub interval_ms: i64,
    pub jitter_enabled: bool,
}

impl Default for SimpleSpammerConfig {
    fn default() -> Self {
        Self {
            key: "E".to_string(),
            interval_ms: 100,
            jitter_enabled: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RecorderSettingsConfig {
    pub record_delays: bool,
    pub merge_key_taps: bool,
    pub capture_click_coordinates: bool,
}

impl Default for RecorderSettingsConfig {
    fn default() -> Self {
        Self {
            record_delays: true,
            merge_key_taps: true,
            capture_click_coordinates: false,
        }
    }
}

/// Persistent application configuration matching Python KeyForge v1.x JSON schema.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppConfig {
    pub schema_version: i64,
    pub ui_mode: String,
    pub simple_task: String,
    pub window_geometry: Option<String>,
    pub start_stop_hotkey: String,
    pub record_hotkey: String,
    pub panic_hotkey: String,
    pub dry_run: bool,
    pub check_updates_on_startup: bool,
    pub auto_save_active_profile: bool,
    pub start_countdown_sec: i64,
    pub last_profile_path: Option<String>,
    pub simple_clicker: SimpleClickerConfig,
    pub simple_spammer: SimpleSpammerConfig,
    pub recorder_settings: RecorderSettingsConfig,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            schema_version: 1,
            ui_mode: "simple".to_string(),
            simple_task: "spammer".to_string(),
            window_geometry: None,
            start_stop_hotkey: "F6".to_string(),
            record_hotkey: "F7".to_string(),
            panic_hotkey: "F12".to_string(),
            dry_run: false,
            check_updates_on_startup: true,
            auto_save_active_profile: true,
            start_countdown_sec: 0,
            last_profile_path: None,
            simple_clicker: SimpleClickerConfig::default(),
            simple_spammer: SimpleSpammerConfig::default(),
            recorder_settings: RecorderSettingsConfig::default(),
        }
    }
}

fn normalize_ui_mode(value: Option<&Value>) -> String {
    let Some(s) = value.and_then(Value::as_str) else {
        return "simple".to_string();
    };
    let lowered = s.trim().to_ascii_lowercase();
    if lowered.contains("studio")
        || lowered.contains("advanced")
        || lowered == "pro"
        || lowered.contains("pro ")
        || lowered.contains(" pro")
    {
        return "studio".to_string();
    }
    "simple".to_string()
}

fn normalize_simple_task(value: Option<&Value>) -> String {
    let Some(s) = value.and_then(Value::as_str) else {
        return "spammer".to_string();
    };
    let lowered = s.trim().to_ascii_lowercase();
    if lowered.contains("seq") || lowered.contains("combo") {
        return "sequence".to_string();
    }
    "spammer".to_string()
}

fn normalize_hotkey(value: Option<&Value>, default: &str) -> String {
    let Some(s) = value.and_then(Value::as_str) else {
        return default.to_string();
    };
    if s.trim().is_empty() {
        return default.to_string();
    }
    normalize_key_combo(s).unwrap_or_else(|_| default.to_string())
}

fn normalize_optional_str(value: Option<&Value>) -> Option<String> {
    let s = value.and_then(Value::as_str)?;
    let cleaned = s.trim();
    if cleaned.is_empty() || cleaned.contains('\0') {
        None
    } else {
        Some(cleaned.to_string())
    }
}

fn coerce_bool(value: Option<&Value>, default: bool) -> bool {
    value.and_then(Value::as_bool).unwrap_or(default)
}

fn coerce_int(value: Option<&Value>, default: i64, minimum: i64, maximum: i64) -> i64 {
    value
        .and_then(Value::as_i64)
        .map(|v| v.clamp(minimum, maximum))
        .unwrap_or(default)
}

fn normalize_simple_clicker(raw: Option<&Value>) -> SimpleClickerConfig {
    let defaults = SimpleClickerConfig::default();
    let Some(obj) = raw.and_then(Value::as_object) else {
        return defaults;
    };

    let button = obj
        .get("button")
        .and_then(Value::as_str)
        .map(|s| match s.trim().to_ascii_lowercase().as_str() {
            "right" => "Right".to_string(),
            "middle" => "Middle".to_string(),
            _ => "Left".to_string(),
        })
        .unwrap_or(defaults.button);

    let click_type = obj
        .get("click_type")
        .and_then(Value::as_str)
        .map(|s| match s.trim().to_ascii_lowercase().as_str() {
            "double" => "Double".to_string(),
            _ => "Single".to_string(),
        })
        .unwrap_or(defaults.click_type);

    let loc_mode = obj
        .get("loc_mode")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_ascii_lowercase)
        .unwrap_or(defaults.loc_mode);

    SimpleClickerConfig {
        button,
        click_type,
        interval_ms: coerce_int(obj.get("interval_ms"), defaults.interval_ms, 1, 3_600_000),
        jitter_enabled: coerce_bool(obj.get("jitter_enabled"), defaults.jitter_enabled),
        loc_mode,
        x: coerce_int(obj.get("x"), defaults.x, -65_535, 65_535),
        y: coerce_int(obj.get("y"), defaults.y, -65_535, 65_535),
    }
}

fn normalize_simple_spammer(raw: Option<&Value>) -> SimpleSpammerConfig {
    let defaults = SimpleSpammerConfig::default();
    let Some(obj) = raw.and_then(Value::as_object) else {
        return defaults;
    };

    let key = obj
        .get("key")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty() && !s.contains('\0'))
        .map(str::to_string)
        .unwrap_or(defaults.key);

    SimpleSpammerConfig {
        key,
        interval_ms: coerce_int(obj.get("interval_ms"), defaults.interval_ms, 1, 3_600_000),
        jitter_enabled: coerce_bool(obj.get("jitter_enabled"), defaults.jitter_enabled),
    }
}

fn normalize_recorder_settings(raw: Option<&Value>) -> RecorderSettingsConfig {
    let defaults = RecorderSettingsConfig::default();
    let Some(obj) = raw.and_then(Value::as_object) else {
        return defaults;
    };
    RecorderSettingsConfig {
        record_delays: coerce_bool(obj.get("record_delays"), defaults.record_delays),
        merge_key_taps: coerce_bool(obj.get("merge_key_taps"), defaults.merge_key_taps),
        capture_click_coordinates: coerce_bool(
            obj.get("capture_click_coordinates"),
            defaults.capture_click_coordinates,
        ),
    }
}

impl AppConfig {
    pub fn sanitize(&mut self) {
        self.schema_version = self.schema_version.clamp(1, 10_000);
        self.ui_mode = if self.ui_mode == "studio" {
            "studio".to_string()
        } else {
            "simple".to_string()
        };
        self.simple_task = if self.simple_task == "sequence" {
            "sequence".to_string()
        } else {
            "spammer".to_string()
        };
        self.start_stop_hotkey =
            normalize_key_combo(&self.start_stop_hotkey).unwrap_or_else(|_| "F6".to_string());
        self.record_hotkey =
            normalize_key_combo(&self.record_hotkey).unwrap_or_else(|_| "F7".to_string());
        self.panic_hotkey =
            normalize_key_combo(&self.panic_hotkey).unwrap_or_else(|_| "F12".to_string());
        self.start_countdown_sec = self.start_countdown_sec.clamp(0, 10);
        self.simple_spammer.interval_ms = self.simple_spammer.interval_ms.clamp(1, 3_600_000);
    }

    pub fn from_json_value(data: &Value) -> Self {
        let Some(obj) = data.as_object() else {
            return Self::default();
        };
        let mut cfg = Self {
            schema_version: coerce_int(obj.get("schema_version"), 1, 1, 10_000),
            ui_mode: normalize_ui_mode(obj.get("ui_mode")),
            simple_task: normalize_simple_task(obj.get("simple_task")),
            window_geometry: normalize_optional_str(obj.get("window_geometry")),
            start_stop_hotkey: normalize_hotkey(obj.get("start_stop_hotkey"), "F6"),
            record_hotkey: normalize_hotkey(obj.get("record_hotkey"), "F7"),
            panic_hotkey: normalize_hotkey(obj.get("panic_hotkey"), "F12"),
            dry_run: coerce_bool(obj.get("dry_run"), false),
            check_updates_on_startup: coerce_bool(obj.get("check_updates_on_startup"), true),
            auto_save_active_profile: coerce_bool(obj.get("auto_save_active_profile"), true),
            start_countdown_sec: coerce_int(obj.get("start_countdown_sec"), 0, 0, 10),
            last_profile_path: normalize_optional_str(obj.get("last_profile_path")),
            simple_clicker: normalize_simple_clicker(obj.get("simple_clicker")),
            simple_spammer: normalize_simple_spammer(obj.get("simple_spammer")),
            recorder_settings: normalize_recorder_settings(obj.get("recorder_settings")),
        };
        cfg.sanitize();
        cfg
    }

    pub fn to_json_value(&self) -> Value {
        let mut map = Map::new();
        map.insert("schema_version".into(), Value::from(self.schema_version));
        map.insert("ui_mode".into(), Value::String(self.ui_mode.clone()));
        map.insert(
            "simple_task".into(),
            Value::String(self.simple_task.clone()),
        );
        map.insert(
            "window_geometry".into(),
            self.window_geometry
                .as_ref()
                .map(|s| Value::String(s.clone()))
                .unwrap_or(Value::Null),
        );
        map.insert(
            "start_stop_hotkey".into(),
            Value::String(self.start_stop_hotkey.clone()),
        );
        map.insert(
            "record_hotkey".into(),
            Value::String(self.record_hotkey.clone()),
        );
        map.insert(
            "panic_hotkey".into(),
            Value::String(self.panic_hotkey.clone()),
        );
        map.insert("dry_run".into(), Value::Bool(self.dry_run));
        map.insert(
            "check_updates_on_startup".into(),
            Value::Bool(self.check_updates_on_startup),
        );
        map.insert(
            "auto_save_active_profile".into(),
            Value::Bool(self.auto_save_active_profile),
        );
        map.insert(
            "start_countdown_sec".into(),
            Value::from(self.start_countdown_sec),
        );
        map.insert(
            "last_profile_path".into(),
            self.last_profile_path
                .as_ref()
                .map(|s| Value::String(s.clone()))
                .unwrap_or(Value::Null),
        );
        map.insert(
            "simple_clicker".into(),
            serde_json::to_value(&self.simple_clicker).unwrap_or(Value::Null),
        );
        map.insert(
            "simple_spammer".into(),
            serde_json::to_value(&self.simple_spammer).unwrap_or(Value::Null),
        );
        map.insert(
            "recorder_settings".into(),
            serde_json::to_value(&self.recorder_settings).unwrap_or(Value::Null),
        );
        Value::Object(map)
    }
}

pub fn home_dir() -> PathBuf {
    if cfg!(windows) {
        std::env::var_os("USERPROFILE")
            .or_else(|| std::env::var_os("HOME"))
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
    } else {
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
    }
}

/// Resolve platform-appropriate KeyForge configuration directory.
pub fn get_config_dir(custom_dir: Option<&Path>) -> PathBuf {
    if let Some(dir) = custom_dir {
        return dir.to_path_buf();
    }
    if let Ok(env_dir) = std::env::var("KEYFORGE_CONFIG_DIR") {
        let trimmed = env_dir.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    if cfg!(target_os = "windows") {
        let base = std::env::var("APPDATA")
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join("AppData").join("Roaming"));
        return base.join("KeyForge");
    }

    if cfg!(target_os = "macos") {
        return home_dir()
            .join("Library")
            .join("Application Support")
            .join("KeyForge");
    }

    let base = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".config"));
    base.join("KeyForge")
}

fn is_unisolated_test_run(custom_dir: Option<&Path>) -> bool {
    if custom_dir.is_some() {
        return false;
    }
    if std::env::var("KEYFORGE_CONFIG_DIR")
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
    {
        return false;
    }
    is_test_binary_env()
}

fn parse_json_dict(raw_bytes: &[u8]) -> Result<Value, String> {
    if raw_bytes.len() > MAX_CONFIG_FILE_BYTES {
        return Err("Configuration file exceeds maximum permitted byte size.".to_string());
    }
    let text = std::str::from_utf8(raw_bytes).map_err(|_| "Invalid UTF-8".to_string())?;
    let val: Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    if !val.is_object() {
        return Err("Configuration JSON root must be an object.".to_string());
    }
    Ok(val)
}

/// Load [`AppConfig`] from disk, recovering automatically from `.bak` if primary is malformed.
pub fn load_app_config(custom_dir: Option<&Path>) -> AppConfig {
    if is_unisolated_test_run(custom_dir) {
        return AppConfig::default();
    }
    let config_dir = get_config_dir(custom_dir);
    let candidates = [
        config_dir.join(CONFIG_FILENAME),
        config_dir.join(CONFIG_BACKUP_FILENAME),
        config_dir.join("config.bak"),
    ];
    for candidate in candidates {
        if !candidate.is_file() {
            continue;
        }
        let Ok(meta) = std::fs::metadata(&candidate) else {
            continue;
        };
        if (meta.len() as usize) > MAX_CONFIG_FILE_BYTES {
            continue;
        }
        let Ok(bytes) = std::fs::read(&candidate) else {
            continue;
        };
        if let Ok(val) = parse_json_dict(&bytes) {
            return AppConfig::from_json_value(&val);
        }
    }
    AppConfig::default()
}

/// Atomically persist [`AppConfig`] via `.tmp` + rename, rotating valid primary to `.bak`.
pub fn save_app_config(
    config: &AppConfig,
    custom_dir: Option<&Path>,
) -> Result<PathBuf, SecurityValidationError> {
    let config_dir = get_config_dir(custom_dir);
    let target = config_dir.join(CONFIG_FILENAME);
    if is_unisolated_test_run(custom_dir) {
        return Ok(target);
    }
    std::fs::create_dir_all(&config_dir)
        .map_err(|e| SecurityValidationError::new(format!("Failed to create config dir: {e}")))?;
    let backup_path = config_dir.join(CONFIG_BACKUP_FILENAME);

    if target.is_file() {
        if let Ok(existing_bytes) = std::fs::read(&target) {
            if parse_json_dict(&existing_bytes).is_ok() {
                let _ = atomic_write_bytes(&backup_path, &existing_bytes);
            }
        }
    }

    let mut clean = config.clone();
    clean.sanitize();
    let mut serialized = serde_json::to_string_pretty(&clean.to_json_value())
        .map_err(|e| SecurityValidationError::new(format!("Failed to serialize AppConfig: {e}")))?;
    serialized.push('\n');
    atomic_write_bytes(&target, serialized.as_bytes())?;
    Ok(target)
}

/// Atomically save a SHA-256 signed backup of the active [`MacroProfile`].
pub fn save_active_profile_backup(
    profile: &mut MacroProfile,
    custom_dir: Option<&Path>,
) -> Result<PathBuf, SecurityValidationError> {
    let config_dir = get_config_dir(custom_dir);
    let target = config_dir.join(ACTIVE_PROFILE_FILENAME);
    if is_unisolated_test_run(custom_dir) {
        return Ok(target);
    }
    std::fs::create_dir_all(&config_dir)
        .map_err(|e| SecurityValidationError::new(format!("Failed to create config dir: {e}")))?;
    let backup_path = config_dir.join(ACTIVE_PROFILE_BACKUP_FILENAME);

    if target.is_file() {
        if let Ok(existing_bytes) = std::fs::read(&target) {
            if existing_bytes.len() <= MAX_PROFILE_FILE_BYTES
                && parse_and_verify_envelope(&existing_bytes, true).is_ok()
            {
                let _ = atomic_write_bytes(&backup_path, &existing_bytes);
            }
        }
    }

    save_profile_secure(profile, &target)
}

/// Load the signed active [`MacroProfile`] backup, falling back to `.bak` on corruption.
pub fn load_active_profile_backup(custom_dir: Option<&Path>) -> Option<MacroProfile> {
    if is_unisolated_test_run(custom_dir) {
        return None;
    }
    let config_dir = get_config_dir(custom_dir);
    let candidates = [
        config_dir.join(ACTIVE_PROFILE_FILENAME),
        config_dir.join(ACTIVE_PROFILE_BACKUP_FILENAME),
    ];
    for candidate in candidates {
        if !candidate.is_file() {
            continue;
        }
        let Ok(meta) = std::fs::metadata(&candidate) else {
            continue;
        };
        if (meta.len() as usize) > MAX_PROFILE_FILE_BYTES {
            continue;
        }
        let Ok(bytes) = std::fs::read(&candidate) else {
            continue;
        };
        if let Ok((profile, _verified)) = parse_and_verify_envelope(&bytes, true) {
            return Some(profile);
        }
    }
    None
}

fn slugify_preset_name(name: &str) -> String {
    let filtered: String = name
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | ' ' | '-'))
        .collect();
    let trimmed = filtered.trim().replace(' ', "_");
    let capped: String = trimmed.chars().take(60).collect();
    if capped.is_empty() {
        "custom_preset".to_string()
    } else {
        capped
    }
}

pub fn get_presets_dir(custom_dir: Option<&Path>) -> PathBuf {
    get_config_dir(custom_dir).join(PRESETS_DIRNAME)
}

pub fn save_custom_preset(
    profile: &mut MacroProfile,
    custom_dir: Option<&Path>,
) -> Result<PathBuf, SecurityValidationError> {
    profile.validate().map_err(SecurityValidationError::new)?;
    let presets_dir = get_presets_dir(custom_dir);
    let slug = slugify_preset_name(&profile.name);
    let target = presets_dir.join(format!("{slug}.kfmacro.json"));
    if is_unisolated_test_run(custom_dir) {
        return Ok(target);
    }
    std::fs::create_dir_all(&presets_dir)
        .map_err(|e| SecurityValidationError::new(format!("Failed to create presets dir: {e}")))?;
    save_profile_secure(profile, &target)
}

pub fn load_custom_presets(custom_dir: Option<&Path>) -> BTreeMap<String, MacroProfile> {
    if is_unisolated_test_run(custom_dir) {
        return BTreeMap::new();
    }
    let presets_dir = get_presets_dir(custom_dir);
    if !presets_dir.is_dir() {
        return BTreeMap::new();
    }
    let mut loaded = BTreeMap::new();
    let Ok(entries) = std::fs::read_dir(&presets_dir) else {
        return loaded;
    };
    let mut paths: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|s| s.to_str())
                .is_some_and(|n| n.ends_with(".kfmacro.json"))
        })
        .collect();
    paths.sort();

    for path in paths {
        if let Ok((profile, _)) = load_profile_secure(&path, false) {
            loaded.insert(profile.name.clone(), profile);
        }
    }
    loaded
}

pub fn delete_custom_preset(preset_name: &str, custom_dir: Option<&Path>) -> bool {
    if is_unisolated_test_run(custom_dir) || preset_name.trim().is_empty() {
        return false;
    }
    let presets_dir = get_presets_dir(custom_dir);
    if !presets_dir.is_dir() {
        return false;
    }
    let target_slug = slugify_preset_name(preset_name);
    if let Ok(entries) = std::fs::read_dir(&presets_dir) {
        let mut paths: Vec<PathBuf> = entries
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .and_then(|s| s.to_str())
                    .is_some_and(|n| n.ends_with(".kfmacro.json"))
            })
            .collect();
        paths.sort();
        for path in paths {
            let matched = match load_profile_secure(&path, false) {
                Ok((p, _)) => p.name == preset_name,
                Err(_) => path
                    .file_name()
                    .and_then(|s| s.to_str())
                    .is_some_and(|n| n == format!("{target_slug}.kfmacro.json")),
            };
            if matched {
                let _ = std::fs::remove_file(&path);
                return true;
            }
        }
    }
    let fallback = presets_dir.join(format!("{target_slug}.kfmacro.json"));
    if fallback.is_file() {
        let _ = std::fs::remove_file(&fallback);
        return true;
    }
    false
}
