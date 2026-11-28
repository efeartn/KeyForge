# kTap v3.0.0 "Pulse" — Complete kTap Rebrand, Hot-Path Optimizations & UI Craft Polish

kTap `v3.0.0 "Pulse"` evolves KeyForge into **kTap** across the binary, crate, installer, cryptographic profile format, Linux Wayland udev integration, and desktop studio UI — while delivering hot-path execution and `evdev` optimizations, zero-loss automatic migration from legacy KeyForge configs, and cleaner Studio ergonomics.

## What's New in v3.0.0 "Pulse"

### Brand Evolution to kTap (`Cargo.toml`, `build.rs`, `.github/workflows/ci.yml`)
- **Unified kTap Binary & Release Assets**: Compiles as `ktap` (`kTap.exe` on installed Windows systems) with cross-platform release downloads `kTap-Setup-Windows-x64.exe`, `kTap-Windows-x64.exe`, `kTap-macOS-arm64`, and `kTap-Linux-x64`.
- **kTap Profiles, Audit Codes & Markers**: Introduces signed `.ktap.json` (`ktap_format: "ktap-v1"`) envelopes, `KT-SEC-001..005` security auditor findings, `0x4B544150` (`"KTAP"`) Win32/macOS synthetic markers, `"kTap Virtual ..."` Wayland devices, `/etc/udev/rules.d/99-ktap-uinput.rules`, and `efeartn/kTap` updater endpoints.

### Automatic Legacy KeyForge Migration & Compatibility (`src/config.rs`, `src/security/integrity.rs`, `src/installer.rs`)
- **Non-Destructive Auto-Migration (`migrate_legacy_dir_to`)**: On first launch when `%APPDATA%\kTap` (or `~/Library/Application Support/kTap` / `~/.config/kTap`) does not yet exist, kTap automatically migrates `config.json`, `active_profile.kfmacro.json` (promoted to `active_profile.ktap.json`), and custom `presets/` from the legacy KeyForge directory without modifying legacy data.
- **100% Legacy Profile Verification**: Continues verifying and loading `.kfmacro.json` (`kfmacro-v1`) signed envelopes alongside `.ktap.json` (`ktap-v1`), and protects both `kTap` and legacy `KeyForge` host install paths under test runners.

### Performance & Engine Optimizations (`src/engine/`, `src/ui/app.rs`)
- **Pre-Compiled Macro Execution (`compile_actions` in `executor.rs`)**: Resolves key combos once per run into `CompiledStep` execution plans before entering the cycle loop, eliminating repeated key parsing on every iteration.
- **Cached Linux Wayland `evdev` Discovery (`evdev_devices_cached` in `platform_linux_wayland.rs`)**: Caches `/dev/input/event*` device enumeration with a 5-second TTL instead of rescanning sysfs/devtmpfs on every hotkey poll tick.
- **O(1) Cached Studio UI Metrics & Zero-Alloc Filtering (`app.rs`)**: Tracks `cached_enabled_count` and `cached_cycle_wait_ms` across sequence mutations and avoids allocating filtered index vectors when the search box is empty.
- **Compact Windows Footprint**: **5.58 MB** (`5,847,552` bytes) standalone executable, **`0%` measured idle CPU** over a 3s sample, and **`143.16 MB` private RAM** (`149.43 MB` working set).

### UI Improvements & Bugfixes (`src/ui/app.rs`)
- **Filter Clear & Keyboard Flow**: Added a `✕` clear control inside the Studio filter bar plus `Esc` handling when focused to reset search filters in one keystroke.
- **Disabled-Aware Batch & History Controls**: `Undo` and `Redo` show available history counts on hover and disable cleanly when empty; `2×`, `0.5×`, `All On`, `All Off`, and `Flip` guard against empty sequences with descriptive tooltips; `Mini HUD` and `Panic` header controls include shortcut tooltips.
- **Settings Persistence on Repeat Mode & Presets**: Selecting `Fixed Count` / `Infinite` / `Duration` tabs or applying presets now persists configuration cleanly alongside rounded `RepeatMode` segmented controls.

## Downloads & Integrity Verification

Every release binary is compiled cleanly on GitHub Actions runners (`windows-latest`, `macos-latest`, `ubuntu-latest`) alongside individual `.sha256.txt` manifests and a combined `SHA256SUMS.txt`:

- **Windows Setup Installer (`x64`)**: `kTap-Setup-Windows-x64.exe` (includes Destination **Browse...**, Desktop/Start Menu shortcuts, and config-preserving Uninstaller)
- **Windows Portable (`x64`)**: `kTap-Windows-x64.exe`
- **macOS (`arm64`)**: `kTap-macOS-arm64`
- **Linux (`x86_64`)**: `kTap-Linux-x64`
- **Combined SHA-256 Manifest**: `SHA256SUMS.txt`

**Full Changelog**: https://github.com/efeartn/kTap/compare/v2.1.0...v3.0.0
