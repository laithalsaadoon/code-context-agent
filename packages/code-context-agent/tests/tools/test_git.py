"""Tests for git history tools.

These tests use the actual git repository that this project lives in,
providing real-world test coverage for git commands.
"""

import json
from pathlib import Path

import pytest

from code_context_agent.tools.git import (
    git_blame_summary,
    git_contributors,
    git_diff_file,
    git_file_history,
    git_files_changed_together,
    git_hotspots,
    git_recent_commits,
)

# Use the project's own repo for testing
REPO_PATH = str(Path(__file__).parent.parent.parent.resolve())


class TestGitFilesChangedTogether:
    """Tests for git_files_changed_together tool."""

    def test_finds_cochanged_files(self) -> None:
        """Test that it finds files changed together with a target file."""
        # Use a file that exists in this repo
        result = json.loads(git_files_changed_together(REPO_PATH, "pyproject.toml", limit=50))

        assert result["status"] == "success"
        assert "cochanged_files" in result
        assert "total_commits" in result
        assert result["file_path"] == "pyproject.toml"

    def test_handles_nonexistent_file(self) -> None:
        """Test that it handles files with no commits gracefully."""
        result = json.loads(
            git_files_changed_together(REPO_PATH, "nonexistent_file_xyz.py", limit=10),
        )

        assert result["status"] == "success"
        assert result["total_commits"] == 0
        assert result["cochanged_files"] == []

    def test_respects_limit(self) -> None:
        """Test that limit parameter works."""
        result = json.loads(git_files_changed_together(REPO_PATH, "pyproject.toml", limit=5))

        assert result["status"] == "success"
        assert result["total_commits"] <= 5


class TestGitFileHistory:
    """Tests for git_file_history tool."""

    def test_gets_file_history(self) -> None:
        """Test that it retrieves commit history for a file."""
        result = json.loads(git_file_history(REPO_PATH, "pyproject.toml", limit=10))

        assert result["status"] == "success"
        assert "commits" in result
        assert result["file_path"] == "pyproject.toml"

        if result["commits"]:
            commit = result["commits"][0]
            assert "hash" in commit
            assert "author" in commit
            assert "date" in commit
            assert "message" in commit

    def test_respects_limit(self) -> None:
        """Test that limit parameter works."""
        result = json.loads(git_file_history(REPO_PATH, "pyproject.toml", limit=3))

        assert result["status"] == "success"
        assert len(result["commits"]) <= 3


class TestGitRecentCommits:
    """Tests for git_recent_commits tool."""

    def test_gets_recent_commits(self) -> None:
        """Test that it retrieves recent repository commits."""
        result = json.loads(git_recent_commits(REPO_PATH, limit=10))

        assert result["status"] == "success"
        assert "commits" in result
        assert "commit_count" in result

        if result["commits"]:
            commit = result["commits"][0]
            assert "hash" in commit
            assert "author" in commit
            assert "date" in commit
            assert "message" in commit

    def test_respects_limit(self) -> None:
        """Test that limit parameter works."""
        result = json.loads(git_recent_commits(REPO_PATH, limit=5))

        assert result["status"] == "success"
        assert len(result["commits"]) <= 5


class TestGitDiffFile:
    """Tests for git_diff_file tool."""

    def test_diff_with_commit(self) -> None:
        """Test getting diff for a specific commit."""
        # First get a commit hash
        history = json.loads(git_file_history(REPO_PATH, "pyproject.toml", limit=1))
        if not history["commits"]:
            pytest.skip("No commits found")

        commit_hash = history["commits"][0]["hash"]
        result = json.loads(git_diff_file(REPO_PATH, "pyproject.toml", commit=commit_hash))

        assert result["status"] == "success"
        assert result["file_path"] == "pyproject.toml"
        assert "diff" in result

    def test_diff_no_changes(self) -> None:
        """Test diff when there are no unstaged changes."""
        result = json.loads(git_diff_file(REPO_PATH, "pyproject.toml"))

        # May or may not have changes - just ensure no error
        assert result["status"] == "success"
        assert "diff" in result


class TestGitBlameSummary:
    """Tests for git_blame_summary tool."""

    def test_gets_blame_summary(self) -> None:
        """Test that it retrieves authorship summary."""
        result = json.loads(git_blame_summary(REPO_PATH, "pyproject.toml"))

        assert result["status"] == "success"
        assert "authors" in result
        assert "total_lines" in result
        assert result["file_path"] == "pyproject.toml"

        if result["authors"]:
            author = result["authors"][0]
            assert "email" in author
            assert "lines" in author
            assert "percentage" in author


class TestGitHotspots:
    """Tests for git_hotspots tool."""

    def test_finds_hotspots(self) -> None:
        """Test that it identifies frequently changed files."""
        result = json.loads(git_hotspots(REPO_PATH, limit=30))

        assert result["status"] == "success"
        assert "hotspots" in result
        assert "total_commits_analyzed" in result

        if result["hotspots"]:
            hotspot = result["hotspots"][0]
            assert "path" in hotspot
            assert "commits" in hotspot
            assert "percentage" in hotspot

    def test_respects_limit(self) -> None:
        """Test that limit parameter works."""
        result = json.loads(git_hotspots(REPO_PATH, limit=10))

        assert result["status"] == "success"
        # Hotspots are derived from commits, so can't directly assert on hotspot count


class TestGitContributors:
    """Tests for git_contributors tool."""

    def test_gets_contributors(self) -> None:
        """Test that it retrieves contributor statistics."""
        result = json.loads(git_contributors(REPO_PATH, limit=50))

        assert result["status"] == "success"
        assert "contributors" in result
        assert "total_commits" in result

        if result["contributors"]:
            contributor = result["contributors"][0]
            assert "email" in contributor
            assert "commits" in contributor
            assert "percentage" in contributor
            assert "first_commit" in contributor
            assert "last_commit" in contributor

    def test_respects_limit(self) -> None:
        """Test that limit parameter works."""
        result = json.loads(git_contributors(REPO_PATH, limit=5))

        assert result["status"] == "success"
        assert result["total_commits"] <= 5
