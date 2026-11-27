//! Static security and operational safety auditor for macro profiles.

use std::collections::BTreeSet;

use crate::models::action::{ActionType, ClickType, MacroAction};
use crate::models::keymap::parse_key_combo;
use crate::models::profile::{MacroProfile, RepeatMode};
use crate::security::validator::MIN_SAFE_CYCLE_DELAY_MS;

const DANGEROUS_KEY_COMBOS: &[(&str, &str, &[&str])] = &[
    (
        "Win+R",
        "Opens the Windows Run dialog (often abused to execute hidden shell commands).",
        &["Win", "R"],
    ),
    (
        "Win+X",
        "Opens the Windows Power User menu (can launch Administrator Terminal).",
        &["Win", "X"],
    ),
    (
        "Alt+F4",
        "Force-closes the active foreground application window.",
        &["Alt", "F4"],
    ),
    (
        "Ctrl+Shift+Esc",
        "Opens Windows Task Manager.",
        &["Ctrl", "Shift", "Esc"],
    ),
    (
        "Ctrl+Alt+Delete",
        "Invokes the Windows Security screen.",
        &["Ctrl", "Alt", "Delete"],
    ),
];

fn normalize_family(canonical_name: &str) -> &str {
    match canonical_name {
        "RWin" => "Win",
        "RCtrl" => "Ctrl",
        "RShift" => "Shift",
        "RAlt" => "Alt",
        other => other,
    }
}

fn normalize_key_set(combo_str: &str) -> BTreeSet<String> {
    parse_key_combo(combo_str)
        .unwrap_or_default()
        .into_iter()
        .map(|k| normalize_family(k.canonical_name).to_string())
        .collect()
}

fn count_expanded_clicks(actions: &[MacroAction]) -> i64 {
    let mut total = 0i64;
    for act in actions {
        if !act.enabled {
            continue;
        }
        match act.action_type {
            ActionType::MouseClick => {
                total = total.saturating_add(if act.click_type == ClickType::Double {
                    2
                } else {
                    1
                });
            }
            ActionType::RepeatBlock => {
                total = total.saturating_add(
                    act.repeat_count
                        .saturating_mul(count_expanded_clicks(&act.sub_actions)),
                );
            }
            _ => {}
        }
    }
    total
}

/// Severity level of a security or operational safety finding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuditSeverity {
    Info,
    Warning,
    Critical,
}

impl AuditSeverity {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Info => "info",
            Self::Warning => "warning",
            Self::Critical => "critical",
        }
    }
}

/// A single finding discovered during static analysis of a [`MacroProfile`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuditFinding {
    pub code: String,
    pub severity: AuditSeverity,
    pub title: String,
    pub description: String,
    pub remediation: String,
}

/// Summary of all security and safety findings for a [`MacroProfile`].
#[derive(Debug, Clone, Default)]
pub struct AuditReport {
    pub findings: Vec<AuditFinding>,
}

impl AuditReport {
    pub fn is_clean(&self) -> bool {
        self.findings.is_empty()
    }

    pub fn has_critical(&self) -> bool {
        self.findings
            .iter()
            .any(|f| f.severity == AuditSeverity::Critical)
    }

    pub fn has_warnings(&self) -> bool {
        self.findings
            .iter()
            .any(|f| matches!(f.severity, AuditSeverity::Warning | AuditSeverity::Critical))
    }

    /// Short one-line summary for UI security status bar.
    pub fn badge_summary(&self) -> String {
        let crit = self
            .findings
            .iter()
            .filter(|f| f.severity == AuditSeverity::Critical)
            .count();
        let warn = self
            .findings
            .iter()
            .filter(|f| f.severity == AuditSeverity::Warning)
            .count();
        if crit > 0 {
            format!("SECURITY ALERT: {crit} Critical, {warn} Warning(s)")
        } else if warn > 0 {
            format!("SAFETY NOTICE: {warn} Warning(s)")
        } else {
            "SECURITY AUDIT: PASSED (0 Issues)".to_string()
        }
    }
}

fn flatten_enabled_actions<'a>(actions: &'a [MacroAction], out: &mut Vec<&'a MacroAction>) {
    for act in actions {
        if !act.enabled {
            continue;
        }
        out.push(act);
        if act.action_type == ActionType::RepeatBlock {
            flatten_enabled_actions(&act.sub_actions, out);
        }
    }
}

/// Run static security and safety checks against a [`MacroProfile`].
pub fn audit_profile(profile: &MacroProfile) -> AuditReport {
    let mut findings = Vec::new();
    let mut enabled_flat = Vec::new();
    flatten_enabled_actions(&profile.actions, &mut enabled_flat);

    // 1. Empty macro
    if enabled_flat.is_empty() {
        findings.push(AuditFinding {
            code: "KF-SEC-000".to_string(),
            severity: AuditSeverity::Info,
            title: "No Enabled Steps".to_string(),
            description: "The profile currently has no enabled macro steps to execute.".to_string(),
            remediation: "Add or enable at least one step in the Visual Sequence Builder."
                .to_string(),
        });
        return AuditReport { findings };
    }

    // 2. Zero-delay loop starvation (KF-SEC-001)
    let total_wait = profile.total_cycle_wait_ms();
    let is_repeating = matches!(
        profile.repeat_mode,
        RepeatMode::Infinite | RepeatMode::Duration
    ) || (profile.repeat_mode == RepeatMode::Count && profile.repeat_count > 1);
    if is_repeating && total_wait < MIN_SAFE_CYCLE_DELAY_MS {
        findings.push(AuditFinding {
            code: "KF-SEC-001".to_string(),
            severity: AuditSeverity::Warning,
            title: "Zero-Delay Loop Detected (Auto-Governor Active)".to_string(),
            description: format!(
                "Total cycle wait time is {total_wait}ms. Running a loop without delays can \
                 flood the OS input queue. KeyForge will automatically enforce a \
                 {MIN_SAFE_CYCLE_DELAY_MS}ms safety yield per cycle."
            ),
            remediation: "Add a Wait step (e.g., 50ms-200ms) or set Inter-Cycle Delay >= 10ms."
                .to_string(),
        });
    }

    // 3. Unbalanced KEY_DOWN / MOUSE_DOWN (KF-SEC-002)
    let mut held_keys: BTreeSet<String> = BTreeSet::new();
    let mut held_buttons: BTreeSet<String> = BTreeSet::new();
    for act in &enabled_flat {
        match act.action_type {
            ActionType::KeyDown => {
                held_keys.insert(act.key.clone());
            }
            ActionType::KeyUp => {
                held_keys.remove(&act.key);
            }
            ActionType::MouseClick => match act.click_type {
                ClickType::Down => {
                    held_buttons.insert(act.button.as_str().to_string());
                }
                ClickType::Up => {
                    held_buttons.remove(act.button.as_str());
                }
                _ => {}
            },
            _ => {}
        }
    }
    if !held_keys.is_empty() || !held_buttons.is_empty() {
        let mut items: Vec<String> = held_keys.into_iter().collect();
        for b in held_buttons {
            items.push(format!("Mouse:{b}"));
        }
        findings.push(AuditFinding {
            code: "KF-SEC-002".to_string(),
            severity: AuditSeverity::Warning,
            title: "Unbalanced Key/Button Hold in Sequence".to_string(),
            description: format!(
                "Inputs {} are pressed down without a matching release step in the sequence. \
                 KeyForge's EmergencyInputSanitizer will automatically release them when the macro stops.",
                items.join(", ")
            ),
            remediation:
                "Add a matching KeyUp / MouseUp step if you do not want the input held across cycles."
                    .to_string(),
        });
    }

    // 4. Dangerous system shortcuts (KF-SEC-003), including modifier variants & split KeyDowns
    let mut active_modifiers: BTreeSet<String> = BTreeSet::new();
    let mut reported_dangerous: BTreeSet<&str> = BTreeSet::new();
    for act in &enabled_flat {
        let combined: BTreeSet<String> = match act.action_type {
            ActionType::KeyDown => {
                for k in normalize_key_set(&act.key) {
                    active_modifiers.insert(k);
                }
                active_modifiers.clone()
            }
            ActionType::KeyUp => {
                for k in normalize_key_set(&act.key) {
                    active_modifiers.remove(&k);
                }
                continue;
            }
            ActionType::KeyPress => {
                let mut set = active_modifiers.clone();
                for k in normalize_key_set(&act.key) {
                    set.insert(k);
                }
                set
            }
            _ => continue,
        };

        for &(label, reason, req_keys) in DANGEROUS_KEY_COMBOS {
            if !reported_dangerous.contains(label)
                && req_keys.iter().all(|req| combined.contains(*req))
            {
                reported_dangerous.insert(label);
                findings.push(AuditFinding {
                    code: "KF-SEC-003".to_string(),
                    severity: AuditSeverity::Critical,
                    title: format!("Sensitive System Shortcut Detected: [{label}]"),
                    description: format!("Sequence dispatches [{label}]: {reason}"),
                    remediation:
                        "Verify that this step is intentional before running an imported macro."
                            .to_string(),
                });
            }
        }
    }

    // 5. Extreme click rate > 100 CPS (KF-SEC-004)
    let click_count = count_expanded_clicks(&profile.actions);
    let effective_wait = total_wait.max(MIN_SAFE_CYCLE_DELAY_MS);
    if click_count > 0 {
        let estimated_cps = (click_count as f64 * 1000.0) / (effective_wait as f64);
        if estimated_cps > 100.0 {
            findings.push(AuditFinding {
                code: "KF-SEC-004".to_string(),
                severity: AuditSeverity::Warning,
                title: format!("Extreme Click Rate (~{estimated_cps:.0} CPS)"),
                description: format!(
                    "Macro dispatches ~{estimated_cps:.0} clicks per second, which may \
                     lag target windows or trigger anti-cheat rate limiters."
                ),
                remediation: "Increase Wait duration to >= 25ms (<= 40 CPS) and add 5-15ms Jitter."
                    .to_string(),
            });
        }
    }

    // 6. Hotkey self-collision (KF-SEC-005)
    let control_hotkeys = [
        profile.start_stop_hotkey.as_str(),
        profile.panic_hotkey.as_str(),
        profile.record_hotkey.as_str(),
    ];
    for act in &enabled_flat {
        if matches!(act.action_type, ActionType::KeyPress | ActionType::KeyDown)
            && control_hotkeys.contains(&act.key.as_str())
        {
            findings.push(AuditFinding {
                code: "KF-SEC-005".to_string(),
                severity: AuditSeverity::Info,
                title: format!("Step Uses Control Hotkey [{}]", act.key),
                description: format!(
                    "Macro step sends [{}], which is also assigned as a KeyForge hotkey. \
                     KeyForge stamps synthetic inputs with 0x4B465247 to prevent self-triggering.",
                    act.key
                ),
                remediation:
                    "Consider using a dedicated F-key (e.g., F6/F7/F12) not used by your macro."
                        .to_string(),
            });
        }
    }

    AuditReport { findings }
}
