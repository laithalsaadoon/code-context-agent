"""Per-run git-worktree isolation for autonomous coding harnesses (plan §2.7, §5).

Each harness run gets its own ``git worktree`` as its ``cwd`` — the only writable
root — so parallel coding runs never collide and a harness cannot touch the base
checkout. :class:`WorkspaceFactory` adds a fresh worktree per run
(:meth:`WorkspaceFactory.create`) and removes it when the run left no changes
behind (:meth:`WorkspaceFactory.cleanup`); a worktree that holds uncommitted
changes is kept so its artifacts survive for collection (AC-5-1..AC-5-4).

This module deliberately shells out only to ``git`` — it launches no coding
harness — so it stays clear of the vendor-free-core import guard, which forbids
*coding-harness* subprocesses in ``strands_teams`` core (AC-1-5). It lives in the
``strands_teams_runtimes`` package regardless, alongside the adapters it serves.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class WorktreeError(RuntimeError):
    """Raised when a ``git worktree`` operation fails."""


async def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    """Run a ``git`` subcommand asynchronously; return ``(returncode, stdout, stderr)``.

    Never raises for a non-zero exit — the caller decides whether a failure is
    fatal (e.g. ``worktree remove`` failing is best-effort).
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


@dataclass
class WorkspaceFactory:
    """Creates and tears down per-run git worktrees (AC-5-1..AC-5-4).

    Attributes:
        repo_root: The base git repository. Worktrees branch from its current
            ``HEAD`` unless an explicit ``base_ref`` is passed to :meth:`create`.
        worktrees_root: Directory under which per-run worktrees are placed.
            Defaults to ``<repo_root>/.code-context/tmp/worktrees``.
        _active: Internal set of worktree paths this factory created (so cleanup
            targets only its own worktrees).
    """

    repo_root: Path
    worktrees_root: Path | None = None
    _active: set[Path] = field(default_factory=set, init=False, repr=False)

    def _resolve_worktrees_root(self) -> Path:
        return self.worktrees_root or (self.repo_root / ".code-context" / "tmp" / "worktrees")

    async def create(self, *, run_id: str | None = None, base_ref: str = "HEAD") -> Path:
        """Add a fresh git worktree for a run and return its path (AC-5-1, AC-5-2).

        Each call generates a distinct worktree path (a per-run ``run_id`` plus a
        random suffix), so two concurrent runs never share a worktree (AC-5-2).
        The worktree is created detached at ``base_ref`` so the run does not
        disturb any branch.

        Args:
            run_id: Optional stable identifier for the run (prefixed onto the
                worktree directory name). A random suffix is always appended so
                distinctness holds even if two runs pass the same ``run_id``.
            base_ref: The git ref the worktree checks out (default ``HEAD``).

        Returns:
            The absolute path to the new worktree (the harness ``cwd``).

        Raises:
            WorktreeError: If ``git worktree add`` fails.
        """
        root = self._resolve_worktrees_root()
        root.mkdir(parents=True, exist_ok=True)
        name = f"{run_id + '-' if run_id else ''}{uuid.uuid4().hex[:12]}"
        worktree_path = root / name

        returncode, _out, err = await _run_git(
            ["worktree", "add", "--detach", str(worktree_path), base_ref],
            cwd=self.repo_root,
        )
        if returncode != 0:
            msg = f"git worktree add failed (exit {returncode}): {err.strip()}"
            raise WorktreeError(msg)
        self._active.add(worktree_path)
        return worktree_path

    async def has_changes(self, worktree_path: Path) -> bool:
        """Return whether the worktree has uncommitted changes (AC-5-3, AC-5-4).

        Uses ``git status --porcelain`` as ground truth: any non-empty output
        (modified, added, or untracked files) counts as a change. On any git
        failure this returns ``True`` (fail-safe: keep the worktree rather than
        risk discarding produced artifacts).
        """
        returncode, out, _err = await _run_git(["status", "--porcelain"], cwd=worktree_path)
        if returncode != 0:
            return True
        return bool(out.strip())

    async def cleanup(self, worktree_path: Path) -> bool:
        """Auto-remove the worktree iff it left no changes behind (AC-5-3, AC-5-4).

        A worktree with uncommitted changes is KEPT so its artifacts survive for
        collection (AC-5-4). An unchanged worktree is removed with
        ``git worktree remove --force`` (AC-5-3). Best-effort: a remove failure is
        swallowed so cleanup never crashes the tool.

        Returns:
            ``True`` if the worktree was removed, ``False`` if it was kept (either
            because it held changes or because removal failed).
        """
        if await self.has_changes(worktree_path):
            return False
        returncode, _out, _err = await _run_git(
            ["worktree", "remove", "--force", str(worktree_path)],
            cwd=self.repo_root,
        )
        removed = returncode == 0
        if removed:
            self._active.discard(worktree_path)
        return removed
