"""Strict schema validation, DoS bomb mitigation, and file path sanitization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from keyforge.models.action import ActionType, MacroAction
from keyforge.models.profile import MacroProfile

MAX_PROFILE_FILE_BYTES = 1_048_576  # 1 MiB max file size
MAX_TOTAL_STEPS = 5_000  # Max static MacroAction nodes
MAX_REPEAT_NESTING_DEPTH = 5  # Max nested REPEAT_BLOCK depth
MAX_EXPANDED_OPS_PER_CYCLE = 100_000  # Prevents "Billion Laughs" nested repeat bombs
MIN_SAFE_CYCLE_DELAY_MS = 5  # Minimum OS input queue safety yield per cycle

ALLOWED_FILE_SUFFIXES = {".json", ".kfmacro"}

WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

ALLOWED_ENVELOPE_KEYS = {"keyforge_format", "integrity_sha256", "profile"}

ALLOWED_PROFILE_KEYS = {
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
}

ALLOWED_ACTION_KEYS = {
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
}


class SecurityValidationError(ValueError):
    """Raised when a profile or file violates KeyForge security constraints."""


def validate_safe_file_path(path: str | Path, must_exist: bool = False) -> Path:
    """Validate and canonicalize a profile file path against traversal, ADS, and unsafe extensions."""
    raw_str = str(path)
    if "\x00" in raw_str or not raw_str.strip():
        raise SecurityValidationError("File path contains null bytes or is empty.")

    raw_name = Path(raw_str).name
    if ":" in raw_name:
        raise SecurityValidationError("File name cannot contain NTFS Alternate Data Stream colons (':').")

    base_stem = raw_name.split(".")[0].upper()
    if base_stem in WINDOWS_RESERVED_STEMS:
        raise SecurityValidationError(f"File name uses reserved Windows device name: {base_stem!r}.")

    candidate = Path(raw_str).expanduser().resolve()

    if candidate.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
        raise SecurityValidationError(f"Unsupported file extension {candidate.suffix!r}. Allowed: .json, .kfmacro")

    if must_exist:
        if not candidate.exists():
            raise SecurityValidationError(f"Profile file does not exist: {candidate}")
        if not candidate.is_file():
            raise SecurityValidationError(f"Path is not a regular file: {candidate}")
        size = candidate.stat().st_size
        if size > MAX_PROFILE_FILE_BYTES:
            raise SecurityValidationError(
                f"Profile file size ({size} bytes) exceeds security limit ({MAX_PROFILE_FILE_BYTES} bytes)."
            )

    return candidate


def _validate_action_dict_schema(data: dict[str, Any], depth: int = 0) -> tuple[int, int]:
    """Recursively validate raw action dict keys and return (node_count, expanded_ops)."""
    if not isinstance(data, dict):
        raise SecurityValidationError("Action entry must be a JSON object.")

    unknown_keys = set(data.keys()) - ALLOWED_ACTION_KEYS
    if unknown_keys:
        raise SecurityValidationError(f"Unexpected fields in action object: {sorted(unknown_keys)}")

    if depth > MAX_REPEAT_NESTING_DEPTH:
        raise SecurityValidationError(
            f"Repeat block nesting depth ({depth}) exceeds limit ({MAX_REPEAT_NESTING_DEPTH})."
        )

    node_count = 1
    action_type = data.get("action_type")
    if action_type == ActionType.REPEAT_BLOCK.value:
        repeat_count = data.get("repeat_count", 2)
        if (
            not isinstance(repeat_count, int)
            or isinstance(repeat_count, bool)
            or repeat_count < 1
            or repeat_count > 10_000
        ):
            raise SecurityValidationError("Invalid repeat_count in repeat_block.")
        sub_actions = data.get("sub_actions", [])
        if not isinstance(sub_actions, list):
            raise SecurityValidationError("sub_actions must be a list.")
        sub_nodes_total = 0
        sub_ops_total = 0
        for sub in sub_actions:
            s_nodes, s_ops = _validate_action_dict_schema(sub, depth + 1)
            sub_nodes_total += s_nodes
            sub_ops_total += s_ops
        node_count += sub_nodes_total
        expanded_ops = repeat_count * max(1, sub_ops_total)
    else:
        expanded_ops = 1

    if node_count > MAX_TOTAL_STEPS:
        raise SecurityValidationError(f"Macro step count ({node_count}) exceeds limit ({MAX_TOTAL_STEPS}).")
    if expanded_ops > MAX_EXPANDED_OPS_PER_CYCLE:
        raise SecurityValidationError(
            f"Expanded operations per cycle ({expanded_ops}) exceeds DoS limit ({MAX_EXPANDED_OPS_PER_CYCLE})."
        )

    return node_count, expanded_ops


def validate_profile_payload_dict(payload: dict[str, Any]) -> None:
    """Validate raw profile dictionary against allowlist schema and complexity caps."""
    if not isinstance(payload, dict):
        raise SecurityValidationError("Profile payload must be a JSON object.")

    unknown = set(payload.keys()) - ALLOWED_PROFILE_KEYS
    if unknown:
        raise SecurityValidationError(f"Unexpected fields in profile payload: {sorted(unknown)}")

    actions = payload.get("actions", [])
    if not isinstance(actions, list):
        raise SecurityValidationError("'actions' field must be a list.")

    total_nodes = 0
    total_expanded_ops = 0
    for item in actions:
        nodes, ops = _validate_action_dict_schema(item, depth=0)
        total_nodes += nodes
        total_expanded_ops += ops
        if total_nodes > MAX_TOTAL_STEPS:
            raise SecurityValidationError(f"Total step count ({total_nodes}) exceeds limit ({MAX_TOTAL_STEPS}).")
        if total_expanded_ops > MAX_EXPANDED_OPS_PER_CYCLE:
            raise SecurityValidationError(
                f"Total expanded operations per cycle ({total_expanded_ops}) "
                f"exceeds DoS limit ({MAX_EXPANDED_OPS_PER_CYCLE})."
            )


def validate_profile_complexity(profile: MacroProfile) -> tuple[int, int]:
    """Validate an in-memory MacroProfile's step count, nesting depth, and expanded ops."""
    profile.validate()

    def _walk(actions: list[MacroAction], depth: int) -> tuple[int, int]:
        if depth > MAX_REPEAT_NESTING_DEPTH:
            raise SecurityValidationError(
                f"Repeat nesting depth ({depth}) exceeds maximum ({MAX_REPEAT_NESTING_DEPTH})."
            )
        nodes = 0
        ops = 0
        for act in actions:
            nodes += 1
            if act.action_type == ActionType.REPEAT_BLOCK:
                c_nodes, c_ops = _walk(act.sub_actions, depth + 1)
                nodes += c_nodes
                ops += act.repeat_count * max(1, c_ops)
            else:
                ops += 1
            if nodes > MAX_TOTAL_STEPS:
                raise SecurityValidationError(f"Total step count ({nodes}) exceeds maximum ({MAX_TOTAL_STEPS}).")
            if ops > MAX_EXPANDED_OPS_PER_CYCLE:
                raise SecurityValidationError(
                    f"Expanded operations per cycle ({ops}) exceeds maximum ({MAX_EXPANDED_OPS_PER_CYCLE})."
                )
        return nodes, ops

    return _walk(profile.actions, depth=0)
