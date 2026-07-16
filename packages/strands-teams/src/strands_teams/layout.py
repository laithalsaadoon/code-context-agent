"""Layout descriptor — single source of truth for every artifact path.

``TeamsLayout`` computes every path a run touches (teams dir, per-team
``findings.md`` / ``metadata.json``, the team-debug JSONL, the artifact-output
dir, per-area artifact filenames, the executive-summary filename, and the
planning-context sentinel) from a single output-directory root plus a
use-case-supplied naming scheme.

Replaces the scattered ``BUNDLE.{area}.md`` / ``CONTEXT.md`` /
``heuristic_summary.json`` / ``findings.md`` string literals that the pre-split
team-tools module and ``hooks.py`` duplicated across dispatch, read, write, and
quality functions (explore.yaml hidden couplings #4, #10; AC-2-1).

Frozen dataclass so it is trivially picklable and safe to place into
``invocation_state`` (research-strands.yaml §ToolContext).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class TeamsLayout:
    """Owns all artifact paths for a run, derived from one output root.

    Every path-producing function in dispatch/read/write/quality code must call
    a method here rather than re-deriving a literal, so the naming scheme lives
    in exactly one place.

    Attributes:
        output_root: The run's output-directory root (e.g., ``<repo>/.code-context``).
        artifact_prefix: Prefix for per-area artifact files (``BUNDLE`` ->
            ``BUNDLE.{area}.md``).
        artifact_suffix: Extension for artifact files (default ``.md``).
        artifacts_subdir: Subdirectory under the root holding per-area artifacts.
        teams_subdir: Subdirectory (under a scratch dir) holding per-team output.
        scratch_subdir: Scratch directory under the root for transient team data.
        findings_name: Per-team findings filename.
        metadata_name: Per-team metadata filename.
        debug_log_name: Team-dispatch debug JSONL filename.
        executive_summary_name: Filename for the top-level executive summary.
        planning_sentinel_name: Filename for the planning-context sentinel
            produced by a deterministic pre-run step.
    """

    output_root: Path
    artifact_prefix: str = "BUNDLE"
    artifact_suffix: str = ".md"
    artifacts_subdir: str = "bundles"
    teams_subdir: str = "teams"
    scratch_subdir: str = "tmp"
    findings_name: str = "findings.md"
    metadata_name: str = "metadata.json"
    debug_log_name: str = "team_debug.jsonl"
    executive_summary_name: str = "CONTEXT.md"
    planning_sentinel_name: str = "heuristic_summary.json"

    def teams_dir(self) -> Path:
        """Directory holding every team's output subdirectory."""
        return self.output_root / self.scratch_subdir / self.teams_subdir

    def team_dir(self, team_id: str) -> Path:
        """Directory for a single team's output."""
        return self.teams_dir() / team_id

    def findings_path(self, team_id: str) -> Path:
        """Path to a team's ``findings.md``."""
        return self.team_dir(team_id) / self.findings_name

    def metadata_path(self, team_id: str) -> Path:
        """Path to a team's ``metadata.json``."""
        return self.team_dir(team_id) / self.metadata_name

    def debug_log_path(self) -> Path:
        """Path to the append-only team-dispatch debug JSONL."""
        return self.teams_dir() / self.debug_log_name

    def artifacts_dir(self) -> Path:
        """Directory holding per-area artifact files."""
        return self.output_root / self.artifacts_subdir

    def artifact_path(self, area: str) -> Path:
        """Path to a per-area artifact file (e.g., ``bundles/BUNDLE.auth.md``)."""
        return self.artifacts_dir() / f"{self.artifact_prefix}.{area}{self.artifact_suffix}"

    def artifact_glob(self) -> str:
        """Glob pattern matching every per-area artifact file."""
        return f"{self.artifact_prefix}.*{self.artifact_suffix}"

    def area_from_artifact(self, path: Path) -> str:
        """Extract the area identifier from an artifact file path.

        Inverse of :meth:`artifact_path` — strips the prefix/suffix so the
        quality hook can name a weak artifact by its area.
        """
        return path.name.removeprefix(f"{self.artifact_prefix}.").removesuffix(self.artifact_suffix)

    def executive_summary_path(self) -> Path:
        """Path to the top-level executive summary file."""
        return self.output_root / self.executive_summary_name

    def planning_sentinel_path(self) -> Path:
        """Path to the planning-context sentinel produced by the pre-run step."""
        return self.output_root / self.planning_sentinel_name
