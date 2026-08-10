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
import re
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

# Word tokenizer for acceptance-criteria coverage. A criterion is "covered" when a
# majority of its significant tokens (len >= 4) appear in the produced artifacts +
# findings corpus. Kept deliberately simple and deterministic (no LLM) so the
# coverage gate is testable and cheap.
_WORD_RE = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_LEN = 4
_COVERAGE_THRESHOLD = 0.5

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

# Resume prompt used when the run's acceptance criteria are not yet covered by the
# produced artifacts and findings. Placeholders: {pass_num}, {max_passes},
# {uncovered_list}. A use case may override via ``coverage_resume_template``.
DEFAULT_COVERAGE_RESUME_TEMPLATE = (
    "[COVERAGE PASS {pass_num}/{max_passes}] "
    "The following acceptance criteria are NOT yet covered by any artifact or "
    "finding: {uncovered_list}. "
    "For each uncovered criterion: investigate it (dispatch a team or read findings "
    "as needed), then write or enrich the relevant artifact via write_artifact so the "
    "criterion is explicitly addressed with file:line evidence."
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
    """Triggers revision passes on weak rubric scores OR uncovered acceptance criteria.

    After the coordinator finishes an invocation this hook applies two gates
    (AC-4-1):

    1. **Rubric.** Scores every artifact under ``layout.artifacts_dir()`` (matched
       by ``layout.artifact_glob()``) with the shared ``rubric.score``. An artifact
       below the rubric's revision threshold is "weak".
    2. **Acceptance-criteria coverage.** Reads every dispatched team's
       ``acceptance_criteria`` from its ``contract.json`` (written by
       ``dispatch_team``) and checks each criterion against the corpus of produced
       artifacts + team findings. A criterion whose significant tokens are mostly
       absent from that corpus is "uncovered".

    If any artifact is weak OR any criterion is uncovered, the run needs revision
    and ``event.resume`` (verified writable on ``AfterInvocationEvent`` in strands
    1.47.0) is set to re-invoke the coordinator. An uncovered criterion forces a
    revision **regardless of the rubric total** (AC-4-2) and the emitted resume
    prompt names the uncovered criteria (AC-4-3). When all criteria are covered and
    no artifact is weak, no resume is emitted (AC-4-4). When no acceptance criteria
    were supplied for the run, the hook falls back to unchanged rubric-only behavior
    (AC-4-5). The single ``max_passes`` budget caps both the rubric- and
    coverage-driven resumes combined (AC-4-6).
    """

    def __init__(  # noqa: D107
        self,
        layout: TeamsLayout,
        rubric: NarrativeRubric,
        max_passes: int = 2,
        resume_template: str = DEFAULT_RESUME_TEMPLATE,
        coverage_resume_template: str = DEFAULT_COVERAGE_RESUME_TEMPLATE,
    ) -> None:
        self._layout = layout
        self._rubric = rubric
        self._max_passes = max_passes
        self._resume_template = resume_template
        self._coverage_resume_template = coverage_resume_template
        self._pass_count = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the artifact-quality callback."""
        registry.add_callback(AfterInvocationEvent, self._check_artifact_quality)

    def _score_artifacts(self) -> tuple[list[tuple[str, float]], float, list[str]]:
        """Score every artifact; return ``(weak, avg, artifact_texts)``.

        ``weak`` lists ``(area, total)`` for artifacts below the revision
        threshold, ``avg`` is the mean total, and ``artifact_texts`` is every
        artifact's raw text (reused as part of the coverage corpus).
        """
        artifacts_dir = self._layout.artifacts_dir()
        if not artifacts_dir.exists():
            return [], 0.0, []

        totals: list[float] = []
        weak: list[tuple[str, float]] = []
        texts: list[str] = []
        for path in artifacts_dir.glob(self._layout.artifact_glob()):
            text = path.read_text()
            texts.append(text)
            report = self._rubric.score(text)
            totals.append(report.total)
            if report.needs_revision:
                weak.append((self._layout.area_from_artifact(path), report.total))

        avg = sum(totals) / len(totals) if totals else 0.0
        return weak, avg, texts

    def _acceptance_criteria(self) -> list[str]:
        """Collect every dispatched team's acceptance criteria (deduped, ordered).

        Reads each team's ``contract.json`` (written by ``dispatch_team``) from the
        run's teams directory. Never raises: a missing dir or malformed contract is
        skipped so a contract I/O problem cannot mask itself as "no criteria".
        """
        teams_dir = self._layout.teams_dir()
        if not teams_dir.exists():
            return []
        seen: set[str] = set()
        criteria: list[str] = []
        for contract_path in sorted(teams_dir.glob(self._layout.contract_glob())):
            try:
                data = _json.loads(contract_path.read_text())
            except (_json.JSONDecodeError, OSError):
                continue
            for c in data.get("acceptance_criteria", []) or []:
                if isinstance(c, str) and c not in seen:
                    seen.add(c)
                    criteria.append(c)
        return criteria

    def _findings_corpus(self) -> list[str]:
        """Gather every team's findings text (markdown + typed statements) for coverage."""
        teams_dir = self._layout.teams_dir()
        if not teams_dir.exists():
            return []
        texts: list[str] = []
        for team_dir in sorted(teams_dir.iterdir()):
            if not team_dir.is_dir():
                continue
            md = team_dir / self._layout.findings_name
            if md.exists():
                try:
                    texts.append(md.read_text())
                except OSError:
                    pass
            js = team_dir / self._layout.findings_json_name
            if js.exists():
                try:
                    texts.append(js.read_text())
                except OSError:
                    pass
        return texts

    @staticmethod
    def _is_covered(criterion: str, corpus_tokens: set[str]) -> bool:
        """Return True when a majority of a criterion's significant tokens appear in the corpus."""
        tokens = {t for t in _WORD_RE.findall(criterion.lower()) if len(t) >= _MIN_TOKEN_LEN}
        if not tokens:
            return True  # No significant tokens to check — treat as covered.
        matched = sum(1 for t in tokens if t in corpus_tokens)
        return (matched / len(tokens)) >= _COVERAGE_THRESHOLD

    def _uncovered_criteria(self, criteria: list[str], corpus: list[str]) -> list[str]:
        """Return the criteria whose tokens are mostly absent from the corpus."""
        corpus_tokens: set[str] = set()
        for text in corpus:
            corpus_tokens.update(_WORD_RE.findall(text.lower()))
        return [c for c in criteria if not self._is_covered(c, corpus_tokens)]

    def _check_artifact_quality(self, event: Any, **kwargs: Any) -> None:
        """Resume the coordinator when artifacts are weak or acceptance criteria are uncovered."""
        if self._pass_count >= self._max_passes:
            return  # Budget exhausted, both triggers combined (AC-4-6, AC-3-6)

        weak, avg, artifact_texts = self._score_artifacts()

        # AC-4-1/AC-4-5: coverage only applies when criteria were supplied; with none,
        # this stays [] and behavior is rubric-only exactly as before.
        criteria = self._acceptance_criteria()
        uncovered = self._uncovered_criteria(criteria, [*artifact_texts, *self._findings_corpus()]) if criteria else []

        if not weak and not uncovered:
            logger.info(
                f"Artifact quality check passed: avg={avg:.1f} "
                f"(threshold {self._rubric.revision_threshold}), "
                f"{len(criteria)} criteria all covered",
            )
            return

        self._pass_count += 1

        # An uncovered criterion forces revision regardless of rubric total, and its
        # resume names the uncovered criteria (AC-4-2, AC-4-3). Otherwise fall back to
        # the rubric enrichment prompt for weak artifacts.
        if uncovered:
            uncovered_list = "; ".join(uncovered)
            event.resume = self._coverage_resume_template.format(
                pass_num=self._pass_count,
                max_passes=self._max_passes,
                uncovered_list=uncovered_list,
            )
            logger.info(
                f"Coverage pass {self._pass_count} triggered: {len(uncovered)}/{len(criteria)} criteria uncovered",
            )
            return

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


class PolicyGateDenied(RuntimeError):
    """Raised by :class:`PolicyGateHook` to block a denied consequential tool call (AC-6-4)."""

    def __init__(self, tool_name: str, verdict: Any) -> None:  # noqa: D107
        self.tool_name = tool_name
        self.verdict = verdict
        gate = getattr(verdict, "failed_gate", None)
        reason = getattr(verdict, "reason", "")
        super().__init__(f"Policy gate '{gate}' blocked tool '{tool_name}': {reason}")


class PolicyGateHook(HookProvider):
    """Structurally blocks a denied consequential tool call before it runs (AC-6-4, §14.7).

    On ``BeforeToolCallEvent`` for a tool whose name is in ``consequential_tools``,
    the hook builds a :class:`~strands_teams.models.TransitionRequest` from the
    tool input and the current actor, calls
    :meth:`~strands_teams.policy.PolicyEngine.authorize_transition` (reading grants,
    approvals, findings, and revision hashes from the event's ``invocation_state``
    by reference — AC-3-7), and raises :class:`PolicyGateDenied` if the verdict is
    ``allowed=False`` — so the action never executes without a valid revision-bound
    approval and a clear blocking-finding slate (AC-6-2, AC-6-3). Non-consequential
    tools pass straight through; the engine is present but, on a run that requests
    no consequential transitions, is never invoked to deny (AC-7-5).

    The transition kind, target revision, and presented tokens are read from the
    tool input under conventional keys (``transition_kind``/``kind``,
    ``target_revision_id``, ``presented_tokens``, ``consequential``); a use case
    that names them differently supplies an ``input_mapper`` callable.
    """

    def __init__(
        self,
        engine: Any,
        consequential_tools: frozenset[str],
        *,
        actor_of: Any = None,
        input_mapper: Any = None,
    ) -> None:
        """Wire the gate to a policy engine and the set of consequential tools.

        Args:
            engine: The :class:`~strands_teams.policy.PolicyEngine` that issues verdicts.
            consequential_tools: Tool names whose calls are gated.
            actor_of: Optional callable mapping ``invocation_state`` to the actor id.
            input_mapper: Optional callable ``(tool_name, args, invocation_state)`` ->
                ``TransitionRequest`` for use cases that name inputs differently.
        """
        self._engine = engine
        self._consequential = consequential_tools
        self._actor_of = actor_of
        self._input_mapper = input_mapper

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register the pre-tool authorization callback."""
        registry.add_callback(BeforeToolCallEvent, self._authorize)

    def _authorize(self, event: BeforeToolCallEvent, **kwargs: Any) -> None:
        """Authorize a consequential tool call, raising to block it on a deny verdict."""
        tool_name = event.tool_use.get("name", "")
        if tool_name not in self._consequential:
            return

        from .models import TransitionRequest  # local import: core has no policy import at module load

        args = event.tool_use.get("input", {})
        if not isinstance(args, dict):
            args = {}
        invocation_state = getattr(event, "invocation_state", None) or {}

        if self._input_mapper is not None:
            transition = self._input_mapper(tool_name, args, invocation_state)
        else:
            actor = self._actor_of(invocation_state) if self._actor_of else str(args.get("actor", "coordinator"))
            transition = TransitionRequest(
                actor=actor,
                kind=str(args.get("transition_kind") or args.get("kind") or tool_name),
                target_revision_id=str(args.get("target_revision_id", "")),
                consequential=bool(args.get("consequential", True)),
                presented_tokens=list(args.get("presented_tokens", [])),
            )

        verdict = self._engine.authorize_transition(transition, invocation_state)
        if not verdict.allowed:
            logger.warning(f"PolicyGateHook blocked '{tool_name}': {verdict.failed_gate} — {verdict.reason}")
            raise PolicyGateDenied(tool_name, verdict)


def create_all_hooks(
    use_case: Any,
    *,
    full_mode: bool = False,
    state: TeamDisplayState | None = None,
    quiet: bool = False,
    policy_engine: Any = None,
    consequential_tools: frozenset[str] | None = None,
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
        policy_engine: Optional :class:`~strands_teams.policy.PolicyEngine`; when
            given together with ``consequential_tools``, a :class:`PolicyGateHook`
            is added to block denied consequential tool calls before they run.
        consequential_tools: Tool names gated by the policy engine (AC-6-4).

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

    if policy_engine is not None and consequential_tools:
        hooks.append(PolicyGateHook(policy_engine, consequential_tools))

    if quiet:
        hooks.append(JsonLogHook())
    elif state is not None:
        hooks.append(ToolDisplayHook(state))
        hooks.append(TeamDispatchHook(state))

    return hooks
