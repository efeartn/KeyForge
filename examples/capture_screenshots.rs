use std::path::PathBuf;

fn main() -> eframe::Result<()> {
    let out_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("docs")
        .join("screenshots");

    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--installer") {
        if let Some(appdata) = std::env::var_os("APPDATA") {
            std::env::set_var("KTAP_CONFIG_DIR", PathBuf::from(appdata).join("kTap"));
        }
        return ktap::ui::installer_window::InstallerWindow::capture_screenshot(
            out_dir.join("setup_installer.png"),
        );
    }

    let tmp = tempfile::TempDir::new().expect("create temp config dir");
    std::env::set_var("KTAP_TEST_ISOLATE", "1");
    std::env::set_var("KTAP_CONFIG_DIR", tmp.path());

    ktap::ui::app::KTapApp::capture_screenshots(out_dir)?;

    let exe = std::env::current_exe().expect("resolve current exe");
    let status = std::process::Command::new(exe)
        .arg("--installer")
        .status()
        .expect("spawn installer screenshot subprocess");
    assert!(status.success(), "installer screenshot capture failed");
    Ok(())
}
