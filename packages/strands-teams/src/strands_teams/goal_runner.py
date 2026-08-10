"""Durable lead-controller runner ``run_goal`` (spec 006, User Story 5).

A NEW runner alongside :func:`strands_teams.runner.run_teams` (never a
replacement — plan §4 Phase 5 bullet 2; AC-5-1). ``run_goal`` drives the ASTR
durable lead loop (ASTR §9.2)::

    decompose → compose → assign → dispatch → integrate → replan

Each stage runs as a :class:`~strands_teams.durable.DurableStep` on the in-process
:class:`~strands_teams.durable.AsyncioDurableEngine`, so every transition is
recorded as a :class:`~strands_teams.models.TeamEvent` on the run's
:class:`~strands_teams.events.EventLog` and the whole loop is replayable
(AC-5-3). The loop:

- Propagates ``goal_id`` / ``task_id`` / ``thread_id`` and the run's
  :class:`~strands_teams.layout.TeamsLayout` to dispatched teams through
  ``invocation_state`` — a plain in-process dict passed **by reference**, treated
  read-only inside concurrently-executing node tools (AC-5-4;
  ``research-coding-runtimes.yaml`` lines 19-22, 29-30). No module-level globals.
- Dispatches through an **injectable** dispatch callable defaulting to the native
  ``Swarm`` :func:`~strands_teams.teams.dispatch_team` tool (AC-5-5), so tests can
  substitute a stub and run with **no Bedrock call** (AC-7-4).
- Re-enters decompose (replan) whenever
  :func:`~strands_teams.events.should_replan` fires, recording a replan event
  first (AC-5-7).
- Terminates with a terminal status on convergence, on reaching
  ``max_lead_iterations``, or on crossing the budget threshold — never looping
  indefinitely (AC-5-6).

It issues **no live model / Bedrock call** itself: dispatch is the only
model-touching stage and it is injected. ``run_goal`` never calls ``asyncio.run``;
:func:`run_goal_sync` is the sync boundary, mirroring
:func:`~strands_teams.runner.run_teams_sync` (AC-5-2).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger
from pydantic import Field

from .durable import AsyncioDurableEngine
from .events import TRIGGER_BUDGET_EXCEEDED, EventLog, should_replan
from .models import FrozenModel, TeamEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .durable import DurableStep
    from .layout import TeamsLayout

# The ordered lead-loop stages that run every iteration before the replan check
# (AC-5-3). ``replan`` is evaluated after ``integrate`` from the event log rather
# than as a fixed step, so it can re-enter ``decompose`` (AC-5-7).
LEAD_STAGES: tuple[str, ...] = ("decompose", "compose", "assign", "dispatch", "integrate")
"""The ordered per-iteration lead-loop stages (AC-5-3)."""


class GoalResult(FrozenModel):
    """The terminal outcome of a :func:`run_goal` run (AC-5-6).

    Attributes:
        goal_id: The goal this run drove.
        status: Terminal status — ``converged``, ``max_iterations``, or ``budget_exceeded``.
        iterations: How many lead-loop iterations executed.
        replans: How many times the loop re-entered decompose via ``should_replan``.
        event_log_path: The path the run's :class:`EventLog` was written to.
        transitions: The ordered ``event_type``s recorded (for assertions/inspection).
    """

    goal_id: str = Field(description="The goal this run drove")
    status: str = Field(description="Terminal status: converged | max_iterations | budget_exceeded")
    iterations: int = Field(default=0, description="Lead-loop iterations executed")
    replans: int = Field(default=0, description="Replan re-entries triggered by should_replan")
    event_log_path: str = Field(default="", description="Path the EventLog was written to")
    transitions: list[str] = Field(default_factory=list, description="Ordered event_types recorded")


async def _default_dispatch(assignment: dict[str, Any], invocation_state: dict[str, Any]) -> dict[str, Any]:
    """Default dispatch: invoke the native-``Swarm`` ``dispatch_team`` tool (AC-5-5).

    Wraps the real :func:`~strands_teams.teams.dispatch_team` tool via its
    ``__wrapped__`` function, building a :class:`~strands.ToolContext` that carries
    the run's ``invocation_state`` by reference. This is the ONLY model-touching
    path; tests inject a stub instead so the suite never calls Bedrock (AC-7-4).
    """
    from strands import ToolContext
    from strands.types.tools import ToolUse

    from .teams import dispatch_team

    tool_use = ToolUse(toolUseId=uuid4().hex, name="dispatch_team", input=dict(assignment))
    tool_context = ToolContext(tool_use=tool_use, agent=None, invocation_state=invocation_state)
    import json

    dispatch_fn = dispatch_team.__wrapped__  # ty: ignore[unresolved-attribute]  # the tool's raw async function
    result = await dispatch_fn(
        team_id=str(assignment.get("team_id", "team")),
        mandate=str(assignment.get("mandate", "")),
        agents=list(assignment.get("agents", [])),
        tool_context=tool_context,
    )
    return json.loads(result) if isinstance(result, str) else dict(result)


def _emit(log: EventLog, event: TeamEvent) -> str:
    """Append ``event`` to ``log`` and return its ``event_id`` for causal chaining."""
    log.append(event)
    return event.event_id


async def run_goal(
    goal: str,
    *,
    layout: TeamsLayout,
    invocation_state: dict[str, Any] | None = None,
    dispatch: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    engine: DurableStep | None = None,
    max_iterations: int = 8,
    budget_threshold: float = 0.0,
    goal_id: str | None = None,
    thread_id: str = "",
) -> GoalResult:
    """Drive the durable lead loop for ``goal`` to a terminal status (AC-5-1, AC-5-3..AC-5-7).

    Runs decompose → compose → assign → dispatch → integrate each iteration, each as
    a :class:`~strands_teams.durable.DurableStep` recording a
    :class:`~strands_teams.models.TeamEvent` (AC-5-3). After ``integrate`` it
    consults :func:`~strands_teams.events.should_replan` over the run's
    :class:`EventLog`; when a trigger fires it records a ``goal.replan`` event and
    re-enters decompose (AC-5-7). Terminates — with a final ``goal.terminated``
    event — on convergence (no replan), on reaching ``max_iterations``, or on
    crossing ``budget_threshold`` (AC-5-6). Never calls ``asyncio.run`` (AC-5-2 is
    the sync boundary).

    Args:
        goal: The goal statement driving the loop.
        layout: The run's :class:`~strands_teams.layout.TeamsLayout`; its
            :meth:`~strands_teams.layout.TeamsLayout.event_log_path` is the plane.
        invocation_state: The by-reference dict propagated to dispatched teams;
            ``goal_id`` / ``task_id`` / ``thread_id`` / ``layout`` are injected into
            a shallow copy so teams (and the default ``dispatch_team``) see them
            (AC-5-4). Never mutated in place across concurrent node tools.
        dispatch: Injectable dispatch callable (AC-5-5); defaults to
            :func:`_default_dispatch` (native-``Swarm`` ``dispatch_team``).
        engine: The :class:`~strands_teams.durable.DurableStep` engine; defaults to
            an in-process :class:`~strands_teams.durable.AsyncioDurableEngine` bound
            to the run's :class:`EventLog`.
        max_iterations: Hard cap on lead-loop iterations (AC-5-6).
        budget_threshold: Cumulative-cost bound; crossing it terminates the loop and
            fires the budget replan trigger (AC-5-6, AC-6-4).
        goal_id: Stable id for the goal (auto-assigned when omitted).
        thread_id: Optional conversation/thread id propagated to teams.

    Returns:
        A :class:`GoalResult` naming the terminal status and recorded transitions.
    """
    gid = goal_id or uuid4().hex
    log = EventLog(layout.event_log_path())
    step_engine: DurableStep = engine or AsyncioDurableEngine(log)
    dispatch_fn = dispatch or _default_dispatch
    # The default dispatch runs the real ``dispatch_team`` tool, which emits its own
    # ``team.dispatched`` event to this same log. An injected stub does not. Only hoist
    # a top-level event for a stub, so the default path is not double-counted by the
    # cumulative budget check (which sums cost across every event). AC-6-4.
    dispatch_self_emits = dispatch is None

    # A shallow copy carrying the loop's ids + layout by reference (AC-5-4). Teams
    # treat it read-only; concurrent node tools never mutate it.
    base_state: dict[str, Any] = {
        **(invocation_state or {}),
        "layout": layout,
        "goal_id": gid,
        "thread_id": thread_id,
    }

    _emit(
        log,
        TeamEvent(event_type="goal.started", actor="lead", goal_id=gid, thread_id=thread_id, payload={"goal": goal}),
    )

    iterations = 0
    replans = 0
    status = "converged"

    while True:
        if iterations >= max_iterations:
            status = "max_iterations"
            break

        iterations += 1
        task_id = f"{gid}-iter{iterations}"

        # Snapshot the log length BEFORE this iteration's stages append anything, so
        # the per-event replan check below is edge-triggered: only events appended
        # during THIS iteration can (re-)fire a trigger (AC-5-7). Level-checking the
        # whole cumulative log would let a single past task.failed / critical_finding
        # re-fire replan forever, so a transient failure that later recovers would
        # still burn to max_iterations instead of converging.
        cursor = len(log.read())

        for stage in LEAD_STAGES:
            step_id = f"{gid}:{iterations}:{stage}"

            async def _run_stage(
                stage: str = stage, task_id: str = task_id, iteration: int = iterations
            ) -> dict[str, Any]:
                if stage == "dispatch":
                    assignment = {
                        "team_id": task_id,
                        "mandate": goal,
                        "agents": [],
                        "goal_id": gid,
                        "task_id": task_id,
                    }
                    team_state = {**base_state, "task_id": task_id}
                    return await dispatch_fn(assignment, team_state)
                return {"stage": stage, "iteration": iteration}

            step = await step_engine.run_step(
                step_id,
                stage,
                _run_stage,
                goal_id=gid,
                task_id=task_id,
                thread_id=thread_id,
                actor="lead",
            )

            # Hoist the dispatch outcome onto the event plane as a top-level
            # ``team.dispatched`` event so should_replan reads its status /
            # resource_usage directly (the step.result event nests them under
            # ``result``). The default dispatch's real dispatch_team already emits
            # its own event to this log, so hoisting only for an injected stub avoids
            # double-counting cost in the cumulative budget check (AC-5-7, AC-6-3, AC-6-4).
            if stage == "dispatch" and not dispatch_self_emits:
                _emit(
                    log,
                    TeamEvent(
                        event_type="team.dispatched",
                        actor="lead",
                        goal_id=gid,
                        task_id=task_id,
                        thread_id=thread_id,
                        epistemic_status="observed",
                        payload=dict(step.payload),
                    ),
                )

        full_events = log.read()

        # Budget is intentionally CUMULATIVE (AC-6-4): summed across the whole run,
        # not just this iteration. Crossing it terminates the loop (AC-5-6). Evaluated
        # over the full log so prior iterations' recorded cost still counts.
        if budget_threshold > 0:
            budget_decision = should_replan(full_events, budget_threshold=budget_threshold)
            if TRIGGER_BUDGET_EXCEEDED in budget_decision.triggers:
                status = "budget_exceeded"
                break

        # Per-event replan triggers are EDGE-triggered (AC-5-7): evaluate only the
        # events appended during THIS iteration (``full_events[cursor:]``), never the
        # cumulative log. A single past task.failed / critical_finding must not
        # re-fire replan forever — otherwise a transient failure that later recovers
        # would still burn every iteration to max_iterations instead of converging.
        # Budget is passed as disabled here; it is handled cumulatively above.
        decision = should_replan(full_events[cursor:])

        if decision.replan:
            replans += 1
            _emit(
                log,
                TeamEvent(
                    event_type="goal.replan",
                    actor="lead",
                    goal_id=gid,
                    task_id=task_id,
                    thread_id=thread_id,
                    epistemic_status="inferred",
                    payload={"triggers": decision.triggers, "reasons": decision.reasons},
                ),
            )
            logger.debug(f"run_goal({gid}): replanning on triggers {decision.triggers}")
            continue

        status = "converged"
        break

    _emit(
        log,
        TeamEvent(
            event_type="goal.terminated",
            actor="lead",
            goal_id=gid,
            thread_id=thread_id,
            epistemic_status="accepted",
            payload={"status": status, "iterations": iterations, "replans": replans},
        ),
    )

    transitions = [e.event_type for e in log.read()]
    return GoalResult(
        goal_id=gid,
        status=status,
        iterations=iterations,
        replans=replans,
        event_log_path=str(layout.event_log_path()),
        transitions=transitions,
    )


def run_goal_sync(
    goal: str,
    *,
    layout: TeamsLayout,
    invocation_state: dict[str, Any] | None = None,
    dispatch: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    engine: DurableStep | None = None,
    max_iterations: int = 8,
    budget_threshold: float = 0.0,
    goal_id: str | None = None,
    thread_id: str = "",
) -> GoalResult:
    """Synchronous wrapper around :func:`run_goal` (AC-5-2).

    Mirrors :func:`~strands_teams.runner.run_teams_sync`: the single ``asyncio.run``
    boundary. Never call this from inside an already-running event loop — use the
    async :func:`run_goal` there (``research-coding-runtimes.yaml`` line 18).
    """
    return asyncio.run(
        run_goal(
            goal,
            layout=layout,
            invocation_state=invocation_state,
            dispatch=dispatch,
            engine=engine,
            max_iterations=max_iterations,
            budget_threshold=budget_threshold,
            goal_id=goal_id,
            thread_id=thread_id,
        ),
    )
