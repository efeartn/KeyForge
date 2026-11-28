//! Cross-platform OS input injection, key-state polling, and cursor helpers (Windows, macOS, Linux X11 + Wayland).

use std::collections::HashMap;
use std::sync::Arc;

use crate::engine::backend::InputBackend;

pub const VK_LBUTTON: u16 = 0x01;
pub const VK_RBUTTON: u16 = 0x02;
pub const VK_MBUTTON: u16 = 0x04;
pub const VK_XBUTTON1: u16 = 0x05;
pub const VK_XBUTTON2: u16 = 0x06;

/// One-line Linux command enabling non-root access to `/dev/uinput` and `/dev/input/event*` on Wayland.
pub fn wayland_udev_setup_command() -> &'static str {
    "echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\", OPTIONS+=\"static_node=uinput\"' | sudo tee /etc/udev/rules.d/99-ktap-uinput.rules >/dev/null && sudo usermod -aG input $USER && sudo udevadm control --reload-rules && sudo udevadm trigger /dev/uinput"
}

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

#[cfg(target_os = "linux")]
fn is_wayland_session() -> bool {
    !std::env::var("WAYLAND_DISPLAY")
        .unwrap_or_default()
        .trim()
        .is_empty()
        || std::env::var("XDG_SESSION_TYPE")
            .unwrap_or_default()
            .trim()
            .eq_ignore_ascii_case("wayland")
}

#[cfg(target_os = "linux")]
fn shared_linux_poll_backend() -> &'static crate::engine::platform_linux::LinuxX11InputBackend {
    static INSTANCE: std::sync::OnceLock<crate::engine::platform_linux::LinuxX11InputBackend> =
        std::sync::OnceLock::new();
    INSTANCE.get_or_init(crate::engine::platform_linux::LinuxX11InputBackend::new)
}

#[cfg(target_os = "linux")]
fn shared_linux_wayland_backend(
) -> &'static crate::engine::platform_linux_wayland::LinuxWaylandUinputBackend {
    static INSTANCE: std::sync::OnceLock<
        crate::engine::platform_linux_wayland::LinuxWaylandUinputBackend,
    > = std::sync::OnceLock::new();
    INSTANCE.get_or_init(crate::engine::platform_linux_wayland::LinuxWaylandUinputBackend::new)
}

#[cfg(target_os = "macos")]
fn shared_macos_poll_backend() -> &'static crate::engine::platform_macos::MacOSInputBackend {
    static INSTANCE: std::sync::OnceLock<crate::engine::platform_macos::MacOSInputBackend> =
        std::sync::OnceLock::new();
    INSTANCE.get_or_init(crate::engine::platform_macos::MacOSInputBackend::new)
}

pub fn platform_badge_label() -> &'static str {
    if cfg!(target_os = "windows") {
        "WIN32"
    } else if cfg!(target_os = "macos") {
        "MACOS"
    } else {
        #[cfg(target_os = "linux")]
        {
            if is_wayland_session() {
                if shared_linux_wayland_backend().available() {
                    "WAYLAND"
                } else if shared_linux_poll_backend().available() {
                    "XWAYLAND"
                } else {
                    "WAYLAND"
                }
            } else {
                "X11"
            }
        }
        #[cfg(not(target_os = "linux"))]
        {
            "LINUX"
        }
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
        if is_wayland_session() {
            let uinput = crate::engine::platform_linux_wayland::LinuxWaylandUinputBackend::new();
            if uinput.available() {
                return Arc::new(uinput);
            }
            let x11 = crate::engine::platform_linux::LinuxX11InputBackend::new();
            if x11.available() {
                return Arc::new(x11);
            }
            Arc::new(uinput)
        } else {
            let x11 = crate::engine::platform_linux::LinuxX11InputBackend::new();
            if x11.available() {
                return Arc::new(x11);
            }
            let uinput = crate::engine::platform_linux_wayland::LinuxWaylandUinputBackend::new();
            if uinput.available() {
                return Arc::new(uinput);
            }
            Arc::new(x11)
        }
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        Arc::new(crate::engine::backend::DryRunBackend::default())
    }
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
        let x11 = shared_linux_poll_backend();
        let uinput = shared_linux_wayland_backend();
        let wayland = is_wayland_session();

        if wayland && uinput.available() {
            let can_poll = uinput.can_poll_evdev() || x11.available();
            let notice = if can_poll {
                String::new()
            } else {
                "Native Wayland uinput injection active! For global F6/F7/F12 hotkeys outside focused window, add your user to the `input` group (`ktap --wayland-setup`).".to_string()
            };
            return PlatformInputCapabilities {
                platform: "linux",
                backend_name: "Wayland (uinput)",
                can_inject: true,
                can_poll_keys: can_poll,
                can_poll_cursor: x11.available(),
                requires_permission: !can_poll,
                notice,
            };
        }

        if wayland && x11.available() {
            return PlatformInputCapabilities {
                platform: "linux",
                backend_name: "Wayland (XWayland)",
                can_inject: true,
                can_poll_keys: true,
                can_poll_cursor: true,
                requires_permission: true,
                notice: "Wayland detected: XWayland fallback active (injects into XWayland apps). Enable native Wayland `/dev/uinput` for all compositors (`ktap --wayland-setup`).".to_string(),
            };
        }

        if wayland {
            let reason = uinput.unavailable_reason();
            return PlatformInputCapabilities {
                platform: "linux",
                backend_name: "Wayland (Setup Needed)",
                can_inject: false,
                can_poll_keys: uinput.can_poll_evdev(),
                can_poll_cursor: false,
                requires_permission: true,
                notice: format!(
                    "Native Wayland requires `/dev/uinput` access ({reason}). Copy & run the udev setup command or use Dry-Run Simulation."
                ),
            };
        }

        if x11.available() {
            PlatformInputCapabilities {
                platform: "linux",
                backend_name: "Linux X11/XTest",
                can_inject: true,
                can_poll_keys: true,
                can_poll_cursor: true,
                requires_permission: false,
                notice: String::new(),
            }
        } else if uinput.available() {
            PlatformInputCapabilities {
                platform: "linux",
                backend_name: "Linux (uinput)",
                can_inject: true,
                can_poll_keys: uinput.can_poll_evdev(),
                can_poll_cursor: false,
                requires_permission: false,
                notice: String::new(),
            }
        } else {
            let reason = x11.unavailable_reason();
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
                    "Linux X11/XTest & /dev/uinput unavailable{detail}. Install libx11/libxtst, configure uinput, or use Dry-Run Simulation."
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
        shared_linux_poll_backend().available() || shared_linux_wayland_backend().can_poll_evdev()
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
        let evdev = shared_linux_wayland_backend();
        let x11 = shared_linux_poll_backend();
        let is_mouse = matches!(
            vk_code,
            VK_LBUTTON | VK_RBUTTON | VK_MBUTTON | VK_XBUTTON1 | VK_XBUTTON2
        );
        if is_mouse {
            if x11.available() && x11.is_mouse_down(vk_code) {
                return true;
            }
            evdev.is_mouse_down(vk_code)
        } else {
            if x11.available() && x11.is_key_down(vk_code) {
                return true;
            }
            evdev.is_key_down(vk_code)
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
        let x11 = shared_linux_poll_backend();
        let evdev = shared_linux_wayland_backend();
        if evdev.can_poll_evdev() && (!x11.available() || is_wayland_session()) {
            return evdev.get_pressed_states(key_vks, mouse_vks);
        }
        x11.get_pressed_states(key_vks, mouse_vks)
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
        let x11 = shared_linux_poll_backend();
        if x11.available() {
            x11.get_cursor_pos()
        } else {
            shared_linux_wayland_backend().get_cursor_pos()
        }
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        (0, 0)
    }
}
