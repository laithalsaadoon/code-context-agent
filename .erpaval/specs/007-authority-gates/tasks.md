# Tasks — 007-authority-gates (Phase P6-gates)

Derived mechanically from `spec.md`. Hand-edits are destroyed on regeneration —
edit `spec.md` instead. Each task maps 1:1 to an AC. `[P]` = parallel-safe in an
isolated worktree (no file overlap). `Dependencies:` lists ACs that must complete
first.

Cross-phase givens (do NOT implement here): `Finding` (spec 002), `AuthorityGrant`
(spec 003), `ArtifactRevision` + `semantic_diff` (spec 005).

---

## User Story 1 — Revision-bound, signed ApprovalToken

- [ ] T-AC-1-1 [P] — Add `ApprovalToken` `FrozenModel` in `strands_teams/models.py` with fields `token_id`, `approved_transition_kind`, `artifact_revision_id`, `revision_content_hash`, `approver`, `granted_at`, `valid_until`, `signature`, `invalidation_rules`.
- [ ] T-AC-1-2 — Bind token to one `ArtifactRevision` via `artifact_revision_id` + captured `content_hash`.
      Dependencies: AC-1-1
- [ ] T-AC-1-3 — Compute `signature` over canonical serialization at mint time with the configured signing key.
      Dependencies: AC-1-1
- [ ] T-AC-1-4 — `PolicyEngine` treats a signature-invalid token as absent.
      Dependencies: AC-1-3
- [ ] T-AC-1-5 — `PolicyEngine` rejects expired tokens (`now >= valid_until`); they do not count toward required approvals.
      Dependencies: AC-1-1

## User Story 2 — Decision record with dissent and lineage

- [ ] T-AC-2-1 — Add `Decision` `FrozenModel` in `strands_teams/models.py` with `decision_id`, `question`, `options`, `selected`, `rationale`, `evidence`, `dissent`, `supersedes`, `decided_at`.
      Dependencies: AC-1-1
- [ ] T-AC-2-2 — `Decision.evidence` reuses the `Finding.evidence` reference vocabulary (file:line, test id, cmd exit).
      Dependencies: AC-2-1
- [ ] T-AC-2-3 — Populate `supersedes` with prior `decision_id` when a decision reverses/replaces one.
      Dependencies: AC-2-1
- [ ] T-AC-2-4 — On every allow/deny verdict, append a `Decision` to the run decision log (new `TeamsLayout` path method).
      Dependencies: AC-2-1
- [ ] T-AC-2-5 — Raise (do not silently proceed) if the decision-log write fails.
      Dependencies: AC-2-4

## User Story 3 — PolicyEngine makes the final allow/deny

- [ ] T-AC-3-1 [P] — Create `strands_teams/policy.py` with `PolicyEngine.authorize_transition(actor, transition) -> PolicyVerdict(allowed, failed_gate, reason)`.
- [ ] T-AC-3-2 — Capability gate: deny with `failed_gate="capability"` when `AuthorityGrant` lacks the required capability.
      Dependencies: AC-3-1
- [ ] T-AC-3-3 — Separation-of-duties gate: deny with `failed_gate="separation_of_duties"` when actor == sole approver.
      Dependencies: AC-3-1
- [ ] T-AC-3-4 — Required-approvals gate: deny with `failed_gate="required_approvals"` when valid token count < threshold.
      Dependencies: AC-3-1
- [ ] T-AC-3-5 — Open-blocking-finding gate: deny with `failed_gate="open_blocking_finding"` when an open `blocking=True` `Finding` affects the target.
      Dependencies: AC-3-1
- [ ] T-AC-3-6 — Allow (`allowed=True`) only when every gate passes for a non-human-gated transition.
      Dependencies: AC-3-2, AC-3-3, AC-3-4, AC-3-5
- [ ] T-AC-3-7 — Read grants/approvals/findings/revisions from `invocation_state` by reference; never mutate it.
      Dependencies: AC-3-1

## User Story 4 — Human gates as first-class protocol states

- [ ] T-AC-4-1 — Human-required gate: for a human-gated kind, emit pending `HumanDecision`, deny with `failed_gate="human_required"`.
      Dependencies: AC-1-1
- [ ] T-AC-4-2 — Add `HumanDecision` `FrozenModel` (`decision_id`, `transition`, `prompt`, `options`, `status`, `resolved_by`, `resolved_at`).
      Dependencies: AC-4-1
- [ ] T-AC-4-3 — Runner suspends the outer loop while a pending `HumanDecision` is unresolved (`runner.py` `_run_coordinator`/`run_teams`).
      Dependencies: AC-4-1
- [ ] T-AC-4-4 — On approving resolution, resume and re-invoke `authorize_transition` before the action executes.
      Dependencies: AC-4-3
- [ ] T-AC-4-5 — Persist the pending `HumanDecision` to disk and return node control rather than blocking past `node_timeout`.
      Dependencies: AC-4-1
- [ ] T-AC-4-6 — On rejecting resolution, abandon the transition and record a denying `Decision`.
      Dependencies: AC-4-2

## User Story 5 — Approvals go stale on material change

- [ ] T-AC-5-1 — Hash-mismatch staleness: token whose `revision_content_hash` != current `content_hash` is treated stale.
      Dependencies: AC-1-2
- [ ] T-AC-5-2 — On supersede, mark token stale when `semantic_diff` touches a facet in `invalidation_rules`.
      Dependencies: AC-5-1
- [ ] T-AC-5-3 — Carry approval forward when `semantic_diff` is empty for every facet in `invalidation_rules`.
      Dependencies: AC-5-2
- [ ] T-AC-5-4 — Deny with `failed_gate="required_approvals"` when all bound tokens are stale.
      Dependencies: AC-5-1

## User Story 6 — Structural enforcement of consequential transitions

- [ ] T-AC-6-1 [P] — Add `TransitionRequest` `FrozenModel` (`actor`, `kind`, `target_revision_id`, `consequential`, `presented_tokens`).
- [ ] T-AC-6-2 — Refuse a consequential transition with no valid revision-bound token.
      Dependencies: AC-3-6, AC-6-1
- [ ] T-AC-6-3 — Refuse a consequential transition while any open blocking `Finding` affects the target, regardless of approvals.
      Dependencies: AC-3-5, AC-6-1
- [ ] T-AC-6-4 — Add `PolicyGateHook` in `strands_teams/hooks.py` that calls `authorize_transition` on `BeforeToolCallEvent` and blocks the call when denied.
      Dependencies: AC-6-2, AC-6-3
- [ ] T-AC-6-5 — Confine the gated coding-harness action to a git-worktree `cwd` with prod-denying grants; Claude `global.anthropic.claude-opus-4-8` or Codex `openai.gpt-5.6-sol` (`amazon-bedrock`, per-run `CODEX_HOME`).
      Dependencies: AC-6-4
- [ ] T-AC-6-6 — Export `PolicyEngine`, `PolicyVerdict`, `Decision`, `ApprovalToken`, `HumanDecision`, `TransitionRequest` from `strands_teams/__init__.py`.
      Dependencies: AC-6-2

## User Story 7 — Test coverage for the gate matrix

- [ ] T-AC-7-1 [P] — Test: allow/deny matrix — one pass + one deny per gate (capability, SoD, required-approvals, open-blocking-finding, human-required).
- [ ] T-AC-7-2 [P] — Test: separation-of-duties — actor==sole-approver denied with `failed_gate="separation_of_duties"`.
- [ ] T-AC-7-3 [P] — Test: stale-approval — content-hash mismatch rejected, not counted toward threshold.
- [ ] T-AC-7-4 [P] — Test: human-gate suspension — pending `HumanDecision` emitted, loop suspends, resumes on resolution.
- [ ] T-AC-7-5 [P] — Regression test: `analyze` use-case with no consequential transitions completes unchanged; `PolicyEngine` present but never denies.

---

## Notes on parallelism

- `[P]` model-only tasks (T-AC-1-1, T-AC-3-1, T-AC-6-1) seed distinct new
  symbols/modules and are safe to start in isolated worktrees. T-AC-3-1 creates a
  new file (`policy.py`); T-AC-1-1/T-AC-6-1 both touch `models.py` — treated `[P]`
  against non-`models.py` tasks only; sequence them within the `models.py` file.
- All User-Story-7 tests are `[P]` — disjoint test files under
  `packages/strands-teams/tests/`.
- Most `PolicyEngine` gate tasks (T-AC-3-2..3-7, 5-x, 6-x) share `policy.py` and
  are therefore dependency-chained, not `[P]`.
