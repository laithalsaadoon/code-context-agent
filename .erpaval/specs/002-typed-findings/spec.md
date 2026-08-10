---
slug: typed-findings
sequence: 002
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

Phase **P1-findings** of the ASTR north-star build (`docs/planning/astr-north-star-plan.md` §Phase 1 — Typed findings + evidence; maps to ASTR §6.6, §8.3, §4.5). Today every team persists free-text prose inside `findings.md`; the coordinator reads it back as an opaque markdown blob (`packages/strands-teams/src/strands_teams/teams.py:441-449`). This phase adds a **typed `Finding` object with a lifecycle and evidence**, writes it to a `findings.json` alongside the human `findings.md`, and makes `read_team_findings` return parsed `Finding[]` per team. It binds the existing `NarrativeRubric` citation-density concept as an **evidence gate**: a `blocking=true` finding with no evidence is flagged rather than silently accepted.

This phase is **additive and regression-gated**: the codebase-analysis use-case must keep emitting the identical `CONTEXT.md` / `BUNDLE.{area}.md` / `analysis_result.json` set (plan §2.1). No new dependencies.

### Grounding

- **Plan (authoritative phase scope):** `docs/planning/astr-north-star-plan.md` §Phase 1 (lines 106-120), §2 design decisions.
- **Research digest (authoritative API facts — conform exactly, do not re-research):** `.erpaval/sessions/session-8c56b2/research-coding-runtimes.yaml`, keys `strands_tool_pattern` (invocation_state by reference / read-only; tools return strings; `BaseModel -> model_dump_json()` automatically via `_wrap_tool_result`), `architecture` (typed contracts live in `strands_teams` core).
- **Current source (read and anchored):** `strands_teams/models.py` (`FrozenModel` L40-55, `CalibratedJudgment` L112-121), `layout.py` (`findings_name` L57, `findings_path` L71-73), `teams.py` (`dispatch_team` L197-331, `_build_augmented_task` required-output block L360-375, `read_team_findings` L384-449, `write_artifact` L457-503), `write_file.py` (`make_write_file` L32-85), `rubric.py` (`FILE_LINE_PATTERN` L34, `specificity` dimension L177-182), `__init__.py` (exports L12-20, `__all__` L45-77); use-case `code_context_agent/usecase.py` (`findings_format_directive` L271, `_FINDINGS_FORMAT_DIRECTIVE` L178-197).
- **Prior lessons (apply):** `.erpaval/solutions/` — native Swarm dispatch; seam contract tests; docstrings-are-prompts.

### Cross-phase references (given, not redefined here)

- This phase **creates** the `Finding` and `Evidence` types. Per the plan (§7, §Phase 3), `SpecialistResult.findings` (Phase 3) reuses `Finding` as-is; the `Evidence.uri` field is designed to later address an `ArtifactRevision` (Phase 4, §4.8). This phase does not define `SpecialistResult`, `ArtifactRevision`, `TaskContract`, or `AuthorityGrant` — those land in later phases and reference `Finding` as given.
- This phase **builds on the completed** `001-strands-teams` workspace split (Status: COMPLETE): `FrozenModel`, `TeamsLayout`, native-Swarm `dispatch_team`, `read_team_findings`, `make_write_file`, and `NarrativeRubric`/`DEFAULT_NARRATIVE_RUBRIC` all exist and are the extension points.

### Terms

- **typed finding** — an instance of the new `Finding` `FrozenModel`: `severity`, `blocking`, `disposition`, `statement`, `affected_artifacts`, `evidence[]`, `confidence`, `created_by`, and lifecycle timestamps.
- **evidence ref** — an `Evidence` `FrozenModel` carrying a `type` (e.g. `file_line` / `test` / `command` / `artifact` / `url`) and a `uri`.
- **evidence gate** — the rule that a `blocking=true` finding must carry ≥1 evidence ref; findings that violate it are flagged (never silently persisted or collected), reusing the `NarrativeRubric` specificity/citation-density concept (`rubric.py` `FILE_LINE_PATTERN` L34, `specificity` dimension L177-182).
- **findings.json** — the machine-readable typed sidecar written alongside the human `findings.md` in the same per-team directory (`TeamsLayout.team_dir`).

---

## User Story 1 — Typed `Finding` + `Evidence` models in the framework core

### Acceptance Criteria

AC-1-1 [P]
Ubiquitous: The `strands_teams.models` module shall define an `Evidence` model subclassing `FrozenModel` with a `type` field constrained to `Literal["file_line", "test", "command", "artifact", "url", "log"]` and a required non-empty `uri` string field.

AC-1-2 [P]
Ubiquitous: The `strands_teams.models` module shall define a `Finding` model subclassing `FrozenModel` with fields `severity` constrained to `Literal["info", "low", "medium", "high", "critical"]`, `blocking` boolean (default `False`), `disposition` constrained to `Literal["open", "acknowledged", "remediated", "accepted_risk", "rejected", "superseded"]` (default `"open"`), `statement` (non-empty string), `affected_artifacts` (list of strings, default empty), `evidence` (list of `Evidence`, default empty), `confidence` (float in the closed interval `[0.0, 1.0]`), `created_by` (string), and lifecycle timestamps `created_at` and `updated_at`.

AC-1-3
Dependencies: AC-1-1, AC-1-2
State-driven: While the `Finding` model is instantiated, it shall remain immutable and reject unknown fields, inheriting the `FrozenModel` config (`frozen=True`, `extra="forbid"`) so a typed finding is safe to pass by reference through `invocation_state`.

AC-1-4
Dependencies: AC-1-2
Event-driven: When `strands_teams` is imported, the package `__init__` shall export both `Finding` and `Evidence` in its public namespace and list them in `__all__`, alongside the existing `FrozenModel` exports.

AC-1-5
Dependencies: AC-1-2
Unwanted behavior: If a `Finding` is constructed with a `severity` or `disposition` value outside its `Literal` set, or with a `confidence` outside `[0.0, 1.0]`, then model construction shall raise a `pydantic.ValidationError` rather than coercing or accepting the value.

---

## User Story 2 — `TeamsLayout` addresses the typed findings sidecar

### Acceptance Criteria

AC-2-1 [P]
Ubiquitous: The `TeamsLayout` dataclass shall carry a `findings_json_name` attribute defaulting to `"findings.json"`, held as a field so the naming scheme lives in exactly one place, consistent with the existing `findings_name` field (`layout.py:57`).

AC-2-2
Dependencies: AC-2-1
Event-driven: When `findings_json_path(team_id)` is called, the layout shall return `team_dir(team_id) / findings_json_name`, resolving to the same per-team directory as `findings_path(team_id)` (`layout.py:71-73`).

AC-2-3
Dependencies: AC-2-2
Ubiquitous: The `TeamsLayout` shall remain a frozen, hashable dataclass after the new field and method are added, so it stays safe to place into `invocation_state`.

---

## User Story 3 — Typed-findings write path (write_file / write_artifact / dispatch required-output)

### Acceptance Criteria

AC-3-1
Dependencies: AC-1-2, AC-2-2
Event-driven: When the last agent of a dispatched team writes its typed findings, the confined `write_file` tool (`make_write_file`, `write_file.py:32-85`) shall accept a write whose target is the team's `findings_json_path`, keeping the existing `output_root` confinement so a path escaping the run root is still rejected.

AC-3-2
Dependencies: AC-1-2, AC-2-2
Event-driven: When `write_artifact` (or a typed-findings write helper it delegates to) is asked to persist a list of `Finding` objects for a team, then it shall serialize them to the team's `findings_json_path` as a JSON array using each model's `model_dump_json`-equivalent form and return a JSON status containing `status`, the absolute `path`, and the finding `count`.

AC-3-3
Dependencies: AC-2-2
Event-driven: When `dispatch_team` builds a team's augmented task, the Required-Output block (`teams.py:360-375`) shall instruct the last agent to write **both** the human `findings.md` (`findings_path`) and the typed `findings.json` (`findings_json_path`), naming both resolved layout paths and describing the required `Finding` JSON shape.

AC-3-4
Dependencies: AC-3-2
Unwanted behavior: If a typed-findings write receives content that does not validate against the `Finding` schema, then the write path shall return a JSON `{"status": "error", ...}` result naming the validation failure rather than persisting an unvalidated `findings.json`.

---

## User Story 4 — `read_team_findings` returns parsed `Finding[]` with markdown fallback

### Acceptance Criteria

AC-4-1
Dependencies: AC-1-2, AC-2-2, AC-3-2
Event-driven: When `read_team_findings` is called with a `team_id` whose `findings_json_path` exists and validates, then it shall return a JSON result whose `findings` value is the parsed list of typed `Finding` objects (as dicts) for that team, plus a `finding_count`.

AC-4-2
Dependencies: AC-4-1
State-driven: While a team's `findings.json` is absent or fails schema validation but its `findings.md` exists, `read_team_findings` shall fall back to returning the raw markdown `findings` content (preserving the current behavior at `teams.py:441-449`) and shall mark the result so the caller can tell typed from fallback.

AC-4-3
Dependencies: AC-4-1
Event-driven: When `read_team_findings` is called with no `team_id`, then for each team it shall report whether typed findings are present and their count, extending the existing per-team listing (`has_findings`, `findings_lines`, `metadata`) at `teams.py:406-426` without removing those keys.

AC-4-4
Dependencies: AC-4-2
Unwanted behavior: If a team's `findings.json` is present but corrupt (invalid JSON or schema-invalid), then `read_team_findings` shall not raise and shall degrade to the markdown fallback for that team rather than aborting the whole listing.

---

## User Story 5 — Evidence gate bound to the citation-density rubric

### Acceptance Criteria

AC-5-1
Dependencies: AC-1-2
Ubiquitous: The framework shall provide an evidence-gate check that, given a `Finding`, returns a pass result when the finding is non-blocking or carries ≥1 `Evidence` ref, and a fail result naming the finding when `blocking` is `True` and its `evidence` list is empty.

AC-5-2
Dependencies: AC-5-1, AC-3-2
State-driven: While typed findings are being persisted through the write path, any `blocking=true` finding with zero evidence refs shall be flagged in the write result (e.g. an `evidence_gate` warning listing the offending findings) rather than silently accepted.

AC-5-3
Dependencies: AC-5-1, AC-4-1
State-driven: While `read_team_findings` returns parsed typed findings, its result shall include an `evidence_gate` summary flagging every `blocking=true` finding lacking evidence, so the coordinator can act on the gap during cross-reference.

AC-5-4
Dependencies: AC-5-1
Ubiquitous: The evidence gate shall reuse the `NarrativeRubric` citation-density concept (`rubric.py` `FILE_LINE_PATTERN` L34 / `specificity` dimension L177-182) rather than introducing a second, divergent citation heuristic, keeping the "evidence density" definition in one place.

AC-5-5
Dependencies: AC-5-2
Unwanted behavior: If a finding is non-blocking and carries no evidence, then the evidence gate shall pass it (evidence is required only for blocking findings), so routine informational findings are not spuriously flagged.

---

## User Story 6 — Regression: codebase-analysis emits the identical artifact set

### Acceptance Criteria

AC-6-1
Dependencies: AC-3-3, AC-4-1
Event-driven: When `code-context-agent analyze <repo>` completes on a repository with a pre-built index, then it shall still write `CONTEXT.md`, one or more `BUNDLE.{area}.md` files under `bundles/`, and `analysis_result.json` into the `.code-context` output directory, matching the pre-phase artifact set.

AC-6-2
Dependencies: AC-3-3
Ubiquitous: The codebase-analysis use-case `findings_format_directive` (`code_context_agent/usecase.py:271`, `_FINDINGS_FORMAT_DIRECTIVE` L178-197) shall continue to require the human `findings.md` markdown format while additionally describing the typed `findings.json` shape, so existing bundle synthesis still has its markdown input.

AC-6-3
Dependencies: AC-6-1
Unwanted behavior: If a team writes only the human `findings.md` and omits `findings.json`, then the analyze run shall still complete and produce the full `CONTEXT.md` / `BUNDLE.*` set via the markdown fallback (AC-4-2), rather than failing the run.

---

## User Story 7 — Unit tests and the team-findings round-trip seam test

### Acceptance Criteria

AC-7-1
Dependencies: AC-1-2, AC-1-5
Ubiquitous: The `strands-teams` test suite shall include unit tests for the `Finding` and `Evidence` models covering the valid-construction path, immutability, and the `Literal`/`confidence`-range rejection path (AC-1-5).

AC-7-2
Dependencies: AC-2-2
Ubiquitous: The `strands-teams` test suite shall include a `TeamsLayout` test asserting `findings_json_path(team_id)` resolves under the same team directory as `findings_path(team_id)` and uses the `findings_json_name` default.

AC-7-3
Dependencies: AC-4-1, AC-4-2, AC-5-3
Event-driven: When the round-trip seam test writes typed `Finding` objects to a real `findings.json` via the write path and then invokes `read_team_findings`, then the test shall assert the parsed findings equal the written findings and that the `evidence_gate` summary flags a seeded blocking-without-evidence finding.

AC-7-4
Dependencies: AC-4-2, AC-4-4
Unwanted behavior: If the seam test seeds a team with only `findings.md` (no `findings.json`) or a corrupt `findings.json`, then `read_team_findings` shall be asserted to return the markdown fallback marked as such and to not raise.

---

## User Story 8 — Strands 1.47 API-fact conformance (research digest)

> Scope note: Phase P1-findings touches only two research-digest fact areas — the native-`Swarm` dispatch/collection path and `invocation_state` semantics / tool return conventions. The Bedrock model ids (`global.anthropic.claude-opus-4-8`, `openai.gpt-5.6-sol`), the Codex built-in `amazon-bedrock` provider, and the subprocess-isolation gotchas (`CODEX_HOME`, worktree confinement, swarm-vs-subprocess timeout ordering) are Phase 3 (`SpecialistRuntime`) concerns; this phase does not touch them and defines no ACs for them.

### Acceptance Criteria

AC-8-1
Dependencies: AC-3-2, AC-4-1
Ubiquitous: The typed-findings write and collection functions shall return `str` (JSON) or a `FrozenModel` that strands' `_wrap_tool_result` serializes via `model_dump_json` — never a raw `dict`-with-status-plus-content shape reserved for tool-result envelopes — conforming to the `strands_tool_pattern` return convention (research digest `strands_tool_pattern`).

AC-8-2
Dependencies: AC-4-1
State-driven: While `read_team_findings` reads the layout from `tool_context.invocation_state`, it shall treat the invocation-state contents as read-only (no mutation of the shared-by-reference dict), conforming to the concurrent-node safety rule in the research digest (`strands_tool_pattern`: "Treat contents read-only inside concurrently-executing node tools").

AC-8-3
Dependencies: AC-3-3
State-driven: While `dispatch_team` runs a team as a native Strands `Swarm` (`teams.py:281-290`), the typed-findings Required-Output instruction shall be delivered through the augmented task string (so the last swarm node writes `findings.json`), with no change to the `Swarm` construction, `invoke_async` call, or the `repetitive_handoff_detection_window=8` / `repetitive_handoff_min_unique_agents=3` settings.

AC-8-4
Dependencies: AC-4-1
Unwanted behavior: If `read_team_findings` deserializes typed findings from disk, then it shall validate them via pydantic model validation over parsed JSON (model-validation of a dict/JSON string) and shall not use `model_validate` against a raw Python object it did not parse, so malformed on-disk content is caught at the parse boundary (AC-4-4).
