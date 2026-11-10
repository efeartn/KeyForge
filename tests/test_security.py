"""Cybersecurity unit tests: schema allowlisting, DoS bomb prevention, SHA-256 integrity, and static auditing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keyforge.models.action import (
    ClickType,
    MouseButton,
    make_key_down,
    make_key_press,
    make_mouse_click,
    make_repeat_block,
    make_wait,
)
from keyforge.models.profile import MacroProfile, RepeatMode, create_default_profile
from keyforge.security.auditor import AuditSeverity, audit_profile
from keyforge.security.integrity import (
    build_signed_envelope,
    load_profile_secure,
    parse_and_verify_envelope,
    save_profile_secure,
)
from keyforge.security.validator import (
    SecurityValidationError,
    validate_profile_complexity,
    validate_profile_payload_dict,
    validate_safe_file_path,
)


def test_sha256_signing_and_tamper_detection(tmp_path: Path) -> None:
    profile = create_default_profile()
    out_file = tmp_path / "test_macro.kfmacro.json"

    saved_path = save_profile_secure(profile, out_file)
    assert saved_path.exists()

    loaded, verified = load_profile_secure(saved_path, require_signature=True)
    assert verified is True
    assert loaded.name == profile.name
    assert len(loaded.actions) == len(profile.actions)

    # Tamper with a single value in the saved JSON without updating integrity_sha256
    envelope = json.loads(saved_path.read_text(encoding="utf-8"))
    envelope["profile"]["actions"][0]["key"] = "F"
    tampered_bytes = json.dumps(envelope).encode("utf-8")

    with pytest.raises(SecurityValidationError, match="SHA-256 integrity check failed"):
        parse_and_verify_envelope(tampered_bytes, require_signature=True)


def test_unknown_field_injection_rejected() -> None:
    profile = create_default_profile()
    payload = profile.to_payload_dict()
    payload["__proto__"] = {"polluted": True}

    with pytest.raises(SecurityValidationError, match="Unexpected fields in profile"):
        validate_profile_payload_dict(payload)

    payload2 = profile.to_payload_dict()
    payload2["actions"][0]["shell_cmd"] = "calc.exe"
    with pytest.raises(SecurityValidationError, match="Unexpected fields in action"):
        validate_profile_payload_dict(payload2)


def test_billion_laughs_repeat_dos_bomb_rejected() -> None:
    # 100 x 100 x 100 = 1,000,000 expanded ops (> 100,000 security cap)
    inner = make_repeat_block(100, [make_key_press("E")])
    middle = make_repeat_block(100, [inner])
    outer = make_repeat_block(100, [middle])
    bomb_profile = MacroProfile(name="DoS Bomb", actions=[outer])

    with pytest.raises(SecurityValidationError, match="Expanded operations per cycle"):
        validate_profile_complexity(bomb_profile)


def test_excessive_nesting_depth_rejected() -> None:
    block = make_key_press("E")
    for _ in range(7):
        block = make_repeat_block(2, [block])
    deep_profile = MacroProfile(name="Too Deep", actions=[block])

    with pytest.raises(SecurityValidationError, match="nesting depth"):
        validate_profile_complexity(deep_profile)


def test_file_path_traversal_and_extension_guard(tmp_path: Path) -> None:
    with pytest.raises(SecurityValidationError, match="null bytes"):
        validate_safe_file_path("bad\x00file.json")

    with pytest.raises(SecurityValidationError, match="Unsupported file extension"):
        validate_safe_file_path(tmp_path / "malware.exe")

    with pytest.raises(SecurityValidationError, match="Unsupported file extension"):
        validate_safe_file_path(tmp_path / "script.ps1")


def test_security_auditor_findings() -> None:
    # 1. Clean default profile passes with 0 findings
    clean_prof = create_default_profile()
    clean_report = audit_profile(clean_prof)
    assert clean_report.is_clean is True
    assert clean_report.badge_summary() == "SECURITY AUDIT: PASSED (0 Issues)"

    # 2. Dangerous Win+R + 0ms infinite loop + unbalanced KeyDown
    risky_prof = MacroProfile(
        name="Suspicious Macro",
        repeat_mode=RepeatMode.INFINITE,
        inter_cycle_delay_ms=0,
        actions=[
            make_key_press("Win+R", hold_ms=0),
            make_key_down("Shift"),
            make_mouse_click(MouseButton.LEFT, ClickType.DOWN, hold_ms=0),
        ],
    )
    report = audit_profile(risky_prof)
    assert report.has_critical is True
    codes = {f.code for f in report.findings}
    assert "KF-SEC-001" in codes  # Zero-delay loop
    assert "KF-SEC-002" in codes  # Unbalanced key/button hold
    assert "KF-SEC-003" in codes  # Dangerous Win+R combo

    # 3. Extreme click rate (> 100 CPS)
    fast_clicker = MacroProfile(
        name="Too Fast",
        repeat_mode=RepeatMode.INFINITE,
        inter_cycle_delay_ms=0,
        actions=[
            make_mouse_click(MouseButton.LEFT, ClickType.SINGLE, hold_ms=2),
            make_wait(2),
        ],
    )
    fast_report = audit_profile(fast_clicker)
    assert any(f.code == "KF-SEC-004" and f.severity == AuditSeverity.WARNING for f in fast_report.findings)


def test_envelope_signature_requirement() -> None:
    profile = create_default_profile()
    raw_unsigned = json.dumps(profile.to_payload_dict())
    with pytest.raises(SecurityValidationError, match="missing cryptographic SHA-256"):
        parse_and_verify_envelope(raw_unsigned, require_signature=True)

    loaded, verified = parse_and_verify_envelope(raw_unsigned, require_signature=False)
    assert verified is False
    assert loaded.name == profile.name

    signed = json.dumps(build_signed_envelope(profile))
    loaded2, verified2 = parse_and_verify_envelope(signed, require_signature=True)
    assert verified2 is True
    assert loaded2.name == profile.name


def test_nan_infinity_and_boolean_type_coercion_rejected(tmp_path: Path) -> None:
    profile = create_default_profile()
    payload = profile.to_payload_dict()

    # 1. NaN / Infinity in JSON string
    nan_json = json.dumps(payload).replace('"repeat_duration_s": 30.0', '"repeat_duration_s": NaN')
    with pytest.raises(SecurityValidationError, match="Non-standard JSON constant"):
        parse_and_verify_envelope(nan_json, require_signature=False)

    with pytest.raises(ValueError, match="finite number"):
        MacroProfile(repeat_duration_s=float("nan"))

    # 2. Boolean passed where int is required in JSON
    bool_payload = profile.to_payload_dict()
    bool_payload["repeat_count"] = True
    with pytest.raises(SecurityValidationError, match="must be an integer"):
        parse_and_verify_envelope(json.dumps(bool_payload), require_signature=False)

    # 3. NTFS Alternate Data Stream & Windows reserved device names
    with pytest.raises(SecurityValidationError, match="Alternate Data Stream"):
        validate_safe_file_path(tmp_path / "benign.txt:evil.json")

    with pytest.raises(SecurityValidationError, match="reserved Windows device name"):
        validate_safe_file_path(tmp_path / "CON.json")


def test_auditor_detects_split_modifier_and_right_hand_shortcuts() -> None:
    from keyforge.models.action import make_key_up

    # Split KeyDown RWin -> Press R -> KeyUp RWin
    split_prof = MacroProfile(
        name="Split Shell Shortcut",
        actions=[
            make_key_down("RWin"),
            make_key_press("R"),
            make_key_up("RWin"),
        ],
    )
    report = audit_profile(split_prof)
    assert report.has_critical is True
    assert any(f.code == "KF-SEC-003" and "Win+R" in f.title for f in report.findings)
