#!/usr/bin/env bash
# Run the whole stack locally: 6 ADK/A2A agent processes, Workspace API, worker, web.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p var/log
# Model provider key (OPENROUTER_API_KEY) and optional PODOS_LLM_* overrides. Never committed.
if [[ -f .env ]]; then set -a; source .env; set +a; fi
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then echo "LLM mode: OpenRouter (${PODOS_LLM_MODEL:-anthropic/claude-sonnet-5.5})"; else echo "LLM mode: off (deterministic agents)"; fi
pids=()
cleanup() { echo "stopping..."; kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

agent() { # id version port
  uv run python -m podos_agents.serve --agent "$1" --version "$2" --port "$3" >"var/log/agent-$1-$2.log" 2>&1 &
  pids+=($!)
}
agent auto-router      1.0.0 8415
agent equity-research  1.0.0 8411
agent filing-analyst   1.0.0 8412
agent filing-analyst   1.1.0 8413
agent catalyst-monitor 1.0.0 8414
agent esg-screener     1.0.0 8416   # running but unregistered: register it from Admin to demo discovery

# Connector MCP servers (mock SEC EDGAR; POST /control/filings releases the next scripted filing).
uv run python -m podos_connectors.serve --connector edgar --mode "${PODOS_EDGAR_MODE:-mock}" --port 8421 >var/log/connector-edgar.log 2>&1 &
pids+=($!)

uv run uvicorn --factory podos_api.main:create_app --host 127.0.0.1 --port 8400 >var/log/api.log 2>&1 &
pids+=($!)
uv run python -m podos_worker --interval 2 >var/log/worker.log 2>&1 &
pids+=($!)
if [[ "${1:-}" != "--no-web" ]]; then
  (cd apps/web && npm run dev -- --port 3400) >var/log/web.log 2>&1 &
  pids+=($!)
fi
echo "PodOS running:"
echo "  web        http://localhost:3400"
echo "  api        http://localhost:8400/docs"
echo "  mailpit    http://localhost:58025"
echo "  minio      http://localhost:59101   (podos-root / podos-root-secret-2026)"
echo "  logs       var/log/*.log"
wait
