pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const CODENAME: &str = "";

/// Mapping of release versions to optional display labels.
pub const RELEASE_CODENAMES: &[(&str, &str)] = &[];

/// Look up an optional display label for a semantic version string.
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

/// Human-friendly version label, e.g. `1.0.0`.
pub fn full_version_label() -> String {
    if CODENAME.is_empty() {
        VERSION.to_string()
    } else {
        format!("v{VERSION} \"{CODENAME}\"")
    }
}

pub mod config;
pub mod engine;
pub mod installer;
pub mod models;
pub mod security;
pub mod ui;
pub mod updater;
