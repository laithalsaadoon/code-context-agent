"""Base Pydantic models and generic result primitives for strands_teams.

This module hosts the framework-level data-transfer types shared by dispatch,
hooks, display, and use-case layers. All models are domain-agnostic: they carry
no codebase-analysis vocabulary. Concrete use cases subclass or compose these.

Copied and renamed from the pre-split ``code_context_agent`` package:
- ``StrictModel`` / ``FrozenModel`` from ``models/base.py``.
- ``Bundle``, ``GeneratedFile``, ``PhaseTiming`` from ``models/output.py`` (verbatim).
- ``AnalysisConfidence`` -> ``TeamConfidence`` and ``CalibratedJudgment`` (renamed).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping


def _utcnow() -> datetime:
    """Return the current UTC timestamp (default factory for lifecycle fields)."""
    return datetime.now(UTC)


class StrictModel(BaseModel):
    """Base model with strict validation.

    This model:
    - Allows mutation (frozen=False)
    - Validates assignments
    - Forbids extra fields
    - Strips whitespace from strings

    Use for internal mutable state.
    """

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class FrozenModel(BaseModel):
    """Immutable model (like a frozen dataclass).

    This model:
    - Prevents mutation (frozen=True)
    - Validates assignments
    - Forbids extra fields

    Use for result objects and immutable data transfer.
    """

    model_config = ConfigDict(
        frozen=True,
        validate_assignment=True,
        extra="forbid",
    )


class GeneratedFile(FrozenModel):
    """Record of a file generated during a run."""

    path: str = Field(description="Relative path to the generated file")
    line_count: int = Field(ge=0, description="Number of lines in the file")
    description: str = Field(description="What this file contains")


class Bundle(FrozenModel):
    """A narrative bundle about a specific area of the domain."""

    area: str = Field(description="Bundle area identifier (e.g., 'auth', 'hotspots', 'security')")
    path: str = Field(description="Relative path to the bundle file")
    line_count: int = Field(ge=0, description="Number of lines in the bundle")
    summary: str = Field(description="One-sentence summary of this bundle's content")
    focus_match: bool = Field(default=False, description="Whether this bundle was generated for the focus area")


class PhaseTiming(FrozenModel):
    """Timing data for a single run phase."""

    phase: int = Field(description="Phase number (1-based)")
    name: str = Field(description="Phase name (e.g., 'indexing', 'team-structure', 'synthesis')")
    start_offset_seconds: float = Field(ge=0.0, default=0.0, description="Seconds from run start")
    duration_seconds: float = Field(ge=0.0, description="Phase duration in seconds")
    tool_count: int = Field(ge=0, default=0, description="Number of tool calls in this phase")
    status: str = Field(default="completed", description="Phase status")


class TeamConfidence(FrozenModel):
    """Confidence metadata attached to any team judgment.

    Renamed from ``AnalysisConfidence`` to keep the framework layer
    domain-agnostic.
    """

    score: float = Field(ge=0.0, le=1.0, default=0.5, description="Calibrated confidence (0.5=uncertain, 0.9+=high)")
    evidence_sources: list[str] = Field(default_factory=list, description="Data sources supporting this judgment")
    information_gaps: list[str] = Field(default_factory=list, description="What couldn't be determined")
    agreement_level: Literal["unanimous", "majority", "split", "contested"] = Field(
        default="unanimous",
        description="Level of agreement among teams",
    )
    dissenting_signals: list[str] = Field(
        default_factory=list,
        description="Signals arguing against the primary judgment",
    )
    data_freshness: Literal["current", "stale", "outdated"] = Field(
        default="current",
        description="Whether underlying data is current",
    )
    index_age_hours: float = Field(ge=0.0, default=0.0, description="Hours since last full index")


class CalibratedJudgment(FrozenModel):
    """A judgment with attached confidence and auditable reasoning."""

    judgment: str = Field(description="The conclusion")
    confidence: TeamConfidence = Field(default_factory=TeamConfidence)
    reasoning_chain: list[str] = Field(default_factory=list, description="Step-by-step reasoning")
    counterfactual: str | None = Field(
        default=None,
        description="What would change the judgment (e.g., 'adding tests would lower risk')",
    )


# ---------------------------------------------------------------------------
# Task-contract authority + budget (ASTR §4.1, §4.7, §6.2, §14.7).
# A dispatched team is granted a typed ``AuthorityGrant`` (what it may do) and a
# ``Budget`` (how much it may spend) as part of its task contract. Both are
# immutable ``FrozenModel``s so they are safe to pass by reference through
# ``invocation_state`` and treated read-only by concurrently-executing node tools
# (research-coding-runtimes.yaml strands_tool_pattern lines 20-22, 30). The
# framework enforces ``AuthorityGrant.may_create_blocking_finding`` structurally
# in ``dispatch_team`` code — never via a team-agent system prompt (ASTR §4.7).
# ---------------------------------------------------------------------------


class AuthorityGrant(FrozenModel):
    """What a dispatched team is permitted to do (ASTR §4.7).

    A capability grant attached to a team's task contract. Every capability
    defaults to ``False`` (deny by default): a caller must explicitly widen a
    team's authority. The framework enforces ``may_create_blocking_finding`` in
    ``dispatch_team`` code — a team whose grant forbids blocking findings cannot
    persist one, regardless of what its agents' prompts say.

    Attributes:
        may_create_blocking_finding: Whether this team may persist a
            ``Finding`` with ``blocking=True``. Enforced structurally by
            ``dispatch_team`` (a forbidden blocking finding is stripped from the
            persisted typed-findings artifact and the dispatch returns an error).
        may_write: Whether a later-phase specialist coding runtime granted to
            this team may modify files. A Codex ``amazon-bedrock`` runtime gates
            file writes on this flag (research-coding-runtimes.yaml
            codex_gpt56_bedrock; consumed in Phase 3 / spec 004-coding-runtime).
        may_approve: Whether this team may approve/accept work (e.g. mark a
            finding remediated or sign off an artifact revision).
    """

    may_create_blocking_finding: bool = Field(
        default=False,
        description="May this team persist a blocking finding (enforced in dispatch_team code)",
    )
    may_write: bool = Field(
        default=False,
        description="May a granted coding runtime modify files (Codex may_write gate, Phase 3)",
    )
    may_approve: bool = Field(
        default=False,
        description="May this team approve/accept work (sign off findings or artifact revisions)",
    )


class Budget(FrozenModel):
    """How much a dispatched team may spend (ASTR §6.2).

    The resource envelope attached to a team's task contract. All three limits
    are non-negative; a negative value is rejected at construction
    (``FrozenModel`` validates assignments). A value of ``0`` means "no explicit
    limit supplied" — ``dispatch_team`` only overrides the run-default swarm
    ``execution_timeout`` when ``max_wall_clock_minutes`` is strictly positive.

    Attributes:
        max_wall_clock_minutes: Wall-clock ceiling for the team, in minutes.
            ``dispatch_team`` uses ``max_wall_clock_minutes * 60`` as the swarm's
            ``execution_timeout`` when positive.
        max_cost_usd: Dollar ceiling for the team. This is the framework-contract
            value that a Bedrock ``ClaudeAgentRuntime`` maps to the Claude Agent
            SDK's ``max_budget_usd`` option (note the field-name difference:
            ``max_cost_usd`` here vs. ``max_budget_usd`` in the SDK —
            research-coding-runtimes.yaml claude_agent_sdk_bedrock; consumed in
            Phase 3 / spec 004-coding-runtime).
        max_retries: Retry ceiling. Bounds a Codex harness's retry count when a
            specialist coding runtime is granted (research-coding-runtimes.yaml
            codex_gpt56_bedrock; consumed in Phase 3 / spec 004-coding-runtime).
    """

    max_wall_clock_minutes: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock ceiling in minutes; when positive, drives the swarm execution_timeout",
    )
    max_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Dollar ceiling; maps to the Claude Agent SDK max_budget_usd option (Phase 3)",
    )
    max_retries: int = Field(
        default=0,
        ge=0,
        description="Retry ceiling; bounds a Codex harness's retry count (Phase 3)",
    )


# ---------------------------------------------------------------------------
# Typed findings + evidence (ASTR §6.6, §8.3, §4.5).
# A ``Finding`` is the structured, queryable replacement for the free-text
# prose a team used to bury inside ``findings.md``. It carries a severity, a
# blocking flag, a disposition lifecycle, evidence refs, and calibrated
# confidence. Both models are immutable ``FrozenModel``s so a typed finding is
# safe to pass by reference through ``invocation_state`` and re-used as-is by
# later phases (``SpecialistResult.findings``).
# ---------------------------------------------------------------------------

EvidenceType = Literal["file_line", "test", "command", "artifact", "url", "log"]
"""Kinds of evidence a finding can cite (file:line, a test, a command exit, an
artifact revision, a URL, or a log excerpt)."""

Severity = Literal["info", "low", "medium", "high", "critical"]
"""Ordered severity levels for a finding."""

Disposition = Literal["open", "acknowledged", "remediated", "accepted_risk", "rejected", "superseded"]
"""Lifecycle states a finding moves through as it is triaged and resolved."""


class Evidence(FrozenModel):
    """A single evidence reference backing a finding.

    Attributes:
        type: The kind of evidence (``file_line``, ``test``, ``command``,
            ``artifact``, ``url``, ``log``).
        uri: A locator for the evidence — e.g. ``src/auth.py:42`` for a
            ``file_line`` ref, a test node id, a command string, an artifact
            revision id, or a URL. Required and non-empty.
        detail: Optional human-readable note about what this evidence shows.
    """

    type: EvidenceType = Field(description="Kind of evidence (file_line, test, command, artifact, url, log)")
    uri: str = Field(min_length=1, description="Locator for the evidence (e.g. 'src/auth.py:42', a test id, a URL)")
    detail: str | None = Field(default=None, description="Optional note about what this evidence shows")


class Finding(FrozenModel):
    """A typed, evidence-bearing finding produced by a team (ASTR §6.6, §8.3).

    Immutable and ``extra="forbid"`` (inherited from :class:`FrozenModel`), so a
    finding is safe to pass by reference through ``invocation_state`` and any
    on-disk ``findings.json`` that carries unknown keys is rejected at the parse
    boundary rather than silently accepted.

    Attributes:
        statement: The finding itself, stated in one or two sentences (non-empty).
        severity: One of ``info``/``low``/``medium``/``high``/``critical``.
        blocking: Whether this finding must be resolved before the work is
            accepted. A ``blocking`` finding is subject to the evidence gate
            (must carry ≥1 evidence ref).
        disposition: Lifecycle state (defaults to ``open``).
        affected_artifacts: Paths/ids of artifacts this finding is about.
        evidence: Evidence refs backing the finding (file:line / test / command
            exit / artifact / url / log).
        confidence: Calibrated confidence in ``[0.0, 1.0]``.
        created_by: Identifier of the agent/team that authored the finding.
        created_at: UTC creation timestamp.
        updated_at: UTC timestamp of the last lifecycle update.
    """

    statement: str = Field(min_length=1, description="The finding, stated in one or two sentences")
    severity: Severity = Field(description="Severity level (info, low, medium, high, critical)")
    blocking: bool = Field(default=False, description="Whether this finding must be resolved before acceptance")
    disposition: Disposition = Field(default="open", description="Lifecycle state of the finding")
    affected_artifacts: list[str] = Field(
        default_factory=list,
        description="Paths or ids of the artifacts this finding is about",
    )
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence refs backing the finding")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5, description="Calibrated confidence in [0.0, 1.0]")
    created_by: str = Field(default="", description="Identifier of the agent/team that authored the finding")
    created_at: datetime = Field(default_factory=_utcnow, description="UTC creation timestamp")
    updated_at: datetime = Field(default_factory=_utcnow, description="UTC timestamp of the last lifecycle update")
    reviewed_revision: str = Field(
        default="",
        description=(
            "Revision binding (Phase 4, spec 005): the ArtifactRevision.revision_id this finding was "
            "produced against. Empty string when the finding is not bound to a revision. Additive to the "
            "Phase 1 Finding — never redefines it (spec 005 AC-3-1)."
        ),
    )
    reviewed_facets: list[str] = Field(
        default_factory=list,
        description=(
            "Revision binding (Phase 4, spec 005): the artifact facets this finding's review covers. "
            "invalidates_review intersects these with semantic_diff's changed facets to decide reuse "
            "vs. re-open (spec 005 AC-3-3/AC-3-4). Empty means the review covers the whole artifact."
        ),
    )


# ---------------------------------------------------------------------------
# Artifact revisions + content addressing (ASTR §4.8, §6.4, §12.6; spec 005).
# An ``ArtifactRevision`` is an immutable, content-addressed record of one
# version of a logical artifact. The revision store (keyed by revision_id) and
# the mutable ``logical_name -> current revision_id`` alias pointer are owned by
# ``TeamsLayout`` (the single source of every path); promotion of the alias is a
# durable optimistic compare-and-set. These types are ``FrozenModel``s so they
# pass by reference through ``invocation_state`` and are treated read-only by
# concurrently-executing node tools (research-coding-runtimes.yaml lines 22, 30):
# the alias CAS is a durable operation against the layout store, never a mutation
# of shared in-memory state.
# ---------------------------------------------------------------------------

NO_CURRENT_REVISION = "∅"
"""Sentinel ``expected_current_revision`` meaning "this logical_name has no current revision yet".

A first promotion of a ``logical_name`` passes this sentinel as
``expected_current_revision`` (spec 005 AC-2-4). It is deliberately a value no
real ``revision_id`` (a hex content hash) can collide with.
"""


def _facet_hashes(facets: Mapping[str, str]) -> dict[str, str]:
    """Hash each facet's content independently for facet-level ``semantic_diff``.

    Returns a ``{facet_name: sha256(content)}`` mapping so two revisions can be
    diffed facet-by-facet (spec 005 AC-3-2) without carrying the raw content in
    the immutable metadata record.
    """
    return {name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in facets.items()}


def compute_content_hash_from_facet_hashes(facet_hashes: Mapping[str, str]) -> str:
    """Fold a revision's per-facet hashes into one whole-artifact ``content_hash``.

    A stable SHA-256 over the sorted ``{facet_name: facet_hash}`` mapping. Because
    it consumes only the persisted per-facet hashes, the whole-artifact hash is
    reproducible from a round-tripped record (the model validator uses exactly
    this), while remaining a deterministic function of content — identical content
    yields identical facet hashes and thus an identical fold; any differing byte
    changes at least one facet hash and thus the fold (AC-1-2).
    """
    canonical = json.dumps(dict(facet_hashes), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_content_hash(facets: Mapping[str, str]) -> str:
    """Return a deterministic content hash over a revision's facet contents (AC-1-2).

    The hash is a function of the facet *content* alone — not of any metadata
    (``logical_name``, ``produced_by``, ``created_at``). Each facet's content is
    hashed independently, then those per-facet hashes are folded into one digest
    via :func:`compute_content_hash_from_facet_hashes` (so the whole-artifact hash
    and the per-facet hashes on the persisted record always agree). Two revisions
    built from byte-identical facet content therefore produce equal hashes; any
    differing byte produces a different hash (AC-1-2).

    Args:
        facets: Mapping of facet name to that facet's content string. An empty
            mapping hashes to the well-defined fold of ``{}``.

    Returns:
        A 64-char lowercase hex SHA-256 digest.
    """
    return compute_content_hash_from_facet_hashes(_facet_hashes(facets))


class ArtifactRevision(FrozenModel):
    """An immutable, content-addressed revision of one logical artifact (ASTR §4.8, §6.4).

    A lightweight metadata record — not the artifact blob itself, which lives at
    the layout-resolved path. Immutable and ``extra="forbid"`` (inherited from
    :class:`FrozenModel`): any attempt to assign a field after construction raises
    a ``pydantic.ValidationError`` (AC-1-1). Build one with :meth:`from_content`
    so ``content_hash`` and per-facet hashes are computed deterministically from
    the content (AC-1-2); constructing directly is supported for round-tripping a
    persisted revision.

    Attributes:
        artifact_id: Stable identity of the *logical* artifact across all its
            revisions (defaults to ``logical_name`` in :meth:`from_content`).
        revision_id: Identity of this specific revision. Content-addressed:
            defaults to ``content_hash`` in :meth:`from_content`. The revision
            store is keyed by this (AC-1-4).
        content_hash: Deterministic hash of the revision's content alone (AC-1-2).
        artifact_type: The kind of artifact (e.g. ``"bundle"``, ``"finding_set"``,
            ``"patch"``) — opaque to the framework.
        logical_name: The name the alias pointer maps to a current revision.
        produced_by: Identifier of the team/agent/runtime that produced it.
        parents: ``revision_id``s this revision was derived from (inputs).
        supersedes: ``revision_id``s this revision replaces (lineage; a promotion
            records the prior current revision here — AC-2-2).
        classification: Handling classification (e.g. ``"internal"``).
        facets: Per-facet content hashes (``{facet_name: sha256}``) enabling the
            facet-level :func:`~strands_teams.revisions.semantic_diff` (AC-3-2).
        created_at: UTC creation timestamp (not part of ``content_hash``).
    """

    artifact_id: str = Field(min_length=1, description="Stable identity of the logical artifact across revisions")
    revision_id: str = Field(min_length=1, description="Identity of this specific revision (content-addressed)")
    content_hash: str = Field(min_length=1, description="Deterministic hash of the revision content alone")
    artifact_type: str = Field(default="artifact", description="Kind of artifact (opaque to the framework)")
    logical_name: str = Field(min_length=1, description="Name the alias pointer maps to a current revision")
    produced_by: str = Field(default="", description="Identifier of the team/agent/runtime that produced it")
    parents: list[str] = Field(default_factory=list, description="revision_ids this revision was derived from")
    supersedes: list[str] = Field(default_factory=list, description="revision_ids this revision replaces (lineage)")
    classification: str = Field(default="internal", description="Handling classification (e.g. 'internal')")
    facets: dict[str, str] = Field(
        default_factory=dict,
        description="Per-facet content hashes enabling facet-level semantic_diff (AC-3-2)",
    )
    created_at: datetime = Field(default_factory=_utcnow, description="UTC creation timestamp (not part of the hash)")

    @model_validator(mode="after")
    def _check_content_hash_matches_facets(self) -> ArtifactRevision:
        """Reject a directly-constructed revision whose ``content_hash`` disagrees with its facets.

        Guards the content-addressing invariant at the parse boundary: a revision
        round-tripped from disk with non-empty facets must carry the hash those
        facets imply. Revisions with no facets (content supplied out of band) are
        exempt, since their ``content_hash`` was computed over that external body.
        """
        if self.facets and self.content_hash != compute_content_hash_from_facet_hashes(self.facets):
            msg = "ArtifactRevision.content_hash does not match its facets — build with from_content()"
            raise ValueError(msg)
        return self

    @classmethod
    def from_content(
        cls,
        *,
        logical_name: str,
        facets: Mapping[str, str] | None = None,
        body: str | None = None,
        artifact_type: str = "artifact",
        produced_by: str = "",
        artifact_id: str | None = None,
        revision_id: str | None = None,
        parents: list[str] | None = None,
        supersedes: list[str] | None = None,
        classification: str = "internal",
        created_at: datetime | None = None,
    ) -> ArtifactRevision:
        """Build a revision from content, computing ``content_hash`` deterministically (AC-1-2).

        Supply the content either as ``facets`` (a ``{facet_name: content}``
        mapping, enabling facet-level ``semantic_diff``) or as a single ``body``
        string (wrapped as one ``"body"`` facet). ``content_hash`` and
        ``revision_id`` (which defaults to ``content_hash`` — content addressing)
        are derived from the content alone; ``artifact_id`` defaults to
        ``logical_name``.

        Args:
            logical_name: The logical artifact's name.
            facets: Facet-name to facet-content mapping. Mutually exclusive with
                ``body``; when both are omitted the revision has empty content.
            body: A single-blob content string (wrapped as facet ``"body"``).
            artifact_type: Kind of artifact.
            produced_by: Producer identifier.
            artifact_id: Stable logical identity; defaults to ``logical_name``.
            revision_id: Explicit revision id; defaults to ``content_hash``.
            parents: Input revision ids.
            supersedes: Superseded revision ids.
            classification: Handling classification.
            created_at: Explicit timestamp; defaults to now.

        Returns:
            A frozen :class:`ArtifactRevision`.
        """
        resolved_facets: dict[str, str] = dict(facets) if facets is not None else {}
        if body is not None:
            resolved_facets.setdefault("body", body)
        content_hash = compute_content_hash(resolved_facets)
        facet_hashes = _facet_hashes(resolved_facets)
        return cls(
            artifact_id=artifact_id or logical_name,
            revision_id=revision_id or content_hash,
            content_hash=content_hash,
            artifact_type=artifact_type,
            logical_name=logical_name,
            produced_by=produced_by,
            parents=list(parents or []),
            supersedes=list(supersedes or []),
            classification=classification,
            facets=facet_hashes,
            created_at=created_at or _utcnow(),
        )


class StaleArtifactError(Exception):
    """Raised by ``promote_artifact`` when the optimistic compare-and-set fails (AC-2-3).

    Signals that ``expected_current_revision`` did not match the alias's stored
    current revision for ``logical_name`` — another promotion won the race, or the
    caller's view of "current" is stale. The alias pointer is left unchanged.

    Attributes:
        logical_name: The artifact whose promotion was rejected.
        expected: The revision the caller believed was current.
        actual: The revision actually current in the alias store.
    """

    def __init__(self, logical_name: str, expected: str, actual: str) -> None:
        """Record the artifact, the expected revision, and the actually-current one."""
        self.logical_name = logical_name
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"stale promotion of {logical_name!r}: expected current revision {expected!r} but alias holds {actual!r}",
        )


class RevisionConflictError(Exception):
    """Raised when a revision_id is re-persisted with content differing from the stored one (AC-1-5).

    The revision store is content-addressed and append-only: a given
    ``revision_id`` binds to exactly one ``content_hash``. Re-persisting the same
    ``revision_id`` with identical content is idempotent; re-persisting it with
    differing content is a conflict that leaves the stored revision unchanged.

    Attributes:
        revision_id: The revision id that was re-persisted with differing content.
        stored_hash: The content hash already on disk for that revision id.
        incoming_hash: The content hash of the rejected write.
    """

    def __init__(self, revision_id: str, stored_hash: str, incoming_hash: str) -> None:
        """Record the conflicting revision id and the stored vs. incoming content hashes."""
        self.revision_id = revision_id
        self.stored_hash = stored_hash
        self.incoming_hash = incoming_hash
        super().__init__(
            f"revision {revision_id!r} already persisted with content_hash {stored_hash!r}; "
            f"refusing to overwrite with differing content {incoming_hash!r}",
        )


class PromotionResult(FrozenModel):
    """The structured outcome of a successful ``promote_artifact`` compare-and-set (AC-2-6).

    Returned (as ``model_dump_json()``) by the ``promote_artifact`` tool so the
    calling agent parses JSON per the strands tool norm (research-coding-runtimes
    line 26).

    Attributes:
        logical_name: The promoted artifact's logical name.
        current_revision: The new current ``revision_id`` after promotion.
        superseded_revision: The prior current ``revision_id`` (the
            :data:`NO_CURRENT_REVISION` sentinel when this was the first revision).
    """

    logical_name: str = Field(description="The promoted artifact's logical name")
    current_revision: str = Field(description="The new current revision_id after promotion")
    superseded_revision: str = Field(description="The prior current revision_id (sentinel if first)")


# ---------------------------------------------------------------------------
# Typed event plane (ASTR §6.5, §7, §15.2; spec 006).
# A ``TeamEvent`` is the typed, causal, replayable unit that supersedes today's
# untyped ``team_debug.jsonl`` line. It carries causal parents, a correlation id,
# and an epistemic status so the event log is a lossless, replayable substrate —
# NOT a transcript store. It deliberately carries NO chain-of-thought or raw
# model transcript (plan §5 "Boundary discipline"; research-coding-runtimes.yaml
# line 99): ``payload`` holds only the structured outcome of a transition. Because
# ``FrozenModel`` is ``extra="forbid"``, an attempt to smuggle a ``transcript`` or
# ``chain_of_thought`` field is rejected at construction (AC-1-4).
# ---------------------------------------------------------------------------

EpistemicStatus = Literal[
    "observed",
    "inferred",
    "hypothesized",
    "disputed",
    "accepted",
    "rejected",
    "superseded",
]
"""The epistemic standing of a ``TeamEvent``'s claim (AC-1-2).

- ``observed`` — a directly recorded fact (e.g. a dispatch completed).
- ``inferred`` — derived from other events without direct observation.
- ``hypothesized`` — a tentative claim awaiting confirmation.
- ``disputed`` — contradicted by another event/actor.
- ``accepted`` / ``rejected`` — an adjudicated outcome.
- ``superseded`` — replaced by a later event (retained in the log, never deleted).
"""


class TeamEvent(FrozenModel):
    """A typed, causal, replayable event on the team event plane (ASTR §6.5, §7).

    Immutable and ``extra="forbid"`` (inherited from :class:`FrozenModel`): an
    unexpected field — notably a raw-transcript or chain-of-thought field — is
    rejected at construction rather than silently accepted (AC-1-4). Serializing
    one event to a single JSON line and re-parsing it yields an equal object, so
    the event log is a lossless round-trip substrate for replay (AC-1-5).

    ``event_id``, ``created_at``, and ``correlation_id`` are auto-assigned when
    omitted: a fresh UUID event id, a UTC creation timestamp, and a correlation id
    defaulting to ``goal_id`` when present and otherwise to the assigned
    ``event_id`` (AC-1-3).

    Attributes:
        event_id: Unique identity of this event (auto-assigned UUID hex if omitted).
        event_type: The kind of transition/claim (e.g. ``"team.dispatched"``,
            ``"critical_finding.created"``, ``"goal.decompose"``) — opaque to the
            framework except for the ``should_replan`` trigger vocabulary.
        actor: Identifier of the agent/team/runtime that produced the event.
        recipients: Identifiers this event is addressed to (empty = broadcast).
        thread_id: The conversation/thread this event belongs to.
        goal_id: The goal (durable lead loop) this event belongs to.
        task_id: The task within the goal this event belongs to.
        artifact_bindings: ``revision_id``/artifact ids this event is about.
        causal_parents: ``event_id``s this event causally follows (replay applies
            an event only after all its causal parents — AC-3-3).
        correlation_id: Groups related events; defaults to ``goal_id`` or
            ``event_id`` (AC-1-3).
        epistemic_status: The claim's standing (AC-1-2).
        payload: The structured outcome of the transition — NO transcript/CoT.
        evidence: Evidence refs backing the event (reuses the P1 type).
        created_at: UTC creation timestamp (auto-assigned if omitted).
    """

    event_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identity of this event")
    event_type: str = Field(min_length=1, description="The kind of transition/claim this event records")
    actor: str = Field(default="", description="Identifier of the agent/team/runtime that produced the event")
    recipients: list[str] = Field(default_factory=list, description="Identifiers this event is addressed to")
    thread_id: str = Field(default="", description="The conversation/thread this event belongs to")
    goal_id: str = Field(default="", description="The goal (durable lead loop) this event belongs to")
    task_id: str = Field(default="", description="The task within the goal this event belongs to")
    artifact_bindings: list[str] = Field(
        default_factory=list,
        description="revision_id/artifact ids this event is about",
    )
    causal_parents: list[str] = Field(
        default_factory=list,
        description="event_ids this event causally follows (replay ordering)",
    )
    correlation_id: str = Field(default="", description="Groups related events; defaults to goal_id or event_id")
    epistemic_status: EpistemicStatus = Field(default="observed", description="The claim's epistemic standing")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured outcome of the transition (never a transcript or chain-of-thought)",
    )
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence refs backing the event")
    created_at: datetime = Field(default_factory=_utcnow, description="UTC creation timestamp")

    @model_validator(mode="after")
    def _default_correlation_id(self) -> TeamEvent:
        """Default ``correlation_id`` to ``goal_id`` when present, else ``event_id`` (AC-1-3).

        Runs after field assignment so ``event_id`` (which itself defaults to a
        fresh UUID) is available. A frozen model cannot assign in place, so the
        default is applied via ``object.__setattr__`` — the same mechanism pydantic
        uses to populate a frozen instance.
        """
        if not self.correlation_id:
            object.__setattr__(self, "correlation_id", self.goal_id or self.event_id)
        return self


# ---------------------------------------------------------------------------
# Authority gates, approvals, and decisions (ASTR §14, §11, §6.7-6.8; spec 007).
# This is the governance layer: the model proposes a consequential transition and
# the ``PolicyEngine`` (see ``strands_teams.policy``) makes the FINAL allow/deny.
# The types below are the immutable records the engine reads and writes:
#
# - ``ApprovalToken`` — a revision-bound, signed, expiring grant of approval. It
#   names the exact ``ArtifactRevision`` it approves (by id AND content hash so it
#   goes stale on material change), carries an HMAC ``signature`` the engine
#   verifies before trusting it, and declares the facet ``invalidation_rules`` that
#   sunset it when a superseding revision touches one of them (AC-1-1..AC-1-5,
#   AC-5-1..AC-5-4).
# - ``Decision`` — the auditable record of a consequential choice: the question,
#   options, selection, rationale, evidence (reusing the P1 ``Evidence`` vocabulary),
#   dissent, and the prior ``decision_id`` it supersedes (AC-2-1..AC-2-5).
# - ``HumanDecision`` — a first-class protocol state: a pending gate the run
#   suspends on rather than letting the model self-approve (AC-4-1..AC-4-6).
# - ``TransitionRequest`` — the proposed transition the engine judges (AC-6-1).
# - ``PolicyVerdict`` — the engine's final answer (AC-3-1).
#
# All are ``FrozenModel``s so they pass by reference through ``invocation_state``
# and are treated read-only by concurrently-executing node tools
# (research-coding-runtimes.yaml strands_tool_pattern lines 20-22, 30).
# ---------------------------------------------------------------------------


def approval_signing_payload(
    *,
    token_id: str,
    approved_transition_kind: str,
    artifact_revision_id: str,
    revision_content_hash: str,
    approver: str,
    valid_until: datetime,
) -> str:
    """Return the canonical serialization an ``ApprovalToken`` signs over (AC-1-3).

    A stable, sorted JSON object over exactly the six fields the signature binds:
    ``token_id``, ``approved_transition_kind``, ``artifact_revision_id``,
    ``revision_content_hash``, ``approver``, and ``valid_until`` (serialized as an
    ISO-8601 string). Deterministic — identical inputs always fold to the same
    string — so :meth:`ApprovalToken.mint` and
    :meth:`ApprovalToken.signature_valid` sign/verify over byte-identical payloads.
    """
    canonical = json.dumps(
        {
            "approved_transition_kind": approved_transition_kind,
            "approver": approver,
            "artifact_revision_id": artifact_revision_id,
            "revision_content_hash": revision_content_hash,
            "token_id": token_id,
            "valid_until": valid_until.isoformat(),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return canonical


def _sign(payload: str, signing_key: str) -> str:
    """Compute the HMAC-SHA256 hex signature of ``payload`` under ``signing_key``."""
    return hmac.new(signing_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


class ApprovalToken(FrozenModel):
    """A revision-bound, signed, expiring approval of a consequential transition (ASTR §14.7).

    An approval names the exact :class:`ArtifactRevision` it approves — by both
    ``artifact_revision_id`` and the revision's immutable ``revision_content_hash``
    captured at grant time (AC-1-2) — so it goes stale the moment the artifact
    changes (AC-5-1). Its ``signature`` is an HMAC over the canonical serialization
    of its identity fields (:func:`approval_signing_payload`), so a forged or
    tampered token fails :meth:`signature_valid` and the ``PolicyEngine`` treats it
    as absent (AC-1-4). Past ``valid_until`` the engine rejects it as expired
    (AC-1-5). ``invalidation_rules`` names the artifact facets whose change sunsets
    the approval (AC-5-2, AC-5-3).

    Build one with :meth:`mint` so the signature is computed with the configured
    signing key; constructing directly is supported for round-tripping a persisted
    token.

    Attributes:
        token_id: Unique identity of this approval (auto UUID hex when minted).
        approved_transition_kind: The transition ``kind`` this token approves
            (e.g. ``"release"``). An approval for one kind never authorizes another.
        artifact_revision_id: The ``revision_id`` of the exact revision approved.
        revision_content_hash: The approved revision's ``content_hash`` at grant
            time — the staleness anchor (AC-5-1).
        approver: Principal who granted the approval (the separation-of-duties and
            required-approvals gates read this — AC-3-3, AC-3-4).
        granted_at: UTC timestamp the approval was minted.
        valid_until: UTC expiry; at or after it the token is expired (AC-1-5).
        signature: HMAC-SHA256 hex over :func:`approval_signing_payload` (AC-1-3).
        invalidation_rules: Facet names whose change sunsets the approval (AC-5-2);
            empty means any change to the bound artifact invalidates it.
    """

    token_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identity of this approval")
    approved_transition_kind: str = Field(min_length=1, description="The transition kind this token approves")
    artifact_revision_id: str = Field(min_length=1, description="revision_id of the exact revision approved")
    revision_content_hash: str = Field(min_length=1, description="content_hash of the approved revision at grant time")
    approver: str = Field(min_length=1, description="Principal who granted the approval")
    granted_at: datetime = Field(default_factory=_utcnow, description="UTC timestamp the approval was minted")
    valid_until: datetime = Field(description="UTC expiry; at or after it the token is expired")
    signature: str = Field(min_length=1, description="HMAC-SHA256 hex over the canonical identity payload")
    invalidation_rules: list[str] = Field(
        default_factory=list,
        description="Facet names whose change sunsets the approval (empty = any change invalidates)",
    )

    @classmethod
    def mint(
        cls,
        *,
        approved_transition_kind: str,
        artifact_revision_id: str,
        revision_content_hash: str,
        approver: str,
        signing_key: str,
        valid_until: datetime | None = None,
        valid_for: timedelta | None = None,
        invalidation_rules: list[str] | None = None,
        token_id: str | None = None,
        granted_at: datetime | None = None,
    ) -> ApprovalToken:
        """Mint a signed :class:`ApprovalToken` over its canonical payload (AC-1-3).

        Computes the HMAC ``signature`` with ``signing_key`` so the token verifies
        against the same key later (AC-1-4). Supply the expiry either as an absolute
        ``valid_until`` or a relative ``valid_for`` (added to ``granted_at``); when
        neither is given the token is valid for 24 hours (AC-1-5).

        Args:
            approved_transition_kind: The transition kind approved.
            artifact_revision_id: The approved revision's id.
            revision_content_hash: The approved revision's content hash (AC-1-2).
            approver: The approving principal.
            signing_key: The configured signing key (from ``TeamsSettings``).
            valid_until: Absolute UTC expiry (wins over ``valid_for``).
            valid_for: Relative validity window from ``granted_at``.
            invalidation_rules: Facet names whose change sunsets the approval.
            token_id: Explicit id; defaults to a fresh UUID hex.
            granted_at: Explicit grant time; defaults to now.

        Returns:
            A signed, frozen :class:`ApprovalToken`.
        """
        resolved_id = token_id or uuid4().hex
        resolved_granted = granted_at or _utcnow()
        if valid_until is None:
            valid_until = resolved_granted + (valid_for if valid_for is not None else timedelta(hours=24))
        payload = approval_signing_payload(
            token_id=resolved_id,
            approved_transition_kind=approved_transition_kind,
            artifact_revision_id=artifact_revision_id,
            revision_content_hash=revision_content_hash,
            approver=approver,
            valid_until=valid_until,
        )
        return cls(
            token_id=resolved_id,
            approved_transition_kind=approved_transition_kind,
            artifact_revision_id=artifact_revision_id,
            revision_content_hash=revision_content_hash,
            approver=approver,
            granted_at=resolved_granted,
            valid_until=valid_until,
            signature=_sign(payload, signing_key),
            invalidation_rules=list(invalidation_rules or []),
        )

    def signing_payload(self) -> str:
        """Return this token's canonical signing payload (AC-1-3)."""
        return approval_signing_payload(
            token_id=self.token_id,
            approved_transition_kind=self.approved_transition_kind,
            artifact_revision_id=self.artifact_revision_id,
            revision_content_hash=self.revision_content_hash,
            approver=self.approver,
            valid_until=self.valid_until,
        )

    def signature_valid(self, signing_key: str) -> bool:
        """Return whether ``signature`` verifies against ``signing_key`` (AC-1-4).

        Uses :func:`hmac.compare_digest` for a constant-time comparison so a
        signature check cannot be timing-attacked. A token whose signature does not
        verify is treated as absent by the ``PolicyEngine``.
        """
        expected = _sign(self.signing_payload(), signing_key)
        return hmac.compare_digest(expected, self.signature)

    def is_expired(self, now: datetime | None = None) -> bool:
        """Return whether the token is at or past its ``valid_until`` (AC-1-5)."""
        return (now or _utcnow()) >= self.valid_until


class Decision(FrozenModel):
    """An auditable record of a consequential choice (ASTR §11).

    Captures the full deliberation: the ``question``, the ``options`` considered,
    what was ``selected``, the ``rationale``, the ``evidence`` (reusing the P1
    :class:`Evidence` vocabulary — file:line / test / command exit / artifact / url
    / log, AC-2-2), any ``dissent``, and the prior ``decision_id`` this record
    ``supersedes`` when it reverses or replaces an earlier decision (AC-2-3). The
    ``PolicyEngine`` appends one on every allow/deny verdict (AC-2-4).

    Attributes:
        decision_id: Unique identity of this decision (auto UUID hex if omitted).
        question: The question the decision answers.
        options: The options that were considered.
        selected: The option that was chosen.
        rationale: Why the selection was made.
        evidence: Evidence refs backing the decision (reuses the P1 type — AC-2-2).
        dissent: Recorded dissenting views (empty when unanimous).
        supersedes: Prior ``decision_id``s this decision reverses/replaces (AC-2-3).
        decided_at: UTC timestamp the decision was recorded.
    """

    decision_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identity of this decision")
    question: str = Field(min_length=1, description="The question the decision answers")
    options: list[str] = Field(default_factory=list, description="The options that were considered")
    selected: str = Field(default="", description="The option that was chosen")
    rationale: str = Field(default="", description="Why the selection was made")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence refs backing the decision")
    dissent: list[str] = Field(default_factory=list, description="Recorded dissenting views (empty = unanimous)")
    supersedes: list[str] = Field(default_factory=list, description="Prior decision_ids this decision replaces")
    decided_at: datetime = Field(default_factory=_utcnow, description="UTC timestamp the decision was recorded")


class TransitionRequest(FrozenModel):
    """A proposed consequential state transition the ``PolicyEngine`` judges (ASTR §14.7).

    The model constructs one of these and hands it to
    ``PolicyEngine.authorize_transition``; the engine — never the model — issues the
    final verdict (AC-6-1). A ``consequential`` transition (e.g. a release or a
    merge) is refused unless a valid revision-bound approval is present and no open
    blocking finding remains (AC-6-2, AC-6-3).

    Attributes:
        actor: The principal requesting the transition (the capability and
            separation-of-duties gates read this — AC-3-2, AC-3-3).
        kind: The transition kind (e.g. ``"release"``, ``"merge"``) — matched
            against approvals' ``approved_transition_kind`` and the human-gated set.
        target_revision_id: The ``revision_id`` the transition targets — the
            revision approvals must bind (AC-3-4) and findings are matched against.
        consequential: Whether this transition is consequential (release path) and
            thus subject to the structural approval + blocking-finding refusal
            (AC-6-2, AC-6-3).
        presented_tokens: The ``token_id``s of the approvals presented for this
            transition (the engine resolves them against the approval store).
        required_capability: The :class:`AuthorityGrant` capability this transition
            requires (defaults to ``may_approve``); the capability gate denies when
            the actor's grant lacks it (AC-3-2).
        target_artifact: Optional logical artifact name the transition targets, used
            to match findings that reference the artifact by name as well as by
            revision id (AC-3-5).
    """

    actor: str = Field(min_length=1, description="The principal requesting the transition")
    kind: str = Field(min_length=1, description="The transition kind (e.g. 'release', 'merge')")
    target_revision_id: str = Field(default="", description="The revision_id the transition targets")
    consequential: bool = Field(default=False, description="Whether this transition is on the release path")
    presented_tokens: list[str] = Field(
        default_factory=list,
        description="token_ids of the approvals presented for this transition",
    )
    required_capability: Literal["may_write", "may_approve", "may_create_blocking_finding"] = Field(
        default="may_approve",
        description="The AuthorityGrant capability this transition requires",
    )
    target_artifact: str = Field(default="", description="Optional logical artifact name the transition targets")


HumanDecisionStatus = Literal["pending", "resolved", "rejected"]
"""Lifecycle of a :class:`HumanDecision` gate.

- ``pending`` — awaiting a human; the runner suspends the transition (AC-4-3).
- ``resolved`` — approved by a human; the runner resumes and re-authorizes (AC-4-4).
- ``rejected`` — declined by a human; the runner abandons the transition (AC-4-6).
"""


class HumanDecision(FrozenModel):
    """A first-class human-gate protocol state (ASTR §14, §5.2).

    Where a transition kind is human-gated, the ``PolicyEngine`` emits one of these
    as ``pending`` rather than letting the model self-approve (AC-4-1). The runner
    persists it to disk and suspends the outer loop for that transition until a
    human resolves it (AC-4-3, AC-4-5); an approving resolution resumes and
    re-authorizes the transition (AC-4-4), a rejecting one abandons it (AC-4-6).

    Attributes:
        decision_id: Unique identity of this gate (auto UUID hex if omitted).
        transition: The :class:`TransitionRequest` this gate governs.
        prompt: The human-readable question posed to the human.
        options: The choices offered to the human.
        status: ``pending`` | ``resolved`` | ``rejected`` (AC-4-2).
        resolved_by: Principal who resolved the gate (empty while pending).
        resolved_at: UTC timestamp of resolution (``None`` while pending).
        created_at: UTC timestamp the gate was emitted.
    """

    decision_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identity of this gate")
    transition: TransitionRequest = Field(description="The transition request this gate governs")
    prompt: str = Field(min_length=1, description="The human-readable question posed to the human")
    options: list[str] = Field(default_factory=list, description="The choices offered to the human")
    status: HumanDecisionStatus = Field(default="pending", description="pending | resolved | rejected")
    resolved_by: str = Field(default="", description="Principal who resolved the gate (empty while pending)")
    resolved_at: datetime | None = Field(default=None, description="UTC timestamp of resolution (None while pending)")
    created_at: datetime = Field(default_factory=_utcnow, description="UTC timestamp the gate was emitted")

    def resolve(self, *, approved: bool, resolved_by: str, resolved_at: datetime | None = None) -> HumanDecision:
        """Return a resolved copy of this gate (frozen — never mutates in place).

        Args:
            approved: ``True`` -> ``resolved`` (approve); ``False`` -> ``rejected``.
            resolved_by: The principal resolving the gate.
            resolved_at: Resolution timestamp; defaults to now.

        Returns:
            A new :class:`HumanDecision` with the updated status and resolver.
        """
        return self.model_copy(
            update={
                "status": "resolved" if approved else "rejected",
                "resolved_by": resolved_by,
                "resolved_at": resolved_at or _utcnow(),
            },
        )


GateName = Literal[
    "capability",
    "separation_of_duties",
    "required_approvals",
    "open_blocking_finding",
    "human_required",
]
"""The ordered gate stack the ``PolicyEngine`` runs; the name of the gate that
failed is reported on a denying :class:`PolicyVerdict` (AC-3-1)."""


class PolicyVerdict(FrozenModel):
    """The ``PolicyEngine``'s final allow/deny on a transition (ASTR §14.7, AC-3-1).

    The model never decides — this verdict, produced by
    ``PolicyEngine.authorize_transition``, is the authority (AC-3-1). On a deny,
    ``failed_gate`` names which gate in the ordered stack refused and ``reason`` is
    human-readable. On an allow, ``failed_gate`` is ``None`` (AC-3-6). When the
    denying gate is ``human_required``, ``pending_human_decision`` carries the gate
    the runner must suspend on (AC-4-1).

    Attributes:
        allowed: Whether the transition is authorized.
        failed_gate: The gate that refused (``None`` when ``allowed``).
        reason: Human-readable explanation of the verdict.
        pending_human_decision: The pending gate to suspend on, set only when
            ``failed_gate == "human_required"`` (AC-4-1).
    """

    allowed: bool = Field(description="Whether the transition is authorized")
    failed_gate: GateName | None = Field(default=None, description="The gate that refused (None when allowed)")
    reason: str = Field(default="", description="Human-readable explanation of the verdict")
    pending_human_decision: HumanDecision | None = Field(
        default=None,
        description="The pending gate to suspend on (set only when failed_gate == 'human_required')",
    )
