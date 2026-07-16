# Tasks — 006-durable-lead-events

Derived from `spec.md` (Phase P5-durable-lead). One task per coherent AC cluster. `[P]` marks
parallel-safe tasks (isolated worktree, no file overlap with other `[P]` tasks in the same wave).
`Dependencies:` lists ACs that must complete before the task starts. Waves are derivable from the
dependency closure.

Cross-phase givens (do NOT implement here): `Finding` (P1, spec `002-typed-findings`) and
`ArtifactRevision` (P4, spec `005-artifact-revisions`) are treated as already present in
`strands_teams.models`. This phase references them only from event payloads and replan triggers.

> Regeneration note: hand-edits here are destroyed on regeneration — edit `spec.md` instead.

---

## Wave 0 — Typed event type (foundation)

### T-TeamEvent — `TeamEvent` FrozenModel + epistemic-status literal
Covers: AC-1-1, AC-1-2, AC-1-3, AC-1-4, AC-1-5
[P]
Dependencies: none
Add `TeamEvent(FrozenModel)` to `strands_teams.models` (`models.py:40`) with all 14 fields; constrain
`epistemic_status` to the 7-value literal; default `event_id` / `created_at` (UTC) / `correlation_id`
(goal_id-or-event_id); `extra="forbid"` rejects transcript/CoT fields; assert JSON single-line
round-trip equality. No chain-of-thought fields (plan §5; `research-coding-runtimes.yaml` line 99).

---

## Wave 1 — Event plane + engine protocol (depend on the type)

### T-EventLog — Append-only `EventLog` + layout path
Covers: AC-2-1, AC-2-2, AC-2-4, AC-2-5
Dependencies: AC-1-1
Add `event_log_path()` to `TeamsLayout` (`layout.py:79`, supersede/co-locate `debug_log_path`) and an
`EventLog` exposing only append + read (no update/delete). `append` writes exactly one line and leaves
prior lines byte-identical; `OSError` is logged and swallowed (never-raises, per `teams.py:183-189`).

### T-DispatchEmit — Emit typed `TeamEvent` from `dispatch_team` on every path
Covers: AC-2-3, AC-2-6
Dependencies: AC-2-1
Replace/augment `_write_debug_entry` (`teams.py:178`, called at `:317`) so every dispatch path
(success/failure/timeout) emits a `TeamEvent` carrying team_id/status/duration. Keep native-`Swarm`
dispatch unchanged (`teams.py:197`; `research-coding-runtimes.yaml` lines 27-31). Preserve the default
`run_teams` debug/read/display behavior (`teams.py:384`, hooks).

### T-DurableStep — `DurableStep` Protocol + asyncio engine
Covers: AC-4-1, AC-4-2, AC-4-5
[P]
Dependencies: AC-1-1
Define the engine-agnostic `DurableStep` `Protocol` (Temporal/Restate-backable later) and an
in-process `asyncio` implementation that runs steps in the current loop and never calls `asyncio.run`
inside a running runtime (`research-coding-runtimes.yaml` line 18; `runner.py:251`).

---

## Wave 2 — Replay + triggers (depend on plane + engine)

### T-Replay — Deterministic causal-order replay
Covers: AC-3-1, AC-3-2, AC-3-3, AC-3-4, AC-3-5
Dependencies: AC-2-1
Replay reconstructs state transitions/artifacts/findings/decisions from the `EventLog` with no live
model call; deterministic across two runs; applies events only after all `causal_parents`; reflects
`superseded` outcomes while retaining originals; skips malformed lines with a warning (no raise).

### T-StepEmit — Durable-step event emission + idempotent replay
Covers: AC-4-3, AC-4-4
Dependencies: AC-4-2, AC-3-1
Each executed durable step emits a transition `TeamEvent` before returning; during replay, a step whose
result event already exists returns the recorded result without re-running side effects.

### T-Replan — `should_replan` predicate + five triggers
Covers: AC-6-1, AC-6-2, AC-6-3, AC-6-4, AC-6-5, AC-6-6, AC-6-7
Dependencies: AC-2-1
`should_replan` evaluates the `EventLog` and fires on `requirement.changed`, `critical_finding.created`
(P1 `Finding` severity=critical or blocking), `task.failed` (dispatch status failed/timeout),
`budget.threshold_exceeded` (new `TeamsSettings` bound, `settings.py:29`), `artifact.interface_changed`
(P4 `ArtifactRevision` interface-facet change); returns false on a trigger-free log.

---

## Wave 3 — Lead-controller runner (depends on all)

### T-RunGoal — `run_goal` + `run_goal_sync` durable lead loop
Covers: AC-5-1, AC-5-2, AC-5-3, AC-5-4, AC-5-5, AC-5-6, AC-5-7
Dependencies: AC-4-2, AC-2-1, AC-6-1
Add `run_goal` (async) alongside `run_teams` (`runner.py:180`) and `run_goal_sync` mirroring
`run_teams_sync` (`runner.py:240-261`), without touching `run_teams`. Loop stages decompose → compose →
assign → dispatch → integrate → replan, each a `DurableStep` emitting a `TeamEvent`. Propagate
goal/task/thread ids + `TeamsLayout` via `invocation_state` (by-reference, read-only in concurrent node
tools; `research-coding-runtimes.yaml` lines 19-22; `teams.py:41`). Dispatch via injectable callable
defaulting to native-`Swarm` `dispatch_team` (`teams.py:197`). Terminate on max-iterations/budget with a
final `TeamEvent`; re-enter decompose when `should_replan` fires.

### T-Exports — Package exports for the durable plane
Covers: AC-7-5
Dependencies: AC-5-1
Add `TeamEvent`, `EventLog`, `run_goal`, `run_goal_sync`, `DurableStep`, replay, and `should_replan`
to `strands_teams.__all__` and imports (`__init__.py:10-45`).

---

## Wave 4 — Tests + regression gate

### T-Tests — Determinism, trigger, happy-path, no-Bedrock, regression
Covers: AC-7-1, AC-7-2, AC-7-3, AC-7-4, AC-7-6
Dependencies: AC-5-5, AC-6-2, AC-6-3, AC-6-4, AC-6-5, AC-6-6, AC-3-2
Tests under `packages/strands-teams/tests/`: (1) event append+replay determinism (byte-identical across
two replays); (2) each of the five replan triggers fires exactly and none on a trigger-free log;
(3) `run_goal` happy path with stubbed dispatch reaching a terminal status with the expected `TeamEvent`
sequence; (4) any Bedrock/network call fails the suite (stubs + in-process engine only); (5) existing
`test_runner.py` regression suite passes unchanged (default one-shot path untouched).
