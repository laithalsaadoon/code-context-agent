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

import contextlib
import fcntl
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import (
    NO_CURRENT_REVISION,
    ApprovalToken,
    ArtifactRevision,
    Decision,
    HumanDecision,
    RevisionConflictError,
    StaleArtifactError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
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
        findings_name: Per-team human findings filename.
        findings_json_name: Per-team typed-findings sidecar filename.
        contract_name: Per-team task-contract sidecar filename (authority,
            budget, acceptance criteria, input revisions).
        metadata_name: Per-team metadata filename.
        debug_log_name: Team-dispatch debug JSONL filename.
        event_log_name: Typed append-only event-plane JSONL filename (Phase 5 /
            spec 006), co-located with the team-debug log under the teams dir.
        executive_summary_name: Filename for the top-level executive summary.
        planning_sentinel_name: Filename for the planning-context sentinel
            produced by a deterministic pre-run step.
        revisions_subdir: Subdirectory under the root holding the content-addressed
            immutable revision store (per-``revision_id`` records).
        alias_name: Filename of the mutable ``logical_name -> current revision_id``
            alias pointer map (Phase 4 / spec 005), resolved under ``output_root``
            the same way :meth:`planning_sentinel_path` is.
        approvals_subdir: Subdirectory (under the scratch dir) holding the
            per-``token_id`` :class:`~strands_teams.models.ApprovalToken` store
            (Phase 6 / spec 007). The single source of every approval path — the
            ``PolicyEngine`` reads it, never a duplicated literal (AC-3-7).
        human_gates_subdir: Subdirectory (under the scratch dir) holding pending
            :class:`~strands_teams.models.HumanDecision` gate sentinels the runner
            suspends on and resumes from (Phase 6 / spec 007; AC-4-3, AC-4-5).
        decision_log_name: Filename of the append-only decision log — one
            JSON-encoded :class:`~strands_teams.models.Decision` per line — written
            on every :class:`~strands_teams.policy.PolicyEngine` verdict (AC-2-4).
    """

    output_root: Path
    artifact_prefix: str = "BUNDLE"
    artifact_suffix: str = ".md"
    artifacts_subdir: str = "bundles"
    teams_subdir: str = "teams"
    scratch_subdir: str = "tmp"
    findings_name: str = "findings.md"
    findings_json_name: str = "findings.json"
    contract_name: str = "contract.json"
    metadata_name: str = "metadata.json"
    debug_log_name: str = "team_debug.jsonl"
    event_log_name: str = "events.jsonl"
    executive_summary_name: str = "CONTEXT.md"
    planning_sentinel_name: str = "heuristic_summary.json"
    revisions_subdir: str = "revisions"
    alias_name: str = "aliases.json"
    approvals_subdir: str = "approvals"
    human_gates_subdir: str = "human_gates"
    decision_log_name: str = "decisions.jsonl"

    def teams_dir(self) -> Path:
        """Directory holding every team's output subdirectory."""
        return self.output_root / self.scratch_subdir / self.teams_subdir

    def team_dir(self, team_id: str) -> Path:
        """Directory for a single team's output."""
        return self.teams_dir() / team_id

    def findings_path(self, team_id: str) -> Path:
        """Path to a team's ``findings.md``."""
        return self.team_dir(team_id) / self.findings_name

    def findings_json_path(self, team_id: str) -> Path:
        """Path to a team's typed ``findings.json`` sidecar.

        Resolves under the same per-team directory as :meth:`findings_path`, so
        the human markdown and the machine-readable typed findings live side by
        side (AC-2-2).
        """
        return self.team_dir(team_id) / self.findings_json_name

    def contract_path(self, team_id: str) -> Path:
        """Path to a team's task-contract sidecar (``contract.json``).

        Resolves under the same per-team directory as :meth:`findings_path`, so a
        team's granted authority, budget, acceptance criteria, and input
        revisions are persisted next to its findings and metadata (AC-2-6). The
        :class:`~strands_teams.hooks.ArtifactQualityHook` reads every team's
        ``acceptance_criteria`` from these files to evaluate criteria coverage.
        """
        return self.team_dir(team_id) / self.contract_name

    def contract_glob(self) -> str:
        """Glob (relative to :meth:`teams_dir`) matching every per-team contract file."""
        return f"*/{self.contract_name}"

    def metadata_path(self, team_id: str) -> Path:
        """Path to a team's ``metadata.json``."""
        return self.team_dir(team_id) / self.metadata_name

    def debug_log_path(self) -> Path:
        """Path to the append-only team-dispatch debug JSONL."""
        return self.teams_dir() / self.debug_log_name

    def event_log_path(self) -> Path:
        """Path to the typed append-only event-plane JSONL (Phase 5 / spec 006).

        Co-located with :meth:`debug_log_path` under the teams dir so the typed
        event plane supersedes the untyped ``team_debug.jsonl`` without moving the
        run's scratch layout (AC-2-1). One JSON-encoded
        :class:`~strands_teams.models.TeamEvent` per line.
        """
        return self.teams_dir() / self.event_log_name

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

    # -----------------------------------------------------------------------
    # Revision store + mutable alias pointer (Phase 4 / spec 005).
    # The layout is the single source of every path, so the revision store and
    # the alias-pointer map resolve here — no dispatch/read/write/promotion code
    # duplicates a revision or alias path literal (AC-1-3, AC-2-1). The store is
    # content-addressed and append-only; the alias is the only mutable pointer
    # into it and is promoted by a durable optimistic compare-and-set (AC-2-2).
    # -----------------------------------------------------------------------

    def revisions_dir(self) -> Path:
        """Directory holding the content-addressed immutable revision store (AC-1-3)."""
        return self.output_root / self.revisions_subdir

    def revision_path(self, logical_name: str, revision_id: str) -> Path:
        """Resolve the on-disk path for one ``(logical_name, revision_id)`` revision (AC-1-3).

        Every revision of a logical artifact is grouped under a per-artifact
        subdirectory of :meth:`revisions_dir`, keyed by ``revision_id`` (which is
        content-addressed). This is the ONLY place a revision path is derived, so
        dispatch/read/write/promotion code never duplicates the literal (AC-1-3).
        """
        return self.revisions_dir() / _slug(logical_name) / f"{_slug(revision_id)}.json"

    def alias_path(self) -> Path:
        """Path to the mutable ``logical_name -> current revision_id`` alias map (AC-2-1).

        Resolved directly under ``output_root`` in the same manner as
        :meth:`planning_sentinel_path` — a single durable JSON file that is the
        only mutable pointer into the content-addressed revision store.
        """
        return self.output_root / self.alias_name

    def persist_revision(self, revision: ArtifactRevision) -> ArtifactRevision:
        """Persist a revision to its content-addressed path and return it (AC-1-4, AC-1-5).

        Writes the revision keyed by ``revision_id`` under :meth:`revision_path` so
        it is addressable by later reads (AC-1-4). Idempotent for an identical
        re-write (same ``revision_id`` and ``content_hash``). If the same
        ``revision_id`` was already persisted with a DIFFERENT ``content_hash``,
        the write is rejected with :class:`RevisionConflictError` and the stored
        revision is left unchanged (AC-1-5) — the store is append-only and
        content-addressed.

        Returns:
            The stored :class:`ArtifactRevision` (the argument on a fresh write, or
            the already-stored record on an idempotent re-write).

        Raises:
            RevisionConflictError: If ``revision.revision_id`` is already stored
                with content differing from ``revision.content_hash`` (AC-1-5).
        """
        path = self.revision_path(revision.logical_name, revision.revision_id)
        if path.exists():
            stored = ArtifactRevision.model_validate_json(path.read_text(encoding="utf-8"))
            if stored.content_hash != revision.content_hash:
                raise RevisionConflictError(revision.revision_id, stored.content_hash, revision.content_hash)
            return stored
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(revision.model_dump_json(indent=2), encoding="utf-8")
        return revision

    def load_revision(self, logical_name: str, revision_id: str) -> ArtifactRevision | None:
        """Load a persisted revision, or ``None`` when it is not in the store."""
        path = self.revision_path(logical_name, revision_id)
        if not path.exists():
            return None
        return ArtifactRevision.model_validate_json(path.read_text(encoding="utf-8"))

    def current_revision_id(self, logical_name: str) -> str:
        """Return the ``revision_id`` the alias currently points ``logical_name`` at.

        Returns the :data:`~strands_teams.models.NO_CURRENT_REVISION` sentinel when
        ``logical_name`` has no current revision (AC-2-4).
        """
        with self._locked_aliases() as aliases:
            return aliases.get(logical_name, NO_CURRENT_REVISION)

    def promote_revision(
        self,
        logical_name: str,
        expected_current_revision: str,
        proposed_revision: ArtifactRevision,
    ) -> ArtifactRevision:
        """Durably compare-and-set the alias for ``logical_name`` (AC-2-2..AC-2-5).

        The compare-and-set is serialized by an OS advisory lock on the alias file,
        so two concurrent promotions of the same ``logical_name`` with the same
        ``expected_current_revision`` cannot both win: whichever acquires the lock
        first flips the alias, and the other observes the new current and is
        rejected with :class:`StaleArtifactError` (AC-2-5). Because the CAS is a
        durable operation against this on-disk store — not a mutation of the
        by-reference ``invocation_state`` dict — it is correct even when the callers
        share that dict read-only (research-coding-runtimes.yaml lines 22, 30).

        On a matching CAS the proposed revision is persisted (content-addressed)
        with the prior current revision recorded in its ``supersedes`` lineage, and
        the alias is atomically flipped to it (AC-2-2). A first promotion passes the
        :data:`~strands_teams.models.NO_CURRENT_REVISION` sentinel as
        ``expected_current_revision`` and sets the alias as the first revision
        (AC-2-4).

        Args:
            logical_name: The logical artifact to promote.
            expected_current_revision: The revision the caller believes is current
                (the sentinel for a first promotion).
            proposed_revision: The revision to make current. Its ``supersedes`` is
                augmented with the prior current revision before it is persisted.

        Returns:
            The persisted, now-current :class:`ArtifactRevision` (with the prior
            revision recorded in ``supersedes``).

        Raises:
            StaleArtifactError: If ``expected_current_revision`` does not match the
                alias's stored current revision — the alias is left unchanged
                (AC-2-3).
            RevisionConflictError: If persisting the proposed revision conflicts
                with an already-stored revision of the same id (AC-1-5).
        """
        with self._locked_aliases() as aliases:
            actual = aliases.get(logical_name, NO_CURRENT_REVISION)
            if actual != expected_current_revision:
                raise StaleArtifactError(logical_name, expected_current_revision, actual)

            superseded = [] if actual == NO_CURRENT_REVISION else [actual]
            merged_supersedes = list(dict.fromkeys([*proposed_revision.supersedes, *superseded]))
            to_store = proposed_revision.model_copy(update={"supersedes": merged_supersedes})

            stored = self.persist_revision(to_store)
            aliases[logical_name] = stored.revision_id
            self._write_aliases(aliases)
            return stored

    @contextlib.contextmanager
    def _locked_aliases(self) -> Iterator[dict[str, str]]:
        """Yield the current alias map under an exclusive OS advisory file lock.

        Serializes every alias read/CAS through ``fcntl.flock`` on a sidecar lock
        file so concurrent promotions of the same ``logical_name`` are ordered by
        the OS, not by luck (AC-2-5). The lock file is separate from the alias JSON
        so the lock survives an atomic rewrite of the alias map. Reads see the
        alias map as of lock acquisition; mutations are persisted by the caller via
        :meth:`_write_aliases` before the lock is released.
        """
        alias_path = self.alias_path()
        alias_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = alias_path.with_suffix(alias_path.suffix + ".lock")
        with lock_path.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield self._read_aliases()
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _read_aliases(self) -> dict[str, str]:
        """Read the alias map from disk (empty map when absent or unreadable)."""
        alias_path = self.alias_path()
        if not alias_path.exists():
            return {}
        try:
            data = json.loads(alias_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def _write_aliases(self, aliases: dict[str, str]) -> None:
        """Atomically rewrite the alias map (write-to-temp then ``os.replace``)."""
        alias_path = self.alias_path()
        alias_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = alias_path.with_suffix(alias_path.suffix + ".tmp")
        tmp.write_text(json.dumps(aliases, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(alias_path)

    # -----------------------------------------------------------------------
    # Approvals store, decision log, and human-gate sentinels (Phase 6 / spec 007).
    # The layout owns every governance path so the PolicyEngine and runner never
    # duplicate a literal (AC-2-4, AC-3-7, AC-4-3). The approvals store is keyed by
    # token_id; the decision log is append-only; each pending human gate is one
    # JSON file keyed by decision_id under the human-gates dir.
    # -----------------------------------------------------------------------

    def approvals_dir(self) -> Path:
        """Directory holding the per-``token_id`` :class:`ApprovalToken` store (AC-3-7)."""
        return self.output_root / self.scratch_subdir / self.approvals_subdir

    def approval_path(self, token_id: str) -> Path:
        """Resolve the on-disk path for one approval token, keyed by ``token_id``."""
        return self.approvals_dir() / f"{_slug(token_id)}.json"

    def persist_approval(self, token: ApprovalToken) -> ApprovalToken:
        """Persist an :class:`ApprovalToken` to the approvals store and return it (AC-1-1)."""
        path = self.approval_path(token.token_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token.model_dump_json(indent=2), encoding="utf-8")
        return token

    def load_approval(self, token_id: str) -> ApprovalToken | None:
        """Load one persisted approval token, or ``None`` when absent/unreadable."""
        path = self.approval_path(token_id)
        if not path.exists():
            return None
        try:
            return ApprovalToken.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def load_approvals(self) -> list[ApprovalToken]:
        """Load every persisted approval token (skipping any that fail to parse — AC-3-7)."""
        approvals_dir = self.approvals_dir()
        if not approvals_dir.exists():
            return []
        tokens: list[ApprovalToken] = []
        for path in sorted(approvals_dir.glob("*.json")):
            try:
                tokens.append(ApprovalToken.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                continue
        return tokens

    def decision_log_path(self) -> Path:
        """Path to the append-only decision log — one :class:`Decision` per line (AC-2-4)."""
        return self.output_root / self.scratch_subdir / self.decision_log_name

    def append_decision(self, decision: Decision) -> None:
        """Append one :class:`Decision` to the decision log (AC-2-4).

        Raises on I/O failure rather than swallowing it, so a transition can never
        proceed with its decision unrecorded (AC-2-5). The caller decides how to
        react; unlike the debug log, this path is auth-critical.

        Raises:
            OSError: If the decision line cannot be written.
        """
        path = self.decision_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(decision.model_dump_json() + "\n")

    def human_gates_dir(self) -> Path:
        """Directory holding pending :class:`HumanDecision` gate sentinels (AC-4-3, AC-4-5)."""
        return self.output_root / self.scratch_subdir / self.human_gates_subdir

    def human_gate_path(self, decision_id: str) -> Path:
        """Resolve the on-disk path for one human-gate sentinel, keyed by ``decision_id``."""
        return self.human_gates_dir() / f"{_slug(decision_id)}.json"

    def persist_human_gate(self, gate: HumanDecision) -> HumanDecision:
        """Persist a :class:`HumanDecision` gate to disk and return it (AC-4-5).

        A pending gate persisted here is what lets a swarm node return control
        rather than block past its ``node_timeout`` (AC-4-5): the gate survives on
        disk and the runner resumes from it later.
        """
        path = self.human_gate_path(gate.decision_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gate.model_dump_json(indent=2), encoding="utf-8")
        return gate

    def load_human_gate(self, decision_id: str) -> HumanDecision | None:
        """Load one persisted human-gate sentinel, or ``None`` when absent/unreadable."""
        path = self.human_gate_path(decision_id)
        if not path.exists():
            return None
        try:
            return HumanDecision.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def load_pending_human_gates(self) -> list[HumanDecision]:
        """Load every human gate still ``pending`` (the runner suspends on these — AC-4-3)."""
        gates_dir = self.human_gates_dir()
        if not gates_dir.exists():
            return []
        pending: list[HumanDecision] = []
        for path in sorted(gates_dir.glob("*.json")):
            try:
                gate = HumanDecision.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if gate.status == "pending":
                pending.append(gate)
        return pending


def _slug(value: str) -> str:
    """Reduce an arbitrary logical name / revision id to a filesystem-safe path segment.

    Replaces every character outside ``[A-Za-z0-9._-]`` with ``_`` so a
    ``logical_name`` like ``"bundles/auth"`` or a sentinel-bearing id cannot escape
    the revision store directory or collide with a path separator.
    """
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value) or "_"
