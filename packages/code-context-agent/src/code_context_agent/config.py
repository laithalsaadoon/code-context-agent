"""Configuration module using pydantic-settings.

This module provides application configuration management through environment
variables using pydantic-settings. Settings are loaded from environment variables
with the CODE_CONTEXT_ prefix and optionally from a .env file.

Example:
    >>> from code_context_agent.config import get_settings
    >>> settings = get_settings()
    >>> print(settings.app_name)
    'code-context-agent'

Environment Variables:
    CODE_CONTEXT_APP_NAME: Application name (default: "code-context-agent")
    CODE_CONTEXT_DEBUG: Enable debug mode (default: False)
    CODE_CONTEXT_LOG_LEVEL: Logging level (default: "INFO")
    CODE_CONTEXT_OUTPUT_FORMAT: Output format - "rich", "json", or "plain" (default: "rich")
    CODE_CONTEXT_MODEL_ID: Bedrock model ID for the agent (default: Opus 4.6)
    CODE_CONTEXT_REGION: AWS region for Bedrock
    CODE_CONTEXT_TEMPERATURE: Model temperature (default 1.0 for thinking)
    CODE_CONTEXT_AGENT_MAX_TURNS: Maximum agent turns before stopping (default: 1000)
    CODE_CONTEXT_AGENT_MAX_DURATION: Maximum agent duration in seconds (default: 1200)
    CODE_CONTEXT_OTEL_DISABLED: Disable OpenTelemetry tracing (default: True)
"""

from __future__ import annotations

import functools

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from strands_teams import TeamsSettings

DEFAULT_OUTPUT_DIR = ".code-context"
"""Default output directory name for analysis artifacts."""


class Settings(TeamsSettings):
    """Application settings loaded from environment variables.

    Subclasses :class:`strands_teams.TeamsSettings`, which carries the
    domain-agnostic model, execution, and team-timeout knobs. This subclass
    parameterizes the env prefix to ``CODE_CONTEXT_``, sets the ``.code-context``
    output directory, and adds the two codebase-analysis-specific MCP toggles
    (``gitnexus_enabled`` / ``context7_enabled``).

    Settings are loaded from environment variables prefixed with CODE_CONTEXT_
    and optionally from a .env file in the current working directory.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODE_CONTEXT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application name override (TeamsSettings defaults to "strands-teams").
    app_name: str = Field(
        default="code-context-agent",
        description="Application name used for identification and logging",
    )

    # Output directory for analysis artifacts, relative to the target root.
    output_dir_name: str = Field(
        default=DEFAULT_OUTPUT_DIR,
        description="Directory name for run artifacts, relative to the target root",
    )

    # MCP tool sources for the analysis agent
    gitnexus_enabled: bool = Field(
        default=True,
        description="Enable GitNexus MCP server for structural code intelligence during analysis",
    )
    context7_enabled: bool = Field(
        default=True,
        description="Enable context7 MCP server for library documentation lookup during analysis",
    )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get application settings instance (cached singleton).

    Returns the same Settings instance on subsequent calls. Settings are
    loaded from environment variables and the optional .env file on first call.

    Call ``get_settings.cache_clear()`` if you need to reload from environment
    (e.g., in tests).

    Returns:
        Settings instance loaded from environment.

    Example:
        >>> settings = get_settings()
        >>> settings.debug
        False
        >>> settings.log_level
        'INFO'
    """
    return Settings()
