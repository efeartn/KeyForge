pub mod backend;
pub mod executor;
pub mod hotkeys;
pub mod platform;
pub mod recorder;

#[cfg(target_os = "windows")]
pub mod platform_win32;

#[cfg(target_os = "macos")]
pub mod platform_macos;

#[cfg(target_os = "linux")]
pub mod platform_linux;
