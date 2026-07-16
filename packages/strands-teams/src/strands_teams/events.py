"""Append-only typed event plane + deterministic replay + replan triggers (spec 006).

This module evolves the untyped ``team_debug.jsonl`` into a real, replayable
event plane (ASTR §6.5, §7, §15.2). It ships three things, all built on the
:class:`~strands_teams.models.TeamEvent` type:

- :class:`EventLog` — an append-only log bound to a single ``TeamsLayout``-resolved
  path (:meth:`~strands_teams.layout.TeamsLayout.event_log_path`). It exposes only
  ``append`` and read operations — no update or delete — so an already-written
  event can never be mutated (AC-2-5). ``append`` writes exactly one line and
  leaves every prior line byte-for-byte unchanged (AC-2-2); an ``OSError`` is
  logged and swallowed, never raised across the caller (AC-2-4), preserving the
  never-raises contract of the pre-split ``_write_debug_entry``.
- :func:`replay` — reconstructs the run's state transitions, artifacts, findings,
  and decisions from an ``EventLog`` alone, with no live model/Bedrock call
  (AC-3-1). Deterministic across two runs (AC-3-2); applies each event only after
  all its causal parents (AC-3-3); reflects superseding outcomes while retaining
  the superseded originals (AC-3-4); skips a malformed line with a recorded
  warning rather than raising (AC-3-5).
- :func:`should_replan` — a predicate over the event log firing on any of the five
  ASTR replan triggers (AC-6-1..AC-6-7).

The event plane carries **no chain-of-thought or raw transcript** (plan §5;
research-coding-runtimes.yaml line 99): a :class:`~strands_teams.models.TeamEvent`
is ``extra="forbid"``, so a transcript field is rejected at construction.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import Field

from .models import FrozenModel, TeamEvent

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

# ---------------------------------------------------------------------------
# Replan trigger vocabulary (ASTR §9.2; AC-6-1).
# ---------------------------------------------------------------------------

TRIGGER_REQUIREMENT_CHANGED = "requirement.changed"
TRIGGER_CRITICAL_FINDING = "critical_finding.created"
TRIGGER_TASK_FAILED = "task.failed"
TRIGGER_BUDGET_EXCEEDED = "budget.threshold_exceeded"
TRIGGER_ARTIFACT_INTERFACE_CHANGED = "artifact.interface_changed"

REPLAN_TRIGGERS: tuple[str, ...] = (
    TRIGGER_REQUIREMENT_CHANGED,
    TRIGGER_CRITICAL_FINDING,
    TRIGGER_TASK_FAILED,
    TRIGGER_BUDGET_EXCEEDED,
    TRIGGER_ARTIFACT_INTERFACE_CHANGED,
)
"""The five ASTR replan triggers, in canonical order."""

_FAILURE_STATUSES = frozenset({"failed", "timeout", "error"})
"""Dispatch statuses that fire the ``task.failed`` trigger (AC-6-3)."""

_DEFAULT_INTERFACE_FACETS = frozenset({"interface", "api", "signature", "schema", "contract"})
"""Facet names treated as interface facets for ``artifact.interface_changed`` (AC-6-5)."""


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


class EventLog:
    """An append-only, replayable log of :class:`~strands_teams.models.TeamEvent`.

    Bound to a single file path (resolved from
    :meth:`~strands_teams.layout.TeamsLayout.event_log_path`), writing one
    JSON-encoded event per line (AC-2-1). The API is deliberately append + read
    only — there is no update or delete method — so an already-written event is
    immutable (AC-2-5). Supersession is expressed by appending a new event, never
    by editing an old line (AC-3-4).
    """

    def __init__(self, path: Path) -> None:
        """Bind the log to ``path`` (created lazily on first :meth:`append`)."""
        self._path = path

    @property
    def path(self) -> Path:
        """The file this log appends to and reads from."""
        return self._path

    def append(self, event: TeamEvent) -> None:
        """Append exactly one JSON line for ``event``; never raise (AC-2-2, AC-2-4).

        Serializes ``event`` to a single line via
        :meth:`~pydantic.BaseModel.model_dump_json` and appends it, leaving every
        previously written line byte-for-byte unchanged. An ``OSError`` (e.g. a
        read-only filesystem) is logged and swallowed so a log-write failure never
        kills the coordinator or the lead loop (AC-2-4), matching the never-raises
        contract of the pre-split ``_write_debug_entry`` (``teams.py``).
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = event.model_dump_json()
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:  # AC-2-4: log and continue, never raise.
            logger.debug(f"EventLog.append failed for {self._path}: {e}")

    def _read_raw_lines(self) -> list[str]:
        """Return the log's non-blank raw lines (empty when the file does not exist)."""
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as e:
            logger.debug(f"EventLog read failed for {self._path}: {e}")
            return []
        return [line for line in text.splitlines() if line.strip()]

    def read(self) -> list[TeamEvent]:
        """Parse and return every valid event, skipping malformed lines.

        A malformed line is skipped; use :func:`replay` when you need the
        malformed-line warnings surfaced structurally (AC-3-5).
        """
        events, _ = parse_lines(self._read_raw_lines())
        return events

    def read_with_warnings(self) -> tuple[list[TeamEvent], list[str]]:
        """Return ``(events, warnings)`` — one warning per skipped malformed line."""
        return parse_lines(self._read_raw_lines())


def parse_lines(lines: Iterable[str]) -> tuple[list[TeamEvent], list[str]]:
    """Parse JSONL ``lines`` into events, collecting a warning per malformed line.

    Deterministic: each line is validated as a :class:`~strands_teams.models.TeamEvent`;
    a line that is not valid JSON or not a valid event is skipped and a warning
    string (naming the 1-based line number) is recorded rather than raised
    (AC-3-5). Returns ``(events, warnings)`` in stable line order.
    """
    events: list[TeamEvent] = []
    warnings: list[str] = []
    for i, line in enumerate(lines, start=1):
        try:
            events.append(TeamEvent.model_validate_json(line))
        except (ValueError, TypeError) as e:
            warnings.append(f"line {i}: skipped malformed event ({type(e).__name__})")
    return events, warnings


# ---------------------------------------------------------------------------
# Replay + reconstructed state
# ---------------------------------------------------------------------------


class ReplayState(FrozenModel):
    """The deterministic reconstruction produced by :func:`replay` (AC-3-1, AC-3-2).

    Immutable. :meth:`to_canonical_json` yields a byte-stable serialization so two
    replays of the same log compare byte-for-byte identical (AC-3-2, AC-7-1).

    Attributes:
        applied: ``event_id``s in causal-apply order (AC-3-3).
        transitions: ``event_type``s in causal-apply order — the state transitions.
        artifacts: ``artifact_binding -> event_id`` of the latest applied,
            non-superseded event touching that binding.
        findings: Finding payloads reconstructed from finding-bearing events, keyed
            by ``event_id``.
        decisions: Decision payloads reconstructed from decision-bearing events,
            keyed by ``event_id``.
        superseded: ``event_id``s reflected as superseded (originals retained in
            the log — supersession never deletes, AC-3-4).
        warnings: One entry per malformed log line skipped during replay (AC-3-5).
    """

    applied: list[str] = Field(default_factory=list, description="event_ids in causal-apply order")
    transitions: list[str] = Field(default_factory=list, description="event_types in causal-apply order")
    artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="artifact_binding -> event_id of the latest non-superseded touching event",
    )
    findings: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Reconstructed finding payloads keyed by event_id",
    )
    decisions: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Reconstructed decision payloads keyed by event_id",
    )
    superseded: list[str] = Field(default_factory=list, description="event_ids reflected as superseded")
    warnings: list[str] = Field(default_factory=list, description="Malformed-line skip warnings")

    def to_canonical_json(self) -> str:
        """Return a byte-stable JSON serialization for cross-replay equality (AC-3-2)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def _causal_order(events: list[TeamEvent]) -> tuple[list[TeamEvent], list[str]]:
    """Order events so each is applied only after all its causal parents (AC-3-3).

    A deterministic topological sort keyed by the log's original order: an event is
    emitted as soon as every one of its ``causal_parents`` present in the log has
    already been emitted. Parents that are not in the log are ignored (they cannot
    block). A causal cycle (or a parent that never becomes available) is broken
    deterministically by emitting the remaining events in original log order, so
    replay always terminates and never raises. Returns ``(ordered, warnings)``.
    """
    by_id: dict[str, TeamEvent] = {e.event_id: e for e in events}
    known = set(by_id)
    emitted: set[str] = set()
    ordered: list[TeamEvent] = []
    warnings: list[str] = []

    remaining = list(events)
    while remaining:
        progressed = False
        still: list[TeamEvent] = []
        for event in remaining:
            parents_in_log = [p for p in event.causal_parents if p in known]
            if all(p in emitted for p in parents_in_log):
                ordered.append(event)
                emitted.add(event.event_id)
                progressed = True
            else:
                still.append(event)
        remaining = still
        if not progressed:
            # A cycle or unresolvable parent — break deterministically in log order.
            warnings.append(
                f"causal ordering could not resolve {len(remaining)} event(s); applied in log order",
            )
            ordered.extend(remaining)
            break
    return ordered, warnings


def _is_finding_event(event: TeamEvent) -> bool:
    """Whether an event carries a reconstructable finding payload."""
    return event.event_type.startswith("finding") or "finding" in event.payload


def _is_decision_event(event: TeamEvent) -> bool:
    """Whether an event carries a reconstructable decision payload."""
    return event.event_type.startswith("decision") or "decision" in event.payload


def replay(events: Iterable[TeamEvent], *, warnings: Iterable[str] = ()) -> ReplayState:
    """Reconstruct run state from events with no live model call (AC-3-1..AC-3-5).

    Applies each event only after all its causal parents (:func:`_causal_order`,
    AC-3-3), records the transition sequence, tracks the latest non-superseded
    event per artifact binding, reconstructs finding and decision payloads, and
    reflects supersession (an event bearing ``epistemic_status="superseded"`` or an
    event naming another in its ``causal_parents`` with an
    ``epistemic_status`` in ``{accepted, rejected, superseded}`` supersedes that
    parent) while retaining the originals in the log (AC-3-4).

    Deterministic: given the same events (and malformed-line ``warnings``), the
    returned :class:`ReplayState` is identical across calls (AC-3-2).

    Args:
        events: The parsed events to replay (typically ``EventLog.read()``).
        warnings: Malformed-line warnings from parsing, folded into the result so
            :meth:`ReplayState.to_canonical_json` reflects them (AC-3-5).

    Returns:
        A :class:`ReplayState` reconstruction.
    """
    event_list = list(events)
    ordered, order_warnings = _causal_order(event_list)

    applied: list[str] = []
    transitions: list[str] = []
    artifacts: dict[str, str] = {}
    findings: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    superseded: set[str] = set()

    for event in ordered:
        applied.append(event.event_id)
        transitions.append(event.event_type)

        # Supersession (AC-3-4): a superseded-status event marks itself; an
        # accepted/rejected/superseded event supersedes the parents it references.
        if event.epistemic_status == "superseded":
            superseded.add(event.event_id)
        if event.epistemic_status in {"accepted", "rejected", "superseded"}:
            superseded.update(event.causal_parents)

        if _is_finding_event(event):
            findings[event.event_id] = dict(event.payload)
        if _is_decision_event(event):
            decisions[event.event_id] = dict(event.payload)

    # Artifacts: latest non-superseded event that touches each binding, in apply order.
    for event in ordered:
        if event.event_id in superseded:
            continue
        for binding in event.artifact_bindings:
            artifacts[binding] = event.event_id

    return ReplayState(
        applied=applied,
        transitions=transitions,
        artifacts=artifacts,
        findings=findings,
        decisions=decisions,
        superseded=sorted(superseded),
        warnings=[*warnings, *order_warnings],
    )


def replay_log(log: EventLog) -> ReplayState:
    """Replay an :class:`EventLog` end to end, surfacing malformed-line warnings (AC-3-5)."""
    events, warnings = log.read_with_warnings()
    return replay(events, warnings=warnings)


# ---------------------------------------------------------------------------
# Replan predicate
# ---------------------------------------------------------------------------


class ReplanDecision(FrozenModel):
    """The structured outcome of :func:`should_replan` (AC-6-1).

    Attributes:
        replan: Whether any replan trigger fired.
        triggers: The distinct triggers that fired, in canonical order (empty when
            ``replan`` is False).
        reasons: Human-readable reason per fired trigger.
    """

    replan: bool = Field(default=False, description="Whether any replan trigger fired")
    triggers: list[str] = Field(default_factory=list, description="Triggers that fired, in canonical order")
    reasons: list[str] = Field(default_factory=list, description="Human-readable reason per fired trigger")


def _finding_is_critical(payload: dict[str, Any]) -> bool:
    """Whether a finding payload is severity=critical or blocking (AC-6-2)."""
    finding = payload.get("finding", payload)
    if not isinstance(finding, dict):
        return False
    return finding.get("severity") == "critical" or bool(finding.get("blocking"))


def _facets_are_interface(facets: Iterable[str]) -> bool:
    """Whether any changed facet is an interface facet (AC-6-5)."""
    return any(str(f).lower() in _DEFAULT_INTERFACE_FACETS for f in facets)


def _fires_requirement_changed(event: TeamEvent) -> bool:
    """Explicit ``requirement.changed`` type or a requirement/input-change payload (AC-6-6)."""
    return event.event_type == TRIGGER_REQUIREMENT_CHANGED or bool(event.payload.get("requirement_changed"))


def _fires_critical_finding(event: TeamEvent) -> bool:
    """Explicit ``critical_finding.created`` type or a critical/blocking finding payload (AC-6-2)."""
    if event.event_type == TRIGGER_CRITICAL_FINDING:
        return True
    return _is_finding_event(event) and _finding_is_critical(event.payload)


def _fires_task_failed(event: TeamEvent) -> bool:
    """Explicit ``task.failed`` type or a dispatch payload reporting failed/timeout (AC-6-3)."""
    if event.event_type == TRIGGER_TASK_FAILED:
        return True
    return str(event.payload.get("status", "")).lower() in _FAILURE_STATUSES


def _fires_interface_changed(event: TeamEvent) -> bool:
    """Explicit ``artifact.interface_changed`` type or a revision touching an interface facet (AC-6-5)."""
    if event.event_type == TRIGGER_ARTIFACT_INTERFACE_CHANGED:
        return True
    changed = event.payload.get("changed_facets")
    return isinstance(changed, (list, tuple, set)) and _facets_are_interface(changed)


def _event_fires(event: TeamEvent) -> tuple[str, str] | None:
    """Return ``(trigger, reason)`` if ``event`` fires a replan trigger, else ``None``.

    Matches on the event's own type first (a caller can emit an explicit
    ``requirement.changed`` / ``artifact.interface_changed`` event), then on the
    structured payload of a generic dispatch/finding/revision event. The cumulative
    budget trigger is evaluated separately by :func:`_budget_exceeded`, so this
    predicate covers only the per-event triggers plus an explicit
    ``budget.threshold_exceeded`` event.
    """
    checks: tuple[tuple[bool, str, str], ...] = (
        (_fires_requirement_changed(event), TRIGGER_REQUIREMENT_CHANGED, "goal requirement or input changed"),
        (_fires_critical_finding(event), TRIGGER_CRITICAL_FINDING, "finding is severity=critical or blocking"),
        (_fires_task_failed(event), TRIGGER_TASK_FAILED, "dispatch status failed/timeout"),
        (event.event_type == TRIGGER_BUDGET_EXCEEDED, TRIGGER_BUDGET_EXCEEDED, "budget-threshold event"),
        (_fires_interface_changed(event), TRIGGER_ARTIFACT_INTERFACE_CHANGED, "revision changed an interface facet"),
    )
    for fired, trigger, reason in checks:
        if fired:
            return trigger, reason
    return None


def _budget_exceeded(events: list[TeamEvent], *, budget_threshold: float) -> bool:
    """Whether cumulative recorded ``cost_usd`` crosses ``budget_threshold`` (AC-6-4)."""
    if budget_threshold <= 0:
        return False
    total = 0.0
    for event in events:
        usage = event.payload.get("resource_usage")
        if isinstance(usage, dict):
            total += float(usage.get("cost_usd", 0.0) or 0.0)
        total += float(event.payload.get("cost_usd", 0.0) or 0.0)
    return total >= budget_threshold


def should_replan(events: Iterable[TeamEvent], *, budget_threshold: float = 0.0) -> ReplanDecision:
    """Decide whether the lead loop should replan (AC-6-1..AC-6-7).

    Scans the event log for any of the five ASTR triggers — ``requirement.changed``,
    ``critical_finding.created``, ``task.failed``, ``budget.threshold_exceeded``,
    ``artifact.interface_changed`` — matching on either an explicit trigger-typed
    event or the structured payload of a generic dispatch/finding/revision event.
    Cumulative recorded cost crossing ``budget_threshold`` fires the budget trigger
    (AC-6-4). Returns ``replan=False`` with no triggers on a trigger-free log
    (AC-6-7).

    Args:
        events: The events to evaluate (typically ``EventLog.read()``).
        budget_threshold: The configured cumulative-cost bound; ``0`` disables the
            cumulative budget check (an explicit ``budget.threshold_exceeded`` event
            still fires).

    Returns:
        A :class:`ReplanDecision` naming the distinct triggers that fired.
    """
    event_list = list(events)
    fired: dict[str, str] = {}

    for event in event_list:
        hit = _event_fires(event)
        if hit is not None:
            trigger, reason = hit
            fired.setdefault(trigger, reason)

    if _budget_exceeded(event_list, budget_threshold=budget_threshold):
        fired.setdefault(TRIGGER_BUDGET_EXCEEDED, f"cumulative cost crossed threshold {budget_threshold}")

    ordered = [t for t in REPLAN_TRIGGERS if t in fired]
    return ReplanDecision(
        replan=bool(ordered),
        triggers=ordered,
        reasons=[fired[t] for t in ordered],
    )
