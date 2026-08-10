# Deterministic Indexer

The `index` command builds a deterministic index of a codebase without any LLM invocations. It runs static analysis tools, git history analysis, and GitNexus graph indexing to produce a `heuristic_summary.json` that bridges cheap indexing with multi-agent analysis.

## Usage

```bash
# Index the current directory
code-context-agent index .

# Index a specific repository
code-context-agent index /path/to/repo

# Custom output directory
code-context-agent index . --output-dir ./output

# Quiet mode
code-context-agent index . --quiet
```

## Pipeline

The indexer runs a fixed 16-step pipeline:

| Step | Source | What It Produces |
|------|--------|-----------------|
| 1. File manifest | ripgrep (`rg --files`) | List of all tracked files |
| 1a. Write manifest | Disk write | `files.all.txt` for BM25 search |
| 2. Language detection | File extensions | Files grouped by language (py, ts, rust, go, java) |
| 3. GitNexus analyze | `gitnexus analyze` | Tree-sitter knowledge graph (symbols, relationships, execution flows) |
| 4. Git hotspots + co-changes | `git log` analysis | `git_hotspots.json`, `git_cochanges.json` |
| 5. Repomix signatures | repomix + Tree-sitter | `CONTEXT.signatures.md` (compressed API surface) |
| 6. Repomix orientation | repomix | `CONTEXT.orientation.md` (token distribution tree) |
| 7. BM25 index | File tokenization | Pre-built search index for `bm25_search` |
| 8. Semgrep auto | semgrep --config auto | `semgrep_auto.json` (security findings) |
| 9. Semgrep OWASP | semgrep OWASP rules | `semgrep_owasp.json` (OWASP category findings) |
| 10. Type checker | ty / pyright | `typecheck.json` (type errors) |
| 11. Linter | ruff | `lint.json` (lint violations) |
| 12. Complexity | radon | `complexity.json` (cyclomatic complexity) |
| 13. Dead code (Python) | vulture | `dead_code_py.json` (unused Python code) |
| 14. Dead code (TypeScript) | knip | `dead_code_ts.json` (unused TypeScript exports) |
| 15. Dependencies | pipdeptree / npm ls | `deps.json` |
| 16. Heuristic summary | All above | `heuristic_summary.json` (compact metrics for coordinator) |

## Graceful Degradation

Each step operates independently. If a tool is missing (e.g., `semgrep` not installed, GitNexus unavailable), that step is skipped and indexing continues with the remaining tools. The heuristic summary reflects whatever data was successfully collected.

## When to Use

| Scenario | Command |
|----------|---------|
| Quick index for MCP queries | `code-context-agent index .` |
| Full narrated analysis with AI | `code-context-agent analyze .` |
| CI pipeline pre-indexing | `code-context-agent index . --quiet` |
| Re-index after code changes | `code-context-agent index .` (overwrites existing) |

The `index` command produces the `.code-context/` artifact set (heuristic summary, git analysis, static scan findings, signatures, orientation). It does not generate `CONTEXT.md`, narrative bundles, or the structured `AnalysisResult`. For those artifacts, use `analyze`.

## Output

The primary output is `.code-context/heuristic_summary.json`, which summarizes all indexer steps into a compact structure:

- **volume**: file count, lines, tokens, languages, frameworks
- **health**: semgrep findings, type errors, lint violations, dead code, complexity
- **git**: commit count, contributors, most coupled file pairs
- **gitnexus**: whether GitNexus indexed the repo, community/process/symbol counts

After indexing, you can:

- Run full analysis: `code-context-agent analyze .` (uses the index as input)
- Inspect `heuristic_summary.json` and the other artifacts directly
- Point an AI coding assistant at the generated `.code-context/` artifacts
