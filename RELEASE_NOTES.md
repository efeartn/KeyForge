# KeyForge v2.0.0 — Complete Pure Native Rust Rewrite

KeyForge `v2.0.0` marks a ground-up migration from Python/CustomTkinter to **100% pure native Rust**, delivering a tiny single-file native binary, ultra-low idle CPU overhead, zero external runtime dependencies, and GPU-accelerated `eframe` / `egui` (`glow`) rendering — while maintaining **100% backward compatibility** with existing `v1.x` `.kfmacro.json` SHA-256 profiles, custom presets, and `%APPDATA%\KeyForge\config.json` settings.

## What's New in v2.0.0

### 100% Pure Native Rust Architecture (`Cargo.toml` · `src/`)
- **Tiny Single Binary & Near-Zero Idle CPU**: Compiles cleanly to a **5.84 MB** (`6,124,032` bytes) standalone Windows x64 executable with zero Python interpreter, zero Chromium/WebView subprocesses, instant startup, and **0.065% average idle CPU usage** on Windows (`142.13 MB` private OpenGL WGL context memory footprint).
- **GPU-Accelerated Obsidian & Mint Emerald UI (`src/ui/`)**: Full fidelity port of **Simple Mode**, **Studio Mode**, the **4-Card Hero Control Strip**, filterable **Action Sequence Timeline** (`/` filter, `Del`, `Ctrl+D`, `Space`, `Alt+Up/Down`), **Live Sequence DSL** bar, floating **Mini HUD**, **Precision Step Inspector**, **Setup Installer Wizard**, and **Software Update Sheet** rendered natively via `eframe 0.29` / `egui` (`glow`).
- **Native OS Input & Hotkey Backends (`src/engine/`)**:
  - **Windows (`platform_win32.rs`)**: Direct 64-bit `Win32` `SendInput`, cursor telemetry, and `GetAsyncKeyState` global hotkeys with `0x4B465247` self-filter markers.
  - **macOS (`platform_macos.rs`)**: Direct `CoreGraphics` `CGEventPost` / `CGEventCreateScrollWheelEvent2`, `CGEventSourceKeyState`, `AXIsProcessTrusted` Accessibility detection, and Apple Silicon/Intel support.
  - **Linux (`platform_linux.rs`)**: Dynamic `libloading` FFI bindings for `libX11` + `libXtst` with `XQueryKeymap`, `XQueryPointer`, and safe Dry-Run fallback on isolated pure-Wayland sessions.
- **Zero-Allocation Execution & Hardware Panic Kill-Switch**: Interruptible wait slices (`EmergencyInputSanitizer` auto-release ledger) guarantee no modifier key or mouse button remains stuck on `F12` panic abort.

### Full `v1.x` Security, Config & Updater Parity
- **Canonical `SHA-256` Golden Vector Compatibility (`src/security/integrity.rs`)**: Preserves exact `v1.x` compact canonical JSON hashing and constant-time verification (`subtle::ConstantTimeEq`), verified directly against captured `v1.6.1` golden digests.
- **Hardened Input & Complexity Validation (`src/security/validator.rs`, `auditor.rs`)**: Rejects `NaN`/`Infinity`, boolean-as-integer spoofing, path traversal, NTFS ADS (`:`), and Windows reserved device names while enforcing `MAX_PROFILE_FILE_BYTES`, `MAX_TOTAL_STEPS`, `MAX_NESTING_DEPTH`, `MAX_EXPANDED_OPS_PER_CYCLE`, `5ms` anti-flood minimum cycle delay, and `KF-SEC-001..005` static audit checks.
- **Non-Destructive Updater & Protected Setup Installer (`src/updater.rs`, `src/installer.rs`)**:
  - Enforces HTTPS-only GitHub redirects, verifies downloads via `SHA256SUMS.txt` / `.sha256.txt`, and executes non-destructive PowerShell staged replacement on Windows (keeping `config.json`, `presets/`, and `active_profile.kfmacro.json` untouched).
  - Test-runner host path guards guarantee cargo/CI tests never touch host Desktop/Start Menu shortcuts, Uninstall registry entries, or user installations.

## Downloads & Integrity Verification

Every release binary is compiled on clean GitHub Actions runners (`windows-latest`, `macos-latest`, `ubuntu-latest`) after passing `cargo fmt --all -- --check`, `cargo clippy --all-targets -- -D warnings`, and `cargo test --all-targets`, alongside individual `.sha256.txt` manifests and a combined `SHA256SUMS.txt`:

- **Windows Setup Installer (`x64`)**: `KeyForge-Setup-Windows-x64.exe` (includes Destination **Browse...**, Desktop/Start Menu shortcuts, and config-preserving Uninstaller)
- **Windows Portable (`x64`)**: `KeyForge-Windows-x64.exe`
- **macOS (`arm64`)**: `KeyForge-macOS-arm64`
- **Linux (`x86_64`)**: `KeyForge-Linux-x64`
- **Combined SHA-256 Manifest**: `SHA256SUMS.txt`

### Upgrading from `v1.x`
Click **Updates** inside KeyForge `v1.1.0–v1.6.1` (or run `KeyForge-Setup-Windows-x64.exe --install --silent`) to verify via `SHA-256` and upgrade cleanly to the pure Rust `v2.0.0` binary while preserving all your existing hotkeys, custom presets, settings, and active macro profile.

**Full Changelog**: https://github.com/efeartn/KeyForge/compare/v1.6.1...v2.0.0
