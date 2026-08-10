"""P5 durable-lead-events tests (spec 006, User Story 7).

Covers AC-7-1 (append+replay determinism, byte-identical across two replays),
AC-7-2 (each of the five replan triggers fires exactly, none on a trigger-free
log), AC-7-3 (``run_goal`` happy path with a stubbed dispatch reaching a terminal
status with the expected ``TeamEvent`` sequence), AC-7-4 (no Bedrock/network call
— everything runs through injected stubs + the in-process engine), and AC-7-5
(the durable plane imports from the package root).

No live model: dispatch is injected as an async stub and the engine is the
in-process :class:`~strands_teams.durable.AsyncioDurableEngine`. Any real Bedrock
call would require ``dispatch_team``'s Swarm, which is never reached here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from strands import ToolContext

import strands_teams
from strands_teams import (
    EventLog,
    TeamEvent,
    TeamsLayout,
    replay,
    run_goal,
    run_goal_sync,
    should_replan,
    teams,
)
from strands_teams.events import (
    TRIGGER_ARTIFACT_INTERFACE_CHANGED,
    TRIGGER_BUDGET_EXCEEDED,
    TRIGGER_CRITICAL_FINDING,
    TRIGGER_REQUIREMENT_CHANGED,
    TRIGGER_TASK_FAILED,
)
from strands_teams.rubric import DEFAULT_NARRATIVE_RUBRIC


@pytest.fixture
def layout(tmp_path: Path) -> TeamsLayout:
    return TeamsLayout(output_root=tmp_path / ".teams")


class _StubStatus:
    value = "completed"


class _StubNode:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id


class _StubSwarmResult:
    def __init__(self, node_ids: list[str]) -> None:
        self.status = _StubStatus()
        self.node_history = [_StubNode(n) for n in node_ids]


class _StubSwarm:
    """A Swarm stub that returns a completed result without any Bedrock call."""

    def __init__(self, nodes: list[Any], **kwargs: Any) -> None:
        self.nodes = nodes
        self.kwargs = kwargs

    async def invoke_async(self, task: str, invocation_state: dict[str, Any] | None = None) -> _StubSwarmResult:
        return _StubSwarmResult([getattr(n, "name", "?") for n in self.nodes])


class _RaisingSwarm(_StubSwarm):
    async def invoke_async(self, task: str, invocation_state: dict[str, Any] | None = None) -> _StubSwarmResult:
        raise RuntimeError("boom in swarm")


# ---------------------------------------------------------------------------
# AC-7-5 — the durable plane is importable from the package root.
# ---------------------------------------------------------------------------


def test_durable_plane_exports_from_package_root() -> None:
    for name in (
        "TeamEvent",
        "EventLog",
        "run_goal",
        "run_goal_sync",
        "DurableStep",
        "replay",
        "should_replan",
    ):
        assert name in strands_teams.__all__, f"{name} missing from __all__"
        assert hasattr(strands_teams, name), f"{name} not importable from package root"


# ---------------------------------------------------------------------------
# AC-4-4 — durable step idempotent replay (recorded result returned, fn not re-run).
# ---------------------------------------------------------------------------


async def test_run_step_replays_recorded_result_without_rerunning(layout: TeamsLayout) -> None:
    from strands_teams.durable import AsyncioDurableEngine

    log = EventLog(layout.event_log_path())
    engine = AsyncioDurableEngine(log)
    calls = {"n": 0}

    async def _fn() -> dict[str, Any]:
        calls["n"] += 1
        return {"value": calls["n"]}

    first = await engine.run_step("g:0:decompose", "decompose", _fn, goal_id="g")
    assert first.replayed is False
    assert first.payload == {"value": 1}

    # Same step_id on the same log: recorded result is returned; fn is NOT re-invoked.
    second = await engine.run_step("g:0:decompose", "decompose", _fn, goal_id="g")
    assert second.replayed is True
    assert second.payload == {"value": 1}
    assert calls["n"] == 1  # fn ran exactly once — the side effect did not repeat


# ---------------------------------------------------------------------------
# AC-7-1 — append + replay determinism (byte-identical across two replays).
# ---------------------------------------------------------------------------


def test_append_and_replay_is_byte_identical_across_two_replays(layout: TeamsLayout) -> None:
    log = EventLog(layout.event_log_path())
    e1 = TeamEvent(event_type="goal.started", actor="lead", goal_id="g1", payload={"goal": "ship it"})
    e2 = TeamEvent(
        event_type="team.dispatched",
        actor="team-a",
        goal_id="g1",
        causal_parents=[e1.event_id],
        payload={"team_id": "team-a", "status": "completed", "duration_seconds": 1.0},
    )
    e3 = TeamEvent(
        event_type="finding.created",
        actor="team-a",
        goal_id="g1",
        causal_parents=[e2.event_id],
        payload={"finding": {"severity": "high", "blocking": False, "statement": "x"}},
    )
    for e in (e1, e2, e3):
        log.append(e)

    # AC-2-2: exactly one line per event, prior lines untouched.
    lines = layout.event_log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3

    events = log.read()
    first = replay(events).to_canonical_json()
    second = replay(events).to_canonical_json()
    assert first == second  # AC-3-2 / AC-7-1: deterministic, byte-identical.

    # Causal order honored (AC-3-3): parent before child.
    state = replay(events)
    assert state.applied.index(e1.event_id) < state.applied.index(e2.event_id)
    assert state.applied.index(e2.event_id) < state.applied.index(e3.event_id)


def test_replay_skips_malformed_line_with_warning(layout: TeamsLayout) -> None:
    path = layout.event_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    good = TeamEvent(event_type="goal.started", actor="lead", goal_id="g1")
    path.write_text(good.model_dump_json() + "\n" + "{not json}\n", encoding="utf-8")

    events, warnings = EventLog(path).read_with_warnings()
    assert len(events) == 1
    assert len(warnings) == 1  # AC-3-5: malformed line skipped with a warning, no raise.


def test_replay_reflects_supersession_but_retains_original(layout: TeamsLayout) -> None:
    original = TeamEvent(event_type="finding.created", goal_id="g1", payload={"finding": {"severity": "low"}})
    superseding = TeamEvent(
        event_type="finding.revised",
        goal_id="g1",
        epistemic_status="accepted",
        causal_parents=[original.event_id],
    )
    state = replay([original, superseding])
    assert original.event_id in state.superseded  # AC-3-4: superseded reflected.
    assert original.event_id in state.applied  # original retained, never deleted.


# ---------------------------------------------------------------------------
# AC-7-2 — each of the five replan triggers fires exactly; none on a clean log.
# ---------------------------------------------------------------------------


def test_no_trigger_on_empty_or_trigger_free_log() -> None:
    assert should_replan([]).replan is False  # AC-6-7
    benign = TeamEvent(event_type="team.dispatched", payload={"team_id": "a", "status": "completed"})
    assert should_replan([benign]).replan is False


def test_requirement_changed_trigger_fires() -> None:
    e = TeamEvent(event_type=TRIGGER_REQUIREMENT_CHANGED, payload={"requirement_changed": True})
    decision = should_replan([e])
    assert decision.replan is True
    assert decision.triggers == [TRIGGER_REQUIREMENT_CHANGED]


def test_critical_finding_trigger_fires_on_severity_and_blocking() -> None:
    crit = TeamEvent(event_type="finding.created", payload={"finding": {"severity": "critical"}})
    assert should_replan([crit]).triggers == [TRIGGER_CRITICAL_FINDING]

    blocking = TeamEvent(event_type="finding.created", payload={"finding": {"severity": "low", "blocking": True}})
    assert should_replan([blocking]).triggers == [TRIGGER_CRITICAL_FINDING]


def test_task_failed_trigger_fires_on_failed_and_timeout() -> None:
    for status in ("failed", "timeout", "error"):
        e = TeamEvent(event_type="team.dispatched", payload={"team_id": "a", "status": status})
        assert should_replan([e]).triggers == [TRIGGER_TASK_FAILED], status


def test_budget_threshold_trigger_fires_on_cumulative_cost() -> None:
    e1 = TeamEvent(event_type="team.dispatched", payload={"resource_usage": {"cost_usd": 0.6}})
    e2 = TeamEvent(event_type="team.dispatched", payload={"cost_usd": 0.6})
    decision = should_replan([e1, e2], budget_threshold=1.0)
    assert TRIGGER_BUDGET_EXCEEDED in decision.triggers
    # Below threshold does not fire (AC-6-4).
    assert should_replan([e1], budget_threshold=1.0).replan is False


def test_artifact_interface_changed_trigger_fires() -> None:
    e = TeamEvent(event_type="artifact.revised", payload={"changed_facets": ["interface", "docs"]})
    assert should_replan([e]).triggers == [TRIGGER_ARTIFACT_INTERFACE_CHANGED]


# ---------------------------------------------------------------------------
# AC-7-3 / AC-7-4 — run_goal happy path with a stubbed dispatch (no Bedrock).
# ---------------------------------------------------------------------------


async def test_run_goal_happy_path_reaches_terminal_status_with_expected_events(layout: TeamsLayout) -> None:
    dispatched: list[dict[str, Any]] = []

    async def stub_dispatch(assignment: dict[str, Any], invocation_state: dict[str, Any]) -> dict[str, Any]:
        # AC-5-4: goal/task/thread ids + layout propagated by reference.
        assert invocation_state["layout"] is layout
        assert invocation_state["goal_id"] == "goal-1"
        assert invocation_state["task_id"] == assignment["task_id"]
        dispatched.append(assignment)
        return {"team_id": assignment["team_id"], "status": "completed", "duration_seconds": 0.0}

    result = await run_goal(
        "ship the feature",
        layout=layout,
        dispatch=stub_dispatch,
        goal_id="goal-1",
        max_iterations=4,
    )

    assert result.status == "converged"  # AC-5-6 terminal status.
    assert result.iterations == 1  # converged after one clean pass.
    assert len(dispatched) == 1

    # AC-5-3: the five ordered stages each recorded a step.result event, bracketed
    # by goal.started / goal.terminated.
    transitions = result.transitions
    assert transitions[0] == "goal.started"
    assert transitions[-1] == "goal.terminated"
    assert transitions.count("step.result") == 5


async def test_run_goal_replans_on_task_failure_then_terminates(layout: TeamsLayout) -> None:
    calls = {"n": 0}

    async def flaky_dispatch(assignment: dict[str, Any], invocation_state: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        status = "failed" if calls["n"] == 1 else "completed"
        return {"team_id": assignment["team_id"], "status": status, "duration_seconds": 0.0}

    result = await run_goal(
        "ship it",
        layout=layout,
        dispatch=flaky_dispatch,
        goal_id="g-replan",
        max_iterations=5,
    )

    assert result.replans >= 1  # AC-5-7: a failed dispatch triggers replan re-entry.
    assert "goal.replan" in result.transitions
    assert result.status in {"converged", "max_iterations"}


async def test_run_goal_replan_is_edge_triggered_not_level_triggered(layout: TeamsLayout) -> None:
    """A dispatch that fails ONCE then recovers must converge, not burn to max_iterations.

    Regression for the level-triggered replan bug: ``should_replan`` was evaluated
    over the whole cumulative append-only log every iteration, so iteration 1's
    ``task.failed`` re-fired the replan trigger on every subsequent iteration and
    the loop could never converge (it always terminated at ``max_iterations`` with a
    replan per iteration). The replan check is now edge-triggered — it sees only the
    events appended during the current iteration (AC-5-7).
    """
    calls = {"n": 0}

    async def fail_once_then_recover(assignment: dict[str, Any], invocation_state: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        status = "failed" if calls["n"] == 1 else "completed"
        return {"team_id": assignment["team_id"], "status": status, "duration_seconds": 0.0}

    result = await run_goal(
        "recover after one flake",
        layout=layout,
        dispatch=fail_once_then_recover,
        goal_id="g-edge",
        max_iterations=8,
    )

    # Iteration 1 fails → one replan. Iteration 2 succeeds → converged. The stale
    # iteration-1 failure must NOT keep re-firing replan on iterations 2..N.
    assert result.status == "converged"
    assert result.replans == 1
    assert result.iterations == 2
    assert calls["n"] == 2


async def test_run_goal_terminates_at_max_iterations_when_never_converging(layout: TeamsLayout) -> None:
    async def always_failing(assignment: dict[str, Any], invocation_state: dict[str, Any]) -> dict[str, Any]:
        return {"team_id": assignment["team_id"], "status": "failed", "duration_seconds": 0.0}

    result = await run_goal(
        "impossible",
        layout=layout,
        dispatch=always_failing,
        goal_id="g-max",
        max_iterations=3,
    )
    assert result.status == "max_iterations"  # AC-5-6: never loops indefinitely.
    assert result.iterations == 3


async def test_run_goal_terminates_on_budget_exceeded(layout: TeamsLayout) -> None:
    async def costly_dispatch(assignment: dict[str, Any], invocation_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "team_id": assignment["team_id"],
            "status": "completed",
            "duration_seconds": 0.0,
            "resource_usage": {"cost_usd": 5.0},
        }

    result = await run_goal(
        "spendy",
        layout=layout,
        dispatch=costly_dispatch,
        goal_id="g-budget",
        max_iterations=10,
        budget_threshold=1.0,
    )
    assert result.status == "budget_exceeded"  # AC-5-6 / AC-6-4.


# ---------------------------------------------------------------------------
# AC-2-3 / AC-2-6 — dispatch_team emits a typed TeamEvent on every path while
# still writing today's debug entry.
# ---------------------------------------------------------------------------


async def test_dispatch_team_emits_event_and_keeps_debug_entry(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    monkeypatch.setattr(teams, "Swarm", _StubSwarm)
    state: dict[str, Any] = {
        "layout": layout,
        "tool_registry": {},
        "node_model_config": {"model_id": "global.anthropic.claude-opus-4-6-v1", "region_name": "us-east-1"},
        "execution_timeout": 300.0,
        "node_timeout": 120.0,
        "use_case_name": "test",
        "rubric": DEFAULT_NARRATIVE_RUBRIC,
        "goal_id": "g-dispatch",
        "thread_id": "th-1",
    }
    ctx = ToolContext(tool_use={"toolUseId": "t", "name": "x", "input": {}}, agent=None, invocation_state=state)

    result = json.loads(
        await teams.dispatch_team.__wrapped__(
            team_id="team-x",
            mandate="Investigate area.",
            agents=[{"name": "analyst", "system_prompt": "You analyze.", "tools": []}],
            tool_context=ctx,
        )
    )
    assert result["status"] == "completed"

    # AC-2-6: the untyped debug entry is still written.
    assert layout.debug_log_path().read_text().strip()

    # AC-2-3: a typed dispatch TeamEvent is on the event plane, carrying team_id/status/duration.
    events = EventLog(layout.event_log_path()).read()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "team.dispatched"
    assert ev.goal_id == "g-dispatch"
    assert ev.thread_id == "th-1"
    assert ev.payload["team_id"] == "team-x"
    assert ev.payload["status"] == "completed"
    assert "duration_seconds" in ev.payload


async def test_dispatch_team_emits_task_failed_status_on_swarm_error(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    monkeypatch.setattr(teams, "Swarm", _RaisingSwarm)
    state: dict[str, Any] = {
        "layout": layout,
        "tool_registry": {},
        "node_model_config": {"model_id": "m", "region_name": "us-east-1"},
        "execution_timeout": 300.0,
        "node_timeout": 120.0,
        "use_case_name": "test",
        "rubric": DEFAULT_NARRATIVE_RUBRIC,
    }
    ctx = ToolContext(tool_use={"toolUseId": "t", "name": "x", "input": {}}, agent=None, invocation_state=state)

    json.loads(
        await teams.dispatch_team.__wrapped__(
            team_id="team-fail",
            mandate="Investigate.",
            agents=[{"name": "a", "system_prompt": "p", "tools": []}],
            tool_context=ctx,
        )
    )
    events = EventLog(layout.event_log_path()).read()
    assert len(events) == 1
    assert events[0].payload["status"] == "error"
    # The error status is what should_replan reads to fire task.failed.
    assert should_replan(events).triggers == [TRIGGER_TASK_FAILED]


def test_run_goal_sync_mirrors_async(layout: TeamsLayout) -> None:
    async def stub_dispatch(assignment: dict[str, Any], invocation_state: dict[str, Any]) -> dict[str, Any]:
        return {"team_id": assignment["team_id"], "status": "completed", "duration_seconds": 0.0}

    result = run_goal_sync("ship", layout=layout, dispatch=stub_dispatch, goal_id="g-sync", max_iterations=2)
    assert result.status == "converged"
    assert result.goal_id == "g-sync"
