# Tools Overview

code-context-agent ships with **26 built-in tools** plus runtime MCP tools from GitNexus (6+) and context7 (2), organized into 6 categories. The Strands coordinator agent selects and orchestrates tools automatically based on the codebase being analyzed.

The team-orchestration tools (`dispatch_team`, `read_team_findings`, `write_bundle`, `score_narrative`, `enrich_bundle`) are provided by the [`strands-teams`](https://github.com/theagenticguy/code-context-agent) framework package; the discovery, search, git, and shell tools live in the `code-context-agent` use-case package.

## Tool Inventory

| Category | Count | Source | Purpose |
|----------|-------|--------|---------|
| [**Coordinator**](#coordinator) | 5 | `strands_teams.teams` | Team dispatch, findings, bundles, scoring |
| [**Discovery**](discovery.md) | 11 | `tools/discovery.py` | File inventory, bundling, search, orientation |
| [**Search**](search.md) | 1 | `tools/search/tools.py` | BM25-ranked full-text search |
| [**Git**](git.md) | 7 | `tools/git.py` | Temporal analysis, coupling, authorship |
| [**Shell**](shell.md) | 1 | `tools/shell_tool.py` | Security-hardened command execution |
| **Planning** | 1 | `usecase.py` | `read_heuristic_summary` domain tool |
| **Built-in total** | **26** | | |
| **GitNexus MCP** | 6+ | Runtime via `npx gitnexus mcp` | Structural code intelligence |
| **context7 MCP** | 2 | Runtime via `npx @upstash/context7-mcp` | Library documentation lookup |

All tool inputs are validated for security. Path parameters pass through traversal prevention, glob patterns are checked for injection characters, and search patterns are validated for length and syntax. See [Security](../security/overview.md) for details.

## Coordinator {: #coordinator }

Five framework tools (from `strands-teams`) used by the coordinator agent to manage multi-agent team workflows, plus the `read_heuristic_summary` planning tool supplied by the use case.

| Tool | Description |
|------|-------------|
| `dispatch_team` | Dispatch a specialist Swarm team to investigate a codebase area |
| `read_team_findings` | Read findings from dispatched teams (list all or read specific) |
| `write_bundle` | Write narrative bundles (`BUNDLE.{area}.md`) or `CONTEXT.md` |
| `score_narrative` | Score a bundle's quality on specificity, structure, diagrams, depth, cross-references |
| `enrich_bundle` | Read an existing bundle with feedback for chain-of-density enrichment |
| `read_heuristic_summary` | (use-case planning tool) Read the pre-computed heuristic summary from the deterministic indexer |

## Discovery

Eleven tools for file discovery, content bundling, and text search. See [Discovery & Search](discovery.md) for full documentation.

| Tool | Description |
|------|-------------|
| `create_file_manifest` | Create ignore-aware file manifest using ripgrep |
| `repomix_orientation` | Token-aware orientation snapshot (directory structure + token distribution) |
| `repomix_bundle` | Pack curated files into markdown context bundle |
| `repomix_bundle_with_context` | Bundle repository files with git diffs and commit logs |
| `repomix_compressed_signatures` | Extract code signatures via Tree-sitter compression |
| `repomix_json_export` | Export repository structure as JSON for programmatic analysis |
| `repomix_split_bundle` | Split large bundles into multiple chunks |
| `rg_search` | Regex search using ripgrep with count mode |
| `write_file_list` | Write curated file path lists for bundling |
| `write_file` | Write content to `.code-context/` output directory |
| `read_file_bounded` | Read a file with line bounds for safe analysis |

## Search

One BM25-ranked search tool. See [BM25 Search](search.md) for full documentation.

| Tool | Description |
|------|-------------|
| `bm25_search` | Ranked text search using BM25 algorithm (TF-IDF-like relevance) |

## Git

Seven tools for git history analysis. See [Git History](git.md) for full documentation.

| Tool | Description |
|------|-------------|
| `git_hotspots` | Identify frequently changed files (change hotspots) |
| `git_files_changed_together` | Find files that frequently co-change (coupling detection) |
| `git_blame_summary` | Authorship summary for a file |
| `git_file_history` | Commit history for a specific file |
| `git_contributors` | Repository contributor statistics |
| `git_recent_commits` | Recent commits across the repository |
| `git_diff_file` | Unified diff for a specific file |

## Shell

One security-hardened shell tool. See [Shell](shell.md) for full documentation.

| Tool | Description |
|------|-------------|
| `shell` | Bounded command execution with program allowlist and git read-only enforcement |

## Orchestration

| Tool | Description |
|------|-------------|
| `graph` | Multi-agent DAG execution from `strands_tools` |

## GitNexus MCP

Six tools provided by the [GitNexus](https://www.npmjs.com/package/gitnexus) MCP server at runtime. These provide structural code intelligence via a Tree-sitter-powered knowledge graph. Enabled when `CODE_CONTEXT_GITNEXUS_ENABLED=true` (default) and `npx` is available.

| Tool | Description |
|------|-------------|
| `gitnexus_query` | Find code by concept (returns execution flows ranked by relevance) |
| `gitnexus_context` | 360-degree view of a symbol (callers, callees, process participation) |
| `gitnexus_impact` | Blast radius analysis before editing a symbol |
| `gitnexus_detect_changes` | Pre-commit scope check (verify only expected symbols changed) |
| `gitnexus_cypher` | Custom Cypher queries against the knowledge graph |
| `gitnexus_list_repos` | List repositories indexed by GitNexus |

## context7 MCP

Two tools provided by the [context7](https://www.npmjs.com/package/@upstash/context7-mcp) MCP server for library documentation lookup. Enabled when `CODE_CONTEXT_CONTEXT7_ENABLED=true` (default) and `npx` is available.

| Tool | Description |
|------|-------------|
| `context7_resolve-library-id` | Resolve a library name to a context7 library identifier |
| `context7_query-docs` | Query documentation for a resolved library |

## How Tools Are Selected

The coordinator agent reads the **heuristic summary** produced by the deterministic indexer, then dispatches specialist Swarm teams with tool subsets tailored to their mandate:

1. **Indexer** -- Deterministic pipeline builds file manifest, GitNexus graph, git hotspots, static scan findings, and heuristic summary
2. **Coordinator reads heuristic summary** -- Plans teams based on codebase size, community count, health signals
3. **Scout teams** -- Light tools (`gitnexus_query`, `git_hotspots`, `rg_search`) survey areas quickly
4. **Deep teams** -- Full tools (`gitnexus_context`, `gitnexus_impact`, `read_file_bounded`) investigate flagged areas
5. **Bundle writing** -- Coordinator cross-references team findings and writes narrative bundles
