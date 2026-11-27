//! Live keyboard & mouse macro recorder with key-tap coalescing and synthetic input filtering.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::engine::backend::KEYFORGE_EXTRA_INFO;
use crate::engine::platform::{
    platform_get_cursor_pos, platform_get_pressed_states, platform_supports_key_polling,
    VK_LBUTTON, VK_MBUTTON, VK_RBUTTON, VK_XBUTTON1, VK_XBUTTON2,
};
use crate::models::action::{
    make_key_down, make_key_press, make_key_up, make_mouse_click, make_mouse_scroll, make_wait,
    ActionType, ClickType, MacroAction, MouseButton,
};
use crate::models::keymap::{all_key_infos, get_key_info_from_vk, parse_key_combo};
use crate::security::validator::MAX_TOTAL_STEPS;

fn mouse_vk_map() -> [(u16, MouseButton); 5] {
    [
        (VK_LBUTTON, MouseButton::Left),
        (VK_RBUTTON, MouseButton::Right),
        (VK_MBUTTON, MouseButton::Middle),
        (VK_XBUTTON1, MouseButton::X1),
        (VK_XBUTTON2, MouseButton::X2),
    ]
}

/// Configuration options for live macro recording.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecorderConfig {
    pub record_delays: bool,
    pub min_delay_ms: i64,
    pub merge_key_taps: bool,
    pub capture_click_coordinates: bool,
    pub max_recorded_steps: usize,
}

impl Default for RecorderConfig {
    fn default() -> Self {
        Self {
            record_delays: true,
            min_delay_ms: 20,
            merge_key_taps: true,
            capture_click_coordinates: true,
            max_recorded_steps: 1000,
        }
    }
}

struct RecorderState {
    config: RecorderConfig,
    is_recording: bool,
    stop_requested: bool,
    actions: Vec<MacroAction>,
    last_event_ts: Option<f64>,
    pending_key_down_ts: HashMap<String, f64>,
    pending_mouse_down_ts: HashMap<MouseButton, (f64, Option<i64>, Option<i64>)>,
    ignored_single_vks: HashSet<u16>,
    ignored_combos: Vec<Vec<u16>>,
    depressed_norm_vks: HashSet<u16>,
}

/// Records user keyboard and mouse actions into editable [`MacroAction`] sequences.
pub struct MacroRecorder {
    state: Arc<Mutex<RecorderState>>,
    stop_cv: Arc<Condvar>,
    poll_thread: Mutex<Option<JoinHandle<()>>>,
    start_ref: Instant,
}

impl Default for MacroRecorder {
    fn default() -> Self {
        Self::new(RecorderConfig::default())
    }
}

impl MacroRecorder {
    pub fn new(config: RecorderConfig) -> Self {
        Self {
            state: Arc::new(Mutex::new(RecorderState {
                config,
                is_recording: false,
                stop_requested: false,
                actions: Vec::new(),
                last_event_ts: None,
                pending_key_down_ts: HashMap::new(),
                pending_mouse_down_ts: HashMap::new(),
                ignored_single_vks: HashSet::new(),
                ignored_combos: Vec::new(),
                depressed_norm_vks: HashSet::new(),
            })),
            stop_cv: Arc::new(Condvar::new()),
            poll_thread: Mutex::new(None),
            start_ref: Instant::now(),
        }
    }

    pub fn set_config(&self, config: RecorderConfig) {
        self.state.lock().expect("recorder lock").config = config;
    }

    pub fn config(&self) -> RecorderConfig {
        self.state.lock().expect("recorder lock").config.clone()
    }

    pub fn is_recording(&self) -> bool {
        self.state.lock().expect("recorder lock").is_recording
    }

    pub fn recorded_actions(&self) -> Vec<MacroAction> {
        self.state.lock().expect("recorder lock").actions.clone()
    }

    pub fn set_ignored_hotkeys(&self, hotkey_combos: &[&str]) {
        let mut st = self.state.lock().expect("recorder lock");
        st.ignored_single_vks.clear();
        st.ignored_combos.clear();
        for &combo in hotkey_combos {
            if let Ok(keys) = parse_key_combo(combo) {
                if keys.len() == 1 {
                    st.ignored_single_vks.insert(keys[0].vk_code);
                } else if keys.len() > 1 {
                    st.ignored_combos
                        .push(keys.into_iter().map(|k| k.vk_code).collect());
                }
            }
        }
    }

    fn matches_ignored_combo(st: &RecorderState, trigger_norm_vk: u16) -> bool {
        for combo_vks in &st.ignored_combos {
            if combo_vks.last().copied() != Some(trigger_norm_vk) {
                continue;
            }
            let all_mods_held = combo_vks[..combo_vks.len() - 1].iter().all(|&mod_vk| {
                let family: &[u16] = match mod_vk {
                    0x11 => &[0x11, 0xA3],
                    0x10 => &[0x10, 0xA1],
                    0x12 => &[0x12, 0xA5],
                    0x5B => &[0x5B, 0x5C],
                    _ => &[mod_vk],
                };
                family.iter().any(|v| st.depressed_norm_vks.contains(v))
            });
            if all_mods_held {
                return true;
            }
        }
        false
    }

    pub fn start(&self, enable_os_poller: bool) {
        let now = self.start_ref.elapsed().as_secs_f64();
        let should_spawn = {
            let mut st = self.state.lock().expect("recorder lock");
            if st.is_recording {
                return;
            }
            st.actions.clear();
            st.last_event_ts = Some(now);
            st.pending_key_down_ts.clear();
            st.pending_mouse_down_ts.clear();
            st.depressed_norm_vks.clear();
            st.stop_requested = false;
            st.is_recording = true;
            enable_os_poller && platform_supports_key_polling()
        };

        if should_spawn {
            let mut guard = self.poll_thread.lock().expect("recorder thread");
            if let Some(old) = guard.take() {
                let _ = old.join();
            }
            let state = Arc::clone(&self.state);
            let cv = Arc::clone(&self.stop_cv);
            let start_ref = self.start_ref;
            if let Ok(handle) = std::thread::Builder::new()
                .name("KeyForge-Recorder".to_string())
                .spawn(move || Self::os_poll_loop(state, cv, start_ref))
            {
                *guard = Some(handle);
            }
        }
    }

    pub fn stop(&self) -> Vec<MacroAction> {
        {
            let mut st = self.state.lock().expect("recorder lock");
            if !st.is_recording {
                return st.actions.clone();
            }
            st.is_recording = false;
            st.stop_requested = true;
            self.stop_cv.notify_all();
        }
        let handle = self.poll_thread.lock().expect("recorder thread").take();
        if let Some(h) = handle {
            let _ = h.join();
        }
        self.state.lock().expect("recorder lock").actions.clone()
    }

    pub fn feed_key_event(
        &self,
        vk_code: u16,
        is_down: bool,
        timestamp_s: Option<f64>,
        extra_info: usize,
    ) {
        if extra_info == KEYFORGE_EXTRA_INFO {
            return;
        }
        let now = timestamp_s.unwrap_or_else(|| self.start_ref.elapsed().as_secs_f64());
        let mut st = self.state.lock().expect("recorder lock");
        if !st.is_recording {
            return;
        }
        let Some(key_info) = get_key_info_from_vk(vk_code) else {
            return;
        };
        let norm_vk = key_info.vk_code;
        if is_down {
            st.depressed_norm_vks.insert(norm_vk);
        } else {
            st.depressed_norm_vks.remove(&norm_vk);
        }
        if st.ignored_single_vks.contains(&vk_code) || st.ignored_single_vks.contains(&norm_vk) {
            return;
        }
        if is_down && Self::matches_ignored_combo(&st, norm_vk) {
            return;
        }

        let cap = st.config.max_recorded_steps.min(MAX_TOTAL_STEPS);
        let key_name = key_info.canonical_name.to_string();

        if is_down {
            if st.actions.len() >= cap {
                return;
            }
            if st.pending_key_down_ts.contains_key(&key_name) {
                return;
            }
            Self::maybe_append_wait(&mut st, now, cap);
            st.pending_key_down_ts.insert(key_name.clone(), now);
            if let Ok(act) = make_key_down(&key_name, "") {
                Self::append_action(&mut st, act, cap);
            }
            st.last_event_ts = Some(now);
        } else {
            let Some(down_ts) = st.pending_key_down_ts.remove(&key_name) else {
                return;
            };
            let hold_ms = (((now - down_ts) * 1000.0).round() as i64).max(10);
            let can_merge = st.config.merge_key_taps
                && st
                    .actions
                    .last()
                    .is_some_and(|a| a.action_type == ActionType::KeyDown && a.key == key_name)
                && hold_ms <= 2000;
            if can_merge {
                if let Ok(merged) = make_key_press(&key_name, hold_ms, "") {
                    if let Some(last) = st.actions.last_mut() {
                        *last = merged;
                    }
                }
            } else {
                if st.actions.len() >= cap {
                    return;
                }
                Self::maybe_append_wait(&mut st, now, cap);
                if let Ok(act) = make_key_up(&key_name, "") {
                    Self::append_action(&mut st, act, cap);
                }
            }
            st.last_event_ts = Some(now);
        }
    }

    pub fn feed_mouse_button_event(
        &self,
        button: MouseButton,
        is_down: bool,
        x: Option<i64>,
        y: Option<i64>,
        timestamp_s: Option<f64>,
        extra_info: usize,
    ) {
        if extra_info == KEYFORGE_EXTRA_INFO {
            return;
        }
        let now = timestamp_s.unwrap_or_else(|| self.start_ref.elapsed().as_secs_f64());
        let mut st = self.state.lock().expect("recorder lock");
        if !st.is_recording {
            return;
        }
        let cap = st.config.max_recorded_steps.min(MAX_TOTAL_STEPS);
        let (coord_x, coord_y) = if st.config.capture_click_coordinates {
            (x, y)
        } else {
            (None, None)
        };

        if is_down {
            if st.actions.len() >= cap {
                return;
            }
            Self::maybe_append_wait(&mut st, now, cap);
            st.pending_mouse_down_ts
                .insert(button, (now, coord_x, coord_y));
            if let Ok(act) = make_mouse_click(button, ClickType::Down, coord_x, coord_y, 25, "") {
                Self::append_action(&mut st, act, cap);
            }
            st.last_event_ts = Some(now);
        } else {
            let Some((down_ts, down_x, down_y)) = st.pending_mouse_down_ts.remove(&button) else {
                return;
            };
            let hold_ms = (((now - down_ts) * 1000.0).round() as i64).max(10);
            let can_merge = st.config.merge_key_taps
                && st.actions.last().is_some_and(|a| {
                    a.action_type == ActionType::MouseClick
                        && a.button == button
                        && a.click_type == ClickType::Down
                })
                && hold_ms <= 2000;
            if can_merge {
                if let Ok(merged) =
                    make_mouse_click(button, ClickType::Single, down_x, down_y, hold_ms, "")
                {
                    if let Some(last) = st.actions.last_mut() {
                        *last = merged;
                    }
                }
            } else {
                if st.actions.len() >= cap {
                    return;
                }
                Self::maybe_append_wait(&mut st, now, cap);
                if let Ok(act) = make_mouse_click(button, ClickType::Up, coord_x, coord_y, 25, "") {
                    Self::append_action(&mut st, act, cap);
                }
            }
            st.last_event_ts = Some(now);
        }
    }

    pub fn feed_mouse_scroll_event(
        &self,
        scroll_dy: i64,
        scroll_dx: i64,
        timestamp_s: Option<f64>,
        extra_info: usize,
    ) {
        if extra_info == KEYFORGE_EXTRA_INFO || (scroll_dx == 0 && scroll_dy == 0) {
            return;
        }
        let now = timestamp_s.unwrap_or_else(|| self.start_ref.elapsed().as_secs_f64());
        let mut st = self.state.lock().expect("recorder lock");
        if !st.is_recording {
            return;
        }
        let cap = st.config.max_recorded_steps.min(MAX_TOTAL_STEPS);
        if st.actions.len() >= cap {
            return;
        }
        Self::maybe_append_wait(&mut st, now, cap);
        if let Ok(act) = make_mouse_scroll(scroll_dy, scroll_dx, "") {
            Self::append_action(&mut st, act, cap);
        }
        st.last_event_ts = Some(now);
    }

    fn maybe_append_wait(st: &mut RecorderState, now: f64, cap: usize) {
        if !st.config.record_delays || st.actions.is_empty() {
            return;
        }
        let Some(last_ts) = st.last_event_ts else {
            return;
        };
        if st.actions.len() + 1 >= cap {
            return;
        }
        let delta_ms = ((now - last_ts) * 1000.0).round() as i64;
        if delta_ms >= st.config.min_delay_ms {
            if let Ok(w) = make_wait(delta_ms.clamp(1, 60_000), 0, "") {
                Self::append_action(st, w, cap);
            }
        }
    }

    fn append_action(st: &mut RecorderState, action: MacroAction, cap: usize) {
        if st.actions.len() < cap {
            st.actions.push(action);
        }
    }

    fn os_poll_loop(state: Arc<Mutex<RecorderState>>, cv: Arc<Condvar>, start_ref: Instant) {
        let monitored_key_vks: Vec<u16> = all_key_infos().iter().map(|k| k.vk_code).collect();
        let mouse_map = mouse_vk_map();
        let monitored_mouse_vks: Vec<u16> = mouse_map.iter().map(|&(vk, _)| vk).collect();

        let (mut prev_keys, mut prev_mouse) =
            platform_get_pressed_states(&monitored_key_vks, &monitored_mouse_vks);

        loop {
            {
                let st = state.lock().expect("recorder lock");
                if st.stop_requested || !st.is_recording {
                    break;
                }
                let (_guard, _) = cv
                    .wait_timeout(st, Duration::from_millis(5))
                    .expect("recorder cv wait");
                if _guard.stop_requested || !_guard.is_recording {
                    break;
                }
            }

            let now = start_ref.elapsed().as_secs_f64();
            let (curr_keys, curr_mouse) =
                platform_get_pressed_states(&monitored_key_vks, &monitored_mouse_vks);

            for &vk in &monitored_key_vks {
                let is_down = curr_keys.get(&vk).copied().unwrap_or(false);
                if is_down != prev_keys.get(&vk).copied().unwrap_or(false) {
                    prev_keys.insert(vk, is_down);
                    Self::feed_key_static(&state, vk, is_down, now);
                }
            }

            let mut cursor_pos: Option<(i64, i64)> = None;
            for &(vk, btn) in &mouse_map {
                let is_down = curr_mouse.get(&vk).copied().unwrap_or(false);
                if is_down != prev_mouse.get(&vk).copied().unwrap_or(false) {
                    prev_mouse.insert(vk, is_down);
                    let (cx, cy) = *cursor_pos.get_or_insert_with(platform_get_cursor_pos);
                    Self::feed_mouse_static(&state, btn, is_down, Some(cx), Some(cy), now);
                }
            }
        }
    }

    fn feed_key_static(state: &Arc<Mutex<RecorderState>>, vk_code: u16, is_down: bool, now: f64) {
        let tmp = MacroRecorder {
            state: Arc::clone(state),
            stop_cv: Arc::new(Condvar::new()),
            poll_thread: Mutex::new(None),
            start_ref: Instant::now(),
        };
        tmp.feed_key_event(vk_code, is_down, Some(now), 0);
    }

    fn feed_mouse_static(
        state: &Arc<Mutex<RecorderState>>,
        button: MouseButton,
        is_down: bool,
        x: Option<i64>,
        y: Option<i64>,
        now: f64,
    ) {
        let tmp = MacroRecorder {
            state: Arc::clone(state),
            stop_cv: Arc::new(Condvar::new()),
            poll_thread: Mutex::new(None),
            start_ref: Instant::now(),
        };
        tmp.feed_mouse_button_event(button, is_down, x, y, Some(now), 0);
    }
}

impl Drop for MacroRecorder {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}
