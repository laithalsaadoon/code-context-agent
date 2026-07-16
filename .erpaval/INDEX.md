# ERPAVal lessons index

Lessons learned from prior ERPAVal sessions. Claude reads this at
session start and greps `.erpaval/solutions/**` for relevant
lessons before starting work.

## By category

### api-patterns
- [Use native strands.multiagent.Swarm, not strands_tools.swarm](solutions/api-patterns/strands-native-swarm-over-tools-wrapper.md) — the wrapper is sync-only, lossy, and silently runs sub-agents on the default model; go native with async invoke_async + invocation_state.

### best-practices
- [Concurrent Act waves need cross-module contract tests at their seams](solutions/best-practices/concurrent-wave-seam-contract-tests.md) — parallel agents each test against their own fake; drive the real object through the real hook, and grep string tool-name renames across both packages.

### architecture-patterns
- [Generalizing a domain agent into framework + UseCase](solutions/architecture-patterns/usecase-contract-for-domain-agnostic-teams.md) — the UseCase parameter checklist, docstrings-are-prompts trap, single layout/rubric ownership, and killing safe-0 fallbacks.

## Recent additions

- 2026-07-16 (session-bea07b): the three lessons above, from the strands-teams workspace split.
