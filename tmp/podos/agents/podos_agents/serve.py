"""Serve one ADK agent over A2A (one process per agent deployment).

    python -m podos_agents.serve --agent equity-research --version 1.0.0 --port 8411

NOTE (ARCHITECTURE.md 2.2): ``to_a2a`` defaults to in-memory session/task
stores. These agents are stateless per invocation (all conversation state is
persisted by the Workspace API under pod RLS), so no task state needs to
survive a restart. A durable ``task_store`` must be supplied before any agent
relies on A2A task resumption.
"""

from __future__ import annotations

import argparse
import logging

import uvicorn
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from starlette.applications import Starlette

from .background import AutoRouterAgent, CatalystMonitorAgent
from .research import EquityResearchAgent, ESGScreenerAgent, FilingAnalystAgent

DESCRIPTIONS = {
    "auto-router": "Routes a request to one agent from a platform-supplied eligible set.",
    "equity-research": "Answers company/thesis questions from the pod's private research vault with citations; drafts decks and memos via governed capabilities.",
    "filing-analyst": "Reviews regulatory filings and extracts changes, risk-factor deltas and guidance language.",
    "catalyst-monitor": "Background monitor for new filings and catalysts; raises alerts and requests PM approval.",
    "esg-screener": "Screens issuers for ESG and governance red flags in the pod vault.",
}


def build_agent(agent_id: str, version: str):
    name = agent_id.replace("-", "_")
    desc = DESCRIPTIONS[agent_id]
    match agent_id:
        case "auto-router":
            return AutoRouterAgent(name=name, description=desc)
        case "equity-research":
            return EquityResearchAgent(name=name, description=desc)
        case "filing-analyst":
            return FilingAnalystAgent(name=name, description=desc, version=version)
        case "catalyst-monitor":
            return CatalystMonitorAgent(name=name, description=desc)
        case "esg-screener":
            return ESGScreenerAgent(name=name, description=desc)
    raise SystemExit(f"unknown agent {agent_id}")


def build_app(agent_id: str, version: str, host: str, port: int) -> Starlette:
    return to_a2a(build_agent(agent_id, version), host=host, port=port)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", required=True)
    p.add_argument("--version", default="1.0.0")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    a = p.parse_args()
    logging.getLogger("google_adk").setLevel(logging.WARNING)
    uvicorn.run(build_app(a.agent, a.version, a.host, a.port), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
