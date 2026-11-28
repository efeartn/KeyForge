//! Windows Virtual-Key (VK) codes, hardware scan codes, and human-readable key normalization.

use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

/// Metadata for a single keyboard key.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct KeyInfo {
    pub canonical_name: &'static str,
    pub vk_code: u16,
    pub scan_code: u16,
    pub is_extended: bool,
    pub is_modifier: bool,
}

type KeyDef = (&'static str, u16, u16, bool, bool, &'static [&'static str]);

const KEY_DEFINITIONS: &[KeyDef] = &[
    // Modifiers
    (
        "Ctrl",
        0x11,
        0x1D,
        false,
        true,
        &["control", "ctrl", "lctrl", "leftctrl"],
    ),
    (
        "RCtrl",
        0xA3,
        0x1D,
        true,
        true,
        &["rctrl", "rightctrl", "rightcontrol"],
    ),
    (
        "Shift",
        0x10,
        0x2A,
        false,
        true,
        &["shift", "lshift", "leftshift"],
    ),
    ("RShift", 0xA1, 0x36, false, true, &["rshift", "rightshift"]),
    (
        "Alt",
        0x12,
        0x38,
        false,
        true,
        &["alt", "lalt", "leftalt", "menu", "option", "opt"],
    ),
    (
        "RAlt",
        0xA5,
        0x38,
        true,
        true,
        &["ralt", "rightalt", "altgr", "roption", "ropt"],
    ),
    (
        "Win",
        0x5B,
        0x5B,
        true,
        true,
        &["win", "lwin", "windows", "super", "meta", "cmd", "command"],
    ),
    (
        "RWin",
        0x5C,
        0x5C,
        true,
        true,
        &["rwin", "rightwin", "rcmd", "rcommand"],
    ),
    // Special & Navigation keys
    (
        "Backspace",
        0x08,
        0x0E,
        false,
        false,
        &["backspace", "bksp", "bs"],
    ),
    ("Tab", 0x09, 0x0F, false, false, &["tab"]),
    (
        "Enter",
        0x0D,
        0x1C,
        false,
        false,
        &["enter", "return", "ret"],
    ),
    ("Pause", 0x13, 0x45, false, false, &["pause", "break"]),
    ("CapsLock", 0x14, 0x3A, false, false, &["capslock", "caps"]),
    ("Esc", 0x1B, 0x01, false, false, &["esc", "escape"]),
    (
        "Space",
        0x20,
        0x39,
        false,
        false,
        &["space", "spacebar", "spc"],
    ),
    (
        "PageUp",
        0x21,
        0x49,
        true,
        false,
        &["pageup", "pgup", "prior"],
    ),
    (
        "PageDown",
        0x22,
        0x51,
        true,
        false,
        &["pagedown", "pgdn", "next"],
    ),
    ("End", 0x23, 0x4F, true, false, &["end"]),
    ("Home", 0x24, 0x47, true, false, &["home"]),
    (
        "Left",
        0x25,
        0x4B,
        true,
        false,
        &["left", "leftarrow", "arrowleft"],
    ),
    ("Up", 0x26, 0x48, true, false, &["up", "uparrow", "arrowup"]),
    (
        "Right",
        0x27,
        0x4D,
        true,
        false,
        &["right", "rightarrow", "arrowright"],
    ),
    (
        "Down",
        0x28,
        0x50,
        true,
        false,
        &["down", "downarrow", "arrowdown"],
    ),
    (
        "PrintScreen",
        0x2C,
        0x37,
        true,
        false,
        &["printscreen", "prtsc", "sysrq", "snapshot"],
    ),
    ("Insert", 0x2D, 0x52, true, false, &["insert", "ins"]),
    ("Delete", 0x2E, 0x53, true, false, &["delete", "del"]),
    ("NumLock", 0x90, 0x45, true, false, &["numlock"]),
    (
        "ScrollLock",
        0x91,
        0x46,
        false,
        false,
        &["scrolllock", "scrlk"],
    ),
    // Number row (0 - 9)
    ("0", 0x30, 0x0B, false, false, &["0", "d0"]),
    ("1", 0x31, 0x02, false, false, &["1", "d1"]),
    ("2", 0x32, 0x03, false, false, &["2", "d2"]),
    ("3", 0x33, 0x04, false, false, &["3", "d3"]),
    ("4", 0x34, 0x05, false, false, &["4", "d4"]),
    ("5", 0x35, 0x06, false, false, &["5", "d5"]),
    ("6", 0x36, 0x07, false, false, &["6", "d6"]),
    ("7", 0x37, 0x08, false, false, &["7", "d7"]),
    ("8", 0x38, 0x09, false, false, &["8", "d8"]),
    ("9", 0x39, 0x0A, false, false, &["9", "d9"]),
    // Alphabet (A - Z)
    ("A", 0x41, 0x1E, false, false, &["a"]),
    ("B", 0x42, 0x30, false, false, &["b"]),
    ("C", 0x43, 0x2E, false, false, &["c"]),
    ("D", 0x44, 0x20, false, false, &["d"]),
    ("E", 0x45, 0x12, false, false, &["e"]),
    ("F", 0x46, 0x21, false, false, &["f"]),
    ("G", 0x47, 0x22, false, false, &["g"]),
    ("H", 0x48, 0x23, false, false, &["h"]),
    ("I", 0x49, 0x17, false, false, &["i"]),
    ("J", 0x4A, 0x24, false, false, &["j"]),
    ("K", 0x4B, 0x25, false, false, &["k"]),
    ("L", 0x4C, 0x26, false, false, &["l"]),
    ("M", 0x4D, 0x32, false, false, &["m"]),
    ("N", 0x4E, 0x31, false, false, &["n"]),
    ("O", 0x4F, 0x18, false, false, &["o"]),
    ("P", 0x50, 0x19, false, false, &["p"]),
    ("Q", 0x51, 0x10, false, false, &["q"]),
    ("R", 0x52, 0x13, false, false, &["r"]),
    ("S", 0x53, 0x1F, false, false, &["s"]),
    ("T", 0x54, 0x14, false, false, &["t"]),
    ("U", 0x55, 0x16, false, false, &["u"]),
    ("V", 0x56, 0x2F, false, false, &["v"]),
    ("W", 0x57, 0x11, false, false, &["w"]),
    ("X", 0x58, 0x2D, false, false, &["x"]),
    ("Y", 0x59, 0x15, false, false, &["y"]),
    ("Z", 0x5A, 0x2C, false, false, &["z"]),
    // Numpad
    (
        "Numpad0",
        0x60,
        0x52,
        false,
        false,
        &["numpad0", "num0", "np0"],
    ),
    (
        "Numpad1",
        0x61,
        0x4F,
        false,
        false,
        &["numpad1", "num1", "np1"],
    ),
    (
        "Numpad2",
        0x62,
        0x50,
        false,
        false,
        &["numpad2", "num2", "np2"],
    ),
    (
        "Numpad3",
        0x63,
        0x51,
        false,
        false,
        &["numpad3", "num3", "np3"],
    ),
    (
        "Numpad4",
        0x64,
        0x4B,
        false,
        false,
        &["numpad4", "num4", "np4"],
    ),
    (
        "Numpad5",
        0x65,
        0x4C,
        false,
        false,
        &["numpad5", "num5", "np5"],
    ),
    (
        "Numpad6",
        0x66,
        0x4D,
        false,
        false,
        &["numpad6", "num6", "np6"],
    ),
    (
        "Numpad7",
        0x67,
        0x47,
        false,
        false,
        &["numpad7", "num7", "np7"],
    ),
    (
        "Numpad8",
        0x68,
        0x48,
        false,
        false,
        &["numpad8", "num8", "np8"],
    ),
    (
        "Numpad9",
        0x69,
        0x49,
        false,
        false,
        &["numpad9", "num9", "np9"],
    ),
    (
        "NumpadMultiply",
        0x6A,
        0x37,
        false,
        false,
        &["numpadmultiply", "nummultiply", "multiply"],
    ),
    (
        "NumpadAdd",
        0x6B,
        0x4E,
        false,
        false,
        &["numpadadd", "numadd", "add"],
    ),
    (
        "NumpadSubtract",
        0x6D,
        0x4A,
        false,
        false,
        &["numpadsubtract", "numsubtract", "subtract"],
    ),
    (
        "NumpadDecimal",
        0x6E,
        0x53,
        false,
        false,
        &["numpaddecimal", "numdecimal", "decimal"],
    ),
    (
        "NumpadDivide",
        0x6F,
        0x35,
        true,
        false,
        &["numpaddivide", "numdivide", "divide"],
    ),
    // Function keys (F1 - F24)
    ("F1", 0x70, 0x3B, false, false, &["f1"]),
    ("F2", 0x71, 0x3C, false, false, &["f2"]),
    ("F3", 0x72, 0x3D, false, false, &["f3"]),
    ("F4", 0x73, 0x3E, false, false, &["f4"]),
    ("F5", 0x74, 0x3F, false, false, &["f5"]),
    ("F6", 0x75, 0x40, false, false, &["f6"]),
    ("F7", 0x76, 0x41, false, false, &["f7"]),
    ("F8", 0x77, 0x42, false, false, &["f8"]),
    ("F9", 0x78, 0x43, false, false, &["f9"]),
    ("F10", 0x79, 0x44, false, false, &["f10"]),
    ("F11", 0x7A, 0x57, false, false, &["f11"]),
    ("F12", 0x7B, 0x58, false, false, &["f12"]),
    ("F13", 0x7C, 0x64, false, false, &["f13"]),
    ("F14", 0x7D, 0x65, false, false, &["f14"]),
    ("F15", 0x7E, 0x66, false, false, &["f15"]),
    ("F16", 0x7F, 0x67, false, false, &["f16"]),
    ("F17", 0x80, 0x68, false, false, &["f17"]),
    ("F18", 0x81, 0x69, false, false, &["f18"]),
    ("F19", 0x82, 0x6A, false, false, &["f19"]),
    ("F20", 0x83, 0x6B, false, false, &["f20"]),
    ("F21", 0x84, 0x6C, false, false, &["f21"]),
    ("F22", 0x85, 0x6D, false, false, &["f22"]),
    ("F23", 0x86, 0x6E, false, false, &["f23"]),
    ("F24", 0x87, 0x76, false, false, &["f24"]),
    // Punctuation & OEM keys
    (
        "Semicolon",
        0xBA,
        0x27,
        false,
        false,
        &["semicolon", ";", "oem1"],
    ),
    (
        "Equals",
        0xBB,
        0x0D,
        false,
        false,
        &["equals", "equal", "=", "plus"],
    ),
    ("Comma", 0xBC, 0x33, false, false, &["comma", ","]),
    (
        "Minus",
        0xBD,
        0x0C,
        false,
        false,
        &["minus", "-", "hyphen", "underscore"],
    ),
    ("Period", 0xBE, 0x34, false, false, &["period", ".", "dot"]),
    (
        "Slash",
        0xBF,
        0x35,
        false,
        false,
        &["slash", "/", "forwardslash"],
    ),
    (
        "Grave",
        0xC0,
        0x29,
        false,
        false,
        &["grave", "`", "tilde", "~", "backtick"],
    ),
    (
        "LeftBracket",
        0xDB,
        0x1A,
        false,
        false,
        &["leftbracket", "[", "lbracket"],
    ),
    ("Backslash", 0xDC, 0x2B, false, false, &["backslash", "\\"]),
    (
        "RightBracket",
        0xDD,
        0x1B,
        false,
        false,
        &["rightbracket", "]", "rbracket"],
    ),
    (
        "Quote",
        0xDE,
        0x28,
        false,
        false,
        &["quote", "'", "apostrophe"],
    ),
];

struct KeyTables {
    ordered_keys: Vec<KeyInfo>,
    canonical_map: HashMap<&'static str, KeyInfo>,
    alias_map: HashMap<String, KeyInfo>,
    vk_map: HashMap<u16, KeyInfo>,
}

fn tables() -> &'static KeyTables {
    static TABLES: OnceLock<KeyTables> = OnceLock::new();
    TABLES.get_or_init(|| {
        let mut ordered_keys = Vec::with_capacity(KEY_DEFINITIONS.len());
        let mut canonical_map = HashMap::with_capacity(KEY_DEFINITIONS.len());
        let mut alias_map = HashMap::with_capacity(KEY_DEFINITIONS.len() * 3);
        let mut vk_map = HashMap::with_capacity(KEY_DEFINITIONS.len() + 4);

        for &(canonical_name, vk_code, scan_code, is_extended, is_modifier, aliases) in
            KEY_DEFINITIONS
        {
            let info = KeyInfo {
                canonical_name,
                vk_code,
                scan_code,
                is_extended,
                is_modifier,
            };
            ordered_keys.push(info);
            canonical_map.insert(canonical_name, info);
            alias_map.insert(canonical_name.to_ascii_lowercase(), info);
            for alias in aliases {
                alias_map.insert((*alias).to_ascii_lowercase(), info);
            }
            vk_map.entry(vk_code).or_insert(info);
        }

        if let Some(&shift) = canonical_map.get("Shift") {
            vk_map.entry(0xA0).or_insert(shift); // VK_LSHIFT
        }
        if let Some(&ctrl) = canonical_map.get("Ctrl") {
            vk_map.entry(0xA2).or_insert(ctrl); // VK_LCONTROL
        }
        if let Some(&alt) = canonical_map.get("Alt") {
            vk_map.entry(0xA4).or_insert(alt); // VK_LMENU
        }

        KeyTables {
            ordered_keys,
            canonical_map,
            alias_map,
            vk_map,
        }
    })
}

/// Resolve a single key name or alias into its canonical [`KeyInfo`].
pub fn resolve_single_key(name: &str) -> Result<KeyInfo, String> {
    let cleaned = name.trim();
    if cleaned.is_empty() {
        return Err("Key name cannot be empty.".to_string());
    }
    tables()
        .alias_map
        .get(&cleaned.to_ascii_lowercase())
        .copied()
        .ok_or_else(|| format!("Unknown key name: {name:?}"))
}

/// Parse a single key or modifier combination (e.g. `'Ctrl+Shift+E'` or `'E'`) into a [`KeyInfo`] list.
pub fn parse_key_combo(combo: &str) -> Result<Vec<KeyInfo>, String> {
    if combo.trim().is_empty() {
        return Err("Key combination must be a non-empty string.".to_string());
    }

    let raw_parts: Vec<&str> = combo.split('+').map(str::trim).collect();
    if raw_parts.iter().any(|p| p.is_empty()) {
        return Err(format!("Malformed key combination: {combo:?}"));
    }

    let mut resolved = Vec::with_capacity(raw_parts.len());
    let mut seen_vks = HashSet::with_capacity(raw_parts.len());
    for part in raw_parts {
        let info = resolve_single_key(part)?;
        if !seen_vks.insert(info.vk_code) {
            return Err(format!(
                "Duplicate key {:?} in combination {combo:?}",
                info.canonical_name
            ));
        }
        resolved.push(info);
    }

    if resolved.len() > 4 {
        return Err(format!(
            "Key combination {combo:?} exceeds maximum of 4 simultaneous keys."
        ));
    }

    Ok(resolved)
}

/// Return the normalized canonical representation of a key or combo (e.g. `'ctrl+e'` -> `'Ctrl+E'`).
pub fn normalize_key_combo(combo: &str) -> Result<String, String> {
    let infos = parse_key_combo(combo)?;
    Ok(infos
        .into_iter()
        .map(|k| k.canonical_name)
        .collect::<Vec<_>>()
        .join("+"))
}

/// Return `true` if `combo` is a valid single key or key combination.
pub fn is_valid_key_combo(combo: &str) -> bool {
    parse_key_combo(combo).is_ok()
}

/// Lookup the canonical key name for a Windows Virtual-Key code.
pub fn get_key_name_from_vk(vk_code: u16) -> Option<&'static str> {
    tables().vk_map.get(&vk_code).map(|k| k.canonical_name)
}

/// Lookup full [`KeyInfo`] by Virtual-Key code.
pub fn get_key_info_from_vk(vk_code: u16) -> Option<KeyInfo> {
    tables().vk_map.get(&vk_code).copied()
}

/// Return all registered canonical keys in definition order.
pub fn all_key_infos() -> &'static [KeyInfo] {
    &tables().ordered_keys
}

/// Return an ordered list of common key names for UI dropdowns.
pub fn list_common_keys() -> Vec<&'static str> {
    const PRIORITY: &[&str] = &[
        "E", "1", "2", "3", "4", "5", "Space", "Enter", "W", "A", "S", "D", "F", "Q", "R", "Tab",
        "Shift", "Ctrl", "Alt", "Esc", "Up", "Down", "Left", "Right", "F1", "F2", "F3", "F4", "F5",
        "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    ];
    let tbl = tables();
    let mut result = Vec::with_capacity(tbl.ordered_keys.len());
    let mut added = HashSet::with_capacity(tbl.ordered_keys.len());
    for &p in PRIORITY {
        if tbl.canonical_map.contains_key(p) && added.insert(p) {
            result.push(p);
        }
    }
    for info in &tbl.ordered_keys {
        if added.insert(info.canonical_name) {
            result.push(info.canonical_name);
        }
    }
    result
}

/// Convert an `egui::Key` into a canonical kTap key name.
pub fn egui_key_to_canonical(key: egui::Key) -> Option<&'static str> {
    use egui::Key;
    match key {
        Key::A => Some("A"),
        Key::B => Some("B"),
        Key::C => Some("C"),
        Key::D => Some("D"),
        Key::E => Some("E"),
        Key::F => Some("F"),
        Key::G => Some("G"),
        Key::H => Some("H"),
        Key::I => Some("I"),
        Key::J => Some("J"),
        Key::K => Some("K"),
        Key::L => Some("L"),
        Key::M => Some("M"),
        Key::N => Some("N"),
        Key::O => Some("O"),
        Key::P => Some("P"),
        Key::Q => Some("Q"),
        Key::R => Some("R"),
        Key::S => Some("S"),
        Key::T => Some("T"),
        Key::U => Some("U"),
        Key::V => Some("V"),
        Key::W => Some("W"),
        Key::X => Some("X"),
        Key::Y => Some("Y"),
        Key::Z => Some("Z"),
        Key::Num0 => Some("0"),
        Key::Num1 => Some("1"),
        Key::Num2 => Some("2"),
        Key::Num3 => Some("3"),
        Key::Num4 => Some("4"),
        Key::Num5 => Some("5"),
        Key::Num6 => Some("6"),
        Key::Num7 => Some("7"),
        Key::Num8 => Some("8"),
        Key::Num9 => Some("9"),
        Key::F1 => Some("F1"),
        Key::F2 => Some("F2"),
        Key::F3 => Some("F3"),
        Key::F4 => Some("F4"),
        Key::F5 => Some("F5"),
        Key::F6 => Some("F6"),
        Key::F7 => Some("F7"),
        Key::F8 => Some("F8"),
        Key::F9 => Some("F9"),
        Key::F10 => Some("F10"),
        Key::F11 => Some("F11"),
        Key::F12 => Some("F12"),
        Key::F13 => Some("F13"),
        Key::F14 => Some("F14"),
        Key::F15 => Some("F15"),
        Key::F16 => Some("F16"),
        Key::F17 => Some("F17"),
        Key::F18 => Some("F18"),
        Key::F19 => Some("F19"),
        Key::F20 => Some("F20"),
        Key::ArrowDown => Some("Down"),
        Key::ArrowLeft => Some("Left"),
        Key::ArrowRight => Some("Right"),
        Key::ArrowUp => Some("Up"),
        Key::Escape => Some("Esc"),
        Key::Tab => Some("Tab"),
        Key::Backspace => Some("Backspace"),
        Key::Enter => Some("Enter"),
        Key::Space => Some("Space"),
        Key::Insert => Some("Insert"),
        Key::Delete => Some("Delete"),
        Key::Home => Some("Home"),
        Key::End => Some("End"),
        Key::PageUp => Some("PageUp"),
        Key::PageDown => Some("PageDown"),
        Key::Minus => Some("Minus"),
        Key::Equals => Some("Equals"),
        Key::Comma => Some("Comma"),
        Key::Period => Some("Period"),
        Key::Slash => Some("Slash"),
        Key::Semicolon => Some("Semicolon"),
        Key::Backtick => Some("Grave"),
        Key::OpenBracket => Some("LeftBracket"),
        Key::Backslash => Some("Backslash"),
        Key::CloseBracket => Some("RightBracket"),
        Key::Quote => Some("Quote"),
        _ => None,
    }
}

/// Build a canonical key or modifier+key combo from an `egui::Key` and active modifiers.
pub fn format_egui_key_combo(key: egui::Key, modifiers: egui::Modifiers) -> Option<String> {
    let canonical = egui_key_to_canonical(key)?;
    let mut parts: Vec<&str> = Vec::with_capacity(5);
    if modifiers.ctrl {
        parts.push("Ctrl");
    }
    if modifiers.shift {
        parts.push("Shift");
    }
    if modifiers.alt {
        parts.push("Alt");
    }
    if modifiers.mac_cmd || modifiers.command && !modifiers.ctrl {
        parts.push("Win");
    }
    parts.push(canonical);
    normalize_key_combo(&parts.join("+")).ok()
}
