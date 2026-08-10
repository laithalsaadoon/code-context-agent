# strands-teams-runtimes

`SpecialistRuntime` adapters that delegate a bounded coding task to an
autonomous external harness on Amazon Bedrock, for the
[`strands-teams`](../strands-teams) framework.

Two adapters behind one interface (the vendor-free `strands_teams.SpecialistRuntime`
Protocol):

- **`ClaudeAgentRuntime`** — Claude Opus 4.8 via the Claude Agent SDK
  (`claude_agent_sdk.query()`, `CLAUDE_CODE_USE_BEDROCK=1`).
- **`CodexRuntime`** — Codex GPT-5.6 Sol via `codex exec --model-provider amazon-bedrock`.

Plus a `WorkspaceFactory` (per-run `git worktree` isolation) and the
`make_invoke_coding_agent(runtime_registry)` factory that returns the
`invoke_coding_agent` Strands `@tool` with its runtimes injected — so the SDK
dependencies never leak into `strands-teams` core.

The tool returns a `SpecialistResult` (structured summary, findings, evidence
refs, resource usage) as a JSON string — never the harness raw transcript
(ASTR §10.4).
