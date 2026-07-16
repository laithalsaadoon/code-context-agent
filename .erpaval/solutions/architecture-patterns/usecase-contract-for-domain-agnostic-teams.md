---
title: Generalizing a domain agent into framework + UseCase — what to parameterize
track: knowledge
category: architecture-patterns
module: packages/strands-teams/src/strands_teams/usecase.py
component: strands-teams framework core
severity: info
tags: [generalization, usecase, plugin, docstring-as-prompt, uv-workspace]
applies_when:
  - Extracting a reusable agent framework from a single-domain agent
  - Deciding what belongs in a generic core vs a domain use-case
  - Prompts, tool docstrings, or output models carry domain content
pattern: |
  code-context-agent was split into a domain-agnostic strands_teams core + a
  code_context_agent UseCase, as a uv workspace (packages/*, core declares NO
  dependency on the use-case — the dependency direction is the proof of
  domain-freedom, enforced by an import check).

  The UseCase contract is the seam. A domain supplies, and the core consumes:
  domain tools list, coordinator prompt template + planning-context loader,
  structured-output model, findings-format directive, artifact naming scheme +
  output-structure spec, scoring rubric, tool->phase map + phase names,
  tool->reasoning-prompt map, fail-fast exempt set, kickoff-prompt builder,
  settings (env prefix + output dir). Make it an ABC with a validate_use_case()
  that raises naming the MISSING member — silent defaults hide integration gaps.

  Non-obvious gotchas that fought the extraction:
  - "Docstrings ARE prompts": strands sends @tool docstrings to the model, so
    per-domain guidance CANNOT live in a generic tool's docstring. Move domain
    guidance into the coordinator SYSTEM PROMPT (use-case template + a
    framework-owned teams_protocol partial); keep generic tool docstrings lean.
  - Single layout descriptor owns ALL artifact paths (findings.md, metadata.json,
    BUNDLE.{area}.md, sentinel) — they were duplicated as literals across 5+
    modules. Grep guard: no path literal outside layout.py.
  - One rubric object shared by the score tool AND the quality hook — the two had
    drifted into different formulas for the same 3.5 threshold stated in 3 places.
  - Kill duck-typed safe-fallbacks that mask schema drift (the old _DictProxy
    returned 0 for any missing key under StrictUndefined). Replace with an
    explicit default skeleton deep-merged under the loaded planning context.
  - MCPClient (ToolProvider) instances do NOT expose per-tool names to a
    name->tool registry until the Agent's own registry expands them; a generic
    registry keyed on tool_name silently drops team-requested MCP tools. (Tracked
    follow-up F1.)
example_files:
  - packages/strands-teams/src/strands_teams/usecase.py
  - packages/code-context-agent/src/code_context_agent/usecase.py
  - packages/strands-teams/src/strands_teams/layout.py
---

# Why this matters

The parameter list above is the reusable checklist for "what varies by domain"
when generalizing any model-driven agent. The docstring-as-prompt constraint in
particular is a strands-specific trap: the obvious "just template the docstring"
move is impossible because docstrings are static and shared.

# Example

```python
class UseCase(ABC):
    name: str; settings: TeamsSettings; layout: TeamsLayout
    rubric: NarrativeRubric; phases: PhaseSpec
    structured_output_model: type[BaseModel]
    findings_format_directive: str
    reasoning_prompts: Mapping[str, str] = {}     # optional; defaulted
    fail_fast_exempt: frozenset[str] = frozenset()
    @abstractmethod
    def domain_tools(self) -> list: ...
    @abstractmethod
    def planning_context(self) -> dict: ...
    @abstractmethod
    def kickoff_prompt(self, *, bundles_only, focus, extra_context) -> str: ...
# validate_use_case(uc) raises MissingUseCaseMemberError naming the gap
```
