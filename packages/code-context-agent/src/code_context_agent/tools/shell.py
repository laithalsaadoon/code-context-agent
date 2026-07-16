"""Standardized tool-response helper.

Provides :class:`ToolResult`, the JSON-serialization pattern shared by the
git/discovery tools. Bounded shell execution (``run_command`` / ``CommandResult``)
and the path validators now live in :mod:`strands_teams.security`; this module
keeps only the domain response helper.
"""

from __future__ import annotations

import json
from typing import Any

from ..models.base import FrozenModel


class ToolResult(FrozenModel):
    """Standardized result structure for tool responses.

    Provides a consistent JSON serialization pattern for tool outputs.

    Example:
        >>> result = ToolResult(status="success", data={"count": 42})
        >>> return result.to_json()
        '{"status": "success", "data": {"count": 42}}'

        >>> result = ToolResult.error("File not found")
        >>> return result.to_json()
        '{"status": "error", "error": "File not found"}'
    """

    status: str
    data: dict[str, Any] | None = None
    error_message: str | None = None

    def to_json(self) -> str:
        """Serialize to JSON string, omitting None values."""
        d: dict[str, Any] = {"status": self.status}
        if self.data:
            d.update(self.data)
        if self.error_message:
            d["error"] = self.error_message
        return json.dumps(d)

    @classmethod
    def success(cls, **data: Any) -> ToolResult:
        """Create a success result with data."""
        return cls(status="success", data=data or None)

    @classmethod
    def error(cls, message: str, **extra: Any) -> ToolResult:
        """Create an error result."""
        return cls(status="error", error_message=message, data=extra or None)
