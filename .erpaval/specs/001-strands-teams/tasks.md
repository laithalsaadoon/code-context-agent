# Tasks — 001-strands-teams

Derived from `spec.md`. One task per coherent AC cluster. `[P]` marks parallel-safe tasks (isolated worktree, no file overlap with other `[P]` tasks in the same wave). `Dependencies:` lists ACs that must complete before the task starts. Waves are derivable from the dependency closure.

> Regeneration note: hand-edits here are destroyed on regeneration — edit `spec.md` instead.

---

## Wave 0 — Workspace skeleton

### T-R1-scaffold — Create the uv workspace and two package skeletons
Covers: AC-1-1, AC-1-2, AC-1-3
[P]
Dependencies: none
Create root `pyproject.toml` with `[tool.uv.workspace] members = ["packages/*"]`, and the two member `pyproject.toml` files (`strands-teams` importing `strands_teams` with `strands-agents>=1.47` + shared deps; `code-context-agent` importing `code_context_agent`, keeping the console script, workspace-depending on `strands-teams`, holding analysis-only deps). Do not move source yet — establish empty `src/` package roots.

---

## Wave 1 — Core extraction (depends on the skeleton)

### T-R2-layout — Layout descriptor + artifact-path single-sourcing
Covers: AC-2-1
Dependencies: AC-1-2
Introduce the layout-descriptor type in `strands_teams` that computes teams dir, `findings.md`, `metadata.json`, `team_debug.jsonl`, artifact-output dir, artifact filenames, executive-summary filename, and planning-context sentinel from an output root + use-case naming scheme. Removes the duplicated `BUNDLE.{area}.md` / `heuristic_summary.json` / `findings.md` literals.

### T-R5-usecase — UseCase abstraction + invocation_state plumbing
Covers: AC-5-1, AC-5-2
Dependencies: AC-1-2
Define the `UseCase` Plugin/protocol carrying tools, prompt template + planning-context loader, structured-output model, findings-format directive, naming scheme + output spec, rubric, tool→phase map + names, tool→reasoning-prompt map, fail-fast exempt set, kickoff-prompt builder, settings. Carry shared state through `invocation_state` via `@tool(context=True)`; delete the module-level `configure()` globals.

### T-R2-scoring — Rubric object + narrative-scoring tool
Covers: AC-2-5, AC-2-6
Dependencies: AC-2-1
Single rubric object (dimensions, threshold tables, 3.5 revision threshold). Scoring tool reads artifact via layout descriptor and returns per-dimension scores, total, `needs_revision`, suggestions.

---

## Wave 2 — Core tools, hooks, display (depend on layout + UseCase + rubric)

### T-R2-dispatch — dispatch_team on native Swarm (async, context, explicit models, handoff detection)
Covers: AC-2-2, AC-2-3, AC-2-8, AC-8-1, AC-8-2, AC-8-3, AC-8-4, AC-8-8
Dependencies: AC-2-1, AC-5-2
Rewrite `dispatch_team` as `async def @tool(context=True)`: build fresh per-team `Agent` nodes each with an explicit `BedrockModel`, construct native `Swarm` with `repetitive_handoff_detection_window=8`/`min_unique_agents=3`, `await swarm.invoke_async(task, invocation_state=...)`. Preserve augmented-task assembly, try/except → `{"status":"error"}`, timeout-tagged errors, and `team_debug.jsonl` append on all paths.

### T-R2-findings-write — read_team_findings + artifact-writing tool
Covers: AC-2-4, AC-2-7
Dependencies: AC-2-1
`read_team_findings` (list vs single-team) and the artifact-writing tool (executive summary vs area artifact) both routed through the layout descriptor.

### T-R5-teamtools — Auto-append write_file + UseCase-driven coordinator tool list; construction guards
Covers: AC-5-3, AC-5-4, AC-5-5
Dependencies: AC-5-1
Coordinator factory composes six coordinator tools + use-case domain tools, renders prompt from use-case template, sets structured-output model. Auto-append `write_file` to every team agent. Fail loudly with a named-member error when a required UseCase member is missing.

### T-R3-hooks — Generic hook providers + create_all_hooks assembly
Covers: AC-3-1, AC-3-2, AC-3-3, AC-3-4, AC-3-5, AC-3-6
[P]
Dependencies: AC-2-1, AC-2-5, AC-5-1
Move compaction/output-quality/efficiency/reasoning/fail-fast/team-dispatch/tool-display/JSON-log hooks into `strands_teams` with no domain literals. Reasoning hook keyed off use-case tool→prompt map; quality hook uses the shared rubric + bounded enrichment passes; fail-fast uses use-case exempt set. `create_all_hooks(use_case, full_mode, state, quiet)` assembles the correct set.

### T-R4-display — Parameterized display core
Covers: AC-4-1, AC-4-2, AC-4-3, AC-4-4, AC-4-5
[P]
Dependencies: AC-4-1
Inject phase count, title, phase-name map, discovery extractor. Coordinator-vs-single-agent layout selection; phase indicator uses injected count; discovery feed driven by injected extractor with safe no-op on bad/error results.

---

## Wave 3 — Strands model config (small, can run alongside Wave 2 core once UseCase exists)

### T-R8-model — CacheConfig, MCP continue_on_error, structured-output kwarg
Covers: AC-8-5, AC-8-6, AC-8-7
Dependencies: AC-5-3, AC-6-1
Coordinator `BedrockModel` uses `cache_config=CacheConfig(strategy="auto")` (keep adaptive-thinking/output-config/context-1m fields). MCP providers pass `continue_on_error`. Structured output via constructor/per-call kwarg + `result.structured_output`, never the deprecated method.

> Note: AC-8-6 lands with the codebase use-case factory (T-R6-usecase) since that owns the MCP providers; AC-8-5/AC-8-7 land in the core coordinator factory.

---

## Wave 4 — Codebase use-case implementation (depends on core + UseCase)

### T-R6-usecase — Analysis UseCase, indexer, heuristic summary, MCP providers
Covers: AC-6-1, AC-6-2, AC-6-6, AC-8-6
Dependencies: AC-5-1
Implement the analysis `UseCase`: tool roster (discovery/git/BM25/shell + GitNexus/context7 MCP), prompt template + partials/steering, `AnalysisResult`, `AnalysisPhase` map + names, `_REASONING_PROMPTS`, exempt set, `CODE_CONTEXT_` settings + `.code-context` dir. Move `read_heuristic_summary` and the indexer. MCP providers degrade to omission when unavailable (`continue_on_error`).

### T-R6-e2e — End-to-end analyze/index/bundles-only behavior preservation
Covers: AC-6-3, AC-6-4, AC-6-5
Dependencies: AC-6-1, AC-6-2, AC-2-2, AC-2-3, AC-2-4, AC-2-6, AC-2-7
Verify `analyze` writes `CONTEXT.md` + `BUNDLE.*.md` + `analysis_result.json`; `index` writes `heuristic_summary.json` + deterministic artifacts with no LLM; `--bundles-only` regenerates from existing findings. Same artifact set as pre-split.

---

## Wave 5 — Deletions (can start early where independent; sequenced before import-check gates)

### T-R7-drop-subsystems — Delete verdict/temporal/issues/ci + CLI commands
Covers: AC-7-1
[P]
Dependencies: none
Remove `verdict.py`, `temporal.py`, `issues/`, `ci/`, and the `verdict` + `ci-init` CLI commands and their imports.

### T-R7-drop-mcp — Delete FastMCP server, registry, serve command; drop fastmcp dep
Covers: AC-7-2, AC-7-3
[P]
Dependencies: none
Remove `mcp/server.py`, `mcp/registry.py`, the `serve` command, and all FastMCP analysis-server tooling. Remove `fastmcp` from every pyproject.

### T-R7-drop-dead — Delete zero-reference dead code + dead CI jobs
Covers: AC-7-4, AC-7-5
[P]
Dependencies: none
Remove `models/index.py`, `exceptions.py`, `templates/context_output.md.j2`, `tests/test_viz.py`, `render_steering`; delete the `.gitlab-ci.yml` `pnpm --dir ui` job and dependabot `/ui` npm entry.

### T-R7-verify — Import-check gate for dropped/moved symbols
Covers: AC-1-5, AC-1-6, AC-7-6
Dependencies: AC-1-4, AC-7-1, AC-7-2, AC-7-4, AC-6-1
Confirm `import strands_teams` + `import code_context_agent` both succeed, `strands_teams` has no `code_context_agent` import, and no surviving module/test references a deleted symbol (import check + test collection fail on violation).

---

## Wave 6 — Lockstep tooling, CI, tests

### T-R9-config — Path lockstep across build/CI/coverage/commitizen
Covers: AC-9-1, AC-9-6, AC-9-7
Dependencies: AC-1-3, AC-6-1
Update commitizen `version_files`, coverage `source`/`--cov` paths (pyproject, ci.yml, .gitlab-ci.yml, mise.toml), per-file-ignore globs, `scripts/security_report.py` self-ignore (both dist names). Place `strands-agents-tools` only in `code-context-agent` if `graph` is kept, else drop it entirely.

### T-R9-tests — Test relocation + patch-string fixups + deletions
Covers: AC-9-3, AC-9-4
Dependencies: AC-6-1, AC-3-1, AC-4-1, AC-2-1, AC-7-1, AC-7-2, AC-7-4
Move each test to the package that now owns its component; fix every mock-patch string to the moved path; delete tests for dropped components.

### T-R9-lock — Regenerate uv.lock; green mise run check
Covers: AC-9-2, AC-9-5
Dependencies: AC-9-1, AC-9-3
`uv lock` resolving both members; commit both pyprojects + lock. `mise run check` (lint, format-check, typecheck, test) passes for both packages.

---

## Wave 7 — Documentation (last)

### T-R10-docs — AGENTS/CLAUDE/README + mkdocs ref + ADR
Covers: AC-10-1, AC-10-2, AC-10-3
Dependencies: AC-6-1, AC-7-1, AC-7-2, AC-9-1
Rewrite `AGENTS.md`/`CLAUDE.md`/`README.md` for the two-package workspace and surviving CLI; update `docs/gen_ref_pages.py` to iterate both package sources and fix mkdocs nav; add the workspace-split ADR.

---

## Follow-ups (post-review, deferred — not blocking the split)

Independent pre-commit review found two warnings that are real but out of the split's critical path. Fixed at commit time: E1 (AgentDisplayState.record_tool missing → crash on every non-quiet run), E2 (bind_live_renderable not called in runner → frozen TUI), W3 (stale write_bundle/enrich_bundle tool names in usecase/template/hooks → LLM tool-not-found + dead phase mapping). Deferred:

- **F1 (was W4): MCP provider tools not expanded into the team tool registry.** `_build_tool_registry` (coordinator.py:74) keys off `tool_name`/`__name__`, but GitNexus/context7 are `MCPClient` (`ToolProvider`) instances whose individual `gitnexus_*`/`context7_*` tools only materialize inside the Agent's own registry. Team-agent specs requesting those names get "unknown tool — dropping" (teams.py) — so dispatched teams silently lose GitNexus/context7 access (the coordinator itself keeps it). Fix requires calling `provider.load_tools()` and registering each AgentTool by name, with MCP session lifecycle held open during swarm dispatch (delicate — a naive fix risks a worse crash than the current graceful degradation). Verify: `MCPClient.load_tools()` exists and needs no session arg, but the session must be entered. Add a test for the npx-present path.
- **F2 (was W5): dispatch_team accepts any non-string tool entry as a resolved tool** (teams.py ~6524). A malformed LLM spec (dict/None) is forwarded to `Agent(tools=...)`, raising a confusing deep error instead of the clean log-and-drop. Fix: gate the non-string branch on `hasattr(tool_name, "tool_name")`.
- **F3 (info): hoist `_deep_merge_defaults`** from code_context_agent.usecase into strands_teams (generic to any UseCase.planning_context). **F4 (info): harden `validate_repo_path`** blocklist in strands_teams.security to reject subpaths of system dirs, not just exact matches.
- **Docs:** the tracked architecture/insight docs under docs/ still describe the pre-split single-package design and old tool names (write_bundle etc.). Regenerate after the split settles.
