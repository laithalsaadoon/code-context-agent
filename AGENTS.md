# code-context-agent

A uv workspace with two packages: **`strands-teams`**, a domain-agnostic
agent-teams framework on strands-agents, and **`code-context-agent`**, a CLI use
case that analyzes codebases and produces structured context documentation for
AI coding assistants.

> Prior ERPAVal lessons live in [`.erpaval/INDEX.md`](.erpaval/INDEX.md); grep `.erpaval/solutions/**` before starting non-trivial work.

## Architecture

Two packages under `packages/`, split along the framework/use-case seam:

- **`strands-teams`** (import `strands_teams`) — the reusable framework: the
  `UseCase` contract, the `run_teams` runner, coordinator construction, native
  Swarm team dispatch, findings/artifact persistence, narrative scoring, the
  hook stack, layout, settings, and the Rich/JSON display.
- **`code-context-agent`** (import `code_context_agent`) — the codebase-analysis
  use case: `CodebaseAnalysisUseCase`, the `analyze`/`index`/`check` CLI, the
  deterministic indexer, the discovery/git/BM25/shell tools, the GitNexus and
  context7 MCP providers, and the `AnalysisResult` output model.

A `CodebaseAnalysisUseCase` object plugs the domain into the framework; the CLI
builds it and hands it to `strands_teams.run_teams_sync`. Two-stage flow:
deterministic index (16 steps, ~30-90s) → coordinator dispatches parallel native
Swarm teams → structured output with narrative bundles. Hook-driven display
(Rich TUI or JSON logs).

```
CLI (cyclopts, code_context_agent.cli)
  ├── analyze → CodebaseAnalysisUseCase → strands_teams.run_teams_sync
  │      ├── pre_run: auto-index if heuristic_summary.json missing
  │      ├── read_heuristic_summary → plan teams
  │      ├── dispatch_team(...) × N  → parallel native Swarm teams
  │      ├── read_team_findings → cross-reference
  │      ├── write_bundle → CONTEXT.md + BUNDLE.{area}.md
  │      ├── HookProvider-driven display (TUI or JSON logs)
  │      └── AnalysisResult (structured output with bundles)
  ├── index → Deterministic Indexer (GitNexus + git + static scanners, no LLM)
  │      └── heuristic_summary.json (bridge artifact to the coordinator)
  └── check → external-tool + AWS-credential preflight

Deterministic Indexer (code_context_agent.indexer)
    ├── GitNexus analyze (Tree-sitter parsing, clustering, process tracing)
    ├── Git hotspot + co-change JSON output
    ├── Repomix signatures + orientation
    ├── Static scanners (semgrep, typecheck, lint, complexity, dead code)
    └── heuristic_summary.json (bridge to coordinator)
```

### Key source locations

Framework — `packages/strands-teams/src/strands_teams/`:

| Path | What |
|------|------|
| `usecase.py` | `UseCase` abstract contract + `validate_use_case` |
| `runner.py` | `run_teams` / `run_teams_sync` analysis runner (hook-based display) |
| `coordinator.py` | `create_coordinator` — coordinator agent factory (tools, model, prompt) |
| `teams.py` | Team tools: `dispatch_team`, `read_team_findings`, `write_artifact`, `score_narrative`, `enrich_artifact` |
| `hooks.py` | HookProviders: quality, compaction, tool efficiency, reasoning, fail-fast, team dispatch, display |
| `phases.py` | `PhaseSpec` (tool→phase map + names) |
| `rubric.py` | `NarrativeRubric` + `DEFAULT_NARRATIVE_RUBRIC` scoring |
| `layout.py` | `TeamsLayout` — run-artifact path contract |
| `settings.py` | `TeamsSettings` base (model, execution, team-timeout knobs) |
| `models.py` | `FrozenModel`, `StrictModel`, `Bundle`, `GeneratedFile`, etc. |
| `display/` | Rich TUI + JSON-log display |
| `templates/` | Framework prompt partials |

Use case — `packages/code-context-agent/src/code_context_agent/`:

| Path | What |
|------|------|
| `cli.py` | CLI entry point: `analyze`, `index`, `check` |
| `usecase.py` | `CodebaseAnalysisUseCase`: tool roster, phase spec, reasoning prompts, kickoff prompt, `pre_run` auto-index |
| `config.py` | `Settings(TeamsSettings)` via pydantic-settings, `CODE_CONTEXT_` prefix |
| `providers.py` | `build_gitnexus_provider` / `build_context7_provider` MCP providers |
| `indexer.py` | Deterministic index pipeline (GitNexus + git + static scanners) |
| `tools/discovery.py` | ripgrep, repomix, file tools |
| `tools/git.py` | Git history analysis tools |
| `tools/search/` | BM25 ranked text search |
| `tools/shell_tool.py` | Security-hardened read-only shell tool |
| `templates/` | Jinja2 prompts: `coordinator.md.j2`, partials/, steering/ |
| `models/output.py` | `AnalysisResult`, `Bundle`, `BusinessLogicItem`, `ArchitecturalRisk`, `RefactoringCandidate`, `CodeHealthMetrics` |

### Tool categories (26 built-in)

- **Coordinator** (5, from `strands_teams.teams`): `dispatch_team`, `read_team_findings`, `write_bundle` (`write_artifact`), `score_narrative`, `enrich_bundle` (`enrich_artifact`)
- **Planning** (1, use case): `read_heuristic_summary`
- **Discovery** (11): `create_file_manifest`, `repomix_orientation`, `repomix_bundle`, `repomix_bundle_with_context`, `repomix_compressed_signatures`, `repomix_json_export`, `repomix_split_bundle`, `rg_search`, `write_file`, `write_file_list`, `read_file_bounded`
- **Search** (1): `bm25_search` (BM25 ranked text search via rank_bm25)
- **Git** (7): `git_hotspots`, `git_files_changed_together`, `git_blame_summary`, `git_file_history`, `git_contributors`, `git_recent_commits`, `git_diff_file`
- **Shell** (1): `shell`
- **GitNexus MCP** (6+): `gitnexus_query`, `gitnexus_context`, `gitnexus_impact`, `gitnexus_detect_changes`, `gitnexus_cypher`, `gitnexus_list_repos`
- **context7 MCP**: `context7_resolve-library-id`, `context7_query-docs`

The `strands_tools` `graph` tool is intentionally dropped (see ADR-0015).

### State management

- **Team findings**: File-based under the layout root at `.code-context/tmp/teams/{team_id}/findings.md` + `metadata.json`, addressed via `TeamsLayout`
- **Heuristic summary**: `.code-context/heuristic_summary.json` (bridge between indexer and coordinator; the planning sentinel resolved by `TeamsLayout.planning_sentinel_path()`)
- **GitNexus / context7**: MCP server lifecycle managed by strands MCPClient in `providers.py`
- **BM25 index cache**: Module-level `_indexes` dict in `tools/search/`
- **Config**: Cached singleton via `get_settings()` with `@lru_cache`

## Patterns and conventions

### Tools use the `@tool` decorator from strands

```python
from strands import tool

@tool
def my_tool(param: str, option: int = 10) -> str:
    """Docstring becomes the tool description for the LLM.

    Args:
        param: Description used by the LLM for parameter understanding.
        option: Description with default shown.
    """
    return json.dumps({"status": "success", "result": ...})
```

All tools return JSON strings. Error responses use `{"status": "error", "message": "..."}`.

### The `UseCase` contract bridges domain to framework

A domain plugs into `strands_teams` by subclassing `strands_teams.UseCase` and
supplying the tool roster, prompt template, phase spec, reasoning prompts,
structured-output model, and settings. `CodebaseAnalysisUseCase`
(`code_context_agent/usecase.py`) is the reference implementation. Build one and
hand it to `run_teams` / `run_teams_sync`.

### Imports inside functions to avoid circular imports

`CodebaseAnalysisUseCase.domain_tools()` imports all tool modules inside the
method (factory pattern) rather than at module level. Follow this pattern when
adding new tool modules.

### Pydantic models use custom base classes

- `FrozenModel`: Immutable, for data transfer (output models)
- `StrictModel`: Mutable, for internal state

Both are exported from `strands_teams` (defined in `strands_teams/models.py`).
Use `FrozenModel` for new data models.

## Development

### Prerequisites

- Python 3.13+ (managed via `mise`)
- Node.js 22+ (managed via `mise`)
- `uv` for Python package management
- External CLIs: `rg` (ripgrep), `repomix`, `npx` (for context7), `gitnexus` (GitNexus CLI for code graph indexing)
- AWS credentials configured for Bedrock access

### Commands

| Task | Command |
|------|---------|
| Install all deps | `mise run install:all` |
| Install Python deps | `mise run install` |
| Run CLI | `uv run code-context-agent` |
| Analyze a repo | `uv run code-context-agent analyze /path/to/repo` |
| Analyze (bundles only) | `uv run code-context-agent analyze /path --bundles-only` |
| Index a repo | `uv run code-context-agent index /path/to/repo` |
| Check tool deps | `uv run code-context-agent check` |
| Lint (Python) | `mise run lint` |
| Format (Python) | `mise run format` |
| Type check (Python) | `mise run typecheck` |
| Test | `mise run test` |
| All checks | `mise run check` |
| Build | `mise run build` |
| Commit | `uv run cz commit` |
| Bump + tag | `uv run cz bump` then `git push origin <tag>` |

### Git hooks (lefthook)

Hooks are enforced automatically. Do not skip them.

- **pre-commit**: ruff check+fix, ruff format, ty check, betterleaks
- **commit-msg**: conventional commit validation via commitizen
- **pre-push**: lint, format-check, typecheck, test, betterleaks, semgrep OWASP

### Conventional commits

All commits must follow conventional commit format. Commitizen enforces this.

- `feat:` → MINOR bump
- `fix:` → PATCH bump
- `feat!:` or `BREAKING CHANGE:` footer → MAJOR bump
- `docs:`, `chore:`, `refactor:`, `test:`, `ci:` → no version bump

**Important**: Do not include `BREAKING CHANGE:` in the commit body unless there
is an actual breaking change. Commitizen parses it literally and will trigger a
MAJOR version bump.

After `cz bump`, push the tag explicitly: `git push origin v<version>` (commitizen
creates lightweight tags, `--follow-tags` only pushes annotated tags).

### Code style

- Line length: 120
- Python 3.13+ typing: `list[str]` not `List[str]`, `X | None` not `Optional[X]`
- Google-style docstrings
- `pathlib.Path` over `os.path`
- `from __future__ import annotations` in all modules

### Testing

- `pytest` with `asyncio_mode = "auto"`, run from the workspace root
- Tests live in each package's `tests/` mirroring its `src/` structure
- Both packages ship a top-level `tests` package, so pytest runs with
  `--import-mode=importlib` (configured in the root `pyproject.toml`)
- Framework and use-case components have unit tests; no full end-to-end
  analysis-pipeline integration test yet

## CI/CD (GitLab)

Pipeline stages: lint → test → build-docs → pages → build → release

- **Release** only triggers on tags matching `^v\d+\.\d+\.\d+$`
- **Pages** deploys mkdocs to GitLab Pages on main branch pushes
- Uses `ghcr.io/astral-sh/uv:0.9-python3.13-bookworm-slim` image
- Lint / type-check / bandit / semgrep all scan `packages/`; coverage spans both
  `code_context_agent` and `strands_teams`

## Configuration reference

The use case reads env vars with the `CODE_CONTEXT_` prefix. Most knobs are
defined on the framework's `TeamsSettings` base and inherited by `Settings`;
the use case adds the two MCP toggles.

| Variable | Default | Notes |
|----------|---------|-------|
| `CODE_CONTEXT_MODEL_ID` | `global.anthropic.claude-opus-4-6-v1` | Bedrock model |
| `CODE_CONTEXT_REGION` | `us-east-1` | AWS region |
| `CODE_CONTEXT_TEMPERATURE` | `1.0` | Must be 1.0 for adaptive thinking |
| `CODE_CONTEXT_REASONING_EFFORT` | `high` | Standard mode thinking effort |
| `CODE_CONTEXT_FULL_REASONING_EFFORT` | `max` | Full mode thinking effort (Opus only) |
| `CODE_CONTEXT_GITNEXUS_ENABLED` | `true` | Enable GitNexus MCP for structural code intelligence |
| `CODE_CONTEXT_CONTEXT7_ENABLED` | `true` | Enable context7 MCP for library docs (requires npx) |
| `CODE_CONTEXT_AGENT_MAX_TURNS` | `1000` | Standard mode max turns |
| `CODE_CONTEXT_AGENT_MAX_DURATION` | `1200` | Standard mode: 20 min default |
| `CODE_CONTEXT_FULL_MAX_TURNS` | `3000` | Full mode max agent turns |
| `CODE_CONTEXT_FULL_MAX_DURATION` | `3600` | Full mode: 60 min default |
| `CODE_CONTEXT_TEAM_EXECUTION_TIMEOUT` | `900` | Max seconds per team swarm (standard) |
| `CODE_CONTEXT_TEAM_NODE_TIMEOUT` | `900` | Max seconds per agent node in team (standard) |
| `CODE_CONTEXT_FULL_TEAM_EXECUTION_TIMEOUT` | `2400` | Max seconds per team swarm (full) |
| `CODE_CONTEXT_FULL_TEAM_NODE_TIMEOUT` | `1800` | Max seconds per agent node in team (full) |
| `CODE_CONTEXT_OTEL_DISABLED` | `true` | Avoids context detachment errors |

A use case built on the framework directly (without the `CODE_CONTEXT_` prefix)
reads the same knobs under the `TEAMS_` prefix via `TeamsSettings`.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **code-context-agent** (3325 symbols, 5152 relationships, 41 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/code-context-agent/context` | Codebase overview, check index freshness |
| `gitnexus://repo/code-context-agent/clusters` | All functional areas |
| `gitnexus://repo/code-context-agent/processes` | All execution flows |
| `gitnexus://repo/code-context-agent/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
