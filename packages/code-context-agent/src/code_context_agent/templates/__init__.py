"""Jinja2 template loading and rendering for agent prompts.

This module provides a configured Jinja2 environment for rendering
prompt templates. Templates are loaded from this directory using
FileSystemLoader with StrictUndefined to catch missing variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape


@lru_cache(maxsize=1)
def _get_environment() -> Environment:
    """Get the configured Jinja2 environment (cached singleton)."""
    template_dir = Path(__file__).parent
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        autoescape=select_autoescape(),  # defaults: html/htm/xml — .md.j2 templates are unaffected
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_prompt(template_name: str, **context: Any) -> str:
    """Render a prompt template with the given context.

    Args:
        template_name: Template filename (e.g., "system.md.j2")
        **context: Variables to pass to the template

    Returns:
        Rendered prompt string
    """
    env = _get_environment()
    template = env.get_template(template_name)
    return template.render(**context)
