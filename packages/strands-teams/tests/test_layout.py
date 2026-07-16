"""Tests for TeamsLayout — every path derives from one output root."""

from __future__ import annotations

from pathlib import Path

from strands_teams.layout import TeamsLayout


def test_all_paths_derive_from_single_root() -> None:
    root = Path("/work/proj/.code-context")
    layout = TeamsLayout(output_root=root)

    # Every produced path must be rooted at output_root.
    produced = [
        layout.teams_dir(),
        layout.team_dir("team-structure"),
        layout.findings_path("team-structure"),
        layout.metadata_path("team-structure"),
        layout.debug_log_path(),
        layout.artifacts_dir(),
        layout.artifact_path("auth"),
        layout.executive_summary_path(),
        layout.planning_sentinel_path(),
    ]
    for p in produced:
        assert str(p).startswith(str(root)), f"{p} escapes {root}"


def test_default_naming_scheme() -> None:
    root = Path("/work/proj/.code-context")
    layout = TeamsLayout(output_root=root)

    assert layout.findings_path("team-x") == root / "tmp" / "teams" / "team-x" / "findings.md"
    assert layout.metadata_path("team-x") == root / "tmp" / "teams" / "team-x" / "metadata.json"
    assert layout.debug_log_path() == root / "tmp" / "teams" / "team_debug.jsonl"
    assert layout.artifact_path("auth") == root / "bundles" / "BUNDLE.auth.md"
    assert layout.executive_summary_path() == root / "CONTEXT.md"
    assert layout.planning_sentinel_path() == root / "heuristic_summary.json"


def test_artifact_glob_and_area_roundtrip() -> None:
    layout = TeamsLayout(output_root=Path("/work/x"))
    assert layout.artifact_glob() == "BUNDLE.*.md"
    path = layout.artifact_path("payments")
    assert layout.area_from_artifact(path) == "payments"


def test_naming_scheme_is_parameterizable() -> None:
    layout = TeamsLayout(
        output_root=Path("/work/x"),
        artifact_prefix="AREA",
        executive_summary_name="SUMMARY.md",
        planning_sentinel_name="index.json",
    )
    assert layout.artifact_path("db").name == "AREA.db.md"
    assert layout.executive_summary_path().name == "SUMMARY.md"
    assert layout.planning_sentinel_path().name == "index.json"
    assert layout.area_from_artifact(layout.artifact_path("db")) == "db"


def test_layout_is_frozen_and_hashable() -> None:
    layout = TeamsLayout(output_root=Path("/work/x"))
    # Frozen dataclass -> hashable -> picklable-friendly for invocation_state.
    assert hash(layout) == hash(TeamsLayout(output_root=Path("/work/x")))
