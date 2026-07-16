"""Tests for the codebase-analysis UseCase implementation (T-C4).

Covers the AC-6-1 / AC-6-2 / AC-6-6 / AC-8-6 / AC-9-7 surface:

* the use case constructs and validates against the strands_teams UseCase ABC,
* it constructs cleanly when ``npx`` is absent (MCP providers omitted, no raise),
* ``planning_context()`` supplies an explicit value for every ``heuristic.*`` path
  the coordinator template dereferences, and the template renders under
  ``StrictUndefined``,
* the phase spec has five phases mapping ``dispatch_team`` -> team-execution etc.,
* the ``strands_tools`` ``graph`` tool is dropped from the roster.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from code_context_agent.models.output import AnalysisResult
from code_context_agent.usecase import CodebaseAnalysisUseCase
from strands_teams import NarrativeRubric, PhaseSpec, TeamsLayout, validate_use_case
from strands_teams.templating import render_prompt


@pytest.fixture
def use_case(tmp_path: Path) -> CodebaseAnalysisUseCase:
    """A use case whose layout points at a temp output root."""
    return CodebaseAnalysisUseCase(output_root=tmp_path / ".code-context")


def _tool_name(tool: object) -> str:
    """Best-effort tool-name extraction across strands tool object shapes."""
    for attr in ("tool_name", "name", "__name__"):
        value = getattr(tool, attr, None)
        if isinstance(value, str):
            return value
    return type(tool).__name__


# ---------------------------------------------------------------------------
# Contract / construction
# ---------------------------------------------------------------------------


def test_constructs_and_validates(use_case: CodebaseAnalysisUseCase) -> None:
    """The use case satisfies the strands_teams UseCase ABC contract (AC-6-1)."""
    validate_use_case(use_case)  # raises MissingUseCaseMemberError if incomplete
    assert use_case.name == "codebase-analysis"
    assert use_case.structured_output_model is AnalysisResult
    assert isinstance(use_case.layout, TeamsLayout)
    assert isinstance(use_case.rubric, NarrativeRubric)
    assert isinstance(use_case.phases, PhaseSpec)
    assert use_case.findings_format_directive


def test_settings_uses_code_context_prefix(use_case: CodebaseAnalysisUseCase) -> None:
    """Settings carry the CODE_CONTEXT_ prefix and .code-context output dir (AC-6-1)."""
    assert use_case.settings.model_config["env_prefix"] == "CODE_CONTEXT_"
    assert use_case.settings.output_dir_name == ".code-context"


def test_rubric_has_domain_dimensions(use_case: CodebaseAnalysisUseCase) -> None:
    """The rubric scores the file:line / mermaid / path domain dimensions."""
    names = {d.name for d in use_case.rubric.dimensions}
    assert {"specificity", "structure", "diagrams", "depth", "cross_references"} <= names


# ---------------------------------------------------------------------------
# AC-6-6 / AC-8-6 — MCP providers omitted when npx is absent
# ---------------------------------------------------------------------------


def test_domain_tools_omit_providers_when_npx_missing(
    use_case: CodebaseAnalysisUseCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With npx absent, the MCP providers are omitted and construction does not raise."""
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    tools = use_case.domain_tools()  # must not raise (AC-6-6)
    names = {_tool_name(t) for t in tools}
    assert not any(n.startswith(("gitnexus", "context7")) for n in names)
    # The domain planning tool and core discovery/git/shell tools remain.
    assert "read_heuristic_summary" in names
    assert "rg_search" in names


def test_domain_tools_provider_builders_pass_continue_on_error(
    use_case: CodebaseAnalysisUseCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider builders construct MCPClient with continue_on_error=True (AC-8-6)."""
    from code_context_agent import providers

    captured: dict[str, object] = {}

    class _FakeMCPClient:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/npx")
    monkeypatch.setattr("strands.tools.mcp.MCPClient", _FakeMCPClient, raising=False)

    provider = providers.build_gitnexus_provider(use_case.settings)
    assert provider is not None
    assert captured.get("continue_on_error") is True
    assert captured.get("prefix") == "gitnexus"


# ---------------------------------------------------------------------------
# AC-9-7 — graph tool dropped, no strands_tools in the roster
# ---------------------------------------------------------------------------


def test_graph_tool_dropped(
    use_case: CodebaseAnalysisUseCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strands_tools graph tool is not in the roster (AC-9-7)."""
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    tools = use_case.domain_tools()
    assert all(_tool_name(t) != "graph" for t in tools)
    # No tool object should originate from the strands_tools package.
    assert all(not type(t).__module__.startswith("strands_tools") for t in tools)


# ---------------------------------------------------------------------------
# Phases — 5-phase spec matching the pre-split TOOL_PHASE_MAP
# ---------------------------------------------------------------------------


def test_phase_spec_shape(use_case: CodebaseAnalysisUseCase) -> None:
    """Five ordered phases with the expected tool->phase mappings."""
    phases = use_case.phases
    assert phases.count == 5
    assert phases.name(1) == "Indexing"
    assert phases.name(3) == "Team Execution"
    assert phases.name(5) == "Bundle Generation"
    # Tool-to-phase mappings match the pre-split TOOL_PHASE_MAP.
    assert phases.resolve("read_heuristic_summary") == 2  # TEAM_PLANNING
    assert phases.resolve("dispatch_team") == 3  # TEAM_EXECUTION
    assert phases.resolve("git_hotspots") == 3
    assert phases.resolve("read_team_findings") == 4  # CONSOLIDATION
    assert phases.resolve("write_artifact") == 5  # BUNDLE_GENERATION
    # MCP prefix rules resolve to team execution.
    assert phases.resolve("gitnexus_query") == 3
    assert phases.resolve("context7_query-docs") == 3
    assert phases.resolve("unknown_tool") is None


# ---------------------------------------------------------------------------
# planning_context — explicit value for every heuristic.* template key
# ---------------------------------------------------------------------------


def _template_heuristic_paths() -> set[str]:
    """Grep the template tree for every ``heuristic.<a>.<b>...`` path referenced."""
    template_root = Path(CodebaseAnalysisUseCase().template_dir())
    paths: set[str] = set()
    for tpl in template_root.rglob("*.j2"):
        for match in re.findall(r"heuristic((?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)", tpl.read_text()):
            paths.add("heuristic" + match)
    return paths


def _resolve(namespace: dict[str, object], dotted: str) -> object:
    """Resolve a dotted path against a nested dict, raising KeyError if missing."""
    node: object = namespace
    for part in dotted.split("."):
        assert isinstance(node, dict), f"expected dict at {part!r} in {dotted!r}"
        if part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def test_planning_context_supplies_every_template_key(use_case: CodebaseAnalysisUseCase) -> None:
    """Every heuristic.* path the template dereferences has an explicit default (AC-6-1)."""
    ctx = use_case.planning_context()
    assert "heuristic" in ctx
    referenced = _template_heuristic_paths()
    assert referenced  # sanity: the template does dereference heuristic.*
    for dotted in referenced:
        _resolve(ctx, dotted)  # raises KeyError if the key is absent


def test_planning_context_overlays_real_summary(use_case: CodebaseAnalysisUseCase) -> None:
    """Loaded summary values take precedence over defaults; missing keys stay present."""
    sentinel = use_case.layout.planning_sentinel_path()
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        json.dumps(
            {
                "volume": {"total_files": 847, "languages": {"python": 40}},
                "gitnexus": {"indexed": True, "community_count": 12},
            },
        ),
    )
    ctx = use_case.planning_context()["heuristic"]
    assert ctx["volume"]["total_files"] == 847
    assert ctx["gitnexus"]["indexed"] is True
    assert ctx["gitnexus"]["community_count"] == 12
    # Defaults for unspecified keys are preserved.
    assert ctx["gitnexus"]["top_communities"] == []
    assert ctx["mcp"]["context7_available"] is False
    assert ctx["health"]["semgrep_findings"]["critical"] == 0


def test_coordinator_template_renders_with_planning_context(use_case: CodebaseAnalysisUseCase) -> None:
    """The coordinator template renders under StrictUndefined with the exact render
    contract strands_teams.coordinator uses (planning_context / use_case_name /
    artifact_structure_spec / findings_format_directive).
    """
    prompt = render_prompt(
        use_case.template_dir(),
        use_case.prompt_template(),
        planning_context=use_case.planning_context(),
        artifact_structure_spec=use_case.artifact_structure_spec(),
        findings_format_directive=use_case.findings_format_directive,
        use_case_name=use_case.name,
    )
    assert "Analysis Coordinator" in prompt
    # Repo/output paths flow in via planning_context (not top-level kwargs).
    assert str(use_case.layout.output_root) in prompt


# ---------------------------------------------------------------------------
# kickoff_prompt / artifact_structure_spec
# ---------------------------------------------------------------------------


def test_kickoff_prompt_modes(use_case: CodebaseAnalysisUseCase) -> None:
    """Kickoff prompt reflects bundles-only, focus, and extra-context inputs."""
    normal = use_case.kickoff_prompt(bundles_only=False, focus=None, extra_context=None)
    assert "Begin analysis" in normal

    bundles = use_case.kickoff_prompt(bundles_only=True, focus=None, extra_context=None)
    assert "Bundles-only mode" in bundles

    focused = use_case.kickoff_prompt(bundles_only=False, focus="auth", extra_context="see bug #42")
    assert "FOCUS AREA: auth" in focused
    assert "Issue Context" in focused
    assert "see bug #42" in focused


def test_artifact_structure_spec_mentions_both_artifacts(use_case: CodebaseAnalysisUseCase) -> None:
    """The artifact-structure spec names CONTEXT.md and the per-area bundles."""
    spec = use_case.artifact_structure_spec()
    assert "CONTEXT.md" in spec
    assert "BUNDLE.{area}.md" in spec
    assert "mermaid" in spec


# ---------------------------------------------------------------------------
# pre_run — auto-index no-op when sentinel exists
# ---------------------------------------------------------------------------


def test_pre_run_noop_when_sentinel_exists(use_case: CodebaseAnalysisUseCase) -> None:
    """pre_run does not re-index when the planning sentinel already exists (AC-6-2)."""
    sentinel = use_case.layout.planning_sentinel_path()
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("{}")
    # Should return immediately without invoking the indexer.
    use_case.pre_run(Path.cwd())
    assert sentinel.read_text() == "{}"


# ---------------------------------------------------------------------------
# AC-5-3 end-to-end — create_coordinator renders and assembles from the use case
# ---------------------------------------------------------------------------


def test_create_coordinator_constructs_without_network(
    use_case: CodebaseAnalysisUseCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_coordinator builds an Agent from this use case with no network.

    Guards the real render contract: strands_teams renders coordinator.md.j2 with
    ``planning_context`` / ``use_case_name`` / ``artifact_structure_spec`` /
    ``findings_format_directive`` only. If the template dereferences anything the
    contract does not supply, this raises under StrictUndefined at construction.
    ``BedrockModel`` constructs offline, so no Bedrock call is made. MCP providers
    are dropped (npx absent) to keep the roster hermetic.
    """
    from strands import Agent

    from strands_teams.coordinator import create_coordinator

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    agent, state = create_coordinator(use_case)
    assert isinstance(agent, Agent)
    # Five team tools present in the invocation-state registry.
    for team_tool in ("dispatch_team", "read_team_findings", "write_artifact"):
        assert team_tool in state["tool_registry"]
    # Domain render + framework teams-protocol partial both present.
    assert "Analysis Coordinator" in agent.system_prompt
    assert "dispatch_team" in agent.system_prompt
    assert str(use_case.layout.output_root) in agent.system_prompt
