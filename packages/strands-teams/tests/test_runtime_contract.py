"""Tests for the vendor-free specialist-runtime contracts in core (US-2, AC-1-5).

Covers the ``SpecialistRuntime`` Protocol, the ``SpecialistResult`` /
``ResourceUsage`` FrozenModels, their top-level exports, and the core-stays-
vendor-free import guard (AC-1-5) — no coding-agent SDK is imported here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import strands_teams
from strands_teams import (
    Evidence,
    Finding,
    ResourceUsage,
    SpecialistResult,
    SpecialistRuntime,
)

_CORE_SRC = Path(strands_teams.__file__).parent
_FORBIDDEN_IMPORTS = {"claude_agent_sdk", "codex", "openai_codex"}


def test_exports_present() -> None:
    for name in ("SpecialistRuntime", "SpecialistResult", "ResourceUsage", "SpecialistDisposition"):
        assert name in strands_teams.__all__
        assert hasattr(strands_teams, name)


def test_specialist_result_reuses_p1_types() -> None:
    finding = Finding(statement="x", severity="low", evidence=[Evidence(type="file_line", uri="a.py:1")])
    result = SpecialistResult(
        disposition="completed",
        artifacts=["src/a.py"],
        findings=[finding],
        evidence=[Evidence(type="command", uri="pytest -q")],
        residual_risk="none",
        resource_usage=ResourceUsage(cost_usd=0.5, input_tokens=10, output_tokens=5, num_turns=3),
    )
    assert result.findings[0] is finding
    assert result.resource_usage.cost_usd == pytest.approx(0.5)


def test_disposition_literal_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        SpecialistResult(disposition="done")  # not one of the four literals


def test_frozen_model_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        SpecialistResult(disposition="completed", surprise="nope")


def test_result_defaults_are_empty() -> None:
    result = SpecialistResult(disposition="refused")
    assert result.artifacts == []
    assert result.findings == []
    assert result.evidence == []
    assert result.residual_risk == ""
    assert result.resource_usage == ResourceUsage()


def test_result_is_immutable() -> None:
    result = SpecialistResult(disposition="partial")
    with pytest.raises(ValidationError):
        result.disposition = "completed"


def test_resource_usage_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        ResourceUsage(cost_usd=-1.0)


def test_runtime_is_runtime_checkable_protocol() -> None:
    class Dummy:
        def submit(self, task, *, workspace, model_id, capabilities=None): ...
        def status(self, handle): ...
        def send_input(self, handle, text): ...
        def cancel(self, handle): ...
        def collect(self, handle): ...

    assert isinstance(Dummy(), SpecialistRuntime)
    assert not isinstance(object(), SpecialistRuntime)


def test_core_source_imports_no_coding_sdk() -> None:
    """AC-1-5: no module under strands_teams/ imports a coding-agent SDK.

    The vendor-free guarantee is that core never imports ``claude_agent_sdk``,
    ``codex``, or ``openai_codex`` — those SDKs (and the harness subprocesses they
    launch) live only in ``strands_teams_runtimes``. A bare ``subprocess`` import
    is NOT forbidden: core legitimately ships a bounded, read-only shell tool
    (``security.run_command``) that predates this phase and launches no coding
    harness. This test asserts the SDK-import boundary, which is the concrete,
    statically-checkable form of AC-1-5.
    """
    offenders: list[str] = []
    for py_file in _CORE_SRC.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            hit = names & _FORBIDDEN_IMPORTS
            if hit:
                offenders.append(f"{py_file.relative_to(_CORE_SRC)}: {sorted(hit)}")
    assert not offenders, f"core must stay vendor-free; offenders: {offenders}"
