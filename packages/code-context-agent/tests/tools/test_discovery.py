"""Tests for discovery tools (count_only mode)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from code_context_agent.tools.discovery import rg_search

# Patch validation to allow /repo in tests
_REPO_VALIDATION_PATCHES = [
    patch(
        "code_context_agent.tools.discovery.validate_repo_path",
        return_value=Path("/repo"),
    ),
    patch(
        "code_context_agent.tools.discovery.validate_search_pattern",
        side_effect=lambda p: p,
    ),
]


def _apply_patches():
    """Apply all validation patches and return a list of started mocks."""
    return [p.start() for p in _REPO_VALIDATION_PATCHES]


def _stop_patches():
    """Stop all validation patches."""
    for p in _REPO_VALIDATION_PATCHES:
        p.stop()


class TestRgSearchCountOnly:
    def setup_method(self):
        _apply_patches()

    def teardown_method(self):
        _stop_patches()

    def test_returns_per_file_counts(self):
        """count_only=True returns per-file counts and total."""
        mock_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="/repo/src/foo.py:12\n/repo/src/bar.py:30\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = json.loads(rg_search("pattern", "/repo", count_only=True))

        assert result["status"] == "success"
        assert result["total_count"] == 42
        assert result["file_count"] == 2
        assert result["files"]["src/foo.py"] == 12
        assert result["files"]["src/bar.py"] == 30

    def test_returns_zero_for_no_matches(self):
        """count_only with no matches returns zero counts."""
        mock_result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = json.loads(rg_search("nonexistent", "/repo", count_only=True))

        assert result["status"] == "success"
        assert result["total_count"] == 0
        assert result["file_count"] == 0

    def test_respects_glob_filter(self):
        """count_only passes glob filter to rg."""
        mock_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="/repo/src/main.py:5\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            rg_search("def ", "/repo", glob="*.py", count_only=True)

        cmd = mock_run.call_args[0][0]
        assert "-g" in " ".join(cmd)

    def test_respects_file_type(self):
        """count_only passes file_type filter to rg."""
        mock_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="/repo/src/main.ts:3\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            rg_search("function", "/repo", file_type="ts", count_only=True)

        cmd = mock_run.call_args[0][0]
        assert "-t" in " ".join(cmd)

    def test_handles_timeout(self):
        """count_only handles subprocess timeout."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("rg", 60)):
            result = json.loads(rg_search("pattern", "/repo", count_only=True))

        assert result["status"] == "error"
        assert "timed out" in result["error"]

    def test_handles_malformed_lines(self):
        """count_only skips lines that aren't in path:count format."""
        mock_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="/repo/src/ok.py:5\nnot-a-count-line\n/repo/src/also-ok.py:3\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = json.loads(rg_search("pattern", "/repo", count_only=True))

        assert result["total_count"] == 8
        assert result["file_count"] == 2
