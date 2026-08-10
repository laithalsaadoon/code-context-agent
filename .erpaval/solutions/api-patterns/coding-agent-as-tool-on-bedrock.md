---
title: Wrapping Claude Agent SDK / Codex as a Strands coding tool on Amazon Bedrock
track: knowledge
category: api-patterns
module: packages/strands-teams-runtimes/src/strands_teams_runtimes/
component: claude-agent-sdk, openai-codex, strands-agents 1.47, Amazon Bedrock
severity: info
tags: [bedrock, claude-agent-sdk, codex, gpt-5.6-sol, opus-4.8, specialist-runtime, strands-tool, subprocess]
applies_when:
  - Giving a strands agent a tool that delegates a bounded coding task to an autonomous harness
  - Targeting Opus 4.8 (Claude Agent SDK) or GPT-5.6 Sol (Codex) on Amazon Bedrock
  - Building an ASTR-style SpecialistRuntime boundary (submit/collect, no raw transcript)
pattern: |
  Verified-real facts (July 2026), used to build strands-teams-runtimes. Keep an
  external coding harness behind a `@tool`, NOT A2AAgent (that's for HTTP A2A servers).

  CLAUDE AGENT SDK -> Opus 4.8 on Bedrock:
  - model id: global.anthropic.claude-opus-4-8 (BARE — no date/version suffix).
  - Route via env CLAUDE_CODE_USE_BEDROCK=1 + AWS_REGION; model via ClaudeAgentOptions(model=...).
  - query() per bounded task (own subprocess). Options: permission_mode="acceptEdits",
    allowed_tools=[Read,Edit,Write,Bash,Glob,Grep], disallowed=[WebSearch,WebFetch]
    (WebSearch unavailable on Bedrock), setting_sources=[] (>=0.1.60, isolates host
    .claude), output_format={"type":"json_schema","schema":...}, max_turns + max_budget_usd.
  - Result on final ResultMessage: subtype, result (text), structured_output (parsed
    dict — model_validate NOT model_validate_json), total_cost_usd, usage. No files-changed
    field — derive from Edit/Write ToolUseBlocks or git status. can_use_tool callback
    confines writes to the worktree (needs a streaming prompt).
  - Auth = AWS default credential chain in the subprocess env; needs claude-agent-sdk>=0.1.57
    for Opus. Field gotchas: output_format (option) vs structured_output (result);
    max_budget_usd not max_cost_usd; temperature fixed 1.0 for adaptive thinking.

  CODEX -> GPT-5.6 Sol on Bedrock (GA 2026-07-13):
  - model id: openai.gpt-5.6-sol. Endpoint: bedrock-mantle (NOT bedrock-runtime),
    Responses API, In-Region only (us-east-1/us-east-2). NO LiteLLM needed.
  - Codex has a BUILT-IN amazon-bedrock provider (wire_api="responses", the only value
    since Feb 2026). config: model_provider="amazon-bedrock",
    [model_providers.amazon-bedrock.aws] region=... ; auth via AWS_BEARER_TOKEN_BEDROCK.
  - codex exec --model openai.gpt-5.6-sol --model-provider amazon-bedrock
    -c model_reasoning_effort=high --cd WS --full-auto --json --output-schema S -o R.json
  - CONCURRENCY: per-run CODEX_HOME + per-run git worktree + --ephemeral, else parallel
    runs cross-contaminate session state (openai/codex#11435).
  - --output-schema also constrains INTERMEDIATE messages (#19816): consume the -o final
    file / turn.completed item, NOT the first schema-valid JSONL line.
  - openai-codex Python SDK (AsyncCodex) exists as an alternative to the subprocess.

  STRANDS TOOL SHAPE (see [[strands-native-swarm-over-tools-wrapper]] for invocation_state):
  - async @tool(context=True), async-generator: non-final yield = heartbeat -> swarm
    multiagent_node_stream; final yield/return = SpecialistResult JSON (never the transcript).
  - Reads workspace Path/model_id/capabilities/runtime from tool_context.invocation_state.
  - Executor imposes NO per-tool timeout — the tool OWNS asyncio.wait_for(subprocess);
    swarm node_timeout/execution_timeout MUST exceed it or the node is cancelled first.
  - module-level asyncio.Semaphore(STRANDS_TEAMS_MAX_HARNESSES) caps parallel subprocesses.
  - make_invoke_coding_agent(runtime_registry) injects runtimes so the framework CORE
    never imports an SDK — adapters live only in the runtimes package.
  - Consistency trap the review caught: thread the caller's model_id into BOTH adapters'
    command/options; a per-runtime default that silently ignores model_id is a bug.
example_files:
  - packages/strands-teams-runtimes/src/strands_teams_runtimes/claude.py
  - packages/strands-teams-runtimes/src/strands_teams_runtimes/codex.py
  - packages/strands-teams-runtimes/src/strands_teams_runtimes/tool.py
---

# Why this matters

These are non-obvious, version-specific facts (bare Opus id, Codex's built-in
amazon-bedrock provider making LiteLLM unnecessary, the two concurrency/output-schema
gotchas) that took three grounded research passes to confirm. Re-deriving them costs
real time; they're stable enough to reuse for any Bedrock coding-agent integration.

# Example

```python
@tool(context=True)
async def invoke_coding_agent(task: str, tool_context: ToolContext):  # async generator
    st = tool_context.invocation_state
    async with _HARNESS_SLOTS:
        yield f"[coding] {st['runtime']} in {st['workspace']}"      # heartbeat
        result = await asyncio.wait_for(runtime.collect(handle), timeout=T)  # own timeout
    yield result.model_dump_json()   # SpecialistResult — no transcript
```
