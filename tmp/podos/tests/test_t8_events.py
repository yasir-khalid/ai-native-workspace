"""T8: event triggers (PLAN_PROACTIVE.md §1) against a real MCP connector (mock SEC EDGAR server).

The mock server's control API decides exactly when an 8-K "lands"; worker ticks are run directly.
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
from conftest import BASE, CONNECTORS, CONTROL, EDGAR
from mcp import Client
from podos_api import background
from podos_api.connectors import call_tool, sign
from podos_api.db import fetchall


async def release(ticker: str, **custom) -> dict:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{EDGAR}/control/filings", json={"ticker": ticker, **custom}, headers=CONTROL)
    assert r.status_code == 201, r.text
    return r.json()


async def ticks(svc, n: int = 1) -> list[dict]:
    return [await background.tick(svc, "test-worker") for _ in range(n)]


async def start_feed(svc) -> None:
    """First poll only sets the cursor to 'now' (no history flood)."""
    await ticks(svc)


async def deliveries(svc, accession: str) -> list[dict]:
    async with svc.db.system_tx() as conn:
        return await fetchall(
            conn,
            """SELECT d.pod_id, d.outcome, d.monitors_triggered, d.doc_id FROM event_deliveries d JOIN events e USING (event_id)
               WHERE e.dedupe_key = %s ORDER BY d.pod_id""",
            (f"sec-edgar:{accession}",),
        )


async def docs(u, pod: str, needle: str) -> list[dict]:
    return [d for d in (await u.get(f"/v1/pods/{pod}/documents")).json() if needle in d["title"]]


async def test_mcp_contract(svc):
    async with Client(f"{EDGAR}/mcp") as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert {"latest_filings", "get_filing"} <= names
    f = await release("LGS")
    out = await call_tool(f"{EDGAR}/mcp", "latest_filings", {"since": "", "tickers": ["LGS"]})
    assert out["filings"][-1]["accession"] == f["accession"] and out["cursor"] == f["accepted_at"]
    assert (await call_tool(f"{EDGAR}/mcp", "latest_filings", {"since": out["cursor"], "tickers": ["LGS"]}))["filings"] == []
    async with httpx.AsyncClient() as c:
        assert (await c.post(f"{EDGAR}/control/filings", json={"ticker": "LGS"})).status_code == 403


async def test_8k_fans_out_to_watching_pods_exactly_once(svc, alice, bianca):
    await start_feed(svc)
    f = await release("NWSC")  # Alpha watches NWSC (monitor + case); Beta has an NWSC case but no NWSC monitor
    await ticks(svc, 2)
    rows = await deliveries(svc, f["accession"])
    assert [(r["pod_id"], r["outcome"], r["monitors_triggered"]) for r in rows] == [
        ("pod_alpha", "delivered", 1), ("pod_beta", "delivered", 0)]
    assert len(await docs(alice, "pod_alpha", "NWSC 8-K: Item")) == 1
    assert len(await docs(bianca, "pod_beta", "NWSC 8-K: Item")) == 1
    # Alpha's monitor ran because of the event and asked the PM to approve a brief.
    async with svc.db.pod_tx("pod_alpha") as conn:
        runs = await fetchall(conn, "SELECT routing FROM agent_runs WHERE pod_id = 'pod_alpha' AND mode = 'scheduled'")
    assert any(r["routing"].get("trigger") == "event" for r in runs)
    inbox = (await alice.get("/v1/pods/pod_alpha/work-items")).json()
    assert any("Approve catalyst brief" in i["title"] for i in inbox)
    # More ticks, same feed: nothing new anywhere.
    await ticks(svc, 2)
    assert len(await docs(alice, "pod_alpha", "NWSC 8-K: Item")) == 1
    assert len(await deliveries(svc, f["accession"])) == 2


async def test_unwatched_pods_get_nothing(svc, alice, bianca):
    await start_feed(svc)
    f = await release("HLXT")  # only Beta watches HLXT
    await ticks(svc, 2)
    assert [r["pod_id"] for r in await deliveries(svc, f["accession"])] == ["pod_beta"]
    assert await docs(alice, "pod_alpha", "HLXT") == []
    assert len(await docs(bianca, "pod_beta", "HLXT 8-K: Item")) == 1
    # Alpha's own watchlist is invisible to Beta's transaction (RLS on routing metadata).
    async with svc.db.pod_tx("pod_beta") as conn:
        assert {r["pod_id"] for r in await fetchall(conn, "SELECT pod_id FROM watchlists")} == {"pod_beta"}


async def test_dataset_licence_is_required(svc, pat, bianca):
    r = await pat.post("/v1/admin/access/grants/revoke", json={
        "pod_id": "pod_beta", "kind": "dataset", "item_id": "sec-edgar", "subject_kind": "group",
        "subject_id": "research_users", "reason": "licence lapsed"})
    assert r.status_code == 200
    await start_feed(svc)
    f = await release("NWSC")
    await ticks(svc, 2)
    by_pod = {r["pod_id"]: r for r in await deliveries(svc, f["accession"])}
    assert by_pod["pod_alpha"]["outcome"] == "delivered"
    assert by_pod["pod_beta"]["outcome"] == "not_licensed" and by_pod["pod_beta"]["doc_id"] is None
    assert await docs(bianca, "pod_beta", "NWSC 8-K: Item") == []


async def test_event_trigger_feature_gate(svc, pat):
    r = await pat.post("/v1/admin/access/grants/revoke", json={
        "pod_id": "pod_alpha", "kind": "feature", "item_id": "event_triggers", "subject_kind": "group",
        "subject_id": "research_users", "reason": "pilot paused"})
    assert r.status_code == 200
    await start_feed(svc)
    f = await release("NWSC")
    await ticks(svc, 2)
    alpha = [r for r in await deliveries(svc, f["accession"]) if r["pod_id"] == "pod_alpha"][0]
    assert alpha["outcome"] == "delivered" and alpha["monitors_triggered"] == 0  # scheduled run will pick it up


async def test_crash_during_delivery_is_exactly_once(svc, alice):
    await start_feed(svc)
    f = await release("NWSC")
    real = svc.search_backend.index_doc
    calls = {"n": 0}

    async def flaky(pod_id, doc):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("index node lost")
        return await real(pod_id, doc)

    svc.search_backend.index_doc = flaky
    try:
        await ticks(svc)  # first delivery attempt crashes mid-transaction -> rolled back
        async with svc.db.system_tx() as conn:
            ev = (await fetchall(conn, "SELECT route_attempts, routed_at FROM events WHERE dedupe_key = %s",
                                 (f"sec-edgar:{f['accession']}",)))[0]
        assert ev["route_attempts"] == 1 and ev["routed_at"] is None
        await ticks(svc, 2)
    finally:
        svc.search_backend.index_doc = real
    assert len(await docs(alice, "pod_alpha", "NWSC 8-K: Item")) == 1
    assert len(await deliveries(svc, f["accession"])) == 2


def _signed(body: dict, *, secret: str = "dev-newswire-webhook-secret-31c4", ts: int | None = None, nonce: str | None = None):
    raw = json.dumps(body).encode()
    ts_s, nonce_s = str(ts or int(time.time())), nonce or uuid.uuid4().hex
    return raw, {"content-type": "application/json", "x-podos-timestamp": ts_s, "x-podos-nonce": nonce_s,
                 "x-podos-signature": sign(secret, ts_s, nonce_s, raw)}


async def test_webhook_signature_replay_and_licensing(svc, alice, pat):
    batch = {"events": [{"id": "nw-1", "ticker": "LGS", "occurred_at": "2026-10-15T09:00:00Z",
                         "headline": "Lumen Grid wins second utility", "summary": "Five-year deal.", "source": "Newswire"}]}
    async with httpx.AsyncClient(base_url=BASE) as c:
        raw, h = _signed(batch)
        r = await c.post("/v1/connectors/newswire/events", content=raw, headers=h)
        assert r.status_code == 202 and r.json()["stored"] == 1
        assert (await c.post("/v1/connectors/newswire/events", content=raw, headers=h)).status_code == 409  # replay
        raw2, h2 = _signed(batch)
        assert (await c.post("/v1/connectors/newswire/events", content=raw2, headers=h2)).json()["duplicates"] == 1
        raw3, h3 = _signed(batch, secret="wrong")
        assert (await c.post("/v1/connectors/newswire/events", content=raw3, headers=h3)).status_code == 401
        raw4, h4 = _signed(batch, ts=int(time.time()) - 3600)
        assert (await c.post("/v1/connectors/newswire/events", content=raw4, headers=h4)).status_code == 401
    # Market news isn't licensed to anyone by default.
    await ticks(svc)
    assert await docs(alice, "pod_alpha", "Lumen Grid wins") == []
    # License it for Alpha's research group; the next story is delivered as a news document.
    r = await pat.post("/v1/admin/access/grants", json={
        "pod_id": "pod_alpha", "kind": "dataset", "item_id": "market-news", "subject_kind": "group",
        "subject_id": "research_users", "reason": "news licence for Alpha"})
    assert r.status_code == 201
    batch["events"][0].update(id="nw-2", headline="Lumen Grid wins third utility")
    async with httpx.AsyncClient(base_url=BASE) as c:
        raw, h = _signed(batch)
        assert (await c.post("/v1/connectors/newswire/events", content=raw, headers=h)).status_code == 202
    await ticks(svc)
    news = await docs(alice, "pod_alpha", "Lumen Grid wins third")
    assert len(news) == 1 and news[0]["kind"] == "news"


async def test_connector_outage_is_contained(svc):
    cfg = json.loads(CONNECTORS.read_text())
    for c in cfg:
        if c["connector_id"] == "sec-edgar":
            c["url"] = "http://127.0.0.1:9/mcp"
    CONNECTORS.write_text(json.dumps(cfg))
    await ticks(svc)  # sets the cursor
    stats = await ticks(svc)
    assert stats[0]["ingested"] == 0
    async with svc.db.system_tx() as conn:
        cur = (await fetchall(conn, "SELECT last_error FROM connector_cursors WHERE connector_id = 'sec-edgar'"))[0]
    assert cur["last_error"]
