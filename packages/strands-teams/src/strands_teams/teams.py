"""Generic team-dispatch and artifact tools for the coordinator agent (Wave C).

These five ``@tool`` functions are the domain-agnostic port of the pre-split
code-context team-tools module. They carry ONLY generic
mechanics in their docstrings — all domain guidance (team sizing, tool subsets,
artifact sections) lives in the coordinator system prompt composed by
:mod:`strands_teams.coordinator`, never here, because tool docstrings cannot vary
per use case (packet §4).

Every tool reads its per-run state (layout, tool registry, node model config,
timeouts, rubric) from ``tool_context.invocation_state`` rather than module-level
globals (AC-8-4; research-strands.yaml §ToolContext). There is deliberately no
``configure()`` module setter and no mutable module state.

``dispatch_team`` runs a team as a native strands :class:`~strands.multiagent.Swarm`
of freshly constructed ``Agent`` nodes, each with its own explicit ``BedrockModel``
(AC-8-1, AC-8-2), and awaits ``swarm.invoke_async`` (AC-8-1). Repetitive-handoff
detection is set to window 8 / min-unique 3 to preserve ping-pong detection
(AC-8-3; research-strands.yaml caveat).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast

from loguru import logger
from pydantic import ValidationError as PydanticValidationError
from strands import Agent, ToolContext, tool
from strands.models import BedrockModel
from strands.models.bedrock import CacheConfig
from strands.multiagent import Swarm

from .evidence_gate import gate_findings
from .findings_io import findings_as_dicts, load_findings, serialize_findings
from .models import AuthorityGrant, Budget
from .write_file import make_write_file, make_write_findings

if TYPE_CHECKING:
    from pathlib import Path

    from .layout import TeamsLayout
    from .models import Finding
    from .rubric import NarrativeRubric


class TeamInvocationState(TypedDict):
    """Shared state propagated to every coordinator tool via ``invocation_state``.

    Built by the coordinator factory (:mod:`strands_teams.coordinator`) and passed
    to ``coordinator.invoke_async(prompt, invocation_state=...)`` by the runner,
    which strands forwards to each tool's ``tool_context.invocation_state``. This
    replaces the pre-split module-level ``_output_dir`` / ``_repo_path`` /
    ``_tool_registry`` globals (AC-8-4).

    Keys:
        layout: The run's :class:`~strands_teams.layout.TeamsLayout` (all paths).
        tool_registry: Map of tool name -> tool object, for resolving the string
            tool names in team-agent specs to real tool objects.
        node_model_config: Kwargs passed to ``BedrockModel(**config)`` when
            constructing each fresh team-agent node.
        execution_timeout: Default max seconds for an entire team swarm.
        node_timeout: Default max seconds per agent node within a team.
        use_case_name: The active use case's name (for logging/debug).
        rubric: The :class:`~strands_teams.rubric.NarrativeRubric` used by
            ``score_narrative``.
        authority: The active team's :class:`~strands_teams.models.AuthorityGrant`
            (what it may do). Present only inside a dispatched team's swarm
            ``invocation_state``; the coordinator's own state omits it. Treat it
            as a **read-only** shared reference — never mutate it from within a
            concurrently-executing node tool (AC-5-3; research-coding-runtimes.yaml
            strands_tool_pattern lines 20-22, 30).
        budget: The active team's :class:`~strands_teams.models.Budget` (how much
            it may spend). Same read-only, dispatched-team-only contract as
            ``authority``.
        acceptance_criteria: The acceptance criteria the active team must satisfy.
        input_revisions: Opaque revision-identifier strings the team should build
            on (resolved to Phase-4 ``ArtifactRevision`` objects in a later phase;
            treated as opaque strings here — AC-2-7).
    """

    layout: TeamsLayout
    tool_registry: dict[str, Any]
    node_model_config: dict[str, Any]
    execution_timeout: float
    node_timeout: float
    use_case_name: str
    rubric: NarrativeRubric
    authority: NotRequired[AuthorityGrant]
    budget: NotRequired[Budget]
    acceptance_criteria: NotRequired[list[str]]
    input_revisions: NotRequired[list[str]]
    goal_id: NotRequired[str]
    task_id: NotRequired[str]
    thread_id: NotRequired[str]


# ---------------------------------------------------------------------------
# invocation_state accessors
# ---------------------------------------------------------------------------


def _require_state(tool_context: ToolContext) -> TeamInvocationState:
    """Return the ``TeamInvocationState`` from a tool's invocation state.

    Raises:
        RuntimeError: If the coordinator was invoked without the required
            ``invocation_state`` keys (a wiring bug, not a runtime condition).
    """
    state: dict[str, Any] = tool_context.invocation_state or {}
    if "layout" not in state:
        msg = "invocation_state missing 'layout' — coordinator must be invoked with a TeamInvocationState"
        raise RuntimeError(msg)
    return cast("TeamInvocationState", state)


def _layout_of(tool_context: ToolContext) -> TeamsLayout:
    return _require_state(tool_context)["layout"]


# ---------------------------------------------------------------------------
# Swarm construction helpers
# ---------------------------------------------------------------------------


def _make_node_model(node_model_config: dict[str, Any]) -> BedrockModel:
    """Construct a fresh ``BedrockModel`` for one team-agent node (AC-8-2).

    A ``cache_config`` given as a plain dict is coerced to :class:`CacheConfig`
    so the factory can keep ``node_model_config`` free of SDK objects if it wants.
    """
    config = dict(node_model_config)
    cache_config = config.get("cache_config")
    if isinstance(cache_config, dict):
        config["cache_config"] = CacheConfig(**cache_config)
    return BedrockModel(**config)


def _build_swarm_nodes(
    agents: list[dict[str, Any]],
    *,
    tool_registry: dict[str, Any],
    node_model_config: dict[str, Any],
    write_file_tool: Any,
    write_findings_tool: Any,
) -> list[Agent]:
    """Build one fresh ``Agent`` per spec, resolving tools and appending writers.

    Each spec is ``{"name": str, "system_prompt": str, "tools": list[str]}``.
    String tool names are resolved through ``tool_registry``; unknown names are
    dropped (with a warning) rather than passed as bare strings, since a Swarm
    node needs real tool objects. The layout-confined ``write_file`` tool and the
    typed ``write_findings`` tool are appended to every node's tool list if absent
    (AC-5-4, AC-3-1).

    Fresh ``Agent`` and ``BedrockModel`` instances are created per node (AC-8-1,
    AC-8-2). Node ids are the spec names; duplicates are rejected downstream by
    the ``Swarm`` constructor.
    """
    nodes: list[Agent] = []
    for spec in agents:
        name = spec.get("name") or f"agent_{len(nodes)}"
        system_prompt = spec.get("system_prompt", "")
        requested = spec.get("tools") or []

        resolved: list[Any] = []
        resolved_names: set[str] = set()
        for tool_name in requested:
            if not isinstance(tool_name, str):
                # Already a tool object — keep it and record its name if any.
                resolved.append(tool_name)
                resolved_names.add(getattr(tool_name, "tool_name", ""))
                continue
            resolved_tool = tool_registry.get(tool_name)
            if resolved_tool is None:
                logger.warning(f"dispatch_team: node {name!r} requested unknown tool {tool_name!r} — dropping")
                continue
            resolved.append(resolved_tool)
            resolved_names.add(tool_name)

        # Auto-append the output-confined write_file tool (AC-5-4).
        if "write_file" not in resolved_names:
            resolved.append(write_file_tool)
        # Auto-append the typed-findings write tool (AC-3-1).
        if "write_findings" not in resolved_names:
            resolved.append(write_findings_tool)

        nodes.append(
            Agent(
                model=_make_node_model(node_model_config),
                name=name,
                system_prompt=system_prompt,
                tools=resolved,
                callback_handler=None,
            ),
        )
    return nodes


def _summarize_swarm_result(result: Any) -> str:
    """Produce a compact text summary from a ``SwarmResult`` (best-effort)."""
    node_history = getattr(result, "node_history", None) or []
    names = [getattr(n, "node_id", getattr(n, "name", "?")) for n in node_history]
    status = getattr(result, "status", None)
    status_name = getattr(status, "value", None) or str(status)
    return f"status={status_name}; nodes={names}"[:500]


def _write_debug_entry(layout: TeamsLayout, entry: dict[str, Any]) -> None:
    """Append a structured debug record to the team-debug JSONL (AC-2-2).

    Never raises — a debug-log failure must not kill the coordinator. Preserved so
    the default ``run_teams`` path keeps producing today's debug records for
    ``read_team_findings`` and the display hooks (AC-2-6).
    """
    try:
        debug_log = layout.debug_log_path()
        debug_log.parent.mkdir(parents=True, exist_ok=True)
        with debug_log.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.debug(f"Failed to write {layout.debug_log_name}: {e}")


def _emit_dispatch_event(
    layout: TeamsLayout,
    *,
    team_id: str,
    status: str,
    duration_seconds: float,
    payload_extra: dict[str, Any],
    goal_id: str = "",
    thread_id: str = "",
) -> None:
    """Emit a typed dispatch ``TeamEvent`` to the run's :class:`EventLog` (AC-2-3).

    Fires on every dispatch path (success, failure, timeout), carrying the same
    ``team_id``, ``status``, and ``duration_seconds`` recorded in the untyped debug
    entry. A ``failed``/``timeout``/``error`` status is what
    :func:`~strands_teams.events.should_replan` reads to fire ``task.failed``. The
    :class:`EventLog` swallows its own ``OSError`` (never-raises, AC-2-4), so this
    never kills the coordinator or the lead loop.
    """
    from .events import EventLog
    from .models import TeamEvent

    event = TeamEvent(
        event_type="team.dispatched",
        actor=team_id,
        goal_id=goal_id,
        task_id=team_id,
        thread_id=thread_id,
        epistemic_status="observed",
        payload={
            "team_id": team_id,
            "status": status,
            "duration_seconds": duration_seconds,
            **payload_extra,
        },
    )
    EventLog(layout.event_log_path()).append(event)


def _record_dispatch_completion(
    layout: TeamsLayout,
    state: TeamInvocationState,
    *,
    team_id: str,
    status: str,
    duration_seconds: float,
    effective_exec: float,
    effective_node: float,
    agent_names: list[str],
    agent_count: int,
    mandate: str,
    error_type: str | None,
) -> None:
    """Write the untyped debug entry and emit the typed dispatch event (AC-2-2, AC-2-3, AC-2-6).

    On every dispatch path both the pre-split ``team_debug.jsonl`` record (so
    ``read_team_findings`` and the display hooks stay intact, AC-2-6) and a typed
    :class:`~strands_teams.models.TeamEvent` on the event plane are produced. The
    event carries ``goal_id`` / ``thread_id`` from the durable lead loop's
    ``invocation_state`` when present (``run_goal``), else empty.
    """
    payload_extra: dict[str, Any] = {
        "execution_timeout": effective_exec,
        "node_timeout": effective_node,
        "agents": agent_names,
        "agent_count": agent_count,
        "mandate_preview": mandate[:200],
        **({"error_type": error_type} if error_type else {}),
    }
    _write_debug_entry(
        layout,
        {"team_id": team_id, "status": status, "duration_seconds": duration_seconds, **payload_extra},
    )
    _emit_dispatch_event(
        layout,
        team_id=team_id,
        status=status,
        duration_seconds=duration_seconds,
        payload_extra=payload_extra,
        goal_id=str(state.get("goal_id", "")),
        thread_id=str(state.get("thread_id", "")),
    )


# ============================================================================
# Tool 1: dispatch_team
# ============================================================================


def _coerce_authority(authority: dict[str, Any] | AuthorityGrant | None) -> AuthorityGrant:
    """Parse an ``authority`` argument into an :class:`AuthorityGrant` (AC-2-2, AC-3-6).

    The coordinator LLM supplies ``authority`` as a plain JSON dict; a programmatic
    caller may pass an :class:`AuthorityGrant` directly. When omitted, a
    default-permissive grant (``may_create_blocking_finding=True``) is returned so
    pre-authority callers behave exactly as before the authority contract existed
    (AC-3-6) — this default is deliberately more permissive than
    :class:`AuthorityGrant`'s own deny-by-default so the ``dispatch_team`` contract
    stays backward compatible.
    """
    if authority is None:
        return AuthorityGrant(may_create_blocking_finding=True)
    if isinstance(authority, AuthorityGrant):
        return authority
    return AuthorityGrant.model_validate(authority)


def _coerce_budget(budget: dict[str, Any] | Budget | None) -> Budget:
    """Parse a ``budget`` argument into a :class:`Budget` (AC-2-2).

    The coordinator LLM supplies ``budget`` as a plain JSON dict; a programmatic
    caller may pass a :class:`Budget` directly. When omitted, an all-zero budget
    (no explicit limits) is returned, so the run-default swarm timeout applies.
    """
    if budget is None:
        return Budget()
    if isinstance(budget, Budget):
        return budget
    return Budget.model_validate(budget)


def _parse_contract(
    layout: TeamsLayout,
    *,
    team_id: str,
    agent_count: int,
    authority: dict[str, Any] | AuthorityGrant | None,
    budget: dict[str, Any] | Budget | None,
) -> tuple[AuthorityGrant, Budget] | str:
    """Parse the authority/budget contract, or return an error-envelope JSON string.

    A malformed LLM-supplied dict must not escape ``dispatch_team`` as a raw
    ``ValidationError``; instead it produces the tool's standard
    ``{"status": "error", ...}`` envelope and a debug entry, on parity with every
    other dispatch failure path (AC-2-2/AC-2-3).
    """
    try:
        return _coerce_authority(authority), _coerce_budget(budget)
    except PydanticValidationError as e:
        message = f"invalid authority/budget contract: {str(e)[:400]}"
        _write_debug_entry(
            layout,
            {
                "team_id": team_id,
                "status": "error",
                "duration_seconds": 0.0,
                "agent_count": agent_count,
                "error_type": "invalid_contract",
                "message": message,
            },
        )
        return json.dumps({"status": "error", "message": message, "error_type": "invalid_contract"})


def _enforce_blocking_authority(
    layout: TeamsLayout,
    team_id: str,
    authority: AuthorityGrant,
) -> str | None:
    """Structurally strip forbidden blocking findings; return an error message or ``None``.

    Reads the team's typed ``findings.json`` (if any), and when the team's
    ``AuthorityGrant`` forbids blocking findings, removes every ``blocking=True``
    finding and rewrites the sidecar so the forbidden finding does not survive the
    run (AC-3-1, AC-3-3, AC-3-4). Enforcement lives here in framework code, not in
    any team-agent system prompt.

    Returns the error message when a forbidden blocking finding was rejected, or
    ``None`` when nothing was rejected — either the grant permits blocking findings
    (AC-3-5), no findings were persisted, or none were blocking. Never raises: a
    malformed or unreadable sidecar degrades to ``None`` so a findings I/O problem
    cannot mask itself as an authority rejection.
    """
    if authority.may_create_blocking_finding:
        return None  # AC-3-5: grant permits blocking findings — persist unchanged.

    json_path = layout.findings_json_path(team_id)
    if not json_path.exists():
        return None
    try:
        findings = load_findings(json_path)
    except (PydanticValidationError, ValueError, OSError) as e:
        logger.warning(f"dispatch_team({team_id}): could not read findings.json for authority enforcement: {e}")
        return None

    rejected = [f.statement for f in findings if f.blocking]
    if not rejected:
        return None

    kept = [f for f in findings if not f.blocking]
    try:
        json_path.write_text(serialize_findings(kept), encoding="utf-8")
    except OSError as e:
        logger.warning(f"dispatch_team({team_id}): failed to rewrite findings.json after authority strip: {e}")
    logger.warning(
        f"dispatch_team({team_id}): rejected {len(rejected)} blocking finding(s) — grant forbids blocking findings",
    )
    preview = "; ".join(rejected)[:400]
    return (
        f"Team {team_id} is not authorized to create blocking findings "
        f"(may_create_blocking_finding=False); rejected and removed "
        f"{len(rejected)} blocking finding(s): {preview}"
    )


def _effective_execution_timeout(
    *,
    execution_timeout: float | None,
    budget: Budget,
    default: float,
) -> float:
    """Pick the swarm execution timeout: explicit arg > positive budget > run default (AC-2-5)."""
    if execution_timeout is not None:
        return execution_timeout
    if budget.max_wall_clock_minutes > 0:
        return budget.max_wall_clock_minutes * 60
    return default


def _write_team_contract(
    layout: TeamsLayout,
    team_id: str,
    *,
    authority: AuthorityGrant,
    budget: Budget,
    acceptance_criteria: list[str] | None,
    input_revisions: list[str] | None,
) -> None:
    """Persist a team's task contract to its ``contract.json`` sidecar (AC-2-6).

    Writes the granted authority, budget, acceptance criteria, and (opaque)
    input-revision identifiers so they survive the run and so the
    :class:`~strands_teams.hooks.ArtifactQualityHook` can read every team's
    acceptance criteria for coverage evaluation. Never raises — a contract-write
    failure must not kill the coordinator.
    """
    contract = {
        "team_id": team_id,
        "authority": authority.model_dump(mode="json"),
        "budget": budget.model_dump(mode="json"),
        "acceptance_criteria": list(acceptance_criteria or []),
        "input_revisions": list(input_revisions or []),
    }
    try:
        path = layout.contract_path(team_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"dispatch_team({team_id}): failed to write {layout.contract_name}: {e}")


async def _build_and_run_swarm(
    agents: list[dict[str, Any]],
    *,
    state: TeamInvocationState,
    write_file_tool: Any,
    write_findings_tool: Any,
    augmented_task: str,
    team_state: dict[str, Any],
    max_handoffs: int,
    execution_timeout: float,
    node_timeout: float,
) -> Any:
    """Build the native ``Swarm`` and run it via ``invoke_async`` (AC-5-2).

    Preserves the repetitive-handoff detection window 8 / min-unique 3 and passes
    the per-team ``invocation_state`` by reference (no serialization boundary).
    """
    nodes = _build_swarm_nodes(
        agents,
        tool_registry=state["tool_registry"],
        node_model_config=state["node_model_config"],
        write_file_tool=write_file_tool,
        write_findings_tool=write_findings_tool,
    )
    swarm = Swarm(
        nodes,
        max_handoffs=max_handoffs,
        max_iterations=max_handoffs,
        execution_timeout=execution_timeout,
        node_timeout=node_timeout,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
    return await swarm.invoke_async(augmented_task, invocation_state=team_state)


async def _run_and_enforce(
    agents: list[dict[str, Any]],
    *,
    state: TeamInvocationState,
    layout: TeamsLayout,
    team_id: str,
    grant: AuthorityGrant,
    write_file_tool: Any,
    write_findings_tool: Any,
    augmented_task: str,
    team_state: dict[str, Any],
    max_handoffs: int,
    execution_timeout: float,
    node_timeout: float,
) -> tuple[str, str, str | None, str | None]:
    """Run the team swarm, then enforce the blocking-finding grant on EVERY path.

    Returns ``(status, summary, message, error_type)``. A team failure never kills
    the coordinator (the swarm call is guarded). Enforcement runs even after a
    timeout/exception — a forbidden blocking finding persisted before the crash
    must not survive — and its violation wins over a prior timeout status
    (AC-3-1..AC-3-4, AC-2-3).
    """
    status = "error"
    error_type: str | None = None
    summary = ""
    message: str | None = None
    try:
        result = await _build_and_run_swarm(
            agents,
            state=state,
            write_file_tool=write_file_tool,
            write_findings_tool=write_findings_tool,
            augmented_task=augmented_task,
            team_state=team_state,
            max_handoffs=max_handoffs,
            execution_timeout=execution_timeout,
            node_timeout=node_timeout,
        )
        result_status = getattr(getattr(result, "status", None), "value", None) or "completed"
        status = "completed" if result_status == "completed" else result_status
        summary = _summarize_swarm_result(result)
    except Exception as e:  # noqa: BLE001 — a team failure must never kill the coordinator
        status = "error"
        message = str(e)[:500]
        summary = message
        if "timed out" in message.lower() or "timeout" in type(e).__name__.lower():
            error_type = "timeout"
        logger.warning(f"Team {team_id} failed: {e}")

    violation = _enforce_blocking_authority(layout, team_id, grant)
    if violation is not None:
        status = "error"
        error_type = "authority_violation"
        message = violation
        summary = violation
    return status, summary, message, error_type


@tool(context=True)
async def dispatch_team(
    team_id: str,
    mandate: str,
    agents: list[dict[str, Any]],
    tool_context: ToolContext,
    file_scope: list[str] | None = None,
    key_questions: list[str] | None = None,
    artifact_pointers: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    authority: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    input_revisions: list[str] | None = None,
    max_handoffs: int = 10,
    execution_timeout: float | None = None,
    node_timeout: float | None = None,
) -> str:
    """Dispatch a specialist team to investigate one area and persist its findings.

    Each team runs as a native Strands Swarm — a group of agents that hand off to
    one another to complete a mandate. The team's task is automatically augmented
    with a Required-Output block instructing the last agent to write its findings
    and metadata files (paths are supplied in the augmented task). A confined
    ``write_file`` tool is auto-added to every agent.

    TASK CONTRACT — a team is dispatched under a typed authority + budget contract:
        - ``authority`` grants capabilities. A team whose grant has
          ``may_create_blocking_finding`` false CANNOT persist a blocking finding:
          this tool strips any such finding from the team's typed findings and
          returns an error. Enforcement is in this tool's code, not the agents'
          prompts. Omit ``authority`` to permit blocking findings (legacy behavior).
        - ``budget`` bounds resources. A positive ``max_wall_clock_minutes`` sets
          the swarm's execution timeout to ``max_wall_clock_minutes * 60``.
        - ``acceptance_criteria`` are the conditions the team must satisfy; they
          are surfaced in the team task AND checked for coverage by the quality hook.
        - ``input_revisions`` are opaque revision-id strings the team builds on.

    AGENT SPEC FORMAT — each entry in ``agents`` is a dict with:
        - ``name`` (str): unique node name (e.g. ``"analyst"``, ``"reader"``).
        - ``system_prompt`` (str): role and instructions for this agent.
        - ``tools`` (list[str]): tool names for this agent, resolved from the
          coordinator's tool registry. Unknown names are dropped.

    Args:
        team_id: Unique team identifier (e.g. ``"team-structure"``).
        mandate: What to investigate and why (1-3 sentences).
        agents: List of agent spec dicts (see AGENT SPEC FORMAT).
        tool_context: Framework-injected run context (invocation state).
        file_scope: Optional file paths or directories to focus on.
        key_questions: Optional questions the team should answer.
        artifact_pointers: Optional pre-computed artifact paths to consult.
        acceptance_criteria: Optional conditions the team's output must satisfy.
        authority: Optional capability grant as a JSON dict with any of
            ``may_create_blocking_finding``, ``may_write``, ``may_approve``
            (each defaults false). Omit to permit blocking findings (legacy).
        budget: Optional resource envelope as a JSON dict with any of
            ``max_wall_clock_minutes``, ``max_cost_usd``, ``max_retries``.
        input_revisions: Optional opaque revision-id strings the team builds on.
        max_handoffs: Max handoffs (and iterations) within the team.
        execution_timeout: Max seconds for the entire team. Omit for the run default.
        node_timeout: Max seconds per agent turn. Omit for the run default.

    Returns:
        JSON with ``team_id``, ``status``, ``duration_seconds``, ``findings_path``,
        ``metadata_path``, and a result ``summary``. On failure — including a
        rejected forbidden blocking finding — ``status`` is ``"error"`` and
        ``message`` carries the reason.
    """
    state = _require_state(tool_context)
    layout = state["layout"]

    # Parse the task-contract dicts into typed models (AC-2-2). A malformed
    # LLM-supplied dict yields the tool's standard error envelope rather than an
    # escaping ValidationError (AC-2-2/AC-2-3).
    parsed = _parse_contract(layout, team_id=team_id, agent_count=len(agents), authority=authority, budget=budget)
    if isinstance(parsed, str):
        return parsed
    grant, team_budget = parsed

    # A positive wall-clock budget overrides the run-default execution timeout
    # (AC-2-5); an explicit ``execution_timeout`` argument still wins over both.
    effective_exec = _effective_execution_timeout(
        execution_timeout=execution_timeout,
        budget=team_budget,
        default=state["execution_timeout"],
    )
    effective_node = node_timeout if node_timeout is not None else state["node_timeout"]

    agent_names = [a.get("name", "?") for a in agents]
    logger.debug(
        f"dispatch_team({team_id}): execution_timeout={effective_exec}s, "
        f"node_timeout={effective_node}s, agents={agent_names}",
    )

    team_dir = layout.team_dir(team_id)
    team_dir.mkdir(parents=True, exist_ok=True)
    findings_path = str(layout.findings_path(team_id))
    findings_json_path = str(layout.findings_json_path(team_id))
    metadata_path = str(layout.metadata_path(team_id))

    # Persist the task contract next to the team's findings so it survives the run
    # and the quality hook can read acceptance criteria for coverage (AC-2-6).
    _write_team_contract(
        layout,
        team_id,
        authority=grant,
        budget=team_budget,
        acceptance_criteria=acceptance_criteria,
        input_revisions=input_revisions,
    )

    augmented_task = _build_augmented_task(
        mandate=mandate,
        findings_path=findings_path,
        findings_json_path=findings_json_path,
        metadata_path=metadata_path,
        file_scope=file_scope,
        key_questions=key_questions,
        artifact_pointers=artifact_pointers,
        acceptance_criteria=acceptance_criteria,
        input_revisions=input_revisions,
        output_root=str(layout.output_root),
    )

    write_file_tool = make_write_file(layout)
    write_findings_tool = make_write_findings(layout, team_id)
    start = time.monotonic()

    # Build a NEW invocation_state carrying the per-team contract by reference,
    # rather than mutating the coordinator's shared state dict (AC-5-1, AC-5-4).
    # The live AuthorityGrant/Budget objects cross no serialization boundary and
    # carry no session_manager; node tools must treat them read-only (AC-5-3).
    team_state: dict[str, Any] = {
        **state,
        "authority": grant,
        "budget": team_budget,
        "acceptance_criteria": list(acceptance_criteria or []),
        "input_revisions": list(input_revisions or []),
    }

    status, summary, message, error_type = await _run_and_enforce(
        agents,
        state=state,
        layout=layout,
        team_id=team_id,
        grant=grant,
        write_file_tool=write_file_tool,
        write_findings_tool=write_findings_tool,
        augmented_task=augmented_task,
        team_state=team_state,
        max_handoffs=max_handoffs,
        execution_timeout=effective_exec,
        node_timeout=effective_node,
    )
    duration = time.monotonic() - start

    # AC-2-2/AC-2-3: on EVERY path (success, failure, timeout) write today's debug
    # entry AND emit a typed dispatch TeamEvent to the event plane.
    _record_dispatch_completion(
        layout,
        state,
        team_id=team_id,
        status=status,
        duration_seconds=round(duration, 1),
        effective_exec=effective_exec,
        effective_node=effective_node,
        agent_names=agent_names,
        agent_count=len(agents),
        mandate=mandate,
        error_type=error_type,
    )

    response: dict[str, Any] = {
        "team_id": team_id,
        "status": status,
        "duration_seconds": round(duration, 1),
        "findings_path": findings_path,
        "findings_json_path": findings_json_path,
        "metadata_path": metadata_path,
        "summary": summary,
    }
    if message is not None:
        response["message"] = message
    if error_type:
        response["error_type"] = error_type
    return json.dumps(response)


def _build_augmented_task(
    *,
    mandate: str,
    findings_path: str,
    findings_json_path: str,
    metadata_path: str,
    file_scope: list[str] | None,
    key_questions: list[str] | None,
    artifact_pointers: list[str] | None,
    output_root: str,
    acceptance_criteria: list[str] | None = None,
    input_revisions: list[str] | None = None,
) -> str:
    """Compose the augmented team task with mandate + Required-Output block (AC-2-3, AC-2-4, AC-3-3).

    ``acceptance_criteria`` and ``input_revisions`` are surfaced as their own task
    sections so the team can act on them (AC-2-4). ``input_revisions`` entries are
    treated as opaque revision-identifier strings — they are never resolved or
    validated here, so an entry that does not yet map to a Phase-4
    ``ArtifactRevision`` is simply listed as text and never raises (AC-2-7).
    """
    parts = [f"## Team Mandate\n{mandate}\n"]

    if file_scope:
        scope_list = "\n".join(f"- {f}" for f in file_scope)
        parts.append(f"## File Scope\nFocus on these files/directories:\n{scope_list}")

    if key_questions:
        parts.append("## Key Questions\n" + "\n".join(f"- {q}" for q in key_questions))

    if acceptance_criteria:
        parts.append(
            "## Acceptance Criteria\nYour output MUST satisfy every one of these:\n"
            + "\n".join(f"- {c}" for c in acceptance_criteria),
        )

    if input_revisions:
        parts.append(
            "## Input Revisions\nBuild on these prior revisions (revision identifiers):\n"
            + "\n".join(f"- {r}" for r in input_revisions),
        )

    if artifact_pointers:
        parts.append(
            "## Pre-computed Artifacts\nConsult these artifacts for context:\n"
            + "\n".join(f"- {a}" for a in artifact_pointers),
        )

    parts.append(
        f"""## Required Output

When your analysis is complete, the LAST agent in the chain MUST write BOTH:

1. Human findings to: {findings_path}
   - Use markdown format with clear sections.
   - Include file:line references for all claims.
   - Use mermaid code-fenced diagrams (never ASCII art).
   - Write this with the write_file tool.

2. Typed findings to: {findings_json_path}
   - Call the write_findings tool with a list of finding objects.
   - Each finding: {{"statement": "...", "severity": "info|low|medium|high|critical",
     "blocking": false, "disposition": "open", "affected_artifacts": ["path"],
     "evidence": [{{"type": "file_line|test|command|artifact|url|log", "uri": "src/x.py:42"}}],
     "confidence": 0.0-1.0, "created_by": "your-team"}}.
   - EVERY finding with "blocking": true MUST include at least one evidence ref.

3. Metadata to: {metadata_path}
   - JSON with keys: files_read (list), tools_used (list), duration_s (float), status ("done").
   - Write this with the write_file tool.

The coordinator reads these after your team completes.
Output directory: {output_root}""",
    )
    return "\n\n".join(parts)


# ============================================================================
# Tool 2: read_team_findings
# ============================================================================


def _try_load_typed_findings(layout: TeamsLayout, team_id: str) -> list[Finding] | None:
    """Load and validate a team's ``findings.json``, or return ``None`` on any failure.

    Never raises: a missing, unreadable, invalid-JSON, or schema-invalid sidecar
    degrades to ``None`` so the caller can fall back to markdown (AC-4-4, AC-8-4).
    Validation happens over the parsed JSON text at the parse boundary.
    """
    json_path = layout.findings_json_path(team_id)
    if not json_path.exists():
        return None
    try:
        return load_findings(json_path)
    except (PydanticValidationError, ValueError, OSError) as e:
        logger.warning(f"read_team_findings: {team_id} findings.json invalid, falling back to markdown: {e}")
        return None


def _typed_read_payload(team_id: str, typed: list[Finding], findings_file: Path) -> dict[str, Any]:
    """Build the typed-read result, augmenting typed findings with the markdown narrative.

    The typed findings never replace the human ``findings.md`` that bundle synthesis
    depends on (AC-6-2): ``findings_path`` is always present so the coordinator can
    discover the narrative, and ``markdown`` carries its full text when the file exists.
    """
    payload: dict[str, Any] = {
        "status": "ok",
        "team_id": team_id,
        "source": "typed",
        "findings": findings_as_dicts(typed),
        "finding_count": len(typed),
        "evidence_gate": gate_findings(typed),
        "findings_path": str(findings_file),
    }
    if findings_file.exists():
        markdown = findings_file.read_text()
        payload["markdown"] = markdown
        payload["markdown_line_count"] = len(markdown.splitlines())
    return payload


@tool(context=True)
def read_team_findings(tool_context: ToolContext, team_id: str | None = None) -> str:
    """Read findings from dispatched teams.

    If ``team_id`` is ``None``, lists all team directories with their status,
    metadata summary, and whether typed findings are present — use this to see
    which teams have completed. If ``team_id`` is given, returns that team's typed
    ``findings.json`` (parsed) when it exists and validates, AND the human
    ``findings.md`` narrative alongside it; when the typed sidecar is absent or
    invalid it falls back to the raw ``findings.md`` markdown.

    Args:
        tool_context: Framework-injected run context (invocation state).
        team_id: Specific team to read, or ``None`` to list all teams.

    Returns:
        JSON listing all teams (when ``team_id`` is ``None``), or the findings
        content (when a ``team_id`` is given). For a typed read, ``findings`` is a
        list of finding objects, ``source`` is ``"typed"``, ``evidence_gate`` flags
        any blocking finding lacking evidence, ``findings_path`` points at the
        human ``findings.md``, and ``markdown`` carries its full narrative (mermaid,
        file:line prose, structured sections) when the file exists — this markdown
        is the rich input for bundle synthesis. For a fallback read, ``findings``
        is the markdown string and ``source`` is ``"markdown"``.
    """
    layout = _layout_of(tool_context)
    teams_root = layout.teams_dir()

    if team_id is None:
        if not teams_root.exists():
            return json.dumps({"status": "no_teams", "teams": []})
        teams = []
        for team_dir in sorted(teams_root.iterdir()):
            if not team_dir.is_dir():
                continue
            meta_path = team_dir / layout.metadata_name
            findings_file = team_dir / layout.findings_name
            meta = None
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass  # Corrupt/unreadable metadata — skip it.
            typed = _try_load_typed_findings(layout, team_dir.name)
            teams.append(
                {
                    "team_id": team_dir.name,
                    "has_findings": findings_file.exists(),
                    "findings_lines": len(findings_file.read_text().splitlines()) if findings_file.exists() else 0,
                    "has_typed_findings": typed is not None,
                    "typed_finding_count": len(typed) if typed is not None else 0,
                    "metadata": meta,
                },
            )
        return json.dumps({"status": "ok", "teams": teams}, indent=2)

    team_dir = layout.team_dir(team_id)
    if not team_dir.exists():
        return json.dumps({"status": "error", "message": f"Team directory not found: {team_id}"})

    # Prefer the typed sidecar, but ALWAYS also surface the human findings.md so
    # bundle synthesis keeps its rich markdown input (mermaid, file:line prose,
    # structured sections) — the typed findings augment, never replace, it
    # (AC-6-2). Even when findings.md is absent, ``findings_path`` lets the
    # coordinator discover the markdown and read it via ``read_file_bounded``.
    findings_file = layout.findings_path(team_id)
    typed = _try_load_typed_findings(layout, team_id)
    if typed is not None:
        return json.dumps(_typed_read_payload(team_id, typed, findings_file))

    if not findings_file.exists():
        return json.dumps(
            {
                "status": "no_findings",
                "message": f"Team {team_id} has no findings — it may still be running or failed before writing.",
            },
        )

    content = findings_file.read_text()
    return json.dumps(
        {
            "status": "ok",
            "team_id": team_id,
            "source": "markdown",
            "findings": content,
            "line_count": len(content.splitlines()),
        },
    )


# ============================================================================
# Tool 3: write_artifact
# ============================================================================


@tool(context=True)
def write_artifact(area: str, content: str, tool_context: ToolContext, is_executive_summary: bool = False) -> str:
    """Write a per-area narrative artifact or the top-level executive summary.

    All artifact paths come from the run's layout. Diagrams must use mermaid
    code-fenced blocks (never ASCII art). The artifact section structure the
    coordinator should follow is described in its system prompt, not here.

    Args:
        area: Artifact area identifier (e.g. ``"auth"``). Ignored when
            ``is_executive_summary`` is True.
        content: The full markdown content to write.
        tool_context: Framework-injected run context (invocation state).
        is_executive_summary: If True, write the executive summary instead of a
            per-area artifact.

    Returns:
        JSON with ``status``, absolute ``path``, ``relative_path``, and ``line_count``.
    """
    layout = _layout_of(tool_context)

    if is_executive_summary:
        file_path = layout.executive_summary_path()
    else:
        layout.artifacts_dir().mkdir(parents=True, exist_ok=True)
        file_path = layout.artifact_path(area)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        file_path.write_text(content, encoding="utf-8")
    except OSError as e:
        return json.dumps({"status": "error", "message": str(e)})

    line_count = len(content.splitlines())
    try:
        relative_path = str(file_path.relative_to(layout.output_root))
    except ValueError:
        relative_path = file_path.name
    logger.info(f"write_artifact: wrote {file_path.name} ({line_count} lines)")
    return json.dumps(
        {
            "status": "ok",
            "path": str(file_path),
            "relative_path": relative_path,
            "line_count": line_count,
        },
    )


# ============================================================================
# Tool 4: score_narrative
# ============================================================================


@tool(context=True)
def score_narrative(area: str, tool_context: ToolContext) -> str:
    """Score a per-area artifact's quality using the run's rubric.

    Call after ``write_artifact`` to evaluate quality. If ``needs_revision`` is
    True (total below the rubric's revision threshold), call ``enrich_artifact``
    and rewrite via ``write_artifact``.

    Args:
        area: The artifact area to score (e.g. ``"auth"``).
        tool_context: Framework-injected run context (invocation state).

    Returns:
        JSON with ``area``, per-dimension ``scores`` and ``counts``, the ``total``
        average, the ``needs_revision`` flag, and improvement ``suggestions``.
    """
    state = _require_state(tool_context)
    layout = state["layout"]
    rubric = state["rubric"]

    artifact_path = layout.artifact_path(area)
    if not artifact_path.exists():
        return json.dumps({"status": "error", "message": f"Artifact not found: {artifact_path}"})

    report = rubric.score(artifact_path.read_text())
    return json.dumps(
        {
            "status": "ok",
            "area": area,
            "scores": report.scores,
            "counts": report.counts,
            "total": report.total,
            "needs_revision": report.needs_revision,
            "suggestions": report.suggestions,
        },
    )


# ============================================================================
# Tool 5: enrich_artifact
# ============================================================================


@tool(context=True)
def enrich_artifact(area: str, feedback: str, tool_context: ToolContext) -> str:
    """Read an existing artifact and prepare enrichment context.

    Call when ``score_narrative`` reports ``needs_revision``. Returns the current
    artifact content plus the feedback so you can rewrite it with improvements via
    ``write_artifact``.

    Args:
        area: The artifact area to enrich (e.g. ``"auth"``).
        feedback: Specific feedback from ``score_narrative`` about what to improve.
        tool_context: Framework-injected run context (invocation state).

    Returns:
        JSON with the current artifact ``current_content``, its ``line_count``, the
        ``feedback``, and a rewrite ``instruction``.
    """
    layout = _layout_of(tool_context)
    artifact_path = layout.artifact_path(area)
    if not artifact_path.exists():
        return json.dumps({"status": "error", "message": f"Artifact not found: {artifact_path}"})

    content = artifact_path.read_text()
    return json.dumps(
        {
            "status": "ok",
            "area": area,
            "current_content": content,
            "line_count": len(content.splitlines()),
            "feedback": feedback,
            "instruction": (
                "Rewrite this artifact incorporating the feedback above. "
                "Use write_artifact to save the improved version. "
                "Preserve all existing accurate information while adding depth."
            ),
        },
    )


TEAM_TOOLS = (dispatch_team, read_team_findings, write_artifact, score_narrative, enrich_artifact)
"""The five coordinator team tools, in canonical order (consumed by the factory)."""
