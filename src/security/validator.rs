//! Strict schema validation, DoS bomb mitigation, and file path sanitization.

use std::fmt;
use std::path::{Component, Path, PathBuf};

use serde_json::Value;

use crate::models::action::{ActionType, MacroAction};
use crate::models::profile::MacroProfile;

pub const MAX_PROFILE_FILE_BYTES: usize = 1_048_576; // 1 MiB max file size
pub const MAX_TOTAL_STEPS: usize = 5_000; // Max static MacroAction nodes
pub const MAX_REPEAT_NESTING_DEPTH: usize = 5; // Max nested REPEAT_BLOCK depth
pub const MAX_EXPANDED_OPS_PER_CYCLE: usize = 100_000; // Prevents "Billion Laughs" nested repeat bombs
pub const MIN_SAFE_CYCLE_DELAY_MS: i64 = 5; // Minimum OS input queue safety yield per cycle

const ALLOWED_FILE_EXTENSIONS: &[&str] = &["json", "ktap", "kfmacro"];

const WINDOWS_RESERVED_STEMS: &[&str] = &[
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

pub const ALLOWED_ENVELOPE_KEYS: &[&str] = &[
    "ktap_format",
    "keyforge_format",
    "integrity_sha256",
    "profile",
];

pub const ALLOWED_PROFILE_KEYS: &[&str] = &[
    "schema_version",
    "name",
    "description",
    "repeat_mode",
    "repeat_count",
    "repeat_duration_s",
    "inter_cycle_delay_ms",
    "start_stop_hotkey",
    "panic_hotkey",
    "record_hotkey",
    "created_at",
    "updated_at",
    "actions",
];

pub const ALLOWED_ACTION_KEYS: &[&str] = &[
    "id",
    "action_type",
    "enabled",
    "label",
    "key",
    "hold_ms",
    "button",
    "click_type",
    "x",
    "y",
    "move_mode",
    "scroll_dx",
    "scroll_dy",
    "wait_ms",
    "jitter_ms",
    "repeat_count",
    "sub_actions",
];

/// Raised when a profile or file violates kTap security constraints.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecurityValidationError {
    pub message: String,
}

impl SecurityValidationError {
    pub fn new(msg: impl Into<String>) -> Self {
        Self {
            message: msg.into(),
        }
    }
}

impl fmt::Display for SecurityValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for SecurityValidationError {}

fn expand_tilde(raw: &str) -> PathBuf {
    if raw == "~" || raw.starts_with("~/") || raw.starts_with("~\\") {
        if let Some(home) = std::env::var_os("USERPROFILE")
            .or_else(|| std::env::var_os("HOME"))
            .map(PathBuf::from)
        {
            if raw.len() == 1 {
                return home;
            }
            return home.join(&raw[2..]);
        }
    }
    PathBuf::from(raw)
}

fn normalize_path_lexical(path: &Path) -> PathBuf {
    let abs = if path.is_absolute() {
        path.to_path_buf()
    } else if let Ok(cwd) = std::env::current_dir() {
        cwd.join(path)
    } else {
        path.to_path_buf()
    };

    let mut out = PathBuf::new();
    for comp in abs.components() {
        match comp {
            Component::Prefix(_) | Component::RootDir | Component::Normal(_) => out.push(comp),
            Component::CurDir => {}
            Component::ParentDir => {
                if !matches!(
                    out.components().next_back(),
                    Some(Component::RootDir | Component::Prefix(_))
                ) {
                    out.pop();
                }
            }
        }
    }
    out
}

/// Validate and canonicalize a profile file path against traversal, ADS, and unsafe extensions.
pub fn validate_safe_file_path(
    path: &Path,
    must_exist: bool,
) -> Result<PathBuf, SecurityValidationError> {
    let raw_str = path.to_string_lossy();
    if raw_str.contains('\0') || raw_str.trim().is_empty() {
        return Err(SecurityValidationError::new(
            "File path contains null bytes or is empty.",
        ));
    }

    let raw_name = path
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    if raw_name.contains(':') {
        return Err(SecurityValidationError::new(
            "File name cannot contain NTFS Alternate Data Stream colons (':').",
        ));
    }

    let base_stem = raw_name
        .split('.')
        .next()
        .unwrap_or("")
        .to_ascii_uppercase();
    if WINDOWS_RESERVED_STEMS.contains(&base_stem.as_str()) {
        return Err(SecurityValidationError::new(format!(
            "File name uses reserved Windows device name: {base_stem:?}."
        )));
    }

    let expanded = expand_tilde(&raw_str);
    let candidate = if must_exist {
        if !expanded.exists() {
            return Err(SecurityValidationError::new(format!(
                "Profile file does not exist: {}",
                expanded.display()
            )));
        }
        expanded.canonicalize().map_err(|e| {
            SecurityValidationError::new(format!("Failed to canonicalize path: {e}"))
        })?
    } else if let Some(parent) = expanded.parent() {
        if parent.as_os_str().is_empty() {
            normalize_path_lexical(&expanded)
        } else if parent.exists() {
            match parent.canonicalize() {
                Ok(canon_parent) => match expanded.file_name() {
                    Some(fname) => canon_parent.join(fname),
                    None => canon_parent,
                },
                Err(_) => normalize_path_lexical(&expanded),
            }
        } else {
            normalize_path_lexical(&expanded)
        }
    } else {
        normalize_path_lexical(&expanded)
    };

    let ext = candidate
        .extension()
        .and_then(|s| s.to_str())
        .map(str::to_ascii_lowercase)
        .unwrap_or_default();
    if !ALLOWED_FILE_EXTENSIONS.contains(&ext.as_str()) {
        return Err(SecurityValidationError::new(format!(
            "Unsupported file extension '.{ext}'. Allowed: .json, .ktap, .kfmacro"
        )));
    }

    if must_exist {
        if !candidate.is_file() {
            return Err(SecurityValidationError::new(format!(
                "Path is not a regular file: {}",
                candidate.display()
            )));
        }
        let meta = std::fs::metadata(&candidate).map_err(|e| {
            SecurityValidationError::new(format!("Cannot inspect profile file: {e}"))
        })?;
        let size = meta.len() as usize;
        if size > MAX_PROFILE_FILE_BYTES {
            return Err(SecurityValidationError::new(format!(
                "Profile file size ({size} bytes) exceeds security limit ({MAX_PROFILE_FILE_BYTES} bytes)."
            )));
        }
    }

    Ok(candidate)
}

fn validate_action_dict_schema(
    data: &Value,
    depth: usize,
) -> Result<(usize, usize), SecurityValidationError> {
    let obj = data
        .as_object()
        .ok_or_else(|| SecurityValidationError::new("Action entry must be a JSON object."))?;

    let mut unknown: Vec<&str> = obj
        .keys()
        .map(String::as_str)
        .filter(|k| !ALLOWED_ACTION_KEYS.contains(k))
        .collect();
    if !unknown.is_empty() {
        unknown.sort_unstable();
        return Err(SecurityValidationError::new(format!(
            "Unexpected fields in action object: {unknown:?}"
        )));
    }

    if depth > MAX_REPEAT_NESTING_DEPTH {
        return Err(SecurityValidationError::new(format!(
            "Repeat block nesting depth ({depth}) exceeds limit ({MAX_REPEAT_NESTING_DEPTH})."
        )));
    }

    let mut node_count: usize = 1;
    let action_type = obj.get("action_type").and_then(Value::as_str).unwrap_or("");
    let expanded_ops: usize = if action_type == ActionType::RepeatBlock.as_str() {
        let repeat_count = match obj.get("repeat_count") {
            None => 2,
            Some(v) => v.as_i64().ok_or_else(|| {
                SecurityValidationError::new("Invalid repeat_count in repeat_block.")
            })?,
        };
        if !(1..=10_000).contains(&repeat_count) {
            return Err(SecurityValidationError::new(
                "Invalid repeat_count in repeat_block.",
            ));
        }
        let sub_actions = match obj.get("sub_actions") {
            None => &[][..],
            Some(Value::Array(arr)) => arr.as_slice(),
            Some(_) => return Err(SecurityValidationError::new("sub_actions must be a list.")),
        };
        let mut sub_nodes_total = 0usize;
        let mut sub_ops_total = 0usize;
        for sub in sub_actions {
            let (s_nodes, s_ops) = validate_action_dict_schema(sub, depth + 1)?;
            sub_nodes_total = sub_nodes_total.saturating_add(s_nodes);
            sub_ops_total = sub_ops_total.saturating_add(s_ops);
        }
        node_count = node_count.saturating_add(sub_nodes_total);
        (repeat_count as usize).saturating_mul(sub_ops_total.max(1))
    } else {
        1
    };

    if node_count > MAX_TOTAL_STEPS {
        return Err(SecurityValidationError::new(format!(
            "Macro step count ({node_count}) exceeds limit ({MAX_TOTAL_STEPS})."
        )));
    }
    if expanded_ops > MAX_EXPANDED_OPS_PER_CYCLE {
        return Err(SecurityValidationError::new(format!(
            "Expanded operations per cycle ({expanded_ops}) exceeds DoS limit ({MAX_EXPANDED_OPS_PER_CYCLE})."
        )));
    }

    Ok((node_count, expanded_ops))
}

/// Validate raw profile JSON object against allowlist schema and complexity caps.
pub fn validate_profile_payload_value(payload: &Value) -> Result<(), SecurityValidationError> {
    let obj = payload
        .as_object()
        .ok_or_else(|| SecurityValidationError::new("Profile payload must be a JSON object."))?;

    let mut unknown: Vec<&str> = obj
        .keys()
        .map(String::as_str)
        .filter(|k| !ALLOWED_PROFILE_KEYS.contains(k))
        .collect();
    if !unknown.is_empty() {
        unknown.sort_unstable();
        return Err(SecurityValidationError::new(format!(
            "Unexpected fields in profile payload: {unknown:?}"
        )));
    }

    let actions = match obj.get("actions") {
        None => &[][..],
        Some(Value::Array(arr)) => arr.as_slice(),
        Some(_) => {
            return Err(SecurityValidationError::new(
                "'actions' field must be a list.",
            ))
        }
    };

    let mut total_nodes = 0usize;
    let mut total_expanded_ops = 0usize;
    for item in actions {
        let (nodes, ops) = validate_action_dict_schema(item, 0)?;
        total_nodes = total_nodes.saturating_add(nodes);
        total_expanded_ops = total_expanded_ops.saturating_add(ops);
        if total_nodes > MAX_TOTAL_STEPS {
            return Err(SecurityValidationError::new(format!(
                "Total step count ({total_nodes}) exceeds limit ({MAX_TOTAL_STEPS})."
            )));
        }
        if total_expanded_ops > MAX_EXPANDED_OPS_PER_CYCLE {
            return Err(SecurityValidationError::new(format!(
                "Total expanded operations per cycle ({total_expanded_ops}) exceeds DoS limit ({MAX_EXPANDED_OPS_PER_CYCLE})."
            )));
        }
    }

    Ok(())
}

/// Validate an in-memory [`MacroProfile`]'s step count, nesting depth, and expanded ops.
pub fn validate_profile_complexity(
    profile: &mut MacroProfile,
) -> Result<(usize, usize), SecurityValidationError> {
    profile.validate().map_err(SecurityValidationError::new)?;

    fn walk(
        actions: &[MacroAction],
        depth: usize,
    ) -> Result<(usize, usize), SecurityValidationError> {
        if depth > MAX_REPEAT_NESTING_DEPTH {
            return Err(SecurityValidationError::new(format!(
                "Repeat nesting depth ({depth}) exceeds maximum ({MAX_REPEAT_NESTING_DEPTH})."
            )));
        }
        let mut nodes = 0usize;
        let mut ops = 0usize;
        for act in actions {
            nodes = nodes.saturating_add(1);
            if act.action_type == ActionType::RepeatBlock {
                let (c_nodes, c_ops) = walk(&act.sub_actions, depth + 1)?;
                nodes = nodes.saturating_add(c_nodes);
                ops = ops.saturating_add(
                    (act.repeat_count.max(1) as usize).saturating_mul(c_ops.max(1)),
                );
            } else {
                ops = ops.saturating_add(1);
            }
            if nodes > MAX_TOTAL_STEPS {
                return Err(SecurityValidationError::new(format!(
                    "Total step count ({nodes}) exceeds maximum ({MAX_TOTAL_STEPS})."
                )));
            }
            if ops > MAX_EXPANDED_OPS_PER_CYCLE {
                return Err(SecurityValidationError::new(format!(
                    "Expanded operations per cycle ({ops}) exceeds maximum ({MAX_EXPANDED_OPS_PER_CYCLE})."
                )));
            }
        }
        Ok((nodes, ops))
    }

    walk(&profile.actions, 0)
}
