#!/usr/bin/env bash
# Run the full local quality gate (mirrors .github/workflows/ci.yml).
set -euo pipefail
cd "$(dirname "$0")/.."

ruff check .
ruff format --check .
mypy
pytest --cov=app --cov-report=term-missing
