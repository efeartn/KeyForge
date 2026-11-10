"""Color palette, badge colors, and typography constants for the KeyForge UI."""

from __future__ import annotations

from keyforge.models.action import ActionType

# Core Background & Surface Colors (Dark Cyber-Industrial Slate)
BG_ROOT = "#0B0F17"
BG_PANEL = "#131A26"
BG_CARD = "#1A2333"
BG_CARD_ACTIVE = "#1E3A5F"
BG_INPUT = "#0E141F"
BORDER_SUBTLE = "#28354A"

# Text Colors
TEXT_PRIMARY = "#F1F5F9"
TEXT_SECONDARY = "#94A3B8"
TEXT_MUTED = "#64748B"

# Accent & Semantic Colors
ACCENT_PRIMARY = "#3B82F6"
ACCENT_PRIMARY_HOVER = "#2563EB"
ACCENT_SUCCESS = "#10B981"
ACCENT_SUCCESS_HOVER = "#059669"
ACCENT_WARNING = "#F59E0B"
ACCENT_DANGER = "#EF4444"
ACCENT_DANGER_HOVER = "#DC2626"
ACCENT_PURPLE = "#8B5CF6"

# Per-Action Type Badge Colors
ACTION_BADGE_COLORS: dict[ActionType, tuple[str, str]] = {
    ActionType.KEY_PRESS: ("#1E3A8A", "#93C5FD"),  # Deep Blue / Light Blue
    ActionType.KEY_DOWN: ("#1E40AF", "#BFDBFE"),
    ActionType.KEY_UP: ("#1E293B", "#CBD5E1"),
    ActionType.MOUSE_CLICK: ("#064E3B", "#6EE7B7"),  # Deep Emerald / Mint
    ActionType.MOUSE_MOVE: ("#164E63", "#67E8F9"),  # Deep Cyan / Aqua
    ActionType.MOUSE_SCROLL: ("#3B0764", "#D8B4FE"),  # Deep Violet / Lavender
    ActionType.WAIT: ("#78350F", "#FCD34D"),  # Deep Amber / Gold
    ActionType.REPEAT_BLOCK: ("#701A75", "#F0ABFC"),  # Deep Fuchsia / Pink
}


def get_badge_colors(action_type: ActionType) -> tuple[str, str]:
    """Return (background_hex, foreground_hex) for an ActionType badge."""
    return ACTION_BADGE_COLORS.get(action_type, ("#1E293B", "#E2E8F0"))
