//! Native Linux Wayland input injection & global key/button polling via `/dev/uinput` and `evdev`.

use std::collections::HashMap;
use std::ffi::CString;
use std::fs::{File, OpenOptions};
use std::io;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::io::AsRawFd;
use std::path::PathBuf;
use std::sync::Mutex;

use crate::engine::backend::InputBackend;
use crate::engine::platform::{
    wayland_udev_setup_command as platform_wayland_udev_setup_command, VK_LBUTTON, VK_MBUTTON,
    VK_RBUTTON, VK_XBUTTON1, VK_XBUTTON2,
};
use crate::models::action::MouseButton;
use crate::models::keymap::KeyInfo;

const EV_SYN: u16 = 0x00;
const EV_KEY: u16 = 0x01;
const EV_REL: u16 = 0x02;
const EV_ABS: u16 = 0x03;

const SYN_REPORT: u16 = 0x00;

const REL_X: u16 = 0x00;
const REL_Y: u16 = 0x01;
const REL_HWHEEL: u16 = 0x06;
const REL_WHEEL: u16 = 0x08;

const ABS_X: u16 = 0x00;
const ABS_Y: u16 = 0x01;
const ABS_VIRTUAL_MAX: i32 = 32_767;

const BTN_LEFT: u16 = 0x110;
const BTN_RIGHT: u16 = 0x111;
const BTN_MIDDLE: u16 = 0x112;
const BTN_SIDE: u16 = 0x113;
const BTN_EXTRA: u16 = 0x114;

const BUS_USB: u16 = 0x03;

const UI_SET_EVBIT: libc::Ioctl = 0x4004_5564 as libc::Ioctl;
const UI_SET_KEYBIT: libc::Ioctl = 0x4004_5565 as libc::Ioctl;
const UI_SET_RELBIT: libc::Ioctl = 0x4004_5566 as libc::Ioctl;
const UI_SET_ABSBIT: libc::Ioctl = 0x4004_5567 as libc::Ioctl;
const UI_DEV_SETUP: libc::Ioctl = 0x405c_5503 as libc::Ioctl;
const UI_ABS_SETUP: libc::Ioctl = 0x401c_5504 as libc::Ioctl;
const UI_DEV_CREATE: libc::Ioctl = 0x5501 as libc::Ioctl;
const UI_DEV_DESTROY: libc::Ioctl = 0x5502 as libc::Ioctl;

const EVIOCGNAME_256: libc::Ioctl = 0x8100_4506 as libc::Ioctl;
const EVIOCGBIT_0_32: libc::Ioctl = 0x8020_4520 as libc::Ioctl;
const EVIOCGKEY_96: libc::Ioctl = 0x8060_4518 as libc::Ioctl;

#[repr(C)]
#[derive(Clone, Copy)]
struct UinputInputId {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct UinputSetup {
    id: UinputInputId,
    name: [u8; 80],
    ff_effects_max: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct InputAbsInfo {
    value: i32,
    minimum: i32,
    maximum: i32,
    fuzz: i32,
    flat: i32,
    resolution: i32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct UinputAbsSetup {
    code: u16,
    absinfo: InputAbsInfo,
}

/// One-line setup command to grant the active user access to `/dev/uinput` and `/dev/input/event*` on Wayland.
pub fn wayland_udev_setup_command() -> &'static str {
    platform_wayland_udev_setup_command()
}

/// Map a Windows Virtual-Key (VK) code to the corresponding Linux `input-event-codes.h` keycode.
pub fn vk_to_linux_evdev_key(vk: u16) -> Option<u16> {
    Some(match vk {
        // Mouse buttons (when polled via VK)
        VK_LBUTTON => BTN_LEFT,
        VK_RBUTTON => BTN_RIGHT,
        VK_MBUTTON => BTN_MIDDLE,
        VK_XBUTTON1 => BTN_SIDE,
        VK_XBUTTON2 => BTN_EXTRA,
        // Modifiers
        0x11 | 0xA2 => 29, // KEY_LEFTCTRL
        0xA3 => 97,        // KEY_RIGHTCTRL
        0x10 | 0xA0 => 42, // KEY_LEFTSHIFT
        0xA1 => 54,        // KEY_RIGHTSHIFT
        0x12 | 0xA4 => 56, // KEY_LEFTALT
        0xA5 => 100,       // KEY_RIGHTALT
        0x5B => 125,       // KEY_LEFTMETA
        0x5C => 126,       // KEY_RIGHTMETA
        // Navigation & special keys
        0x08 => 14,  // KEY_BACKSPACE
        0x09 => 15,  // KEY_TAB
        0x0D => 28,  // KEY_ENTER
        0x13 => 119, // KEY_PAUSE
        0x14 => 58,  // KEY_CAPSLOCK
        0x1B => 1,   // KEY_ESC
        0x20 => 57,  // KEY_SPACE
        0x21 => 104, // KEY_PAGEUP
        0x22 => 109, // KEY_PAGEDOWN
        0x23 => 107, // KEY_END
        0x24 => 102, // KEY_HOME
        0x25 => 105, // KEY_LEFT
        0x26 => 103, // KEY_UP
        0x27 => 106, // KEY_RIGHT
        0x28 => 108, // KEY_DOWN
        0x2C => 99,  // KEY_SYSRQ
        0x2D => 110, // KEY_INSERT
        0x2E => 111, // KEY_DELETE
        0x90 => 69,  // KEY_NUMLOCK
        0x91 => 70,  // KEY_SCROLLLOCK
        // Number row
        0x30 => 11,               // KEY_0
        0x31..=0x39 => vk - 0x2F, // KEY_1 (2) .. KEY_9 (10)
        // Alphabet (US QWERTY kernel keycodes)
        0x41 => 30, // A
        0x42 => 48, // B
        0x43 => 46, // C
        0x44 => 32, // D
        0x45 => 18, // E
        0x46 => 33, // F
        0x47 => 34, // G
        0x48 => 35, // H
        0x49 => 23, // I
        0x4A => 36, // J
        0x4B => 37, // K
        0x4C => 38, // L
        0x4D => 50, // M
        0x4E => 49, // N
        0x4F => 24, // O
        0x50 => 25, // P
        0x51 => 16, // Q
        0x52 => 19, // R
        0x53 => 31, // S
        0x54 => 20, // T
        0x55 => 22, // U
        0x56 => 47, // V
        0x57 => 17, // W
        0x58 => 45, // X
        0x59 => 21, // Y
        0x5A => 44, // Z
        // Numpad
        0x60 => 82, // KEY_KP0
        0x61 => 79, // KEY_KP1
        0x62 => 80, // KEY_KP2
        0x63 => 81, // KEY_KP3
        0x64 => 75, // KEY_KP4
        0x65 => 76, // KEY_KP5
        0x66 => 77, // KEY_KP6
        0x67 => 71, // KEY_KP7
        0x68 => 72, // KEY_KP8
        0x69 => 73, // KEY_KP9
        0x6A => 55, // KEY_KPASTERISK
        0x6B => 78, // KEY_KPPLUS
        0x6D => 74, // KEY_KPMINUS
        0x6E => 83, // KEY_KPDOT
        0x6F => 98, // KEY_KPSLASH
        // Function keys
        0x70..=0x79 => vk - 0x70 + 59,  // KEY_F1 .. KEY_F10
        0x7A => 87,                     // KEY_F11
        0x7B => 88,                     // KEY_F12
        0x7C..=0x87 => vk - 0x7C + 183, // KEY_F13 .. KEY_F24
        // OEM punctuation
        0xBA => 39, // Semicolon
        0xBB => 13, // Equals
        0xBC => 51, // Comma
        0xBD => 12, // Minus
        0xBE => 52, // Period
        0xBF => 53, // Slash
        0xC0 => 41, // Grave
        0xDB => 26, // LeftBracket
        0xDC => 43, // Backslash
        0xDD => 27, // RightBracket
        0xDE => 40, // Quote
        _ => return None,
    })
}

pub fn mouse_button_to_evdev(button: MouseButton) -> u16 {
    match button {
        MouseButton::Left => BTN_LEFT,
        MouseButton::Right => BTN_RIGHT,
        MouseButton::Middle => BTN_MIDDLE,
        MouseButton::X1 => BTN_SIDE,
        MouseButton::X2 => BTN_EXTRA,
    }
}

fn make_setup(name: &str, product_id: u16) -> UinputSetup {
    let mut buf = [0u8; 80];
    let bytes = name.as_bytes();
    let len = bytes.len().min(79);
    buf[..len].copy_from_slice(&bytes[..len]);
    UinputSetup {
        id: UinputInputId {
            bustype: BUS_USB,
            vendor: 0x4B46, // "KF"
            product: product_id,
            version: 0x0210,
        },
        name: buf,
        ff_effects_max: 0,
    }
}

fn open_uinput_file() -> Result<File, String> {
    let path = if std::path::Path::new("/dev/uinput").exists() {
        "/dev/uinput"
    } else if std::path::Path::new("/dev/input/uinput").exists() {
        "/dev/input/uinput"
    } else {
        return Err(
            "/dev/uinput device not found (kernel uinput module may not be loaded).".to_string(),
        );
    };

    OpenOptions::new()
        .write(true)
        .open(path)
        .map_err(|e| match e.kind() {
            io::ErrorKind::PermissionDenied => {
                format!("Permission denied opening {path}. Run `keyforge --wayland-setup` to enable native Wayland input.")
            }
            _ => format!("Failed to open {path}: {e}"),
        })
}

fn emit_events(file: &File, events: &[(u16, u16, i32)]) {
    let mut tv = libc::timeval {
        tv_sec: 0,
        tv_usec: 0,
    };
    unsafe {
        libc::gettimeofday(&mut tv, std::ptr::null_mut());
    }
    let fd = file.as_raw_fd();
    for &(ev_type, code, value) in events {
        let ev = libc::input_event {
            time: tv,
            type_: ev_type,
            code,
            value,
        };
        let ptr = (&ev as *const libc::input_event).cast::<libc::c_void>();
        unsafe {
            let _ = libc::write(fd, ptr, std::mem::size_of::<libc::input_event>());
        }
    }
}

struct UinputDevice {
    file: File,
}

impl UinputDevice {
    fn create_keyboard_rel_mouse() -> Result<Self, String> {
        let file = open_uinput_file()?;
        let fd = file.as_raw_fd();
        unsafe {
            if libc::ioctl(fd, UI_SET_EVBIT, i32::from(EV_KEY)) < 0
                || libc::ioctl(fd, UI_SET_EVBIT, i32::from(EV_REL)) < 0
            {
                return Err("Failed configuring uinput event bits.".to_string());
            }
            for code in 1..=248 {
                let _ = libc::ioctl(fd, UI_SET_KEYBIT, code);
            }
            for btn in [BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA] {
                let _ = libc::ioctl(fd, UI_SET_KEYBIT, i32::from(btn));
            }
            for rel in [REL_X, REL_Y, REL_WHEEL, REL_HWHEEL] {
                let _ = libc::ioctl(fd, UI_SET_RELBIT, i32::from(rel));
            }
            let setup = make_setup("KeyForge Virtual Input", 0x0001);
            if libc::ioctl(fd, UI_DEV_SETUP, &setup) < 0 {
                return Err("uinput UI_DEV_SETUP failed.".to_string());
            }
            if libc::ioctl(fd, UI_DEV_CREATE) < 0 {
                return Err("uinput UI_DEV_CREATE failed.".to_string());
            }
        }
        Ok(Self { file })
    }

    fn create_abs_pointer() -> Result<Self, String> {
        let file = open_uinput_file()?;
        let fd = file.as_raw_fd();
        unsafe {
            if libc::ioctl(fd, UI_SET_EVBIT, i32::from(EV_KEY)) < 0
                || libc::ioctl(fd, UI_SET_EVBIT, i32::from(EV_ABS)) < 0
            {
                return Err("Failed configuring absolute uinput pointer bits.".to_string());
            }
            for btn in [BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA] {
                let _ = libc::ioctl(fd, UI_SET_KEYBIT, i32::from(btn));
            }
            let _ = libc::ioctl(fd, UI_SET_ABSBIT, i32::from(ABS_X));
            let _ = libc::ioctl(fd, UI_SET_ABSBIT, i32::from(ABS_Y));

            for axis in [ABS_X, ABS_Y] {
                let abs = UinputAbsSetup {
                    code: axis,
                    absinfo: InputAbsInfo {
                        value: 0,
                        minimum: 0,
                        maximum: ABS_VIRTUAL_MAX,
                        fuzz: 0,
                        flat: 0,
                        resolution: 0,
                    },
                };
                if libc::ioctl(fd, UI_ABS_SETUP, &abs) < 0 {
                    return Err("uinput UI_ABS_SETUP failed.".to_string());
                }
            }
            let setup = make_setup("KeyForge Virtual Pointer", 0x0002);
            if libc::ioctl(fd, UI_DEV_SETUP, &setup) < 0 {
                return Err("uinput pointer UI_DEV_SETUP failed.".to_string());
            }
            if libc::ioctl(fd, UI_DEV_CREATE) < 0 {
                return Err("uinput pointer UI_DEV_CREATE failed.".to_string());
            }
        }
        Ok(Self { file })
    }
}

impl Drop for UinputDevice {
    fn drop(&mut self) {
        unsafe {
            let _ = libc::ioctl(self.file.as_raw_fd(), UI_DEV_DESTROY);
        }
    }
}

fn discover_evdev_devices() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    let Ok(entries) = std::fs::read_dir("/dev/input") else {
        return paths;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if !name_str.starts_with("event") {
            continue;
        }
        let path = entry.path();
        if let Ok(cpath) = CString::new(path.as_os_str().as_bytes()) {
            unsafe {
                let fd = libc::open(cpath.as_ptr(), libc::O_RDONLY | libc::O_NONBLOCK);
                if fd < 0 {
                    continue;
                }
                let mut dev_name = [0u8; 256];
                let nlen = libc::ioctl(fd, EVIOCGNAME_256, dev_name.as_mut_ptr());
                let is_keyforge_virtual = if nlen > 0 {
                    let s = String::from_utf8_lossy(
                        &dev_name[..dev_name.iter().position(|&b| b == 0).unwrap_or(255)],
                    );
                    s.starts_with("KeyForge Virtual")
                } else {
                    false
                };

                let mut ev_bits = [0u8; 32];
                let has_keys = !is_keyforge_virtual
                    && libc::ioctl(fd, EVIOCGBIT_0_32, ev_bits.as_mut_ptr()) >= 0
                    && (ev_bits[0] & (1 << EV_KEY)) != 0;
                libc::close(fd);
                if has_keys {
                    paths.push(path);
                }
            }
        }
    }
    paths
}

struct WaylandState {
    dev: Option<UinputDevice>,
    abs_dev: Option<UinputDevice>,
    unavailable_reason: String,
    screen_w: i64,
    screen_h: i64,
    cursor_x: i64,
    cursor_y: i64,
}

/// Native Linux Wayland backend using kernel `/dev/uinput` and `evdev`.
pub struct LinuxWaylandUinputBackend {
    state: Mutex<WaylandState>,
}

impl Default for LinuxWaylandUinputBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl LinuxWaylandUinputBackend {
    pub fn new() -> Self {
        match UinputDevice::create_keyboard_rel_mouse() {
            Ok(dev) => Self {
                state: Mutex::new(WaylandState {
                    dev: Some(dev),
                    abs_dev: None,
                    unavailable_reason: String::new(),
                    screen_w: 1920,
                    screen_h: 1080,
                    cursor_x: 960,
                    cursor_y: 540,
                }),
            },
            Err(reason) => Self {
                state: Mutex::new(WaylandState {
                    dev: None,
                    abs_dev: None,
                    unavailable_reason: reason,
                    screen_w: 1920,
                    screen_h: 1080,
                    cursor_x: 960,
                    cursor_y: 540,
                }),
            },
        }
    }

    pub fn available(&self) -> bool {
        self.state.lock().expect("wayland lock").dev.is_some()
    }

    pub fn can_poll_evdev(&self) -> bool {
        !discover_evdev_devices().is_empty()
    }

    pub fn unavailable_reason(&self) -> String {
        self.state
            .lock()
            .expect("wayland lock")
            .unavailable_reason
            .clone()
    }

    pub fn get_pressed_states(
        &self,
        key_vks: &[u16],
        mouse_vks: &[u16],
    ) -> (HashMap<u16, bool>, HashMap<u16, bool>) {
        let mut key_states: HashMap<u16, bool> = key_vks.iter().map(|&vk| (vk, false)).collect();
        let mut mouse_states: HashMap<u16, bool> =
            mouse_vks.iter().map(|&vk| (vk, false)).collect();

        let devices = discover_evdev_devices();
        if devices.is_empty() {
            return (key_states, mouse_states);
        }

        let mut aggregated_bits = [0u8; 96];
        for path in devices {
            if let Ok(cpath) = CString::new(path.as_os_str().as_bytes()) {
                unsafe {
                    let fd = libc::open(cpath.as_ptr(), libc::O_RDONLY | libc::O_NONBLOCK);
                    if fd < 0 {
                        continue;
                    }
                    let mut buf = [0u8; 96];
                    if libc::ioctl(fd, EVIOCGKEY_96, buf.as_mut_ptr()) >= 0 {
                        for (dst, src) in aggregated_bits.iter_mut().zip(buf.iter()) {
                            *dst |= *src;
                        }
                    }
                    libc::close(fd);
                }
            }
        }

        let is_code_down = |code: u16| -> bool {
            let idx = (code / 8) as usize;
            let bit = code % 8;
            idx < aggregated_bits.len() && (aggregated_bits[idx] & (1 << bit)) != 0
        };

        for &vk in key_vks {
            if let Some(code) = vk_to_linux_evdev_key(vk) {
                key_states.insert(vk, is_code_down(code));
            }
        }
        for &vk in mouse_vks {
            if let Some(code) = vk_to_linux_evdev_key(vk) {
                mouse_states.insert(vk, is_code_down(code));
            }
        }
        (key_states, mouse_states)
    }

    pub fn is_key_down(&self, vk: u16) -> bool {
        self.get_pressed_states(&[vk], &[])
            .0
            .get(&vk)
            .copied()
            .unwrap_or(false)
    }

    pub fn is_mouse_down(&self, vk: u16) -> bool {
        self.get_pressed_states(&[], &[vk])
            .1
            .get(&vk)
            .copied()
            .unwrap_or(false)
    }
}

impl InputBackend for LinuxWaylandUinputBackend {
    fn key_down(&self, key: KeyInfo) {
        let Some(code) = vk_to_linux_evdev_key(key.vk_code) else {
            return;
        };
        let st = self.state.lock().expect("wayland lock");
        if let Some(ref dev) = st.dev {
            emit_events(&dev.file, &[(EV_KEY, code, 1), (EV_SYN, SYN_REPORT, 0)]);
        }
    }

    fn key_up(&self, key: KeyInfo) {
        let Some(code) = vk_to_linux_evdev_key(key.vk_code) else {
            return;
        };
        let st = self.state.lock().expect("wayland lock");
        if let Some(ref dev) = st.dev {
            emit_events(&dev.file, &[(EV_KEY, code, 0), (EV_SYN, SYN_REPORT, 0)]);
        }
    }

    fn mouse_down(&self, button: MouseButton) {
        let btn = mouse_button_to_evdev(button);
        let st = self.state.lock().expect("wayland lock");
        if let Some(ref dev) = st.dev {
            emit_events(&dev.file, &[(EV_KEY, btn, 1), (EV_SYN, SYN_REPORT, 0)]);
        }
    }

    fn mouse_up(&self, button: MouseButton) {
        let btn = mouse_button_to_evdev(button);
        let st = self.state.lock().expect("wayland lock");
        if let Some(ref dev) = st.dev {
            emit_events(&dev.file, &[(EV_KEY, btn, 0), (EV_SYN, SYN_REPORT, 0)]);
        }
    }

    fn move_cursor(&self, x: i64, y: i64, relative: bool) {
        let mut st = self.state.lock().expect("wayland lock");
        if relative {
            st.cursor_x = (st.cursor_x + x).clamp(0, st.screen_w.max(1));
            st.cursor_y = (st.cursor_y + y).clamp(0, st.screen_h.max(1));
            if let Some(ref dev) = st.dev {
                emit_events(
                    &dev.file,
                    &[
                        (EV_REL, REL_X, x.clamp(-32_767, 32_767) as i32),
                        (EV_REL, REL_Y, y.clamp(-32_767, 32_767) as i32),
                        (EV_SYN, SYN_REPORT, 0),
                    ],
                );
            }
        } else {
            st.cursor_x = x.clamp(0, st.screen_w.max(1));
            st.cursor_y = y.clamp(0, st.screen_h.max(1));
            if st.abs_dev.is_none() && st.dev.is_some() {
                if let Ok(abs_d) = UinputDevice::create_abs_pointer() {
                    st.abs_dev = Some(abs_d);
                }
            }
            if let Some(ref abs_d) = st.abs_dev {
                let sw = st.screen_w.max(1) as f64;
                let sh = st.screen_h.max(1) as f64;
                let nx = ((x.clamp(0, st.screen_w) as f64 / sw) * f64::from(ABS_VIRTUAL_MAX))
                    .round() as i32;
                let ny = ((y.clamp(0, st.screen_h) as f64 / sh) * f64::from(ABS_VIRTUAL_MAX))
                    .round() as i32;
                emit_events(
                    &abs_d.file,
                    &[
                        (EV_ABS, ABS_X, nx),
                        (EV_ABS, ABS_Y, ny),
                        (EV_SYN, SYN_REPORT, 0),
                    ],
                );
            }
        }
    }

    fn scroll(&self, dx: i64, dy: i64) {
        if dx == 0 && dy == 0 {
            return;
        }
        let st = self.state.lock().expect("wayland lock");
        if let Some(ref dev) = st.dev {
            let mut events = Vec::with_capacity(3);
            if dy != 0 {
                events.push((EV_REL, REL_WHEEL, dy.clamp(-50, 50) as i32));
            }
            if dx != 0 {
                events.push((EV_REL, REL_HWHEEL, dx.clamp(-50, 50) as i32));
            }
            events.push((EV_SYN, SYN_REPORT, 0));
            emit_events(&dev.file, &events);
        }
    }

    fn get_cursor_pos(&self) -> (i64, i64) {
        let st = self.state.lock().expect("wayland lock");
        (st.cursor_x, st.cursor_y)
    }

    fn close(&self) {
        let mut st = self.state.lock().expect("wayland lock");
        st.abs_dev.take();
        st.dev.take();
    }
}
