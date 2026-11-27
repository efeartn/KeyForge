//! Thread-safe macro execution engine with Panic Kill-Switch and EmergencyInputSanitizer.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use crate::engine::backend::InputBackend;
use crate::models::action::{ActionType, ClickType, MacroAction, MouseButton, MoveMode};
use crate::models::keymap::{parse_key_combo, resolve_single_key, KeyInfo};
use crate::models::profile::{MacroProfile, RepeatMode};
use crate::security::validator::{validate_profile_complexity, MIN_SAFE_CYCLE_DELAY_MS};

/// Lifecycle states of the [`MacroExecutor`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutorState {
    Idle,
    Running,
    Stopping,
    PanicAborted,
    Error,
}

impl ExecutorState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Running => "running",
            Self::Stopping => "stopping",
            Self::PanicAborted => "panic_aborted",
            Self::Error => "error",
        }
    }
}

/// Snapshot of live execution metrics.
#[derive(Debug, Clone)]
pub struct ExecutionTelemetry {
    pub state: ExecutorState,
    pub cycles_completed: u64,
    pub actions_executed: u64,
    pub current_action_id: Option<String>,
    pub elapsed_seconds: f64,
    pub last_error: Option<String>,
}

impl Default for ExecutionTelemetry {
    fn default() -> Self {
        Self {
            state: ExecutorState::Idle,
            cycles_completed: 0,
            actions_executed: 0,
            current_action_id: None,
            elapsed_seconds: 0.0,
            last_error: None,
        }
    }
}

struct SharedControl {
    backend: Arc<dyn InputBackend>,
    stop_requested: bool,
    panic_flag: bool,
    running: bool,
    telemetry: ExecutionTelemetry,
    start_instant: Option<Instant>,
    held_key_order: Vec<KeyInfo>,
    held_keys: HashMap<u16, KeyInfo>,
    held_buttons: HashSet<MouseButton>,
    rng: StdRng,
    time_scale: f64,
}

/// Executes [`MacroProfile`]s with sub-millisecond cancelability and guaranteed input release.
pub struct MacroExecutor {
    ctrl: Arc<Mutex<SharedControl>>,
    stop_cv: Arc<Condvar>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl MacroExecutor {
    pub fn new(backend: Arc<dyn InputBackend>, time_scale: f64, rng_seed: Option<u64>) -> Self {
        let rng = match rng_seed {
            Some(seed) => StdRng::seed_from_u64(seed),
            None => StdRng::from_entropy(),
        };
        Self {
            ctrl: Arc::new(Mutex::new(SharedControl {
                backend,
                stop_requested: false,
                panic_flag: false,
                running: false,
                telemetry: ExecutionTelemetry::default(),
                start_instant: None,
                held_key_order: Vec::new(),
                held_keys: HashMap::new(),
                held_buttons: HashSet::new(),
                rng,
                time_scale: time_scale.max(0.0),
            })),
            stop_cv: Arc::new(Condvar::new()),
            worker: Mutex::new(None),
        }
    }

    pub fn set_backend(&self, new_backend: Arc<dyn InputBackend>) -> Result<(), String> {
        let mut st = self.ctrl.lock().expect("executor lock");
        if st.running {
            return Err("Cannot swap input backend while macro is running.".to_string());
        }
        st.backend = new_backend;
        Ok(())
    }

    pub fn is_running(&self) -> bool {
        let st = self.ctrl.lock().expect("executor lock");
        st.running
            || matches!(
                st.telemetry.state,
                ExecutorState::Running | ExecutorState::Stopping
            )
    }

    pub fn telemetry(&self) -> ExecutionTelemetry {
        let st = self.ctrl.lock().expect("executor lock");
        let mut snap = st.telemetry.clone();
        if st.running {
            if let Some(started) = st.start_instant {
                snap.elapsed_seconds = started.elapsed().as_secs_f64();
            }
        }
        snap
    }

    /// Start executing an isolated snapshot of `profile` on a background thread.
    pub fn start(&self, mut profile: MacroProfile) -> Result<bool, String> {
        validate_profile_complexity(&mut profile).map_err(|e| e.to_string())?;

        // Reclaim finished join handle if any
        {
            let mut guard = self.worker.lock().expect("worker lock");
            if guard.as_ref().is_some_and(|h| !h.is_finished()) {
                return Ok(false);
            }
            if let Some(old) = guard.take() {
                let _ = old.join();
            }

            {
                let mut st = self.ctrl.lock().expect("executor lock");
                if st.running {
                    return Ok(false);
                }
                st.stop_requested = false;
                st.panic_flag = false;
                st.running = true;
                st.start_instant = Some(Instant::now());
                st.telemetry = ExecutionTelemetry {
                    state: ExecutorState::Running,
                    ..ExecutionTelemetry::default()
                };
            }

            let ctrl = Arc::clone(&self.ctrl);
            let cv = Arc::clone(&self.stop_cv);
            let handle = std::thread::Builder::new()
                .name("KeyForge-Executor".to_string())
                .spawn(move || Self::run_worker(ctrl, cv, profile))
                .map_err(|e| format!("Failed to spawn executor thread: {e}"))?;
            *guard = Some(handle);
        }
        Ok(true)
    }

    /// Execute a [`MacroProfile`] synchronously on the calling thread (for CLI and tests).
    pub fn run_blocking(&self, mut profile: MacroProfile) -> Result<ExecutionTelemetry, String> {
        validate_profile_complexity(&mut profile).map_err(|e| e.to_string())?;
        {
            let mut st = self.ctrl.lock().expect("executor lock");
            if st.running {
                return Err("Executor is already running.".to_string());
            }
            st.stop_requested = false;
            st.panic_flag = false;
            st.running = true;
            st.start_instant = Some(Instant::now());
            st.telemetry = ExecutionTelemetry {
                state: ExecutorState::Running,
                ..ExecutionTelemetry::default()
            };
        }
        Self::run_worker(Arc::clone(&self.ctrl), Arc::clone(&self.stop_cv), profile);
        Ok(self.telemetry())
    }

    /// Request graceful stop of the active macro and wait up to `timeout` for completion.
    pub fn stop(&self, timeout: Duration) {
        {
            let mut st = self.ctrl.lock().expect("executor lock");
            if !st.running {
                return;
            }
            st.stop_requested = true;
            st.telemetry.state = ExecutorState::Stopping;
            self.stop_cv.notify_all();
        }
        self.join_worker_until(timeout);
    }

    /// Hardware Panic Kill-Switch: immediately abort execution and release all held inputs.
    pub fn panic_abort(&self, timeout: Duration) {
        {
            let mut st = self.ctrl.lock().expect("executor lock");
            st.panic_flag = true;
            st.stop_requested = true;
            Self::release_all_held_locked(&mut st);
            if st.telemetry.state != ExecutorState::Idle {
                st.telemetry.state = ExecutorState::PanicAborted;
            }
            self.stop_cv.notify_all();
        }
        self.join_worker_until(timeout);
    }

    fn join_worker_until(&self, timeout: Duration) {
        let maybe_handle = {
            let mut guard = self.worker.lock().expect("worker lock");
            guard.take()
        };
        if let Some(handle) = maybe_handle {
            let deadline = Instant::now() + timeout;
            while !handle.is_finished() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(2));
            }
            if handle.is_finished() {
                let _ = handle.join();
            } else {
                // Keep thread handle for later cleanup if still winding down
                let mut guard = self.worker.lock().expect("worker lock");
                if guard.is_none() {
                    *guard = Some(handle);
                }
            }
        }
    }

    fn run_worker(ctrl: Arc<Mutex<SharedControl>>, cv: Arc<Condvar>, profile: MacroProfile) {
        let enabled_actions: Vec<MacroAction> = profile
            .actions
            .iter()
            .filter(|a| a.enabled)
            .cloned()
            .collect();

        let res = (|| -> Result<(), String> {
            if enabled_actions.is_empty() {
                return Ok(());
            }

            let mut cycle_index: i64 = 0;
            loop {
                {
                    let st = ctrl.lock().expect("executor lock");
                    if st.stop_requested {
                        break;
                    }
                    if profile.repeat_mode == RepeatMode::Count
                        && cycle_index >= profile.repeat_count
                    {
                        break;
                    }
                    if profile.repeat_mode == RepeatMode::Duration {
                        if let Some(started) = st.start_instant {
                            if started.elapsed().as_secs_f64() >= profile.repeat_duration_s {
                                break;
                            }
                        }
                    }
                }

                let cycle_start = Instant::now();
                Self::execute_action_list(&ctrl, &cv, &enabled_actions, &profile)?;

                let has_more_cycles = {
                    let mut st = ctrl.lock().expect("executor lock");
                    if st.stop_requested {
                        break;
                    }
                    cycle_index += 1;
                    st.telemetry.cycles_completed = cycle_index as u64;
                    if let Some(started) = st.start_instant {
                        st.telemetry.elapsed_seconds = started.elapsed().as_secs_f64();
                    }

                    match profile.repeat_mode {
                        RepeatMode::Count => cycle_index < profile.repeat_count,
                        RepeatMode::Duration => st
                            .start_instant
                            .map(|s| s.elapsed().as_secs_f64() < profile.repeat_duration_s)
                            .unwrap_or(false),
                        RepeatMode::Infinite => true,
                    }
                };

                if !has_more_cycles {
                    break;
                }

                let mut inter_delay_ms = profile.inter_cycle_delay_ms;
                let cycle_elapsed_ms = cycle_start.elapsed().as_secs_f64() * 1000.0;
                if inter_delay_ms <= 0 && cycle_elapsed_ms < (MIN_SAFE_CYCLE_DELAY_MS as f64) {
                    inter_delay_ms = MIN_SAFE_CYCLE_DELAY_MS;
                }
                if inter_delay_ms > 0 {
                    Self::interruptible_wait(&ctrl, &cv, inter_delay_ms, 0);
                }
            }
            Ok(())
        })();

        let mut st = ctrl.lock().expect("executor lock");
        Self::release_all_held_locked(&mut st);
        st.telemetry.current_action_id = None;
        if let Some(started) = st.start_instant {
            st.telemetry.elapsed_seconds = started.elapsed().as_secs_f64();
        }
        if let Err(err) = res {
            st.telemetry.state = ExecutorState::Error;
            st.telemetry.last_error = Some(err);
        } else if st.panic_flag {
            st.telemetry.state = ExecutorState::PanicAborted;
        } else if st.telemetry.state != ExecutorState::Error {
            st.telemetry.state = ExecutorState::Idle;
        }
        st.running = false;
    }

    fn execute_action_list(
        ctrl: &Arc<Mutex<SharedControl>>,
        cv: &Arc<Condvar>,
        actions: &[MacroAction],
        profile: &MacroProfile,
    ) -> Result<(), String> {
        for action in actions {
            if !action.enabled {
                continue;
            }
            {
                let mut st = ctrl.lock().expect("executor lock");
                if st.stop_requested {
                    return Ok(());
                }
                if profile.repeat_mode == RepeatMode::Duration {
                    if let Some(started) = st.start_instant {
                        if started.elapsed().as_secs_f64() >= profile.repeat_duration_s {
                            st.stop_requested = true;
                            cv.notify_all();
                            return Ok(());
                        }
                    }
                }
                st.telemetry.current_action_id = Some(action.id.clone());
            }
            Self::execute_single_action(ctrl, cv, action, profile)?;
        }
        Ok(())
    }

    fn execute_single_action(
        ctrl: &Arc<Mutex<SharedControl>>,
        cv: &Arc<Condvar>,
        action: &MacroAction,
        profile: &MacroProfile,
    ) -> Result<(), String> {
        match action.action_type {
            ActionType::KeyPress => {
                let keys = parse_key_combo(&action.key)?;
                for &k in &keys {
                    if ctrl.lock().expect("executor lock").stop_requested {
                        return Ok(());
                    }
                    Self::safe_key_down(ctrl, k);
                }
                if action.hold_ms > 0 {
                    Self::interruptible_wait(ctrl, cv, action.hold_ms, 0);
                }
                for &k in keys.iter().rev() {
                    Self::safe_key_up(ctrl, k);
                }
                Self::increment_actions(ctrl);
            }
            ActionType::KeyDown => {
                let k = resolve_single_key(&action.key)?;
                Self::safe_key_down(ctrl, k);
                Self::increment_actions(ctrl);
            }
            ActionType::KeyUp => {
                let k = resolve_single_key(&action.key)?;
                Self::safe_key_up(ctrl, k);
                Self::increment_actions(ctrl);
            }
            ActionType::MouseClick => {
                if let (Some(cx), Some(cy)) = (action.x, action.y) {
                    let backend = Arc::clone(&ctrl.lock().expect("executor lock").backend);
                    backend.move_cursor(cx, cy, false);
                }
                let btn = action.button;
                match action.click_type {
                    ClickType::Single => {
                        Self::safe_mouse_down(ctrl, btn);
                        if action.hold_ms > 0 {
                            Self::interruptible_wait(ctrl, cv, action.hold_ms, 0);
                        }
                        Self::safe_mouse_up(ctrl, btn);
                    }
                    ClickType::Double => {
                        for idx in 0..2 {
                            if ctrl.lock().expect("executor lock").stop_requested {
                                break;
                            }
                            Self::safe_mouse_down(ctrl, btn);
                            if action.hold_ms > 0 {
                                Self::interruptible_wait(ctrl, cv, action.hold_ms, 0);
                            }
                            Self::safe_mouse_up(ctrl, btn);
                            if idx == 0 && !ctrl.lock().expect("executor lock").stop_requested {
                                Self::interruptible_wait(ctrl, cv, 40, 0);
                            }
                        }
                    }
                    ClickType::Down => Self::safe_mouse_down(ctrl, btn),
                    ClickType::Up => Self::safe_mouse_up(ctrl, btn),
                }
                Self::increment_actions(ctrl);
            }
            ActionType::MouseMove => {
                if let (Some(cx), Some(cy)) = (action.x, action.y) {
                    let backend = Arc::clone(&ctrl.lock().expect("executor lock").backend);
                    backend.move_cursor(cx, cy, action.move_mode == MoveMode::Relative);
                    Self::increment_actions(ctrl);
                }
            }
            ActionType::MouseScroll => {
                let backend = Arc::clone(&ctrl.lock().expect("executor lock").backend);
                backend.scroll(action.scroll_dx, action.scroll_dy);
                Self::increment_actions(ctrl);
            }
            ActionType::Wait => {
                Self::interruptible_wait(ctrl, cv, action.wait_ms, action.jitter_ms);
                Self::increment_actions(ctrl);
            }
            ActionType::RepeatBlock => {
                let subs: Vec<MacroAction> = action
                    .sub_actions
                    .iter()
                    .filter(|s| s.enabled)
                    .cloned()
                    .collect();
                for _ in 0..action.repeat_count {
                    if ctrl.lock().expect("executor lock").stop_requested {
                        break;
                    }
                    Self::execute_action_list(ctrl, cv, &subs, profile)?;
                }
            }
        }
        Ok(())
    }

    fn safe_key_down(ctrl: &Arc<Mutex<SharedControl>>, key: KeyInfo) {
        let backend = {
            let mut st = ctrl.lock().expect("executor lock");
            if st.stop_requested {
                return;
            }
            if !st.held_keys.contains_key(&key.vk_code) {
                st.held_key_order.push(key);
            }
            st.held_keys.insert(key.vk_code, key);
            Arc::clone(&st.backend)
        };
        backend.key_down(key);
    }

    fn safe_key_up(ctrl: &Arc<Mutex<SharedControl>>, key: KeyInfo) {
        let backend = {
            let mut st = ctrl.lock().expect("executor lock");
            st.held_keys.remove(&key.vk_code);
            st.held_key_order.retain(|k| k.vk_code != key.vk_code);
            Arc::clone(&st.backend)
        };
        backend.key_up(key);
    }

    fn safe_mouse_down(ctrl: &Arc<Mutex<SharedControl>>, button: MouseButton) {
        let backend = {
            let mut st = ctrl.lock().expect("executor lock");
            if st.stop_requested {
                return;
            }
            st.held_buttons.insert(button);
            Arc::clone(&st.backend)
        };
        backend.mouse_down(button);
    }

    fn safe_mouse_up(ctrl: &Arc<Mutex<SharedControl>>, button: MouseButton) {
        let backend = {
            let mut st = ctrl.lock().expect("executor lock");
            st.held_buttons.remove(&button);
            Arc::clone(&st.backend)
        };
        backend.mouse_up(button);
    }

    fn release_all_held_locked(st: &mut SharedControl) {
        let keys: Vec<KeyInfo> = st.held_key_order.drain(..).rev().collect();
        st.held_keys.clear();
        for key in keys {
            st.backend.key_up(key);
        }
        let buttons: Vec<MouseButton> = st.held_buttons.drain().collect();
        for btn in buttons {
            st.backend.mouse_up(btn);
        }
    }

    fn interruptible_wait(
        ctrl: &Arc<Mutex<SharedControl>>,
        cv: &Arc<Condvar>,
        wait_ms: i64,
        jitter_ms: i64,
    ) {
        let mut st = ctrl.lock().expect("executor lock");
        let mut effective_ms = wait_ms as f64;
        if jitter_ms > 0 {
            let j = jitter_ms as f64;
            let delta = st.rng.gen_range(-j..=j);
            effective_ms = (effective_ms + delta).max(1.0);
        }
        let duration_s = (effective_ms / 1000.0) * st.time_scale;
        if duration_s <= 0.0 {
            return;
        }
        let target_dur = Duration::from_secs_f64(duration_s);
        let deadline = Instant::now() + target_dur;
        while !st.stop_requested {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            let (guard, timeout_res) = cv
                .wait_timeout(st, deadline.saturating_duration_since(now))
                .expect("condvar wait");
            st = guard;
            if timeout_res.timed_out() {
                break;
            }
        }
    }

    fn increment_actions(ctrl: &Arc<Mutex<SharedControl>>) {
        let mut st = ctrl.lock().expect("executor lock");
        st.telemetry.actions_executed += 1;
        if let Some(started) = st.start_instant {
            st.telemetry.elapsed_seconds = started.elapsed().as_secs_f64();
        }
    }
}
