"""Crash-safe platform configuration persistence and active profile backup for KeyForge."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from keyforge.models.keymap import normalize_key_combo
from keyforge.models.profile import MacroProfile
from keyforge.security.integrity import (
    load_profile_secure,
    parse_and_verify_envelope,
    save_profile_secure,
)
from keyforge.security.validator import MAX_PROFILE_FILE_BYTES, SecurityValidationError

CONFIG_FILENAME = "config.json"
CONFIG_BACKUP_FILENAME = "config.json.bak"
ACTIVE_PROFILE_FILENAME = "active_profile.kfmacro.json"
ACTIVE_PROFILE_BACKUP_FILENAME = "active_profile.kfmacro.json.bak"
PRESETS_DIRNAME = "presets"
MAX_CONFIG_FILE_BYTES = 1_048_576  # 1 MiB safety limit
_PRESET_FILENAME_SANITIZER = re.compile(r"[^A-Za-z0-9_ -]+")

VALID_UI_MODES = ("simple", "studio")
VALID_SIMPLE_TASKS = ("clicker", "spammer", "sequence")


def _default_simple_clicker() -> dict[str, Any]:
    return {
        "button": "Left",
        "click_type": "Single",
        "interval_ms": 100,
        "jitter_enabled": True,
        "loc_mode": "cursor",
        "x": 0,
        "y": 0,
    }


def _default_simple_spammer() -> dict[str, Any]:
    return {
        "key": "E",
        "interval_ms": 100,
        "jitter_enabled": True,
    }


def _default_recorder_settings() -> dict[str, Any]:
    return {
        "record_delays": True,
        "merge_key_taps": True,
        "capture_click_coordinates": False,
    }


def _normalize_ui_mode(value: Any) -> str:
    """Normalize UI mode, migrating legacy labels ('✨ Simple', '⚡ Studio', 'Pro Mode', 'advanced')."""
    if not isinstance(value, str):
        return "simple"
    lowered = value.strip().lower()
    if "studio" in lowered or "advanced" in lowered or re.search(r"\bpro\b", lowered):
        return "studio"
    if "simple" in lowered or "easy" in lowered:
        return "simple"
    return "simple"


def _normalize_simple_task(value: Any) -> str:
    """Normalize Simple Mode task selection, gracefully migrating legacy labels."""
    if not isinstance(value, str):
        return "clicker"
    lowered = value.strip().lower()
    if "spam" in lowered:
        return "spammer"
    if "seq" in lowered or "combo" in lowered:
        return "sequence"
    if "click" in lowered:
        return "clicker"
    return "clicker"


def _normalize_hotkey(value: Any, default: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return normalize_key_combo(value)
    except ValueError:
        return default


def _normalize_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or "\x00" in cleaned:
        return None
    return cleaned


def _coerce_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, min(maximum, value))


def _normalize_simple_clicker(raw: Any) -> dict[str, Any]:
    defaults = _default_simple_clicker()
    if not isinstance(raw, dict):
        return defaults

    merged = dict(defaults)
    button_raw = raw.get("button")
    if isinstance(button_raw, str):
        btn_map = {"left": "Left", "right": "Right", "middle": "Middle"}
        merged["button"] = btn_map.get(button_raw.strip().lower(), defaults["button"])

    click_type_raw = raw.get("click_type")
    if isinstance(click_type_raw, str):
        ct_map = {"single": "Single", "double": "Double"}
        merged["click_type"] = ct_map.get(click_type_raw.strip().lower(), defaults["click_type"])

    merged["interval_ms"] = _coerce_int(raw.get("interval_ms"), defaults["interval_ms"], 1, 3_600_000)
    merged["jitter_enabled"] = _coerce_bool(raw.get("jitter_enabled"), defaults["jitter_enabled"])

    loc_raw = raw.get("loc_mode")
    if isinstance(loc_raw, str) and loc_raw.strip():
        merged["loc_mode"] = loc_raw.strip().lower()

    merged["x"] = _coerce_int(raw.get("x"), defaults["x"], -65_535, 65_535)
    merged["y"] = _coerce_int(raw.get("y"), defaults["y"], -65_535, 65_535)
    return merged


def _normalize_simple_spammer(raw: Any) -> dict[str, Any]:
    defaults = _default_simple_spammer()
    if not isinstance(raw, dict):
        return defaults

    merged = dict(defaults)
    key_raw = raw.get("key")
    if isinstance(key_raw, str) and key_raw.strip() and "\x00" not in key_raw:
        merged["key"] = key_raw.strip()

    merged["interval_ms"] = _coerce_int(raw.get("interval_ms"), defaults["interval_ms"], 1, 3_600_000)
    merged["jitter_enabled"] = _coerce_bool(raw.get("jitter_enabled"), defaults["jitter_enabled"])
    return merged


def _normalize_recorder_settings(raw: Any) -> dict[str, Any]:
    defaults = _default_recorder_settings()
    if not isinstance(raw, dict):
        return defaults

    return {
        "record_delays": _coerce_bool(raw.get("record_delays"), defaults["record_delays"]),
        "merge_key_taps": _coerce_bool(raw.get("merge_key_taps"), defaults["merge_key_taps"]),
        "capture_click_coordinates": _coerce_bool(
            raw.get("capture_click_coordinates"),
            defaults["capture_click_coordinates"],
        ),
    }


@dataclass(slots=True)
class AppConfig:
    """Persistent application configuration for KeyForge."""

    schema_version: int = 1
    ui_mode: str = "simple"
    simple_task: str = "clicker"
    window_geometry: str | None = None
    start_stop_hotkey: str = "F6"
    record_hotkey: str = "F7"
    panic_hotkey: str = "F12"
    dry_run: bool = False
    check_updates_on_startup: bool = True
    auto_save_active_profile: bool = True
    start_countdown_sec: int = 0
    last_profile_path: str | None = None
    simple_clicker: dict[str, Any] = field(default_factory=_default_simple_clicker)
    simple_spammer: dict[str, Any] = field(default_factory=_default_simple_spammer)
    recorder_settings: dict[str, Any] = field(default_factory=_default_recorder_settings)

    def __post_init__(self) -> None:
        self.schema_version = _coerce_int(self.schema_version, 1, 1, 10_000)
        self.ui_mode = _normalize_ui_mode(self.ui_mode)
        self.simple_task = _normalize_simple_task(self.simple_task)
        self.window_geometry = _normalize_optional_str(self.window_geometry)
        self.start_stop_hotkey = _normalize_hotkey(self.start_stop_hotkey, "F6")
        self.record_hotkey = _normalize_hotkey(self.record_hotkey, "F7")
        self.panic_hotkey = _normalize_hotkey(self.panic_hotkey, "F12")
        self.dry_run = _coerce_bool(self.dry_run, False)
        self.check_updates_on_startup = _coerce_bool(self.check_updates_on_startup, True)
        self.auto_save_active_profile = _coerce_bool(self.auto_save_active_profile, True)
        self.start_countdown_sec = _coerce_int(self.start_countdown_sec, 0, 0, 10)
        self.last_profile_path = _normalize_optional_str(self.last_profile_path)
        self.simple_clicker = _normalize_simple_clicker(self.simple_clicker)
        self.simple_spammer = _normalize_simple_spammer(self.simple_spammer)
        self.recorder_settings = _normalize_recorder_settings(self.recorder_settings)

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration to a JSON-compatible dictionary."""
        return {
            "schema_version": self.schema_version,
            "ui_mode": self.ui_mode,
            "simple_task": self.simple_task,
            "window_geometry": self.window_geometry,
            "start_stop_hotkey": self.start_stop_hotkey,
            "record_hotkey": self.record_hotkey,
            "panic_hotkey": self.panic_hotkey,
            "dry_run": self.dry_run,
            "check_updates_on_startup": self.check_updates_on_startup,
            "auto_save_active_profile": self.auto_save_active_profile,
            "start_countdown_sec": self.start_countdown_sec,
            "last_profile_path": self.last_profile_path,
            "simple_clicker": dict(self.simple_clicker),
            "simple_spammer": dict(self.simple_spammer),
            "recorder_settings": dict(self.recorder_settings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        """Construct a normalized AppConfig from an untrusted dictionary."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            schema_version=data.get("schema_version", 1),
            ui_mode=data.get("ui_mode", "simple"),
            simple_task=data.get("simple_task", "clicker"),
            window_geometry=data.get("window_geometry"),
            start_stop_hotkey=data.get("start_stop_hotkey", "F6"),
            record_hotkey=data.get("record_hotkey", "F7"),
            panic_hotkey=data.get("panic_hotkey", "F12"),
            dry_run=data.get("dry_run", False),
            check_updates_on_startup=data.get("check_updates_on_startup", True),
            auto_save_active_profile=data.get("auto_save_active_profile", True),
            start_countdown_sec=data.get("start_countdown_sec", 0),
            last_profile_path=data.get("last_profile_path"),
            simple_clicker=data.get("simple_clicker", _default_simple_clicker()),
            simple_spammer=data.get("simple_spammer", _default_simple_spammer()),
            recorder_settings=data.get("recorder_settings", _default_recorder_settings()),
        )


def get_config_dir(custom_dir: Path | None = None) -> Path:
    """Resolve the platform-appropriate KeyForge configuration directory.

    Precedence:
    1. Explicit ``custom_dir`` argument
    2. ``KEYFORGE_CONFIG_DIR`` environment variable
    3. Platform user config directory:
       - Windows: ``%APPDATA%/KeyForge``
       - macOS: ``~/Library/Application Support/KeyForge``
       - Linux/Other: ``$XDG_CONFIG_HOME/KeyForge`` or ``~/.config/KeyForge``
    """
    if custom_dir is not None:
        return Path(custom_dir).expanduser()

    env_dir = os.environ.get("KEYFORGE_CONFIG_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "").strip()
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / "KeyForge"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "KeyForge"

    xdg_config = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_config).expanduser() if xdg_config else (Path.home() / ".config")
    return base / "KeyForge"


def _is_unisolated_pytest_run(custom_dir: Path | None) -> bool:
    """Return True when running under pytest without an explicit isolated config directory."""
    if custom_dir is not None:
        return False
    if os.environ.get("KEYFORGE_CONFIG_DIR", "").strip():
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _reject_non_standard_json_constant(constant: str) -> Any:
    raise ValueError(f"Non-standard JSON constant {constant!r} is not allowed.")


def _parse_json_dict(raw_bytes: bytes) -> dict[str, Any]:
    if len(raw_bytes) > MAX_CONFIG_FILE_BYTES:
        raise ValueError("Configuration file exceeds maximum permitted byte size.")
    text = raw_bytes.decode("utf-8")
    parsed = json.loads(text, parse_constant=_reject_non_standard_json_constant)
    if not isinstance(parsed, dict):
        raise ValueError("Configuration JSON root must be an object.")
    return parsed


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Atomically write bytes to ``target`` using a same-directory temp file and ``os.replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{target.name}_",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def load_app_config(custom_dir: Path | None = None) -> AppConfig:
    """Load AppConfig from disk, recovering automatically from ``.bak`` if primary is malformed."""
    if _is_unisolated_pytest_run(custom_dir):
        return AppConfig()

    config_dir = get_config_dir(custom_dir)
    primary_path = config_dir / CONFIG_FILENAME
    backup_candidates = (
        config_dir / CONFIG_BACKUP_FILENAME,
        config_dir / "config.bak",
    )

    for candidate in (primary_path, *backup_candidates):
        if not candidate.is_file():
            continue
        try:
            if candidate.stat().st_size > MAX_CONFIG_FILE_BYTES:
                continue
            data = _parse_json_dict(candidate.read_bytes())
            return AppConfig.from_dict(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            continue

    return AppConfig()


def save_app_config(config: AppConfig, custom_dir: Path | None = None) -> Path:
    """Atomically persist AppConfig via ``.tmp`` + ``os.replace``, preserving a ``.bak`` backup."""
    config_dir = get_config_dir(custom_dir)
    target = config_dir / CONFIG_FILENAME

    if _is_unisolated_pytest_run(custom_dir):
        return target

    config_dir.mkdir(parents=True, exist_ok=True)
    backup_path = config_dir / CONFIG_BACKUP_FILENAME

    # Only rotate the current config.json into .bak if it is valid JSON so a corrupt file never overwrites a good .bak.
    if target.is_file():
        try:
            existing_bytes = target.read_bytes()
            _parse_json_dict(existing_bytes)
            _atomic_write_bytes(backup_path, existing_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            pass

    serialized = (json.dumps(config.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    _atomic_write_bytes(target, serialized)
    return target


def save_active_profile_backup(profile: MacroProfile, custom_dir: Path | None = None) -> Path:
    """Atomically save a SHA-256 signed backup of the active MacroProfile."""
    config_dir = get_config_dir(custom_dir)
    target = config_dir / ACTIVE_PROFILE_FILENAME

    if _is_unisolated_pytest_run(custom_dir):
        return target

    config_dir.mkdir(parents=True, exist_ok=True)
    backup_path = config_dir / ACTIVE_PROFILE_BACKUP_FILENAME

    if target.is_file():
        try:
            existing_bytes = target.read_bytes()
            if len(existing_bytes) <= MAX_PROFILE_FILE_BYTES:
                parse_and_verify_envelope(existing_bytes, require_signature=True)
                _atomic_write_bytes(backup_path, existing_bytes)
        except (OSError, SecurityValidationError, ValueError, TypeError):
            pass

    return save_profile_secure(profile, target)


def load_active_profile_backup(custom_dir: Path | None = None) -> MacroProfile | None:
    """Load the signed active MacroProfile backup, falling back to ``.bak`` on corruption."""
    if _is_unisolated_pytest_run(custom_dir):
        return None

    config_dir = get_config_dir(custom_dir)
    primary_path = config_dir / ACTIVE_PROFILE_FILENAME
    backup_path = config_dir / ACTIVE_PROFILE_BACKUP_FILENAME

    for candidate in (primary_path, backup_path):
        if not candidate.is_file():
            continue
        try:
            if candidate.stat().st_size > MAX_PROFILE_FILE_BYTES:
                continue
            profile, _verified = parse_and_verify_envelope(
                candidate.read_bytes(),
                require_signature=True,
            )
            return profile
        except (OSError, SecurityValidationError, ValueError, TypeError):
            continue

    return None


def _slugify_preset_name(name: str) -> str:
    cleaned = _PRESET_FILENAME_SANITIZER.sub("", name).strip().replace(" ", "_")[:60]
    return cleaned or "custom_preset"


def get_presets_dir(custom_dir: Path | None = None) -> Path:
    """Return the directory storing custom saved macro presets."""
    return get_config_dir(custom_dir) / PRESETS_DIRNAME


def save_custom_preset(profile: MacroProfile, custom_dir: Path | None = None) -> Path:
    """Validate and save a custom MacroProfile preset into the presets library folder."""
    profile.validate()
    presets_dir = get_presets_dir(custom_dir)
    slug = _slugify_preset_name(profile.name)
    target = presets_dir / f"{slug}.kfmacro.json"
    if _is_unisolated_pytest_run(custom_dir):
        return target
    presets_dir.mkdir(parents=True, exist_ok=True)
    return save_profile_secure(profile, target)


def load_custom_presets(custom_dir: Path | None = None) -> dict[str, MacroProfile]:
    """Load all valid custom presets from the presets library folder."""
    if _is_unisolated_pytest_run(custom_dir):
        return {}
    presets_dir = get_presets_dir(custom_dir)
    if not presets_dir.is_dir():
        return {}

    loaded: dict[str, MacroProfile] = {}
    for path in sorted(presets_dir.glob("*.kfmacro.json")):
        with contextlib.suppress(Exception):
            profile, _sig_ok = load_profile_secure(path, require_signature=False)
            loaded[profile.name] = profile
    return loaded


def delete_custom_preset(preset_name: str, custom_dir: Path | None = None) -> bool:
    """Delete a custom saved preset by name. Returns True if removed."""
    if _is_unisolated_pytest_run(custom_dir):
        return False
    if not isinstance(preset_name, str) or not preset_name.strip():
        return False
    presets_dir = get_presets_dir(custom_dir)
    if not presets_dir.is_dir():
        return False

    target_slug = _slugify_preset_name(preset_name)
    for path in sorted(presets_dir.glob("*.kfmacro.json")):
        matched = False
        try:
            profile, _sig_ok = load_profile_secure(path, require_signature=False)
            matched = profile.name == preset_name
        except Exception:
            matched = path.name == f"{target_slug}.kfmacro.json"
        if matched:
            path.unlink(missing_ok=True)
            return True

    fallback = presets_dir / f"{target_slug}.kfmacro.json"
    if fallback.is_file():
        fallback.unlink(missing_ok=True)
        return True
    return False
