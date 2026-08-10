"""Command-line interface for code-context-agent.

This module provides the main CLI entry point using cyclopts.

Example:
    $ code-context-agent analyze /path/to/repo
    $ code-context-agent analyze /path/to/repo --focus "authentication"
    $ code-context-agent analyze . --quiet
"""

from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Parameter
from rich.console import Console

from code_context_agent import __version__
from code_context_agent.config import DEFAULT_OUTPUT_DIR, Settings, get_settings
from code_context_agent.display import display_welcome

console = Console()

app = App(
    name="code-context-agent",
    help="A CLI tool for code context analysis.",
    version=__version__,
)


@app.default
def main(
    debug: Annotated[bool, Parameter(help="Enable debug mode.")] = False,
    output_format: Annotated[
        Literal["rich", "json", "plain"],
        Parameter(help="Output format: rich, json, plain."),
    ] = "rich",
) -> None:
    """Run the code-context-agent CLI.

    This is the main entry point for the CLI. It loads settings from the
    environment and displays a welcome message with current configuration.

    Args:
        debug: Enable debug output for verbose logging and diagnostics.
        output_format: Format for output display. Supported values are
            "rich" (default), "json", or "plain".
    """
    settings = get_settings()
    if debug or output_format != "rich":
        settings = Settings(debug=debug, output_format=output_format)

    display_welcome(settings)


@app.command
def analyze(  # noqa: C901, PLR0912, PLR0915
    path: Annotated[
        Path,
        Parameter(help="Path to the repository to analyze."),
    ] = Path(),
    *,
    output_dir: Annotated[
        Path | None,
        Parameter(help="Output directory for context files. Defaults to <repo>/.code-context"),
    ] = None,
    focus: Annotated[
        str,
        Parameter(help="Focus area for analysis (e.g., 'authentication', 'API endpoints', 'database layer')."),
    ] = "",
    output_format: Annotated[
        Literal["rich", "json"],
        Parameter(help="Output format: rich (default TUI), json (AnalysisResult JSON to stdout)."),
    ] = "rich",
    since: Annotated[
        str,
        Parameter(
            help="Git ref for incremental analysis (e.g., 'HEAD~5', 'main', 'abc123'). "
            "Only re-analyzes files changed since this ref.",
        ),
    ] = "",
    full: Annotated[
        bool,
        Parameter(help="Run exhaustive analysis with no size limits and fail-fast error handling."),
    ] = False,
    quiet: Annotated[
        bool,
        Parameter(help="Suppress all output except errors on stderr. No TUI, no JSON."),
    ] = False,
    quick: Annotated[
        bool,
        Parameter(help="Lightweight analysis: single scout wave, risk profile refresh only. For nightly CI/CD."),
    ] = False,
    bundles_only: Annotated[
        bool,
        Parameter(help="Skip indexing and team dispatch; regenerate bundles from existing team findings."),
    ] = False,
    debug: Annotated[
        bool,
        Parameter(help="Enable debug logging for troubleshooting."),
    ] = False,
) -> None:
    """Analyze a codebase and produce targeted context bundles.

    The coordinator dispatches parallel specialist teams that use GitNexus,
    ripgrep, repomix, and git history tools to understand the codebase,
    then consolidates findings into targeted bundle files.

    Outputs:
        .code-context/bundles/BUNDLE.{area}.md - Targeted context bundles
        .code-context/heuristic_summary.json - Deterministic index summary

    Example:
        $ code-context-agent analyze /path/to/repo
        $ code-context-agent analyze . --focus "authentication"
        $ code-context-agent analyze . --output-dir ./output
        $ code-context-agent analyze . --bundles-only
    """
    import sys

    from loguru import logger

    from code_context_agent.usecase import CodebaseAnalysisUseCase
    from code_context_agent.utils import setup_logger
    from strands_teams import run_teams_sync

    # Configure logging: suppress loguru in normal mode (Rich Live conflict),
    # enable full logging in debug mode (which uses QuietConsumer instead)
    if debug:
        setup_logger(level="DEBUG")
        if not quiet:
            console.print("[dim]Debug logging enabled (live display disabled)[/dim]")
    else:
        # Remove default loguru handler to prevent stderr writes that break Rich Live
        logger.remove()
        setup_logger(level="WARNING")

    repo_path = path.resolve()

    if not repo_path.exists():
        print(f"Error: Path does not exist: {repo_path}", file=sys.stderr)
        raise SystemExit(1)

    if not repo_path.is_dir():
        print(f"Error: Path is not a directory: {repo_path}", file=sys.stderr)
        raise SystemExit(1)

    # Validate flag combinations
    _validate_flags(full=full, since=since, bundles_only=bundles_only, quick=quick)

    # Derive analysis mode
    mode = _derive_mode(full=full, focus=focus, since=since, quick=quick)

    # Auto-preflight in full mode
    if full and not quiet:
        preflight = _preflight_check()
        missing = [name for name, info in preflight.items() if not info["available"]]
        if missing:
            console.print(f"[yellow]Warning:[/yellow] Missing tools: {', '.join(missing)}")
            console.print("  Full mode works best with all tools installed.")

    # Show analysis configuration
    if not quiet:
        console.print()
        console.print("[bold]Code Context Analysis[/bold]")
        console.print(f"  Repository: [cyan]{repo_path}[/cyan]")
        if focus:
            console.print(f"  Focus: [magenta]{focus}[/magenta]")
        if full:
            console.print("  Mode: [bold magenta]FULL (exhaustive)[/bold magenta]")
        if bundles_only:
            console.print("  Mode: [bold magenta]BUNDLES ONLY (regenerate from findings)[/bold magenta]")
        console.print()

    # In debug mode or JSON mode, use quiet consumer (log output replaces Live display)
    use_quiet = quiet or debug or (output_format == "json")

    # Build incremental analysis context if --since provided
    since_context = None
    if since:
        effective_output = output_dir or repo_path / DEFAULT_OUTPUT_DIR
        since_context = _build_since_context(repo_path, since, effective_output)
    if since_context and not use_quiet:
        console.print(f"  Incremental: [magenta]since {since}[/magenta]")

    # Build the use case, binding the layout to the requested output directory.
    output_root = (output_dir or repo_path / DEFAULT_OUTPUT_DIR).resolve()
    use_case = CodebaseAnalysisUseCase(output_root=output_root)

    # Run the analysis through the strands_teams runner.
    result = run_teams_sync(
        use_case,
        repo_path=repo_path,
        focus=focus or None,
        extra_context=since_context,
        bundles_only=bundles_only,
        full_mode=mode in ("full", "full+focus"),
        quiet=use_quiet,
    )
    # Bridge the runner's result keys to the CLI display contract: the runner
    # names the executive-summary path ``executive_summary_path`` (CONTEXT.md),
    # while ``_display_result*`` expect ``context_path``.
    result["context_path"] = result.pop("executive_summary_path", None)

    if output_format == "json":
        _display_result_json(result)
    else:
        _display_result(result, debug=debug, quiet=quiet)


@app.command
def index(
    path: Annotated[
        Path,
        Parameter(help="Path to the repository to index."),
    ] = Path(),
    *,
    output_dir: Annotated[
        Path | None,
        Parameter(help="Output directory for the code graph. Defaults to <repo>/.code-context"),
    ] = None,
    quiet: Annotated[
        bool,
        Parameter(help="Suppress all output except errors."),
    ] = False,
) -> None:
    """Build a deterministic index without LLM calls.

    Faster and cheaper than full analysis. Runs GitNexus indexing, git
    analysis, and static scanners to produce artifacts for agent analysis.

    Example:
        $ code-context-agent index /path/to/repo
        $ code-context-agent index . --output-dir ./output
        $ code-context-agent index . --quiet
    """
    import asyncio
    import sys

    from code_context_agent.indexer import build_index

    repo_path = path.resolve()

    if not repo_path.exists():
        print(f"Error: Path does not exist: {repo_path}", file=sys.stderr)
        raise SystemExit(1)

    if not repo_path.is_dir():
        print(f"Error: Path is not a directory: {repo_path}", file=sys.stderr)
        raise SystemExit(1)

    asyncio.run(build_index(repo_path, output_dir, quiet))


@app.command
def check() -> None:
    """Check availability of external tool dependencies and AWS credentials.

    Verifies that all CLI tools used by the indexer and analysis pipeline
    are installed, and that AWS credentials are configured for Bedrock access.

    Example:
        $ code-context-agent check
    """
    preflight = _preflight_check()
    groups = [
        ("Core tools (required)", "core", True),
        ("Static analysis (optional, used by indexer)", "analysis", False),
        ("Security scanners (optional)", "security", False),
    ]

    all_ok = True
    required_ok = True
    for label, group, is_required in groups:
        group_ok = _print_tool_group(preflight, label, group, is_required)
        if not group_ok:
            all_ok = False
            if is_required:
                required_ok = False

    console.print("\n[bold]AWS credentials:[/bold]")
    if _check_aws_credentials():
        console.print("  [green]\u2713[/green] sts get-caller-identity")
    else:
        console.print("  [red]\u2717[/red] sts get-caller-identity \u2014 configure AWS credentials for Bedrock")
        all_ok = False

    if all_ok:
        console.print("\n[green]All tools and credentials available.[/green]")
    elif required_ok:
        console.print("\n[yellow]Some optional tools are missing. Core analysis will work.[/yellow]")
    else:
        console.print("\n[red]Required tools are missing. Analysis will not work.[/red]")
        raise SystemExit(1)


def _print_tool_group(
    preflight: dict[str, dict[str, bool | str]],
    label: str,
    group: str,
    is_required: bool,
) -> bool:
    """Print tool availability for a group. Returns True if all tools available."""
    console.print(f"\n[bold]{label}:[/bold]")
    group_ok = True
    for name, info in preflight.items():
        if info["group"] != group:
            continue
        if info["available"]:
            console.print(f"  [green]\u2713[/green] {name}")
        elif is_required:
            console.print(f"  [red]\u2717[/red] {name} \u2014 install via {info['package']}")
            group_ok = False
        else:
            console.print(f"  [dim]\u2022[/dim] {name} \u2014 {info['package']}")
            group_ok = False
    return group_ok


def _preflight_check() -> dict[str, dict[str, bool | str]]:
    """Check availability of external tool dependencies.

    Returns:
        Dict mapping tool name to status info with group classification.
    """
    import shutil

    tools = {
        # Core (required for indexing)
        "ripgrep": {"cmd": "rg", "package": "ripgrep", "group": "core"},
        "gitnexus": {"cmd": "gitnexus", "package": "gitnexus (npm)", "group": "core"},
        "repomix": {"cmd": "repomix", "package": "repomix (npm)", "group": "core"},
        "npx": {"cmd": "npx", "package": "Node.js", "group": "core"},
        # Static analysis (optional, enrich indexer output)
        "semgrep": {"cmd": "semgrep", "package": "semgrep (pip or brew)", "group": "analysis"},
        "ruff": {"cmd": "ruff", "package": "ruff (pip or brew)", "group": "analysis"},
        "ty": {"cmd": "ty", "package": "ty (pip)", "group": "analysis"},
        "radon": {"cmd": "radon", "package": "radon (pip)", "group": "analysis"},
        "vulture": {"cmd": "vulture", "package": "vulture (pip)", "group": "analysis"},
        "pipdeptree": {"cmd": "pipdeptree", "package": "pipdeptree (pip)", "group": "analysis"},
        # Security scanners
        "betterleaks": {"cmd": "betterleaks", "package": "betterleaks (brew or mise)", "group": "security"},
        "bandit": {"cmd": "bandit", "package": "bandit (pip)", "group": "security"},
        "osv-scanner": {"cmd": "osv-scanner", "package": "osv-scanner (mise)", "group": "security"},
    }

    result: dict[str, dict[str, bool | str]] = {}
    for name, info in tools.items():
        available = shutil.which(info["cmd"]) is not None
        result[name] = {"available": available, "package": info["package"], "group": info["group"]}

    return result


def _check_aws_credentials() -> bool:
    """Verify AWS credentials are configured and valid via sts get-caller-identity."""
    import subprocess

    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _validate_flags(
    *,
    full: bool = False,
    since: str = "",
    bundles_only: bool = False,
    quick: bool = False,
) -> None:
    """Validate mutually exclusive flag combinations.

    Raises:
        SystemExit: If invalid flag combinations are detected.
    """
    if full and since:
        console.print("[red]Error:[/red] --full and --since cannot be combined.")
        console.print("  --full runs exhaustive analysis on the entire repo.")
        console.print("  --since runs incremental analysis on changed files only.")
        raise SystemExit(1)
    if bundles_only and full:
        console.print("[red]Error:[/red] --bundles-only and --full cannot be combined.")
        console.print("  --bundles-only regenerates bundles from existing team findings.")
        raise SystemExit(1)
    if bundles_only and since:
        console.print("[red]Error:[/red] --bundles-only and --since cannot be combined.")
        console.print("  --bundles-only regenerates bundles from existing team findings.")
        raise SystemExit(1)
    if quick and full:
        console.print("[red]Error:[/red] --quick and --full cannot be combined.")
        console.print("  --quick runs lightweight analysis; --full runs exhaustive analysis.")
        raise SystemExit(1)
    if quick and bundles_only:
        console.print("[red]Error:[/red] --quick and --bundles-only cannot be combined.")
        raise SystemExit(1)


def _derive_mode(*, full: bool = False, focus: str = "", since: str = "", quick: bool = False) -> str:
    """Derive the analysis mode string from CLI flags.

    Returns:
        Mode string: "standard", "full", "focus", "incremental", "quick", or "full+focus".
    """
    if quick:
        return "quick"
    if full and focus:
        return "full+focus"
    if full:
        return "full"
    if focus:
        return "focus"
    if since:
        return "incremental"
    return "standard"


def _display_result(result: dict, *, debug: bool = False, quiet: bool = False) -> None:
    """Display analysis result to the console.

    In quiet mode, only errors are printed to stderr with no formatting.

    Args:
        result: Analysis result dictionary.
        debug: Whether debug mode is enabled.
        quiet: Whether quiet mode is enabled.
    """
    import sys

    if result["status"] == "completed":
        if not quiet:
            console.print()
            console.print("[green]Analysis completed successfully[/green]")
            console.print(f"  Output directory: [cyan]{result['output_dir']}[/cyan]")
            if result.get("context_path"):
                console.print(f"  Context file: [cyan]{result['context_path']}[/cyan]")
            # List bundle files if present
            _display_bundles(result.get("output_dir"))
    elif result["status"] == "stopped":
        if quiet:
            print(
                f"Error: Analysis stopped: {result.get('exceeded_limit', 'limit exceeded')}",
                file=sys.stderr,
            )
        else:
            console.print()
            console.print(f"[yellow]Analysis stopped:[/yellow] {result.get('exceeded_limit', 'limit exceeded')}")
            console.print(
                f"  Turns: {result.get('turn_count', '?')}, Duration: {result.get('duration_seconds', 0):.1f}s",
            )
            if result.get("context_path"):
                console.print(f"  Partial output: [cyan]{result['context_path']}[/cyan]")
        raise SystemExit(1)
    else:
        error = result.get("error") or "Unknown error (no error message captured)"
        if quiet:
            print(f"Error: {error}", file=sys.stderr)
        else:
            console.print()
            console.print(f"[red]Analysis failed:[/red] {error}")
            if debug:
                console.print(f"[dim]Status: {result.get('status')}, Turns: {result.get('turn_count', 0)}[/dim]")
        raise SystemExit(1)


def _display_bundles(output_dir: str | None) -> None:
    """List bundle files from the output directory.

    Args:
        output_dir: Path to the analysis output directory.
    """
    if not output_dir:
        return

    bundles_dir = Path(output_dir) / "bundles"
    if not bundles_dir.exists():
        return

    bundle_files = sorted(bundles_dir.glob("BUNDLE.*.md"))
    if bundle_files:
        console.print("  Bundles:")
        for bf in bundle_files:
            console.print(f"    [cyan]{bf}[/cyan]")


def _build_since_context(repo_path: Path, since: str, output_dir: Path) -> str | None:
    """Build incremental analysis context from git diff.

    Args:
        repo_path: Repository path.
        since: Git ref to diff against.
        output_dir: Output directory to check for existing artifacts.

    Returns:
        XML-wrapped since context string, or None if git diff fails or is empty.
    """
    import html as _html
    import subprocess

    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", f"{since}..HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None

    changed_files = [f.strip() for f in diff_result.stdout.strip().splitlines() if f.strip()]
    if not changed_files:
        return None

    has_existing_graph = (output_dir / "code_graph.json").exists()
    has_existing_context = (output_dir / "CONTEXT.md").exists()
    safe_since = _html.escape(since)
    file_list = "\n".join(f"- {f}" for f in changed_files)

    return (
        f"<since_context>\n"
        f"<ref>{safe_since}</ref>\n"
        f"<changed_file_count>{len(changed_files)}</changed_file_count>\n"
        f"<changed_files>\n{file_list}\n</changed_files>\n"
        f"<has_existing_graph>{has_existing_graph}</has_existing_graph>\n"
        f"<has_existing_context>{has_existing_context}</has_existing_context>\n"
        f"<output_dir>{output_dir}</output_dir>\n"
        f"</since_context>"
    )


def _display_result_json(result: dict) -> None:
    """Write analysis result as JSON to stdout.

    On success, reads the analysis_result.json written by the agent.
    Falls back to the run metadata dict if the file doesn't exist.
    On error/stopped, writes JSON to stderr and exits with code 1.

    Args:
        result: Analysis run result dictionary.
    """
    import json
    import sys

    if result["status"] == "completed":
        analysis_result_path = Path(result["output_dir"]) / "analysis_result.json"
        if analysis_result_path.exists():
            sys.stdout.write(analysis_result_path.read_text())
            sys.stdout.write("\n")
        else:
            print(json.dumps(result, indent=2))
        return

    # Error or stopped
    error_payload = {
        "status": result["status"],
        "error": result.get("error"),
        "exceeded_limit": result.get("exceeded_limit"),
    }
    print(json.dumps(error_payload, indent=2), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    app()
