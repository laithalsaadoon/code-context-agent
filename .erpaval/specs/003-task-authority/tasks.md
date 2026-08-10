# Tasks — 003-task-authority (Phase P2-authority)

**Status:** IMPLEMENTED (2026-07-16) — all checks green: `ruff check` + `ruff format`
+ `ty check` clean; `pytest -q` = 315 passed (25 new in `test_authority.py`).

## Implementation record (files + anchors)

Framework (`packages/strands-teams/src/strands_teams/`):

- `models.py:131-224` — `AuthorityGrant` (3 bools, default `False`) + `Budget`
  (`max_wall_clock_minutes`, `max_cost_usd`, `max_retries`, all `ge=0`). Both
  `FrozenModel`. Docstrings carry the Phase-3 forward hooks: `max_cost_usd` →
  Claude SDK `max_budget_usd` (AC-5-5), `max_retries`/`may_write` → Codex (AC-5-6).
- `layout.py:62` (`contract_name="contract.json"`), `layout.py:97-108`
  (`contract_path(team_id)` + `contract_glob()`) — per-team contract sidecar (AC-2-6).
- `teams.py:83-93` — `TeamInvocationState` extended with `authority` / `budget` /
  `acceptance_criteria` / `input_revisions` (`NotRequired`) (AC-2-3).
- `teams.py:226-255` — `_coerce_authority` (default-permissive when omitted, AC-3-6) /
  `_coerce_budget` (AC-2-2).
- `teams.py:258-306` — `_enforce_blocking_authority`: reads typed findings, strips
  `blocking=True` when grant forbids, rewrites sidecar, returns error message
  (AC-3-1, AC-3-3, AC-3-4, AC-3-5). Never relies on the agent prompt.
- `teams.py:304-315` — `_effective_execution_timeout` (budget wall-clock ×60, AC-2-5).
- `teams.py:317-347` — `_write_team_contract` persists contract.json (AC-2-6).
- `teams.py:349-382` — `_build_and_run_swarm` preserves native `Swarm` +
  handoff window 8 / min-unique 3 (AC-5-2).
- `teams.py:385-...` — `dispatch_team` extended with `acceptance_criteria` /
  `authority` / `budget` / `input_revisions` params + docstring (AC-2-1); builds a
  NEW `team_state` dict, never mutating coordinator state (AC-5-1, AC-5-4); calls
  enforcement after the swarm returns (AC-3-2 error result).
- `teams.py` `_build_augmented_task` — surfaces acceptance criteria + opaque input
  revisions in the team task (AC-2-4, AC-2-7).
- `hooks.py:53-59,81-91` — coverage tokenizer + `DEFAULT_COVERAGE_RESUME_TEMPLATE`.
- `hooks.py` `ArtifactQualityHook` — `_acceptance_criteria` (reads contracts),
  `_findings_corpus`, `_is_covered`, `_uncovered_criteria`; `_check_artifact_quality`
  gates on rubric OR coverage, uncovered forces resume naming criteria, single
  `max_passes` cap (AC-4-1..AC-4-6).
- `__init__.py` — exports `AuthorityGrant` + `Budget` (imports + `__all__`) (AC-1-5).

Tests: `packages/strands-teams/tests/test_authority.py` (25 tests) — US1 model
contracts, US2 parsing/threading/contract/timeout/opaque-revisions, US3
refuse/permit/omit/code-enforcement, US4 coverage resume/no-resume/fallback/cap,
US5 native-swarm/live-objects/no-session_manager, US6 acceptance assertions
(AC-6-1..AC-6-6).

No new third-party deps added to `strands-teams` core (Phase-3 SDK deps deferred to
`strands-teams-runtimes` per plan). Additive: full suite 315 passed, no regressions.

---

Derived mechanically from `spec.md`. Each task implements one AC. `[P]` = parallel-safe
(isolated worktree, no file overlap). `Dependencies:` lists ACs that must complete first.
Do not hand-edit — regenerate from `spec.md`.

Cross-phase note: `Finding` (with `blocking: bool`), `TeamsLayout.findings_json_path`,
and `read_team_findings -> Finding[]` are given by Phase 1 (`002-typed-findings`);
`ArtifactRevision` is given by Phase 4 (`005-artifact-revisions`). Do not redefine.

Primary files: `packages/strands-teams/src/strands_teams/models.py`,
`.../teams.py`, `.../hooks.py`, `.../layout.py`, `.../__init__.py`, and
`packages/strands-teams/tests/`.

---

## US1 — Authority and budget as typed contracts

### T-AC-1-1: AuthorityGrant model [P]
Implement AC-1-1: add `AuthorityGrant(FrozenModel)` with bool fields
`may_create_blocking_finding`, `may_write`, `may_approve` after
`models.py:112-122` (`CalibratedJudgment`).

### T-AC-1-2: AuthorityGrant defaults [P]
Implement AC-1-2: default all three `AuthorityGrant` fields to `False`.
Dependencies: (bundled with T-AC-1-1 — same model)

### T-AC-1-3: Budget model [P]
Implement AC-1-3: add `Budget(FrozenModel)` with `max_wall_clock_minutes`,
`max_cost_usd`, `max_retries` in `models.py`.

### T-AC-1-4: Budget non-negative validation [P]
Implement AC-1-4: constrain the three `Budget` fields to `ge=0` so negatives raise.
Dependencies: (bundled with T-AC-1-3 — same model)

### T-AC-1-5: Export AuthorityGrant + Budget
Implement AC-1-5: export both from `__init__.py:12-20` imports and `:45-77` `__all__`.
Dependencies: AC-1-1, AC-1-3

---

## US2 — dispatch_team contract enrichment

### T-AC-2-1: dispatch_team params
Implement AC-2-1: add optional `acceptance_criteria`, `authority`, `budget`,
`input_revisions` params to `dispatch_team` (`teams.py:197-234`) + docstring.
Dependencies: AC-1-1, AC-1-3

### T-AC-2-2: Parse dict → models
Implement AC-2-2: parse `authority`/`budget` dicts into `AuthorityGrant`/`Budget`
inside `dispatch_team`.
Dependencies: AC-2-1

### T-AC-2-3: Extend TeamInvocationState
Implement AC-2-3: add `authority`, `budget`, `acceptance_criteria`,
`input_revisions` keys to the `TeamInvocationState` TypedDict (`teams.py:41-69`).
Dependencies: AC-1-1, AC-1-3

### T-AC-2-4: Surface criteria/revisions in task
Implement AC-2-4: thread `acceptance_criteria` + `input_revisions` into
`_build_augmented_task` (`teams.py:334-376`).
Dependencies: AC-2-1

### T-AC-2-5: Budget-driven execution_timeout
Implement AC-2-5: use `max_wall_clock_minutes * 60` as the effective swarm
`execution_timeout` override (`teams.py:243, 281-289`).
Dependencies: AC-2-2

### T-AC-2-6: Persist acceptance-criteria contract artifact
Implement AC-2-6: add a per-team contract path to `TeamsLayout` (`layout.py:63-109`)
and write `acceptance_criteria` to it in `dispatch_team`.
Dependencies: AC-2-1

### T-AC-2-7: Opaque input-revision tolerance
Implement AC-2-7: treat unresolved `input_revisions` entries as opaque strings; no raise.
Dependencies: AC-2-1

---

## US3 — Structural blocking-finding refusal

### T-AC-3-1: Refuse forbidden blocking findings
Implement AC-3-1: after the swarm completes, read the team's typed `Finding[]`
(via Phase-1 `findings_json_path`) and reject `blocking=True` when
`authority.may_create_blocking_finding` is `False` (`teams.py:290-303`).
Dependencies: AC-2-2, AC-2-3

### T-AC-3-2: Error result on rejection
Implement AC-3-2: return `status="error"` + a `message` naming the rejected finding.
Dependencies: AC-3-1

### T-AC-3-3: Strip rejected finding from artifact
Implement AC-3-3: remove the forbidden blocking finding from the persisted
typed-findings artifact so it does not survive.
Dependencies: AC-3-1

### T-AC-3-4: Enforce in code, not prompt
Implement AC-3-4: assert enforcement lives in `dispatch_team` code (no reliance on
node system prompt). Verified via test T-AC-6-1.
Dependencies: AC-3-1

### T-AC-3-5: Permit when granted
Implement AC-3-5: persist blocking findings unchanged when
`may_create_blocking_finding` is `True`.
Dependencies: AC-3-1

### T-AC-3-6: Default-permissive when authority omitted
Implement AC-3-6: when `authority` is omitted, apply a default grant permitting
blocking findings (backward-compat).
Dependencies: AC-2-2

---

## US4 — Acceptance-criteria coverage in ArtifactQualityHook

### T-AC-4-1: Coverage evaluation in hook
Implement AC-4-1: extend `ArtifactQualityHook` (`hooks.py:295-360`) to evaluate
acceptance-criteria coverage alongside the rubric score (constructor `:307-318`).
Dependencies: AC-2-6

### T-AC-4-2: Uncovered → needs revision
Implement AC-4-2: mark run as needing revision when any acceptance criterion is
uncovered, regardless of rubric total (`_check_artifact_quality` `:324-360`).
Dependencies: AC-4-1

### T-AC-4-3: Resume prompt names uncovered criteria
Implement AC-4-3: extend `DEFAULT_RESUME_TEMPLATE` (`hooks.py:60-70`) / `event.resume`
(`:353`) to name uncovered criteria.
Dependencies: AC-4-2

### T-AC-4-4: No resume when covered + rubric passes
Implement AC-4-4: leave `event.resume` unset when all criteria covered and rubric
at/above threshold.
Dependencies: AC-4-1

### T-AC-4-5: Rubric-only fallback when no criteria
Implement AC-4-5: fall back to unchanged rubric-only behavior when no acceptance
criteria were supplied.
Dependencies: AC-4-1

### T-AC-4-6: Combined max_passes cap
Implement AC-4-6: keep the `max_passes` cap covering both rubric- and coverage-driven
resumes (`hooks.py:326-327`).
Dependencies: AC-4-2

---

## US5 — invocation_state threading + native-Swarm and isolation invariants

### T-AC-5-1: New invocation_state dict, no mutation
Implement AC-5-1: build a fresh dict (`{**state, ...}`) for the swarm's
`invocation_state` carrying authority/budget/criteria/revisions; do not mutate the
coordinator's shared state (`teams.py:290`).
Dependencies: AC-2-3

### T-AC-5-2: Preserve native-Swarm dispatch
Implement AC-5-2: keep `swarm.invoke_async` + handoff-detection window 8 / min-unique
3 (`teams.py:281-290`).
Dependencies: AC-5-1

### T-AC-5-3: Read-only shared references under concurrency
Implement AC-5-3: document + enforce read-only treatment of `authority`/`budget` in
concurrently-executing node tools.
Dependencies: AC-5-1

### T-AC-5-4: Live objects, no serialization/session_manager
Implement AC-5-4: pass live `AuthorityGrant`/`Budget` objects by reference; assert no
`session_manager` attached.
Dependencies: AC-5-1

### T-AC-5-5: Budget.max_cost_usd → Claude max_budget_usd (forward hook) [P]
Implement AC-5-5: document `Budget.max_cost_usd` as the contract value a Bedrock
`ClaudeAgentRuntime` maps to SDK `max_budget_usd` (Phase 4 consumer; field-name
gotcha per research digest). Docstring-only in this phase.
Dependencies: AC-1-3

### T-AC-5-6: Budget.max_retries + may_write → Codex (forward hook) [P]
Implement AC-5-6: document `Budget.max_retries` as harness retry bound and
`AuthorityGrant.may_write` as file-write gate for a Codex `amazon-bedrock` runtime.
Docstring-only in this phase.
Dependencies: AC-1-1, AC-1-3

---

## US6 — Tests

### T-AC-6-1: Test forbidden blocking finding → error
Implement AC-6-1 in `packages/strands-teams/tests/`.
Dependencies: AC-3-2

### T-AC-6-2: Test permitted blocking finding survives
Implement AC-6-2.
Dependencies: AC-3-5

### T-AC-6-3: Test uncovered criterion → resume names it
Implement AC-6-3.
Dependencies: AC-4-3

### T-AC-6-4: Test covered + passing → no resume
Implement AC-6-4.
Dependencies: AC-4-4

### T-AC-6-5: Test parse + thread without mutating input state
Implement AC-6-5.
Dependencies: AC-5-1

### T-AC-6-6: Test backward-compat when authority/criteria omitted
Implement AC-6-6.
Dependencies: AC-3-6, AC-4-5
