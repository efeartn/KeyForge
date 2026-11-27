//! Cross-platform OS input injection, key-state polling, and cursor helpers (Windows, macOS, Linux).

use std::collections::HashMap;
use std::sync::Arc;

use crate::engine::backend::InputBackend;

pub const VK_LBUTTON: u16 = 0x01;
pub const VK_RBUTTON: u16 = 0x02;
pub const VK_MBUTTON: u16 = 0x04;
pub const VK_XBUTTON1: u16 = 0x05;
pub const VK_XBUTTON2: u16 = 0x06;

/// Describes input injection, key polling, and permission status on the active OS.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlatformInputCapabilities {
    pub platform: &'static str,
    pub backend_name: &'static str,
    pub can_inject: bool,
    pub can_poll_keys: bool,
    pub can_poll_cursor: bool,
    pub requires_permission: bool,
    pub notice: String,
}

pub fn platform_badge_label() -> &'static str {
    if cfg!(target_os = "windows") {
        "WIN32"
    } else if cfg!(target_os = "macos") {
        "MACOS"
    } else {
        "LINUX"
    }
}

/// Instantiate the native input backend appropriate for the current OS.
pub fn create_os_input_backend() -> Arc<dyn InputBackend> {
    #[cfg(target_os = "windows")]
    {
        Arc::new(crate::engine::platform_win32::Win32InputBackend::new())
    }
    #[cfg(target_os = "macos")]
    {
        Arc::new(crate::engine::platform_macos::MacOSInputBackend::new())
    }
    #[cfg(target_os = "linux")]
    {
        Arc::new(crate::engine::platform_linux::LinuxX11InputBackend::new())
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        Arc::new(crate::engine::backend::DryRunBackend::default())
    }
}

#[cfg(target_os = "linux")]
fn shared_linux_poll_backend() -> &'static crate::engine::platform_linux::LinuxX11InputBackend {
    static INSTANCE: std::sync::OnceLock<crate::engine::platform_linux::LinuxX11InputBackend> =
        std::sync::OnceLock::new();
    INSTANCE.get_or_init(crate::engine::platform_linux::LinuxX11InputBackend::new)
}

#[cfg(target_os = "macos")]
fn shared_macos_poll_backend() -> &'static crate::engine::platform_macos::MacOSInputBackend {
    static INSTANCE: std::sync::OnceLock<crate::engine::platform_macos::MacOSInputBackend> =
        std::sync::OnceLock::new();
    INSTANCE.get_or_init(crate::engine::platform_macos::MacOSInputBackend::new)
}

/// Return input injection, global polling, and OS permission status cleanly.
pub fn get_platform_capabilities() -> PlatformInputCapabilities {
    #[cfg(target_os = "windows")]
    {
        PlatformInputCapabilities {
            platform: "win32",
            backend_name: "Win32 SendInput",
            can_inject: true,
            can_poll_keys: true,
            can_poll_cursor: true,
            requires_permission: false,
            notice: String::new(),
        }
    }
    #[cfg(target_os = "macos")]
    {
        let trusted = shared_macos_poll_backend().is_accessibility_trusted();
        let notice = if trusted {
            String::new()
        } else {
            "macOS Accessibility permission is needed for live key injection & global hotkeys \
             (System Settings -> Privacy & Security -> Accessibility). \
             Enable Dry-Run Sim anytime to test without permissions."
                .to_string()
        };
        PlatformInputCapabilities {
            platform: "darwin",
            backend_name: "macOS CoreGraphics",
            can_inject: trusted,
            can_poll_keys: true,
            can_poll_cursor: true,
            requires_permission: !trusted,
            notice,
        }
    }
    #[cfg(target_os = "linux")]
    {
        let backend = shared_linux_poll_backend();
        let is_wayland = !std::env::var("WAYLAND_DISPLAY")
            .unwrap_or_default()
            .trim()
            .is_empty()
            || std::env::var("XDG_SESSION_TYPE")
                .unwrap_or_default()
                .trim()
                .eq_ignore_ascii_case("wayland");
        if backend.available() {
            PlatformInputCapabilities {
                platform: "linux",
                backend_name: if is_wayland {
                    "Linux X11 (XWayland)"
                } else {
                    "Linux X11/XTest"
                },
                can_inject: true,
                can_poll_keys: true,
                can_poll_cursor: true,
                requires_permission: false,
                notice: if is_wayland {
                    "Wayland session detected: X11/XTest works in XWayland windows \
                     (native Wayland compositors isolate global input)."
                        .to_string()
                } else {
                    String::new()
                },
            }
        } else if is_wayland {
            PlatformInputCapabilities {
                platform: "linux",
                backend_name: "Wayland (Preview Only)",
                can_inject: false,
                can_poll_keys: false,
                can_poll_cursor: false,
                requires_permission: false,
                notice: "Native Wayland isolates global input injection without XWayland/libXtst. \
                         Use an X11 session for live input or enable Dry-Run Simulation."
                    .to_string(),
            }
        } else {
            let reason = backend.unavailable_reason();
            let detail = if reason.is_empty() {
                String::new()
            } else {
                format!(" ({reason})")
            };
            PlatformInputCapabilities {
                platform: "linux",
                backend_name: "Unavailable",
                can_inject: false,
                can_poll_keys: false,
                can_poll_cursor: false,
                requires_permission: false,
                notice: format!(
                    "Linux X11/XTest unavailable{detail}. Install libx11 & libxtst or use Dry-Run Simulation."
                ),
            }
        }
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        PlatformInputCapabilities {
            platform: "other",
            backend_name: "DryRun",
            can_inject: false,
            can_poll_keys: false,
            can_poll_cursor: false,
            requires_permission: false,
            notice: String::new(),
        }
    }
}

/// Return `true` if background OS key polling is available on the active platform.
pub fn platform_supports_key_polling() -> bool {
    #[cfg(target_os = "windows")]
    {
        true
    }
    #[cfg(target_os = "macos")]
    {
        true
    }
    #[cfg(target_os = "linux")]
    {
        shared_linux_poll_backend().available()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        false
    }
}

/// Query whether a Virtual-Key (keyboard or mouse VK) is depressed across Win32/macOS/Linux.
pub fn platform_get_async_key_state(vk_code: u16) -> bool {
    #[cfg(target_os = "windows")]
    {
        crate::engine::platform_win32::get_async_key_state(vk_code)
    }
    #[cfg(target_os = "macos")]
    {
        if matches!(
            vk_code,
            VK_LBUTTON | VK_RBUTTON | VK_MBUTTON | VK_XBUTTON1 | VK_XBUTTON2
        ) {
            crate::engine::platform_macos::is_mouse_down(vk_code)
        } else {
            crate::engine::platform_macos::is_key_down(vk_code)
        }
    }
    #[cfg(target_os = "linux")]
    {
        if matches!(
            vk_code,
            VK_LBUTTON | VK_RBUTTON | VK_MBUTTON | VK_XBUTTON1 | VK_XBUTTON2
        ) {
            shared_linux_poll_backend().is_mouse_down(vk_code)
        } else {
            shared_linux_poll_backend().is_key_down(vk_code)
        }
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = vk_code;
        false
    }
}

/// Batch-read key and mouse button states efficiently on Windows, macOS, and Linux.
pub fn platform_get_pressed_states(
    key_vks: &[u16],
    mouse_vks: &[u16],
) -> (HashMap<u16, bool>, HashMap<u16, bool>) {
    #[cfg(target_os = "linux")]
    {
        shared_linux_poll_backend().get_pressed_states(key_vks, mouse_vks)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let ks = key_vks
            .iter()
            .map(|&vk| (vk, platform_get_async_key_state(vk)))
            .collect();
        let ms = mouse_vks
            .iter()
            .map(|&vk| (vk, platform_get_async_key_state(vk)))
            .collect();
        (ks, ms)
    }
}

/// Query the current global OS cursor coordinates across Windows, macOS, and Linux.
pub fn platform_get_cursor_pos() -> (i64, i64) {
    #[cfg(target_os = "windows")]
    {
        crate::engine::platform_win32::get_system_cursor_pos()
    }
    #[cfg(target_os = "macos")]
    {
        crate::engine::platform_macos::get_system_cursor_pos()
    }
    #[cfg(target_os = "linux")]
    {
        shared_linux_poll_backend().get_cursor_pos()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        (0, 0)
    }
}
