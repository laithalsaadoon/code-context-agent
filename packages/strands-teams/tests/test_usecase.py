"""Tests for the UseCase contract and validate_use_case (AC-5-1, AC-5-5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict

from strands_teams.layout import TeamsLayout
from strands_teams.phases import PhaseSpec
from strands_teams.rubric import DEFAULT_NARRATIVE_RUBRIC
from strands_teams.settings import TeamsSettings
from strands_teams.usecase import MissingUseCaseMemberError, UseCase, validate_use_case


class _DummyOutput(BaseModel):
    status: str = "ok"


class _DummySettings(TeamsSettings):
    model_config = SettingsConfigDict(env_prefix="DUMMY_", extra="ignore")
    output_dir_name: str = ".dummy"


class _CompleteUseCase(UseCase):
    name = "dummy"
    structured_output_model = _DummyOutput
    findings_format_directive = "Write findings as markdown."

    def __init__(self) -> None:
        self.settings = _DummySettings()
        self.layout = TeamsLayout(output_root=Path("/work/dummy/.dummy"))
        self.rubric = DEFAULT_NARRATIVE_RUBRIC
        self.phases = PhaseSpec(names={1: "Plan", 2: "Execute"})
        self.reasoning_prompts = {"dispatch_team": "Reflect before dispatching."}
        self.fail_fast_exempt = frozenset({"rg_search"})

    def domain_tools(self) -> list[Any]:
        return []

    def prompt_template(self) -> str:
        return "coordinator.md.j2"

    def template_dir(self) -> Path:
        return Path("/work/dummy/templates")

    def planning_context(self) -> dict[str, Any]:
        return {"volume": {}}

    def kickoff_prompt(self, *, bundles_only: bool, focus: str | None, extra_context: str | None) -> str:
        return f"go bundles_only={bundles_only} focus={focus} extra={extra_context}"

    def artifact_structure_spec(self) -> str:
        return "Section 1..7"


def test_complete_use_case_validates() -> None:
    uc = _CompleteUseCase()
    validate_use_case(uc)  # must not raise
    assert uc.name == "dummy"
    assert uc.phases.count == 2
    assert uc.pre_run(Path("/work/dummy")) is None  # default no-op hook


def test_missing_instance_member_raises_naming_the_member() -> None:
    uc = _CompleteUseCase()
    # Simulate a use case that forgot to set its rubric in __init__.
    uc.rubric = None  # type: ignore[assignment]
    with pytest.raises(MissingUseCaseMemberError) as exc_info:
        validate_use_case(uc)
    assert exc_info.value.member == "rubric"
    assert "rubric" in str(exc_info.value)
    assert "dummy" in str(exc_info.value)


def test_missing_class_attr_raises_at_definition() -> None:
    # A concrete subclass that omits a required class attribute (findings_format_directive)
    # must fail at class-definition time, naming the member.
    with pytest.raises(MissingUseCaseMemberError) as exc_info:

        class _Broken(UseCase):
            name = "broken"
            structured_output_model = _DummyOutput
            # findings_format_directive intentionally omitted

            def domain_tools(self) -> list[Any]:
                return []

            def prompt_template(self) -> str:
                return "x"

            def template_dir(self) -> Path:
                return Path("/work")

            def planning_context(self) -> dict[str, Any]:
                return {}

            def kickoff_prompt(self, *, bundles_only: bool, focus: str | None, extra_context: str | None) -> str:
                return ""

            def artifact_structure_spec(self) -> str:
                return ""

    assert exc_info.value.member == "findings_format_directive"


def test_kickoff_prompt_signature() -> None:
    uc = _CompleteUseCase()
    out = uc.kickoff_prompt(bundles_only=True, focus="auth", extra_context=None)
    assert "bundles_only=True" in out
    assert "focus=auth" in out
