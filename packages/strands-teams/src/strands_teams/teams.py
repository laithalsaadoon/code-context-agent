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
from typing import TYPE_CHECKING, Any, TypedDict, cast

from loguru import logger
from strands import Agent, ToolContext, tool
from strands.models import BedrockModel
from strands.models.bedrock import CacheConfig
from strands.multiagent import Swarm

from .write_file import make_write_file

if TYPE_CHECKING:
    from .layout import TeamsLayout
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
    """

    layout: TeamsLayout
    tool_registry: dict[str, Any]
    node_model_config: dict[str, Any]
    execution_timeout: float
    node_timeout: float
    use_case_name: str
    rubric: NarrativeRubric


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
) -> list[Agent]:
    """Build one fresh ``Agent`` per spec, resolving tools and appending write_file.

    Each spec is ``{"name": str, "system_prompt": str, "tools": list[str]}``.
    String tool names are resolved through ``tool_registry``; unknown names are
    dropped (with a warning) rather than passed as bare strings, since a Swarm
    node needs real tool objects. The layout-confined ``write_file`` tool is
    appended to every node's tool list if absent (AC-5-4).

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

    Never raises — a debug-log failure must not kill the coordinator.
    """
    try:
        debug_log = layout.debug_log_path()
        debug_log.parent.mkdir(parents=True, exist_ok=True)
        with debug_log.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.debug(f"Failed to write {layout.debug_log_name}: {e}")


# ============================================================================
# Tool 1: dispatch_team
# ============================================================================


@tool(context=True)
async def dispatch_team(
    team_id: str,
    mandate: str,
    agents: list[dict[str, Any]],
    tool_context: ToolContext,
    file_scope: list[str] | None = None,
    key_questions: list[str] | None = None,
    artifact_pointers: list[str] | None = None,
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
        max_handoffs: Max handoffs (and iterations) within the team.
        execution_timeout: Max seconds for the entire team. Omit for the run default.
        node_timeout: Max seconds per agent turn. Omit for the run default.

    Returns:
        JSON with ``team_id``, ``status``, ``duration_seconds``, ``findings_path``,
        ``metadata_path``, and a result ``summary``. On failure, ``status`` is
        ``"error"`` and ``message`` carries the reason.
    """
    state = _require_state(tool_context)
    layout = state["layout"]
    effective_exec = execution_timeout if execution_timeout is not None else state["execution_timeout"]
    effective_node = node_timeout if node_timeout is not None else state["node_timeout"]

    agent_names = [a.get("name", "?") for a in agents]
    logger.debug(
        f"dispatch_team({team_id}): execution_timeout={effective_exec}s, "
        f"node_timeout={effective_node}s, agents={agent_names}",
    )

    team_dir = layout.team_dir(team_id)
    team_dir.mkdir(parents=True, exist_ok=True)
    findings_path = str(layout.findings_path(team_id))
    metadata_path = str(layout.metadata_path(team_id))

    augmented_task = _build_augmented_task(
        mandate=mandate,
        findings_path=findings_path,
        metadata_path=metadata_path,
        file_scope=file_scope,
        key_questions=key_questions,
        artifact_pointers=artifact_pointers,
        output_root=str(layout.output_root),
    )

    write_file_tool = make_write_file(layout)
    start = time.monotonic()
    status = "error"
    error_type: str | None = None
    summary = ""
    message: str | None = None

    try:
        nodes = _build_swarm_nodes(
            agents,
            tool_registry=state["tool_registry"],
            node_model_config=state["node_model_config"],
            write_file_tool=write_file_tool,
        )
        swarm = Swarm(
            nodes,
            max_handoffs=max_handoffs,
            max_iterations=max_handoffs,
            execution_timeout=effective_exec,
            node_timeout=effective_node,
            repetitive_handoff_detection_window=8,
            repetitive_handoff_min_unique_agents=3,
        )
        result = await swarm.invoke_async(augmented_task, invocation_state={**state})
        duration = time.monotonic() - start
        result_status = getattr(getattr(result, "status", None), "value", None) or "completed"
        status = "completed" if result_status == "completed" else result_status
        summary = _summarize_swarm_result(result)
    except Exception as e:  # noqa: BLE001 — a team failure must never kill the coordinator
        duration = time.monotonic() - start
        status = "error"
        message = str(e)[:500]
        summary = message
        if "timed out" in message.lower() or "timeout" in type(e).__name__.lower():
            error_type = "timeout"
        logger.warning(f"Team {team_id} failed after {duration:.1f}s: {e}")

    # AC-2-2: write a debug entry on EVERY path (success, failure, timeout).
    debug_entry: dict[str, Any] = {
        "team_id": team_id,
        "status": status,
        "duration_seconds": round(duration, 1),
        "execution_timeout": effective_exec,
        "node_timeout": effective_node,
        "agents": agent_names,
        "agent_count": len(agents),
        "mandate_preview": mandate[:200],
    }
    if error_type:
        debug_entry["error_type"] = error_type
    _write_debug_entry(layout, debug_entry)

    response: dict[str, Any] = {
        "team_id": team_id,
        "status": status,
        "duration_seconds": round(duration, 1),
        "findings_path": findings_path,
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
    metadata_path: str,
    file_scope: list[str] | None,
    key_questions: list[str] | None,
    artifact_pointers: list[str] | None,
    output_root: str,
) -> str:
    """Compose the augmented team task with mandate + Required-Output block (AC-2-3)."""
    parts = [f"## Team Mandate\n{mandate}\n"]

    if file_scope:
        scope_list = "\n".join(f"- {f}" for f in file_scope)
        parts.append(f"## File Scope\nFocus on these files/directories:\n{scope_list}")

    if key_questions:
        parts.append("## Key Questions\n" + "\n".join(f"- {q}" for q in key_questions))

    if artifact_pointers:
        parts.append(
            "## Pre-computed Artifacts\nConsult these artifacts for context:\n"
            + "\n".join(f"- {a}" for a in artifact_pointers),
        )

    parts.append(
        f"""## Required Output

When your analysis is complete, the LAST agent in the chain MUST:

1. Write detailed findings to: {findings_path}
   - Use markdown format with clear sections.
   - Include file:line references for all claims.
   - Use mermaid code-fenced diagrams (never ASCII art).

2. Write metadata to: {metadata_path}
   - JSON with keys: files_read (list), tools_used (list), duration_s (float), status ("done").

Use the write_file tool for both files. The coordinator will read these after your team completes.
Output directory: {output_root}""",
    )
    return "\n\n".join(parts)


# ============================================================================
# Tool 2: read_team_findings
# ============================================================================


@tool(context=True)
def read_team_findings(tool_context: ToolContext, team_id: str | None = None) -> str:
    """Read findings from dispatched teams.

    If ``team_id`` is ``None``, lists all team directories with their status and
    metadata summary — use this to see which teams have completed. If ``team_id``
    is given, returns that team's full ``findings.md`` content.

    Args:
        tool_context: Framework-injected run context (invocation state).
        team_id: Specific team to read, or ``None`` to list all teams.

    Returns:
        JSON listing all teams (when ``team_id`` is ``None``), or the findings
        content (when a ``team_id`` is given).
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
            teams.append(
                {
                    "team_id": team_dir.name,
                    "has_findings": findings_file.exists(),
                    "findings_lines": len(findings_file.read_text().splitlines()) if findings_file.exists() else 0,
                    "metadata": meta,
                },
            )
        return json.dumps({"status": "ok", "teams": teams}, indent=2)

    team_dir = layout.team_dir(team_id)
    if not team_dir.exists():
        return json.dumps({"status": "error", "message": f"Team directory not found: {team_id}"})

    findings_file = layout.findings_path(team_id)
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
