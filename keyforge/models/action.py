"""Data models for individual macro actions and step primitives."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from keyforge.models.keymap import normalize_key_combo, resolve_single_key


class ActionType(StrEnum):
    """Supported primitive and composite macro step types."""

    KEY_PRESS = "key_press"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    MOUSE_CLICK = "mouse_click"
    MOUSE_MOVE = "mouse_move"
    MOUSE_SCROLL = "mouse_scroll"
    WAIT = "wait"
    REPEAT_BLOCK = "repeat_block"


class MouseButton(StrEnum):
    """Supported mouse buttons."""

    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    X1 = "x1"
    X2 = "x2"


class ClickType(StrEnum):
    """Mouse button actuation mode."""

    SINGLE = "single"
    DOUBLE = "double"
    DOWN = "down"
    UP = "up"


class MoveMode(StrEnum):
    """Cursor movement coordinate mode."""

    ABSOLUTE = "absolute"
    RELATIVE = "relative"


def _new_action_id() -> str:
    return uuid.uuid4().hex[:12]


def _require_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer (got {type(value).__name__}).")
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean (got {type(value).__name__}).")
    return value


@dataclass(slots=True)
class MacroAction:
    """Represents a single step in a KeyForge macro sequence."""

    action_type: ActionType
    id: str = field(default_factory=_new_action_id)
    enabled: bool = True
    label: str = ""

    # Keyboard parameters
    key: str = "E"
    hold_ms: int = 25

    # Mouse parameters
    button: MouseButton = MouseButton.LEFT
    click_type: ClickType = ClickType.SINGLE
    x: int | None = None
    y: int | None = None
    move_mode: MoveMode = MoveMode.ABSOLUTE
    scroll_dx: int = 0
    scroll_dy: int = 0

    # Timing parameters
    wait_ms: int = 200
    jitter_ms: int = 0

    # Repeat block parameters
    repeat_count: int = 2
    sub_actions: list[MacroAction] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.action_type = ActionType(self.action_type)
        self.button = MouseButton(self.button)
        self.click_type = ClickType(self.click_type)
        self.move_mode = MoveMode(self.move_mode)
        self.validate()

    def validate(self) -> None:
        """Validate action fields according to action_type."""
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Action id must be a non-empty string.")
        _require_bool(self.enabled, "enabled")
        if not isinstance(self.label, str) or len(self.label) > 120:
            raise ValueError("Action label cannot exceed 120 characters.")

        if self.action_type == ActionType.KEY_PRESS:
            self.key = normalize_key_combo(self.key)
            _require_int(self.hold_ms, "hold_ms")
            if self.hold_ms < 0 or self.hold_ms > 60_000:
                raise ValueError("hold_ms must be between 0 and 60000 ms.")

        elif self.action_type in (ActionType.KEY_DOWN, ActionType.KEY_UP):
            # Single key for explicit down/up to maintain deterministic state tracking
            info = resolve_single_key(self.key)
            self.key = info.canonical_name

        elif self.action_type == ActionType.MOUSE_CLICK:
            _require_int(self.hold_ms, "hold_ms")
            if (self.x is None) != (self.y is None):
                raise ValueError("Mouse click coordinates x and y must both be set or both be None.")
            if self.x is not None and self.y is not None:
                _require_int(self.x, "x")
                _require_int(self.y, "y")
                if not (-32768 <= self.x <= 32767 and -32768 <= self.y <= 32767):
                    raise ValueError("Mouse click coordinates must be within [-32768, 32767].")
            if self.hold_ms < 0 or self.hold_ms > 60_000:
                raise ValueError("hold_ms must be between 0 and 60000 ms.")

        elif self.action_type == ActionType.MOUSE_MOVE:
            if self.x is None or self.y is None:
                raise ValueError("MOUSE_MOVE requires both x and y integer coordinates.")
            _require_int(self.x, "x")
            _require_int(self.y, "y")
            if not (-32768 <= self.x <= 32767 and -32768 <= self.y <= 32767):
                raise ValueError("Mouse move coordinates must be within [-32768, 32767].")

        elif self.action_type == ActionType.MOUSE_SCROLL:
            _require_int(self.scroll_dx, "scroll_dx")
            _require_int(self.scroll_dy, "scroll_dy")
            if not (-1000 <= self.scroll_dx <= 1000 and -1000 <= self.scroll_dy <= 1000):
                raise ValueError("Mouse scroll ticks must be within [-1000, 1000].")
            if self.scroll_dx == 0 and self.scroll_dy == 0:
                raise ValueError("Mouse scroll requires non-zero scroll_dx or scroll_dy.")

        elif self.action_type == ActionType.WAIT:
            _require_int(self.wait_ms, "wait_ms")
            _require_int(self.jitter_ms, "jitter_ms")
            if self.wait_ms < 1 or self.wait_ms > 3_600_000:
                raise ValueError("wait_ms must be between 1 and 3,600,000 ms (1 hour).")
            if self.jitter_ms < 0 or self.jitter_ms > 600_000:
                raise ValueError("jitter_ms must be between 0 and 600,000 ms.")

        elif self.action_type == ActionType.REPEAT_BLOCK:
            _require_int(self.repeat_count, "repeat_count")
            if self.repeat_count < 1 or self.repeat_count > 10_000:
                raise ValueError("repeat_count must be between 1 and 10,000.")
            for sub in self.sub_actions:
                sub.validate()

    def badge_text(self) -> str:
        """Short uppercase badge tag for UI step cards."""
        mapping = {
            ActionType.KEY_PRESS: "KEY",
            ActionType.KEY_DOWN: "KEY ↓",
            ActionType.KEY_UP: "KEY ↑",
            ActionType.MOUSE_CLICK: "CLICK",
            ActionType.MOUSE_MOVE: "MOVE",
            ActionType.MOUSE_SCROLL: "SCROLL",
            ActionType.WAIT: "WAIT",
            ActionType.REPEAT_BLOCK: "LOOP",
        }
        return mapping[self.action_type]

    def describe(self) -> str:
        """Generate a concise, human-friendly summary of the action."""
        prefix = f"[{self.label}] " if self.label else ""
        if self.action_type == ActionType.KEY_PRESS:
            hold_str = f" (hold {self.hold_ms}ms)" if self.hold_ms != 25 else ""
            return f"{prefix}Press [{self.key}]{hold_str}"
        if self.action_type == ActionType.KEY_DOWN:
            return f"{prefix}Hold Key Down [{self.key}]"
        if self.action_type == ActionType.KEY_UP:
            return f"{prefix}Release Key Up [{self.key}]"
        if self.action_type == ActionType.MOUSE_CLICK:
            btn = self.button.value.capitalize()
            ctype = {
                ClickType.SINGLE: "Click",
                ClickType.DOUBLE: "Double-Click",
                ClickType.DOWN: "Button Down",
                ClickType.UP: "Button Up",
            }[self.click_type]
            loc = f" at ({self.x}, {self.y})" if self.x is not None and self.y is not None else " at Cursor"
            return f"{prefix}{btn} {ctype}{loc}"
        if self.action_type == ActionType.MOUSE_MOVE:
            if self.move_mode == MoveMode.RELATIVE:
                return f"{prefix}Move Cursor Relative ({self.x:+d}, {self.y:+d})"
            return f"{prefix}Move Cursor to ({self.x}, {self.y})"
        if self.action_type == ActionType.MOUSE_SCROLL:
            parts: list[str] = []
            if self.scroll_dy != 0:
                direction = "Up" if self.scroll_dy > 0 else "Down"
                parts.append(f"{direction} {abs(self.scroll_dy)}")
            if self.scroll_dx != 0:
                direction = "Right" if self.scroll_dx > 0 else "Left"
                parts.append(f"{direction} {abs(self.scroll_dx)}")
            return f"{prefix}Scroll Wheel ({', '.join(parts)})"
        if self.action_type == ActionType.WAIT:
            jitter = f" (±{self.jitter_ms}ms jitter)" if self.jitter_ms > 0 else ""
            return f"{prefix}Wait {self.wait_ms}ms{jitter}"
        if self.action_type == ActionType.REPEAT_BLOCK:
            inner = " -> ".join(s.describe() for s in self.sub_actions[:4])
            if len(self.sub_actions) > 4:
                inner += f" ... (+{len(self.sub_actions) - 4} more)"
            return f"{prefix}Repeat {self.repeat_count}x {{ {inner} }}"
        return f"{prefix}{self.action_type.value}"

    def clone(self) -> MacroAction:
        """Create a deep copy with a fresh unique action ID."""
        data = self.to_dict()
        data["id"] = _new_action_id()
        cloned = MacroAction.from_dict(data)
        if cloned.sub_actions:
            cloned.sub_actions = [s.clone() for s in self.sub_actions]
        return cloned

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a compact, deterministic dictionary."""
        base: dict[str, Any] = {
            "id": self.id,
            "action_type": self.action_type.value,
            "enabled": self.enabled,
        }
        if self.label:
            base["label"] = self.label

        if self.action_type == ActionType.KEY_PRESS:
            base["key"] = self.key
            base["hold_ms"] = self.hold_ms
        elif self.action_type in (ActionType.KEY_DOWN, ActionType.KEY_UP):
            base["key"] = self.key
        elif self.action_type == ActionType.MOUSE_CLICK:
            base["button"] = self.button.value
            base["click_type"] = self.click_type.value
            base["hold_ms"] = self.hold_ms
            base["x"] = self.x
            base["y"] = self.y
        elif self.action_type == ActionType.MOUSE_MOVE:
            base["x"] = self.x
            base["y"] = self.y
            base["move_mode"] = self.move_mode.value
        elif self.action_type == ActionType.MOUSE_SCROLL:
            base["scroll_dx"] = self.scroll_dx
            base["scroll_dy"] = self.scroll_dy
        elif self.action_type == ActionType.WAIT:
            base["wait_ms"] = self.wait_ms
            base["jitter_ms"] = self.jitter_ms
        elif self.action_type == ActionType.REPEAT_BLOCK:
            base["repeat_count"] = self.repeat_count
            base["sub_actions"] = [s.to_dict() for s in self.sub_actions]

        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MacroAction:
        """Deserialize a MacroAction from a dictionary with strict validation."""
        if not isinstance(data, dict):
            raise ValueError("MacroAction data must be a dictionary.")

        raw_type = data.get("action_type")
        if not isinstance(raw_type, str):
            raise ValueError("Missing or invalid 'action_type'.")
        action_type = ActionType(raw_type)

        sub_raw = data.get("sub_actions", [])
        if not isinstance(sub_raw, list):
            raise ValueError("'sub_actions' must be a list.")
        sub_actions = [cls.from_dict(item) for item in sub_raw]

        return cls(
            id=str(data.get("id") or _new_action_id()),
            action_type=action_type,
            enabled=_require_bool(data.get("enabled", True), "enabled"),
            label=str(data.get("label", "")),
            key=str(data.get("key", "E")),
            hold_ms=_require_int(data.get("hold_ms", 25), "hold_ms"),
            button=MouseButton(data.get("button", MouseButton.LEFT.value)),
            click_type=ClickType(data.get("click_type", ClickType.SINGLE.value)),
            x=_require_int(data["x"], "x") if data.get("x") is not None else None,
            y=_require_int(data["y"], "y") if data.get("y") is not None else None,
            move_mode=MoveMode(data.get("move_mode", MoveMode.ABSOLUTE.value)),
            scroll_dx=_require_int(data.get("scroll_dx", 0), "scroll_dx"),
            scroll_dy=_require_int(
                data.get("scroll_dy", 0 if action_type != ActionType.MOUSE_SCROLL else -1),
                "scroll_dy",
            ),
            wait_ms=_require_int(data.get("wait_ms", 200), "wait_ms"),
            jitter_ms=_require_int(data.get("jitter_ms", 0), "jitter_ms"),
            repeat_count=_require_int(data.get("repeat_count", 2), "repeat_count"),
            sub_actions=sub_actions,
        )


# Convenience factory constructors
def make_key_press(key: str = "E", hold_ms: int = 25, label: str = "") -> MacroAction:
    return MacroAction(action_type=ActionType.KEY_PRESS, key=key, hold_ms=hold_ms, label=label)


def make_key_down(key: str = "Shift", label: str = "") -> MacroAction:
    return MacroAction(action_type=ActionType.KEY_DOWN, key=key, label=label)


def make_key_up(key: str = "Shift", label: str = "") -> MacroAction:
    return MacroAction(action_type=ActionType.KEY_UP, key=key, label=label)


def make_mouse_click(
    button: MouseButton | str = MouseButton.LEFT,
    click_type: ClickType | str = ClickType.SINGLE,
    x: int | None = None,
    y: int | None = None,
    hold_ms: int = 25,
    label: str = "",
) -> MacroAction:
    return MacroAction(
        action_type=ActionType.MOUSE_CLICK,
        button=MouseButton(button),
        click_type=ClickType(click_type),
        x=x,
        y=y,
        hold_ms=hold_ms,
        label=label,
    )


def make_mouse_move(
    x: int,
    y: int,
    move_mode: MoveMode | str = MoveMode.ABSOLUTE,
    label: str = "",
) -> MacroAction:
    return MacroAction(
        action_type=ActionType.MOUSE_MOVE,
        x=x,
        y=y,
        move_mode=MoveMode(move_mode),
        label=label,
    )


def make_mouse_scroll(scroll_dy: int = -1, scroll_dx: int = 0, label: str = "") -> MacroAction:
    return MacroAction(
        action_type=ActionType.MOUSE_SCROLL,
        scroll_dx=scroll_dx,
        scroll_dy=scroll_dy,
        label=label,
    )


def make_wait(wait_ms: int = 200, jitter_ms: int = 0, label: str = "") -> MacroAction:
    return MacroAction(
        action_type=ActionType.WAIT,
        wait_ms=wait_ms,
        jitter_ms=jitter_ms,
        label=label,
    )


def make_repeat_block(
    repeat_count: int = 2,
    sub_actions: list[MacroAction] | None = None,
    label: str = "",
) -> MacroAction:
    return MacroAction(
        action_type=ActionType.REPEAT_BLOCK,
        repeat_count=repeat_count,
        sub_actions=list(sub_actions or []),
        label=label,
    )
