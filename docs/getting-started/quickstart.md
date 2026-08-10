# Quick Start

## Basic Usage

```bash
# Analyze current directory
code-context-agent analyze .

# Analyze specific repository
code-context-agent analyze /path/to/repo

# Focus on specific area
code-context-agent analyze . --focus "authentication system"

# GitHub issue-focused analysis
code-context-agent analyze . --issue "gh:1694"

# Custom output directory
code-context-agent analyze . --output-dir ./analysis

# Only analyze changes since a git ref
code-context-agent analyze . --since "HEAD~5"

# JSON output format (for programmatic consumption)
code-context-agent analyze . --output-format json

# Quiet mode (suppress Rich TUI)
code-context-agent analyze . --quiet

# Debug mode (verbose logging)
code-context-agent analyze . --debug

# Exhaustive analysis (no size limits, all algorithms)
code-context-agent analyze . --full

# Regenerate bundles from existing team findings (skip indexing + dispatch)
code-context-agent analyze . --bundles-only

# Verify external tool dependencies
code-context-agent check
```

The agent automatically determines analysis depth based on repository size and complexity. Use `--full` for exhaustive analysis with no size limits.

## What Happens During Analysis

1. **Deterministic index** (~30-90s, no LLM) -- Builds structural intelligence:
    - GitNexus: Tree-sitter parsing, clustering, execution flow tracing
    - Git: hotspot and co-change analysis
    - Repomix: compressed signatures and orientation
    - Static scanners: semgrep, typecheck, lint, complexity, dead code
    - Produces `heuristic_summary.json` (compact metrics for the coordinator)
2. **Coordinator agent** -- Reads the heuristic summary and plans parallel teams
3. **Team dispatch** -- Specialist Swarm teams investigate in parallel using GitNexus, ripgrep, repomix, and git tools
4. **Consolidation** -- Coordinator cross-references team findings
5. **Bundle writing** -- Narrated bundles written to `.code-context/`

## Output Files

All outputs land in `.code-context/` (or your custom `--output-dir`):

| File | Description |
|------|-------------|
| `CONTEXT.md` | Main narrated context (executive summary) |
| `bundles/BUNDLE.{area}.md` | Targeted narrative bundles per investigation area |
| `CONTEXT.signatures.md` | Signatures-only structural view (Tree-sitter compressed) |
| `CONTEXT.orientation.md` | Token distribution tree |
| `CONTEXT.bundle.md` | Curated source code bundle |
| `files.all.txt` | Complete file manifest |
| `heuristic_summary.json` | Compact metrics bridging indexer and coordinator |
| `analysis_result.json` | Structured analysis result (Pydantic JSON) |
| `git_hotspots.json` | File churn ranking from git history |
| `git_cochanges.json` | Co-change coupling data |

## Using the Output

The `.code-context/` directory is designed for consumption by AI coding assistants. Point your assistant at `CONTEXT.md` as the entry point:

```bash
# Example: feed context to another agent
cat .code-context/CONTEXT.md | your-ai-assistant
```

The narrated context includes architecture diagrams, ranked file tables, risk assessments, and business logic summaries -- all formatted for machine parsing (tables over prose, typed schemas, bounded diagrams).

## Quick Indexing (No LLM)

For a fast, deterministic index without AI narration:

```bash
# Build the index deterministically
code-context-agent index .
```

See the [Deterministic Indexer documentation](../tools/indexer.md) for details.
