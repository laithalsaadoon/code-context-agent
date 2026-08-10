"""The ``invoke_coding_agent`` Strands tool + its dependency-injection factory (spec US-6, US-7).

:func:`make_invoke_coding_agent` returns the ``invoke_coding_agent`` tool with a
runtime registry (``Mapping[str, SpecialistRuntime-like]``) injected, so no
coding-agent SDK is imported to construct the tool — core stays vendor-free
(plan §3, §5). The returned tool is an ``async @tool(context=True)`` in
async-generator form: non-final ``yield``s are heartbeats that surface on the
swarm ``multiagent_node_stream`` feed, and the final ``yield`` is the
:class:`~strands_teams.SpecialistResult` serialized with ``model_dump_json()``
(research-coding-runtimes.yaml ``strands_tool_pattern``).

Concurrency + timeout discipline (US-7):

- A **module-level** ``asyncio.Semaphore`` sized from ``STRANDS_TEAMS_MAX_HARNESSES``
  caps concurrent harness launches (AC-7-1). It is module-level, not per-tool, so
  the cap holds across every team that shares this tool instance.
- The tool owns its **own** ``asyncio.wait_for(..., timeout=_SUBPROCESS_TIMEOUT_S)``
  because the Strands concurrent executor imposes no per-tool timeout (AC-7-2). On
  timeout it yields a ``disposition="failed"`` result rather than hanging or
  raising (AC-7-3).

.. important::
   The swarm ``node_timeout`` and ``execution_timeout`` (defaults at
   ``strands_teams.settings.TeamsSettings`` lines 82-94, applied in
   ``strands_teams.teams`` around lines 285-286) MUST be configured ABOVE
   ``_SUBPROCESS_TIMEOUT_S``. Otherwise the swarm cancels the node before the
   harness finishes (AC-7-4; research-coding-runtimes.yaml ``strands_tool_pattern``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger
from strands import ToolContext, tool

from strands_teams import SpecialistResult

if TYPE_CHECKING:
    from collections.abc import Awaitable


def _max_harnesses() -> int:
    """Read the concurrent-harness cap from ``STRANDS_TEAMS_MAX_HARNESSES`` (default 2)."""
    raw = os.environ.get("STRANDS_TEAMS_MAX_HARNESSES", "2")
    try:
        value = int(raw)
    except ValueError:
        return 2
    return max(1, value)


_HARNESS_SEMAPHORE = asyncio.Semaphore(_max_harnesses())
"""Module-level cap on concurrent harness subprocess launches (AC-7-1, AC-8-3)."""

_SUBPROCESS_TIMEOUT_S = float(os.environ.get("STRANDS_TEAMS_HARNESS_TIMEOUT_S", "1800"))
"""Per-run wall-clock the tool enforces itself (AC-7-2). Swarm timeouts must exceed this (AC-7-4)."""

_HEARTBEAT_INTERVAL_S = 15.0
"""Seconds between heartbeat yields while a harness run is in flight (AC-6-4)."""


class _CodingRuntime(Protocol):
    """Structural interface the tool needs from an injected runtime.

    Narrower than :class:`~strands_teams.SpecialistRuntime`: the tool drives a
    single bounded run, so it only needs an awaitable ``run(task, *, workspace,
    model_id, capabilities) -> SpecialistResult``. Both ``ClaudeAgentRuntime`` and
    ``CodexRuntime`` satisfy it.
    """

    def run(
        self,
        task: str,
        *,
        workspace: Path,
        model_id: str | None = ...,
        capabilities: Mapping[str, Any] | None = ...,
    ) -> Awaitable[SpecialistResult]:
        """Drive one bounded harness run and return its structured result."""
        ...


def _error_result(message: str) -> str:
    """Serialize a JSON error envelope for a missing/unknown invocation-state key (AC-6-5)."""
    import json

    return json.dumps({"status": "error", "message": message})


def _resolve_workspace(state: Mapping[str, Any]) -> Path | None:
    """Return the ``workspace`` Path from invocation_state, or ``None`` if absent/invalid."""
    workspace = state.get("workspace")
    if isinstance(workspace, Path):
        return workspace
    if isinstance(workspace, str) and workspace:
        return Path(workspace)
    return None


def make_invoke_coding_agent(runtime_registry: Mapping[str, _CodingRuntime]) -> Any:
    """Return the ``invoke_coding_agent`` tool with ``runtime_registry`` injected (AC-6-1).

    Args:
        runtime_registry: Map of runtime name -> runtime adapter (e.g.
            ``{"claude": ClaudeAgentRuntime(...), "codex": CodexRuntime(...)}``).
            The tool selects one per call by the ``runtime`` key in
            ``invocation_state``. Injecting the registry keeps every coding-agent
            SDK out of the import path used to *construct* the tool (plan §3).

    Returns:
        An ``async @tool(context=True)`` async-generator tool ready to add to a
        team member's tool list.
    """

    @tool(context=True)
    async def invoke_coding_agent(task: str, tool_context: ToolContext) -> AsyncIterator[str]:
        """Delegate a bounded coding task to an autonomous external harness on Bedrock.

        Runs the task in an isolated per-run git worktree via a Claude Opus 4.8 or
        Codex GPT-5.6 Sol harness (selected by the ``runtime`` key in the run's
        invocation state) and returns a structured :class:`SpecialistResult` — the
        changed files, typed findings, evidence refs, residual risk, and resource
        usage. The harness raw transcript, private chain-of-thought, and terminal
        command log NEVER cross back (ASTR §10.4).

        This tool owns its own subprocess wall-clock timeout and a module-level
        concurrency cap. **The swarm ``node_timeout`` / ``execution_timeout`` MUST
        be configured above this tool's ``_SUBPROCESS_TIMEOUT_S``** (default 1800s)
        or the swarm node is cancelled before the harness finishes.

        Args:
            task: The coding task for the harness (what to implement/fix and why).
            tool_context: Framework-injected run context. Its ``invocation_state``
                must carry ``workspace`` (a ``Path``), ``runtime`` (a key present
                in the injected registry), and optionally ``model_id`` and
                ``capabilities``.

        Yields:
            Zero or more heartbeat strings while the harness runs, then a final
            JSON string: the ``SpecialistResult`` (``model_dump_json()``), or a
            ``{"status": "error", ...}`` envelope if invocation state is missing a
            required key.
        """
        state: dict[str, Any] = tool_context.invocation_state or {}

        workspace = _resolve_workspace(state)
        if workspace is None:
            yield _error_result("invocation_state missing 'workspace' (a Path) — cannot run a coding harness.")
            return

        runtime_name = state.get("runtime")
        if not isinstance(runtime_name, str) or not runtime_name:
            yield _error_result("invocation_state missing 'runtime' — specify which coding runtime to use.")
            return

        runtime = runtime_registry.get(runtime_name)
        if runtime is None:
            available = ", ".join(sorted(runtime_registry)) or "(none)"
            yield _error_result(f"Unknown runtime {runtime_name!r}; registered runtimes: {available}.")
            return

        model_id = state.get("model_id") if isinstance(state.get("model_id"), str) else None
        capabilities = state.get("capabilities") if isinstance(state.get("capabilities"), Mapping) else None

        async for chunk in _drive_run(
            runtime,
            task=task,
            workspace=workspace,
            model_id=model_id,
            capabilities=capabilities,
            runtime_name=runtime_name,
        ):
            yield chunk

    return invoke_coding_agent


async def _drive_run(
    runtime: _CodingRuntime,
    *,
    task: str,
    workspace: Path,
    model_id: str | None,
    capabilities: Mapping[str, Any] | None,
    runtime_name: str,
) -> AsyncIterator[str]:
    """Run one harness under the semaphore + wall-clock timeout, emitting heartbeats (AC-6-4, AC-7-1, AC-7-3).

    Acquires the module-level semaphore around the subprocess launch (AC-7-1),
    starts the run as a task, and yields a heartbeat every
    ``_HEARTBEAT_INTERVAL_S`` until it finishes (AC-6-4). Enforces
    ``_SUBPROCESS_TIMEOUT_S`` itself (AC-7-2): on timeout it cancels the run task
    and yields a ``disposition="failed"`` result rather than raising (AC-7-3).
    """
    async with _HARNESS_SEMAPHORE:
        run_task = asyncio.ensure_future(
            runtime.run(task, workspace=workspace, model_id=model_id, capabilities=capabilities),
        )
        waited = 0.0
        while not run_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(run_task), timeout=_HEARTBEAT_INTERVAL_S)
            except TimeoutError:
                waited += _HEARTBEAT_INTERVAL_S
                if waited >= _SUBPROCESS_TIMEOUT_S:
                    run_task.cancel()
                    await _await_cancelled(run_task)
                    logger.warning(
                        f"invoke_coding_agent({runtime_name}): harness exceeded {_SUBPROCESS_TIMEOUT_S}s — failing.",
                    )
                    yield _timeout_result(runtime_name)
                    return
                yield f"heartbeat: {runtime_name} harness running ({int(waited)}s elapsed)"
            except Exception:  # noqa: BLE001 — run_task finished (with an error); handled below via .result()
                break

        try:
            result = run_task.result()
        except asyncio.CancelledError:
            yield _timeout_result(runtime_name)
            return
        except Exception as exc:  # noqa: BLE001 — a harness failure must not raise across the tool boundary
            logger.warning(f"invoke_coding_agent({runtime_name}): harness raised {type(exc).__name__}: {exc}")
            yield SpecialistResult(
                disposition="failed",
                residual_risk=f"Harness raised {type(exc).__name__}: {str(exc)[:300]}",
            ).model_dump_json()
            return

        yield result.model_dump_json()


async def _await_cancelled(run_task: asyncio.Future[SpecialistResult]) -> None:
    """Await a cancelled run task, swallowing any error raised during teardown."""
    try:
        await run_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — cleanup path, ignore all
        logger.debug("invoke_coding_agent: cancelled harness task raised during teardown (ignored)")


def _timeout_result(runtime_name: str) -> str:
    """Serialize a ``disposition="failed"`` result for a timed-out harness (AC-7-3, AC-8-4)."""
    note = f"{runtime_name} harness exceeded the {_SUBPROCESS_TIMEOUT_S:.0f}s tool timeout and was cancelled."
    return SpecialistResult(disposition="failed", residual_risk=note).model_dump_json()
