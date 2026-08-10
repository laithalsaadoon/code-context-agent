"""Structured output models for agent analysis results.

These models define the shape of the agent's final structured response.
File generation (CONTEXT.md, bundles, etc.) still happens via tools.
The structured output captures the analysis summary and metadata.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import FrozenModel


class BusinessLogicItem(FrozenModel):
    """A ranked business logic item discovered during analysis."""

    rank: int = Field(description="Priority rank (1 = highest)")
    name: str = Field(description="Function/class/method name")
    role: str = Field(description="Brief description of business role")
    location: str = Field(description="File path and line reference (e.g., src/auth.py:42)")
    score: float = Field(ge=0.0, le=1.0, description="Relevance score from graph analysis")
    category: str | None = Field(default=None, description="Category: db, auth, validation, workflows, integrations")


class ArchitecturalRisk(FrozenModel):
    """An identified architectural risk."""

    description: str = Field(description="What the risk is")
    severity: str = Field(description="high, medium, or low")
    location: str | None = Field(default=None, description="File or module location")
    mitigation: str | None = Field(default=None, description="Suggested mitigation")


class GeneratedFile(FrozenModel):
    """Record of a file generated during analysis."""

    path: str = Field(description="Relative path to generated file")
    line_count: int = Field(ge=0, description="Number of lines in the file")
    description: str = Field(description="What this file contains")


class RefactoringCandidate(FrozenModel):
    """A suggested refactoring opportunity."""

    type: Literal["extract_helper", "inline_wrapper", "dead_code", "code_smell"] = Field(
        description="Kind of refactoring opportunity",
    )
    pattern: str = Field(description="Name or description of the pattern")
    files: list[str] = Field(description="File:line locations involved")
    occurrence_count: int = Field(ge=1, description="Number of occurrences")
    duplicated_lines: int = Field(ge=0, default=0, description="Lines of duplicated code")
    score: float = Field(ge=0.0, description="Priority score (higher = more impactful)")


class CodeHealthMetrics(FrozenModel):
    """Aggregate code health metrics."""

    duplication_percentage: float = Field(ge=0.0, le=100.0, default=0.0)
    total_clone_groups: int = Field(ge=0, default=0)
    unused_symbol_count: int = Field(ge=0, default=0)
    code_smell_count: int = Field(ge=0, default=0)


class PhaseTiming(FrozenModel):
    """Timing data for an analysis phase."""

    phase: int = Field(description="Phase number (1-based)")
    name: str = Field(description="Phase name (e.g., 'indexing', 'team-structure', 'synthesis')")
    start_offset_seconds: float = Field(ge=0.0, default=0.0, description="Seconds from analysis start")
    duration_seconds: float = Field(ge=0.0, description="Phase duration in seconds")
    tool_count: int = Field(ge=0, default=0, description="Number of tool calls in this phase")
    status: str = Field(default="completed", description="Phase status")


class AreaRiskProfile(FrozenModel):
    """Risk profile for a specific codebase area, used for review routing."""

    area: str = Field(description="Functional area (e.g., 'auth', 'payments', 'config')")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        description="Overall risk level for changes in this area",
    )
    blast_radius: int = Field(ge=0, default=0, description="Number of upstream dependents (from gitnexus_impact)")
    churn_rank: int = Field(ge=0, default=0, description="Churn ranking position (1 = highest churn)")
    complexity_score: float = Field(ge=0.0, default=0.0, description="Average cyclomatic complexity")
    test_coverage: Literal["high", "medium", "low", "none"] = Field(
        default="low",
        description="Estimated test coverage level",
    )
    contributor_count: int = Field(ge=0, default=0, description="Number of distinct contributors")
    review_recommendation: Literal["auto_approve", "single_review", "dual_review", "expert_review"] = Field(
        default="single_review",
        description="Recommended review level for changes in this area",
    )
    rationale: str = Field(default="", description="Why this review level was recommended")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Calibrated confidence in this area's risk assessment",
    )
    information_gaps: list[str] = Field(
        default_factory=list,
        description="What couldn't be determined for this area (e.g., 'no test coverage data')",
    )


class RiskProfile(FrozenModel):
    """Overall codebase risk profile for review routing decisions."""

    overall_risk: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Aggregate risk level across all areas",
    )
    areas: list[AreaRiskProfile] = Field(
        default_factory=list,
        description="Per-area risk profiles with review recommendations",
    )
    high_risk_paths: list[str] = Field(
        default_factory=list,
        description="File paths that always require dual review",
    )
    auto_approvable_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns for auto-approvable changes (e.g., docs, config, tests)",
    )


# ---------------------------------------------------------------------------
# Phase 2: Confidence Calibration
# ---------------------------------------------------------------------------


class AnalysisConfidence(FrozenModel):
    """Confidence metadata attached to any analysis judgment."""

    score: float = Field(ge=0.0, le=1.0, default=0.5, description="Calibrated confidence (0.5=uncertain, 0.9+=high)")
    evidence_sources: list[str] = Field(default_factory=list, description="Data sources supporting this judgment")
    information_gaps: list[str] = Field(default_factory=list, description="What couldn't be determined")
    agreement_level: Literal["unanimous", "majority", "split", "contested"] = Field(
        default="unanimous",
        description="Level of agreement among analysis teams",
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
    confidence: AnalysisConfidence = Field(default_factory=AnalysisConfidence)
    reasoning_chain: list[str] = Field(default_factory=list, description="Step-by-step reasoning")
    counterfactual: str | None = Field(
        default=None,
        description="What would change the judgment (e.g., 'adding tests would lower risk')",
    )


# ---------------------------------------------------------------------------
# Phase 3: Architectural Pattern Persistence
# ---------------------------------------------------------------------------


class ArchitecturalPattern(FrozenModel):
    """A codified architectural pattern detected in the codebase."""

    name: str = Field(description="Pattern name (e.g., 'repository-pattern', 'event-driven-command')")
    description: str = Field(description="What this pattern looks like in this codebase")
    exemplar_files: list[str] = Field(default_factory=list, description="Files that best demonstrate the pattern")
    enforced_by: Literal["types", "tests", "convention", "none"] = Field(
        default="convention",
        description="How the pattern is currently enforced",
    )
    violation_count: int = Field(ge=0, default=0, description="Known existing violations")
    communities: list[str] = Field(default_factory=list, description="GitNexus communities where this applies")


class PatternViolation(FrozenModel):
    """A specific deviation from an established pattern."""

    pattern_name: str = Field(description="Name of the violated pattern")
    violation: str = Field(description="What the change does differently")
    severity: Literal["style", "structural", "architectural"] = Field(
        default="style",
        description="How severe the deviation is",
    )
    suggested_fix: str | None = Field(default=None, description="How to align with the pattern")
    is_intentional_evolution: bool = Field(
        default=False,
        description="True if this appears to be deliberate pattern evolution vs. accidental drift",
    )


class ConsistencyReport(FrozenModel):
    """How consistent a set of changes is with established patterns."""

    consistent_patterns: list[str] = Field(default_factory=list, description="Patterns the changes follow")
    violated_patterns: list[PatternViolation] = Field(default_factory=list)
    novel_patterns: list[str] = Field(
        default_factory=list,
        description="New patterns introduced (may be intentional evolution)",
    )
    overall_consistency: float = Field(ge=0.0, le=1.0, default=1.0)


# ---------------------------------------------------------------------------
# Phase 4: Temporal Risk Intelligence
# ---------------------------------------------------------------------------


class RiskSnapshot(FrozenModel):
    """Point-in-time risk measurement for a codebase area."""

    timestamp: str = Field(description="ISO 8601 timestamp of this measurement")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(default="medium")
    blast_radius: int = Field(ge=0, default=0)
    churn_rank: int = Field(ge=0, default=0)
    contributor_count: int = Field(ge=0, default=0)
    test_coverage: Literal["high", "medium", "low", "none"] = Field(default="low")


class TemporalRiskProfile(FrozenModel):
    """Risk trajectory for a codebase area across analysis snapshots."""

    area: str = Field(description="Functional area name")
    current_risk: Literal["low", "medium", "high", "critical"] = Field(default="medium")
    risk_trend: Literal["improving", "stable", "degrading", "accelerating_risk"] = Field(default="stable")
    churn_velocity: float = Field(default=0.0, description="Rate of change in churn (positive = accelerating)")
    contributor_trend: Literal["gaining", "stable", "losing"] = Field(default="stable")
    test_coverage_trend: Literal["improving", "stable", "declining"] = Field(default="stable")
    complexity_trend: Literal["simplifying", "stable", "growing"] = Field(default="stable")
    projected_risk_30d: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Projected risk level in 30 days based on current trajectory",
    )
    history: list[RiskSnapshot] = Field(default_factory=list, description="Last N analysis snapshots")


# ---------------------------------------------------------------------------
# Phase 5: Cross-Repo Intelligence
# ---------------------------------------------------------------------------


class ContractConsumer(FrozenModel):
    """A repo that consumes a service contract."""

    repo: str = Field(description="Consumer repository name or path")
    usage_locations: list[str] = Field(default_factory=list, description="File:line references")
    criticality: Literal["blocking", "degraded", "cosmetic"] = Field(
        default="degraded",
        description="How critical this consumption is",
    )


class ServiceContract(FrozenModel):
    """A detected interface contract between repos."""

    source_repo: str = Field(description="Repository that owns the contract")
    source_symbol: str = Field(description="API endpoint, exported function, schema, etc.")
    contract_type: Literal["api_endpoint", "shared_schema", "event_topic", "grpc_service", "sdk_export"] = Field(
        description="Type of interface contract",
    )
    consumers: list[ContractConsumer] = Field(default_factory=list)


class CrossRepoImpact(FrozenModel):
    """Cross-repository impact assessment for a set of changes."""

    changed_contracts: list[ServiceContract] = Field(
        default_factory=list,
        description="Service contracts modified by this diff",
    )
    affected_repos: list[str] = Field(default_factory=list, description="Repos consuming changed contracts")
    affected_teams: list[str] = Field(default_factory=list, description="Teams owning affected repos")
    breaking_changes: list[str] = Field(default_factory=list, description="Contract changes that break consumers")
    verdict_modifier: Literal["none", "escalate_one", "escalate_two", "block"] = Field(
        default="none",
        description="How cross-repo impact modifies the change verdict",
    )


# ---------------------------------------------------------------------------
# Phase 1: Change Verdict Engine (existing)
# ---------------------------------------------------------------------------


class VerdictSignal(FrozenModel):
    """A single signal contributing to a change verdict."""

    signal_type: Literal[
        "blast_radius",
        "churn_rate",
        "bus_factor",
        "test_gap",
        "security_finding",
        "pattern_violation",
        "cross_community",
        "ownership_gap",
        "complexity_spike",
    ] = Field(description="Category of risk signal")
    severity: Literal["info", "warning", "escalation", "block"] = Field(
        description="How severely this signal impacts the verdict",
    )
    description: str = Field(description="Human-readable explanation of this signal")
    source: str = Field(description="Tool or data source that produced this signal")
    weight: float = Field(ge=0.0, le=1.0, description="How much this signal influenced the verdict")


class ReviewerRecommendation(FrozenModel):
    """Recommended reviewer with rationale."""

    identity: str = Field(description="Git author email or team name")
    reason: Literal["area_expert", "recent_contributor", "bus_factor_mitigation", "security_specialist"] = Field(
        description="Why this reviewer is recommended",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.5, description="Confidence in this recommendation")


class DecisionBoundary(FrozenModel):
    """Transparency about how close a verdict is to changing tier."""

    current_verdict: str = Field(description="The current verdict tier")
    next_higher_verdict: str = Field(description="The next more restrictive tier")
    distance: float = Field(ge=0.0, le=1.0, description="0.0=firmly in tier, 1.0=about to flip")
    escalation_triggers: list[str] = Field(
        default_factory=list,
        description="What would escalate (e.g., 'one more untested code path')",
    )
    de_escalation_triggers: list[str] = Field(
        default_factory=list,
        description="What would de-escalate (e.g., 'add test for validateCard')",
    )


class ChangeVerdict(FrozenModel):
    """Machine-readable verdict for a set of code changes.

    Produced by the deterministic verdict engine from a diff against
    pre-computed codebase context (risk profiles, GitNexus structural data,
    git history). Designed for CI/CD consumption.
    """

    verdict: Literal["auto_merge", "single_review", "dual_review", "expert_review", "block"] = Field(
        description="Recommended action for this change set",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Calibrated confidence in verdict")

    # What changed (structural)
    affected_symbols: list[str] = Field(default_factory=list, description="Functions/classes/methods touched")
    affected_communities: list[str] = Field(default_factory=list, description="GitNexus communities impacted")
    affected_processes: list[str] = Field(default_factory=list, description="Execution flows traversing changed code")
    blast_radius: int = Field(ge=0, default=0, description="Total upstream dependents across all changed symbols")

    # Why this verdict
    signals: list[VerdictSignal] = Field(default_factory=list, description="Individual signals composing the verdict")
    escalation_reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable reasons if escalated beyond auto_merge",
    )
    reasoning_chain: list[str] = Field(
        default_factory=list,
        description="Step-by-step auditable reasoning (e.g., '1. gitnexus_impact: 47 callers')",
    )

    # Decision boundary transparency
    decision_boundary: DecisionBoundary | None = Field(
        default=None,
        description="How close the verdict is to flipping to the next tier",
    )

    # Who should review
    recommended_reviewers: list[ReviewerRecommendation] = Field(default_factory=list)

    # Test gap analysis
    untested_paths: list[str] = Field(
        default_factory=list,
        description="New/modified code paths with no test coverage",
    )

    # Files changed
    files_changed: list[str] = Field(default_factory=list, description="File paths in the diff")


class IndexFreshness(FrozenModel):
    """Tracks how current the pre-computed analysis is."""

    last_full_analysis: str | None = Field(default=None, description="ISO 8601 timestamp of last full analysis")
    last_incremental_index: str | None = Field(
        default=None,
        description="ISO 8601 timestamp of last incremental index",
    )
    commits_since_last_full: int = Field(ge=0, default=0, description="Commits to main since last full analysis")
    freshness: Literal["current", "stale", "outdated"] = Field(
        default="current",
        description="current=<48h, stale=48h-7d, outdated=>7d since last full analysis",
    )
    confidence_penalty: float = Field(
        ge=0.0,
        le=0.5,
        default=0.0,
        description="Subtracted from all confidence scores due to staleness",
    )


class VerdictResponse(FrozenModel):
    """Top-level CI/CD output consumed by pipelines and bots."""

    verdict: ChangeVerdict = Field(description="The change verdict with signals and reasoning")
    index_freshness: IndexFreshness = Field(
        default_factory=IndexFreshness,
        description="How current the underlying analysis data is",
    )

    # CI-actionable fields
    exit_code: int = Field(ge=0, le=4, description="0=auto_merge, 1=needs_review, 2=expert, 3=block, 4=error")
    should_block: bool = Field(default=False, description="Whether CI should block the merge")
    review_comment_markdown: str = Field(
        default="",
        description="Pre-formatted PR comment with verdict explanation",
    )
    github_labels: list[str] = Field(
        default_factory=list,
        description="Suggested PR labels (e.g., 'needs-security-review', 'auto-approvable')",
    )


class Bundle(FrozenModel):
    """A narrative bundle about a specific codebase area."""

    area: str = Field(description="Bundle area identifier (e.g., 'auth', 'hotspots', 'security')")
    path: str = Field(description="Relative path to the bundle file")
    line_count: int = Field(ge=0, description="Number of lines in the bundle")
    summary: str = Field(description="One-sentence summary of this bundle's content")
    focus_match: bool = Field(default=False, description="Whether this bundle was generated for the --focus area")


class AnalysisResult(FrozenModel):
    """Structured output for the complete analysis.

    This model captures the analysis summary returned by the coordinator agent.
    The actual files (CONTEXT.md, bundles, etc.) are created by tools.
    """

    status: str = Field(description="completed, partial, or failed")
    summary: str = Field(description="2-3 sentence executive summary")
    total_files_analyzed: int = Field(ge=0, description="Number of files in the repository")
    business_logic_items: list[BusinessLogicItem] = Field(
        default_factory=list,
        description="Ranked business logic items",
    )
    risks: list[ArchitecturalRisk] = Field(
        default_factory=list,
        description="Identified architectural risks",
    )
    generated_files: list[GeneratedFile] = Field(
        default_factory=list,
        description="Files created during analysis",
    )
    refactoring_candidates: list[RefactoringCandidate] = Field(
        default_factory=list,
        description="Suggested refactoring opportunities from code health analysis",
    )
    code_health: CodeHealthMetrics | None = Field(
        default=None,
        description="Aggregate code health metrics",
    )
    bundles: list[Bundle] = Field(
        default_factory=list,
        description="Generated narrative bundles for codebase areas",
    )
    risk_profile: RiskProfile | None = Field(
        default=None,
        description="Risk profile for review routing decisions (per-area risk levels and review recommendations)",
    )
    phase_timings: list[PhaseTiming] = Field(
        default_factory=list,
        description="Timing data for each analysis phase",
    )
    analysis_mode: str = Field(default="standard", description="Analysis mode used")
