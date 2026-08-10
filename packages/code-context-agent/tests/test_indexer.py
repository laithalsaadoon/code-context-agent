"""Tests for the deterministic indexer pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from code_context_agent.indexer import _detect_languages, _get_file_manifest, _get_gitnexus_stats, build_index


@pytest.fixture
def _mock_subprocess_rg(monkeypatch):
    """Mock subprocess.run to return a file list for rg --files."""
    import subprocess

    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "rg" and "--files" in cmd:
            result = MagicMock()
            result.returncode = 0
            result.stdout = "src/main.py\nsrc/utils.py\napp.ts\nlib/helper.js\n"
            return result
        if cmd[0] == "git":
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "not a git repo"
            return result
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)


@pytest.fixture
def _mock_no_external_tools(monkeypatch):
    """Mock shutil.which to report no external tools available."""
    monkeypatch.setattr("shutil.which", lambda _cmd: None)


async def test_build_index_creates_heuristic_summary(tmp_path, monkeypatch):
    """build_index should create heuristic_summary.json in the output directory."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def hello(): pass\n")

    out = tmp_path / "output"

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if cmd[0] == "rg":
            result.returncode = 0
            result.stdout = "main.py\n"
        elif cmd[0] == "git":
            result.returncode = 1
            result.stdout = ""
            result.stderr = "not a git repo"
        else:
            result.returncode = 0
            result.stdout = "{}"
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/rg" if cmd == "rg" else None)

    await build_index(repo, output_dir=out, quiet=True)

    assert (out / "heuristic_summary.json").exists()
    data = json.loads((out / "heuristic_summary.json").read_text())
    assert "volume" in data
    assert "health" in data
    assert "gitnexus" in data


async def test_build_index_returns_none(tmp_path, monkeypatch):
    """build_index should return None (no longer returns a CodeGraph)."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n")

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0 if cmd[0] == "rg" else 1
        result.stdout = "app.py\n" if cmd[0] == "rg" else ""
        result.stderr = ""
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/rg" if cmd == "rg" else None)

    result = await build_index(repo, quiet=True)

    assert result is None


async def test_build_index_writes_file_manifest(tmp_path, monkeypatch):
    """build_index should write files.all.txt to the output directory."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "server.ts").write_text("export function serve() {}\n")

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if cmd[0] == "rg":
            result.returncode = 0
            result.stdout = "server.ts\n"
        elif cmd[0] == "git" and "--name-only" in cmd:
            result.returncode = 0
            result.stdout = "server.ts\n\nserver.ts\n"
        elif cmd[0] == "git":
            result.returncode = 1
            result.stdout = ""
            result.stderr = ""
        else:
            result.returncode = 0
            result.stdout = "{}"
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "rg" else None)

    out = tmp_path / "output"
    await build_index(repo, output_dir=out, quiet=True)

    assert (out / "files.all.txt").exists()
    content = (out / "files.all.txt").read_text()
    assert "server.ts" in content


async def test_build_index_gitnexus_section(tmp_path, monkeypatch):
    """build_index should include gitnexus section in heuristic summary."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n")

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0 if cmd[0] == "rg" else 1
        result.stdout = "app.py\n" if cmd[0] == "rg" else ""
        result.stderr = ""
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    # No gitnexus available
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/rg" if cmd == "rg" else None)

    out = tmp_path / "output"
    await build_index(repo, output_dir=out, quiet=True)

    data = json.loads((out / "heuristic_summary.json").read_text())
    assert data["gitnexus"]["indexed"] is False
    assert data["gitnexus"]["repo_name"] == "repo"
    # New structural fields should be present with defaults when not indexed
    assert data["gitnexus"]["community_count"] == 0
    assert data["gitnexus"]["process_count"] == 0
    assert data["gitnexus"]["symbol_count"] == 0
    assert data["gitnexus"]["edge_count"] == 0
    assert data["gitnexus"]["top_communities"] == []


def test_get_gitnexus_stats_reads_meta_json(tmp_path):
    """_get_gitnexus_stats should read stats from .gitnexus/meta.json."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    gitnexus_dir = repo / ".gitnexus"
    gitnexus_dir.mkdir()
    meta = {
        "repoPath": str(repo),
        "lastCommit": "abc123",
        "indexedAt": "2026-01-01T00:00:00Z",
        "stats": {
            "files": 50,
            "nodes": 300,
            "edges": 900,
            "communities": 12,
            "processes": 45,
            "embeddings": 0,
        },
    }
    (gitnexus_dir / "meta.json").write_text(json.dumps(meta))

    result = _get_gitnexus_stats(repo, "myrepo")

    assert result["community_count"] == 12
    assert result["process_count"] == 45
    assert result["symbol_count"] == 300
    assert result["edge_count"] == 900
    # top_communities requires gitnexus CLI, so defaults to empty
    assert isinstance(result["top_communities"], list)


def test_get_gitnexus_stats_graceful_without_meta(tmp_path):
    """_get_gitnexus_stats should return defaults when .gitnexus/meta.json is missing."""
    repo = tmp_path / "norepo"
    repo.mkdir()

    result = _get_gitnexus_stats(repo, "norepo")

    assert result["community_count"] == 0
    assert result["process_count"] == 0
    assert result["symbol_count"] == 0
    assert result["edge_count"] == 0
    assert result["top_communities"] == []


def test_get_gitnexus_stats_handles_corrupt_meta(tmp_path):
    """_get_gitnexus_stats should return defaults when meta.json is corrupt."""
    repo = tmp_path / "badrepo"
    repo.mkdir()
    gitnexus_dir = repo / ".gitnexus"
    gitnexus_dir.mkdir()
    (gitnexus_dir / "meta.json").write_text("not valid json{{{")

    result = _get_gitnexus_stats(repo, "badrepo")

    assert result["community_count"] == 0
    assert result["process_count"] == 0


def test_file_manifest_uses_ripgrep(monkeypatch):
    """_get_file_manifest should call rg --files."""
    import subprocess

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "a.py\nb.ts\n"
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/rg" if cmd == "rg" else None)

    files = _get_file_manifest(Path("/fake/repo"))

    assert len(calls) == 1
    assert calls[0][0] == "rg"
    assert "--files" in calls[0]
    assert files == ["a.py", "b.ts"]


def test_detect_languages_groups_by_extension():
    """_detect_languages should group files by their language extension."""
    files = [
        "src/main.py",
        "src/utils.py",
        "app.ts",
        "lib/helper.js",
        "lib/component.tsx",
        "README.md",
        "data.json",
        "server.go",
    ]
    result = _detect_languages(files)

    assert set(result["py"]) == {"src/main.py", "src/utils.py"}
    assert set(result["ts"]) == {"app.ts", "lib/helper.js", "lib/component.tsx"}
    assert result["go"] == ["server.go"]
    # README.md and data.json should not be in any language group
    assert "md" not in result
    assert "json" not in result
