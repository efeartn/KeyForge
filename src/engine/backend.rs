//! Input backend trait and deterministic dry-run recorder backend for testing/simulation.

use std::collections::HashSet;
use std::sync::Mutex;

use crate::models::action::MouseButton;
use crate::models::keymap::KeyInfo;

/// Magic signature stamped into synthetic OS inputs ("KTAP" in ASCII hex).
pub const KTAP_EXTRA_INFO: usize = 0x4B544150;

/// Record of a low-level input operation emitted by an [`InputBackend`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DispatchedEvent {
    pub kind: &'static str,
    pub target: String,
    pub x: Option<i64>,
    pub y: Option<i64>,
    pub dx: i64,
    pub dy: i64,
}

/// Abstract thread-safe interface for OS-level or simulated keyboard & mouse injection.
pub trait InputBackend: Send + Sync {
    fn key_down(&self, key: KeyInfo);
    fn key_up(&self, key: KeyInfo);
    fn mouse_down(&self, button: MouseButton);
    fn mouse_up(&self, button: MouseButton);
    fn move_cursor(&self, x: i64, y: i64, relative: bool);
    fn scroll(&self, dx: i64, dy: i64);
    fn get_cursor_pos(&self) -> (i64, i64);
    fn close(&self) {}
}

#[derive(Debug)]
struct DryRunState {
    cursor_x: i64,
    cursor_y: i64,
    events: Vec<DispatchedEvent>,
    held_keys: HashSet<String>,
    held_buttons: HashSet<MouseButton>,
}

/// In-memory deterministic input backend for safe dry-run simulation and unit testing.
#[derive(Debug)]
pub struct DryRunBackend {
    state: Mutex<DryRunState>,
}

impl Default for DryRunBackend {
    fn default() -> Self {
        Self::new((960, 540))
    }
}

impl DryRunBackend {
    pub fn new(initial_cursor: (i64, i64)) -> Self {
        Self {
            state: Mutex::new(DryRunState {
                cursor_x: initial_cursor.0,
                cursor_y: initial_cursor.1,
                events: Vec::new(),
                held_keys: HashSet::new(),
                held_buttons: HashSet::new(),
            }),
        }
    }

    pub fn clear(&self) {
        let mut st = self.state.lock().expect("dry-run lock");
        st.events.clear();
        st.held_keys.clear();
        st.held_buttons.clear();
    }

    pub fn events(&self) -> Vec<DispatchedEvent> {
        self.state.lock().expect("dry-run lock").events.clone()
    }

    pub fn held_keys(&self) -> HashSet<String> {
        self.state.lock().expect("dry-run lock").held_keys.clone()
    }

    pub fn held_buttons(&self) -> HashSet<MouseButton> {
        self.state
            .lock()
            .expect("dry-run lock")
            .held_buttons
            .clone()
    }
}

impl InputBackend for DryRunBackend {
    fn key_down(&self, key: KeyInfo) {
        let mut st = self.state.lock().expect("dry-run lock");
        st.held_keys.insert(key.canonical_name.to_string());
        st.events.push(DispatchedEvent {
            kind: "key_down",
            target: key.canonical_name.to_string(),
            x: None,
            y: None,
            dx: 0,
            dy: 0,
        });
    }

    fn key_up(&self, key: KeyInfo) {
        let mut st = self.state.lock().expect("dry-run lock");
        st.held_keys.remove(key.canonical_name);
        st.events.push(DispatchedEvent {
            kind: "key_up",
            target: key.canonical_name.to_string(),
            x: None,
            y: None,
            dx: 0,
            dy: 0,
        });
    }

    fn mouse_down(&self, button: MouseButton) {
        let mut st = self.state.lock().expect("dry-run lock");
        st.held_buttons.insert(button);
        let (x, y) = (st.cursor_x, st.cursor_y);
        st.events.push(DispatchedEvent {
            kind: "mouse_down",
            target: button.as_str().to_string(),
            x: Some(x),
            y: Some(y),
            dx: 0,
            dy: 0,
        });
    }

    fn mouse_up(&self, button: MouseButton) {
        let mut st = self.state.lock().expect("dry-run lock");
        st.held_buttons.remove(&button);
        let (x, y) = (st.cursor_x, st.cursor_y);
        st.events.push(DispatchedEvent {
            kind: "mouse_up",
            target: button.as_str().to_string(),
            x: Some(x),
            y: Some(y),
            dx: 0,
            dy: 0,
        });
    }

    fn move_cursor(&self, x: i64, y: i64, relative: bool) {
        let mut st = self.state.lock().expect("dry-run lock");
        if relative {
            st.cursor_x += x;
            st.cursor_y += y;
            let (cx, cy) = (st.cursor_x, st.cursor_y);
            st.events.push(DispatchedEvent {
                kind: "mouse_move",
                target: "relative".to_string(),
                x: Some(cx),
                y: Some(cy),
                dx: x,
                dy: y,
            });
        } else {
            st.cursor_x = x;
            st.cursor_y = y;
            st.events.push(DispatchedEvent {
                kind: "mouse_move",
                target: "absolute".to_string(),
                x: Some(x),
                y: Some(y),
                dx: 0,
                dy: 0,
            });
        }
    }

    fn scroll(&self, dx: i64, dy: i64) {
        let mut st = self.state.lock().expect("dry-run lock");
        let (cx, cy) = (st.cursor_x, st.cursor_y);
        st.events.push(DispatchedEvent {
            kind: "mouse_scroll",
            target: "wheel".to_string(),
            x: Some(cx),
            y: Some(cy),
            dx,
            dy,
        });
    }

    fn get_cursor_pos(&self) -> (i64, i64) {
        let st = self.state.lock().expect("dry-run lock");
        (st.cursor_x, st.cursor_y)
    }
}
