"""Today briefing (PLAN_PROACTIVE.md §2): what changed, what is waiting on you, what finished, what is
going stale. A typed spec (never free text) built from the pod's own data inside one pod transaction,
so it can only ever contain this pod's content. Every item says *why* it is shown.

Ranking is deliberately simple and explainable for now: things you own or watch first, newest first;
dismissed items stay hidden. §5 replaces the ordering with a learned relevance score.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from podos_contracts.models import PodContext

from .db import fetchall

LOOKBACK = timedelta(hours=36)
STALE_AFTER = timedelta(days=21)
MAX_PER_SECTION = 6


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


async def build_briefing(svc, ctx: PodContext) -> dict[str, Any]:
    pod, me = ctx.active_pod_id, ctx.subject_id
    now = datetime.now(UTC)
    since = now - LOOKBACK
    async with svc.db.pod_tx(pod) as conn:
        dismissed = {r["item_key"] for r in await fetchall(
            conn, "SELECT item_key FROM attention_events WHERE pod_id = %s AND subject_id = %s AND action = 'dismissed'",
            (pod, me))}
        waiting = await fetchall(
            conn,
            """SELECT work_item_id::text AS id, title, summary, category, state, priority, source_agent_id, created_at, deadline_at
               FROM work_items WHERE pod_id = %s AND assignee_user_id = %s AND archived_at IS NULL
                 AND (state IN ('WAITING_FOR_HUMAN', 'CHANGES_REQUESTED') OR (read_at IS NULL AND kind = 'actionable'))
               ORDER BY (priority = 'high') DESC, created_at DESC LIMIT 20""",
            (pod, me),
        )
        mine = await fetchall(
            conn,
            """SELECT DISTINCT upper(t) AS ticker FROM (
                 SELECT unnest(tickers) AS t FROM research_cases WHERE pod_id = %s AND owner_user_id = %s
                 UNION ALL
                 SELECT jsonb_array_elements_text(configuration->'tickers') FROM monitors
                   WHERE pod_id = %s AND (owner_user_id = %s OR configuration->'notify' ? %s)) x""",
            (pod, me, pod, me, me),
        )
        changed = await fetchall(
            conn,
            """SELECT doc_id::text AS id, kind, title, ticker, source, published_at, created_by FROM research_documents
               WHERE pod_id = %s AND published_at >= %s AND kind IN ('filing', 'news', 'transcript')
               ORDER BY published_at DESC LIMIT 30""",
            (pod, since),
        )
        finished = await fetchall(
            conn,
            """SELECT work_item_id::text AS id, title, summary, category, created_at, evidence_artifact_id::text AS artifact_id
               FROM work_items WHERE pod_id = %s AND assignee_user_id = %s AND category = 'report' AND created_at >= %s
               ORDER BY created_at DESC LIMIT 10""",
            (pod, me, since),
        )
        stale = await fetchall(
            conn,
            """SELECT c.case_id::text AS id, c.title, c.tickers, greatest(c.updated_at,
                      coalesce((SELECT max(d.published_at) FROM research_documents d
                                WHERE d.pod_id = c.pod_id AND d.case_id = c.case_id AND d.created_by NOT LIKE 'connector:%%'),
                               c.updated_at)) AS touched
               FROM research_cases c WHERE c.pod_id = %s AND c.owner_user_id = %s AND c.status = 'active'""",
            (pod, me),
        )
    watched = {r["ticker"] for r in mine}

    def keep(kind: str, item_id: str) -> bool:
        return f"{kind}:{item_id}" not in dismissed

    sections: list[dict[str, Any]] = []
    w_items = [{
        "key": f"work:{w['id']}", "kind": "approval" if w["state"] == "WAITING_FOR_HUMAN" else "work",
        "title": w["title"], "detail": w["summary"],
        "why": "Waiting for your approval" if w["state"] == "WAITING_FOR_HUMAN" else
               "You asked for changes; check the revision" if w["state"] == "CHANGES_REQUESTED" else "Needs your attention",
        "at": _iso(w["created_at"]), "href": f"inbox?item={w['id']}", "priority": w["priority"],
    } for w in waiting if keep("work", w["id"])]
    if w_items:
        sections.append({"id": "waiting", "title": "Waiting on you", "items": w_items[:MAX_PER_SECTION]})

    c_items = []
    for d in changed:
        if not keep("doc", d["id"]):
            continue
        yours = d["ticker"] in watched
        via_feed = d["created_by"].startswith("connector:")
        why = (f"New {d['kind']} for {d['ticker']}, which you follow" if yours else
               f"New {d['kind']} for {d['ticker']}, on your pod's watchlist" if via_feed else f"Added to {d['ticker'] or 'the vault'}")
        c_items.append({
            "key": f"doc:{d['id']}", "kind": d["kind"], "title": d["title"], "why": why, "ticker": d["ticker"],
            "source": d["source"], "at": _iso(d["published_at"]), "doc_id": d["id"], "yours": yours,
            "ask": f"What changed in the latest {d['ticker']} {d['kind']} and does it matter for our thesis?",
        })
    c_items.sort(key=lambda i: (not i["yours"], -(datetime.fromisoformat(i["at"]).timestamp())))
    if c_items:
        sections.append({"id": "changed", "title": "What changed", "items": c_items[:MAX_PER_SECTION]})

    f_items = [{
        "key": f"work:{f['id']}", "kind": "report", "title": f["title"], "detail": f["summary"],
        "why": "Finished in the background for you", "at": _iso(f["created_at"]), "href": f"inbox?item={f['id']}",
        "artifact_id": f["artifact_id"],
    } for f in finished if keep("work", f["id"])]
    if f_items:
        sections.append({"id": "finished", "title": "Ready for you", "items": f_items[:MAX_PER_SECTION]})

    s_items = []
    for c in stale:
        age = now - c["touched"]
        if age >= STALE_AFTER and keep("case", c["id"]):
            s_items.append({
                "key": f"case:{c['id']}", "kind": "stale", "title": c["title"],
                "why": f"Thesis not updated in {age.days} days", "at": _iso(c["touched"]), "href": f"vault/{c['id']}",
                "ticker": (c["tickers"] or [None])[0],
                "ask": f"Is our {', '.join(c['tickers'])} thesis still intact? Check what changed since it was last updated.",
            })
    if s_items:
        sections.append({"id": "stale", "title": "Going stale", "items": s_items[:MAX_PER_SECTION]})

    n_wait, n_new = len(w_items), sum(1 for i in c_items if i["kind"] in ("filing", "news"))
    parts = []
    if n_wait:
        parts.append(f"{n_wait} {'thing needs' if n_wait == 1 else 'things need'} you")
    if n_new:
        parts.append(f"{n_new} new {'filing or story' if n_new == 1 else 'filings and stories'} since yesterday")
    if f_items:
        parts.append(f"{len(f_items)} {'report is' if len(f_items) == 1 else 'reports are'} ready")
    summary = (", ".join(parts) + ".").capitalize() if parts else "Nothing new since yesterday. Your monitors are watching."
    return {"generated_at": now.isoformat(), "summary": summary, "sections": sections, "watched_tickers": sorted(watched)}
