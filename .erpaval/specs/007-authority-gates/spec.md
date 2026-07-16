---
slug: authority-gates
sequence: 007
phase: P6-gates
maps_to_astr: "§14 (authority/approvals/policy), §11 (decision records), §6.7–6.8 (Decision/ApprovalToken/policy gate)"
builds_on: "P1/P3 Finding (strands_teams.models), P2/P3 AuthorityGrant (spec 003-task-authority), P4 ArtifactRevision (spec 005-artifact-revisions)"
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

## Context & grounding

This phase adds the ASTR governance layer to `strands-teams`: an `ApprovalToken`,
a `Decision` record, a `HumanDecision` protocol state, and a `PolicyEngine` that
makes the **final** allow/deny on every consequential state transition. The model
proposes; the engine decides (ASTR §14.7 — structural enforcement, not prompt).

**Cross-phase types referenced as given (do NOT redefine here):**

- `Finding` — a `FrozenModel` in `strands_teams.models` with a `blocking: bool`
  field and an `open→…→closed` lifecycle, introduced in Phase 1 (spec
  `002-typed-findings`). This phase reads its `blocking`/lifecycle state for the
  open-blocking-finding gate; it does not alter the type.
- `AuthorityGrant` — the capability grant (`may_write`, `may_approve`,
  `may_create_blocking_finding`) introduced in Phase 2/3 (spec `003-task-authority`).
  This phase reads it for the capability check and separation-of-duties gate.
- `ArtifactRevision` — the immutable, content-hashed revision with `supersedes[]`
  and `produced_by`, introduced in Phase 4 (spec `005-artifact-revisions`), plus
  its `semantic_diff` of touched facets. This phase binds approvals to an exact
  revision and reuses `semantic_diff` for stale-approval invalidation.

**New surfaces this phase introduces:**

- `strands_teams/models.py` (anchor: `FrozenModel` at `models.py:40`;
  `StrictModel` at `models.py:20`) — new `ApprovalToken`, `Decision`,
  `HumanDecision`, `TransitionRequest`, `PolicyVerdict` `FrozenModel`s.
- `strands_teams/policy.py` (NEW module) — `PolicyEngine.authorize_transition`.
- `strands_teams/layout.py` (anchor: `TeamsLayout` at `layout.py:27`) — path
  methods for the approvals store, decision log, and the pending-human-gate sentinel.
- `strands_teams/teams.py` (anchors: `TeamInvocationState` at `teams.py:41`;
  `_require_state` at `teams.py:77`; `dispatch_team` at `teams.py:197`) — the
  `PolicyEngine` is threaded through `invocation_state`, read-only, by reference.
- `strands_teams/runner.py` (anchors: `_run_coordinator` at `runner.py:111`;
  `run_teams` at `runner.py:180`) — the outer loop suspends on a pending
  `HumanDecision` and resumes when it is resolved.
- `strands_teams/settings.py` (anchor: `TeamsSettings` at `settings.py:29`) — policy
  knobs (required-approvals count, signing key source, human-gated transition kinds).
- `strands_teams/__init__.py` (anchor: `__all__` at `__init__.py:45`) — exports.
- `strands_teams/hooks.py` (anchor: `FailFastHook` at `hooks.py:257`;
  `BeforeToolCallEvent` usage) — a `PolicyGateHook` that intercepts consequential
  tool calls and denies them before execution when the engine refuses.

**Research-digest facts encoded (session-8c56b2, conform exactly, do NOT re-research):**

- Per-run state (here: the `PolicyEngine`, active `AuthorityGrant`s, workspace
  `Path`) flows through `@tool(context=True)` → `tool_context.invocation_state`
  as a plain in-process dict passed **by reference**, never serialized; it must be
  treated **read-only** inside concurrently-executing node tools (digest
  §strands_tool_pattern, `invocation_state`).
- Teams run as a native Strands `Swarm` of freshly constructed `Agent` nodes;
  the executor imposes no per-tool timeout, so a human gate must **not** block a
  swarm node past its `node_timeout`/`execution_timeout` — a pending gate persists
  to disk and returns control (digest §strands_tool_pattern).
- The gated consequential action in the reference use-case is an autonomous coding
  harness run on Bedrock: Claude via `global.anthropic.claude-opus-4-8`
  (`CLAUDE_CODE_USE_BEDROCK=1`) or Codex via `openai.gpt-5.6-sol` on the built-in
  `amazon-bedrock` provider (`wire_api="responses"`, `AWS_BEARER_TOKEN_BEDROCK`).
  Isolation is mandatory: git-worktree `cwd`, per-run `CODEX_HOME`, capability
  grants that deny production paths (digest §claude_agent_sdk_bedrock,
  §codex_gpt56_bedrock; plan §2.7, §6).

---

## User Story 1 — Revision-bound, signed ApprovalToken

An approval must name the exact artifact revision it approves, carry a signature
and expiry, and declare the rules that invalidate it — so a stale or forged
approval can never authorize a transition.

### Acceptance Criteria

AC-1-1 [P]
Ubiquitous: The `ApprovalToken` model shall be a `FrozenModel` carrying `token_id`, `approved_transition_kind`, `artifact_revision_id`, `revision_content_hash`, `approver`, `granted_at`, `valid_until`, `signature`, and `invalidation_rules`.

AC-1-2
Dependencies: AC-1-1
Ubiquitous: The `ApprovalToken` shall bind to exactly one `ArtifactRevision` by recording both its `artifact_revision_id` and the revision's immutable `content_hash` at grant time.

AC-1-3
Dependencies: AC-1-1
Event-driven: When an `ApprovalToken` is minted, the system shall compute its `signature` over the canonical serialization of (`token_id`, `approved_transition_kind`, `artifact_revision_id`, `revision_content_hash`, `approver`, `valid_until`) using the configured signing key.

AC-1-4
Dependencies: AC-1-3
State-driven: While an `ApprovalToken`'s `signature` does not verify against the configured signing key, the `PolicyEngine` shall treat the token as absent.

AC-1-5
Dependencies: AC-1-1
If the current time is at or after an `ApprovalToken`'s `valid_until`, then the `PolicyEngine` shall reject that token as expired and shall not count it toward required approvals.

## User Story 2 — Decision record with dissent and lineage

Every consequential choice must be recorded as an auditable `Decision` capturing
the question, the options considered, what was selected, the rationale, the
evidence, any dissent, and what prior decision it supersedes.

### Acceptance Criteria

AC-2-1
Dependencies: AC-1-1
Ubiquitous: The `Decision` model shall be a `FrozenModel` carrying `decision_id`, `question`, `options`, `selected`, `rationale`, `evidence`, `dissent`, `supersedes`, and `decided_at`.

AC-2-2
Dependencies: AC-2-1
Ubiquitous: The `Decision.evidence` field shall hold a list of evidence references (file:line, test id, or command exit) drawn from the same evidence vocabulary as `Finding.evidence`.

AC-2-3
Dependencies: AC-2-1
When a `Decision` is recorded that reverses or replaces an earlier decision, the system shall list the prior `decision_id` in the new record's `supersedes` field.

AC-2-4
Dependencies: AC-2-1
When a `PolicyEngine` allows or denies a consequential transition, the system shall append a `Decision` record capturing the verdict, its rationale, and the evidence to the run's decision log.

AC-2-5
Dependencies: AC-2-4
If writing a `Decision` record to the decision log fails, then the system shall raise rather than silently allowing the transition to proceed unrecorded.

## User Story 3 — PolicyEngine makes the final allow/deny

The engine — never the model — issues the final verdict on a transition by
running an ordered gate stack: capability check, separation-of-duties,
required-approvals, open-blocking-finding, and the human-required gate.

### Acceptance Criteria

AC-3-1 [P]
Ubiquitous: The `PolicyEngine.authorize_transition(actor, transition)` method shall return a `PolicyVerdict` `FrozenModel` carrying `allowed: bool`, `failed_gate`, and a human-readable `reason`.

AC-3-2
Dependencies: AC-3-1
State-driven: While the `actor`'s `AuthorityGrant` lacks the capability the `transition` requires, the `PolicyEngine` shall return a `PolicyVerdict` with `allowed=False` and `failed_gate="capability"`.

AC-3-3
Dependencies: AC-3-1
If the `actor` who requests a transition is the same principal recorded as the `approver` on every `ApprovalToken` presented for it, then the `PolicyEngine` shall return a `PolicyVerdict` with `allowed=False` and `failed_gate="separation_of_duties"`.

AC-3-4
Dependencies: AC-3-1
While the count of valid, non-expired, signature-verified `ApprovalToken`s bound to the target revision is below the configured required-approvals threshold, the `PolicyEngine` shall return a `PolicyVerdict` with `allowed=False` and `failed_gate="required_approvals"`.

AC-3-5
Dependencies: AC-3-1
If any `Finding` affecting the transition's target artifact is `blocking=True` and still open, then the `PolicyEngine` shall return a `PolicyVerdict` with `allowed=False` and `failed_gate="open_blocking_finding"`.

AC-3-6
Dependencies: AC-3-2, AC-3-3, AC-3-4, AC-3-5
Complex: While every gate in the ordered stack passes, when `authorize_transition` is called for a non-human-gated transition, the `PolicyEngine` shall return a `PolicyVerdict` with `allowed=True`.

AC-3-7
Dependencies: AC-3-1
Ubiquitous: The `PolicyEngine` shall read its active `AuthorityGrant`s, approval store, findings, and revisions from `tool_context.invocation_state` by reference and shall not mutate that state.

## User Story 4 — Human gates as first-class protocol states

Where a transition is classified human-required, the engine suspends the run on a
pending `HumanDecision` rather than letting the model self-approve; the loop
resumes only when a human resolves the decision.

### Acceptance Criteria

AC-4-1
Dependencies: AC-1-1
Where a transition kind is in the configured set of human-gated kinds, the `PolicyEngine` shall emit a pending `HumanDecision` and return a `PolicyVerdict` with `allowed=False` and `failed_gate="human_required"`.

AC-4-2
Dependencies: AC-4-1
Ubiquitous: The `HumanDecision` model shall be a `FrozenModel` carrying `decision_id`, `transition`, `prompt`, `options`, `status` (`pending`|`resolved`|`rejected`), `resolved_by`, and `resolved_at`.

AC-4-3
Dependencies: AC-4-1
While a run has an unresolved pending `HumanDecision`, the runner shall suspend the outer loop for that transition rather than advancing it.

AC-4-4
Dependencies: AC-4-3
When a pending `HumanDecision` is resolved with an approving outcome, the runner shall resume the suspended transition and re-invoke `authorize_transition` before the action executes.

AC-4-5
Dependencies: AC-4-1
While a swarm node is executing, the system shall persist a pending `HumanDecision` to disk and return control to the node rather than blocking it beyond its `node_timeout`.

AC-4-6
Dependencies: AC-4-2
If a pending `HumanDecision` is resolved with a rejecting outcome, then the runner shall abandon the gated transition and record a denying `Decision`.

## User Story 5 — Approvals go stale on material change

Because approvals bind an exact revision, a material change to that revision (a
non-empty `semantic_diff` of a facet named in the token's `invalidation_rules`)
must invalidate the approval so stale work is never reused.

### Acceptance Criteria

AC-5-1
Dependencies: AC-1-2
State-driven: While an `ApprovalToken`'s `revision_content_hash` does not equal the current `content_hash` of the artifact it names, the `PolicyEngine` shall treat that token as stale and shall not count it toward required approvals.

AC-5-2
Dependencies: AC-5-1
When a new `ArtifactRevision` supersedes the revision an `ApprovalToken` was bound to, the system shall mark that token stale if the `semantic_diff` touches any facet listed in the token's `invalidation_rules`.

AC-5-3
Dependencies: AC-5-2
Where the `semantic_diff` between the superseded and superseding revisions is empty for every facet in a token's `invalidation_rules`, the `PolicyEngine` shall carry the approval forward and continue to count it as valid.

AC-5-4
Dependencies: AC-5-1
If a transition is requested against a revision for which every bound `ApprovalToken` is stale, then the `PolicyEngine` shall return a `PolicyVerdict` with `allowed=False` and `failed_gate="required_approvals"`.

## User Story 6 — Structural enforcement of consequential transitions

A release or otherwise consequential transition is refused unless a valid,
revision-bound approval token is present and no open blocking finding remains —
enforced structurally at the tool boundary, with the gated action isolated per
the research digest.

### Acceptance Criteria

AC-6-1 [P]
Ubiquitous: The `TransitionRequest` model shall be a `FrozenModel` carrying `actor`, `kind`, `target_revision_id`, `consequential: bool`, and `presented_tokens`.

AC-6-2
Dependencies: AC-3-6, AC-6-1
If a consequential transition is requested with no valid, non-expired, signature-verified, revision-bound `ApprovalToken`, then the `PolicyEngine` shall refuse it with `allowed=False`.

AC-6-3
Dependencies: AC-3-5, AC-6-1
If a consequential transition is requested while any open `blocking=True` `Finding` affects its target artifact, then the `PolicyEngine` shall refuse it regardless of how many approvals are present.

AC-6-4
Dependencies: AC-6-2, AC-6-3
When a coordinator tool attempts a consequential action, the `PolicyGateHook` shall call `authorize_transition` on the `BeforeToolCallEvent` and shall block the tool call before execution if the verdict is `allowed=False`.

AC-6-5
Dependencies: AC-6-4
Where the gated consequential action is an autonomous coding harness run, the system shall confine it to its own git-worktree `cwd` with capability grants that deny production paths, using `global.anthropic.claude-opus-4-8` (Claude on Bedrock) or `openai.gpt-5.6-sol` on the built-in `amazon-bedrock` provider (Codex, per-run `CODEX_HOME`).

AC-6-6
Dependencies: AC-6-2
Ubiquitous: The `PolicyEngine`, its verdict, and the `Decision`/`ApprovalToken`/`HumanDecision` types shall be exported from `strands_teams.__init__` so use cases can wire the gate without reaching into private modules.

## User Story 7 — Test coverage for the gate matrix

The gate behavior is auth-critical and must be pinned by tests: the allow/deny
matrix, separation-of-duties, stale-approval rejection, and human-gate suspension.

### Acceptance Criteria

AC-7-1 [P]
Event-driven: When the `authorize_transition` allow/deny matrix test runs, the system shall assert one pass case and one deny case for each of the five gates (capability, separation-of-duties, required-approvals, open-blocking-finding, human-required).

AC-7-2 [P]
Event-driven: When the separation-of-duties test runs, the system shall assert that a transition whose sole approver is also its actor is denied with `failed_gate="separation_of_duties"`.

AC-7-3 [P]
Event-driven: When the stale-approval test runs, the system shall assert that an approval whose `revision_content_hash` no longer matches the current revision is rejected and does not count toward the required-approvals threshold.

AC-7-4 [P]
Event-driven: When the human-gate suspension test runs, the system shall assert that a human-gated transition emits a pending `HumanDecision`, suspends the loop, and resumes only after the decision is resolved.

AC-7-5 [P]
Event-driven: When the regression test for the existing `analyze` use-case runs, the system shall assert that a run using no consequential transitions completes unchanged with the `PolicyEngine` present but never invoked to deny.
