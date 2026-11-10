"""Quick-Forge Expression DSL Parser & Formatter.

Allows users to write or share readable macro sequences such as:
    Press E -> Wait 200ms -> Click -> Press 1
    Repeat 3 { Press Space -> Wait 100ms } -> Click Right at (800, 450)
without any scripting complexity or unsafe code evaluation.
"""

from __future__ import annotations

import re

from keyforge.models.action import (
    ActionType,
    ClickType,
    MacroAction,
    MouseButton,
    MoveMode,
    make_key_down,
    make_key_press,
    make_key_up,
    make_mouse_click,
    make_mouse_move,
    make_mouse_scroll,
    make_repeat_block,
    make_wait,
)
from keyforge.models.keymap import is_valid_key_combo, normalize_key_combo, resolve_single_key

_WAIT_RE = re.compile(
    r"^(?:wait|sleep|delay|pause)?\s*"
    r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|sec|seconds|milliseconds)?"
    r"(?:\s*(?:\+-|±|~)\s*(?P<jitter>\d+)\s*(?:ms)?)?$",
    re.IGNORECASE,
)

_PRESS_RE = re.compile(
    r"^(?:press|tap|key|send)\s+\[?(?P<key>[A-Za-z0-9_+;\-=,./`\[\]\\']+)\]?"
    r"(?:\s*\(\s*hold\s+(?P<hold>\d+)\s*ms\s*\))?$",
    re.IGNORECASE,
)

_KEYDOWN_RE = re.compile(
    r"^(?:keydown|hold(?:\s+key(?:\s+down)?)?)\s+\[?(?P<key>[A-Za-z0-9_;\-=,./`\[\]\\']+)\]?$",
    re.IGNORECASE,
)

_KEYUP_RE = re.compile(
    r"^(?:keyup|release(?:\s+key(?:\s+up)?)?)\s+\[?(?P<key>[A-Za-z0-9_;\-=,./`\[\]\\']+)\]?$",
    re.IGNORECASE,
)

_CLICK_RE = re.compile(
    r"^(?:(?P<btn1>left|right|middle|x1|x2|l|r|m)?\s*"
    r"(?P<kind>double[\s\-]?click|dclick|click|lclick|rclick|mclick|mousedown|mouseup|button\s+down|button\s+up))"
    r"(?:\s+(?P<btn2>left|right|middle|x1|x2))?"
    r"(?:\s+at\s+(?:cursor|\(\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*\)))?$",
    re.IGNORECASE,
)

_MOVE_RE = re.compile(
    r"^(?:move(?:\s+cursor)?|cursor)\s+"
    r"(?P<rel>relative|rel|offset|to)?\s*"
    r"\(?\s*(?P<x>[+\-]?\d+)\s*,\s*(?P<y>[+\-]?\d+)\s*\)?$",
    re.IGNORECASE,
)

_SCROLL_RE = re.compile(
    r"^scroll(?:\s+wheel)?\s+"
    r"(?:(?P<dir>up|down|left|right)\s*(?P<amt>\d+)|(?P<signed>[+\-]?\d+))$",
    re.IGNORECASE,
)

_REPEAT_RE = re.compile(
    r"^repeat\s+(?P<count>\d+)\s*x?\s*\{\s*(?P<body>.+)\s*\}$",
    re.IGNORECASE | re.DOTALL,
)


def _split_top_level_steps(expression: str) -> list[str]:
    """Split an expression by '->', ';', or newline while respecting '{ ... }' blocks and '(x, y)'."""
    steps: list[str] = []
    current: list[str] = []
    brace_depth = 0
    paren_depth = 0
    i = 0
    n = len(expression)

    while i < n:
        ch = expression[i]
        if ch == "{":
            brace_depth += 1
            current.append(ch)
            i += 1
        elif ch == "}":
            brace_depth = max(0, brace_depth - 1)
            current.append(ch)
            i += 1
        elif ch == "(":
            paren_depth += 1
            current.append(ch)
            i += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
            current.append(ch)
            i += 1
        elif brace_depth == 0 and paren_depth == 0:
            if expression[i : i + 2] == "->":
                token = "".join(current).strip()
                if token:
                    steps.append(token)
                current = []
                i += 2
            elif ch in (";", "\n", ","):
                token = "".join(current).strip()
                if token:
                    steps.append(token)
                current = []
                i += 1
            else:
                current.append(ch)
                i += 1
        else:
            current.append(ch)
            i += 1

    if brace_depth != 0 or paren_depth != 0:
        raise ValueError("Unbalanced braces '{ }' or parentheses '( )' in expression.")

    tail = "".join(current).strip()
    if tail:
        steps.append(tail)

    return steps


def _parse_single_step(step_str: str, depth: int = 0) -> MacroAction:
    """Parse a single step token into a MacroAction."""
    if depth > 5:
        raise ValueError("Maximum repeat nesting depth (5) exceeded in expression.")

    text = step_str.strip()
    if not text:
        raise ValueError("Empty step token.")

    # 1. Repeat block: Repeat 3 { ... }
    m_repeat = _REPEAT_RE.match(text)
    if m_repeat:
        count = int(m_repeat.group("count"))
        body = m_repeat.group("body")
        sub_tokens = _split_top_level_steps(body)
        if not sub_tokens:
            raise ValueError("Repeat block cannot be empty.")
        sub_actions = [_parse_single_step(tok, depth + 1) for tok in sub_tokens]
        return make_repeat_block(repeat_count=count, sub_actions=sub_actions)

    # 2. Wait step: "Wait 200ms", "Wait 1.5s", "200ms", "Wait 200ms ±15ms"
    m_wait = _WAIT_RE.match(text)
    if m_wait:
        raw_val = float(m_wait.group("val"))
        unit = (m_wait.group("unit") or "").lower()
        has_wait_keyword = text.lower().startswith(("wait", "sleep", "delay", "pause"))
        if has_wait_keyword or unit in ("ms", "s", "sec", "seconds", "milliseconds"):
            if unit in ("s", "sec", "seconds"):
                wait_ms = int(round(raw_val * 1000))
            else:
                wait_ms = int(round(raw_val))
            jitter_ms = int(m_wait.group("jitter") or 0)
            return make_wait(wait_ms=max(1, wait_ms), jitter_ms=jitter_ms)

    # 3. Mouse Click step: "Click", "Left Click", "Click Right at (100, 200)", "Double-Click"
    m_click = _CLICK_RE.match(text)
    if m_click:
        kind = m_click.group("kind").lower().replace("-", " ")
        btn_raw = (m_click.group("btn1") or m_click.group("btn2") or "").lower()
        if kind == "lclick" or btn_raw in ("l", "left", ""):
            button = MouseButton.LEFT
        elif kind == "rclick" or btn_raw in ("r", "right"):
            button = MouseButton.RIGHT
        elif kind == "mclick" or btn_raw in ("m", "middle"):
            button = MouseButton.MIDDLE
        elif btn_raw == "x1":
            button = MouseButton.X1
        elif btn_raw == "x2":
            button = MouseButton.X2
        else:
            button = MouseButton.LEFT

        if kind in ("double click", "dclick"):
            click_type = ClickType.DOUBLE
        elif kind in ("mousedown", "button down"):
            click_type = ClickType.DOWN
        elif kind in ("mouseup", "button up"):
            click_type = ClickType.UP
        else:
            click_type = ClickType.SINGLE

        x = int(m_click.group("x")) if m_click.group("x") is not None else None
        y = int(m_click.group("y")) if m_click.group("y") is not None else None
        return make_mouse_click(button=button, click_type=click_type, x=x, y=y)

    # 4. Key Down / Key Up
    m_kd = _KEYDOWN_RE.match(text)
    if m_kd:
        key_info = resolve_single_key(m_kd.group("key"))
        return make_key_down(key=key_info.canonical_name)

    m_ku = _KEYUP_RE.match(text)
    if m_ku:
        key_info = resolve_single_key(m_ku.group("key"))
        return make_key_up(key=key_info.canonical_name)

    # 5. Explicit Key Press: "Press E", "Press [1] (hold 50ms)"
    m_press = _PRESS_RE.match(text)
    if m_press:
        combo = normalize_key_combo(m_press.group("key"))
        hold = int(m_press.group("hold")) if m_press.group("hold") else 25
        return make_key_press(key=combo, hold_ms=hold)

    # 6. Mouse Move: "Move (500, 300)", "Move Relative (+10, -5)"
    m_move = _MOVE_RE.match(text)
    if m_move:
        rel_kw = (m_move.group("rel") or "").lower()
        mode = MoveMode.RELATIVE if rel_kw in ("relative", "rel", "offset") else MoveMode.ABSOLUTE
        return make_mouse_move(x=int(m_move.group("x")), y=int(m_move.group("y")), move_mode=mode)

    # 7. Mouse Scroll: "Scroll Down 3", "Scroll Up 2", "Scroll -3"
    m_scroll = _SCROLL_RE.match(text)
    if m_scroll:
        if m_scroll.group("dir"):
            direction = m_scroll.group("dir").lower()
            amt = int(m_scroll.group("amt"))
            if direction == "up":
                return make_mouse_scroll(scroll_dy=amt, scroll_dx=0)
            if direction == "down":
                return make_mouse_scroll(scroll_dy=-amt, scroll_dx=0)
            if direction == "right":
                return make_mouse_scroll(scroll_dy=0, scroll_dx=amt)
            if direction == "left":
                return make_mouse_scroll(scroll_dy=0, scroll_dx=-amt)
        else:
            signed = int(m_scroll.group("signed"))
            return make_mouse_scroll(scroll_dy=signed, scroll_dx=0)

    # 8. Bare key shorthand (e.g., "E", "1", "Space", "Ctrl+C")
    if is_valid_key_combo(text):
        return make_key_press(key=normalize_key_combo(text))

    raise ValueError(
        f"Unrecognized macro step syntax: {step_str!r}. "
        "Examples: 'Press E', 'Wait 200ms', 'Click', 'Press 1', 'Click Right at (500, 300)'."
    )


def parse_quick_expression(expression: str) -> list[MacroAction]:
    """Parse a human-readable Quick-Forge expression into a list of MacroAction steps."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Expression cannot be empty.")
    if len(expression) > 50_000:
        raise ValueError("Expression exceeds maximum allowed length (50,000 characters).")

    tokens = _split_top_level_steps(expression)
    if not tokens:
        raise ValueError("No valid steps found in expression.")

    return [_parse_single_step(tok) for tok in tokens]


def format_action_to_dsl(action: MacroAction) -> str:
    """Convert a single MacroAction back into a canonical Quick-Forge expression token."""
    if action.action_type == ActionType.KEY_PRESS:
        hold = f" (hold {action.hold_ms}ms)" if action.hold_ms != 25 else ""
        return f"Press {action.key}{hold}"
    if action.action_type == ActionType.KEY_DOWN:
        return f"KeyDown {action.key}"
    if action.action_type == ActionType.KEY_UP:
        return f"KeyUp {action.key}"
    if action.action_type == ActionType.MOUSE_CLICK:
        btn = action.button.value.capitalize()
        kind = {
            ClickType.SINGLE: "Click",
            ClickType.DOUBLE: "Double-Click",
            ClickType.DOWN: "MouseDown",
            ClickType.UP: "MouseUp",
        }[action.click_type]
        if action.button == MouseButton.LEFT and action.click_type == ClickType.SINGLE:
            base = "Click"
        else:
            base = f"{btn} {kind}"
        if action.x is not None and action.y is not None:
            return f"{base} at ({action.x}, {action.y})"
        return base
    if action.action_type == ActionType.MOUSE_MOVE:
        rel = " Relative" if action.move_mode == MoveMode.RELATIVE else ""
        return f"Move{rel} ({action.x}, {action.y})"
    if action.action_type == ActionType.MOUSE_SCROLL:
        if action.scroll_dy != 0:
            direction = "Up" if action.scroll_dy > 0 else "Down"
            return f"Scroll {direction} {abs(action.scroll_dy)}"
        direction = "Right" if action.scroll_dx > 0 else "Left"
        return f"Scroll {direction} {abs(action.scroll_dx)}"
    if action.action_type == ActionType.WAIT:
        jitter = f" ±{action.jitter_ms}ms" if action.jitter_ms > 0 else ""
        return f"Wait {action.wait_ms}ms{jitter}"
    if action.action_type == ActionType.REPEAT_BLOCK:
        inner = " -> ".join(format_action_to_dsl(s) for s in action.sub_actions)
        return f"Repeat {action.repeat_count} {{ {inner} }}"
    return action.describe()


def format_actions_to_dsl(actions: list[MacroAction]) -> str:
    """Format a list of MacroActions into a single-line Quick-Forge expression."""
    return " -> ".join(format_action_to_dsl(a) for a in actions if a.enabled)
