"""Serialize and parse typed ``Finding`` collections to/from ``findings.json``.

The write path (``write_findings``) and the read path (``read_team_findings``)
share these helpers so the on-disk shape has exactly one producer and one
consumer. Both validate through pydantic over parsed JSON at the parse boundary
(AC-8-4): a ``findings.json`` is a JSON array of finding objects, each validated
by :class:`~strands_teams.models.Finding`. Unknown keys or out-of-range values
are rejected (``FrozenModel`` is ``extra="forbid"``) rather than silently
coerced, so malformed on-disk content is caught here, not downstream.

None of these helpers raise across the tool boundary for expected failures —
:func:`parse_findings` raises ``pydantic.ValidationError`` / ``ValueError`` which
the callers catch and turn into a ``{"status": "error", ...}`` result or a
markdown fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from .models import Finding

if TYPE_CHECKING:
    from pathlib import Path

_FINDINGS_ADAPTER = TypeAdapter(list[Finding])
"""Validates and serializes a ``list[Finding]`` in one place."""


def serialize_findings(findings: list[Finding]) -> str:
    """Serialize a list of ``Finding`` objects to a JSON-array string.

    Uses the model's ``model_dump_json``-equivalent form via a ``TypeAdapter`` so
    datetimes and nested ``Evidence`` models round-trip through pydantic (AC-3-2,
    AC-8-1).
    """
    return _FINDINGS_ADAPTER.dump_json(findings, indent=2).decode("utf-8")


def parse_findings(raw: str | bytes | bytearray) -> list[Finding]:
    """Parse and validate a ``findings.json`` payload into typed ``Finding`` objects.

    Validates JSON text (not an arbitrary Python object) so malformed on-disk
    content is caught at the parse boundary (AC-8-4).

    Raises:
        pydantic.ValidationError: If the payload is not a JSON array of valid
            ``Finding`` objects.
    """
    return _FINDINGS_ADAPTER.validate_json(raw)


def coerce_findings(data: Any) -> list[Finding]:
    """Validate an already-parsed Python object (e.g. a list of dicts) into findings.

    Used by the write path, whose input arrives as tool arguments (dicts), not as
    a JSON string. Still routes through the same adapter so the schema is enforced
    identically (AC-3-4).

    Raises:
        pydantic.ValidationError: If any element is not a valid ``Finding``.
    """
    return _FINDINGS_ADAPTER.validate_python(data)


def load_findings(path: Path) -> list[Finding]:
    """Read and validate the typed findings at ``path``.

    Raises:
        OSError: If the file cannot be read.
        pydantic.ValidationError | json.JSONDecodeError: If the content is not a
            valid findings array.
    """
    return parse_findings(path.read_text(encoding="utf-8"))


def findings_as_dicts(findings: list[Finding]) -> list[dict[str, Any]]:
    """Return findings as JSON-mode dicts for embedding in a tool's JSON result.

    ``mode="json"`` renders datetimes as ISO strings so the surrounding
    ``json.dumps`` in the tool result does not choke on ``datetime`` objects.
    """
    return [f.model_dump(mode="json") for f in findings]
