"""MCP provider builders for the codebase-analysis use case.

Ported from the pre-split ``agent/factory.py`` (L91-164). Each builder returns an
``MCPClient`` (a strands ``ToolProvider`` the Agent handles natively) or ``None``
when the provider is disabled or ``npx`` is unavailable — so the coordinator is
constructed with whatever providers are actually reachable rather than aborting
(AC-6-6).

Per AC-8-6 / AC-7-1, each ``MCPClient`` is constructed with
``continue_on_error=True`` so a failing npx-launched server degrades gracefully
instead of aborting the run.

Imports of ``strands``/``mcp`` are kept inside the builder functions (the
factory pattern) so importing this module stays cheap and circular-import-safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from .config import Settings


def build_gitnexus_provider(settings: Settings) -> Any | None:
    """Create an ``MCPClient`` for the GitNexus code-intelligence server.

    Args:
        settings: The active use-case settings (``gitnexus_enabled`` toggle).

    Returns:
        The ``MCPClient`` provider, or ``None`` if GitNexus is disabled or
        ``npx`` is not available (AC-6-6).
    """
    if not settings.gitnexus_enabled:
        logger.info("GitNexus MCP server disabled via settings")
        return None

    import shutil

    if not shutil.which("npx"):
        logger.warning("npx not found, skipping GitNexus MCP server")
        return None

    try:
        from mcp import StdioServerParameters, stdio_client
        from strands.tools.mcp import MCPClient

        gitnexus = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command="npx",
                    args=["gitnexus", "mcp"],
                ),
            ),
            prefix="gitnexus",
            continue_on_error=True,
        )
        logger.info("GitNexus MCP server configured (tools will be prefixed with 'gitnexus_')")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to configure GitNexus MCP server: {e}")
        return None
    else:
        return gitnexus


def build_context7_provider(settings: Settings) -> Any | None:
    """Create an ``MCPClient`` for the context7 documentation server.

    Args:
        settings: The active use-case settings (``context7_enabled`` toggle).

    Returns:
        The ``MCPClient`` provider, or ``None`` if context7 is disabled or
        ``npx`` is not available (AC-6-6).
    """
    if not settings.context7_enabled:
        logger.info("context7 MCP server disabled via settings")
        return None

    import shutil

    if not shutil.which("npx"):
        logger.warning("npx not found, skipping context7 MCP server")
        return None

    try:
        from mcp import StdioServerParameters, stdio_client
        from strands.tools.mcp import MCPClient

        context7 = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command="npx",
                    args=["-y", "@upstash/context7-mcp@1"],
                ),
            ),
            prefix="context7",
            continue_on_error=True,
        )
        logger.info("context7 MCP server configured (tools will be prefixed with 'context7_')")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to configure context7 MCP server: {e}")
        return None
    else:
        return context7
