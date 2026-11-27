//! Secure cross-platform GitHub release checker, SHA-256 updater, and hot-swap installer.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::Duration;

use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine as _;
use regex::Regex;
use serde_json::Value;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use url::Url;

use crate::installer::resolve_powershell_executable;
use crate::VERSION;

pub const DEFAULT_REPO: &str = "efeartn/KeyForge";
pub const MAX_API_RESPONSE_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_CHECKSUM_FILE_BYTES: usize = 1024 * 1024;

pub fn user_agent() -> String {
    format!("KeyForge/{VERSION}")
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateCheckError {
    pub message: String,
    pub reason: String,
    pub status_code: Option<u16>,
}

impl UpdateCheckError {
    pub fn new(message: impl Into<String>, reason: &str, status_code: Option<u16>) -> Self {
        Self {
            message: message.into(),
            reason: reason.to_string(),
            status_code,
        }
    }
}

impl fmt::Display for UpdateCheckError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for UpdateCheckError {}

#[derive(Debug, Clone, PartialEq, Eq)]
enum PreToken {
    Num(u64),
    Alpha(String),
}

impl Ord for PreToken {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        match (self, other) {
            (Self::Num(a), Self::Num(b)) => a.cmp(b),
            (Self::Num(_), Self::Alpha(_)) => std::cmp::Ordering::Less,
            (Self::Alpha(_), Self::Num(_)) => std::cmp::Ordering::Greater,
            (Self::Alpha(a), Self::Alpha(b)) => a.cmp(b),
        }
    }
}

impl PartialOrd for PreToken {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

/// Semantic Version 2.0.0 representation with prerelease precedence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SemVer {
    pub major: u64,
    pub minor: u64,
    pub patch: u64,
    pub prerelease: Vec<String>,
}

impl SemVer {
    fn sort_key(&self) -> (u64, u64, u64, u8, Vec<PreToken>) {
        let release_rank = u8::from(self.prerelease.is_empty());
        let pre_tokens = self
            .prerelease
            .iter()
            .map(|p| match p.parse::<u64>() {
                Ok(n) if !p.starts_with('0') || p == "0" => PreToken::Num(n),
                _ => PreToken::Alpha(p.clone()),
            })
            .collect();
        (self.major, self.minor, self.patch, release_rank, pre_tokens)
    }
}

impl Ord for SemVer {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.sort_key().cmp(&other.sort_key())
    }
}

impl PartialOrd for SemVer {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

/// Metadata for the latest GitHub release and its matched platform binary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReleaseInfo {
    pub tag_name: String,
    pub version: String,
    pub name: String,
    pub body: String,
    pub html_url: String,
    pub published_at: String,
    pub prerelease: bool,
    pub asset_name: Option<String>,
    pub download_url: Option<String>,
    pub asset_size: Option<u64>,
    pub sha256: Option<String>,
    pub checksum_url: Option<String>,
    pub is_update_available: bool,
}

fn semver_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"^[vV]?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$",
        )
        .expect("semver regex")
    })
}

fn repo_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$").expect("repo regex"))
}

fn checksum_line_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^(?P<digest>[0-9a-fA-F]{64})(?:\s+\*?(?P<filename>.+))?$")
            .expect("checksum regex")
    })
}

/// Parse a semantic version string (with optional leading `v` and prerelease/build metadata).
pub fn parse_semver(version_str: &str) -> Result<SemVer, String> {
    let caps = semver_re()
        .captures(version_str.trim())
        .ok_or_else(|| format!("Invalid semantic version: {version_str:?}"))?;
    let major = caps["major"]
        .parse::<u64>()
        .map_err(|_| format!("Invalid major version: {version_str:?}"))?;
    let minor = caps["minor"]
        .parse::<u64>()
        .map_err(|_| format!("Invalid minor version: {version_str:?}"))?;
    let patch = caps["patch"]
        .parse::<u64>()
        .map_err(|_| format!("Invalid patch version: {version_str:?}"))?;
    let prerelease = caps
        .name("pre")
        .map(|m| m.as_str().split('.').map(str::to_string).collect())
        .unwrap_or_default();
    Ok(SemVer {
        major,
        minor,
        patch,
        prerelease,
    })
}

/// Return `true` if `candidate` is strictly newer than `current` per SemVer 2.0.0.
pub fn is_version_newer(current: &str, candidate: &str) -> Result<bool, String> {
    Ok(parse_semver(candidate)? > parse_semver(current)?)
}

/// Ensure a URL uses HTTPS with a non-empty host and no control characters.
pub fn validate_https_url(url_str: &str) -> Result<String, UpdateCheckError> {
    let cleaned = url_str.trim();
    if cleaned.is_empty() {
        return Err(UpdateCheckError::new(
            "Update URL must be a non-empty HTTPS URL.",
            "unsafe_url",
            None,
        ));
    }
    if cleaned.chars().any(|c| matches!(c, '\0' | '\r' | '\n')) {
        return Err(UpdateCheckError::new(
            "Update URL contains invalid control characters.",
            "unsafe_url",
            None,
        ));
    }
    let parsed = Url::parse(cleaned).map_err(|_| {
        UpdateCheckError::new(
            format!("Refusing invalid update URL: {cleaned:?}"),
            "unsafe_url",
            None,
        )
    })?;
    if !parsed.scheme().eq_ignore_ascii_case("https") || parsed.host_str().is_none() {
        return Err(UpdateCheckError::new(
            format!("Refusing non-HTTPS update URL: {cleaned:?}"),
            "unsafe_url",
            None,
        ));
    }
    Ok(cleaned.to_string())
}

fn normalize_arch(machine: &str) -> &'static str {
    let m = machine.trim().to_ascii_lowercase();
    if m.contains("arm64") || m.contains("aarch64") || m.contains("arm") {
        "arm64"
    } else {
        "x64"
    }
}

pub fn get_platform_asset_candidates(
    system: Option<&str>,
    machine: Option<&str>,
) -> Vec<&'static str> {
    let sys_name = system
        .unwrap_or(std::env::consts::OS)
        .trim()
        .to_ascii_lowercase();
    let arch = normalize_arch(machine.unwrap_or(std::env::consts::ARCH));

    if sys_name == "windows" {
        if arch == "arm64" {
            return vec!["KeyForge-Windows-arm64.exe", "KeyForge-Windows-x64.exe"];
        }
        return vec!["KeyForge-Windows-x64.exe"];
    }
    if sys_name == "macos" || sys_name == "darwin" {
        if arch == "arm64" {
            return vec![
                "KeyForge-macOS-arm64",
                "KeyForge-macOS-arm64.zip",
                "KeyForge-macOS-arm64.dmg",
            ];
        }
        return vec![
            "KeyForge-macOS-x64",
            "KeyForge-macOS-x64.zip",
            "KeyForge-macOS-x64.dmg",
        ];
    }
    if arch == "arm64" {
        vec!["KeyForge-Linux-arm64"]
    } else {
        vec!["KeyForge-Linux-x64"]
    }
}

fn is_binary_asset_name(name: &str) -> bool {
    const NON_BINARY: &[&str] = &[
        ".txt", ".sha256", ".sha512", ".sig", ".asc", ".json", ".yml", ".yaml",
    ];
    let lowered = name.trim().to_ascii_lowercase();
    if lowered.is_empty() || lowered == "sha256sums.txt" {
        return false;
    }
    !NON_BINARY.iter().any(|suf| lowered.ends_with(suf))
}

fn tokenize_asset_name(name: &str) -> HashSet<String> {
    let stem = Path::new(name)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(name)
        .to_ascii_lowercase();
    stem.split(['-', '_', '.'])
        .filter(|tok| !tok.is_empty())
        .map(str::to_string)
        .collect()
}

pub fn select_platform_asset<'a>(
    assets: &'a [Value],
    candidates: &[&str],
) -> Option<&'a serde_json::Map<String, Value>> {
    let mut binary_assets: Vec<&serde_json::Map<String, Value>> = Vec::new();
    let mut by_lower_name: HashMap<String, &serde_json::Map<String, Value>> = HashMap::new();

    for item in assets {
        let Some(obj) = item.as_object() else {
            continue;
        };
        let Some(name) = obj.get("name").and_then(Value::as_str) else {
            continue;
        };
        if is_binary_asset_name(name) {
            binary_assets.push(obj);
            by_lower_name.insert(name.trim().to_ascii_lowercase(), obj);
        }
    }

    for &cand in candidates {
        if let Some(&matched) = by_lower_name.get(&cand.to_ascii_lowercase()) {
            return Some(matched);
        }
    }

    for &cand in candidates {
        let cand_tokens = tokenize_asset_name(cand);
        let cand_suffix = Path::new(cand)
            .extension()
            .and_then(|s| s.to_str())
            .map(|s| format!(".{}", s.to_ascii_lowercase()))
            .unwrap_or_default();

        let want_os = if cand_tokens.contains("windows") {
            "windows"
        } else if cand_tokens.contains("macos") {
            "macos"
        } else {
            "linux"
        };
        let want_arch = if cand_tokens.contains("arm64") {
            "arm64"
        } else {
            "x64"
        };

        let os_wanted: &[&str] = match want_os {
            "windows" => &["windows", "win"],
            "macos" => &["macos", "darwin"],
            _ => &["linux"],
        };
        let os_forbidden: &[&str] = match want_os {
            "windows" => &["macos", "darwin", "linux"],
            "macos" => &["windows", "win", "linux"],
            _ => &["windows", "win", "macos", "darwin"],
        };
        let (arch_wanted, arch_forbidden): (&[&str], &[&str]) = if want_arch == "arm64" {
            (&["arm64", "aarch64"], &["x64", "x86_64", "amd64"])
        } else {
            (&["x64", "x86_64", "amd64"], &["arm64", "aarch64"])
        };

        for &item in &binary_assets {
            let asset_name = item
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim();
            let asset_lower = asset_name.to_ascii_lowercase();
            if !cand_suffix.is_empty() && !asset_lower.ends_with(&cand_suffix) {
                continue;
            }
            if cand_suffix.is_empty() && asset_lower.ends_with(".exe") {
                continue;
            }
            let tokens = tokenize_asset_name(asset_name);
            if ["setup", "installer", "uninstall"]
                .iter()
                .any(|t| tokens.contains(*t))
            {
                continue;
            }
            if os_forbidden.iter().any(|t| tokens.contains(*t))
                || arch_forbidden.iter().any(|t| tokens.contains(*t))
            {
                continue;
            }
            if os_wanted.iter().any(|t| tokens.contains(*t))
                && arch_wanted.iter().any(|t| tokens.contains(*t))
            {
                return Some(item);
            }
        }
    }
    None
}

fn open_https_follow_redirects(
    url_str: &str,
    timeout: Duration,
    extra_headers: &[(&str, &str)],
) -> Result<ureq::Response, UpdateCheckError> {
    let mut current_url = validate_https_url(url_str)?;
    let agent = ureq::AgentBuilder::new()
        .redirects(0)
        .timeout(timeout)
        .build();

    for _hop in 0..=5 {
        let mut req = agent.get(&current_url).set("User-Agent", &user_agent());
        for &(k, v) in extra_headers {
            req = req.set(k, v);
        }

        match req.call() {
            Ok(resp) => {
                let status = resp.status();
                if matches!(status, 301 | 302 | 303 | 307 | 308) {
                    let loc = resp.header("location").ok_or_else(|| {
                        UpdateCheckError::new(
                            "Redirect response missing Location header.",
                            "invalid_response",
                            Some(status),
                        )
                    })?;
                    let base = Url::parse(&current_url).map_err(|_| {
                        UpdateCheckError::new("Invalid base URL", "unsafe_url", None)
                    })?;
                    let resolved = base.join(loc).map_err(|_| {
                        UpdateCheckError::new("Invalid redirect URL", "unsafe_url", None)
                    })?;
                    current_url = validate_https_url(resolved.as_str())?;
                    continue;
                }
                validate_https_url(resp.get_url())?;
                return Ok(resp);
            }
            Err(ureq::Error::Status(code, resp)) => {
                let remaining = resp
                    .header("x-ratelimit-remaining")
                    .unwrap_or("")
                    .trim()
                    .to_string();
                let body_hint = resp
                    .into_string()
                    .unwrap_or_default()
                    .chars()
                    .take(4096)
                    .collect::<String>()
                    .to_ascii_lowercase();
                let is_rate_limited = code == 429
                    || (code == 403 && (remaining == "0" || body_hint.contains("rate limit")));
                if is_rate_limited {
                    return Err(UpdateCheckError::new(
                        format!(
                            "GitHub API rate limit exceeded (HTTP {code}). Please try again later."
                        ),
                        "rate_limited",
                        Some(code),
                    ));
                }
                return Err(UpdateCheckError::new(
                    format!("HTTP {code} while requesting {current_url}"),
                    "http_error",
                    Some(code),
                ));
            }
            Err(ureq::Error::Transport(tr)) => {
                return Err(UpdateCheckError::new(
                    format!("Network error while contacting update server: {tr}"),
                    "offline",
                    None,
                ));
            }
        }
    }

    Err(UpdateCheckError::new(
        "Too many HTTPS redirects from update server.",
        "http_error",
        None,
    ))
}

fn read_bounded_bytes(resp: ureq::Response, max_bytes: usize) -> Result<Vec<u8>, UpdateCheckError> {
    let mut buf = Vec::new();
    let mut reader = resp.into_reader().take((max_bytes + 1) as u64);
    reader.read_to_end(&mut buf).map_err(|e| {
        UpdateCheckError::new(
            format!("Network error while reading response: {e}"),
            "offline",
            None,
        )
    })?;
    if buf.len() > max_bytes {
        return Err(UpdateCheckError::new(
            format!("Response exceeds maximum size of {max_bytes} bytes."),
            "invalid_response",
            None,
        ));
    }
    Ok(buf)
}

pub fn extract_sha256_from_checksum_text(
    text: &str,
    asset_name: &str,
    is_combined_file: bool,
) -> Option<String> {
    let asset_lower = asset_name.to_ascii_lowercase();
    let stem_lower = Path::new(asset_name)
        .file_stem()
        .and_then(|s| s.to_str())
        .map(str::to_ascii_lowercase)
        .unwrap_or_default();

    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty() {
            continue;
        }
        let Some(caps) = checksum_line_re().captures(line) else {
            continue;
        };
        let digest = caps["digest"].to_ascii_lowercase();
        if let Some(filename_m) = caps.name("filename") {
            let entry_name = Path::new(filename_m.as_str().trim())
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if entry_name == asset_lower
                || (!is_combined_file && !stem_lower.is_empty() && entry_name == stem_lower)
            {
                return Some(digest);
            }
        } else if !is_combined_file {
            return Some(digest);
        }
    }
    None
}

fn resolve_asset_sha256(
    assets: &[Value],
    asset_name: &str,
    timeout: Duration,
) -> Result<(Option<String>, Option<String>), UpdateCheckError> {
    let mut by_lower_name: HashMap<String, &serde_json::Map<String, Value>> = HashMap::new();
    for item in assets {
        if let Some(obj) = item.as_object() {
            if let Some(name) = obj.get("name").and_then(Value::as_str) {
                by_lower_name.insert(name.trim().to_ascii_lowercase(), obj);
            }
        }
    }

    let asset_lower = asset_name.to_ascii_lowercase();
    let stem_lower = Path::new(asset_name)
        .file_stem()
        .and_then(|s| s.to_str())
        .map(str::to_ascii_lowercase)
        .unwrap_or_else(|| asset_lower.clone());

    let candidates = [
        (format!("{asset_lower}.sha256.txt"), false),
        (format!("{stem_lower}.sha256.txt"), false),
        (format!("{asset_lower}.sha256"), false),
        (format!("{stem_lower}.sha256"), false),
        ("sha256sums.txt".to_string(), true),
    ];

    for (cand_name, is_combined) in candidates {
        let Some(&entry) = by_lower_name.get(&cand_name) else {
            continue;
        };
        let Some(url_raw) = entry.get("browser_download_url").and_then(Value::as_str) else {
            continue;
        };
        let safe_url = validate_https_url(url_raw)?;
        let Ok(resp) = open_https_follow_redirects(&safe_url, timeout, &[]) else {
            continue;
        };
        let Ok(raw_bytes) = read_bounded_bytes(resp, MAX_CHECKSUM_FILE_BYTES) else {
            continue;
        };
        let Ok(text) = std::str::from_utf8(&raw_bytes) else {
            continue;
        };
        if let Some(digest) = extract_sha256_from_checksum_text(text, asset_name, is_combined) {
            return Ok((Some(digest), Some(safe_url)));
        }
    }
    Ok((None, None))
}

/// Query GitHub Releases over HTTPS, select the matching binary asset, and resolve its SHA-256.
pub fn fetch_latest_release(
    current_version: Option<&str>,
    repo: Option<&str>,
    timeout_secs: f64,
) -> Result<ReleaseInfo, UpdateCheckError> {
    let cur_ver = current_version.unwrap_or(VERSION);
    let repo_clean = repo.unwrap_or(DEFAULT_REPO).trim();
    if !repo_re().is_match(repo_clean) {
        return Err(UpdateCheckError::new(
            format!("Invalid GitHub repository identifier: {repo_clean:?}. Expected 'owner/repo'."),
            "invalid_response",
            None,
        ));
    }
    parse_semver(cur_ver).map_err(|e| UpdateCheckError::new(e, "invalid_response", None))?;

    let timeout = Duration::from_secs_f64(timeout_secs.max(1.0));
    let api_url = format!("https://api.github.com/repos/{repo_clean}/releases/latest");
    let resp = open_https_follow_redirects(
        &api_url,
        timeout,
        &[
            ("Accept", "application/vnd.github+json"),
            ("X-GitHub-Api-Version", "2022-11-28"),
        ],
    )?;
    let raw_payload = read_bounded_bytes(resp, MAX_API_RESPONSE_BYTES)?;
    let payload: Value = serde_json::from_slice(&raw_payload).map_err(|e| {
        UpdateCheckError::new(
            format!("Malformed JSON in GitHub release response: {e}"),
            "invalid_response",
            None,
        )
    })?;

    parse_release_payload_value(&payload, cur_ver, Some(timeout))
}

/// Parse GitHub release JSON metadata into [`ReleaseInfo`] (used directly in unit tests too).
pub fn parse_release_payload_value(
    payload: &Value,
    current_version: &str,
    resolve_checksum_timeout: Option<Duration>,
) -> Result<ReleaseInfo, UpdateCheckError> {
    let obj = payload.as_object().ok_or_else(|| {
        UpdateCheckError::new(
            "GitHub release metadata must be a JSON object.",
            "invalid_response",
            None,
        )
    })?;

    let tag_clean = obj
        .get("tag_name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            UpdateCheckError::new(
                "Release metadata missing valid 'tag_name'.",
                "invalid_response",
                None,
            )
        })?
        .to_string();

    parse_semver(&tag_clean).map_err(|_| {
        UpdateCheckError::new(
            format!("Release tag_name is not valid SemVer: {tag_clean:?}"),
            "invalid_response",
            None,
        )
    })?;

    let version_str = tag_clean.trim_start_matches(['v', 'V']).to_string();
    let newer = is_version_newer(current_version, &tag_clean)
        .map_err(|e| UpdateCheckError::new(e, "invalid_response", None))?;

    let html_url = match obj.get("html_url") {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => {
            if s.trim().is_empty() {
                String::new()
            } else {
                validate_https_url(s)?
            }
        }
        Some(_) => {
            return Err(UpdateCheckError::new(
                "Invalid 'html_url' field in release metadata.",
                "invalid_response",
                None,
            ))
        }
    };

    let assets = match obj.get("assets") {
        None => &[][..],
        Some(Value::Array(arr)) => arr.as_slice(),
        Some(_) => {
            return Err(UpdateCheckError::new(
                "Release 'assets' field must be a list.",
                "invalid_response",
                None,
            ))
        }
    };

    let candidates = get_platform_asset_candidates(None, None);
    let matched_asset = select_platform_asset(assets, &candidates);

    let mut asset_name: Option<String> = None;
    let mut download_url: Option<String> = None;
    let mut asset_size: Option<u64> = None;
    let mut sha256: Option<String> = None;
    let mut checksum_url: Option<String> = None;

    if let Some(asset_obj) = matched_asset {
        let name_str = asset_obj
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let raw_dl = asset_obj
            .get("browser_download_url")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                UpdateCheckError::new(
                    format!("Selected asset {name_str:?} is missing 'browser_download_url'."),
                    "invalid_response",
                    None,
                )
            })?;
        download_url = Some(validate_https_url(raw_dl)?);
        asset_size = asset_obj.get("size").and_then(Value::as_u64);
        if let Some(t) = resolve_checksum_timeout {
            let (s, cu) = resolve_asset_sha256(assets, &name_str, t)?;
            sha256 = s;
            checksum_url = cu;
        }
        asset_name = Some(name_str);
    }

    let name = obj
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| tag_clean.clone());
    let body = obj
        .get("body")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let published_at = obj
        .get("published_at")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let prerelease = obj
        .get("prerelease")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    Ok(ReleaseInfo {
        tag_name: tag_clean,
        version: version_str,
        name,
        body,
        html_url,
        published_at,
        prerelease,
        asset_name,
        download_url,
        asset_size,
        sha256,
        checksum_url,
        is_update_available: newer,
    })
}

/// Stream-download a release asset over HTTPS, verify SHA-256, and atomically place `target_file`.
pub fn download_update_asset<F, C>(
    release: &ReleaseInfo,
    target_file: &Path,
    mut progress_callback: Option<F>,
    mut cancel_check: Option<C>,
    chunk_size: usize,
    timeout_secs: f64,
    require_sha256: bool,
) -> Result<PathBuf, UpdateCheckError>
where
    F: FnMut(u64, Option<u64>),
    C: FnMut() -> bool,
{
    if chunk_size == 0 {
        return Err(UpdateCheckError::new(
            "chunk_size must be a positive integer.",
            "invalid_response",
            None,
        ));
    }
    let dl_url = release.download_url.as_deref().ok_or_else(|| {
        UpdateCheckError::new(
            "Release does not contain a download URL for this platform.",
            "invalid_response",
            None,
        )
    })?;
    let safe_url = validate_https_url(dl_url)?;

    let expected_sha = release
        .sha256
        .as_ref()
        .map(|s| s.trim().to_ascii_lowercase());
    if require_sha256 && expected_sha.is_none() {
        return Err(UpdateCheckError::new(
            "SHA-256 verification is required, but the release provided no SHA-256 checksum.",
            "invalid_response",
            None,
        ));
    }
    if let Some(ref hex_s) = expected_sha {
        if hex_s.len() != 64 || !hex_s.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(UpdateCheckError::new(
                format!("Malformed expected SHA-256 checksum: {hex_s:?}"),
                "invalid_response",
                None,
            ));
        }
    }

    if cancel_check.as_mut().is_some_and(|c| c()) {
        return Err(UpdateCheckError::new(
            "Update download cancelled before starting.",
            "cancelled",
            None,
        ));
    }

    if let Some(parent) = target_file.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            UpdateCheckError::new(
                format!("Failed to create updates directory: {e}"),
                "io_error",
                None,
            )
        })?;
    }
    let temp_name = format!(
        "{}.downloading",
        target_file
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("KeyForge")
    );
    let temp_file = target_file.with_file_name(temp_name);
    let _ = std::fs::remove_file(&temp_file);

    let res = (|| -> Result<PathBuf, UpdateCheckError> {
        let resp = open_https_follow_redirects(
            &safe_url,
            Duration::from_secs_f64(timeout_secs.max(5.0)),
            &[],
        )?;
        let total_bytes = resp
            .header("content-length")
            .and_then(|s| s.trim().parse::<u64>().ok())
            .filter(|&n| n > 0)
            .or(release.asset_size.filter(|&n| n > 0));

        let mut reader = resp.into_reader();
        let mut handle = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&temp_file)
            .map_err(|e| {
                UpdateCheckError::new(format!("Failed to open temp file: {e}"), "io_error", None)
            })?;

        let mut hasher = Sha256::new();
        let mut buf = vec![0u8; chunk_size];
        let mut downloaded: u64 = 0;

        loop {
            if cancel_check.as_mut().is_some_and(|c| c()) {
                return Err(UpdateCheckError::new(
                    "Update download cancelled by user.",
                    "cancelled",
                    None,
                ));
            }
            let n = reader.read(&mut buf).map_err(|e| {
                UpdateCheckError::new(
                    format!("Network error during update download: {e}"),
                    "offline",
                    None,
                )
            })?;
            if n == 0 {
                break;
            }
            handle.write_all(&buf[..n]).map_err(|e| {
                UpdateCheckError::new(format!("Disk write error: {e}"), "io_error", None)
            })?;
            hasher.update(&buf[..n]);
            downloaded += n as u64;
            if let Some(ref mut cb) = progress_callback {
                cb(downloaded, total_bytes);
            }
        }

        if cancel_check.as_mut().is_some_and(|c| c()) {
            return Err(UpdateCheckError::new(
                "Update download cancelled by user.",
                "cancelled",
                None,
            ));
        }

        handle.flush().ok();
        handle.sync_all().ok();
        drop(handle);

        if downloaded == 0 {
            return Err(UpdateCheckError::new(
                "Downloaded update binary is empty (0 bytes).",
                "invalid_response",
                None,
            ));
        }

        if let Some(expected) = expected_sha {
            let actual = hex::encode(hasher.finalize());
            let eq: bool = actual.as_bytes().ct_eq(expected.as_bytes()).into();
            if !eq {
                return Err(UpdateCheckError::new(
                    format!("SHA-256 verification failed! Expected {expected}, got {actual}."),
                    "checksum_mismatch",
                    None,
                ));
            }
        }

        if target_file.exists() {
            let _ = std::fs::remove_file(target_file);
        }
        std::fs::rename(&temp_file, target_file).map_err(|e| {
            UpdateCheckError::new(format!("Failed to promote download: {e}"), "io_error", None)
        })?;
        Ok(target_file.to_path_buf())
    })();

    if res.is_err() {
        let _ = std::fs::remove_file(&temp_file);
    }
    res
}

fn ps_single_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

fn validate_hotswap_path(path: &Path, label: &str) -> Result<String, String> {
    let raw = path.to_string_lossy().to_string();
    if raw.trim().is_empty() || raw.chars().any(|c| matches!(c, '\0' | '\r' | '\n')) {
        return Err(format!("Invalid {label} path for hot-swap."));
    }
    Ok(raw)
}

/// Build an injection-proof, non-destructive staged PowerShell hot-swap script for Windows.
pub fn build_windows_hotswap_script(
    new_binary: &Path,
    target_exe: &Path,
    app_pid: u32,
) -> Result<String, String> {
    if app_pid == 0 {
        return Err(format!(
            "app_pid must be a positive integer, got {app_pid}."
        ));
    }

    let new_bin_str = validate_hotswap_path(new_binary, "new_binary")?;
    let target_exe_str = validate_hotswap_path(target_exe, "target_exe")?;
    let backup_exe_str = format!("{target_exe_str}.old");
    let stage_exe_str = format!("{target_exe_str}.new");

    let ps_new = ps_single_quote(&new_bin_str);
    let ps_target = ps_single_quote(&target_exe_str);
    let ps_backup = ps_single_quote(&backup_exe_str);
    let ps_stage = ps_single_quote(&stage_exe_str);

    Ok([
        format!("$TargetPid = {app_pid}"),
        format!("$NewBinary = {ps_new}"),
        format!("$TargetExe = {ps_target}"),
        format!("$BackupExe = {ps_backup}"),
        format!("$StageExe = {ps_stage}"),
        "$WorkDir = Split-Path -LiteralPath $TargetExe -Parent".to_string(),
        String::new(),
        "$Deadline = (Get-Date).AddSeconds(30)".to_string(),
        "while ((Get-Date) -lt $Deadline) {".to_string(),
        "    $proc = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue".to_string(),
        "    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $TargetExe })".to_string(),
        "    if (($null -eq $proc) -and ($running.Count -eq 0)) { break }".to_string(),
        "    Start-Sleep -Milliseconds 250".to_string(),
        "}".to_string(),
        String::new(),
        "if ($NewBinary -eq $TargetExe) {".to_string(),
        "    if (Test-Path -LiteralPath $TargetExe) {".to_string(),
        "        Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir".to_string(),
        "        exit 0".to_string(),
        "    }".to_string(),
        "    exit 1".to_string(),
        "}".to_string(),
        String::new(),
        "if ((-not (Test-Path -LiteralPath $NewBinary)) -or ((Get-Item -LiteralPath $NewBinary).Length -le 0)) {".to_string(),
        "    exit 1".to_string(),
        "}".to_string(),
        String::new(),
        "if (Test-Path -LiteralPath $StageExe) {".to_string(),
        "    Remove-Item -LiteralPath $StageExe -Force -ErrorAction SilentlyContinue".to_string(),
        "}".to_string(),
        String::new(),
        "$Swapped = $false".to_string(),
        "for ($Attempt = 0; $Attempt -lt 15; $Attempt++) {".to_string(),
        "    try {".to_string(),
        "        Copy-Item -LiteralPath $NewBinary -Destination $StageExe -Force -ErrorAction Stop".to_string(),
        "        if (-not (Test-Path -LiteralPath $StageExe)) { throw 'Staged binary copy failed' }".to_string(),
        "        if ((Get-Item -LiteralPath $StageExe).Length -le 0) { throw 'Staged binary empty' }".to_string(),
        "        if (Test-Path -LiteralPath $TargetExe) {".to_string(),
        "            if (Test-Path -LiteralPath $BackupExe) {".to_string(),
        "                Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue".to_string(),
        "            }".to_string(),
        "            Move-Item -LiteralPath $TargetExe -Destination $BackupExe -Force -ErrorAction Stop".to_string(),
        "        }".to_string(),
        "        Move-Item -LiteralPath $StageExe -Destination $TargetExe -Force -ErrorAction Stop".to_string(),
        "        if ((Test-Path -LiteralPath $TargetExe) -and ((Get-Item -LiteralPath $TargetExe).Length -gt 0)) {".to_string(),
        "            $Swapped = $true".to_string(),
        "            break".to_string(),
        "        }".to_string(),
        "    } catch {".to_string(),
        "        if ((-not (Test-Path -LiteralPath $TargetExe)) -and (Test-Path -LiteralPath $BackupExe)) {".to_string(),
        "            Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue".to_string(),
        "        }".to_string(),
        "        Start-Sleep -Milliseconds 300".to_string(),
        "    }".to_string(),
        "}".to_string(),
        String::new(),
        "if (-not $Swapped) {".to_string(),
        "    if (Test-Path -LiteralPath $StageExe) {".to_string(),
        "        Remove-Item -LiteralPath $StageExe -Force -ErrorAction SilentlyContinue".to_string(),
        "    }".to_string(),
        "    for ($Rollback = 0; $Rollback -lt 15; $Rollback++) {".to_string(),
        "        if (Test-Path -LiteralPath $TargetExe) { break }".to_string(),
        "        if (Test-Path -LiteralPath $BackupExe) {".to_string(),
        "            Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue".to_string(),
        "            if (-not (Test-Path -LiteralPath $TargetExe)) {".to_string(),
        "                Copy-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue".to_string(),
        "            }".to_string(),
        "        }".to_string(),
        "        Start-Sleep -Milliseconds 200".to_string(),
        "    }".to_string(),
        "    exit 1".to_string(),
        "}".to_string(),
        String::new(),
        "if (Test-Path -LiteralPath $StageExe) {".to_string(),
        "    Remove-Item -LiteralPath $StageExe -Force -ErrorAction SilentlyContinue".to_string(),
        "}".to_string(),
        "if ((Test-Path -LiteralPath $TargetExe) -and (Test-Path -LiteralPath $BackupExe)) {".to_string(),
        "    Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue".to_string(),
        "}".to_string(),
        "if ((Test-Path -LiteralPath $TargetExe) -and (Test-Path -LiteralPath $NewBinary) -and ($NewBinary -ne $TargetExe)) {".to_string(),
        "    Remove-Item -LiteralPath $NewBinary -Force -ErrorAction SilentlyContinue".to_string(),
        "}".to_string(),
        String::new(),
        "Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir".to_string(),
    ]
    .join("\n"))
}

pub fn build_windows_hotswap_command(
    new_binary: &Path,
    target_exe: &Path,
    app_pid: u32,
) -> Result<Vec<String>, String> {
    let script = build_windows_hotswap_script(new_binary, target_exe, app_pid)?;
    let mut utf16_bytes = Vec::with_capacity(script.len() * 2);
    for unit in script.encode_utf16() {
        utf16_bytes.extend_from_slice(&unit.to_le_bytes());
    }
    let encoded_cmd = B64.encode(&utf16_bytes);
    Ok(vec![
        resolve_powershell_executable(),
        "-NoProfile".to_string(),
        "-NonInteractive".to_string(),
        "-WindowStyle".to_string(),
        "Hidden".to_string(),
        "-ExecutionPolicy".to_string(),
        "Bypass".to_string(),
        "-EncodedCommand".to_string(),
        encoded_cmd,
    ])
}

/// Replace the running compiled KeyForge executable with `new_binary` and relaunch cleanly.
pub fn apply_update_and_restart(new_binary: &Path, dry_run: bool) -> Result<Vec<String>, String> {
    if !new_binary.is_file() {
        return Err(format!(
            "Downloaded update binary does not exist: {}",
            new_binary.display()
        ));
    }

    let target_exe =
        std::env::current_exe().map_err(|e| format!("Failed to determine current exe: {e}"))?;
    let pid = std::process::id();

    if cfg!(target_os = "windows") {
        let cmd = build_windows_hotswap_command(new_binary, &target_exe, pid)?;
        if dry_run {
            return Ok(cmd);
        }
        let mut proc = Command::new(&cmd[0]);
        proc.args(&cmd[1..])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const DETACHED_PROCESS: u32 = 0x0000_0008;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            proc.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
        }
        proc.spawn()
            .map_err(|e| format!("Failed to spawn PowerShell updater: {e}"))?;
        return Ok(cmd);
    }

    let target_str = target_exe.to_string_lossy().to_string();
    if dry_run {
        return Ok(vec![target_str]);
    }

    let stage_path = target_exe
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(format!(
            ".{}.update.tmp",
            target_exe
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("KeyForge")
        ));
    std::fs::copy(new_binary, &stage_path)
        .map_err(|e| format!("Failed to stage POSIX update: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&stage_path, std::fs::Permissions::from_mode(0o755));
    }
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("xattr")
            .args(["-d", "com.apple.quarantine"])
            .arg(&stage_path)
            .status();
    }
    std::fs::rename(&stage_path, &target_exe)
        .map_err(|e| format!("Failed to replace executable: {e}"))?;
    if new_binary != target_exe {
        let _ = std::fs::remove_file(new_binary);
    }
    Command::new(&target_exe)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to relaunch updated binary: {e}"))?;

    Ok(vec![target_str])
}
