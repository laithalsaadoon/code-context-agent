# Tasks — 005-artifact-revisions

Derived from `spec.md`. One task per coherent AC cluster. `[P]` marks parallel-safe tasks (isolated worktree, no file overlap with other `[P]` tasks in the same wave). `Dependencies:` lists ACs that must complete before the task starts. Waves are derivable from the dependency closure.

> Regeneration note: hand-edits here are destroyed on regeneration — edit `spec.md` instead.

Phase P4-revisions of the ASTR north-star build (`docs/planning/astr-north-star-plan.md` §4 Phase 4). Builds on Phase 1 `Finding` (`.erpaval/specs/002-typed-findings`), referenced as given.

---

## Wave 0 — Immutable revision model

### T-AC-1-1-2 — ArtifactRevision FrozenModel + deterministic content hash
Covers: AC-1-1, AC-1-2
[P]
Dependencies: none
Add `ArtifactRevision(FrozenModel)` to `packages/strands-teams/src/strands_teams/models.py` (follow `GeneratedFile`/`Bundle` at `models.py:58`/`:66`) with fields `artifact_id`, `revision_id`, `content_hash`, `artifact_type`, `logical_name`, `produced_by`, `parents: list`, `supersedes: list`, `classification`, `created_at`. Frozen: field assignment after construction raises. `content_hash` is a deterministic function of content alone (identical content → equal hash; differing content → differing hash).

---

## Wave 1 — Layout owns the revision store + alias

### T-AC-1-3-4-5 — Revision store path resolution + content-addressed persistence
Covers: AC-1-3, AC-1-4, AC-1-5
Dependencies: AC-1-1
Extend `TeamsLayout` (`layout.py:27`, frozen dataclass) with a method resolving the on-disk path for `(logical_name, revision_id)` from `output_root` — no revision/alias path literal duplicated in dispatch/read/write/promotion code. Persist a revision keyed by `content_hash`/`revision_id` and return the stored revision. Reject a re-write of an existing `revision_id` whose content differs, leaving the stored revision unchanged.

### T-AC-2-1 — Mutable alias pointer owned by the layout
Covers: AC-2-1
Dependencies: AC-1-3
Add the mutable `logical_name → current revision_id` alias pointer to `TeamsLayout`, resolved to a durable path from `output_root` the same way `planning_sentinel_path` is (`layout.py:107`).

---

## Wave 2 — Optimistic promotion (CAS) + exports

### T-AC-2-2-through-6 — promote_artifact compare-and-set + StaleArtifactError
Covers: AC-2-2, AC-2-3, AC-2-4, AC-2-5, AC-2-6
Dependencies: AC-2-1, AC-1-1
Implement `promote_artifact(logical_name, expected_current_revision, proposed_revision)` as an atomic CAS against the layout alias store: on match, set alias to `proposed_revision` and record the prior revision in `supersedes`; on mismatch, raise `StaleArtifactError` and leave the alias unchanged; on no-current + sentinel, set as first revision. Concurrency: at most one of two same-turn same-`logical_name` promotions with equal `expected_current_revision` succeeds; the other raises `StaleArtifactError` — durable CAS, not shared-memory mutation (research-coding-runtimes.yaml lines 22, 30). Return a `FrozenModel` serialized via `model_dump_json()` (line 26) reporting `logical_name`, new current `revision_id`, superseded revision.

### T-AC-1-6 — Package exports
Covers: AC-1-6
Dependencies: AC-1-1, AC-2-2
Export `ArtifactRevision`, `StaleArtifactError`, and the promotion tool from `strands_teams/__init__.py` (`__init__.py:12-40`, `__all__` at `:45`).

---

## Wave 3 — Facet-level selective review invalidation

### T-AC-3-1 — Bind reviews/findings to a revision
Covers: AC-3-1
Dependencies: AC-1-1
Record the `revision_id` an `ArtifactRevision` was produced against onto the review / Phase 1 `Finding` (`.erpaval/specs/002-typed-findings`, referenced as given — do not redefine).

### T-AC-3-2 — semantic_diff facet set
Covers: AC-3-2
[P]
Dependencies: AC-1-1
Implement `semantic_diff(old_revision, new_revision)` returning the set of artifact facets whose content changed between two revisions.

### T-AC-3-3-4-5 — invalidates_review facet-intersection logic
Covers: AC-3-3, AC-3-4, AC-3-5
Dependencies: AC-3-1, AC-3-2
Implement `invalidates_review(old_binding, new_revision, review)`: invalidate iff `semantic_diff` facets intersect the review's covered facets; otherwise report still-valid (reuse). Error when `new_revision` lineage does not supersede the review's bound `revision_id`.

### T-AC-3-6 — Re-run carry-forward of still-valid reviews
Covers: AC-3-6
Dependencies: AC-3-3, AC-2-2
On promoting a new revision of a `logical_name`, carry forward every review `invalidates_review` reports still-valid and re-open only the invalidated ones.

---

## Test plan (from spec, plan §4 Phase 4 "Tests")

- **Revision immutability** — AC-1-1 (field assignment raises), AC-1-2 (deterministic hash), AC-1-5 (no silent overwrite).
- **Alias promotion CAS + stale error** — AC-2-2 (match promotes + supersedes), AC-2-3 (mismatch raises `StaleArtifactError`), AC-2-4 (first revision), AC-2-5 (concurrent same-turn, one wins).
- **Selective invalidation** — AC-3-3 (facet intersection invalidates), AC-3-4 (disjoint facets stay valid), AC-3-6 (carry-forward reuse), AC-3-5 (lineage guard).

---

## Implementation record (P4-revisions)

Status: **IMPLEMENTED** — all waves landed; `uv run ruff check packages && uv run ruff format packages && uv run ty check packages` clean; `uv run pytest -q` = **403 passed** (24 new in `test_revisions.py`, no regressions).

Additive only: no prior-phase type redefined; `Finding` extended (not replaced); the codebase-analysis use-case and all P1–P3 tests keep passing. New deps: none (SHA-256/`fcntl`/`json` are stdlib) — nothing added to `strands-teams` core deps or `strands-teams-runtimes`.

### Files modified

- `packages/strands-teams/src/strands_teams/models.py`
  - `ArtifactRevision(FrozenModel)` + `from_content()` classmethod — `models.py:384` (class), immutable content-addressed revision with all spec fields + per-facet hash map (AC-1-1, AC-1-2, AC-3-2).
  - `compute_content_hash()` / `compute_content_hash_from_facet_hashes()` / `_facet_hashes()` — deterministic content-only hashing (AC-1-2), `models.py:363`/`:349`/`:339`.
  - `NO_CURRENT_REVISION` sentinel — `models.py:330` (AC-2-4).
  - `StaleArtifactError` — `models.py:506` (AC-2-3); `RevisionConflictError` — `models.py:529` (AC-1-5); `PromotionResult(FrozenModel)` — `models.py:554` (AC-2-6).
  - `Finding.reviewed_revision` (`models.py:299`) + `Finding.reviewed_facets` — revision binding added to the P1 finding (AC-3-1).
- `packages/strands-teams/src/strands_teams/layout.py`
  - New fields `revisions_subdir`, `alias_name`; methods `revisions_dir()`, `revision_path()`, `alias_path()`, `persist_revision()`, `load_revision()`, `current_revision_id()`, `promote_revision()` (CAS), `_locked_aliases()` (`fcntl.flock`), `_read_aliases()`, `_write_aliases()` (atomic replace), module-level `_slug()`. All revision/alias paths single-sourced here (AC-1-3, AC-1-4, AC-1-5, AC-2-1, AC-2-2, AC-2-3, AC-2-4, AC-2-5).
- `packages/strands-teams/src/strands_teams/__init__.py`
  - Exported `ArtifactRevision`, `StaleArtifactError`, `RevisionConflictError`, `PromotionResult`, `NO_CURRENT_REVISION`, `compute_content_hash`, `Review`, `ReviewVerdict`, `ReviewReconciliation`, `semantic_diff`, `invalidates_review`, `reconcile_reviews`, `promote_artifact` (AC-1-6).

### Files created

- `packages/strands-teams/src/strands_teams/revisions.py`
  - `Review(FrozenModel)`, `ReviewBinding` Protocol, `ReviewVerdict`, `ReviewReconciliation`; `semantic_diff()` (AC-3-2), `invalidates_review()` (AC-3-3/4/5), `reconcile_reviews()` (AC-3-6), `_lineage_supersedes()` (AC-3-5 guard); `promote_artifact` `@tool(context=True)` wrapping the layout CAS, returns `PromotionResult.model_dump_json()` / a `{"status":"stale",...}` result on CAS failure (AC-2-2, AC-2-6).
- `packages/strands-teams/tests/test_revisions.py`
  - 24 tests: revision immutability + deterministic hash + content-addressed persist/conflict (US1); first-promotion sentinel, matching CAS + supersedes, stale error, concurrent same-turn CAS via `ThreadPoolExecutor` (one wins) (US2); `semantic_diff` changed-facet set, facet-intersection invalidation, disjoint reuse, whole-artifact review, lineage-mismatch error, `reconcile_reviews` partition incl. a bound `Finding` (US3). Zero network — tool exercised via `__wrapped__` + tmp_path layout.

### Design notes / tradeoffs

- **CAS is durable, not in-memory.** `promote_revision` serializes on an `fcntl.flock` advisory lock over a `<alias>.lock` sidecar and rewrites the alias JSON atomically (`os.replace`). This satisfies AC-2-5 without mutating the by-reference, read-only `invocation_state` dict (research-coding-runtimes.yaml lines 22, 30). Concurrency test uses threads (flock is process/thread-fair on the fd) to prove exactly one promotion wins.
- **Content-addressing invariant.** `content_hash` is folded from per-facet hashes so a persisted record validates without the raw content (model validator recomputes and rejects a tampered record). `revision_id` defaults to `content_hash`; `promote_artifact` strips any caller-supplied `content_hash`/`revision_id`/`logical_name` and derives them from content — the LLM cannot forge identity.
- **Facets enable selective invalidation.** A review with empty `reviewed_facets` is treated as covering the whole artifact (any change invalidates); a facet-scoped review is reused unless one of its facets changed. Lineage guard: a `new_revision` that neither is nor supersedes/parents `old_binding` (or whose review names a different bound revision) yields `status="error"` rather than a silent verdict (AC-3-5).
