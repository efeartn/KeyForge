pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const CODENAME: &str = "Pulse";

/// Canonical mapping of kTap (and historical KeyForge) release versions to codenames.
pub const RELEASE_CODENAMES: &[(&str, &str)] = &[
    ("1.0.0", "Ignition"),
    ("1.1.0", "Titanium"),
    ("1.2.0", "Carbon"),
    ("1.3.0", "Quench"),
    ("1.4.0", "Temper"),
    ("1.5.0", "Obsidian"),
    ("1.6.0", "Alloy"),
    ("1.6.1", "Shield"),
    ("2.0.0", "Ironclad"),
    ("2.1.0", "Waystone"),
    ("3.0.0", "Pulse"),
];

/// Look up the release codename for a semantic version string (with optional leading `v`).
pub fn codename_for_version(version_str: &str) -> Option<&'static str> {
    let clean = version_str
        .trim()
        .trim_start_matches(['v', 'V'])
        .split(['-', '+'])
        .next()
        .unwrap_or("")
        .trim();
    RELEASE_CODENAMES
        .iter()
        .find(|(ver, _)| *ver == clean)
        .map(|(_, name)| *name)
}

/// Human-friendly version + codename label, e.g. `v3.0.0 "Pulse"`.
pub fn full_version_label() -> String {
    format!("v{VERSION} \"{CODENAME}\"")
}

pub mod config;
pub mod engine;
pub mod installer;
pub mod models;
pub mod security;
pub mod ui;
pub mod updater;
