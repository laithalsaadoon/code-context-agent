"""Vendor-free specialist-runtime contracts (ASTR §10, §2.1).

This module defines the typed boundary between the ``strands_teams`` framework
and any autonomous external coding harness (Phase 3 / spec 004-coding-runtime).
It is deliberately **domain- and vendor-free**: it imports no coding-agent SDK
(``claude_agent_sdk``, ``codex``, ``openai_codex``) and no ``subprocess``. The
concrete adapters that DO import those SDKs live in the separate
``strands_teams_runtimes`` workspace package; core only owns the contract they
satisfy (plan §3; research-coding-runtimes.yaml ``architecture``).

The contract has three parts:

- :class:`ResourceUsage` — a ``FrozenModel`` carrying the cost/token/turn/duration
  accounting a harness reports.
- :class:`SpecialistResult` — a ``FrozenModel`` carrying only the **structured
  outcome** of a harness run: a disposition, the worktree-relative paths it
  changed, typed :class:`~strands_teams.models.Finding` objects (reusing the P1
  type), evidence refs (reusing the P1 :class:`~strands_teams.models.Evidence`
  type), a residual-risk note, and resource usage. It carries **no raw
  transcript, private chain-of-thought, or full terminal command log** — that is
  an ASTR §10.4 invariant and a context-budget necessity.
- :class:`SpecialistRuntime` — a ``Protocol`` (structural interface) declaring
  ``submit`` / ``status`` / ``send_input`` / ``cancel`` / ``collect``.
  ``collect`` returns a :class:`SpecialistResult`, never the transcript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import Field

from .models import Evidence, Finding, FrozenModel

if TYPE_CHECKING:
    from collections.abc import Mapping

SpecialistDisposition = Literal["completed", "partial", "failed", "refused"]
"""How a specialist harness run concluded.

- ``completed`` — the harness finished its task successfully.
- ``partial`` — the harness ran to a limit (e.g. max turns) with usable but
  incomplete work.
- ``failed`` — the harness errored, timed out, or exceeded its budget without a
  usable result.
- ``refused`` — the harness declined the task (e.g. a safety refusal).
"""


class ResourceUsage(FrozenModel):
    """Cost and token accounting reported by a specialist harness run (ASTR §10.4).

    Immutable and ``extra="forbid"`` (inherited from :class:`FrozenModel`). Every
    field defaults to a zero-ish value so an adapter can report a partial
    accounting (e.g. a Codex run that surfaces token counts but no dollar cost)
    without constructing an invalid object.

    Attributes:
        cost_usd: Dollar cost of the run (Claude ``ResultMessage.total_cost_usd``;
            ``0.0`` when the provider reports none).
        input_tokens: Total input tokens consumed.
        output_tokens: Total output tokens generated.
        num_turns: Number of agent turns (tool-use round trips) the harness took.
        duration_seconds: Wall-clock duration of the run, in seconds.
    """

    cost_usd: float = Field(default=0.0, ge=0.0, description="Dollar cost of the run")
    input_tokens: int = Field(default=0, ge=0, description="Total input tokens consumed")
    output_tokens: int = Field(default=0, ge=0, description="Total output tokens generated")
    num_turns: int = Field(default=0, ge=0, description="Number of agent turns (tool-use round trips)")
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Wall-clock duration of the run in seconds")


class SpecialistResult(FrozenModel):
    """The structured outcome of one specialist harness run (ASTR §10.4).

    This is the ONLY thing that crosses the harness boundary back into the swarm's
    context. It carries a disposition, the artifacts the harness changed, typed
    findings, evidence refs, a residual-risk note, and resource usage — but never
    the raw transcript, private chain-of-thought, or full terminal command log
    (ASTR §10.4; plan §5 "Boundary discipline"). Immutable and ``extra="forbid"``
    (inherited from :class:`FrozenModel`), so an out-of-range ``disposition`` or an
    unexpected extra field is rejected at construction rather than silently
    coerced (AC-2-7).

    Attributes:
        disposition: How the run concluded — one of ``completed`` / ``partial`` /
            ``failed`` / ``refused``.
        artifacts: Worktree-relative path strings the harness created or modified
            (ground truth derivable from ``git status --porcelain``). Typed as
            plain strings in Phase 3; a later phase MAY enrich these to
            ``ArtifactRevision`` objects (spec cross-phase note; P4 not present here).
        findings: Typed :class:`~strands_teams.models.Finding` objects the harness
            produced (reuses the P1 type — never redefined here).
        evidence: Standalone :class:`~strands_teams.models.Evidence` refs backing
            the result (reuses the P1 evidence-reference type).
        residual_risk: A short prose note on risk the harness could not eliminate
            (empty string when none).
        resource_usage: Cost/token/turn/duration accounting for the run.
    """

    disposition: SpecialistDisposition = Field(description="How the run concluded")
    artifacts: list[str] = Field(
        default_factory=list,
        description="Worktree-relative paths the harness created or modified",
    )
    findings: list[Finding] = Field(default_factory=list, description="Typed findings the harness produced")
    evidence: list[Evidence] = Field(default_factory=list, description="Standalone evidence refs backing the result")
    residual_risk: str = Field(default="", description="Short prose note on risk the harness could not eliminate")
    resource_usage: ResourceUsage = Field(
        default_factory=ResourceUsage,
        description="Cost/token/turn/duration accounting for the run",
    )


@runtime_checkable
class SpecialistRuntime(Protocol):
    """Structural interface for an autonomous external coding harness (ASTR §10).

    A ``SpecialistRuntime`` drives one bounded coding task in its own OS
    subprocess (a Claude Agent SDK ``query()`` subprocess, or ``codex exec``) and
    reports back only a :class:`SpecialistResult`. This Protocol is defined in
    ``strands_teams`` core **without importing any coding-agent SDK or
    ``subprocess``** — the concrete adapters live in ``strands_teams_runtimes``
    and are injected into the coding tool by dependency injection, so core stays
    vendor-free (plan §3; research-coding-runtimes.yaml ``architecture``).

    Implementations are typically async because they await a subprocess; a
    ``runtime_checkable`` Protocol only checks method *presence*, not signatures,
    so both sync and async adapters satisfy it structurally.

    The five methods model a submit/poll/collect lifecycle:

    - ``submit(task, *, workspace, model_id, capabilities)`` — start a harness run
      rooted at ``workspace`` (its only writable root) and return an opaque
      run-handle/id.
    - ``status(handle)`` — report the run's current lifecycle state.
    - ``send_input(handle, text)`` — feed additional input to a running harness.
    - ``cancel(handle)`` — cancel a running harness (e.g. on a wall-clock timeout).
    - ``collect(handle)`` — return the :class:`SpecialistResult`. **Returns the
      structured result only — never the harness raw transcript, private
      chain-of-thought, or full terminal command log** (ASTR §10.4).
    """

    def submit(
        self,
        task: str,
        *,
        workspace: object,
        model_id: str,
        capabilities: Mapping[str, object] | None = ...,
    ) -> object:
        """Start a harness run rooted at ``workspace``; return an opaque run handle."""
        ...

    def status(self, handle: object) -> str:
        """Report the run's current lifecycle state (e.g. ``running`` / ``done``)."""
        ...

    def send_input(self, handle: object, text: str) -> None:
        """Feed additional input to a running harness."""
        ...

    def cancel(self, handle: object) -> None:
        """Cancel a running harness (e.g. on a wall-clock timeout)."""
        ...

    def collect(self, handle: object) -> SpecialistResult:
        """Return the structured :class:`SpecialistResult` — never the raw transcript (ASTR §10.4)."""
        ...
