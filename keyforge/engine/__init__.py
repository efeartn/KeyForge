"""Engine package for KeyForge input injection, execution, recording, and global hotkeys."""

from keyforge.engine.backend import (
    KEYFORGE_EXTRA_INFO,
    DispatchedEvent,
    DryRunBackend,
    InputBackend,
)
from keyforge.engine.executor import (
    ExecutionTelemetry,
    ExecutorState,
    MacroExecutor,
)
from keyforge.engine.hotkeys import GlobalHotkeyManager
from keyforge.engine.platform_input import (
    LinuxX11InputBackend,
    MacOSInputBackend,
    PlatformInputCapabilities,
    create_os_input_backend,
    get_platform_capabilities,
    platform_get_async_key_state,
    platform_get_cursor_pos,
    platform_get_pressed_states,
    platform_supports_key_polling,
)
from keyforge.engine.recorder import MacroRecorder, RecorderConfig
from keyforge.engine.win32_input import INPUT, Win32InputBackend

__all__ = [
    "DispatchedEvent",
    "DryRunBackend",
    "ExecutionTelemetry",
    "ExecutorState",
    "GlobalHotkeyManager",
    "INPUT",
    "InputBackend",
    "KEYFORGE_EXTRA_INFO",
    "LinuxX11InputBackend",
    "MacOSInputBackend",
    "MacroExecutor",
    "MacroRecorder",
    "PlatformInputCapabilities",
    "RecorderConfig",
    "Win32InputBackend",
    "create_os_input_backend",
    "get_platform_capabilities",
    "platform_get_async_key_state",
    "platform_get_cursor_pos",
    "platform_get_pressed_states",
    "platform_supports_key_polling",
]
