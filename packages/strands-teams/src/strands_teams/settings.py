"""Framework settings base for strands_teams use cases.

``TeamsSettings`` carries the domain-agnostic model, execution, and team-timeout
knobs shared by every use case. A use case subclasses it to set its own env
prefix and output-directory default, plus any domain-specific fields.

Adapted from the pre-split ``code_context_agent.config.Settings``, dropping the
codebase-analysis-specific fields (``gitnexus_enabled``, ``context7_enabled``)
which belong to the use-case subclass.

Env prefix parameterization
----------------------------
``pydantic-settings`` reads ``env_prefix`` from the class-level ``model_config``.
A subclass parameterizes it by overriding ``model_config``::

    class MyToolSettings(TeamsSettings):
        model_config = SettingsConfigDict(env_prefix="MY_TOOL_", extra="ignore")
        output_dir_name: str = ".my-tool"
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TeamsSettings(BaseSettings):
    """Base settings for a strands_teams use case.

    Subclass this and override ``model_config`` to set the env prefix, and set
    ``output_dir_name`` to the use case's artifact directory name.

    Attributes:
        output_dir_name: Directory name for run artifacts, relative to the
            target root (e.g., ``.code-context``). Use cases override the default.
    """

    model_config = SettingsConfigDict(
        env_prefix="TEAMS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    output_dir_name: str = Field(
        default=".teams",
        description="Directory name for run artifacts, relative to the target root",
    )

    # Application / display
    app_name: str = Field(default="strands-teams", description="Application name for identification and logging")
    debug: bool = Field(default=False, description="Enable debug mode for verbose output")
    log_level: str = Field(default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    output_format: Literal["rich", "json", "plain"] = Field(default="rich", description="Output format for CLI")

    # Model settings
    model_id: str = Field(default="global.anthropic.claude-opus-4-6-v1", description="Bedrock model ID")
    region: str = Field(default="us-east-1", description="AWS region for Bedrock API calls")
    temperature: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Model temperature (must be 1.0 when thinking is enabled)",
    )
    reasoning_effort: Literal["low", "medium", "high", "max"] = Field(
        default="high",
        description="Reasoning effort level for standard mode",
    )
    full_reasoning_effort: Literal["low", "medium", "high", "max"] = Field(
        default="max",
        description="Reasoning effort level for full mode (Opus only)",
    )

    # Coordinator execution bounds
    agent_max_turns: int = Field(default=1000, ge=10, le=5000, description="Max coordinator turns before stopping")
    agent_max_duration: int = Field(default=1200, ge=60, le=7200, description="Max coordinator duration (seconds)")
    full_max_duration: int = Field(default=3600, ge=300, le=14400, description="Max duration for full mode (seconds)")
    full_max_turns: int = Field(default=3000, ge=100, le=10000, description="Max turns for full mode")

    # Team (swarm) timeout bounds — defaults when the coordinator omits explicit values.
    team_execution_timeout: int = Field(
        default=900,
        ge=120,
        le=7200,
        description="Max seconds for entire team swarm execution (standard mode)",
    )
    team_node_timeout: int = Field(
        default=900,
        ge=120,
        le=7200,
        description="Max seconds per agent node within a team (standard mode)",
    )
    full_team_execution_timeout: int = Field(
        default=2400,
        ge=300,
        le=14400,
        description="Max seconds for entire team swarm execution (full mode)",
    )
    full_team_node_timeout: int = Field(
        default=1800,
        ge=300,
        le=14400,
        description="Max seconds per agent node within a team (full mode)",
    )

    # Durable lead loop (Phase 5 / spec 006)
    budget_threshold_usd: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Cumulative-cost bound (USD) for the durable lead loop; when recorded resource "
            "usage crosses it, should_replan fires budget.threshold_exceeded. 0 disables the "
            "cumulative-cost trigger (an explicit budget.threshold_exceeded event still fires)."
        ),
    )
    max_lead_iterations: int = Field(
        default=8,
        ge=1,
        le=100,
        description="Max decompose→…→integrate iterations run_goal executes before terminating (AC-5-6).",
    )

    # Authority gates + policy engine (Phase 6 / spec 007)
    policy_signing_key: str = Field(
        default="",
        description=(
            "Signing key the PolicyEngine uses to mint/verify ApprovalToken signatures (spec 007 "
            "AC-1-3/AC-1-4). An empty key means no run-configured key; the engine treats every token as "
            "signature-invalid (absent), so approvals must be minted with an explicit key to count."
        ),
    )
    required_approvals: int = Field(
        default=1,
        ge=0,
        le=16,
        description=(
            "Number of valid, non-expired, signature-verified, revision-bound ApprovalTokens a "
            "consequential transition requires before the PolicyEngine allows it (spec 007 AC-3-4)."
        ),
    )
    human_gated_transition_kinds: tuple[str, ...] = Field(
        default=(),
        description=(
            "Transition kinds that are human-gated: the PolicyEngine emits a pending HumanDecision and "
            "denies with failed_gate='human_required' rather than letting the model self-approve (spec 007 "
            "AC-4-1). Empty means no kind is human-gated."
        ),
    )

    # Telemetry
    otel_disabled: bool = Field(default=True, description="Disable OpenTelemetry tracing")
