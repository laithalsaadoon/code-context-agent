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

    # Telemetry
    otel_disabled: bool = Field(default=True, description="Disable OpenTelemetry tracing")
