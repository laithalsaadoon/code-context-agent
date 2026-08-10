"""Generic, phase-enum-agnostic phase specification.

The pre-split ``consumer/phases.py`` hardcoded a 5-value ``AnalysisPhase``
``IntEnum`` plus codebase-specific tool→phase and phase-name maps. ``PhaseSpec``
generalizes that: a use case supplies its own phase indices, names,
descriptions, tool→phase map, and tool-name prefix rules. The display layer
(Wave D) reads ``count`` and ``names`` instead of a literal ``/5``.

Phases are identified by 1-based integer index (order matters). No enum is
imposed — a use case may still back the indices with its own ``IntEnum`` and
pass ``int(member)`` values.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003 — pydantic resolves PhaseSpec field types at runtime
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PhaseSpec:
    """Describes the ordered phases of a run for progress display and tagging.

    Attributes:
        names: Map of phase index (1-based) to human-readable phase name.
        descriptions: Map of phase index to a one-line description.
        tool_phase_map: Map of exact tool name to the phase index it belongs to.
        prefix_rules: Map of tool-name prefix to a phase index, applied when no
            exact match is found in ``tool_phase_map`` (e.g., ``"gitnexus_"``).
    """

    names: Mapping[int, str]
    descriptions: Mapping[int, str] = field(default_factory=dict)
    tool_phase_map: Mapping[str, int] = field(default_factory=dict)
    prefix_rules: Mapping[str, int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        """Total number of phases (the ``N`` in a ``[i/N]`` indicator)."""
        return len(self.names)

    def name(self, phase: int) -> str:
        """Return the display name for a phase index, or a fallback label."""
        return self.names.get(phase, f"Phase {phase}")

    def description(self, phase: int) -> str:
        """Return the description for a phase index, or an empty string."""
        return self.descriptions.get(phase, "")

    def resolve(self, tool_name: str) -> int | None:
        """Resolve a tool name to its phase index.

        Checks the exact ``tool_phase_map`` first, then ``prefix_rules``.

        Args:
            tool_name: The invoked tool's name.

        Returns:
            The phase index, or ``None`` if the tool maps to no phase.
        """
        phase = self.tool_phase_map.get(tool_name)
        if phase is not None:
            return phase
        for prefix, mapped in self.prefix_rules.items():
            if tool_name.startswith(prefix):
                return mapped
        return None
