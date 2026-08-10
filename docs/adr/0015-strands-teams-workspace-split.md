# ADR-0015: Split into a two-package workspace (`strands-teams` + `code-context-agent`)

**Date**: 2026-07-16

**Status**: accepted

## Context

Through v3, `code-context-agent` was a single distribution that mixed two
concerns that change for different reasons:

1. A **domain-agnostic agent-teams framework** — coordinator construction, team
   dispatch, findings/artifact persistence, narrative scoring, the hook stack,
   the Rich/JSON display, layout, and settings.
2. A **codebase-analysis use case** — the discovery/git/BM25/shell tool roster,
   the GitNexus and context7 MCP providers, the deterministic indexer, the
   `AnalysisResult` output model, and the coordinator prompt templates.

Because both lived in one importable package, the framework could not be reused
for any other domain without dragging in code-context specifics, and the tool
surface had accreted features that no longer earned their keep. The single
package also carried subsystems that had drifted out of the core value
proposition: a deterministic **verdict** engine, **temporal** risk-trend
computation, a GitHub **issues** provider, a **ci-init** workflow generator, and
a **FastMCP server** plus a **multi-repo registry** exposed through a `serve`
command. Each added dependencies, test surface, CLI commands, and documentation
that the primary `analyze`/`index` flow did not need.

A separate line of drift was the multi-agent execution model. The pipeline
had moved from a hand-rolled Swarm orchestration (ADR-0012, already superseded
by ADR-0013's coordinator dispatch) toward strands-agents' native team/Swarm
primitives. Standing on the framework's own dispatch instead of bespoke glue
was only practical once the framework code was cleanly separated from the use
case.

Forces at play:

- **Reuse**: the teams framework should be installable and testable on its own,
  with a stable `UseCase` contract that any domain can implement.
- **Focus**: the CLI should present only the surviving, load-bearing commands.
- **Dependency hygiene**: framework-only dependencies should not leak into the
  use case, and use-case-only dependencies (GitNexus/context7 providers, the
  indexer's scanners) should not burden the framework.
- **Release discipline**: the split is a breaking change to the import surface
  and the CLI, so it must land as a major version bump.

## Decision

Restructure the repository as a **uv workspace with two members** under
`packages/`:

- **`strands-teams`** (import package `strands_teams`) — the domain-agnostic
  framework. Public surface: `UseCase` (+ `validate_use_case`), `run_teams` /
  `run_teams_sync`, `create_coordinator`, `TeamsLayout`, `PhaseSpec`,
  `TeamsSettings`, the team tools (`dispatch_team`, `read_team_findings`,
  `write_artifact`, `score_narrative`, `enrich_artifact`), the narrative rubric,
  the hook stack, and the Rich/JSON display. Depends only on
  `strands-agents>=1.47`, pydantic, jinja2, loguru, and rich.
- **`code-context-agent`** (import package `code_context_agent`) — the
  codebase-analysis use case. Ships `CodebaseAnalysisUseCase` (implementing the
  `strands_teams.UseCase` contract), the `analyze` / `index` / `check` CLI, the
  deterministic indexer, the discovery/git/BM25/shell tools, the GitNexus and
  context7 MCP providers, and the `AnalysisResult` output model. Depends on
  `strands-teams` (via `[tool.uv.sources] workspace = true`) plus cyclopts and
  rank-bm25.

The root `pyproject.toml` is a **virtual workspace project** (no `[project]`
table). It declares `[tool.uv.workspace] members = ["packages/*"]`, hosts the
repo-wide dev/docs/security dependency groups, and holds the shared tool
configuration (ruff, ty, coverage over both `code_context_agent` and
`strands_teams`, pytest with `--import-mode=importlib`, bandit, pip-licenses).
Commitizen lives in the `code-context-agent` member and its `version_files`
points at `packages/code-context-agent/src/code_context_agent/__init__.py`.

Alongside the split, the following subsystems are **dropped**:

- **verdict** engine and its CLI command,
- **temporal** risk-trend module,
- GitHub **issues** provider,
- **ci-init** workflow generator,
- **FastMCP server** and the **multi-repo registry**, and the `serve` command.

The surviving CLI surface is `analyze`, `index`, and `check`. Execution uses
**strands-agents' native team/Swarm dispatch** through the framework's
`run_teams` runner rather than bespoke orchestration. The `strands_tools`
`graph` tool is also dropped from the analysis roster.

Because this changes both the import surface and the CLI, it lands as a
**breaking change** (major version bump, `feat!`).

## Consequences

**Positive:**

- The teams framework is independently reusable and independently testable; a
  new domain implements `UseCase` without importing any code-context specifics.
- The CLI and dependency graph are smaller and match the actual value path.
- Framework and use-case dependencies are cleanly separated; each package's
  `pyproject.toml` declares only what it needs.
- Standing on native strands Swarm dispatch removes hand-rolled orchestration
  glue and keeps the framework aligned with the SDK.
- Coverage, lint, type-check, and the docs reference now span both packages via
  workspace-aware configuration.

**Negative:**

- Breaking change: importers of the old `code_context_agent.*` framework paths
  and users of `serve` / `verdict` / `ci-init` must migrate. This supersedes the
  FastMCP server (ADR-0004) and the multi-repo registry (ADR-0010).
- Two packages mean two `pyproject.toml` files and a lockfile that must resolve
  both members; contributors must understand the workspace layout.
- Test collection requires `--import-mode=importlib` because both packages ship
  a top-level `tests` package.

**Neutral:**

- `strands-agents` is pinned to `>=1.47` in both packages for the native team
  APIs; the nightly dependency-update job can still bump it within that range.
- This continues the trajectory of ADR-0012 → ADR-0013: the coordinator-dispatch
  model is retained, now expressed through the extracted framework.
