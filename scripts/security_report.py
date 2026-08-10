#!/usr/bin/env python3
"""Run all security scans and produce a unified JSON report.

Usage:
    uv run python scripts/security_report.py [--output report.json]

Runs bandit, osv-scanner, semgrep, betterleaks, and pip-licenses,
then merges all findings into a single JSON report with summary statistics.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def _run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing stdout/stderr."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def run_bandit(src: str = "packages/") -> list[dict]:
    """Run bandit and return findings."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    _run(["uv", "run", "bandit", "-c", "pyproject.toml", "-r", src, "-f", "json", "-o", tmp, "--exit-zero"])
    try:
        data = json.loads(Path(tmp).read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []
    finally:
        Path(tmp).unlink(missing_ok=True)

    return [
        {
            "tool": "bandit",
            "rule": r.get("test_id", ""),
            "name": r.get("test_name", ""),
            "severity": r.get("issue_severity", "UNDEFINED"),
            "confidence": r.get("issue_confidence", "UNDEFINED"),
            "message": r.get("issue_text", ""),
            "file": r.get("filename", ""),
            "line": r.get("line_number", 0),
            "more_info": r.get("more_info", ""),
        }
        for r in data.get("results", [])
    ]


_CVSS_THRESHOLDS = [(9.0, "CRITICAL"), (7.0, "HIGH"), (4.0, "MEDIUM")]


def _cvss_to_severity(score_str: str) -> str:
    """Map a CVSS score string (e.g. '7.5' or 'CVSS:3.1/.../7.5') to a severity label."""
    try:
        cvss = float(score_str.split("/", maxsplit=1)[0]) if "/" in score_str else float(score_str)
    except (ValueError, IndexError):
        return "UNKNOWN"
    for threshold, label in _CVSS_THRESHOLDS:
        if cvss >= threshold:
            return label
    return "LOW"


def run_osv_scanner() -> list[dict]:
    """Run osv-scanner against uv.lock and return findings."""
    result = _run(["osv-scanner", "scan", "--format", "json", "--lockfile", "uv.lock"])
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    findings = []
    for scan_result in data.get("results", []):
        for pkg_info in scan_result.get("packages", []):
            pkg = pkg_info.get("package", {})
            pkg_name = pkg.get("name", "unknown")
            pkg_version = pkg.get("version", "unknown")
            for vuln in pkg_info.get("vulnerabilities", []):
                vuln_id = vuln.get("id", "UNKNOWN")
                severity = "UNKNOWN"
                for sev in vuln.get("severity", []):
                    if "CVSS" in sev.get("type", ""):
                        severity = _cvss_to_severity(sev.get("score", ""))
                        break
                findings.append(
                    {
                        "tool": "osv-scanner",
                        "rule": vuln_id,
                        "name": f"{vuln_id} in {pkg_name}",
                        "severity": severity,
                        "confidence": "HIGH",
                        "message": vuln.get("summary", "")[:200],
                        "package": pkg_name,
                        "version": pkg_version,
                        "aliases": vuln.get("aliases", []),
                    },
                )
    return findings


def run_semgrep(src: str = "packages/") -> list[dict]:
    """Run semgrep and return findings."""
    result = _run(
        ["semgrep", "scan", "--config", "auto", "--config", "p/owasp-top-ten", "--json", "--quiet", src],
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    findings = []
    for r in data.get("results", []):
        severity_map = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}
        findings.append(
            {
                "tool": "semgrep",
                "rule": r.get("check_id", ""),
                "name": r.get("check_id", "").rsplit(".", 1)[-1],
                "severity": severity_map.get(r.get("extra", {}).get("severity", ""), "UNKNOWN"),
                "confidence": r.get("extra", {}).get("metadata", {}).get("confidence", "UNKNOWN"),
                "message": r.get("extra", {}).get("message", ""),
                "file": r.get("path", ""),
                "line": r.get("start", {}).get("line", 0),
            },
        )
    return findings


def run_betterleaks() -> list[dict]:
    """Run betterleaks and return findings."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    _run(["betterleaks", "git", "--no-banner", "-f", "json", "-r", tmp, "."])
    try:
        data = json.loads(Path(tmp).read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []
    finally:
        Path(tmp).unlink(missing_ok=True)

    if not isinstance(data, list):
        return []

    return [
        {
            "tool": "betterleaks",
            "rule": r.get("RuleID", ""),
            "name": r.get("Description", ""),
            "severity": "HIGH",
            "confidence": "HIGH",
            "message": f"Secret detected: {r.get('Description', '')}",
            "file": r.get("File", ""),
            "line": r.get("StartLine", 0),
        }
        for r in data
    ]


def run_license_check() -> list[dict]:
    """Run pip-licenses and return findings for restricted licenses."""
    result = _run(
        [
            "uv",
            "run",
            "--with",
            "pip-licenses",
            "pip-licenses",
            "--fail-on=GPL;AGPL;SSPL;EUPL;CPAL;OSL;RPL",
            "--partial-match",
            "--ignore-packages",
            "code-context-agent",
            "--ignore-packages",
            "strands-teams",
            "--ignore-packages",
            "docutils",
            "--format=json",
        ],
    )
    # pip-licenses exits 1 if restricted licenses found
    if result.returncode == 0:
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    findings = []
    restricted = {"GPL", "AGPL", "SSPL", "EUPL", "CPAL", "OSL", "RPL"}
    for pkg in data:
        license_name = pkg.get("License", "UNKNOWN")
        if any(r in license_name.upper() for r in restricted):
            findings.append(
                {
                    "tool": "pip-licenses",
                    "rule": "restricted-license",
                    "name": f"Restricted license in {pkg.get('Name', '?')}",
                    "severity": "HIGH",
                    "confidence": "HIGH",
                    "message": f"{pkg.get('Name', '?')} {pkg.get('Version', '?')} uses {license_name}",
                    "package": pkg.get("Name", ""),
                    "version": pkg.get("Version", ""),
                    "license": license_name,
                },
            )
    return findings


def main() -> None:
    """Run all security tools and produce a unified report."""
    output_path = "security-report.json"
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = sys.argv[idx + 1]

    print("Running security scans...")
    print("  [1/5] bandit (SAST)...")
    bandit = run_bandit()
    print(f"         {len(bandit)} findings")

    print("  [2/5] osv-scanner (dependency vulnerabilities)...")
    osv = run_osv_scanner()
    print(f"         {len(osv)} findings")

    print("  [3/5] semgrep (SAST + OWASP)...")
    semgrep = run_semgrep()
    print(f"         {len(semgrep)} findings")

    print("  [4/5] betterleaks (secrets)...")
    betterleaks = run_betterleaks()
    print(f"         {len(betterleaks)} findings")

    print("  [5/5] pip-licenses (license compliance)...")
    licenses = run_license_check()
    print(f"         {len(licenses)} findings")

    # Build unified report
    all_findings = bandit + osv + semgrep + betterleaks + licenses
    severity_counts: dict[str, int] = {}
    for f in all_findings:
        sev = f.get("severity", "UNKNOWN")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    tool_counts = {
        "bandit": len(bandit),
        "osv-scanner": len(osv),
        "semgrep": len(semgrep),
        "betterleaks": len(betterleaks),
        "pip-licenses": len(licenses),
    }

    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "summary": {
            "total_findings": len(all_findings),
            "by_severity": dict(sorted(severity_counts.items())),
            "by_tool": tool_counts,
        },
        "findings": all_findings,
    }

    Path(output_path).write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {output_path}")
    print(f"Total: {len(all_findings)} findings")
    for sev in ["HIGH", "MEDIUM", "LOW", "UNKNOWN", "UNDEFINED"]:
        if count := severity_counts.get(sev, 0):
            print(f"  {sev}: {count}")

    # Exit non-zero if any HIGH severity findings
    high_count = severity_counts.get("HIGH", 0)
    if high_count > 0:
        print(f"\n{high_count} HIGH severity finding(s) detected.")
        sys.exit(1)


if __name__ == "__main__":
    main()
