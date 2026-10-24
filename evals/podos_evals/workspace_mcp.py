"""PodOS itself as an MCP server, for agent-driven testing (PLAN_PROACTIVE.md F5, §12).

A simulated-user agent (or Claude Code, or any MCP client) uses PodOS *as one signed-in person*: every
tool is a plain call to the public Workspace API with that person's session, so OpenFGA, RLS and
governance apply exactly as they do in the browser. Nothing here has elevated access.

    python -m podos_evals.workspace_mcp --user alice.chen --port 8431    # dev only (uses dev login)
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer

TERMINAL = ("run.completed", "run.failed", "stream.terminated")


class WorkspaceSession:
    def __init__(self, api: str, subject_id: str) -> None:
        self.api = api.rstrip("/")
        self.subject_id = subject_id
        self._http: httpx.AsyncClient | None = None

    async def http(self) -> httpx.AsyncClient:
        if self._http is None:
            async with httpx.AsyncClient(base_url=self.api) as c:
                r = await c.post("/v1/auth/dev-login", json={"subject_id": self.subject_id})
                r.raise_for_status()
                token = r.json()["token"]
            self._http = httpx.AsyncClient(base_url=self.api, timeout=60, headers={"Authorization": f"Bearer {token}"})
        return self._http

    async def call(self, method: str, path: str, *, pod: str | None = None, body: Any = None, params: dict | None = None) -> Any:
        h = {"X-Active-Pod": pod} if pod else {}
        r = await (await self.http()).request(method, path, json=body, params=params, headers=h)
        if r.status_code >= 400:
            try:
                err = r.json()
            except ValueError:
                err = {"detail": r.text[:200]}
            return {"error": err.get("code") or err.get("title") or "error", "status": r.status_code, "detail": err.get("detail")}
        return r.json() if r.content else {"status": r.status_code}

    async def ask(self, pod: str, question: str, mode: str = "auto") -> dict[str, Any]:
        t = await self.call("POST", f"/v1/pods/{pod}/threads", body={})
        if "error" in t:
            return t
        run = await self.call("POST", f"/v1/threads/{t['thread_id']}/runs", pod=pod, body={"mode": mode, "message": question})
        if "error" in run:
            return run
        text, sources, agent, outcome = [], [], None, "unknown"
        async with (await self.http()).stream("GET", f"/v1/runs/{run['run_id']}/events", headers={"X-Active-Pod": pod}) as r:
            etype = None
            async for line in r.aiter_lines():
                if line.startswith("event: "):
                    etype = line[7:]
                elif line.startswith("data: "):
                    ev = json.loads(line[6:])
                    if etype == "message.delta":
                        text.append(ev.get("text", ""))
                    elif etype == "agent.selected":
                        agent = ev.get("agent_id")
                    if etype == "run.completed":
                        sources = [c.get("title") for c in ev.get("citations", [])]
                    if etype in TERMINAL:
                        outcome = etype
                        break
        return {"answer": "".join(text), "agent": agent, "sources": sources, "outcome": outcome, "thread_id": t["thread_id"]}


def build_server(session: WorkspaceSession) -> MCPServer:
    srv = MCPServer(
        "podos-workspace", version="1.0.0",
        instructions=f"You are using PodOS as {session.subject_id}. Every call has exactly that person's access.",
    )

    @srv.tool()
    async def my_pods() -> dict[str, Any]:
        """Pods this person belongs to, with role."""
        pods = await session.call("GET", "/v1/me/pods")
        return {"pods": [{"pod_id": p["pod_id"], "name": p["display_name"], "role": p["pod_role"]} for p in pods]} \
            if isinstance(pods, list) else pods

    @srv.tool()
    async def today(pod_id: str) -> dict[str, Any]:
        """The Today briefing for a pod: what is waiting on you, what changed, what finished, what is stale."""
        return await session.call("GET", f"/v1/pods/{pod_id}/briefing")

    @srv.tool()
    async def dismiss(pod_id: str, item_key: str) -> dict[str, Any]:
        """Dismiss a briefing item (feedback: 'not useful')."""
        return await session.call("POST", f"/v1/pods/{pod_id}/briefing/feedback", body={"item_key": item_key, "action": "dismissed"})

    @srv.tool()
    async def inbox(pod_id: str) -> dict[str, Any]:
        """Inbox items (alerts, approvals, reports) assigned to you in a pod."""
        items = await session.call("GET", f"/v1/pods/{pod_id}/work-items")
        if not isinstance(items, list):
            return items
        return {"items": [{k: i.get(k) for k in ("work_item_id", "title", "state", "category", "summary", "version")} for i in items]}

    @srv.tool()
    async def decide(pod_id: str, work_item_id: str, decision: str, comment: str = "") -> dict[str, Any]:
        """Approve, reject or request changes on a work item. decision: approve | reject | request_changes."""
        item = await session.call("GET", f"/v1/work-items/{work_item_id}", pod=pod_id)
        if "error" in item:
            return item
        return await session.call("POST", f"/v1/work-items/{work_item_id}/decisions", pod=pod_id, body={
            "decision": decision, "expected_version": item["version"], "comment": comment,
            "idempotency_key": f"mcp-{work_item_id}-{item['version']}-{decision}"})

    @srv.tool()
    async def ask(pod_id: str, question: str) -> dict[str, Any]:
        """Ask PodOS a research question in a pod (Auto picks the agent). Returns the answer and its sources."""
        return await session.ask(pod_id, question)

    @srv.tool()
    async def search_research(pod_id: str, query: str) -> dict[str, Any]:
        """Search the pod's research vault."""
        out = await session.call("GET", f"/v1/pods/{pod_id}/search", params={"q": query})
        if isinstance(out, list):
            return {"results": [{k: r.get(k) for k in ("doc_id", "title", "kind", "ticker", "source", "snippet")} for r in out]}
        return out

    return srv


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True)
    p.add_argument("--api", default=os.environ.get("PODOS_API", "http://127.0.0.1:8400"))
    p.add_argument("--port", type=int, default=8431)
    a = p.parse_args()
    app = build_server(WorkspaceSession(a.api, a.user)).streamable_http_app(stateless_http=True, json_response=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
