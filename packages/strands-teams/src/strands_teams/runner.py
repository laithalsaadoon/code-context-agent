"""Generic run loop for a strands_teams coordinator (Wave C).

:func:`run_teams` orchestrates a use-case run: it validates and builds the
coordinator via :func:`strands_teams.coordinator.create_coordinator`, wires
optional hook-driven display, invokes the coordinator with the per-run
``invocation_state`` (AC-8-1, AC-8-4), persists the structured output, and cleans
up. Adapted from the pre-split ``code_context_agent.agent.runner`` skeleton
(limits, Live wiring, structured-output persistence, ``_cleanup``, ``StreamResult``)
but domain-agnostic — all domain vocabulary comes from the ``UseCase``.

The hooks (:mod:`strands_teams.hooks`) and display (:mod:`strands_teams.display`)
modules are owned by concurrent Wave C tasks; this module imports them lazily and
tolerates their absence so it can be developed and tested independently. The
expected names are ``create_all_hooks(use_case, *, full_mode, state, quiet)`` and
the display types ``AgentDisplayState`` / ``RichEventConsumer`` / ``QuietConsumer``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from strands import Agent

    from .teams import TeamInvocationState
    from .usecase import UseCase

# Non-interactive defaults — the coordinator runs unattended.
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")


class StreamResult(BaseModel):
    """Result of a coordinator run."""

    status: str  # "completed", "error", "stopped"
    duration_seconds: float = 0.0
    error_message: str | None = None
    structured_output: Any = None


def _make_display_state(use_case: UseCase, *, quiet: bool, full_mode: bool) -> Any:
    """Build the display state object (or ``None`` in quiet mode).

    Imports :mod:`strands_teams.display` lazily (owned by a concurrent Wave C
    task); returns ``None`` if display is unavailable so the runner still works
    headless. The state carries the use case's :class:`PhaseSpec` and duration
    bound so the TUI can render ``[i/N]`` without any codebase vocabulary.
    """
    if quiet:
        return None
    try:
        from .display import AgentDisplayState  # type: ignore[attr-defined]
    except ImportError:
        logger.debug("strands_teams.display unavailable — running without a display state")
        return None
    settings = use_case.settings
    max_duration = float(settings.full_max_duration if full_mode else settings.agent_max_duration)
    return AgentDisplayState(phase_spec=use_case.phases, max_duration=max_duration)


def _make_hooks(use_case: UseCase, *, full_mode: bool, state: Any, quiet: bool) -> list[Any]:
    """Build hook providers via :mod:`strands_teams.hooks`, tolerating its absence."""
    try:
        from .hooks import create_all_hooks  # type: ignore[attr-defined]
    except ImportError:
        logger.debug("strands_teams.hooks unavailable — running without hooks")
        return []
    return list(create_all_hooks(use_case, full_mode=full_mode, state=state, quiet=quiet))


def _start_live_display(use_case: UseCase, state: Any, *, full_mode: bool) -> Any:
    """Start a Rich Live display bound to the consumer, if display is available.

    Returns the ``Live`` instance (already started) or ``None``.
    """
    if state is None:
        return None
    try:
        from rich.live import Live

        from .display import RichEventConsumer, bind_live_renderable  # type: ignore[attr-defined]
    except ImportError:
        return None

    consumer = RichEventConsumer(
        state,
        title=use_case.name,
        mode_label="FULL" if full_mode else None,
    )
    live = Live(
        consumer._build_display(),
        console=consumer.console,
        refresh_per_second=2,
        transient=True,
        vertical_overflow="ellipsis",
    )
    bind_live_renderable(live, consumer._build_display)
    live.start()
    return live


async def _run_coordinator(coordinator: Agent, prompt: str, invocation_state: TeamInvocationState) -> StreamResult:
    """Invoke the coordinator once and normalize its result into a ``StreamResult``."""
    from strands.types.exceptions import ContextWindowOverflowException

    start = time.monotonic()
    error_message: str | None = None
    structured_output = None
    status = "completed"

    try:
        result = await coordinator.invoke_async(prompt, invocation_state={**invocation_state})
        structured_output = getattr(result, "structured_output", None)
        stop_reason = getattr(result, "stop_reason", "end_turn")
        if stop_reason in ("max_tokens", "content_filtered", "guardrail_intervened"):
            status, error_message = "error", f"Coordinator stopped: {stop_reason}"
        elif stop_reason == "cancelled":
            status = "stopped"
    except ContextWindowOverflowException:
        logger.error("Context window overflow after compaction")
        status = "error"
        error_message = "Run exceeded the model's context window. Narrow the focus or reduce mode."
    except Exception as e:  # noqa: BLE001 — surface any failure as an error result
        import traceback

        logger.error(f"Coordinator error: {e}\n{traceback.format_exc()}")
        status = "error"
        error_message = str(e) or type(e).__name__

    return StreamResult(
        status=status,
        duration_seconds=time.monotonic() - start,
        error_message=error_message,
        structured_output=structured_output,
    )


def _persist_structured_output(use_case: UseCase, structured_output: Any) -> Path | None:
    """Write the coordinator's structured output next to the run's artifacts.

    Returns the written path, or ``None`` if there was nothing to persist or the
    write failed.
    """
    if structured_output is None:
        return None
    output_root = use_case.layout.output_root
    result_path = output_root / "analysis_result.json"
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        if hasattr(structured_output, "model_dump"):
            data = structured_output.model_dump(mode="json")
        else:
            data = structured_output
        result_path.write_text(json.dumps(data, indent=2, default=str))
        logger.info(f"Wrote structured output to {result_path}")
    except (OSError, TypeError, ValueError) as e:
        logger.warning(f"Failed to write structured output: {e}")
        return None
    return result_path


def _cleanup(live: Any) -> None:
    """Stop the Live display, tolerating errors."""
    if live is not None:
        try:
            live.stop()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Error stopping Live display: {e}")


async def run_teams(
    use_case: UseCase,
    *,
    repo_path: str | Path,
    focus: str | None = None,
    extra_context: str | None = None,
    bundles_only: bool = False,
    full_mode: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Run a use-case coordinator end to end.

    Args:
        use_case: The domain use case to run.
        repo_path: The target root the run operates on (passed to ``pre_run``).
        focus: Optional focus area.
        extra_context: Optional free-form context for the kickoff prompt.
        bundles_only: Skip dispatch and regenerate artifacts from existing findings.
        full_mode: Use full-mode reasoning effort and timeouts.
        quiet: Suppress the Rich display.

    Returns:
        Dict with run status, duration, output paths, and the structured output.
    """
    from .coordinator import create_coordinator

    repo = Path(repo_path).resolve()
    output_root = use_case.layout.output_root
    output_root.mkdir(parents=True, exist_ok=True)

    # Deterministic pre-run (e.g. indexing) unless we are only regenerating artifacts.
    if not bundles_only:
        use_case.pre_run(repo)

    state = _make_display_state(use_case, quiet=quiet, full_mode=full_mode)
    hooks = _make_hooks(use_case, full_mode=full_mode, state=state, quiet=quiet)
    coordinator, invocation_state = create_coordinator(use_case, full_mode=full_mode, focus=focus, hooks=hooks)

    prompt = use_case.kickoff_prompt(bundles_only=bundles_only, focus=focus, extra_context=extra_context)

    live = _start_live_display(use_case, state, full_mode=full_mode)
    try:
        stream_result = await _run_coordinator(coordinator, prompt, invocation_state)
    finally:
        _cleanup(live)

    _persist_structured_output(use_case, stream_result.structured_output)

    summary_path = use_case.layout.executive_summary_path()
    return {
        "status": stream_result.status,
        "error": stream_result.error_message,
        "duration_seconds": stream_result.duration_seconds,
        "repo_path": str(repo),
        "output_dir": str(output_root),
        "executive_summary_path": str(summary_path) if summary_path.exists() else None,
        "structured_output": stream_result.structured_output,
    }


def run_teams_sync(
    use_case: UseCase,
    *,
    repo_path: str | Path,
    focus: str | None = None,
    extra_context: str | None = None,
    bundles_only: bool = False,
    full_mode: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Synchronous wrapper around :func:`run_teams`."""
    return asyncio.run(
        run_teams(
            use_case,
            repo_path=repo_path,
            focus=focus,
            extra_context=extra_context,
            bundles_only=bundles_only,
            full_mode=full_mode,
            quiet=quiet,
        ),
    )
