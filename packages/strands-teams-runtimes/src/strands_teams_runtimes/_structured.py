"""Shared structured-output shape for coding harnesses.

Both adapters ask their harness for the SAME model-authored structured payload —
the fields the harness reports about the work it did (``summary``, ``artifacts``,
``findings``, ``evidence``, ``residual_risk``, and an optional self-reported
``disposition``). :class:`StructuredHarnessOutput` is the JSON-schema source for
that payload (fed to Claude's ``output_format`` and Codex's ``--output-schema``),
and :func:`structured_to_result` folds it — together with the authoritative
disposition and resource usage derived from the harness's own completion signal —
into a vendor-free :class:`~strands_teams.SpecialistResult`.

Keeping this in one place means the two adapters produce identically shaped
results and the boundary discipline (no transcript — ASTR §10.4) is enforced once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from strands_teams import Evidence, Finding, ResourceUsage, SpecialistResult

if TYPE_CHECKING:
    from strands_teams import SpecialistDisposition


class StructuredHarnessOutput(BaseModel):
    """The model-authored portion of a harness result (its self-report).

    Not a ``FrozenModel``: this is the JSON-schema contract handed to the harness,
    so it is permissive about extra keys (``extra="ignore"``) — a harness that
    adds a stray field must not fail the whole run. The authoritative
    ``disposition`` and ``resource_usage`` are NOT taken from here; they are
    derived from the harness's completion signal (Claude ``ResultMessage.subtype``
    / Codex ``turn.completed`` vs ``turn.failed``).

    Attributes:
        summary: One-paragraph summary of what the harness did (never a transcript).
        disposition: Optional harness self-report of how it concluded; the adapter
            may use it only as a fallback when the completion signal is ambiguous.
        artifacts: Worktree-relative paths the harness reports it changed.
        findings: Typed findings the harness produced.
        evidence: Standalone evidence refs.
        residual_risk: Short note on risk the harness could not eliminate.
    """

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(default="", description="One-paragraph summary of the work (never a transcript)")
    disposition: str | None = Field(default=None, description="Harness self-reported disposition (fallback only)")
    artifacts: list[str] = Field(default_factory=list, description="Worktree-relative paths changed")
    findings: list[Finding] = Field(default_factory=list, description="Typed findings produced")
    evidence: list[Evidence] = Field(default_factory=list, description="Standalone evidence refs")
    residual_risk: str = Field(default="", description="Risk the harness could not eliminate")


def structured_to_result(
    structured: StructuredHarnessOutput,
    *,
    disposition: SpecialistDisposition,
    resource_usage: ResourceUsage,
) -> SpecialistResult:
    """Fold a harness self-report + authoritative signals into a ``SpecialistResult``.

    The ``disposition`` and ``resource_usage`` args are the authoritative,
    adapter-derived values; the ``structured`` payload supplies only the
    model-authored artifacts/findings/evidence/residual-risk. The harness
    ``summary`` is intentionally dropped from the result to keep the boundary
    strictly structured (ASTR §10.4) — it is not a field of ``SpecialistResult``.
    """
    residual = structured.residual_risk
    return SpecialistResult(
        disposition=disposition,
        artifacts=list(structured.artifacts),
        findings=list(structured.findings),
        evidence=list(structured.evidence),
        residual_risk=residual,
        resource_usage=resource_usage,
    )
