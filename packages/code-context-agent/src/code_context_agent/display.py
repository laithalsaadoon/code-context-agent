"""Rich display utilities for code-context-agent.

This module provides display functions using Rich for beautiful terminal output.
It includes utilities for rendering markdown content, tables, and styled text.

Example:
    >>> from code_context_agent.config import get_settings
    >>> from code_context_agent.display import display_welcome
    >>> settings = get_settings()
    >>> display_welcome(settings)
"""

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from code_context_agent.config import Settings

# Module-level console instance for consistent output
console = Console()

WELCOME_MARKDOWN = """\
# Code Context Agent

Welcome to **Code Context Agent** - your CLI tool for code analysis.

## Features

- Fast code context extraction
- Multiple output formats
- Configurable via environment variables
"""


def create_settings_table(settings: Settings) -> Table:
    """Create a Rich Table displaying the current settings.

    Builds a formatted table showing all configuration values with
    their current settings for easy visual inspection.

    Args:
        settings: The Settings instance containing configuration values.

    Returns:
        A Rich Table object ready for console display.

    Example:
        >>> from code_context_agent.config import Settings
        >>> settings = Settings(debug=True)
        >>> table = create_settings_table(settings)
        >>> console.print(table)
    """
    table = Table(title="Current Settings", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="green")
    table.add_column("Value", style="yellow")

    table.add_row("app_name", settings.app_name)
    table.add_row("debug", str(settings.debug))
    table.add_row("log_level", settings.log_level)
    table.add_row("output_format", settings.output_format)

    return table


def display_welcome(settings: Settings) -> None:
    """Display the welcome message with current settings.

    Renders the welcome markdown content followed by a table showing
    the current configuration settings. This provides users with
    immediate feedback about the application state.

    Args:
        settings: The Settings instance containing configuration values.

    Example:
        >>> from code_context_agent.config import get_settings
        >>> settings = get_settings()
        >>> display_welcome(settings)
    """
    # Display welcome markdown
    markdown = Markdown(WELCOME_MARKDOWN)
    console.print(markdown)
    console.print()

    # Display settings table
    table = create_settings_table(settings)
    console.print(table)
    console.print()

    # Display feature list as additional information
    if settings.debug:
        console.print("[dim]Debug mode is enabled - verbose output active[/dim]")
