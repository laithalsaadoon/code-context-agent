"""Single scoring rubric shared by the narrative-scoring tool and quality hook.

``NarrativeRubric`` is the one place the scoring dimensions, their threshold
tables, and the revision threshold (default ``3.5``) live. Both the
``score_narrative``-equivalent coordinator tool (AC-2-6) and the quality hook
(AC-3-3) call :meth:`NarrativeRubric.score`, deduping the two pre-split
formulas that drifted apart (the pre-split team-tools module used discrete
threshold tables; ``hooks.py:509-539`` used continuous ``min(5, count/divisor)``
divisors). This module keeps the threshold-table intent as the single canonical
scoring; the continuous variant is subsumed (same dimensions, same 3.5
threshold) per explore.yaml hidden coupling #5.

Dimensions are data-driven: each :class:`RubricDimension` names a measure
(a regex ``pattern`` or a ``counter`` callable), an ascending
``(min_count, score)`` threshold table, and a suggestion template. The domain
regexes (file:line, mermaid, file-path) ship here as importable defaults so a
use case just references :data:`DEFAULT_NARRATIVE_RUBRIC` rather than hardcoding
them — but a use case may build its own ``NarrativeRubric`` with different
dimensions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Importable default domain regexes (referenced by DEFAULT_NARRATIVE_RUBRIC).
# ---------------------------------------------------------------------------
FILE_LINE_PATTERN = r"\w+\.\w+:\d+"
"""Matches ``file.ext:123`` style file:line references (specificity)."""

HEADING_PATTERN = r"^#{2,3}\s"
"""Matches level-2/3 markdown headings (structure). Needs ``re.MULTILINE``."""

MERMAID_PATTERN = r"```mermaid"
"""Matches a mermaid code fence (diagrams). Case-insensitive."""

# Match paths like src/foo/bar.py, ./utils/helpers.ts — at least one slash.
FILE_PATH_PATTERN = r"(?:\.?/)?(?:[\w.-]+/)+[\w.-]+\.\w+"
"""Matches slash-separated file paths (cross-references). Counted as unique."""

DEFAULT_REVISION_THRESHOLD = 3.5
"""Average below which an artifact needs a revision/enrichment pass."""


def count_lines(text: str) -> int:
    """Count the lines in ``text`` (depth measure)."""
    return len(text.splitlines())


@dataclass(frozen=True)
class RubricDimension:
    """One scored dimension of a narrative artifact.

    A dimension measures a count from the text (via a regex ``pattern`` or a
    ``counter`` callable), then maps that count to a 0-5 score using an ascending
    ``(min_count, score)`` threshold table: the score is the highest table entry
    whose ``min_count`` does not exceed the measured count.

    Attributes:
        name: Dimension key (e.g., ``"specificity"``).
        thresholds: Ascending ``(min_count, score)`` pairs. The first pair should
            use ``min_count=0`` to define the floor score.
        suggestion: Improvement hint; may contain a ``{count}`` placeholder.
        pattern: Regex whose match count is the measure. Ignored if ``counter``
            is set.
        flags: Regex flags applied to ``pattern`` (e.g., ``re.MULTILINE``).
        unique: If True, count distinct matches rather than all matches.
        counter: Callable measuring the count directly (overrides ``pattern``).
        weak_score: Emit ``suggestion`` when the dimension scores at or below this.
    """

    name: str
    thresholds: tuple[tuple[int, float], ...]
    suggestion: str
    pattern: str | None = None
    flags: int = 0
    unique: bool = False
    counter: Callable[[str], int] | None = None
    weak_score: float = 2.0

    def measure(self, text: str) -> int:
        """Return the raw count this dimension measures from ``text``."""
        if self.counter is not None:
            return self.counter(text)
        if self.pattern is None:
            msg = f"RubricDimension {self.name!r} has neither pattern nor counter"
            raise ValueError(msg)
        matches = re.findall(self.pattern, text, self.flags)
        return len(set(matches)) if self.unique else len(matches)

    def score_count(self, count: int) -> float:
        """Map a measured ``count`` to its 0-5 score via the threshold table."""
        score = 0.0
        for min_count, value in self.thresholds:
            if count >= min_count:
                score = value
        return score


@dataclass(frozen=True)
class DimensionScore:
    """The scored result for one dimension."""

    name: str
    count: int
    score: float


@dataclass(frozen=True)
class ScoreReport:
    """Result of scoring a text against a :class:`NarrativeRubric`."""

    scores: dict[str, float]
    total: float
    needs_revision: bool
    suggestions: list[str]
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class NarrativeRubric:
    """The single rubric object: dimensions + revision threshold.

    Attributes:
        dimensions: Ordered scoring dimensions.
        revision_threshold: Average (and per-artifact) score below which the
            artifact needs a revision pass (default ``3.5``).
    """

    dimensions: tuple[RubricDimension, ...]
    revision_threshold: float = DEFAULT_REVISION_THRESHOLD

    def score(self, text: str) -> ScoreReport:
        """Score ``text`` across every dimension.

        Args:
            text: The narrative artifact content to score.

        Returns:
            A :class:`ScoreReport` with per-dimension scores, the rounded total
            average, the ``needs_revision`` flag (average below the revision
            threshold), and improvement suggestions for weak dimensions.
        """
        scores: dict[str, float] = {}
        counts: dict[str, int] = {}
        suggestions: list[str] = []
        for dim in self.dimensions:
            count = dim.measure(text)
            value = dim.score_count(count)
            scores[dim.name] = value
            counts[dim.name] = count
            if value <= dim.weak_score:
                suggestions.append(dim.suggestion.format(count=count))
        total = round(sum(scores.values()) / len(scores), 2) if scores else 0.0
        return ScoreReport(
            scores=scores,
            total=total,
            needs_revision=total < self.revision_threshold,
            suggestions=suggestions,
            counts=counts,
        )


# ---------------------------------------------------------------------------
# Default rubric — the five narrative dimensions the codebase use case scores.
# Threshold tables are the min_count form of the pre-split score_narrative
# tables, verified equivalent.
# ---------------------------------------------------------------------------
DEFAULT_NARRATIVE_RUBRIC = NarrativeRubric(
    dimensions=(
        RubricDimension(
            name="specificity",
            pattern=FILE_LINE_PATTERN,
            thresholds=((0, 1.0), (5, 2.0), (10, 3.0), (20, 4.0), (30, 5.0)),
            suggestion="Add more file:line references (currently {count}).",
        ),
        RubricDimension(
            name="structure",
            pattern=HEADING_PATTERN,
            flags=re.MULTILINE,
            thresholds=((0, 1.0), (3, 2.0), (5, 3.0), (8, 4.0), (11, 5.0)),
            suggestion="Add more section headings for structure (currently {count}).",
        ),
        RubricDimension(
            name="diagrams",
            pattern=MERMAID_PATTERN,
            flags=re.IGNORECASE,
            thresholds=((0, 1.0), (1, 3.0), (2, 5.0)),
            suggestion="Add at least one mermaid diagram for call flow or architecture.",
            weak_score=1.0,
        ),
        RubricDimension(
            name="depth",
            counter=count_lines,
            thresholds=((0, 1.0), (20, 2.0), (50, 3.0), (100, 4.0), (200, 5.0)),
            suggestion="Expand content depth (currently {count} lines).",
        ),
        RubricDimension(
            name="cross_references",
            pattern=FILE_PATH_PATTERN,
            unique=True,
            thresholds=((0, 1.0), (3, 2.0), (8, 3.0), (15, 4.0), (25, 5.0)),
            suggestion="Cross-reference more files/modules (currently {count} unique paths).",
        ),
    ),
)
"""The default five-dimension narrative rubric (revision threshold 3.5)."""
