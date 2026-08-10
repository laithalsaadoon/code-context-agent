"""The authority gate: :class:`PolicyEngine` (ASTR §14, spec 007).

The model never decides a consequential transition — this engine does (AC-3-1).
:meth:`PolicyEngine.authorize_transition` runs an ordered stack of five gates over
a :class:`~strands_teams.models.TransitionRequest` and returns a
:class:`~strands_teams.models.PolicyVerdict`:

1. ``capability`` — the actor's :class:`~strands_teams.models.AuthorityGrant` must
   carry the capability the transition requires (AC-3-2).
2. ``separation_of_duties`` — the actor may not be the sole approver of the very
   transition they request (AC-3-3).
3. ``human_required`` — a human-gated transition kind emits a pending
   :class:`~strands_teams.models.HumanDecision` and denies rather than letting the
   model self-approve (AC-4-1).
4. ``required_approvals`` — enough valid, non-expired, signature-verified,
   revision-bound approvals must be present (AC-3-4); stale approvals (content-hash
   mismatch) do not count (AC-5-1, AC-5-4).
5. ``open_blocking_finding`` — no open ``blocking`` finding may affect the target
   (AC-3-5).

All inputs are read from ``invocation_state`` by reference and never mutated
(AC-3-7). The engine is deterministic and does no I/O, so the gate matrix is cheap
to test (AC-7-1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import Decision, HumanDecision, PolicyVerdict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from .models import ApprovalToken, AuthorityGrant, Finding, TransitionRequest

# invocation_state keys the engine reads (by reference, never mutated — AC-3-7).
STATE_GRANTS = "authority_grants"  # Mapping[str, AuthorityGrant] keyed by actor
STATE_APPROVALS = "approval_store"  # Mapping[str, ApprovalToken] keyed by token_id
STATE_FINDINGS = "findings"  # Sequence[Finding]
STATE_REVISION_HASHES = "revision_hashes"  # Mapping[str, str] revision_id -> current content_hash


class PolicyEngine:
    """Runs the ordered authority-gate stack over a transition (AC-3-1).

    Construct one with the run's ``signing_key``, ``required_approvals`` threshold,
    and ``human_gated_kinds`` (all from :class:`~strands_teams.TeamsSettings`), then
    call :meth:`authorize_transition` with the transition and the
    ``invocation_state`` dict. The engine reads grants, the approval store,
    findings, and current revision hashes from that state by reference; it never
    writes to it (AC-3-7).

    Attributes:
        signing_key: Key that approval signatures must verify against; a token that
            fails verification is treated as absent (AC-1-4).
        required_approvals: Valid revision-bound approvals a consequential
            transition needs before the required-approvals gate passes (AC-3-4).
        human_gated_kinds: Transition kinds that are human-gated (AC-4-1).
    """

    def __init__(
        self,
        *,
        signing_key: str,
        required_approvals: int = 1,
        human_gated_kinds: Sequence[str] = (),
    ) -> None:
        """Initialize the engine with the run's policy configuration.

        Args:
            signing_key: The approval signing/verification key.
            required_approvals: Required valid approvals for a consequential transition.
            human_gated_kinds: Transition kinds that must go through a human gate.
        """
        self.signing_key = signing_key
        self.required_approvals = required_approvals
        self.human_gated_kinds = frozenset(human_gated_kinds)

    def authorize_transition(
        self,
        transition: TransitionRequest,
        invocation_state: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> PolicyVerdict:
        """Return the final allow/deny verdict for ``transition`` (AC-3-1, AC-3-6).

        Runs the gate stack in order and short-circuits on the first failure. When
        every gate passes for a non-human-gated transition, returns
        ``allowed=True`` (AC-3-6). Reads all state from ``invocation_state`` by
        reference and does not mutate it (AC-3-7).

        Args:
            transition: The proposed transition to judge.
            invocation_state: The run state (grants/approvals/findings/revision
                hashes) read by reference.
            now: Clock override for expiry checks (defaults to real UTC now).

        Returns:
            A :class:`PolicyVerdict`; on a ``human_required`` deny it also carries
            the pending :class:`HumanDecision` to suspend on (AC-4-1).
        """
        grants: Mapping[str, AuthorityGrant] = invocation_state.get(STATE_GRANTS, {})
        approvals: Mapping[str, ApprovalToken] = invocation_state.get(STATE_APPROVALS, {})
        findings: Sequence[Finding] = invocation_state.get(STATE_FINDINGS, [])
        revision_hashes: Mapping[str, str] = invocation_state.get(STATE_REVISION_HASHES, {})

        # Gate 1: capability (AC-3-2)
        grant = grants.get(transition.actor)
        if grant is None or not getattr(grant, transition.required_capability, False):
            return PolicyVerdict(
                allowed=False,
                failed_gate="capability",
                reason=(
                    f"Actor '{transition.actor}' lacks required capability "
                    f"'{transition.required_capability}' for transition '{transition.kind}'."
                ),
            )

        presented = [approvals[t] for t in transition.presented_tokens if t in approvals]

        # Gate 2: separation of duties (AC-3-3) — actor cannot be sole approver.
        if presented and all(tok.approver == transition.actor for tok in presented):
            return PolicyVerdict(
                allowed=False,
                failed_gate="separation_of_duties",
                reason=(
                    f"Actor '{transition.actor}' is the sole approver of the transition they requested; "
                    "separation of duties requires an independent approver."
                ),
            )

        # Gate 3: human-required (AC-4-1) — emit a pending gate, do not self-approve.
        if transition.kind in self.human_gated_kinds:
            pending = HumanDecision(
                transition=transition,
                prompt=(
                    f"Human approval required for '{transition.kind}' transition "
                    f"on revision '{transition.target_revision_id}' requested by '{transition.actor}'."
                ),
                options=["approve", "reject"],
            )
            return PolicyVerdict(
                allowed=False,
                failed_gate="human_required",
                reason=f"Transition kind '{transition.kind}' is human-gated; a human must resolve the decision.",
                pending_human_decision=pending,
            )

        # Gate 4: required approvals (AC-3-4, AC-5-1, AC-5-4) — count only VALID tokens.
        if transition.consequential or self.required_approvals > 0:
            valid = [tok for tok in presented if self._approval_is_valid(tok, transition, revision_hashes, now)]
            if len(valid) < max(self.required_approvals, 1 if transition.consequential else 0):
                return PolicyVerdict(
                    allowed=False,
                    failed_gate="required_approvals",
                    reason=(
                        f"Transition '{transition.kind}' has {len(valid)} valid revision-bound approval(s); "
                        f"{self.required_approvals} required (stale/expired/forged tokens do not count)."
                    ),
                )

        # Gate 5: open blocking finding (AC-3-5, AC-6-3).
        if self._has_open_blocking_finding(findings, transition):
            return PolicyVerdict(
                allowed=False,
                failed_gate="open_blocking_finding",
                reason=(
                    f"An open blocking finding affects the target of transition '{transition.kind}'; "
                    "it must be resolved before the transition is authorized."
                ),
            )

        return PolicyVerdict(
            allowed=True,
            reason=f"All gates passed for transition '{transition.kind}'.",
        )

    def _approval_is_valid(
        self,
        token: ApprovalToken,
        transition: TransitionRequest,
        revision_hashes: Mapping[str, str],
        now: datetime | None,
    ) -> bool:
        """Return whether ``token`` counts toward required approvals.

        A token counts only when it is for this transition kind, binds the target
        revision, verifies against the signing key (AC-1-4), is not expired
        (AC-1-5), and its ``revision_content_hash`` still matches the artifact's
        current hash — i.e. it is not stale (AC-5-1).
        """
        if token.approved_transition_kind != transition.kind:
            return False
        if token.artifact_revision_id != transition.target_revision_id:
            return False
        if not token.signature_valid(self.signing_key):
            return False
        if token.is_expired(now):
            return False
        current_hash = revision_hashes.get(token.artifact_revision_id)
        # Stale: the bound revision's content has changed since the approval was minted (AC-5-1).
        return current_hash is None or current_hash == token.revision_content_hash

    @staticmethod
    def _has_open_blocking_finding(findings: Sequence[Finding], transition: TransitionRequest) -> bool:
        """Return whether an open blocking finding affects the transition target (AC-3-5)."""
        targets = {transition.target_revision_id, transition.target_artifact} - {""}
        for finding in findings:
            if not finding.blocking or finding.disposition != "open":
                continue
            # Untargeted blocking findings apply globally; targeted ones must overlap.
            affected = set(finding.affected_artifacts)
            if not affected or affected & targets:
                return True
        return False


def denying_decision(verdict: PolicyVerdict, transition: TransitionRequest) -> Decision:
    """Build an auditable :class:`Decision` recording a denied transition (AC-4-6).

    Used by the runner when a human rejects a gate or the engine refuses a
    transition, so the denial is captured as a first-class artifact rather than
    living only in a log line.

    Args:
        verdict: The denying verdict.
        transition: The transition that was refused.

    Returns:
        A frozen :class:`Decision` selecting ``deny``.
    """
    return Decision(
        question=f"Authorize '{transition.kind}' transition on '{transition.target_revision_id}'?",
        options=["allow", "deny"],
        selected="deny",
        rationale=verdict.reason or f"Denied at gate '{verdict.failed_gate}'.",
    )
