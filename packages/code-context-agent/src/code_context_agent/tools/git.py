"""Git history tools for codebase context analysis.

This module provides tools for extracting contextual information from git history:
- Files changed together (coupling detection)
- Commit history and messages
- Diffs for understanding code evolution
- Blame information for authorship context

These tools help understand:
- Which files are coupled (change together frequently)
- How code has evolved over time
- Who has worked on what areas
- The intent behind changes (via commit messages)
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger
from strands import tool

from strands_teams.security import ValidationError, run_command, validate_repo_path

from .shell import ToolResult


@tool
def git_files_changed_together(
    repo_path: str,
    file_path: str,
    limit: int = 100,
) -> str:
    """Find files that frequently change together with a given file (coupling detection).

    USE THIS TOOL:
    - To identify tightly coupled files that may need to change together
    - To understand implicit dependencies not captured by imports
    - To find related files when making changes
    - To detect architectural coupling patterns

    DO NOT USE:
    - For untracked files (not yet in git)
    - For files with no commit history

    Analyzes git history to find files that appear in the same commits
    as the target file, ranked by co-occurrence frequency.

    Args:
        repo_path: Absolute path to the repository root.
        file_path: Path to the file (relative to repo root or absolute).
        limit: Maximum number of commits to analyze (default 100).

    Returns:
        JSON with:
        - cochanged_files: List of {path, count, percentage} sorted by frequency
        - total_commits: Number of commits analyzed
        - file_path: The analyzed file

    Output Size: ~100 bytes per co-changed file.

    Example success:
        {"status": "success", "file_path": "src/auth.py", "total_commits": 45,
         "cochanged_files": [{"path": "src/user.py", "count": 20, "percentage": 44.4}, ...]}

    Example patterns detected:
        - High coupling (>50%): Files should possibly be merged or abstracted
        - Medium coupling (20-50%): Normal feature-level coupling
        - Low coupling (<20%): Incidental changes, less significant
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return ToolResult.error(str(e)).to_json()

    # Normalize file path to be relative to repo
    if Path(file_path).is_absolute():
        try:
            file_path = str(Path(file_path).relative_to(repo))
        except ValueError:
            return ToolResult.error(f"File path {file_path} is not within repo {repo}").to_json()

    # Get commits that touched this file
    commits_cmd = [
        "git",
        "log",
        f"-n{limit}",
        "--pretty=format:%H",
        "--",
        file_path,
    ]
    commits_result = run_command(commits_cmd, cwd=str(repo))

    if commits_result["status"] != "success":
        return ToolResult.error(
            f"Failed to get commits: {commits_result['stderr']}",
        ).to_json()

    commit_hashes = [h.strip() for h in commits_result["stdout"].strip().split("\n") if h.strip()]

    if not commit_hashes:
        return ToolResult.success(
            file_path=file_path,
            total_commits=0,
            cochanged_files=[],
            note="No commits found for this file",
        ).to_json()

    # Get files changed in each commit
    cochange_counter: Counter[str] = Counter()
    total_commits = len(commit_hashes)

    for commit_hash in commit_hashes:
        files_cmd = [
            "git",
            "show",
            "--pretty=format:",
            "--name-only",
            commit_hash,
        ]
        files_result = run_command(files_cmd, cwd=str(repo), timeout=30)

        if files_result["status"] == "success":
            changed_files = [f.strip() for f in files_result["stdout"].strip().split("\n") if f.strip()]
            # Exclude the target file itself
            other_files = [f for f in changed_files if f != file_path]
            cochange_counter.update(other_files)

    # Build ranked list
    cochanged_files = [
        {
            "path": path,
            "count": count,
            "percentage": round(100 * count / total_commits, 1),
        }
        for path, count in cochange_counter.most_common(50)
    ]

    logger.info(f"Found {len(cochanged_files)} co-changed files for {file_path}")

    return ToolResult.success(
        file_path=file_path,
        total_commits=total_commits,
        cochanged_files=cochanged_files,
    ).to_json()


@tool
def git_file_history(
    repo_path: str,
    file_path: str,
    limit: int = 20,
) -> str:
    """Get commit history for a specific file.

    USE THIS TOOL:
    - To understand how a file has evolved over time
    - To find when specific changes were introduced
    - To identify who has worked on a file
    - To trace the intent behind changes via commit messages

    DO NOT USE:
    - For repository-wide history (use git_recent_commits instead)
    - For files not yet tracked by git

    Returns recent commits that touched the specified file, including
    commit messages which often explain the "why" behind changes.

    Args:
        repo_path: Absolute path to the repository root.
        file_path: Path to the file (relative to repo root or absolute).
        limit: Maximum commits to return (default 20).

    Returns:
        JSON with commits array containing hash, author, date, and message.

    Output Size: ~200 bytes per commit.

    Example success:
        {"status": "success", "file_path": "src/main.py",
         "commits": [{"hash": "abc123", "author": "dev@example.com",
                      "date": "2024-01-15", "message": "Fix auth bug"}]}
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return ToolResult.error(str(e)).to_json()

    # Normalize file path
    if Path(file_path).is_absolute():
        try:
            file_path = str(Path(file_path).relative_to(repo))
        except ValueError:
            return ToolResult.error(f"File path {file_path} is not within repo {repo}").to_json()

    cmd = [
        "git",
        "log",
        f"-n{limit}",
        "--pretty=format:%H|%ae|%as|%s",
        "--",
        file_path,
    ]

    result = run_command(cmd, cwd=str(repo))

    if result["status"] != "success":
        return ToolResult.error(f"Failed to get history: {result['stderr']}").to_json()

    commits = []
    for line in result["stdout"].strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) >= 4:
            commits.append(
                {
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                },
            )

    return ToolResult.success(
        file_path=file_path,
        commits=commits,
        commit_count=len(commits),
    ).to_json()


@tool
def git_recent_commits(
    repo_path: str,
    limit: int = 30,
    branch: str = "HEAD",
) -> str:
    """Get recent commits from the repository.

    USE THIS TOOL:
    - To understand recent development activity
    - To identify active areas of the codebase
    - To see the general direction of development
    - To find commits relevant to a feature or bug

    DO NOT USE:
    - For file-specific history (use git_file_history instead)

    Returns recent commits from the specified branch with messages
    that provide context about development activity.

    Args:
        repo_path: Absolute path to the repository root.
        limit: Maximum commits to return (default 30).
        branch: Branch or ref to query (default HEAD).

    Returns:
        JSON with commits array containing hash, author, date, message,
        and files_changed count.

    Output Size: ~250 bytes per commit.

    Example success:
        {"status": "success", "branch": "main",
         "commits": [{"hash": "abc123", "author": "dev@example.com",
                      "date": "2024-01-15", "message": "Add feature X",
                      "files_changed": 5}]}
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return ToolResult.error(str(e)).to_json()

    cmd = [
        "git",
        "log",
        f"-n{limit}",
        "--pretty=format:%H|%ae|%as|%s",
        "--shortstat",
        branch,
    ]

    result = run_command(cmd, cwd=str(repo))

    if result["status"] != "success":
        return ToolResult.error(f"Failed to get commits: {result['stderr']}").to_json()

    commits = []
    lines = result["stdout"].strip().split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Check if this is a commit line (contains pipe separators)
        if "|" in line:
            parts = line.split("|", 3)
            if len(parts) >= 4:
                commit = {
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                    "files_changed": 0,
                }

                # Look for stat line following commit
                if i + 1 < len(lines):
                    stat_line = lines[i + 1].strip()
                    # Parse "X files changed, Y insertions(+), Z deletions(-)"
                    files_match = re.search(r"(\d+) files? changed", stat_line)
                    if files_match:
                        commit["files_changed"] = int(files_match.group(1))
                        i += 1  # Skip the stat line

                commits.append(commit)
        i += 1

    return ToolResult.success(
        branch=branch,
        commits=commits,
        commit_count=len(commits),
    ).to_json()


@tool
def git_diff_file(
    repo_path: str,
    file_path: str,
    commit: str | None = None,
    context_lines: int = 3,
) -> str:
    """Get the diff for a specific file.

    USE THIS TOOL:
    - To see exact changes in a file
    - To understand what changed between commits
    - For code review or change analysis
    - To investigate recent modifications

    DO NOT USE:
    - For large binary files
    - When you need full file content (use read_file_bounded instead)

    Shows the unified diff for a file. Without a commit, shows unstaged changes.
    With a commit hash, shows changes introduced by that commit.

    Args:
        repo_path: Absolute path to the repository root.
        file_path: Path to the file (relative to repo root or absolute).
        commit: Optional commit hash to show changes from that commit.
        context_lines: Lines of context around changes (default 3).

    Returns:
        JSON with diff content and metadata.

    Output Size: Varies by change size, typically 1-10KB.

    Example success:
        {"status": "success", "file_path": "src/main.py",
         "commit": "abc123", "diff": "@@ -10,5 +10,7 @@..."}
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return ToolResult.error(str(e)).to_json()

    # Normalize file path
    if Path(file_path).is_absolute():
        try:
            file_path = str(Path(file_path).relative_to(repo))
        except ValueError:
            return ToolResult.error(f"File path {file_path} is not within repo {repo}").to_json()

    if commit:
        # Show diff for specific commit
        cmd = [
            "git",
            "show",
            f"-U{context_lines}",
            "--pretty=format:",
            commit,
            "--",
            file_path,
        ]
    else:
        # Show unstaged changes
        cmd = [
            "git",
            "diff",
            f"-U{context_lines}",
            "--",
            file_path,
        ]

    result = run_command(cmd, cwd=str(repo), max_output=50_000)

    if result["status"] != "success":
        return ToolResult.error(f"Failed to get diff: {result['stderr']}").to_json()

    diff_content = result["stdout"].strip()

    if not diff_content:
        return ToolResult.success(
            file_path=file_path,
            commit=commit,
            diff="",
            note="No changes found",
        ).to_json()

    # Parse diff stats
    additions = len(re.findall(r"^\+[^+]", diff_content, re.MULTILINE))
    deletions = len(re.findall(r"^-[^-]", diff_content, re.MULTILINE))

    return ToolResult.success(
        file_path=file_path,
        commit=commit,
        diff=diff_content,
        additions=additions,
        deletions=deletions,
        truncated=result.get("truncated", False),
    ).to_json()


def _parse_blame_line(
    line: str,
    current_author: str,
    current_date: str,
) -> tuple[str, str]:
    """Parse a blame porcelain line and extract author/date info.

    Args:
        line: A line from git blame --line-porcelain output
        current_author: Current author being tracked
        current_date: Current date being tracked

    Returns:
        Tuple of (updated_author, updated_date)
    """
    import datetime

    if line.startswith("author-mail "):
        return line[12:].strip("<>"), current_date
    if line.startswith("author-time "):
        try:
            ts = int(line[12:].strip())
            date_str = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return current_author, "unknown"
        else:
            return current_author, date_str
    return current_author, current_date


@tool
def git_blame_summary(
    repo_path: str,
    file_path: str,
) -> str:
    """Get authorship summary for a file.

    USE THIS TOOL:
    - To identify who has expertise on a file
    - To understand code ownership distribution
    - To find the right person to ask about code
    - To see how recently different parts were modified

    DO NOT USE:
    - For files not tracked by git
    - When you need line-by-line attribution (use git blame directly)

    Provides a summary of who wrote which portions of a file,
    aggregated by author.

    Args:
        repo_path: Absolute path to the repository root.
        file_path: Path to the file (relative to repo root or absolute).

    Returns:
        JSON with author breakdown by lines owned.

    Output Size: ~100 bytes per author.

    Example success:
        {"status": "success", "file_path": "src/main.py", "total_lines": 150,
         "authors": [{"email": "dev@example.com", "lines": 100, "percentage": 66.7,
                      "last_commit_date": "2024-01-15"}]}
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return ToolResult.error(str(e)).to_json()

    # Normalize file path
    if Path(file_path).is_absolute():
        try:
            file_path = str(Path(file_path).relative_to(repo))
        except ValueError:
            return ToolResult.error(f"File path {file_path} is not within repo {repo}").to_json()

    # Get blame output with email and date
    cmd = ["git", "blame", "--line-porcelain", file_path]
    result = run_command(cmd, cwd=str(repo), timeout=60)

    if result["status"] != "success":
        return ToolResult.error(f"Failed to get blame: {result['stderr']}").to_json()

    # Parse porcelain output
    author_stats: dict[str, dict[str, Any]] = {}
    total_lines = 0
    current_author = ""
    current_date = ""

    for line in result["stdout"].split("\n"):
        current_author, current_date = _parse_blame_line(line, current_author, current_date)

        if line.startswith("\t") and current_author:
            # Content line marks end of a blame entry
            total_lines += 1
            if current_author not in author_stats:
                author_stats[current_author] = {"lines": 0, "last_date": ""}
            author_stats[current_author]["lines"] += 1
            author_stats[current_author]["last_date"] = max(
                author_stats[current_author]["last_date"],
                current_date,
            )

    # Build summary sorted by lines
    authors = [
        {
            "email": email,
            "lines": data["lines"],
            "percentage": round(100 * data["lines"] / total_lines, 1) if total_lines > 0 else 0,
            "last_commit_date": data["last_date"],
        }
        for email, data in sorted(author_stats.items(), key=lambda x: x[1]["lines"], reverse=True)
    ]

    return ToolResult.success(
        file_path=file_path,
        total_lines=total_lines,
        authors=authors,
        author_count=len(authors),
    ).to_json()


@tool
def git_hotspots(
    repo_path: str,
    limit: int = 50,
    since: str | None = None,
) -> str:
    """Identify frequently changed files (change hotspots).

    USE THIS TOOL:
    - To find areas of high activity/churn
    - To identify potentially problematic code (frequent changes may indicate bugs)
    - To prioritize code review or refactoring efforts
    - To understand where development effort is concentrated

    DO NOT USE:
    - For small repositories with little history

    Analyzes git history to find files with the most commits,
    which often indicates areas of active development or instability.

    Args:
        repo_path: Absolute path to the repository root.
        limit: Maximum commits to analyze (default 50).
        since: Optional date filter (e.g., "2024-01-01", "6 months ago").

    Returns:
        JSON with hotspots ranked by commit frequency.

    Output Size: ~80 bytes per file.

    Example success:
        {"status": "success", "hotspots": [
            {"path": "src/auth.py", "commits": 25, "percentage": 50.0},
            {"path": "src/api.py", "commits": 15, "percentage": 30.0}
        ], "total_commits_analyzed": 50}

    Interpretation:
        - High commit files may need: better tests, refactoring, or documentation
        - Stable files (few commits) are often mature/well-designed
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return ToolResult.error(str(e)).to_json()

    cmd = ["git", "log", f"-n{limit}", "--pretty=format:", "--name-only"]
    if since:
        cmd.extend(["--since", since])

    result = run_command(cmd, cwd=str(repo), timeout=60)

    if result["status"] != "success":
        return ToolResult.error(f"Failed to analyze history: {result['stderr']}").to_json()

    # Count file occurrences
    file_counter: Counter[str] = Counter()
    commit_count = 0

    lines = result["stdout"].strip().split("\n")
    in_commit = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            if in_commit:
                commit_count += 1
            in_commit = False
            continue
        in_commit = True
        file_counter[stripped] += 1

    # Account for last commit if file ends without blank line
    if in_commit:
        commit_count += 1

    hotspots = [
        {
            "path": path,
            "commits": count,
            "percentage": round(100 * count / commit_count, 1) if commit_count > 0 else 0,
        }
        for path, count in file_counter.most_common(30)
    ]

    logger.info(f"Found {len(hotspots)} hotspot files from {commit_count} commits")

    return ToolResult.success(
        hotspots=hotspots,
        total_commits_analyzed=commit_count,
        unique_files=len(file_counter),
    ).to_json()


@tool
def git_contributors(
    repo_path: str,
    limit: int = 100,
) -> str:
    """Get contributor statistics for the repository.

    USE THIS TOOL:
    - To identify key contributors and their areas of focus
    - To understand team structure and expertise distribution
    - To find domain experts for specific areas

    DO NOT USE:
    - When you only need file-specific authorship (use git_blame_summary instead)

    Args:
        repo_path: Absolute path to the repository root.
        limit: Maximum commits to analyze (default 100).

    Returns:
        JSON with contributors ranked by commit count.

    Output Size: ~100 bytes per contributor.

    Example success:
        {"status": "success", "contributors": [
            {"email": "dev1@example.com", "commits": 50, "percentage": 50.0,
             "first_commit": "2023-06-01", "last_commit": "2024-01-15"}
        ], "total_commits": 100}
    """
    try:
        repo = validate_repo_path(repo_path)
    except ValidationError as e:
        return ToolResult.error(str(e)).to_json()

    cmd = [
        "git",
        "log",
        f"-n{limit}",
        "--pretty=format:%ae|%as",
    ]

    result = run_command(cmd, cwd=str(repo))

    if result["status"] != "success":
        return ToolResult.error(f"Failed to get contributors: {result['stderr']}").to_json()

    # Track commits and date ranges per author
    author_data: dict[str, dict[str, Any]] = {}
    total_commits = 0

    for line in result["stdout"].strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 1)
        if len(parts) >= 2:
            email, date = parts[0], parts[1]
            total_commits += 1

            if email not in author_data:
                author_data[email] = {"commits": 0, "first_date": date, "last_date": date}

            author_data[email]["commits"] += 1
            # Update date range
            author_data[email]["first_date"] = min(author_data[email]["first_date"], date)
            author_data[email]["last_date"] = max(author_data[email]["last_date"], date)

    contributors = [
        {
            "email": email,
            "commits": data["commits"],
            "percentage": round(100 * data["commits"] / total_commits, 1) if total_commits > 0 else 0,
            "first_commit": data["first_date"],
            "last_commit": data["last_date"],
        }
        for email, data in sorted(author_data.items(), key=lambda x: x[1]["commits"], reverse=True)
    ]

    return ToolResult.success(
        contributors=contributors,
        total_commits=total_commits,
        contributor_count=len(contributors),
    ).to_json()
