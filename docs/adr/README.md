# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for code-context-agent.

ADRs document significant architectural decisions made during development, including the context that motivated the decision, the decision itself, and its consequences.

## Format

We follow [Michael Nygard's ADR format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):

- **Title**: ADR-NNNN: Short descriptive title
- **Date**: When the decision was made or recorded
- **Status**: Current lifecycle state
- **Context**: What forces or constraints led to this decision
- **Decision**: What we decided and why
- **Consequences**: What results from this decision (positive, negative, neutral)

## Status Lifecycle

| Status | Meaning |
|---|---|
| **proposed** | Under discussion, not yet accepted |
| **accepted** | Approved and in effect |
| **deprecated** | No longer recommended but still in use |
| **superseded** | Replaced by a newer ADR (link to replacement) |

## Creating a New ADR

1. Copy `template.md` to `NNNN-short-title.md` (next sequential number)
2. Fill in all sections with concrete technical detail
3. Set status to `proposed`
4. Submit for review; update status to `accepted` when approved
5. If a decision replaces an older one, update the old ADR's status to `superseded by ADR-NNNN`

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-strands-agents-framework.md) | Use Strands Agents as the agent framework | accepted |
| [0002](0002-networkx-multi-layer-code-graph.md) | NetworkX multi-layer code graph | accepted |
| [0003](0003-ag-ui-event-streaming.md) | AG-UI event streaming protocol | superseded |
| [0004](0004-fastmcp-v3-server.md) | FastMCP v3 for MCP server | superseded by ADR-0015 |
| [0005](0005-lsp-fallback-chains.md) | LSP fallback chains | accepted |
| [0006](0006-security-hardened-shell.md) | Security-hardened shell with allowlist | accepted |
| [0007](0007-openspec-spec-driven-development.md) | Adopt OpenSpec for spec-driven development | accepted |
| [0008](0008-bm25-ranked-text-search.md) | BM25 ranked text search | accepted |
| [0009](0009-kuzudb-persistent-graph-backend.md) | KuzuDB persistent graph backend | accepted |
| [0010](0010-multi-repo-registry.md) | Multi-repo registry | superseded by ADR-0015 |
| [0011](0011-deterministic-indexer.md) | Deterministic indexer pipeline | accepted |
| [0012](0012-strands-swarm-multi-agent.md) | Strands Swarm multi-agent pipeline | superseded by ADR-0013 |
| [0013](0013-coordinator-team-dispatch.md) | Coordinator + team dispatch architecture | accepted |
| [0014](0014-gitnexus-code-intelligence.md) | GitNexus code intelligence | accepted |
| [0015](0015-strands-teams-workspace-split.md) | strands-teams workspace split (two packages, native Swarm) | accepted |
