#!/usr/bin/env bash
# One-time (idempotent) local setup: data plane, schema, per-pod credentials, seed.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d
echo "waiting for OpenSearch..."; until docker compose ps opensearch --format '{{.Status}}' | grep -q healthy; do sleep 3; done
echo "waiting for OpenFGA..."; until docker compose ps openfga --format '{{.Status}}' | grep -q healthy; do sleep 2; done
uv sync -q
uv run python -m podos_api.migrate
PODOS_OWNER_DATABASE_URL=postgresql://podos_owner:podos_owner@127.0.0.1:55432/podos_test uv run python -m podos_api.migrate
uv run python -m podos_api.provision
uv run python -m podos_api.seed
# OpenUI's install script sends pseudonymous install telemetry unless disabled.
(cd apps/web && OPENUI_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 npm install --silent)
echo "setup complete. run: scripts/dev.sh"
