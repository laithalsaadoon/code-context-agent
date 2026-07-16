"""Base Pydantic models and generic result primitives for strands_teams.

This module hosts the framework-level data-transfer types shared by dispatch,
hooks, display, and use-case layers. All models are domain-agnostic: they carry
no codebase-analysis vocabulary. Concrete use cases subclass or compose these.

Copied and renamed from the pre-split ``code_context_agent`` package:
- ``StrictModel`` / ``FrozenModel`` from ``models/base.py``.
- ``Bundle``, ``GeneratedFile``, ``PhaseTiming`` from ``models/output.py`` (verbatim).
- ``AnalysisConfidence`` -> ``TeamConfidence`` and ``CalibratedJudgment`` (renamed).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model with strict validation.

    This model:
    - Allows mutation (frozen=False)
    - Validates assignments
    - Forbids extra fields
    - Strips whitespace from strings

    Use for internal mutable state.
    """

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class FrozenModel(BaseModel):
    """Immutable model (like a frozen dataclass).

    This model:
    - Prevents mutation (frozen=True)
    - Validates assignments
    - Forbids extra fields

    Use for result objects and immutable data transfer.
    """

    model_config = ConfigDict(
        frozen=True,
        validate_assignment=True,
        extra="forbid",
    )


class GeneratedFile(FrozenModel):
    """Record of a file generated during a run."""

    path: str = Field(description="Relative path to the generated file")
    line_count: int = Field(ge=0, description="Number of lines in the file")
    description: str = Field(description="What this file contains")


class Bundle(FrozenModel):
    """A narrative bundle about a specific area of the domain."""

    area: str = Field(description="Bundle area identifier (e.g., 'auth', 'hotspots', 'security')")
    path: str = Field(description="Relative path to the bundle file")
    line_count: int = Field(ge=0, description="Number of lines in the bundle")
    summary: str = Field(description="One-sentence summary of this bundle's content")
    focus_match: bool = Field(default=False, description="Whether this bundle was generated for the focus area")


class PhaseTiming(FrozenModel):
    """Timing data for a single run phase."""

    phase: int = Field(description="Phase number (1-based)")
    name: str = Field(description="Phase name (e.g., 'indexing', 'team-structure', 'synthesis')")
    start_offset_seconds: float = Field(ge=0.0, default=0.0, description="Seconds from run start")
    duration_seconds: float = Field(ge=0.0, description="Phase duration in seconds")
    tool_count: int = Field(ge=0, default=0, description="Number of tool calls in this phase")
    status: str = Field(default="completed", description="Phase status")


class TeamConfidence(FrozenModel):
    """Confidence metadata attached to any team judgment.

    Renamed from ``AnalysisConfidence`` to keep the framework layer
    domain-agnostic.
    """

    score: float = Field(ge=0.0, le=1.0, default=0.5, description="Calibrated confidence (0.5=uncertain, 0.9+=high)")
    evidence_sources: list[str] = Field(default_factory=list, description="Data sources supporting this judgment")
    information_gaps: list[str] = Field(default_factory=list, description="What couldn't be determined")
    agreement_level: Literal["unanimous", "majority", "split", "contested"] = Field(
        default="unanimous",
        description="Level of agreement among teams",
    )
    dissenting_signals: list[str] = Field(
        default_factory=list,
        description="Signals arguing against the primary judgment",
    )
    data_freshness: Literal["current", "stale", "outdated"] = Field(
        default="current",
        description="Whether underlying data is current",
    )
    index_age_hours: float = Field(ge=0.0, default=0.0, description="Hours since last full index")


class CalibratedJudgment(FrozenModel):
    """A judgment with attached confidence and auditable reasoning."""

    judgment: str = Field(description="The conclusion")
    confidence: TeamConfidence = Field(default_factory=TeamConfidence)
    reasoning_chain: list[str] = Field(default_factory=list, description="Step-by-step reasoning")
    counterfactual: str | None = Field(
        default=None,
        description="What would change the judgment (e.g., 'adding tests would lower risk')",
    )
