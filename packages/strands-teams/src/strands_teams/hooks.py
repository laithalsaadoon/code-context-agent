"""Generic HookProviders for agent guidance, quality control, and display.

Ported from the pre-split ``code_context_agent.agent.hooks`` with every domain
literal replaced by :class:`~strands_teams.usecase.UseCase`-supplied data. The
eight providers here know nothing about any specific domain: reasoning prompts,
shell alternatives, fail-fast exemptions, artifact paths, and the scoring rubric
all arrive through constructor parameters (fed by :func:`create_all_hooks` from a
``UseCase``), so the framework never hardcodes a tool name or artifact scheme.

Uses the stable ``strands.hooks`` API (verified against strands-agents 1.47.0):

* ``BeforeInvocationEvent`` / ``AfterInvocationEvent`` — invocation lifecycle.
  ``AfterInvocationEvent.resume`` is the single writable field
  (``_can_write`` returns ``True`` only for ``"resume"``); setting it to a
  non-``None`` agent input triggers a full re-invocation cycle. This mechanism is
  unchanged from the port source, so :class:`ArtifactQualityHook` uses it directly
  (AC-3-3, AC-3-6).
* ``BeforeToolCallEvent`` / ``AfterToolCallEvent`` — per-tool hooks.

Display coupling (T-C3 anti-goal)
---------------------------------
The concrete display state lives in the consumer/display layer (a separate task).
To avoid a hard dependency in the wrong direction, :class:`ToolDisplayHook` and
:class:`TeamDispatchHook` accept any object satisfying the duck-typed
:class:`TeamDisplayState` ``Protocol`` (``start_team`` / ``complete_team`` /
``record_tool``). The framework never imports the concrete state class.
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from loguru import logger
from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .layout import TeamsLayout
    from .rubric import NarrativeRubric

_MAX_OUTPUT_SIZE = 100_000

# Core team-dispatch tool name. This is a framework-level tool (auto-provided by
# strands_teams), not a domain literal, so it is fixed here per task §4.
_DISPATCH_TEAM_TOOL = "dispatch_team"

# Default enrichment/resume prompt for ArtifactQualityHook. Uses the generic
# score/enrich/write coordinator-tool names; a use case may override via the
# ``resume_template`` param. Placeholders: {pass_num}, {max_passes}, {avg},
# {threshold}, {weak_list}.
DEFAULT_RESUME_TEMPLATE = (
    "[ENRICHMENT PASS {pass_num}/{max_passes}] "
    "Quality check: avg score {avg:.1f} (threshold {threshold:.1f}). "
    "Weak artifacts: {weak_list}. "
    "For each weak artifact: "
    "1) Call score_narrative(area) to get detailed dimension scores. "
    "2) Call enrich_artifact(area, feedback) with the suggestions. "
    "3) Rewrite the artifact via write_artifact with richer content. "
    "4) Call score_narrative again to verify improvement. "
    "Focus on adding file:line references, cross-cutting insights, and surprising findings."
)


def _is_error_result(result: Any) -> bool:
    """Return True if a tool result string indicates an error status."""
    if not result:
        return False
    result_str = str(result)
    return '"status": "error"' in result_str or '"status":"error"' in result_str


@runtime_checkable
class TeamDisplayState(Protocol):
    """Duck-typed contract the display hooks require of a display-state object.

    The concrete state class lives in the consumer/display layer (a separate
    task). Depending on this ``Protocol`` rather than the concrete class keeps
    the framework free of a dependency on its own consumer.
    """

    def start_team(self, team_id: str, mandate: str, agent_count: int) -> None:
        """Record that a team was dispatched."""
        ...

    def complete_team(self, team_id: str, *, status: str) -> None:
        """Record that a dispatched team finished."""
        ...

    def record_tool(self, *, tool_id: str, tool_name: str, status: str, result: str) -> None:
        """Record a completed tool call for display."""
        ...


class FullModeToolError(RuntimeError):
    """Raised by :class:`FailFastHook` when a critical tool returns an error."""

    def __init__(self, tool_name: str, error_message: str) -> None:  # noqa: D107
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' failed: {error_message}")


class OutputQualityHook(HookProvider):
    """Logs a warning when a tool produces unusually large output."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the output-quality callback."""
        registry.add_callback(AfterToolCallEvent, self._check_output_quality)

    def _check_output_quality(self, event: AfterToolCallEvent, **kwargs: Any) -> None:
        """Warn when a tool result exceeds the size ceiling."""
        tool_name = event.tool_use.get("name", "")
        result_str = str(event.result) if event.result else ""
        if len(result_str) > _MAX_OUTPUT_SIZE:
            logger.warning(f"Tool {tool_name} produced oversized output: {len(result_str)} chars")


class ConversationCompactionHook(HookProvider):
    """Strips large toolUse/toolResult payloads from older conversation history.

    After the model has reasoned about tool results, the raw payloads are no
    longer needed. Before each model invocation this hook replaces large tool
    payloads in older messages with stubs, keeping all reasoning turns and the
    most recent messages intact — preventing context overflow without ever
    truncating data the model has not yet seen.
    """

    COMPACTION_THRESHOLD = 2000  # chars — compact payloads larger than this
    KEEP_RECENT_MESSAGES = 4  # keep last N messages uncompacted (current turn)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the conversation-compaction callback."""
        registry.add_callback(BeforeInvocationEvent, self._compact_history)

    def _compact_history(self, event: Any, **kwargs: Any) -> None:
        """Strip large tool payloads from older messages before model invocation."""
        messages = getattr(event, "messages", None)
        if not messages:
            return

        boundary = max(0, len(messages) - self.KEEP_RECENT_MESSAGES)
        compacted = 0
        for i in range(boundary):
            content = messages[i].get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                compacted += self._compact_tool_result(block)
                compacted += self._compact_tool_use(block)

        if compacted:
            logger.info(f"Compacted {compacted} large tool payloads from conversation history")

    def _compact_tool_result(self, block: dict[str, Any]) -> int:
        """Replace large ``toolResult`` content with a stub. Returns 1 if compacted."""
        if "toolResult" not in block:
            return 0
        tool_result = block["toolResult"]
        result_content = tool_result.get("content", [])
        total = sum(len(rc.get("text", "")) for rc in result_content if isinstance(rc, dict))
        if total <= self.COMPACTION_THRESHOLD:
            return 0
        stub = f"[Tool output consumed ({total} chars) — see reasoning above]"
        tool_result["content"] = [{"text": stub}]
        logger.debug(f"Compacted toolResult {tool_result.get('toolUseId', '?')}: {total} chars")
        return 1

    def _compact_tool_use(self, block: dict[str, Any]) -> int:
        """Replace large ``toolUse`` input with a stub. Returns 1 if compacted."""
        if "toolUse" not in block:
            return 0
        tool_use = block["toolUse"]
        input_str = str(tool_use.get("input", ""))
        if len(input_str) <= self.COMPACTION_THRESHOLD:
            return 0
        tool_use["input"] = {"_compacted": True, "tool": tool_use.get("name", "?")}
        logger.debug(f"Compacted toolUse input for {tool_use.get('name', '?')}: {len(input_str)} chars")
        return 1


class ToolEfficiencyHook(HookProvider):
    """Warns when ``shell`` is used for a task that has a dedicated tool.

    The mapping of shell-command substring -> suggested alternative is supplied
    by the use case (``alternatives``), so the framework holds no domain literals.
    """

    def __init__(self, alternatives: Mapping[str, str]) -> None:  # noqa: D107
        self._alternatives = alternatives

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the tool-efficiency callback."""
        registry.add_callback(BeforeToolCallEvent, self._check_tool_efficiency)

    def _check_tool_efficiency(self, event: BeforeToolCallEvent, **kwargs: Any) -> None:
        """Suggest a dedicated tool when a shell command matches a known pattern."""
        if not self._alternatives:
            return
        tool_name = event.tool_use.get("name", "")
        if tool_name != "shell":
            return
        args = event.tool_use.get("input", {})
        command = args.get("command", "") if isinstance(args, dict) else ""
        if not isinstance(command, str):
            return
        for pattern, alternative in self._alternatives.items():
            if pattern in command:
                logger.info(f"Shell command '{command[:50]}' could use {alternative} instead")
                break


class ReasoningCheckpointHook(HookProvider):
    """Appends a reasoning-checkpoint prompt to results of mapped analysis tools.

    Only tools present in the ``prompts`` map (supplied by the use case) get a
    checkpoint appended; every other tool's result passes through unchanged
    (AC-3-2). Forces the model to interpret combined signals rather than merely
    collecting tool outputs.
    """

    def __init__(self, prompts: Mapping[str, str]) -> None:  # noqa: D107
        self._prompts = prompts

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the reasoning-checkpoint callback."""
        registry.add_callback(AfterToolCallEvent, self._inject_reasoning_prompt)

    def _inject_reasoning_prompt(self, event: AfterToolCallEvent, **kwargs: Any) -> None:
        """Append the mapped reasoning prompt to a successful tool result."""
        tool_name = event.tool_use.get("name", "")
        prompt = self._prompts.get(tool_name)
        if not prompt:
            return

        result = event.result
        if not result or result.get("status") == "error":
            return

        content = result.get("content", [])
        if not content:
            return

        content.append({"text": f"\n\n{prompt}"})
        logger.debug(f"Reasoning checkpoint injected for {tool_name}")


class FailFastHook(HookProvider):
    """Raises :class:`FullModeToolError` when a non-exempt tool errors (full mode).

    In full mode, most tool failures should halt the run rather than silently
    degrade. The exempt tool-name set and exempt name-prefixes both come from the
    use case (search tools may return no results; external MCP tools may be
    unavailable), so the framework holds no domain literals (AC-3-4).
    """

    def __init__(self, exempt: frozenset[str], exempt_prefixes: tuple[str, ...] = ()) -> None:  # noqa: D107
        self._exempt = exempt
        self._exempt_prefixes = exempt_prefixes

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the fail-fast callback."""
        registry.add_callback(AfterToolCallEvent, self._check_for_error)

    def _check_for_error(self, event: AfterToolCallEvent, **kwargs: Any) -> None:
        """Raise if a non-exempt tool returned an ``error`` status."""
        tool_name = event.tool_use.get("name", "")

        if tool_name in self._exempt or (self._exempt_prefixes and tool_name.startswith(self._exempt_prefixes)):
            return

        result_str = str(event.result) if event.result else ""
        if not result_str:
            return

        try:
            data = _json.loads(result_str)
        except (_json.JSONDecodeError, TypeError):
            return

        if isinstance(data, dict) and data.get("status") == "error":
            error_msg = data.get("error", data.get("message", "unknown error"))
            raise FullModeToolError(tool_name, str(error_msg))


class ArtifactQualityHook(HookProvider):
    """Triggers enrichment passes when artifact quality is below the rubric threshold.

    After the coordinator finishes an invocation, scores every artifact under
    ``layout.artifacts_dir()`` (matched by ``layout.artifact_glob()``) with the
    shared ``rubric.score`` (AC-3-3). If any artifact needs revision, sets
    ``event.resume`` (verified writable on ``AfterInvocationEvent`` in strands
    1.47.0) to re-invoke the coordinator with enrichment instructions. Capped at
    ``max_passes`` to avoid infinite loops; once exhausted no further resume is
    emitted (AC-3-6).
    """

    def __init__(  # noqa: D107
        self,
        layout: TeamsLayout,
        rubric: NarrativeRubric,
        max_passes: int = 2,
        resume_template: str = DEFAULT_RESUME_TEMPLATE,
    ) -> None:
        self._layout = layout
        self._rubric = rubric
        self._max_passes = max_passes
        self._resume_template = resume_template
        self._pass_count = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the artifact-quality callback."""
        registry.add_callback(AfterInvocationEvent, self._check_artifact_quality)

    def _check_artifact_quality(self, event: Any, **kwargs: Any) -> None:
        """Score artifacts after an invocation and resume the agent if any are weak."""
        if self._pass_count >= self._max_passes:
            return  # Budget exhausted (AC-3-6)

        artifacts_dir = self._layout.artifacts_dir()
        if not artifacts_dir.exists():
            return

        artifact_files = list(artifacts_dir.glob(self._layout.artifact_glob()))
        if not artifact_files:
            return

        totals: list[float] = []
        weak: list[tuple[str, float]] = []
        for path in artifact_files:
            report = self._rubric.score(path.read_text())
            totals.append(report.total)
            if report.needs_revision:
                weak.append((self._layout.area_from_artifact(path), report.total))

        avg = sum(totals) / len(totals) if totals else 0.0

        if not weak:
            logger.info(f"Artifact quality check passed: avg={avg:.1f} (threshold {self._rubric.revision_threshold})")
            return

        self._pass_count += 1
        weak_list = ", ".join(f"{area} ({score:.1f})" for area, score in weak)
        event.resume = self._resume_template.format(
            pass_num=self._pass_count,
            max_passes=self._max_passes,
            avg=avg,
            threshold=self._rubric.revision_threshold,
            weak_list=weak_list,
        )
        logger.info(f"Artifact enrichment pass {self._pass_count} triggered: avg={avg:.1f}, weak={len(weak)} artifacts")


class ToolDisplayHook(HookProvider):
    """Tracks tool calls on a display state for TUI rendering.

    Depends on the duck-typed :class:`TeamDisplayState` ``Protocol`` rather than a
    concrete state class so the framework does not import its own consumer.
    """

    def __init__(self, state: TeamDisplayState) -> None:  # noqa: D107
        self._state = state

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the tool-display callbacks."""
        registry.add_callback(BeforeToolCallEvent, self._on_tool_start)
        registry.add_callback(AfterToolCallEvent, self._on_tool_end)

    def _on_tool_start(self, event: BeforeToolCallEvent, **kwargs: Any) -> None:
        """Record a tool call beginning."""
        tool_name = event.tool_use.get("name", "")
        tool_id = event.tool_use.get("toolUseId", "")
        if tool_name:
            self._state.record_tool(tool_id=tool_id, tool_name=tool_name, status="running", result="")

    def _on_tool_end(self, event: AfterToolCallEvent, **kwargs: Any) -> None:
        """Record a tool call completing."""
        tool_name = event.tool_use.get("name", "")
        if not tool_name:
            return
        tool_id = event.tool_use.get("toolUseId", "")
        is_error = _is_error_result(event.result)
        result = str(event.result)[:200] if event.result else ""
        self._state.record_tool(
            tool_id=tool_id,
            tool_name=tool_name,
            status="error" if is_error else "completed",
            result=result,
        )


class TeamDispatchHook(HookProvider):
    """Tracks ``dispatch_team`` tool calls on a display state.

    ``dispatch_team`` is a framework-level core tool (not a domain literal), so
    the name is fixed. Depends on the duck-typed :class:`TeamDisplayState`.
    """

    def __init__(self, state: TeamDisplayState) -> None:  # noqa: D107
        self._state = state

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the team-dispatch callbacks."""
        registry.add_callback(BeforeToolCallEvent, self._on_dispatch_start)
        registry.add_callback(AfterToolCallEvent, self._on_dispatch_end)

    def _on_dispatch_start(self, event: BeforeToolCallEvent, **kwargs: Any) -> None:
        """Record a team dispatch beginning."""
        if event.tool_use.get("name", "") != _DISPATCH_TEAM_TOOL:
            return
        args = event.tool_use.get("input", {})
        if not isinstance(args, dict):
            return
        team_id = args.get("team_id", "unknown")
        mandate = args.get("mandate", "")
        agents = args.get("agents", [])
        agent_count = len(agents) if isinstance(agents, list) else 0
        self._state.start_team(team_id, mandate, agent_count)
        logger.debug(f"Team dispatched: {team_id} ({agent_count} agents)")

    def _on_dispatch_end(self, event: AfterToolCallEvent, **kwargs: Any) -> None:
        """Record a team dispatch completing."""
        if event.tool_use.get("name", "") != _DISPATCH_TEAM_TOOL:
            return
        args = event.tool_use.get("input", {})
        if not isinstance(args, dict):
            return
        team_id = args.get("team_id", "unknown")
        status = "error" if _is_error_result(event.result) else "done"
        self._state.complete_team(team_id, status=status)
        logger.debug(f"Team completed: {team_id} (status={status})")


class JsonLogHook(HookProvider):
    """Emits one structured JSON log line per tool start/end for ``--quiet`` mode."""

    def __init__(self) -> None:  # noqa: D107
        self._json_logger = logger.bind(output="json")

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the JSON-log callbacks."""
        registry.add_callback(BeforeToolCallEvent, self._on_tool_start)
        registry.add_callback(AfterToolCallEvent, self._on_tool_end)

    def _on_tool_start(self, event: BeforeToolCallEvent, **kwargs: Any) -> None:
        """Emit a JSON log line when a tool call begins."""
        tool_name = event.tool_use.get("name", "")
        if tool_name:
            self._json_logger.info("tool_start", tool=tool_name)

    def _on_tool_end(self, event: AfterToolCallEvent, **kwargs: Any) -> None:
        """Emit a JSON log line when a tool call completes."""
        tool_name = event.tool_use.get("name", "")
        if tool_name:
            self._json_logger.info(
                "tool_end",
                tool=tool_name,
                status="error" if _is_error_result(event.result) else "ok",
            )


def create_all_hooks(
    use_case: Any,
    *,
    full_mode: bool = False,
    state: TeamDisplayState | None = None,
    quiet: bool = False,
) -> list[HookProvider]:
    """Assemble the hook providers for a run from a ``UseCase`` (AC-3-5).

    Always includes: :class:`ConversationCompactionHook`, :class:`OutputQualityHook`,
    :class:`ToolEfficiencyHook`, :class:`ReasoningCheckpointHook`, and
    :class:`ArtifactQualityHook`. Adds :class:`FailFastHook` iff ``full_mode``.
    For display: :class:`JsonLogHook` iff ``quiet``; otherwise
    :class:`ToolDisplayHook` + :class:`TeamDispatchHook` iff ``state`` is provided.

    The reasoning prompts, fail-fast exempt set, artifact layout, and rubric come
    from the use-case contract members (``reasoning_prompts``, ``fail_fast_exempt``,
    ``layout``, ``rubric``). Two knobs are NOT part of the ``UseCase`` contract and
    are read optionally via ``getattr`` so a use case may supply them without the
    framework requiring them: ``shell_alternatives`` (mapping, default empty) and
    ``fail_fast_exempt_prefixes`` (tuple, default empty).

    Args:
        use_case: The use case supplying reasoning prompts, exemptions, layout,
            and rubric.
        full_mode: If True, include :class:`FailFastHook`.
        state: A display state (duck-typed :class:`TeamDisplayState`). Ignored
            when ``quiet`` is True.
        quiet: If True, use :class:`JsonLogHook` instead of the display hooks.

    Returns:
        A flat list of :class:`HookProvider` instances to register on the agent.
    """
    shell_alternatives: Mapping[str, str] = getattr(use_case, "shell_alternatives", {})
    exempt_prefixes: tuple[str, ...] = tuple(getattr(use_case, "fail_fast_exempt_prefixes", ()))

    hooks: list[HookProvider] = [
        ConversationCompactionHook(),
        OutputQualityHook(),
        ToolEfficiencyHook(shell_alternatives),
        ReasoningCheckpointHook(use_case.reasoning_prompts),
        ArtifactQualityHook(use_case.layout, use_case.rubric),
    ]
    if full_mode:
        hooks.append(FailFastHook(use_case.fail_fast_exempt, exempt_prefixes))

    if quiet:
        hooks.append(JsonLogHook())
    elif state is not None:
        hooks.append(ToolDisplayHook(state))
        hooks.append(TeamDispatchHook(state))

    return hooks
