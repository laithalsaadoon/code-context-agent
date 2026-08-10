"""Domain-data assertions for the codebase-analysis hook inputs.

The generic hook *mechanics* (ReasoningCheckpointHook injection, FailFastHook
exemption, create_all_hooks assembly) are tested in ``strands_teams`` (T-C2).
These tests pin the *domain data* the use case supplies to those hooks — the
reasoning-checkpoint prompt map and the fail-fast exempt set — which moved
verbatim from the pre-split ``agent/hooks.py`` into
:class:`~code_context_agent.usecase.CodebaseAnalysisUseCase`.
"""

from __future__ import annotations

from code_context_agent.usecase import CodebaseAnalysisUseCase
from strands_teams.hooks import FailFastHook, ReasoningCheckpointHook, create_all_hooks


def _use_case(tmp_path) -> CodebaseAnalysisUseCase:
    return CodebaseAnalysisUseCase(output_root=tmp_path / ".code-context")


class TestReasoningPromptData:
    """The reasoning-prompt map the use case feeds ReasoningCheckpointHook."""

    def test_covers_expected_analysis_tools(self, tmp_path) -> None:
        prompts = _use_case(tmp_path).reasoning_prompts
        for tool in (
            "gitnexus_query",
            "gitnexus_context",
            "gitnexus_impact",
            "git_hotspots",
            "git_files_changed_together",
            "git_blame_summary",
            "read_file_bounded",
            "write_artifact",
        ):
            assert tool in prompts

    def test_prompts_are_checkpoint_directives(self, tmp_path) -> None:
        prompts = _use_case(tmp_path).reasoning_prompts
        assert "REASONING CHECKPOINT" in prompts["git_hotspots"]
        # write_artifact uses the enrichment-checkpoint phrasing and names the scorer.
        assert "ENRICHMENT CHECKPOINT" in prompts["write_artifact"]
        assert "score_narrative" in prompts["write_artifact"]

    def test_prompts_wire_into_reasoning_checkpoint_hook(self, tmp_path) -> None:
        """The prompt map is what ReasoningCheckpointHook actually injects."""
        uc = _use_case(tmp_path)
        hook = ReasoningCheckpointHook(uc.reasoning_prompts)
        event = _AfterEvent(
            "git_hotspots",
            {"status": "success", "content": [{"text": "hotspot data long enough to matter"}]},
        )
        hook._inject_reasoning_prompt(event)
        assert len(event.result["content"]) == 2
        assert "REASONING CHECKPOINT" in event.result["content"][1]["text"]


class TestFailFastExemptData:
    """The fail-fast exempt set the use case feeds FailFastHook (full mode)."""

    def test_contains_expected_exemptions(self, tmp_path) -> None:
        exempt = _use_case(tmp_path).fail_fast_exempt
        assert "rg_search" in exempt
        assert "context7_resolve-library-id" in exempt
        assert "context7_query-docs" in exempt
        assert "shell" in exempt

    def test_exempt_tool_does_not_raise(self, tmp_path) -> None:
        """An exempt tool's error result is non-fatal even in full mode."""
        import json

        uc = _use_case(tmp_path)
        hook = FailFastHook(uc.fail_fast_exempt)
        event = _AfterEvent("rg_search", json.dumps({"status": "error", "error": "no matches"}))
        hook._check_for_error(event)  # must not raise


class TestCreateAllHooksWithUseCase:
    """create_all_hooks assembles the roster from this use case's contract members."""

    def test_full_mode_adds_failfast(self, tmp_path) -> None:
        uc = _use_case(tmp_path)
        standard = create_all_hooks(uc, full_mode=False)
        full = create_all_hooks(uc, full_mode=True)
        assert not any(isinstance(h, FailFastHook) for h in standard)
        assert any(isinstance(h, FailFastHook) for h in full)


class _AfterEvent:
    """Minimal AfterToolCallEvent-like stand-in."""

    def __init__(self, tool_name: str, result: str | dict) -> None:
        self.tool_use = {"name": tool_name}
        self.result = result
