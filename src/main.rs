#![cfg_attr(
    all(target_os = "windows", not(debug_assertions)),
    windows_subsystem = "windows"
)]

use std::path::PathBuf;
use std::sync::Arc;

use clap::Parser;

use keyforge::engine::backend::DryRunBackend;
use keyforge::engine::executor::{ExecutorState, MacroExecutor};
use keyforge::engine::platform::create_os_input_backend;
use keyforge::installer::{install_keyforge, is_running_as_setup_installer, uninstall_keyforge};
use keyforge::models::dsl::{format_actions_to_dsl, parse_quick_expression};
use keyforge::models::profile::{create_default_profile, MacroProfile, RepeatMode};
use keyforge::security::auditor::audit_profile;
use keyforge::security::integrity::{load_profile_secure, save_profile_secure};
use keyforge::ui::app::KeyForgeApp;
use keyforge::ui::installer_window::{InstallerMode, InstallerWindow};
use keyforge::VERSION;

#[derive(Parser, Debug)]
#[command(
    name = "keyforge",
    about = "KeyForge — A modern, secure, FOSS visual keyboard & mouse macro builder and auto-clicker for Windows, macOS, and Linux.",
    disable_version_flag = true
)]
struct Cli {
    /// Print version and exit.
    #[arg(short = 'v', long = "version")]
    version: bool,

    /// Run a Quick-Forge expression directly (e.g. 'Press E -> Wait 200ms -> Click -> Press 1').
    #[arg(long)]
    expr: Option<String>,

    /// Path to a .json / .kfmacro profile file to execute or audit.
    #[arg(long)]
    profile: Option<PathBuf>,

    /// Number of repeat cycles when running via --expr (default: 1).
    #[arg(long, default_value_t = 1)]
    repeat: i64,

    /// Simulate execution using DryRunBackend without sending real OS inputs.
    #[arg(long)]
    dry_run: bool,

    /// Run static security & operational safety audit on the specified profile or expression.
    #[arg(long)]
    audit: bool,

    /// Export the default signed starter profile to the given file path and exit.
    #[arg(long)]
    export_default: Option<PathBuf>,

    /// Launch the KeyForge Setup Installer GUI.
    #[arg(long)]
    setup: bool,

    /// Install KeyForge to the local user programs directory (supports --silent).
    #[arg(long)]
    install: bool,

    /// Uninstall KeyForge (preserves user configs/macros unless --purge-data is passed).
    #[arg(long)]
    uninstall: bool,

    /// Run --install or --uninstall headlessly without opening a wizard window.
    #[arg(long)]
    silent: bool,

    /// Remove saved configs, custom presets, and active profile during --uninstall.
    #[arg(long)]
    purge_data: bool,

    /// Skip creating a Desktop shortcut during --install.
    #[arg(long)]
    no_desktop_shortcut: bool,

    /// Optional custom installation directory for --install or --uninstall.
    #[arg(long)]
    install_dir: Option<PathBuf>,
}

fn attach_parent_console_if_needed(has_cli_args: bool) {
    if !has_cli_args {
        return;
    }
    #[cfg(target_os = "windows")]
    unsafe {
        use windows_sys::Win32::System::Console::{
            AttachConsole, GetStdHandle, ATTACH_PARENT_PROCESS, STD_OUTPUT_HANDLE,
        };
        let handle = GetStdHandle(STD_OUTPUT_HANDLE);
        if handle.is_null() {
            let _ = AttachConsole(ATTACH_PARENT_PROCESS);
        }
    }
}

fn run_cli_mode(cli: &Cli) -> i32 {
    let mut profile = if let Some(ref path) = cli.profile {
        match load_profile_secure(path, false) {
            Ok((loaded, sig_ok)) => {
                let sig_status = if sig_ok {
                    "SHA-256 Verified"
                } else {
                    "Unsigned (Schema Validated)"
                };
                println!("Loaded Profile: {} [{sig_status}]", loaded.name);
                loaded
            }
            Err(e) => {
                eprintln!("Error: {e}");
                return 1;
            }
        }
    } else if let Some(ref expr) = cli.expr {
        let steps = match parse_quick_expression(expr) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("Error: {e}");
                return 1;
            }
        };
        let mut prof = MacroProfile {
            name: "CLI Expression Macro".to_string(),
            description: expr.clone(),
            repeat_mode: RepeatMode::Count,
            repeat_count: cli.repeat.max(1),
            inter_cycle_delay_ms: 10,
            actions: steps,
            ..MacroProfile::default()
        };
        if let Err(e) = prof.validate() {
            eprintln!("Error: {e}");
            return 1;
        }
        prof
    } else {
        create_default_profile()
    };

    let report = audit_profile(&profile);
    println!("Security Audit: {}", report.badge_summary());
    for finding in &report.findings {
        println!(
            "  - [{}] ({}) {}: {}",
            finding.code,
            finding.severity.as_str().to_uppercase(),
            finding.title,
            finding.description
        );
    }

    if cli.audit {
        return i32::from(report.has_critical());
    }

    let backend = if cli.dry_run {
        Arc::new(DryRunBackend::default()) as Arc<dyn keyforge::engine::backend::InputBackend>
    } else {
        create_os_input_backend()
    };
    let time_scale = if cli.dry_run { 0.01 } else { 1.0 };
    let executor = MacroExecutor::new(Arc::clone(&backend), time_scale, None);

    println!("Executing: {}", format_actions_to_dsl(&profile.actions));
    let res = executor.run_blocking(profile.clone());
    backend.close();
    let _ = &mut profile;

    match res {
        Ok(telem) => {
            println!(
                "Completed: state={}, cycles={}, actions={}, elapsed={:.3}s",
                telem.state.as_str(),
                telem.cycles_completed,
                telem.actions_executed,
                telem.elapsed_seconds
            );
            i32::from(telem.state != ExecutorState::Idle)
        }
        Err(e) => {
            eprintln!("Error: {e}");
            1
        }
    }
}

fn main() {
    let has_args = std::env::args_os().nth(1).is_some();
    attach_parent_console_if_needed(has_args);

    let cli = Cli::parse();

    if cli.version {
        println!("KeyForge v{VERSION}");
        std::process::exit(0);
    }

    if let Some(ref export_path) = cli.export_default {
        let mut profile = create_default_profile();
        match save_profile_secure(&mut profile, export_path) {
            Ok(target) => {
                println!("Exported signed default profile to: {}", target.display());
                std::process::exit(0);
            }
            Err(e) => {
                eprintln!("Error: {e}");
                std::process::exit(1);
            }
        }
    }

    if cli.install && cli.silent {
        match install_keyforge(
            cli.install_dir.as_deref(),
            None,
            !cli.no_desktop_shortcut,
            true,
            true,
        ) {
            Ok(manifest) => {
                println!(
                    "Installed KeyForge v{VERSION} to: {}",
                    manifest.installed_exe.display()
                );
                std::process::exit(0);
            }
            Err(e) => {
                eprintln!("Error: {e}");
                std::process::exit(1);
            }
        }
    }

    if cli.uninstall && cli.silent {
        let res = uninstall_keyforge(cli.install_dir.as_deref(), cli.purge_data, true);
        println!(
            "Uninstalled KeyForge from: {} (purged_user_data={})",
            res.uninstalled_dir.display(),
            res.purged_user_data
        );
        std::process::exit(0);
    }

    if cli.uninstall || cli.install || cli.setup {
        let mode = if cli.uninstall {
            InstallerMode::Uninstall
        } else {
            InstallerMode::Setup
        };
        if let Err(e) = InstallerWindow::run(mode, cli.install_dir) {
            eprintln!("Installer UI Error: {e}");
            std::process::exit(1);
        }
        std::process::exit(0);
    }

    if cli.expr.is_some() || cli.profile.is_some() || cli.audit {
        std::process::exit(run_cli_mode(&cli));
    }

    if is_running_as_setup_installer(None) {
        if let Err(e) = InstallerWindow::run(InstallerMode::Setup, cli.install_dir) {
            eprintln!("Setup UI Error: {e}");
            std::process::exit(1);
        }
        std::process::exit(0);
    }

    if let Err(e) = KeyForgeApp::run() {
        eprintln!("KeyForge GUI Error: {e}");
        std::process::exit(1);
    }
}
