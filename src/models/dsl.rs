//! Quick-Forge Expression DSL Parser & Formatter.

use std::sync::OnceLock;

use regex::Regex;

use crate::models::action::{
    make_key_down, make_key_press, make_key_up, make_mouse_click, make_mouse_move,
    make_mouse_scroll, make_repeat_block, make_wait, ActionType, ClickType, MacroAction,
    MouseButton, MoveMode,
};
use crate::models::keymap::{is_valid_key_combo, normalize_key_combo, resolve_single_key};

fn wait_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:wait|sleep|delay|pause)?\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|sec|seconds|milliseconds)?(?:\s*(?:\+-|±|~)\s*(?P<jitter>\d+)\s*(?:ms)?)?$",
        )
        .expect("valid wait regex")
    })
}

fn press_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:press|tap|key|send)\s+\[?(?P<key>[A-Za-z0-9_+;\-=,./`\[\]\\']+)\]?(?:\s*\(\s*hold\s+(?P<hold>\d+)\s*ms\s*\))?$",
        )
        .expect("valid press regex")
    })
}

fn keydown_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:keydown|hold(?:\s+key(?:\s+down)?)?)\s+\[?(?P<key>[A-Za-z0-9_;\-=,./`\[\]\\']+)\]?$",
        )
        .expect("valid keydown regex")
    })
}

fn keyup_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:keyup|release(?:\s+key(?:\s+up)?)?)\s+\[?(?P<key>[A-Za-z0-9_;\-=,./`\[\]\\']+)\]?$",
        )
        .expect("valid keyup regex")
    })
}

fn click_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:(?P<btn1>left|right|middle|x1|x2|l|r|m)?\s*(?P<kind>double[\s\-]?click|dclick|click|lclick|rclick|mclick|mousedown|mouseup|button\s+down|button\s+up))(?:\s+(?P<btn2>left|right|middle|x1|x2))?(?:\s+at\s+(?:cursor|\(\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*\)))?$",
        )
        .expect("valid click regex")
    })
}

fn move_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^(?:move(?:\s+cursor)?|cursor)\s+(?P<rel>relative|rel|offset|to)?\s*\(?\s*(?P<x>[+\-]?\d+)\s*,\s*(?P<y>[+\-]?\d+)\s*\)?$",
        )
        .expect("valid move regex")
    })
}

fn scroll_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)^scroll(?:\s+wheel)?\s+(?:(?P<dir>up|down|left|right)\s*(?P<amt>\d+)|(?P<signed>[+\-]?\d+))$",
        )
        .expect("valid scroll regex")
    })
}

fn repeat_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?is)^repeat\s+(?P<count>\d+)\s*x?\s*\{\s*(?P<body>.+)\s*\}$")
            .expect("valid repeat regex")
    })
}

fn split_top_level_steps(expression: &str) -> Result<Vec<String>, String> {
    let bytes = expression.as_bytes();
    let n = bytes.len();
    let mut steps = Vec::new();
    let mut start = 0usize;
    let mut brace_depth: usize = 0;
    let mut paren_depth: usize = 0;
    let mut i = 0usize;

    while i < n {
        match bytes[i] {
            b'{' => {
                brace_depth += 1;
                i += 1;
            }
            b'}' => {
                brace_depth = brace_depth.saturating_sub(1);
                i += 1;
            }
            b'(' => {
                paren_depth += 1;
                i += 1;
            }
            b')' => {
                paren_depth = paren_depth.saturating_sub(1);
                i += 1;
            }
            b'-' if brace_depth == 0 && paren_depth == 0 && i + 1 < n && bytes[i + 1] == b'>' => {
                let token = expression[start..i].trim();
                if !token.is_empty() {
                    steps.push(token.to_string());
                }
                i += 2;
                start = i;
            }
            b';' | b'\n' | b',' if brace_depth == 0 && paren_depth == 0 => {
                let token = expression[start..i].trim();
                if !token.is_empty() {
                    steps.push(token.to_string());
                }
                i += 1;
                start = i;
            }
            _ => {
                i += 1;
            }
        }
    }

    if brace_depth != 0 || paren_depth != 0 {
        return Err("Unbalanced braces '{ }' or parentheses '( )' in expression.".to_string());
    }

    let tail = expression[start..].trim();
    if !tail.is_empty() {
        steps.push(tail.to_string());
    }
    Ok(steps)
}

fn parse_single_step(step_str: &str, depth: usize) -> Result<MacroAction, String> {
    if depth > 5 {
        return Err("Maximum repeat nesting depth (5) exceeded in expression.".to_string());
    }

    let text = step_str.trim();
    if text.is_empty() {
        return Err("Empty step token.".to_string());
    }

    // 1. Repeat block
    if let Some(caps) = repeat_re().captures(text) {
        let count: i64 = caps["count"]
            .parse()
            .map_err(|_| "Invalid repeat count in expression.".to_string())?;
        let body = &caps["body"];
        let sub_tokens = split_top_level_steps(body)?;
        if sub_tokens.is_empty() {
            return Err("Repeat block cannot be empty.".to_string());
        }
        let sub_actions = sub_tokens
            .iter()
            .map(|tok| parse_single_step(tok, depth + 1))
            .collect::<Result<Vec<_>, _>>()?;
        return make_repeat_block(count, sub_actions, "");
    }

    // 2. Wait step
    if let Some(caps) = wait_re().captures(text) {
        let raw_val: f64 = caps["val"]
            .parse()
            .map_err(|_| "Invalid wait duration.".to_string())?;
        let unit = caps
            .name("unit")
            .map(|m| m.as_str().to_ascii_lowercase())
            .unwrap_or_default();
        let lowered = text.to_ascii_lowercase();
        let has_wait_kw = lowered.starts_with("wait")
            || lowered.starts_with("sleep")
            || lowered.starts_with("delay")
            || lowered.starts_with("pause");
        if has_wait_kw
            || matches!(
                unit.as_str(),
                "ms" | "s" | "sec" | "seconds" | "milliseconds"
            )
        {
            let wait_ms = if matches!(unit.as_str(), "s" | "sec" | "seconds") {
                (raw_val * 1000.0).round() as i64
            } else {
                raw_val.round() as i64
            };
            let jitter_ms = caps
                .name("jitter")
                .and_then(|m| m.as_str().parse::<i64>().ok())
                .unwrap_or(0);
            return make_wait(wait_ms.max(1), jitter_ms, "");
        }
    }

    // 3. Mouse Click step
    if let Some(caps) = click_re().captures(text) {
        let kind = caps["kind"].to_ascii_lowercase().replace('-', " ");
        let btn_raw = caps
            .name("btn1")
            .or_else(|| caps.name("btn2"))
            .map(|m| m.as_str().to_ascii_lowercase())
            .unwrap_or_default();
        let button = if kind == "lclick" || matches!(btn_raw.as_str(), "l" | "left" | "") {
            MouseButton::Left
        } else if kind == "rclick" || matches!(btn_raw.as_str(), "r" | "right") {
            MouseButton::Right
        } else if kind == "mclick" || matches!(btn_raw.as_str(), "m" | "middle") {
            MouseButton::Middle
        } else if btn_raw == "x1" {
            MouseButton::X1
        } else if btn_raw == "x2" {
            MouseButton::X2
        } else {
            MouseButton::Left
        };

        let click_type = match kind.as_str() {
            "double click" | "dclick" => ClickType::Double,
            "mousedown" | "button down" => ClickType::Down,
            "mouseup" | "button up" => ClickType::Up,
            _ => ClickType::Single,
        };

        let x = caps
            .name("x")
            .map(|m| m.as_str().parse::<i64>())
            .transpose()
            .map_err(|_| "Invalid click X coordinate.".to_string())?;
        let y = caps
            .name("y")
            .map(|m| m.as_str().parse::<i64>())
            .transpose()
            .map_err(|_| "Invalid click Y coordinate.".to_string())?;
        return make_mouse_click(button, click_type, x, y, 25, "");
    }

    // 4. KeyDown / KeyUp
    if let Some(caps) = keydown_re().captures(text) {
        let info = resolve_single_key(&caps["key"])?;
        return make_key_down(info.canonical_name, "");
    }
    if let Some(caps) = keyup_re().captures(text) {
        let info = resolve_single_key(&caps["key"])?;
        return make_key_up(info.canonical_name, "");
    }

    // 5. Explicit Key Press
    if let Some(caps) = press_re().captures(text) {
        let combo = normalize_key_combo(&caps["key"])?;
        let hold = caps
            .name("hold")
            .map(|m| m.as_str().parse::<i64>())
            .transpose()
            .map_err(|_| "Invalid hold duration.".to_string())?
            .unwrap_or(25);
        return make_key_press(&combo, hold, "");
    }

    // 6. Mouse Move
    if let Some(caps) = move_re().captures(text) {
        let rel_kw = caps
            .name("rel")
            .map(|m| m.as_str().to_ascii_lowercase())
            .unwrap_or_default();
        let mode = if matches!(rel_kw.as_str(), "relative" | "rel" | "offset") {
            MoveMode::Relative
        } else {
            MoveMode::Absolute
        };
        let x: i64 = caps["x"]
            .parse()
            .map_err(|_| "Invalid move X coordinate.".to_string())?;
        let y: i64 = caps["y"]
            .parse()
            .map_err(|_| "Invalid move Y coordinate.".to_string())?;
        return make_mouse_move(x, y, mode, "");
    }

    // 7. Mouse Scroll
    if let Some(caps) = scroll_re().captures(text) {
        if let Some(dir_m) = caps.name("dir") {
            let dir = dir_m.as_str().to_ascii_lowercase();
            let amt: i64 = caps["amt"]
                .parse()
                .map_err(|_| "Invalid scroll amount.".to_string())?;
            return match dir.as_str() {
                "up" => make_mouse_scroll(amt, 0, ""),
                "down" => make_mouse_scroll(-amt, 0, ""),
                "right" => make_mouse_scroll(0, amt, ""),
                "left" => make_mouse_scroll(0, -amt, ""),
                _ => Err("Invalid scroll direction.".to_string()),
            };
        } else if let Some(signed_m) = caps.name("signed") {
            let signed: i64 = signed_m
                .as_str()
                .parse()
                .map_err(|_| "Invalid scroll delta.".to_string())?;
            return make_mouse_scroll(signed, 0, "");
        }
    }

    // 8. Bare key shorthand
    if is_valid_key_combo(text) {
        let norm = normalize_key_combo(text)?;
        return make_key_press(&norm, 25, "");
    }

    Err(format!(
        "Unrecognized macro step syntax: {step_str:?}. \
         Examples: 'Press E', 'Wait 200ms', 'Click', 'Press 1', 'Click Right at (500, 300)'."
    ))
}

/// Parse a human-readable Quick-Forge expression into a list of [`MacroAction`] steps.
pub fn parse_quick_expression(expression: &str) -> Result<Vec<MacroAction>, String> {
    if expression.trim().is_empty() {
        return Err("Expression cannot be empty.".to_string());
    }
    if expression.chars().count() > 50_000 {
        return Err("Expression exceeds maximum allowed length (50,000 characters).".to_string());
    }

    let tokens = split_top_level_steps(expression)?;
    if tokens.is_empty() {
        return Err("No valid steps found in expression.".to_string());
    }

    tokens.iter().map(|tok| parse_single_step(tok, 0)).collect()
}

/// Convert a single [`MacroAction`] back into a canonical Quick-Forge expression token.
pub fn format_action_to_dsl(action: &MacroAction) -> String {
    match action.action_type {
        ActionType::KeyPress => {
            if action.hold_ms != 25 {
                format!("Press {} (hold {}ms)", action.key, action.hold_ms)
            } else {
                format!("Press {}", action.key)
            }
        }
        ActionType::KeyDown => format!("KeyDown {}", action.key),
        ActionType::KeyUp => format!("KeyUp {}", action.key),
        ActionType::MouseClick => {
            let base =
                if action.button == MouseButton::Left && action.click_type == ClickType::Single {
                    "Click".to_string()
                } else {
                    let kind = match action.click_type {
                        ClickType::Single => "Click",
                        ClickType::Double => "Double-Click",
                        ClickType::Down => "MouseDown",
                        ClickType::Up => "MouseUp",
                    };
                    format!("{} {kind}", action.button.capitalize())
                };
            match (action.x, action.y) {
                (Some(x), Some(y)) => format!("{base} at ({x}, {y})"),
                _ => base,
            }
        }
        ActionType::MouseMove => {
            let rel = if action.move_mode == MoveMode::Relative {
                " Relative"
            } else {
                ""
            };
            format!(
                "Move{rel} ({}, {})",
                action.x.unwrap_or(0),
                action.y.unwrap_or(0)
            )
        }
        ActionType::MouseScroll => {
            if action.scroll_dy != 0 {
                let dir = if action.scroll_dy > 0 { "Up" } else { "Down" };
                format!("Scroll {dir} {}", action.scroll_dy.abs())
            } else {
                let dir = if action.scroll_dx > 0 {
                    "Right"
                } else {
                    "Left"
                };
                format!("Scroll {dir} {}", action.scroll_dx.abs())
            }
        }
        ActionType::Wait => {
            if action.jitter_ms > 0 {
                format!("Wait {}ms \u{00b1}{}ms", action.wait_ms, action.jitter_ms)
            } else {
                format!("Wait {}ms", action.wait_ms)
            }
        }
        ActionType::RepeatBlock => {
            let inner = action
                .sub_actions
                .iter()
                .map(format_action_to_dsl)
                .collect::<Vec<_>>()
                .join(" -> ");
            format!("Repeat {} {{ {inner} }}", action.repeat_count)
        }
    }
}

/// Format enabled [`MacroAction`]s into a single-line Quick-Forge expression.
pub fn format_actions_to_dsl(actions: &[MacroAction]) -> String {
    actions
        .iter()
        .filter(|a| a.enabled)
        .map(format_action_to_dsl)
        .collect::<Vec<_>>()
        .join(" -> ")
}
