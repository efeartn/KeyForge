"""Cryptographic SHA-256 profile integrity signing, verification, and atomic file I/O."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from keyforge.models.profile import MacroProfile
from keyforge.security.validator import (
    ALLOWED_ENVELOPE_KEYS,
    MAX_PROFILE_FILE_BYTES,
    SecurityValidationError,
    validate_profile_complexity,
    validate_profile_payload_dict,
    validate_safe_file_path,
)

ENVELOPE_FORMAT_ID = "kfmacro-v1"


def canonicalize_payload(payload: dict[str, Any]) -> bytes:
    """Serialize a profile payload dictionary into deterministic UTF-8 JSON bytes."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def compute_payload_sha256(payload: dict[str, Any]) -> str:
    """Compute the hex-encoded SHA-256 digest of a canonicalized profile payload."""
    canonical_bytes = canonicalize_payload(payload)
    return hashlib.sha256(canonical_bytes).hexdigest()


def build_signed_envelope(profile: MacroProfile) -> dict[str, Any]:
    """Validate a MacroProfile and wrap it in a signed integrity envelope."""
    validate_profile_complexity(profile)
    payload = profile.to_payload_dict()
    validate_profile_payload_dict(payload)
    digest = compute_payload_sha256(payload)
    return {
        "keyforge_format": ENVELOPE_FORMAT_ID,
        "integrity_sha256": digest,
        "profile": payload,
    }


def parse_and_verify_envelope(
    raw_json: str | bytes,
    require_signature: bool = True,
) -> tuple[MacroProfile, bool]:
    """Parse JSON data, verify schema & SHA-256 integrity, and return (MacroProfile, signature_verified).

    Raises SecurityValidationError on tampering, oversize input, or schema violation.
    """
    if isinstance(raw_json, bytes):
        if len(raw_json) > MAX_PROFILE_FILE_BYTES:
            raise SecurityValidationError(
                f"Input size ({len(raw_json)} bytes) exceeds limit ({MAX_PROFILE_FILE_BYTES} bytes)."
            )
        try:
            text = raw_json.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecurityValidationError("Profile file is not valid UTF-8 text.") from exc
    elif isinstance(raw_json, str):
        if len(raw_json.encode("utf-8")) > MAX_PROFILE_FILE_BYTES:
            raise SecurityValidationError("Profile JSON string exceeds maximum byte size.")
        text = raw_json
    else:
        raise SecurityValidationError("Profile input must be str or bytes.")

    def _reject_non_standard_constants(constant: str) -> Any:
        raise SecurityValidationError(f"Non-standard JSON constant {constant!r} is not permitted.")

    try:
        parsed = json.loads(text, parse_constant=_reject_non_standard_constants)
    except json.JSONDecodeError as exc:
        raise SecurityValidationError(f"Malformed JSON in profile: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise SecurityValidationError("Top-level JSON value must be an object.")

    # Case 1: Signed KeyForge envelope
    if "keyforge_format" in parsed or "profile" in parsed:
        unknown_env = set(parsed.keys()) - ALLOWED_ENVELOPE_KEYS
        if unknown_env:
            raise SecurityValidationError(f"Unknown fields in profile envelope: {sorted(unknown_env)}")
        fmt = parsed.get("keyforge_format")
        if fmt != ENVELOPE_FORMAT_ID:
            raise SecurityValidationError(f"Unsupported keyforge_format: {fmt!r}")

        payload = parsed.get("profile")
        if not isinstance(payload, dict):
            raise SecurityValidationError("Missing or invalid 'profile' object in envelope.")

        validate_profile_payload_dict(payload)

        expected_hash = parsed.get("integrity_sha256")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise SecurityValidationError("Missing or malformed 'integrity_sha256' digest.")

        actual_hash = compute_payload_sha256(payload)
        if not hmac.compare_digest(expected_hash.lower(), actual_hash.lower()):
            raise SecurityValidationError(
                "Profile SHA-256 integrity check failed! The file may have been tampered with or corrupted."
            )

        try:
            profile = MacroProfile.from_payload_dict(payload)
        except (ValueError, TypeError) as exc:
            raise SecurityValidationError(f"Invalid profile field value: {exc}") from exc
        validate_profile_complexity(profile)
        return profile, True

    # Case 2: Raw unsigned profile dictionary
    if require_signature:
        raise SecurityValidationError(
            "Profile is missing cryptographic SHA-256 envelope ('keyforge_format' / 'integrity_sha256')."
        )

    validate_profile_payload_dict(parsed)
    try:
        profile = MacroProfile.from_payload_dict(parsed)
    except (ValueError, TypeError) as exc:
        raise SecurityValidationError(f"Invalid profile field value: {exc}") from exc
    validate_profile_complexity(profile)
    return profile, False


def save_profile_secure(profile: MacroProfile, path: str | Path) -> Path:
    """Atomically write a signed MacroProfile to disk after path validation."""
    target = validate_safe_file_path(path, must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True)

    envelope = build_signed_envelope(profile)
    serialized = json.dumps(envelope, indent=2, ensure_ascii=False) + "\n"

    fd, tmp_path_str = tempfile.mkstemp(
        prefix=".keyforge_",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    return target


def load_profile_secure(
    path: str | Path,
    require_signature: bool = False,
) -> tuple[MacroProfile, bool]:
    """Load and validate a MacroProfile from disk with size, path, and SHA-256 checks."""
    target = validate_safe_file_path(path, must_exist=True)
    raw_bytes = target.read_bytes()
    return parse_and_verify_envelope(raw_bytes, require_signature=require_signature)
