"""The evidence gate — a ``blocking`` finding must carry evidence.

ASTR §4.5 makes communication evidence-bearing; the ``NarrativeRubric``
``specificity`` dimension already scores *citation density* over a narrative via
:data:`~strands_teams.rubric.FILE_LINE_PATTERN`. This module binds that same
citation-density concept to a typed :class:`~strands_teams.models.Finding`:
a ``blocking=True`` finding must carry ≥1 evidence ref, or it is flagged rather
than silently accepted.

To keep the "evidence density" definition in exactly one place (AC-5-4), the gate
reuses ``FILE_LINE_PATTERN`` to recognize inline ``file.ext:line`` citations in a
finding's ``statement`` and evidence ``uri``s, rather than introducing a second,
divergent citation heuristic. An explicit :class:`~strands_teams.models.Evidence`
ref and an inline file:line citation both count toward the same evidence-ref
tally.

The gate is a pure function over already-parsed ``Finding`` objects; it does no
I/O and never raises, so both the write path (``write_findings``) and the read
path (``read_team_findings``) can call it and surface an ``evidence_gate`` summary
in their JSON results.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .models import FrozenModel
from .rubric import FILE_LINE_PATTERN

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .models import Finding

_FILE_LINE_RE = re.compile(FILE_LINE_PATTERN)


def _evidence_ref_count(finding: Finding) -> int:
    """Count the evidence refs backing a finding (AC-5-1, AC-5-4).

    Reuses the rubric's :data:`~strands_teams.rubric.FILE_LINE_PATTERN`
    citation-density concept: every explicit :class:`Evidence` ref counts, plus
    any inline ``file.ext:line`` citation found in the finding's ``statement``
    that isn't already represented by an explicit evidence ref. This keeps the
    "what counts as evidence" definition sourced from the one rubric pattern.
    """
    count = len(finding.evidence)
    if count == 0:
        # No explicit evidence — fall back to inline file:line citations in the
        # statement, using the same pattern the specificity dimension scores.
        count = len(_FILE_LINE_RE.findall(finding.statement))
    return count


class EvidenceGateResult(FrozenModel):
    """Outcome of running the evidence gate over one or more findings.

    Attributes:
        passed: True when no ``blocking`` finding lacks evidence.
        flagged: Statements of the ``blocking`` findings that carry no evidence.
        checked: Total number of findings evaluated.
    """

    passed: bool
    flagged: list[str]
    checked: int


def finding_passes_gate(finding: Finding) -> bool:
    """Return whether a single finding satisfies the evidence gate (AC-5-1, AC-5-5).

    A finding passes when it is non-blocking (evidence optional — AC-5-5) or when
    it carries ≥1 evidence ref. It fails only when ``blocking`` is True and it has
    zero evidence refs.
    """
    if not finding.blocking:
        return True
    return _evidence_ref_count(finding) >= 1


def evaluate_evidence_gate(findings: Iterable[Finding]) -> EvidenceGateResult:
    """Evaluate the evidence gate over a collection of findings (AC-5-1..5-3).

    Args:
        findings: The typed findings to check.

    Returns:
        An :class:`EvidenceGateResult`: ``passed`` is False when any
        ``blocking=True`` finding lacks evidence, and ``flagged`` names each such
        finding's statement so the coordinator can act on the gap.
    """
    flagged: list[str] = []
    checked = 0
    for finding in findings:
        checked += 1
        if not finding_passes_gate(finding):
            flagged.append(finding.statement)
    return EvidenceGateResult(passed=not flagged, flagged=flagged, checked=checked)


def gate_findings(findings: Iterable[Finding]) -> dict[str, object]:
    """Return the evidence-gate summary as a plain dict for embedding in tool JSON.

    Convenience wrapper around :func:`evaluate_evidence_gate` producing the
    ``evidence_gate`` block used by the write and read tool results (AC-5-2, AC-5-3).
    """
    result = evaluate_evidence_gate(findings)
    return {"passed": result.passed, "flagged": result.flagged, "checked": result.checked}
