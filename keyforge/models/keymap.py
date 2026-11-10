"""Windows Virtual-Key (VK) codes, hardware scan codes, and human-readable key normalization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KeyInfo:
    """Metadata for a single keyboard key."""

    canonical_name: str
    vk_code: int
    scan_code: int = 0
    is_extended: bool = False
    is_modifier: bool = False


# Complete mapping of canonical key names to Windows Virtual-Key codes & scan codes
_KEY_DEFINITIONS: list[tuple[str, int, int, bool, bool, list[str]]] = [
    # Modifiers
    ("Ctrl", 0x11, 0x1D, False, True, ["control", "ctrl", "lctrl", "leftctrl"]),
    ("RCtrl", 0xA3, 0x1D, True, True, ["rctrl", "rightctrl", "rightcontrol"]),
    ("Shift", 0x10, 0x2A, False, True, ["shift", "lshift", "leftshift"]),
    ("RShift", 0xA1, 0x36, False, True, ["rshift", "rightshift"]),
    ("Alt", 0x12, 0x38, False, True, ["alt", "lalt", "leftalt", "menu"]),
    ("RAlt", 0xA5, 0x38, True, True, ["ralt", "rightalt", "altgr"]),
    ("Win", 0x5B, 0x5B, True, True, ["win", "lwin", "windows", "super", "meta"]),
    ("RWin", 0x5C, 0x5C, True, True, ["rwin", "rightwin"]),
    # Special & Navigation keys
    ("Backspace", 0x08, 0x0E, False, False, ["backspace", "bksp", "bs"]),
    ("Tab", 0x09, 0x0F, False, False, ["tab"]),
    ("Enter", 0x0D, 0x1C, False, False, ["enter", "return", "ret"]),
    ("Pause", 0x13, 0x45, False, False, ["pause", "break"]),
    ("CapsLock", 0x14, 0x3A, False, False, ["capslock", "caps"]),
    ("Esc", 0x1B, 0x01, False, False, ["esc", "escape"]),
    ("Space", 0x20, 0x39, False, False, ["space", "spacebar", "spc"]),
    ("PageUp", 0x21, 0x49, True, False, ["pageup", "pgup", "prior"]),
    ("PageDown", 0x22, 0x51, True, False, ["pagedown", "pgdn", "next"]),
    ("End", 0x23, 0x4F, True, False, ["end"]),
    ("Home", 0x24, 0x47, True, False, ["home"]),
    ("Left", 0x25, 0x4B, True, False, ["left", "leftarrow", "arrowleft"]),
    ("Up", 0x26, 0x48, True, False, ["up", "uparrow", "arrowup"]),
    ("Right", 0x27, 0x4D, True, False, ["right", "rightarrow", "arrowright"]),
    ("Down", 0x28, 0x50, True, False, ["down", "downarrow", "arrowdown"]),
    ("PrintScreen", 0x2C, 0x37, True, False, ["printscreen", "prtsc", "sysrq", "snapshot"]),
    ("Insert", 0x2D, 0x52, True, False, ["insert", "ins"]),
    ("Delete", 0x2E, 0x53, True, False, ["delete", "del"]),
    ("NumLock", 0x90, 0x45, True, False, ["numlock"]),
    ("ScrollLock", 0x91, 0x46, False, False, ["scrolllock", "scrlk"]),
    # Number row (0 - 9)
    ("0", 0x30, 0x0B, False, False, ["0", "d0"]),
    ("1", 0x31, 0x02, False, False, ["1", "d1"]),
    ("2", 0x32, 0x03, False, False, ["2", "d2"]),
    ("3", 0x33, 0x04, False, False, ["3", "d3"]),
    ("4", 0x34, 0x05, False, False, ["4", "d4"]),
    ("5", 0x35, 0x06, False, False, ["5", "d5"]),
    ("6", 0x36, 0x07, False, False, ["6", "d6"]),
    ("7", 0x37, 0x08, False, False, ["7", "d7"]),
    ("8", 0x38, 0x09, False, False, ["8", "d8"]),
    ("9", 0x39, 0x0A, False, False, ["9", "d9"]),
    # Alphabet (A - Z)
    ("A", 0x41, 0x1E, False, False, ["a"]),
    ("B", 0x42, 0x30, False, False, ["b"]),
    ("C", 0x43, 0x2E, False, False, ["c"]),
    ("D", 0x44, 0x20, False, False, ["d"]),
    ("E", 0x45, 0x12, False, False, ["e"]),
    ("F", 0x46, 0x21, False, False, ["f"]),
    ("G", 0x47, 0x22, False, False, ["g"]),
    ("H", 0x48, 0x23, False, False, ["h"]),
    ("I", 0x49, 0x17, False, False, ["i"]),
    ("J", 0x4A, 0x24, False, False, ["j"]),
    ("K", 0x4B, 0x25, False, False, ["k"]),
    ("L", 0x4C, 0x26, False, False, ["l"]),
    ("M", 0x4D, 0x32, False, False, ["m"]),
    ("N", 0x4E, 0x31, False, False, ["n"]),
    ("O", 0x4F, 0x18, False, False, ["o"]),
    ("P", 0x50, 0x19, False, False, ["p"]),
    ("Q", 0x51, 0x10, False, False, ["q"]),
    ("R", 0x52, 0x13, False, False, ["r"]),
    ("S", 0x53, 0x1F, False, False, ["s"]),
    ("T", 0x54, 0x14, False, False, ["t"]),
    ("U", 0x55, 0x16, False, False, ["u"]),
    ("V", 0x56, 0x2F, False, False, ["v"]),
    ("W", 0x57, 0x11, False, False, ["w"]),
    ("X", 0x58, 0x2D, False, False, ["x"]),
    ("Y", 0x59, 0x15, False, False, ["y"]),
    ("Z", 0x5A, 0x2C, False, False, ["z"]),
    # Numpad
    ("Numpad0", 0x60, 0x52, False, False, ["numpad0", "num0", "np0"]),
    ("Numpad1", 0x61, 0x4F, False, False, ["numpad1", "num1", "np1"]),
    ("Numpad2", 0x62, 0x50, False, False, ["numpad2", "num2", "np2"]),
    ("Numpad3", 0x63, 0x51, False, False, ["numpad3", "num3", "np3"]),
    ("Numpad4", 0x64, 0x4B, False, False, ["numpad4", "num4", "np4"]),
    ("Numpad5", 0x65, 0x4C, False, False, ["numpad5", "num5", "np5"]),
    ("Numpad6", 0x66, 0x4D, False, False, ["numpad6", "num6", "np6"]),
    ("Numpad7", 0x67, 0x47, False, False, ["numpad7", "num7", "np7"]),
    ("Numpad8", 0x68, 0x48, False, False, ["numpad8", "num8", "np8"]),
    ("Numpad9", 0x69, 0x49, False, False, ["numpad9", "num9", "np9"]),
    ("NumpadMultiply", 0x6A, 0x37, False, False, ["numpadmultiply", "nummultiply", "multiply"]),
    ("NumpadAdd", 0x6B, 0x4E, False, False, ["numpadadd", "numadd", "add"]),
    ("NumpadSubtract", 0x6D, 0x4A, False, False, ["numpadsubtract", "numsubtract", "subtract"]),
    ("NumpadDecimal", 0x6E, 0x53, False, False, ["numpaddecimal", "numdecimal", "decimal"]),
    ("NumpadDivide", 0x6F, 0x35, True, False, ["numpaddivide", "numdivide", "divide"]),
    # Function keys (F1 - F24)
    ("F1", 0x70, 0x3B, False, False, ["f1"]),
    ("F2", 0x71, 0x3C, False, False, ["f2"]),
    ("F3", 0x72, 0x3D, False, False, ["f3"]),
    ("F4", 0x73, 0x3E, False, False, ["f4"]),
    ("F5", 0x74, 0x3F, False, False, ["f5"]),
    ("F6", 0x75, 0x40, False, False, ["f6"]),
    ("F7", 0x76, 0x41, False, False, ["f7"]),
    ("F8", 0x77, 0x42, False, False, ["f8"]),
    ("F9", 0x78, 0x43, False, False, ["f9"]),
    ("F10", 0x79, 0x44, False, False, ["f10"]),
    ("F11", 0x7A, 0x57, False, False, ["f11"]),
    ("F12", 0x7B, 0x58, False, False, ["f12"]),
    ("F13", 0x7C, 0x64, False, False, ["f13"]),
    ("F14", 0x7D, 0x65, False, False, ["f14"]),
    ("F15", 0x7E, 0x66, False, False, ["f15"]),
    ("F16", 0x7F, 0x67, False, False, ["f16"]),
    ("F17", 0x80, 0x68, False, False, ["f17"]),
    ("F18", 0x81, 0x69, False, False, ["f18"]),
    ("F19", 0x82, 0x6A, False, False, ["f19"]),
    ("F20", 0x83, 0x6B, False, False, ["f20"]),
    ("F21", 0x84, 0x6C, False, False, ["f21"]),
    ("F22", 0x85, 0x6D, False, False, ["f22"]),
    ("F23", 0x86, 0x6E, False, False, ["f23"]),
    ("F24", 0x87, 0x76, False, False, ["f24"]),
    # Punctuation & OEM keys
    ("Semicolon", 0xBA, 0x27, False, False, ["semicolon", ";", "oem1"]),
    ("Equals", 0xBB, 0x0D, False, False, ["equals", "equal", "=", "plus"]),
    ("Comma", 0xBC, 0x33, False, False, ["comma", ","]),
    ("Minus", 0xBD, 0x0C, False, False, ["minus", "-", "hyphen", "underscore"]),
    ("Period", 0xBE, 0x34, False, False, ["period", ".", "dot"]),
    ("Slash", 0xBF, 0x35, False, False, ["slash", "/", "forwardslash"]),
    ("Grave", 0xC0, 0x29, False, False, ["grave", "`", "tilde", "~", "backtick"]),
    ("LeftBracket", 0xDB, 0x1A, False, False, ["leftbracket", "[", "lbracket"]),
    ("Backslash", 0xDC, 0x2B, False, False, ["backslash", "\\"]),
    ("RightBracket", 0xDD, 0x1B, False, False, ["rightbracket", "]", "rbracket"]),
    ("Quote", 0xDE, 0x28, False, False, ["quote", "'", "apostrophe"]),
]

CANONICAL_KEYS: dict[str, KeyInfo] = {}
ALIAS_TO_KEY: dict[str, KeyInfo] = {}
VK_TO_KEY: dict[int, KeyInfo] = {}

for canonical, vk, scan, ext, mod, aliases in _KEY_DEFINITIONS:
    info = KeyInfo(
        canonical_name=canonical,
        vk_code=vk,
        scan_code=scan,
        is_extended=ext,
        is_modifier=mod,
    )
    CANONICAL_KEYS[canonical] = info
    ALIAS_TO_KEY[canonical.lower()] = info
    for alias in aliases:
        ALIAS_TO_KEY[alias.lower()] = info
    if vk not in VK_TO_KEY:
        VK_TO_KEY[vk] = info

# Additional VK codes emitted by Windows low-level hooks (left/right modifier variants)
VK_TO_KEY.setdefault(0xA0, CANONICAL_KEYS["Shift"])  # VK_LSHIFT
VK_TO_KEY.setdefault(0xA2, CANONICAL_KEYS["Ctrl"])  # VK_LCONTROL
VK_TO_KEY.setdefault(0xA4, CANONICAL_KEYS["Alt"])  # VK_LMENU


def resolve_single_key(name: str) -> KeyInfo:
    """Resolve a single key name or alias into its canonical KeyInfo.

    Raises ValueError if the key name is unknown or empty.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Key name cannot be empty.")
    info = ALIAS_TO_KEY.get(cleaned.lower())
    if info is None:
        raise ValueError(f"Unknown key name: {name!r}")
    return info


def parse_key_combo(combo: str) -> list[KeyInfo]:
    """Parse a single key or modifier combination (e.g. 'Ctrl+Shift+E' or 'E') into KeyInfo list.

    Raises ValueError if any part of the key combination is invalid or empty.
    """
    if not isinstance(combo, str) or not combo.strip():
        raise ValueError("Key combination must be a non-empty string.")

    raw_parts = [part.strip() for part in combo.split("+")]
    if any(not p for p in raw_parts):
        raise ValueError(f"Malformed key combination: {combo!r}")

    resolved: list[KeyInfo] = []
    seen_vks: set[int] = set()
    for part in raw_parts:
        info = resolve_single_key(part)
        if info.vk_code in seen_vks:
            raise ValueError(f"Duplicate key {info.canonical_name!r} in combination {combo!r}")
        seen_vks.add(info.vk_code)
        resolved.append(info)

    if len(resolved) > 4:
        raise ValueError(f"Key combination {combo!r} exceeds maximum of 4 simultaneous keys.")

    return resolved


def normalize_key_combo(combo: str) -> str:
    """Return the normalized canonical representation of a key or combo (e.g. 'ctrl+e' -> 'Ctrl+E')."""
    infos = parse_key_combo(combo)
    return "+".join(k.canonical_name for k in infos)


def is_valid_key_combo(combo: str) -> bool:
    """Return True if combo is a valid single key or key combination."""
    try:
        parse_key_combo(combo)
        return True
    except ValueError:
        return False


def get_key_name_from_vk(vk_code: int) -> str | None:
    """Lookup the canonical key name for a Windows Virtual-Key code."""
    info = VK_TO_KEY.get(vk_code)
    return info.canonical_name if info else None


def list_common_keys() -> list[str]:
    """Return an ordered list of common key names for UI dropdowns."""
    priority = [
        "E",
        "1",
        "2",
        "3",
        "4",
        "5",
        "Space",
        "Enter",
        "Click",
        "W",
        "A",
        "S",
        "D",
        "F",
        "Q",
        "R",
        "Tab",
        "Shift",
        "Ctrl",
        "Alt",
        "Esc",
        "Up",
        "Down",
        "Left",
        "Right",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "F10",
        "F11",
        "F12",
    ]
    result = [k for k in priority if k in CANONICAL_KEYS]
    for k in CANONICAL_KEYS:
        if k not in result:
            result.append(k)
    return result
