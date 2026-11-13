"""Macro profile model, repeat modes, and built-in starter presets."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from keyforge.models.action import (
    ClickType,
    MacroAction,
    MouseButton,
    MoveMode,
    _require_int,
    make_key_press,
    make_mouse_click,
    make_mouse_move,
    make_repeat_block,
    make_wait,
)
from keyforge.models.keymap import normalize_key_combo


def _require_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number (got {type(value).__name__}).")
    fval = float(value)
    if not math.isfinite(fval):
        raise ValueError(f"{field_name} must be a finite number.")
    return fval


class RepeatMode(StrEnum):
    """Macro sequence repetition mode."""

    COUNT = "count"  # Run N times and stop
    INFINITE = "infinite"  # Run continuously until stopped or Panic hotkey pressed
    DURATION = "duration"  # Run continuously for N seconds and stop


SCHEMA_VERSION = "1.0"


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class MacroProfile:
    """A complete, serializable KeyForge macro profile."""

    name: str = "Action Loop (E -> 200ms -> Click -> 1)"
    description: str = "Press E -> Wait 200ms -> Left Click -> Press 1"
    schema_version: str = SCHEMA_VERSION
    repeat_mode: RepeatMode = RepeatMode.INFINITE
    repeat_count: int = 10
    repeat_duration_s: float = 30.0
    inter_cycle_delay_ms: int = 100
    start_stop_hotkey: str = "F6"
    panic_hotkey: str = "F12"
    record_hotkey: str = "F7"
    actions: list[MacroAction] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.repeat_mode = RepeatMode(self.repeat_mode)
        self.validate()

    def validate(self) -> None:
        """Validate profile settings, hotkeys, and all child actions."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Profile name must be a non-empty string.")
        if len(self.name) > 100:
            raise ValueError("Profile name cannot exceed 100 characters.")
        if len(self.description) > 500:
            raise ValueError("Profile description cannot exceed 500 characters.")

        _require_int(self.repeat_count, "repeat_count")
        if self.repeat_count < 1 or self.repeat_count > 1_000_000:
            raise ValueError("repeat_count must be between 1 and 1,000,000.")

        self.repeat_duration_s = _require_float(self.repeat_duration_s, "repeat_duration_s")
        if self.repeat_duration_s < 0.1 or self.repeat_duration_s > 86_400:
            raise ValueError("repeat_duration_s must be between 0.1 and 86,400 seconds (24h).")

        _require_int(self.inter_cycle_delay_ms, "inter_cycle_delay_ms")
        if self.inter_cycle_delay_ms < 0 or self.inter_cycle_delay_ms > 3_600_000:
            raise ValueError("inter_cycle_delay_ms must be between 0 and 3,600,000 ms.")

        self.start_stop_hotkey = normalize_key_combo(self.start_stop_hotkey)
        self.panic_hotkey = normalize_key_combo(self.panic_hotkey)
        self.record_hotkey = normalize_key_combo(self.record_hotkey)

        if len({self.start_stop_hotkey, self.panic_hotkey, self.record_hotkey}) < 3:
            raise ValueError("start_stop_hotkey, panic_hotkey, and record_hotkey must be distinct keys.")

        for action in self.actions:
            action.validate()

    def total_cycle_wait_ms(self) -> int:
        """Compute the minimum guaranteed wait time (in ms) within a single enabled cycle."""

        def _sum_waits(items: list[MacroAction]) -> int:
            total = 0
            for item in items:
                if not item.enabled:
                    continue
                if item.action_type.value == "wait":
                    total += item.wait_ms
                elif item.action_type.value in ("key_press", "mouse_click"):
                    total += item.hold_ms
                elif item.action_type.value == "repeat_block":
                    total += item.repeat_count * _sum_waits(item.sub_actions)
            return total

        return _sum_waits(self.actions) + self.inter_cycle_delay_ms

    def to_payload_dict(self) -> dict[str, Any]:
        """Serialize profile data (excluding external integrity wrapper) to a dictionary."""
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "repeat_mode": self.repeat_mode.value,
            "repeat_count": self.repeat_count,
            "repeat_duration_s": float(self.repeat_duration_s),
            "inter_cycle_delay_ms": self.inter_cycle_delay_ms,
            "start_stop_hotkey": self.start_stop_hotkey,
            "panic_hotkey": self.panic_hotkey,
            "record_hotkey": self.record_hotkey,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "actions": [a.to_dict() for a in self.actions],
        }

    @classmethod
    def from_payload_dict(cls, data: dict[str, Any]) -> MacroProfile:
        """Construct and validate a MacroProfile from a dictionary payload."""
        if not isinstance(data, dict):
            raise ValueError("Profile payload must be a dictionary.")

        raw_actions = data.get("actions", [])
        if not isinstance(raw_actions, list):
            raise ValueError("'actions' must be a list.")

        actions = [MacroAction.from_dict(item) for item in raw_actions]
        return cls(
            name=str(data.get("name", "Untitled Profile")),
            description=str(data.get("description", "")),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            repeat_mode=RepeatMode(data.get("repeat_mode", RepeatMode.COUNT.value)),
            repeat_count=_require_int(data.get("repeat_count", 10), "repeat_count"),
            repeat_duration_s=_require_float(data.get("repeat_duration_s", 30.0), "repeat_duration_s"),
            inter_cycle_delay_ms=_require_int(data.get("inter_cycle_delay_ms", 100), "inter_cycle_delay_ms"),
            start_stop_hotkey=str(data.get("start_stop_hotkey", "F6")),
            panic_hotkey=str(data.get("panic_hotkey", "F12")),
            record_hotkey=str(data.get("record_hotkey", "F7")),
            actions=actions,
            created_at=str(data.get("created_at") or _utc_now_iso()),
            updated_at=str(data.get("updated_at") or _utc_now_iso()),
        )


def create_default_profile() -> MacroProfile:
    """Create the flagship default profile: Press E -> Wait 200ms -> Click -> Press 1."""
    return MacroProfile(
        name="Action Loop (E -> 200ms -> Click -> 1)",
        description="Classic action combo: Press E -> Wait 200ms -> Left Click -> Press 1",
        repeat_mode=RepeatMode.INFINITE,
        repeat_count=10,
        inter_cycle_delay_ms=100,
        actions=[
            make_key_press("E", hold_ms=25, label="Interact"),
            make_wait(200, jitter_ms=10, label="Animation Delay"),
            make_mouse_click(MouseButton.LEFT, ClickType.SINGLE, label="Primary Attack / Confirm"),
            make_wait(100, jitter_ms=5, label="Post-Click Pause"),
            make_key_press("1", hold_ms=25, label="Equip Slot 1"),
        ],
    )


def get_builtin_presets() -> dict[str, MacroProfile]:
    """Return built-in ready-to-run macro presets."""
    presets = [
        create_default_profile(),
        MacroProfile(
            name="Simple Auto-Clicker (10 CPS)",
            description="Left Click every 100ms (10 CPS) with ±10ms natural timing jitter.",
            repeat_mode=RepeatMode.INFINITE,
            repeat_count=100,
            inter_cycle_delay_ms=0,
            actions=[
                make_mouse_click(MouseButton.LEFT, ClickType.SINGLE, hold_ms=15, label="Left Click"),
                make_wait(85, jitter_ms=10, label="10 CPS Interval"),
            ],
        ),
        MacroProfile(
            name="Key Spammer (Press E Loop)",
            description="Repeatedly taps [E] every 100ms with ±10ms natural timing jitter.",
            repeat_mode=RepeatMode.INFINITE,
            repeat_count=100,
            inter_cycle_delay_ms=0,
            actions=[
                make_key_press("E", hold_ms=25, label="Tap E"),
                make_wait(75, jitter_ms=10, label="Repeat Interval"),
            ],
        ),
        MacroProfile(
            name="Humanized Auto-Clicker (20 CPS)",
            description="Rapid Left Click every 50ms with ±12ms natural timing jitter.",
            repeat_mode=RepeatMode.INFINITE,
            repeat_count=100,
            inter_cycle_delay_ms=0,
            actions=[
                make_mouse_click(MouseButton.LEFT, ClickType.SINGLE, hold_ms=15, label="Fast Click"),
                make_wait(35, jitter_ms=12, label="Humanized Interval"),
            ],
        ),
        MacroProfile(
            name="Loot & Salvage Nested Loop",
            description="Repeats 'Press F -> Wait 120ms' 3 times, then clicks Confirm.",
            repeat_mode=RepeatMode.COUNT,
            repeat_count=20,
            inter_cycle_delay_ms=250,
            actions=[
                make_repeat_block(
                    repeat_count=3,
                    label="Collect 3 Items",
                    sub_actions=[
                        make_key_press("F", hold_ms=25),
                        make_wait(120, jitter_ms=15),
                    ],
                ),
                make_mouse_click(MouseButton.LEFT, ClickType.SINGLE, label="Confirm Dialog"),
                make_wait(200, jitter_ms=20),
            ],
        ),
        MacroProfile(
            name="Anti-AFK Subtle Pulse",
            description="Gently nudges cursor relative (+4, 0) and (-4, 0) with Space tap every 5s.",
            repeat_mode=RepeatMode.DURATION,
            repeat_duration_s=600.0,
            inter_cycle_delay_ms=5000,
            actions=[
                make_mouse_move(4, 0, MoveMode.RELATIVE, label="Nudge Right"),
                make_wait(150, jitter_ms=25),
                make_mouse_move(-4, 0, MoveMode.RELATIVE, label="Nudge Left"),
                make_wait(150, jitter_ms=25),
                make_key_press("Space", hold_ms=30, label="Jump / Keep-Alive"),
            ],
        ),
    ]
    return {p.name: p for p in presets}
