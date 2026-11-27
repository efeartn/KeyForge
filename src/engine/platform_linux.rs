//! Linux X11 / XTest runtime-loaded input backend with graceful Wayland/headless fallback.

use std::collections::HashMap;
use std::ffi::{c_char, c_int, c_uint, c_ulong, c_void};
use std::sync::Mutex;

use libloading::Library;

use crate::engine::backend::InputBackend;
use crate::engine::platform::{VK_LBUTTON, VK_MBUTTON, VK_RBUTTON, VK_XBUTTON1, VK_XBUTTON2};
use crate::models::action::MouseButton;
use crate::models::keymap::KeyInfo;

pub fn vk_to_x11_keysym(vk: u16) -> Option<c_ulong> {
    Some(match vk {
        0x10 | 0xA0 => 0xFFE1, // Shift_L
        0xA1 => 0xFFE2,        // Shift_R
        0x11 | 0xA2 => 0xFFE3, // Control_L
        0xA3 => 0xFFE4,        // Control_R
        0x14 => 0xFFE5,        // Caps_Lock
        0x12 | 0xA4 => 0xFFE9, // Alt_L
        0xA5 => 0xFFEA,        // Alt_R
        0x5B => 0xFFEB,        // Super_L
        0x5C => 0xFFEC,        // Super_R
        0x08 => 0xFF08,        // BackSpace
        0x09 => 0xFF09,        // Tab
        0x0D => 0xFF0D,        // Return
        0x13 => 0xFF13,        // Pause
        0x91 => 0xFF14,        // Scroll_Lock
        0x1B => 0xFF1B,        // Escape
        0x20 => 0x0020,        // space
        0x21 => 0xFF55,        // Page_Up
        0x22 => 0xFF56,        // Page_Down
        0x23 => 0xFF57,        // End
        0x24 => 0xFF50,        // Home
        0x25 => 0xFF51,        // Left
        0x26 => 0xFF52,        // Up
        0x27 => 0xFF53,        // Right
        0x28 => 0xFF54,        // Down
        0x2C => 0xFF61,        // Print
        0x2D => 0xFF63,        // Insert
        0x2E => 0xFFFF,        // Delete
        0x90 => 0xFF7F,        // Num_Lock
        0x30..=0x39 => c_ulong::from(vk),
        0x41..=0x5A => c_ulong::from(vk + 0x20),
        0x60..=0x69 => 0xFFB0 + c_ulong::from(vk - 0x60),
        0x6A => 0xFFAA,
        0x6B => 0xFFAB,
        0x6D => 0xFFAD,
        0x6E => 0xFFAE,
        0x6F => 0xFFAF,
        0x70..=0x87 => 0xFFBE + c_ulong::from(vk - 0x70),
        0xBA => 0x003B,
        0xBB => 0x003D,
        0xBC => 0x002C,
        0xBD => 0x002D,
        0xBE => 0x002E,
        0xBF => 0x002F,
        0xC0 => 0x0060,
        0xDB => 0x005B,
        0xDC => 0x005C,
        0xDD => 0x005D,
        0xDE => 0x0027,
        _ => return None,
    })
}

fn mouse_button_number(button: MouseButton) -> c_uint {
    match button {
        MouseButton::Left => 1,
        MouseButton::Middle => 2,
        MouseButton::Right => 3,
        MouseButton::X1 => 8,
        MouseButton::X2 => 9,
    }
}

fn mouse_vk_mask(vk: u16) -> c_uint {
    match vk {
        VK_LBUTTON => 1 << 8,
        VK_MBUTTON => 1 << 9,
        VK_RBUTTON => 1 << 10,
        VK_XBUTTON1 => 1 << 13,
        VK_XBUTTON2 => 1 << 14,
        _ => 0,
    }
}

type FnXOpenDisplay = unsafe extern "C" fn(*const c_char) -> *mut c_void;
type FnXCloseDisplay = unsafe extern "C" fn(*mut c_void) -> c_int;
type FnXFlush = unsafe extern "C" fn(*mut c_void) -> c_int;
type FnXDefaultScreen = unsafe extern "C" fn(*mut c_void) -> c_int;
type FnXDefaultRootWindow = unsafe extern "C" fn(*mut c_void) -> c_ulong;
type FnXKeysymToKeycode = unsafe extern "C" fn(*mut c_void, c_ulong) -> c_uint;
type FnXQueryKeymap = unsafe extern "C" fn(*mut c_void, *mut c_char) -> c_int;
type FnXQueryPointer = unsafe extern "C" fn(
    *mut c_void,
    c_ulong,
    *mut c_ulong,
    *mut c_ulong,
    *mut c_int,
    *mut c_int,
    *mut c_int,
    *mut c_int,
    *mut c_uint,
) -> c_int;

type FnXTestQueryExtension =
    unsafe extern "C" fn(*mut c_void, *mut c_int, *mut c_int, *mut c_int, *mut c_int) -> c_int;
type FnXTestFakeKeyEvent = unsafe extern "C" fn(*mut c_void, c_uint, c_int, c_ulong) -> c_int;
type FnXTestFakeButtonEvent = unsafe extern "C" fn(*mut c_void, c_uint, c_int, c_ulong) -> c_int;
type FnXTestFakeMotionEvent =
    unsafe extern "C" fn(*mut c_void, c_int, c_int, c_int, c_ulong) -> c_int;
type FnXTestFakeRelativeMotionEvent =
    unsafe extern "C" fn(*mut c_void, c_int, c_int, c_ulong) -> c_int;

struct X11Api {
    _x11_lib: Library,
    _xtst_lib: Library,
    display: *mut c_void,
    root_window: c_ulong,
    screen: c_int,
    close_display: FnXCloseDisplay,
    flush: FnXFlush,
    keysym_to_keycode: FnXKeysymToKeycode,
    query_keymap: FnXQueryKeymap,
    query_pointer: FnXQueryPointer,
    fake_key_event: FnXTestFakeKeyEvent,
    fake_button_event: FnXTestFakeButtonEvent,
    fake_motion_event: FnXTestFakeMotionEvent,
    fake_rel_motion_event: FnXTestFakeRelativeMotionEvent,
    keycode_cache: HashMap<u16, c_uint>,
}

unsafe impl Send for X11Api {}

impl Drop for X11Api {
    fn drop(&mut self) {
        if !self.display.is_null() {
            unsafe {
                let _ = (self.close_display)(self.display);
            }
            self.display = std::ptr::null_mut();
        }
    }
}

impl X11Api {
    fn connect() -> Result<Self, String> {
        let display_env = std::env::var("DISPLAY").unwrap_or_default();
        if display_env.trim().is_empty() {
            return Err("DISPLAY environment variable is not set.".to_string());
        }

        let x11_lib = unsafe {
            Library::new("libX11.so.6")
                .or_else(|_| Library::new("libX11.so"))
                .map_err(|e| format!("X11 library not available: {e}"))?
        };
        let xtst_lib = unsafe {
            Library::new("libXtst.so.6")
                .or_else(|_| Library::new("libXtst.so"))
                .map_err(|e| format!("XTest library not available: {e}"))?
        };

        unsafe {
            let open_display: FnXOpenDisplay = *x11_lib
                .get::<FnXOpenDisplay>(b"XOpenDisplay\0")
                .map_err(|e| e.to_string())?;
            let close_display: FnXCloseDisplay = *x11_lib
                .get::<FnXCloseDisplay>(b"XCloseDisplay\0")
                .map_err(|e| e.to_string())?;
            let flush: FnXFlush = *x11_lib
                .get::<FnXFlush>(b"XFlush\0")
                .map_err(|e| e.to_string())?;
            let default_screen: FnXDefaultScreen = *x11_lib
                .get::<FnXDefaultScreen>(b"XDefaultScreen\0")
                .map_err(|e| e.to_string())?;
            let default_root: FnXDefaultRootWindow = *x11_lib
                .get::<FnXDefaultRootWindow>(b"XDefaultRootWindow\0")
                .map_err(|e| e.to_string())?;
            let keysym_to_keycode: FnXKeysymToKeycode = *x11_lib
                .get::<FnXKeysymToKeycode>(b"XKeysymToKeycode\0")
                .map_err(|e| e.to_string())?;
            let query_keymap: FnXQueryKeymap = *x11_lib
                .get::<FnXQueryKeymap>(b"XQueryKeymap\0")
                .map_err(|e| e.to_string())?;
            let query_pointer: FnXQueryPointer = *x11_lib
                .get::<FnXQueryPointer>(b"XQueryPointer\0")
                .map_err(|e| e.to_string())?;

            let query_ext: FnXTestQueryExtension = *xtst_lib
                .get::<FnXTestQueryExtension>(b"XTestQueryExtension\0")
                .map_err(|e| e.to_string())?;
            let fake_key_event: FnXTestFakeKeyEvent = *xtst_lib
                .get::<FnXTestFakeKeyEvent>(b"XTestFakeKeyEvent\0")
                .map_err(|e| e.to_string())?;
            let fake_button_event: FnXTestFakeButtonEvent = *xtst_lib
                .get::<FnXTestFakeButtonEvent>(b"XTestFakeButtonEvent\0")
                .map_err(|e| e.to_string())?;
            let fake_motion_event: FnXTestFakeMotionEvent = *xtst_lib
                .get::<FnXTestFakeMotionEvent>(b"XTestFakeMotionEvent\0")
                .map_err(|e| e.to_string())?;
            let fake_rel_motion_event: FnXTestFakeRelativeMotionEvent = *xtst_lib
                .get::<FnXTestFakeRelativeMotionEvent>(b"XTestFakeRelativeMotionEvent\0")
                .map_err(|e| e.to_string())?;

            let display = open_display(std::ptr::null());
            if display.is_null() {
                return Err("Could not connect to X11 display.".to_string());
            }

            let (mut ev, mut err, mut maj, mut min) = (0, 0, 0, 0);
            if query_ext(display, &mut ev, &mut err, &mut maj, &mut min) == 0 {
                let _ = close_display(display);
                return Err("XTEST extension is not enabled on this X server.".to_string());
            }

            let screen = default_screen(display);
            let root_window = default_root(display);

            Ok(Self {
                _x11_lib: x11_lib,
                _xtst_lib: xtst_lib,
                display,
                root_window,
                screen,
                close_display,
                flush,
                keysym_to_keycode,
                query_keymap,
                query_pointer,
                fake_key_event,
                fake_button_event,
                fake_motion_event,
                fake_rel_motion_event,
                keycode_cache: HashMap::new(),
            })
        }
    }

    fn resolve_keycode(&mut self, vk: u16) -> c_uint {
        if let Some(&cached) = self.keycode_cache.get(&vk) {
            return cached;
        }
        let Some(keysym) = vk_to_x11_keysym(vk) else {
            return 0;
        };
        let kc = unsafe { (self.keysym_to_keycode)(self.display, keysym) };
        if kc > 0 {
            self.keycode_cache.insert(vk, kc);
        }
        kc
    }

    fn query_pointer(&self) -> (i64, i64, c_uint) {
        let (mut root_ret, mut child_ret) = (0, 0);
        let (mut root_x, mut root_y, mut win_x, mut win_y) = (0, 0, 0, 0);
        let mut mask = 0;
        let ok = unsafe {
            (self.query_pointer)(
                self.display,
                self.root_window,
                &mut root_ret,
                &mut child_ret,
                &mut root_x,
                &mut root_y,
                &mut win_x,
                &mut win_y,
                &mut mask,
            )
        };
        if ok == 0 {
            (0, 0, 0)
        } else {
            (i64::from(root_x), i64::from(root_y), mask)
        }
    }
}

struct LinuxState {
    api: Option<X11Api>,
    unavailable_reason: String,
    fallback_x: i64,
    fallback_y: i64,
}

/// Linux X11 / XTest backend with thread safety and graceful headless/Wayland fallback.
pub struct LinuxX11InputBackend {
    state: Mutex<LinuxState>,
}

impl Default for LinuxX11InputBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl LinuxX11InputBackend {
    pub fn new() -> Self {
        match X11Api::connect() {
            Ok(api) => Self {
                state: Mutex::new(LinuxState {
                    api: Some(api),
                    unavailable_reason: String::new(),
                    fallback_x: 0,
                    fallback_y: 0,
                }),
            },
            Err(reason) => Self {
                state: Mutex::new(LinuxState {
                    api: None,
                    unavailable_reason: reason,
                    fallback_x: 0,
                    fallback_y: 0,
                }),
            },
        }
    }

    pub fn available(&self) -> bool {
        self.state.lock().expect("linux lock").api.is_some()
    }

    pub fn unavailable_reason(&self) -> String {
        self.state
            .lock()
            .expect("linux lock")
            .unavailable_reason
            .clone()
    }

    pub fn get_pressed_states(
        &self,
        key_vks: &[u16],
        mouse_vks: &[u16],
    ) -> (HashMap<u16, bool>, HashMap<u16, bool>) {
        let mut key_states = HashMap::with_capacity(key_vks.len());
        let mut mouse_states = HashMap::with_capacity(mouse_vks.len());

        let mut st = self.state.lock().expect("linux lock");
        let Some(api) = st.api.as_mut() else {
            for &vk in key_vks {
                key_states.insert(vk, false);
            }
            for &vk in mouse_vks {
                mouse_states.insert(vk, false);
            }
            return (key_states, mouse_states);
        };

        if !key_vks.is_empty() {
            let mut buf = [0u8; 32];
            unsafe {
                let _ = (api.query_keymap)(api.display, buf.as_mut_ptr() as *mut c_char);
            }
            for &vk in key_vks {
                let kc = api.resolve_keycode(vk) as usize;
                let down = if kc > 0 && kc < 256 {
                    (buf[kc >> 3] & (1 << (kc & 7))) != 0
                } else {
                    false
                };
                key_states.insert(vk, down);
            }
        }

        if !mouse_vks.is_empty() {
            let (_, _, mask) = api.query_pointer();
            for &vk in mouse_vks {
                let bit = mouse_vk_mask(vk);
                mouse_states.insert(vk, (mask & bit) != 0);
            }
        }

        (key_states, mouse_states)
    }

    pub fn is_key_down(&self, vk: u16) -> bool {
        let (ks, _) = self.get_pressed_states(&[vk], &[]);
        ks.get(&vk).copied().unwrap_or(false)
    }

    pub fn is_mouse_down(&self, vk: u16) -> bool {
        let (_, ms) = self.get_pressed_states(&[], &[vk]);
        ms.get(&vk).copied().unwrap_or(false)
    }
}

impl InputBackend for LinuxX11InputBackend {
    fn key_down(&self, key: KeyInfo) {
        let mut st = self.state.lock().expect("linux lock");
        if let Some(api) = st.api.as_mut() {
            let kc = api.resolve_keycode(key.vk_code);
            if kc > 0 {
                unsafe {
                    let _ = (api.fake_key_event)(api.display, kc, 1, 0);
                    let _ = (api.flush)(api.display);
                }
            }
        }
    }

    fn key_up(&self, key: KeyInfo) {
        let mut st = self.state.lock().expect("linux lock");
        if let Some(api) = st.api.as_mut() {
            let kc = api.resolve_keycode(key.vk_code);
            if kc > 0 {
                unsafe {
                    let _ = (api.fake_key_event)(api.display, kc, 0, 0);
                    let _ = (api.flush)(api.display);
                }
            }
        }
    }

    fn mouse_down(&self, button: MouseButton) {
        let st = self.state.lock().expect("linux lock");
        if let Some(api) = st.api.as_ref() {
            let btn = mouse_button_number(button);
            unsafe {
                let _ = (api.fake_button_event)(api.display, btn, 1, 0);
                let _ = (api.flush)(api.display);
            }
        }
    }

    fn mouse_up(&self, button: MouseButton) {
        let st = self.state.lock().expect("linux lock");
        if let Some(api) = st.api.as_ref() {
            let btn = mouse_button_number(button);
            unsafe {
                let _ = (api.fake_button_event)(api.display, btn, 0, 0);
                let _ = (api.flush)(api.display);
            }
        }
    }

    fn move_cursor(&self, x: i64, y: i64, relative: bool) {
        let mut st = self.state.lock().expect("linux lock");
        if relative {
            st.fallback_x += x;
            st.fallback_y += y;
            if let Some(api) = st.api.as_ref() {
                unsafe {
                    let _ = (api.fake_rel_motion_event)(api.display, x as c_int, y as c_int, 0);
                    let _ = (api.flush)(api.display);
                }
            }
        } else {
            st.fallback_x = x;
            st.fallback_y = y;
            if let Some(api) = st.api.as_ref() {
                unsafe {
                    let _ =
                        (api.fake_motion_event)(api.display, api.screen, x as c_int, y as c_int, 0);
                    let _ = (api.flush)(api.display);
                }
            }
        }
    }

    fn scroll(&self, dx: i64, dy: i64) {
        let st = self.state.lock().expect("linux lock");
        if let Some(api) = st.api.as_ref() {
            if dy != 0 {
                let btn_v = if dy > 0 { 4 } else { 5 };
                for _ in 0..dy.unsigned_abs().min(50) {
                    unsafe {
                        let _ = (api.fake_button_event)(api.display, btn_v, 1, 0);
                        let _ = (api.fake_button_event)(api.display, btn_v, 0, 0);
                    }
                }
            }
            if dx != 0 {
                let btn_h = if dx > 0 { 7 } else { 6 };
                for _ in 0..dx.unsigned_abs().min(50) {
                    unsafe {
                        let _ = (api.fake_button_event)(api.display, btn_h, 1, 0);
                        let _ = (api.fake_button_event)(api.display, btn_h, 0, 0);
                    }
                }
            }
            unsafe {
                let _ = (api.flush)(api.display);
            }
        }
    }

    fn get_cursor_pos(&self) -> (i64, i64) {
        let st = self.state.lock().expect("linux lock");
        if let Some(api) = st.api.as_ref() {
            let (x, y, _) = api.query_pointer();
            (x, y)
        } else {
            (st.fallback_x, st.fallback_y)
        }
    }

    fn close(&self) {
        let mut st = self.state.lock().expect("linux lock");
        st.api.take();
    }
}
