# KeyForge v1.5.0 — Obsidian & Mint Emerald UI Overhaul

KeyForge `v1.5.0` introduces a cleaner desktop interface inspired by disciplined native hardware/network utilities while keeping the same ultra-lightweight footprint (~44.6 MB Private RAM idle on Windows, zero Chromium/WebView bloat) and preserving all existing configs, custom presets, and macro profiles across updates.

## What's New in v1.5.0

### UI Architecture & Visual Craft
- **Obsidian & Mint Emerald Design System**: Unified deep charcoal canvas (`#090C10`), elevated panel surfaces (`#0E1319`), crisp `1px` hairline borders (`#1B2430`), calm elevated slate segment switches, and a single high-contrast Mint Emerald primary CTA (`#2EE59D`) across Studio Mode, Simple Mode, Floating Mini-HUD, Step Editor, Software Update, and Setup Installer.
- **Zero-Clipping Header Bar**: Decluttered the top bar (`Simple | Studio`, `New`, `Open`, `Save`, `Updates`, `Mini HUD`, `Panic`) so `Panic (F12)` and header controls fit comfortably down to the `960x660` minimum window size.
- **4-Card Hero Control Strip (Studio Mode)**: Consolidated top-level controls into four balanced cards:
  - **Profile**: Preset selector, `+ Save Preset`, `Delete`, and clickable `Verified` security audit badge.
  - **Engine**: Live state, cycle/action telemetry, elapsed timer, `Live Input` / `Dry-Run` pill, and full-width bottom progress rail.
  - **Hotkeys**: Active `Run` / `Rec` / `Panic` bindings, live `XY` cursor coordinates, and quick `Dry-Run Simulation` toggle.
  - **Delay & Run**: `[0s | 1s | 3s | 5s]` segmented delay picker, Mint Emerald `Start Macro` CTA, and loop/cycle summary.
- **Straight-Column Timeline & Zero Truncation**:
  - Replaced bracketed `[label]` prefix clutter and duplicate `"Tap •"` strings with crisp step headlines (`Press [E]`, `Wait 75ms`) and inline soft-tinted category pills (`KEY`, `WAIT`, `CLICK`, `MOVE`, `SCROLL`, `LOOP`).
  - Fixed-width aligned metric pills, subtle center bridge lines, bordered `Edit` buttons, and quiet ghost row actions (`▲`, `▼`, `Dup`, `✕`) keep every timeline row aligned cleanly.
  - Removed the inset dark scroll well so step cards sit directly on the root canvas.
- **Category Filter Pills & Instant Search**: Filter steps one-click via `All Steps`, `Keys`, `Waits`, and `Mouse`, or press `/` anywhere outside inputs to jump directly to the step filter box.

### Bug Fixes & Low-Spec Optimization
- Eliminated the stray `0.0` pixel dot on idle `CTkProgressBar` widgets in both Studio Engine card and `UpdateDialog`.
- Updated Setup Installer checkboxes, `MiniHudWindow`, `StepEditorDialog`, and `Loop & Keys` spacing so all inspector controls remain visible at `960x660`.
- Maintained low resource consumption with the warm 64-slot pooled step card renderer, dirty-checked `_ui_tick`, bounded 200-line console log, cached app icon image, and lock-free hotkey polling.

## Downloads & Integrity Verification

Every release binary is compiled on clean GitHub Actions runners alongside individual `.sha256.txt` manifests and a combined `SHA256SUMS.txt` used automatically by KeyForge's in-app updater:

- **Windows Setup Installer (`x64`)**: `KeyForge-Setup-Windows-x64.exe` (includes Destination **Browse...**, Desktop/Start Menu shortcuts, and config-preserving Uninstaller)
- **Windows Portable (`x64`)**: `KeyForge-Windows-x64.exe`
- **macOS (`arm64`)**: `KeyForge-macOS-arm64`
- **Linux (`x86_64`)**: `KeyForge-Linux-x64`
- **Combined SHA-256 Manifest**: `SHA256SUMS.txt`

### Upgrading from Earlier Versions
Click **Updates** (or **Update Now** in the startup banner) inside KeyForge v1.1.0–v1.4.0 to verify via `SHA-256` and upgrade in-place while preserving your `%APPDATA%\KeyForge` settings, hotkeys, custom presets, and saved active macro.

**Full Changelog**: https://github.com/efeartn/KeyForge/compare/v1.4.0...v1.5.0
