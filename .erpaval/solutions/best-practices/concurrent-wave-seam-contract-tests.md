---
title: Concurrent Act waves need cross-module contract tests at their seams
track: knowledge
category: best-practices
module: packages/strands-teams
component: erpaval parallel Act waves, duck-typed Protocols
severity: warning
tags: [erpaval, parallel-agents, protocol, seam-bug, testing, review]
applies_when:
  - Splitting one Act wave into multiple parallel agents that share an interface
  - Using a runtime_checkable Protocol to decouple concurrently-built modules
  - A hook/consumer in module A is wired to a concrete class from module B
pattern: |
  When two concurrent agents author opposite sides of an interface (a hook that
  CALLS state.record_tool vs the AgentDisplayState that must DEFINE it), each
  invents its own view of the contract and nothing forces them to match. A
  @runtime_checkable Protocol gives false safety: it only checks method presence
  at an explicit isinstance() call, and nobody actually calls isinstance before
  wiring. Result: a method the hook needs is simply absent on the real class,
  and it crashes on first use in production mode — while unit tests pass because
  each side tests against its own hand-written fake.

  Two defenses:
  1. Freeze the shared contract in a Wave-B "contracts" task FIRST (layout,
     rubric, UseCase, Protocol) and have every downstream agent code against the
     actual source, not the packet paraphrase. This worked here for
     layout/rubric/UseCase.
  2. For any hook↔state / producer↔consumer seam, add a test that drives the
     REAL concrete object through the REAL hook — not a fake that happens to
     implement the method. The fake is what hides the bug.

  Also: when a wave renames a tool/function (write_bundle -> write_artifact),
  grep every STRING occurrence across BOTH packages (prompt templates, phase
  maps, reasoning-prompt dicts, resume templates, and tests that pin the old
  name) — the rename tooling only catches symbol references, not string data,
  and a test can actively certify the stale name as correct.
example_files:
  - packages/strands-teams/tests/test_hooks.py
  - packages/strands-teams/src/strands_teams/display/state.py
  - packages/strands-teams/src/strands_teams/hooks.py
---

# Why this matters

The independent pre-commit review caught two crash-on-every-run seam bugs
(missing AgentDisplayState.record_tool; bind_live_renderable never called in the
new runner) and a stale-tool-name drift that silently defeated the enrichment
quality hook — none caught by 261 passing tests, all born from concurrent waves
each testing against its own fake. The mandatory fresh-reviewer gate is what
surfaced them; a cheaper standing defense is a seam contract test per interface.

# Example

```python
def test_drives_real_agent_display_state():
    # NOT a fake — the concrete class the runner actually builds
    state = AgentDisplayState(phase_spec=spec)
    hook = ToolDisplayHook(state)
    hook._on_tool_start(_FakeBeforeToolEvent("rg_search", {}))
    hook._on_tool_end(_FakeAfterToolEvent("rg_search", '{"status":"error"}'))
    assert state.tool_errors == 1   # would AttributeError if record_tool missing
```
