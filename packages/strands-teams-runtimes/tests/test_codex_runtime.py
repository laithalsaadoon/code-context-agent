"""Tests for CodexRuntime (US-4). Subprocess is fully mocked — no Bedrock/network.

Covers argv construction (AC-4-1), per-run CODEX_HOME isolation (AC-4-4),
result-from-`-o`-file + turn.completed parsing (AC-4-2/AC-4-3/AC-8-5),
turn.failed → failed disposition (AC-4-5), and transcript boundary (AC-8-8).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import strands_teams_runtimes.codex as codex_mod
from strands_teams import SpecialistResult, SpecialistRuntime
from strands_teams_runtimes import CODEX_MODEL_ID, CodexRuntime


class FakeProc:
    """Stand-in for an asyncio subprocess: emits JSONL stdout and writes the -o file."""

    def __init__(self, *, stdout_lines: list[dict[str, Any]], returncode: int, result_file: Path, result_payload):
        self._stdout = ("\n".join(json.dumps(line) for line in stdout_lines)).encode()
        self.returncode = returncode
        self._result_file = result_file
        self._result_payload = result_payload

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._result_payload is not None:
            self._result_file.write_text(json.dumps(self._result_payload), encoding="utf-8")
        return self._stdout, b""


def _result_payload() -> dict[str, Any]:
    return {
        "summary": "Refactored the parser and added a regression test.",
        "artifacts": ["src/parser.py", "tests/test_parser.py"],
        "findings": [
            {
                "statement": "Parser now rejects malformed headers.",
                "severity": "medium",
                "evidence": [{"type": "file_line", "uri": "src/parser.py:88"}],
            },
        ],
        "evidence": [],
        "residual_risk": "",
    }


def test_build_command_matches_spec(tmp_path: Path) -> None:
    rt = CodexRuntime(reasoning_effort="high")
    cmd = rt.build_command(tmp_path, schema_path=tmp_path / "s.json", result_path=tmp_path / "r.json", task="fix it")
    assert cmd[:2] == ["codex", "exec"]
    assert "--model" in cmd and CODEX_MODEL_ID in cmd
    assert "--model-provider" in cmd and "amazon-bedrock" in cmd
    assert "model_reasoning_effort=high" in cmd
    assert "--cd" in cmd and str(tmp_path) in cmd
    assert "--full-auto" in cmd
    assert "--json" in cmd
    assert "--output-schema" in cmd
    assert "-o" in cmd
    assert cmd[-1] == "fix it"


def test_build_env_sets_per_run_codex_home(tmp_path: Path) -> None:
    rt = CodexRuntime(region="us-east-2", bearer_token="tok-123")
    env = rt.build_env(tmp_path / "home-a")
    assert env["CODEX_HOME"] == str(tmp_path / "home-a")
    assert env["AWS_REGION"] == "us-east-2"
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == "tok-123"


def test_prepare_run_dirs_distinct_codex_home(tmp_path: Path) -> None:
    """AC-4-4: two runs get distinct CODEX_HOME dirs so they don't share ~/.codex state."""
    rt = CodexRuntime(codex_home_root=tmp_path / "homes")
    home_a, _, _ = rt._prepare_run_dirs(tmp_path / "wt", run_id="a")
    home_b, _, _ = rt._prepare_run_dirs(tmp_path / "wt", run_id="b")
    assert home_a != home_b
    assert home_a.exists() and home_b.exists()


async def test_run_completed_parses_o_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rt = CodexRuntime(codex_home_root=tmp_path / "homes")
    events = [
        {"type": "thread.started"},
        # An intermediate schema-valid line that must NOT be used as the result (#19816):
        {"type": "item.completed", "summary": "WRONG intermediate payload", "artifacts": ["decoy.py"]},
        {"type": "turn.completed", "usage": {"input_tokens": 200, "output_tokens": 80, "cost_usd": 0.9}},
    ]

    def fake_exec(*_args: Any, **kwargs: Any) -> FakeProc:
        # result_path is the 3rd prepared path; recover it from the argv.
        argv = list(_args)
        result_path = Path(argv[argv.index("-o") + 1])
        return FakeProc(stdout_lines=events, returncode=0, result_file=result_path, result_payload=_result_payload())

    async def fake_create(*args: Any, **kwargs: Any) -> FakeProc:
        return fake_exec(*args, **kwargs)

    monkeypatch.setattr(codex_mod.asyncio, "create_subprocess_exec", fake_create)

    result = await rt.run("refactor the parser", workspace=tmp_path / "wt", run_id="run1")
    assert isinstance(result, SpecialistResult)
    assert result.disposition == "completed"
    assert result.artifacts == ["src/parser.py", "tests/test_parser.py"]  # from -o file, not the decoy
    assert "decoy.py" not in result.artifacts
    assert result.findings[0].severity == "medium"
    assert result.resource_usage.input_tokens == 200
    assert result.resource_usage.cost_usd == pytest.approx(0.9)


async def test_run_turn_failed_is_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rt = CodexRuntime(codex_home_root=tmp_path / "homes")
    events = [{"type": "thread.started"}, {"type": "turn.failed", "error": "model error"}]

    async def fake_create(*args: Any, **kwargs: Any) -> FakeProc:
        argv = list(args)
        result_path = Path(argv[argv.index("-o") + 1])
        return FakeProc(stdout_lines=events, returncode=1, result_file=result_path, result_payload=None)

    monkeypatch.setattr(codex_mod.asyncio, "create_subprocess_exec", fake_create)
    result = await rt.run("do it", workspace=tmp_path / "wt", run_id="run2")
    assert result.disposition == "failed"  # AC-4-5


def test_collect_never_surfaces_transcript(tmp_path: Path) -> None:
    """AC-8-8: JSONL transcript never crosses into the result."""
    rt = CodexRuntime()
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps(_result_payload()), encoding="utf-8")
    handle = codex_mod.CodexRunHandle(
        workspace=tmp_path,
        codex_home=tmp_path,
        result_file=result_file,
        events=[{"type": "item.completed", "text": "secret chain of thought"}, {"type": "turn.completed"}],
        returncode=0,
    )
    result = rt.collect(handle)
    assert "secret chain of thought" not in result.model_dump_json()


def test_collect_missing_result_file_degrades(tmp_path: Path) -> None:
    rt = CodexRuntime()
    handle = codex_mod.CodexRunHandle(
        workspace=tmp_path,
        codex_home=tmp_path,
        result_file=tmp_path / "absent.json",
        events=[{"type": "turn.completed"}],
        returncode=0,
    )
    result = rt.collect(handle)
    assert result.disposition == "completed"
    assert result.artifacts == []


# -- SpecialistRuntime Protocol conformance (AC-4-1) --------------------------


def test_codex_runtime_satisfies_specialist_runtime_protocol() -> None:
    """The real adapter implements submit/status/send_input/cancel/collect (not just run)."""
    assert isinstance(CodexRuntime(), SpecialistRuntime)


class KillableProc:
    """Stand-in subprocess whose communicate() blocks until it is killed (AC-7-3)."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self._killed = asyncio.Event()

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._killed.wait()  # block until kill() releases us
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._killed.set()

    async def wait(self) -> int:
        await self._killed.wait()
        return self.returncode or -9


def test_run_kills_subprocess_on_cancel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-7-3: cancelling run() kills the orphaned codex exec subprocess (plan §6 cost mitigation)."""
    proc = KillableProc()

    async def fake_create(*_args: Any, **_kwargs: Any) -> KillableProc:
        return proc

    monkeypatch.setattr(codex_mod.asyncio, "create_subprocess_exec", fake_create)

    async def scenario() -> None:
        rt = CodexRuntime(codex_home_root=tmp_path / "homes")
        task = asyncio.ensure_future(rt.run("do it", workspace=tmp_path / "wt", run_id="k"))
        await asyncio.sleep(0.02)  # let run() launch the subprocess and start awaiting
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert proc.killed is True  # the subprocess was terminated, not orphaned
