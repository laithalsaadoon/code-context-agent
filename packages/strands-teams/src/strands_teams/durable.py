"""Engine-agnostic durable-step contract + in-process asyncio engine (spec 006).

A **durable step** is one lead-loop transition (decompose, compose, assign,
dispatch, integrate, replan) whose outcome is recorded as a
:class:`~strands_teams.models.TeamEvent` so replay can reconstruct it
deterministically (ASTR §9.2). This module defines:

- :class:`DurableStep` — a ``Protocol`` describing the engine-agnostic contract
  for executing one step, so a Temporal or Restate worker can implement it later
  without changing the ``run_goal`` call sites (AC-4-1).
- :class:`AsyncioDurableEngine` — an in-process ``asyncio`` implementation that
  runs steps in the current event loop (AC-4-2), emits a transition
  :class:`~strands_teams.models.TeamEvent` to the :class:`~strands_teams.events.EventLog`
  before returning (AC-4-3), returns a recorded result without re-running side
  effects when a result event for the step already exists — idempotent replay
  (AC-4-4), and never calls ``asyncio.run`` or otherwise blocks the loop
  (AC-4-5; research-coding-runtimes.yaml line 18).

The engine imports no coding-agent SDK and no ``subprocess``; it is domain- and
vendor-free (plan §3).
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from loguru import logger
from pydantic import Field

from .models import FrozenModel, TeamEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .events import EventLog

# The event_type prefix under which a durable step records its transition result.
STEP_RESULT_EVENT_PREFIX = "step.result"
"""``TeamEvent.event_type`` for a recorded durable-step result (idempotency key base)."""


class StepResult(FrozenModel):
    """The recorded outcome of one durable step (AC-4-3, AC-4-4).

    Attributes:
        step_id: Stable idempotency key for the step within a goal run — a step
            re-executed during replay with a matching recorded result is not
            re-run (AC-4-4).
        step_type: The lead-loop transition this step performed (e.g. ``decompose``).
        payload: The structured result of the step (no transcript/CoT).
        replayed: True when this result was returned from a prior recorded event
            rather than freshly executed (AC-4-4).
    """

    step_id: str = Field(description="Stable idempotency key for the step within a goal run")
    step_type: str = Field(description="The lead-loop transition this step performed")
    payload: dict[str, Any] = Field(default_factory=dict, description="Structured result of the step")
    replayed: bool = Field(default=False, description="Whether the result came from a recorded event (replay)")


@runtime_checkable
class DurableStep(Protocol):
    """Engine-agnostic contract for executing one lead-loop transition (AC-4-1).

    An implementation drives one durable step to completion and records its
    outcome as a :class:`~strands_teams.models.TeamEvent` so the step is
    replayable. The in-process :class:`AsyncioDurableEngine` satisfies this today;
    a Temporal or Restate worker can implement the same method later without
    changing ``run_goal``'s call sites (plan §4 Phase 5; AC-4-1). A
    ``runtime_checkable`` Protocol checks method *presence* only.
    """

    async def run_step(
        self,
        step_id: str,
        step_type: str,
        fn: Callable[[], Awaitable[dict[str, Any]] | dict[str, Any]],
        *,
        goal_id: str = "",
        task_id: str = "",
        thread_id: str = "",
        actor: str = "lead",
        causal_parents: list[str] | None = None,
    ) -> StepResult:
        """Execute one step and record its transition result; return the outcome."""
        ...


class AsyncioDurableEngine:
    """In-process ``asyncio`` :class:`DurableStep` engine (AC-4-2..AC-4-5).

    Runs each step's callable in the current event loop, emitting a result
    :class:`~strands_teams.models.TeamEvent` to the bound
    :class:`~strands_teams.events.EventLog` before returning (AC-4-3). Before
    running, it scans the log for an existing result event whose payload names the
    same ``step_id``; if found, it returns that recorded result without re-running
    the callable — idempotent replay (AC-4-4). It never calls ``asyncio.run`` and
    awaits (rather than blocks on) its callables, so it is safe inside an
    already-running strands runtime (AC-4-5; research-coding-runtimes.yaml line 18).
    """

    def __init__(self, event_log: EventLog) -> None:
        """Bind the engine to the run's :class:`~strands_teams.events.EventLog`."""
        self._event_log = event_log

    def _recorded_result(self, step_id: str) -> StepResult | None:
        """Return a previously recorded result for ``step_id``, or ``None`` (AC-4-4).

        Scans the event log for a ``step.result`` event whose payload names this
        ``step_id``. Because the log is append-only and replay-deterministic, a
        matching recorded result means the step's side effects already happened, so
        the engine must not re-run them.
        """
        for event in self._event_log.read():
            if event.event_type == STEP_RESULT_EVENT_PREFIX and event.payload.get("step_id") == step_id:
                return StepResult(
                    step_id=step_id,
                    step_type=str(event.payload.get("step_type", "")),
                    payload=dict(event.payload.get("result", {})),
                    replayed=True,
                )
        return None

    async def run_step(
        self,
        step_id: str,
        step_type: str,
        fn: Callable[[], Awaitable[dict[str, Any]] | dict[str, Any]],
        *,
        goal_id: str = "",
        task_id: str = "",
        thread_id: str = "",
        actor: str = "lead",
        causal_parents: list[str] | None = None,
    ) -> StepResult:
        """Execute (or replay) one durable step (AC-4-3, AC-4-4, AC-4-5).

        If a result event for ``step_id`` already exists in the log, returns it
        without re-running ``fn`` (idempotent replay, AC-4-4). Otherwise awaits
        ``fn`` (which may be sync or async — an async callable is awaited, a sync
        one is called directly; neither blocks the loop via ``asyncio.run``,
        AC-4-5), emits a ``step.result`` :class:`~strands_teams.models.TeamEvent`
        recording the transition and result to the log (AC-4-3), and returns the
        fresh :class:`StepResult`.

        Args:
            step_id: Stable idempotency key for the step within the goal run.
            step_type: The lead-loop transition (e.g. ``decompose``).
            fn: A zero-arg callable producing the step's result payload dict; may be
                sync or async.
            goal_id: The goal this step belongs to.
            task_id: The task this step belongs to.
            thread_id: The thread this step belongs to.
            actor: Identifier of the step's executor (defaults to ``"lead"``).
            causal_parents: ``event_id``s this step's event causally follows.

        Returns:
            A :class:`StepResult` — ``replayed=True`` when returned from a recorded
            event, else the freshly executed result.
        """
        recorded = self._recorded_result(step_id)
        if recorded is not None:
            logger.debug(f"AsyncioDurableEngine: step {step_id!r} already recorded — replaying without re-running")
            return recorded

        outcome = fn()
        awaited = await outcome if inspect.isawaitable(outcome) else outcome
        payload = cast("dict[str, Any]", awaited)
        result_payload: dict[str, Any] = dict(payload) if payload else {}

        event = TeamEvent(
            event_type=STEP_RESULT_EVENT_PREFIX,
            actor=actor,
            goal_id=goal_id,
            task_id=task_id,
            thread_id=thread_id,
            causal_parents=list(causal_parents or []),
            epistemic_status="observed",
            payload={"step_id": step_id, "step_type": step_type, "result": result_payload},
        )
        self._event_log.append(event)
        return StepResult(step_id=step_id, step_type=step_type, payload=result_payload, replayed=False)
