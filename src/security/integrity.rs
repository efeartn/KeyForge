//! Cryptographic SHA-256 profile integrity signing, verification, and atomic file I/O.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use uuid::Uuid;

use crate::models::profile::MacroProfile;
use crate::security::validator::{
    validate_profile_complexity, validate_profile_payload_value, validate_safe_file_path,
    SecurityValidationError, ALLOWED_ENVELOPE_KEYS, MAX_PROFILE_FILE_BYTES,
};

pub const ENVELOPE_FORMAT_ID: &str = "kfmacro-v1";

/// Serialize a profile payload JSON value into deterministic compact UTF-8 JSON bytes.
/// Note: `serde_json` uses `BTreeMap` by default (`preserve_order` is disabled), which sorts keys
/// identically to Python `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
pub fn canonicalize_payload(payload: &Value) -> Result<Vec<u8>, SecurityValidationError> {
    serde_json::to_vec(payload)
        .map_err(|e| SecurityValidationError::new(format!("Failed to canonicalize payload: {e}")))
}

/// Compute the lowercase hex-encoded SHA-256 digest of a canonicalized profile payload.
pub fn compute_payload_sha256(payload: &Value) -> Result<String, SecurityValidationError> {
    let canonical_bytes = canonicalize_payload(payload)?;
    let mut hasher = Sha256::new();
    hasher.update(&canonical_bytes);
    Ok(hex::encode(hasher.finalize()))
}

/// Validate a [`MacroProfile`] and wrap it in a signed integrity envelope.
pub fn build_signed_envelope(profile: &mut MacroProfile) -> Result<Value, SecurityValidationError> {
    validate_profile_complexity(profile)?;
    let payload = profile.to_payload_value();
    validate_profile_payload_value(&payload)?;
    let digest = compute_payload_sha256(&payload)?;

    let mut env = Map::new();
    env.insert(
        "keyforge_format".to_string(),
        Value::String(ENVELOPE_FORMAT_ID.to_string()),
    );
    env.insert("integrity_sha256".to_string(), Value::String(digest));
    env.insert("profile".to_string(), payload);
    Ok(Value::Object(env))
}

/// Parse JSON bytes, verify schema & SHA-256 integrity, and return `(MacroProfile, signature_verified)`.
pub fn parse_and_verify_envelope(
    raw_bytes: &[u8],
    require_signature: bool,
) -> Result<(MacroProfile, bool), SecurityValidationError> {
    if raw_bytes.len() > MAX_PROFILE_FILE_BYTES {
        return Err(SecurityValidationError::new(format!(
            "Input size ({}) exceeds limit ({MAX_PROFILE_FILE_BYTES} bytes).",
            raw_bytes.len()
        )));
    }
    let text = std::str::from_utf8(raw_bytes)
        .map_err(|_| SecurityValidationError::new("Profile file is not valid UTF-8 text."))?;

    let parsed: Value = serde_json::from_str(text)
        .map_err(|e| SecurityValidationError::new(format!("Malformed JSON in profile: {e}")))?;

    let obj = parsed
        .as_object()
        .ok_or_else(|| SecurityValidationError::new("Top-level JSON value must be an object."))?;

    // Case 1: Signed KeyForge envelope
    if obj.contains_key("keyforge_format") || obj.contains_key("profile") {
        let mut unknown: Vec<&str> = obj
            .keys()
            .map(String::as_str)
            .filter(|k| !ALLOWED_ENVELOPE_KEYS.contains(k))
            .collect();
        if !unknown.is_empty() {
            unknown.sort_unstable();
            return Err(SecurityValidationError::new(format!(
                "Unknown fields in profile envelope: {unknown:?}"
            )));
        }
        let fmt = obj.get("keyforge_format").and_then(Value::as_str);
        if fmt != Some(ENVELOPE_FORMAT_ID) {
            return Err(SecurityValidationError::new(format!(
                "Unsupported keyforge_format: {:?}",
                obj.get("keyforge_format")
            )));
        }

        let payload = obj
            .get("profile")
            .filter(|v| v.is_object())
            .ok_or_else(|| {
                SecurityValidationError::new("Missing or invalid 'profile' object in envelope.")
            })?;

        validate_profile_payload_value(payload)?;

        let expected_hash = obj
            .get("integrity_sha256")
            .and_then(Value::as_str)
            .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
            .ok_or_else(|| {
                SecurityValidationError::new("Missing or malformed 'integrity_sha256' digest.")
            })?;

        let actual_hash = compute_payload_sha256(payload)?;
        let eq: bool = expected_hash
            .to_ascii_lowercase()
            .as_bytes()
            .ct_eq(actual_hash.as_bytes())
            .into();
        if !eq {
            return Err(SecurityValidationError::new(
                "Profile SHA-256 integrity check failed! The file may have been tampered with or corrupted.",
            ));
        }

        let mut profile = MacroProfile::from_payload_value(payload).map_err(|e| {
            SecurityValidationError::new(format!("Invalid profile field value: {e}"))
        })?;
        validate_profile_complexity(&mut profile)?;
        return Ok((profile, true));
    }

    // Case 2: Raw unsigned profile dictionary
    if require_signature {
        return Err(SecurityValidationError::new(
            "Profile is missing cryptographic SHA-256 envelope ('keyforge_format' / 'integrity_sha256').",
        ));
    }

    validate_profile_payload_value(&parsed)?;
    let mut profile = MacroProfile::from_payload_value(&parsed)
        .map_err(|e| SecurityValidationError::new(format!("Invalid profile field value: {e}")))?;
    validate_profile_complexity(&mut profile)?;
    Ok((profile, false))
}

/// Atomically write bytes to `target` via a same-directory temp file, `sync_all()`, and `rename`.
pub fn atomic_write_bytes(target: &Path, payload: &[u8]) -> Result<(), SecurityValidationError> {
    let parent = target.parent().ok_or_else(|| {
        SecurityValidationError::new(format!("Invalid destination path: {}", target.display()))
    })?;
    std::fs::create_dir_all(parent).map_err(|e| {
        SecurityValidationError::new(format!("Failed to create parent directory: {e}"))
    })?;

    let tmp_name = format!(".keyforge_{}.tmp", Uuid::new_v4().simple());
    let tmp_path = parent.join(tmp_name);

    let write_res = (|| -> std::io::Result<()> {
        let mut handle = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp_path)?;
        handle.write_all(payload)?;
        handle.flush()?;
        handle.sync_all()?;
        drop(handle);
        std::fs::rename(&tmp_path, target)?;
        Ok(())
    })();

    if let Err(err) = write_res {
        let _ = std::fs::remove_file(&tmp_path);
        return Err(SecurityValidationError::new(format!(
            "Failed atomic write to {}: {err}",
            target.display()
        )));
    }
    Ok(())
}

/// Atomically write a signed [`MacroProfile`] to disk after path validation.
pub fn save_profile_secure(
    profile: &mut MacroProfile,
    path: &Path,
) -> Result<PathBuf, SecurityValidationError> {
    let target = validate_safe_file_path(path, false)?;
    let envelope = build_signed_envelope(profile)?;
    let mut serialized = serde_json::to_string_pretty(&envelope)
        .map_err(|e| SecurityValidationError::new(format!("Failed to serialize profile: {e}")))?;
    serialized.push('\n');
    atomic_write_bytes(&target, serialized.as_bytes())?;
    Ok(target)
}

/// Load and validate a [`MacroProfile`] from disk with size, path, and SHA-256 checks.
pub fn load_profile_secure(
    path: &Path,
    require_signature: bool,
) -> Result<(MacroProfile, bool), SecurityValidationError> {
    let target = validate_safe_file_path(path, true)?;
    let raw_bytes = std::fs::read(&target).map_err(|e| {
        SecurityValidationError::new(format!("Failed to read profile {}: {e}", target.display()))
    })?;
    parse_and_verify_envelope(&raw_bytes, require_signature)
}
