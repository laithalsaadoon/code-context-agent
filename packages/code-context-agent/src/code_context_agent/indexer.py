"""Deterministic indexing pipeline for code analysis without LLM invocations.

Runs static analysis tools (ripgrep, semgrep, radon, vulture, knip, repomix),
git history analysis, and GitNexus graph indexing. All external tool calls are
graceful -- if a tool is missing the step is skipped and indexing continues.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger

from code_context_agent.config import DEFAULT_OUTPUT_DIR

# Extension to language mapping (used by _detect_languages and downstream scanners)
_EXTENSION_TO_LANG: dict[str, str] = {
    ".py": "py",
    ".ts": "ts",
    ".tsx": "ts",
    ".js": "ts",
    ".jsx": "ts",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
}


async def build_index(
    repo_path: Path,
    output_dir: Path | None = None,
    quiet: bool = False,
) -> None:
    """Build a deterministic index of a codebase without LLM invocations.

    Pipeline:
    1.  File manifest via ripgrep
    1a. Write file manifest to disk
    2.  Language detection from file extensions
    3.  GitNexus analyze (graph indexing)
    4.  Git hotspots + co-changes -> JSON files
    5.  Repomix compressed signatures
    6.  Repomix orientation
    7.  BM25 index prebuild
    8.  Semgrep auto
    9.  Semgrep OWASP
    10. Type checker
    11. Linter
    12. Complexity analysis
    13. Dead code (Python)
    14. Dead code (TypeScript)
    15. Dependencies
    16. Generate heuristic_summary.json

    Args:
        repo_path: Path to the repository root.
        output_dir: Where to save artifacts (default: repo_path/.code-context/).
        quiet: Suppress progress output.
    """
    repo = repo_path.resolve()
    out = output_dir or (repo / DEFAULT_OUTPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)

    # Step 1: File manifest
    files = _get_file_manifest(repo)
    if not quiet:
        logger.info(f"Indexing {len(files)} files in {repo}")

    # Step 1a: Write file manifest to disk (BM25 search checks for this)
    manifest_path = out / "files.all.txt"
    manifest_path.write_text("\n".join(files))

    # Step 2: Detect languages
    lang_files = _detect_languages(files)
    if not quiet:
        lang_summary = {lang: len(fs) for lang, fs in lang_files.items()}
        logger.info(f"Languages detected: {lang_summary}")

    # Step 3: GitNexus analyze
    gitnexus_ok = _run_gitnexus_analyze(repo, quiet)

    # Step 4: Git hotspots + co-changes (write to JSON)
    _ingest_git(repo, out, quiet)

    # Step 5: Repomix compressed signatures
    _run_repomix_signatures(repo, out, quiet)

    # Step 6: Repomix orientation
    _run_repomix_orientation(repo, out, quiet)

    # Step 7: BM25 index prebuild
    _prebuild_bm25(files, repo, quiet)

    # Step 8-9: Semgrep
    _run_semgrep_auto(repo, out, quiet)
    _run_semgrep_owasp(repo, out, quiet)

    # Step 10: Type checker
    _run_typecheck(repo, out, lang_files, quiet)

    # Step 11: Linter
    _run_lint(repo, out, quiet)

    # Step 12: Complexity
    _run_complexity(repo, out, lang_files, quiet)

    # Step 13-14: Dead code
    _run_dead_code_py(repo, out, lang_files, quiet)
    _run_dead_code_ts(repo, out, lang_files, quiet)

    # Step 15: Dependencies
    _run_deps(repo, out, lang_files, quiet)

    # Step 16: Generate heuristic_summary.json
    _generate_heuristic_summary(files, lang_files, out, repo, gitnexus_ok, quiet)


# --------------------------------------------------------------------------- #
# File manifest
# --------------------------------------------------------------------------- #


def _get_file_manifest(repo: Path) -> list[str]:
    """Get list of files in the repo using ripgrep.

    Falls back to a simple glob if rg is not available.

    Args:
        repo: Repository root path.

    Returns:
        List of file paths relative to repo root.
    """
    if shutil.which("rg") is None:
        logger.warning("ripgrep (rg) not found, falling back to Path.rglob")
        return _get_file_manifest_fallback(repo)

    try:
        result = subprocess.run(
            ["rg", "--files"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"rg --files failed: {e}, falling back to Path.rglob")

    return _get_file_manifest_fallback(repo)


def _get_file_manifest_fallback(repo: Path) -> list[str]:
    """Fallback file manifest using pathlib."""
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}
    files: list[str] = []
    for p in repo.rglob("*"):
        if p.is_file() and not any(part in skip_dirs for part in p.parts):
            try:
                files.append(str(p.relative_to(repo)))
            except ValueError:
                continue
    return files


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #


def _detect_languages(files: list[str]) -> dict[str, list[str]]:
    """Group files by language based on file extension.

    Args:
        files: List of relative file paths.

    Returns:
        Dict mapping language key (e.g. "py", "ts") to list of file paths.
    """
    lang_files: dict[str, list[str]] = {}
    for f in files:
        ext = Path(f).suffix.lower()
        lang = _EXTENSION_TO_LANG.get(ext)
        if lang:
            lang_files.setdefault(lang, []).append(f)
    return lang_files


# --------------------------------------------------------------------------- #
# GitNexus analyze
# --------------------------------------------------------------------------- #


def _run_gitnexus_analyze(repo: Path, quiet: bool) -> bool:
    """Run GitNexus graph indexing on the repository.

    Args:
        repo: Repository root path.
        quiet: Suppress progress output.

    Returns:
        True if GitNexus analysis succeeded, False otherwise.
    """
    if shutil.which("gitnexus") is None:
        logger.warning("gitnexus not found -- skipping GitNexus analysis")
        return False

    try:
        result = subprocess.run(
            ["gitnexus", "analyze", str(repo)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        logger.warning("GitNexus analyze timed out (300s)")
        return False
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"GitNexus analyze failed: {e}")
        return False

    if result.returncode != 0:
        logger.warning(f"GitNexus analyze failed (exit {result.returncode}): {result.stderr[:300]}")
        return False
    if not quiet:
        logger.info(f"GitNexus analyze: completed for {repo}")
    return True


# --------------------------------------------------------------------------- #
# Git analysis (hotspots + co-changes -> JSON files)
# --------------------------------------------------------------------------- #


def _ingest_git(repo: Path, out: Path, quiet: bool) -> None:  # noqa: C901
    """Compute git hotspots and co-change data, writing results to JSON files."""
    # Hotspots
    try:
        result = subprocess.run(
            ["git", "log", "-n200", "--pretty=format:", "--name-only"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Git hotspot analysis failed: {e}")
        return

    if result.returncode != 0:
        logger.warning(f"Git hotspot analysis failed: {result.stderr[:200]}")
        return

    file_counter: Counter[str] = Counter()
    commit_count = 0
    in_commit = False

    for raw_line in result.stdout.strip().splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if in_commit:
                commit_count += 1
            in_commit = False
            continue
        in_commit = True
        file_counter[stripped] += 1
    if in_commit:
        commit_count += 1

    if commit_count == 0:
        return

    hotspots = [
        {"path": path, "commits": count, "percentage": round(100 * count / commit_count, 1)}
        for path, count in file_counter.most_common(30)
    ]

    hotspots_data: dict[str, Any] = {
        "hotspots": hotspots,
        "total_commits_analyzed": commit_count,
    }

    # Write hotspots to JSON
    hotspots_path = out / "git_hotspots.json"
    hotspots_path.write_text(json.dumps(hotspots_data, indent=2))

    if not quiet:
        logger.info(f"Git hotspots: {len(hotspots)} files from {commit_count} commits -> {hotspots_path}")

    # Co-changes for top hotspot files
    top_files = [str(h["path"]) for h in hotspots[:10]]
    all_cochanges: dict[str, Any] = {}
    for file_path in top_files:
        cochange_result = _get_git_cochanges(repo, file_path)
        if cochange_result:
            all_cochanges[file_path] = cochange_result

    if all_cochanges:
        cochanges_path = out / "git_cochanges.json"
        cochanges_path.write_text(json.dumps(all_cochanges, indent=2))

        if not quiet:
            logger.info(f"Git co-changes: {len(all_cochanges)} files analyzed -> {cochanges_path}")


def _get_git_cochanges(repo: Path, file_path: str) -> dict[str, Any] | None:
    """Get co-change data for a single file."""
    try:
        commits_result = subprocess.run(
            ["git", "log", "-n100", "--pretty=format:%H", "--", file_path],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return None

    if commits_result.returncode != 0:
        return None

    commit_hashes = [h.strip() for h in commits_result.stdout.strip().splitlines() if h.strip()]
    if not commit_hashes:
        return None

    cochange_counter: Counter[str] = Counter()
    total_commits = len(commit_hashes)

    for commit_hash in commit_hashes:
        try:
            files_result = subprocess.run(
                ["git", "show", "--pretty=format:", "--name-only", commit_hash],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if files_result.returncode == 0:
                changed = [f.strip() for f in files_result.stdout.strip().splitlines() if f.strip()]
                other = [f for f in changed if f != file_path]
                cochange_counter.update(other)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
            continue

    if not cochange_counter:
        return None

    cochanged_files = [
        {"path": path, "count": count, "percentage": round(100 * count / total_commits, 1)}
        for path, count in cochange_counter.most_common(50)
    ]

    return {
        "file_path": file_path,
        "total_commits": total_commits,
        "cochanged_files": cochanged_files,
    }


# --------------------------------------------------------------------------- #
# Repomix
# --------------------------------------------------------------------------- #


def _run_repomix_signatures(repo: Path, out: Path, quiet: bool) -> None:
    """Generate compressed signatures via repomix (tree-sitter body stripping)."""
    if shutil.which("repomix") is None:
        logger.debug("repomix not found -- skipping signatures")
        return

    sig_path = out / "CONTEXT.signatures.md"
    try:
        subprocess.run(
            [
                "repomix",
                "--compress",
                "--remove-comments",
                "--remove-empty-lines",
                "--style",
                "markdown",
                "-o",
                str(sig_path),
                str(repo),
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if not quiet and sig_path.exists():
            size_kb = sig_path.stat().st_size / 1024
            logger.info(f"Repomix signatures: {size_kb:.0f}KB -> {sig_path}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Repomix signatures failed: {e}")


def _run_repomix_orientation(repo: Path, out: Path, quiet: bool) -> None:
    """Generate token-aware orientation overview via repomix."""
    if shutil.which("repomix") is None:
        logger.debug("repomix not found -- skipping orientation")
        return

    orient_path = out / "CONTEXT.orientation.md"
    try:
        subprocess.run(
            [
                "repomix",
                "--no-files",
                "--style",
                "markdown",
                "--token-count-tree",
                "300",
                "-o",
                str(orient_path),
                str(repo),
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if not quiet and orient_path.exists():
            size_kb = orient_path.stat().st_size / 1024
            logger.info(f"Repomix orientation: {size_kb:.0f}KB -> {orient_path}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Repomix orientation failed: {e}")


# --------------------------------------------------------------------------- #
# BM25
# --------------------------------------------------------------------------- #


def _prebuild_bm25(files: list[str], repo: Path, quiet: bool) -> None:
    """Pre-build BM25 search index so first search has zero latency."""
    try:
        from code_context_agent.tools.search.bm25 import BM25Index
        from code_context_agent.tools.search.tools import _indexes

        index = BM25Index.from_files(files, repo)
        _indexes[str(repo)] = index
        if not quiet:
            logger.info(f"BM25 index: {len(files)} files indexed")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"BM25 prebuild failed: {e}")


# --------------------------------------------------------------------------- #
# Static analysis steps
# --------------------------------------------------------------------------- #


def _run_semgrep_auto(repo: Path, out: Path, quiet: bool) -> None:
    """Run semgrep with auto config for general findings."""
    if shutil.which("semgrep") is None:
        logger.debug("semgrep not found -- skipping semgrep auto scan")
        return

    try:
        result = subprocess.run(
            ["semgrep", "--config", "auto", "--json", "--quiet", str(repo)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.stdout:
            (out / "semgrep_auto.json").write_text(result.stdout)
            if not quiet:
                logger.info(f"Semgrep auto: wrote {out / 'semgrep_auto.json'}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Semgrep auto scan failed: {e}")


def _run_semgrep_owasp(repo: Path, out: Path, quiet: bool) -> None:
    """Run semgrep with OWASP Top Ten config."""
    if shutil.which("semgrep") is None:
        logger.debug("semgrep not found -- skipping semgrep OWASP scan")
        return

    try:
        result = subprocess.run(
            ["semgrep", "--config", "p/owasp-top-ten", "--json", "--quiet", str(repo)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.stdout:
            (out / "semgrep_owasp.json").write_text(result.stdout)
            if not quiet:
                logger.info(f"Semgrep OWASP: wrote {out / 'semgrep_owasp.json'}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Semgrep OWASP scan failed: {e}")


def _run_typecheck(repo: Path, out: Path, lang_files: dict[str, list[str]], quiet: bool) -> None:
    """Run type checker (ty or pyright) for Python projects."""
    if "py" not in lang_files:
        return

    # Try ty first
    if shutil.which("ty"):
        try:
            result = subprocess.run(
                ["ty", "check", "--output-format", "json"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.stdout:
                (out / "typecheck.json").write_text(result.stdout)
                if not quiet:
                    logger.info(f"Type check (ty): wrote {out / 'typecheck.json'}")
                return
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
            logger.warning(f"ty check failed: {e}")

    # Fallback to pyright
    if shutil.which("pyright"):
        try:
            result = subprocess.run(
                ["pyright", "--outputjson"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.stdout:
                (out / "typecheck.json").write_text(result.stdout)
                if not quiet:
                    logger.info(f"Type check (pyright): wrote {out / 'typecheck.json'}")
                return
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
            logger.warning(f"pyright check failed: {e}")

    logger.debug("No type checker (ty or pyright) found -- skipping type check")


def _run_lint(repo: Path, out: Path, quiet: bool) -> None:
    """Run ruff linter."""
    if shutil.which("ruff") is None:
        logger.debug("ruff not found -- skipping lint")
        return

    try:
        result = subprocess.run(
            ["ruff", "check", "--output-format", "json", str(repo)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        # ruff returns exit code 1 when it finds violations (normal, not an error)
        if result.stdout:
            # Validate it's parseable JSON before writing
            try:
                json.loads(result.stdout)
                (out / "lint.json").write_text(result.stdout)
                if not quiet:
                    logger.info(f"Lint (ruff): wrote {out / 'lint.json'}")
            except json.JSONDecodeError:
                logger.debug("ruff output is not valid JSON")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Ruff lint failed: {e}")


def _run_complexity(repo: Path, out: Path, lang_files: dict[str, list[str]], quiet: bool) -> None:
    """Run radon cyclomatic complexity analysis."""
    if "py" not in lang_files:
        return

    if shutil.which("radon") is None:
        logger.debug("radon not found -- skipping complexity analysis")
        return

    try:
        result = subprocess.run(
            ["radon", "cc", "-j", str(repo)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.stdout:
            (out / "complexity.json").write_text(result.stdout)
            if not quiet:
                logger.info(f"Complexity (radon): wrote {out / 'complexity.json'}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Radon complexity analysis failed: {e}")


def _run_dead_code_py(repo: Path, out: Path, lang_files: dict[str, list[str]], quiet: bool) -> None:
    """Run vulture dead code detection for Python."""
    if "py" not in lang_files:
        return

    if shutil.which("vulture") is None:
        logger.debug("vulture not found -- skipping Python dead code detection")
        return

    try:
        result = subprocess.run(
            ["vulture", str(repo), "--min-confidence", "80"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        # vulture outputs text lines, not JSON -- parse them
        pattern = re.compile(r"^(.+?):(\d+): (.+)$")
        entries: list[dict[str, Any]] = []
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                m = pattern.match(line.strip())
                if m:
                    entries.append({"file": m.group(1), "line": int(m.group(2)), "message": m.group(3)})

        (out / "dead_code_py.json").write_text(json.dumps(entries, indent=2))
        if not quiet:
            logger.info(f"Dead code (vulture): {len(entries)} findings -> {out / 'dead_code_py.json'}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Vulture dead code detection failed: {e}")


def _run_dead_code_ts(repo: Path, out: Path, lang_files: dict[str, list[str]], quiet: bool) -> None:
    """Run knip dead code detection for TypeScript/JavaScript."""
    if "ts" not in lang_files:
        return

    if shutil.which("npx") is None:
        logger.debug("npx not found -- skipping TS/JS dead code detection")
        return

    try:
        result = subprocess.run(
            ["npx", "-y", "knip@5", "--reporter", "json"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.stdout:
            (out / "dead_code_ts.json").write_text(result.stdout)
            if not quiet:
                logger.info(f"Dead code (knip): wrote {out / 'dead_code_ts.json'}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Knip dead code detection failed: {e}")


def _try_dep_tool(cmd: list[str], repo: Path, out: Path, label: str, quiet: bool) -> bool:
    """Try running a dependency tool and write output to deps.json.

    Returns True if the tool ran successfully (even with empty output), False on error.
    """
    try:
        result = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"{label} failed: {e}")
        return False

    if result.stdout:
        (out / "deps.json").write_text(result.stdout)
        if not quiet:
            logger.info(f"Dependencies ({label}): wrote {out / 'deps.json'}")
    return True


def _run_deps(repo: Path, out: Path, lang_files: dict[str, list[str]], quiet: bool) -> None:
    """Generate dependency graph."""
    if (
        "py" in lang_files
        and shutil.which("pipdeptree")
        and _try_dep_tool(
            ["pipdeptree", "--json"],
            repo,
            out,
            "pipdeptree",
            quiet,
        )
    ):
        return

    if (
        "ts" in lang_files
        and shutil.which("npm")
        and _try_dep_tool(
            ["npm", "ls", "--json", "--depth=1"],
            repo,
            out,
            "npm",
            quiet,
        )
    ):
        return

    logger.debug("No dependency tool available -- skipping dependency graph")


# --------------------------------------------------------------------------- #
# Heuristic summary helpers
# --------------------------------------------------------------------------- #


def _count_total_lines(repo: Path, files: list[str]) -> int:
    """Count total lines across all files (capped at 5000 files for perf)."""
    total = 0
    for f in files[:5000]:
        try:
            total += (repo / f).read_bytes().count(b"\n")
        except (OSError, ValueError):
            continue
    return total


def _extract_token_count(orientation_path: Path) -> int | None:
    """Parse repomix orientation file for a token count line."""
    if not orientation_path.exists():
        return None
    try:
        text = orientation_path.read_text(errors="replace")
        # Look for patterns like "Token count: 123456" or "Tokens: 123,456"
        m = re.search(r"[Tt]oken[s]?\s*(?:count)?[:\s]+([0-9][0-9,_]*)", text)
        if m:
            return int(m.group(1).replace(",", "").replace("_", ""))
    except (OSError, ValueError):
        pass  # Orientation file missing or unparseable -- fall through to None
    return None


def _load_json_artifact(path: Path) -> Any | None:
    """Load a JSON artifact, returning None on missing or parse error."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _count_semgrep_by_severity(data: Any) -> dict[str, int]:
    """Parse semgrep JSON and count findings by severity."""
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    if not isinstance(data, dict):
        return counts
    results = data.get("results", [])
    if not isinstance(results, list):
        return counts
    for item in results:
        severity = str(item.get("extra", {}).get("severity", "info")).lower()
        if severity in counts:
            counts[severity] += 1
        else:
            counts["info"] += 1
    return counts


def _count_owasp_by_category(data: Any) -> dict[str, int]:
    """Parse semgrep OWASP JSON and group findings by check_id prefix."""
    categories: dict[str, int] = {}
    if not isinstance(data, dict):
        return categories
    results = data.get("results", [])
    if not isinstance(results, list):
        return categories
    for item in results:
        check_id = str(item.get("check_id", "unknown"))
        # Extract category from check_id (e.g. "python.django.security.injection.sql" -> "injection")
        parts = check_id.split(".")
        # Use the broadest meaningful segment -- typically after "security" or 3rd segment
        _min_owasp_depth = 3
        category = check_id
        if len(parts) >= _min_owasp_depth:
            category = parts[2] if len(parts) > _min_owasp_depth else parts[-1]
        categories[category] = categories.get(category, 0) + 1
    return categories


def _count_type_errors(data: Any) -> int:
    """Count type errors from ty or pyright output."""
    if not isinstance(data, (dict, list)):
        return 0
    # ty outputs a JSON list of diagnostics
    if isinstance(data, list):
        return len(data)
    # pyright outputs a dict with generalDiagnostics
    diagnostics = data.get("generalDiagnostics", [])
    if isinstance(diagnostics, list):
        return len(diagnostics)
    return 0


def _count_dead_code(out: Path) -> int:
    """Count total dead code findings from Python and TS artifacts."""
    total = 0
    py_data = _load_json_artifact(out / "dead_code_py.json")
    if isinstance(py_data, list):
        total += len(py_data)
    ts_data = _load_json_artifact(out / "dead_code_ts.json")
    if isinstance(ts_data, list):
        total += len(ts_data)
    elif isinstance(ts_data, dict):
        # knip JSON may have arrays under various keys
        for v in ts_data.values():
            if isinstance(v, list):
                total += len(v)
    return total


def _avg_complexity(data: Any) -> float:
    """Compute mean cyclomatic complexity from radon cc JSON output.

    Radon outputs a dict of filepath -> list of function dicts, each with a
    ``complexity`` field.
    """
    if not isinstance(data, dict):
        return 0.0
    all_cc: list[float] = []
    for funcs in data.values():
        if not isinstance(funcs, list):
            continue
        all_cc.extend(float(func["complexity"]) for func in funcs if isinstance(func, dict) and "complexity" in func)
    if not all_cc:
        return 0.0
    return round(statistics.mean(all_cc), 2)


def _extract_top_complex_functions(data: Any, top_k: int = 10) -> list[dict[str, Any]]:
    """Extract top N functions by cyclomatic complexity from radon output."""
    if not isinstance(data, dict):
        return []
    entries: list[dict[str, Any]] = []
    for filepath, funcs in data.items():
        if not isinstance(funcs, list):
            continue
        for func in funcs:
            if not isinstance(func, dict) or "complexity" not in func:
                continue
            entries.append(
                {
                    "name": func.get("name", "unknown"),
                    "file": filepath,
                    "lines": f"{func.get('lineno', 0)}-{func.get('endline', func.get('lineno', 0))}",
                    "complexity": func.get("complexity", 0),
                },
            )
    entries.sort(key=lambda x: x["complexity"], reverse=True)
    return entries[:top_k]


def _compute_bus_factor_risks(repo: Path, files: list[str]) -> list[str]:
    """Find directories with a single contributor (bus factor risk).

    For each directory with >5 files, count distinct git authors. Cap to
    20 directories for performance.
    """
    dir_files: dict[str, int] = {}
    for f in files:
        parent = str(Path(f).parent)
        if parent == ".":
            continue
        dir_files[parent] = dir_files.get(parent, 0) + 1

    # Only check directories with >5 files, capped at 20
    _min_dir_files = 5
    candidate_dirs = sorted(
        ((d, c) for d, c in dir_files.items() if c > _min_dir_files),
        key=lambda x: x[1],
        reverse=True,
    )[:20]

    risks: list[str] = []
    for dir_path, file_count in candidate_dirs:
        try:
            result = subprocess.run(
                ["git", "log", "--format=%aN", "--", f"{dir_path}/"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout:
                authors = set(result.stdout.strip().splitlines())
                if len(authors) <= 1:
                    risks.append(f"{dir_path} ({len(authors)} contributor, {file_count} files)")
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
            continue

    return risks


def _count_contributors(repo: Path) -> int:
    """Count distinct contributors via git shortlog."""
    try:
        result = subprocess.run(
            ["git", "shortlog", "-sn", "--all", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout:
            return len([line for line in result.stdout.strip().splitlines() if line.strip()])
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        pass  # git shortlog unavailable or timed out -- fall through to 0
    return 0


def _get_total_commits(repo: Path) -> int:
    """Count total commits via git rev-list."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout:
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError, ValueError):
        pass  # git rev-list unavailable or timed out -- fall through to 0
    return 0


# --------------------------------------------------------------------------- #
# Heuristic summary generation
# --------------------------------------------------------------------------- #


def _build_health_section(out: Path) -> dict[str, Any]:
    """Build the health section of the heuristic summary from index artifacts."""
    semgrep_data = _load_json_artifact(out / "semgrep_auto.json")
    semgrep_severities = _count_semgrep_by_severity(semgrep_data) if semgrep_data else {}

    owasp_data = _load_json_artifact(out / "semgrep_owasp.json")
    owasp_categories = _count_owasp_by_category(owasp_data) if owasp_data else {}

    typecheck_data = _load_json_artifact(out / "typecheck.json")
    type_error_count = _count_type_errors(typecheck_data) if typecheck_data else 0

    lint_data = _load_json_artifact(out / "lint.json")
    lint_count = len(lint_data) if isinstance(lint_data, list) else 0

    complexity_data = _load_json_artifact(out / "complexity.json")
    avg_cc = _avg_complexity(complexity_data) if complexity_data else 0.0

    return {
        "semgrep_findings": semgrep_severities,
        "owasp_findings": owasp_categories,
        "type_errors": type_error_count,
        "lint_violations": lint_count,
        "dead_code_symbols": _count_dead_code(out),
        "avg_cyclomatic_complexity": avg_cc,
    }


def _build_git_section(repo: Path, out: Path) -> dict[str, Any]:
    """Build the git section of the heuristic summary."""
    # Extract most coupled pairs from co-changes JSON
    coupled_pairs: list[str] = []
    cochanges_data = _load_json_artifact(out / "git_cochanges.json")
    if isinstance(cochanges_data, dict):
        # Each key is a file path, value has cochanged_files sorted by count
        pair_scores: list[tuple[str, int]] = []
        for source_file, info in cochanges_data.items():
            if not isinstance(info, dict):
                continue
            for cochanged in info.get("cochanged_files", [])[:3]:
                target = cochanged.get("path", "")
                count = cochanged.get("count", 0)
                pair_scores.append((f"{source_file} <-> {target}", count))
        pair_scores.sort(key=lambda x: x[1], reverse=True)
        coupled_pairs = [p[0] for p in pair_scores[:5]]

    return {
        "total_commits_analyzed": _get_total_commits(repo),
        "active_contributors": _count_contributors(repo),
        "most_coupled_pairs": coupled_pairs,
    }


def _get_gitnexus_stats(repo: Path, repo_name: str) -> dict[str, Any]:  # noqa: C901
    """Extract GitNexus structural metadata from the local index.

    Reads .gitnexus/meta.json for stats (community count, process count,
    symbol count, edge count). Optionally queries community names via
    ``gitnexus cypher`` for top-community data.

    Args:
        repo: Repository root path.
        repo_name: The repo identifier (used for --repo flag in cypher queries).

    Returns:
        Dict with community_count, process_count, symbol_count, edge_count,
        and top_communities list. Defaults to 0/empty on any failure.
    """
    result: dict[str, Any] = {
        "community_count": 0,
        "process_count": 0,
        "symbol_count": 0,
        "edge_count": 0,
        "top_communities": [],
    }

    # --- Read stats from .gitnexus/meta.json ---
    meta_path = repo / ".gitnexus" / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            stats = meta.get("stats", {})
            result["community_count"] = stats.get("communities", 0)
            result["process_count"] = stats.get("processes", 0)
            result["symbol_count"] = stats.get("nodes", 0)
            result["edge_count"] = stats.get("edges", 0)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Failed to read .gitnexus/meta.json: {e}")

    # --- Query top communities via gitnexus cypher (graceful failure) ---
    if shutil.which("gitnexus") is not None:
        try:
            cypher_result = subprocess.run(
                [
                    "gitnexus",
                    "cypher",
                    "--repo",
                    repo_name,
                    (
                        "MATCH (c:Community) RETURN c.label, c.symbolCount, c.cohesion "
                        "ORDER BY c.symbolCount DESC LIMIT 10"
                    ),
                ],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if cypher_result.returncode == 0:
                cypher_data = json.loads(cypher_result.stdout)
                markdown = cypher_data.get("markdown", "")
                # Parse markdown table: | label | symbolCount | cohesion |
                communities: list[dict[str, Any]] = []
                seen_names: set[str] = set()
                for raw_line in markdown.split("\n"):
                    line = raw_line.strip()
                    if not line or line.startswith(("| ---", "| c.")):
                        continue
                    parts = [p.strip() for p in line.split("|") if p.strip()]
                    if len(parts) >= 3:  # noqa: PLR2004
                        name = parts[0]
                        # Deduplicate community names (clusters can share labels)
                        if name in seen_names:
                            continue
                        seen_names.add(name)
                        try:
                            symbols = int(parts[1])
                            cohesion = round(float(parts[2]), 3)
                        except (ValueError, IndexError):
                            continue
                        communities.append({"name": name, "symbols": symbols, "cohesion": cohesion})
                result["top_communities"] = communities
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
            logger.debug(f"Failed to query GitNexus communities: {e}")

    return result


def _generate_heuristic_summary(
    files: list[str],
    lang_files: dict[str, list[str]],
    out: Path,
    repo: Path,
    gitnexus_indexed: bool,
    quiet: bool,
) -> dict[str, Any]:
    """Aggregate all index outputs into heuristic_summary.json.

    This is the bridge artifact between cheap deterministic indexing and
    expensive LLM inference -- it tells the coordinator what to focus on.
    """
    total_lines = _count_total_lines(repo, files)
    estimated_tokens = _extract_token_count(out / "CONTEXT.orientation.md")

    complexity_data = _load_json_artifact(out / "complexity.json")
    top_complex = _extract_top_complex_functions(complexity_data) if complexity_data else []

    # Derive repo name from path
    repo_name = repo.name

    summary: dict[str, Any] = {
        "volume": {
            "total_files": len(files),
            "total_lines": total_lines,
            "estimated_tokens": estimated_tokens or int(total_lines * 2.5),
            "languages": {lang: len(fs) for lang, fs in lang_files.items()},
        },
        "health": _build_health_section(out),
        "git": _build_git_section(repo, out),
        "complexity": {
            "top_complex_functions": top_complex,
            "bus_factor_risks": _compute_bus_factor_risks(repo, files),
        },
        "gitnexus": {
            "indexed": gitnexus_indexed,
            "repo_name": repo_name,
            **(
                _get_gitnexus_stats(repo, repo_name)
                if gitnexus_indexed
                else {
                    "community_count": 0,
                    "process_count": 0,
                    "symbol_count": 0,
                    "edge_count": 0,
                    "top_communities": [],
                }
            ),
        },
        "mcp": {
            "context7_available": shutil.which("npx") is not None,
        },
    }

    summary_path = out / "heuristic_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    if not quiet:
        logger.info(f"Heuristic summary: {summary_path}")

    return summary
