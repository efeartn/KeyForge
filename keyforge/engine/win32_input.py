"""Native Win32 ctypes SendInput implementation for Windows 10/11 (x64/x86)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from keyforge.engine.backend import KEYFORGE_EXTRA_INFO
from keyforge.models.action import MouseButton
from keyforge.models.keymap import KeyInfo

# Win32 Constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
WHEEL_DELTA = 120

MAPVK_VK_TO_VSC = 0

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    """Win32 MOUSEINPUT structure."""

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    """Win32 KEYBDINPUT structure."""

    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    """Win32 HARDWAREINPUT structure."""

    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    """Union of MOUSEINPUT, KEYBDINPUT, and HARDWAREINPUT."""

    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    """Win32 INPUT structure passed to user32.SendInput."""

    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_u", _INPUT_UNION),
    ]


class POINT(ctypes.Structure):
    """Win32 POINT structure."""

    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


_user32 = ctypes.windll.user32
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT

_user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
_user32.MapVirtualKeyW.restype = wintypes.UINT

_user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL

_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL

_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = wintypes.SHORT


def _send_single_input(inp: INPUT) -> int:
    """Dispatch a single Win32 INPUT struct via user32.SendInput."""
    return int(_user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


class Win32InputBackend:
    """Production Windows input injection backend using user32.SendInput."""

    def key_down(self, key: KeyInfo) -> None:
        scan = key.scan_code or int(_user32.MapVirtualKeyW(key.vk_code, MAPVK_VK_TO_VSC))
        flags = 0
        if key.is_extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=key.vk_code,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=KEYFORGE_EXTRA_INFO,
            ),
        )
        _send_single_input(inp)

    def key_up(self, key: KeyInfo) -> None:
        scan = key.scan_code or int(_user32.MapVirtualKeyW(key.vk_code, MAPVK_VK_TO_VSC))
        flags = KEYEVENTF_KEYUP
        if key.is_extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=key.vk_code,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=KEYFORGE_EXTRA_INFO,
            ),
        )
        _send_single_input(inp)

    def mouse_down(self, button: MouseButton) -> None:
        flags, data = self._button_flags(button, is_up=False)
        inp = INPUT(
            type=INPUT_MOUSE,
            mi=MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=data,
                dwFlags=flags,
                time=0,
                dwExtraInfo=KEYFORGE_EXTRA_INFO,
            ),
        )
        _send_single_input(inp)

    def mouse_up(self, button: MouseButton) -> None:
        flags, data = self._button_flags(button, is_up=True)
        inp = INPUT(
            type=INPUT_MOUSE,
            mi=MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=data,
                dwFlags=flags,
                time=0,
                dwExtraInfo=KEYFORGE_EXTRA_INFO,
            ),
        )
        _send_single_input(inp)

    def move_cursor(self, x: int, y: int, relative: bool = False) -> None:
        if relative:
            inp = INPUT(
                type=INPUT_MOUSE,
                mi=MOUSEINPUT(
                    dx=x,
                    dy=y,
                    mouseData=0,
                    dwFlags=MOUSEEVENTF_MOVE,
                    time=0,
                    dwExtraInfo=KEYFORGE_EXTRA_INFO,
                ),
            )
            _send_single_input(inp)
        else:
            _user32.SetCursorPos(int(x), int(y))

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        if dy != 0:
            wheel_amount = ctypes.c_ulong(dy * WHEEL_DELTA).value
            inp_v = INPUT(
                type=INPUT_MOUSE,
                mi=MOUSEINPUT(
                    dx=0,
                    dy=0,
                    mouseData=wheel_amount,
                    dwFlags=MOUSEEVENTF_WHEEL,
                    time=0,
                    dwExtraInfo=KEYFORGE_EXTRA_INFO,
                ),
            )
            _send_single_input(inp_v)
        if dx != 0:
            hwheel_amount = ctypes.c_ulong(dx * WHEEL_DELTA).value
            inp_h = INPUT(
                type=INPUT_MOUSE,
                mi=MOUSEINPUT(
                    dx=0,
                    dy=0,
                    mouseData=hwheel_amount,
                    dwFlags=MOUSEEVENTF_HWHEEL,
                    time=0,
                    dwExtraInfo=KEYFORGE_EXTRA_INFO,
                ),
            )
            _send_single_input(inp_h)

    def get_cursor_pos(self) -> tuple[int, int]:
        pt = POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
        return (0, 0)

    @staticmethod
    def _button_flags(button: MouseButton, is_up: bool) -> tuple[int, int]:
        if button == MouseButton.LEFT:
            return (MOUSEEVENTF_LEFTUP if is_up else MOUSEEVENTF_LEFTDOWN, 0)
        if button == MouseButton.RIGHT:
            return (MOUSEEVENTF_RIGHTUP if is_up else MOUSEEVENTF_RIGHTDOWN, 0)
        if button == MouseButton.MIDDLE:
            return (MOUSEEVENTF_MIDDLEUP if is_up else MOUSEEVENTF_MIDDLEDOWN, 0)
        if button == MouseButton.X1:
            return (MOUSEEVENTF_XUP if is_up else MOUSEEVENTF_XDOWN, XBUTTON1)
        if button == MouseButton.X2:
            return (MOUSEEVENTF_XUP if is_up else MOUSEEVENTF_XDOWN, XBUTTON2)
        return (MOUSEEVENTF_LEFTUP if is_up else MOUSEEVENTF_LEFTDOWN, 0)
