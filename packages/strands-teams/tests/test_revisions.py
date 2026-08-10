"""Tests for Phase P4-revisions — artifact revisions + stale-review invalidation (spec 005).

Covers immutable content-addressed revisions (US1), optimistic alias promotion
compare-and-set + StaleArtifactError (US2), and facet-level selective review
invalidation (US3). No real Bedrock/network: the promote_artifact tool is
exercised directly via its ``__wrapped__`` with a hand-built ToolContext, and all
persistence is to a tmp_path layout.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError
from strands import ToolContext

import strands_teams
from strands_teams import (
    NO_CURRENT_REVISION,
    ArtifactRevision,
    Finding,
    PromotionResult,
    Review,
    RevisionConflictError,
    StaleArtifactError,
    compute_content_hash,
    invalidates_review,
    reconcile_reviews,
    semantic_diff,
)
from strands_teams import revisions as revisions_mod
from strands_teams.layout import TeamsLayout

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def layout(tmp_path: Path) -> TeamsLayout:
    return TeamsLayout(output_root=tmp_path / ".teams")


def _make_context(layout: TeamsLayout) -> ToolContext:
    return ToolContext(
        tool_use={"toolUseId": "t", "name": "promote_artifact", "input": {}},
        agent=None,
        invocation_state={"layout": layout},
    )


def _promote(ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
    return json.loads(revisions_mod.promote_artifact.__wrapped__(tool_context=ctx, **kwargs))


def _first(ctx: ToolContext, logical_name: str, body: str) -> dict[str, Any]:
    return _promote(
        ctx,
        logical_name=logical_name,
        expected_current_revision=NO_CURRENT_REVISION,
        proposed_revision={"body": body},
    )


# ---------------------------------------------------------------------------
# US1 — Immutable, content-addressed revisions
# ---------------------------------------------------------------------------
def test_revision_is_frozen() -> None:  # AC-1-1
    rev = ArtifactRevision.from_content(logical_name="BUNDLE.auth", body="hello")
    with pytest.raises(ValidationError):
        rev.revision_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        rev.content_hash = "x"  # type: ignore[misc]


def test_revision_has_all_fields() -> None:  # AC-1-1
    rev = ArtifactRevision.from_content(
        logical_name="BUNDLE.auth",
        facets={"overview": "o", "risks": "r"},
        artifact_type="bundle",
        produced_by="team-auth",
        parents=["p1"],
        classification="internal",
    )
    for field in (
        "artifact_id",
        "revision_id",
        "content_hash",
        "artifact_type",
        "logical_name",
        "produced_by",
        "parents",
        "supersedes",
        "classification",
        "created_at",
    ):
        assert hasattr(rev, field)
    assert rev.artifact_id == "BUNDLE.auth"  # defaults to logical_name
    assert rev.revision_id == rev.content_hash  # content-addressed
    assert rev.produced_by == "team-auth"
    assert rev.parents == ["p1"]


def test_content_hash_deterministic_and_content_sensitive() -> None:  # AC-1-2
    a = ArtifactRevision.from_content(logical_name="x", facets={"s": "same"})
    b = ArtifactRevision.from_content(logical_name="x", facets={"s": "same"})
    c = ArtifactRevision.from_content(logical_name="x", facets={"s": "different"})
    # Byte-identical content -> equal hash; producer/timestamp do not affect it.
    assert a.content_hash == b.content_hash
    d = ArtifactRevision.from_content(logical_name="x", facets={"s": "same"}, produced_by="someone-else")
    assert a.content_hash == d.content_hash
    # Differing content -> differing hash.
    assert a.content_hash != c.content_hash


def test_content_hash_order_independent() -> None:  # AC-1-2
    assert compute_content_hash({"a": "1", "b": "2"}) == compute_content_hash({"b": "2", "a": "1"})


def test_persist_and_load_roundtrip(layout: TeamsLayout) -> None:  # AC-1-4
    rev = ArtifactRevision.from_content(logical_name="BUNDLE.auth", body="content")
    stored = layout.persist_revision(rev)
    assert stored == rev
    loaded = layout.load_revision("BUNDLE.auth", rev.revision_id)
    assert loaded == rev
    assert layout.revision_path("BUNDLE.auth", rev.revision_id).exists()


def test_persist_is_idempotent_for_identical_content(layout: TeamsLayout) -> None:  # AC-1-5
    rev = ArtifactRevision.from_content(logical_name="a", body="same")
    layout.persist_revision(rev)
    # Re-persisting identical content is a no-op, not a conflict.
    again = layout.persist_revision(rev)
    assert again.content_hash == rev.content_hash


def test_persist_rejects_conflicting_content_same_id(layout: TeamsLayout) -> None:  # AC-1-5
    rev = ArtifactRevision.from_content(logical_name="a", body="original")
    layout.persist_revision(rev)
    # Forge a revision reusing the same revision_id but with different content.
    forged = rev.model_copy(update={"content_hash": "deadbeef", "facets": {}})
    with pytest.raises(RevisionConflictError):
        layout.persist_revision(forged)
    # The stored revision is left unchanged.
    reloaded = layout.load_revision("a", rev.revision_id)
    assert reloaded is not None
    assert reloaded.content_hash == rev.content_hash


def test_exports_present() -> None:  # AC-1-6
    for name in ("ArtifactRevision", "StaleArtifactError", "promote_artifact"):
        assert name in strands_teams.__all__
        assert hasattr(strands_teams, name)


# ---------------------------------------------------------------------------
# US2 — Optimistic alias promotion (compare-and-set)
# ---------------------------------------------------------------------------
def test_first_promotion_with_sentinel(layout: TeamsLayout) -> None:  # AC-2-4
    ctx = _make_context(layout)
    result = _promote(
        ctx,
        logical_name="BUNDLE.auth",
        expected_current_revision=NO_CURRENT_REVISION,
        proposed_revision={"body": "v1"},
    )
    v1 = ArtifactRevision.from_content(logical_name="BUNDLE.auth", body="v1")
    assert result["current_revision"] == v1.revision_id
    assert result["superseded_revision"] == NO_CURRENT_REVISION
    assert layout.current_revision_id("BUNDLE.auth") == v1.revision_id


def test_matching_cas_promotes_and_records_supersedes(layout: TeamsLayout) -> None:  # AC-2-2
    ctx = _make_context(layout)
    r1 = _first(ctx, "a", "v1")
    rev1_id = r1["current_revision"]
    r2 = _promote(ctx, logical_name="a", expected_current_revision=rev1_id, proposed_revision={"body": "v2"})
    assert r2["superseded_revision"] == rev1_id
    assert layout.current_revision_id("a") == r2["current_revision"]
    # The prior revision is recorded in the promoted revision's supersedes lineage.
    promoted = layout.load_revision("a", r2["current_revision"])
    assert promoted is not None
    assert rev1_id in promoted.supersedes


def test_stale_cas_returns_stale_and_leaves_alias(layout: TeamsLayout) -> None:  # AC-2-3
    ctx = _make_context(layout)
    r1 = _first(ctx, "a", "v1")
    current = r1["current_revision"]
    # Promote with a wrong expected revision -> stale, alias unchanged.
    stale = _promote(ctx, logical_name="a", expected_current_revision="wrong-rev", proposed_revision={"body": "v2"})
    assert stale["status"] == "stale"
    assert stale["expected"] == "wrong-rev"
    assert stale["actual"] == current
    assert layout.current_revision_id("a") == current


def test_promote_revision_raises_stale_at_layout_level(layout: TeamsLayout) -> None:  # AC-2-3
    r1 = ArtifactRevision.from_content(logical_name="a", body="v1")
    layout.promote_revision("a", NO_CURRENT_REVISION, r1)
    r2 = ArtifactRevision.from_content(logical_name="a", body="v2")
    with pytest.raises(StaleArtifactError):
        layout.promote_revision("a", "not-current", r2)


def test_concurrent_same_turn_promotions_one_wins(layout: TeamsLayout) -> None:  # AC-2-5
    # Two promotions of the same logical_name with the same expected_current_revision;
    # the durable CAS admits exactly one and raises StaleArtifactError for the other.
    layout.alias_path().parent.mkdir(parents=True, exist_ok=True)

    def promote(body: str) -> str:
        rev = ArtifactRevision.from_content(logical_name="a", body=body, produced_by=body)
        try:
            stored = layout.promote_revision("a", NO_CURRENT_REVISION, rev)
        except StaleArtifactError:
            return "stale"
        return stored.revision_id

    with ThreadPoolExecutor(max_workers=2) as ex:
        outcomes = list(ex.map(promote, ["A", "B"]))

    stale_count = outcomes.count("stale")
    won = [o for o in outcomes if o != "stale"]
    assert stale_count == 1, outcomes
    assert len(won) == 1
    assert layout.current_revision_id("a") == won[0]


def test_promotion_result_shape(layout: TeamsLayout) -> None:  # AC-2-6
    ctx = _make_context(layout)
    raw = revisions_mod.promote_artifact.__wrapped__(
        logical_name="a",
        expected_current_revision=NO_CURRENT_REVISION,
        proposed_revision={"body": "v1"},
        tool_context=ctx,
    )
    parsed = PromotionResult.model_validate_json(raw)
    assert parsed.logical_name == "a"
    assert parsed.current_revision
    assert parsed.superseded_revision == NO_CURRENT_REVISION


# ---------------------------------------------------------------------------
# US3 — Revision-bound reviews + facet-level selective invalidation
# ---------------------------------------------------------------------------
def _rev(logical_name: str, facets: dict[str, str], **kwargs: Any) -> ArtifactRevision:
    return ArtifactRevision.from_content(logical_name=logical_name, facets=facets, **kwargs)


def test_finding_carries_revision_binding() -> None:  # AC-3-1
    f = Finding(statement="x", severity="low", reviewed_revision="rev-1", reviewed_facets=["security"])
    assert f.reviewed_revision == "rev-1"
    assert f.reviewed_facets == ["security"]
    # Binding is additive: default is unbound.
    assert Finding(statement="y", severity="info").reviewed_revision == ""


def test_semantic_diff_returns_changed_facets() -> None:  # AC-3-2
    old = _rev("a", {"overview": "o", "security": "s1", "perf": "p"})
    new = _rev("a", {"overview": "o", "security": "s2", "deps": "d"})
    changed = semantic_diff(old, new)
    # security changed, perf removed, deps added; overview unchanged.
    assert changed == {"security", "perf", "deps"}


def test_semantic_diff_empty_when_identical() -> None:  # AC-3-2
    old = _rev("a", {"x": "1"})
    new = _rev("a", {"x": "1"}, produced_by="different-team")
    assert semantic_diff(old, new) == set()


def test_invalidates_review_when_covered_facet_changed() -> None:  # AC-3-3
    old = _rev("a", {"security": "s1", "perf": "p"})
    new = _rev("a", {"security": "s2", "perf": "p"}, supersedes=[old.revision_id])
    review = Review(review_id="r1", reviewed_revision=old.revision_id, reviewed_facets=["security"])
    verdict = invalidates_review(old, new, review)
    assert verdict.status == "invalidated"
    assert verdict.intersecting_facets == ["security"]


def test_review_valid_when_facets_disjoint() -> None:  # AC-3-4
    old = _rev("a", {"security": "s1", "perf": "p1"})
    new = _rev("a", {"security": "s1", "perf": "p2"}, supersedes=[old.revision_id])
    review = Review(review_id="r1", reviewed_revision=old.revision_id, reviewed_facets=["security"])
    verdict = invalidates_review(old, new, review)
    assert verdict.status == "valid"
    assert verdict.intersecting_facets == []


def test_whole_artifact_review_invalidated_by_any_change() -> None:  # AC-3-3
    old = _rev("a", {"security": "s1", "perf": "p1"})
    new = _rev("a", {"security": "s1", "perf": "p2"}, supersedes=[old.revision_id])
    # Empty reviewed_facets => whole-artifact review => any change invalidates.
    review = Review(review_id="r1", reviewed_revision=old.revision_id, reviewed_facets=[])
    assert invalidates_review(old, new, review).status == "invalidated"


def test_lineage_mismatch_is_error() -> None:  # AC-3-5
    old = _rev("a", {"security": "s1"})
    # new_revision does not record old in its lineage.
    unrelated = _rev("a", {"security": "s2"})
    review = Review(review_id="r1", reviewed_revision=old.revision_id, reviewed_facets=["security"])
    verdict = invalidates_review(old, unrelated, review)
    assert verdict.status == "error"


def test_lineage_error_when_bound_revision_names_other() -> None:  # AC-3-5
    old = _rev("a", {"security": "s1"})
    new = _rev("a", {"security": "s2"}, supersedes=[old.revision_id])
    # Review is bound to some OTHER revision, not old_binding.
    review = Review(review_id="r1", reviewed_revision="some-other-rev", reviewed_facets=["security"])
    assert invalidates_review(old, new, review).status == "error"


def test_reconcile_reviews_partitions(  # AC-3-6
) -> None:
    old = _rev("a", {"security": "s1", "perf": "p1", "docs": "d1"})
    new = _rev("a", {"security": "s2", "perf": "p1", "docs": "d1"}, supersedes=[old.revision_id])
    reuse = Review(review_id="perf", reviewed_revision=old.revision_id, reviewed_facets=["perf"])
    reopen = Review(review_id="sec", reviewed_revision=old.revision_id, reviewed_facets=["security"])
    bad = Review(review_id="bad", reviewed_revision="elsewhere", reviewed_facets=["docs"])

    result = reconcile_reviews(old, new, [reuse, reopen, bad])

    assert len(result.carried_forward) == 1
    assert len(result.reopened) == 1
    assert len(result.errored) == 1


def test_reconcile_carries_forward_finding() -> None:  # AC-3-1, AC-3-6
    old = _rev("a", {"security": "s1", "perf": "p1"})
    new = _rev("a", {"security": "s2", "perf": "p1"}, supersedes=[old.revision_id])
    # A Phase 1 Finding with a revision binding participates structurally.
    finding = Finding(
        statement="perf is fine",
        severity="info",
        reviewed_revision=old.revision_id,
        reviewed_facets=["perf"],
    )
    result = reconcile_reviews(old, new, [finding])
    assert len(result.carried_forward) == 1
    assert result.reopened == []
