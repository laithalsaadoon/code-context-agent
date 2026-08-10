"""Tests for the five generic team tools (T-C1, AC-2-2..2-8, AC-8-1..8-4, AC-8-8).

No real Bedrock/network calls: the Swarm is monkeypatched with a stub, and tools
are invoked via their ``__wrapped__`` function with a hand-built ``ToolContext``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from strands import ToolContext, tool

from strands_teams import teams
from strands_teams.layout import TeamsLayout
from strands_teams.rubric import DEFAULT_NARRATIVE_RUBRIC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_context(layout: TeamsLayout, **extra: Any) -> ToolContext:
    """Build a ToolContext carrying a TeamInvocationState-shaped dict."""
    state: dict[str, Any] = {
        "layout": layout,
        "tool_registry": extra.pop("tool_registry", {}),
        "node_model_config": extra.pop(
            "node_model_config",
            {"model_id": "global.anthropic.claude-opus-4-6-v1", "region_name": "us-east-1"},
        ),
        "execution_timeout": extra.pop("execution_timeout", 300.0),
        "node_timeout": extra.pop("node_timeout", 120.0),
        "use_case_name": extra.pop("use_case_name", "test"),
        "rubric": extra.pop("rubric", DEFAULT_NARRATIVE_RUBRIC),
    }
    state.update(extra)
    return ToolContext(
        tool_use={"toolUseId": "t", "name": "x", "input": {}},
        agent=None,
        invocation_state=state,
    )


@tool
def _fake_reader(path: str) -> str:
    """A fake domain tool."""
    return path


class _StubStatus:
    value = "completed"


class _StubNode:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id


class _StubSwarmResult:
    def __init__(self, node_ids: list[str]) -> None:
        self.status = _StubStatus()
        self.node_history = [_StubNode(n) for n in node_ids]


class _StubSwarm:
    """Records the nodes/kwargs it was built with; returns a stub result."""

    last_instance: _StubSwarm | None = None

    def __init__(self, nodes: list[Any], **kwargs: Any) -> None:
        self.nodes = nodes
        self.kwargs = kwargs
        _StubSwarm.last_instance = self

    async def invoke_async(self, task: str, invocation_state: dict[str, Any] | None = None) -> _StubSwarmResult:
        self.task = task
        self.invocation_state = invocation_state
        return _StubSwarmResult([getattr(n, "name", "?") for n in self.nodes])


class _RaisingSwarm(_StubSwarm):
    async def invoke_async(self, task: str, invocation_state: dict[str, Any] | None = None) -> _StubSwarmResult:
        raise RuntimeError("boom in swarm")


class _TimeoutSwarm(_StubSwarm):
    async def invoke_async(self, task: str, invocation_state: dict[str, Any] | None = None) -> _StubSwarmResult:
        raise Exception("Node 'x' execution timed out after 120s")  # noqa: TRY002 — mirrors strands' raise


# ---------------------------------------------------------------------------
# dispatch_team
# ---------------------------------------------------------------------------
@pytest.fixture
def layout(tmp_path: Path) -> TeamsLayout:
    return TeamsLayout(output_root=tmp_path / ".teams")


async def test_dispatch_team_success_writes_findings_dir_and_debug(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    monkeypatch.setattr(teams, "Swarm", _StubSwarm)
    ctx = _make_context(layout, tool_registry={"_fake_reader": _fake_reader})

    result = json.loads(
        await teams.dispatch_team.__wrapped__(
            team_id="team-structure",
            mandate="Investigate the auth area.",
            agents=[{"name": "analyst", "system_prompt": "You analyze.", "tools": ["_fake_reader"]}],
            tool_context=ctx,
        )
    )

    assert result["status"] == "completed"
    assert result["team_id"] == "team-structure"
    # Team dir created; debug JSONL written on success (AC-2-2).
    assert layout.team_dir("team-structure").exists()
    debug_lines = layout.debug_log_path().read_text().splitlines()
    assert len(debug_lines) == 1
    entry = json.loads(debug_lines[0])
    assert entry["team_id"] == "team-structure"
    assert entry["status"] == "completed"
    assert entry["agents"] == ["analyst"]
    assert entry["agent_count"] == 1
    assert entry["mandate_preview"].startswith("Investigate")
    assert "duration_seconds" in entry
    assert "node_timeout" in entry


async def test_dispatch_team_auto_appends_write_file(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    monkeypatch.setattr(teams, "Swarm", _StubSwarm)
    ctx = _make_context(layout, tool_registry={"_fake_reader": _fake_reader})

    await teams.dispatch_team.__wrapped__(
        team_id="t1",
        mandate="m",
        agents=[{"name": "a", "system_prompt": "p", "tools": ["_fake_reader"]}],
        tool_context=ctx,
    )

    node = _StubSwarm.last_instance.nodes[0]
    tool_names = set(node.tool_names)
    assert "write_file" in tool_names  # AC-5-4
    assert "_fake_reader" in tool_names
    # Swarm ping-pong detection preserved (AC-8-3).
    assert _StubSwarm.last_instance.kwargs["repetitive_handoff_detection_window"] == 8
    assert _StubSwarm.last_instance.kwargs["repetitive_handoff_min_unique_agents"] == 3


async def test_dispatch_team_error_path_writes_debug_and_error(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    monkeypatch.setattr(teams, "Swarm", _RaisingSwarm)
    ctx = _make_context(layout)

    result = json.loads(
        await teams.dispatch_team.__wrapped__(
            team_id="t-err",
            mandate="m",
            agents=[{"name": "a", "system_prompt": "p", "tools": []}],
            tool_context=ctx,
        )
    )

    assert result["status"] == "error"  # AC-2-8
    assert "boom" in result["message"]
    # Debug entry still written on the raise path (AC-2-2).
    entry = json.loads(layout.debug_log_path().read_text().splitlines()[0])
    assert entry["status"] == "error"


async def test_dispatch_team_timeout_tagged(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    monkeypatch.setattr(teams, "Swarm", _TimeoutSwarm)
    ctx = _make_context(layout)

    result = json.loads(
        await teams.dispatch_team.__wrapped__(
            team_id="t-timeout",
            mandate="m",
            agents=[{"name": "a", "system_prompt": "p", "tools": []}],
            tool_context=ctx,
        )
    )
    assert result["status"] == "error"
    assert result["error_type"] == "timeout"  # AC-8-8
    entry = json.loads(layout.debug_log_path().read_text().splitlines()[0])
    assert entry["error_type"] == "timeout"


async def test_dispatch_team_augmented_task_embeds_context(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    monkeypatch.setattr(teams, "Swarm", _StubSwarm)
    ctx = _make_context(layout)

    await teams.dispatch_team.__wrapped__(
        team_id="t-ctx",
        mandate="Do the thing.",
        agents=[{"name": "a", "system_prompt": "p", "tools": []}],
        tool_context=ctx,
        file_scope=["src/auth.py"],
        key_questions=["How does login work?"],
        artifact_pointers=["bundles/BUNDLE.auth.md"],
    )
    task = _StubSwarm.last_instance.task
    # AC-2-3: mandate, file_scope, key_questions, artifact_pointers, and Required-Output paths.
    assert "Do the thing." in task
    assert "src/auth.py" in task
    assert "How does login work?" in task
    assert "bundles/BUNDLE.auth.md" in task
    assert str(layout.findings_path("t-ctx")) in task
    assert str(layout.metadata_path("t-ctx")) in task


async def test_dispatch_team_explicit_timeouts_override_defaults(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    monkeypatch.setattr(teams, "Swarm", _StubSwarm)
    ctx = _make_context(layout, execution_timeout=999.0, node_timeout=888.0)
    await teams.dispatch_team.__wrapped__(
        team_id="t",
        mandate="m",
        agents=[{"name": "a", "system_prompt": "p", "tools": []}],
        tool_context=ctx,
        execution_timeout=42.0,
        node_timeout=7.0,
    )
    assert _StubSwarm.last_instance.kwargs["execution_timeout"] == 42.0
    assert _StubSwarm.last_instance.kwargs["node_timeout"] == 7.0


# ---------------------------------------------------------------------------
# read_team_findings
# ---------------------------------------------------------------------------
def _write_team(layout: TeamsLayout, team_id: str, findings: str | None, meta: dict[str, Any] | None) -> None:
    layout.team_dir(team_id).mkdir(parents=True, exist_ok=True)
    if findings is not None:
        layout.findings_path(team_id).write_text(findings)
    if meta is not None:
        layout.metadata_path(team_id).write_text(json.dumps(meta))


def test_read_team_findings_list(layout: TeamsLayout) -> None:
    _write_team(layout, "team-a", "# A\nline\n", {"status": "done"})
    _write_team(layout, "team-b", None, None)
    ctx = _make_context(layout)

    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx))
    assert result["status"] == "ok"
    by_id = {t["team_id"]: t for t in result["teams"]}
    assert by_id["team-a"]["has_findings"] is True
    assert by_id["team-a"]["findings_lines"] == 2
    assert by_id["team-a"]["metadata"] == {"status": "done"}
    assert by_id["team-b"]["has_findings"] is False


def test_read_team_findings_single(layout: TeamsLayout) -> None:
    _write_team(layout, "team-a", "# A\ncontent here\n", None)
    ctx = _make_context(layout)
    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="team-a"))
    assert result["status"] == "ok"
    assert "content here" in result["findings"]
    assert result["line_count"] == 2


def test_read_team_findings_no_findings(layout: TeamsLayout) -> None:
    _write_team(layout, "team-c", None, {"status": "running"})
    ctx = _make_context(layout)
    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="team-c"))
    assert result["status"] == "no_findings"


def test_read_team_findings_missing_team(layout: TeamsLayout) -> None:
    ctx = _make_context(layout)
    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="ghost"))
    assert result["status"] == "error"


def test_read_team_findings_no_teams(layout: TeamsLayout) -> None:
    ctx = _make_context(layout)
    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx))
    assert result["status"] == "no_teams"


# ---------------------------------------------------------------------------
# write_artifact
# ---------------------------------------------------------------------------
def test_write_artifact_area(layout: TeamsLayout) -> None:
    ctx = _make_context(layout)
    result = json.loads(teams.write_artifact.__wrapped__(area="auth", content="# Auth\nl1\nl2\n", tool_context=ctx))
    assert result["status"] == "ok"
    assert Path(result["path"]) == layout.artifact_path("auth")
    assert result["relative_path"] == "bundles/BUNDLE.auth.md"
    assert result["line_count"] == 3
    assert layout.artifact_path("auth").read_text().startswith("# Auth")


def test_write_artifact_executive_summary(layout: TeamsLayout) -> None:
    ctx = _make_context(layout)
    result = json.loads(
        teams.write_artifact.__wrapped__(
            area="ignored", content="# Summary\n", tool_context=ctx, is_executive_summary=True
        )
    )
    assert result["status"] == "ok"
    assert Path(result["path"]) == layout.executive_summary_path()
    assert result["relative_path"] == "CONTEXT.md"


# ---------------------------------------------------------------------------
# score_narrative + enrich_artifact
# ---------------------------------------------------------------------------
def test_score_narrative_needs_revision_below_threshold(layout: TeamsLayout) -> None:
    layout.artifacts_dir().mkdir(parents=True, exist_ok=True)
    layout.artifact_path("weak").write_text("just a sentence, nothing structured.")
    ctx = _make_context(layout)
    result = json.loads(teams.score_narrative.__wrapped__(area="weak", tool_context=ctx))
    assert result["status"] == "ok"
    assert result["total"] < 3.5
    assert result["needs_revision"] is True  # AC-2-6
    assert result["suggestions"]
    assert set(result["scores"]) == {"specificity", "structure", "diagrams", "depth", "cross_references"}


def test_score_narrative_uses_injected_rubric(layout: TeamsLayout) -> None:
    from strands_teams.rubric import NarrativeRubric, RubricDimension

    # A rubric that always scores 5 (never needs revision) proves injection is used.
    always5 = NarrativeRubric(
        dimensions=(RubricDimension(name="len", counter=lambda _t: 100, thresholds=((0, 5.0),), suggestion="x"),),
    )
    layout.artifacts_dir().mkdir(parents=True, exist_ok=True)
    layout.artifact_path("a").write_text("short")
    ctx = _make_context(layout, rubric=always5)
    result = json.loads(teams.score_narrative.__wrapped__(area="a", tool_context=ctx))
    assert result["total"] == 5.0
    assert result["needs_revision"] is False


def test_score_narrative_missing_artifact(layout: TeamsLayout) -> None:
    ctx = _make_context(layout)
    result = json.loads(teams.score_narrative.__wrapped__(area="ghost", tool_context=ctx))
    assert result["status"] == "error"


def test_enrich_artifact_returns_content_and_feedback(layout: TeamsLayout) -> None:
    layout.artifacts_dir().mkdir(parents=True, exist_ok=True)
    layout.artifact_path("auth").write_text("# Auth\nexisting\n")
    ctx = _make_context(layout)
    result = json.loads(teams.enrich_artifact.__wrapped__(area="auth", feedback="add diagrams", tool_context=ctx))
    assert result["status"] == "ok"
    assert "existing" in result["current_content"]
    assert result["feedback"] == "add diagrams"
    assert "write_artifact" in result["instruction"]


def test_enrich_artifact_missing(layout: TeamsLayout) -> None:
    ctx = _make_context(layout)
    result = json.loads(teams.enrich_artifact.__wrapped__(area="ghost", feedback="x", tool_context=ctx))
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# invocation_state / no module-level mutable config
# ---------------------------------------------------------------------------
def test_missing_layout_in_state_raises() -> None:
    ctx = ToolContext(tool_use={"toolUseId": "t", "name": "x", "input": {}}, agent=None, invocation_state={})
    with pytest.raises(RuntimeError, match="layout"):
        teams.read_team_findings.__wrapped__(tool_context=ctx)


# ---------------------------------------------------------------------------
# Typed findings — write path + read_team_findings round-trip seam (AC-7-3, AC-7-4)
# ---------------------------------------------------------------------------
def _seed_typed_findings(layout: TeamsLayout, team_id: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Write typed findings via the real write_findings tool (the write path seam)."""
    from strands_teams.write_file import make_write_findings

    write_findings = make_write_findings(layout, team_id)
    layout.team_dir(team_id).mkdir(parents=True, exist_ok=True)
    return json.loads(write_findings.__wrapped__(findings=findings))


def test_typed_findings_round_trip_via_write_and_read(layout: TeamsLayout) -> None:
    # AC-7-3: write real Finding objects through the write path, then read them
    # back typed via read_team_findings; parsed == written, and the evidence gate
    # flags a seeded blocking-without-evidence finding.
    seeded = [
        {
            "statement": "Blocking issue with no evidence",
            "severity": "critical",
            "blocking": True,
            "created_by": "team-a",
        },
        {
            "statement": "Evidenced high finding",
            "severity": "high",
            "blocking": True,
            "evidence": [{"type": "file_line", "uri": "src/auth.py:42"}],
            "confidence": 0.8,
            "created_by": "team-a",
        },
        {"statement": "Just an info note", "severity": "info", "created_by": "team-a"},
    ]
    write_result = _seed_typed_findings(layout, "team-a", seeded)
    assert write_result["status"] == "success"
    assert write_result["count"] == 3
    # write-path evidence gate already flags the blocking-without-evidence one (AC-5-2).
    assert write_result["evidence_gate"]["passed"] is False
    assert write_result["evidence_gate"]["flagged"] == ["Blocking issue with no evidence"]

    ctx = _make_context(layout)
    read = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="team-a"))
    assert read["status"] == "ok"
    assert read["source"] == "typed"
    assert read["finding_count"] == 3
    statements = [f["statement"] for f in read["findings"]]
    assert statements == [s["statement"] for s in seeded]
    # AC-5-3: read-path evidence gate summary flags the blocking-without-evidence finding.
    assert read["evidence_gate"]["passed"] is False
    assert read["evidence_gate"]["flagged"] == ["Blocking issue with no evidence"]


def test_typed_read_also_surfaces_markdown_narrative(layout: TeamsLayout) -> None:
    # AC-6-2: on the happy path (both findings.json and findings.md present) the
    # typed read must ALSO carry the rich markdown narrative + findings_path so
    # bundle synthesis keeps its markdown input rather than terse typed statements.
    md_body = "# Findings\n\n```mermaid\ngraph TD; A-->B\n```\n\nSee `src/auth.py:42` for detail.\n"
    _seed_typed_findings(layout, "team-a", [{"statement": "note", "severity": "low", "created_by": "team-a"}])
    layout.findings_path("team-a").write_text(md_body)
    ctx = _make_context(layout)

    read = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="team-a"))
    assert read["source"] == "typed"
    assert read["findings_path"] == str(layout.findings_path("team-a"))
    assert read["markdown"] == md_body
    assert read["markdown_line_count"] == len(md_body.splitlines())


def test_typed_read_reports_findings_path_when_markdown_absent(layout: TeamsLayout) -> None:
    # AC-6-2: even with no findings.md, the typed read surfaces findings_path so the
    # coordinator can discover (and later read) the narrative; no markdown key is set.
    _seed_typed_findings(layout, "team-a", [{"statement": "note", "severity": "low", "created_by": "team-a"}])
    ctx = _make_context(layout)

    read = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="team-a"))
    assert read["source"] == "typed"
    assert read["findings_path"] == str(layout.findings_path("team-a"))
    assert "markdown" not in read


def test_write_findings_rejects_invalid_schema(layout: TeamsLayout) -> None:
    # AC-3-4: schema-invalid content returns error and does not persist findings.json.
    result = _seed_typed_findings(layout, "team-bad", [{"statement": "x", "severity": "not-a-severity"}])
    assert result["status"] == "error"
    assert not layout.findings_json_path("team-bad").exists()


def test_read_falls_back_to_markdown_when_no_json(layout: TeamsLayout) -> None:
    # AC-4-2 / AC-7-4: only findings.md present -> markdown fallback marked as such.
    _write_team(layout, "team-md", "# Findings\ndetail here\n", {"status": "done"})
    ctx = _make_context(layout)
    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="team-md"))
    assert result["status"] == "ok"
    assert result["source"] == "markdown"
    assert "detail here" in result["findings"]


def test_read_falls_back_on_corrupt_json(layout: TeamsLayout) -> None:
    # AC-4-4 / AC-7-4: corrupt findings.json -> degrade to markdown, never raise.
    _write_team(layout, "team-corrupt", "# Findings\nmd body\n", None)
    layout.findings_json_path("team-corrupt").write_text("{ this is not valid json ]")
    ctx = _make_context(layout)
    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx, team_id="team-corrupt"))
    assert result["status"] == "ok"
    assert result["source"] == "markdown"
    assert "md body" in result["findings"]


def test_read_list_reports_typed_presence(layout: TeamsLayout) -> None:
    # AC-4-3: no-team_id listing reports typed presence + count without dropping keys.
    _seed_typed_findings(layout, "team-typed", [{"statement": "note", "severity": "low"}])
    layout.findings_path("team-typed").write_text("# md\n")
    _write_team(layout, "team-plain", "# only md\n", {"status": "done"})
    ctx = _make_context(layout)
    result = json.loads(teams.read_team_findings.__wrapped__(tool_context=ctx))
    by_id = {t["team_id"]: t for t in result["teams"]}
    assert by_id["team-typed"]["has_typed_findings"] is True
    assert by_id["team-typed"]["typed_finding_count"] == 1
    assert by_id["team-typed"]["has_findings"] is True  # existing key preserved
    assert by_id["team-plain"]["has_typed_findings"] is False
    assert by_id["team-plain"]["typed_finding_count"] == 0
    assert "findings_lines" in by_id["team-plain"]  # existing key preserved
