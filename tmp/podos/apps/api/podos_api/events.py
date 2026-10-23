"""Event bus: watchlists, routing and per-pod delivery (PLAN_PROACTIVE.md F1, F4, §1).

Flow: a connector (connectors.py) stores a firm-wide ``events`` row once. ``route_events`` finds the
pods whose watchlist contains the event's ticker and calls ``deliver`` for each:

* the dataset must be licensed in that pod (OpenFGA ``dataset#can_read`` for the person the pod's
  monitor or case belongs to) or nothing is copied;
* inside ONE pod transaction: claim the (event, pod) delivery row, copy the filing into the pod's own
  vault + search index, and enqueue that pod's watching monitors with trigger="event" (only for owners
  with the ``event_triggers`` feature; others pick the document up on their next scheduled run).

A crash anywhere before commit leaves nothing behind; after commit the delivery row makes a retry a
no-op, so each pod sees each event exactly once. Pods never learn which other pods watch a ticker.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .db import Database, Jsonb, fetchall, fetchone
from .errors import Problem
from .search import index_name_for
from .security.context import Principal, build_pod_context
from .workitems import add_outbox

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger("podos.events")
ROUTE_BATCH = 20
MAX_ROUTE_ATTEMPTS = 5


# ---------------------------------------------------------------------------------------------
# Watchlists (F4): derived from each pod's monitors and research cases.
# ---------------------------------------------------------------------------------------------
async def refresh_watchlist(db: Database, pod_id: str) -> int:
    async with db.pod_tx(pod_id) as conn:
        mons = await fetchall(
            conn, "SELECT monitor_id::text AS id, owner_user_id, configuration FROM monitors WHERE pod_id = %s AND enabled", (pod_id,)
        )
        cases = await fetchall(
            conn, "SELECT case_id::text AS id, owner_user_id, tickers FROM research_cases WHERE pod_id = %s AND status = 'active'",
            (pod_id,),
        )
        rows = [(t.upper(), "monitor", m["id"], m["owner_user_id"]) for m in mons for t in m["configuration"].get("tickers", [])]
        rows += [(t.upper(), "case", c["id"], c["owner_user_id"]) for c in cases for t in c["tickers"]]
        await conn.execute("DELETE FROM watchlists WHERE pod_id = %s", (pod_id,))
        for ticker, reason, ref, owner in set(rows):
            await conn.execute(
                "INSERT INTO watchlists(pod_id, ticker, reason, ref_id, owner_user_id) VALUES (%s,%s,%s,%s,%s)",
                (pod_id, ticker, reason, ref, owner),
            )
    return len(rows)


async def refresh_all_watchlists(svc: Services) -> int:
    async with svc.db.system_tx() as conn:
        pods = [r["pod_id"] for r in await fetchall(conn, "SELECT pod_id FROM pods")]
    return sum([await refresh_watchlist(svc.db, p) for p in pods])


async def watched_tickers(svc: Services) -> list[str]:
    """Distinct tickers any pod watches (no pod ids): what connectors ask their sources about."""
    async with svc.db.system_tx() as conn:
        return [r["ticker"] for r in await fetchall(conn, "SELECT DISTINCT ticker FROM watchlists ORDER BY ticker")]


# ---------------------------------------------------------------------------------------------
# Ingestion helper used by connectors (pull) and webhooks (push).
# ---------------------------------------------------------------------------------------------
async def store_event(
    svc: Services, *, connector_id: str, dataset_id: str, event_type: str, ticker: str | None,
    occurred_at: datetime, dedupe_key: str, payload: dict[str, Any],
) -> str | None:
    async with svc.db.system_tx() as conn:
        row = await fetchone(
            conn,
            """INSERT INTO events(connector_id, dataset_id, event_type, ticker, occurred_at, dedupe_key, payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING event_id::text""",
            (connector_id, dataset_id, event_type, ticker.upper() if ticker else None, occurred_at, dedupe_key, Jsonb(payload)),
        )
    return row["event_id"] if row else None


# ---------------------------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------------------------
async def route_events(svc: Services) -> dict[str, int]:
    stats = {"events": 0, "deliveries": 0, "not_licensed": 0}
    async with svc.db.system_tx() as conn:
        batch = await fetchall(
            conn,
            """SELECT * FROM events WHERE routed_at IS NULL AND route_attempts < %s
               ORDER BY received_at LIMIT %s""",
            (MAX_ROUTE_ATTEMPTS, ROUTE_BATCH),
        )
    for ev in batch:
        try:
            async with svc.db.system_tx() as conn:
                pods = [r["pod_id"] for r in await fetchall(
                    conn, "SELECT DISTINCT pod_id FROM watchlists WHERE ticker = %s ORDER BY pod_id", (ev["ticker"],))]
            for pod_id in pods:
                outcome = await deliver(svc, ev, pod_id)
                if outcome == "delivered":
                    stats["deliveries"] += 1
                elif outcome == "not_licensed":
                    stats["not_licensed"] += 1
            async with svc.db.system_tx() as conn:
                await conn.execute("UPDATE events SET routed_at = now() WHERE event_id = %s", (ev["event_id"],))
            stats["events"] += 1
        except Exception as exc:  # retried next tick; poison events stop after MAX_ROUTE_ATTEMPTS
            log.warning("routing event %s failed: %s", ev["event_id"], exc)
            async with svc.db.system_tx() as conn:
                await conn.execute(
                    "UPDATE events SET route_attempts = route_attempts + 1, last_error = %s WHERE event_id = %s",
                    (str(exc)[:500], ev["event_id"]),
                )
    return stats


async def _owner_access(svc: Services, pod_id: str, owner: str, dataset_id: str) -> tuple[bool, bool]:
    """(dataset licensed, event_triggers feature) for this owner in this pod, resolved now."""
    try:
        ctx = await build_pod_context(
            svc.db, svc.pdp, Principal(subject_id=owner, display_name=owner, title="", platform_roles=[], session_id="router"),
            pod_id,
        )
    except Problem:
        return False, False
    return svc.pdp.dataset_readable(ctx, dataset_id), svc.pdp.feature_enabled(ctx, "event_triggers")


def document_for(ev: dict[str, Any]) -> dict[str, str]:
    """How an event reads inside a pod's vault (kind, title, body, source, classification)."""
    p, ticker = ev["payload"], ev["ticker"]
    when = p.get("accepted_at", "")[:16].replace("T", " ")
    if ev["event_type"] == "filing":
        return {
            "kind": "filing", "classification": "public",
            "title": f"{ticker} {p.get('form', '')}: {p.get('title', '')}"[:300],
            "source": f"SEC EDGAR · {p.get('form', '')}".strip(),
            "body": (f"{p.get('company', ticker)} ({ticker}) filed a {p.get('form', 'filing')} with the SEC"
                     f" (accepted {when} UTC).\n\n{p.get('title', '')}\n\n{p.get('summary', '')}\n\n"
                     f"Source: {p.get('url', 'SEC EDGAR')}").strip(),
        }
    return {
        "kind": "news", "classification": "internal",  # licensed content: firm-internal, never public
        "title": f"{ticker}: {p.get('title', '')}"[:300],
        "source": p.get("source") or ev["connector_id"],
        "body": f"{p.get('title', '')}\n\n{p.get('summary', '')}\n\nSource: {p.get('url', '')} ({when} UTC)".strip(),
    }


async def deliver(svc: Services, ev: dict[str, Any], pod_id: str) -> str:
    async with svc.db.system_tx() as conn:
        watchers = await fetchall(
            conn, "SELECT reason, ref_id, owner_user_id FROM watchlists WHERE pod_id = %s AND ticker = %s",
            (pod_id, ev["ticker"]),
        )
    access = {o: await _owner_access(svc, pod_id, o, ev["dataset_id"]) for o in {w["owner_user_id"] for w in watchers}}
    licensed_owners = {o for o, (licensed, _) in access.items() if licensed}
    triggered = [w["ref_id"] for w in watchers
                 if w["reason"] == "monitor" and w["owner_user_id"] in licensed_owners and access[w["owner_user_id"]][1]]

    backend = svc.search_backend
    async with svc.db.pod_tx(pod_id) as conn:
        claimed = await fetchone(
            conn,
            """INSERT INTO event_deliveries(event_id, pod_id, outcome) VALUES (%s,%s,%s)
               ON CONFLICT (event_id, pod_id) DO NOTHING RETURNING event_id""",
            (ev["event_id"], pod_id, "delivered" if licensed_owners else "not_licensed"),
        )
        if claimed is None:
            return "duplicate"
        if not licensed_owners:
            return "not_licensed"
        d = document_for(ev)
        doc = await fetchone(
            conn,
            """INSERT INTO research_documents
                 (pod_id, index_name, kind, title, ticker, body, source, published_at, classification, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,now(),%s,%s)
               RETURNING doc_id, case_id, kind, title, ticker, body, source, published_at, classification""",
            (pod_id, index_name_for(pod_id), d["kind"], d["title"], ev["ticker"], d["body"], d["source"],
             d["classification"], f"connector:{ev['connector_id']}"),
        )
        assert doc is not None
        n = 0
        for monitor_id in sorted(set(triggered)):
            if await add_outbox(conn, pod_id, f"monitor:{monitor_id}:event:{ev['event_id']}", "monitor.run",
                                {"monitor_id": monitor_id, "trigger": "event", "event_id": str(ev["event_id"])}):
                n += 1
        await conn.execute(
            "UPDATE event_deliveries SET doc_id = %s, monitors_triggered = %s WHERE event_id = %s AND pod_id = %s",
            (doc["doc_id"], n, ev["event_id"], pod_id),
        )
        # Index inside the transaction: if the pod index rejects it, the whole delivery rolls back and retries.
        await backend.index_doc(pod_id, doc)
    await svc.audit(actor=f"connector:{ev['connector_id']}", action="event.delivered", target=str(ev["event_id"]),
                    pod_id=pod_id, detail={"ticker": ev["ticker"], "monitors_triggered": n})
    return "delivered"
