---
slug: strands-teams
sequence: 001
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

Split the single `code-context-agent` package into a uv workspace of two packages:

- **`packages/strands-teams`** (`strands_teams`) — a domain-agnostic agent-teams framework on strands-agents >=1.47.
- **`packages/code-context-agent`** (`code_context_agent`) — the codebase-analysis use-case that plugs a `UseCase` implementation into `strands_teams`.

The split drops the FastMCP analysis server, verdict/temporal/issues/ci subsystems, and assorted dead code. It migrates the team-dispatch mechanics from `strands_tools.swarm.swarm` to native Strands `Swarm`, replaces module-level mutable state with `invocation_state`, and preserves all `analyze`/`index` behavior end-to-end.

Grounding for every strands-touching AC: `.erpaval/sessions/session-bea07b/research-strands.yaml` (Strands 1.47 API facts). Grounding for coupling/moves/blast-radius: `.erpaval/sessions/session-bea07b/explore.yaml`. Terms used below:

- **layout descriptor** — a single object owning all artifact paths (findings.md, metadata.json, artifact/bundle files, planning-context sentinel) consumed by tools, hooks, runner, and CLI. Replaces the scattered `BUNDLE.{area}.md` / `heuristic_summary.json` / `findings.md` string literals (explore.yaml hidden couplings #4, #10).
- **rubric object** — one scoring-rubric object shared by the `score_narrative`-equivalent tool and the quality hook, deduping the 3.5 threshold and threshold tables (explore.yaml hidden coupling #5).
- **UseCase contract** — the abstraction (Plugin subclass per research-strands.yaml §Plugins) through which a domain supplies tools, prompts, models, rubric, phase map, and settings.

---

## User Story 1 — R1: Workspace scaffolding

### Acceptance Criteria

AC-1-1 [P]
Ubiquitous: The repository root `pyproject.toml` shall declare a uv workspace via `[tool.uv.workspace]` with `members = ["packages/*"]` and shall itself contain no `[project]` package sources for `code_context_agent`.

AC-1-2 [P]
Ubiquitous: The `packages/strands-teams/pyproject.toml` shall define a distribution whose import package is `strands_teams`, declare `strands-agents>=1.47` plus the shared runtime deps (`jinja2`, `loguru`, `pydantic`, `pydantic-settings`, `rich`), and declare no dependency on `code_context_agent`.

AC-1-3 [P]
Ubiquitous: The `packages/code-context-agent/pyproject.toml` shall define a distribution whose import package is `code_context_agent`, keep the `code-context-agent = "code_context_agent.cli:app"` console script, declare a workspace dependency on `strands-teams`, and declare the analysis-only deps (`cyclopts`, `rank-bm25`, `ty`).

AC-1-4
Dependencies: AC-1-1, AC-1-2, AC-1-3
Event-driven: When `uv sync` is run at the repository root, then both `strands_teams` and `code_context_agent` shall be installed as editable workspace members resolvable in the same environment.

AC-1-5
Dependencies: AC-1-4
Event-driven: When `python -c "import strands_teams; import code_context_agent"` is executed in the synced environment, then both imports shall succeed without error.

AC-1-6
Dependencies: AC-1-2
Unwanted behavior: If any module under `packages/strands-teams/src/strands_teams/` imports a `code_context_agent` symbol, then a static import check over that source tree shall report the violation and fail.

---

## User Story 2 — R2: strands_teams core (dispatch, findings, artifacts, scoring)

### Acceptance Criteria

AC-2-1 [P]
Ubiquitous: The `strands_teams` core shall expose a layout descriptor type that computes every artifact path (teams dir, `{team_id}/findings.md`, `{team_id}/metadata.json`, `team_debug.jsonl`, artifact-output dir, artifact filenames, executive-summary filename, planning-context sentinel) from an output-directory root and a use-case-supplied naming scheme, with no path string literal duplicated across dispatch/read/write functions.

AC-2-2
Dependencies: AC-2-1
State-driven: While a team is being dispatched, the `dispatch_team` tool shall persist a `team_debug.jsonl` append entry containing `team_id`, `status`, `duration_seconds`, effective execution/node timeouts, agent names, agent count, and mandate preview, regardless of whether the swarm succeeded or errored.

AC-2-3
Dependencies: AC-2-1
Event-driven: When `dispatch_team` builds a team's task, the augmented task string shall embed the mandate, optional file scope, optional key questions, optional artifact pointers, and a required-output block naming the resolved `findings.md` and `metadata.json` paths from the layout descriptor.

AC-2-4
Dependencies: AC-2-1
Event-driven: When `read_team_findings` is called with no team id, it shall return a JSON listing of every team directory with `has_findings`, `findings_lines`, and parsed `metadata`; and when called with a team id, it shall return that team's `findings.md` content or a `no_findings` status if the file is absent.

AC-2-5 [P]
Ubiquitous: The `strands_teams` core shall expose a single rubric object that defines the scoring dimensions, their threshold tables, and the revision threshold (default 3.5), consumed by both the narrative-scoring tool and the quality hook so the threshold and dimension logic exist in exactly one place.

AC-2-6
Dependencies: AC-2-1, AC-2-5
Event-driven: When the scoring tool is called for an artifact area, it shall read the artifact via the layout descriptor, score it against the rubric object, and return per-dimension scores, a rounded total average, a `needs_revision` flag set by the rubric threshold, and improvement suggestions.

AC-2-7
Dependencies: AC-2-1
Event-driven: When the artifact-writing tool is called, it shall write the executive-summary file or an area artifact file to the layout-descriptor path and return status, absolute path, output-relative path, and line count.

AC-2-8
Dependencies: AC-2-3
Unwanted behavior: If the underlying swarm raises during `dispatch_team`, then the tool shall catch the exception, return a JSON object with `status: "error"` and a truncated message, and still write the `team_debug.jsonl` entry rather than propagating the exception.

---

## User Story 3 — R3: Hooks generalization

### Acceptance Criteria

AC-3-1 [P]
Ubiquitous: The `strands_teams` core shall provide generic `HookProvider` implementations for conversation compaction, output-quality logging, tool efficiency, reasoning checkpoints, fail-fast, team-dispatch display, tool display, and JSON logging, none of which reference codebase-analysis tool names or artifact names in their module-level code.

AC-3-2
Dependencies: AC-3-1
State-driven: While the reasoning-checkpoint hook is active, it shall append a reasoning prompt to a tool result only when the tool name is present in a use-case-supplied tool→prompt map, and shall leave results for unmapped tools unchanged.

AC-3-3
Dependencies: AC-3-1, AC-2-5
State-driven: While the quality hook runs after an invocation, it shall score existing artifacts using the shared rubric object, and if the average is below the rubric revision threshold or any artifact is below it, shall request at most a fixed number of enrichment passes (default 2) via the invocation resume mechanism.

AC-3-4
Dependencies: AC-3-1
State-driven: While the fail-fast hook is active, it shall raise on a non-exempt tool returning an error result, and shall treat a use-case-supplied exempt set (plus MCP-prefixed tool names) as non-fatal.

AC-3-5
Dependencies: AC-3-1
Event-driven: When `create_all_hooks` is called with a use-case, full-mode flag, display state, and quiet flag, then it shall return the compaction, output-quality, efficiency, reasoning, and quality hooks always; the fail-fast hook only in full mode; and either the JSON-log hook (quiet) or the tool-display plus team-dispatch hooks (non-quiet).

AC-3-6
Dependencies: AC-3-3
Unwanted behavior: If the enrichment-pass budget is already exhausted, then the quality hook shall return without requesting another resume even when artifacts remain below threshold.

---

## User Story 4 — R4: Display generalization

### Acceptance Criteria

AC-4-1 [P]
Ubiquitous: The `strands_teams` display core shall accept an injected phase count, panel title, phase-name map, and discovery-extractor callable rather than hardcoding `/5`, `"Code Context Agent"`, or codebase-specific discovery keys (explore.yaml hidden coupling #8).

AC-4-2
Dependencies: AC-4-1
State-driven: While teams have been dispatched, the display core shall render the coordinator layout (timer, phase indicator, team table, tool summary, discovery feed, active-tool spinner, recent-tool history); otherwise it shall render the single-agent layout.

AC-4-3
Dependencies: AC-4-1
Event-driven: When the phase indicator is rendered, it shall show the current phase index and the injected total phase count (e.g. `[3/N]`) using the injected phase-name map, not a literal `5`.

AC-4-4
Dependencies: AC-4-1
Event-driven: When a tool result is passed to the injected discovery extractor and it returns a discovery event, then the display core shall add that event to the discovery feed; and when it returns nothing, the feed shall be unchanged.

AC-4-5
Dependencies: AC-4-1
Unwanted behavior: If a tool result is not valid JSON or carries an error status, then the discovery extractor shall return nothing and the display core shall not raise.

---

## User Story 5 — R5: UseCase contract

### Acceptance Criteria

AC-5-1 [P]
Ubiquitous: The `strands_teams` core shall define a `UseCase` abstraction (Plugin subclass or protocol per research-strands.yaml §Plugins) that supplies: the domain tools list, the coordinator system-prompt template plus a planning-context loader, the structured-output model, the findings-format directive, the artifact naming scheme and output-structure spec, the scoring rubric, the tool→phase map with phase names, the tool→reasoning-prompt map, the fail-fast exempt set, the kickoff-prompt builder, and the settings (env prefix and output dir).

AC-5-2
Dependencies: AC-5-1
Ubiquitous: The `UseCase` contract shall carry per-team shared state (output dir, repo/root path, tool registry, timeouts, rubric) through `invocation_state` accessed via `@tool(context=True)` rather than through module-level mutable globals (research-strands.yaml §ToolContext; explore.yaml hidden coupling #2).

AC-5-3
Dependencies: AC-5-1
Event-driven: When the coordinator factory is given a `UseCase`, then it shall build the coordinator Agent's tool list as the six coordinator tools plus the use-case's domain tools, render the system prompt from the use-case template, and set the structured-output model from the use-case.

AC-5-4
Dependencies: AC-5-1
State-driven: While a team agent's tool list is being assembled inside `dispatch_team`, the core shall auto-append the `write_file` tool to every team agent even when the use-case or coordinator omitted it (explore.yaml hidden coupling #9).

AC-5-5
Dependencies: AC-5-1
Unwanted behavior: If a `UseCase` omits a required member (tools, prompt template, structured-output model, rubric, or settings), then constructing the coordinator with that use-case shall fail with an explicit error naming the missing member rather than proceeding with a silent default.

---

## User Story 6 — R6: Codebase use-case implementation

### Acceptance Criteria

AC-6-1
Dependencies: AC-5-1
Ubiquitous: The `code_context_agent` package shall provide a `UseCase` implementation that supplies the analysis tool roster (discovery, git, BM25, shell tools plus the GitNexus and context7 MCP providers), the coordinator prompt template and partials/steering, the `AnalysisResult` structured-output model, the `AnalysisPhase` map with its five phase names, the `_REASONING_PROMPTS` map, the fail-fast exempt set, and the `CODE_CONTEXT_`-prefixed settings with `.code-context` output dir.

AC-6-2
Dependencies: AC-6-1
Ubiquitous: The `code_context_agent` package shall provide the `read_heuristic_summary` tool and the deterministic indexer, retaining production of `heuristic_summary.json` in the `.code-context` layout.

AC-6-3
Dependencies: AC-6-1, AC-2-2, AC-2-3, AC-2-4, AC-2-6, AC-2-7
Event-driven: When `code-context-agent analyze <repo>` completes on a repository with a pre-built index, then it shall write `CONTEXT.md`, one or more `BUNDLE.{area}.md` files under `bundles/`, and `analysis_result.json` into the `.code-context` output directory, matching the pre-split artifact set.

AC-6-4
Dependencies: AC-6-2
Event-driven: When `code-context-agent index <repo>` completes, then it shall produce `heuristic_summary.json` and the deterministic index artifacts under `.code-context` without any LLM invocation.

AC-6-5
Dependencies: AC-6-3
Event-driven: When `code-context-agent analyze <repo> --bundles-only` is run against existing team findings, then it shall skip indexing and team dispatch and regenerate bundles from the existing `findings.md` files.

AC-6-6
Dependencies: AC-6-1
Unwanted behavior: If the GitNexus or context7 MCP provider cannot be constructed (npx missing or provider disabled), then the use-case shall omit that provider and continue rather than aborting coordinator construction.

---

## User Story 7 — R7: Deletions

### Acceptance Criteria

AC-7-1 [P]
Ubiquitous: The codebase shall contain no `verdict.py`, `temporal.py`, `issues/`, or `ci/` module, and no `verdict` or `ci-init` CLI command.

AC-7-2 [P]
Ubiquitous: The codebase shall contain no `mcp/server.py`, `mcp/registry.py`, `serve` CLI command, `list_repos`/`start_analysis`/`check_analysis` MCP tooling, or multi-repo registry.

AC-7-3
Dependencies: AC-7-2
Ubiquitous: No `pyproject.toml` in the workspace shall list `fastmcp` as a dependency.

AC-7-4 [P]
Ubiquitous: The codebase shall contain no `models/index.py`, `exceptions.py`, `templates/context_output.md.j2`, `tests/test_viz.py`, or `render_steering` function (all zero-reference dead code per explore.yaml moving_inventory).

AC-7-5 [P]
Ubiquitous: The `.gitlab-ci.yml` shall contain no `pnpm --dir ui` job and the dependabot config shall contain no `directory: /ui` npm entry (both dead — `ui/` absent).

AC-7-6
Dependencies: AC-7-1, AC-7-2, AC-7-4
Unwanted behavior: If any surviving module or test imports a deleted symbol (verdict, temporal, issues, ci, mcp server/registry, index metadata, exceptions), then the import check and test collection shall fail, so no dangling reference to dropped code remains.

---

## User Story 8 — R8: Strands 1.47 migration specifics

### Acceptance Criteria

AC-8-1
Dependencies: AC-2-3, AC-5-2
Ubiquitous: The `dispatch_team` tool shall be an `async def` decorated with `@tool(context=True)` that builds fresh `Agent` nodes per team and awaits `swarm.invoke_async`, replacing the synchronous `strands_tools.swarm.swarm` wrapper (research-strands.yaml VERDICT; Concurrency).

AC-8-2
Dependencies: AC-8-1
State-driven: While constructing each team's `Agent` node, the core shall assign an explicit `BedrockModel` to that node so no node silently falls back to a default model (research-strands.yaml — per-agent model config was a no-op bug).

AC-8-3
Dependencies: AC-8-1
Ubiquitous: The native `Swarm` shall be constructed with `repetitive_handoff_detection_window=8` and `min_unique_agents=3` to preserve the ping-pong handoff detection that the dropped wrapper defaulted to (research-strands.yaml Caveats).

AC-8-4
Dependencies: AC-5-2
Event-driven: When the coordinator invokes teams, then per-team shared state shall propagate through `swarm(task, invocation_state={...})` to node tools and tool hooks, and no `coordinator_tools.configure()` module-level setter shall remain (research-strands.yaml §ToolContext).

AC-8-5 [P]
Ubiquitous: The coordinator's `BedrockModel` shall set prompt caching via `cache_config=CacheConfig(strategy="auto")` rather than the deprecated `cache_prompt`, keeping the existing `additional_request_fields` (adaptive thinking, output-config effort, context-1m beta) intact (research-strands.yaml §BedrockModel).

AC-8-6
Dependencies: AC-6-1
State-driven: While constructing the GitNexus and context7 MCP providers, the factory shall pass `continue_on_error` to `MCPClient` so a failing npx-launched server degrades gracefully instead of aborting the run (research-strands.yaml Additive).

AC-8-7
Dependencies: AC-5-3
Ubiquitous: The coordinator Agent's structured output shall be configured via the `structured_output_model` constructor kwarg (or per-call kwarg) and read from `result.structured_output`, not via the deprecated `agent.structured_output()` method (research-strands.yaml §Structured output).

AC-8-8
Dependencies: AC-8-1
Unwanted behavior: If a team swarm exceeds its execution or node timeout, then `dispatch_team` shall surface a timeout-tagged error result and still record the `team_debug.jsonl` entry, rather than hanging or crashing the coordinator.

---

## User Story 9 — R9: Lockstep tooling, CI, and tests

### Acceptance Criteria

AC-9-1
Dependencies: AC-1-3, AC-6-1
Ubiquitous: The commitizen `version_files`, coverage `source`/`--cov` paths (`pyproject.toml`, `.github/workflows/ci.yml`, `.gitlab-ci.yml`, `mise.toml`), and per-file-ignore globs shall reference the package under `packages/code-context-agent/src/code_context_agent` (and `packages/strands-teams/src/strands_teams` where the core is covered).

AC-9-2
Dependencies: AC-9-1
Event-driven: When `uv.lock` is regenerated after the workspace split, then it shall resolve both workspace members and shall be committed alongside the two `pyproject.toml` files.

AC-9-3
Dependencies: AC-6-1, AC-3-1, AC-4-1, AC-2-1
Ubiquitous: Every test module shall import from and mock-patch the package where its component now lives (e.g. `code_context_agent.tools.discovery.*` patches follow the moved module; core-hook and core-display tests target `strands_teams.*`), with no patch string pointing at a pre-split path.

AC-9-4
Dependencies: AC-7-1, AC-7-2, AC-7-4
Ubiquitous: Tests for dropped components (verdict, temporal, issues, ci, mcp server/registry, viz) shall be deleted, and tests for moved components shall live in the test tree of the package that now owns them.

AC-9-5
Dependencies: AC-9-1, AC-9-3
Event-driven: When `mise run check` (lint, format-check, typecheck, test) is run over the workspace, then it shall pass for both packages with a non-error exit code.

AC-9-6
Dependencies: AC-9-1
Ubiquitous: The `scripts/security_report.py` self-ignore list shall include both distribution names so neither workspace package is flagged as an external dependency.

AC-9-7
Dependencies: AC-9-1
Unwanted behavior: If `strands-agents-tools` is retained only for the `graph` tool, then that dependency shall appear only in `packages/code-context-agent`; and if the `graph` tool is dropped, then no workspace package shall depend on `strands-agents-tools`.

---

## User Story 10 — R10: Documentation

### Acceptance Criteria

AC-10-1
Dependencies: AC-6-1, AC-7-1, AC-7-2
Ubiquitous: `AGENTS.md`, `CLAUDE.md`, and `README.md` shall describe the two-package workspace, the `UseCase`-based architecture, and the surviving CLI surface (`analyze`, `index`), with no reference to `serve`, `verdict`, `ci-init`, the FastMCP server, or the multi-repo registry.

AC-10-2
Dependencies: AC-10-1
Event-driven: When the mkdocs reference is generated, then `docs/gen_ref_pages.py` shall iterate both `packages/strands-teams/src/strands_teams` and `packages/code-context-agent/src/code_context_agent`, and the mkdocs nav shall not link removed reference pages.

AC-10-3 [P]
Ubiquitous: A new ADR recording the workspace-split decision (two packages, native Swarm, dropped subsystems) shall be added to the docs ADR set.
