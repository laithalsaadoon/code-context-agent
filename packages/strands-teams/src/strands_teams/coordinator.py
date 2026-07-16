"""Generic coordinator-agent factory (Wave C).

:func:`create_coordinator` builds the domain-agnostic coordinator ``Agent`` from a
:class:`~strands_teams.usecase.UseCase`: it validates the use case (AC-5-5),
composes the system prompt from the use case's rendered template plus a
framework-owned "teams protocol" partial, assembles the five generic team tools
plus ``use_case.domain_tools()`` (AC-5-3), builds the tool registry that
``dispatch_team`` resolves node tool names against, and sets the coordinator's
``structured_output_model`` (AC-5-3).

Per-run shared state (layout, registry, node model config, timeouts, rubric) is
returned as a :class:`~strands_teams.teams.TeamInvocationState` alongside the
agent so the runner can pass it via ``coordinator.invoke_async(prompt,
invocation_state=...)`` — no module-level globals (AC-8-4).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager
from strands.models import BedrockModel
from strands.models.bedrock import CacheConfig

from .teams import TEAM_TOOLS, TeamInvocationState
from .templating import render_prompt
from .usecase import validate_use_case

if TYPE_CHECKING:
    from strands.hooks import HookProvider

    from .usecase import UseCase


def _framework_template_dir() -> Path:
    """Return the directory of framework-owned prompt templates."""
    return Path(__file__).parent / "templates"


def _reasoning_effort_for_mode(use_case: UseCase, *, full_mode: bool) -> str:
    """Pick the reasoning effort for the run mode from the use case's settings."""
    settings = use_case.settings
    return settings.full_reasoning_effort if full_mode else settings.reasoning_effort


def _node_model_config(use_case: UseCase, *, full_mode: bool) -> dict[str, Any]:
    """Build the ``BedrockModel`` kwargs used for both the coordinator and team nodes.

    Returns a plain dict (``cache_config`` given as a dict, coerced to
    ``CacheConfig`` at construction) so it is safe to place into
    ``invocation_state`` (AC-8-2; research-strands.yaml BedrockModel).
    """
    from botocore.config import Config as BotoConfig

    settings = use_case.settings
    effort = _reasoning_effort_for_mode(use_case, full_mode=full_mode)
    return {
        "model_id": settings.model_id,
        "region_name": settings.region,
        "temperature": settings.temperature,
        "boto_client_config": BotoConfig(read_timeout=600, retries={"max_attempts": 10, "mode": "adaptive"}),
        "cache_config": {"strategy": "auto"},
        "additional_request_fields": {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
            "anthropic_beta": ["context-1m-2025-08-07"],
        },
    }


def _build_tool_registry(team_tools: list[Any], domain_tools: list[Any]) -> dict[str, Any]:
    """Map tool name -> tool object for both team tools and domain tools.

    Keys off the ``tool_name`` attribute strands sets on ``@tool``-decorated
    functions (unchanged in 1.47), falling back to ``__name__``. ``dispatch_team``
    resolves the string tool names in team-agent specs through this registry.
    """
    registry: dict[str, Any] = {}
    for t in (*team_tools, *domain_tools):
        name = getattr(t, "tool_name", None) or getattr(t, "__name__", None)
        if name:
            registry[name] = t
    return registry


def _compose_system_prompt(use_case: UseCase) -> str:
    """Compose the coordinator system prompt: use-case render + teams-protocol partial.

    The use case renders its own template (with its planning context, artifact
    structure spec, and findings-format directive); the framework appends its
    generic teams-protocol partial so the domain guidance and the mechanics live
    in the prompt rather than in tool docstrings (packet §4).
    """
    domain_prompt = render_prompt(
        use_case.template_dir(),
        use_case.prompt_template(),
        planning_context=use_case.planning_context(),
        artifact_structure_spec=use_case.artifact_structure_spec(),
        findings_format_directive=use_case.findings_format_directive,
        use_case_name=use_case.name,
    )
    protocol = render_prompt(_framework_template_dir(), "teams_protocol.md.j2")
    return f"{domain_prompt.rstrip()}\n\n{protocol}"


def create_coordinator(
    use_case: UseCase,
    *,
    full_mode: bool = False,
    focus: str | None = None,  # noqa: ARG001 — reserved for use-case kickoff; prompt handles focus
    hooks: list[HookProvider] | None = None,
) -> tuple[Agent, TeamInvocationState]:
    """Create the coordinator ``Agent`` and its per-run invocation state.

    Args:
        use_case: The domain use case supplying tools, prompt, model, layout,
            rubric, and structured-output model.
        full_mode: Select full-mode reasoning effort and timeouts when True.
        focus: Optional focus area (the use case bakes this into the kickoff
            prompt built by the runner; kept for signature symmetry).
        hooks: Optional ``HookProvider`` list added to the coordinator agent.

    Returns:
        A ``(agent, invocation_state)`` tuple. The runner invokes the agent with
        ``coordinator.invoke_async(prompt, invocation_state=invocation_state)``.

    Raises:
        MissingUseCaseMemberError: If the use case omits a required member (AC-5-5).
    """
    validate_use_case(use_case)

    settings = use_case.settings
    domain_tools = use_case.domain_tools()
    team_tools = list(TEAM_TOOLS)
    tools = [*team_tools, *domain_tools]

    tool_registry = _build_tool_registry(team_tools, domain_tools)
    node_model_config = _node_model_config(use_case, full_mode=full_mode)

    if full_mode:
        execution_timeout = float(settings.full_team_execution_timeout)
        node_timeout = float(settings.full_team_node_timeout)
    else:
        execution_timeout = float(settings.team_execution_timeout)
        node_timeout = float(settings.team_node_timeout)

    invocation_state: TeamInvocationState = {
        "layout": use_case.layout,
        "tool_registry": tool_registry,
        "node_model_config": node_model_config,
        "execution_timeout": execution_timeout,
        "node_timeout": node_timeout,
        "use_case_name": use_case.name,
        "rubric": use_case.rubric,
    }

    system_prompt = _compose_system_prompt(use_case)
    model = BedrockModel(**_coordinator_model_config(node_model_config))

    conversation_manager = SummarizingConversationManager(summary_ratio=0.3, preserve_recent_messages=10)

    agent = Agent(
        name="coordinator",
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        structured_output_model=use_case.structured_output_model,
        callback_handler=None,
        conversation_manager=conversation_manager,
    )

    if hooks:
        for hook in hooks:
            agent.hooks.add_hook(hook)

    logger.info(
        f"Created coordinator for use_case={use_case.name!r} with {len(tools)} tools "
        f"({len(team_tools)} team + {len(domain_tools)} domain), full_mode={full_mode}",
    )
    return agent, invocation_state


def _coordinator_model_config(node_model_config: dict[str, Any]) -> dict[str, Any]:
    """Coerce the shared model config into ``BedrockModel`` kwargs for the coordinator.

    Coerces the ``cache_config`` dict to a :class:`CacheConfig` instance (the
    node builder does the same at node-construction time).
    """
    config = dict(node_model_config)
    cache_config = config.get("cache_config")
    if isinstance(cache_config, dict):
        config["cache_config"] = CacheConfig(**cache_config)
    return config
