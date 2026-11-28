# KeyForge v2.1.0 "Waystone" — Minimalist Craft Icon, Native Linux Wayland Support & UI Polish

KeyForge `v2.1.0 "Waystone"` refines the native Rust desktop experience with a brand-new razor-sharp geometric minimalist icon, full **native Linux Wayland** kernel `/dev/uinput` + `evdev` input & hotkey support, synchronized release codenames across every version, cleaner Obsidian/Emerald UI interactions, Windows CLI console fixes, and Node.js 24 GitHub Actions runners.

## What's New in v2.1.0 "Waystone"

### Native Linux Wayland Support (`src/engine/platform_linux_wayland.rs`, `src/engine/platform.rs`)
- **Kernel `/dev/uinput` Injection (`LinuxWaylandUinputBackend`)**: Injects live keyboard presses, holds, releases, relative/absolute cursor movements, mouse clicks, and horizontal/vertical wheel scrolling natively across pure Wayland compositors (GNOME, KDE Plasma, Sway, Hyprland) via zero-dependency `libc` FFI — no external `ydotool`/`wtype` daemons needed.
- **Native Wayland Global Hotkeys & Self-Filter Polling (`evdev`)**: Queries `/dev/input/event*` via `EVIOCGKEY` bitmasks so `F6` Start/Stop, `F7` Live Recorder, and `F12` Panic Stop work globally on Wayland while automatically ignoring KeyForge's own `"KeyForge Virtual ..."` devices.
- **Smart Hybrid Routing & One-Line Setup (`--wayland-setup`)**: Automatically uses `/dev/uinput` on Wayland when accessible, falls back seamlessly to `XWayland`/`X11`, and surfaces both `keyforge --wayland-setup` in the CLI and a one-click **Copy Setup Cmd** banner button in the GUI when `/dev/uinput` udev permissions need configuration.

### Minimalist Geometric App Icon (`assets/icon.png`, `assets/icon.ico`)
- Replaced the previous raster bevel icon with a crisp, flat architectural Obsidian + Mint Emerald `K` monogram keycap rendered at `2048×2048` supersampled precision (`512×512` PNG + multi-resolution `16–256` ICO), eliminating visual clutter and improving legibility at taskbar/tray scales.

### UI Craft Improvements & Bugfixes (`src/ui/`, `src/main.rs`)
- **Ghost Controls & Timeline Card Hover Feedback**: Replaced bordered utility buttons (`+ Save Preset`, `Delete`, banner `Dismiss`, toast `✕`, and timeline row arrows/`Dup`/`✕`) with clean borderless `ghost_button` controls and smooth row hover highlights (`BG_CARD_HOVER` + `ACCENT_PRIMARY` active step ring).
- **Top Bar Codename & Backend Badges**: Displays `v2.1.0 · Waystone` alongside the live native engine badge (`WIN32`, `MACOS`, `WAYLAND`, `XWAYLAND`, or `X11`).
- **Simple Mode Preset Highlighting & Synchronized Codenames**: Highlights the active speed preset pill (`25ms–500ms`) in Mint Emerald and shows release codenames cleanly in the Setup Wizard and Update Dialog.
- **Mini-HUD Auto-Restore & Lower Idle Footprint**: Restoring the main window automatically syncs out of Mini-HUD mode, and adaptive idle repaints (`1000ms` idle / `120ms` HUD / `50ms` active) trim the standalone Windows x64 binary to **5.57 MB** (`5,841,408` bytes), **`0.032%` idle CPU**, and **`89.48 MB` private RAM** (`91.1 MB` working set).
- **Windows CLI Console Fix & Synced Release Codenames**: Fixed parent-console attachment when `STD_OUTPUT_HANDLE` returns `INVALID_HANDLE_VALUE`, fixed README Windows `.exe.sha256.txt` links, synced release titles cleanly (`v1.0.0 "Ignition"` through `v2.1.0 "Waystone"`), and upgraded all GitHub Actions workflows to Node.js 24 (`checkout@v5`, `cache@v5`, `upload-artifact@v6`, `download-artifact@v7`, `action-gh-release@v3`).

## Downloads & Integrity Verification

Every release binary is compiled cleanly on GitHub Actions runners (`windows-latest`, `macos-latest`, `ubuntu-latest`) alongside individual `.sha256.txt` manifests and a combined `SHA256SUMS.txt`:

- **Windows Setup Installer (`x64`)**: `KeyForge-Setup-Windows-x64.exe` (includes Destination **Browse...**, Desktop/Start Menu shortcuts, and config-preserving Uninstaller)
- **Windows Portable (`x64`)**: `KeyForge-Windows-x64.exe`
- **macOS (`arm64`)**: `KeyForge-macOS-arm64`
- **Linux (`x86_64`)**: `KeyForge-Linux-x64`
- **Combined SHA-256 Manifest**: `SHA256SUMS.txt`

**Full Changelog**: https://github.com/efeartn/KeyForge/compare/v2.0.0...v2.1.0
