"""Tests for the generic run loop (T-C1). No Bedrock/network — coordinator is stubbed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict

from strands_teams import runner
from strands_teams.layout import TeamsLayout
from strands_teams.phases import PhaseSpec
from strands_teams.rubric import DEFAULT_NARRATIVE_RUBRIC
from strands_teams.settings import TeamsSettings
from strands_teams.usecase import UseCase

if TYPE_CHECKING:
    import pytest


class _Output(BaseModel):
    status: str = "ok"
    note: str = "done"


class _Settings(TeamsSettings):
    model_config = SettingsConfigDict(env_prefix="RUN_", extra="ignore")
    output_dir_name: str = ".run"


class _UseCase(UseCase):
    name = "runcase"
    structured_output_model = _Output
    findings_format_directive = "markdown"

    def __init__(self, output_root: Path) -> None:
        self.settings = _Settings()
        self.layout = TeamsLayout(output_root=output_root)
        self.rubric = DEFAULT_NARRATIVE_RUBRIC
        self.phases = PhaseSpec(names={1: "Plan"})
        self._pre_run_called = False

    def domain_tools(self) -> list[Any]:
        return []

    def prompt_template(self) -> str:
        return "coordinator.md.j2"

    def template_dir(self) -> Path:
        return Path(__file__).parent

    def planning_context(self) -> dict[str, Any]:
        return {}

    def kickoff_prompt(self, *, bundles_only: bool, focus: str | None, extra_context: str | None) -> str:
        return "kickoff"

    def artifact_structure_spec(self) -> str:
        return "spec"

    def pre_run(self, repo_path: Path) -> None:
        self._pre_run_called = True


class _StubResult:
    def __init__(self) -> None:
        self.structured_output = _Output(status="ok", note="stub")
        self.stop_reason = "end_turn"


class _StubCoordinator:
    def __init__(self) -> None:
        self.invoked_with: dict[str, Any] | None = None

    async def invoke_async(self, prompt: str, invocation_state: dict[str, Any] | None = None) -> _StubResult:
        self.invoked_with = {"prompt": prompt, "invocation_state": invocation_state}
        return _StubResult()


async def test_run_teams_persists_structured_output_and_runs_pre_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uc = _UseCase(tmp_path / ".run")
    stub = _StubCoordinator()

    def fake_create(use_case: Any, **kwargs: Any) -> tuple[_StubCoordinator, dict[str, Any]]:
        return stub, {"layout": use_case.layout, "rubric": use_case.rubric}

    monkeypatch.setattr("strands_teams.coordinator.create_coordinator", fake_create)

    result = await runner.run_teams(uc, repo_path=tmp_path, quiet=True)

    assert result["status"] == "completed"
    assert uc._pre_run_called is True
    # invocation_state is forwarded to the coordinator (AC-8-4).
    assert stub.invoked_with is not None
    assert stub.invoked_with["invocation_state"]["layout"] is uc.layout
    # Structured output persisted next to artifacts.
    persisted = json.loads((uc.layout.output_root / "analysis_result.json").read_text())
    assert persisted["note"] == "stub"


async def test_run_teams_bundles_only_skips_pre_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    uc = _UseCase(tmp_path / ".run")
    monkeypatch.setattr(
        "strands_teams.coordinator.create_coordinator",
        lambda use_case, **_: (_StubCoordinator(), {"layout": use_case.layout}),
    )
    await runner.run_teams(uc, repo_path=tmp_path, bundles_only=True, quiet=True)
    assert uc._pre_run_called is False
