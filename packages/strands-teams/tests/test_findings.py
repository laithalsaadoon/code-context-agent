"""Tests for the typed ``Finding`` / ``Evidence`` models and the evidence gate.

Covers AC-1-1..1-5 (model shape, immutability, Literal/range rejection), AC-7-1,
and AC-5-1/5-5 (evidence-gate pass/fail semantics). No network or Bedrock calls.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from strands_teams import (
    Evidence,
    Finding,
    evaluate_evidence_gate,
    gate_findings,
)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
def test_evidence_valid_construction() -> None:
    ev = Evidence(type="file_line", uri="src/auth.py:42", detail="login path")
    assert ev.type == "file_line"
    assert ev.uri == "src/auth.py:42"
    assert ev.detail == "login path"


def test_evidence_requires_nonempty_uri() -> None:
    with pytest.raises(ValidationError):
        Evidence(type="url", uri="")


def test_evidence_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        Evidence(type="screenshot", uri="x")  # type: ignore[arg-type]


def test_evidence_is_immutable() -> None:
    ev = Evidence(type="test", uri="tests/test_x.py::test_y")
    with pytest.raises(ValidationError):
        ev.uri = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Finding — valid construction + defaults (AC-1-2)
# ---------------------------------------------------------------------------
def test_finding_valid_with_defaults() -> None:
    f = Finding(statement="Auth bypass in login", severity="high")
    assert f.blocking is False
    assert f.disposition == "open"
    assert f.affected_artifacts == []
    assert f.evidence == []
    assert f.confidence == 0.5
    assert f.created_by == ""
    assert isinstance(f.created_at, datetime)
    assert isinstance(f.updated_at, datetime)


def test_finding_full_construction() -> None:
    f = Finding(
        statement="SQL injection risk",
        severity="critical",
        blocking=True,
        disposition="acknowledged",
        affected_artifacts=["src/db.py"],
        evidence=[Evidence(type="file_line", uri="src/db.py:88")],
        confidence=0.9,
        created_by="security-team",
    )
    assert f.blocking is True
    assert f.evidence[0].uri == "src/db.py:88"
    assert f.confidence == 0.9


# ---------------------------------------------------------------------------
# Finding — immutability + rejection (AC-1-3, AC-1-5)
# ---------------------------------------------------------------------------
def test_finding_is_immutable() -> None:
    f = Finding(statement="x", severity="low")
    with pytest.raises(ValidationError):
        f.severity = "high"  # type: ignore[misc]


def test_finding_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Finding(statement="x", severity="low", bogus=1)  # type: ignore[call-arg]


def test_finding_rejects_bad_severity() -> None:
    with pytest.raises(ValidationError):
        Finding(statement="x", severity="fatal")  # type: ignore[arg-type]


def test_finding_rejects_bad_disposition() -> None:
    with pytest.raises(ValidationError):
        Finding(statement="x", severity="low", disposition="closed")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [-0.1, 1.1, 2.0])
def test_finding_rejects_out_of_range_confidence(bad: float) -> None:
    with pytest.raises(ValidationError):
        Finding(statement="x", severity="low", confidence=bad)


def test_finding_requires_nonempty_statement() -> None:
    with pytest.raises(ValidationError):
        Finding(statement="", severity="low")


# ---------------------------------------------------------------------------
# Evidence gate (AC-5-1, AC-5-5)
# ---------------------------------------------------------------------------
def test_gate_passes_nonblocking_without_evidence() -> None:
    f = Finding(statement="informational note", severity="info")
    result = evaluate_evidence_gate([f])
    assert result.passed is True
    assert result.flagged == []
    assert result.checked == 1


def test_gate_flags_blocking_without_evidence() -> None:
    f = Finding(statement="blocking with no proof", severity="high", blocking=True)
    result = evaluate_evidence_gate([f])
    assert result.passed is False
    assert result.flagged == ["blocking with no proof"]


def test_gate_passes_blocking_with_explicit_evidence() -> None:
    f = Finding(
        statement="blocking but evidenced",
        severity="high",
        blocking=True,
        evidence=[Evidence(type="file_line", uri="a.py:1")],
    )
    assert evaluate_evidence_gate([f]).passed is True


def test_gate_passes_blocking_with_inline_file_line_citation() -> None:
    # No explicit Evidence, but the statement cites a file:line (reuses the
    # rubric FILE_LINE_PATTERN citation-density concept, AC-5-4).
    f = Finding(statement="race condition in worker.py:203 under load", severity="high", blocking=True)
    assert evaluate_evidence_gate([f]).passed is True


def test_gate_findings_dict_shape() -> None:
    findings = [
        Finding(statement="ok note", severity="info"),
        Finding(statement="bad blocker", severity="critical", blocking=True),
    ]
    summary = gate_findings(findings)
    assert summary == {"passed": False, "flagged": ["bad blocker"], "checked": 2}
