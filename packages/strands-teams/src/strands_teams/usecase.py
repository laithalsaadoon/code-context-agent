"""The ``UseCase`` contract — how a domain plugs into strands_teams.

A ``UseCase`` supplies everything the coordinator factory (Wave C) needs to
build a domain-specific run without the framework knowing any domain vocabulary:
the domain tools list, the coordinator prompt template plus a planning-context
loader, the structured-output model, the findings-format directive, the artifact
naming scheme (via :class:`~strands_teams.layout.TeamsLayout`) and output-structure
spec, the scoring rubric, the phase spec (tool→phase map + names), the
tool→reasoning-prompt map, the fail-fast exempt set, the kickoff-prompt builder,
and the settings (env prefix + output dir) (AC-5-1).

Chosen as an **ABC** (not a ``Protocol``) so AC-5-5 can raise an explicit,
member-naming error: :func:`validate_use_case` inspects a concrete instance and
raises :class:`MissingUseCaseMemberError` naming the first missing required
member, rather than proceeding with a silent default. ``__init_subclass__`` also
guards that concrete subclasses declare the required class attributes.

Per-team shared state (output dir, repo/root path, tool registry, timeouts,
rubric) flows through ``invocation_state`` at dispatch time (AC-5-2;
research-strands.yaml §ToolContext), never through module-level globals — so the
``UseCase`` instance itself is designed to be simple and picklable enough to
place into ``invocation_state``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pydantic import BaseModel

    from .layout import TeamsLayout
    from .phases import PhaseSpec
    from .rubric import NarrativeRubric
    from .settings import TeamsSettings

# Required members checked by validate_use_case (AC-5-5). Names match the
# attribute/method a use case must supply; the error names the missing one.
_REQUIRED_MEMBERS: tuple[str, ...] = (
    "name",
    "settings",
    "layout",
    "rubric",
    "phases",
    "structured_output_model",
    "findings_format_directive",
    "domain_tools",
    "prompt_template",
    "template_dir",
    "planning_context",
    "kickoff_prompt",
    "artifact_structure_spec",
)

# Class-level attributes a concrete subclass must declare (checked in
# __init_subclass__ as an early guard; validate_use_case is the authoritative,
# instance-level check that also covers values set in __init__).
_REQUIRED_CLASS_ATTRS: tuple[str, ...] = (
    "name",
    "structured_output_model",
    "findings_format_directive",
)


class MissingUseCaseMemberError(TypeError):
    """Raised when a ``UseCase`` omits a required member.

    The message names the missing member so the failure is actionable rather
    than a silent default (AC-5-5).
    """

    def __init__(self, use_case: str, member: str) -> None:
        """Build the error, recording the use-case name and missing member."""
        self.use_case = use_case
        self.member = member
        super().__init__(f"UseCase {use_case!r} is missing required member: {member!r}")


class UseCase(ABC):
    """Abstract contract a domain implements to run on strands_teams.

    Class attributes (a concrete subclass must set these):
        name: Stable identifier for the use case.
        structured_output_model: Pydantic model type for the coordinator's
            structured output.
        findings_format_directive: Text injected into a dispatched team's
            Required-Output block describing the findings format.

    Instance attributes (set in ``__init__``):
        settings: The use case's :class:`~strands_teams.settings.TeamsSettings`
            subclass instance (env prefix + output dir).
        layout: The :class:`~strands_teams.layout.TeamsLayout` describing artifact
            paths for this run.
        rubric: The :class:`~strands_teams.rubric.NarrativeRubric` for scoring.
        phases: The :class:`~strands_teams.phases.PhaseSpec` (tool→phase + names).
        reasoning_prompts: Map of tool name to a reasoning-checkpoint prompt.
        fail_fast_exempt: Tool names whose error results are non-fatal.
    """

    # --- required class attributes (declared for typing; set by subclass) ---
    name: str
    structured_output_model: type[BaseModel]
    findings_format_directive: str

    # --- instance attributes (populated in subclass __init__) ---
    settings: TeamsSettings
    layout: TeamsLayout
    rubric: NarrativeRubric
    phases: PhaseSpec
    reasoning_prompts: Mapping[str, str] = {}
    fail_fast_exempt: frozenset[str] = frozenset()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Guard that a concrete subclass declares the required class attributes.

        Skips abstract subclasses (those still carrying ``__abstractmethods__``)
        so intermediate bases need not declare everything.
        """
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return
        for attr in _REQUIRED_CLASS_ATTRS:
            if getattr(cls, attr, None) is None:
                raise MissingUseCaseMemberError(getattr(cls, "name", None) or cls.__name__, attr)

    @abstractmethod
    def domain_tools(self) -> list[Any]:
        """Return the domain-specific tool objects for the coordinator agent."""

    @abstractmethod
    def prompt_template(self) -> str:
        """Return the coordinator system-prompt template filename."""

    @abstractmethod
    def template_dir(self) -> Path:
        """Return the directory containing this use case's prompt templates."""

    @abstractmethod
    def planning_context(self) -> dict[str, Any]:
        """Load the planning context injected into the coordinator prompt.

        Typically reads the planning sentinel via ``self.layout.planning_sentinel_path()``.
        """

    @abstractmethod
    def kickoff_prompt(
        self,
        *,
        bundles_only: bool,
        focus: str | None,
        extra_context: str | None,
    ) -> str:
        """Build the initial task prompt handed to the coordinator.

        Args:
            bundles_only: Skip indexing/dispatch and regenerate artifacts only.
            focus: Optional area the run should prioritize.
            extra_context: Optional free-form context to append.

        Returns:
            The kickoff prompt string.
        """

    @abstractmethod
    def artifact_structure_spec(self) -> str:
        """Return prompt text describing the sections each artifact must contain."""

    def pre_run(self, repo_path: Path) -> None:
        """Optional deterministic pre-run step (e.g., indexing).

        Default implementation is a no-op. A use case overrides this to produce
        its planning sentinel before the coordinator runs.

        Args:
            repo_path: The target root the run operates on.
        """
        return


def validate_use_case(use_case: UseCase) -> None:
    """Validate a concrete ``UseCase`` supplies every required member.

    Called by the coordinator factory (Wave C) before constructing the
    coordinator. Raises with the missing member's name rather than proceeding
    with a silent default (AC-5-5).

    Args:
        use_case: The use-case instance to validate.

    Raises:
        MissingUseCaseMemberError: If a required member is absent or ``None``.
    """
    name = getattr(use_case, "name", None) or type(use_case).__name__
    for member in _REQUIRED_MEMBERS:
        if getattr(use_case, member, None) is None:
            raise MissingUseCaseMemberError(name, member)
