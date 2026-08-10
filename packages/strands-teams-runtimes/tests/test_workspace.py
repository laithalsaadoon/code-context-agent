"""Tests for WorkspaceFactory git-worktree isolation (US-5, AC-8-2).

Uses a real temporary git repo (offline, deterministic) — git worktree is not a
coding harness, Bedrock call, or network request, so no mocking is needed here.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from strands_teams_runtimes import WorkspaceFactory


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], root)
    _git(["config", "user.email", "t@t.dev"], root)
    _git(["config", "user.name", "t"], root)
    (root / "README.md").write_text("hello\n")
    _git(["add", "."], root)
    _git(["commit", "-m", "init"], root)
    return root


async def test_create_returns_worktree_path(repo: Path) -> None:
    factory = WorkspaceFactory(repo_root=repo)
    wt = await factory.create(run_id="r1")
    assert wt.exists()
    assert (wt / "README.md").exists()


async def test_concurrent_creates_get_distinct_paths(repo: Path) -> None:
    factory = WorkspaceFactory(repo_root=repo)
    paths = await asyncio.gather(*(factory.create(run_id="run") for _ in range(4)))
    assert len({str(p) for p in paths}) == 4  # AC-5-2 / AC-8-2: all distinct


async def test_cleanup_removes_unchanged_worktree(repo: Path) -> None:
    factory = WorkspaceFactory(repo_root=repo)
    wt = await factory.create()
    removed = await factory.cleanup(wt)
    assert removed is True  # AC-5-3
    assert not wt.exists()


async def test_cleanup_keeps_worktree_with_changes(repo: Path) -> None:
    factory = WorkspaceFactory(repo_root=repo)
    wt = await factory.create()
    (wt / "new_file.py").write_text("print('produced artifact')\n")
    removed = await factory.cleanup(wt)
    assert removed is False  # AC-5-4: keep artifacts
    assert wt.exists()
    assert (wt / "new_file.py").exists()


async def test_has_changes_detects_untracked(repo: Path) -> None:
    factory = WorkspaceFactory(repo_root=repo)
    wt = await factory.create()
    assert await factory.has_changes(wt) is False
    (wt / "x.txt").write_text("x")
    assert await factory.has_changes(wt) is True
