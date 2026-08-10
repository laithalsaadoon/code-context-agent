"""Shell tool with STDIO capture and security hardening.

Commands are validated against an allowlist of read-only programs.
Shell operators, path traversal, and write operations are blocked.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, computed_field
from strands import tool

DEFAULT_TIMEOUT = 900
MAX_OUTPUT_SIZE = 100_000

# fmt: off
ALLOWED_PROGRAMS: frozenset[str] = frozenset({
    # File inspection
    "ls", "find", "stat", "file", "du", "wc", "head", "tail", "cat", "less",
    "sort", "uniq", "diff", "comm", "tr", "cut", "paste", "column",
    # Text search & processing
    "grep", "egrep", "rg", "ag", "awk", "sed", "xargs", "jq", "yq",
    # Version control (read-only — subcommands validated separately)
    "git",
    # Language tooling & build inspection
    "python", "python3", "node", "npx", "uv", "cargo", "go", "java", "javac",
    "npm", "pip", "pip3", "make",
    # Encoding & system info
    "base64", "xxd", "hexdump",
    "echo", "printf", "date", "env", "printenv", "which", "type",
    "uname", "id", "whoami", "pwd", "realpath", "dirname", "basename",
    # Analysis tools
    "ast-grep", "repomix", "tree", "tokei", "cloc", "scc",
})

GIT_READ_ONLY: frozenset[str] = frozenset({
    "log", "diff", "show", "blame", "status", "branch", "tag", "remote",
    "rev-parse", "rev-list", "shortlog", "describe", "ls-files", "ls-tree",
    "cat-file", "name-rev", "reflog", "stash", "config",
})
# fmt: on

_DANGEROUS_RE = re.compile(
    r"[;&|]"  # command chaining
    r"|`"  # backtick substitution
    r"|\$[({]"  # $( or ${ expansion
    r"|\beval\b"
    r"|\bexec\b"
    r"|\bsource\b"
    r"|^\s*\.[\s/]"  # dot-sourcing
    r"|\s>>?\s",  # output redirection
)

_SENSITIVE_DIRS = ("/etc", "/root", "/boot", "/usr/sbin", "/proc", "/sys")


class CommandResult(BaseModel):
    """Result of a shell command execution."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    execution_time: float = 0.0

    @computed_field
    @property
    def success(self) -> bool:
        """Command succeeded if exit code is 0 and no stderr."""
        return self.exit_code == 0 and not self.stderr

    @computed_field
    @property
    def status(self) -> str:
        """Status string for display."""
        return "success" if self.success else "error"


def _check_git_readonly(tokens: list[str], start: int) -> str | None:
    """Return error if git subcommand is not read-only."""
    git_flags_with_arg = {"-C", "-c", "--git-dir", "--work-tree"}
    i = start
    subcommand = None
    while i < len(tokens):
        if tokens[i].startswith("-"):
            i += 2 if tokens[i] in git_flags_with_arg else 1
        else:
            subcommand = tokens[i]
            break

    if subcommand and subcommand not in GIT_READ_ONLY:
        return f"Blocked: git {subcommand!r} is not a read-only operation"

    # git config without --get/--list is a write — block it
    if subcommand == "config":
        rest = tokens[i + 1 :] if i + 1 < len(tokens) else []
        read_flags = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin", "--show-scope"}
        if rest and not any(f in read_flags for f in rest):
            return "Blocked: git config writes are not allowed (use --get or --list)"

    return None


def _path_under(path: str, directory: str) -> bool:
    """True if *path* equals or is a child of *directory*."""
    return Path(path).is_relative_to(directory)


def _check_sensitive_paths(tokens: list[str]) -> str | None:
    """Return error if any token targets a sensitive system directory."""
    for token in tokens:
        if token.startswith("/"):
            resolved = str(Path(token).resolve())
            for d in _SENSITIVE_DIRS:
                if _path_under(token, d) or _path_under(resolved, d):
                    return f"Blocked: access to {d} is not allowed"
    return None


def _validate_command(cmd: str) -> str | None:
    """Return None if *cmd* is safe, or an error message if blocked."""
    stripped = cmd.strip()
    if not stripped:
        return "Empty command"

    if _DANGEROUS_RE.search(stripped):
        return f"Blocked: shell operator not allowed: {stripped!r}"

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return f"Blocked: malformed command: {stripped!r}"

    # Skip env-var assignments (FOO=bar cmd ...)
    idx = 0
    while idx < len(tokens) and "=" in tokens[idx]:
        idx += 1
    if idx >= len(tokens):
        return "Empty command after variable assignments"

    program = Path(tokens[idx]).name.lower()

    if program not in ALLOWED_PROGRAMS:
        return f"Blocked: {program!r} is not in the allowed programs list"

    if program == "git":
        if violation := _check_git_readonly(tokens, idx + 1):
            return violation

    return _check_sensitive_paths(tokens)


def _execute(cmd: str, work_dir: str, timeout: int) -> CommandResult:
    """Execute one shell command and return structured result."""
    start = time.time()
    try:
        proc = subprocess.run(
            ["sh", "-c", cmd],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if len(stdout) > MAX_OUTPUT_SIZE:
            stdout = stdout[:MAX_OUTPUT_SIZE] + f"\n... (truncated, {len(proc.stdout)} total chars)"
        if len(stderr) > MAX_OUTPUT_SIZE:
            stderr = stderr[:MAX_OUTPUT_SIZE] + f"\n... (truncated, {len(proc.stderr)} total chars)"
        return CommandResult(
            command=cmd,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            execution_time=time.time() - start,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            command=cmd,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout} seconds",
            execution_time=time.time() - start,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.exception(f"Command execution failed: {cmd}")
        return CommandResult(
            command=cmd,
            exit_code=-1,
            stdout="",
            stderr=str(e),
            execution_time=time.time() - start,
        )


@tool
def shell(
    command: str | list[str],
    work_dir: str | None = None,
    timeout: int | None = None,
    ignore_errors: bool = False,
) -> dict[str, Any]:
    """Execute shell commands with proper STDIO capture.

    USE THIS TOOL:
    - For running read-only shell commands (ls, git log, wc, etc.)
    - When you need command output for analysis

    DO NOT USE:
    - For reading file contents (use read_file_bounded instead)
    - For searching code (use rg_search instead)
    - For modifying files, network access, or running arbitrary scripts

    Security: Commands are validated against an allowlist of read-only programs.
    Shell operators (pipes, redirects, chaining) are blocked. Git commands are
    restricted to read-only subcommands.

    Args:
        command: Shell command string or list of commands to execute sequentially.
        work_dir: Working directory for command execution (default: current dir).
        timeout: Timeout in seconds for command execution (default: 900).
        ignore_errors: If True, continue on errors and return success (default: False).

    Returns:
        Dict with status and content blocks.

    Example:
        >>> shell("ls -la")
        >>> shell(["git status", "git diff"], work_dir="/repo")
    """
    timeout = timeout or DEFAULT_TIMEOUT
    work_dir = work_dir or str(Path.cwd())
    commands = [command] if isinstance(command, str) else command

    results: list[CommandResult] = []
    for cmd in commands:
        violation = _validate_command(cmd)
        if violation:
            logger.warning(f"Shell command blocked: {violation}")
            results.append(CommandResult(command=cmd, exit_code=-1, stdout="", stderr=violation))
            if not ignore_errors:
                break
            continue

        result = _execute(cmd, work_dir, timeout)
        results.append(result)
        if not result.success and not ignore_errors:
            break

    # Build response
    success_count = sum(1 for r in results if r.success)
    content = [
        {
            "text": f"Execution Summary:\nTotal commands: {len(results)}\n"
            f"Successful: {success_count}\nFailed: {len(results) - success_count}",
        },
    ]
    for r in results:
        parts = [f"Command: {r.command}", f"Status: {r.status}", f"Exit Code: {r.exit_code}"]
        if r.stdout:
            parts.append(f"Output:\n{r.stdout}")
        if r.stderr:
            parts.append(f"Error:\n{r.stderr}")
        content.append({"text": "\n".join(parts)})

    has_errors = any(not r.success for r in results)
    return {"status": "error" if has_errors and not ignore_errors else "success", "content": content}
