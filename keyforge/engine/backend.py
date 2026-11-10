"""Input backend protocol and deterministic dry-run recorder backend for testing/simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from keyforge.models.action import MouseButton
from keyforge.models.keymap import KeyInfo

# Magic signature stamped into Win32 INPUT.dwExtraInfo ("KFRG" in ASCII hex)
KEYFORGE_EXTRA_INFO = 0x4B465247


@dataclass(frozen=True, slots=True)
class DispatchedEvent:
    """Record of a low-level input operation emitted by an InputBackend."""

    kind: str  # "key_down", "key_up", "mouse_down", "mouse_up", "mouse_move", "mouse_scroll"
    target: str
    x: int | None = None
    y: int | None = None
    dx: int = 0
    dy: int = 0


@runtime_checkable
class InputBackend(Protocol):
    """Abstract interface for OS-level or simulated keyboard & mouse injection."""

    def key_down(self, key: KeyInfo) -> None:
        """Depress a keyboard key."""

    def key_up(self, key: KeyInfo) -> None:
        """Release a keyboard key."""

    def mouse_down(self, button: MouseButton) -> None:
        """Depress a mouse button at the current cursor position."""

    def mouse_up(self, button: MouseButton) -> None:
        """Release a mouse button at the current cursor position."""

    def move_cursor(self, x: int, y: int, relative: bool = False) -> None:
        """Move the mouse cursor to absolute (x, y) or by relative offset (dx, dy)."""

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        """Scroll the mouse wheel vertically (dy) and/or horizontally (dx)."""

    def get_cursor_pos(self) -> tuple[int, int]:
        """Return current cursor (x, y) coordinates."""


class DryRunBackend:
    """In-memory deterministic input backend for safe dry-run simulation and unit testing."""

    def __init__(self, initial_cursor: tuple[int, int] = (960, 540)) -> None:
        self._cursor_x, self._cursor_y = initial_cursor
        self.events: list[DispatchedEvent] = []
        self.currently_held_keys: set[str] = set()
        self.currently_held_buttons: set[MouseButton] = set()

    def clear(self) -> None:
        """Reset recorded events and held state."""
        self.events.clear()
        self.currently_held_keys.clear()
        self.currently_held_buttons.clear()

    def key_down(self, key: KeyInfo) -> None:
        self.currently_held_keys.add(key.canonical_name)
        self.events.append(DispatchedEvent(kind="key_down", target=key.canonical_name))

    def key_up(self, key: KeyInfo) -> None:
        self.currently_held_keys.discard(key.canonical_name)
        self.events.append(DispatchedEvent(kind="key_up", target=key.canonical_name))

    def mouse_down(self, button: MouseButton) -> None:
        self.currently_held_buttons.add(button)
        self.events.append(
            DispatchedEvent(
                kind="mouse_down",
                target=button.value,
                x=self._cursor_x,
                y=self._cursor_y,
            )
        )

    def mouse_up(self, button: MouseButton) -> None:
        self.currently_held_buttons.discard(button)
        self.events.append(
            DispatchedEvent(
                kind="mouse_up",
                target=button.value,
                x=self._cursor_x,
                y=self._cursor_y,
            )
        )

    def move_cursor(self, x: int, y: int, relative: bool = False) -> None:
        if relative:
            self._cursor_x += x
            self._cursor_y += y
            self.events.append(
                DispatchedEvent(
                    kind="mouse_move",
                    target="relative",
                    x=self._cursor_x,
                    y=self._cursor_y,
                    dx=x,
                    dy=y,
                )
            )
        else:
            self._cursor_x = x
            self._cursor_y = y
            self.events.append(
                DispatchedEvent(
                    kind="mouse_move",
                    target="absolute",
                    x=self._cursor_x,
                    y=self._cursor_y,
                )
            )

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        self.events.append(
            DispatchedEvent(
                kind="mouse_scroll",
                target="wheel",
                x=self._cursor_x,
                y=self._cursor_y,
                dx=dx,
                dy=dy,
            )
        )

    def get_cursor_pos(self) -> tuple[int, int]:
        return (self._cursor_x, self._cursor_y)
