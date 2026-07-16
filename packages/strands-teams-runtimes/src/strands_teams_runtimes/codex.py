"""CodexRuntime — Codex GPT-5.6 Sol coding harness on Bedrock (spec US-4).

Launches ``codex exec`` as its own OS subprocess via
``asyncio.create_subprocess_exec`` and distills the run to a vendor-free
:class:`~strands_teams.SpecialistResult`. No LiteLLM — Codex has a built-in
``amazon-bedrock`` provider (``wire_api="responses"``); the adapter only sets the
model, provider, reasoning effort, worktree cwd, output schema, and a per-run
``CODEX_HOME`` + ``AWS_BEARER_TOKEN_BEDROCK``.

Grounded in ``research-coding-runtimes.yaml`` ``codex_gpt56_bedrock``:

- ``codex exec --model openai.gpt-5.6-sol --model-provider amazon-bedrock
  -c model_reasoning_effort=high --cd <worktree> --full-auto --json
  --output-schema <schema> -o result.json "<prompt>"``.
- ``--json`` emits JSONL events (``thread.started``, ``turn.started``, ``item.*``,
  ``turn.completed`` [usage], ``turn.failed``, ``error``).
- GOTCHA #19816: ``--output-schema`` also constrains INTERMEDIATE messages —
  consume the ``-o`` final file / the ``turn.completed`` item, NOT the first
  schema-valid JSONL line.
- GOTCHA #11435: parallel ``codex exec`` share ``~/.codex`` state — each run gets
  its own ``CODEX_HOME``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError

from strands_teams import ResourceUsage, SpecialistResult

from ._structured import StructuredHarnessOutput, structured_to_result

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

CODEX_MODEL_ID = "openai.gpt-5.6-sol"
"""Confirmed GA Codex-on-Bedrock model id (research-coding-runtimes.yaml)."""

CODEX_MODEL_PROVIDER = "amazon-bedrock"
_RESULT_FILENAME = "result.json"
_SCHEMA_FILENAME = "output-schema.json"


@dataclass
class CodexRunHandle:
    """Opaque handle for one ``CodexRuntime`` run (the ``SpecialistRuntime`` handle type).

    Carries both the in-flight process (so :meth:`CodexRuntime.cancel` can kill it)
    and, once the run finishes, the parsed transcript needed by
    :meth:`CodexRuntime.collect`.

    Attributes:
        workspace: The run's worktree (harness cwd).
        codex_home: The per-run ``CODEX_HOME`` (concurrency isolation, #11435).
        result_file: Path to the ``-o`` final result file.
        events: Parsed JSONL events emitted on stdout.
        returncode: Subprocess exit code.
        duration_seconds: Wall-clock duration measured by the adapter.
        process: The live ``asyncio`` subprocess while the run is in flight
            (``None`` before launch and after collection).
        started_at: ``time.monotonic()`` at launch (for duration accounting).
    """

    workspace: Path
    codex_home: Path
    result_file: Path
    events: list[dict[str, Any]] = field(default_factory=list)
    returncode: int = 0
    duration_seconds: float = 0.0
    process: Any = field(default=None, repr=False)
    started_at: float = 0.0


def _event_type(event: Mapping[str, Any]) -> str:
    """Return an event's type across the two shapes Codex JSONL uses (``type``/``msg.type``)."""
    if isinstance(event.get("msg"), dict):
        inner = event["msg"].get("type")
        if isinstance(inner, str):
            return inner
    outer = event.get("type")
    return outer if isinstance(outer, str) else ""


class CodexRuntime:
    """A :class:`~strands_teams.SpecialistRuntime` backed by Codex GPT-5.6 Sol on Bedrock.

    Args:
        region: AWS region for the Bedrock provider (``AWS_REGION``).
        bearer_token: Bedrock long-term API key (``AWS_BEARER_TOKEN_BEDROCK``);
            when ``None`` the value is read from the parent environment at launch.
        codex_home_root: Directory under which per-run ``CODEX_HOME`` dirs are
            created (default ``<tempdir-style>`` under the workspace parent).
        reasoning_effort: ``model_reasoning_effort`` (default ``high``).
        executable: The Codex CLI executable (default ``codex``).
    """

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        bearer_token: str | None = None,
        codex_home_root: Path | None = None,
        reasoning_effort: str = "high",
        executable: str = "codex",
    ) -> None:
        """Store the region, Bedrock bearer token, CODEX_HOME root, effort, and CLI path."""
        self.region = region
        self.bearer_token = bearer_token
        self.codex_home_root = codex_home_root
        self.reasoning_effort = reasoning_effort
        self.executable = executable

    # -- command + env --------------------------------------------------------

    def build_command(
        self,
        workspace: Path,
        *,
        schema_path: Path,
        result_path: Path,
        task: str,
        model_id: str | None = None,
    ) -> list[str]:
        """Build the exact ``codex exec`` argv (AC-4-1).

        Honors a caller-supplied ``model_id`` (from ``invocation_state``) over the
        ``openai.gpt-5.6-sol`` default, matching :class:`ClaudeAgentRuntime`'s
        model-override behavior so the two adapters are consistent.
        """
        return [
            self.executable,
            "exec",
            "--model",
            model_id or CODEX_MODEL_ID,
            "--model-provider",
            CODEX_MODEL_PROVIDER,
            "-c",
            f"model_reasoning_effort={self.reasoning_effort}",
            "--cd",
            str(workspace),
            "--full-auto",
            "--json",
            "--output-schema",
            str(schema_path),
            "-o",
            str(result_path),
            task,
        ]

    def build_env(self, codex_home: Path) -> dict[str, str]:
        """Build the subprocess env with a per-run ``CODEX_HOME`` (AC-4-1, AC-4-4)."""
        env = dict(os.environ)
        env["CODEX_HOME"] = str(codex_home)
        env["AWS_REGION"] = self.region
        token = self.bearer_token or os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
        if token:
            env["AWS_BEARER_TOKEN_BEDROCK"] = token
        return env

    def _prepare_run_dirs(self, workspace: Path, *, run_id: str | None) -> tuple[Path, Path, Path]:
        """Create the per-run ``CODEX_HOME`` and write the output schema; return the paths."""
        rid = run_id or uuid.uuid4().hex[:12]
        home_root = self.codex_home_root or (workspace.parent / ".codex-homes")
        codex_home = home_root / f"codex-run-{rid}"
        codex_home.mkdir(parents=True, exist_ok=True)
        schema_path = codex_home / _SCHEMA_FILENAME
        schema_path.write_text(json.dumps(StructuredHarnessOutput.model_json_schema()), encoding="utf-8")
        result_path = codex_home / _RESULT_FILENAME
        return codex_home, schema_path, result_path

    # -- SpecialistRuntime lifecycle -----------------------------------------

    async def submit(
        self,
        task: str,
        *,
        workspace: Path,
        model_id: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> CodexRunHandle:
        """Launch ``codex exec`` and return a live :class:`CodexRunHandle` (AC-4-1).

        Starts the OS subprocess and stores it on the handle so :meth:`cancel` can
        terminate it while it is still running. Does NOT wait for completion — call
        :meth:`collect` to drive the process to its end and build the result.
        """
        codex_home, schema_path, result_path = self._prepare_run_dirs(workspace, run_id=run_id)
        cmd = self.build_command(
            workspace, schema_path=schema_path, result_path=result_path, task=task, model_id=model_id
        )
        env = self.build_env(codex_home)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return CodexRunHandle(
            workspace=workspace,
            codex_home=codex_home,
            result_file=result_path,
            process=proc,
            started_at=time.monotonic(),
        )

    def status(self, handle: CodexRunHandle) -> str:
        """Report the run's lifecycle state — ``running`` while the subprocess is live, else ``done``."""
        proc = handle.process
        if proc is None or proc.returncode is not None:
            return "done"
        return "running"

    def send_input(self, handle: CodexRunHandle, text: str) -> None:
        """Not supported: a headless ``codex exec --full-auto`` run takes no mid-run input."""
        msg = "CodexRuntime runs are headless (codex exec --full-auto) and accept no mid-run input."
        raise NotImplementedError(msg)

    def cancel(self, handle: CodexRunHandle) -> None:
        """Kill the in-flight ``codex exec`` subprocess so no orphan keeps spending on Bedrock (AC-7-3).

        Idempotent and best-effort: if the process already exited (or was never
        launched) this is a no-op. The subsequent :meth:`collect` (or its
        ``communicate()`` in flight) reaps the killed process.
        """
        proc = handle.process
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()

    async def run(
        self,
        task: str,
        *,
        workspace: Path,
        model_id: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> SpecialistResult:
        """Compose :meth:`submit` + :meth:`collect` into one bounded run (AC-4-1..AC-4-5).

        If the awaiting coroutine is cancelled (e.g. the tool's wall-clock timeout
        fires — AC-7-3), the ``finally`` block kills the ``codex exec`` subprocess
        and reaps it, so a cancelled run never leaves ``codex exec`` (and its
        Bedrock spend) running detached (plan §6 cost-blowout mitigation).
        """
        handle = await self.submit(
            task,
            workspace=workspace,
            model_id=model_id,
            capabilities=capabilities,
            run_id=run_id,
        )
        try:
            return await self._collect_async(handle)
        except asyncio.CancelledError:
            self.cancel(handle)
            await self._reap(handle)
            raise
        finally:
            self.cancel(handle)

    async def _collect_async(self, handle: CodexRunHandle) -> SpecialistResult:
        """Drive the live subprocess to completion, populate the handle, and collect the result."""
        proc = handle.process
        if proc is None:
            return self.collect(handle)
        stdout, _stderr = await proc.communicate()
        handle.events = _parse_jsonl(stdout)
        handle.returncode = proc.returncode or 0
        handle.duration_seconds = time.monotonic() - handle.started_at
        return self.collect(handle)

    @staticmethod
    async def _reap(handle: CodexRunHandle) -> None:
        """Await a killed subprocess so it is reaped and leaves no zombie (best-effort)."""
        proc = handle.process
        if proc is None:
            return
        with contextlib.suppress(Exception):
            await proc.wait()
        logger.debug("CodexRuntime: cancelled codex exec subprocess reaped after kill")

    def collect(self, handle: CodexRunHandle) -> SpecialistResult:
        """Build a ``SpecialistResult`` from the ``-o`` file + ``turn.completed`` (AC-4-2, AC-4-3, AC-4-5).

        The structured result is read from the ``-o`` final result file (NOT the
        first schema-valid JSONL line — gotcha #19816). The disposition is
        ``failed`` if any ``turn.failed`` / ``error`` event appeared or the
        subprocess exited non-zero (AC-4-5), else ``completed``. Token usage is
        mapped from the ``turn.completed`` event's ``usage`` object (AC-4-3). The
        raw JSONL transcript never crosses the boundary (ASTR §10.4).
        """
        failed = handle.returncode != 0 or any(_event_type(e) in {"turn.failed", "error"} for e in handle.events)
        disposition = "failed" if failed else "completed"

        completed = next((e for e in handle.events if _event_type(e) == "turn.completed"), None)
        resource_usage = _usage_from_completed(completed, duration_seconds=handle.duration_seconds)
        structured = _read_result_file(handle.result_file)
        return structured_to_result(structured, disposition=disposition, resource_usage=resource_usage)


def _parse_jsonl(raw: bytes) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON from Codex stdout; skip unparsable lines."""
    events: list[dict[str, Any]] = []
    for line in raw.decode(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _usage_from_completed(event: Mapping[str, Any] | None, *, duration_seconds: float) -> ResourceUsage:
    """Map a ``turn.completed`` event's ``usage`` object into a :class:`ResourceUsage` (AC-4-3)."""
    if event is None:
        return ResourceUsage(duration_seconds=duration_seconds)
    usage = event.get("usage")
    if not isinstance(usage, dict) and isinstance(event.get("msg"), dict):
        usage = event["msg"].get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return ResourceUsage(
        cost_usd=float(usage.get("cost_usd", 0.0) or 0.0),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        num_turns=int(usage.get("num_turns", 1) or 1),
        duration_seconds=duration_seconds,
    )


def _read_result_file(result_file: Path) -> StructuredHarnessOutput:
    """Read + validate the ``-o`` final result file into a :class:`StructuredHarnessOutput`.

    A missing, unreadable, or schema-invalid file degrades to an empty structured
    output so a partial run still yields a valid, transcript-free result.
    """
    if not result_file.exists():
        return StructuredHarnessOutput()
    try:
        payload = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return StructuredHarnessOutput()
    if not isinstance(payload, dict):
        return StructuredHarnessOutput()
    try:
        return StructuredHarnessOutput.model_validate(payload)
    except ValidationError:
        return StructuredHarnessOutput()
