"""Unit tests for keymaps, MacroAction primitives, MacroProfile, and Quick-Forge DSL."""

from __future__ import annotations

import pytest

from keyforge.models.action import (
    ActionType,
    ClickType,
    MacroAction,
    MouseButton,
    MoveMode,
    make_key_press,
    make_mouse_click,
    make_mouse_move,
    make_mouse_scroll,
    make_repeat_block,
    make_wait,
)
from keyforge.models.dsl import (
    format_action_to_dsl,
    format_actions_to_dsl,
    parse_quick_expression,
)
from keyforge.models.keymap import (
    get_key_name_from_vk,
    is_valid_key_combo,
    list_common_keys,
    normalize_key_combo,
    parse_key_combo,
    resolve_single_key,
)
from keyforge.models.profile import (
    MacroProfile,
    create_default_profile,
    get_builtin_presets,
)


def test_keymap_resolution_and_normalization() -> None:
    e_info = resolve_single_key("e")
    assert e_info.canonical_name == "E"
    assert e_info.vk_code == 0x45

    space_info = resolve_single_key("spacebar")
    assert space_info.canonical_name == "Space"
    assert space_info.vk_code == 0x20

    assert normalize_key_combo("lctrl + shift + e") == "Ctrl+Shift+E"
    combo_infos = parse_key_combo("Ctrl+Shift+1")
    assert [k.canonical_name for k in combo_infos] == ["Ctrl", "Shift", "1"]

    assert is_valid_key_combo("F12") is True
    assert is_valid_key_combo("InvalidKey999") is False
    assert get_key_name_from_vk(0x45) == "E"
    assert "E" in list_common_keys()


def test_keymap_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_single_key("   ")
    with pytest.raises(ValueError, match="Unknown key"):
        resolve_single_key("NonExistentKey")
    with pytest.raises(ValueError, match="Duplicate key"):
        parse_key_combo("Ctrl+Ctrl+E")
    with pytest.raises(ValueError, match="exceeds maximum of 4"):
        parse_key_combo("Ctrl+Shift+Alt+Win+E")
    with pytest.raises(ValueError, match="Malformed key"):
        parse_key_combo("Ctrl++E")


def test_macro_action_types_and_descriptions() -> None:
    a_key = make_key_press("e", hold_ms=40, label="Interact")
    assert a_key.key == "E"
    assert a_key.badge_text() == "KEY"
    assert a_key.describe() == "[Interact] Press [E] (hold 40ms)"

    a_wait = make_wait(200, jitter_ms=15)
    assert a_wait.badge_text() == "WAIT"
    assert a_wait.describe() == "Wait 200ms (±15ms jitter)"

    a_click = make_mouse_click(MouseButton.RIGHT, ClickType.DOUBLE, x=400, y=300)
    assert a_click.badge_text() == "CLICK"
    assert a_click.describe() == "Right Double-Click at (400, 300)"

    a_move = make_mouse_move(-10, 25, MoveMode.RELATIVE)
    assert a_move.badge_text() == "MOVE"
    assert a_move.describe() == "Move Cursor Relative (-10, +25)"

    a_scroll = make_mouse_scroll(scroll_dy=-3, scroll_dx=1)
    assert a_scroll.badge_text() == "SCROLL"
    assert "Down 3" in a_scroll.describe()
    assert "Right 1" in a_scroll.describe()

    a_loop = make_repeat_block(repeat_count=3, sub_actions=[make_key_press("1"), make_wait(50)])
    assert a_loop.badge_text() == "LOOP"
    assert "Repeat 3x" in a_loop.describe()

    cloned = a_loop.clone()
    assert cloned.id != a_loop.id
    assert cloned.sub_actions[0].id != a_loop.sub_actions[0].id
    assert cloned.describe() == a_loop.describe()


def test_macro_action_validation_boundaries() -> None:
    with pytest.raises(ValueError, match="hold_ms must be between"):
        make_key_press("E", hold_ms=-5)
    with pytest.raises(ValueError, match="both be set or both be None"):
        MacroAction(action_type=ActionType.MOUSE_CLICK, x=100, y=None)
    with pytest.raises(ValueError, match="coordinates must be within"):
        make_mouse_move(99999, 0)
    with pytest.raises(ValueError, match="non-zero scroll_dx or scroll_dy"):
        make_mouse_scroll(0, 0)
    with pytest.raises(ValueError, match="wait_ms must be between"):
        make_wait(0)
    with pytest.raises(ValueError, match="repeat_count must be between"):
        make_repeat_block(0, [make_key_press("E")])


def test_dsl_flagship_expression_and_roundtrip() -> None:
    expr = "Press E -> Wait 200ms -> Click -> Press 1"
    steps = parse_quick_expression(expr)
    assert len(steps) == 4
    assert steps[0].action_type == ActionType.KEY_PRESS
    assert steps[0].key == "E"
    assert steps[1].action_type == ActionType.WAIT
    assert steps[1].wait_ms == 200
    assert steps[2].action_type == ActionType.MOUSE_CLICK
    assert steps[2].button == MouseButton.LEFT
    assert steps[3].action_type == ActionType.KEY_PRESS
    assert steps[3].key == "1"

    formatted = format_actions_to_dsl(steps)
    assert formatted == "Press E -> Wait 200ms -> Click -> Press 1"


def test_dsl_advanced_constructs() -> None:
    expr = (
        "KeyDown Shift -> Click Right at (500, 600) -> KeyUp Shift -> "
        "Move Relative (+15, -10) -> Scroll Down 4 -> "
        "Repeat 3 { Press Space (hold 40ms) -> Wait 150ms ±20ms }"
    )
    steps = parse_quick_expression(expr)
    assert len(steps) == 6
    assert steps[0].action_type == ActionType.KEY_DOWN
    assert steps[0].key == "Shift"
    assert steps[1].action_type == ActionType.MOUSE_CLICK
    assert steps[1].button == MouseButton.RIGHT
    assert (steps[1].x, steps[1].y) == (500, 600)
    assert steps[2].action_type == ActionType.KEY_UP
    assert steps[3].action_type == ActionType.MOUSE_MOVE
    assert steps[3].move_mode == MoveMode.RELATIVE
    assert (steps[3].x, steps[3].y) == (15, -10)
    assert steps[4].action_type == ActionType.MOUSE_SCROLL
    assert steps[4].scroll_dy == -4
    assert steps[5].action_type == ActionType.REPEAT_BLOCK
    assert steps[5].repeat_count == 3
    assert len(steps[5].sub_actions) == 2
    assert steps[5].sub_actions[0].hold_ms == 40
    assert steps[5].sub_actions[1].jitter_ms == 20

    # Round-trip test
    re_parsed = parse_quick_expression(format_actions_to_dsl(steps))
    assert len(re_parsed) == len(steps)
    for orig, new in zip(steps, re_parsed, strict=True):
        assert format_action_to_dsl(orig) == format_action_to_dsl(new)


def test_dsl_error_handling() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        parse_quick_expression("   ")
    with pytest.raises(ValueError, match="Unbalanced braces"):
        parse_quick_expression("Repeat 2 { Press E -> Wait 100ms")
    with pytest.raises(ValueError, match="Unrecognized macro step"):
        parse_quick_expression("Press E -> DoSomethingMagical 999")


def test_profile_and_presets() -> None:
    default_prof = create_default_profile()
    assert default_prof.total_cycle_wait_ms() > 300
    payload = default_prof.to_payload_dict()
    restored = MacroProfile.from_payload_dict(payload)
    assert restored.name == default_prof.name
    assert len(restored.actions) == len(default_prof.actions)

    presets = get_builtin_presets()
    assert len(presets) >= 4

    with pytest.raises(ValueError, match="must be distinct"):
        MacroProfile(start_stop_hotkey="F6", panic_hotkey="F6", record_hotkey="F7")
