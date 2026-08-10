---
slug: artifact-revisions
sequence: 005
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

Phase P4-revisions of the ASTR north-star build (`docs/planning/astr-north-star-plan.md` §4 Phase 4, mapping to ASTR §4.8, §6.4, §12.6). It makes runs **iterative without redoing valid work**: artifacts become immutable, content-addressed revisions with lineage; reviews/findings bind a specific revision; and a material change to an artifact invalidates only the reviews whose reviewed *facets* changed. This lands after Phase 3's autonomous-coding loops make re-runs the common case (plan §4 Phase 4 rationale: "Worth it once runs become iterative").

Two capabilities ship together:

1. **Immutable revisions + a mutable alias pointer**, both owned by `TeamsLayout` — the single source of every artifact path (`packages/strands-teams/src/strands_teams/layout.py:27`, the frozen dataclass; existing path methods at `:63-109`). Promotion of the alias is an **optimistic compare-and-set (CAS)** that raises `StaleArtifactError` on mismatch.
2. **Facet-level selective review invalidation** via `semantic_diff`, so re-analysis after a code change reuses reviews whose facets are untouched.

### Terms

- **ArtifactRevision** — an immutable `FrozenModel` record of one version of a logical artifact: `artifact_id`, `revision_id`, `content_hash`, `artifact_type`, `logical_name`, `produced_by`, `parents[]`, `supersedes[]`, `classification`, `created_at`. `FrozenModel` (immutable data-transfer base) is defined at `packages/strands-teams/src/strands_teams/models.py:40`; `GeneratedFile`/`Bundle` at `:58`/`:66` are the pattern to follow.
- **alias pointer** — a mutable `logical_name → current revision_id` map owned by `TeamsLayout`, resolved to a path the same way the planning sentinel is (`layout.py:107` `planning_sentinel_path`). The revision store is content-addressed; the alias is the only mutable pointer into it.
- **review binding** — a review/finding records the `revision_id` it was produced against. `Finding` (severity/blocking/disposition/evidence, ASTR §6.6) is delivered by **Phase 1** (`.erpaval/specs/002-typed-findings`); this phase references it as given and does **not** redefine it (plan §4 Phase 1; plan §7: "`SpecialistResult.findings` reuses them").
- **semantic_diff** — a facet-level diff of two `ArtifactRevision`s returning the set of changed facets; `invalidates_review` consults it.
- **StaleArtifactError** — raised by `promote_artifact` when `expected_current_revision` does not match the alias's stored current revision.

### Grounding facts (research-coding-runtimes.yaml, `.erpaval/sessions/session-8c56b2/`) encoded where this phase touches them

- **FrozenModel return / JSON norm** — tools return strings; a `FrozenModel` is serialized via `model_dump_json()` and the caller parses JSON (digest `strands_tool_pattern` line 26). `ArtifactRevision` is a `FrozenModel`; the promotion tool returns its `model_dump_json()`.
- **invocation_state is a by-reference dict, read-only under concurrency** — "Treat contents read-only inside concurrently-executing node tools (shared by reference)" and "Do not mutate invocation_state from concurrent tools" (digest lines 22, 30). This is exactly why alias promotion is a durable CAS against the layout store, not a mutation of shared in-memory state.
- **native Swarm concurrency** — "3 concurrent tool calls in ONE turn run truly concurrently (3 subprocesses)" (digest line 28); `dispatch_team` awaits `swarm.invoke_async(...)` (`teams.py:290`). Concurrent promotions of the same `logical_name` must be serialized by the CAS, not by luck.
- **TeamsLayout is a frozen, picklable dataclass safe in invocation_state** (`layout.py:14-16` module docstring; passed via `TeamInvocationState` at `teams.py:41-69`, key `layout`). New revision/alias methods extend that same descriptor; no new global state.
- **Bedrock model ids / Codex built-in `amazon-bedrock` provider** (digest `claude_agent_sdk_bedrock`, `codex_gpt56_bedrock`) — not touched by this phase; they belong to Phase 3 (`.erpaval/specs/004-coding-runtime`). No AC here encodes them, by design (plan §3: model/runtime specifics live in `strands-teams-runtimes`, not core).

---

## User Story 1 — Immutable, content-addressed artifact revisions owned by the layout

### Acceptance Criteria

AC-1-1 [P]
Ubiquitous: The `strands_teams` core shall expose an `ArtifactRevision` type that subclasses `FrozenModel` (`models.py:40`) and carries the fields `artifact_id`, `revision_id`, `content_hash`, `artifact_type`, `logical_name`, `produced_by`, `parents` (list), `supersedes` (list), `classification`, and `created_at`, such that any attempt to assign to a field after construction raises a validation error.

AC-1-2
Dependencies: AC-1-1
Ubiquitous: The `ArtifactRevision` `content_hash` shall be a deterministic function of the revision's content alone, such that two revisions built from byte-identical content produce equal `content_hash` values and two revisions built from differing content produce differing `content_hash` values.

AC-1-3 [P]
Ubiquitous: The `TeamsLayout` descriptor (`layout.py:27`) shall own the revision store and expose a method that resolves the on-disk path for a given `(logical_name, revision_id)` from the `output_root`, with no revision or alias path string literal duplicated in dispatch/read/write/promotion code.

AC-1-4
Dependencies: AC-1-1, AC-1-3
Event-driven: When an `ArtifactRevision` is persisted, the system shall write it to its layout-resolved path keyed by `content_hash`/`revision_id` and return the stored revision, so the revision is addressable by later reads.

AC-1-5
Dependencies: AC-1-1, AC-1-4
Unwanted behavior: If a write is attempted for an already-persisted `revision_id` whose content differs from the stored revision's `content_hash`, then the system shall reject the write with an error and leave the previously stored revision unchanged.

AC-1-6
Dependencies: AC-1-1
Ubiquitous: The `strands_teams` package `__init__` (`__init__.py:12-40`) shall export `ArtifactRevision`, `StaleArtifactError`, and the promotion tool so a use case can reference them without importing private modules.

---

## User Story 2 — Optimistic alias promotion (compare-and-set)

### Acceptance Criteria

AC-2-1
Dependencies: AC-1-3
Ubiquitous: The `TeamsLayout` descriptor shall own a mutable alias pointer that maps each `logical_name` to its current `revision_id`, resolved to a durable path from `output_root` in the same manner as `planning_sentinel_path` (`layout.py:107`).

AC-2-2
Dependencies: AC-2-1, AC-1-1
Event-driven: When `promote_artifact(logical_name, expected_current_revision, proposed_revision)` is called and `expected_current_revision` equals the alias's stored current revision for `logical_name`, then the system shall atomically set the alias to `proposed_revision` and record the prior revision in the proposed revision's `supersedes` lineage.

AC-2-3
Dependencies: AC-2-2
Unwanted behavior: If `expected_current_revision` does not equal the alias's stored current revision for `logical_name`, then `promote_artifact` shall raise `StaleArtifactError` and leave the alias pointer unchanged.

AC-2-4
Dependencies: AC-2-2
Event-driven: When `promote_artifact` is called for a `logical_name` that has no current alias and `expected_current_revision` is the sentinel for "no current revision", then the system shall set the alias to `proposed_revision` as the first revision.

AC-2-5
Dependencies: AC-2-2
Complex: While two tool calls in the same turn attempt to promote the same `logical_name` concurrently (invocation_state is shared by reference and read-only per research-coding-runtimes.yaml lines 22, 30), when both pass the same `expected_current_revision`, the compare-and-set shall admit at most one promotion and raise `StaleArtifactError` for the other, rather than relying on mutation of shared in-memory state.

AC-2-6
Dependencies: AC-2-2
Event-driven: When `promote_artifact` completes, the promotion tool shall return a JSON string (a `FrozenModel` serialized via `model_dump_json()`, per research-coding-runtimes.yaml line 26) reporting the `logical_name`, the new current `revision_id`, and the superseded revision.

---

## User Story 3 — Revision-bound reviews and facet-level selective invalidation

### Acceptance Criteria

AC-3-1
Dependencies: AC-1-1
Ubiquitous: A review or `Finding` (the typed finding delivered by Phase 1, `.erpaval/specs/002-typed-findings`, referenced as given) shall record the `revision_id` of the `ArtifactRevision` it was produced against as its review binding.

AC-3-2
Dependencies: AC-1-1
Ubiquitous: The system shall provide a `semantic_diff(old_revision, new_revision)` function that returns the set of artifact facets whose content changed between the two revisions.

AC-3-3
Dependencies: AC-3-1, AC-3-2
Event-driven: When `invalidates_review(old_binding, new_revision, review)` is called and the facets returned by `semantic_diff` intersect the facets the `review` covers, then the function shall report the review as invalidated.

AC-3-4
Dependencies: AC-3-3
State-driven: While the facets changed by `semantic_diff` do not intersect the facets a bound `review` covers, `invalidates_review` shall report the review as still valid so it is reused rather than recomputed.

AC-3-5
Dependencies: AC-3-3
Unwanted behavior: If `invalidates_review` is called with a `new_revision` whose lineage does not supersede the review's bound `revision_id`, then the function shall report an error rather than silently treating the review as valid or invalidated.

AC-3-6
Dependencies: AC-3-3, AC-2-2
Event-driven: When a re-run promotes a new revision of a `logical_name`, the system shall carry forward every bound review that `invalidates_review` reports as still valid and re-open only the reviews it reports as invalidated.

---

## Cross-phase dependencies assumed

- **Phase 1 `Finding`** (`.erpaval/specs/002-typed-findings`) exists and is importable from `strands_teams.models`; AC-3-1 binds a `revision_id` onto it and does not redefine it (plan §4 Phase 1; §7).
- **Phase 3 coding loops** (`.erpaval/specs/004-coding-runtime`) are what make re-runs common; this phase does not depend on that code shipping first, but its value (AC-3-6 reuse) is realized once iterative runs exist (plan §4 Phase 4 rationale).
- No dependency on Phase 5/6 (durable loop, approvals); the alias CAS is deliberately in-layout and synchronous.
