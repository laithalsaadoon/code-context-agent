---
title: A long multi-phase Workflow can stall on its hardest phase — take it over manually
track: knowledge
category: best-practices
module: (orchestration)
component: Workflow tool + ERPAVal sequential build loop
severity: warning
tags: [workflow, ultracode, erpaval, monitoring, stall, subagent, task-decomposition]
applies_when:
  - Running a long sequential multi-phase Workflow (implement→verify→review per phase)
  - A phase's implementer agent decomposes into sub-tasks and stops making disk progress
  - "It failed N times" — many in-flight agents with no recorded result
pattern: |
  A Workflow phase whose implementer is a single high-effort agent can wedge on the
  most complex phase: it decomposes the phase into its own sub-tasks (visible as a
  growing TaskCreate list), completes the easy ones, then its agents die on terminal
  errors before writing the hard files — each restart is a fresh in-flight agent with
  no journal "result" entry. The signal is NOT test failures: read the workflow
  journal.jsonl and count verify results with passed=false (here only 1 of 9 failed,
  and it self-recovered) vs. count started-without-result agentIds (here 9 and growing).

  Diagnosis commands (workflow transcript dir = the runId under
  .../subagents/workflows/wf_*/):
  - `wc -l journal.jsonl`; grep '"tests_summary"' to see per-phase progress (each green
    verify prints its pass count).
  - Parse started vs result agentIds: many started, few results = agents dying.
  - Check newest agent transcript mtime/size: if size grows but the phase packet
    doesn't advance, it's a heavy read/reason loop; if BOTH are frozen minutes-old,
    it's wedged. (ls -t sorts by the wrong time field on macOS/eza — use `stat -f %Sm`.)

  Recovery: TaskStop the workflow (by its task id), then finish the stuck phase BY HAND.
  Earlier phases are already on disk and green — assess with the real gate (uv run
  ruff/ty/pytest), read the phase's EARS spec + the partial files the workflow wrote
  (models/layout often land; the engine/hook/tests are what's missing), and complete
  it directly. Manual completion of one phase is far cheaper than fighting a thrashing
  agent loop. The IDE "new-diagnostics" import errors during the run are editable-
  workspace-not-on-IDE-path false positives — trust `uv run` (imports + ty), not the
  IDE squiggles, and NEVER read the agent .jsonl transcripts (they overflow context).
example_files:
  - .erpaval/specs/007-authority-gates/spec.md
  - packages/strands-teams/src/strands_teams/policy.py
---

# Why this matters

The user's "something failed / it failed 4 times" was a wedged implementer, not a
broken build — the build was green through P5. Watching the workflow longer would
have burned tokens on dead agents. Reading the journal to separate real verify
failures (1) from dead in-flight agents (9) is what told me to stop and take over.

# Example

```bash
WF=.../subagents/workflows/wf_<runid>
# real failures vs dead agents:
python3 -c "import json;s={};d=set()
for l in open('$WF/journal.jsonl'):
 e=json.loads(l);_=s.setdefault(e['agentId'],1) if e['type']=='started' else d.add(e.get('agentId'))
print('in-flight (no result):',[a for a in s if a not in d])"
grep '\"tests_summary\"' $WF/journal.jsonl   # per-phase green counts
```
