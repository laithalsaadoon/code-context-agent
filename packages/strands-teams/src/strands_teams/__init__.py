"""strands_teams — a domain-agnostic agent-teams framework on strands-agents.

This package hosts the generic team-dispatch, findings, artifact, scoring, hook,
display, and UseCase-contract machinery shared across use cases. Concrete
implementations (e.g. codebase analysis) live in separate workspace packages.
"""

from __future__ import annotations

from .coordinator import create_coordinator
from .durable import AsyncioDurableEngine, DurableStep, StepResult
from .events import (
    EventLog,
    ReplanDecision,
    ReplayState,
    replay,
    replay_log,
    should_replan,
)
from .evidence_gate import EvidenceGateResult, evaluate_evidence_gate, gate_findings
from .goal_runner import GoalResult, run_goal, run_goal_sync
from .layout import TeamsLayout
from .models import (
    NO_CURRENT_REVISION,
    ApprovalToken,
    ArtifactRevision,
    AuthorityGrant,
    Budget,
    Bundle,
    CalibratedJudgment,
    Decision,
    Disposition,
    EpistemicStatus,
    Evidence,
    EvidenceType,
    Finding,
    FrozenModel,
    GateName,
    GeneratedFile,
    HumanDecision,
    HumanDecisionStatus,
    PhaseTiming,
    PolicyVerdict,
    PromotionResult,
    RevisionConflictError,
    Severity,
    StaleArtifactError,
    StrictModel,
    TeamConfidence,
    TeamEvent,
    TransitionRequest,
    compute_content_hash,
)
from .phases import PhaseSpec
from .policy import PolicyEngine, denying_decision
from .revisions import (
    Review,
    ReviewReconciliation,
    ReviewVerdict,
    invalidates_review,
    promote_artifact,
    reconcile_reviews,
    semantic_diff,
)
from .rubric import (
    DEFAULT_NARRATIVE_RUBRIC,
    DimensionScore,
    NarrativeRubric,
    RubricDimension,
    ScoreReport,
)
from .runner import StreamResult, run_teams, run_teams_sync
from .runtime import (
    ResourceUsage,
    SpecialistDisposition,
    SpecialistResult,
    SpecialistRuntime,
)
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
from .write_file import make_write_file, make_write_findings

__version__ = "3.0.0"

__all__ = [
    "DEFAULT_NARRATIVE_RUBRIC",
    "NO_CURRENT_REVISION",
    "TEAM_TOOLS",
    "ApprovalToken",
    "ArtifactRevision",
    "AsyncioDurableEngine",
    "AuthorityGrant",
    "Budget",
    "Bundle",
    "CalibratedJudgment",
    "Decision",
    "DimensionScore",
    "Disposition",
    "DurableStep",
    "EpistemicStatus",
    "EventLog",
    "Evidence",
    "EvidenceGateResult",
    "EvidenceType",
    "Finding",
    "FrozenModel",
    "GateName",
    "GeneratedFile",
    "GoalResult",
    "HumanDecision",
    "HumanDecisionStatus",
    "MissingUseCaseMemberError",
    "NarrativeRubric",
    "PhaseSpec",
    "PhaseTiming",
    "PolicyEngine",
    "PolicyVerdict",
    "PromotionResult",
    "ReplanDecision",
    "ReplayState",
    "ResourceUsage",
    "Review",
    "ReviewReconciliation",
    "ReviewVerdict",
    "RevisionConflictError",
    "RubricDimension",
    "ScoreReport",
    "Severity",
    "SpecialistDisposition",
    "SpecialistResult",
    "SpecialistRuntime",
    "StaleArtifactError",
    "StepResult",
    "StreamResult",
    "StrictModel",
    "TeamConfidence",
    "TeamEvent",
    "TeamInvocationState",
    "TeamsLayout",
    "TeamsSettings",
    "TransitionRequest",
    "UseCase",
    "__version__",
    "compute_content_hash",
    "create_coordinator",
    "denying_decision",
    "dispatch_team",
    "enrich_artifact",
    "evaluate_evidence_gate",
    "gate_findings",
    "invalidates_review",
    "make_write_file",
    "make_write_findings",
    "promote_artifact",
    "read_team_findings",
    "reconcile_reviews",
    "replay",
    "replay_log",
    "run_goal",
    "run_goal_sync",
    "run_teams",
    "run_teams_sync",
    "score_narrative",
    "semantic_diff",
    "should_replan",
    "validate_use_case",
    "write_artifact",
]
