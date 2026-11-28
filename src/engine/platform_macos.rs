//! macOS CoreGraphics CGEvent input injection, polling, and Accessibility detection.

use std::collections::HashSet;
use std::ffi::c_void;
use std::sync::Mutex;

use crate::engine::backend::{InputBackend, KTAP_EXTRA_INFO};
use crate::engine::platform::{VK_LBUTTON, VK_MBUTTON, VK_RBUTTON, VK_XBUTTON1, VK_XBUTTON2};
use crate::models::action::MouseButton;
use crate::models::keymap::KeyInfo;

const CG_HID_EVENT_TAP: u32 = 0;
const CG_EVENT_SOURCE_STATE_COMBINED_SESSION: i32 = 0;
const CG_EVENT_LEFT_MOUSE_DOWN: u32 = 1;
const CG_EVENT_LEFT_MOUSE_UP: u32 = 2;
const CG_EVENT_RIGHT_MOUSE_DOWN: u32 = 3;
const CG_EVENT_RIGHT_MOUSE_UP: u32 = 4;
const CG_EVENT_MOUSE_MOVED: u32 = 5;
const CG_EVENT_LEFT_MOUSE_DRAGGED: u32 = 6;
const CG_EVENT_RIGHT_MOUSE_DRAGGED: u32 = 7;
const CG_EVENT_OTHER_MOUSE_DOWN: u32 = 25;
const CG_EVENT_OTHER_MOUSE_UP: u32 = 26;
const CG_EVENT_OTHER_MOUSE_DRAGGED: u32 = 27;
const CG_SCROLL_EVENT_UNIT_LINE: u32 = 1;
const CG_EVENT_SOURCE_USER_DATA: u32 = 42;

const CG_FLAG_SHIFT: u64 = 0x0002_0000;
const CG_FLAG_CONTROL: u64 = 0x0004_0000;
const CG_FLAG_ALTERNATE: u64 = 0x0008_0000;
const CG_FLAG_COMMAND: u64 = 0x0010_0000;

#[repr(C)]
#[derive(Debug, Clone, Copy)]
struct CGPoint {
    x: f64,
    y: f64,
}

#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGEventCreate(source: *const c_void) -> *mut c_void;
    fn CGEventGetLocation(event: *const c_void) -> CGPoint;
    fn CGEventCreateKeyboardEvent(
        source: *const c_void,
        virtual_key: u16,
        key_down: bool,
    ) -> *mut c_void;
    fn CGEventCreateMouseEvent(
        source: *const c_void,
        mouse_type: u32,
        mouse_cursor_position: CGPoint,
        mouse_button: u32,
    ) -> *mut c_void;
    fn CGEventCreateScrollWheelEvent2(
        source: *const c_void,
        units: u32,
        wheel_count: u32,
        wheel1: i32,
        wheel2: i32,
        wheel3: i32,
    ) -> *mut c_void;
    fn CGEventSetFlags(event: *mut c_void, flags: u64);
    fn CGEventSetIntegerValueField(event: *mut c_void, field: u32, value: i64);
    fn CGEventPost(tap: u32, event: *mut c_void);
    fn CGEventSourceKeyState(state_id: i32, key: u16) -> bool;
    fn CGEventSourceButtonState(state_id: i32, button: u32) -> bool;
}

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFRelease(cf: *const c_void);
}

#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    fn AXIsProcessTrusted() -> bool;
}

/// Complete Windows Virtual-Key -> macOS Carbon virtual keycode table (ANSI).
pub fn vk_to_mac_keycode(vk: u16) -> Option<u16> {
    Some(match vk {
        // Modifiers
        0x11 | 0xA2 => 0x3B, // Ctrl / LCtrl
        0xA3 => 0x3E,        // RCtrl
        0x10 | 0xA0 => 0x38, // Shift / LShift
        0xA1 => 0x3C,        // RShift
        0x12 | 0xA4 => 0x3A, // Alt / Option
        0xA5 => 0x3D,        // RAlt / ROption
        0x5B => 0x37,        // Win / Command
        0x5C => 0x36,        // RWin / RCommand
        // Navigation & Special
        0x08 => 0x33, // Backspace
        0x09 => 0x30, // Tab
        0x0D => 0x24, // Enter
        0x14 => 0x39, // CapsLock
        0x1B => 0x35, // Esc
        0x20 => 0x31, // Space
        0x21 => 0x74, // PageUp
        0x22 => 0x79, // PageDown
        0x23 => 0x77, // End
        0x24 => 0x73, // Home
        0x25 => 0x7B, // Left
        0x26 => 0x7E, // Up
        0x27 => 0x7C, // Right
        0x28 => 0x7D, // Down
        0x2D => 0x72, // Insert
        0x2E => 0x75, // Delete
        0x90 => 0x47, // NumLock
        // Number row
        0x30 => 0x1D,
        0x31 => 0x12,
        0x32 => 0x13,
        0x33 => 0x14,
        0x34 => 0x15,
        0x35 => 0x17,
        0x36 => 0x16,
        0x37 => 0x1A,
        0x38 => 0x1C,
        0x39 => 0x19,
        // Alphabet A-Z
        0x41 => 0x00,
        0x42 => 0x0B,
        0x43 => 0x08,
        0x44 => 0x02,
        0x45 => 0x0E,
        0x46 => 0x03,
        0x47 => 0x05,
        0x48 => 0x04,
        0x49 => 0x22,
        0x4A => 0x26,
        0x4B => 0x28,
        0x4C => 0x25,
        0x4D => 0x2E,
        0x4E => 0x2D,
        0x4F => 0x1F,
        0x50 => 0x23,
        0x51 => 0x0C,
        0x52 => 0x0F,
        0x53 => 0x01,
        0x54 => 0x11,
        0x55 => 0x20,
        0x56 => 0x09,
        0x57 => 0x0D,
        0x58 => 0x07,
        0x59 => 0x10,
        0x5A => 0x06,
        // Numpad
        0x60 => 0x52,
        0x61 => 0x53,
        0x62 => 0x54,
        0x63 => 0x55,
        0x64 => 0x56,
        0x65 => 0x57,
        0x66 => 0x58,
        0x67 => 0x59,
        0x68 => 0x5B,
        0x69 => 0x5C,
        0x6A => 0x43,
        0x6B => 0x45,
        0x6D => 0x4E,
        0x6E => 0x41,
        0x6F => 0x4B,
        // F1-F20
        0x70 => 0x7A,
        0x71 => 0x78,
        0x72 => 0x63,
        0x73 => 0x76,
        0x74 => 0x60,
        0x75 => 0x61,
        0x76 => 0x62,
        0x77 => 0x64,
        0x78 => 0x65,
        0x79 => 0x6D,
        0x7A => 0x67,
        0x7B => 0x6F,
        0x7C => 0x69,
        0x7D => 0x6B,
        0x7E => 0x71,
        0x7F => 0x6A,
        0x80 => 0x40,
        0x81 => 0x4F,
        0x82 => 0x50,
        0x83 => 0x5A,
        // OEM Punctuation
        0xBA => 0x29,
        0xBB => 0x18,
        0xBC => 0x2B,
        0xBD => 0x1B,
        0xBE => 0x2F,
        0xBF => 0x2C,
        0xC0 => 0x32,
        0xDB => 0x21,
        0xDC => 0x2A,
        0xDD => 0x1E,
        0xDE => 0x27,
        _ => return None,
    })
}

fn vk_to_cg_flag(vk: u16) -> u64 {
    match vk {
        0x10 | 0xA0 | 0xA1 => CG_FLAG_SHIFT,
        0x11 | 0xA2 | 0xA3 => CG_FLAG_CONTROL,
        0x12 | 0xA4 | 0xA5 => CG_FLAG_ALTERNATE,
        0x5B | 0x5C => CG_FLAG_COMMAND,
        _ => 0,
    }
}

fn is_mac_modifier_keycode(kc: u16) -> bool {
    matches!(kc, 0x36 | 0x37 | 0x38 | 0x3A | 0x3B | 0x3C | 0x3D | 0x3E)
}

#[derive(Debug, Default)]
struct MacState {
    active_flags: u64,
    held_buttons: HashSet<MouseButton>,
}

/// macOS native CoreGraphics input backend.
#[derive(Debug, Default)]
pub struct MacOSInputBackend {
    state: Mutex<MacState>,
}

impl MacOSInputBackend {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn is_accessibility_trusted(&self) -> bool {
        unsafe { AXIsProcessTrusted() }
    }

    unsafe fn post_and_release(&self, evt: *mut c_void) {
        if evt.is_null() {
            return;
        }
        CGEventSetIntegerValueField(evt, CG_EVENT_SOURCE_USER_DATA, KTAP_EXTRA_INFO as i64);
        CGEventPost(CG_HID_EVENT_TAP, evt);
        CFRelease(evt);
    }

    fn mouse_event_types(button: MouseButton, is_down: bool) -> (u32, u32) {
        match (button, is_down) {
            (MouseButton::Left, true) => (CG_EVENT_LEFT_MOUSE_DOWN, 0),
            (MouseButton::Left, false) => (CG_EVENT_LEFT_MOUSE_UP, 0),
            (MouseButton::Right, true) => (CG_EVENT_RIGHT_MOUSE_DOWN, 1),
            (MouseButton::Right, false) => (CG_EVENT_RIGHT_MOUSE_UP, 1),
            (MouseButton::Middle, true) => (CG_EVENT_OTHER_MOUSE_DOWN, 2),
            (MouseButton::Middle, false) => (CG_EVENT_OTHER_MOUSE_UP, 2),
            (MouseButton::X1, true) => (CG_EVENT_OTHER_MOUSE_DOWN, 3),
            (MouseButton::X1, false) => (CG_EVENT_OTHER_MOUSE_UP, 3),
            (MouseButton::X2, true) => (CG_EVENT_OTHER_MOUSE_DOWN, 4),
            (MouseButton::X2, false) => (CG_EVENT_OTHER_MOUSE_UP, 4),
        }
    }

    fn current_cg_point() -> CGPoint {
        let (x, y) = get_system_cursor_pos();
        CGPoint {
            x: x as f64,
            y: y as f64,
        }
    }
}

pub fn get_system_cursor_pos() -> (i64, i64) {
    unsafe {
        let evt = CGEventCreate(std::ptr::null());
        if evt.is_null() {
            return (0, 0);
        }
        let loc = CGEventGetLocation(evt);
        CFRelease(evt);
        (loc.x.round() as i64, loc.y.round() as i64)
    }
}

pub fn is_key_down(vk: u16) -> bool {
    let Some(kc) = vk_to_mac_keycode(vk) else {
        return false;
    };
    unsafe { CGEventSourceKeyState(CG_EVENT_SOURCE_STATE_COMBINED_SESSION, kc) }
}

pub fn is_mouse_down(vk: u16) -> bool {
    let cg_btn = match vk {
        VK_LBUTTON => 0,
        VK_RBUTTON => 1,
        VK_MBUTTON => 2,
        VK_XBUTTON1 => 3,
        VK_XBUTTON2 => 4,
        _ => return false,
    };
    unsafe { CGEventSourceButtonState(CG_EVENT_SOURCE_STATE_COMBINED_SESSION, cg_btn) }
}

impl InputBackend for MacOSInputBackend {
    fn key_down(&self, key: KeyInfo) {
        let Some(keycode) = vk_to_mac_keycode(key.vk_code) else {
            return;
        };
        let mut st = self.state.lock().expect("mac state");
        let flag = vk_to_cg_flag(key.vk_code);
        if flag != 0 {
            st.active_flags |= flag;
        }
        unsafe {
            let evt = CGEventCreateKeyboardEvent(std::ptr::null(), keycode, true);
            if !evt.is_null() {
                if st.active_flags != 0 {
                    CGEventSetFlags(evt, st.active_flags);
                }
                self.post_and_release(evt);
            }
        }
    }

    fn key_up(&self, key: KeyInfo) {
        let Some(keycode) = vk_to_mac_keycode(key.vk_code) else {
            return;
        };
        let mut st = self.state.lock().expect("mac state");
        let flag = vk_to_cg_flag(key.vk_code);
        if flag != 0 {
            st.active_flags &= !flag;
        }
        unsafe {
            let evt = CGEventCreateKeyboardEvent(std::ptr::null(), keycode, false);
            if !evt.is_null() {
                if st.active_flags != 0 || is_mac_modifier_keycode(keycode) {
                    CGEventSetFlags(evt, st.active_flags);
                }
                self.post_and_release(evt);
            }
        }
    }

    fn mouse_down(&self, button: MouseButton) {
        let mut st = self.state.lock().expect("mac state");
        st.held_buttons.insert(button);
        let (ev_type, cg_btn) = Self::mouse_event_types(button, true);
        let pt = Self::current_cg_point();
        unsafe {
            let evt = CGEventCreateMouseEvent(std::ptr::null(), ev_type, pt, cg_btn);
            self.post_and_release(evt);
        }
    }

    fn mouse_up(&self, button: MouseButton) {
        let mut st = self.state.lock().expect("mac state");
        st.held_buttons.remove(&button);
        let (ev_type, cg_btn) = Self::mouse_event_types(button, false);
        let pt = Self::current_cg_point();
        unsafe {
            let evt = CGEventCreateMouseEvent(std::ptr::null(), ev_type, pt, cg_btn);
            self.post_and_release(evt);
        }
    }

    fn move_cursor(&self, x: i64, y: i64, relative: bool) {
        let st = self.state.lock().expect("mac state");
        let (target_x, target_y) = if relative {
            let (cx, cy) = get_system_cursor_pos();
            (cx + x, cy + y)
        } else {
            (x, y)
        };
        let (ev_type, cg_btn) = if st.held_buttons.contains(&MouseButton::Left) {
            (CG_EVENT_LEFT_MOUSE_DRAGGED, 0)
        } else if st.held_buttons.contains(&MouseButton::Right) {
            (CG_EVENT_RIGHT_MOUSE_DRAGGED, 1)
        } else if !st.held_buttons.is_empty() {
            (CG_EVENT_OTHER_MOUSE_DRAGGED, 2)
        } else {
            (CG_EVENT_MOUSE_MOVED, 0)
        };
        unsafe {
            let evt = CGEventCreateMouseEvent(
                std::ptr::null(),
                ev_type,
                CGPoint {
                    x: target_x as f64,
                    y: target_y as f64,
                },
                cg_btn,
            );
            self.post_and_release(evt);
        }
    }

    fn scroll(&self, dx: i64, dy: i64) {
        if dx == 0 && dy == 0 {
            return;
        }
        let _st = self.state.lock().expect("mac state");
        let wheel_count = if dx != 0 { 2 } else { 1 };
        unsafe {
            let evt = CGEventCreateScrollWheelEvent2(
                std::ptr::null(),
                CG_SCROLL_EVENT_UNIT_LINE,
                wheel_count,
                dy as i32,
                (-dx) as i32,
                0,
            );
            self.post_and_release(evt);
        }
    }

    fn get_cursor_pos(&self) -> (i64, i64) {
        get_system_cursor_pos()
    }
}
