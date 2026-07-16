---
slug: durable-lead-events
sequence: 006
hmw_source: null
---

**Status:** COMPLETE

<write_protocol>
Your output file is the single source of truth for your work. Edit it after every meaningful step, before starting the next one. Partial progress written to disk survives timeouts, SendMessage interrupts, and orchestrator context pressure; state held in working memory does not.

The rhythm is: one unit of thought → edit the file with the outcome → next unit. One decision at a time.

Work through your sections in numbered order. For each section:

1. Think through the decision, research finding, or draft. Read adjacent files, run a web search, or consult the framework reference when the answer is not in your head.
2. Edit the file under that section — the claim, the evidence, the user story or HMW or spec statement. Cite sources inline.
3. If the section needs more depth, do another unit of thought and edit again.
4. Move to the next section only after the current one has real content.

Name the tradeoff on every non-obvious call. "Chose JTBD job story over user story for the top-level framing because the goal is reframing around progress, not stakeholder persona" beats "used job story." The synthesizer reads these attributions when composing the final artifact.

Cite adjacent material inline when a decision depends on source evidence — framework file + heading, research synthesis line number, interview quote, or external URL. Reviewers read the citations to verify your reasoning.

When every section has real content, change the `Status:` line at the top of the file from `IN PROGRESS` to `COMPLETE`.
</write_protocol>

## Overview

Phase P5 of the ASTR north-star build (`docs/planning/astr-north-star-plan.md` §4 "Phase 5",
§9.2/§6.5/§7/§15.2). Adds two things to the domain-agnostic `strands_teams` core, both
**additive** — the one-shot `run_teams` / `run_teams_sync` path
(`packages/strands-teams/src/strands_teams/runner.py:180`, `:240`) stays the default and is never
broken (plan §2.1, §4 "Phase 5" bullet 2):

1. **A typed event plane.** A `TeamEvent` FrozenModel and an append-only `EventLog` that evolve
   today's untyped `team_debug.jsonl` (`layout.py:59` `debug_log_name`; `layout.py:79`
   `debug_log_path`; written by `teams.py:178` `_write_debug_entry`, appended on every dispatch
   path at `teams.py:317`) into a **replayable** plane carrying causal parents, correlation id, and
   epistemic status. It carries **no chain-of-thought / raw transcript** (plan §5 "Boundary
   discipline"; research digest `research-coding-runtimes.yaml` line 99 — the tool returns only the
   structured summary, never the transcript).

2. **A durable lead-controller runner `run_goal`.** A NEW runner alongside `run_teams_sync`
   (NOT a replacement, plan §4 Phase 5 bullet 2) that runs the ASTR lead loop
   decompose → compose → assign → dispatch → integrate → replan, with `should_replan` triggers
   (`requirement.changed`, `critical_finding.created`, `task.failed`, `budget.threshold_exceeded`,
   `artifact.interface_changed`). It is **engine-agnostic** via a `DurableStep` Protocol so
   Temporal/Restate can back it later; this phase ships an in-process `asyncio` engine.

**Grounding.** Every strands-touching AC conforms to `research-coding-runtimes.yaml` (Strands 1.47 /
Bedrock facts — treat as authoritative, do not re-research): native `Swarm` dispatch
(`strands_tool_pattern`, lines 27-31); `invocation_state` is a plain in-process dict passed **by
reference**, no serialization, treated read-only inside concurrently-executing node tools
(lines 19-22, 29-30); never `asyncio.run` inside an already-async strands runtime (line 18);
Bedrock model ids are bare (`global.anthropic.claude-opus-4-8`, `openai.gpt-5.6-sol`)
(`claude_agent_sdk_bedrock` line 34, `codex_gpt56_bedrock` line 65).

**Cross-phase givens (do not redefine).** `Finding` (P1, spec `002-typed-findings`) and
`ArtifactRevision` (P4, spec `005-artifact-revisions`) are treated as **already present** in
`strands_teams.models`. This phase references them from event payloads and replan triggers only.

Terms used below:

- **event plane** — the `TeamEvent` type + append-only `EventLog` that supersede `team_debug.jsonl`.
- **epistemic status** — the literal set `observed | inferred | hypothesized | disputed | accepted |
  rejected | superseded` on every `TeamEvent`.
- **durable step** — one lead-loop transition executed through the `DurableStep` Protocol, whose
  outcome is recorded as a `TeamEvent` so replay can reconstruct it deterministically.
- **replay** — reconstructing state transitions, artifacts, findings, and decisions from the
  `EventLog` alone, with no live model call.
- **lead loop** — the durable outer state machine decompose → compose → assign → dispatch →
  integrate → replan driven by `run_goal`.

---

## User Story 1 — Typed `TeamEvent`

### Acceptance Criteria

AC-1-1 [P]
Ubiquitous: The `strands_teams` core shall expose a `TeamEvent` `FrozenModel` (subclassing `strands_teams.models.FrozenModel`, `models.py:40`) with the fields `event_id`, `event_type`, `actor`, `recipients`, `thread_id`, `goal_id`, `task_id`, `artifact_bindings`, `causal_parents`, `correlation_id`, `epistemic_status`, `payload`, `evidence`, and `created_at`.

AC-1-2
Dependencies: AC-1-1
Ubiquitous: The `TeamEvent.epistemic_status` field shall be constrained to the literal set `{observed, inferred, hypothesized, disputed, accepted, rejected, superseded}` and shall reject any value outside that set at construction.

AC-1-3
Dependencies: AC-1-1
Event-driven: When a `TeamEvent` is constructed without an explicit `event_id`, `created_at`, or `correlation_id`, the framework shall assign a unique `event_id`, a UTC creation timestamp, and a `correlation_id` defaulting to the event's `goal_id` when present and otherwise to its `event_id`.

AC-1-4
Dependencies: AC-1-1
Unwanted behavior: If a `TeamEvent` is constructed with a field not in its declared schema (e.g. a raw-transcript or chain-of-thought field), then construction shall fail with a validation error, because the model forbids extra fields (`FrozenModel` `extra="forbid"`, `models.py:53`).

AC-1-5
Dependencies: AC-1-1, AC-1-2
Event-driven: When a `TeamEvent` is serialized to a single JSON line and re-parsed, then the reconstructed `TeamEvent` shall compare equal to the original, so the event plane is a lossless round-trip substrate for replay.

## User Story 2 — Append-only `EventLog` (evolves `team_debug.jsonl`)

### Acceptance Criteria

AC-2-1
Dependencies: AC-1-1
Ubiquitous: The `strands_teams` core shall expose an append-only `EventLog` bound to a single file path resolved from `TeamsLayout` (a new `event_log_path()` method that supersedes / co-locates the current `debug_log_path()`, `layout.py:79`), writing one JSON-encoded `TeamEvent` per line.

AC-2-2
Dependencies: AC-2-1
Event-driven: When `EventLog.append(event)` is called, the system shall append exactly one line to the log file and shall leave every previously written line byte-for-byte unchanged.

AC-2-3
Dependencies: AC-2-1, AC-1-1
Complex: While a team is dispatched via `dispatch_team` on a native `Swarm` (`teams.py:197`, `research-coding-runtimes.yaml` lines 27-31), when the dispatch completes on any path (success, failure, or timeout), the system shall emit a typed `TeamEvent` to the `EventLog` carrying the same team_id, status, and duration recorded in today's debug entry (`teams.py:305-317`).

AC-2-4
Dependencies: AC-2-3
Unwanted behavior: If appending to the `EventLog` raises an `OSError`, then the system shall log the failure and continue, and the coordinator or lead loop shall not crash (preserving the never-raises contract of `_write_debug_entry`, `teams.py:183-189`).

AC-2-5
Dependencies: AC-2-1
Unwanted behavior: If any caller attempts to mutate or delete an already-written event, then the operation shall be unsupported, because the `EventLog` exposes only append and read operations and no update or delete API.

AC-2-6
Dependencies: AC-2-3
State-driven: While the codebase-analysis use case runs the default `run_teams` path unchanged, the system shall still produce the team-dispatch debug records at the layout-resolved path so existing `read_team_findings` and display behavior (`teams.py:384`, `hooks.py`) remain intact.

## User Story 3 — Replay and state reconstruction

### Acceptance Criteria

AC-3-1
Dependencies: AC-2-1
Ubiquitous: The `strands_teams` core shall expose a replay function that reconstructs the run's state transitions, artifacts, findings, and decisions from an `EventLog` alone, without issuing any live model or Bedrock call.

AC-3-2
Dependencies: AC-3-1
Event-driven: When the same `EventLog` is replayed twice, then the two reconstructed states shall be identical (deterministic replay).

AC-3-3
Dependencies: AC-3-1
State-driven: While events carry `causal_parents`, the replay function shall apply each event only after all of its causal-parent events have been applied, yielding a causal-order-consistent reconstruction.

AC-3-4
Dependencies: AC-3-1, AC-1-2
Event-driven: When an event bears `epistemic_status = superseded` (or is superseded by a later event referencing it in `causal_parents`), then replay shall reflect the superseding outcome while retaining the original event in the log (supersession, never deletion).

AC-3-5
Dependencies: AC-3-1
Unwanted behavior: If the `EventLog` contains a malformed or non-parseable line, then replay shall skip that line deterministically and record a reconstruction warning rather than raise or silently corrupt the reconstructed state.

## User Story 4 — `DurableStep` Protocol + in-process engine

### Acceptance Criteria

AC-4-1 [P]
Ubiquitous: The `strands_teams` core shall define a `DurableStep` `Protocol` describing the engine-agnostic contract for executing one lead-loop transition, so a Temporal or Restate worker can implement it later without changing call sites.

AC-4-2
Dependencies: AC-4-1
Where an in-process engine is included, the `strands_teams` core shall ship an `asyncio`-based `DurableStep` implementation that executes steps in the current event loop.

AC-4-3
Dependencies: AC-4-1, AC-2-1
Event-driven: When a durable step executes, then the engine shall emit a `TeamEvent` recording the step's transition and result to the `EventLog` before returning, so the step is replayable.

AC-4-4
Dependencies: AC-4-2, AC-3-1
Event-driven: When a durable step is re-executed during replay and a result event for that step already exists in the `EventLog`, then the engine shall return the recorded result without re-running the step's side effects (idempotent replay).

AC-4-5
Dependencies: AC-4-2
Unwanted behavior: If a durable step runs inside an already-running async runtime, then the engine shall neither call `asyncio.run` nor otherwise block the event loop, conforming to `research-coding-runtimes.yaml` line 18 and the sync-boundary discipline of `run_teams_sync` (`runner.py:251`).

## User Story 5 — `run_goal` durable lead-controller runner

### Acceptance Criteria

AC-5-1
Dependencies: AC-4-2, AC-2-1
Ubiquitous: The `strands_teams` core shall expose an asynchronous `run_goal` runner alongside `run_teams` (`runner.py:180`) that drives the durable lead loop, added without modifying the signature or behavior of the existing `run_teams`.

AC-5-2
Dependencies: AC-5-1
Ubiquitous: The `strands_teams` core shall expose a synchronous `run_goal_sync` wrapper that invokes `run_goal` via `asyncio.run`, mirroring `run_teams_sync` (`runner.py:240-261`) and never calling `asyncio.run` inside an already-running loop.

AC-5-3
Dependencies: AC-5-1
Complex: While a goal is active, when `run_goal` advances the lead loop, the system shall execute the ordered stages decompose → compose → assign → dispatch → integrate → replan, each as a `DurableStep` whose outcome is recorded as a `TeamEvent`.

AC-5-4
Dependencies: AC-5-1
Ubiquitous: The `run_goal` runner shall propagate `goal_id`, `task_id`, `thread_id`, and the run's `TeamsLayout` to dispatched teams through `invocation_state` — a plain in-process dict passed by reference, treated read-only inside concurrently-executing node tools — rather than through module-level globals (`research-coding-runtimes.yaml` lines 19-22, 29-30; `teams.py:41` `TeamInvocationState`).

AC-5-5
Dependencies: AC-5-1
Event-driven: When `run_goal` reaches the dispatch stage, then it shall dispatch teams through an injectable dispatch callable that defaults to the native-`Swarm` `dispatch_team` tool (`teams.py:197`), so tests can substitute a stub dispatch and run with no Bedrock call.

AC-5-6
Dependencies: AC-5-3
Unwanted behavior: If the lead loop reaches its configured maximum iterations or exceeds its budget threshold without converging, then `run_goal` shall terminate with a terminal status, persist a final `TeamEvent`, and never loop indefinitely.

AC-5-7
Dependencies: AC-5-3, AC-6-1
Complex: While the lead loop is running, when `should_replan` returns true, `run_goal` shall re-enter the decompose stage (replan) and record a replan `TeamEvent` before continuing.

## User Story 6 — `should_replan` triggers

### Acceptance Criteria

AC-6-1
Dependencies: AC-2-1
Ubiquitous: The `strands_teams` core shall expose a `should_replan` predicate that evaluates the `EventLog` and fires on any of the triggers `requirement.changed`, `critical_finding.created`, `task.failed`, `budget.threshold_exceeded`, and `artifact.interface_changed`.

AC-6-2
Dependencies: AC-6-1
Event-driven: When a `TeamEvent` recording a `Finding` (P1 type, spec `002-typed-findings`, treated as given) whose severity is critical or whose `blocking` flag is set is appended, then `should_replan` shall fire the `critical_finding.created` trigger.

AC-6-3
Dependencies: AC-6-1
Event-driven: When a dispatch `TeamEvent` reports a `status` of failed or timeout, then `should_replan` shall fire the `task.failed` trigger.

AC-6-4
Dependencies: AC-6-1
Event-driven: When cumulative recorded resource usage crosses the configured budget threshold (a new bound on `TeamsSettings`, `settings.py:29`), then `should_replan` shall fire the `budget.threshold_exceeded` trigger.

AC-6-5
Dependencies: AC-6-1
Event-driven: When a `TeamEvent` records an `ArtifactRevision` (P4 type, spec `005-artifact-revisions`, treated as given) that changes an interface facet of a bound artifact, then `should_replan` shall fire the `artifact.interface_changed` trigger.

AC-6-6
Dependencies: AC-6-1
Event-driven: When a `TeamEvent` records a change to the goal's requirement or input, then `should_replan` shall fire the `requirement.changed` trigger.

AC-6-7
Dependencies: AC-6-1
Unwanted behavior: If the `EventLog` contains no event matching any of the five triggers, then `should_replan` shall return false and the lead loop shall proceed to integrate or terminate without replanning.

## User Story 7 — Determinism and isolation gates (tests + surface)

### Acceptance Criteria

AC-7-1
Dependencies: AC-2-2, AC-3-2
Event-driven: When the event-append-and-replay test appends a fixed sequence of `TeamEvent`s and replays the resulting `EventLog`, then it shall assert that the reconstructed state is byte-for-byte identical across two replays (append+replay determinism).

AC-7-2
Dependencies: AC-6-2, AC-6-3, AC-6-4, AC-6-5, AC-6-6
Event-driven: When the replan-trigger test appends an event matching each of the five triggers in turn, then it shall assert that `should_replan` fires for exactly that trigger and does not fire on a trigger-free log.

AC-7-3
Dependencies: AC-5-5
Event-driven: When the `run_goal` happy-path test runs the lead loop with a stubbed dispatch callable, then it shall complete decompose → compose → assign → dispatch → integrate to a terminal status and assert the expected `TeamEvent` sequence in the `EventLog`.

AC-7-4
Dependencies: AC-7-3
Unwanted behavior: If any P5 test issues a Bedrock or network call, then the test shall fail, because `run_goal`, dispatch, and replay are exercised only through injected stubs and the in-process engine (no live model).

AC-7-5
Dependencies: AC-1-1, AC-2-1, AC-3-1, AC-4-1, AC-5-1, AC-6-1
Ubiquitous: The `strands_teams` package `__all__` (`__init__.py:45`) shall export `TeamEvent`, `EventLog`, `run_goal`, `run_goal_sync`, `DurableStep`, and the replay and `should_replan` entry points, so a use case can import the durable plane from the package root.

AC-7-6
Dependencies: AC-5-1
State-driven: While the P5 additions are present, the existing `run_teams` / `run_teams_sync` regression suite (`tests/test_runner.py`) shall pass unchanged, proving the durable loop is additive and the default one-shot path is untouched.
