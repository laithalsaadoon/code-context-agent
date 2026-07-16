"""Discovery tools for codebase analysis.

This module provides tools for file discovery, manifest creation, and
context bundling using external tools like ripgrep and repomix.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from loguru import logger
from strands import tool

from strands_teams.security import (
    ValidationError,
    run_command,
    validate_file_path,
    validate_repo_path,
    validate_search_pattern,
)

from ..config import DEFAULT_OUTPUT_DIR


@tool
def create_file_manifest(repo_path: str) -> str:
    """Create ignore-aware file manifest using ripgrep.

    USE THIS TOOL: As the FIRST step in any codebase analysis workflow.
    Creates a safe inventory of files without reading contents.

    DO NOT USE:
    - If you already have a manifest from a previous call in this session
    - If .code-context/files.all.txt exists and is recent

    Generates a list of all files in the repository, respecting .gitignore
    and skipping hidden/binary files. Output is written to .code-context/files.all.txt.

    Args:
        repo_path: Absolute path to the repository root.

    Returns:
        JSON with:
        - manifest_path: Path to .code-context/files.all.txt
        - file_count: Number of files found (typical: 100-5000)

    Output Size: ~100 bytes JSON + manifest file (~50 bytes per file path)

    Common Errors:
        - "rg not found": ripgrep not installed (install with: cargo install ripgrep)
        - Empty manifest: Check if repo_path is correct and contains files
        - Permission denied: Ensure read access to the repository

    Example success:
        {"status": "success", "manifest_path": "/repo/.code-context/files.all.txt", "file_count": 847}
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    agent_dir = repo / DEFAULT_OUTPUT_DIR
    agent_dir.mkdir(exist_ok=True)
    manifest_path = agent_dir / "files.all.txt"

    # Use ripgrep --files which respects .gitignore
    # Use sh -c for shell redirection with shlex.quote for path safety
    result = run_command(
        ["sh", "-c", f"rg --files > {shlex.quote(str(manifest_path))}"],
        cwd=str(repo),
    )

    if result["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "error": result["stderr"],
            },
        )

    # Count files
    try:
        file_count = sum(1 for _ in manifest_path.open())
    except OSError:
        file_count = 0

    logger.info(f"Created file manifest with {file_count} files")

    return json.dumps(
        {
            "status": "success",
            "manifest_path": str(manifest_path),
            "file_count": file_count,
        },
    )


@tool
def repomix_orientation(
    repo_path: str,
    token_threshold: int = 300,
    max_file_count: int = 10000,
) -> str:
    """Generate token-aware orientation snapshot without file contents.

    USE THIS TOOL: After create_file_manifest to understand codebase structure
    and identify high-complexity areas via token distribution.

    DO NOT USE:
    - If repo has >10K files (will auto-skip with recommendation)
    - If you only need to find specific files (use rg_search instead)
    - If .code-context/CONTEXT.orientation.md exists and repo hasn't changed

    Uses repomix to create a metadata overview including directory structure
    and token distribution tree. Helps identify where code complexity lies
    without bundling actual content.

    Args:
        repo_path: Absolute path to the repository root.
        token_threshold: Minimum tokens to show in tree (filters noise).
        max_file_count: Maximum files allowed before skipping (default 10000).

    Returns:
        JSON with output path and status, or skipped status for large repos.

    Output Size:
        - Small repos (<500 files): ~5-20KB markdown
        - Medium repos (500-2000 files): ~20-100KB markdown
        - Large repos (2000-10000 files): ~100-500KB markdown
        - Execution time: 5-60 seconds depending on repo size

    Common Errors:
        - "repomix not found": Install with npm install -g repomix
        - "skipped" status: Repo exceeds max_file_count, use --include patterns
        - Timeout after 180s: Repo too large, reduce scope with glob patterns

    Example success:
        {"status": "success", "output_path": "/repo/.code-context/CONTEXT.orientation.md"}

    Example skipped:
        {"status": "skipped", "reason": "Repository has 15000 files (max: 10000)"}
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    agent_dir = repo / DEFAULT_OUTPUT_DIR
    agent_dir.mkdir(exist_ok=True)
    output_path = agent_dir / "CONTEXT.orientation.md"

    # Pre-check file count to avoid long-running operations on large repos
    # Use cwd instead of embedding path in command to avoid shell escaping issues
    count_result = run_command(
        ["sh", "-c", "rg --files | wc -l"],
        cwd=str(repo),
        timeout=10,
    )

    if count_result["status"] == "success":
        try:
            file_count = int(count_result["stdout"].strip())
            if file_count > max_file_count:
                logger.warning(f"Repository has {file_count} files, exceeding max of {max_file_count}")
                return json.dumps(
                    {
                        "status": "skipped",
                        "reason": f"Repository has {file_count} files (max: {max_file_count})",
                        "recommendation": "Use --include patterns to limit scope",
                    },
                )
        except ValueError:
            pass  # Proceed if we can't parse count

    cmd = [
        "repomix",
        "--no-files",
        "--style",
        "markdown",
        "--token-count-tree",
        str(token_threshold),
        "-o",
        str(output_path),
        str(repo),
    ]

    result = run_command(cmd, cwd=str(repo), timeout=180)

    if result["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "error": result["stderr"],
                "stdout": result["stdout"],
            },
        )

    logger.info(f"Created orientation snapshot: {output_path}")

    return json.dumps(
        {
            "status": "success",
            "output_path": str(output_path),
        },
    )


@tool
def repomix_bundle(
    file_list_path: str,
    output_path: str,
    compress: bool = True,
    include_diffs: bool = False,
    include_logs: bool = False,
    include_logs_count: int = 50,
    split_size: str | None = None,
    truncate_base64: bool = True,
    remove_comments: bool = False,
) -> str:
    """Pack curated files into markdown context bundle.

    USE THIS TOOL: When you have a curated list of file paths and want to
    bundle their contents into a single markdown file for analysis.

    DO NOT USE:
    - For initial exploration (use repomix_orientation first)
    - If you don't have a file list yet (use write_file_list first)

    Takes a list of file paths and bundles their contents into a single
    markdown file using repomix. The --stdin flag reads paths from the
    provided file list.

    Args:
        file_list_path: Path to file containing paths to pack (one per line).
        output_path: Output markdown file path.
        compress: Use tree-sitter compression to reduce size.
        include_diffs: Include git working tree + staged changes in the bundle.
        include_logs: Include recent git commit history in the bundle.
        include_logs_count: Number of recent commits to include (only when include_logs=True).
        split_size: Split output into chunks of this size (e.g., "500kb", "2mb").
            Useful for very large bundles that exceed context windows.
        truncate_base64: Truncate base64-encoded data to reduce token waste (default True).
        remove_comments: Strip comments from source code for minimal structural output.

    Returns:
        JSON with output path, file size, and status.

    Output Size: Varies by file count and content. Compressed bundles are ~30-50% smaller.

    Common Errors:
        - "File list not found": Ensure file_list_path exists and has content
        - Timeout after 300s: Too many/large files, reduce scope or use split_size
        - "repomix not found": Install with npm install -g repomix

    Example:
        >>> result = repomix_bundle(".code-context/files.targeted.txt", ".code-context/CONTEXT.bundle.md")
        >>> result = repomix_bundle(
        ...     ".code-context/files.targeted.txt",
        ...     ".code-context/CONTEXT.bundle.md",
        ...     include_diffs=True,
        ...     include_logs=True,
        ...     include_logs_count=20,
        ... )
    """
    try:
        file_list = validate_file_path(file_list_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    try:
        output = validate_file_path(output_path, must_exist=False)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})

    if not file_list.exists():
        return json.dumps(
            {
                "status": "error",
                "error": f"File list not found: {file_list}",
            },
        )

    # Build repomix argument list
    repomix_parts = ["--stdin", "--style", "markdown", "--output-show-line-numbers"]

    if compress:
        repomix_parts.append("--compress")
    if include_diffs:
        repomix_parts.append("--include-diffs")
    if include_logs:
        repomix_parts.append("--include-logs")
        repomix_parts.extend(["--include-logs-count", str(include_logs_count)])
    if split_size is not None:
        repomix_parts.extend(["--split-output", split_size])
    if truncate_base64:
        repomix_parts.append("--truncate-base64")
    if remove_comments:
        repomix_parts.append("--remove-comments")

    # Read file list content and pipe via stdin (no shell)
    file_list_content = file_list.read_text()
    cmd = ["repomix", *repomix_parts, "-o", str(output)]

    result = run_command(cmd, timeout=300, input_data=file_list_content)

    if result["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "error": result["stderr"],
                "stdout": result["stdout"],
            },
        )

    # Get file size
    try:
        file_size = output.stat().st_size
    except OSError:
        file_size = 0

    logger.info(f"Created context bundle: {output} ({file_size} bytes)")

    return json.dumps(
        {
            "status": "success",
            "output_path": str(output),
            "file_size_bytes": file_size,
        },
    )


@tool
def repomix_bundle_with_context(
    repo_path: str,
    output_path: str,
    include_patterns: str | None = None,
    compress: bool = True,
    include_diffs: bool = True,
    include_logs: bool = True,
    include_logs_count: int = 50,
    truncate_base64: bool = True,
) -> str:
    """Bundle repository files with git context (diffs and logs).

    USE THIS TOOL: When you need a comprehensive snapshot of a repository
    that includes both file contents and recent git activity. Combines
    file bundling with git diffs and commit history in a single call.

    DO NOT USE:
    - For initial exploration (use repomix_orientation first)
    - If you only need file contents without git context (use repomix_bundle)
    - For very large repos without include_patterns (will be slow/huge)

    Unlike repomix_bundle which reads from a file list via --stdin, this tool
    operates directly on a repo path with optional glob include patterns.
    It always includes git context (diffs and/or logs) to provide a
    change-aware view of the codebase.

    Args:
        repo_path: Absolute path to the repository root.
        output_path: Output markdown file path.
        include_patterns: Comma-separated glob patterns to include (e.g., "src/**/*.py,tests/**/*.py").
            If None, includes all files (respecting .gitignore).
        compress: Use tree-sitter compression to reduce size.
        include_diffs: Include git working tree + staged changes (default True).
        include_logs: Include recent git commit history (default True).
        include_logs_count: Number of recent commits to include (only when include_logs=True).
        truncate_base64: Truncate base64-encoded data to reduce token waste (default True).

    Returns:
        JSON with output path, file size, and status.

    Output Size:
        - Small repos with few changes: ~50-200KB
        - Medium repos with active changes: ~200KB-1MB
        - Execution time: 10-120 seconds depending on repo size and history

    Common Errors:
        - "repomix not found": Install with npm install -g repomix
        - Timeout after 300s: Use include_patterns to narrow scope
        - Large output: Reduce include_logs_count or use include_patterns

    Example:
        >>> result = repomix_bundle_with_context(
        ...     "/repo",
        ...     ".code-context/CONTEXT.git-aware.md",
        ...     include_patterns="src/**/*.py",
        ...     include_logs_count=20,
        ... )
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    try:
        output = validate_file_path(output_path, must_exist=False)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})

    # Build repomix command
    cmd_parts = [
        "repomix",
        "--style",
        "markdown",
        "--output-show-line-numbers",
    ]

    if compress:
        cmd_parts.append("--compress")
    if include_diffs:
        cmd_parts.append("--include-diffs")
    if include_logs:
        cmd_parts.append("--include-logs")
        cmd_parts.extend(["--include-logs-count", str(include_logs_count)])
    if truncate_base64:
        cmd_parts.append("--truncate-base64")
    if include_patterns:
        cmd_parts.extend(["--include", include_patterns])

    cmd_parts.extend(["-o", str(output)])
    cmd_parts.append(str(repo))

    result = run_command(cmd_parts, cwd=str(repo), timeout=300)

    if result["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "error": result["stderr"],
                "stdout": result["stdout"],
            },
        )

    # Get file size
    try:
        file_size = output.stat().st_size
    except OSError:
        file_size = 0

    logger.info(f"Created git-aware context bundle: {output} ({file_size} bytes)")

    return json.dumps(
        {
            "status": "success",
            "output_path": str(output),
            "file_size_bytes": file_size,
        },
    )


@tool
def repomix_json_export(repo_path: str, include_patterns: str | None = None) -> str:
    """Export repository structure as JSON for programmatic analysis.

    USE THIS TOOL: When you need structured data about the repository
    rather than a human-readable markdown bundle. Useful for getting
    exact file counts, token distributions, and directory structure as
    machine-parseable data.

    DO NOT USE:
    - For reading file contents (use repomix_bundle or read_file_bounded)
    - For initial high-level overview (use repomix_orientation)
    - If you only need file paths (use create_file_manifest)

    Uses repomix --style json to produce structured output that can be
    parsed programmatically. The output includes file metadata without
    file contents (--no-files), keeping the output compact.

    Args:
        repo_path: Absolute path to the repository root.
        include_patterns: Comma-separated glob patterns to include (e.g., "src/**/*.py,tests/**/*.py").

    Returns:
        JSON with output_path and parsed metadata (total_files, total_tokens).

    Output Size: ~200 bytes JSON response + JSON file on disk (~1-50KB depending on repo).

    Common Errors:
        - "repomix not found": Install with npm install -g repomix
        - Timeout after 180s: Use include_patterns to narrow scope
        - JSON parse error: repomix output format may have changed

    Example success:
        {"status": "success", "output_path": "/repo/.code-context/structure.json",
         "total_files": 247, "total_tokens": 185420}

    Example:
        >>> result = repomix_json_export("/repo", include_patterns="src/**/*.py,tests/**/*.py")
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    agent_dir = repo / DEFAULT_OUTPUT_DIR
    agent_dir.mkdir(exist_ok=True)
    output_path = agent_dir / "structure.json"

    # Build repomix command for JSON export
    cmd_parts = [
        "repomix",
        "--style",
        "json",
        "--no-files",
        "-o",
        str(output_path),
    ]

    if include_patterns:
        cmd_parts.extend(["--include", include_patterns])

    cmd_parts.append(str(repo))

    result = run_command(cmd_parts, cwd=str(repo), timeout=180)

    if result["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "error": result["stderr"],
                "stdout": result["stdout"],
            },
        )

    # Parse the JSON output to extract metadata
    total_files = 0
    total_tokens = 0

    try:
        with output_path.open(encoding="utf-8") as f:
            data = json.load(f)

        # Extract metadata from repomix JSON structure
        if isinstance(data, dict):
            total_files = data.get("totalFiles", data.get("total_files", 0))
            total_tokens = data.get("totalTokens", data.get("total_tokens", 0))
    except (OSError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not parse repomix JSON metadata: {e}")

    logger.info(f"Exported JSON structure: {output_path} ({total_files} files, {total_tokens} tokens)")

    return json.dumps(
        {
            "status": "success",
            "output_path": str(output_path),
            "total_files": total_files,
            "total_tokens": total_tokens,
        },
    )


@tool
def repomix_compressed_signatures(
    repo_path: str,
    include_patterns: str | None = None,
    output_path: str | None = None,
) -> str:
    """Extract code signatures and types from a repository using Tree-sitter compression.

    Produces a minimal structural view: function/method signatures, class declarations,
    interface/type definitions, imports — with implementation bodies stripped.
    Also removes comments and empty lines for maximum token efficiency.

    Supported languages: JavaScript, TypeScript, Python, Go, Rust, Java, C#, Ruby,
    PHP, Swift, C, C++, CSS, Solidity, Vue, Dart.

    USE THIS TOOL:
    - For a quick structural overview of specific directories or file patterns
    - When you need to understand the API surface without reading implementations
    - To identify function signatures and types across a large codebase efficiently

    DO NOT USE:
    - If you need full implementation details (use repomix_bundle)
    - For initial codebase overview (use repomix_orientation first)
    - For non-code files (compression only works on supported languages)

    Args:
        repo_path: Absolute path to the repository root.
        include_patterns: Comma-separated glob patterns to include (e.g., "src/**/*.py,lib/**/*.ts").
        output_path: Output path. Defaults to .code-context/CONTEXT.signatures.md

    Returns:
        JSON with output path, file size, and status.

    Output Size:
        - Typically 60-80% smaller than full bundles due to body stripping + comment removal
        - Small repos: ~5-30KB
        - Medium repos: ~30-150KB
        - Execution time: 5-60 seconds

    Common Errors:
        - "repomix not found": Install with npm install -g repomix
        - Timeout after 180s: Use include_patterns to narrow scope
        - Empty output: No supported language files matched

    Example:
        >>> result = repomix_compressed_signatures("/repo", include_patterns="src/**/*.py")
        >>> result = repomix_compressed_signatures("/repo")  # All files
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    agent_dir = repo / DEFAULT_OUTPUT_DIR
    agent_dir.mkdir(exist_ok=True)

    if output_path is None:
        output = agent_dir / "CONTEXT.signatures.md"
    else:
        output = Path(output_path).resolve()

    # Build repomix command for compressed signatures
    cmd_parts = [
        "repomix",
        "--compress",
        "--remove-comments",
        "--remove-empty-lines",
        "--style",
        "markdown",
        "--output-show-line-numbers",
        "-o",
        str(output),
    ]

    if include_patterns:
        cmd_parts.extend(["--include", include_patterns])

    cmd_parts.append(str(repo))

    result = run_command(cmd_parts, cwd=str(repo), timeout=180)

    if result["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "error": result["stderr"],
                "stdout": result["stdout"],
            },
        )

    # Get file size
    try:
        file_size = output.stat().st_size
    except OSError:
        file_size = 0

    logger.info(f"Created compressed signatures: {output} ({file_size} bytes)")

    return json.dumps(
        {
            "status": "success",
            "output_path": str(output),
            "file_size_bytes": file_size,
        },
    )


@tool
def repomix_split_bundle(
    file_list_path: str,
    output_dir: str,
    max_size: str = "500kb",
    compress: bool = True,
) -> str:
    """Pack files into multiple split bundles for large codebases.

    When a codebase is too large for a single context window, this tool
    splits the output into numbered files (e.g., output.1.md, output.2.md).

    USE THIS TOOL:
    - When a previous repomix_bundle call produced output exceeding context limits
    - For large codebases where you want to process files in manageable chunks
    - When you need to parallelize analysis across multiple context windows

    DO NOT USE:
    - For small repos that fit in a single bundle (use repomix_bundle)
    - For initial exploration (use repomix_orientation first)
    - If you don't have a file list yet (use write_file_list first)

    Args:
        file_list_path: Path to file containing paths to pack (one per line).
        output_dir: Directory for split output files.
        max_size: Maximum size per file (e.g., "500kb", "1mb", "2mb").
        compress: Use tree-sitter compression.

    Returns:
        JSON with output directory, file count, and individual file paths.

    Output Size: Each split file will be at most max_size. Total output depends on input.

    Common Errors:
        - "File list not found": Ensure file_list_path exists and has content
        - Timeout after 300s: Reduce the number of files in the list
        - "repomix not found": Install with npm install -g repomix

    Example:
        >>> result = repomix_split_bundle(".code-context/files.all.txt", ".code-context/splits/", max_size="1mb")
    """
    try:
        file_list = validate_file_path(file_list_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    try:
        validate_file_path(output_dir, must_exist=False)
    except ValidationError:
        pass  # output_dir is a directory, not a file — just validate traversal
    out_dir = Path(output_dir).resolve()

    if not file_list.exists():
        return json.dumps(
            {
                "status": "error",
                "error": f"File list not found: {file_list}",
            },
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    # repomix --split-output writes numbered files into the output directory.
    # We set -o to a base path inside output_dir; repomix appends .1.md, .2.md, etc.
    base_output = out_dir / "output.md"

    # Build repomix argument list
    repomix_parts = [
        "--stdin",
        "--style",
        "markdown",
        "--output-show-line-numbers",
        "--split-output",
        max_size,
    ]

    if compress:
        repomix_parts.append("--compress")

    # Read file list content and pipe via stdin (no shell)
    file_list_content = file_list.read_text()
    cmd = ["repomix", *repomix_parts, "-o", str(base_output)]

    result = run_command(cmd, timeout=300, input_data=file_list_content)

    if result["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "error": result["stderr"],
                "stdout": result["stdout"],
            },
        )

    # List the resulting split files
    split_files = sorted(str(p) for p in out_dir.iterdir() if p.is_file() and p.suffix == ".md")

    logger.info(f"Created {len(split_files)} split bundles in {out_dir}")

    return json.dumps(
        {
            "status": "success",
            "output_dir": str(out_dir),
            "file_count": len(split_files),
            "files": split_files,
        },
    )


def _rg_count(
    pattern: str,
    repo: Path,
    *,
    glob: str | None = None,
    file_type: str | None = None,
) -> str:
    """Run rg --count and return per-file counts with exact totals."""
    cmd: list[str] = ["rg", "--count"]

    if glob:
        cmd.extend(["-g", glob])
    if file_type:
        cmd.extend(["-t", file_type])

    cmd.append(pattern)
    cmd.append(str(repo))

    try:
        proc_result = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "error": "Command timed out after 60 seconds"})
    except (subprocess.SubprocessError, OSError) as e:
        return json.dumps({"status": "error", "error": str(e)})

    if proc_result.returncode not in (0, 1):
        return json.dumps({"status": "error", "error": proc_result.stderr[:10000]})

    files: dict[str, int] = {}
    total = 0
    for line in proc_result.stdout.strip().splitlines():
        if not line:
            continue
        # rg --count outputs "path:count" — split on last colon
        sep = line.rfind(":")
        if sep == -1:
            continue
        path = line[:sep]
        try:
            count = int(line[sep + 1 :])
        except ValueError:
            continue
        # Make path relative to repo if possible
        try:
            rel = str(Path(path).relative_to(repo))
        except ValueError:
            rel = path
        files[rel] = count
        total += count

    return json.dumps(
        {
            "status": "success",
            "pattern": pattern,
            "total_count": total,
            "files": files,
            "file_count": len(files),
        },
    )


@tool
def rg_search(
    pattern: str,
    repo_path: str,
    glob: str | None = None,
    file_type: str | None = None,
    max_count: int = 100,
    context_lines: int = 0,
    count_only: bool = False,
) -> str:
    """Search for pattern in repository using ripgrep.

    USE THIS TOOL:
    - To find entrypoints (e.g., "def main", "createServer", "app.listen")
    - To locate specific functions, classes, or patterns
    - To discover imports and dependencies
    - When you know WHAT to search for but not WHERE
    - With count_only=True for precise occurrence counts across the entire codebase

    DO NOT USE:
    - For listing all files (use create_file_manifest instead)
    - For reading file contents (use read_file_bounded instead)
    - For structural analysis (use gitnexus_query or gitnexus_context instead)

    Args:
        pattern: Regex pattern to search for.
        repo_path: Repository root path.
        glob: Optional glob filter (e.g., "*.py", "src/**/*.ts").
        file_type: Optional file type (e.g., "py", "ts", "js").
        max_count: Maximum matches to return per file (default 100).
        context_lines: Lines of context around matches (0-5 recommended).
        count_only: Return only match counts per file (no match details).
            Uses rg --count for exact totals without truncation.

    Returns:
        JSON with matches array containing path, line_number, and lines.
        When count_only=True: JSON with total_count and per-file counts.

    Output Size: ~200 bytes per match. Results capped at 500 lines.
        count_only mode: ~50 bytes per file, no cap.

    Pattern Tips:
        - Literal strings: "createServer" (no regex escaping needed)
        - Function definitions: "def \\w+\\(" or "function \\w+\\("
        - Class definitions: "class \\w+"
        - Imports: "^import|^from .* import"
        - Case insensitive: Use "(?i)pattern"

    Common Errors:
        - "rg not found": ripgrep not installed
        - Empty matches with valid pattern: Try broader glob or check file_type
        - Regex syntax error: Escape special chars like ( ) [ ] { }

    Example success:
        {"status": "success", "pattern": "def main", "matches": [...], "match_count": 3}

    Example count_only:
        {"status": "success", "pattern": "TODO", "total_count": 42,
         "files": {"src/main.py": 12, "src/utils.py": 30}, "file_count": 2}

    Example searches:
        >>> rg_search("def main", "/repo", glob="*.py")  # Python entrypoints
        >>> rg_search("createServer", "/repo", file_type="ts")  # TS server setup
        >>> rg_search("TODO|FIXME", "/repo", count_only=True)  # Exact count across repo
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    try:
        validate_search_pattern(pattern)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})

    if count_only:
        return _rg_count(pattern, repo, glob=glob, file_type=file_type)

    # Build ripgrep command parts for shell execution with proper escaping
    cmd_parts = ["rg", "--json", f"-m {max_count}"]

    if glob:
        cmd_parts.append(f"-g {shlex.quote(glob)}")
    if file_type:
        cmd_parts.append(f"-t {shlex.quote(file_type)}")
    if context_lines > 0:
        cmd_parts.append(f"-C {context_lines}")

    cmd_parts.append(shlex.quote(pattern))
    cmd_parts.append(shlex.quote(str(repo)))

    # Run rg directly without shell pipe to avoid SIGPIPE/broken pipe errors
    # Use subprocess directly for proper STDIO capture
    full_cmd = " ".join(cmd_parts)
    try:
        proc_result = subprocess.run(
            ["sh", "-c", full_cmd],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
        )
        # Limit output to 500 lines after capture to avoid broken pipe
        stdout_lines = proc_result.stdout.split("\n")[:500]
        result = {
            "status": "success" if proc_result.returncode in (0, 1) else "error",
            "stdout": "\n".join(stdout_lines),
            "stderr": proc_result.stderr[:10000] if proc_result.stderr else "",
            "return_code": proc_result.returncode,
        }
    except subprocess.TimeoutExpired:
        result = {
            "status": "error",
            "stdout": "",
            "stderr": "Command timed out after 60 seconds",
            "return_code": -1,
        }
    except (subprocess.SubprocessError, OSError) as e:
        result = {
            "status": "error",
            "stdout": "",
            "stderr": str(e),
            "return_code": -1,
        }

    # Parse JSON lines output
    matches = []
    if result["stdout"]:
        for line in result["stdout"].strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "match":
                    match_data = data.get("data", {})
                    matches.append(
                        {
                            "path": match_data.get("path", {}).get("text", ""),
                            "line_number": match_data.get("line_number"),
                            "lines": match_data.get("lines", {}).get("text", ""),
                        },
                    )
            except json.JSONDecodeError:
                continue

    return json.dumps(
        {
            "status": "success" if result["return_code"] in (0, 1) else "error",  # rg returns 1 for no matches
            "pattern": pattern,
            "matches": matches,
            "match_count": len(matches),
        },
    )


@tool
def write_file_list(file_paths: list[str], output_path: str) -> str:
    """Write a list of file paths to a file for repomix bundling.

    Use this to create the curated file list before calling repomix_bundle.

    Args:
        file_paths: List of file paths to include in the bundle.
        output_path: Path to write the file list.

    Returns:
        JSON with output path and file count.

    Example:
        >>> result = write_file_list(["src/main.ts", "src/utils.ts"], ".code-context/files.targeted.txt")
    """
    try:
        output = validate_file_path(output_path, must_exist=False)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})
    output.parent.mkdir(parents=True, exist_ok=True)

    # Deduplicate and sort
    unique_paths = sorted(set(file_paths))

    with output.open("w") as f:
        for path in unique_paths:
            f.write(f"{path}\n")

    logger.info(f"Wrote {len(unique_paths)} paths to {output}")

    return json.dumps(
        {
            "status": "success",
            "output_path": str(output),
            "file_count": len(unique_paths),
        },
    )


@tool
def write_file(file_path: str, content: str) -> str:
    """Write content to a file in the output directory.

    USE THIS TOOL:
    - To write CONTEXT.md and other analysis output files
    - To save narrated context, summaries, or generated documentation
    - For any file that needs to be created or overwritten in .code-context/

    DO NOT USE:
    - For writing file lists (use write_file_list instead)
    - For writing to paths outside the analysis output directory

    Security: Only allows writing to paths within the .code-context/ output
    directory to prevent unintended modifications to the analyzed repository.

    Args:
        file_path: Absolute path to the file to write. Must be within a .code-context/ directory.
        content: String content to write to the file.

    Returns:
        JSON with status, path, and bytes written.

    Example:
        >>> write_file("/repo/.code-context/CONTEXT.md", "# Project Context\\n\\n## Summary\\n...")
    """
    try:
        path = validate_file_path(file_path, must_exist=False)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})

    # Security: only allow writes within a .code-context/ directory
    parts = path.parts
    if ".code-context" not in parts:
        return json.dumps(
            {
                "status": "error",
                "error": f"Write denied: {path} is not within a .code-context/ directory",
            },
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return json.dumps({"status": "error", "error": str(e)})

    logger.info(f"Wrote {len(content)} bytes to {path}")

    return json.dumps(
        {
            "status": "success",
            "path": str(path),
            "bytes_written": len(content),
        },
    )


@tool
def read_file_bounded(file_path: str, max_lines: int = 500, start_line: int = 1) -> str:
    """Read a file with bounded output for safe analysis.

    USE THIS TOOL:
    - To deeply read and understand business logic files identified by graph analysis,
      LSP, or AST-grep. Essential for Phase 6.5 (Deep Read).
    - To read a SINGLE specific file when you know the exact path
    - To inspect implementation details after finding via rg_search
    - To read configuration files (package.json, pyproject.toml, etc.)
    - When you need line numbers for subsequent LSP calls
    - For files >500 lines, paginate using start_line (e.g., read 1-500, then 501-1000)

    DO NOT USE:
    - To read multiple files at once (use repomix_bundle instead)
    - For initial exploration before Phase 3 (use repomix_orientation first)
    - For files >500 lines without specifying start_line for pagination

    Reads file contents with line limits to prevent token overflow.
    Includes line numbers formatted as "  123| code here".

    Args:
        file_path: Absolute path to the file.
        max_lines: Maximum lines to read (default 500, reduce for large files).
        start_line: Starting line number (1-indexed, use for pagination).

    Returns:
        JSON with content (with line numbers), path, lines_read, and truncated flag.

    Output Size: ~80 bytes per line average. 500 lines = ~40KB.

    Common Errors:
        - "File not found": Check path is absolute and file exists
        - "truncated": true: File has more lines, use start_line to paginate
        - UnicodeDecodeError: File is binary, not suitable for text reading

    Example success:
        {"status": "success", "path": "/repo/src/main.py", "content": "     1| ...",
         "start_line": 1, "lines_read": 150, "truncated": false}

    Example pagination (reading lines 500-1000):
        >>> read_file_bounded("/repo/large_file.py", max_lines=500, start_line=500)
    """
    try:
        path = validate_file_path(file_path)
    except ValidationError as e:
        return json.dumps({"status": "error", "error": str(e)})

    if not path.exists():
        return json.dumps(
            {
                "status": "error",
                "error": f"File not found: {path}",
            },
        )

    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f, 1):
                if i < start_line:
                    continue
                if i >= start_line + max_lines:
                    break
                lines.append(f"{i:6d}| {line.rstrip()}")

        content = "\n".join(lines)
        total_lines_read = len(lines)

        return json.dumps(
            {
                "status": "success",
                "path": str(path),
                "content": content,
                "start_line": start_line,
                "lines_read": total_lines_read,
                "truncated": total_lines_read >= max_lines,
            },
        )
    except (OSError, ValueError) as e:
        return json.dumps(
            {
                "status": "error",
                "error": str(e),
            },
        )
