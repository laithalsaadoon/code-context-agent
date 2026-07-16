"""Tests for invoke_coding_agent + make_invoke_coding_agent (US-6, US-7).

The runtime registry is populated with fake async runtimes — no SDK, subprocess,
Bedrock, or network. Covers heartbeats (AC-8-6), semaphore cap (AC-8-3),
timeout → failed (AC-8-4), error envelopes on bad state (AC-6-5), and the final
yield being a SpecialistResult JSON string (AC-6-4).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from strands import ToolContext

import strands_teams_runtimes.tool as tool_mod
from strands_teams import SpecialistResult
from strands_teams_runtimes import make_invoke_coding_agent


def _context(**state: Any) -> ToolContext:
    return ToolContext(
        tool_use={"toolUseId": "t", "name": "invoke_coding_agent", "input": {}},
        agent=None,
        invocation_state=dict(state),
    )


async def _drain(tool: Any, task: str, ctx: ToolContext) -> list[str]:
    """Run the async-generator tool via its wrapped function; collect all yields."""
    return [chunk async for chunk in tool.__wrapped__(task=task, tool_context=ctx)]


class FakeRuntime:
    """A runtime whose run() returns a fixed SpecialistResult after an optional delay."""

    def __init__(self, *, result: SpecialistResult, delay: float = 0.0) -> None:
        self._result = result
        self._delay = delay
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        task: str,
        *,
        workspace: Path,
        model_id: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
    ) -> SpecialistResult:
        self.calls.append({"task": task, "workspace": workspace, "model_id": model_id})
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._result


def _ok_result() -> SpecialistResult:
    return SpecialistResult(disposition="completed", artifacts=["src/x.py"])


async def test_final_yield_is_specialist_result_json(tmp_path: Path) -> None:
    tool = make_invoke_coding_agent({"claude": FakeRuntime(result=_ok_result())})
    ctx = _context(workspace=tmp_path, runtime="claude", model_id="m")
    chunks = await _drain(tool, "do it", ctx)
    parsed = json.loads(chunks[-1])
    assert parsed["disposition"] == "completed"  # AC-6-4
    assert parsed["artifacts"] == ["src/x.py"]


async def test_workspace_string_is_accepted(tmp_path: Path) -> None:
    rt = FakeRuntime(result=_ok_result())
    tool = make_invoke_coding_agent({"claude": rt})
    ctx = _context(workspace=str(tmp_path), runtime="claude")
    await _drain(tool, "do it", ctx)
    assert rt.calls[0]["workspace"] == tmp_path


async def test_missing_workspace_yields_error(tmp_path: Path) -> None:
    tool = make_invoke_coding_agent({"claude": FakeRuntime(result=_ok_result())})
    ctx = _context(runtime="claude")
    chunks = await _drain(tool, "do it", ctx)
    payload = json.loads(chunks[-1])
    assert payload["status"] == "error"  # AC-6-5
    assert "workspace" in payload["message"]


async def test_missing_runtime_yields_error(tmp_path: Path) -> None:
    tool = make_invoke_coding_agent({"claude": FakeRuntime(result=_ok_result())})
    ctx = _context(workspace=tmp_path)
    payload = json.loads((await _drain(tool, "do it", ctx))[-1])
    assert payload["status"] == "error"
    assert "runtime" in payload["message"]


async def test_unknown_runtime_yields_error(tmp_path: Path) -> None:
    tool = make_invoke_coding_agent({"claude": FakeRuntime(result=_ok_result())})
    ctx = _context(workspace=tmp_path, runtime="ghost")
    payload = json.loads((await _drain(tool, "do it", ctx))[-1])
    assert payload["status"] == "error"
    assert "ghost" in payload["message"]


async def test_heartbeat_before_final(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-8-6: a multi-step run produces >=1 non-final heartbeat before the result."""
    monkeypatch.setattr(tool_mod, "_HEARTBEAT_INTERVAL_S", 0.02)
    tool = make_invoke_coding_agent({"claude": FakeRuntime(result=_ok_result(), delay=0.09)})
    ctx = _context(workspace=tmp_path, runtime="claude")
    chunks = await _drain(tool, "do it", ctx)
    heartbeats = [c for c in chunks[:-1] if c.startswith("heartbeat:")]
    assert heartbeats  # at least one heartbeat
    assert json.loads(chunks[-1])["disposition"] == "completed"


async def test_timeout_yields_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-8-4: a harness exceeding the tool timeout produces disposition=failed."""
    monkeypatch.setattr(tool_mod, "_HEARTBEAT_INTERVAL_S", 0.02)
    monkeypatch.setattr(tool_mod, "_SUBPROCESS_TIMEOUT_S", 0.05)
    tool = make_invoke_coding_agent({"slow": FakeRuntime(result=_ok_result(), delay=5.0)})
    ctx = _context(workspace=tmp_path, runtime="slow")
    chunks = await _drain(tool, "do it", ctx)
    payload = json.loads(chunks[-1])
    assert payload["disposition"] == "failed"  # AC-7-3 / AC-8-4


async def test_runtime_exception_yields_failed(tmp_path: Path) -> None:
    class Boom:
        async def run(self, *_a: Any, **_k: Any) -> SpecialistResult:
            raise RuntimeError("harness blew up")

    tool = make_invoke_coding_agent({"boom": Boom()})
    ctx = _context(workspace=tmp_path, runtime="boom")
    payload = json.loads((await _drain(tool, "do it", ctx))[-1])
    assert payload["disposition"] == "failed"


async def test_semaphore_caps_concurrency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-8-3: concurrent harness runs never exceed the semaphore cap."""
    cap = 2
    monkeypatch.setattr(tool_mod, "_HARNESS_SEMAPHORE", asyncio.Semaphore(cap))

    active = 0
    peak = 0

    class Counting:
        async def run(
            self,
            task: str,
            *,
            workspace: Path,
            model_id: str | None = None,
            capabilities: Mapping[str, Any] | None = None,
        ) -> SpecialistResult:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.03)
            active -= 1
            return _ok_result()

    tool = make_invoke_coding_agent({"c": Counting()})

    async def one() -> list[str]:
        ctx = _context(workspace=tmp_path, runtime="c")
        return await _drain(tool, "do it", ctx)

    await asyncio.gather(*(one() for _ in range(6)))
    assert peak <= cap  # never exceeded the cap


async def test_tool_docstring_warns_about_swarm_timeouts(tmp_path: Path) -> None:
    """AC-7-4: the tool docstring states swarm timeouts must exceed the tool timeout."""
    tool = make_invoke_coding_agent({})
    doc = tool.__wrapped__.__doc__ or ""
    assert "node_timeout" in doc
    assert "execution_timeout" in doc


def test_module_docstring_notes_timeout_coordination() -> None:
    assert "node_timeout" in (tool_mod.__doc__ or "")
