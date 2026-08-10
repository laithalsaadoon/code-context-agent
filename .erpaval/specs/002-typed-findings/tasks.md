# Tasks — 002-typed-findings

Derived from `spec.md` (Phase P1-findings). One task per coherent AC cluster. `[P]` marks parallel-safe tasks (isolated worktree, no file overlap with other `[P]` tasks in the same wave). `Dependencies:` lists ACs that must complete before the task starts. Waves are derivable from the dependency closure.

> Regeneration note: hand-edits here are destroyed on regeneration — edit `spec.md` instead.

---

## Implementation record (Act — feat/astr-north-star)

All checks green: `ruff check` clean, `ruff format` clean, `ty check packages` = "All checks passed!", `pytest -q` = **288 passed**.

Files created:
- `packages/strands-teams/src/strands_teams/evidence_gate.py` — evidence gate bound to the rubric's `FILE_LINE_PATTERN` citation-density concept. `finding_passes_gate` (evidence_gate.py:66), `evaluate_evidence_gate` -> `EvidenceGateResult` (evidence_gate.py:79), `gate_findings` dict wrapper (evidence_gate.py:100), `_evidence_ref_count` reuses `FILE_LINE_PATTERN` for inline citations (evidence_gate.py:37). (AC-5-1..5-5, AC-8-1 return shape)
- `packages/strands-teams/src/strands_teams/findings_io.py` — single producer/consumer of the `findings.json` shape via `TypeAdapter(list[Finding])`: `serialize_findings` (findings_io.py:35), `parse_findings` (validate_json at parse boundary, findings_io.py:44), `coerce_findings` (validate_python for tool dict args, findings_io.py:56), `load_findings` (findings_io.py:67), `findings_as_dicts` (json-mode dicts, findings_io.py:77). (AC-3-2, AC-8-4)
- `packages/strands-teams/tests/test_findings.py` — model + evidence-gate unit tests (AC-1-1..1-5, AC-5-1/5-5, AC-7-1).

Files modified:
- `packages/strands-teams/src/strands_teams/models.py` — added `Evidence` (models.py:151), `Finding` (models.py:168) FrozenModels + `EvidenceType`/`Severity`/`Disposition` aliases (models.py:140-148) + `_utcnow` factory (models.py:21). (AC-1-1..1-5)
- `packages/strands-teams/src/strands_teams/layout.py` — `findings_json_name` field (layout.py:58) + `findings_json_path(team_id)` method (layout.py:76); dataclass stays frozen/hashable. (AC-2-1..2-3)
- `packages/strands-teams/src/strands_teams/write_file.py` — `make_write_findings(layout, team_id)` (write_file.py:88): validates via `coerce_findings`, serializes to `findings_json_path`, returns `{status, path, count, evidence_gate}`; schema-invalid returns `{"status":"error"}` without writing. (AC-3-1, AC-3-2, AC-3-4, AC-5-2, AC-8-1)
- `packages/strands-teams/src/strands_teams/teams.py` — `dispatch_team` builds + auto-appends `write_findings` per node (`_build_swarm_nodes`) and adds `findings_json_path` to the response; `_build_augmented_task` Required-Output block names both `findings.md` + `findings.json` and the `Finding` JSON shape (AC-3-3, AC-8-3, no Swarm-arg change); `read_team_findings` returns parsed typed `Finding[]` (source="typed") + `finding_count` + `evidence_gate`, markdown fallback marked source="markdown" (AC-4-1..4-4, AC-5-3, AC-8-2/8-4); `_try_load_typed_findings` never raises on corrupt/absent JSON.
- `packages/strands-teams/src/strands_teams/__init__.py` — export `Evidence`, `Finding`, `EvidenceType`, `Severity`, `Disposition`, `EvidenceGateResult`, `evaluate_evidence_gate`, `gate_findings`, `make_write_findings` + `__all__`. (AC-1-4)
- `packages/code-context-agent/src/code_context_agent/usecase.py` — `_FINDINGS_FORMAT_DIRECTIVE` (usecase.py:180) additively describes the typed `findings.json` shape while still requiring markdown. (AC-6-2)
- `packages/strands-teams/tests/test_layout.py` — `findings_json_path` assertions (AC-7-2).
- `packages/strands-teams/tests/test_teams.py` — typed round-trip seam test + markdown/corrupt fallback + listing-typed-presence tests (AC-7-3, AC-7-4).

Regression (AC-6-1, AC-6-3): existing dispatch/read/write_artifact tests unchanged and green; markdown fallback preserves the `CONTEXT.md`/`BUNDLE.*` synthesis input path when a team omits `findings.json`.

---

## Wave 0 — Typed models (foundation, no dependents yet)

### T-typed-models — `Finding` + `Evidence` FrozenModels + export
Covers: AC-1-1, AC-1-2, AC-1-3, AC-1-4, AC-1-5
[P]
Dependencies: none
Add `Evidence` (`type` Literal + non-empty `uri`) and `Finding` (`severity`, `blocking`, `disposition`, `statement`, `affected_artifacts`, `evidence[]`, `confidence` in `[0,1]`, `created_by`, `created_at`/`updated_at`) to `strands_teams/models.py`, both subclassing `FrozenModel` (immutable, `extra="forbid"`). Export both from `strands_teams/__init__.py` and add to `__all__`. Literal/range violations raise `ValidationError`.

### T-layout-json — `TeamsLayout.findings_json_path`
Covers: AC-2-1, AC-2-2, AC-2-3
[P]
Dependencies: none
Add `findings_json_name = "findings.json"` field and `findings_json_path(team_id)` method to the frozen `TeamsLayout` dataclass, resolving under the same `team_dir(team_id)` as `findings_path`. Keep the dataclass frozen and hashable.

---

## Wave 1 — Write path (depends on models + layout)

### T-write-path — Typed-findings write via write_file / write_artifact + dispatch required-output
Covers: AC-3-1, AC-3-2, AC-3-3, AC-3-4, AC-8-1, AC-8-3
Dependencies: AC-1-2, AC-2-2
Confined `write_file` accepts the `findings_json_path` target (unchanged `output_root` confinement). Add a typed-findings write path on `write_artifact` (or a delegated helper) that serializes `Finding[]` to `findings.json` via `model_dump_json`-equivalent and returns `{status, path, count}`; schema-invalid content returns `{"status":"error"}` (no unvalidated write). Extend the `dispatch_team` Required-Output block (`teams.py:360-375`) to name both `findings_path` and `findings_json_path` and describe the `Finding` JSON shape — no change to `Swarm` construction / `invoke_async` / handoff-detection settings. Return `str`/`FrozenModel`, never a tool-result envelope dict.

---

## Wave 2 — Read path + evidence gate (depends on write path + models)

### T-read-findings — `read_team_findings` returns parsed `Finding[]` with markdown fallback
Covers: AC-4-1, AC-4-2, AC-4-3, AC-4-4, AC-8-2, AC-8-4
Dependencies: AC-1-2, AC-2-2, AC-3-2
`read_team_findings` returns parsed typed `Finding[]` (+ `finding_count`) when `findings.json` exists and validates; falls back to raw `findings.md` (marked as fallback) when absent/invalid; extends the no-`team_id` listing with typed-present + count without dropping `has_findings`/`findings_lines`/`metadata`. Validate via pydantic over parsed JSON at the parse boundary; never raise on corrupt files; treat `invocation_state` read-only.

### T-evidence-gate — Blocking-without-evidence gate bound to citation-density rubric
Covers: AC-5-1, AC-5-2, AC-5-3, AC-5-4, AC-5-5
Dependencies: AC-1-2, AC-3-2, AC-4-1
Evidence-gate check: pass if non-blocking or ≥1 evidence ref; fail (naming the finding) if `blocking=true` with empty `evidence`. Wire it into both the write result (`evidence_gate` warning) and the `read_team_findings` result (`evidence_gate` summary). Reuse the `NarrativeRubric` `FILE_LINE_PATTERN`/`specificity` citation-density concept rather than a second heuristic. Non-blocking + no evidence passes.

---

## Wave 3 — Regression (codebase-analysis use-case unchanged output)

### T-usecase-regression — Same CONTEXT.md/BUNDLE.* set + directive update
Covers: AC-6-1, AC-6-2, AC-6-3
Dependencies: AC-3-3, AC-4-1
Update the use-case `findings_format_directive` (`code_context_agent/usecase.py:271`, `_FINDINGS_FORMAT_DIRECTIVE` L178-197) to keep requiring markdown `findings.md` and additionally describe the typed `findings.json` shape. Verify `analyze <repo>` still emits `CONTEXT.md` + `BUNDLE.*.md` + `analysis_result.json`; a team that writes only `findings.md` still completes via the markdown fallback.

---

## Wave 4 — Tests (last; depend on all behavior)

### T-model-tests — `Finding`/`Evidence` model unit tests
Covers: AC-7-1
[P]
Dependencies: AC-1-2, AC-1-5
Unit tests in `strands-teams/tests/` for valid construction, immutability, and Literal/`confidence`-range rejection.

### T-layout-tests — `findings_json_path` layout test
Covers: AC-7-2
[P]
Dependencies: AC-2-2
Assert `findings_json_path(team_id)` resolves under the same team dir as `findings_path` and uses the `findings_json_name` default (extend `test_layout.py`).

### T-seam-test — Team-findings typed round-trip seam test
Covers: AC-7-3, AC-7-4
Dependencies: AC-4-1, AC-4-2, AC-4-4, AC-5-3
Write real `Finding[]` to a `findings.json` via the write path, then `read_team_findings`; assert parsed == written and the `evidence_gate` flags a seeded blocking-without-evidence finding. Seed a markdown-only team and a corrupt-`findings.json` team; assert markdown fallback (marked) and no raise.
