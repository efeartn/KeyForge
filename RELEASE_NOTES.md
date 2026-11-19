# KeyForge v1.6.0 — Native macOS & Linux Engine, Bug Fixes & Optimization

KeyForge `v1.6.0` brings **full native macOS & Linux input, global hotkey, live recording, and setup support** with zero new dependencies, alongside UI/UX bug fixes and lower idle memory & CPU usage (`~42.5 MB` Private RAM, `~0.78%` Idle CPU on Windows).

## What's New in v1.6.0

### Native macOS & Linux Engine (`keyforge/engine/platform_input.py`)
- **macOS CoreGraphics Engine (`MacOSInputBackend`)**: Native `CGEventPost` keyboard, mouse click/drag, absolute/relative move, non-variadic Apple Silicon-safe scroll (`CGEventCreateScrollWheelEvent2`), active modifier flags (`Cmd`/`Shift`/`Ctrl`/`Alt`), global `CGEventSourceKeyState` polling, cursor queries, and `AXIsProcessTrusted` Accessibility detection.
- **Linux X11/XTest Engine (`LinuxX11InputBackend`)**: Thread-safe `libX11` + `libXtst` injection, batch `XQueryKeymap` + `XQueryPointer` polling, XWayland detection, and graceful fallback on isolated pure-Wayland sessions with actionable UI guidance.
- **macOS & Linux Workflow Parity**:
  - Added `Cmd`/`Command` and `Option`/`Opt` aliases plus macOS Aqua modifier capture in `keymap.py`.
  - Enabled native `<Command-Z/Y/S/O/N/D/Return>` bindings and `⌘` status-bar hints on macOS.
  - Added POSIX installer support: `~/Applications/KeyForge` on macOS, `~/.local/share/KeyForge` + XDG `.desktop` launchers on Linux, automatic `0755` executable permissions, and POSIX launch after setup.
  - Added POSIX self-update temporary download cleanup and macOS `com.apple.quarantine` attribute clearing during hot-swap updates.

### UI Polish & Bug Fixes
- **Update Dialog Disabled Button Styling**: Fixed the `"Up to Date"` button inside `UpdateDialog` showing a vivid green disabled background — disabled actions now use subtle elevated slate (`#151D27`) with muted text and hairline borders.
- **Clean Step Subtitles & Read-Only Logs**: Prevented premature mid-word truncation on click steps with labels (e.g. `Primary Attack / Confirm • At cursor`) and locked the Security Audit and Event Console textboxes as read-only.
- **Timeline Filtering & Edit Safety**: Filtering steps no longer allows invisible deletion/toggling via keyboard shortcuts; loading presets, opening files, or creating a new profile resets stale filters cleanly; live active-step highlights keep card hover states synchronized; editing a step resolves by step ID safely.
- **Studio → Simple Bidirectional Sync & Hotkey Combo Recording**: Fixed Studio Mode repeat-count edits resetting when switching back to Simple Mode, and fixed `MacroRecorder` so combo hotkeys like `Ctrl+F6` never suppress recording normal shortcuts like `Ctrl+C`.
- **macOS Retina & Linux HiDPI Positioning**: Fixed centered dialogs and the floating `Mini HUD` shifting off-screen when OS display scaling differs from Tk geometry coordinates.

### Low-Spec & Binary Size Optimizations
- Adaptive global hotkey polling (`20ms` active / `50ms` idle — up to 60% fewer background wakeups) and adaptive UI hotkey queue polling (`50ms` active / `150ms` idle / `300ms` minimized).
- Downsized the cached window/brand icon bitmap to `64×64` RGBA in memory and skipped spawning empty polling threads when OS key polling is unavailable.
- Added safe unused stdlib exclusions and `Linux-arm64` naming support in `scripts/build_exe.py`.

## Downloads & Integrity Verification

Every release binary is compiled on clean GitHub Actions runners alongside individual `.sha256.txt` manifests and a combined `SHA256SUMS.txt` used automatically by KeyForge's in-app updater:

- **Windows Setup Installer (`x64`)**: `KeyForge-Setup-Windows-x64.exe` (includes Destination **Browse...**, Desktop/Start Menu shortcuts, and config-preserving Uninstaller)
- **Windows Portable (`x64`)**: `KeyForge-Windows-x64.exe`
- **macOS (`arm64`)**: `KeyForge-macOS-arm64`
- **Linux (`x86_64`)**: `KeyForge-Linux-x64`
- **Combined SHA-256 Manifest**: `SHA256SUMS.txt`

### Upgrading from Earlier Versions
Click **Updates** (or **Update Now** in the startup banner) inside KeyForge `v1.1.0–v1.5.0` to verify via `SHA-256` and upgrade in-place while preserving your settings, hotkeys, custom presets, and saved active macro.

**Full Changelog**: https://github.com/efeartn/KeyForge/compare/v1.5.0...v1.6.0
