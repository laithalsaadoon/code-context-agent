"""Rich terminal display consumer for agent events.

This module provides a dashboard-style event consumer focused on tool
execution status rather than streaming text. The display is fixed-height and
never grows — it shows what's running, what finished, and phase progress.

Everything domain-specific is injected: the panel title, an optional mode
label, the phase specification (count + names, via :class:`AgentDisplayState`),
and a discovery-extractor callable. The framework hardcodes no ``/5``, no
product name, and no codebase-specific discovery keys.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .base import EventConsumer
from .state import AgentDisplayState, DiscoveryEvent, ToolCallState

if TYPE_CHECKING:
    from collections.abc import Callable

_RECENT_TOOLS_SHOWN = 8
_KB = 1024
_MB = 1024 * 1024
_MANDATE_TRUNCATE_LEN = 53


def bind_live_renderable(live: Live, builder: Callable[[], RenderableType]) -> None:
    """Point Rich Live's auto-refresh at a custom display builder.

    Rich calls ``get_renderable()`` on each refresh tick. Replacing it with
    *builder* lets the dashboard show fresh state (timer, tool elapsed, etc.)
    without manually calling ``live.update()`` on every change.
    """
    live.get_renderable = builder  # type: ignore[assignment]  # ty: ignore[invalid-assignment]


def _no_discovery(tool_name: str, result: Any) -> DiscoveryEvent | None:  # noqa: ARG001
    """Default discovery extractor: never produces an event."""
    return None


class RichEventConsumer(EventConsumer):
    """Dashboard-style consumer for agent execution.

    Shows a fixed-height panel with:
    - Timer and progress bar
    - Tool category breakdown with mini bars
    - Active tool with spinner
    - Recent tool history with timing and status
    - Phase indicator ``[i/N]`` where ``N`` comes from the injected phase spec
    - Discovery feed populated by the injected extractor

    No streaming text display. The agent's reasoning is not shown — only tool
    execution status matters for a long-running analysis agent.

    When teams have been dispatched the coordinator layout is rendered
    (adding a team-status table); otherwise the single-agent layout is used.
    """

    def __init__(
        self,
        state: AgentDisplayState,
        *,
        title: str,
        mode_label: str | None = None,
        discovery_extractor: Callable[[str, Any], DiscoveryEvent | None] | None = None,
        console: Console | None = None,
    ) -> None:
        """Initialize the dashboard consumer.

        Args:
            state: Display state carrying the injected phase spec.
            title: Panel title (replaces the hardcoded product name).
            mode_label: Optional badge appended to the title (e.g. ``"FULL"``).
            discovery_extractor: Callable mapping ``(tool_name, result)`` to a
                :class:`DiscoveryEvent` or ``None``. Defaults to a no-op. The
                consumer guards the call so a raising extractor never crashes
                the display.
            console: Optional Rich Console instance.
        """
        self.console = console or Console()
        self.state = state
        self._live: Live | None = None
        self._title = title
        self._mode_label = mode_label
        self._discovery_extractor = discovery_extractor or _no_discovery

    def _detect_phase(self, tool_name: str) -> None:
        """Advance the phase indicator based on tool name via the injected spec."""
        phase = self.state.phase_spec.resolve(tool_name)
        if phase is not None:
            self.state.advance_phase(phase)

    def _extract_discovery(self, tool_name: str, result: Any) -> DiscoveryEvent | None:
        """Run the injected discovery extractor, guarding against exceptions.

        Returns ``None`` (never raises) if the extractor returns nothing or
        fails — satisfying the "invalid input must not crash the display"
        contract regardless of extractor robustness.
        """
        try:
            return self._discovery_extractor(tool_name, result)
        except Exception:  # noqa: BLE001 — display must never crash on a bad extractor
            return None

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"

    def _build_progress_bar(self, ratio: float, width: int = 40) -> str:
        """Build a text progress bar from a 0-1 ratio."""
        ratio = max(0.0, min(ratio, 1.0))
        filled = int(width * ratio)
        empty = width - filled
        percent = int(ratio * 100)
        return "━" * filled + "░" * empty + f"  {percent:3d}%"

    def _build_mini_bar(self, count: int, max_count: int, width: int = 10) -> str:
        """Build a mini bar for tool category stats."""
        if max_count <= 0:
            return "░" * width
        ratio = min(count / max_count, 1.0)
        filled = int(width * ratio)
        return "█" * filled + "░" * (width - filled)

    def _build_timer(self) -> Text:
        """Build timer + progress section."""
        elapsed = self.state.get_elapsed_seconds()
        max_dur = self.state.max_duration
        turns = self.state.turn_count

        time_ratio = elapsed / max_dur if max_dur > 0 else 0
        turn_ratio = turns / self.state.max_turns if self.state.max_turns > 0 else 0
        progress = max(time_ratio, turn_ratio)

        t = Text()
        t.append("  Time: ", style="dim")
        t.append(self._format_time(elapsed), style="bold cyan")
        t.append(f" / {self._format_time(max_dur)}", style="dim")
        t.append("    Turns: ", style="dim")
        t.append(str(turns), style="bold cyan")
        t.append(f" / {self.state.max_turns}", style="dim")
        t.append("\n")
        t.append("  " + self._build_progress_bar(progress), style="cyan")
        return t

    def _build_tool_summary(self) -> Text:
        """Build tool count summary + category breakdown."""
        total = len(self.state.completed_tools) + (1 if self.state.active_tool else 0)
        success = self.state.get_success_count()
        errors = self.state.tool_errors

        t = Text()
        t.append("  Tools: ", style="dim")
        t.append(str(total), style="bold")
        t.append(" total   ", style="dim")
        t.append("✓ ", style="green")
        t.append(str(success), style="bold green")
        t.append("   ", style="dim")
        t.append("✗ ", style="red")
        t.append(str(errors), style="bold red")

        stats = self.state.get_tool_stats()
        if stats:
            max_count = max(stats.values())
            sorted_stats = sorted(stats.items(), key=lambda x: -x[1])
            for i, (prefix, count) in enumerate(sorted_stats[:5]):
                connector = "└──" if i == len(sorted_stats[:5]) - 1 else "├──"
                t.append(f"\n  {connector} ", style="dim")
                t.append(f"{prefix:<15}", style="cyan")
                t.append(f"{count:>3}  ", style="dim")
                t.append(self._build_mini_bar(count, max_count), style="green")

        return t

    def _build_active_tool(self) -> RenderableType | None:
        """Build spinner for active tool."""
        if not self.state.active_tool:
            return None

        elapsed = self.state.get_tool_elapsed_seconds()
        return Spinner(
            "dots",
            text=Text.assemble(
                ("  Running: ", "dim"),
                (self.state.active_tool.tool_name, "bold yellow"),
                (f"  ({elapsed:.1f}s)", "dim"),
            ),
        )

    @staticmethod
    def _extract_tool_info(result: Any) -> str:  # noqa: PLR0911
        """Extract a brief info string from a tool result."""
        if not isinstance(result, str) or not result:
            return ""
        import json

        try:
            d = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return ""

        if d.get("status") == "error":
            return f"[red]{d.get('error', '')[:40]}[/red]"

        for key, label in (("file_count", "files"), ("count", "results"), ("match_count", "matches")):
            if key in d:
                return f"[dim]{d[key]} {label}[/dim]"

        size = d.get("file_size_bytes", 0)
        if size > _MB:
            return f"[dim]{size / _MB:.1f} MB[/dim]"
        if size > _KB:
            return f"[dim]{size / _KB:.0f} KB[/dim]"
        return ""

    def _build_recent_tools(self) -> RenderableType | None:
        """Build recent tool history table."""
        recent = self.state.get_recent_tools(_RECENT_TOOLS_SHOWN)
        if not recent:
            return None

        table = Table(
            show_header=False,
            show_edge=False,
            padding=(0, 1),
            expand=True,
            box=None,
        )
        table.add_column("status", width=3)
        table.add_column("name", ratio=3)
        table.add_column("info", ratio=2, justify="right")

        for tool in reversed(recent):
            is_error = tool.status == "error" or (isinstance(tool.result, str) and '"status": "error"' in tool.result)
            icon = "[red]✗[/red]" if is_error else "[green]✓[/green]"
            info = self._extract_tool_info(tool.result)
            table.add_row(icon, f"[bold]{tool.tool_name}[/bold]", info)

        return table

    def _build_phase_indicator(self) -> RenderableType | None:
        """Build phase progress indicator using the injected phase count."""
        if not self.state.phases:
            return None

        t = Text()
        current = self.state.phases[-1]
        t.append("  Phase: ", style="dim")
        t.append(f"[{current.phase}/{self.state.phase_spec.count}] ", style="bold cyan")
        t.append(current.name, style="bold")
        t.append(f"  ({current.description})", style="dim")
        return t

    def _build_discovery_feed(self) -> RenderableType | None:
        """Build discovery event feed (last 3 items)."""
        recent = self.state.discoveries[-3:] if self.state.discoveries else []
        if not recent:
            return None

        t = Text()
        for event in reversed(recent):
            t.append("  ◆ ", style="cyan")
            t.append(event.summary, style="bold")
            t.append("\n")
        return t

    def _build_team_table(self) -> RenderableType | None:
        """Build a table showing dispatched team status."""
        if not self.state.teams:
            return None

        table = Table(
            show_header=True,
            show_edge=False,
            padding=(0, 1),
            expand=True,
            box=None,
        )
        table.add_column("Team", ratio=2)
        table.add_column("Mandate", ratio=4)
        table.add_column("Agents", width=7, justify="right")
        table.add_column("Status", width=10, justify="center")
        table.add_column("Time", width=8, justify="right")

        for team in self.state.teams:
            if team.status == "running":
                status_text = "[yellow]running[/yellow]"
            elif team.status == "done":
                status_text = "[green]done[/green]"
            else:
                status_text = "[red]error[/red]"

            if team.status == "running" and team.started_at is not None:
                elapsed = time.monotonic() - team.started_at
            else:
                elapsed = team.duration_seconds
            time_str = self._format_time(elapsed)

            mandate = team.mandate[:50] + "..." if len(team.mandate) > _MANDATE_TRUNCATE_LEN else team.mandate

            table.add_row(
                f"[bold]{team.team_id}[/bold]",
                f"[dim]{mandate}[/dim]",
                str(team.agent_count),
                status_text,
                f"[dim]{time_str}[/dim]",
            )

        return table

    def _panel_title(self) -> str:
        """Compose the panel title with an optional mode badge."""
        if self._mode_label:
            return f"{self._title} [{self._mode_label}]"
        return self._title

    def _build_coordinator_display(self) -> RenderableType:
        """Build the coordinator pipeline display with team tracking."""
        elements: list[RenderableType] = []

        elements.append(self._build_timer())
        elements.append(Text(""))

        phase = self._build_phase_indicator()
        if phase:
            elements.append(phase)
            elements.append(Text(""))

        team_table = self._build_team_table()
        if team_table:
            elements.append(Text("  Teams:", style="dim"))
            elements.append(team_table)
            elements.append(Text(""))

        elements.append(self._build_tool_summary())

        discoveries = self._build_discovery_feed()
        if discoveries:
            elements.append(Text(""))
            elements.append(Text("  Discoveries:", style="dim"))
            elements.append(discoveries)

        active = self._build_active_tool()
        if active:
            elements.append(Text(""))
            elements.append(active)

        recent = self._build_recent_tools()
        if recent:
            elements.append(Text(""))
            elements.append(Text("  Recent:", style="dim"))
            elements.append(recent)

        if self.state.error:
            elements.append(Text(""))
            elements.append(
                Panel(
                    Text(self.state.error, style="bold red"),
                    title="Error",
                    border_style="red",
                ),
            )

        if self.state.completed:
            elements.append(Text(""))
            elements.append(Text("  ✓ Analysis complete", style="bold green"))

        return Panel(
            Group(*elements) if elements else Text("Starting analysis...", style="dim"),
            title=self._panel_title(),
            border_style="cyan",
            padding=(1, 2),
        )

    def _build_single_agent_display(self) -> RenderableType:
        """Build the fixed-height single-agent dashboard display."""
        elements: list[RenderableType] = []

        elements.append(self._build_timer())
        elements.append(Text(""))

        phase = self._build_phase_indicator()
        if phase:
            elements.append(phase)
            elements.append(Text(""))

        elements.append(self._build_tool_summary())

        discoveries = self._build_discovery_feed()
        if discoveries:
            elements.append(Text(""))
            elements.append(Text("  Discoveries:", style="dim"))
            elements.append(discoveries)

        active = self._build_active_tool()
        if active:
            elements.append(Text(""))
            elements.append(active)

        recent = self._build_recent_tools()
        if recent:
            elements.append(Text(""))
            elements.append(Text("  Recent:", style="dim"))
            elements.append(recent)

        if self.state.error:
            elements.append(Text(""))
            elements.append(
                Panel(
                    Text(self.state.error, style="bold red"),
                    title="Error",
                    border_style="red",
                ),
            )

        if self.state.completed:
            elements.append(Text(""))
            elements.append(Text("  ✓ Analysis complete", style="bold green"))

        return Panel(
            Group(*elements) if elements else Text("Starting analysis...", style="dim"),
            title=self._panel_title(),
            border_style="cyan",
            padding=(1, 2),
        )

    def _build_display(self) -> RenderableType:
        """Build the dashboard display.

        Uses the coordinator display when teams have been dispatched, otherwise
        falls back to the single-agent display.
        """
        if self.state.teams:
            return self._build_coordinator_display()
        return self._build_single_agent_display()

    async def start(self) -> None:
        """Start the dashboard display."""
        self.state.reset()
        self._live = Live(
            self._build_display(),
            console=self.console,
            refresh_per_second=2,
            transient=True,
            vertical_overflow="ellipsis",
        )
        bind_live_renderable(self._live, self._build_display)
        self._live.start()

    async def stop(self) -> None:
        """Stop the dashboard and print final summary."""
        if self._live:
            self._live.stop()
            self._live = None

        elapsed = self.state.get_elapsed_seconds()
        total = len(self.state.completed_tools)
        errors = self.state.tool_errors
        self.console.print(
            f"[dim]  {total} tools in {self._format_time(elapsed)}  ✓ {total - errors}  ✗ {errors}[/dim]",
        )

    def _refresh(self) -> None:
        """No-op. Rich's auto-refresh handles updates via get_renderable."""

    # ── Event Handlers ──────────────────────────────────────────

    async def on_run_started(self, thread_id: str, run_id: str) -> None:
        """Handle run started."""
        self.state.thread_id = thread_id
        self.state.run_id = run_id
        self.state.start_time = time.monotonic()

    async def on_text_start(self, message_id: str, role: str) -> None:
        """Handle text start — no display action."""
        self.state.active_message_id = message_id

    async def on_text_content(self, message_id: str, delta: str) -> None:
        """Handle text delta — silently accumulate (not displayed)."""
        self.state.text_buffer += delta

    async def on_text_end(self, message_id: str) -> None:
        """Handle text end — increment turn counter."""
        self.state.active_message_id = None
        self.state.turn_count += 1
        self.state.text_buffer = ""

    async def on_tool_start(self, tool_call_id: str, tool_name: str) -> None:
        """Handle tool start — show in active spinner."""
        self.state.active_tool = ToolCallState(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        self.state.tool_start_time = time.monotonic()
        self._detect_phase(tool_name)

    async def on_tool_args(self, tool_call_id: str, args_delta: str) -> None:
        """Handle tool args — silently accumulate."""
        if self.state.active_tool and self.state.active_tool.tool_call_id == tool_call_id:
            self.state.active_tool.args_buffer += args_delta

    async def on_tool_result(self, tool_call_id: str, result: Any) -> None:
        """Handle tool result — check for errors and extract discoveries."""
        if self.state.active_tool and self.state.active_tool.tool_call_id == tool_call_id:
            self.state.active_tool.result = result
            if (isinstance(result, str) and "error" in result.lower()) or (
                isinstance(result, dict) and result.get("error")
            ):
                self.state.tool_errors += 1
            discovery = self._extract_discovery(self.state.active_tool.tool_name, result)
            if discovery:
                self.state.add_discovery(discovery)

    async def on_tool_end(self, tool_call_id: str) -> None:
        """Handle tool end — move to completed list."""
        if self.state.active_tool and self.state.active_tool.tool_call_id == tool_call_id:
            self.state.complete_active_tool()

    async def on_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Handle state snapshot."""
        self.state.state_snapshot = snapshot
        if "phase" in snapshot:
            self.state.current_phase = str(snapshot["phase"])

    async def on_run_finished(self, thread_id: str, run_id: str) -> None:
        """Handle run finished."""
        self.state.completed = True

    async def on_error(self, message: str, code: str | None = None) -> None:
        """Handle error."""
        error_text = f"[{code}] {message}" if code else message
        self.state.error = error_text


class QuietConsumer(EventConsumer):
    """Silent consumer that only writes errors to stderr."""

    def __init__(self) -> None:
        """Initialize quiet consumer."""
        self._stderr = Console(stderr=True, no_color=True, highlight=False)

    async def on_run_started(self, thread_id: str, run_id: str) -> None:
        """No output."""

    async def on_text_start(self, message_id: str, role: str) -> None:
        """No output."""

    async def on_text_content(self, message_id: str, delta: str) -> None:
        """No output."""

    async def on_text_end(self, message_id: str) -> None:
        """No output."""

    async def on_tool_start(self, tool_call_id: str, tool_name: str) -> None:
        """No output."""

    async def on_tool_args(self, tool_call_id: str, args_delta: str) -> None:
        """No output."""

    async def on_tool_result(self, tool_call_id: str, result: Any) -> None:
        """No output."""

    async def on_tool_end(self, tool_call_id: str) -> None:
        """No output."""

    async def on_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        """No output."""

    async def on_run_finished(self, thread_id: str, run_id: str) -> None:
        """No output."""

    async def on_error(self, message: str, code: str | None = None) -> None:
        """Print error to stderr."""
        error_text = f"[{code}] {message}" if code else message
        self._stderr.print(f"Error: {error_text}")
