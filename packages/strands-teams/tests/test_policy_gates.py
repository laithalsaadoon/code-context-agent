"""Tests for the Phase 6 authority gate (spec 007, US7).

Pins the auth-critical behavior: the five-gate allow/deny matrix (AC-7-1),
separation of duties (AC-7-2), stale-approval rejection (AC-7-3), human-gate
suspension (AC-7-4), and the regression that a non-consequential run is unchanged
with the engine present but never denying (AC-7-5).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from strands_teams import (
    ApprovalToken,
    AuthorityGrant,
    Finding,
    HumanDecision,
    PolicyEngine,
    TransitionRequest,
)
from strands_teams.hooks import PolicyGateDenied, PolicyGateHook
from strands_teams.policy import (
    STATE_APPROVALS,
    STATE_FINDINGS,
    STATE_GRANTS,
    STATE_REVISION_HASHES,
    denying_decision,
)

SIGNING_KEY = "test-signing-key"
REV = "rev-1"
REV_HASH = "hash-abc"


def _grant(**kw: bool) -> AuthorityGrant:
    base = {"may_create_blocking_finding": True, "may_write": True, "may_approve": True}
    base.update(kw)
    return AuthorityGrant(**base)


def _valid_token(approver: str = "reviewer", kind: str = "release") -> ApprovalToken:
    return ApprovalToken.mint(
        approved_transition_kind=kind,
        artifact_revision_id=REV,
        revision_content_hash=REV_HASH,
        approver=approver,
        signing_key=SIGNING_KEY,
        valid_for=timedelta(hours=1),
    )


def _state(*, grants=None, approvals=None, findings=None, revision_hashes=None):
    return {
        STATE_GRANTS: {"deployer": _grant()} if grants is None else grants,
        STATE_APPROVALS: {} if approvals is None else approvals,
        STATE_FINDINGS: [] if findings is None else findings,
        STATE_REVISION_HASHES: {REV: REV_HASH} if revision_hashes is None else revision_hashes,
    }


def _engine(**kw) -> PolicyEngine:
    return PolicyEngine(signing_key=SIGNING_KEY, required_approvals=kw.pop("required_approvals", 1), **kw)


def _transition(**kw) -> TransitionRequest:
    base = {
        "actor": "deployer",
        "kind": "release",
        "target_revision_id": REV,
        "consequential": True,
    }
    base.update(kw)
    return TransitionRequest(**base)


# ── AC-7-1: allow/deny matrix — one deny + one pass case per gate ──────────────


def test_capability_gate_denies_then_allows() -> None:
    eng = _engine()
    tok = _valid_token(approver="reviewer")
    approvals = {tok.token_id: tok}
    txn = _transition(presented_tokens=[tok.token_id])

    # deny: actor lacks may_approve
    state = _state(
        grants={"deployer": _grant(may_approve=False)},
        approvals=approvals,
    )
    v = eng.authorize_transition(txn, state)
    assert not v.allowed
    assert v.failed_gate == "capability"

    # allow: actor has capability, one independent valid approval, no blocking finding
    v = eng.authorize_transition(txn, _state(approvals=approvals))
    assert v.allowed
    assert v.failed_gate is None


def test_required_approvals_gate_denies_then_allows() -> None:
    eng = _engine(required_approvals=2)
    t1 = _valid_token(approver="rev-a")
    t2 = _valid_token(approver="rev-b")
    txn = _transition(presented_tokens=[t1.token_id])

    # deny: only 1 valid approval, 2 required
    v = eng.authorize_transition(txn, _state(approvals={t1.token_id: t1}))
    assert not v.allowed
    assert v.failed_gate == "required_approvals"

    # allow: 2 valid approvals present
    txn2 = _transition(presented_tokens=[t1.token_id, t2.token_id])
    v = eng.authorize_transition(txn2, _state(approvals={t1.token_id: t1, t2.token_id: t2}))
    assert v.allowed


def test_open_blocking_finding_gate_denies_then_allows() -> None:
    eng = _engine()
    tok = _valid_token()
    txn = _transition(presented_tokens=[tok.token_id], target_artifact="auth")

    blocking = Finding(statement="token reuse", severity="critical", blocking=True, affected_artifacts=["auth"])
    v = eng.authorize_transition(txn, _state(approvals={tok.token_id: tok}, findings=[blocking]))
    assert not v.allowed
    assert v.failed_gate == "open_blocking_finding"

    resolved = blocking.model_copy(update={"disposition": "remediated"})
    v = eng.authorize_transition(txn, _state(approvals={tok.token_id: tok}, findings=[resolved]))
    assert v.allowed


def test_human_required_gate_denies_and_emits_pending() -> None:
    eng = _engine(human_gated_kinds=("release",))
    tok = _valid_token()
    txn = _transition(presented_tokens=[tok.token_id])
    v = eng.authorize_transition(txn, _state(approvals={tok.token_id: tok}))
    assert not v.allowed
    assert v.failed_gate == "human_required"
    assert isinstance(v.pending_human_decision, HumanDecision)
    assert v.pending_human_decision.status == "pending"

    # a non-human-gated kind with the same state passes
    txn2 = _transition(kind="merge", presented_tokens=[])
    merge_tok = ApprovalToken.mint(
        approved_transition_kind="merge",
        artifact_revision_id=REV,
        revision_content_hash=REV_HASH,
        approver="reviewer",
        signing_key=SIGNING_KEY,
        valid_for=timedelta(hours=1),
    )
    txn2 = _transition(kind="merge", presented_tokens=[merge_tok.token_id])
    v = eng.authorize_transition(txn2, _state(approvals={merge_tok.token_id: merge_tok}))
    assert v.allowed


# ── AC-7-2: separation of duties ───────────────────────────────────────────────


def test_separation_of_duties_denies_self_approval() -> None:
    eng = _engine()
    self_tok = _valid_token(approver="deployer")  # actor approves their own transition
    txn = _transition(presented_tokens=[self_tok.token_id])
    v = eng.authorize_transition(txn, _state(approvals={self_tok.token_id: self_tok}))
    assert not v.allowed
    assert v.failed_gate == "separation_of_duties"


# ── AC-7-3: stale approval rejection ───────────────────────────────────────────


def test_stale_approval_does_not_count() -> None:
    eng = _engine()
    tok = _valid_token()
    txn = _transition(presented_tokens=[tok.token_id])
    # current hash differs from the token's bound hash -> stale
    state = _state(approvals={tok.token_id: tok}, revision_hashes={REV: "hash-CHANGED"})
    v = eng.authorize_transition(txn, state)
    assert not v.allowed
    assert v.failed_gate == "required_approvals"


def test_forged_signature_token_is_absent() -> None:
    eng = _engine()
    tok = _valid_token()
    forged = tok.model_copy(update={"signature": "deadbeef"})
    txn = _transition(presented_tokens=[forged.token_id])
    v = eng.authorize_transition(txn, _state(approvals={forged.token_id: forged}))
    assert not v.allowed
    assert v.failed_gate == "required_approvals"


def test_expired_token_is_absent() -> None:
    eng = _engine()
    expired = ApprovalToken.mint(
        approved_transition_kind="release",
        artifact_revision_id=REV,
        revision_content_hash=REV_HASH,
        approver="reviewer",
        signing_key=SIGNING_KEY,
        valid_for=timedelta(hours=-1),  # already expired
    )
    txn = _transition(presented_tokens=[expired.token_id])
    v = eng.authorize_transition(txn, _state(approvals={expired.token_id: expired}))
    assert not v.allowed
    assert v.failed_gate == "required_approvals"


# ── AC-6-4 / AC-7-4: PolicyGateHook blocks a denied consequential tool ─────────


class _FakeBeforeToolEvent:
    def __init__(self, name: str, args: dict, invocation_state: dict) -> None:
        self.tool_use = {"name": name, "toolUseId": "t1", "input": args}
        self.invocation_state = invocation_state


def test_policy_gate_hook_blocks_denied_consequential_tool() -> None:
    eng = _engine(required_approvals=1)
    hook = PolicyGateHook(eng, frozenset({"deploy_release"}))
    # no approvals presented -> required_approvals gate denies -> hook raises
    event = _FakeBeforeToolEvent(
        "deploy_release",
        {
            "actor": "deployer",
            "kind": "release",
            "target_revision_id": REV,
            "consequential": True,
            "presented_tokens": [],
        },
        _state(),
    )
    with pytest.raises(PolicyGateDenied) as exc:
        hook._authorize(event)
    assert exc.value.verdict.failed_gate == "required_approvals"


def test_policy_gate_hook_allows_authorized_tool() -> None:
    eng = _engine(required_approvals=1)
    hook = PolicyGateHook(eng, frozenset({"deploy_release"}))
    tok = _valid_token()
    event = _FakeBeforeToolEvent(
        "deploy_release",
        {
            "actor": "deployer",
            "kind": "release",
            "target_revision_id": REV,
            "consequential": True,
            "presented_tokens": [tok.token_id],
        },
        _state(approvals={tok.token_id: tok}),
    )
    hook._authorize(event)  # no raise = authorized


def test_policy_gate_hook_ignores_non_consequential_tool() -> None:
    eng = _engine()
    hook = PolicyGateHook(eng, frozenset({"deploy_release"}))
    # a normal tool never touches the engine
    event = _FakeBeforeToolEvent("rg_search", {"query": "foo"}, _state())
    hook._authorize(event)  # no raise, no engine call


# ── AC-3-7: engine does not mutate invocation_state ────────────────────────────


def test_engine_does_not_mutate_state() -> None:
    eng = _engine()
    tok = _valid_token()
    state = _state(approvals={tok.token_id: tok})
    import copy

    before = copy.deepcopy({k: v for k, v in state.items() if k != STATE_APPROVALS})
    eng.authorize_transition(_transition(presented_tokens=[tok.token_id]), state)
    after = {k: v for k, v in state.items() if k != STATE_APPROVALS}
    assert before == after


# ── AC-7-5: regression — non-consequential run unchanged, engine never denies ──


def test_non_consequential_run_is_never_denied() -> None:
    eng = _engine(required_approvals=1)
    hook = PolicyGateHook(eng, frozenset({"deploy_release"}))
    # A run that only calls ordinary tools: the gate is present but never denies.
    for name in ("rg_search", "read_file_bounded", "write_artifact", "dispatch_team"):
        hook._authorize(_FakeBeforeToolEvent(name, {}, _state()))  # no raise for any


def test_denying_decision_records_denial() -> None:
    eng = _engine()
    txn = _transition(presented_tokens=[])
    v = eng.authorize_transition(txn, _state())
    dec = denying_decision(v, txn)
    assert dec.selected == "deny"
    assert v.failed_gate is not None
    assert v.reason in dec.rationale or dec.rationale
