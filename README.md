<div align="center">

<img src="assets/icon.png" alt="kTap Logo" width="108" height="108" />

# kTap

**Minimalist Native Rust Keyboard & Macro Sequence Studio and Live Input Recorder.**  
Free, Open-Source (`GPL-3.0-or-later`), Cybersecurity-Hardened, Obsidian & Mint Emerald GUI, and Native for Windows, macOS & Linux — with Zero Python Runtime or WebView Bloat.

[![CI & Release Compiler](https://github.com/efeartn/kTap/actions/workflows/ci.yml/badge.svg)](https://github.com/efeartn/kTap/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/efeartn/kTap?color=2EE59D&label=Release)](https://github.com/efeartn/kTap/releases/latest)
[![Platform: Windows | macOS | Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0EA5E9)](https://github.com/efeartn/kTap/releases/latest)
[![Built With: Pure Native Rust](https://img.shields.io/badge/Rust-Pure%20Native%20eframe%2Fegui-F97316)](Cargo.toml)
[![Security: SHA-256 Signed + Static Auditor](https://img.shields.io/badge/Security-SHA--256%20Verified%20%2B%20Static%20Auditor-10B981)](SECURITY.md)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-8B5CF6.svg)](LICENSE)

[![Download 1.0.0](https://img.shields.io/badge/Version-1.0.0-2EE59D)](#download-standalone-executables) • [**Release Notes**](RELEASE_NOTES.md) • [**Visual Tour**](#visual-tour) • [**Cross-Platform Engine**](#cross-platform-support-windows--macos--linux) • [**Setup & Updater**](#setup-installer--auto-updater) • [**Sequence DSL**](#sequence-dsl-reference) • [**Cybersecurity**](#cybersecurity--safety-engineering) • [**CLI & Source**](#installation--quick-start)

</div>

---

## Studio Workspace at a Glance

<div align="center">
  <img src="docs/screenshots/studio_mode.png" alt="kTap 1.0.0 Studio Mode Desktop Interface" width="100%" />
  <br />
  <sub><b>kTap 1.0.0 Studio Mode</b> — Minimalist Obsidian &amp; Mint Emerald workspace featuring the <b>4-Card Hero Control Strip</b> (Profile, Engine Telemetry, Hotkeys &amp; Dry-Run, Start Delay &amp; CTA), <b>All Steps / Keys / Waits / Mouse</b> filter pills with <code>/</code> quick search &amp; <code>Esc</code> / <code>×</code> clear, disabled-aware batch toolbar, one-line <b>DSL</b> editor, step cards with keyboard shortcuts (<code>Del</code> / <code>Ctrl+D</code> / <code>Space</code> / <code>Alt+Up/Dn</code>), and right-hand <b>Loop / Recorder / Security / Console</b> inspector.</sub>
</div>

---

## Visual Tour

### Dual Workspaces: `Simple` & `Studio`

kTap adapts cleanly from everyday key repeating to full multi-step macro engineering without forcing complex timelines onto simple tasks — styled in deep charcoal-obsidian (`#090C10` / `#0E1319`), hairline `1px` borders, and a restrained Mint Emerald primary CTA (`#2EE59D`).

<table>
  <tr>
    <td width="58%" valign="top">
      <img src="docs/screenshots/simple_mode.png" alt="kTap 1.0.0 Simple Mode Key Repeater and Control Center" width="100%" />
    </td>
    <td width="42%" valign="top">
      <h3>Everyday Keyboard Automation</h3>
      <ul>
        <li><b>Two Synchronized Modes:</b> Switch seamlessly between <b>Simple</b> (Key Repeater &amp; Quick Sequence) and <b>Studio</b> (4-card hero strip + timeline) with live bidirectional profile, timing, and repeat-mode sync.</li>
        <li><b>Click-to-Bind Key Capture &amp; Speed Presets:</b> Click <b>Bind Key</b> to capture any physical key or modifier combo (e.g. <code>Ctrl+Shift+F</code> or <code>Cmd+C</code>), pick highlighted speed presets (<code>25ms–500ms</code>), or toggle humanized timing variance (<code>±10%</code>).</li>
        <li><b>Fast Step Filtering &amp; Shortcuts:</b> Filter by <b>Keys</b>, <b>Waits</b>, or <b>Mouse</b>, press <code>/</code> to search (<code>Esc</code> or <code>×</code> to clear), and edit visible rows safely with <code>Del</code>, <code>Ctrl+D</code> / <code>⌘D</code>, <code>Space</code>, and <code>Alt+Up/Dn</code>.</li>
        <li><b>Native Rust Performance:</b> Compiled 100% in native Rust (<code>eframe</code> / <code>egui</code> + <code>glow</code>) with pre-compiled execution plans, cached metrics, zero Python or WebView bloat, and a <b>5.58 MB</b> standalone Windows x64 executable (<code>5,848,576</code> bytes).</li>
      </ul>
    </td>
  </tr>
</table>

### Floating Mini-HUD & Setup Installer Wizard

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <br /><br />
      <img src="docs/screenshots/mini_hud.png" alt="kTap 1.0.0 Always-on-Top Compact Floating Mini-HUD Pill" width="90%" />
      <br /><br />
      <p align="left">
        <b>Compact Always-on-Top Mini-HUD</b><br />
        Click <b>Mini HUD</b> in the top bar to collapse kTap into a slim floating control pill that stays above games or desktop apps on Windows, macOS, and Linux with live cycle telemetry, Start/Stop (<code>F6</code>), Panic (<code>F12</code>), and one-click <b>Expand</b>.
      </p>
    </td>
    <td width="50%" valign="top" align="center">
      <img src="docs/screenshots/setup_installer.png" alt="kTap 1.0.0 Setup Installer Wizard" width="90%" />
      <p align="left">
        <b>Cross-Platform Setup Installer &amp; Uninstaller</b><br />
        Install per-user without admin elevation (<code>%LOCALAPPDATA%\Programs\kTap</code> on Windows, <code>~/Applications/kTap</code> on macOS, <code>~/.local/share/kTap</code> on Linux) with a native <b>Browse...</b> folder picker, Desktop/Start Menu shortcuts or <code>.desktop</code> launchers, and a config-preserving Uninstaller.
      </p>
    </td>
  </tr>
</table>

### Modal Step Inspector & Cryptographic In-App Updater

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <img src="docs/screenshots/step_inspector.png" alt="kTap 1.0.0 Modal Action Step Editor" width="94%" />
      <p align="left">
        <b>Precision Step Inspector</b><br />
        Double-click any step (or click <code>Edit</code>) to configure key presses, waits with jitter, mouse clicks/moves/scrolls, or nested <code>Repeat</code> blocks, with direct <b>Bind Key</b> capture and live screen <b>Pick XY</b> coordinate picking.
      </p>
    </td>
    <td width="50%" valign="top" align="center">
      <img src="docs/screenshots/updater_modal.png" alt="kTap 1.0.0 SHA-256 Verified Software Update Sheet" width="94%" />
      <p align="left">
        <b>Startup Check &amp; SHA-256 Auto-Updater</b><br />
        Checks GitHub Releases over HTTPS on launch, displays release notes and SHA-256 status, verifies downloads against <code>SHA256SUMS.txt</code> with constant-time <code>subtle::ConstantTimeEq</code>, and applies non-destructive staged upgrades while preserving every config and macro.
      </p>
    </td>
  </tr>
</table>

### Built-In Static Security Audit & Integrity Verification

<div align="center">
  <img src="docs/screenshots/security_audit.png" alt="kTap 1.0.0 Security Inspector Tab and SHA-256 Digest" width="96%" />
  <br />
  <sub><b>Live Security Inspector Tab</b> — Displays the active profile's canonical <code>SHA-256</code> integrity digest, complexity budget counters, and real-time <code>KT-SEC-001..005</code> static safety checks.</sub>
</div>

---

## Download Standalone Executables

Every release is compiled with Cargo `--release --locked`, verified with `rustfmt`, `clippy -D warnings`, and cross-platform integration tests on [GitHub Actions](https://github.com/efeartn/kTap/actions), and published with individual checksums plus a combined [`SHA256SUMS.txt`](https://github.com/efeartn/kTap/releases/latest/download/SHA256SUMS.txt):

| Operating System | Variant | Standalone Binary | Checksum Manifest |
| :--- | :--- | :--- | :--- |
| **Windows 10 / 11** (`x64`) | **Setup Installer** (Shortcuts + Uninstaller) | [**`kTap-Setup-Windows-x64.exe`**](https://github.com/efeartn/kTap/releases/latest/download/kTap-Setup-Windows-x64.exe) | [`kTap-Setup-Windows-x64.exe.sha256.txt`](https://github.com/efeartn/kTap/releases/latest/download/kTap-Setup-Windows-x64.exe.sha256.txt) |
| **Windows 10 / 11** (`x64`) | **Portable Single-File** | [**`kTap-Windows-x64.exe`**](https://github.com/efeartn/kTap/releases/latest/download/kTap-Windows-x64.exe) | [`kTap-Windows-x64.exe.sha256.txt`](https://github.com/efeartn/kTap/releases/latest/download/kTap-Windows-x64.exe.sha256.txt) |
| **macOS** (`arm64`) | **Apple Silicon Standalone** | [**`kTap-macOS-arm64`**](https://github.com/efeartn/kTap/releases/latest/download/kTap-macOS-arm64) | [`kTap-macOS-arm64.sha256.txt`](https://github.com/efeartn/kTap/releases/latest/download/kTap-macOS-arm64.sha256.txt) |
| **Linux** (`x86_64`) | **Standalone ELF Binary** | [**`kTap-Linux-x64`**](https://github.com/efeartn/kTap/releases/latest/download/kTap-Linux-x64) | [`kTap-Linux-x64.sha256.txt`](https://github.com/efeartn/kTap/releases/latest/download/kTap-Linux-x64.sha256.txt) |

<details>
<summary><b>Verify Binary Checksum Before Running (Optional)</b></summary>

```powershell
# Windows (PowerShell)
Get-FileHash .\kTap-Setup-Windows-x64.exe -Algorithm SHA256
Get-FileHash .\kTap-Windows-x64.exe -Algorithm SHA256
```

```bash
# macOS
shasum -a 256 kTap-macOS-arm64 && chmod +x kTap-macOS-arm64

# Linux
sha256sum -c kTap-Linux-x64.sha256.txt && chmod +x kTap-Linux-x64
```

</details>

---

## Cross-Platform Support (Windows · macOS · Linux)

kTap injects live keyboard and mouse events, polls global hotkeys, and captures live input natively on every desktop OS directly via Rust FFI (`windows-sys`, `CoreGraphics` / `ApplicationServices`, Linux `/dev/uinput` + `evdev`, and `libX11` / `libXtst`):

| Platform | Native Input & Hotkey Engine | Permissions & Notes |
| :--- | :--- | :--- |
| **Windows 10 / 11** | `Win32InputBackend` (`src/engine/platform_win32.rs`: `SendInput`, `GetAsyncKeyState`) | Works immediately out of the box with `0x4B544150` (`"KTAP"`) self-filter signature |
| **macOS (Apple Silicon / Intel)** | `MacOSInputBackend` (`src/engine/platform_macos.rs`: `CGEventPost`, non-variadic scroll, `CGEventSourceKeyState`) | Grant **Accessibility** permission in *System Settings → Privacy & Security → Accessibility* for live injection & global hotkeys; supports `Cmd` / `Option` aliases and `⌘` shortcuts |
| **Linux (`x86_64` / `arm64`) — Wayland & X11** | `LinuxWaylandUinputBackend` (`src/engine/platform_linux_wayland.rs`: `/dev/uinput` + cached `evdev` polling) + `LinuxX11InputBackend` (`src/engine/platform_linux.rs`: `libX11` + `libXtst`) | Native injection & global hotkeys on Wayland compositors (GNOME, KDE, Sway, Hyprland) via `/dev/uinput` with automatic `XWayland` / `X11` fallback. Run `ktap --wayland-setup` (or click **Copy Setup Cmd** in-app) to grant `/dev/uinput` access without running as root. |

---

## Setup Installer & Auto-Updater

### Safe Setup & Uninstaller (Preserves User Data by Default)
- **Zero-Elevation Per-User Install**: Installs cleanly into `%LOCALAPPDATA%\Programs\kTap\kTap.exe` on Windows, `~/Applications/kTap/kTap` on macOS, or `~/.local/share/kTap/kTap` on Linux with a native folder picker (**Browse...**).
- **Native Shortcuts & Registration**: Creates optional Desktop shortcuts and Windows Start Menu / Uninstall registry entries or Linux `.desktop` Application Menu launchers, setting `0755` permissions on POSIX.
- **Uninstall Without Data Loss**: Removing kTap deletes binaries and shortcuts while keeping your config, custom presets, and active macro profile intact unless `--purge-data` is passed.
- **Silent CLI Support**: Supports headless `--install --silent` and `--uninstall --silent [--purge-data]`.

### Verified Auto-Updater & Atomic Config Storage
- **HTTPS + SHA-256 Enforcement**: Rejects insecure HTTP redirects, verifies downloads in constant time (`subtle::ConstantTimeEq`) against `SHA256SUMS.txt` / `.sha256.txt`, and executes non-destructive staged hot-swap replacement (`$TargetExe.new` -> `.old` backup -> atomic promote).
- **Crash-Safe Config & Presets**: Atomic temp-write with `.bak` recovery (overridable via `KTAP_CONFIG_DIR`). If you previously used KeyForge, kTap automatically migrates existing configs and continues verifying legacy `.kfmacro.json` files alongside `.ktap.json`.

| Platform | Configuration Directory | Stored Files |
| :--- | :--- | :--- |
| **Windows** | `%APPDATA%\kTap` | `config.json`, `active_profile.ktap.json` (+ `.bak`), `presets/` |
| **macOS** | `~/Library/Application Support/kTap` | `config.json`, `active_profile.ktap.json` (+ `.bak`), `presets/` |
| **Linux / BSD** | `$XDG_CONFIG_HOME/kTap` (or `~/.config/kTap`) | `config.json`, `active_profile.ktap.json` (+ `.bak`), `presets/` |

---

## Why kTap?

| Feature / Guarantee | **kTap (`GPL-3.0`)** | **TinyTask** | **AutoHotkey** | **Paid Macro Tools** |
| :--- | :---: | :---: | :---: | :---: |
| **100% Free, Native Rust & Open Source** | ✅ Yes (`5.58 MB` single binary) | ❌ Closed binary | ✅ Yes | ❌ Paywalls / Bloat |
| **Simple Key Repeater + Visual Studio** | ✅ Dual UI Modes | ❌ Record-only blob | ❌ Scripts only | ⚠️ Often split tiers |
| **One-Line Sequence DSL Bar** | ✅ `Press E -> Wait 200ms -> Click` | ❌ No | ❌ Manual script | ❌ No |
| **Humanized Random Jitter (`±ms`)** | ✅ Built into Wait steps | ❌ Fixed delays | ⚠️ Manual `Random()` | ❌ Rare |
| **Auto-Release Stuck Keys on Abort** | ✅ `EmergencyInputSanitizer` | ❌ Leaves Shift/Ctrl stuck | ❌ Manual cleanup | ⚠️ Unreliable |
| **SHA-256 Signed Profiles + Static Auditor** | ✅ Yes (`KT-SEC-001..005`) | ❌ Unverified binary blobs | ❌ Arbitrary execution | ❌ Closed format |
| **In-App Verified Updater + Config Sync** | ✅ HTTPS + SHA-256 | ❌ None | ⚠️ External installer | ⚠️ Telemetry-heavy |
| **Cross-Platform Native Binaries** | ✅ Windows, macOS & Linux | ⚠️ Windows only | ⚠️ Windows only | ⚠️ Windows only |

---

## Sequence DSL Reference

Type or paste human-readable macro recipes directly into the **DSL** bar and press `Enter` (or click **Replace** / **+ Append**):

| Action | Sequence DSL Syntax Examples | Description |
| :--- | :--- | :--- |
| **Key Tap** | `Press E` • `Press Space` • `Press 1 (hold 40ms)` | Presses and releases a key with configurable hold time |
| **Modifier Combo** | `Press Ctrl+Shift+E` • `Press Alt+F` | Holds modifiers in order, taps primary key, releases in reverse |
| **Key Hold / Release** | `KeyDown Shift` • `KeyUp Shift` | Explicit key state transitions (auto-sanitized on stop) |
| **Mouse Click** | `Click` • `Click Right` • `DoubleClick Left at (640, 480)` | Single, double, down, or up click at live cursor or fixed `(X, Y)` |
| **Mouse Move** | `Move (500, 500)` • `Move (+120, -40) rel` | Absolute screen coordinates or relative pixel delta |
| **Mouse Scroll** | `Scroll -3` • `Scroll +5` | Vertical (`+Up` / `-Down`) mouse wheel scrolling |
| **Wait + Jitter** | `Wait 200ms` • `Wait 150ms ±20ms` • `Wait 1.5s` | High-precision interruptible sleep with optional humanization jitter |
| **Nested Repeat** | `Repeat 3 { Press F -> Wait 120ms ±15ms } -> Click` | Repeats an inner sequence `N` times inside the main macro loop |

### Live Input Recorder (`F7`) & Hardware Panic Kill-Switch (`F12`)

- **Live Recorder (`F7`)**: Records live keyboard and mouse inputs into editable timeline cards, automatically coalesces `KeyDown + KeyUp` taps, and filters out kTap's own `0x4B544150` (`"KTAP"`) synthetic injection marker and active hotkeys.
- **Panic Kill-Switch (`F12`)**: Interrupts running macros immediately — even mid-`Wait` — and invokes **`EmergencyInputSanitizer`** to dispatch matching `KeyUp` / `MouseUp` events in reverse order so modifiers or mouse buttons never stay stuck down.

---

## Cybersecurity & Safety Engineering

kTap is built with defensive local and supply-chain safeguards documented in [`SECURITY.md`](SECURITY.md):

1. **Declarative JSON Profiles (Zero `eval` / `exec` / Arbitrary Scripts)**: `.ktap.json` profiles validate strictly against allowlists, bounds, and types in [`src/security/validator.rs`](src/security/validator.rs), rejecting `NaN`/`Infinity`, boolean-as-integer confusion, path traversal, NTFS ADS (`:`), and reserved device names (`CON`, `NUL`, `COM1`).
2. **Canonical `SHA-256` Signing**: Profiles embed an `integrity_sha256` digest computed via `sha2` (`ktap-v1`, plus legacy `kfmacro-v1` support) and verified using constant-time `subtle::ConstantTimeEq` in [`src/security/integrity.rs`](src/security/integrity.rs).
3. **Anti-DoS Complexity Budgets**: Caps `MAX_PROFILE_FILE_BYTES = 1 MiB`, `MAX_TOTAL_STEPS = 5,000`, `MAX_NESTING_DEPTH = 5`, `MAX_EXPANDED_OPS_PER_CYCLE = 100,000`, plus a `5ms` minimum inter-cycle flood governor.
4. **Built-In Static Security Auditor ([`src/security/auditor.rs`](src/security/auditor.rs))**:

| Rule Code | Severity | Security & Safety Check |
| :--- | :--- | :--- |
| **`KT-SEC-001`** | `WARNING` | Detects zero-wait infinite loops and confirms the `5ms` anti-flood governor is active |
| **`KT-SEC-002`** | `WARNING` | Detects unbalanced `KeyDown` / `MouseDown` actions without matching release steps |
| **`KT-SEC-003`** | `CRITICAL` | Flags suspicious shell/system shortcuts (`Win+R`, `Win+X`, `Alt+F4`), including split `KeyDown RWin -> Press R` sequences |
| **`KT-SEC-004`** | `WARNING` | Flags extreme mouse click rates (`> 100 CPS`) |
| **`KT-SEC-005`** | `INFO` | Alerts when a step injects a key also bound as a kTap control hotkey (`0x4B544150` self-filter active) |

---

## Architecture & Repository Layout

```mermaid
flowchart LR
    UI["kTap Native Rust GUI<br/>(eframe / egui + glow)"] --> DSL["Sequence DSL<br/>(src/models/dsl.rs)"]
    UI --> SEC["Security Layer<br/>(Validator + SHA-256 + Auditor)"]
    UI --> UPD["Auto-Updater + Config<br/>(src/updater.rs + src/config.rs)"]
    UI --> EXEC["MacroExecutor<br/>(src/engine/executor.rs)"]
    UI --> REC["MacroRecorder<br/>(src/engine/recorder.rs)"]
    HK["GlobalHotkeyManager<br/>(F6 Run / F7 Rec / F12 Panic)"] --> EXEC
    HK --> REC
    EXEC --> SAN["EmergencyInputSanitizer<br/>(Auto-Release Ledger)"]
    SAN --> PLAT["Cross-Platform Backend Router<br/>(src/engine/platform.rs)"]
    PLAT --> WIN32["Win32 SendInput<br/>(Windows)"]
    PLAT --> MAC["CoreGraphics CGEvent<br/>(macOS)"]
    PLAT --> X11["X11 / Wayland uinput<br/>(Linux)"]
    SAN --> DRY["DryRunBackend<br/>(Offline Simulator)"]
```

```text
kTap/
├── .github/workflows/ci.yml      # Multi-OS Rust fmt, clippy, test & Release Pipeline
├── assets/                       # Embedded Window & Installer Icons (icon.png, icon.ico)
├── docs/screenshots/             # kTap 1.0.0 Desktop Interface & Dialog Screenshots
├── examples/capture_screenshots.rs # Feature-gated screenshot generator (--features screenshots)
├── src/
│   ├── engine/
│   │   ├── backend.rs            # InputBackend Trait, Marker & DryRunBackend Simulator
│   │   ├── executor.rs           # Pre-Compiled MacroExecutor, Sanitizer & Telemetry
│   │   ├── hotkeys.rs            # Adaptive Cross-Platform Global Hotkey Polling Thread
│   │   ├── platform.rs           # Platform Backend Router (Win32 / macOS / Wayland / X11)
│   │   ├── platform_win32.rs     # Native 64-bit Win32 SendInput & GetAsyncKeyState
│   │   ├── platform_macos.rs     # Native macOS CoreGraphics CGEvent & AX Trust Check
│   │   ├── platform_linux.rs     # Native Linux libX11 + libXtst Input & Query Backend
│   │   ├── platform_linux_wayland.rs # Native Linux Wayland /dev/uinput & cached evdev Backend
│   │   └── recorder.rs           # Live Input Recorder with Tap Coalescing & Combo Filter
│   ├── models/
│   │   ├── action.rs             # MacroAction Struct, ActionType & Bounds Validation
│   │   ├── dsl.rs                # Sequence DSL Parser & Canonical Formatter
│   │   ├── keymap.rs             # Virtual-Key (VK), Scan-Code & Cross-OS Key Registry
│   │   └── profile.rs            # MacroProfile, RepeatMode & Built-In Starter Presets
│   ├── security/
│   │   ├── auditor.rs            # Static Security & Safety Analyzer (KT-SEC-001..005)
│   │   ├── integrity.rs          # Canonical SHA-256 Signing & Constant-Time Verification
│   │   └── validator.rs          # Allowlist Schema, Path & Complexity Budget Validator
│   ├── ui/
│   │   ├── app.rs                # Dual Simple/Studio Obsidian Desktop GUI & 4-Card Hero
│   │   ├── installer_window.rs   # Cross-Platform Setup & Uninstaller Wizard with Browse...
│   │   ├── mini_hud.rs           # Always-on-Top Floating Compact Control Pill
│   │   ├── step_dialog.rs        # Modal Step Inspector with Bind Key & Pick XY Capture
│   │   ├── theme.rs              # Obsidian/Emerald Palette & Native Window Styling
│   │   └── update_dialog.rs      # SHA-256 Verified GitHub Releases Update Sheet
│   ├── config.rs                 # Crash-Safe Config, Legacy Migration & Active Profile Backup
│   ├── installer.rs              # Per-User Installer, Desktop Launchers & Registry Manager
│   ├── lib.rs                    # Library Crate Root (VERSION = "1.0.0")
│   ├── main.rs                   # Setup Wizard, CLI & Desktop GUI Entry Point
│   └── updater.rs                # SemVer Client, SHA-256 Verifier & Non-Destructive Hot-Swapper
├── tests/integration_tests.rs    # Full Rust Integration, SHA-256 Parity & CLI Suite
├── build.rs                      # Windows MSVC Resource Embedding
├── Cargo.toml                    # Dependencies & Size/Speed Optimized Release Profile
└── RELEASE_NOTES.md              # Latest Release Notes (Published to GitHub Releases)
```

---

## Installation & Quick Start

### Run from Source (Rust Toolchain)

```bash
git clone https://github.com/efeartn/kTap.git
cd kTap

# Launch kTap Desktop GUI (release optimized)
cargo run --release
```

### Command-Line Interface (CLI)

```bash
# Check installed version
cargo run -- --version

# Print the one-line Linux Wayland /dev/uinput setup command
cargo run -- --wayland-setup

# Simulate a Sequence DSL recipe in safe Dry-Run mode (no live OS inputs)
cargo run -- --expr "Press E -> Wait 200ms -> Click -> Press 1" --repeat 3 --dry-run

# Export the signed starter profile
cargo run -- --export-default my_combo.ktap.json

# Run the static security & safety audit on a saved profile
cargo run -- --profile my_combo.ktap.json --audit

# Execute a saved profile with live OS injection
cargo run -- --profile my_combo.ktap.json

# Launch Setup / Install / Uninstall workflows
cargo run -- --setup
cargo run -- --install --silent
cargo run -- --uninstall --silent
```

### Compile Standalone Binary & Run Tests

```bash
# Build single-file release executable under target/release/
cargo build --release --locked

# Run formatting, clippy (-D warnings), and full integration tests
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
```

---

## License & Security Policy

- **License**: Distributed under the [**GNU General Public License v3.0**](LICENSE).
- **Security Policy**: See [**SECURITY.md**](SECURITY.md) for architecture threat models, auto-updater verification details, and vulnerability disclosure guidelines.
