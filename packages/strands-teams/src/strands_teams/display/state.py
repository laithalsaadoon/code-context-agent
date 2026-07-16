"""Mutable display state and phase/discovery models for event rendering.

This module provides the runtime state a consumer accumulates while an agent
run streams: active/completed tool calls, phase progression, discovery events,
and dispatched-team status. All types are domain-agnostic — phase identity and
names come from an injected :class:`~strands_teams.phases.PhaseSpec`, and
discovery-event kinds are plain strings supplied by the use case.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ConfigDict, Field

from ..models import FrozenModel, StrictModel
from ..phases import PhaseSpec  # noqa: TC001 — pydantic needs PhaseSpec resolvable at runtime for the model field


class DiscoveryEvent(FrozenModel):
    """A notable discovery made during a run.

    ``kind`` is a free-form string tag supplied by the use case's discovery
    extractor (e.g. ``"files_discovered"``); the framework imposes no enum.
    """

    kind: str = Field(description="Use-case-defined discovery category tag")
    summary: str = Field(description="Human-readable summary (e.g., 'Found 847 files')")
    tool_name: str = Field(description="Tool that produced this discovery")
    timestamp: float = Field(description="monotonic timestamp")
    detail: dict[str, Any] = Field(default_factory=dict, description="Optional structured detail")


class PhaseState(StrictModel):
    """Mutable runtime state for a single run phase.

    The ``phase`` index and ``name``/``description`` are resolved against the
    injected :class:`~strands_teams.phases.PhaseSpec` when the phase is entered.
    """

    phase: int
    name: str
    description: str
    started_at: float
    completed_at: float | None = None

    @property
    def is_complete(self) -> bool:
        """Whether this phase has completed."""
        return self.completed_at is not None

    @property
    def elapsed_seconds(self) -> float:
        """Seconds elapsed since phase started (or total if completed)."""
        if self.completed_at is None:
            return time.monotonic() - self.started_at
        return self.completed_at - self.started_at


class TeamDispatchState(StrictModel):
    """State for a dispatched specialist team.

    Attributes:
        team_id: Unique identifier for the team (e.g., "security", "architecture").
        mandate: One-line description of the team's investigation focus.
        agent_count: Number of agents in the team swarm.
        status: Current status ("running", "done", "error").
        started_at: Monotonic timestamp when the team was dispatched.
        duration_seconds: Total elapsed seconds (set when completed).
    """

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    team_id: str
    mandate: str
    agent_count: int = 0
    status: str = "running"
    started_at: float | None = None
    duration_seconds: float = 0.0


class ToolCallState(StrictModel):
    """State for a single tool call.

    Attributes:
        tool_call_id: Unique identifier for the tool call.
        tool_name: Name of the tool being executed.
        agent_name: Name of the agent that invoked this tool.
        args_buffer: Accumulated tool arguments (streaming).
        result: Tool execution result (when complete).
        status: Current status ("running", "completed", "error").
    """

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
        arbitrary_types_allowed=True,
    )

    tool_call_id: str
    tool_name: str
    agent_name: str = ""
    args_buffer: str = ""
    result: Any = None
    status: str = "running"


class AgentDisplayState(StrictModel):
    """Mutable state for agent display rendering.

    This class tracks all state needed to render an agent's progress in a
    terminal or UI. It accumulates streaming text, tracks active and completed
    tool calls, phase progression, discoveries, dispatched teams, and error
    state. Phase identity is resolved against the injected ``phase_spec``.

    Attributes:
        phase_spec: Injected phase specification (names, count, tool map).
        current_phase: Description of the current phase.
        text_buffer: Accumulated streaming text from the agent.
        active_message_id: ID of currently streaming message.
        active_tool: Currently executing tool state (if any).
        completed_tools: List of completed tool executions.
        state_snapshot: Latest state snapshot from the agent.
        error: Error message if the run failed.
        completed: Whether the run has finished.
        thread_id: Current thread identifier.
        run_id: Current run identifier.

    Example:
        >>> state = AgentDisplayState(phase_spec=my_spec)
        >>> state.text_buffer += "Analyzing repository..."
    """

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
        arbitrary_types_allowed=True,
    )

    phase_spec: PhaseSpec

    current_phase: str = ""
    text_buffer: str = ""
    active_message_id: str | None = None
    active_tool: ToolCallState | None = None
    completed_tools: list[ToolCallState] = Field(default_factory=list)
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    completed: bool = False
    thread_id: str | None = None
    run_id: str | None = None

    # Metrics for the TUI dashboard
    start_time: float | None = None
    max_duration: float = 1200.0
    max_turns: int = 1000
    turn_count: int = 0
    tool_errors: int = 0
    tool_start_time: float | None = None

    # Phase tracking
    phases: list[PhaseState] = Field(default_factory=list)
    current_phase_index: int = -1
    discoveries: list[DiscoveryEvent] = Field(default_factory=list)
    max_discoveries: int = 50

    # Team tracking (coordinator pipeline)
    teams: list[TeamDispatchState] = Field(default_factory=list)

    def clear_text_buffer(self) -> str:
        """Clear and return the text buffer.

        Returns:
            The text buffer contents before clearing.
        """
        text = self.text_buffer
        self.text_buffer = ""
        return text

    def complete_active_tool(self, result: Any = None) -> None:
        """Mark the active tool as completed and move to history.

        Args:
            result: Optional result to store with the tool call.
        """
        if self.active_tool:
            self.active_tool.status = "completed"
            self.active_tool.result = result
            self.completed_tools.append(self.active_tool)
            self.active_tool = None

    def record_tool(self, *, tool_id: str, tool_name: str, status: str, result: str) -> None:
        """Record a tool call's start or completion for the dashboard.

        Called by ``ToolDisplayHook`` on both the before- and after-tool-call
        events. A ``running`` status opens a new active tool (and advances the
        phase resolved from the tool name); any other status closes it and moves
        it to history, counting errors.

        Args:
            tool_id: The tool-use identifier from the event.
            tool_name: Name of the tool being executed.
            status: ``"running"`` to open the call, ``"completed"``/``"error"`` to close it.
            result: Truncated result string to store when closing the call.
        """
        if status == "running":
            self.active_tool = ToolCallState(tool_call_id=tool_id, tool_name=tool_name, status="running")
            self.tool_start_time = time.monotonic()
            phase = self.phase_spec.resolve(tool_name)
            if phase is not None:
                self.advance_phase(phase)
            return

        if self.active_tool is not None:
            self.active_tool.status = status
            self.active_tool.result = result
            self.completed_tools.append(self.active_tool)
            self.active_tool = None
        else:
            self.completed_tools.append(
                ToolCallState(tool_call_id=tool_id, tool_name=tool_name, status=status, result=result)
            )
        if status == "error":
            self.tool_errors += 1
        self.tool_start_time = None

    def get_recent_tools(self, count: int = 5) -> list[ToolCallState]:
        """Get the most recent completed tools.

        Args:
            count: Maximum number of tools to return.

        Returns:
            List of recently completed tool states.
        """
        return self.completed_tools[-count:]

    def get_tool_stats(self) -> dict[str, int]:
        """Get tool call counts grouped by prefix.

        Groups tools by their name prefix (e.g., ``lsp_*``, ``git_*``) for
        display in the TUI dashboard.

        Returns:
            Dictionary mapping tool prefixes to call counts.
        """
        stats: dict[str, int] = {}
        for tool in self.completed_tools:
            parts = tool.tool_name.split("_")
            prefix = parts[0] + "_*" if len(parts) > 1 else tool.tool_name
            stats[prefix] = stats.get(prefix, 0) + 1
        return stats

    def get_elapsed_seconds(self) -> float:
        """Get elapsed time since the run started.

        Returns:
            Elapsed seconds, or 0.0 if not started.
        """
        if self.start_time is None:
            return 0.0
        return time.monotonic() - self.start_time

    def get_tool_elapsed_seconds(self) -> float:
        """Get elapsed time for the current tool.

        Returns:
            Elapsed seconds for the active tool, or 0.0 if no active tool.
        """
        if self.tool_start_time is None:
            return 0.0
        return time.monotonic() - self.tool_start_time

    def get_success_count(self) -> int:
        """Get count of successful tool calls.

        Returns:
            Number of completed tools minus errors.
        """
        return len(self.completed_tools) - self.tool_errors

    def advance_phase(self, phase: int) -> None:
        """Advance to a new phase if it is higher than the current one.

        Phase name and description are resolved from the injected
        ``phase_spec``.

        Args:
            phase: 1-based phase index to advance to.
        """
        # Don't regress to a lower phase
        if self.phases and phase <= self.phases[-1].phase:
            return

        now = time.monotonic()
        if self.phases and not self.phases[-1].is_complete:
            self.phases[-1].completed_at = now

        self.phases.append(
            PhaseState(
                phase=phase,
                name=self.phase_spec.name(phase),
                description=self.phase_spec.description(phase),
                started_at=now,
            ),
        )
        self.current_phase_index = len(self.phases) - 1

    def add_discovery(self, event: DiscoveryEvent) -> None:
        """Add a discovery event, evicting oldest if at capacity.

        Args:
            event: DiscoveryEvent to add.
        """
        self.discoveries.append(event)
        while len(self.discoveries) > self.max_discoveries:
            self.discoveries.pop(0)

    def start_team(self, team_id: str, mandate: str, agent_count: int = 0) -> None:
        """Register a newly dispatched team.

        Args:
            team_id: Unique team identifier.
            mandate: One-line description of the team's focus.
            agent_count: Number of agents in the team.
        """
        self.teams.append(
            TeamDispatchState(
                team_id=team_id,
                mandate=mandate,
                agent_count=agent_count,
                status="running",
                started_at=time.monotonic(),
            ),
        )

    def complete_team(self, team_id: str, status: str = "done") -> None:
        """Mark a team as completed.

        Args:
            team_id: Team identifier to mark complete.
            status: Final status ("done" or "error").
        """
        for team in self.teams:
            if team.team_id == team_id:
                team.status = status
                if team.started_at is not None:
                    team.duration_seconds = time.monotonic() - team.started_at
                break

    def reset(self) -> None:
        """Reset state for a new run (preserves the injected ``phase_spec``)."""
        self.current_phase = ""
        self.text_buffer = ""
        self.active_message_id = None
        self.active_tool = None
        self.completed_tools = []
        self.state_snapshot = {}
        self.error = None
        self.completed = False
        self.start_time = None
        self.turn_count = 0
        self.tool_errors = 0
        self.tool_start_time = None
        self.phases = []
        self.current_phase_index = -1
        self.discoveries = []
        self.teams = []
