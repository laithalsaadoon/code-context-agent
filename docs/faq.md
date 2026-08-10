# FAQ

Frequently asked questions about code-context-agent.

---

### How much does analysis cost?

Token consumption depends on the analysis mode and codebase size:

| Mode | Input tokens | Output tokens |
|------|-------------|---------------|
| Standard | 50K--200K | 10K--30K |
| Full | 200K--500K | 30K--50K |
| Index only (`index` command) | 0 | 0 |

The `index` command makes zero LLM calls -- it builds the index entirely with deterministic tools (GitNexus, git history, repomix, semgrep, radon, vulture, knip, ruff, ty). Cost depends on your Amazon Bedrock pricing tier (on-demand vs. provisioned throughput).

!!! tip
    Run `code-context-agent index .` first (free). The `analyze` command loads the pre-built graph automatically, reducing the token budget spent on structural discovery.

---

### Is it safe to run on my codebase?

Yes. The agent operates under strict security constraints:

- **Read-only filesystem access** -- the agent cannot modify your source code.
- **Shell allowlist** -- the `shell` tool enforces a read-only program allowlist. Destructive commands (`rm`, `mv`, `curl`, `wget`, etc.) and shell operators (`|`, `;`, `&&`, `>`, etc.) are blocked.
- **Input validation** -- all tool inputs are validated for path traversal (`..`) and injection attacks.
- **No external data exfiltration** -- data is sent only to Amazon Bedrock for LLM inference. Nothing is sent to third-party services (context7 runs locally via `npx`).
- **Scoped output** -- all output files are written exclusively to `.code-context/` (or your custom `--output-dir`).

See the [Security](security/overview.md) page for full details on shell hardening, input validation, and CI security scanning.

---

### Why Claude Opus 4.6 specifically?

Opus 4.6 is selected as the default for several reasons:

- **Complex multi-tool reasoning** -- the analysis pipeline involves 26+ tools used by the coordinator and parallel team agents, plus GitNexus and context7 MCP tools. Opus-class models handle long tool chains and cross-signal synthesis more reliably.
- **Adaptive thinking (extended reasoning)** -- improves architectural analysis quality by allowing the model to reason through ambiguous structural patterns.
- **1M context window** -- via `anthropic_beta: context-1m-2025-08-07`, the model can process large codebases without truncation.
- **Cross-region inference** -- the `global.` prefix in the default model ID (`global.anthropic.claude-opus-4-6-v1`) provides best availability across AWS regions.

---

### Can I use a different model?

Yes. Set the `CODE_CONTEXT_MODEL_ID` environment variable to any Bedrock model ID:

```bash
export CODE_CONTEXT_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0
```

!!! warning
    Models without adaptive thinking support require `CODE_CONTEXT_TEMPERATURE` to be set below 1.0 (temperature 1.0 is required specifically for thinking-enabled models). The system prompts and tool descriptions are optimized for Opus-class models -- results may vary with smaller models.

---

### Does it work with monorepos?

Yes, but large monorepos (more than 10,000 files) benefit from these strategies:

1. **Scope with `--focus`** to analyze a specific area:
    ```bash
    code-context-agent analyze . --focus "payments service"
    ```

2. **Pre-index** to separate the (free) index-building step from the (token-consuming) analysis step:
    ```bash
    code-context-agent index .
    code-context-agent analyze .
    ```

---

### What languages are supported?

The analysis is **language-agnostic** at the core level. Ripgrep, git history, repomix, and GitNexus (Tree-sitter parsing) work with any programming language.

**GitNexus** provides structural code intelligence (symbols, relationships, execution flows, community detection) for languages supported by Tree-sitter.

**Static analysis tools** provide deeper analysis for specific languages:

| Tool | Languages | What it provides |
|------|-----------|-----------------|
| semgrep | Most languages | Security findings, OWASP Top Ten |
| ty / pyright | Python | Type checking |
| ruff | Python | Linting |
| radon | Python | Cyclomatic complexity |
| vulture | Python | Dead code detection |
| knip | TypeScript / JavaScript | Dead code detection |

---

### How do coding agents consume the output?

`code-context-agent` writes its analysis to a `.code-context/` directory as
plain markdown and JSON. AI coding assistants (Claude Code, Cursor, and others)
consume those artifacts directly — point the assistant at `CONTEXT.md` as the
entry point, and expand into the per-area `bundles/BUNDLE.{area}.md` files as
needed. The structured `analysis_result.json` is available for programmatic
consumption.

For live structural code intelligence (impact analysis, symbol context, execution
flow tracing), the analysis relies on the external [GitNexus](https://www.npmjs.com/package/gitnexus)
MCP server rather than a bundled server. The `serve` command and the built-in
FastMCP server were removed in the workspace split (see
[ADR-0015](adr/0015-strands-teams-workspace-split.md)).

---

### What's the difference between `analyze` and `index`?

| | `index` | `analyze` |
|---|---------|-----------|
| **What it does** | Builds a deterministic index (16-step pipeline) | Full LLM-driven codebase analysis |
| **Time** | ~30--90 seconds | ~5--15 minutes (standard), ~30--60 minutes (full) |
| **LLM calls** | None (deterministic) | Yes (Coordinator dispatches parallel specialist teams) |
| **Cost** | Free | Token-based (see cost FAQ above) |
| **Output** | `heuristic_summary.json`, signatures, hotspots, static scan results | `CONTEXT.md`, `bundles/BUNDLE.{area}.md`, `analysis_result.json`, + all index artifacts |
| **Uses** | GitNexus, git, repomix, semgrep, radon, vulture, knip, ruff, ty | All index tools + LLM reasoning + ripgrep + repomix |

**Best practice**: Run `index` first, then `analyze`. The analysis coordinator reads the pre-built `heuristic_summary.json` and uses GitNexus's knowledge graph, which saves significant time and tokens on structural discovery.

```bash
code-context-agent index /path/to/repo
code-context-agent analyze /path/to/repo
```

---

### What output files should I use?

After running `analyze`, the `.code-context/` directory contains:

| File | Best for |
|------|----------|
| `CONTEXT.md` | Feeding to AI coding assistants as context. This is the narrated architecture document. |
| `bundles/BUNDLE.{area}.md` | Targeted narrative bundles per investigation area. Each bundle covers a specific subsystem or concern identified by the coordinator. |
| `CONTEXT.bundle.md` | Compressed source code bundle (via repomix Tree-sitter compression). Useful as supplementary context. |
| `CONTEXT.signatures.md` | Compressed Tree-sitter signatures (function/class signatures, bodies stripped). |
| `heuristic_summary.json` | Bridge artifact between the deterministic indexer and the LLM coordinator. Contains volume, health, git, and GitNexus metrics. |
| `analysis_result.json` | Programmatic consumption. Contains the structured `AnalysisResult` with business logic items, architectural risks, and risk profiles. |

---

### How do I run incremental analysis?

The `--since` flag is accepted by the CLI but incremental mode is **not yet implemented** in V10. The flag is reserved for future use.

```bash
# Accepted but not yet functional in V10
code-context-agent analyze . --since HEAD~5
```

!!! warning
    In V10, `--since` is parsed and stored but does not change analysis behavior. Full incremental support is planned for a future release.

---

### How do I analyze a specific area of a codebase?

Use `--focus` to direct the analysis toward a specific subsystem or concern:

```bash
code-context-agent analyze . --focus "authentication and authorization"
code-context-agent analyze . --focus "database layer"
code-context-agent analyze . --focus "API endpoints"
```

The focus string is passed to the LLM agents, which prioritize exploring files, symbols, and patterns related to that area. The deterministic index and git history analysis are still performed on the full repository, but the narrative output concentrates on the focused area.

`--focus` can be combined with `--full` for exhaustive analysis scoped to a specific area:

```bash
code-context-agent analyze . --full --focus "payments"
```

---

### What is `--bundles-only`?

The `--bundles-only` flag skips indexing and team dispatch, and regenerates bundles from existing team findings already present in `.code-context/`. This is useful when you want to re-run bundle generation with a different focus or after manually editing team findings, without re-running the full analysis pipeline.

```bash
# Re-generate bundles from existing team findings
code-context-agent analyze . --bundles-only
```

!!! tip
    Use `--bundles-only` to iterate on output formatting or focus without paying the token cost of a full analysis run.

---

### What is `heuristic_summary.json`?

`heuristic_summary.json` is the bridge artifact between the deterministic indexer and the LLM coordinator. It is produced by the `index` step (or the indexing phase of `analyze`) and contains:

- **Volume metrics** -- file counts, line counts, language distribution
- **Symbol metrics** -- function/class/module counts and density
- **Health metrics** -- test coverage signals, lint/type-check indicators
- **Topology metrics** -- graph density, connected components, centrality outliers
- **Git metrics** -- churn hotspots, contributor distribution, recent activity

The coordinator agent reads this summary to decide how many parallel teams to dispatch and what investigation areas to assign. This avoids feeding the entire code graph to the LLM, keeping the coordinator prompt focused and token-efficient.
