"""Jinja2 environment factory for agent-prompt templates.

Adapted from the pre-split ``code_context_agent.templates`` package initializer.
Generalized so the template directory is a parameter rather than the module's
own directory: each use case supplies its own template dir.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape


@cache
def _get_environment(template_dir: str) -> Environment:
    """Build (and cache) a configured Jinja2 environment for a template dir.

    Cached per resolved template-dir string so repeated renders reuse one
    environment.

    Args:
        template_dir: Absolute path (as a string) to the template directory.

    Returns:
        Configured Jinja2 ``Environment``.
    """
    return Environment(
        loader=FileSystemLoader(template_dir),
        undefined=StrictUndefined,
        autoescape=select_autoescape(),  # defaults: html/htm/xml — .md.j2 templates are unaffected
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def make_environment(template_dir: str | Path) -> Environment:
    """Create a Jinja2 environment rooted at ``template_dir``.

    Args:
        template_dir: Directory containing ``.md.j2`` prompt templates.

    Returns:
        Configured Jinja2 ``Environment`` (cached per directory).
    """
    return _get_environment(str(Path(template_dir).resolve()))


def render_prompt(template_dir: str | Path, template_name: str, **context: object) -> str:
    """Render a prompt template from a use-case template directory.

    Args:
        template_dir: Directory containing the template.
        template_name: Template filename (e.g., ``"coordinator.md.j2"``).
        **context: Variables passed to the template.

    Returns:
        Rendered prompt string.
    """
    env = make_environment(template_dir)
    template = env.get_template(template_name)
    return template.render(**context)
