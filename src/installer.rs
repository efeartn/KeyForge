//! Cross-platform installation, shortcut management, and safe uninstallation for KeyForge.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine as _;
use serde_json::json;

use crate::config::{get_config_dir, home_dir, is_test_binary_env};
use crate::VERSION;

pub const UNINSTALL_REG_SUBKEY: &str =
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\KeyForge";
pub const EMBEDDED_ICON_PNG: &[u8] = include_bytes!("../assets/icon.png");

#[derive(Debug, Clone)]
pub struct InstallManifest {
    pub version: String,
    pub installed_exe: PathBuf,
    pub desktop_shortcut: Option<PathBuf>,
    pub start_menu_shortcut: Option<PathBuf>,
    pub config_dir: PathBuf,
}

#[derive(Debug, Clone)]
pub struct UninstallResult {
    pub uninstalled_dir: PathBuf,
    pub purged_user_data: bool,
}

struct HostProtectedPaths {
    install_dir: PathBuf,
    shortcuts: HashSet<PathBuf>,
}

fn canonical_or_norm(p: &Path) -> PathBuf {
    std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf())
}

fn protected_host_paths() -> &'static HostProtectedPaths {
    static PROTECTED: OnceLock<HostProtectedPaths> = OnceLock::new();
    PROTECTED.get_or_init(|| {
        let home = home_dir();
        let desktop_win = std::env::var("USERPROFILE")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.clone())
            .join("Desktop")
            .join("KeyForge.lnk");

        let start_menu_win = std::env::var("APPDATA")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join("AppData").join("Roaming"))
            .join("Microsoft")
            .join("Windows")
            .join("Start Menu")
            .join("Programs")
            .join("KeyForge.lnk");

        let install_win = std::env::var("LOCALAPPDATA")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join("AppData").join("Local"))
            .join("Programs")
            .join("KeyForge");

        let mac_desktop = home.join("Desktop").join("KeyForge");
        let linux_desktop = home.join("Desktop").join("KeyForge.desktop");
        let linux_app = std::env::var("XDG_DATA_HOME")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".local").join("share"))
            .join("applications")
            .join("keyforge.desktop");

        let install_dir = if cfg!(target_os = "windows") {
            install_win
        } else if cfg!(target_os = "macos") {
            home.join("Applications").join("KeyForge")
        } else {
            home.join(".local").join("share").join("KeyForge")
        };

        let mut shortcuts = HashSet::new();
        for p in [
            desktop_win,
            start_menu_win,
            mac_desktop,
            linux_desktop,
            linux_app,
        ] {
            shortcuts.insert(canonical_or_norm(&p));
        }
        HostProtectedPaths {
            install_dir: canonical_or_norm(&install_dir),
            shortcuts,
        }
    })
}

pub fn init_host_protection_snapshot() {
    let _ = protected_host_paths();
}

pub fn is_protected_host_shortcut(path: &Path) -> bool {
    if !is_test_binary_env() {
        return false;
    }
    protected_host_paths()
        .shortcuts
        .contains(&canonical_or_norm(path))
}

pub fn is_protected_host_install_dir(path: &Path) -> bool {
    if !is_test_binary_env() {
        return false;
    }
    canonical_or_norm(path) == protected_host_paths().install_dir
}

/// Return the default per-user installation directory for KeyForge.
pub fn get_default_install_dir() -> PathBuf {
    if let Ok(env_dir) = std::env::var("KEYFORGE_INSTALL_DIR") {
        let trimmed = env_dir.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    if cfg!(target_os = "windows") {
        let base = std::env::var("LOCALAPPDATA")
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join("AppData").join("Local"));
        return base.join("Programs").join("KeyForge");
    }

    if cfg!(target_os = "macos") {
        return home_dir().join("Applications").join("KeyForge");
    }

    home_dir().join(".local").join("share").join("KeyForge")
}

/// Return `true` if launched from a standalone `*Setup*` installer executable.
pub fn is_running_as_setup_installer(argv0: Option<&str>) -> bool {
    let candidate = argv0
        .map(PathBuf::from)
        .or_else(|| std::env::current_exe().ok());
    candidate
        .and_then(|p| {
            p.file_stem()
                .map(|s| s.to_string_lossy().to_ascii_lowercase())
        })
        .is_some_and(|stem| stem.contains("setup"))
}

fn ps_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

pub fn resolve_powershell_executable() -> String {
    if !cfg!(windows) {
        return "powershell.exe".to_string();
    }
    let sys_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_string());
    let candidate = PathBuf::from(sys_root)
        .join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe");
    if candidate.is_file() {
        candidate.to_string_lossy().to_string()
    } else {
        "powershell.exe".to_string()
    }
}

fn encode_ps_utf16le(script: &str) -> String {
    let mut bytes = Vec::with_capacity(script.len() * 2);
    for unit in script.encode_utf16() {
        bytes.extend_from_slice(&unit.to_le_bytes());
    }
    B64.encode(&bytes)
}

pub fn get_windows_shortcut_paths() -> (PathBuf, PathBuf) {
    let desktop = std::env::var("USERPROFILE")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(home_dir)
        .join("Desktop")
        .join("KeyForge.lnk");

    let roaming = std::env::var("APPDATA")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join("AppData").join("Roaming"));
    let start_menu = roaming
        .join("Microsoft")
        .join("Windows")
        .join("Start Menu")
        .join("Programs")
        .join("KeyForge.lnk");
    (desktop, start_menu)
}

fn get_linux_shortcut_paths() -> (PathBuf, PathBuf) {
    let desktop = home_dir().join("Desktop").join("KeyForge.desktop");
    let data_base = std::env::var("XDG_DATA_HOME")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".local").join("share"));
    let app_menu = data_base.join("applications").join("keyforge.desktop");
    (desktop, app_menu)
}

fn get_macos_shortcut_path() -> PathBuf {
    home_dir().join("Desktop").join("KeyForge")
}

fn create_windows_shortcut(shortcut_path: &Path, target_exe: &Path, description: &str) -> bool {
    if !cfg!(windows) || is_protected_host_shortcut(shortcut_path) {
        return false;
    }
    if let Some(parent) = shortcut_path.parent() {
        if std::fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let ps_script = [
        "$ErrorActionPreference = 'Stop'".to_string(),
        "$ws = New-Object -ComObject WScript.Shell".to_string(),
        format!(
            "$sc = $ws.CreateShortcut({})",
            ps_quote(&shortcut_path.to_string_lossy())
        ),
        format!(
            "$sc.TargetPath = {}",
            ps_quote(&target_exe.to_string_lossy())
        ),
        format!(
            "$sc.WorkingDirectory = {}",
            ps_quote(
                &target_exe
                    .parent()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default()
            )
        ),
        format!(
            "$sc.IconLocation = {}",
            ps_quote(&format!("{},0", target_exe.to_string_lossy()))
        ),
        format!("$sc.Description = {}", ps_quote(description)),
        "$sc.Save()".to_string(),
    ]
    .join("\n");

    let encoded = encode_ps_utf16le(&ps_script);
    let mut cmd = Command::new(resolve_powershell_executable());
    cmd.args([
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        &encoded,
    ])
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::null());

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    match cmd.status() {
        Ok(status) => status.success() && shortcut_path.exists(),
        Err(_) => false,
    }
}

#[cfg(target_os = "windows")]
fn to_wide_null(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn register_windows_uninstaller(target_dir: &Path, installed_exe: &Path) -> bool {
    if is_test_binary_env() {
        return false;
    }
    #[cfg(target_os = "windows")]
    {
        use windows_sys::Win32::System::Registry::{
            RegCloseKey, RegCreateKeyW, RegSetValueExW, HKEY, HKEY_CURRENT_USER, REG_DWORD, REG_SZ,
        };
        let subkey = to_wide_null(UNINSTALL_REG_SUBKEY);
        let mut hkey: HKEY = std::ptr::null_mut();
        let rc = unsafe { RegCreateKeyW(HKEY_CURRENT_USER, subkey.as_ptr(), &mut hkey) };
        if rc != 0 {
            return false;
        }
        let set_sz = |name: &str, val: &str| {
            let n = to_wide_null(name);
            let v = to_wide_null(val);
            unsafe {
                RegSetValueExW(
                    hkey,
                    n.as_ptr(),
                    0,
                    REG_SZ,
                    v.as_ptr() as *const u8,
                    (v.len() * 2) as u32,
                );
            }
        };
        let set_dword = |name: &str, val: u32| {
            let n = to_wide_null(name);
            let bytes = val.to_le_bytes();
            unsafe {
                RegSetValueExW(
                    hkey,
                    n.as_ptr(),
                    0,
                    REG_DWORD,
                    bytes.as_ptr(),
                    bytes.len() as u32,
                );
            }
        };

        let exe_s = installed_exe.to_string_lossy();
        let dir_s = target_dir.to_string_lossy();
        set_sz("DisplayName", "KeyForge");
        set_sz("DisplayVersion", VERSION);
        set_sz("Publisher", "KeyForge Open Source");
        set_sz("DisplayIcon", &exe_s);
        set_sz("InstallLocation", &dir_s);
        set_sz("UninstallString", &format!("\"{exe_s}\" --uninstall"));
        set_sz(
            "QuietUninstallString",
            &format!("\"{exe_s}\" --uninstall --silent"),
        );
        set_dword("NoModify", 1);
        set_dword("NoRepair", 1);
        unsafe {
            RegCloseKey(hkey);
        }
        true
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = (target_dir, installed_exe);
        false
    }
}

#[cfg(target_os = "windows")]
fn unregister_windows_uninstaller() {
    if is_test_binary_env() {
        return;
    }
    use windows_sys::Win32::System::Registry::{RegDeleteKeyW, HKEY_CURRENT_USER};
    let subkey = to_wide_null(UNINSTALL_REG_SUBKEY);
    unsafe {
        let _ = RegDeleteKeyW(HKEY_CURRENT_USER, subkey.as_ptr());
    }
}

#[cfg(not(target_os = "windows"))]
fn unregister_windows_uninstaller() {}

#[cfg(unix)]
fn create_macos_shortcut(shortcut_path: &Path, target_exe: &Path) -> bool {
    if is_protected_host_shortcut(shortcut_path) {
        return false;
    }
    if let Some(parent) = shortcut_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if shortcut_path.exists() || shortcut_path.is_symlink() {
        let _ = std::fs::remove_file(shortcut_path);
    }
    std::os::unix::fs::symlink(target_exe, shortcut_path).is_ok()
}

#[cfg(not(unix))]
fn create_macos_shortcut(_shortcut_path: &Path, _target_exe: &Path) -> bool {
    false
}

fn create_linux_desktop_entry(
    entry_path: &Path,
    target_exe: &Path,
    icon_path: Option<&Path>,
    description: &str,
) -> bool {
    if is_protected_host_shortcut(entry_path) {
        return false;
    }
    if let Some(parent) = entry_path.parent() {
        if std::fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let icon_line = match icon_path {
        Some(p) if p.exists() => format!("Icon={}\n", p.display()),
        _ => String::new(),
    };
    let content = format!(
        "[Desktop Entry]\nVersion=1.0\nType=Application\nName=KeyForge\nComment={description}\nExec=\"{}\"\n{icon_line}Terminal=false\nCategories=Utility;\n",
        target_exe.display()
    );
    if std::fs::write(entry_path, content).is_err() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(entry_path, std::fs::Permissions::from_mode(0o755));
    }
    entry_path.exists()
}

fn spawn_windows_dir_cleanup(target_dir: &Path) {
    let ps_script = [
        "$ErrorActionPreference = 'SilentlyContinue'".to_string(),
        "Start-Sleep -Milliseconds 1200".to_string(),
        format!("$dir = {}", ps_quote(&target_dir.to_string_lossy())),
        "for ($i = 0; $i -lt 30; $i++) {".to_string(),
        "    if (-not (Test-Path -LiteralPath $dir)) { break }".to_string(),
        "    Remove-Item -LiteralPath $dir -Recurse -Force".to_string(),
        "    if (-not (Test-Path -LiteralPath $dir)) { break }".to_string(),
        "    Start-Sleep -Milliseconds 300".to_string(),
        "}".to_string(),
    ]
    .join("\n");

    let encoded = encode_ps_utf16le(&ps_script);
    let mut cmd = Command::new(resolve_powershell_executable());
    cmd.args([
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        &encoded,
    ])
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::null());

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
    }

    let _ = cmd.spawn();
}

/// Install KeyForge standalone binary into `install_dir` and register shortcuts/uninstaller.
pub fn install_keyforge(
    install_dir: Option<&Path>,
    source_exe: Option<&Path>,
    create_desktop_shortcut: bool,
    create_start_menu_shortcut: bool,
    register_uninstaller: bool,
) -> Result<InstallManifest, String> {
    init_host_protection_snapshot();

    let target_dir = install_dir
        .map(PathBuf::from)
        .unwrap_or_else(get_default_install_dir);
    let exe_name = if cfg!(target_os = "windows") {
        "KeyForge.exe"
    } else {
        "KeyForge"
    };
    let installed_exe = target_dir.join(exe_name);

    if is_protected_host_install_dir(&target_dir) {
        return Ok(InstallManifest {
            version: VERSION.to_string(),
            installed_exe,
            desktop_shortcut: None,
            start_menu_shortcut: None,
            config_dir: get_config_dir(None),
        });
    }

    std::fs::create_dir_all(&target_dir)
        .map_err(|e| format!("Failed to create install dir {}: {e}", target_dir.display()))?;

    let resolved_source = source_exe
        .map(PathBuf::from)
        .or_else(|| std::env::current_exe().ok());

    if let Some(src) = resolved_source {
        if src.is_file() {
            let same_file = canonical_or_norm(&src) == canonical_or_norm(&installed_exe);
            if !same_file {
                std::fs::copy(&src, &installed_exe).map_err(|e| {
                    format!(
                        "Failed to copy {} -> {}: {e}",
                        src.display(),
                        installed_exe.display()
                    )
                })?;
            }
        } else if !installed_exe.exists() {
            let _ = std::fs::write(&installed_exe, b"");
        }
    } else if !installed_exe.exists() {
        let _ = std::fs::write(&installed_exe, b"");
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&installed_exe, std::fs::Permissions::from_mode(0o755));
    }

    let mut desktop_created: Option<PathBuf> = None;
    let mut start_menu_created: Option<PathBuf> = None;
    let desc = format!("KeyForge v{VERSION} Macro Studio");

    if cfg!(target_os = "windows") {
        let (desktop_lnk, start_menu_lnk) = get_windows_shortcut_paths();
        if create_desktop_shortcut && create_windows_shortcut(&desktop_lnk, &installed_exe, &desc) {
            desktop_created = Some(desktop_lnk);
        }
        if create_start_menu_shortcut
            && create_windows_shortcut(&start_menu_lnk, &installed_exe, &desc)
        {
            start_menu_created = Some(start_menu_lnk);
        }
        if register_uninstaller {
            let _ = register_windows_uninstaller(&target_dir, &installed_exe);
        }
    } else if cfg!(target_os = "macos") {
        let mac_sc = get_macos_shortcut_path();
        if create_desktop_shortcut
            && mac_sc.parent().is_some_and(Path::is_dir)
            && create_macos_shortcut(&mac_sc, &installed_exe)
        {
            desktop_created = Some(mac_sc);
        }
    } else {
        let dst_icon = target_dir.join("keyforge.png");
        let copied_icon = if std::fs::write(&dst_icon, EMBEDDED_ICON_PNG).is_ok() {
            Some(dst_icon)
        } else {
            None
        };
        let (desktop_entry, app_menu_entry) = get_linux_shortcut_paths();
        if create_desktop_shortcut
            && desktop_entry.parent().is_some_and(Path::is_dir)
            && create_linux_desktop_entry(
                &desktop_entry,
                &installed_exe,
                copied_icon.as_deref(),
                &desc,
            )
        {
            desktop_created = Some(desktop_entry);
        }
        if create_start_menu_shortcut
            && create_linux_desktop_entry(
                &app_menu_entry,
                &installed_exe,
                copied_icon.as_deref(),
                &desc,
            )
        {
            start_menu_created = Some(app_menu_entry);
        }
    }

    let manifest_val = json!({
        "version": VERSION,
        "installed_exe": installed_exe.to_string_lossy(),
        "desktop_shortcut": desktop_created.as_ref().map(|p| p.to_string_lossy().to_string()),
        "start_menu_shortcut": start_menu_created.as_ref().map(|p| p.to_string_lossy().to_string()),
        "config_dir": get_config_dir(None).to_string_lossy(),
    });
    let manifest_path = target_dir.join("install_manifest.json");
    let mut text = serde_json::to_string_pretty(&manifest_val).unwrap_or_default();
    text.push('\n');
    std::fs::write(&manifest_path, text)
        .map_err(|e| format!("Failed to write install_manifest.json: {e}"))?;

    Ok(InstallManifest {
        version: VERSION.to_string(),
        installed_exe,
        desktop_shortcut: desktop_created,
        start_menu_shortcut: start_menu_created,
        config_dir: get_config_dir(None),
    })
}

/// Uninstall KeyForge binaries/shortcuts/registry, keeping user data intact by default.
pub fn uninstall_keyforge(
    install_dir: Option<&Path>,
    purge_user_data: bool,
    spawn_self_delete: bool,
) -> UninstallResult {
    init_host_protection_snapshot();

    let default_dir = get_default_install_dir();
    let target_dir = install_dir
        .map(PathBuf::from)
        .unwrap_or_else(|| default_dir.clone());

    if is_protected_host_install_dir(&target_dir) {
        return UninstallResult {
            uninstalled_dir: target_dir,
            purged_user_data: false,
        };
    }

    let is_default_target =
        install_dir.is_none() || canonical_or_norm(&target_dir) == canonical_or_norm(&default_dir);

    let mut shortcut_candidates: Vec<PathBuf> = Vec::new();
    let manifest_path = target_dir.join("install_manifest.json");
    if let Ok(raw) = std::fs::read_to_string(&manifest_path) {
        if let Ok(serde_json::Value::Object(map)) = serde_json::from_str(&raw) {
            for key in ["desktop_shortcut", "start_menu_shortcut"] {
                if let Some(serde_json::Value::String(s)) = map.get(key) {
                    if !s.trim().is_empty() {
                        shortcut_candidates.push(PathBuf::from(s));
                    }
                }
            }
        }
    }

    if is_default_target {
        if cfg!(target_os = "windows") {
            let (d, s) = get_windows_shortcut_paths();
            shortcut_candidates.push(d);
            shortcut_candidates.push(s);
        } else if cfg!(target_os = "macos") {
            shortcut_candidates.push(get_macos_shortcut_path());
        } else {
            let (d, a) = get_linux_shortcut_paths();
            shortcut_candidates.push(d);
            shortcut_candidates.push(a);
        }
    }

    for lnk in shortcut_candidates {
        if is_protected_host_shortcut(&lnk) {
            continue;
        }
        if lnk.exists() || lnk.is_symlink() {
            let _ = std::fs::remove_file(&lnk);
        }
    }

    if is_default_target {
        unregister_windows_uninstaller();
    }

    if purge_user_data {
        let cfg_dir = get_config_dir(None);
        if cfg_dir.exists() {
            let _ = std::fs::remove_dir_all(&cfg_dir);
        }
    }

    let running_from_target = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(canonical_or_norm))
        .is_some_and(|parent| parent == canonical_or_norm(&target_dir));

    if cfg!(windows) && running_from_target && spawn_self_delete {
        spawn_windows_dir_cleanup(&target_dir);
    } else if target_dir.exists() {
        let _ = std::fs::remove_dir_all(&target_dir);
    }

    UninstallResult {
        uninstalled_dir: target_dir,
        purged_user_data: purge_user_data,
    }
}
