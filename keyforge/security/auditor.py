"""Static security and operational safety auditor for macro profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from keyforge.models.action import ActionType, ClickType, MacroAction
from keyforge.models.keymap import parse_key_combo
from keyforge.models.profile import MacroProfile, RepeatMode
from keyforge.security.validator import MIN_SAFE_CYCLE_DELAY_MS

# Key combinations commonly abused in BadUSB / social-engineered macro attacks
DANGEROUS_KEY_COMBOS: dict[str, str] = {
    "Win+R": "Opens the Windows Run dialog (often abused to execute hidden shell commands).",
    "Win+X": "Opens the Windows Power User menu (can launch Administrator Terminal).",
    "Alt+F4": "Force-closes the active foreground application window.",
    "Ctrl+Shift+Esc": "Opens Windows Task Manager.",
    "Ctrl+Alt+Delete": "Invokes the Windows Security screen.",
}

_MODIFIER_FAMILIES: dict[str, str] = {
    "RWin": "Win",
    "RCtrl": "Ctrl",
    "RShift": "Shift",
    "RAlt": "Alt",
}

_DANGEROUS_KEY_SETS: list[tuple[frozenset[str], str, str]] = [
    (frozenset(combo.split("+")), combo, reason) for combo, reason in DANGEROUS_KEY_COMBOS.items()
]


def _normalize_key_set(combo_str: str) -> frozenset[str]:
    infos = parse_key_combo(combo_str)
    return frozenset(_MODIFIER_FAMILIES.get(k.canonical_name, k.canonical_name) for k in infos)


def _count_expanded_clicks(actions: list[MacroAction]) -> int:
    total = 0
    for act in actions:
        if not act.enabled:
            continue
        if act.action_type == ActionType.MOUSE_CLICK:
            total += 2 if act.click_type == ClickType.DOUBLE else 1
        elif act.action_type == ActionType.REPEAT_BLOCK:
            total += act.repeat_count * _count_expanded_clicks(act.sub_actions)
    return total


class AuditSeverity(StrEnum):
    """Severity level of a security or operational safety finding."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """A single finding discovered during static analysis of a MacroProfile."""

    code: str
    severity: AuditSeverity
    title: str
    description: str
    remediation: str


@dataclass(slots=True)
class AuditReport:
    """Summary of all security and safety findings for a MacroProfile."""

    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.findings) == 0

    @property
    def has_critical(self) -> bool:
        return any(f.severity == AuditSeverity.CRITICAL for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity in (AuditSeverity.WARNING, AuditSeverity.CRITICAL) for f in self.findings)

    def badge_summary(self) -> str:
        """Short one-line summary for UI security status bar."""
        crit = sum(1 for f in self.findings if f.severity == AuditSeverity.CRITICAL)
        warn = sum(1 for f in self.findings if f.severity == AuditSeverity.WARNING)
        if crit > 0:
            return f"SECURITY ALERT: {crit} Critical, {warn} Warning(s)"
        if warn > 0:
            return f"SAFETY NOTICE: {warn} Warning(s)"
        return "SECURITY AUDIT: PASSED (0 Issues)"


def _flatten_enabled_actions(actions: list[MacroAction]) -> list[MacroAction]:
    """Flatten enabled actions (expanding 1 iteration of repeat blocks for static inspection)."""
    flat: list[MacroAction] = []
    for act in actions:
        if not act.enabled:
            continue
        flat.append(act)
        if act.action_type == ActionType.REPEAT_BLOCK:
            flat.extend(_flatten_enabled_actions(act.sub_actions))
    return flat


def audit_profile(profile: MacroProfile) -> AuditReport:
    """Run static security and safety checks against a MacroProfile."""
    findings: list[AuditFinding] = []
    enabled_flat = _flatten_enabled_actions(profile.actions)

    # 1. Check for empty macro
    if not enabled_flat:
        findings.append(
            AuditFinding(
                code="KF-SEC-000",
                severity=AuditSeverity.INFO,
                title="No Enabled Steps",
                description="The profile currently has no enabled macro steps to execute.",
                remediation="Add or enable at least one step in the Visual Sequence Builder.",
            )
        )
        return AuditReport(findings=findings)

    # 2. Check for 0ms infinite / repeat loop starvation (KF-SEC-001)
    total_wait = profile.total_cycle_wait_ms()
    is_repeating = profile.repeat_mode in (RepeatMode.INFINITE, RepeatMode.DURATION) or (
        profile.repeat_mode == RepeatMode.COUNT and profile.repeat_count > 1
    )
    if is_repeating and total_wait < MIN_SAFE_CYCLE_DELAY_MS:
        findings.append(
            AuditFinding(
                code="KF-SEC-001",
                severity=AuditSeverity.WARNING,
                title="Zero-Delay Loop Detected (Auto-Governor Active)",
                description=(
                    f"Total cycle wait time is {total_wait}ms. Running a loop without delays can "
                    f"flood the Windows input queue. KeyForge will automatically enforce a "
                    f"{MIN_SAFE_CYCLE_DELAY_MS}ms safety yield per cycle."
                ),
                remediation="Add a Wait step (e.g., 50ms-200ms) or set Inter-Cycle Delay >= 10ms.",
            )
        )

    # 3. Check for unbalanced KEY_DOWN / MOUSE_DOWN (KF-SEC-002)
    held_keys: set[str] = set()
    held_buttons: set[str] = set()
    for act in enabled_flat:
        if act.action_type == ActionType.KEY_DOWN:
            held_keys.add(act.key)
        elif act.action_type == ActionType.KEY_UP:
            held_keys.discard(act.key)
        elif act.action_type == ActionType.MOUSE_CLICK:
            if act.click_type == ClickType.DOWN:
                held_buttons.add(act.button.value)
            elif act.click_type == ClickType.UP:
                held_buttons.discard(act.button.value)

    if held_keys or held_buttons:
        items = sorted(held_keys) + [f"Mouse:{b}" for b in sorted(held_buttons)]
        findings.append(
            AuditFinding(
                code="KF-SEC-002",
                severity=AuditSeverity.WARNING,
                title="Unbalanced Key/Button Hold in Sequence",
                description=(
                    f"Inputs {', '.join(items)} are pressed down without a matching release step in "
                    "the sequence. KeyForge's EmergencyInputSanitizer will automatically release "
                    "them when the macro stops."
                ),
                remediation="Add a matching KeyUp / MouseUp step if you do not want the input held across cycles.",
            )
        )

    # 4. Check for dangerous system/shell key combos (KF-SEC-003), including modifier variants & split KeyDowns
    active_modifiers: set[str] = set()
    reported_dangerous: set[str] = set()
    for act in enabled_flat:
        if act.action_type == ActionType.KEY_DOWN:
            norm_keys = _normalize_key_set(act.key)
            active_modifiers.update(norm_keys)
            combined = frozenset(active_modifiers)
        elif act.action_type == ActionType.KEY_UP:
            norm_keys = _normalize_key_set(act.key)
            active_modifiers.difference_update(norm_keys)
            continue
        elif act.action_type == ActionType.KEY_PRESS:
            norm_keys = _normalize_key_set(act.key)
            combined = frozenset(active_modifiers | norm_keys)
        else:
            continue

        for req_set, canonical_label, reason in _DANGEROUS_KEY_SETS:
            if req_set.issubset(combined) and canonical_label not in reported_dangerous:
                reported_dangerous.add(canonical_label)
                findings.append(
                    AuditFinding(
                        code="KF-SEC-003",
                        severity=AuditSeverity.CRITICAL,
                        title=f"Sensitive System Shortcut Detected: [{canonical_label}]",
                        description=f"Sequence dispatches [{canonical_label}]: {reason}",
                        remediation="Verify that this step is intentional before running an imported macro.",
                    )
                )

    # 5. Check for extreme click rate > 100 CPS (KF-SEC-004)
    click_count = _count_expanded_clicks(profile.actions)
    effective_wait = max(MIN_SAFE_CYCLE_DELAY_MS, total_wait)
    if click_count > 0:
        estimated_cps = (click_count * 1000.0) / float(effective_wait)
        if estimated_cps > 100.0:
            findings.append(
                AuditFinding(
                    code="KF-SEC-004",
                    severity=AuditSeverity.WARNING,
                    title=f"Extreme Click Rate (~{estimated_cps:.0f} CPS)",
                    description=(
                        f"Macro dispatches ~{estimated_cps:.0f} clicks per second, which may "
                        "lag target windows or trigger anti-cheat rate limiters."
                    ),
                    remediation="Increase Wait duration to >= 25ms (<= 40 CPS) and add 5-15ms Jitter.",
                )
            )

    # 6. Check for hotkey self-collision (KF-SEC-005)
    control_hotkeys = {
        profile.start_stop_hotkey,
        profile.panic_hotkey,
        profile.record_hotkey,
    }
    for act in enabled_flat:
        if act.action_type in (ActionType.KEY_PRESS, ActionType.KEY_DOWN):
            if act.key in control_hotkeys:
                findings.append(
                    AuditFinding(
                        code="KF-SEC-005",
                        severity=AuditSeverity.INFO,
                        title=f"Step Uses Control Hotkey [{act.key}]",
                        description=(
                            f"Macro step sends [{act.key}], which is also assigned as a KeyForge hotkey. "
                            "KeyForge stamps synthetic inputs with 0x4B465247 to prevent self-triggering."
                        ),
                        remediation="Consider using a dedicated F-key (e.g., F6/F7/F12) not used by your macro.",
                    )
                )

    return AuditReport(findings=findings)
