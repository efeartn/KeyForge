"""Cross-platform OS input injection, key-state polling, and cursor backend (Windows, macOS, Linux)."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import sys
import threading
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from keyforge.engine.backend import KEYFORGE_EXTRA_INFO, InputBackend
from keyforge.engine.win32_input import (
    Win32InputBackend,
    get_async_key_state,
    get_system_cursor_pos,
)
from keyforge.models.action import MouseButton
from keyforge.models.keymap import KeyInfo

# Mouse Virtual-Key codes matching keyforge.engine.recorder
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

# Complete Windows Virtual-Key -> macOS Carbon virtual keycode table (ANSI)
VK_TO_MAC_KEYCODE: dict[int, int] = {
    # Modifiers
    0x11: 0x3B,  # Ctrl (kVK_Control)
    0xA2: 0x3B,  # LCtrl
    0xA3: 0x3E,  # RCtrl (kVK_RightControl)
    0x10: 0x38,  # Shift (kVK_Shift)
    0xA0: 0x38,  # LShift
    0xA1: 0x3C,  # RShift (kVK_RightShift)
    0x12: 0x3A,  # Alt / Option (kVK_Option)
    0xA4: 0x3A,  # LAlt
    0xA5: 0x3D,  # RAlt / ROption (kVK_RightOption)
    0x5B: 0x37,  # Win / Command (kVK_Command)
    0x5C: 0x36,  # RWin / RCommand (kVK_RightCommand)
    # Navigation & Special
    0x08: 0x33,  # Backspace (kVK_Delete)
    0x09: 0x30,  # Tab
    0x0D: 0x24,  # Enter (kVK_Return)
    0x14: 0x39,  # CapsLock
    0x1B: 0x35,  # Esc
    0x20: 0x31,  # Space
    0x21: 0x74,  # PageUp
    0x22: 0x79,  # PageDown
    0x23: 0x77,  # End
    0x24: 0x73,  # Home
    0x25: 0x7B,  # Left
    0x26: 0x7E,  # Up
    0x27: 0x7C,  # Right
    0x28: 0x7D,  # Down
    0x2D: 0x72,  # Insert (Help)
    0x2E: 0x75,  # Delete (kVK_ForwardDelete)
    0x90: 0x47,  # NumLock (kVK_ANSI_KeypadClear)
    # Number row
    0x30: 0x1D,  # 0
    0x31: 0x12,  # 1
    0x32: 0x13,  # 2
    0x33: 0x14,  # 3
    0x34: 0x15,  # 4
    0x35: 0x17,  # 5
    0x36: 0x16,  # 6
    0x37: 0x1A,  # 7
    0x38: 0x1C,  # 8
    0x39: 0x19,  # 9
    # Alphabet A-Z
    0x41: 0x00,  # A
    0x42: 0x0B,  # B
    0x43: 0x08,  # C
    0x44: 0x02,  # D
    0x45: 0x0E,  # E
    0x46: 0x03,  # F
    0x47: 0x05,  # G
    0x48: 0x04,  # H
    0x49: 0x22,  # I
    0x4A: 0x26,  # J
    0x4B: 0x28,  # K
    0x4C: 0x25,  # L
    0x4D: 0x2E,  # M
    0x4E: 0x2D,  # N
    0x4F: 0x1F,  # O
    0x50: 0x23,  # P
    0x51: 0x0C,  # Q
    0x52: 0x0F,  # R
    0x53: 0x01,  # S
    0x54: 0x11,  # T
    0x55: 0x20,  # U
    0x56: 0x09,  # V
    0x57: 0x0D,  # W
    0x58: 0x07,  # X
    0x59: 0x10,  # Y
    0x5A: 0x06,  # Z
    # Numpad
    0x60: 0x52,  # Numpad0
    0x61: 0x53,  # Numpad1
    0x62: 0x54,  # Numpad2
    0x63: 0x55,  # Numpad3
    0x64: 0x56,  # Numpad4
    0x65: 0x57,  # Numpad5
    0x66: 0x58,  # Numpad6
    0x67: 0x59,  # Numpad7
    0x68: 0x5B,  # Numpad8
    0x69: 0x5C,  # Numpad9
    0x6A: 0x43,  # NumpadMultiply
    0x6B: 0x45,  # NumpadAdd
    0x6D: 0x4E,  # NumpadSubtract
    0x6E: 0x41,  # NumpadDecimal
    0x6F: 0x4B,  # NumpadDivide
    # Function keys F1-F20
    0x70: 0x7A,  # F1
    0x71: 0x78,  # F2
    0x72: 0x63,  # F3
    0x73: 0x76,  # F4
    0x74: 0x60,  # F5
    0x75: 0x61,  # F6
    0x76: 0x62,  # F7
    0x77: 0x64,  # F8
    0x78: 0x65,  # F9
    0x79: 0x6D,  # F10
    0x7A: 0x67,  # F11
    0x7B: 0x6F,  # F12
    0x7C: 0x69,  # F13
    0x7D: 0x6B,  # F14
    0x7E: 0x71,  # F15
    0x7F: 0x6A,  # F16
    0x80: 0x40,  # F17
    0x81: 0x4F,  # F18
    0x82: 0x50,  # F19
    0x83: 0x5A,  # F20
    # OEM Punctuation
    0xBA: 0x29,  # Semicolon
    0xBB: 0x18,  # Equals
    0xBC: 0x2B,  # Comma
    0xBD: 0x1B,  # Minus
    0xBE: 0x2F,  # Period
    0xBF: 0x2C,  # Slash
    0xC0: 0x32,  # Grave
    0xDB: 0x21,  # LeftBracket
    0xDC: 0x2A,  # Backslash
    0xDD: 0x1E,  # RightBracket
    0xDE: 0x27,  # Quote
}

# Complete Windows Virtual-Key -> X11 KeySym table
VK_TO_X11_KEYSYM: dict[int, int] = {
    # Modifiers
    0x10: 0xFFE1,  # Shift_L
    0xA0: 0xFFE1,  # Shift_L
    0xA1: 0xFFE2,  # Shift_R
    0x11: 0xFFE3,  # Control_L
    0xA2: 0xFFE3,  # Control_L
    0xA3: 0xFFE4,  # Control_R
    0x14: 0xFFE5,  # Caps_Lock
    0x12: 0xFFE9,  # Alt_L
    0xA4: 0xFFE9,  # Alt_L
    0xA5: 0xFFEA,  # Alt_R
    0x5B: 0xFFEB,  # Super_L
    0x5C: 0xFFEC,  # Super_R
    # Navigation & Special
    0x08: 0xFF08,  # BackSpace
    0x09: 0xFF09,  # Tab
    0x0D: 0xFF0D,  # Return
    0x13: 0xFF13,  # Pause
    0x91: 0xFF14,  # Scroll_Lock
    0x1B: 0xFF1B,  # Escape
    0x20: 0x0020,  # space
    0x21: 0xFF55,  # Prior / Page_Up
    0x22: 0xFF56,  # Next / Page_Down
    0x23: 0xFF57,  # End
    0x24: 0xFF50,  # Home
    0x25: 0xFF51,  # Left
    0x26: 0xFF52,  # Up
    0x27: 0xFF53,  # Right
    0x28: 0xFF54,  # Down
    0x2C: 0xFF61,  # Print
    0x2D: 0xFF63,  # Insert
    0x2E: 0xFFFF,  # Delete
    0x90: 0xFF7F,  # Num_Lock
    # Number row
    **{0x30 + d: 0x0030 + d for d in range(10)},
    # Alphabet A-Z mapped to lowercase X11 keysyms (XK_a..XK_z) for clean XKeysymToKeycode lookup
    **{0x41 + i: 0x0061 + i for i in range(26)},
    # Numpad
    **{0x60 + d: 0xFFB0 + d for d in range(10)},
    0x6A: 0xFFAA,  # KP_Multiply
    0x6B: 0xFFAB,  # KP_Add
    0x6D: 0xFFAD,  # KP_Subtract
    0x6E: 0xFFAE,  # KP_Decimal
    0x6F: 0xFFAF,  # KP_Divide
    # F1-F24
    **{0x70 + i: 0xFFBE + i for i in range(24)},
    # Punctuation
    0xBA: 0x003B,  # semicolon
    0xBB: 0x003D,  # equal
    0xBC: 0x002C,  # comma
    0xBD: 0x002D,  # minus
    0xBE: 0x002E,  # period
    0xBF: 0x002F,  # slash
    0xC0: 0x0060,  # grave
    0xDB: 0x005B,  # bracketleft
    0xDC: 0x005C,  # backslash
    0xDD: 0x005D,  # bracketright
    0xDE: 0x0027,  # apostrophe
}

# macOS CoreGraphics constants
_CG_HID_EVENT_TAP = 0
_CG_EVENT_SOURCE_STATE_COMBINED_SESSION = 0
_CG_EVENT_LEFT_MOUSE_DOWN = 1
_CG_EVENT_LEFT_MOUSE_UP = 2
_CG_EVENT_RIGHT_MOUSE_DOWN = 3
_CG_EVENT_RIGHT_MOUSE_UP = 4
_CG_EVENT_MOUSE_MOVED = 5
_CG_EVENT_LEFT_MOUSE_DRAGGED = 6
_CG_EVENT_RIGHT_MOUSE_DRAGGED = 7
_CG_EVENT_KEY_DOWN = 10
_CG_EVENT_KEY_UP = 11
_CG_EVENT_FLAGS_CHANGED = 12
_CG_EVENT_OTHER_MOUSE_DOWN = 25
_CG_EVENT_OTHER_MOUSE_UP = 26
_CG_EVENT_OTHER_MOUSE_DRAGGED = 27
_CG_SCROLL_EVENT_UNIT_LINE = 1
_CG_EVENT_SOURCE_USER_DATA = 42

_CG_FLAG_SHIFT = 0x00020000
_CG_FLAG_CONTROL = 0x00040000
_CG_FLAG_ALTERNATE = 0x00080000
_CG_FLAG_COMMAND = 0x00100000

_VK_TO_CG_FLAG: dict[int, int] = {
    0x10: _CG_FLAG_SHIFT,
    0xA0: _CG_FLAG_SHIFT,
    0xA1: _CG_FLAG_SHIFT,
    0x11: _CG_FLAG_CONTROL,
    0xA2: _CG_FLAG_CONTROL,
    0xA3: _CG_FLAG_CONTROL,
    0x12: _CG_FLAG_ALTERNATE,
    0xA4: _CG_FLAG_ALTERNATE,
    0xA5: _CG_FLAG_ALTERNATE,
    0x5B: _CG_FLAG_COMMAND,
    0x5C: _CG_FLAG_COMMAND,
}

_MAC_MODIFIER_KEYCODES = frozenset({0x36, 0x37, 0x38, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E})

# X11 mouse button numbering
_X11_MOUSE_BUTTON_MAP: dict[MouseButton, int] = {
    MouseButton.LEFT: 1,
    MouseButton.MIDDLE: 2,
    MouseButton.RIGHT: 3,
    MouseButton.X1: 8,
    MouseButton.X2: 9,
}

# XQueryPointer mask bits (Button1Mask..Button5Mask + extra masks)
_X11_MOUSE_VK_MASK: dict[int, int] = {
    VK_LBUTTON: 1 << 8,  # Button1Mask
    VK_MBUTTON: 1 << 9,  # Button2Mask
    VK_RBUTTON: 1 << 10,  # Button3Mask
    VK_XBUTTON1: 1 << 13,  # X1 heuristic on systems mapping buttons
    VK_XBUTTON2: 1 << 14,
}


class CGPoint(ctypes.Structure):
    """64-bit CoreGraphics CGPoint struct (two 64-bit CGFloats)."""

    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


@dataclass(frozen=True, slots=True)
class PlatformInputCapabilities:
    """Describes input injection, key polling, and permission status on the active OS."""

    platform: str
    backend_name: str
    can_inject: bool
    can_poll_keys: bool
    can_poll_cursor: bool
    requires_permission: bool = False
    notice: str = ""


class MacOSInputBackend:
    """macOS CoreGraphics CGEvent input injection & polling backend (zero extra pip deps)."""

    def __init__(self, load_frameworks: bool = True) -> None:
        self._lock = threading.RLock()
        self._cg: Any = None
        self._cf: Any = None
        self._app_services: Any = None
        self._available = False
        self._active_flags = 0
        self._held_buttons: set[MouseButton] = set()
        self._fallback_x = 0
        self._fallback_y = 0
        if load_frameworks:
            self._init_frameworks()

    def _init_frameworks(self) -> None:
        cg_path = (
            ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cf_path = (
            ctypes.util.find_library("CoreFoundation")
            or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        as_path = (
            ctypes.util.find_library("ApplicationServices")
            or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        with contextlib.suppress(Exception):
            cg = ctypes.CDLL(cg_path)
            cf = ctypes.CDLL(cf_path)
            self._configure_cg_signatures(cg, cf)
            self._cg = cg
            self._cf = cf
            self._available = True
        with contextlib.suppress(Exception):
            app_svc = ctypes.CDLL(as_path)
            app_svc.AXIsProcessTrusted.argtypes = []
            app_svc.AXIsProcessTrusted.restype = ctypes.c_bool
            self._app_services = app_svc

    @staticmethod
    def _configure_cg_signatures(cg: Any, cf: Any) -> None:
        c_void_p = ctypes.c_void_p
        c_uint32 = ctypes.c_uint32
        c_int32 = ctypes.c_int32
        c_int64 = ctypes.c_int64
        c_uint16 = ctypes.c_uint16
        c_uint64 = ctypes.c_uint64
        c_bool = ctypes.c_bool

        cg.CGEventCreate.argtypes = [c_void_p]
        cg.CGEventCreate.restype = c_void_p

        cg.CGEventGetLocation.argtypes = [c_void_p]
        cg.CGEventGetLocation.restype = CGPoint

        cg.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint16, c_bool]
        cg.CGEventCreateKeyboardEvent.restype = c_void_p

        cg.CGEventCreateMouseEvent.argtypes = [c_void_p, c_uint32, CGPoint, c_uint32]
        cg.CGEventCreateMouseEvent.restype = c_void_p

        # Non-variadic CGEventCreateScrollWheelEvent2 avoids Apple Silicon ARM64 varargs ABI crashes.
        cg.CGEventCreateScrollWheelEvent2.argtypes = [c_void_p, c_uint32, c_uint32, c_int32, c_int32, c_int32]
        cg.CGEventCreateScrollWheelEvent2.restype = c_void_p

        cg.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
        cg.CGEventSetFlags.restype = None

        cg.CGEventSetIntegerValueField.argtypes = [c_void_p, c_uint32, c_int64]
        cg.CGEventSetIntegerValueField.restype = None

        cg.CGEventPost.argtypes = [c_uint32, c_void_p]
        cg.CGEventPost.restype = None

        cg.CGEventSourceKeyState.argtypes = [c_int32, c_uint16]
        cg.CGEventSourceKeyState.restype = c_bool

        cg.CGEventSourceButtonState.argtypes = [c_int32, c_uint32]
        cg.CGEventSourceButtonState.restype = c_bool

        cf.CFRelease.argtypes = [c_void_p]
        cf.CFRelease.restype = None

    @property
    def available(self) -> bool:
        return self._available

    def is_accessibility_trusted(self) -> bool:
        if not self._available:
            return False
        if self._app_services is None:
            return True
        try:
            return bool(self._app_services.AXIsProcessTrusted())
        except Exception:
            return True

    def _release_event(self, event: Any) -> None:
        if event and self._cf is not None:
            with contextlib.suppress(Exception):
                self._cf.CFRelease(event)

    def _post_event(self, event: Any) -> None:
        if not event or self._cg is None:
            return
        try:
            with contextlib.suppress(Exception):
                self._cg.CGEventSetIntegerValueField(event, _CG_EVENT_SOURCE_USER_DATA, KEYFORGE_EXTRA_INFO)
            self._cg.CGEventPost(_CG_HID_EVENT_TAP, event)
        finally:
            self._release_event(event)

    @staticmethod
    def _mouse_event_types(button: MouseButton, is_down: bool) -> tuple[int, int]:
        if button == MouseButton.LEFT:
            return (_CG_EVENT_LEFT_MOUSE_DOWN if is_down else _CG_EVENT_LEFT_MOUSE_UP, 0)
        if button == MouseButton.RIGHT:
            return (_CG_EVENT_RIGHT_MOUSE_DOWN if is_down else _CG_EVENT_RIGHT_MOUSE_UP, 1)
        if button == MouseButton.MIDDLE:
            return (_CG_EVENT_OTHER_MOUSE_DOWN if is_down else _CG_EVENT_OTHER_MOUSE_UP, 2)
        if button == MouseButton.X1:
            return (_CG_EVENT_OTHER_MOUSE_DOWN if is_down else _CG_EVENT_OTHER_MOUSE_UP, 3)
        if button == MouseButton.X2:
            return (_CG_EVENT_OTHER_MOUSE_DOWN if is_down else _CG_EVENT_OTHER_MOUSE_UP, 4)
        return (_CG_EVENT_LEFT_MOUSE_DOWN if is_down else _CG_EVENT_LEFT_MOUSE_UP, 0)

    def _current_cg_point(self) -> CGPoint:
        x, y = self.get_cursor_pos()
        return CGPoint(float(x), float(y))

    def key_down(self, key: KeyInfo) -> None:
        keycode = VK_TO_MAC_KEYCODE.get(key.vk_code)
        if keycode is None:
            return
        with self._lock:
            flag = _VK_TO_CG_FLAG.get(key.vk_code, 0)
            if flag:
                self._active_flags |= flag
            if not self._available:
                return
            evt = self._cg.CGEventCreateKeyboardEvent(None, keycode, True)
            if not evt:
                return
            try:
                if self._active_flags:
                    self._cg.CGEventSetFlags(evt, self._active_flags)
            except Exception:
                self._release_event(evt)
                raise
            self._post_event(evt)

    def key_up(self, key: KeyInfo) -> None:
        keycode = VK_TO_MAC_KEYCODE.get(key.vk_code)
        if keycode is None:
            return
        with self._lock:
            flag = _VK_TO_CG_FLAG.get(key.vk_code, 0)
            if flag:
                self._active_flags &= ~flag
            if not self._available:
                return
            evt = self._cg.CGEventCreateKeyboardEvent(None, keycode, False)
            if not evt:
                return
            try:
                if self._active_flags or keycode in _MAC_MODIFIER_KEYCODES:
                    self._cg.CGEventSetFlags(evt, self._active_flags)
            except Exception:
                self._release_event(evt)
                raise
            self._post_event(evt)

    def mouse_down(self, button: MouseButton) -> None:
        with self._lock:
            self._held_buttons.add(button)
            if not self._available:
                return
            ev_type, cg_btn = self._mouse_event_types(button, is_down=True)
            pt = self._current_cg_point()
            evt = self._cg.CGEventCreateMouseEvent(None, ev_type, pt, cg_btn)
            self._post_event(evt)

    def mouse_up(self, button: MouseButton) -> None:
        with self._lock:
            self._held_buttons.discard(button)
            if not self._available:
                return
            ev_type, cg_btn = self._mouse_event_types(button, is_down=False)
            pt = self._current_cg_point()
            evt = self._cg.CGEventCreateMouseEvent(None, ev_type, pt, cg_btn)
            self._post_event(evt)

    def move_cursor(self, x: int, y: int, relative: bool = False) -> None:
        with self._lock:
            if relative:
                cur_x, cur_y = self.get_cursor_pos()
                target_x = cur_x + int(x)
                target_y = cur_y + int(y)
            else:
                target_x = int(x)
                target_y = int(y)
            self._fallback_x = target_x
            self._fallback_y = target_y
            if not self._available:
                return

            if MouseButton.LEFT in self._held_buttons:
                ev_type, cg_btn = _CG_EVENT_LEFT_MOUSE_DRAGGED, 0
            elif MouseButton.RIGHT in self._held_buttons:
                ev_type, cg_btn = _CG_EVENT_RIGHT_MOUSE_DRAGGED, 1
            elif self._held_buttons:
                ev_type, cg_btn = _CG_EVENT_OTHER_MOUSE_DRAGGED, 2
            else:
                ev_type, cg_btn = _CG_EVENT_MOUSE_MOVED, 0

            evt = self._cg.CGEventCreateMouseEvent(None, ev_type, CGPoint(float(target_x), float(target_y)), cg_btn)
            self._post_event(evt)

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        if dx == 0 and dy == 0:
            return
        with self._lock:
            if not self._available:
                return
            wheel_count = 2 if dx != 0 else 1
            evt = self._cg.CGEventCreateScrollWheelEvent2(
                None,
                _CG_SCROLL_EVENT_UNIT_LINE,
                wheel_count,
                int(dy),
                int(-dx),
                0,
            )
            self._post_event(evt)

    def get_cursor_pos(self) -> tuple[int, int]:
        with self._lock:
            if not self._available:
                return (self._fallback_x, self._fallback_y)
            evt = self._cg.CGEventCreate(None)
            if not evt:
                return (self._fallback_x, self._fallback_y)
            try:
                loc = self._cg.CGEventGetLocation(evt)
                return (int(round(loc.x)), int(round(loc.y)))
            finally:
                self._release_event(evt)

    def is_key_down(self, vk_code: int) -> bool:
        if not self._available:
            return False
        keycode = VK_TO_MAC_KEYCODE.get(vk_code)
        if keycode is None:
            return False
        try:
            return bool(self._cg.CGEventSourceKeyState(_CG_EVENT_SOURCE_STATE_COMBINED_SESSION, keycode))
        except Exception:
            return False

    def is_mouse_down(self, vk_code: int) -> bool:
        if not self._available:
            return False
        btn_map = {
            VK_LBUTTON: 0,
            VK_RBUTTON: 1,
            VK_MBUTTON: 2,
            VK_XBUTTON1: 3,
            VK_XBUTTON2: 4,
        }
        cg_btn = btn_map.get(vk_code)
        if cg_btn is None:
            return False
        try:
            return bool(self._cg.CGEventSourceButtonState(_CG_EVENT_SOURCE_STATE_COMBINED_SESSION, cg_btn))
        except Exception:
            return False

    def close(self) -> None:
        return None


class LinuxX11InputBackend:
    """Linux X11 / XTest ctypes backend with thread safety and graceful headless/Wayland fallback."""

    def __init__(self, connect_display: bool = True) -> None:
        self._lock = threading.RLock()
        self._x11: Any = None
        self._xtst: Any = None
        self._display: Any = None
        self._root_window: int = 0
        self._screen: int = 0
        self._available = False
        self._keycode_cache: dict[int, int] = {}
        self._keymap_buf = (ctypes.c_char * 32)()
        self._fallback_x = 0
        self._fallback_y = 0
        self._unavailable_reason = ""
        self._finalizer: weakref.finalize | None = None
        if connect_display:
            self._init_x11()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def _init_x11(self) -> None:
        if not os.environ.get("DISPLAY", "").strip():
            self._unavailable_reason = "DISPLAY environment variable is not set."
            return

        x11_name = ctypes.util.find_library("X11") or "libX11.so.6"
        xtst_name = ctypes.util.find_library("Xtst") or "libXtst.so.6"
        try:
            x11 = ctypes.CDLL(x11_name)
            xtst = ctypes.CDLL(xtst_name)
        except OSError as exc:
            self._unavailable_reason = f"X11/XTest libraries not available: {exc}"
            return

        try:
            self._configure_x11_signatures(x11, xtst)
            display = x11.XOpenDisplay(None)
            if not display:
                self._unavailable_reason = "Could not connect to X11 display."
                return

            ev_base = ctypes.c_int(0)
            err_base = ctypes.c_int(0)
            major = ctypes.c_int(0)
            minor = ctypes.c_int(0)
            has_xtest = xtst.XTestQueryExtension(
                display,
                ctypes.byref(ev_base),
                ctypes.byref(err_base),
                ctypes.byref(major),
                ctypes.byref(minor),
            )
            if not has_xtest:
                x11.XCloseDisplay(display)
                self._unavailable_reason = "XTEST extension is not enabled on this X server."
                return

            self._x11 = x11
            self._xtst = xtst
            self._display = display
            self._screen = int(x11.XDefaultScreen(display))
            self._root_window = int(x11.XDefaultRootWindow(display))
            self._available = True
            self._unavailable_reason = ""
            self._finalizer = weakref.finalize(self, _close_x11_display, x11, display)
        except Exception as exc:
            self._unavailable_reason = str(exc)
            self._available = False

    @staticmethod
    def _configure_x11_signatures(x11: Any, xtst: Any) -> None:
        c_void_p = ctypes.c_void_p
        c_char_p = ctypes.c_char_p
        c_int = ctypes.c_int
        c_uint = ctypes.c_uint
        c_ulong = ctypes.c_ulong
        c_int_p = ctypes.POINTER(c_int)
        c_uint_p = ctypes.POINTER(c_uint)
        c_ulong_p = ctypes.POINTER(c_ulong)

        x11.XOpenDisplay.argtypes = [c_char_p]
        x11.XOpenDisplay.restype = c_void_p

        x11.XCloseDisplay.argtypes = [c_void_p]
        x11.XCloseDisplay.restype = c_int

        x11.XFlush.argtypes = [c_void_p]
        x11.XFlush.restype = c_int

        x11.XDefaultScreen.argtypes = [c_void_p]
        x11.XDefaultScreen.restype = c_int

        x11.XDefaultRootWindow.argtypes = [c_void_p]
        x11.XDefaultRootWindow.restype = c_ulong

        x11.XKeysymToKeycode.argtypes = [c_void_p, c_ulong]
        x11.XKeysymToKeycode.restype = c_uint

        x11.XQueryKeymap.argtypes = [c_void_p, ctypes.POINTER(ctypes.c_char)]
        x11.XQueryKeymap.restype = c_int

        x11.XQueryPointer.argtypes = [
            c_void_p,
            c_ulong,
            c_ulong_p,
            c_ulong_p,
            c_int_p,
            c_int_p,
            c_int_p,
            c_int_p,
            c_uint_p,
        ]
        x11.XQueryPointer.restype = c_int

        xtst.XTestQueryExtension.argtypes = [c_void_p, c_int_p, c_int_p, c_int_p, c_int_p]
        xtst.XTestQueryExtension.restype = c_int

        xtst.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
        xtst.XTestFakeKeyEvent.restype = c_int

        xtst.XTestFakeButtonEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
        xtst.XTestFakeButtonEvent.restype = c_int

        xtst.XTestFakeMotionEvent.argtypes = [c_void_p, c_int, c_int, c_int, c_ulong]
        xtst.XTestFakeMotionEvent.restype = c_int

        xtst.XTestFakeRelativeMotionEvent.argtypes = [c_void_p, c_int, c_int, c_ulong]
        xtst.XTestFakeRelativeMotionEvent.restype = c_int

    def _resolve_keycode(self, vk_code: int) -> int:
        cached = self._keycode_cache.get(vk_code)
        if cached is not None:
            return cached
        keysym = VK_TO_X11_KEYSYM.get(vk_code)
        if keysym is None or not self._available:
            return 0
        kc = int(self._x11.XKeysymToKeycode(self._display, keysym))
        if kc > 0:
            self._keycode_cache[vk_code] = kc
        return kc

    def key_down(self, key: KeyInfo) -> None:
        with self._lock:
            if not self._available:
                return
            kc = self._resolve_keycode(key.vk_code)
            if kc <= 0:
                return
            self._xtst.XTestFakeKeyEvent(self._display, kc, 1, 0)
            self._x11.XFlush(self._display)

    def key_up(self, key: KeyInfo) -> None:
        with self._lock:
            if not self._available:
                return
            kc = self._resolve_keycode(key.vk_code)
            if kc <= 0:
                return
            self._xtst.XTestFakeKeyEvent(self._display, kc, 0, 0)
            self._x11.XFlush(self._display)

    def mouse_down(self, button: MouseButton) -> None:
        with self._lock:
            if not self._available:
                return
            btn = _X11_MOUSE_BUTTON_MAP.get(button, 1)
            self._xtst.XTestFakeButtonEvent(self._display, btn, 1, 0)
            self._x11.XFlush(self._display)

    def mouse_up(self, button: MouseButton) -> None:
        with self._lock:
            if not self._available:
                return
            btn = _X11_MOUSE_BUTTON_MAP.get(button, 1)
            self._xtst.XTestFakeButtonEvent(self._display, btn, 0, 0)
            self._x11.XFlush(self._display)

    def move_cursor(self, x: int, y: int, relative: bool = False) -> None:
        with self._lock:
            if relative:
                self._fallback_x += int(x)
                self._fallback_y += int(y)
                if self._available:
                    self._xtst.XTestFakeRelativeMotionEvent(self._display, int(x), int(y), 0)
                    self._x11.XFlush(self._display)
            else:
                self._fallback_x = int(x)
                self._fallback_y = int(y)
                if self._available:
                    self._xtst.XTestFakeMotionEvent(self._display, self._screen, int(x), int(y), 0)
                    self._x11.XFlush(self._display)

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        with self._lock:
            if not self._available:
                return
            if dy != 0:
                btn_v = 4 if dy > 0 else 5
                for _ in range(min(50, abs(int(dy)))):
                    self._xtst.XTestFakeButtonEvent(self._display, btn_v, 1, 0)
                    self._xtst.XTestFakeButtonEvent(self._display, btn_v, 0, 0)
            if dx != 0:
                btn_h = 7 if dx > 0 else 6
                for _ in range(min(50, abs(int(dx)))):
                    self._xtst.XTestFakeButtonEvent(self._display, btn_h, 1, 0)
                    self._xtst.XTestFakeButtonEvent(self._display, btn_h, 0, 0)
            self._x11.XFlush(self._display)

    def _query_pointer_unlocked(self) -> tuple[int, int, int]:
        if not self._available:
            return (self._fallback_x, self._fallback_y, 0)
        root_ret = ctypes.c_ulong(0)
        child_ret = ctypes.c_ulong(0)
        root_x = ctypes.c_int(0)
        root_y = ctypes.c_int(0)
        win_x = ctypes.c_int(0)
        win_y = ctypes.c_int(0)
        mask = ctypes.c_uint(0)
        ok = self._x11.XQueryPointer(
            self._display,
            self._root_window,
            ctypes.byref(root_ret),
            ctypes.byref(child_ret),
            ctypes.byref(root_x),
            ctypes.byref(root_y),
            ctypes.byref(win_x),
            ctypes.byref(win_y),
            ctypes.byref(mask),
        )
        if not ok:
            return (self._fallback_x, self._fallback_y, 0)
        return (int(root_x.value), int(root_y.value), int(mask.value))

    def get_cursor_pos(self) -> tuple[int, int]:
        with self._lock:
            x, y, _ = self._query_pointer_unlocked()
            return (x, y)

    def get_pressed_states(
        self,
        key_vks: Sequence[int],
        mouse_vks: Sequence[int],
    ) -> tuple[dict[int, bool], dict[int, bool]]:
        key_states: dict[int, bool] = {}
        mouse_states: dict[int, bool] = {}
        with self._lock:
            if not self._available:
                for vk in key_vks:
                    key_states[vk] = False
                for vk in mouse_vks:
                    mouse_states[vk] = False
                return key_states, mouse_states

            if key_vks:
                self._x11.XQueryKeymap(self._display, self._keymap_buf)
                raw = bytes(self._keymap_buf)
                for vk in key_vks:
                    kc = self._resolve_keycode(vk)
                    if 0 < kc < 256:
                        key_states[vk] = bool(raw[kc >> 3] & (1 << (kc & 7)))
                    else:
                        key_states[vk] = False

            if mouse_vks:
                _, _, mask = self._query_pointer_unlocked()
                for vk in mouse_vks:
                    bit = _X11_MOUSE_VK_MASK.get(vk, 0)
                    mouse_states[vk] = bool(mask & bit)

        return key_states, mouse_states

    def is_key_down(self, vk_code: int) -> bool:
        key_states, _ = self.get_pressed_states((vk_code,), ())
        return key_states.get(vk_code, False)

    def is_mouse_down(self, vk_code: int) -> bool:
        _, mouse_states = self.get_pressed_states((), (vk_code,))
        return mouse_states.get(vk_code, False)

    def close(self) -> None:
        with self._lock:
            disp = self._display
            x11 = self._x11
            finalizer = self._finalizer
            self._finalizer = None
            self._display = None
            self._available = False
            self._keycode_cache.clear()
        if finalizer is not None:
            finalizer.detach()
        _close_x11_display(x11, disp)


def _close_x11_display(x11: Any, disp: Any) -> None:
    if disp and x11 is not None:
        with contextlib.suppress(Exception):
            x11.XCloseDisplay(disp)


def create_os_input_backend() -> InputBackend:
    """Instantiate the native input backend appropriate for the current OS."""
    if sys.platform == "win32":
        return Win32InputBackend()
    if sys.platform == "darwin":
        return MacOSInputBackend()
    return LinuxX11InputBackend()


def get_platform_capabilities(backend: InputBackend | None = None) -> PlatformInputCapabilities:
    """Return input injection, global polling, and OS permission status cleanly."""
    if sys.platform == "win32":
        return PlatformInputCapabilities(
            platform="win32",
            backend_name="Win32 SendInput",
            can_inject=True,
            can_poll_keys=True,
            can_poll_cursor=True,
        )

    if sys.platform == "darwin":
        mac_backend = backend if isinstance(backend, MacOSInputBackend) else _get_shared_poll_backend()
        if isinstance(mac_backend, MacOSInputBackend) and mac_backend.available:
            trusted = mac_backend.is_accessibility_trusted()
            notice = (
                ""
                if trusted
                else (
                    "macOS Accessibility permission is needed for live key injection & global hotkeys "
                    "(System Settings -> Privacy & Security -> Accessibility). "
                    "Enable Dry-Run Sim anytime to test without permissions."
                )
            )
            return PlatformInputCapabilities(
                platform="darwin",
                backend_name="macOS CoreGraphics",
                can_inject=trusted,
                can_poll_keys=True,
                can_poll_cursor=True,
                requires_permission=not trusted,
                notice=notice,
            )
        return PlatformInputCapabilities(
            platform="darwin",
            backend_name="Unavailable",
            can_inject=False,
            can_poll_keys=False,
            can_poll_cursor=False,
            notice="CoreGraphics framework could not be initialized on macOS.",
        )

    linux_backend = backend if isinstance(backend, LinuxX11InputBackend) else _get_shared_poll_backend()
    is_wayland = bool(os.environ.get("WAYLAND_DISPLAY", "").strip()) or (
        os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
    )
    if isinstance(linux_backend, LinuxX11InputBackend) and linux_backend.available:
        return PlatformInputCapabilities(
            platform="linux",
            backend_name="Linux X11 (XWayland)" if is_wayland else "Linux X11/XTest",
            can_inject=True,
            can_poll_keys=True,
            can_poll_cursor=True,
            notice=(
                "Wayland session detected: X11/XTest works in XWayland windows "
                "(native Wayland compositors isolate global input)."
                if is_wayland
                else ""
            ),
        )

    if is_wayland:
        return PlatformInputCapabilities(
            platform="linux",
            backend_name="Wayland (Preview Only)",
            can_inject=False,
            can_poll_keys=False,
            can_poll_cursor=False,
            notice=(
                "Native Wayland isolates global input injection without XWayland/libXtst. "
                "Use an X11 session for live input or enable Dry-Run Simulation."
            ),
        )

    reason = linux_backend.unavailable_reason if isinstance(linux_backend, LinuxX11InputBackend) else ""
    detail = f" ({reason})" if reason else ""
    return PlatformInputCapabilities(
        platform="linux",
        backend_name="Unavailable",
        can_inject=False,
        can_poll_keys=False,
        can_poll_cursor=False,
        notice=f"Linux X11/XTest unavailable{detail}. Install libx11 & libxtst or use Dry-Run Simulation.",
    )


_POLL_LOCK = threading.Lock()
_SHARED_POLL_BACKEND: MacOSInputBackend | LinuxX11InputBackend | None = None
_SHARED_POLL_PLATFORM: str | None = None


def _get_shared_poll_backend() -> MacOSInputBackend | LinuxX11InputBackend | None:
    global _SHARED_POLL_BACKEND, _SHARED_POLL_PLATFORM
    if sys.platform == "win32":
        return None
    with _POLL_LOCK:
        if _SHARED_POLL_BACKEND is None or _SHARED_POLL_PLATFORM != sys.platform:
            if _SHARED_POLL_BACKEND is not None:
                with contextlib.suppress(Exception):
                    _SHARED_POLL_BACKEND.close()
            _SHARED_POLL_PLATFORM = sys.platform
            _SHARED_POLL_BACKEND = MacOSInputBackend() if sys.platform == "darwin" else LinuxX11InputBackend()
        return _SHARED_POLL_BACKEND


def platform_supports_key_polling() -> bool:
    """Return True if background OS key polling is available on the active platform."""
    if sys.platform == "win32":
        return True
    backend = _get_shared_poll_backend()
    return bool(backend is not None and backend.available)


def platform_get_async_key_state(vk_code: int) -> bool:
    """Query whether a Virtual-Key (keyboard or mouse VK) is depressed across Win32/macOS/Linux."""
    if sys.platform == "win32":
        return get_async_key_state(vk_code)
    backend = _get_shared_poll_backend()
    if backend is None or not backend.available:
        return False
    if vk_code in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2):
        return backend.is_mouse_down(vk_code)
    return backend.is_key_down(vk_code)


def platform_get_pressed_states(
    key_vks: Sequence[int],
    mouse_vks: Sequence[int],
) -> tuple[dict[int, bool], dict[int, bool]]:
    """Batch-read key and mouse button states efficiently on Windows, macOS, and Linux."""
    if sys.platform == "win32":
        return (
            {vk: get_async_key_state(vk) for vk in key_vks},
            {vk: get_async_key_state(vk) for vk in mouse_vks},
        )
    backend = _get_shared_poll_backend()
    if backend is None or not backend.available:
        return (
            {vk: False for vk in key_vks},
            {vk: False for vk in mouse_vks},
        )
    if isinstance(backend, LinuxX11InputBackend):
        return backend.get_pressed_states(key_vks, mouse_vks)
    return (
        {vk: backend.is_key_down(vk) for vk in key_vks},
        {vk: backend.is_mouse_down(vk) for vk in mouse_vks},
    )


def platform_get_cursor_pos() -> tuple[int, int]:
    """Query the current global OS cursor coordinates across Windows, macOS, and Linux."""
    if sys.platform == "win32":
        return get_system_cursor_pos()
    backend = _get_shared_poll_backend()
    if backend is None or not backend.available:
        return (0, 0)
    return backend.get_cursor_pos()
