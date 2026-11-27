//! Macro profile model, repeat modes, and built-in starter presets.

use std::collections::HashSet;

use chrono::{SubsecRound, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::models::action::{
    make_key_down, make_key_press, make_key_up, make_mouse_click, make_mouse_move,
    make_repeat_block, make_wait, require_int, ActionType, ClickType, MacroAction, MouseButton,
    MoveMode,
};
use crate::models::keymap::normalize_key_combo;

pub const SCHEMA_VERSION: &str = "1.0";

/// Macro sequence repetition mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RepeatMode {
    Count,
    Infinite,
    Duration,
}

impl RepeatMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Count => "count",
            Self::Infinite => "infinite",
            Self::Duration => "duration",
        }
    }

    pub fn from_str_value(s: &str) -> Result<Self, String> {
        match s {
            "count" => Ok(Self::Count),
            "infinite" => Ok(Self::Infinite),
            "duration" => Ok(Self::Duration),
            _ => Err(format!("Unsupported repeat_mode: {s:?}")),
        }
    }
}

pub fn utc_now_iso() -> String {
    Utc::now().round_subsecs(0).to_rfc3339()
}

fn require_finite_f64(value: &Value, field_name: &str) -> Result<f64, String> {
    let fval = value
        .as_f64()
        .ok_or_else(|| format!("{field_name} must be a number."))?;
    if !fval.is_finite() {
        return Err(format!("{field_name} must be a finite number."));
    }
    Ok(fval)
}

/// A complete, serializable KeyForge macro profile.
#[derive(Debug, Clone, PartialEq)]
pub struct MacroProfile {
    pub name: String,
    pub description: String,
    pub schema_version: String,
    pub repeat_mode: RepeatMode,
    pub repeat_count: i64,
    pub repeat_duration_s: f64,
    pub inter_cycle_delay_ms: i64,
    pub start_stop_hotkey: String,
    pub panic_hotkey: String,
    pub record_hotkey: String,
    pub actions: Vec<MacroAction>,
    pub created_at: String,
    pub updated_at: String,
}

impl Default for MacroProfile {
    fn default() -> Self {
        let now = utc_now_iso();
        Self {
            name: "Action Loop (E -> 200ms -> Click -> 1)".to_string(),
            description: "Press E -> Wait 200ms -> Left Click -> Press 1".to_string(),
            schema_version: SCHEMA_VERSION.to_string(),
            repeat_mode: RepeatMode::Infinite,
            repeat_count: 10,
            repeat_duration_s: 30.0,
            inter_cycle_delay_ms: 100,
            start_stop_hotkey: "F6".to_string(),
            panic_hotkey: "F12".to_string(),
            record_hotkey: "F7".to_string(),
            actions: Vec::new(),
            created_at: now.clone(),
            updated_at: now,
        }
    }
}

impl MacroProfile {
    /// Validate profile settings, hotkeys, and all child actions.
    pub fn validate(&mut self) -> Result<(), String> {
        if self.name.trim().is_empty() {
            return Err("Profile name must be a non-empty string.".to_string());
        }
        if self.name.chars().count() > 100 {
            return Err("Profile name cannot exceed 100 characters.".to_string());
        }
        if self.description.chars().count() > 500 {
            return Err("Profile description cannot exceed 500 characters.".to_string());
        }
        if !(1..=1_000_000).contains(&self.repeat_count) {
            return Err("repeat_count must be between 1 and 1,000,000.".to_string());
        }
        if !self.repeat_duration_s.is_finite()
            || self.repeat_duration_s < 0.1
            || self.repeat_duration_s > 86_400.0
        {
            return Err(
                "repeat_duration_s must be between 0.1 and 86,400 seconds (24h).".to_string(),
            );
        }
        if !(0..=3_600_000).contains(&self.inter_cycle_delay_ms) {
            return Err("inter_cycle_delay_ms must be between 0 and 3,600,000 ms.".to_string());
        }

        self.start_stop_hotkey = normalize_key_combo(&self.start_stop_hotkey)?;
        self.panic_hotkey = normalize_key_combo(&self.panic_hotkey)?;
        self.record_hotkey = normalize_key_combo(&self.record_hotkey)?;

        let mut distinct = HashSet::new();
        distinct.insert(self.start_stop_hotkey.as_str());
        distinct.insert(self.panic_hotkey.as_str());
        distinct.insert(self.record_hotkey.as_str());
        if distinct.len() < 3 {
            return Err(
                "start_stop_hotkey, panic_hotkey, and record_hotkey must be distinct keys."
                    .to_string(),
            );
        }

        for act in &mut self.actions {
            act.validate()?;
        }
        Ok(())
    }

    /// Compute the minimum guaranteed wait time (in ms) within a single enabled cycle.
    pub fn total_cycle_wait_ms(&self) -> i64 {
        fn sum_waits(items: &[MacroAction]) -> i64 {
            let mut total = 0i64;
            for item in items {
                if !item.enabled {
                    continue;
                }
                match item.action_type {
                    ActionType::Wait => total = total.saturating_add(item.wait_ms),
                    ActionType::KeyPress | ActionType::MouseClick => {
                        total = total.saturating_add(item.hold_ms);
                    }
                    ActionType::RepeatBlock => {
                        total = total.saturating_add(
                            item.repeat_count
                                .saturating_mul(sum_waits(&item.sub_actions)),
                        );
                    }
                    _ => {}
                }
            }
            total
        }
        sum_waits(&self.actions).saturating_add(self.inter_cycle_delay_ms)
    }

    /// Serialize profile data (excluding external integrity wrapper) to a JSON [`Value`].
    pub fn to_payload_value(&self) -> Value {
        let mut map = Map::new();
        map.insert(
            "schema_version".to_string(),
            Value::String(self.schema_version.clone()),
        );
        map.insert("name".to_string(), Value::String(self.name.clone()));
        map.insert(
            "description".to_string(),
            Value::String(self.description.clone()),
        );
        map.insert(
            "repeat_mode".to_string(),
            Value::String(self.repeat_mode.as_str().to_string()),
        );
        map.insert("repeat_count".to_string(), Value::from(self.repeat_count));
        map.insert(
            "repeat_duration_s".to_string(),
            Value::from(self.repeat_duration_s),
        );
        map.insert(
            "inter_cycle_delay_ms".to_string(),
            Value::from(self.inter_cycle_delay_ms),
        );
        map.insert(
            "start_stop_hotkey".to_string(),
            Value::String(self.start_stop_hotkey.clone()),
        );
        map.insert(
            "panic_hotkey".to_string(),
            Value::String(self.panic_hotkey.clone()),
        );
        map.insert(
            "record_hotkey".to_string(),
            Value::String(self.record_hotkey.clone()),
        );
        map.insert(
            "created_at".to_string(),
            Value::String(self.created_at.clone()),
        );
        map.insert(
            "updated_at".to_string(),
            Value::String(self.updated_at.clone()),
        );
        map.insert(
            "actions".to_string(),
            Value::Array(
                self.actions
                    .iter()
                    .map(MacroAction::to_json_value)
                    .collect(),
            ),
        );
        Value::Object(map)
    }

    /// Construct and validate a [`MacroProfile`] from a JSON payload object.
    pub fn from_payload_value(value: &Value) -> Result<Self, String> {
        let obj = value
            .as_object()
            .ok_or_else(|| "Profile payload must be a dictionary.".to_string())?;

        let actions = match obj.get("actions") {
            None => Vec::new(),
            Some(Value::Array(arr)) => arr
                .iter()
                .map(MacroAction::from_json_value)
                .collect::<Result<Vec<_>, _>>()?,
            Some(_) => return Err("'actions' must be a list.".to_string()),
        };

        let name = match obj.get("name") {
            None => "Untitled Profile".to_string(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("name must be a string.".to_string()),
        };
        let description = match obj.get("description") {
            None => String::new(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("description must be a string.".to_string()),
        };
        let schema_version = match obj.get("schema_version") {
            None => SCHEMA_VERSION.to_string(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("schema_version must be a string.".to_string()),
        };
        let repeat_mode = match obj.get("repeat_mode") {
            None => RepeatMode::Count,
            Some(Value::String(s)) => RepeatMode::from_str_value(s)?,
            Some(_) => return Err("repeat_mode must be a string.".to_string()),
        };
        let repeat_count = match obj.get("repeat_count") {
            None => 10,
            Some(v) => require_int(v, "repeat_count")?,
        };
        let repeat_duration_s = match obj.get("repeat_duration_s") {
            None => 30.0,
            Some(v) => require_finite_f64(v, "repeat_duration_s")?,
        };
        let inter_cycle_delay_ms = match obj.get("inter_cycle_delay_ms") {
            None => 100,
            Some(v) => require_int(v, "inter_cycle_delay_ms")?,
        };
        let start_stop_hotkey = match obj.get("start_stop_hotkey") {
            None => "F6".to_string(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("start_stop_hotkey must be a string.".to_string()),
        };
        let panic_hotkey = match obj.get("panic_hotkey") {
            None => "F12".to_string(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("panic_hotkey must be a string.".to_string()),
        };
        let record_hotkey = match obj.get("record_hotkey") {
            None => "F7".to_string(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("record_hotkey must be a string.".to_string()),
        };
        let created_at = match obj.get("created_at") {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            _ => utc_now_iso(),
        };
        let updated_at = match obj.get("updated_at") {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            _ => utc_now_iso(),
        };

        let mut profile = Self {
            name,
            description,
            schema_version,
            repeat_mode,
            repeat_count,
            repeat_duration_s,
            inter_cycle_delay_ms,
            start_stop_hotkey,
            panic_hotkey,
            record_hotkey,
            actions,
            created_at,
            updated_at,
        };
        profile.validate()?;
        Ok(profile)
    }
}

/// Create the flagship default profile: Press E -> Wait 200ms -> Click -> Press 1.
pub fn create_default_profile() -> MacroProfile {
    MacroProfile {
        name: "Action Loop (E -> 200ms -> Click -> 1)".to_string(),
        description: "Classic action combo: Press E -> Wait 200ms -> Left Click -> Press 1"
            .to_string(),
        repeat_mode: RepeatMode::Infinite,
        repeat_count: 10,
        inter_cycle_delay_ms: 100,
        actions: vec![
            make_key_press("E", 25, "Interact").expect("valid preset"),
            make_wait(200, 10, "Animation Delay").expect("valid preset"),
            make_mouse_click(
                MouseButton::Left,
                ClickType::Single,
                None,
                None,
                25,
                "Primary Attack / Confirm",
            )
            .expect("valid preset"),
            make_wait(100, 5, "Post-Click Pause").expect("valid preset"),
            make_key_press("1", 25, "Equip Slot 1").expect("valid preset"),
        ],
        ..MacroProfile::default()
    }
}

/// Return built-in ready-to-run keyboard & sequence macro presets in deterministic order.
pub fn get_builtin_presets() -> Vec<MacroProfile> {
    vec![
        create_default_profile(),
        MacroProfile {
            name: "Key Spammer (Press E Loop)".to_string(),
            description: "Repeatedly taps [E] every 100ms with \u{00b1}10ms natural timing jitter."
                .to_string(),
            repeat_mode: RepeatMode::Infinite,
            repeat_count: 100,
            inter_cycle_delay_ms: 0,
            actions: vec![
                make_key_press("E", 25, "Tap E").expect("valid preset"),
                make_wait(75, 10, "Repeat Interval").expect("valid preset"),
            ],
            ..MacroProfile::default()
        },
        MacroProfile {
            name: "Tactical Sprint & Reload Combo".to_string(),
            description: "Holds Shift briefly, releases, then presses R with clean cadence."
                .to_string(),
            repeat_mode: RepeatMode::Count,
            repeat_count: 5,
            inter_cycle_delay_ms: 150,
            actions: vec![
                make_key_down("Shift", "Hold Sprint").expect("valid preset"),
                make_wait(150, 10, "Sprint Window").expect("valid preset"),
                make_key_up("Shift", "Release Sprint").expect("valid preset"),
                make_key_press("R", 30, "Reload").expect("valid preset"),
                make_wait(250, 15, "Recovery Pause").expect("valid preset"),
            ],
            ..MacroProfile::default()
        },
        MacroProfile {
            name: "Loot & Salvage Nested Loop".to_string(),
            description: "Repeats 'Press F -> Wait 120ms' 3 times, then clicks Confirm."
                .to_string(),
            repeat_mode: RepeatMode::Count,
            repeat_count: 20,
            inter_cycle_delay_ms: 250,
            actions: vec![
                make_repeat_block(
                    3,
                    vec![
                        make_key_press("F", 25, "").expect("valid preset"),
                        make_wait(120, 15, "").expect("valid preset"),
                    ],
                    "Collect 3 Items",
                )
                .expect("valid preset"),
                make_mouse_click(
                    MouseButton::Left,
                    ClickType::Single,
                    None,
                    None,
                    25,
                    "Confirm Dialog",
                )
                .expect("valid preset"),
                make_wait(200, 20, "").expect("valid preset"),
            ],
            ..MacroProfile::default()
        },
        MacroProfile {
            name: "Anti-AFK Subtle Pulse".to_string(),
            description:
                "Gently nudges cursor relative (+4, 0) and (-4, 0) with Space tap every 5s."
                    .to_string(),
            repeat_mode: RepeatMode::Duration,
            repeat_duration_s: 600.0,
            inter_cycle_delay_ms: 5000,
            actions: vec![
                make_mouse_move(4, 0, MoveMode::Relative, "Nudge Right").expect("valid preset"),
                make_wait(150, 25, "").expect("valid preset"),
                make_mouse_move(-4, 0, MoveMode::Relative, "Nudge Left").expect("valid preset"),
                make_wait(150, 25, "").expect("valid preset"),
                make_key_press("Space", 30, "Jump / Keep-Alive").expect("valid preset"),
            ],
            ..MacroProfile::default()
        },
    ]
}
