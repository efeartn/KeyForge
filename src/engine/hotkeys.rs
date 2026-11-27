//! Global hotkey listener with adaptive polling and instant Panic Kill-Switch detection.

use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use crate::engine::platform::{platform_get_async_key_state, platform_supports_key_polling};
use crate::models::keymap::{parse_key_combo, KeyInfo};

type HotkeyCallback = Arc<dyn Fn() + Send + Sync>;

struct BindingEntry {
    vks: Vec<u16>,
    keys: Vec<KeyInfo>,
    callback: HotkeyCallback,
    was_active: bool,
}

struct HotkeyState {
    bindings: HashMap<String, BindingEntry>,
    stop_requested: bool,
    active_mode: bool,
    active_interval: Duration,
    idle_interval: Duration,
}

/// Monitors global key combinations (`start_stop`, `record`, `panic`) in the background.
pub struct GlobalHotkeyManager {
    state: Arc<Mutex<HotkeyState>>,
    stop_cv: Arc<Condvar>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl Default for GlobalHotkeyManager {
    fn default() -> Self {
        Self::new(Duration::from_millis(20), Duration::from_millis(50))
    }
}

impl GlobalHotkeyManager {
    pub fn new(active_interval: Duration, idle_interval: Duration) -> Self {
        let active = active_interval.max(Duration::from_millis(5));
        let idle = idle_interval.max(active);
        Self {
            state: Arc::new(Mutex::new(HotkeyState {
                bindings: HashMap::new(),
                stop_requested: false,
                active_mode: false,
                active_interval: active,
                idle_interval: idle,
            })),
            stop_cv: Arc::new(Condvar::new()),
            thread: Mutex::new(None),
        }
    }

    pub fn current_poll_interval(&self) -> Duration {
        let st = self.state.lock().expect("hotkey lock");
        if st.active_mode {
            st.active_interval
        } else {
            st.idle_interval
        }
    }

    pub fn set_active_mode(&self, active: bool) {
        let mut st = self.state.lock().expect("hotkey lock");
        st.active_mode = active;
    }

    pub fn register<F>(&self, name: &str, combo: &str, callback: F) -> Result<(), String>
    where
        F: Fn() + Send + Sync + 'static,
    {
        let keys = parse_key_combo(combo)?;
        let vks: Vec<u16> = keys.iter().map(|k| k.vk_code).collect();
        let mut st = self.state.lock().expect("hotkey lock");
        st.bindings.insert(
            name.to_string(),
            BindingEntry {
                vks,
                keys,
                callback: Arc::new(callback),
                was_active: false,
            },
        );
        Ok(())
    }

    pub fn unregister(&self, name: &str) {
        let mut st = self.state.lock().expect("hotkey lock");
        st.bindings.remove(name);
    }

    pub fn clear(&self) {
        let mut st = self.state.lock().expect("hotkey lock");
        st.bindings.clear();
    }

    pub fn registered_keys(&self, name: &str) -> Option<Vec<KeyInfo>> {
        self.state
            .lock()
            .expect("hotkey lock")
            .bindings
            .get(name)
            .map(|b| b.keys.clone())
    }

    pub fn start(&self) {
        if !platform_supports_key_polling() {
            return;
        }
        let mut guard = self.thread.lock().expect("hotkey thread");
        if guard.as_ref().is_some_and(|h| !h.is_finished()) {
            return;
        }
        if let Some(old) = guard.take() {
            let _ = old.join();
        }
        {
            let mut st = self.state.lock().expect("hotkey lock");
            st.stop_requested = false;
        }
        let state = Arc::clone(&self.state);
        let cv = Arc::clone(&self.stop_cv);
        if let Ok(handle) = std::thread::Builder::new()
            .name("KeyForge-Hotkeys".to_string())
            .spawn(move || Self::poll_loop(state, cv))
        {
            *guard = Some(handle);
        }
    }

    pub fn stop(&self) {
        {
            let mut st = self.state.lock().expect("hotkey lock");
            st.stop_requested = true;
            self.stop_cv.notify_all();
        }
        let handle = self.thread.lock().expect("hotkey thread").take();
        if let Some(h) = handle {
            let _ = h.join();
        }
    }

    pub fn trigger_for_test(&self, name: &str) -> bool {
        let cb = {
            let st = self.state.lock().expect("hotkey lock");
            st.bindings.get(name).map(|b| Arc::clone(&b.callback))
        };
        if let Some(callback) = cb {
            callback();
            true
        } else {
            false
        }
    }

    fn poll_loop(state: Arc<Mutex<HotkeyState>>, cv: Arc<Condvar>) {
        loop {
            let interval = {
                let mut st = state.lock().expect("hotkey lock");
                if st.stop_requested {
                    break;
                }
                let wait = if st.active_mode {
                    st.active_interval
                } else {
                    st.idle_interval
                };
                let (guard, _) = cv.wait_timeout(st, wait).expect("hotkey cv wait");
                st = guard;
                if st.stop_requested {
                    break;
                }
                if st.bindings.is_empty() {
                    continue;
                }
                wait
            };
            let _ = interval;

            let callbacks_to_fire: Vec<HotkeyCallback> = {
                let mut st = state.lock().expect("hotkey lock");
                let mut to_fire = Vec::new();
                for entry in st.bindings.values_mut() {
                    if entry.vks.is_empty() {
                        continue;
                    }
                    let primary_vk = entry.vks[entry.vks.len() - 1];
                    let mut all_pressed = platform_get_async_key_state(primary_vk);
                    if all_pressed && entry.vks.len() > 1 {
                        for &mod_vk in &entry.vks[..entry.vks.len() - 1] {
                            if !platform_get_async_key_state(mod_vk) {
                                all_pressed = false;
                                break;
                            }
                        }
                    }

                    if all_pressed && !entry.was_active {
                        entry.was_active = true;
                        to_fire.push(Arc::clone(&entry.callback));
                    } else if !all_pressed && entry.was_active {
                        entry.was_active = false;
                    }
                }
                to_fire
            };

            for cb in callbacks_to_fire {
                cb();
            }
        }
    }
}

impl Drop for GlobalHotkeyManager {
    fn drop(&mut self) {
        self.stop();
    }
}
