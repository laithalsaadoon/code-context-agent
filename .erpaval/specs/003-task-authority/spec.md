---
slug: task-authority
sequence: 003
phase: P2-authority
maps_to_astr: "§4.1, §6.2, §4.7, §14.7"
depends_on_phases:
  - "001-strands-teams (native Swarm dispatch_team, TeamInvocationState, ArtifactQualityHook)"
  - "002-typed-findings (Finding model with blocking: bool; TeamsLayout.findings_json_path; read_team_findings -> Finding[])"
forward_refs:
  - "005-artifact-revisions (Phase 4 ArtifactRevision — input_revisions[] entries are opaque revision-id strings until then)"
  - "004-coding-runtime (Phase 3 SpecialistRuntime — Budget/AuthorityGrant are read by the coding tool from invocation_state)"
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

## Scope and grounding

Phase **P2-authority** enriches the `dispatch_team` task contract with typed authority
and budget, plus acceptance criteria and input revisions, and makes the authority
**structurally enforced in code** — not a prompt instruction (plan §4 Phase 2; ASTR
§4.7). It is additive: the existing `analyze` use case keeps working unchanged
(plan §2 decision 1, the regression gate).

Source anchors (read before implementing — verified against current `feat/astr-north-star`):

- `packages/strands-teams/src/strands_teams/models.py:40` — `FrozenModel` base for new
  `AuthorityGrant` / `Budget`; append after `CalibratedJudgment` (`models.py:112-122`).
- `packages/strands-teams/src/strands_teams/teams.py:41-69` — `TeamInvocationState`
  TypedDict to extend.
- `packages/strands-teams/src/strands_teams/teams.py:197-234` — `dispatch_team`
  signature + docstring to extend.
- `packages/strands-teams/src/strands_teams/teams.py:281-290` — native `Swarm(...)`
  construction + `swarm.invoke_async(augmented_task, invocation_state={**state})`.
- `packages/strands-teams/src/strands_teams/teams.py:334-376` — `_build_augmented_task`
  (surface `acceptance_criteria` / `input_revisions` here).
- `packages/strands-teams/src/strands_teams/hooks.py:60-70` — `DEFAULT_RESUME_TEMPLATE`.
- `packages/strands-teams/src/strands_teams/hooks.py:295-360` — `ArtifactQualityHook`
  (`__init__` at `:307-318`, `_check_artifact_quality` at `:324-360`, writable
  `event.resume` at `:353`); constructed in `create_all_hooks` at `hooks.py:512`.
- `packages/strands-teams/src/strands_teams/layout.py:63-109` — `TeamsLayout` methods
  (add a per-team contract path here).
- `packages/strands-teams/src/strands_teams/__init__.py:12-20,45-77` — public exports.

Types treated **as given** (do not redefine): `Finding` (with `blocking: bool`),
`TeamsLayout.findings_json_path(team_id)`, and `read_team_findings -> Finding[]` from
Phase 1 (spec `002-typed-findings`); `ArtifactRevision` from Phase 4 (spec
`005-artifact-revisions`).

Research-digest API facts encoded below (session-8c56b2 `research-coding-runtimes.yaml`):
native `Swarm.invoke_async` + repetitive-handoff window 8 / min-unique 3
(`strands_tool_pattern`, teams.py:287-290); `invocation_state` is a plain in-process
dict passed **by reference**, no serialization, no session_manager, "Do not mutate
invocation_state from concurrent tools" (`strands_tool_pattern` lines 20-22, 30);
Claude field-name gotcha `max_budget_usd` not `max_cost_usd`
(`claude_agent_sdk_bedrock` line 61); Codex built-in `amazon-bedrock` provider +
`may_write`/sandbox isolation (`codex_gpt56_bedrock` lines 65-84).

---

## User Story 1 — Authority and budget as typed contracts

### Acceptance Criteria

AC-1-1 [P]
Ubiquitous: The `strands_teams` framework shall provide an `AuthorityGrant` immutable model (`FrozenModel`) with the boolean fields `may_create_blocking_finding`, `may_write`, and `may_approve`.

AC-1-2 [P]
Ubiquitous: The `AuthorityGrant` model shall default each of `may_create_blocking_finding`, `may_write`, and `may_approve` to `False`.

AC-1-3 [P]
Ubiquitous: The `strands_teams` framework shall provide a `Budget` immutable model (`FrozenModel`) with the fields `max_wall_clock_minutes`, `max_cost_usd`, and `max_retries`.

AC-1-4 [P]
Unwanted behavior: If a `Budget` is constructed with `max_wall_clock_minutes`, `max_cost_usd`, or `max_retries` less than zero, then the `Budget` model shall raise a validation error.

AC-1-5
Dependencies: AC-1-1, AC-1-3
Ubiquitous: The `strands_teams` top-level package shall export `AuthorityGrant` and `Budget` from its public `__init__` surface and `__all__`.

---

## User Story 2 — dispatch_team contract enrichment

### Acceptance Criteria

AC-2-1
Dependencies: AC-1-1, AC-1-3
Ubiquitous: The `dispatch_team` tool shall accept optional `acceptance_criteria`, `authority`, `budget`, and `input_revisions` parameters in addition to its existing parameters.

AC-2-2
Dependencies: AC-2-1
Event-driven: When `dispatch_team` receives `authority` or `budget` as plain JSON dicts from the coordinator LLM, the tool shall parse each into an `AuthorityGrant` and a `Budget` instance respectively.

AC-2-3
Dependencies: AC-1-1, AC-1-3
Ubiquitous: The `TeamInvocationState` TypedDict shall carry the active team's `authority` grant, `budget`, `acceptance_criteria`, and `input_revisions`.

AC-2-4
Dependencies: AC-2-1
Event-driven: When `acceptance_criteria` or `input_revisions` are supplied, `dispatch_team` shall include them in the augmented team task built by `_build_augmented_task` so the team can act on them.

AC-2-5
Dependencies: AC-2-2
Event-driven: When a `Budget` with a positive `max_wall_clock_minutes` is supplied, `dispatch_team` shall use `max_wall_clock_minutes` × 60 as the effective swarm `execution_timeout`, overriding the run-default.

AC-2-6
Dependencies: AC-2-1
Ubiquitous: The `dispatch_team` tool shall persist the team's `acceptance_criteria` to a per-team contract artifact whose path is supplied by `TeamsLayout`.

AC-2-7
Dependencies: AC-2-1
Unwanted behavior: If an `input_revisions` entry does not yet resolve to a Phase-4 `ArtifactRevision`, then `dispatch_team` shall treat the entry as an opaque revision-identifier string and shall not raise.

---

## User Story 3 — Structural blocking-finding refusal

### Acceptance Criteria

AC-3-1
Dependencies: AC-2-2, AC-2-3
State-driven: While a dispatched team's `AuthorityGrant` has `may_create_blocking_finding` equal to `False`, `dispatch_team` shall reject any persisted `Finding` whose `blocking` field is `True`.

AC-3-2
Dependencies: AC-3-1
Event-driven: When `dispatch_team` rejects a forbidden blocking finding, the tool shall return a JSON result whose `status` is `"error"` and whose `message` identifies the rejected finding.

AC-3-3
Dependencies: AC-3-1
Unwanted behavior: If a team persists a blocking finding its grant forbids, then `dispatch_team` shall remove that finding from the persisted typed-findings artifact so it does not survive the run.

AC-3-4
Dependencies: AC-3-1
Ubiquitous: The `dispatch_team` tool shall enforce the blocking-finding authority check in its own code and shall not rely on the team-agent system prompt for enforcement.

AC-3-5
Dependencies: AC-3-1
State-driven: While a dispatched team's `AuthorityGrant` has `may_create_blocking_finding` equal to `True`, `dispatch_team` shall persist that team's blocking findings unchanged.

AC-3-6
Dependencies: AC-2-2
Unwanted behavior: If the `authority` parameter is omitted from a `dispatch_team` call, then `dispatch_team` shall apply a default grant that permits blocking findings, so pre-authority callers behave as before.

---

## User Story 4 — Acceptance-criteria coverage in ArtifactQualityHook

### Acceptance Criteria

AC-4-1
Dependencies: AC-2-6
Ubiquitous: The `ArtifactQualityHook` shall evaluate acceptance-criteria coverage in addition to the rubric score.

AC-4-2
Dependencies: AC-4-1
State-driven: While one or more of the run's acceptance criteria are uncovered by the produced artifacts and findings, the `ArtifactQualityHook` shall treat the run as needing revision regardless of the rubric total.

AC-4-3
Dependencies: AC-4-2
Event-driven: When the `ArtifactQualityHook` triggers a resume pass because of uncovered acceptance criteria, the emitted `event.resume` prompt shall name the uncovered criteria.

AC-4-4
Dependencies: AC-4-1
State-driven: While all acceptance criteria are covered and the rubric total is at or above the revision threshold, the `ArtifactQualityHook` shall not emit a resume.

AC-4-5
Dependencies: AC-4-1
Unwanted behavior: If no acceptance criteria were supplied for the run, then the `ArtifactQualityHook` shall fall back to rubric-only behavior unchanged.

AC-4-6
Dependencies: AC-4-2
Ubiquitous: The `ArtifactQualityHook` shall remain capped at `max_passes` resume passes across both rubric-driven and coverage-driven triggers combined.

---

## User Story 5 — invocation_state threading + native-Swarm and isolation invariants

### Acceptance Criteria

AC-5-1
Dependencies: AC-2-3
Ubiquitous: The `dispatch_team` tool shall pass the per-team `authority`, `budget`, `acceptance_criteria`, and `input_revisions` to the team swarm by constructing a new `invocation_state` dict rather than mutating the coordinator's shared state dict.

AC-5-2
Dependencies: AC-5-1
Ubiquitous: The `dispatch_team` tool shall continue to execute each team as a native strands `Swarm` via `swarm.invoke_async`, preserving `repetitive_handoff_detection_window` 8 and `repetitive_handoff_min_unique_agents` 3.

AC-5-3
Dependencies: AC-5-1
State-driven: While team nodes execute concurrently, the framework shall treat the `authority` and `budget` objects in `invocation_state` as read-only shared references and shall not mutate them from within concurrently-executing node tools.

AC-5-4
Dependencies: AC-5-1
Ubiquitous: The `invocation_state` handed to the swarm shall carry live in-process `AuthorityGrant` and `Budget` objects with no serialization boundary and no attached `session_manager`.

AC-5-5
Dependencies: AC-1-3
Optional feature: Where a Claude specialist coding runtime is granted in a later phase, the `Budget.max_cost_usd` field shall be the framework-contract value that a Bedrock `ClaudeAgentRuntime` maps to the SDK's `max_budget_usd` option.

AC-5-6
Dependencies: AC-1-1, AC-1-3
Optional feature: Where a Codex specialist coding runtime is granted in a later phase, `Budget.max_retries` shall bound the harness retry count and `AuthorityGrant.may_write` shall gate whether that runtime may modify files.

---

## User Story 6 — Tests for authority enforcement and coverage

### Acceptance Criteria

AC-6-1
Dependencies: AC-3-2
Event-driven: When a team whose grant has `may_create_blocking_finding` `False` persists a blocking `Finding`, the test suite shall assert `dispatch_team` returns `status` `"error"`.

AC-6-2
Dependencies: AC-3-5
Event-driven: When a team whose grant has `may_create_blocking_finding` `True` persists a blocking `Finding`, the test suite shall assert the finding survives in the persisted typed-findings artifact.

AC-6-3
Dependencies: AC-4-3
State-driven: While a run has an uncovered acceptance criterion, the test suite shall assert the `ArtifactQualityHook` sets `event.resume` naming that criterion.

AC-6-4
Dependencies: AC-4-4
State-driven: While all acceptance criteria are covered and the rubric passes, the test suite shall assert the `ArtifactQualityHook` leaves `event.resume` unset.

AC-6-5
Dependencies: AC-5-1
Event-driven: When `authority` and `budget` dicts are supplied to `dispatch_team`, the test suite shall assert they are parsed and threaded into a new `invocation_state` dict without mutating the input state.

AC-6-6
Dependencies: AC-3-6, AC-4-5
Unwanted behavior: If `authority` and `acceptance_criteria` are omitted, then the test suite shall assert pre-authority dispatch behavior (blocking findings permitted, rubric-only quality gating) is preserved.
