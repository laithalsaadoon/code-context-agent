"""Security guards: path validation, bounded shell execution, write confinement.

Adapted from the pre-split ``code_context_agent`` package:
- Path validators from ``tools/validation.py``.
- ``run_command`` / ``CommandResult`` from ``tools/shell.py``.
- ``confine_write_path`` is new: it confines artifact writes to an output-dir root.

Domain-agnostic: no use-case vocabulary. Keeps the framework layer testable
without any ``strands`` import.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import TypedDict


class ValidationError(ValueError):
    """Raised when input validation fails."""


def validate_repo_path(path: str) -> Path:
    """Validate a repository path is safe to use.

    Args:
        path: User-provided path string.

    Returns:
        Resolved Path object.

    Raises:
        ValidationError: If the path is dangerous or invalid.

    Example:
        >>> validate_repo_path("/home/user/project")  # doctest: +SKIP
        PosixPath('/home/user/project')
    """
    resolved = Path(path).resolve()

    # Prevent path traversal
    if ".." in path:
        raise ValidationError(f"Path traversal detected: {path}")

    # Prevent system paths
    dangerous = {"/", "/etc", "/usr", "/var", "/bin", "/sbin", "/root", "/boot"}
    if str(resolved) in dangerous:
        raise ValidationError(f"Dangerous system path: {path}")

    # Must exist and be a directory
    if not resolved.exists():
        raise ValidationError(f"Path does not exist: {path}")
    if not resolved.is_dir():
        raise ValidationError(f"Path is not a directory: {path}")

    return resolved


def validate_file_path(path: str, must_exist: bool = True) -> Path:
    """Validate a file path is safe.

    Args:
        path: User-provided file path.
        must_exist: If True, the file must exist.

    Returns:
        Resolved Path object.

    Raises:
        ValidationError: If the path is dangerous or invalid.
    """
    resolved = Path(path).resolve()

    # Prevent path traversal
    if ".." in path:
        raise ValidationError(f"Path traversal detected: {path}")

    # Must exist if required
    if must_exist and not resolved.exists():
        raise ValidationError(f"File does not exist: {path}")

    # Must be a file if it exists
    if resolved.exists() and not resolved.is_file():
        raise ValidationError(f"Path is not a file: {path}")

    return resolved


def validate_glob_pattern(pattern: str) -> str:
    """Validate a glob pattern is safe.

    Args:
        pattern: User-provided glob pattern.

    Returns:
        Validated pattern string.

    Raises:
        ValidationError: If the pattern contains dangerous characters.
    """
    # Prevent command injection via glob
    dangerous_chars = re.compile(r"[;&|`$(){}\\]")
    if dangerous_chars.search(pattern):
        raise ValidationError(f"Invalid characters in pattern: {pattern}")

    # Prevent path traversal
    if ".." in pattern:
        raise ValidationError(f"Path traversal in pattern: {pattern}")

    return pattern


def validate_path_within_repo(path: str, repo_root: str) -> Path:
    """Validate that a path is contained within a repository root.

    Args:
        path: Path to validate.
        repo_root: Repository root path.

    Returns:
        Resolved Path object.

    Raises:
        ValidationError: If the path escapes the repository root.
    """
    resolved = Path(path).resolve()
    root = Path(repo_root).resolve()
    if resolved != root and not str(resolved).startswith(str(root) + "/"):
        raise ValidationError(f"Path {resolved} is outside repository root {root}")
    return resolved


def validate_search_pattern(pattern: str, max_length: int = 1000) -> str:
    """Validate a search pattern (regex) is safe.

    Args:
        pattern: User-provided search pattern.
        max_length: Maximum allowed pattern length.

    Returns:
        Validated pattern string.

    Raises:
        ValidationError: If the pattern is invalid or too long.
    """
    if len(pattern) > max_length:
        raise ValidationError(f"Pattern too long: {len(pattern)} > {max_length}")

    # Try to compile regex to catch syntax errors early
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValidationError(f"Invalid regex pattern: {e}") from e

    return pattern


def confine_write_path(path: str, output_root: str | Path) -> Path:
    """Confine an artifact write target to the output-directory root.

    Guards the artifact-writing tools so a team agent cannot write outside the
    run's output directory. Rejects traversal and absolute escapes.

    Args:
        path: Requested write path (absolute or relative to ``output_root``).
        output_root: The run's output-directory root that writes must stay under.

    Returns:
        Resolved Path object guaranteed to be at or under ``output_root``.

    Raises:
        ValidationError: If the resolved path escapes ``output_root``.
    """
    root = Path(output_root).resolve()
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if resolved != root and not str(resolved).startswith(str(root) + "/"):
        raise ValidationError(f"Write path {resolved} is outside output root {root}")
    return resolved


class CommandResult(TypedDict):
    """Result of a shell command execution."""

    status: str
    stdout: str
    stderr: str
    return_code: int
    truncated: bool


def run_command(
    cmd: str | list[str],
    cwd: str | None = None,
    timeout: int = 120,
    max_output: int = 100_000,
    input_data: str | None = None,
) -> CommandResult:
    """Run a shell command with bounds.

    Uses ``shell=False`` with ``shlex`` parsing for security. For commands
    requiring shell features (pipes, redirects), pass a list like
    ``["sh", "-c", "cmd"]``.

    Args:
        cmd: Command string or list of arguments.
        cwd: Working directory.
        timeout: Maximum execution time in seconds.
        max_output: Maximum characters to capture.
        input_data: Optional string to send to stdin.

    Returns:
        Dict with status, stdout, stderr, return_code, and truncated flag.
    """
    # Convert string to list safely
    cmd_list = shlex.split(cmd) if isinstance(cmd, str) else cmd

    try:
        result = subprocess.run(
            cmd_list,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
            check=False,
        )

        stdout = result.stdout[:max_output]
        truncated = len(result.stdout) > max_output

        if truncated:
            stdout += f"\n... (truncated, {len(result.stdout)} total chars)"

        return {
            "status": "success" if result.returncode == 0 else "error",
            "stdout": stdout,
            "stderr": result.stderr[:10000],
            "return_code": result.returncode,
            "truncated": truncated,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "return_code": -1,
            "truncated": False,
        }
    except (subprocess.SubprocessError, OSError) as e:
        return {
            "status": "error",
            "stdout": "",
            "stderr": str(e),
            "return_code": -1,
            "truncated": False,
        }
