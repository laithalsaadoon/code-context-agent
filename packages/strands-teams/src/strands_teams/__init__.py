"""strands_teams — a domain-agnostic agent-teams framework on strands-agents.

This package hosts the generic team-dispatch, findings, artifact, scoring, hook,
display, and UseCase-contract machinery shared across use cases. Concrete
implementations (e.g. codebase analysis) live in separate workspace packages.
"""

from __future__ import annotations

from .coordinator import create_coordinator
from .layout import TeamsLayout
from .models import (
    Bundle,
    CalibratedJudgment,
    FrozenModel,
    GeneratedFile,
    PhaseTiming,
    StrictModel,
    TeamConfidence,
)
from .phases import PhaseSpec
from .rubric import (
    DEFAULT_NARRATIVE_RUBRIC,
    DimensionScore,
    NarrativeRubric,
    RubricDimension,
    ScoreReport,
)
from .runner import StreamResult, run_teams, run_teams_sync
from .settings import TeamsSettings
from .teams import (
    TEAM_TOOLS,
    TeamInvocationState,
    dispatch_team,
    enrich_artifact,
    read_team_findings,
    score_narrative,
    write_artifact,
)
from .usecase import MissingUseCaseMemberError, UseCase, validate_use_case
from .write_file import make_write_file

__version__ = "3.0.0"

__all__ = [
    "DEFAULT_NARRATIVE_RUBRIC",
    "TEAM_TOOLS",
    "Bundle",
    "CalibratedJudgment",
    "DimensionScore",
    "FrozenModel",
    "GeneratedFile",
    "MissingUseCaseMemberError",
    "NarrativeRubric",
    "PhaseSpec",
    "PhaseTiming",
    "RubricDimension",
    "ScoreReport",
    "StreamResult",
    "StrictModel",
    "TeamConfidence",
    "TeamInvocationState",
    "TeamsLayout",
    "TeamsSettings",
    "UseCase",
    "__version__",
    "create_coordinator",
    "dispatch_team",
    "enrich_artifact",
    "make_write_file",
    "read_team_findings",
    "run_teams",
    "run_teams_sync",
    "score_narrative",
    "validate_use_case",
    "write_artifact",
]
