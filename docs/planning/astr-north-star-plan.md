# strands-teams → ASTR North Star: End-to-End Plan

Status: proposal · Date: 2026-07-16 · Owner: Laith Al-Saadoon

This plan takes `strands-teams` (today: a single-goal hierarchical fan-out that
produces analysis bundles) to the **Agentic SDLC Team Runtime (ASTR)** north star
in the working paper: a task-centric, hierarchical-blackboard runtime that
coordinates autonomous specialist harnesses through typed contracts, versioned
artifacts, evidence, authority, and gates — *coordinating commitments and state
transitions, not personalities or transcripts.*

It includes the headline capability the request calls for: **a Strands tool that
delegates a bounded coding task to an autonomous external harness** — Claude Agent
SDK (Opus 4.8) or Codex (GPT-5.6 Sol) — **both served on Amazon Bedrock.** That
tool is the concrete instance of ASTR's `SpecialistRuntime` boundary (§10).

> Grounding: research digests for this plan live in the session record
> (`.erpaval/sessions/*/research-*`). The Strands 1.47, Claude Agent SDK, and
> Codex/Bedrock facts below were verified against installed source, official docs,
> and a live Bedrock inference-profile listing.

---

## 1. Where we are vs. the north star

| ASTR concept (paper §) | strands-teams today | Gap |
|---|---|---|
| Roles organize capability/authority; tasks drive behavior (§4.1) | `UseCase` + LLM-authored team specs `{name, system_prompt, tools}` | No typed authority/budget/acceptance on the contract |
| Inner specialist loop = autonomous harness (§2.1, §10) | Swarm nodes = in-process strands `Agent`s on one Bedrock model | **No black-box `SpecialistRuntime` boundary** |
| Task contract (§4.1, §6.2) | `dispatch_team(mandate, file_scope, key_questions, …)` | Missing acceptance_criteria, authority, budget, dependencies, input revisions |
| Artifact = immutable revision (§4.8, §6.4) | `findings.md` / `BUNDLE.*.md` via `TeamsLayout` (mutable files) | No revisions, content hash, lineage |
| Finding = typed object w/ lifecycle (§6.6, §8.3) | free-text prose inside `findings.md` | Not typed; no severity/blocking/disposition |
| Evidence-bearing communication (§4.5) | `NarrativeRubric` scores citation density | Not bound to a finding/transition |
| Protocol event plane (§6.5, §7) | hook callbacks + `team_debug.jsonl` | Not typed/causal/replayable |
| Lead controller loop (§9.2) | one coordinator `invoke_async` turn | No durable outer state machine / replanning |
| Decision / ApprovalToken / policy gate (§6.7–6.8, §14) | — | Absent |
| Governed memory promotion (§13) | `.erpaval/solutions/` (one level up, manual) | Not in-runtime |
| Collaboration plane / team room (§5.2, §12.2) | — | Absent (no human-in-the-loop room) |

**Thesis alignment is already high** — the task-first, artifact-over-transcript,
evidence-scored posture is what we built. The gap is *depth of typing, an autonomous
runtime boundary, and a durable governed outer loop.* This plan closes it in phases
that each ship standalone value and never break the existing `analyze` use-case.

---

## 2. Design decisions (settled going in)

1. **Additive, not a rewrite.** Every phase extends the `UseCase` / `dispatch_team`
   / findings contracts already in `packages/strands-teams`. The codebase-analysis
   use-case keeps working at each phase boundary (regression gate).
2. **The coding harness is a `@tool`, not an A2A node.** Research verdict: for a
   local subprocess harness, wrap it as an `async @tool` (submit → await subprocess
   → collect structured result). `A2AAgent` is only for harnesses already deployed
   as HTTP A2A servers; there is no generic subprocess `MultiAgentBase` adapter in
   1.47, and the `@tool` boundary is exactly what keeps the raw transcript out of
   the swarm's context (ASTR §10.4).
3. **Two runtimes, one interface, both on Bedrock.** A `SpecialistRuntime` Protocol
   with two adapters: `ClaudeAgentRuntime` (Opus 4.8) and `CodexRuntime` (GPT-5.6
   Sol). Runtime chosen per task, defaulting from the `UseCase`.
4. **Model IDs (confirmed real):**
   - Claude: `global.anthropic.claude-opus-4-8` via `CLAUDE_CODE_USE_BEDROCK=1`.
   - Codex: `openai.gpt-5.6-sol` via Codex's **built-in `amazon-bedrock` provider**
     (`wire_api="responses"`, `bedrock-mantle` endpoint, `AWS_BEARER_TOKEN_BEDROCK`
     auth). No LiteLLM — Bedrock serves a native OpenAI Responses shim. In-Region
     only (us-east-1 / us-east-2).
5. **State via `invocation_state`, not globals.** Workspace `Path`, capability
   grants, model id, and task-contract fields flow through `@tool(context=True)` —
   already the framework's pattern; passes live Python objects, no serialization.
6. **Durable outer loop is Phase 5+, opt-in.** The one-shot coordinator stays the
   default; the Temporal/Restate lead controller is a separate runner for
   long-horizon goals, not a replacement.
7. **Isolation is mandatory for autonomous coding.** Each harness run gets its own
   git worktree as `cwd` and (Codex) its own `CODEX_HOME`; capability grants deny
   production paths; a `PreToolUse`/`can_use_tool` gate confines writes to the
   worktree subtree.

---

## 3. New package boundary

Coding harnesses do NOT belong in the domain-free `strands-teams` core (they'd drag
in `claude-agent-sdk`, the `codex` CLI, and AWS specifics). Introduce a third
workspace package:

```
packages/
  strands-teams/            # unchanged: domain-agnostic core
  strands-teams-runtimes/   # NEW: SpecialistRuntime adapters (Claude/Codex on Bedrock)
  code-context-agent/       # the analysis use-case
```

`strands-teams` core gains only the **typed contracts** (Finding, ArtifactRevision,
TaskContract fields, SpecialistRuntime Protocol, the coding-tool factory that takes
a runtime by dependency injection). The adapters + their SDK deps live in
`strands-teams-runtimes`. A use-case that wants coding teams depends on both.

---

## 4. Phased roadmap

Each phase = one ERPAVal change (its own EARS spec + waves). Phases are ordered so
value ships early and the autonomous-coding capability (the request's headline)
lands by Phase 3.

### Phase 1 — Typed findings + evidence (foundation)
**Maps to:** ASTR §6.6, §8.3, §4.5. **Ships:** structured, queryable findings.

- Add `Finding` (FrozenModel: `severity`, `blocking`, `disposition`, `statement`,
  `affected_artifacts`, `evidence[]`, `confidence`, lifecycle `open→…→closed`) to
  `strands_teams.models`.
- Team members write `findings.json` (typed) alongside `findings.md` (human) — extend
  `TeamsLayout` with `findings_json_path(team_id)`; `write_artifact`/`make_write_file`
  gain a typed-finding path.
- `read_team_findings` returns parsed `Finding[]` per team instead of raw markdown blob.
- Bind the existing `NarrativeRubric` citation-density score as the **evidence gate**:
  a `blocking=true` finding must carry ≥1 evidence ref (file:line / test / cmd exit).
- **Regression gate:** codebase-analysis still emits the same CONTEXT.md/BUNDLE.* set;
  findings are now also structured.
- Effort: S. Risk: low. No new deps.

### Phase 2 — Task contract enrichment + authority
**Maps to:** ASTR §4.1, §6.2, §14.7. **Ships:** contracts that carry success + limits.

- Extend the `dispatch_team` agent-spec / mandate with typed fields:
  `acceptance_criteria[]`, `authority` (`may_create_blocking_finding`, `may_write`,
  `may_approve`), `budget` (max_wall_clock, max_cost, max_retries), `input_revisions[]`.
- Add an `AuthorityGrant` model; `dispatch_team` refuses to persist a blocking finding
  from a team whose grant forbids it (structural enforcement, ASTR §4.7 — not prompt).
- Acceptance criteria feed the quality ratchet: `ArtifactQualityHook` checks criteria
  coverage, not just rubric score.
- Effort: M. Risk: low. Pure extension of the dispatch contract.

### Phase 3 — SpecialistRuntime coding tool (THE headline)
**Maps to:** ASTR §10, §2.1, §15.9. **Ships:** autonomous coding delegation on Bedrock.

New package `strands-teams-runtimes`:

- `SpecialistRuntime` Protocol: `submit / status / send_input / cancel / collect`,
  `collect() -> SpecialistResult` (FrozenModel: `disposition`, `artifacts[]`,
  `findings[]`, `evidence[]`, `residual_risk`, `resource_usage`) — **never the raw
  transcript** (ASTR §10.4).
- `ClaudeAgentRuntime` — wraps `claude_agent_sdk.query()`:
  `ClaudeAgentOptions(model="global.anthropic.claude-opus-4-8", cwd=worktree,
  permission_mode="acceptEdits", allowed_tools=[Read,Edit,Write,Bash,Glob,Grep],
  disallowed_tools=[WebSearch,WebFetch], max_turns, max_budget_usd,
  output_format=json_schema, setting_sources=[], env={CLAUDE_CODE_USE_BEDROCK:1,
  AWS_REGION})`; result from `ResultMessage.structured_output` + `total_cost_usd` +
  usage; `can_use_tool` callback confines writes to the worktree.
- `CodexRuntime` — `codex exec --model openai.gpt-5.6-sol --model-provider
  amazon-bedrock --cd worktree --full-auto --json --output-schema … -o result.json`
  with per-run `CODEX_HOME` (concurrency isolation, openai/codex#11435) and
  `AWS_BEARER_TOKEN_BEDROCK`; parse the `turn.completed` event + `-o` final file
  (avoid #19816). Optional Variant B: `openai-codex` Python SDK `AsyncCodex` for a
  typed `TurnResult`.
- The Strands tool `invoke_coding_agent(task, tool_context) -> SpecialistResult(json)`:
  `async @tool(context=True)`, async-generator form so non-final `yield`s become
  heartbeats on the swarm `multiagent_node_stream` feed; reads `workspace`, `model_id`,
  `capabilities`, `runtime` from `invocation_state`; owns its own
  `asyncio.wait_for(subprocess)` timeout (executor imposes none); guards concurrent
  launches with a module-level `asyncio.Semaphore(STRANDS_TEAMS_MAX_HARNESSES)`.
- **Swarm timeouts must exceed the harness timeout** — set `node_timeout`/
  `execution_timeout` above `_SUBPROCESS_TIMEOUT_S` or the node is cancelled first.
- Worktree lifecycle: a small `WorkspaceFactory` (`git worktree add` per run,
  auto-remove if unchanged) so parallel coding runs don't collide.
- **A coding use-case** (`packages/`, e.g. `feature-delivery-agent` or extend the
  analysis use-case) that grants the coding tool to an "implementer" team member.
- Effort: L. Risk: medium (subprocess lifecycle, isolation, cost). New deps live only
  in `strands-teams-runtimes`.

### Phase 4 — Artifact revisions + stale-review invalidation
**Maps to:** ASTR §4.8, §6.4, §12.6. **Ships:** iterative runs that don't reuse stale work.

- `ArtifactRevision` (content hash, `supersedes[]`, `produced_by`, immutable) +
  mutable alias pointer in `TeamsLayout`.
- Reviews/findings bind an artifact revision; a material change (`semantic_diff` of
  touched facets) invalidates only the reviews whose facets changed.
- Enables re-analysis / re-implementation after code changes without redoing valid work.
- Effort: M. Risk: medium. Worth it once runs become iterative (Phase 3 coding loops).

### Phase 5 — Durable lead controller + typed event plane
**Maps to:** ASTR §9.2, §6.5, §7, §15.2. **Ships:** long-horizon, replayable goals.

- A `TeamEvent` typed/append-only event log (extend `team_debug.jsonl` into a real
  event plane: causal parents, correlation id, epistemic status) — replayable, no CoT.
- A durable outer loop (Temporal or Restate worker) that runs the ASTR lead loop:
  decompose → compose → assign → dispatch → integrate → replan, surviving waits on
  humans/CI/coding-harness completion. This is a *new runner* alongside
  `run_teams_sync`, selected for multi-goal / long-running work.
- Replanning triggers (`requirement.changed`, `critical_finding.created`,
  `task.failed`, `budget.threshold_exceeded`).
- Effort: XL. Risk: high. Only for the "software org runtime" ambition; gated on real demand.

### Phase 6 — Authority gates, approvals, policy engine + collaboration plane
**Maps to:** ASTR §14, §11, §5.2. **Ships:** governed consequential actions + human gates.

- `ApprovalToken` (revision-bound, signed), `Decision` records, a `policy` engine that
  makes the final allow/deny on transitions (not the model), human gates for
  destructive/regulated actions.
- Optional Yjs collaboration plane / team-room projection for human-in-the-loop.
- Only meaningful once agents take consequential actions (deploy/merge) — deferred
  until Phase 3 coding output feeds a real release path.
- Effort: XL. Risk: high.

**Sequencing rationale:** 1→2→3 are the value spine (typed findings → contracts →
autonomous coding), each additive and low/medium risk, delivering the requested
capability by Phase 3. 4 supports iteration. 5–6 are the durable-org leap, gated on
demonstrated need — consistent with the paper's own MVP-first roadmap (§22–23).

---

## 5. The coding-tool architecture (Phase 3 detail)

```
swarm node "implementer" (strands Agent, Opus coordinator model)
  │  emits tool_use: invoke_coding_agent(task=…)
  ▼
invoke_coding_agent  (async @tool(context=True), strands-teams-runtimes)
  │  reads invocation_state: {workspace: Path, model_id, capabilities, runtime}
  │  acquires asyncio.Semaphore slot (cap parallel harnesses)
  │  WorkspaceFactory.create() → git worktree
  ▼
SpecialistRuntime adapter  (submit → await → collect)
  ├── ClaudeAgentRuntime → claude_agent_sdk.query()
  │     CLAUDE_CODE_USE_BEDROCK=1, model=global.anthropic.claude-opus-4-8
  └── CodexRuntime → codex exec --model-provider amazon-bedrock
        model=openai.gpt-5.6-sol, CODEX_HOME per-run, AWS_BEARER_TOKEN_BEDROCK
  ▼
SpecialistResult (typed: artifacts, findings, evidence, residual_risk, usage)
  │  returned as JSON string (strands norm); raw transcript stays inside the harness
  ▼
swarm node parses result → writes typed Finding[] + artifact → coordinator synthesizes
```

Boundary discipline (ASTR §10.4): the tool returns only the structured summary +
evidence refs + resource usage. Private CoT, every terminal command, and the full
model transcript do **not** cross back into the swarm's context — this is both an
ASTR invariant and a context-budget necessity.

---

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Autonomous coding harness escapes workspace | git-worktree `cwd` + `can_use_tool`/`PreToolUse` path confinement + capability grants deny prod; Codex `--sandbox workspace-write` |
| Cost blowout (nested autonomous agents) | `max_budget_usd` (Claude) + budget field on task contract; `asyncio.Semaphore` caps parallel harnesses; per-run cost in `resource_usage` |
| Swarm cancels node before harness finishes | Set swarm `node_timeout`/`execution_timeout` above the tool's subprocess timeout |
| Concurrent Codex runs cross-contaminate | Per-run `CODEX_HOME` + per-run worktree + `--ephemeral` (openai/codex#11435) |
| GPT-5.6 model-id drift in a Codex build | Verify `codex models` lists `openai.gpt-5.6-sol` before shipping; In-Region only |
| `--output-schema` picks wrong (intermediate) message | Consume `-o` final-message file / `turn.completed` item, not first schema-valid line (#19816) |
| Core package pulls in SDK/AWS deps | Adapters isolated in `strands-teams-runtimes`; core stays domain- and vendor-free |
| Claude SDK `asyncio.run` inside a running loop | Expose the tool async; never `asyncio.run` inside an already-async strands runtime |

---

## 7. Immediate next step

Phase 1 (typed findings + evidence) is the highest-leverage, lowest-risk start and
unblocks everything downstream. Recommend running it as a full ERPAVal change next:
EARS spec → waves → validate → compound. Phase 3 (the coding tool) is the headline
and can begin in parallel design once Phase 1's `Finding`/evidence types exist, since
`SpecialistResult.findings` reuses them.

Non-goals for now (YAGNI until the org-runtime ambition is committed): CRDT
collaboration, marketplace agent contracting, autonomous memory-policy modification,
unrestricted production deployment (ASTR §22.2).
