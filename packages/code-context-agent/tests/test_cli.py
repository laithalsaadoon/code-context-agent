"""Tests for CLI helper functions (JSON output, incremental analysis)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from code_context_agent.cli import _build_since_context, _display_result_json


class TestDisplayResultJson:
    def test_completed_reads_analysis_file(self, tmp_path, capsys):
        """JSON mode reads analysis_result.json from output dir."""
        analysis = {"status": "completed", "summary": "test analysis"}
        (tmp_path / "analysis_result.json").write_text(json.dumps(analysis))

        result = {"status": "completed", "output_dir": str(tmp_path), "context_path": None}
        _display_result_json(result)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["status"] == "completed"
        assert parsed["summary"] == "test analysis"

    def test_completed_fallback_no_file(self, tmp_path, capsys):
        """Falls back to run metadata when analysis_result.json is missing."""
        result = {"status": "completed", "output_dir": str(tmp_path), "context_path": None}
        _display_result_json(result)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["status"] == "completed"
        assert parsed["output_dir"] == str(tmp_path)

    def test_error_writes_to_stderr(self, capsys):
        """Error status writes JSON to stderr and raises SystemExit."""
        result = {"status": "error", "error": "something broke", "exceeded_limit": None}

        with pytest.raises(SystemExit) as exc_info:
            _display_result_json(result)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        parsed = json.loads(captured.err)
        assert parsed["status"] == "error"
        assert parsed["error"] == "something broke"

    def test_stopped_writes_to_stderr(self, capsys):
        """Stopped status writes JSON to stderr and raises SystemExit."""
        result = {"status": "stopped", "error": None, "exceeded_limit": "max_turns (1000)"}

        with pytest.raises(SystemExit) as exc_info:
            _display_result_json(result)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        parsed = json.loads(captured.err)
        assert parsed["status"] == "stopped"
        assert parsed["exceeded_limit"] == "max_turns (1000)"


class TestBuildSinceContext:
    def test_returns_xml_with_changed_files(self, tmp_path):
        """Produces XML context with file list when git diff succeeds."""
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="src/auth.py\nsrc/db.py\n", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _build_since_context(tmp_path, "HEAD~5", tmp_path)

        assert result is not None
        assert "<since_context>" in result
        assert "src/auth.py" in result
        assert "src/db.py" in result
        assert "<changed_file_count>2</changed_file_count>" in result

    def test_returns_none_when_no_changes(self, tmp_path):
        """Returns None when git diff is empty."""
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _build_since_context(tmp_path, "HEAD", tmp_path)

        assert result is None

    def test_returns_none_on_bad_ref(self, tmp_path):
        """Returns None when git ref is invalid."""
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            result = _build_since_context(tmp_path, "nonexistent-ref", tmp_path)

        assert result is None

    def test_detects_existing_graph(self, tmp_path):
        """Sets has_existing_graph flag when code_graph.json exists."""
        (tmp_path / "code_graph.json").write_text("{}")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="src/main.py\n", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _build_since_context(Path("/repo"), "HEAD~1", tmp_path)

        assert result is not None
        assert "<has_existing_graph>True</has_existing_graph>" in result

    def test_detects_existing_context(self, tmp_path):
        """Sets has_existing_context flag when CONTEXT.md exists."""
        (tmp_path / "CONTEXT.md").write_text("# Context")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="src/main.py\n", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _build_since_context(Path("/repo"), "HEAD~1", tmp_path)

        assert result is not None
        assert "<has_existing_context>True</has_existing_context>" in result
