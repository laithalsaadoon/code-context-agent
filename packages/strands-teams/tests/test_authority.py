"""Tests for Phase P2-authority — task-contract authority + budget (spec 003).

Covers the model contracts (US1), dispatch_team enrichment + parsing (US2),
structural blocking-finding refusal (US3), acceptance-criteria coverage in
ArtifactQualityHook (US4), invocation_state threading + isolation (US5), and the
US6 acceptance tests. No real Bedrock/network: the Swarm is monkeypatched with a
stub and the write_findings write path is exercised directly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError
from strands import ToolContext, tool

from strands_teams import AuthorityGrant, Budget
from strands_teams import teams as teams_mod
from strands_teams.hooks import ArtifactQualityHook
from strands_teams.layout import TeamsLayout
from strands_teams.rubric import DEFAULT_NARRATIVE_RUBRIC
from strands_teams.write_file import make_write_findings

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Test doubles + helpers
# ---------------------------------------------------------------------------
class _StubStatus:
    value = "completed"


class _StubNode:
    def __init__(self, name: str) -> None:
        self.name = name


class _StubSwarmResult:
    def __init__(self, names: list[str]) -> None:
        self.status = _StubStatus()
        self.node_history = [_StubNode(n) for n in names]


class _StubSwarm:
    """Records the invocation_state it received; returns a stub result.

    Optionally seeds the team's typed findings.json when constructed so the
    post-swarm authority enforcement has findings to act on — mimicking a team
    that persisted findings via write_findings during the (stubbed) run.
    """

    last_instance: _StubSwarm | None = None
    seed_findings: list[dict[str, Any]] | None = None
    seed_layout: TeamsLayout | None = None
    seed_team_id: str | None = None

    def __init__(self, nodes: list[Any], **kwargs: Any) -> None:
        self.nodes = nodes
        self.kwargs = kwargs
        _StubSwarm.last_instance = self

    async def invoke_async(self, task: str, invocation_state: dict[str, Any] | None = None) -> _StubSwarmResult:
        self.task = task
        self.invocation_state = invocation_state
        if _StubSwarm.seed_findings is not None and _StubSwarm.seed_layout is not None:
            writer = make_write_findings(_StubSwarm.seed_layout, _StubSwarm.seed_team_id or "t")
            _StubSwarm.seed_layout.team_dir(_StubSwarm.seed_team_id or "t").mkdir(parents=True, exist_ok=True)
            writer.__wrapped__(findings=_StubSwarm.seed_findings)
        return _StubSwarmResult([getattr(n, "name", "?") for n in self.nodes])


def _make_context(layout: TeamsLayout, **extra: Any) -> ToolContext:
    state: dict[str, Any] = {
        "layout": layout,
        "tool_registry": extra.pop("tool_registry", {}),
        "node_model_config": {"model_id": "m", "region_name": "us-east-1"},
        "execution_timeout": extra.pop("execution_timeout", 300.0),
        "node_timeout": extra.pop("node_timeout", 120.0),
        "use_case_name": "test",
        "rubric": DEFAULT_NARRATIVE_RUBRIC,
    }
    state.update(extra)
    return ToolContext(
        tool_use={"toolUseId": "t", "name": "dispatch_team", "input": {}},
        agent=None,
        invocation_state=state,
    )


@tool
def _fake_reader(path: str) -> str:
    """A fake domain tool."""
    return path


@pytest.fixture
def layout(tmp_path: Path) -> TeamsLayout:
    return TeamsLayout(output_root=tmp_path / ".teams")


@pytest.fixture(autouse=True)
def _reset_stub() -> Any:
    _StubSwarm.seed_findings = None
    _StubSwarm.seed_layout = None
    _StubSwarm.seed_team_id = None
    yield
    _StubSwarm.seed_findings = None
    _StubSwarm.seed_layout = None
    _StubSwarm.seed_team_id = None


async def _dispatch(ctx: ToolContext, team_id: str = "t", **kwargs: Any) -> dict[str, Any]:
    result = await teams_mod.dispatch_team.__wrapped__(
        team_id=team_id,
        mandate=kwargs.pop("mandate", "Investigate."),
        agents=kwargs.pop("agents", [{"name": "a", "system_prompt": "p", "tools": []}]),
        tool_context=ctx,
        **kwargs,
    )
    return json.loads(result)


# ---------------------------------------------------------------------------
# US1 — AuthorityGrant + Budget models
# ---------------------------------------------------------------------------
def test_authority_grant_defaults_all_false() -> None:  # AC-1-1, AC-1-2
    grant = AuthorityGrant()
    assert grant.may_create_blocking_finding is False
    assert grant.may_write is False
    assert grant.may_approve is False


def test_authority_grant_is_frozen() -> None:
    grant = AuthorityGrant(may_write=True)
    assert grant.may_write is True
    with pytest.raises(ValidationError):
        grant.may_write = False  # type: ignore[misc]


def test_budget_fields_and_defaults() -> None:  # AC-1-3
    budget = Budget()
    assert budget.max_wall_clock_minutes == 0.0
    assert budget.max_cost_usd == 0.0
    assert budget.max_retries == 0
    full = Budget(max_wall_clock_minutes=5.0, max_cost_usd=2.5, max_retries=3)
    assert full.max_wall_clock_minutes == 5.0
    assert full.max_cost_usd == 2.5
    assert full.max_retries == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_wall_clock_minutes": -1.0},
        {"max_cost_usd": -0.01},
        {"max_retries": -1},
    ],
)
def test_budget_rejects_negative(kwargs: dict[str, Any]) -> None:  # AC-1-4
    with pytest.raises(ValidationError):
        Budget(**kwargs)


def test_models_exported_from_package() -> None:  # AC-1-5
    import strands_teams

    assert strands_teams.AuthorityGrant is AuthorityGrant
    assert strands_teams.Budget is Budget
    assert "AuthorityGrant" in strands_teams.__all__
    assert "Budget" in strands_teams.__all__


# ---------------------------------------------------------------------------
# US2 — dispatch_team contract enrichment
# ---------------------------------------------------------------------------
async def test_dispatch_parses_dicts_and_threads_state(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-2-1, AC-2-2, AC-2-3, AC-6-5: dicts parsed to models, threaded into a NEW
    # invocation_state without mutating the coordinator's shared state.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout)
    input_state = ctx.invocation_state
    input_state_id = id(input_state)

    await _dispatch(
        ctx,
        team_id="t-parse",
        authority={"may_create_blocking_finding": True, "may_write": True},
        budget={"max_wall_clock_minutes": 10, "max_cost_usd": 3.0, "max_retries": 2},
        acceptance_criteria=["Login uses bcrypt"],
        input_revisions=["rev-abc"],
    )

    inv = _StubSwarm.last_instance.invocation_state
    assert isinstance(inv["authority"], AuthorityGrant)
    assert inv["authority"].may_write is True
    assert isinstance(inv["budget"], Budget)
    assert inv["budget"].max_cost_usd == 3.0
    assert inv["acceptance_criteria"] == ["Login uses bcrypt"]
    assert inv["input_revisions"] == ["rev-abc"]
    # New dict, coordinator state untouched (AC-5-1).
    assert inv is not input_state
    assert id(ctx.invocation_state) == input_state_id
    assert "authority" not in input_state
    assert "budget" not in input_state


async def test_dispatch_surfaces_criteria_and_revisions_in_task(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    # AC-2-4: acceptance_criteria + input_revisions appear in the augmented task.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout)
    await _dispatch(
        ctx,
        team_id="t-task",
        acceptance_criteria=["Sessions expire after 30 minutes"],
        input_revisions=["rev-xyz-123"],
    )
    task = _StubSwarm.last_instance.task
    assert "Sessions expire after 30 minutes" in task
    assert "rev-xyz-123" in task


async def test_budget_wall_clock_drives_execution_timeout(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-2-5: positive max_wall_clock_minutes * 60 overrides the run default.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout, execution_timeout=300.0)
    await _dispatch(ctx, team_id="t-budget", budget={"max_wall_clock_minutes": 5})
    assert _StubSwarm.last_instance.kwargs["execution_timeout"] == 300.0  # 5 * 60


async def test_explicit_timeout_beats_budget(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-2-5 boundary: an explicit execution_timeout arg still wins over the budget.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout, execution_timeout=300.0)
    await _dispatch(ctx, team_id="t-b2", budget={"max_wall_clock_minutes": 5}, execution_timeout=42.0)
    assert _StubSwarm.last_instance.kwargs["execution_timeout"] == 42.0


async def test_dispatch_persists_contract_artifact(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-2-6: acceptance_criteria persisted to the per-team contract path.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout)
    await _dispatch(
        ctx,
        team_id="t-contract",
        acceptance_criteria=["Password hashing uses bcrypt"],
        authority={"may_write": True},
        budget={"max_cost_usd": 1.0},
    )
    contract_path = layout.contract_path("t-contract")
    assert contract_path.exists()
    data = json.loads(contract_path.read_text())
    assert data["acceptance_criteria"] == ["Password hashing uses bcrypt"]
    assert data["authority"]["may_write"] is True
    assert data["budget"]["max_cost_usd"] == 1.0


async def test_input_revisions_opaque_no_raise(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-2-7: an unresolved revision id is treated as an opaque string, never raises.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout)
    result = await _dispatch(ctx, team_id="t-rev", input_revisions=["not-a-real-revision-id"])
    assert result["status"] == "completed"
    assert _StubSwarm.last_instance.invocation_state["input_revisions"] == ["not-a-real-revision-id"]


# ---------------------------------------------------------------------------
# US3 — Structural blocking-finding refusal
# ---------------------------------------------------------------------------
async def test_forbidden_blocking_finding_rejected(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-3-1, AC-3-2, AC-3-3, AC-6-1: grant forbids blocking; a blocking finding is
    # rejected (status=error, message names it) and stripped from the artifact.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    _StubSwarm.seed_layout = layout
    _StubSwarm.seed_team_id = "t-forbid"
    _StubSwarm.seed_findings = [
        {
            "statement": "Critical SQL injection in login",
            "severity": "critical",
            "blocking": True,
            "evidence": [{"type": "file_line", "uri": "src/auth.py:42"}],
            "created_by": "t-forbid",
        },
        {"statement": "Style nit", "severity": "info", "blocking": False, "created_by": "t-forbid"},
    ]
    ctx = _make_context(layout)

    result = await _dispatch(ctx, team_id="t-forbid", authority={"may_create_blocking_finding": False})

    assert result["status"] == "error"
    assert result["error_type"] == "authority_violation"
    assert "Critical SQL injection in login" in result["message"]
    # AC-3-3: the blocking finding is stripped; only the non-blocking one survives.
    from strands_teams.findings_io import load_findings

    surviving = load_findings(layout.findings_json_path("t-forbid"))
    assert [f.statement for f in surviving] == ["Style nit"]
    assert all(not f.blocking for f in surviving)


class _CrashingSwarm(_StubSwarm):
    """Seeds the team's findings, then raises — mimics a node persisting a forbidden
    blocking finding before a later node times out / the swarm aborts."""

    async def invoke_async(self, task: str, invocation_state: dict[str, Any] | None = None) -> _StubSwarmResult:
        if _StubSwarm.seed_findings is not None and _StubSwarm.seed_layout is not None:
            writer = make_write_findings(_StubSwarm.seed_layout, _StubSwarm.seed_team_id or "t")
            _StubSwarm.seed_layout.team_dir(_StubSwarm.seed_team_id or "t").mkdir(parents=True, exist_ok=True)
            writer.__wrapped__(findings=_StubSwarm.seed_findings)
        raise TimeoutError("node timed out")


async def test_forbidden_blocking_finding_stripped_even_when_swarm_crashes(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    # AC-3-1, AC-3-3: enforcement runs on EVERY path — a forbidden blocking finding
    # persisted before a swarm timeout must NOT survive the run, and the dispatch
    # surfaces the authority violation (not a bare timeout).
    monkeypatch.setattr(teams_mod, "Swarm", _CrashingSwarm)
    _StubSwarm.seed_layout = layout
    _StubSwarm.seed_team_id = "t-crash"
    _StubSwarm.seed_findings = [
        {
            "statement": "Blocking finding persisted then crash",
            "severity": "critical",
            "blocking": True,
            "evidence": [{"type": "file_line", "uri": "src/auth.py:42"}],
            "created_by": "t-crash",
        },
    ]
    ctx = _make_context(layout)

    result = await _dispatch(ctx, team_id="t-crash", authority={"may_create_blocking_finding": False})

    assert result["status"] == "error"
    assert result["error_type"] == "authority_violation"
    from strands_teams.findings_io import load_findings

    surviving = load_findings(layout.findings_json_path("t-crash"))
    assert surviving == []  # the forbidden blocking finding did not survive


async def test_malformed_contract_returns_error_envelope(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-2-2/AC-2-3: a malformed authority dict yields the tool's standard error
    # envelope (not an escaping ValidationError) and still records a debug entry.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout)

    result = await _dispatch(ctx, team_id="t-bad", authority={"may_create_blocking_finding": "not-a-bool-value"})

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_contract"
    debug = layout.debug_log_path()
    assert debug.exists()
    assert "invalid_contract" in debug.read_text()


async def test_granted_blocking_finding_survives(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-3-5, AC-6-2: grant permits blocking; the blocking finding survives unchanged.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    _StubSwarm.seed_layout = layout
    _StubSwarm.seed_team_id = "t-allow"
    _StubSwarm.seed_findings = [
        {
            "statement": "Blocking risk with evidence",
            "severity": "high",
            "blocking": True,
            "evidence": [{"type": "file_line", "uri": "src/x.py:1"}],
            "created_by": "t-allow",
        },
    ]
    ctx = _make_context(layout)

    result = await _dispatch(ctx, team_id="t-allow", authority={"may_create_blocking_finding": True})

    assert result["status"] == "completed"
    from strands_teams.findings_io import load_findings

    surviving = load_findings(layout.findings_json_path("t-allow"))
    assert len(surviving) == 1
    assert surviving[0].blocking is True


async def test_omitted_authority_permits_blocking(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-3-6, AC-6-6: omitting authority applies a default-permissive grant, so a
    # blocking finding survives and the dispatch completes (pre-authority behavior).
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    _StubSwarm.seed_layout = layout
    _StubSwarm.seed_team_id = "t-legacy"
    _StubSwarm.seed_findings = [
        {
            "statement": "Legacy blocking finding",
            "severity": "high",
            "blocking": True,
            "evidence": [{"type": "file_line", "uri": "src/y.py:9"}],
            "created_by": "t-legacy",
        },
    ]
    ctx = _make_context(layout)

    result = await _dispatch(ctx, team_id="t-legacy")  # no authority arg

    assert result["status"] == "completed"
    inv = _StubSwarm.last_instance.invocation_state
    assert inv["authority"].may_create_blocking_finding is True
    from strands_teams.findings_io import load_findings

    surviving = load_findings(layout.findings_json_path("t-legacy"))
    assert len(surviving) == 1


async def test_enforcement_independent_of_agent_prompt(monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout) -> None:
    # AC-3-4: enforcement is in dispatch_team code — a team whose agent prompt never
    # mentions authority still has its forbidden blocking finding stripped.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    _StubSwarm.seed_layout = layout
    _StubSwarm.seed_team_id = "t-code"
    _StubSwarm.seed_findings = [
        {"statement": "Blocking one", "severity": "high", "blocking": True, "created_by": "t-code"},
    ]
    ctx = _make_context(layout)
    result = await _dispatch(
        ctx,
        team_id="t-code",
        agents=[{"name": "a", "system_prompt": "Just analyze the code.", "tools": []}],
        authority={"may_create_blocking_finding": False},
    )
    assert result["status"] == "error"
    from strands_teams.findings_io import load_findings

    assert load_findings(layout.findings_json_path("t-code")) == []


# ---------------------------------------------------------------------------
# US4 — Acceptance-criteria coverage in ArtifactQualityHook
# ---------------------------------------------------------------------------
class _FakeInvocationEvent:
    resume: Any = None


def _write_contract(layout: TeamsLayout, team_id: str, criteria: list[str]) -> None:
    layout.team_dir(team_id).mkdir(parents=True, exist_ok=True)
    layout.contract_path(team_id).write_text(json.dumps({"team_id": team_id, "acceptance_criteria": criteria}))


def _write_rich_artifact(layout: TeamsLayout, area: str, extra: str = "") -> None:
    layout.artifacts_dir().mkdir(parents=True, exist_ok=True)
    rich = "\n".join(
        [f"## Section {i}" for i in range(12)]
        + ["```mermaid", "graph LR", "A --> B", "```", "```mermaid", "graph TD", "X --> Y", "```"]
        + [f"module_{i}.py:{i} references src/pkg/mod_{i}.py in detail." for i in range(40)]
        + [f"Detailed analysis line {i} covering implementation specifics." for i in range(200)]
        + [extra]
    )
    layout.artifact_path(area).write_text(rich)


def test_uncovered_criterion_forces_resume(layout: TeamsLayout) -> None:
    # AC-4-2, AC-4-3, AC-6-3: a rich (rubric-passing) artifact still triggers a resume
    # when a criterion is uncovered, and the resume names the uncovered criterion.
    _write_rich_artifact(layout, "auth")  # rubric passes
    _write_contract(layout, "team-auth", ["Kerberos delegation across trusted realms is validated"])
    hook = ArtifactQualityHook(layout, DEFAULT_NARRATIVE_RUBRIC)
    event = _FakeInvocationEvent()

    hook._check_artifact_quality(event)

    assert event.resume is not None
    assert "COVERAGE PASS 1" in event.resume
    assert "Kerberos delegation across trusted realms is validated" in event.resume
    assert hook._pass_count == 1


def test_covered_criteria_and_passing_rubric_no_resume(layout: TeamsLayout) -> None:
    # AC-4-4, AC-6-4: all criteria covered + rubric passing -> no resume.
    _write_rich_artifact(layout, "auth", extra="Kerberos delegation across trusted realms is validated here.")
    _write_contract(layout, "team-auth", ["Kerberos delegation across trusted realms is validated"])
    hook = ArtifactQualityHook(layout, DEFAULT_NARRATIVE_RUBRIC)
    event = _FakeInvocationEvent()

    hook._check_artifact_quality(event)

    assert event.resume is None
    assert hook._pass_count == 0


def test_criterion_covered_by_findings_corpus(layout: TeamsLayout) -> None:
    # AC-4-1: coverage corpus includes team findings, not only artifacts.
    _write_rich_artifact(layout, "auth")
    _write_contract(layout, "team-auth", ["Kerberos delegation across trusted realms is validated"])
    layout.findings_path("team-auth").write_text("We validated Kerberos delegation across trusted realms in detail.")
    hook = ArtifactQualityHook(layout, DEFAULT_NARRATIVE_RUBRIC)
    event = _FakeInvocationEvent()

    hook._check_artifact_quality(event)

    assert event.resume is None


def test_no_criteria_falls_back_to_rubric_only(layout: TeamsLayout) -> None:
    # AC-4-5, AC-6-6: no criteria -> rubric-only behavior unchanged (weak -> enrich).
    layout.artifacts_dir().mkdir(parents=True, exist_ok=True)
    layout.artifact_path("weak").write_text("Thin content.\nNo refs.")
    hook = ArtifactQualityHook(layout, DEFAULT_NARRATIVE_RUBRIC)
    event = _FakeInvocationEvent()

    hook._check_artifact_quality(event)

    assert event.resume is not None
    assert "ENRICHMENT PASS 1" in event.resume
    assert "weak" in event.resume


def test_no_criteria_rich_artifact_no_resume(layout: TeamsLayout) -> None:
    # AC-4-5: no criteria + rubric passing -> no resume (pure rubric-only).
    _write_rich_artifact(layout, "core")
    hook = ArtifactQualityHook(layout, DEFAULT_NARRATIVE_RUBRIC)
    event = _FakeInvocationEvent()
    hook._check_artifact_quality(event)
    assert event.resume is None


def test_max_passes_caps_combined_triggers(layout: TeamsLayout) -> None:
    # AC-4-6: the single budget caps rubric + coverage resumes combined.
    _write_rich_artifact(layout, "auth")
    _write_contract(layout, "team-auth", ["Some entirely unrelated uncovered requirement phrase"])
    hook = ArtifactQualityHook(layout, DEFAULT_NARRATIVE_RUBRIC, max_passes=2)

    ev1 = _FakeInvocationEvent()
    hook._check_artifact_quality(ev1)
    assert ev1.resume is not None
    assert hook._pass_count == 1

    ev2 = _FakeInvocationEvent()
    hook._check_artifact_quality(ev2)
    assert ev2.resume is not None
    assert hook._pass_count == 2

    ev3 = _FakeInvocationEvent()
    hook._check_artifact_quality(ev3)
    assert ev3.resume is None
    assert hook._pass_count == 2


# ---------------------------------------------------------------------------
# US5 — invocation_state threading + native-Swarm + isolation invariants
# ---------------------------------------------------------------------------
async def test_native_swarm_and_handoff_detection_preserved(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    # AC-5-2: dispatch still constructs a native Swarm via invoke_async with the
    # ping-pong detection window 8 / min-unique 3 preserved.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout)
    await _dispatch(ctx, team_id="t-native", authority={"may_write": True})
    assert _StubSwarm.last_instance.kwargs["repetitive_handoff_detection_window"] == 8
    assert _StubSwarm.last_instance.kwargs["repetitive_handoff_min_unique_agents"] == 3
    assert _StubSwarm.last_instance.invocation_state is not None


async def test_invocation_state_carries_live_objects_no_session_manager(
    monkeypatch: pytest.MonkeyPatch, layout: TeamsLayout
) -> None:
    # AC-5-4: live AuthorityGrant/Budget objects, no session_manager attached.
    monkeypatch.setattr(teams_mod, "Swarm", _StubSwarm)
    ctx = _make_context(layout)
    await _dispatch(ctx, team_id="t-live", authority={"may_write": True}, budget={"max_cost_usd": 2.0})
    inv = _StubSwarm.last_instance.invocation_state
    assert isinstance(inv["authority"], AuthorityGrant)
    assert isinstance(inv["budget"], Budget)
    assert "session_manager" not in inv
