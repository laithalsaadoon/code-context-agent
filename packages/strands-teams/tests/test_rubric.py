"""Tests for the single NarrativeRubric scoring object."""

from __future__ import annotations

from strands_teams.rubric import (
    DEFAULT_NARRATIVE_RUBRIC,
    DEFAULT_REVISION_THRESHOLD,
    NarrativeRubric,
    RubricDimension,
)

# A rich fixture: 20+ file:line refs, 8+ headings, two mermaid blocks, long body,
# 15+ unique file paths -> should score high and NOT need revision.
_FILE_LINE_REFS = [f"src/mod{i}/file{i}.py:{i * 3}" for i in range(1, 25)]  # 24 refs, 24 unique paths
_HEADINGS = [f"## Section {i}" for i in range(1, 10)]  # 9 headings
_RICH_LINES = [
    _HEADINGS[0],
    "See " + ", ".join(_FILE_LINE_REFS[:12]) + " for the flow.",
    _HEADINGS[1],
    "Also " + ", ".join(_FILE_LINE_REFS[12:]) + " downstream.",
    "```mermaid",
    "graph TD; A-->B",
    "```",
    *_HEADINGS[2:],
    "```mermaid",
    "sequenceDiagram",
    "```",
]
RICH_TEXT = "\n".join(_RICH_LINES + ["filler line"] * 120)

# A weak fixture: no refs, one heading, no diagrams, short -> needs revision.
WEAK_TEXT = "## Only heading\nSome prose without references or diagrams.\n"


def test_rich_text_scores_deterministically() -> None:
    r1 = DEFAULT_NARRATIVE_RUBRIC.score(RICH_TEXT)
    r2 = DEFAULT_NARRATIVE_RUBRIC.score(RICH_TEXT)
    assert r1.scores == r2.scores  # deterministic
    assert set(r1.scores) == {"specificity", "structure", "diagrams", "depth", "cross_references"}
    assert r1.scores["diagrams"] == 5.0  # two mermaid blocks
    assert not r1.needs_revision
    assert r1.total >= DEFAULT_REVISION_THRESHOLD


def test_weak_text_needs_revision_with_suggestions() -> None:
    report = DEFAULT_NARRATIVE_RUBRIC.score(WEAK_TEXT)
    assert report.needs_revision
    assert report.total < DEFAULT_REVISION_THRESHOLD
    # Suggestions surface for the weak dimensions (specificity, diagrams, ...).
    joined = " ".join(report.suggestions)
    assert "file:line" in joined
    assert "mermaid" in joined


def test_needs_revision_flips_at_threshold() -> None:
    # A synthetic 2-dimension rubric: score is exactly the counter value.
    dim_a = RubricDimension(
        name="a",
        counter=lambda _t: 0,
        thresholds=((0, 3.0), (1, 5.0)),
        suggestion="more a",
    )
    dim_b = RubricDimension(
        name="b",
        counter=lambda _t: 1,
        thresholds=((0, 3.0), (1, 4.0)),
        suggestion="more b",
    )
    # avg of 3.0 and 4.0 = 3.5 -> NOT below 3.5 -> no revision (boundary is strict <).
    rubric_at = NarrativeRubric(dimensions=(dim_a, dim_b), revision_threshold=3.5)
    report_at = rubric_at.score("x")
    assert report_at.total == 3.5
    assert not report_at.needs_revision

    # Nudge one dimension down so avg = 3.0 -> below 3.5 -> needs revision.
    dim_a_low = RubricDimension(
        name="a",
        counter=lambda _t: 0,
        thresholds=((0, 2.0), (1, 5.0)),
        suggestion="more a",
    )
    rubric_below = NarrativeRubric(dimensions=(dim_a_low, dim_b), revision_threshold=3.5)
    report_below = rubric_below.score("x")
    assert report_below.total == 3.0
    assert report_below.needs_revision


def test_default_revision_threshold_is_3_5() -> None:
    assert DEFAULT_NARRATIVE_RUBRIC.revision_threshold == 3.5
    assert DEFAULT_REVISION_THRESHOLD == 3.5


def test_dimension_thresholds_are_ascending_step_function() -> None:
    dim = RubricDimension(
        name="specificity",
        pattern=r"\w+\.\w+:\d+",
        thresholds=((0, 1.0), (5, 2.0), (10, 3.0), (20, 4.0), (30, 5.0)),
        suggestion="more",
    )
    assert dim.score_count(0) == 1.0
    assert dim.score_count(4) == 1.0
    assert dim.score_count(5) == 2.0
    assert dim.score_count(29) == 4.0
    assert dim.score_count(30) == 5.0
    assert dim.score_count(1000) == 5.0
