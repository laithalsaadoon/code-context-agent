"""Tests for the generic hook providers (AC-3-1..AC-3-6).

Ported from ``code_context_agent/tests/test_hooks.py``, keeping the generic
semantics (compaction, efficiency warn, fail-fast raise/exempt, reasoning
append/unmapped-unchanged, artifact-quality pass budget) and dropping domain
literals. All domain data is injected via the ``UseCase``-supplied params.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict

from strands_teams.hooks import (
    ArtifactQualityHook,
    ConversationCompactionHook,
    FailFastHook,
    FullModeToolError,
    JsonLogHook,
    OutputQualityHook,
    ReasoningCheckpointHook,
    TeamDispatchHook,
    ToolDisplayHook,
    ToolEfficiencyHook,
    create_all_hooks,
)
from strands_teams.layout import TeamsLayout
from strands_teams.phases import PhaseSpec
from strands_teams.rubric import DEFAULT_NARRATIVE_RUBRIC
from strands_teams.settings import TeamsSettings
from strands_teams.usecase import UseCase

# ---------------------------------------------------------------------------
# Test fixtures: minimal fakes and a use case.
# ---------------------------------------------------------------------------

_PROMPTS = {"git_hotspots": "[REASONING CHECKPOINT] interpret the hotspots"}
_ALTERNATIVES = {"grep": "rg_search", "cat ": "read_file_bounded"}
_EXEMPT = frozenset({"rg_search", "shell"})
_EXEMPT_PREFIXES = ("context7_", "gitnexus_")


class _FakeAfterToolEvent:
    """Minimal ``AfterToolCallEvent`` stand-in."""

    def __init__(
        self, tool_name: str, result: Any, tool_id: str = "t1", input_args: dict[str, Any] | None = None
    ) -> None:
        self.tool_use = {"name": tool_name, "toolUseId": tool_id, "input": input_args or {}}
        self.result = result


class _FakeBeforeToolEvent:
    """Minimal ``BeforeToolCallEvent`` stand-in."""

    def __init__(self, tool_name: str, input_args: dict[str, Any], tool_id: str = "t1") -> None:
        self.tool_use = {"name": tool_name, "toolUseId": tool_id, "input": input_args}


class _FakeInvocationEvent:
    """Minimal ``AfterInvocationEvent`` stand-in (only ``resume`` matters)."""

    resume: Any = None


class _RecordingState:
    """Duck-typed TeamDisplayState that records calls."""

    def __init__(self) -> None:
        self.teams_started: list[tuple[str, str, int]] = []
        self.teams_completed: list[tuple[str, str]] = []
        self.tools: list[tuple[str, str, str]] = []

    def start_team(self, team_id: str, mandate: str, agent_count: int) -> None:
        self.teams_started.append((team_id, mandate, agent_count))

    def complete_team(self, team_id: str, *, status: str) -> None:
        self.teams_completed.append((team_id, status))

    def record_tool(self, *, tool_id: str, tool_name: str, status: str, result: str) -> None:
        self.tools.append((tool_name, status, result))


class _DummyOutput(BaseModel):
    status: str = "ok"


class _DummySettings(TeamsSettings):
    model_config = SettingsConfigDict(env_prefix="DUMMY_", extra="ignore")
    output_dir_name: str = ".dummy"


class _UseCase(UseCase):
    """A concrete use case wired with generic hook data."""

    name = "dummy"
    structured_output_model = _DummyOutput
    findings_format_directive = "Write findings as markdown."

    # Optional (non-contract) knobs read by create_all_hooks via getattr.
    shell_alternatives = _ALTERNATIVES
    fail_fast_exempt_prefixes = _EXEMPT_PREFIXES

    def __init__(self, output_root: Path) -> None:
        self.settings = _DummySettings()
        self.layout = TeamsLayout(output_root=output_root)
        self.rubric = DEFAULT_NARRATIVE_RUBRIC
        self.phases = PhaseSpec(names={1: "Plan", 2: "Execute"})
        self.reasoning_prompts = _PROMPTS
        self.fail_fast_exempt = _EXEMPT

    def domain_tools(self) -> list[Any]:
        return []

    def prompt_template(self) -> str:
        return "coordinator.md.j2"

    def template_dir(self) -> Path:
        return Path("/work/templates")

    def planning_context(self) -> dict[str, Any]:
        return {}

    def kickoff_prompt(self, *, bundles_only: bool, focus: str | None, extra_context: str | None) -> str:
        return "go"

    def artifact_structure_spec(self) -> str:
        return "Section 1..7"


@pytest.fixture
def use_case(tmp_path: Path) -> _UseCase:
    return _UseCase(output_root=tmp_path)


# ---------------------------------------------------------------------------
# OutputQualityHook
# ---------------------------------------------------------------------------


class TestOutputQualityHook:
    def test_instantiates(self) -> None:
        assert OutputQualityHook() is not None

    def test_warns_on_oversized(self) -> None:
        hook = OutputQualityHook()
        event = _FakeAfterToolEvent("big_tool", "x" * 200_000)
        hook._check_output_quality(event)  # should not raise

    def test_silent_on_small(self) -> None:
        hook = OutputQualityHook()
        hook._check_output_quality(_FakeAfterToolEvent("t", "small"))


# ---------------------------------------------------------------------------
# ConversationCompactionHook
# ---------------------------------------------------------------------------


class TestConversationCompactionHook:
    def test_compacts_large_tool_result(self) -> None:
        hook = ConversationCompactionHook()
        big_text = "y" * 5000
        messages = [
            {"content": [{"toolResult": {"toolUseId": "a", "content": [{"text": big_text}]}}]},
            *({"content": [{"text": "recent"}]} for _ in range(hook.KEEP_RECENT_MESSAGES)),
        ]

        class Ev:
            pass

        ev = Ev()
        ev.messages = messages
        hook._compact_history(ev)
        assert messages[0]["content"][0]["toolResult"]["content"] == [
            {"text": f"[Tool output consumed ({len(big_text)} chars) — see reasoning above]"}
        ]

    def test_keeps_small_payload(self) -> None:
        hook = ConversationCompactionHook()
        block = {"toolResult": {"toolUseId": "a", "content": [{"text": "short"}]}}
        assert hook._compact_tool_result(block) == 0

    def test_no_messages_noop(self) -> None:
        hook = ConversationCompactionHook()

        class Ev:
            messages = None

        hook._compact_history(Ev())  # no raise


# ---------------------------------------------------------------------------
# ToolEfficiencyHook (alternatives injected)
# ---------------------------------------------------------------------------


class TestToolEfficiencyHook:
    def test_instantiates_with_alternatives(self) -> None:
        hook = ToolEfficiencyHook(_ALTERNATIVES)
        assert hook._alternatives == _ALTERNATIVES

    def test_warns_on_matching_shell_command(self) -> None:
        hook = ToolEfficiencyHook(_ALTERNATIVES)
        hook._check_tool_efficiency(_FakeBeforeToolEvent("shell", {"command": "grep -r foo ."}))

    def test_ignores_non_shell(self) -> None:
        hook = ToolEfficiencyHook(_ALTERNATIVES)
        hook._check_tool_efficiency(_FakeBeforeToolEvent("rg_search", {"query": "grep"}))

    def test_empty_alternatives_noop(self) -> None:
        hook = ToolEfficiencyHook({})
        hook._check_tool_efficiency(_FakeBeforeToolEvent("shell", {"command": "grep foo"}))


# ---------------------------------------------------------------------------
# ReasoningCheckpointHook (prompts injected) — AC-3-2
# ---------------------------------------------------------------------------


class TestReasoningCheckpointHook:
    def test_injects_prompt_for_mapped_tool(self) -> None:
        hook = ReasoningCheckpointHook(_PROMPTS)
        event = _FakeAfterToolEvent("git_hotspots", {"status": "success", "content": [{"text": "hotspot data"}]})
        hook._inject_reasoning_prompt(event)
        assert len(event.result["content"]) == 2
        assert "REASONING CHECKPOINT" in event.result["content"][1]["text"]

    def test_no_prompt_for_unmapped_tool(self) -> None:
        hook = ReasoningCheckpointHook(_PROMPTS)
        event = _FakeAfterToolEvent("create_file_manifest", {"status": "success", "content": [{"text": "data"}]})
        hook._inject_reasoning_prompt(event)
        assert len(event.result["content"]) == 1  # unchanged

    def test_no_prompt_for_error_result(self) -> None:
        hook = ReasoningCheckpointHook(_PROMPTS)
        event = _FakeAfterToolEvent("git_hotspots", {"status": "error", "content": [{"text": "fail"}]})
        hook._inject_reasoning_prompt(event)
        assert len(event.result["content"]) == 1

    def test_no_prompt_for_empty_content(self) -> None:
        hook = ReasoningCheckpointHook(_PROMPTS)
        event = _FakeAfterToolEvent("git_hotspots", {"status": "success", "content": []})
        hook._inject_reasoning_prompt(event)
        assert len(event.result["content"]) == 0


# ---------------------------------------------------------------------------
# FailFastHook (exempt set + prefixes injected) — AC-3-4
# ---------------------------------------------------------------------------


class TestFullModeToolError:
    def test_inherits_runtime_error(self) -> None:
        assert isinstance(FullModeToolError("t", "boom"), RuntimeError)

    def test_preserves_tool_name(self) -> None:
        assert FullModeToolError("t", "boom").tool_name == "t"

    def test_message_format(self) -> None:
        err = FullModeToolError("lsp_start", "not found")
        assert "lsp_start" in str(err)
        assert "not found" in str(err)


class TestFailFastHook:
    def test_raises_on_error_status(self) -> None:
        hook = FailFastHook(_EXEMPT, _EXEMPT_PREFIXES)
        event = _FakeAfterToolEvent("some_tool", json.dumps({"status": "error", "error": "boom"}))
        with pytest.raises(FullModeToolError) as exc_info:
            hook._check_for_error(event)
        assert exc_info.value.tool_name == "some_tool"

    def test_does_not_raise_on_success(self) -> None:
        hook = FailFastHook(_EXEMPT, _EXEMPT_PREFIXES)
        hook._check_for_error(_FakeAfterToolEvent("some_tool", json.dumps({"status": "success"})))

    def test_does_not_raise_for_exempt_tool(self) -> None:
        hook = FailFastHook(_EXEMPT, _EXEMPT_PREFIXES)
        hook._check_for_error(_FakeAfterToolEvent("rg_search", json.dumps({"status": "error"})))

    def test_does_not_raise_for_exempt_prefix(self) -> None:
        hook = FailFastHook(_EXEMPT, _EXEMPT_PREFIXES)
        hook._check_for_error(_FakeAfterToolEvent("context7_query-docs", json.dumps({"status": "error"})))

    def test_no_prefixes_still_works(self) -> None:
        hook = FailFastHook(_EXEMPT)  # default empty prefixes
        with pytest.raises(FullModeToolError):
            hook._check_for_error(_FakeAfterToolEvent("gitnexus_query", json.dumps({"status": "error"})))

    def test_non_json_result_ignored(self) -> None:
        hook = FailFastHook(_EXEMPT, _EXEMPT_PREFIXES)
        hook._check_for_error(_FakeAfterToolEvent("some_tool", "not json"))


# ---------------------------------------------------------------------------
# ArtifactQualityHook — AC-3-3, AC-3-6
# ---------------------------------------------------------------------------


def _write_artifact(layout: TeamsLayout, area: str, content: str) -> None:
    layout.artifacts_dir().mkdir(parents=True, exist_ok=True)
    layout.artifact_path(area).write_text(content)


class TestArtifactQualityHook:
    def test_instantiates(self, use_case: _UseCase) -> None:
        hook = ArtifactQualityHook(use_case.layout, use_case.rubric)
        assert hook._pass_count == 0
        assert hook._max_passes == 2

    def test_skips_when_no_artifacts_dir(self, use_case: _UseCase) -> None:
        hook = ArtifactQualityHook(use_case.layout, use_case.rubric)
        event = _FakeInvocationEvent()
        hook._check_artifact_quality(event)
        assert event.resume is None

    def test_skips_when_no_artifact_files(self, use_case: _UseCase) -> None:
        use_case.layout.artifacts_dir().mkdir(parents=True)
        hook = ArtifactQualityHook(use_case.layout, use_case.rubric)
        event = _FakeInvocationEvent()
        hook._check_artifact_quality(event)
        assert event.resume is None

    def test_triggers_enrichment_for_weak_artifact(self, use_case: _UseCase) -> None:
        _write_artifact(use_case.layout, "api", "Thin content.\nNo refs.")
        hook = ArtifactQualityHook(use_case.layout, use_case.rubric)
        event = _FakeInvocationEvent()
        hook._check_artifact_quality(event)
        assert event.resume is not None
        assert "ENRICHMENT PASS 1" in event.resume
        assert "api" in event.resume
        assert hook._pass_count == 1

    def test_passes_for_rich_artifact(self, use_case: _UseCase) -> None:
        rich = "\n".join(
            [f"## Section {i}" for i in range(12)]
            + ["```mermaid", "graph LR", "A --> B", "```", "```mermaid", "graph TD", "X --> Y", "```"]
            + [f"module_{i}.py:{i} references src/pkg/mod_{i}.py in detail." for i in range(40)]
            + [f"Detailed analysis line {i} covering implementation specifics." for i in range(200)]
        )
        _write_artifact(use_case.layout, "core", rich)
        hook = ArtifactQualityHook(use_case.layout, use_case.rubric)
        event = _FakeInvocationEvent()
        hook._check_artifact_quality(event)
        assert event.resume is None
        assert hook._pass_count == 0

    def test_budget_exhaustion_stops_resume(self, use_case: _UseCase) -> None:
        _write_artifact(use_case.layout, "api", "Thin.")
        hook = ArtifactQualityHook(use_case.layout, use_case.rubric, max_passes=2)

        ev1 = _FakeInvocationEvent()
        hook._check_artifact_quality(ev1)
        assert hook._pass_count == 1
        assert ev1.resume is not None

        ev2 = _FakeInvocationEvent()
        hook._check_artifact_quality(ev2)
        assert hook._pass_count == 2
        assert ev2.resume is not None

        # Third call: budget exhausted, no further resume (AC-3-6).
        ev3 = _FakeInvocationEvent()
        hook._check_artifact_quality(ev3)
        assert ev3.resume is None
        assert hook._pass_count == 2

    def test_custom_resume_template(self, use_case: _UseCase) -> None:
        _write_artifact(use_case.layout, "api", "Thin.")
        hook = ArtifactQualityHook(
            use_case.layout, use_case.rubric, resume_template="RETRY {pass_num}/{max_passes} {weak_list}"
        )
        event = _FakeInvocationEvent()
        hook._check_artifact_quality(event)
        assert event.resume.startswith("RETRY 1/2")
        assert "api" in event.resume


# ---------------------------------------------------------------------------
# Display hooks (duck-typed state)
# ---------------------------------------------------------------------------


class TestToolDisplayHook:
    def test_records_tool_lifecycle(self) -> None:
        state = _RecordingState()
        hook = ToolDisplayHook(state)
        hook._on_tool_start(_FakeBeforeToolEvent("rg_search", {}))
        hook._on_tool_end(_FakeAfterToolEvent("rg_search", json.dumps({"status": "success"})))
        assert state.tools[0] == ("rg_search", "running", "")
        assert state.tools[1][0] == "rg_search"
        assert state.tools[1][1] == "completed"

    def test_records_error_status(self) -> None:
        state = _RecordingState()
        hook = ToolDisplayHook(state)
        hook._on_tool_end(_FakeAfterToolEvent("t", json.dumps({"status": "error"})))
        assert state.tools[0][1] == "error"

    def test_drives_real_agent_display_state(self) -> None:
        """Seam test: the concrete AgentDisplayState the runner builds must satisfy
        the ToolDisplayHook contract (regression for a missing record_tool method)."""
        from strands_teams.display.state import AgentDisplayState
        from strands_teams.phases import PhaseSpec

        spec = PhaseSpec(
            names={1: "Discovery", 2: "Execution"},
            descriptions={1: "d1", 2: "d2"},
            tool_phase_map={"rg_search": 1, "dispatch_team": 2},
        )
        state = AgentDisplayState(phase_spec=spec)
        hook = ToolDisplayHook(state)
        hook._on_tool_start(_FakeBeforeToolEvent("rg_search", {}))
        assert state.active_tool is not None
        assert state.active_tool.tool_name == "rg_search"
        hook._on_tool_end(_FakeAfterToolEvent("rg_search", json.dumps({"status": "error"})))
        assert state.active_tool is None
        assert len(state.completed_tools) == 1
        assert state.tool_errors == 1


class TestTeamDispatchHook:
    def test_tracks_dispatch_lifecycle(self) -> None:
        state = _RecordingState()
        hook = TeamDispatchHook(state)
        hook._on_dispatch_start(
            _FakeBeforeToolEvent("dispatch_team", {"team_id": "team-a", "mandate": "m", "agents": [1, 2, 3]})
        )
        hook._on_dispatch_end(
            _FakeAfterToolEvent("dispatch_team", json.dumps({"status": "success"}), input_args={"team_id": "team-a"})
        )
        assert state.teams_started == [("team-a", "m", 3)]
        assert state.teams_completed == [("team-a", "done")]

    def test_ignores_non_dispatch_tools(self) -> None:
        state = _RecordingState()
        hook = TeamDispatchHook(state)
        hook._on_dispatch_start(_FakeBeforeToolEvent("rg_search", {}))
        assert state.teams_started == []

    def test_error_dispatch_status(self) -> None:
        state = _RecordingState()
        hook = TeamDispatchHook(state)
        hook._on_dispatch_end(_FakeAfterToolEvent("dispatch_team", json.dumps({"status": "error"}), tool_id="x"))
        # start not called, but end records completion
        assert state.teams_completed[0][1] == "error"


# ---------------------------------------------------------------------------
# create_all_hooks composition matrix — AC-3-5
# ---------------------------------------------------------------------------


class TestCreateAllHooks:
    def test_standard_mode_five_hooks(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=False, state=None, quiet=False)
        assert len(hooks) == 5
        types = {type(h) for h in hooks}
        assert types == {
            ConversationCompactionHook,
            OutputQualityHook,
            ToolEfficiencyHook,
            ReasoningCheckpointHook,
            ArtifactQualityHook,
        }

    def test_full_mode_adds_failfast(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=True, state=None, quiet=False)
        assert len(hooks) == 6
        assert any(isinstance(h, FailFastHook) for h in hooks)

    def test_standard_mode_no_failfast(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=False, state=None, quiet=False)
        assert not any(isinstance(h, FailFastHook) for h in hooks)

    def test_quiet_adds_json_log(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=False, state=None, quiet=True)
        assert any(isinstance(h, JsonLogHook) for h in hooks)
        assert not any(isinstance(h, (ToolDisplayHook, TeamDispatchHook)) for h in hooks)

    def test_state_adds_display_hooks(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=False, state=_RecordingState(), quiet=False)
        assert any(isinstance(h, ToolDisplayHook) for h in hooks)
        assert any(isinstance(h, TeamDispatchHook) for h in hooks)

    def test_quiet_overrides_state(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=False, state=_RecordingState(), quiet=True)
        assert any(isinstance(h, JsonLogHook) for h in hooks)
        assert not any(isinstance(h, (ToolDisplayHook, TeamDispatchHook)) for h in hooks)

    def test_no_state_no_quiet_has_no_display_hooks(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=False, state=None, quiet=False)
        assert not any(isinstance(h, (JsonLogHook, ToolDisplayHook, TeamDispatchHook)) for h in hooks)

    def test_full_quiet_matrix(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=True, state=None, quiet=True)
        types = [type(h) for h in hooks]
        assert FailFastHook in types
        assert JsonLogHook in types
        assert len(hooks) == 7

    def test_reasoning_prompts_wired_from_use_case(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=False, state=None, quiet=False)
        reasoning = next(h for h in hooks if isinstance(h, ReasoningCheckpointHook))
        assert reasoning._prompts == _PROMPTS

    def test_failfast_wired_from_use_case(self, use_case: _UseCase) -> None:
        hooks = create_all_hooks(use_case, full_mode=True, state=None, quiet=False)
        failfast = next(h for h in hooks if isinstance(h, FailFastHook))
        assert failfast._exempt == _EXEMPT
        assert failfast._exempt_prefixes == _EXEMPT_PREFIXES

    def test_optional_knobs_default_empty(self, tmp_path: Path) -> None:
        """A use case without the optional (non-contract) knobs gets empty defaults."""

        class _Bare:
            """Bare object exposing only the members create_all_hooks reads."""

            reasoning_prompts = _PROMPTS
            fail_fast_exempt = _EXEMPT
            layout = TeamsLayout(output_root=tmp_path)
            rubric = DEFAULT_NARRATIVE_RUBRIC

        hooks = create_all_hooks(_Bare(), full_mode=True, state=None, quiet=False)
        eff = next(h for h in hooks if isinstance(h, ToolEfficiencyHook))
        assert eff._alternatives == {}
        failfast = next(h for h in hooks if isinstance(h, FailFastHook))
        assert failfast._exempt_prefixes == ()
