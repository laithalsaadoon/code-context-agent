---
slug: coding-runtime
sequence: 004
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

Phase P3 of the ASTR north-star build (`docs/planning/astr-north-star-plan.md` §4 Phase 3, §5, §10). It ships the headline capability: a Strands tool that delegates a bounded coding task to an autonomous external harness — **Claude Opus 4.8** (Claude Agent SDK) or **Codex GPT-5.6 Sol** — both served on **Amazon Bedrock**. This is the concrete instance of ASTR's `SpecialistRuntime` boundary.

Two edits, one seam:

- **`strands-teams` core gains only vendor-free typed contracts** — the `SpecialistRuntime` Protocol and the `SpecialistResult` / `ResourceUsage` FrozenModels. No SDK, no `subprocess`, no AWS specifics enter core (plan §3; research-coding-runtimes.yaml `architecture`).
- **A new workspace package `packages/strands-teams-runtimes`** (import `strands_teams_runtimes`) holds the two adapters (`ClaudeAgentRuntime`, `CodexRuntime`), the `WorkspaceFactory`, and the `invoke_coding_agent` `@tool` factory. The SDK dependencies (`claude-agent-sdk`, `openai-codex`) live only here.

Grounding for every API-touching AC: the research digest `.erpaval/sessions/session-8c56b2/research-coding-runtimes.yaml` (`strands_tool_pattern`, `claude_agent_sdk_bedrock`, `codex_gpt56_bedrock`, `architecture`). Conform to those facts exactly. Current-source anchors are cited inline (`packages/strands-teams/src/strands_teams/…`).

### Cross-phase dependencies (types treated as given, not redefined here)

- **P1 `Finding`** (typed finding: severity/blocking/disposition/statement/affected_artifacts/evidence[]/confidence/lifecycle) is assumed present in `strands_teams.models` by the time P3 runs (plan Phase 1; §7 "since `SpecialistResult.findings` reuses them"). `SpecialistResult.findings` reuses it. Do **not** redefine `Finding` or its evidence-reference type in this phase.
- **P4 `ArtifactRevision`** does **not** exist in P3 (plan Phase 4 introduces it). Therefore `SpecialistResult.artifacts` is typed in P3 as a list of worktree-relative path strings (ground truth derivable from `git status --porcelain`; research-coding-runtimes.yaml `claude_agent_sdk_bedrock` "NO dedicated files-changed field"). A later phase MAY enrich this to `ArtifactRevision`; P3 must not import or forward-declare it.

### Terms

- **harness** — an autonomous external coding agent run in its own OS subprocess (Claude Agent SDK `query()` subprocess, or `codex exec`).
- **worktree** — a per-run `git worktree` serving as the harness `cwd`, the only writable root.
- **runtime registry** — a `Mapping[str, SpecialistRuntime]` (e.g. `{"claude": …, "codex": …}`) injected into the tool factory so core never imports an SDK (dependency injection; plan §3).
- **tool timeout** — `_SUBPROCESS_TIMEOUT_S`, the `asyncio.wait_for` wall-clock the tool owns; distinct from the swarm's `node_timeout`/`execution_timeout`.

---

## User Story 1 — Workspace package boundary (vendor-free core, SDK-bearing runtimes package)

### Acceptance Criteria

AC-1-1 [P]
Ubiquitous: The repository-root `pyproject.toml` `[tool.uv.workspace]` `members` glob shall resolve `packages/strands-teams-runtimes` as a workspace member alongside the existing `packages/strands-teams` and `packages/code-context-agent` members.

AC-1-2 [P]
Ubiquitous: The `packages/strands-teams-runtimes/pyproject.toml` shall define a distribution whose import package is `strands_teams_runtimes`, require `python>=3.13`, declare runtime dependencies `claude-agent-sdk>=0.1.60` and `openai-codex`, and declare a `[tool.uv.sources]` workspace dependency on `strands-teams`.

AC-1-3
Dependencies: AC-1-1, AC-1-2
Event-driven: When `uv sync` is run at the repository root, then `strands_teams`, `code_context_agent`, and `strands_teams_runtimes` shall all be installed as editable workspace members resolvable in the same environment and the `uv.lock` file shall be updated to include `strands-teams-runtimes` and its SDK dependencies.

AC-1-4
Dependencies: AC-1-3
Event-driven: When `python -c "import strands_teams_runtimes"` is executed in the synced environment, then the import shall succeed without error.

AC-1-5 [P]
Unwanted behavior: If any module under `packages/strands-teams/src/strands_teams/` imports `claude_agent_sdk`, `codex`, `openai_codex`, or launches a coding-harness subprocess, then a static import check over that source tree shall report the violation and fail (core stays domain- and vendor-free; plan §3; research-coding-runtimes.yaml `architecture`).

---

## User Story 2 — SpecialistRuntime Protocol + SpecialistResult contract (in core)

### Acceptance Criteria

AC-2-1 [P]
Ubiquitous: The `strands_teams` core shall expose a `SpecialistRuntime` Protocol declaring the methods `submit`, `status`, `send_input`, `cancel`, and `collect`, defined without importing any coding-agent SDK or `subprocess` (plan §10; research-coding-runtimes.yaml `architecture`).

AC-2-2
Dependencies: AC-2-1
Ubiquitous: The `SpecialistRuntime.collect` method's declared return type shall be `SpecialistResult`, and its contract shall state that it returns the structured result only and never the harness raw transcript, private chain-of-thought, or full terminal command log (ASTR §10.4; plan §5 "Boundary discipline").

AC-2-3
Dependencies: AC-2-1
Ubiquitous: The `strands_teams` core shall define a `SpecialistResult` `FrozenModel` (subclassing the existing `FrozenModel` at `packages/strands-teams/src/strands_teams/models.py:40`) with fields `disposition`, `artifacts`, `findings`, `evidence`, `residual_risk`, and `resource_usage`, where `findings` reuses the P1 `Finding` type and `evidence` reuses the P1 evidence-reference type.

AC-2-4 [P]
Ubiquitous: The `SpecialistResult.disposition` field shall be a `Literal["completed", "partial", "failed", "refused"]`.

AC-2-5
Dependencies: AC-2-3
Ubiquitous: The `strands_teams` core shall define a `ResourceUsage` `FrozenModel` carrying at minimum `cost_usd`, `input_tokens`, `output_tokens`, `num_turns`, and `duration_seconds`, and `SpecialistResult.resource_usage` shall be typed as `ResourceUsage`.

AC-2-6
Dependencies: AC-2-3, AC-2-5
Event-driven: When the `strands_teams` package is imported, then `SpecialistRuntime`, `SpecialistResult`, and `ResourceUsage` shall be importable from the top-level `strands_teams` namespace and present in its `__all__` (mirroring the export pattern at `packages/strands-teams/src/strands_teams/__init__.py:45`).

AC-2-7 [P]
Unwanted behavior: If a `SpecialistResult` is constructed with a `disposition` value outside the four permitted literals or with an unexpected extra field, then Pydantic validation shall raise an error rather than silently coercing or accepting it (FrozenModel `extra="forbid"`; models.py:51).

---

## User Story 3 — ClaudeAgentRuntime adapter (Opus 4.8 on Bedrock)

### Acceptance Criteria

AC-3-1
Dependencies: AC-2-1
Event-driven: When `ClaudeAgentRuntime.submit` starts a harness run, then it shall invoke `claude_agent_sdk.query()` with `ClaudeAgentOptions` setting `model="global.anthropic.claude-opus-4-8"`, `cwd=<worktree>`, `permission_mode="acceptEdits"`, `allowed_tools=["Read","Edit","Write","Bash","Glob","Grep"]`, `disallowed_tools=["WebSearch","WebFetch"]`, `setting_sources=[]`, an `output_format` of type `json_schema`, both `max_turns` and `max_budget_usd`, and `env={"CLAUDE_CODE_USE_BEDROCK":"1","AWS_REGION":<region>}` (research-coding-runtimes.yaml `claude_agent_sdk_bedrock`).

AC-3-2
Dependencies: AC-3-1
State-driven: While a `ClaudeAgentRuntime` harness is running, the adapter shall supply a `can_use_tool` callback (over a streaming prompt) that returns a deny decision for any `Edit`/`Write`/`Bash` tool call whose target path resolves outside the worktree subtree (path confinement; plan §2.7, §6; research-coding-runtimes.yaml `claude_agent_sdk_bedrock` "Path confinement").

AC-3-3
Dependencies: AC-2-3, AC-3-1
Event-driven: When a `ClaudeAgentRuntime` run reaches its final `ResultMessage`, then `collect` shall build the `SpecialistResult` from `ResultMessage.structured_output` (parsed with `model_validate`, not `model_validate_json`), `total_cost_usd`, and `usage`, and shall not surface the message transcript (research-coding-runtimes.yaml `claude_agent_sdk_bedrock`; ASTR §10.4).

AC-3-4
Dependencies: AC-3-3
Event-driven: When the final `ResultMessage.subtype` is `"success"`, then `collect` shall set `disposition="completed"`; and when the subtype is `"error_max_turns"` or `"error_max_budget_usd"`, then `collect` shall set `disposition` to `"partial"` or `"failed"` rather than `"completed"`.

AC-3-5
Dependencies: AC-3-2
Unwanted behavior: If the harness attempts an `Edit` or `Write` to a path outside the worktree root, then the `can_use_tool` callback shall return a permission-deny result and the write shall not occur.

AC-3-6 [P]
Unwanted behavior: If `ClaudeAgentRuntime` is executed inside an already-running asyncio event loop, then the adapter shall drive `query()` with `await` (async) and shall never call `asyncio.run()` internally (research-coding-runtimes.yaml `strands_tool_pattern`, risk table "Claude SDK `asyncio.run` inside a running loop").

---

## User Story 4 — CodexRuntime adapter (GPT-5.6 Sol on Bedrock)

### Acceptance Criteria

AC-4-1
Dependencies: AC-2-1
Event-driven: When `CodexRuntime.submit` launches a harness run, then it shall start `codex exec` via `asyncio.create_subprocess_exec` with `--model openai.gpt-5.6-sol`, `--model-provider amazon-bedrock`, `-c model_reasoning_effort=high`, `--cd <worktree>`, `--full-auto`, `--json`, `--output-schema <schema>`, and `-o result.json`, and shall set a per-run `CODEX_HOME` and `AWS_BEARER_TOKEN_BEDROCK` in the subprocess environment (research-coding-runtimes.yaml `codex_gpt56_bedrock`).

AC-4-2
Dependencies: AC-2-3, AC-4-1
Event-driven: When a `CodexRuntime` run finishes, then `collect` shall build the `SpecialistResult` from the `-o` final result file and the `turn.completed` event, and shall not treat the first schema-valid JSONL line as the result (avoids gotcha #19816; research-coding-runtimes.yaml `codex_gpt56_bedrock`).

AC-4-3
Dependencies: AC-4-2
Event-driven: When the `turn.completed` event carries a `usage` object, then `collect` shall map its token counts into `SpecialistResult.resource_usage`.

AC-4-4 [P]
State-driven: While two `CodexRuntime` harness runs execute concurrently, each run shall be launched with a distinct `CODEX_HOME` directory so the runs do not share `~/.codex` session state and cross-contaminate (gotcha #11435; research-coding-runtimes.yaml `codex_gpt56_bedrock`).

AC-4-5
Dependencies: AC-4-2
Unwanted behavior: If the Codex JSONL stream emits a `turn.failed` or `error` event, then `collect` shall set `SpecialistResult.disposition` to `"failed"` rather than `"completed"`.

---

## User Story 5 — WorkspaceFactory (git-worktree isolation)

### Acceptance Criteria

AC-5-1
Dependencies: AC-2-1
Event-driven: When `WorkspaceFactory.create` is called for a run, then it shall add a fresh `git worktree` and return its path for use as the harness `cwd` (plan §2.7, §5 "Worktree lifecycle").

AC-5-2
Dependencies: AC-5-1
State-driven: While multiple harness runs are created concurrently, the factory shall assign each run a distinct worktree path so parallel coding runs do not collide (plan §5).

AC-5-3
Dependencies: AC-5-1
Event-driven: When a harness run completes and its worktree contains no changes relative to its base, then the factory shall auto-remove that worktree (plan §5 "auto-remove if unchanged").

AC-5-4
Dependencies: AC-5-3
Unwanted behavior: If a completed run's worktree contains uncommitted changes, then the factory shall not auto-remove it, so produced artifacts survive for collection.

---

## User Story 6 — invoke_coding_agent tool + make_invoke_coding_agent factory

### Acceptance Criteria

AC-6-1
Dependencies: AC-2-1
Ubiquitous: The `strands_teams_runtimes` package shall expose a `make_invoke_coding_agent(runtime_registry)` factory that returns the `invoke_coding_agent` tool with its runtimes injected, so no coding-agent SDK is imported to construct the tool (dependency injection; plan §3, §5).

AC-6-2
Dependencies: AC-6-1
Ubiquitous: The `invoke_coding_agent` tool returned by the factory shall be an `async` `@tool(context=True)` in async-generator form (research-coding-runtimes.yaml `strands_tool_pattern`, decorator.py ~622-639).

AC-6-3
Dependencies: AC-6-2
State-driven: While `invoke_coding_agent` is executing, it shall read `workspace` (a `Path`), `model_id`, `capabilities`, and `runtime` from `tool_context.invocation_state`, following the accessor pattern used by the existing coordinator tools (`packages/strands-teams/src/strands_teams/teams.py:41`, `:77`).

AC-6-4
Dependencies: AC-6-2
Event-driven: When `invoke_coding_agent` yields before the run is finished, then that non-final `yield` shall carry a heartbeat value; and when the run finishes, the final `yield` shall be the `SpecialistResult` serialized as a JSON string via `model_dump_json()` (research-coding-runtimes.yaml `strands_tool_pattern` — tools return strings; last yield is the tool result).

AC-6-5
Dependencies: AC-6-3
Unwanted behavior: If `invocation_state` is missing a required key (`workspace`, `runtime`, or a `runtime` value absent from the registry), then `invoke_coding_agent` shall yield a final JSON string with `status: "error"` and a descriptive message rather than raising an unhandled exception.

---

## User Story 7 — Concurrency cap, subprocess timeout, and swarm-timeout coordination

### Acceptance Criteria

AC-7-1
Dependencies: AC-6-2
State-driven: While `invoke_coding_agent` launches a harness subprocess, it shall acquire a slot from a module-level `asyncio.Semaphore` sized from the `STRANDS_TEAMS_MAX_HARNESSES` setting so concurrent harness launches never exceed that cap (research-coding-runtimes.yaml `strands_tool_pattern` concurrency).

AC-7-2
Dependencies: AC-6-2
Ubiquitous: The `invoke_coding_agent` tool shall bound each harness run with its own `asyncio.wait_for(..., timeout=_SUBPROCESS_TIMEOUT_S)`, because the Strands concurrent executor imposes no per-tool timeout (research-coding-runtimes.yaml `strands_tool_pattern` — executor imposes NO per-tool timeout).

AC-7-3
Dependencies: AC-7-2
Unwanted behavior: If a harness subprocess exceeds `_SUBPROCESS_TIMEOUT_S`, then `invoke_coding_agent` shall cancel the subprocess and yield a final `SpecialistResult` with `disposition="failed"` rather than hanging or raising an unhandled `TimeoutError`.

AC-7-4 [P]
Ubiquitous: The `invoke_coding_agent` docstring and the phase implementation notes shall state that the swarm `node_timeout` and `execution_timeout` (defaults surfaced at `packages/strands-teams/src/strands_teams/settings.py:82-94` and applied at `teams.py:285-286`) must be configured above `_SUBPROCESS_TIMEOUT_S`, or the swarm node is cancelled before the harness finishes (research-coding-runtimes.yaml `strands_tool_pattern`; plan §5, risk table).

---

## User Story 8 — Mocked test suite (no real Bedrock or network)

### Acceptance Criteria

AC-8-1 [P]
Ubiquitous: The `strands_teams_runtimes` test suite shall mock the Claude Agent SDK and the Codex subprocess so that no test issues a real Bedrock inference call or any network request.

AC-8-2
Dependencies: AC-5-2, AC-8-1
Event-driven: When two harness runs are created in one test, then the test shall assert each received a distinct worktree path (worktree isolation).

AC-8-3
Dependencies: AC-7-1, AC-8-1
State-driven: While more harness launches are requested than `STRANDS_TEAMS_MAX_HARNESSES` permits, the test shall assert the number of concurrently running harness subprocesses never exceeds the semaphore cap.

AC-8-4
Dependencies: AC-7-3, AC-8-1
Event-driven: When a mocked harness is made to exceed `_SUBPROCESS_TIMEOUT_S`, then the test shall assert `invoke_coding_agent` produces a final `SpecialistResult` with `disposition="failed"` (timeout path).

AC-8-5
Dependencies: AC-3-3, AC-4-2, AC-8-1
Event-driven: When a mocked Claude `ResultMessage.structured_output` and a mocked Codex `-o` result file are supplied, then the tests shall assert each runtime's `collect` parses them into a valid `SpecialistResult` with the expected `disposition`, `artifacts`, `findings`, and `resource_usage`.

AC-8-6
Dependencies: AC-6-4, AC-8-1
Event-driven: When a mocked harness is made to run for multiple steps before finishing, then the test shall assert `invoke_coding_agent` produces at least one non-final heartbeat `yield` before the final `SpecialistResult` yield.

AC-8-7
Dependencies: AC-3-5, AC-8-1
Event-driven: When a mocked harness requests a write to a path outside the worktree, then the test shall assert the `can_use_tool` path-confinement callback returns a permission-deny result.

AC-8-8
Dependencies: AC-2-2, AC-8-5
Unwanted behavior: If a runtime's `collect` were to place the raw harness transcript into the returned `SpecialistResult`, then a test shall fail — the result shall carry only structured summary, findings, evidence refs, and resource usage (ASTR §10.4 boundary discipline).
