# ERPAVal lessons index

Lessons learned from prior ERPAVal sessions. Claude reads this at
session start and greps `.erpaval/solutions/**` for relevant
lessons before starting work.

## By category

### api-patterns
- [Use native strands.multiagent.Swarm, not strands_tools.swarm](solutions/api-patterns/strands-native-swarm-over-tools-wrapper.md) — the wrapper is sync-only, lossy, and silently runs sub-agents on the default model; go native with async invoke_async + invocation_state.
- [Wrapping Claude Agent SDK / Codex as a Strands coding tool on Bedrock](solutions/api-patterns/coding-agent-as-tool-on-bedrock.md) — Opus 4.8 (global.anthropic.claude-opus-4-8) + GPT-5.6 Sol (codex built-in amazon-bedrock provider, no LiteLLM); async @tool generator, self-owned timeout, per-run CODEX_HOME, no transcript.

### best-practices
- [Concurrent Act waves need cross-module contract tests at their seams](solutions/best-practices/concurrent-wave-seam-contract-tests.md) — parallel agents each test against their own fake; drive the real object through the real hook, and test the crash/except path for any "on every path" enforcement.
- [A long multi-phase Workflow can stall on its hardest phase — take it over manually](solutions/best-practices/workflow-phase-stall-manual-takeover.md) — read journal.jsonl to tell real verify failures from dead in-flight agents; TaskStop and finish the stuck phase by hand.

### architecture-patterns
- [Generalizing a domain agent into framework + UseCase](solutions/architecture-patterns/usecase-contract-for-domain-agnostic-teams.md) — the UseCase parameter checklist, docstrings-are-prompts trap, single layout/rubric ownership, and killing safe-0 fallbacks.

## Recent additions

- 2026-07-16 (session-bea07b): strands-teams workspace split (native Swarm; seam tests; UseCase contract).
- 2026-07-16 (session-8c56b2): ASTR six-phase build — coding-agent-on-Bedrock integration + workflow-stall manual takeover; seam-test lesson reconfirmed.
