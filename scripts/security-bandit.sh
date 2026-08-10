#!/usr/bin/env bash
set -euo pipefail

uv run bandit -c pyproject.toml -r packages/ -f json -o bandit-report.json --exit-zero
uv run bandit -c pyproject.toml -r packages/ --severity-level=high --confidence-level=high
