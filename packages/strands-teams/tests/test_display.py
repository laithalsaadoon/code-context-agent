"""Tests for the parameterized Rich TUI display core.

Covers AC-4-1..AC-4-5: injected phase count / title / phase-name map /
discovery-extractor, coordinator-vs-single-agent layout switching, the
``[i/N]`` phase indicator drawn from the injected spec, extractor
present/absent feed behavior, and malformed-input safety.

All tests use a fake 3-phase ``PhaseSpec`` so no ``/5`` or product name leaks.
"""

from __future__ import annotations

import json
import time

import pytest
from rich.console import Console

from strands_teams.display import (
    AgentDisplayState,
    DiscoveryEvent,
    QuietConsumer,
    RichEventConsumer,
)
from strands_teams.phases import PhaseSpec

# ── Fixtures ─────────────────────────────────────────────────────────


def _fake_spec() -> PhaseSpec:
    """A 3-phase spec with no codebase vocabulary."""
    return PhaseSpec(
        names={1: "Alpha", 2: "Beta", 3: "Gamma"},
        descriptions={1: "first", 2: "second", 3: "third"},
        tool_phase_map={"tool_alpha": 1, "tool_beta": 2, "tool_gamma": 3},
        prefix_rules={"ext_": 2},
    )


def _state() -> AgentDisplayState:
    return AgentDisplayState(phase_spec=_fake_spec())


def _quiet_console() -> Console:
    """A non-terminal console so Live/rendering is inert in tests."""
    return Console(file=open("/dev/null", "w"), force_terminal=False)  # noqa: PTH123


def _consumer(state: AgentDisplayState, **kwargs: object) -> RichEventConsumer:
    kwargs.setdefault("console", _quiet_console())
    return RichEventConsumer(state, **kwargs)  # type: ignore[arg-type]


def _render(consumer: RichEventConsumer) -> str:
    """Render the current display panel to plain text."""
    console = Console(file=open("/dev/null", "w"), record=True, width=120)  # noqa: SIM115, PTH123
    console.print(consumer._build_display())
    return console.export_text()


# ── AC-4-1: injection, no hardcoded strings ─────────────────────────


def test_phase_count_comes_from_spec() -> None:
    assert _state().phase_spec.count == 3


def test_title_is_injected() -> None:
    state = _state()
    consumer = _consumer(state, title="My Runner")
    state.advance_phase(1)
    out = _render(consumer)
    assert "My Runner" in out
    assert "Code Context Agent" not in out


def test_mode_label_appended_to_title() -> None:
    state = _state()
    consumer = _consumer(state, title="My Runner", mode_label="FULL")
    assert consumer._panel_title() == "My Runner [FULL]"


def test_no_mode_label_keeps_bare_title() -> None:
    consumer = _consumer(_state(), title="My Runner")
    assert consumer._panel_title() == "My Runner"


# ── AC-4-3: phase indicator [i/N] from injected spec ────────────────


def test_phase_indicator_uses_injected_count_and_names() -> None:
    state = _state()
    consumer = _consumer(state, title="R")
    state.advance_phase(2)
    out = _render(consumer)
    assert "[2/3]" in out
    assert "Beta" in out
    assert "/5" not in out


def test_phase_indicator_absent_before_any_phase() -> None:
    consumer = _consumer(_state(), title="R")
    assert consumer._build_phase_indicator() is None


def test_detect_phase_resolves_via_spec_prefix_rule() -> None:
    state = _state()
    consumer = _consumer(state, title="R")
    consumer._detect_phase("ext_something")  # prefix rule -> phase 2
    assert state.phases[-1].phase == 2
    assert state.phases[-1].name == "Beta"


def test_detect_phase_ignores_unknown_tool() -> None:
    state = _state()
    consumer = _consumer(state, title="R")
    consumer._detect_phase("totally_unknown_tool")
    assert state.phases == []


# ── AC-4-2: coordinator layout iff teams present ────────────────────


def test_single_agent_layout_when_no_teams() -> None:
    state = _state()
    consumer = _consumer(state, title="R")
    assert not state.teams
    out = _render(consumer)
    assert "Teams:" not in out


def test_coordinator_layout_when_teams_present() -> None:
    state = _state()
    consumer = _consumer(state, title="R")
    state.start_team("team-x", "investigate the widget", agent_count=3)
    out = _render(consumer)
    assert "Teams:" in out
    assert "team-x" in out


# ── AC-4-4: extractor present/absent feed behavior ──────────────────


@pytest.mark.asyncio
async def test_extractor_event_appended_to_feed() -> None:
    def extractor(tool_name: str, result: object) -> DiscoveryEvent | None:
        return DiscoveryEvent(
            kind="widgets_found",
            summary="Found 5 widgets",
            tool_name=tool_name,
            timestamp=time.monotonic(),
        )

    state = _state()
    consumer = _consumer(state, title="R", discovery_extractor=extractor)
    await consumer.on_tool_start("t1", "scan")
    await consumer.on_tool_result("t1", json.dumps({"status": "ok", "widgets": 5}))
    assert len(state.discoveries) == 1
    assert state.discoveries[0].summary == "Found 5 widgets"
    assert state.discoveries[0].kind == "widgets_found"


@pytest.mark.asyncio
async def test_extractor_none_leaves_feed_unchanged() -> None:
    def extractor(tool_name: str, result: object) -> DiscoveryEvent | None:
        return None

    state = _state()
    consumer = _consumer(state, title="R", discovery_extractor=extractor)
    await consumer.on_tool_start("t1", "scan")
    await consumer.on_tool_result("t1", json.dumps({"status": "ok"}))
    assert state.discoveries == []


@pytest.mark.asyncio
async def test_default_extractor_never_produces_events() -> None:
    state = _state()
    consumer = _consumer(state, title="R")  # no extractor -> no-op default
    await consumer.on_tool_start("t1", "scan")
    await consumer.on_tool_result("t1", json.dumps({"status": "ok", "count": 9}))
    assert state.discoveries == []


# ── AC-4-5: malformed JSON / error status / raising extractor ───────


@pytest.mark.asyncio
async def test_malformed_json_does_not_raise() -> None:
    def extractor(tool_name: str, result: object) -> DiscoveryEvent | None:
        # Mimic a real extractor that parses JSON and may explode on bad input.
        data = json.loads(result)  # type: ignore[arg-type]
        return DiscoveryEvent(
            kind="x",
            summary=str(data),
            tool_name=tool_name,
            timestamp=time.monotonic(),
        )

    state = _state()
    consumer = _consumer(state, title="R", discovery_extractor=extractor)
    await consumer.on_tool_start("t1", "scan")
    # Not valid JSON — extractor raises, consumer swallows, feed unchanged.
    await consumer.on_tool_result("t1", "<<<not json>>>")
    assert state.discoveries == []


@pytest.mark.asyncio
async def test_raising_extractor_is_guarded() -> None:
    def extractor(tool_name: str, result: object) -> DiscoveryEvent | None:
        raise ValueError("boom")

    state = _state()
    consumer = _consumer(state, title="R", discovery_extractor=extractor)
    # Direct call must return None, not propagate.
    assert consumer._extract_discovery("scan", "anything") is None


def test_extract_discovery_returns_none_on_error_status_when_extractor_checks() -> None:
    def extractor(tool_name: str, result: object) -> DiscoveryEvent | None:
        data = json.loads(result)  # type: ignore[arg-type]
        if data.get("status") == "error":
            return None
        return DiscoveryEvent(kind="x", summary="ok", tool_name=tool_name, timestamp=0.0)

    consumer = _consumer(_state(), title="R", discovery_extractor=extractor)
    assert consumer._extract_discovery("scan", json.dumps({"status": "error"})) is None


# ── State plumbing / lifecycle ──────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_lifecycle_tracks_completed_and_errors() -> None:
    state = _state()
    consumer = _consumer(state, title="R")
    await consumer.on_run_started("thread", "run")
    await consumer.on_tool_start("t1", "tool_alpha")
    await consumer.on_tool_result("t1", json.dumps({"status": "ok"}))
    await consumer.on_tool_end("t1")
    await consumer.on_tool_start("t2", "tool_beta")
    await consumer.on_tool_result("t2", json.dumps({"status": "error", "error": "bad"}))
    await consumer.on_tool_end("t2")

    assert len(state.completed_tools) == 2
    assert state.tool_errors == 1
    # tool_alpha -> phase 1, tool_beta -> phase 2 advanced the indicator.
    assert state.phases[-1].phase == 2


def test_reset_preserves_phase_spec() -> None:
    state = _state()
    state.advance_phase(1)
    state.start_team("t", "m")
    spec_before = state.phase_spec
    state.reset()
    assert state.phase_spec is spec_before
    assert state.phases == []
    assert state.teams == []


@pytest.mark.asyncio
async def test_quiet_consumer_is_silent_and_handles_all_events() -> None:
    c = QuietConsumer()
    await c.on_run_started("t", "r")
    await c.on_text_start("m", "assistant")
    await c.on_text_content("m", "hi")
    await c.on_text_end("m")
    await c.on_tool_start("t1", "x")
    await c.on_tool_args("t1", "{}")
    await c.on_tool_result("t1", "{}")
    await c.on_tool_end("t1")
    await c.on_state_snapshot({})
    await c.on_run_finished("t", "r")
    await c.on_error("nope", code="E1")  # writes to stderr, must not raise
