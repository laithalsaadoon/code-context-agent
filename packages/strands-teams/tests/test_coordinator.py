"""Tests for the generic coordinator factory (T-C1, AC-5-3, AC-5-4, AC-8-2, AC-8-4).

No real Bedrock/network calls: ``BedrockModel`` constructs offline (no network on
init) so we build a real coordinator ``Agent`` and inspect its tools + invocation
state without invoking it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict
from strands import Agent, tool

from strands_teams.coordinator import create_coordinator
from strands_teams.layout import TeamsLayout
from strands_teams.phases import PhaseSpec
from strands_teams.rubric import DEFAULT_NARRATIVE_RUBRIC
from strands_teams.settings import TeamsSettings
from strands_teams.usecase import MissingUseCaseMemberError, UseCase

if TYPE_CHECKING:
    from pathlib import Path


class _Output(BaseModel):
    status: str = "ok"


class _Settings(TeamsSettings):
    model_config = SettingsConfigDict(env_prefix="FAKE_", extra="ignore")
    output_dir_name: str = ".fake"


@tool
def fake_domain_tool(query: str) -> str:
    """A fake domain tool for registry/agent assembly checks."""
    return query


class FakeUseCase(UseCase):
    name = "fake"
    structured_output_model = _Output
    findings_format_directive = "Write markdown findings."

    def __init__(self, template_dir: Path, output_root: Path | None = None) -> None:
        self.settings = _Settings()
        self.layout = TeamsLayout(output_root=output_root or (template_dir.parent / ".fake"))
        self.rubric = DEFAULT_NARRATIVE_RUBRIC
        self.phases = PhaseSpec(names={1: "Plan", 2: "Execute"})
        self._template_dir = template_dir

    def domain_tools(self) -> list[Any]:
        return [fake_domain_tool]

    def prompt_template(self) -> str:
        return "coordinator.md.j2"

    def template_dir(self) -> Path:
        return self._template_dir

    def planning_context(self) -> dict[str, Any]:
        return {"volume": {"total_files": 10}}

    def kickoff_prompt(self, *, bundles_only: bool, focus: str | None, extra_context: str | None) -> str:
        return "go"

    def artifact_structure_spec(self) -> str:
        return "Sections 1..7"


@pytest.fixture
def template_dir(tmp_path: Path) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    (d / "coordinator.md.j2").write_text(
        "# Coordinator for {{ use_case_name }}\n"
        "Files: {{ planning_context.volume.total_files }}\n"
        "{{ artifact_structure_spec }}\n"
        "{{ findings_format_directive }}\n"
    )
    return d


def test_create_coordinator_assembles_tools(template_dir: Path) -> None:
    uc = FakeUseCase(template_dir)
    agent, _state = create_coordinator(uc)

    assert isinstance(agent, Agent)
    names = set(agent.tool_names)
    # AC-5-3: five team tools + the domain tool.
    for team_tool in ("dispatch_team", "read_team_findings", "write_artifact", "score_narrative", "enrich_artifact"):
        assert team_tool in names
    assert "fake_domain_tool" in names


def test_create_coordinator_prompt_composition(template_dir: Path) -> None:
    uc = FakeUseCase(template_dir)
    agent, _ = create_coordinator(uc)
    prompt = agent.system_prompt
    # Domain render present.
    assert "Coordinator for fake" in prompt
    assert "Files: 10" in prompt
    assert "Sections 1..7" in prompt
    assert "Write markdown findings." in prompt
    # Framework teams-protocol partial appended.
    assert "Teams protocol" in prompt
    assert "dispatch_team" in prompt


def test_create_coordinator_invocation_state(template_dir: Path) -> None:
    uc = FakeUseCase(template_dir)
    _, state = create_coordinator(uc)
    # AC-8-4: shared state carried in invocation_state (no module globals).
    assert state["layout"] is uc.layout
    assert state["rubric"] is uc.rubric
    assert state["use_case_name"] == "fake"
    assert "fake_domain_tool" in state["tool_registry"]
    assert "dispatch_team" in state["tool_registry"]
    # Standard-mode timeouts from settings.
    assert state["execution_timeout"] == float(uc.settings.team_execution_timeout)
    assert state["node_timeout"] == float(uc.settings.team_node_timeout)
    # AC-8-2: explicit model config for team nodes.
    cfg = state["node_model_config"]
    assert cfg["model_id"] == uc.settings.model_id
    assert cfg["region_name"] == uc.settings.region
    assert cfg["additional_request_fields"]["thinking"] == {"type": "adaptive"}
    assert cfg["cache_config"] == {"strategy": "auto"}


def test_create_coordinator_structured_output_model(template_dir: Path) -> None:
    uc = FakeUseCase(template_dir)
    agent, _ = create_coordinator(uc)
    # AC-5-3: structured_output_model set on the coordinator (stored as
    # _default_structured_output_model in strands 1.47).
    assert getattr(agent, "_default_structured_output_model", None) is _Output


def test_create_coordinator_full_mode_timeouts(template_dir: Path) -> None:
    uc = FakeUseCase(template_dir)
    _, state = create_coordinator(uc, full_mode=True)
    assert state["execution_timeout"] == float(uc.settings.full_team_execution_timeout)
    assert state["node_timeout"] == float(uc.settings.full_team_node_timeout)
    assert state["node_model_config"]["additional_request_fields"]["output_config"]["effort"] == (
        uc.settings.full_reasoning_effort
    )


def test_create_coordinator_rejects_incomplete_use_case(template_dir: Path) -> None:
    uc = FakeUseCase(template_dir)
    uc.rubric = None  # type: ignore[assignment]
    with pytest.raises(MissingUseCaseMemberError) as exc_info:
        create_coordinator(uc)
    assert exc_info.value.member == "rubric"
