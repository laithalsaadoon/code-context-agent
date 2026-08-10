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
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError as PydanticValidationError
from strands import tool

from .evidence_gate import gate_findings
from .findings_io import coerce_findings, serialize_findings
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


def make_write_findings(layout: TeamsLayout, team_id: str) -> DecoratedFunctionTool:
    """Build a ``write_findings`` tool that persists typed findings for one team.

    The returned tool validates its input against the :class:`~strands_teams.models.Finding`
    schema, serializes the collection to the team's ``findings.json`` (via
    :meth:`~strands_teams.layout.TeamsLayout.findings_json_path`), and flags any
    ``blocking=True`` finding that lacks evidence through the shared evidence gate
    (AC-3-2, AC-3-4, AC-5-2). It closes over ``layout`` and ``team_id`` so it needs
    no module-level state and writes only inside the run's team directory.

    Args:
        layout: The run's layout; the sidecar path derives from it.
        team_id: The team whose ``findings.json`` this tool writes.

    Returns:
        A decorated ``write_findings`` tool object.
    """

    @tool(name="write_findings")
    def write_findings(findings: list[dict[str, Any]]) -> str:
        """Write this team's typed findings to its ``findings.json`` sidecar.

        Call this once, as the LAST agent, IN ADDITION to writing the human
        ``findings.md``. Each finding is a JSON object with these keys:

        - ``statement`` (str, required): the finding in one or two sentences.
        - ``severity`` (str, required): one of ``info``/``low``/``medium``/``high``/``critical``.
        - ``blocking`` (bool): must be resolved before acceptance (default false).
          A blocking finding MUST carry at least one ``evidence`` ref.
        - ``disposition`` (str): ``open``/``acknowledged``/``remediated``/
          ``accepted_risk``/``rejected``/``superseded`` (default ``open``).
        - ``affected_artifacts`` (list[str]): paths/ids the finding is about.
        - ``evidence`` (list): each ``{"type": one of file_line|test|command|artifact|url|log,
          "uri": "src/auth.py:42", "detail": "optional note"}``.
        - ``confidence`` (float in [0,1], default 0.5).
        - ``created_by`` (str): your team/agent name.

        Args:
            findings: The list of finding objects to persist.

        Returns:
            JSON with ``status``, absolute ``path``, ``count``, and an
            ``evidence_gate`` summary flagging any blocking finding without
            evidence. On a schema violation, ``status`` is ``"error"`` and the
            file is not written.
        """
        try:
            parsed = coerce_findings(findings)
        except PydanticValidationError as e:
            return json.dumps({"status": "error", "message": f"findings failed schema validation: {e}"})

        path = layout.findings_json_path(team_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(serialize_findings(parsed), encoding="utf-8")
        except OSError as e:
            return json.dumps({"status": "error", "message": str(e)})

        gate = gate_findings(parsed)
        logger.info(f"write_findings: wrote {len(parsed)} findings for {team_id} to {path}")
        return json.dumps(
            {
                "status": "success",
                "path": str(path),
                "count": len(parsed),
                "evidence_gate": gate,
            },
        )

    return write_findings
