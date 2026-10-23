#!/usr/bin/env bash
# Reset the dev database + search indexes to the canonical seed (keeps docker volumes).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m podos_api.migrate --reset
uv run python -m podos_api.seed
