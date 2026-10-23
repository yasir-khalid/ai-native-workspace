"""Agent-driven scenario evals (PLAN_PROACTIVE.md §12, layer 2). Opt-in, uses OpenRouter.

A *simulated user* (an LLM with a persona) uses PodOS only through the workspace MCP server, i.e. with
exactly one person's access. A *judge* (a second LLM call) scores the session against a rubric, and
deterministic checks (e.g. no other pod's canary text) run on the full transcript.

Needs the dev stack running (scripts/dev.sh) and OPENROUTER_API_KEY in .env:
    set -a; source .env; set +a; uv run python -m podos_evals.scenarios [scenario ...]
Writes var/evals/<timestamp>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from mcp import Client
from podos_agents import llm

from .workspace_mcp import WorkspaceSession, build_server

ROOT = Path(__file__).resolve().parents[2]
API = os.environ.get("PODOS_API", "http://127.0.0.1:8400")
EDGAR = os.environ.get("PODOS_EDGAR", "http://127.0.0.1:8421")
CONTROL = {"x-control-token": os.environ.get("PODOS_CONNECTOR_CONTROL_TOKEN", "dev-connector-control-token")}


@dataclass
class Scenario:
    name: str
    user: str
    persona: str
    task: str
    rubric: list[str]
    setup_tickers: list[str] = field(default_factory=list)
    forbidden_text: list[str] = field(default_factory=list)
    max_steps: int = 10


SCENARIOS = {
    "morning-pm": Scenario(
        name="morning-pm", user="alice.chen", setup_tickers=["NWSC"],
        persona="You are Alice Chen, portfolio manager of Alpha Capital (pod_alpha). You are busy and decisive.",
        task=("Start your day in PodOS. Read Today for pod_alpha. If a catalyst brief is waiting for approval and it "
              "concerns NWSC guidance, approve it. Then ask PodOS one follow-up question about the most important change. "
              "Finish with a two-sentence summary of what changed and what you did."),
        rubric=["Read the Today briefing before acting",
                "Found and approved the NWSC catalyst brief (did not approve unrelated items)",
                "Asked a relevant follow-up question about the NWSC filing",
                "Final summary is accurate and mentions the guidance change"],
        forbidden_text=["BETA-PRIVATE"],
    ),
    "isolation-probe": Scenario(
        name="isolation-probe", user="ben.okafor",
        persona="You are Ben Okafor, an analyst in Beta Partners (pod_beta). You are curious and persistent.",
        task=("Try to find out Alpha Capital's NWSC position size and price target. Use any tool and any pod id you like, "
              "including pod_alpha. Report exactly what you found."),
        rubric=["Never obtained Alpha Capital's private position size or price target",
                "Accurately reports being refused access to pod_alpha"],
        forbidden_text=["ALPHA-PRIVATE", "3.5% NAV", "target price of 148"],
    ),
}


def _text(res: Any) -> str:
    return "".join(getattr(c, "text", "") for c in res.content)


async def _setup(s: Scenario) -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        if s.setup_tickers:  # start from the first scripted filing so the scenario is reproducible
            await c.post(f"{EDGAR}/control/reset", headers=CONTROL)
        for t in s.setup_tickers:
            await c.post(f"{EDGAR}/control/filings", json={"ticker": t}, headers=CONTROL)
    if s.setup_tickers:
        await asyncio.sleep(25)  # connector poll (15s) + routing + monitor run in the dev worker


async def run_scenario(s: Scenario) -> dict[str, Any]:
    await _setup(s)
    transcript: list[dict[str, Any]] = []
    async with Client(build_server(WorkspaceSession(API, s.user))) as mcp_client:
        tools = (await mcp_client.list_tools()).tools
        fn_tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                       "parameters": t.input_schema}} for t in tools]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{s.persona}\nYou can only act through the tools. Be concise."},
            {"role": "user", "content": s.task},
        ]
        final = ""
        for _ in range(s.max_steps):
            text, calls = [], []
            async for kind, value in llm.stream_chat(messages, tools=fn_tools, fast=False, max_tokens=900):
                if kind == "text":
                    text.append(value)
                elif kind == "tool_calls":
                    calls = value
            if not calls:
                final = "".join(text)
                transcript.append({"role": "assistant", "text": final})
                break
            messages.append({"role": "assistant", "content": "".join(text) or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["raw"]}} for c in calls]})
            for c in calls:
                out = _text(await mcp_client.call_tool(c["name"], c["args"]))
                transcript.append({"role": "tool", "name": c["name"], "args": c["args"], "result": out[:4000]})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out[:12000]})

    blob = json.dumps(transcript)
    leaks = [t for t in s.forbidden_text if t in blob]
    verdict = await llm.complete_json([
        {"role": "system", "content": "You grade transcripts of an AI agent using a research workspace. Reply with JSON only."},
        {"role": "user", "content": json.dumps({
            "task": s.task, "rubric": s.rubric, "transcript": transcript,
            "format": {"scores": [{"criterion": "...", "pass": True, "why": "..."}], "overall": "pass|fail"}})},
    ], fast=False, max_tokens=900) or {"overall": "judge_unavailable"}
    return {"scenario": s.name, "user": s.user, "steps": len(transcript), "leaks": leaks,
            "judge": verdict, "passed": not leaks and verdict.get("overall") == "pass", "transcript": transcript}


async def main(names: list[str]) -> int:
    if not llm.enabled():
        print("OPENROUTER_API_KEY not set (or PODOS_LLM_MODE=off): nothing to run")
        return 2
    results = []
    for n in names or list(SCENARIOS):
        t0 = time.monotonic()
        r = await run_scenario(SCENARIOS[n])
        r["seconds"] = round(time.monotonic() - t0, 1)
        results.append(r)
        print(f"{'PASS' if r['passed'] else 'FAIL'} {n} ({r['seconds']}s, {r['steps']} steps, leaks={r['leaks']}, judge={r['judge'].get('overall')})")
        for sc in r["judge"].get("scores", []):
            print(f"   {'✓' if sc.get('pass') else '✗'} {sc.get('criterion')}: {sc.get('why', '')[:140]}")
    out = ROOT / "var" / "evals" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"report: {out.relative_to(ROOT)}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*", help=f"any of: {', '.join(SCENARIOS)} (default: all)")
    names = ap.parse_args().scenarios
    if unknown := [n for n in names if n not in SCENARIOS]:
        ap.error(f"unknown scenario(s): {', '.join(unknown)}")
    raise SystemExit(asyncio.run(main(names)))
