"""Security layer for KeyForge: validation, SHA-256 integrity, and static safety auditing."""

from keyforge.security.auditor import (
    AuditFinding,
    AuditReport,
    AuditSeverity,
    audit_profile,
)
from keyforge.security.integrity import (
    ENVELOPE_FORMAT_ID,
    build_signed_envelope,
    canonicalize_payload,
    compute_payload_sha256,
    load_profile_secure,
    parse_and_verify_envelope,
    save_profile_secure,
)
from keyforge.security.validator import (
    MAX_EXPANDED_OPS_PER_CYCLE,
    MAX_PROFILE_FILE_BYTES,
    MAX_REPEAT_NESTING_DEPTH,
    MAX_TOTAL_STEPS,
    MIN_SAFE_CYCLE_DELAY_MS,
    SecurityValidationError,
    validate_profile_complexity,
    validate_profile_payload_dict,
    validate_safe_file_path,
)

__all__ = [
    "AuditFinding",
    "AuditReport",
    "AuditSeverity",
    "ENVELOPE_FORMAT_ID",
    "MAX_EXPANDED_OPS_PER_CYCLE",
    "MAX_PROFILE_FILE_BYTES",
    "MAX_REPEAT_NESTING_DEPTH",
    "MAX_TOTAL_STEPS",
    "MIN_SAFE_CYCLE_DELAY_MS",
    "SecurityValidationError",
    "audit_profile",
    "build_signed_envelope",
    "canonicalize_payload",
    "compute_payload_sha256",
    "load_profile_secure",
    "parse_and_verify_envelope",
    "save_profile_secure",
    "validate_profile_complexity",
    "validate_profile_payload_dict",
    "validate_safe_file_path",
]
