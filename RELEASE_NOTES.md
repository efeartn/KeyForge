# kTap 1.0.0

**kTap `1.0.0`** is the initial release of **kTap** — a minimalist, pure native Rust keyboard & macro sequence studio and live input recorder built with `eframe/egui` for Windows, macOS, and Linux.

## Highlights

### Native Cross-Platform Input & Hotkey Engines
- **Windows 10/11 (`Win32InputBackend`)**: Direct 64-bit `SendInput` & `GetAsyncKeyState` with `0x4B544150` (`"KTAP"`) synthetic event self-filtering.
- **macOS (`MacOSInputBackend`)**: Native `CoreGraphics` (`CGEventPost`, smooth scroll, `CGEventSourceKeyState`, `Cmd` / `Option` aliases, and Accessibility trust detection).
- **Linux Wayland & X11 (`LinuxWaylandUinputBackend` + `LinuxX11InputBackend`)**: Native `/dev/uinput` virtual input + 5-second TTL cached `evdev` global hotkey polling on Wayland compositors, automatic `XWayland` / `X11` fallback, and one-line `ktap --wayland-setup` udev setup.

### Dual Simple & Studio Workspaces
- **Simple Mode**: Fast Key Repeater with click-to-bind capture, highlighted speed presets (`25ms–500ms`), humanized `±10%` variance, and Quick Sequence DSL bar.
- **Studio Mode**: 4-card hero control strip (`Profile`, `Engine`, `Hotkeys`, `Delay & Start CTA`), filter pills (`All Steps`, `Keys`, `Waits`, `Mouse`) with `/` search & `Esc`/`×` clear, disabled-aware batch toolbar (`Undo`, `Redo`, `2×`, `0.5×`, `All On`, `All Off`, `Flip`), modal Step Inspector, and right-hand `Loop / Recorder / Security / Console` inspector.
- **Floating Always-on-Top Mini-HUD**: Compact control pill with live cycle counter, Start/Stop (`F6`), Panic (`F12`), and one-click Expand.

### Performance, Safety & Security
- **Pre-Compiled Execution Plans (`compile_actions`)**: Resolves key combos once per macro run before entering the repeat loop, paired with O(1) cached UI metrics and a **5.58 MB** (`5,848,576` bytes) standalone Windows x64 binary.
- **Cryptographic Profiles & Static Auditor**: Signed `.ktap.json` (`ktap-v1`, with legacy `.kfmacro.json` compatibility) verified with constant-time `SHA-256` (`subtle::ConstantTimeEq`), anti-DoS complexity limits, `KT-SEC-001..005` static security checks, and `EmergencyInputSanitizer` stuck-key release on panic abort.
- **Per-User Setup Installer & SHA-256 Auto-Updater**: Zero-elevation install with native folder picker, config-preserving Uninstaller, automatic legacy KeyForge config migration, and HTTPS + SHA-256 verified staged hot-swap updates.

## Downloads & Integrity Verification

Every release binary is compiled on GitHub Actions (`windows-latest`, `macos-latest`, `ubuntu-latest`) alongside individual `.sha256.txt` manifests and a combined `SHA256SUMS.txt`:

- **Windows Setup Installer (`x64`)**: `kTap-Setup-Windows-x64.exe`
- **Windows Portable (`x64`)**: `kTap-Windows-x64.exe`
- **macOS (`arm64`)**: `kTap-macOS-arm64`
- **Linux (`x86_64`)**: `kTap-Linux-x64`
- **Combined SHA-256 Manifest**: `SHA256SUMS.txt`
