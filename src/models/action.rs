//! Data models for individual macro actions and step primitives.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use uuid::Uuid;

use crate::models::keymap::{normalize_key_combo, resolve_single_key};

/// Supported primitive and composite macro step types.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionType {
    KeyPress,
    KeyDown,
    KeyUp,
    MouseClick,
    MouseMove,
    MouseScroll,
    Wait,
    RepeatBlock,
}

impl ActionType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::KeyPress => "key_press",
            Self::KeyDown => "key_down",
            Self::KeyUp => "key_up",
            Self::MouseClick => "mouse_click",
            Self::MouseMove => "mouse_move",
            Self::MouseScroll => "mouse_scroll",
            Self::Wait => "wait",
            Self::RepeatBlock => "repeat_block",
        }
    }

    pub fn from_str_value(s: &str) -> Result<Self, String> {
        match s {
            "key_press" => Ok(Self::KeyPress),
            "key_down" => Ok(Self::KeyDown),
            "key_up" => Ok(Self::KeyUp),
            "mouse_click" => Ok(Self::MouseClick),
            "mouse_move" => Ok(Self::MouseMove),
            "mouse_scroll" => Ok(Self::MouseScroll),
            "wait" => Ok(Self::Wait),
            "repeat_block" => Ok(Self::RepeatBlock),
            _ => Err(format!("Unsupported action_type: {s:?}")),
        }
    }
}

/// Supported mouse buttons.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MouseButton {
    Left,
    Right,
    Middle,
    X1,
    X2,
}

impl MouseButton {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Left => "left",
            Self::Right => "right",
            Self::Middle => "middle",
            Self::X1 => "x1",
            Self::X2 => "x2",
        }
    }

    pub fn capitalize(self) -> &'static str {
        match self {
            Self::Left => "Left",
            Self::Right => "Right",
            Self::Middle => "Middle",
            Self::X1 => "X1",
            Self::X2 => "X2",
        }
    }

    pub fn from_str_value(s: &str) -> Result<Self, String> {
        match s {
            "left" => Ok(Self::Left),
            "right" => Ok(Self::Right),
            "middle" => Ok(Self::Middle),
            "x1" => Ok(Self::X1),
            "x2" => Ok(Self::X2),
            _ => Err(format!("Unsupported mouse button: {s:?}")),
        }
    }
}

/// Mouse button actuation mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClickType {
    Single,
    Double,
    Down,
    Up,
}

impl ClickType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Single => "single",
            Self::Double => "double",
            Self::Down => "down",
            Self::Up => "up",
        }
    }

    pub fn from_str_value(s: &str) -> Result<Self, String> {
        match s {
            "single" => Ok(Self::Single),
            "double" => Ok(Self::Double),
            "down" => Ok(Self::Down),
            "up" => Ok(Self::Up),
            _ => Err(format!("Unsupported click_type: {s:?}")),
        }
    }
}

/// Cursor movement coordinate mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MoveMode {
    Absolute,
    Relative,
}

impl MoveMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Absolute => "absolute",
            Self::Relative => "relative",
        }
    }

    pub fn from_str_value(s: &str) -> Result<Self, String> {
        match s {
            "absolute" => Ok(Self::Absolute),
            "relative" => Ok(Self::Relative),
            _ => Err(format!("Unsupported move_mode: {s:?}")),
        }
    }
}

pub fn new_action_id() -> String {
    let hex = Uuid::new_v4().simple().to_string();
    hex[..12].to_string()
}

/// Represents a single step in a kTap macro sequence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MacroAction {
    pub action_type: ActionType,
    pub id: String,
    pub enabled: bool,
    pub label: String,
    pub key: String,
    pub hold_ms: i64,
    pub button: MouseButton,
    pub click_type: ClickType,
    pub x: Option<i64>,
    pub y: Option<i64>,
    pub move_mode: MoveMode,
    pub scroll_dx: i64,
    pub scroll_dy: i64,
    pub wait_ms: i64,
    pub jitter_ms: i64,
    pub repeat_count: i64,
    pub sub_actions: Vec<MacroAction>,
}

impl MacroAction {
    pub fn new(action_type: ActionType) -> Self {
        Self {
            action_type,
            id: new_action_id(),
            enabled: true,
            label: String::new(),
            key: "E".to_string(),
            hold_ms: 25,
            button: MouseButton::Left,
            click_type: ClickType::Single,
            x: None,
            y: None,
            move_mode: MoveMode::Absolute,
            scroll_dx: 0,
            scroll_dy: 0,
            wait_ms: 200,
            jitter_ms: 0,
            repeat_count: 2,
            sub_actions: Vec::new(),
        }
    }

    /// Validate and canonicalize all action fields according to `action_type`.
    pub fn validate(&mut self) -> Result<(), String> {
        if self.id.trim().is_empty() {
            return Err("Action id must be a non-empty string.".to_string());
        }
        if self.label.chars().count() > 120 {
            return Err("Action label cannot exceed 120 characters.".to_string());
        }

        match self.action_type {
            ActionType::KeyPress => {
                self.key = normalize_key_combo(&self.key)?;
                if !(0..=60_000).contains(&self.hold_ms) {
                    return Err("hold_ms must be between 0 and 60000 ms.".to_string());
                }
            }
            ActionType::KeyDown | ActionType::KeyUp => {
                let info = resolve_single_key(&self.key)?;
                self.key = info.canonical_name.to_string();
            }
            ActionType::MouseClick => {
                if self.x.is_some() != self.y.is_some() {
                    return Err(
                        "Mouse click coordinates x and y must both be set or both be None."
                            .to_string(),
                    );
                }
                if let (Some(cx), Some(cy)) = (self.x, self.y) {
                    if !(-32_768..=32_767).contains(&cx) || !(-32_768..=32_767).contains(&cy) {
                        return Err(
                            "Mouse click coordinates must be within [-32768, 32767].".to_string()
                        );
                    }
                }
                if !(0..=60_000).contains(&self.hold_ms) {
                    return Err("hold_ms must be between 0 and 60000 ms.".to_string());
                }
            }
            ActionType::MouseMove => {
                let (Some(cx), Some(cy)) = (self.x, self.y) else {
                    return Err("MOUSE_MOVE requires both x and y integer coordinates.".to_string());
                };
                if !(-32_768..=32_767).contains(&cx) || !(-32_768..=32_767).contains(&cy) {
                    return Err(
                        "Mouse move coordinates must be within [-32768, 32767].".to_string()
                    );
                }
            }
            ActionType::MouseScroll => {
                if !(-1000..=1000).contains(&self.scroll_dx)
                    || !(-1000..=1000).contains(&self.scroll_dy)
                {
                    return Err("Mouse scroll ticks must be within [-1000, 1000].".to_string());
                }
                if self.scroll_dx == 0 && self.scroll_dy == 0 {
                    return Err(
                        "Mouse scroll requires non-zero scroll_dx or scroll_dy.".to_string()
                    );
                }
            }
            ActionType::Wait => {
                if !(1..=3_600_000).contains(&self.wait_ms) {
                    return Err("wait_ms must be between 1 and 3,600,000 ms (1 hour).".to_string());
                }
                if !(0..=600_000).contains(&self.jitter_ms) {
                    return Err("jitter_ms must be between 0 and 600,000 ms.".to_string());
                }
            }
            ActionType::RepeatBlock => {
                if !(1..=10_000).contains(&self.repeat_count) {
                    return Err("repeat_count must be between 1 and 10,000.".to_string());
                }
                for sub in &mut self.sub_actions {
                    sub.validate()?;
                }
            }
        }
        Ok(())
    }

    /// Short uppercase badge tag for UI step cards.
    pub fn badge_text(&self) -> &'static str {
        match self.action_type {
            ActionType::KeyPress => "KEY",
            ActionType::KeyDown => "KEY \u{2193}",
            ActionType::KeyUp => "KEY \u{2191}",
            ActionType::MouseClick => "CLICK",
            ActionType::MouseMove => "MOVE",
            ActionType::MouseScroll => "SCROLL",
            ActionType::Wait => "WAIT",
            ActionType::RepeatBlock => "LOOP",
        }
    }

    /// Concise, human-friendly summary of the action.
    pub fn describe(&self) -> String {
        let prefix = if self.label.is_empty() {
            String::new()
        } else {
            format!("[{}] ", self.label)
        };
        match self.action_type {
            ActionType::KeyPress => {
                let hold_str = if self.hold_ms != 25 {
                    format!(" (hold {}ms)", self.hold_ms)
                } else {
                    String::new()
                };
                format!("{prefix}Press [{}]{hold_str}", self.key)
            }
            ActionType::KeyDown => format!("{prefix}Hold Key Down [{}]", self.key),
            ActionType::KeyUp => format!("{prefix}Release Key Up [{}]", self.key),
            ActionType::MouseClick => {
                let btn = self.button.capitalize();
                let ctype = match self.click_type {
                    ClickType::Single => "Click",
                    ClickType::Double => "Double-Click",
                    ClickType::Down => "Button Down",
                    ClickType::Up => "Button Up",
                };
                let loc = match (self.x, self.y) {
                    (Some(x), Some(y)) => format!(" at ({x}, {y})"),
                    _ => " at Cursor".to_string(),
                };
                format!("{prefix}{btn} {ctype}{loc}")
            }
            ActionType::MouseMove => match (self.move_mode, self.x, self.y) {
                (MoveMode::Relative, Some(x), Some(y)) => {
                    format!("{prefix}Move Cursor Relative ({x:+}, {y:+})")
                }
                (_, Some(x), Some(y)) => format!("{prefix}Move Cursor to ({x}, {y})"),
                _ => format!("{prefix}Move Cursor"),
            },
            ActionType::MouseScroll => {
                let mut parts = Vec::new();
                if self.scroll_dy != 0 {
                    let dir = if self.scroll_dy > 0 { "Up" } else { "Down" };
                    parts.push(format!("{dir} {}", self.scroll_dy.abs()));
                }
                if self.scroll_dx != 0 {
                    let dir = if self.scroll_dx > 0 { "Right" } else { "Left" };
                    parts.push(format!("{dir} {}", self.scroll_dx.abs()));
                }
                format!("{prefix}Scroll Wheel ({})", parts.join(", "))
            }
            ActionType::Wait => {
                let jitter = if self.jitter_ms > 0 {
                    format!(" (\u{00b1}{}ms jitter)", self.jitter_ms)
                } else {
                    String::new()
                };
                format!("{prefix}Wait {}ms{jitter}", self.wait_ms)
            }
            ActionType::RepeatBlock => {
                let mut inner = self
                    .sub_actions
                    .iter()
                    .take(4)
                    .map(Self::describe)
                    .collect::<Vec<_>>()
                    .join(" -> ");
                if self.sub_actions.len() > 4 {
                    inner.push_str(&format!(" ... (+{} more)", self.sub_actions.len() - 4));
                }
                format!("{prefix}Repeat {}x {{ {inner} }}", self.repeat_count)
            }
        }
    }

    /// Create a deep copy with fresh unique action IDs.
    pub fn clone_with_new_id(&self) -> Self {
        let mut cloned = self.clone();
        cloned.id = new_action_id();
        cloned.sub_actions = self
            .sub_actions
            .iter()
            .map(Self::clone_with_new_id)
            .collect();
        cloned
    }

    /// Serialize to the exact deterministic JSON payload matching KeyForge Python v1.x.
    pub fn to_json_value(&self) -> Value {
        let mut map = Map::new();
        map.insert("id".to_string(), Value::String(self.id.clone()));
        map.insert(
            "action_type".to_string(),
            Value::String(self.action_type.as_str().to_string()),
        );
        map.insert("enabled".to_string(), Value::Bool(self.enabled));
        if !self.label.is_empty() {
            map.insert("label".to_string(), Value::String(self.label.clone()));
        }

        match self.action_type {
            ActionType::KeyPress => {
                map.insert("key".to_string(), Value::String(self.key.clone()));
                map.insert("hold_ms".to_string(), Value::from(self.hold_ms));
            }
            ActionType::KeyDown | ActionType::KeyUp => {
                map.insert("key".to_string(), Value::String(self.key.clone()));
            }
            ActionType::MouseClick => {
                map.insert(
                    "button".to_string(),
                    Value::String(self.button.as_str().to_string()),
                );
                map.insert(
                    "click_type".to_string(),
                    Value::String(self.click_type.as_str().to_string()),
                );
                map.insert("hold_ms".to_string(), Value::from(self.hold_ms));
                map.insert(
                    "x".to_string(),
                    self.x.map(Value::from).unwrap_or(Value::Null),
                );
                map.insert(
                    "y".to_string(),
                    self.y.map(Value::from).unwrap_or(Value::Null),
                );
            }
            ActionType::MouseMove => {
                map.insert(
                    "x".to_string(),
                    self.x.map(Value::from).unwrap_or(Value::Null),
                );
                map.insert(
                    "y".to_string(),
                    self.y.map(Value::from).unwrap_or(Value::Null),
                );
                map.insert(
                    "move_mode".to_string(),
                    Value::String(self.move_mode.as_str().to_string()),
                );
            }
            ActionType::MouseScroll => {
                map.insert("scroll_dx".to_string(), Value::from(self.scroll_dx));
                map.insert("scroll_dy".to_string(), Value::from(self.scroll_dy));
            }
            ActionType::Wait => {
                map.insert("wait_ms".to_string(), Value::from(self.wait_ms));
                map.insert("jitter_ms".to_string(), Value::from(self.jitter_ms));
            }
            ActionType::RepeatBlock => {
                map.insert("repeat_count".to_string(), Value::from(self.repeat_count));
                map.insert(
                    "sub_actions".to_string(),
                    Value::Array(self.sub_actions.iter().map(Self::to_json_value).collect()),
                );
            }
        }
        Value::Object(map)
    }

    /// Deserialize a [`MacroAction`] from JSON with strict type & range validation.
    pub fn from_json_value(value: &Value) -> Result<Self, String> {
        let obj = value
            .as_object()
            .ok_or_else(|| "MacroAction data must be a dictionary.".to_string())?;

        let raw_type = obj
            .get("action_type")
            .and_then(Value::as_str)
            .ok_or_else(|| "Missing or invalid 'action_type'.".to_string())?;
        let action_type = ActionType::from_str_value(raw_type)?;

        let sub_actions = match obj.get("sub_actions") {
            None => Vec::new(),
            Some(Value::Array(arr)) => arr
                .iter()
                .map(Self::from_json_value)
                .collect::<Result<Vec<_>, _>>()?,
            Some(_) => return Err("'sub_actions' must be a list.".to_string()),
        };

        let id = match obj.get("id") {
            None | Some(Value::Null) => new_action_id(),
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            Some(Value::String(_)) => new_action_id(),
            Some(_) => return Err("id must be a string.".to_string()),
        };

        let enabled = match obj.get("enabled") {
            None => true,
            Some(v) => require_bool(v, "enabled")?,
        };

        let label = match obj.get("label") {
            None | Some(Value::Null) => String::new(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("label must be a string.".to_string()),
        };

        let key = match obj.get("key") {
            None => "E".to_string(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err("key must be a string.".to_string()),
        };

        let hold_ms = match obj.get("hold_ms") {
            None => 25,
            Some(v) => require_int(v, "hold_ms")?,
        };

        let button = match obj.get("button") {
            None => MouseButton::Left,
            Some(Value::String(s)) => MouseButton::from_str_value(s)?,
            Some(_) => return Err("button must be a string.".to_string()),
        };

        let click_type = match obj.get("click_type") {
            None => ClickType::Single,
            Some(Value::String(s)) => ClickType::from_str_value(s)?,
            Some(_) => return Err("click_type must be a string.".to_string()),
        };

        let x = match obj.get("x") {
            None | Some(Value::Null) => None,
            Some(v) => Some(require_int(v, "x")?),
        };
        let y = match obj.get("y") {
            None | Some(Value::Null) => None,
            Some(v) => Some(require_int(v, "y")?),
        };

        let move_mode = match obj.get("move_mode") {
            None => MoveMode::Absolute,
            Some(Value::String(s)) => MoveMode::from_str_value(s)?,
            Some(_) => return Err("move_mode must be a string.".to_string()),
        };

        let scroll_dx = match obj.get("scroll_dx") {
            None => 0,
            Some(v) => require_int(v, "scroll_dx")?,
        };
        let scroll_dy = match obj.get("scroll_dy") {
            None => {
                if action_type == ActionType::MouseScroll {
                    -1
                } else {
                    0
                }
            }
            Some(v) => require_int(v, "scroll_dy")?,
        };

        let wait_ms = match obj.get("wait_ms") {
            None => 200,
            Some(v) => require_int(v, "wait_ms")?,
        };
        let jitter_ms = match obj.get("jitter_ms") {
            None => 0,
            Some(v) => require_int(v, "jitter_ms")?,
        };
        let repeat_count = match obj.get("repeat_count") {
            None => 2,
            Some(v) => require_int(v, "repeat_count")?,
        };

        let mut action = Self {
            action_type,
            id,
            enabled,
            label,
            key,
            hold_ms,
            button,
            click_type,
            x,
            y,
            move_mode,
            scroll_dx,
            scroll_dy,
            wait_ms,
            jitter_ms,
            repeat_count,
            sub_actions,
        };
        action.validate()?;
        Ok(action)
    }
}

pub fn require_int(value: &Value, field_name: &str) -> Result<i64, String> {
    value
        .as_i64()
        .ok_or_else(|| format!("{field_name} must be an integer."))
}

pub fn require_bool(value: &Value, field_name: &str) -> Result<bool, String> {
    value
        .as_bool()
        .ok_or_else(|| format!("{field_name} must be a boolean."))
}

pub fn make_key_press(key: &str, hold_ms: i64, label: &str) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::KeyPress);
    act.key = key.to_string();
    act.hold_ms = hold_ms;
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}

pub fn make_key_down(key: &str, label: &str) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::KeyDown);
    act.key = key.to_string();
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}

pub fn make_key_up(key: &str, label: &str) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::KeyUp);
    act.key = key.to_string();
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}

pub fn make_mouse_click(
    button: MouseButton,
    click_type: ClickType,
    x: Option<i64>,
    y: Option<i64>,
    hold_ms: i64,
    label: &str,
) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::MouseClick);
    act.button = button;
    act.click_type = click_type;
    act.x = x;
    act.y = y;
    act.hold_ms = hold_ms;
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}

pub fn make_mouse_move(
    x: i64,
    y: i64,
    move_mode: MoveMode,
    label: &str,
) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::MouseMove);
    act.x = Some(x);
    act.y = Some(y);
    act.move_mode = move_mode;
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}

pub fn make_mouse_scroll(
    scroll_dy: i64,
    scroll_dx: i64,
    label: &str,
) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::MouseScroll);
    act.scroll_dx = scroll_dx;
    act.scroll_dy = scroll_dy;
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}

pub fn make_wait(wait_ms: i64, jitter_ms: i64, label: &str) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::Wait);
    act.wait_ms = wait_ms;
    act.jitter_ms = jitter_ms;
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}

pub fn make_repeat_block(
    repeat_count: i64,
    sub_actions: Vec<MacroAction>,
    label: &str,
) -> Result<MacroAction, String> {
    let mut act = MacroAction::new(ActionType::RepeatBlock);
    act.repeat_count = repeat_count;
    act.sub_actions = sub_actions;
    act.label = label.to_string();
    act.validate()?;
    Ok(act)
}
