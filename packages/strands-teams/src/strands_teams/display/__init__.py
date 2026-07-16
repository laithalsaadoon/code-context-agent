"""Parameterized Rich TUI display for agent team runs.

The display core is domain-agnostic: panel title, mode label, phase spec
(count + names), and discovery-extractor callable are all injected. Nothing
here hardcodes a product name, a phase total, or codebase-specific tool keys.
"""

from __future__ import annotations

from .base import EventConsumer
from .rich import QuietConsumer, RichEventConsumer, bind_live_renderable
from .state import (
    AgentDisplayState,
    DiscoveryEvent,
    PhaseState,
    TeamDispatchState,
    ToolCallState,
)

__all__ = [
    "AgentDisplayState",
    "DiscoveryEvent",
    "EventConsumer",
    "PhaseState",
    "QuietConsumer",
    "RichEventConsumer",
    "TeamDispatchState",
    "ToolCallState",
    "bind_live_renderable",
]
