"""T9: Today briefing (§2) and agent-driven tests through the PodOS workspace MCP server (F5/§12)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import BASE
from mcp import Client
from podos_evals.workspace_mcp import WorkspaceSession, build_server
from test_t8_events import release, start_feed, ticks


def items(b: dict, section: str) -> list[dict]:
    return next((s["items"] for s in b["sections"] if s["id"] == section), [])


async def test_briefing_after_an_8k(svc, alice, bianca):
    await start_feed(svc)
    await release("NWSC")
    await ticks(svc, 2)
    b = (await alice.get("/v1/pods/pod_alpha/briefing")).json()
    assert any(i["kind"] == "approval" and "Approve catalyst brief" in i["title"] for i in items(b, "waiting"))
    nwsc = [i for i in items(b, "changed") if i["ticker"] == "NWSC" and i["kind"] == "filing"]
    assert nwsc and nwsc[0]["why"] == "New filing for NWSC, which you follow" and nwsc[0]["yours"]
    assert "need" in b["summary"] and "new" in b["summary"]
    # Beta's briefing has its own copy of the public filing and nothing of Alpha's.
    beta = (await bianca.get("/v1/pods/pod_beta/briefing")).json()
    flat = json.dumps(beta)
    assert "NWSC" in flat and "Approve catalyst brief" not in flat and "ALPHA" not in flat


async def test_dismiss_and_stale_and_feature_gate(svc, alice, pat):
    await start_feed(svc)
    await release("NWSC")
    await ticks(svc, 2)
    b = (await alice.get("/v1/pods/pod_alpha/briefing")).json()
    doc = items(b, "changed")[0]
    r = await alice.post("/v1/pods/pod_alpha/briefing/feedback", json={"item_key": doc["key"], "action": "dismissed"})
    assert r.status_code == 204
    b = (await alice.get("/v1/pods/pod_alpha/briefing")).json()
    assert doc["key"] not in [i["key"] for i in items(b, "changed")]
    # A thesis untouched for a month shows up as going stale.
    async with svc.db.pod_tx("pod_alpha") as conn:
        await conn.execute("UPDATE research_cases SET updated_at = %s WHERE pod_id = 'pod_alpha' AND owner_user_id = 'alice.chen'",
                           (datetime.now(UTC) - timedelta(days=40),))
    stale = items((await alice.get("/v1/pods/pod_alpha/briefing")).json(), "stale")
    assert stale and stale[0]["why"].startswith("Thesis not updated in")
    # The feature is an OpenFGA grant (pod-wide by default); removing it closes the endpoint.
    r = await pat.post("/v1/admin/access/grants/revoke", json={
        "pod_id": "pod_alpha", "kind": "feature", "item_id": "briefing", "subject_kind": "pod", "reason": "trial ended"})
    assert r.status_code == 200
    r = await alice.get("/v1/pods/pod_alpha/briefing")
    assert r.status_code == 403 and r.json()["code"] == "feature_disabled"


async def test_simulated_user_over_mcp(svc):
    """An MCP client acting as Alice reads Today, approves the brief, and the background deck is produced."""
    await start_feed(svc)
    await release("NWSC")
    await ticks(svc, 2)
    async with Client(build_server(WorkspaceSession(BASE, "alice.chen"))) as c:
        def data(res):
            return json.loads("".join(getattr(x, "text", "") for x in res.content))

        pods = data(await c.call_tool("my_pods", {}))["pods"]
        assert [p["pod_id"] for p in pods] == ["pod_alpha"]
        today = data(await c.call_tool("today", {"pod_id": "pod_alpha"}))
        approval = next(i for i in items(today, "waiting") if i["kind"] == "approval")
        wid = approval["key"].split(":", 1)[1]
        res = data(await c.call_tool("decide", {"pod_id": "pod_alpha", "work_item_id": wid, "decision": "approve"}))
        assert res.get("state") == "APPROVED", res
        await ticks(svc, 3)
        inbox = data(await c.call_tool("inbox", {"pod_id": "pod_alpha"}))["items"]
        assert any(i["title"].startswith("Ready: ") for i in inbox)
        ans = data(await c.call_tool("ask", {"pod_id": "pod_alpha", "question": "What is our NWSC thesis?"}))
        assert ans["outcome"] == "run.completed" and ans["sources"]

    # The same tools as Ben (Beta) can't see Alpha at all.
    async with Client(build_server(WorkspaceSession(BASE, "ben.okafor"))) as c:
        out = json.loads("".join(getattr(x, "text", "") for x in (await c.call_tool("today", {"pod_id": "pod_alpha"})).content))
        assert out["status"] == 403
        out = json.loads("".join(getattr(x, "text", "") for x in (await c.call_tool("ask", {"pod_id": "pod_alpha", "question": "NWSC"})).content))
        assert out["status"] == 403
