"""Facet-level review reconciliation + the ``promote_artifact`` CAS tool (spec 005).

This module sits on top of the immutable, content-addressed
:class:`~strands_teams.models.ArtifactRevision` store owned by
:class:`~strands_teams.layout.TeamsLayout`. It ships three things (ASTR §4.8,
§6.4, §12.6):

- :func:`semantic_diff` — a facet-level diff of two revisions returning the set
  of facets whose content changed (AC-3-2).
- :func:`invalidates_review` / :func:`reconcile_reviews` — the selective
  invalidation rule: a bound review is invalidated ONLY when the facets it covers
  intersect the facets ``semantic_diff`` reports as changed; otherwise it is
  carried forward and reused rather than recomputed (AC-3-3, AC-3-4, AC-3-6). A
  ``new_revision`` whose lineage does not supersede the review's bound revision is
  a lineage error, not a silent verdict (AC-3-5).
- :func:`promote_artifact` — the ``@tool`` wrapping the layout's optimistic
  compare-and-set, returning a :class:`~strands_teams.models.PromotionResult` JSON
  string (AC-2-2..AC-2-6). A stale CAS surfaces as a structured error result
  rather than an exception crossing the tool boundary.

A **review** here is anything carrying a revision binding and covered facets:
either the Phase 1 :class:`~strands_teams.models.Finding` (extended in this phase
with ``reviewed_revision`` / ``reviewed_facets``) or the lightweight
:class:`Review` model. Both are consumed structurally via :func:`_review_binding`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

from loguru import logger
from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from strands import ToolContext, tool

from .models import (
    NO_CURRENT_REVISION,
    ArtifactRevision,
    FrozenModel,
    PromotionResult,
    StaleArtifactError,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .layout import TeamsLayout


# ---------------------------------------------------------------------------
# Review binding + verdict types
# ---------------------------------------------------------------------------


@runtime_checkable
class ReviewBinding(Protocol):
    """Structural interface for a revision-bound review (AC-3-1).

    Any object carrying ``reviewed_revision`` (the ``revision_id`` the review was
    produced against) and ``reviewed_facets`` (the facets it covers) satisfies
    this — including the Phase 1 :class:`~strands_teams.models.Finding` and the
    lightweight :class:`Review` model. A ``runtime_checkable`` Protocol checks
    attribute presence only.
    """

    reviewed_revision: str
    reviewed_facets: list[str]


class Review(FrozenModel):
    """A lightweight revision-bound review (AC-3-1).

    A minimal record for callers that want to bind a review to an artifact
    revision without producing a full :class:`~strands_teams.models.Finding`.
    Immutable; ``reviewed_revision`` is the ``revision_id`` the review was produced
    against and ``reviewed_facets`` are the facets it covers (empty = the whole
    artifact).

    Attributes:
        review_id: Identifier of the review.
        reviewed_revision: The ``revision_id`` this review was produced against.
        reviewed_facets: The artifact facets this review covers (empty = whole
            artifact — any changed facet invalidates it).
        summary: Optional human-readable summary of the review.
    """

    review_id: str = Field(min_length=1, description="Identifier of the review")
    reviewed_revision: str = Field(min_length=1, description="revision_id this review was produced against")
    reviewed_facets: list[str] = Field(
        default_factory=list,
        description="Facets this review covers (empty = whole artifact)",
    )
    summary: str = Field(default="", description="Optional human-readable summary of the review")


ReviewStatus = Literal["valid", "invalidated", "error"]
"""Outcome of evaluating a bound review against a new revision.

- ``valid`` — the changed facets do not intersect the review's covered facets;
  the review is carried forward and reused (AC-3-4).
- ``invalidated`` — the changed facets intersect the covered facets; the review
  is re-opened (AC-3-3).
- ``error`` — the ``new_revision`` lineage does not supersede the review's bound
  revision, so no reuse/invalidation verdict can be trusted (AC-3-5).
"""


class ReviewVerdict(FrozenModel):
    """The structured outcome of :func:`invalidates_review` (AC-3-3, AC-3-4, AC-3-5).

    Attributes:
        status: ``valid`` (reuse), ``invalidated`` (re-open), or ``error``
            (lineage mismatch).
        reason: Human-readable explanation of the verdict.
        changed_facets: Facets ``semantic_diff`` reported as changed between the
            bound revision and the new revision.
        intersecting_facets: The changed facets that the review covers (the
            invalidation trigger; empty when ``valid``).
    """

    status: ReviewStatus = Field(description="valid (reuse), invalidated (re-open), or error (lineage mismatch)")
    reason: str = Field(description="Human-readable explanation of the verdict")
    changed_facets: list[str] = Field(default_factory=list, description="Facets semantic_diff reported as changed")
    intersecting_facets: list[str] = Field(
        default_factory=list,
        description="Changed facets the review covers (the invalidation trigger)",
    )


class ReviewReconciliation(FrozenModel):
    """Result of reconciling a set of bound reviews against a promoted revision (AC-3-6).

    Attributes:
        carried_forward: Reviews :func:`invalidates_review` reports as still valid
            — reused rather than recomputed.
        reopened: Reviews reported as invalidated — must be recomputed against the
            new revision.
        errored: Reviews whose bound revision is not in the new revision's lineage
            (AC-3-5) — surfaced, never silently dropped.
    """

    carried_forward: list[ReviewVerdict] = Field(default_factory=list, description="Still-valid reviews (reused)")
    reopened: list[ReviewVerdict] = Field(default_factory=list, description="Invalidated reviews (recomputed)")
    errored: list[ReviewVerdict] = Field(default_factory=list, description="Lineage-mismatch reviews (AC-3-5)")


# ---------------------------------------------------------------------------
# Facet diff + selective invalidation
# ---------------------------------------------------------------------------


def semantic_diff(old_revision: ArtifactRevision, new_revision: ArtifactRevision) -> set[str]:
    """Return the set of artifact facets whose content changed between two revisions (AC-3-2).

    Compares the two revisions' per-facet content hashes
    (:attr:`~strands_teams.models.ArtifactRevision.facets`). A facet is *changed*
    when its hash differs between the revisions, when it exists in only one of them
    (added or removed), and unchanged when both hashes are equal. The comparison is
    over hashes, not raw content, so it is exact and cheap.

    Args:
        old_revision: The earlier revision.
        new_revision: The later revision.

    Returns:
        The set of changed facet names. Empty when the two revisions carry
        identical facet content.
    """
    old_facets = old_revision.facets
    new_facets = new_revision.facets
    changed: set[str] = set()
    for name in old_facets.keys() | new_facets.keys():
        if old_facets.get(name) != new_facets.get(name):
            changed.add(name)
    return changed


def _review_binding(review: ReviewBinding | Any) -> tuple[str, list[str]]:
    """Extract ``(bound_revision_id, covered_facets)`` from a review (AC-3-1).

    Works structurally over any object carrying ``reviewed_revision`` and
    ``reviewed_facets`` — the Phase 1 :class:`~strands_teams.models.Finding` and
    the :class:`Review` model both qualify.
    """
    bound = str(getattr(review, "reviewed_revision", "") or "")
    facets = list(getattr(review, "reviewed_facets", []) or [])
    return bound, facets


def _lineage_supersedes(old_binding: ArtifactRevision, new_revision: ArtifactRevision, bound_revision: str) -> bool:
    """Return whether ``new_revision`` supersedes the review's bound revision (AC-3-5).

    Two conditions must hold:

    1. The review's ``bound_revision`` must identify ``old_binding`` — if the
       review names a revision other than ``old_binding``, the caller paired the
       wrong ``old_binding`` and no verdict can be trusted. An empty
       ``bound_revision`` (an unbound Phase 1 finding) is permitted and treated as
       implicitly bound to ``old_binding``.
    2. ``new_revision`` must actually descend from ``old_binding`` — either it IS
       ``old_binding`` (a degenerate in-place re-evaluation) or ``old_binding`` is
       in its ``supersedes`` / ``parents`` lineage.
    """
    if bound_revision and bound_revision != old_binding.revision_id:
        return False
    if new_revision.revision_id == old_binding.revision_id:
        return True
    return old_binding.revision_id in new_revision.supersedes or old_binding.revision_id in new_revision.parents


def invalidates_review(
    old_binding: ArtifactRevision,
    new_revision: ArtifactRevision,
    review: ReviewBinding | Any,
) -> ReviewVerdict:
    """Decide whether ``new_revision`` invalidates a review bound to ``old_binding`` (AC-3-3..5).

    Runs :func:`semantic_diff` between the revisions and intersects the changed
    facets with the facets the review covers:

    - **invalidated** when the intersection is non-empty — a facet the review
      examined changed, so the review must be re-opened (AC-3-3).
    - **valid** when the intersection is empty — the review's facets are untouched,
      so it is carried forward and reused rather than recomputed (AC-3-4). A review
      that covers no specific facets (empty ``reviewed_facets``) is treated as
      covering the WHOLE artifact, so any changed facet invalidates it.
    - **error** when ``new_revision``'s lineage does not supersede the review's
      bound revision — the verdict cannot be trusted, so it is reported as an error
      rather than silently treated as valid or invalidated (AC-3-5).

    Args:
        old_binding: The revision the review was produced against.
        new_revision: The candidate new revision.
        review: A revision-bound review — a :class:`Review` or a Phase 1
            :class:`~strands_teams.models.Finding` (consumed structurally).

    Returns:
        A :class:`ReviewVerdict` with the status, reason, and the changed /
        intersecting facet sets.
    """
    bound_revision, covered = _review_binding(review)

    if not _lineage_supersedes(old_binding, new_revision, bound_revision):
        return ReviewVerdict(
            status="error",
            reason=(
                f"new_revision {new_revision.revision_id!r} does not supersede the review's bound revision "
                f"{bound_revision or old_binding.revision_id!r} (lineage: supersedes="
                f"{new_revision.supersedes}, parents={new_revision.parents})"
            ),
        )

    changed = semantic_diff(old_binding, new_revision)
    # Empty covered-facet set means the review covers the whole artifact: any change touches it.
    intersecting = sorted(changed) if not covered else sorted(changed & set(covered))

    if intersecting:
        return ReviewVerdict(
            status="invalidated",
            reason=f"changed facets {intersecting} intersect the review's covered facets",
            changed_facets=sorted(changed),
            intersecting_facets=intersecting,
        )
    return ReviewVerdict(
        status="valid",
        reason="no changed facet intersects the review's covered facets — reuse the review",
        changed_facets=sorted(changed),
        intersecting_facets=[],
    )


def reconcile_reviews(
    old_binding: ArtifactRevision,
    new_revision: ArtifactRevision,
    reviews: Iterable[ReviewBinding | Any],
) -> ReviewReconciliation:
    """Partition bound reviews into carried-forward, re-opened, and errored (AC-3-6).

    When a re-run promotes a new revision of a logical artifact, every bound review
    that :func:`invalidates_review` reports as still valid is carried forward
    (reused), only the invalidated reviews are re-opened, and any lineage-mismatch
    review is surfaced as an error (AC-3-5, AC-3-6).

    Args:
        old_binding: The revision the reviews were produced against.
        new_revision: The newly promoted revision.
        reviews: The bound reviews to reconcile.

    Returns:
        A :class:`ReviewReconciliation` grouping the verdicts.
    """
    carried: list[ReviewVerdict] = []
    reopened: list[ReviewVerdict] = []
    errored: list[ReviewVerdict] = []
    for review in reviews:
        verdict = invalidates_review(old_binding, new_revision, review)
        if verdict.status == "valid":
            carried.append(verdict)
        elif verdict.status == "invalidated":
            reopened.append(verdict)
        else:
            errored.append(verdict)
    return ReviewReconciliation(carried_forward=carried, reopened=reopened, errored=errored)


# ---------------------------------------------------------------------------
# promote_artifact tool
# ---------------------------------------------------------------------------


def _layout_of(tool_context: ToolContext) -> TeamsLayout:
    """Return the run's :class:`TeamsLayout` from ``invocation_state`` (AC-2-1).

    Raises:
        RuntimeError: If the coordinator was invoked without a ``layout`` key — a
            wiring bug, not a runtime condition. Mirrors ``teams._require_state``.
    """
    state: dict[str, Any] = tool_context.invocation_state or {}
    layout = state.get("layout")
    if layout is None:
        msg = "invocation_state missing 'layout' — coordinator must be invoked with a TeamInvocationState"
        raise RuntimeError(msg)
    return cast("TeamsLayout", layout)


def _coerce_proposed_revision(
    logical_name: str,
    proposed_revision: dict[str, Any] | ArtifactRevision,
) -> ArtifactRevision:
    """Build the proposed :class:`ArtifactRevision` from a tool argument (AC-2-6).

    The coordinator LLM supplies ``proposed_revision`` as a plain JSON dict of
    content — typically ``{"facets": {...}}`` or ``{"body": "..."}`` — and this
    routes it through :meth:`ArtifactRevision.from_content` so ``content_hash`` and
    ``revision_id`` are computed deterministically from the content (never trusted
    from the LLM). ``logical_name`` from the tool argument always wins. A
    programmatic caller may pass an :class:`ArtifactRevision` directly.
    """
    if isinstance(proposed_revision, ArtifactRevision):
        return proposed_revision
    payload = dict(proposed_revision)
    payload.pop("logical_name", None)
    # content_hash / revision_id are derived, never taken from the caller.
    payload.pop("content_hash", None)
    payload.pop("revision_id", None)
    return ArtifactRevision.from_content(logical_name=logical_name, **payload)


@tool(context=True)
def promote_artifact(
    logical_name: str,
    expected_current_revision: str,
    proposed_revision: dict[str, Any],
    tool_context: ToolContext,
) -> str:
    """Promote a new revision of a logical artifact by optimistic compare-and-set.

    Makes ``proposed_revision`` the current revision of ``logical_name`` ONLY when
    ``expected_current_revision`` still matches the alias's stored current revision
    — a durable optimistic lock. If another promotion changed the current revision
    since you read it, this returns a ``status="stale"`` error and leaves the alias
    unchanged; re-read the current revision and retry. Pass the sentinel ``"∅"`` as
    ``expected_current_revision`` for the FIRST promotion of a logical name.

    The proposed revision's content is content-addressed: supply its content as
    ``proposed_revision={"facets": {"<facet>": "<content>", ...}}`` (preferred, so
    facet-level review invalidation works) or ``{"body": "<content>"}``; the
    revision id and content hash are computed from that content. On success the
    prior current revision is recorded in the new revision's ``supersedes`` lineage.

    Args:
        logical_name: The logical artifact to promote (e.g. ``"BUNDLE.auth"``).
        expected_current_revision: The revision id you believe is current, or the
            sentinel ``"∅"`` for the first promotion.
        proposed_revision: The new revision's content as a JSON dict — ``facets``
            (a ``{name: content}`` map) and/or ``body`` (a single string), plus
            optional ``artifact_type``, ``produced_by``, ``parents``,
            ``classification``.
        tool_context: Framework-injected run context (invocation state).

    Returns:
        On success, a JSON object with ``logical_name``, the new current
        ``current_revision`` id, and the ``superseded_revision`` id (the sentinel
        when this was the first revision). On a failed compare-and-set,
        ``{"status": "stale", "message": ..., "expected": ..., "actual": ...}`` so
        you can re-read and retry.
    """
    layout = _layout_of(tool_context)
    try:
        proposed = _coerce_proposed_revision(logical_name, proposed_revision)
    except (PydanticValidationError, ValueError, TypeError) as e:
        return json.dumps({"status": "error", "message": f"invalid proposed_revision: {e}"})

    try:
        stored = layout.promote_revision(logical_name, expected_current_revision, proposed)
    except StaleArtifactError as e:
        logger.info(f"promote_artifact({logical_name}): stale CAS — expected {e.expected!r}, actual {e.actual!r}")
        return json.dumps(
            {
                "status": "stale",
                "message": str(e),
                "logical_name": logical_name,
                "expected": e.expected,
                "actual": e.actual,
            },
        )
    except OSError as e:
        return json.dumps({"status": "error", "message": f"promotion failed: {e}"})

    superseded = expected_current_revision if expected_current_revision != NO_CURRENT_REVISION else NO_CURRENT_REVISION
    result = PromotionResult(
        logical_name=logical_name,
        current_revision=stored.revision_id,
        superseded_revision=superseded,
    )
    return result.model_dump_json()
