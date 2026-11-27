//! Native Win32 SendInput / GetAsyncKeyState backend.

use std::mem::size_of;

use windows_sys::Win32::Foundation::POINT;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, MapVirtualKeyW, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE,
    KEYBDINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, MAPVK_VK_TO_VSC, MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_MOVE, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_WHEEL,
    MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, MOUSEINPUT,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{GetCursorPos, SetCursorPos};

use crate::engine::backend::{InputBackend, KEYFORGE_EXTRA_INFO};
use crate::models::action::MouseButton;
use crate::models::keymap::KeyInfo;

const XBUTTON1_DATA: u32 = 0x0001;
const XBUTTON2_DATA: u32 = 0x0002;
const WHEEL_DELTA: i64 = 120;

/// Query whether a Windows Virtual-Key code is currently depressed.
pub fn get_async_key_state(vk_code: u16) -> bool {
    let state = unsafe { GetAsyncKeyState(i32::from(vk_code)) };
    (state as u16 & 0x8000) != 0
}

/// Query the current OS cursor position.
pub fn get_system_cursor_pos() -> (i64, i64) {
    let mut pt = POINT { x: 0, y: 0 };
    let ok = unsafe { GetCursorPos(&mut pt) };
    if ok != 0 {
        (i64::from(pt.x), i64::from(pt.y))
    } else {
        (0, 0)
    }
}

fn send_single_input(inp: INPUT) -> u32 {
    unsafe { SendInput(1, &inp, size_of::<INPUT>() as i32) }
}

fn button_flags(button: MouseButton, is_up: bool) -> (u32, u32) {
    match (button, is_up) {
        (MouseButton::Left, false) => (MOUSEEVENTF_LEFTDOWN, 0),
        (MouseButton::Left, true) => (MOUSEEVENTF_LEFTUP, 0),
        (MouseButton::Right, false) => (MOUSEEVENTF_RIGHTDOWN, 0),
        (MouseButton::Right, true) => (MOUSEEVENTF_RIGHTUP, 0),
        (MouseButton::Middle, false) => (MOUSEEVENTF_MIDDLEDOWN, 0),
        (MouseButton::Middle, true) => (MOUSEEVENTF_MIDDLEUP, 0),
        (MouseButton::X1, false) => (MOUSEEVENTF_XDOWN, XBUTTON1_DATA),
        (MouseButton::X1, true) => (MOUSEEVENTF_XUP, XBUTTON1_DATA),
        (MouseButton::X2, false) => (MOUSEEVENTF_XDOWN, XBUTTON2_DATA),
        (MouseButton::X2, true) => (MOUSEEVENTF_XUP, XBUTTON2_DATA),
    }
}

/// Production Windows `user32.SendInput` input backend.
#[derive(Debug, Default)]
pub struct Win32InputBackend;

impl Win32InputBackend {
    pub fn new() -> Self {
        Self
    }

    fn resolve_scan(key: KeyInfo) -> u16 {
        if key.scan_code != 0 {
            key.scan_code
        } else {
            unsafe { MapVirtualKeyW(u32::from(key.vk_code), MAPVK_VK_TO_VSC) as u16 }
        }
    }
}

impl InputBackend for Win32InputBackend {
    fn key_down(&self, key: KeyInfo) {
        let scan = Self::resolve_scan(key);
        let flags = if key.is_extended {
            KEYEVENTF_EXTENDEDKEY
        } else {
            0
        };
        let inp = INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: key.vk_code,
                    wScan: scan,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: KEYFORGE_EXTRA_INFO,
                },
            },
        };
        let _ = send_single_input(inp);
    }

    fn key_up(&self, key: KeyInfo) {
        let scan = Self::resolve_scan(key);
        let mut flags = KEYEVENTF_KEYUP;
        if key.is_extended {
            flags |= KEYEVENTF_EXTENDEDKEY;
        }
        let inp = INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: key.vk_code,
                    wScan: scan,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: KEYFORGE_EXTRA_INFO,
                },
            },
        };
        let _ = send_single_input(inp);
    }

    fn mouse_down(&self, button: MouseButton) {
        let (flags, data) = button_flags(button, false);
        let inp = INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: 0,
                    dy: 0,
                    mouseData: data,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: KEYFORGE_EXTRA_INFO,
                },
            },
        };
        let _ = send_single_input(inp);
    }

    fn mouse_up(&self, button: MouseButton) {
        let (flags, data) = button_flags(button, true);
        let inp = INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: 0,
                    dy: 0,
                    mouseData: data,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: KEYFORGE_EXTRA_INFO,
                },
            },
        };
        let _ = send_single_input(inp);
    }

    fn move_cursor(&self, x: i64, y: i64, relative: bool) {
        if relative {
            let inp = INPUT {
                r#type: INPUT_MOUSE,
                Anonymous: INPUT_0 {
                    mi: MOUSEINPUT {
                        dx: x.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32,
                        dy: y.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32,
                        mouseData: 0,
                        dwFlags: MOUSEEVENTF_MOVE,
                        time: 0,
                        dwExtraInfo: KEYFORGE_EXTRA_INFO,
                    },
                },
            };
            let _ = send_single_input(inp);
        } else {
            let cx = x.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32;
            let cy = y.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32;
            unsafe {
                let _ = SetCursorPos(cx, cy);
            }
        }
    }

    fn scroll(&self, dx: i64, dy: i64) {
        if dy != 0 {
            let amount = (dy.saturating_mul(WHEEL_DELTA)) as i32 as u32;
            let inp_v = INPUT {
                r#type: INPUT_MOUSE,
                Anonymous: INPUT_0 {
                    mi: MOUSEINPUT {
                        dx: 0,
                        dy: 0,
                        mouseData: amount,
                        dwFlags: MOUSEEVENTF_WHEEL,
                        time: 0,
                        dwExtraInfo: KEYFORGE_EXTRA_INFO,
                    },
                },
            };
            let _ = send_single_input(inp_v);
        }
        if dx != 0 {
            let amount = (dx.saturating_mul(WHEEL_DELTA)) as i32 as u32;
            let inp_h = INPUT {
                r#type: INPUT_MOUSE,
                Anonymous: INPUT_0 {
                    mi: MOUSEINPUT {
                        dx: 0,
                        dy: 0,
                        mouseData: amount,
                        dwFlags: MOUSEEVENTF_HWHEEL,
                        time: 0,
                        dwExtraInfo: KEYFORGE_EXTRA_INFO,
                    },
                },
            };
            let _ = send_single_input(inp_h);
        }
    }

    fn get_cursor_pos(&self) -> (i64, i64) {
        get_system_cursor_pos()
    }
}
