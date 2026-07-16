"""Generic, output-confined ``write_file`` tool for team agents.

Team agents need a way to persist findings and artifacts, but must never write
outside the run's output-directory root. This module builds a ``write_file``
``@tool`` bound to a specific :class:`~strands_teams.layout.TeamsLayout` via
:func:`make_write_file` — the coordinator factory auto-appends the built tool to
every team agent's tool list (AC-5-4), and :func:`dispatch_team` also injects it
when a team spec omits it.

Confinement is enforced by :func:`strands_teams.security.confine_write_path`,
which rejects traversal and absolute escapes. Ported from the pre-split
``discovery.py:1020-1076`` ``write_file`` semantics, generalized from the
hardcoded ``.code-context`` check to the layout's ``output_root``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger
from strands import tool

from .security import ValidationError, confine_write_path

if TYPE_CHECKING:
    from strands.tools.decorator import DecoratedFunctionTool

    from .layout import TeamsLayout


def make_write_file(layout: TeamsLayout) -> DecoratedFunctionTool:
    """Build a ``write_file`` tool confined to ``layout.output_root``.

    The returned tool is a normal strands ``@tool`` whose ``tool_name`` is
    ``write_file``. It closes over ``layout`` so it needs no module-level state.

    Args:
        layout: The run's layout, whose ``output_root`` all writes must stay under.

    Returns:
        A decorated ``write_file`` tool object.
    """
    output_root = layout.output_root

    @tool(name="write_file")
    def write_file(file_path: str, content: str) -> str:
        """Write content to a file inside the run's output directory.

        USE THIS TOOL:
        - To write your team's findings.md and metadata.json.
        - To save any generated documentation or artifact.

        Security: writes are confined to the run's output directory. Paths that
        escape it (via ``..`` or an absolute path outside the root) are rejected.

        Args:
            file_path: Path to write. Absolute (inside the output root) or
                relative to the output root.
            content: String content to write.

        Returns:
            JSON with status, absolute path, and bytes written.
        """
        try:
            path = confine_write_path(file_path, output_root)
        except ValidationError as e:
            return json.dumps({"status": "error", "message": str(e)})

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as e:
            return json.dumps({"status": "error", "message": str(e)})

        logger.info(f"write_file: wrote {len(content)} bytes to {path}")
        return json.dumps(
            {
                "status": "success",
                "path": str(path),
                "bytes_written": len(content),
            },
        )

    return write_file
