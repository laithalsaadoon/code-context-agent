# Tasks — 004-coding-runtime

Derived from `spec.md`. One task per coherent AC cluster. `[P]` marks parallel-safe tasks (isolated worktree, no file overlap with other `[P]` tasks in the same wave). `Dependencies:` lists ACs that must complete before the task starts. Waves are derivable from the dependency closure.

> Cross-phase given: P1 `Finding` + evidence-reference types exist in `strands_teams.models` before this phase runs; `SpecialistResult.findings`/`.evidence` reuse them. P4 `ArtifactRevision` does NOT exist yet — `SpecialistResult.artifacts` is a list of worktree-relative path strings in P3.

> Regeneration note: hand-edits here are destroyed on regeneration — edit `spec.md` instead.

---

## Wave 0 — Workspace package skeleton

### T-R1-pkg — Add the strands-teams-runtimes workspace package
Covers: AC-1-1, AC-1-2, AC-1-3, AC-1-4
[P]
Dependencies: none
Create `packages/strands-teams-runtimes/` with `pyproject.toml` (import package `strands_teams_runtimes`, `python>=3.13`, deps `claude-agent-sdk>=0.1.60` + `openai-codex`, `[tool.uv.sources] strands-teams = { workspace = true }`) and an empty `src/strands_teams_runtimes/` package root. The root `[tool.uv.workspace] members = ["packages/*"]` already resolves it. Run `uv sync` and commit the updated `uv.lock`. Verify `python -c "import strands_teams_runtimes"`.

---

## Wave 1 — Core typed contracts (vendor-free, in strands_teams)

### T-R2-contracts — SpecialistRuntime Protocol + SpecialistResult + ResourceUsage
Covers: AC-2-1, AC-2-2, AC-2-3, AC-2-4, AC-2-5, AC-2-6, AC-2-7
Dependencies: none
In `strands_teams` core add: the `SpecialistRuntime` Protocol (`submit`/`status`/`send_input`/`cancel`/`collect`, no SDK/`subprocess` import; `collect -> SpecialistResult`, structured only, never transcript); the `SpecialistResult` FrozenModel (`disposition: Literal["completed","partial","failed","refused"]`, `artifacts` (path strings), `findings` reusing P1 `Finding`, `evidence` reusing P1 evidence ref, `residual_risk`, `resource_usage`); the `ResourceUsage` FrozenModel (`cost_usd`, `input_tokens`, `output_tokens`, `num_turns`, `duration_seconds`). Export all three from `strands_teams.__init__` `__all__`.

### T-R1-guard — Core-stays-vendor-free import check
Covers: AC-1-5
[P]
Dependencies: none
Add a static import check (test or lint rule) over `packages/strands-teams/src/strands_teams/` asserting no module imports `claude_agent_sdk`, `codex`, or `openai_codex`, and none launches a coding-harness subprocess.

---

## Wave 2 — Runtime adapters + workspace factory (in strands_teams_runtimes)

### T-R3-claude — ClaudeAgentRuntime (Opus 4.8 on Bedrock)
Covers: AC-3-1, AC-3-2, AC-3-3, AC-3-4, AC-3-6
Dependencies: AC-2-1, AC-2-3
Wrap `claude_agent_sdk.query()` with the exact `ClaudeAgentOptions` from AC-3-1 (`model=global.anthropic.claude-opus-4-8`, `CLAUDE_CODE_USE_BEDROCK=1`, `acceptEdits`, allowed/disallowed tools, `setting_sources=[]`, `output_format` json_schema, `max_turns`+`max_budget_usd`). Add the streaming-prompt `can_use_tool` path-confinement callback. `collect` builds `SpecialistResult` from `ResultMessage.structured_output` (`model_validate`), `total_cost_usd`, `usage`; maps `subtype` → disposition. Drive with `await`, never `asyncio.run`.

### T-R4-codex — CodexRuntime (GPT-5.6 Sol on Bedrock)
Covers: AC-4-1, AC-4-2, AC-4-3, AC-4-4, AC-4-5
Dependencies: AC-2-1, AC-2-3
Launch `codex exec` via `asyncio.create_subprocess_exec` with the exact flags from AC-4-1 (`--model openai.gpt-5.6-sol --model-provider amazon-bedrock -c model_reasoning_effort=high --cd <worktree> --full-auto --json --output-schema <schema> -o result.json`), per-run `CODEX_HOME` + `AWS_BEARER_TOKEN_BEDROCK`. `collect` reads the `-o` final file + `turn.completed` (never first schema-valid line, #19816), maps `usage` → `resource_usage`, sets `failed` on `turn.failed`/`error`.

### T-R5-worktree — WorkspaceFactory (git-worktree isolation)
Covers: AC-5-1, AC-5-2, AC-5-3, AC-5-4
[P]
Dependencies: AC-2-1
`git worktree add` per run returning the worktree path (harness `cwd`); distinct path per concurrent run; auto-remove when unchanged, keep when it holds uncommitted changes.

---

## Wave 3 — The tool (depends on adapters + factory + contracts)

### T-R6-tool — invoke_coding_agent + make_invoke_coding_agent factory
Covers: AC-6-1, AC-6-2, AC-6-3, AC-6-4, AC-6-5, AC-7-1, AC-7-2, AC-7-3, AC-7-4
Dependencies: AC-2-1, AC-3-1, AC-4-1, AC-5-1
`make_invoke_coding_agent(runtime_registry)` returns an `async @tool(context=True)` async-generator tool with runtimes injected (no SDK import in core). Reads `workspace`/`model_id`/`capabilities`/`runtime` from `invocation_state`; non-final yields = heartbeats, final yield = `SpecialistResult.model_dump_json()`; error JSON on missing/unknown state keys. Module-level `asyncio.Semaphore(STRANDS_TEAMS_MAX_HARNESSES)` around subprocess launch; own `asyncio.wait_for(_SUBPROCESS_TIMEOUT_S)` → `disposition="failed"` on timeout. Docstring + notes: swarm `node_timeout`/`execution_timeout` must exceed `_SUBPROCESS_TIMEOUT_S`.

---

## Wave 4 — Mocked test suite (no real Bedrock/network)

### T-R8-tests — Mocked runtime + tool tests
Covers: AC-8-1, AC-8-2, AC-8-3, AC-8-4, AC-8-5, AC-8-6, AC-8-7, AC-8-8
Dependencies: AC-3-3, AC-4-2, AC-5-2, AC-6-4, AC-7-1, AC-7-3
Mock the Claude Agent SDK and Codex subprocess (no real Bedrock/network). Assert: worktree isolation (distinct paths); semaphore cap (concurrent subprocesses ≤ cap); timeout path → `failed`; both runtimes parse structured result into a valid `SpecialistResult`; ≥1 heartbeat yield before final; path-confinement deny; transcript never crosses the boundary.

---

## Implementation log (P3 — completed)

Status: COMPLETE. All waves implemented; `uv run ruff check packages`, `ruff format --check`, `ty check packages`, and `pytest -q` (360 passed) all green.

### Files created

**New package `packages/strands-teams-runtimes/`** (import `strands_teams_runtimes`):
- `pyproject.toml` — dist `strands-teams-runtimes`, `python>=3.13`, deps `claude-agent-sdk>=0.1.60` + `openai-codex`, `[tool.uv.sources] strands-teams = { workspace = true }` (AC-1-1, AC-1-2). Resolved by root `[tool.uv.workspace] members=["packages/*"]`; `uv sync` locked `openai-codex==0.1.0b2` + `claude-agent-sdk` into `uv.lock` (AC-1-3, AC-1-4).
- `README.md`.
- `src/strands_teams_runtimes/__init__.py` — exports `ClaudeAgentRuntime`, `CodexRuntime`, `WorkspaceFactory`, `WorktreeError`, `make_invoke_coding_agent`, model-id constants; re-exports core contracts.
- `src/strands_teams_runtimes/workspace.py:66` `WorkspaceFactory.create` (git-worktree per run, distinct paths), `:96` `has_changes`, `:110` `cleanup` (auto-remove iff unchanged; keep if dirty) — AC-5-1..AC-5-4.
- `src/strands_teams_runtimes/claude.py:124` `ClaudeAgentRuntime`: `:147` `build_options` (model `global.anthropic.claude-opus-4-8`, `acceptEdits`, allowed/disallowed tools, `setting_sources=[]`, json_schema `output_format`, `max_turns`+`max_budget_usd`, `env CLAUDE_CODE_USE_BEDROCK=1` — AC-3-1); `:171` `make_can_use_tool` path confinement (AC-3-2, AC-3-5); `:220` `run` (await query, no asyncio.run — AC-3-6); `:236` `collect` (structured_output via model_validate, subtype→disposition, usage/cost — AC-3-3, AC-3-4).
- `src/strands_teams_runtimes/codex.py:83` `CodexRuntime`: `:114` `build_command` (exact `codex exec` flags — AC-4-1), `:136` `build_env` (per-run `CODEX_HOME` + `AWS_BEARER_TOKEN_BEDROCK` — AC-4-1, AC-4-4), `:159` `run`, `:192` `collect` (`-o` final file + `turn.completed`, not first schema-valid line #19816; `turn.failed`/`error`→failed — AC-4-2, AC-4-3, AC-4-5).
- `src/strands_teams_runtimes/_structured.py` — shared `StructuredHarnessOutput` json-schema + `structured_to_result` (drops the harness summary; no transcript crosses — ASTR §10.4).
- `src/strands_teams_runtimes/tool.py:96` `make_invoke_coding_agent` factory → `invoke_coding_agent` async-gen `@tool(context=True)` (AC-6-1..AC-6-5); module-level `asyncio.Semaphore(STRANDS_TEAMS_MAX_HARNESSES)` (AC-7-1), own `asyncio.wait_for(_SUBPROCESS_TIMEOUT_S)`→failed (AC-7-2, AC-7-3); heartbeats (AC-6-4); docstring + module docstring warn swarm `node_timeout`/`execution_timeout` must exceed `_SUBPROCESS_TIMEOUT_S` (AC-7-4).

**Core `packages/strands-teams/src/strands_teams/`** (vendor-free):
- `runtime.py` — `SpecialistDisposition` literal, `ResourceUsage` FrozenModel (AC-2-5), `SpecialistResult` FrozenModel (reuses P1 `Finding`/`Evidence`; artifacts=path strings; AC-2-3, AC-2-4, AC-2-7), `SpecialistRuntime` `@runtime_checkable` Protocol (`submit`/`status`/`send_input`/`cancel`/`collect`→SpecialistResult, never transcript — AC-2-1, AC-2-2). No SDK/subprocess import.
- `__init__.py` — added the four names to imports + `__all__` (AC-2-6).

**Tests (all mock SDK/subprocess — zero network, AC-8-1):**
- `packages/strands-teams/tests/test_runtime_contract.py` — contract + exports + AST core-vendor-free guard (AC-1-5, scoped to SDK imports; core's pre-existing `security.py` subprocess shell tool is allowed).
- `packages/strands-teams-runtimes/tests/test_workspace.py` — worktree isolation/cleanup (AC-8-2).
- `packages/strands-teams-runtimes/tests/test_claude_runtime.py` — options, path-deny (AC-8-7), structured parse + subtype map (AC-8-5), transcript boundary (AC-8-8).
- `packages/strands-teams-runtimes/tests/test_codex_runtime.py` — argv, per-run CODEX_HOME, `-o`-file parse (not decoy line), turn.failed→failed, transcript boundary.
- `packages/strands-teams-runtimes/tests/test_tool.py` — heartbeats (AC-8-6), semaphore cap (AC-8-3), timeout→failed (AC-8-4), error envelopes (AC-6-5), docstring timeout note (AC-7-4).

### Files modified
- Root `pyproject.toml` — added runtimes package to ruff `src`, ty `include`, coverage `source`, pytest `testpaths`, pip-licenses `ignore-packages`; extended tests per-file-ignore (`S105/S106/PT018/TC00x`).
- `uv.lock` — regenerated by `uv sync`.
