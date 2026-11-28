use std::path::Path;
use std::process::Command;
use std::sync::Arc;
use std::time::Duration;

use keyforge::config::{
    delete_custom_preset, get_config_dir, load_active_profile_backup, load_app_config,
    load_custom_presets, save_active_profile_backup, save_app_config, save_custom_preset,
    AppConfig, ACTIVE_PROFILE_BACKUP_FILENAME, ACTIVE_PROFILE_FILENAME, CONFIG_BACKUP_FILENAME,
    CONFIG_FILENAME,
};
use keyforge::engine::backend::DryRunBackend;
use keyforge::engine::backend::KEYFORGE_EXTRA_INFO;
use keyforge::engine::executor::{ExecutorState, MacroExecutor};
use keyforge::engine::hotkeys::GlobalHotkeyManager;
use keyforge::engine::recorder::{MacroRecorder, RecorderConfig};
use keyforge::installer::{
    init_host_protection_snapshot, install_keyforge, is_protected_host_install_dir,
    is_protected_host_shortcut, is_running_as_setup_installer, resolve_powershell_executable,
    uninstall_keyforge,
};
use keyforge::models::action::{
    make_key_down, make_key_press, make_mouse_click, make_mouse_scroll, make_repeat_block,
    make_wait, ActionType, ClickType, MouseButton, MoveMode,
};
use keyforge::models::dsl::{format_actions_to_dsl, parse_quick_expression};
use keyforge::models::keymap::{
    get_key_name_from_vk, is_valid_key_combo, normalize_key_combo, parse_key_combo,
    resolve_single_key,
};
use keyforge::models::profile::{
    create_default_profile, get_builtin_presets, MacroProfile, RepeatMode,
};
use keyforge::security::auditor::audit_profile;
use keyforge::security::integrity::{
    build_signed_envelope, load_profile_secure, parse_and_verify_envelope, save_profile_secure,
};
use keyforge::security::validator::{validate_profile_complexity, validate_safe_file_path};
use keyforge::updater::{
    build_windows_hotswap_command, build_windows_hotswap_script, extract_sha256_from_checksum_text,
    get_platform_asset_candidates, is_version_newer, parse_release_payload_value, parse_semver,
    select_platform_asset, validate_https_url,
};
use keyforge::{codename_for_version, full_version_label, CODENAME, RELEASE_CODENAMES, VERSION};
use serde_json::json;
use tempfile::TempDir;

#[test]
fn test_version_and_keymap_resolution() {
    assert_eq!(VERSION, "2.1.0");
    assert_eq!(CODENAME, "Waystone");
    assert_eq!(full_version_label(), "v2.1.0 \"Waystone\"");
    assert_eq!(codename_for_version("v1.0.0"), Some("Ignition"));
    assert_eq!(codename_for_version("1.6.1"), Some("Shield"));
    assert_eq!(codename_for_version("v2.0.0"), Some("Ironclad"));
    assert_eq!(codename_for_version("v2.1.0-rc.1"), Some("Waystone"));
    assert_eq!(codename_for_version("9.9.9"), None);
    for &(ver, name) in RELEASE_CODENAMES {
        assert_eq!(codename_for_version(ver), Some(name));
    }

    assert_eq!(resolve_single_key("e").unwrap().canonical_name, "E");
    assert_eq!(resolve_single_key("cmd").unwrap().canonical_name, "Win");
    assert_eq!(resolve_single_key("option").unwrap().canonical_name, "Alt");
    assert_eq!(
        resolve_single_key("bksp").unwrap().canonical_name,
        "Backspace"
    );
    assert_eq!(get_key_name_from_vk(0x75), Some("F6"));
    assert_eq!(get_key_name_from_vk(0xA0), Some("Shift"));

    assert_eq!(normalize_key_combo("ctrl+shift+e").unwrap(), "Ctrl+Shift+E");
    assert_eq!(parse_key_combo("Alt+F4").unwrap().len(), 2);
    assert!(is_valid_key_combo("Ctrl+C"));
    assert!(!is_valid_key_combo(""));
    assert!(!is_valid_key_combo("Ctrl+ctrl"));
    assert!(!is_valid_key_combo("Ctrl+Shift+Alt+Win+E")); // > 4 keys
    assert!(resolve_single_key("NoSuchKey_XYZ").is_err());
}

#[test]
fn test_actions_dsl_and_presets_roundtrip() {
    let expr = "Press E -> Wait 200ms ±10ms -> Click -> Press 1";
    let parsed = parse_quick_expression(expr).unwrap();
    assert_eq!(parsed.len(), 4);
    assert_eq!(parsed[0].action_type, ActionType::KeyPress);
    assert_eq!(parsed[0].key, "E");
    assert_eq!(parsed[1].wait_ms, 200);
    assert_eq!(parsed[1].jitter_ms, 10);
    assert_eq!(parsed[2].action_type, ActionType::MouseClick);
    assert_eq!(parsed[3].key, "1");

    let formatted = format_actions_to_dsl(&parsed);
    let reparsed = parse_quick_expression(&formatted).unwrap();
    assert_eq!(reparsed.len(), 4);

    let nested = parse_quick_expression(
        "Repeat 3 { Press F -> Wait 120ms ±15ms } -> Right Click at (640, 480) -> Move Relative (+10, -5) -> Scroll Down 3",
    )
    .unwrap();
    assert_eq!(nested.len(), 4);
    assert_eq!(nested[0].action_type, ActionType::RepeatBlock);
    assert_eq!(nested[0].repeat_count, 3);
    assert_eq!(nested[1].button, MouseButton::Right);
    assert_eq!(nested[1].x, Some(640));
    assert_eq!(nested[2].move_mode, MoveMode::Relative);
    assert_eq!(nested[3].scroll_dy, -3);

    // Validation boundaries
    assert!(make_wait(0, 0, "").is_err());
    assert!(make_mouse_scroll(0, 0, "").is_err());
    assert!(
        make_mouse_click(MouseButton::Left, ClickType::Single, Some(10), None, 25, "").is_err()
    );
    assert!(parse_quick_expression("Repeat 2 { Wait 10ms ").is_err());

    // Presets
    let presets = get_builtin_presets();
    assert_eq!(presets.len(), 5);
    for mut p in presets {
        assert!(p.validate().is_ok());
    }
}

#[test]
fn test_sha256_python_v1_parity_and_security_guards() {
    // Exact signed JSON generated by Python KeyForge v1.6.1
    let golden_python_json = r#"{"keyforge_format": "kfmacro-v1", "integrity_sha256": "b88166d8df2bdfd1bda7f9bd71c777e9078621c7eebe10083746e66ed407d5cd", "profile": {"schema_version": "1.0", "name": "Action Loop (E -> 200ms -> Click -> 1)", "description": "Classic action combo: Press E -> Wait 200ms -> Left Click -> Press 1", "repeat_mode": "infinite", "repeat_count": 10, "repeat_duration_s": 30.0, "inter_cycle_delay_ms": 100, "start_stop_hotkey": "F6", "panic_hotkey": "F12", "record_hotkey": "F7", "created_at": "2026-11-27T14:29:18+00:00", "updated_at": "2026-11-27T14:29:18+00:00", "actions": [{"id": "97a583d4304b", "action_type": "key_press", "enabled": true, "label": "Interact", "key": "E", "hold_ms": 25}, {"id": "fd8d4e4bb87c", "action_type": "wait", "enabled": true, "label": "Animation Delay", "wait_ms": 200, "jitter_ms": 10}, {"id": "e6a8daf6c9f1", "action_type": "mouse_click", "enabled": true, "label": "Primary Attack / Confirm", "button": "left", "click_type": "single", "hold_ms": 25, "x": null, "y": null}, {"id": "86989c6a3edc", "action_type": "wait", "enabled": true, "label": "Post-Click Pause", "wait_ms": 100, "jitter_ms": 5}, {"id": "4db7dcefa4ea", "action_type": "key_press", "enabled": true, "label": "Equip Slot 1", "key": "1", "hold_ms": 25}]}}"#;

    let (profile, verified) = parse_and_verify_envelope(golden_python_json.as_bytes(), true)
        .expect("Rust verifier must match Python canonical SHA-256 byte-for-byte!");
    assert!(verified);
    assert_eq!(profile.actions.len(), 5);

    // Tampered payload rejected
    let tampered = golden_python_json.replace("\"wait_ms\": 200", "\"wait_ms\": 201");
    assert!(parse_and_verify_envelope(tampered.as_bytes(), true).is_err());

    // Envelope signature requirement & unknown field injection
    let unsigned = json!({"name": "Test", "actions": []});
    assert!(parse_and_verify_envelope(unsigned.to_string().as_bytes(), true).is_err());
    let injected = json!({
        "keyforge_format": "kfmacro-v1",
        "integrity_sha256": "a".repeat(64),
        "evil_field": 1,
        "profile": {"name": "X", "actions": []}
    });
    assert!(parse_and_verify_envelope(injected.to_string().as_bytes(), true).is_err());

    // Float/Bool coercion rejected
    let bad_bool = json!({
        "name": "Bad",
        "repeat_count": true,
        "actions": []
    });
    assert!(parse_and_verify_envelope(bad_bool.to_string().as_bytes(), false).is_err());

    // Billion-laughs nested repeat DoS bomb rejected
    let mut bomb = MacroProfile::default();
    let leaf = make_repeat_block(100, vec![make_key_press("E", 10, "").unwrap()], "").unwrap();
    let mid = make_repeat_block(100, vec![leaf], "").unwrap();
    let outer = make_repeat_block(100, vec![mid], "").unwrap(); // 1,000,000 > 100,000
    bomb.actions = vec![outer];
    assert!(validate_profile_complexity(&mut bomb).is_err());

    // File path guards
    assert!(validate_safe_file_path(Path::new("C:\\tmp\\evil.txt"), false).is_err());
    assert!(validate_safe_file_path(Path::new("C:\\tmp\\CON.json"), false).is_err());
    assert!(validate_safe_file_path(Path::new("C:\\tmp\\stream.json:hidden"), false).is_err());

    // Save & load secure file round-trip
    let tmp = TempDir::new().unwrap();
    let file_path = tmp.path().join("signed.kfmacro.json");
    let mut prof = create_default_profile();
    save_profile_secure(&mut prof, &file_path).unwrap();
    let (reloaded, sig_ok) = load_profile_secure(&file_path, true).unwrap();
    assert!(sig_ok);
    assert_eq!(reloaded.name, prof.name);
    let _ = build_signed_envelope(&mut prof).unwrap();
}

#[test]
fn test_security_auditor_findings() {
    let mut p = create_default_profile();
    assert!(audit_profile(&p).is_clean());

    // Split KeyDown + right-hand modifier dangerous combo (KF-SEC-003) + KF-SEC-002 + KF-SEC-001
    p.repeat_mode = RepeatMode::Count;
    p.repeat_count = 5;
    p.inter_cycle_delay_ms = 0;
    p.actions = vec![
        make_key_down("RWin", "").unwrap(),
        make_key_press("R", 0, "").unwrap(),
        make_key_press("F6", 0, "").unwrap(),
    ];
    let rep = audit_profile(&p);
    assert!(rep.has_critical());
    assert!(rep.has_warnings());
    let codes: Vec<&str> = rep.findings.iter().map(|f| f.code.as_str()).collect();
    assert!(codes.contains(&"KF-SEC-001"));
    assert!(codes.contains(&"KF-SEC-002"));
    assert!(codes.contains(&"KF-SEC-003"));
    assert!(codes.contains(&"KF-SEC-005"));
}

#[test]
fn test_executor_panic_killswitch_and_recorder() {
    let backend = Arc::new(DryRunBackend::default());
    let executor = MacroExecutor::new(backend.clone(), 0.01, Some(42));
    let mut p = create_default_profile();
    p.repeat_mode = RepeatMode::Count;
    p.repeat_count = 2;
    let telem = executor.run_blocking(p).unwrap();
    assert_eq!(telem.state, ExecutorState::Idle);
    assert_eq!(telem.cycles_completed, 2);
    assert!(backend.held_keys().is_empty());
    assert!(backend.held_buttons().is_empty());

    // Panic abort releases held Shift and Left mouse button
    let backend2 = Arc::new(DryRunBackend::default());
    let exec2 = MacroExecutor::new(backend2.clone(), 1.0, None);
    let mut hold_prof = MacroProfile {
        repeat_mode: RepeatMode::Infinite,
        inter_cycle_delay_ms: 100,
        actions: vec![
            make_key_down("Shift", "").unwrap(),
            make_mouse_click(MouseButton::Left, ClickType::Down, None, None, 25, "").unwrap(),
            make_wait(5000, 0, "").unwrap(),
        ],
        ..MacroProfile::default()
    };
    hold_prof.validate().unwrap();
    assert!(exec2.start(hold_prof).unwrap());
    std::thread::sleep(Duration::from_millis(60));
    exec2.panic_abort(Duration::from_millis(500));
    assert_eq!(exec2.telemetry().state, ExecutorState::PanicAborted);
    assert!(backend2.held_keys().is_empty());
    assert!(backend2.held_buttons().is_empty());

    // Recorder coalescing & synthetic ignore
    let rec = MacroRecorder::new(RecorderConfig {
        max_recorded_steps: 3,
        ..RecorderConfig::default()
    });
    rec.set_ignored_hotkeys(&["F7", "F12", "Ctrl+F6"]);
    rec.start(false);
    // Synthetic events must be ignored
    rec.feed_key_event(0x45, true, Some(0.1), KEYFORGE_EXTRA_INFO);
    rec.feed_key_event(0x45, false, Some(0.15), KEYFORGE_EXTRA_INFO);
    // Orphan key-up must be ignored
    rec.feed_key_event(0x41, false, Some(0.2), 0);
    // Single hotkey F7 must be ignored
    rec.feed_key_event(0x76, true, Some(0.25), 0);
    // Real key tap coalesces into KEY_PRESS on final cap slot!
    rec.feed_key_event(0x45, true, Some(0.30), 0);
    rec.feed_key_event(0x45, false, Some(0.35), 0);
    let actions = rec.stop();
    assert_eq!(actions.len(), 1);
    assert_eq!(actions[0].action_type, ActionType::KeyPress);
    assert_eq!(actions[0].key, "E");

    // Hotkey manager registration & test trigger
    let hk = GlobalHotkeyManager::default();
    let fired = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let f2 = Arc::clone(&fired);
    hk.register("panic", "Ctrl+F12", move || {
        f2.store(true, std::sync::atomic::Ordering::SeqCst);
    })
    .unwrap();
    assert!(hk.trigger_for_test("panic"));
    assert!(fired.load(std::sync::atomic::Ordering::SeqCst));
}

#[test]
fn test_config_persistence_and_backup_recovery() {
    let tmp = TempDir::new().unwrap();
    let dir = tmp.path();

    let cfg = AppConfig {
        ui_mode: "studio".to_string(),
        start_countdown_sec: 3,
        ..AppConfig::default()
    };
    save_app_config(&cfg, Some(dir)).unwrap();
    let loaded = load_app_config(Some(dir));
    assert_eq!(loaded.ui_mode, "studio");
    assert_eq!(loaded.start_countdown_sec, 3);

    // Save second valid config -> rotates first into .bak, then corrupt primary -> recovers valid .bak
    let cfg2 = AppConfig {
        ui_mode: "simple".to_string(),
        start_countdown_sec: 5,
        ..AppConfig::default()
    };
    save_app_config(&cfg2, Some(dir)).unwrap();
    std::fs::write(dir.join(CONFIG_FILENAME), b"{corrupt json!!!").unwrap();
    assert!(dir.join(CONFIG_BACKUP_FILENAME).is_file());
    let recovered = load_app_config(Some(dir));
    assert_eq!(recovered.start_countdown_sec, 3);

    // Active profile signed backup & recovery
    let mut prof = create_default_profile();
    save_active_profile_backup(&mut prof, Some(dir)).unwrap();
    save_active_profile_backup(&mut prof, Some(dir)).unwrap();
    std::fs::write(dir.join(ACTIVE_PROFILE_FILENAME), b"tampered").unwrap();
    assert!(dir.join(ACTIVE_PROFILE_BACKUP_FILENAME).is_file());
    assert!(load_active_profile_backup(Some(dir)).is_some());

    // Custom presets CRUD
    let mut custom = MacroProfile {
        name: "Farm Combo v2!".to_string(),
        ..create_default_profile()
    };
    save_custom_preset(&mut custom, Some(dir)).unwrap();
    assert!(load_custom_presets(Some(dir)).contains_key("Farm Combo v2!"));
    assert!(delete_custom_preset("Farm Combo v2!", Some(dir)));
    assert!(load_custom_presets(Some(dir)).is_empty());

    // Unisolated dir resolution still honors KEYFORGE_CONFIG_DIR
    let _ = get_config_dir(Some(dir));
}

#[test]
fn test_updater_semver_assets_and_windows_hotswap() {
    assert_eq!(parse_semver("v2.0.0").unwrap().major, 2);
    assert!(is_version_newer("1.6.1", "2.0.0").unwrap());
    assert!(is_version_newer("2.0.0-rc.1", "2.0.0").unwrap());
    assert!(!is_version_newer("2.0.0", "2.0.0").unwrap());

    assert!(validate_https_url("https://github.com/efeartn/KeyForge").is_ok());
    assert!(validate_https_url("http://github.com/efeartn/KeyForge").is_err());
    assert!(validate_https_url("https://evil.com/\nLocation").is_err());

    let assets = vec![
        json!({"name": "KeyForge-Setup-Windows-x64.exe", "browser_download_url": "https://example.com/s.exe", "size": 100}),
        json!({"name": "KeyForge-Windows-x64.exe", "browser_download_url": "https://example.com/w.exe", "size": 100}),
        json!({"name": "KeyForge-macOS-arm64", "browser_download_url": "https://example.com/m", "size": 100}),
        json!({"name": "KeyForge-Linux-x64", "browser_download_url": "https://example.com/l", "size": 100}),
    ];
    let picked_win = select_platform_asset(
        &assets,
        &get_platform_asset_candidates(Some("windows"), Some("amd64")),
    )
    .unwrap();
    assert_eq!(picked_win["name"], "KeyForge-Windows-x64.exe");
    let picked_mac = select_platform_asset(
        &assets,
        &get_platform_asset_candidates(Some("macos"), Some("aarch64")),
    )
    .unwrap();
    assert_eq!(picked_mac["name"], "KeyForge-macOS-arm64");
    let picked_lin = select_platform_asset(
        &assets,
        &get_platform_asset_candidates(Some("linux"), Some("x86_64")),
    )
    .unwrap();
    assert_eq!(picked_lin["name"], "KeyForge-Linux-x64");

    assert_eq!(
        extract_sha256_from_checksum_text(
            &format!("{}  KeyForge-Windows-x64.exe\n", "a".repeat(64)),
            "KeyForge-Windows-x64.exe",
            true
        ),
        Some("a".repeat(64))
    );

    let payload = json!({
        "tag_name": "v9.9.9",
        "html_url": "https://github.com/efeartn/KeyForge/releases/tag/v9.9.9",
        "assets": assets
    });
    let rel = parse_release_payload_value(&payload, "2.0.0", None).unwrap();
    assert!(rel.is_update_available);

    // Non-destructive staged PowerShell hot-swap script check
    let script = build_windows_hotswap_script(
        Path::new(r"C:\Users\O'Connor & Co\updates\KeyForge-Windows-x64.exe"),
        Path::new(r"C:\Users\O'Connor & Co\Programs\KeyForge\KeyForge.exe"),
        12345,
    )
    .unwrap();
    assert!(script.contains("$StageExe"));
    assert!(script.contains("Copy-Item -LiteralPath $NewBinary -Destination $StageExe"));
    assert!(!script.contains("Remove-Item -LiteralPath $TargetExe"));

    if cfg!(target_os = "windows") {
        let tmp = TempDir::new().unwrap();
        let app_dir = tmp.path().join("app");
        std::fs::create_dir_all(&app_dir).unwrap();
        let target_exe = app_dir.join("KeyForge.exe");
        let backup_exe = app_dir.join("KeyForge.exe.old");
        let new_bin = tmp.path().join("updates").join("KeyForge-Windows-x64.exe");
        std::fs::create_dir_all(new_bin.parent().unwrap()).unwrap();

        std::fs::write(&target_exe, b"MZ-ORIG").unwrap();
        std::fs::write(&backup_exe, b"MZ-STALE-OLD").unwrap();

        let ps_exe = resolve_powershell_executable();

        // Missing new binary must never delete TargetExe
        let sc_missing = build_windows_hotswap_script(&new_bin, &target_exe, 999_999)
            .unwrap()
            .replace(
                "Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir",
                "Write-Output 'LAUNCHED'",
            );
        let out_missing = Command::new(&ps_exe)
            .args(["-NoProfile", "-NonInteractive", "-Command", &sc_missing])
            .output()
            .unwrap();
        assert_eq!(out_missing.status.code(), Some(1));
        assert_eq!(std::fs::read(&target_exe).unwrap(), b"MZ-ORIG");

        // Happy path swaps cleanly and cleans .old / .new / NewBinary
        std::fs::write(&new_bin, b"MZ-NEW-V200").unwrap();
        let sc_ok = build_windows_hotswap_script(&new_bin, &target_exe, 999_999)
            .unwrap()
            .replace(
                "Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir",
                "Write-Output 'LAUNCHED'",
            );
        let out_ok = Command::new(&ps_exe)
            .args(["-NoProfile", "-NonInteractive", "-Command", &sc_ok])
            .output()
            .unwrap();
        assert!(out_ok.status.success());
        assert_eq!(std::fs::read(&target_exe).unwrap(), b"MZ-NEW-V200");
        assert!(!backup_exe.exists());
        assert!(!app_dir.join("KeyForge.exe.new").exists());
        assert!(!new_bin.exists());

        let cmd = build_windows_hotswap_command(&target_exe, &target_exe, 42).unwrap();
        assert!(cmd.iter().any(|a| a == "-EncodedCommand"));
    }
}

#[test]
fn test_installer_host_protection_and_cli_binary() {
    init_host_protection_snapshot();

    // Verify setup binary name detection
    assert!(is_running_as_setup_installer(Some(
        "C:\\Downloads\\KeyForge-Setup-Windows-x64.exe"
    )));
    assert!(!is_running_as_setup_installer(Some(
        "C:\\Apps\\KeyForge-Windows-x64.exe"
    )));

    // Verify custom dir install & uninstall does not disturb host paths
    let tmp = TempDir::new().unwrap();
    let custom_dir = tmp.path().join("custom_install");
    let src_exe = tmp.path().join("KeyForge-Setup-Windows-x64.exe");
    std::fs::write(&src_exe, b"MZ-BIN").unwrap();
    let manifest =
        install_keyforge(Some(&custom_dir), Some(&src_exe), false, false, false).unwrap();
    assert!(manifest.installed_exe.is_file());
    assert!(custom_dir.join("install_manifest.json").is_file());

    let res = uninstall_keyforge(Some(&custom_dir), false, false);
    assert!(!res.purged_user_data);
    assert!(!custom_dir.exists());

    // Host guards are active under test runner
    assert!(is_protected_host_install_dir(
        &keyforge::installer::get_default_install_dir()
    ));
    let (desk, start) = keyforge::installer::get_windows_shortcut_paths();
    assert!(is_protected_host_shortcut(&desk));
    assert!(is_protected_host_shortcut(&start));

    // Smoke-test compiled binary CLI flags
    let exe = env!("CARGO_BIN_EXE_keyforge");
    let v_out = Command::new(exe).arg("--version").output().unwrap();
    assert!(v_out.status.success());
    assert_eq!(
        String::from_utf8_lossy(&v_out.stdout).trim(),
        format!("KeyForge v{VERSION} \"{CODENAME}\"")
    );

    let wl_out = Command::new(exe).arg("--wayland-setup").output().unwrap();
    assert!(wl_out.status.success());
    assert!(String::from_utf8_lossy(&wl_out.stdout).contains("/dev/uinput"));

    let exported = tmp.path().join("exported.kfmacro.json");
    let exp_out = Command::new(exe)
        .args(["--export-default", &exported.to_string_lossy()])
        .output()
        .unwrap();
    assert!(exp_out.status.success());
    assert!(exported.is_file());

    let audit_out = Command::new(exe)
        .args(["--profile", &exported.to_string_lossy(), "--audit"])
        .output()
        .unwrap();
    assert!(audit_out.status.success());

    let dry_out = Command::new(exe)
        .args([
            "--expr",
            "Press E -> Wait 50ms -> Click -> Press 1",
            "--repeat",
            "2",
            "--dry-run",
        ])
        .output()
        .unwrap();
    assert!(dry_out.status.success());
    assert!(String::from_utf8_lossy(&dry_out.stdout).contains("cycles=2"));
}

#[cfg(target_os = "linux")]
#[test]
fn test_linux_wayland_uinput_mapping_and_fallback() {
    use keyforge::engine::backend::InputBackend;
    use keyforge::engine::platform::wayland_udev_setup_command;
    use keyforge::engine::platform_linux_wayland::{
        mouse_button_to_evdev, vk_to_linux_evdev_key, LinuxWaylandUinputBackend,
    };

    assert!(wayland_udev_setup_command().contains("99-keyforge-uinput.rules"));
    assert_eq!(vk_to_linux_evdev_key(0x1B), Some(1)); // Esc
    assert_eq!(vk_to_linux_evdev_key(0x45), Some(18)); // E
    assert_eq!(vk_to_linux_evdev_key(0x11), Some(29)); // LeftCtrl
    assert_eq!(vk_to_linux_evdev_key(0x75), Some(64)); // F6
    assert_eq!(vk_to_linux_evdev_key(0x7B), Some(88)); // F12
    assert_eq!(vk_to_linux_evdev_key(0x5B), Some(125)); // Super / Win
    assert_eq!(mouse_button_to_evdev(MouseButton::Left), 0x110);
    assert_eq!(mouse_button_to_evdev(MouseButton::Right), 0x111);

    // Must initialize safely without panicking even inside headless CI without /dev/uinput.
    let backend = LinuxWaylandUinputBackend::new();
    let e_key = resolve_single_key("E").unwrap();
    backend.key_down(e_key);
    backend.key_up(e_key);
    backend.move_cursor(120, 240, false);
    assert_eq!(backend.get_cursor_pos(), (120, 240));
    backend.scroll(0, -3);
    backend.close();
}
