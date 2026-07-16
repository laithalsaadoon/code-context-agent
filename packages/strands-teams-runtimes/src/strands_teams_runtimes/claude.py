"""ClaudeAgentRuntime — Claude Opus 4.8 coding harness on Bedrock (spec US-3).

Wraps ``claude_agent_sdk.query()`` as a :class:`~strands_teams.SpecialistRuntime`.
A bounded, headless task runs in its own subprocess (new stateless session per
call) with writes confined to a per-run git worktree. ``collect`` distills the
run to a vendor-free :class:`~strands_teams.SpecialistResult` built from the final
``ResultMessage`` — the message transcript never crosses the boundary (ASTR
§10.4).

All Claude Agent SDK facts here are grounded in
``research-coding-runtimes.yaml`` ``claude_agent_sdk_bedrock``:

- Model id ``global.anthropic.claude-opus-4-8`` via ``CLAUDE_CODE_USE_BEDROCK=1``.
- ``output_format={"type":"json_schema", ...}`` → parsed onto
  ``ResultMessage.structured_output`` (use ``model_validate``, not
  ``model_validate_json``).
- Budget field is ``max_budget_usd`` (not ``max_cost_usd``).
- ``can_use_tool`` path confinement requires a *streaming* (AsyncIterable) prompt
  and is mutually exclusive with ``permission_prompt_tool_name``.
- Drive with ``await``; never ``asyncio.run()`` inside an already-async runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    query,
)
from loguru import logger
from pydantic import ValidationError

from strands_teams import ResourceUsage, SpecialistResult

from ._structured import StructuredHarnessOutput, structured_to_result

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Mapping

    from strands_teams import SpecialistDisposition

CLAUDE_OPUS_MODEL_ID = "global.anthropic.claude-opus-4-8"
"""Confirmed-live bare Opus 4.8 inference-profile id (research-coding-runtimes.yaml)."""

_ALLOWED_TOOLS = ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
_DISALLOWED_TOOLS = ["WebSearch", "WebFetch"]
# File-path tools whose target path is confined by _extract_paths (AC-3-2). Bash
# is NOT here — its input is {"command": ...} with no path key, so it needs its
# own command-shape guard (see _bash_command_denial), not path extraction.
_PATH_CONFINED_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})
_PATH_ARG_KEYS = ("file_path", "path", "notebook_path")

# Shell control operators that separate one simple command from the next. A
# command line is lexed into tokens (quotes respected) and split on these, so the
# Bash guard inspects each simple command (and its redirects) in a chained line
# (``a && b; c | d``) rather than only the first word.
_SHELL_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "\n"})

# Redirect operators that WRITE to a file target (the token after them is a path to
# confine). Fd-dups (``>&``, ``<&``) and input redirects (``<``) are not writes.
_WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>"})

# Executables that reach the network — an autonomous harness confined to a worktree
# has no business exfiltrating or fetching over the wire (research digest
# ``claude_agent_sdk_bedrock``, "Path confinement"; WebSearch/WebFetch are already
# disallowed, so these are the shell escape hatches).
_NETWORK_EXECUTABLES = frozenset(
    {"curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "rsync", "ftp"},
)

# Maps a final ``ResultMessage.subtype`` to a SpecialistResult disposition
# (AC-3-4): success → completed; max-turns → partial (usable but incomplete);
# everything else (budget cap, runtime error, structured-output retries) → failed.
_SUBTYPE_DISPOSITION: dict[str, SpecialistDisposition] = {
    "success": "completed",
    "error_max_turns": "partial",
    "error_max_budget_usd": "failed",
    "error_during_execution": "failed",
    "error_max_structured_output_retries": "failed",
}


def _structured_output_schema() -> dict[str, Any]:
    """JSON schema for the harness's structured output (the model-authored fields).

    Covers the fields the harness reports about its own work — ``artifacts``,
    ``findings``, ``evidence``, ``residual_risk`` — plus an optional self-reported
    ``disposition``. The authoritative disposition and resource usage are derived
    from the ``ResultMessage`` (subtype + cost/usage), not from these fields.
    """
    return {
        "type": "json_schema",
        "schema": StructuredHarnessOutput.model_json_schema(),
    }


def _resolves_inside(root: Path, candidate: str) -> bool:
    """Return whether ``candidate`` resolves inside ``root`` (path confinement).

    A relative ``candidate`` is resolved against ``root`` (the worktree cwd). An
    absolute path is checked as-is. ``..`` traversal and symlink escapes are
    caught because both sides are fully resolved before the containment check.
    """
    root_resolved = root.resolve()
    target = Path(candidate)
    target = target if target.is_absolute() else root_resolved / target
    try:
        target.resolve().relative_to(root_resolved)
    except ValueError:
        return False
    return True


def _extract_paths(tool_input: Mapping[str, Any]) -> list[str]:
    """Pull candidate filesystem paths out of a tool-use input dict."""
    paths: list[str] = []
    for key in _PATH_ARG_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    return paths


# rm flags that make the delete recursive (``-r``, ``-R``, ``-rf``, ``-fr``, ``--recursive``).
_RM_RECURSIVE_RE = re.compile(r"^-(?:-recursive$|[A-Za-z]*[rR])")

# Command wrappers to peel off when finding a simple command's real executable.
_COMMAND_WRAPPERS = frozenset({"sudo", "env", "command", "nice", "nohup", "time", "exec", "builtin", "\\"})


def _lex_command(command: str) -> list[str] | None:
    """Lex a Bash command line into tokens, respecting quotes and shell operators.

    Uses ``shlex`` in POSIX mode with ``punctuation_chars`` so operators
    (``;`` ``&&`` ``||`` ``|`` ``&``) and redirects (``>`` ``>>`` ``&>`` ``>&``)
    become their own tokens while quoted separators (``"a; b"``) stay inside their
    word. Returns ``None`` if the line cannot be lexed (unbalanced quotes) so the
    caller denies it fail-safe.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """Split a token stream into simple commands on shell separator tokens."""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _redirect_targets(tokens: list[str]) -> list[str]:
    """Return the write targets of any output redirects in one simple command's tokens.

    A write-redirect operator (``>`` ``>>`` ``&>`` ``>|``) is followed by its file
    target token; input redirects (``<``) and fd-dups (``>&``) are not writes, so
    their targets are not confined here.
    """
    targets: list[str] = []
    for idx, token in enumerate(tokens):
        if token in _WRITE_REDIRECTS and idx + 1 < len(tokens):
            target = tokens[idx + 1]
            if target and not target.isdigit():  # a bare fd number is not a path
                targets.append(target)
    return targets


def _command_executable(tokens: list[str]) -> tuple[str, list[str]]:
    """Return ``(executable, remaining_args)`` after peeling env-assignments and wrappers.

    ``FOO=bar sudo git push`` resolves to ``("git", ["push"])`` so the guard
    inspects the real command, not an assignment or a ``sudo`` prefix. Redirect
    operators and their targets are dropped so they are not mistaken for args.
    """
    words = _strip_redirects(tokens)
    idx = 0
    while idx < len(words):
        token = words[idx]
        if "=" in token and token.split("=", 1)[0].isidentifier():  # VAR=value assignment
            idx += 1
            continue
        if token in _COMMAND_WRAPPERS:
            idx += 1
            continue
        return token, words[idx + 1 :]
    return "", []


def _strip_redirects(tokens: list[str]) -> list[str]:
    """Drop redirect operators and their target tokens, leaving the command words."""
    words: list[str] = []
    skip_next = False
    for idx, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token in _WRITE_REDIRECTS or token in {"<", ">&", "<&", ">|"}:
            skip_next = idx + 1 < len(tokens)  # the target token, if any
            continue
        words.append(token)
    return words


def _simple_command_denial(root: Path, tokens: list[str]) -> str | None:
    """Return a denial reason if one simple Bash command escapes the worktree, else ``None``.

    Confines a Bash command the way ``can_use_tool`` confines ``Write`` / ``Edit``
    (AC-3-2, AC-8-7): denies output redirects to paths outside ``root``, recursive
    ``rm`` reaching outside ``root``, ``cd`` outside ``root``, ``git push``, and any
    network executable — the escape hatches a ``{"command": ...}`` Bash input opens
    that plain path-argument extraction cannot see.
    """
    for target in _redirect_targets(tokens):
        if not _resolves_inside(root, target):
            return f"Bash redirect to {target!r} writes outside the worktree {root} — denied."

    executable, args = _command_executable(tokens)
    if not executable:
        return None
    return _executable_denial(root, Path(executable).name, args)


def _executable_denial(root: Path, exe_name: str, args: list[str]) -> str | None:
    """Return a denial reason for a specific executable + args, else ``None``.

    Denies network executables, ``git push``, ``cd`` out of ``root``, and recursive
    ``rm`` of a path outside ``root`` — the shell escape hatches a Bash command
    opens that path-argument extraction cannot see.
    """
    if exe_name in _NETWORK_EXECUTABLES:
        return f"Bash network command {exe_name!r} is not permitted from a confined worktree — denied."
    if exe_name == "git" and "push" in args:
        return "Bash 'git push' is not permitted from a confined worktree — denied."
    if exe_name == "cd":
        target = args[0] if args else "~"
        if not _resolves_inside(root, target):
            return f"Bash 'cd {target}' leaves the worktree {root} — denied."
    if exe_name == "rm" and any(_RM_RECURSIVE_RE.match(a) for a in args):
        for target in (a for a in args if not a.startswith("-")):
            if not _resolves_inside(root, target):
                return f"Bash 'rm -r {target}' targets a path outside the worktree {root} — denied."
    return None


def _bash_command_denial(root: Path, tool_input: Mapping[str, Any]) -> str | None:
    """Return a denial reason if a Bash tool-use escapes the worktree, else ``None``.

    Lexes the ``command`` string (quotes respected), splits it into simple commands
    on shell separators (``;`` ``&&`` ``||`` ``|`` ``&`` newline), and confines each
    one (:func:`_simple_command_denial`), so a chained ``cd /tmp && rm -rf x`` is
    caught on any offending link, not just the first word. An unparseable command
    line is denied fail-safe.
    """
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    tokens = _lex_command(command)
    if tokens is None:
        return f"Bash command {command.strip()!r} could not be parsed safely — denied."
    for segment in _split_segments(tokens):
        reason = _simple_command_denial(root, segment)
        if reason is not None:
            return reason
    return None


@dataclass
class ClaudeRunHandle:
    """Opaque handle for one in-flight/finished ``ClaudeAgentRuntime`` run (the ``SpecialistRuntime`` handle type).

    Carries the in-flight ``query()`` async generator (so :meth:`ClaudeAgentRuntime.cancel`
    can close it and tear down the harness subprocess) and, once the run finishes,
    the final ``ResultMessage`` :meth:`ClaudeAgentRuntime.collect` distills.

    Attributes:
        workspace: The run's worktree (its only writable root, the harness cwd).
        result_message: The final ``ResultMessage`` once the run completes, else
            ``None``.
        duration_seconds: Wall-clock duration measured by the adapter.
        generator: The live ``query()`` async generator while the run is in flight
            (``None`` before submit and after collection). Closing it tears down the
            SDK transport subprocess.
        started_at: ``time.monotonic()`` at submit (for duration accounting).
    """

    workspace: Path
    result_message: Any = None
    duration_seconds: float = 0.0
    generator: Any = field(default=None, repr=False)
    started_at: float = 0.0
    _done: bool = field(default=False, repr=False)


class ClaudeAgentRuntime:
    """A :class:`~strands_teams.SpecialistRuntime` backed by Claude Opus 4.8 on Bedrock.

    Args:
        region: AWS region passed to the harness subprocess (``AWS_REGION``).
        max_turns: Default per-run turn cap (``ClaudeAgentOptions.max_turns``).
        max_budget_usd: Default per-run dollar cap
            (``ClaudeAgentOptions.max_budget_usd`` — note the SDK field name).
    """

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        max_turns: int = 60,
        max_budget_usd: float = 5.0,
    ) -> None:
        """Store the region and default per-run turn/budget caps."""
        self.region = region
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd

    # -- options + confinement ------------------------------------------------

    def build_options(
        self,
        *,
        workspace: Path,
        model_id: str | None = None,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
    ) -> ClaudeAgentOptions:
        """Build the exact ``ClaudeAgentOptions`` for a confined Bedrock run (AC-3-1).

        Sets ``model=global.anthropic.claude-opus-4-8`` (unless a caller overrides
        ``model_id``), ``cwd=<worktree>``, ``permission_mode="acceptEdits"``, the
        allowed/disallowed tool lists, ``setting_sources=[]`` (isolate host
        ``.claude``), a ``json_schema`` ``output_format``, both ``max_turns`` and
        ``max_budget_usd``, the ``can_use_tool`` path-confinement callback, and
        ``env={"CLAUDE_CODE_USE_BEDROCK":"1","AWS_REGION":<region>}``.
        """
        return ClaudeAgentOptions(
            model=model_id or CLAUDE_OPUS_MODEL_ID,
            cwd=str(workspace),
            permission_mode="acceptEdits",
            allowed_tools=list(_ALLOWED_TOOLS),
            disallowed_tools=list(_DISALLOWED_TOOLS),
            setting_sources=[],
            system_prompt={"type": "preset", "preset": "claude_code"},
            output_format=_structured_output_schema(),
            max_turns=max_turns if max_turns is not None else self.max_turns,
            max_budget_usd=max_budget_usd if max_budget_usd is not None else self.max_budget_usd,
            can_use_tool=self.make_can_use_tool(workspace),
            env={"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": self.region},
        )

    def make_can_use_tool(self, workspace: Path) -> Any:
        """Build the ``can_use_tool`` callback confining writes to ``workspace`` (AC-3-2, AC-3-5).

        The returned async callback denies, and everything else it allows
        (AC-3-5, AC-8-7 — denial returns ``PermissionResultDeny`` so the action
        never occurs):

        - any ``Edit`` / ``Write`` / ``NotebookEdit`` whose target path resolves
          outside the worktree subtree (via :func:`_extract_paths`); and
        - any ``Bash`` command that escapes the worktree — an output redirect to an
          outside path, ``cd`` out of the worktree, recursive ``rm`` of an outside
          path, ``git push``, or a network executable (via
          :func:`_bash_command_denial`). Bash needs its own guard because its input
          is ``{"command": ...}`` with no ``file_path`` key for path extraction to
          read (research digest ``claude_agent_sdk_bedrock``, "Path confinement").
        """
        root = workspace

        async def can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            context: Any,  # noqa: ARG001 — ToolPermissionContext, unused here
        ) -> Any:
            if tool_name in _PATH_CONFINED_TOOLS:
                for candidate in _extract_paths(tool_input):
                    if not _resolves_inside(root, candidate):
                        return PermissionResultDeny(
                            message=(f"Path {candidate!r} resolves outside the worktree {root} — denied."),
                        )
            elif tool_name == "Bash":
                reason = _bash_command_denial(root, tool_input)
                if reason is not None:
                    return PermissionResultDeny(message=reason)
            return PermissionResultAllow()

        return can_use_tool

    # -- SpecialistRuntime lifecycle -----------------------------------------

    def submit(
        self,
        task: str,
        *,
        workspace: Path,
        model_id: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
    ) -> ClaudeRunHandle:
        """Start a bounded ``query()`` run and return a live :class:`ClaudeRunHandle` (AC-3-1).

        Builds the confined options and opens the ``query()`` async generator
        (which spawns the SDK transport subprocess), storing it on the handle so
        :meth:`cancel` can close it. Does NOT drain it — call :meth:`collect` (or
        :meth:`run`) to iterate to the final ``ResultMessage``. Uses a streaming
        (AsyncIterable) prompt so the ``can_use_tool`` callback is legal.
        """
        options = self.build_options(workspace=workspace, model_id=model_id)
        generator = query(prompt=_single_prompt(task), options=options)
        return ClaudeRunHandle(workspace=workspace, generator=generator, started_at=time.monotonic())

    def status(self, handle: ClaudeRunHandle) -> str:
        """Report the run's lifecycle state — ``done`` once the final ``ResultMessage`` landed, else ``running``."""
        return "done" if handle._done else "running"

    def send_input(self, handle: ClaudeRunHandle, text: str) -> None:
        """Not supported: a bounded ``query()`` run is unidirectional and takes no mid-run input."""
        msg = "ClaudeAgentRuntime uses one-shot query(); use ClaudeSDKClient for interactive input (research digest)."
        raise NotImplementedError(msg)

    def cancel(self, handle: ClaudeRunHandle) -> None:
        """Close the ``query()`` generator, tearing down the harness subprocess (AC-7-3).

        Schedules ``generator.aclose()`` so the SDK transport subprocess is torn
        down and no orphaned harness keeps spending on Bedrock. Idempotent and
        best-effort: a no-op once the run finished or was already cancelled.
        """
        generator = handle.generator
        if generator is None or handle._done:
            return
        aclose = getattr(generator, "aclose", None)
        if aclose is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_safe_aclose(generator))
        else:  # no running loop (sync caller): close synchronously
            with contextlib.suppress(Exception):
                asyncio.run(_safe_aclose(generator))

    async def run(
        self,
        task: str,
        *,
        workspace: Path,
        model_id: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
    ) -> SpecialistResult:
        """Compose :meth:`submit` + :meth:`collect` into one bounded run (AC-3-6).

        Iterates ``query()`` with an ``await`` (never ``asyncio.run()``), captures
        the final ``ResultMessage``, and hands it to :meth:`collect`. If the
        awaiting coroutine is cancelled (e.g. the tool wall-clock timeout — AC-7-3),
        the ``query()`` generator is closed so the SDK transport subprocess is torn
        down rather than left running detached (plan §6 cost-blowout mitigation).
        """
        handle = self.submit(task, workspace=workspace, model_id=model_id, capabilities=capabilities)
        try:
            async for message in handle.generator:
                if isinstance(message, ResultMessage):
                    handle.result_message = message
            handle.duration_seconds = time.monotonic() - handle.started_at
            handle._done = True
            return self.collect(handle)
        except asyncio.CancelledError:
            await _safe_aclose(handle.generator)
            raise
        finally:
            if not handle._done:
                await _safe_aclose(handle.generator)

    def collect(self, handle: ClaudeRunHandle) -> SpecialistResult:
        """Build a :class:`~strands_teams.SpecialistResult` from the final ``ResultMessage`` (AC-3-3, AC-3-4).

        Parses ``ResultMessage.structured_output`` with ``model_validate`` (a
        parsed dict, not a JSON string), maps ``subtype`` to a disposition
        (AC-3-4), and reads ``total_cost_usd`` + ``usage`` + ``num_turns`` into
        ``resource_usage``. The message transcript is never surfaced (ASTR §10.4).
        """
        rm = handle.result_message
        if rm is None:
            return SpecialistResult(disposition="failed", residual_risk="Harness produced no final ResultMessage.")

        disposition = _SUBTYPE_DISPOSITION.get(getattr(rm, "subtype", ""), "failed")
        structured = _parse_structured(getattr(rm, "structured_output", None))
        usage = getattr(rm, "usage", None) or {}
        resource_usage = ResourceUsage(
            cost_usd=float(getattr(rm, "total_cost_usd", None) or 0.0),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            num_turns=int(getattr(rm, "num_turns", 0) or 0),
            duration_seconds=handle.duration_seconds,
        )
        return structured_to_result(
            structured,
            disposition=disposition,
            resource_usage=resource_usage,
        )


def _parse_structured(structured_output: Any) -> StructuredHarnessOutput:
    """Validate a parsed ``structured_output`` dict into a :class:`StructuredHarnessOutput`.

    Uses ``model_validate`` (the SDK already parsed the JSON — AC-3-3). A ``None``
    or invalid payload degrades to an empty structured output so a
    schema-retry-exhausted run still yields a valid, transcript-free result.
    """
    if not isinstance(structured_output, dict):
        return StructuredHarnessOutput()
    try:
        return StructuredHarnessOutput.model_validate(structured_output)
    except ValidationError:
        return StructuredHarnessOutput()


async def _single_prompt(task: str) -> AsyncIterable[dict[str, Any]]:
    """Yield a single streaming user message (required for the ``can_use_tool`` callback)."""
    yield {"type": "user", "message": {"role": "user", "content": task}}


async def _safe_aclose(generator: Any) -> None:
    """Close a ``query()`` async generator, swallowing teardown errors (harness-subprocess cleanup)."""
    aclose = getattr(generator, "aclose", None)
    if aclose is None:
        return
    with contextlib.suppress(Exception):
        await aclose()
    logger.debug("ClaudeAgentRuntime: query() generator closed (harness subprocess torn down)")
