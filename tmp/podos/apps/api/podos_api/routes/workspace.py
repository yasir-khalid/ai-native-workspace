"""Research Vault, direct exports, artifact downloads, monitors and inbox."""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from podos_contracts.models import Classification, DecisionCommand, DocumentSpec, PodContext
from pydantic import BaseModel, Field

from .. import background
from ..briefing import build_briefing
from ..capabilities.service import artifact_ref
from ..connectors import load_connectors
from ..db import Jsonb, fetchall, fetchone
from ..errors import Problem, forbidden, not_found
from ..events import refresh_watchlist
from ..governance import load_snapshot
from ..objectstore import ObjectStoreError
from ..security.context import (
    SESSION_COOKIE,
    current_principal,
    is_active_member,
    pod_ctx_from_header,
    pod_ctx_from_path,
    principal_from_token,
    services,
)
from ..security.tokens import TokenError, issue_download, verify_download
from ..workitems import decide, list_work_items
from .threads import _jsonable

router = APIRouter(prefix="/v1")


# ---------------------------------------------------------------------------
# Research Vault
# ---------------------------------------------------------------------------
class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    thesis: str = Field(default="", max_length=4000)
    tickers: list[str] = Field(default_factory=list, max_length=20)


class NoteCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    ticker: str | None = Field(default=None, max_length=12)
    case_id: str | None = None
    kind: Literal["note", "model", "transcript"] = "note"


@router.get("/pods/{pod_id}/research-cases")
async def list_cases(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        rows = await fetchall(
            conn,
            """SELECT c.case_id, c.title, c.thesis, c.tickers, c.status, c.owner_user_id, c.classification, c.created_at,
                      c.updated_at,
                      (SELECT count(*) FROM research_documents d WHERE d.pod_id = c.pod_id AND d.case_id = c.case_id) AS documents,
                      (SELECT count(*) FROM artifacts a WHERE a.pod_id = c.pod_id AND a.case_id = c.case_id) AS artifacts
               FROM research_cases c WHERE c.pod_id = %s ORDER BY c.updated_at DESC""",
            (ctx.active_pod_id,),
        )
    return [_jsonable(r) for r in rows]


@router.post("/pods/{pod_id}/research-cases", status_code=201)
async def create_case(body: CaseCreate, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn,
            """INSERT INTO research_cases(pod_id, title, thesis, tickers, owner_user_id) VALUES (%s,%s,%s,%s,%s)
               RETURNING case_id, title, thesis, tickers, status, created_at""",
            (ctx.active_pod_id, body.title, body.thesis, [t.upper() for t in body.tickers], ctx.subject_id),
        )
    await refresh_watchlist(svc.db, ctx.active_pod_id)
    return _jsonable(row)


@router.get("/pods/{pod_id}/research-cases/{case_id}")
async def get_case(case_id: str, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        c = await fetchone(conn, "SELECT * FROM research_cases WHERE pod_id = %s AND case_id::text = %s",
                           (ctx.active_pod_id, case_id))
        if c is None:
            raise not_found("research case")
        docs = await fetchall(
            conn,
            """SELECT doc_id, kind, title, ticker, source, published_at, classification, created_by, left(body, 600) AS excerpt
               FROM research_documents WHERE pod_id = %s AND case_id = %s ORDER BY published_at DESC""",
            (ctx.active_pod_id, c["case_id"]),
        )
        arts = await fetchall(
            conn,
            """SELECT artifact_id, filename, mime_type, title, capability_id, classification, size_bytes, created_at, created_by
               FROM artifacts WHERE pod_id = %s AND case_id = %s ORDER BY created_at DESC""",
            (ctx.active_pod_id, c["case_id"]),
        )
    return {**_jsonable(c), "documents": [_jsonable(d) for d in docs], "artifacts": [_jsonable(a) for a in arts]}


@router.get("/pods/{pod_id}/documents")
async def list_documents(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services), kind: str | None = None):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        rows = await fetchall(
            conn,
            """SELECT d.doc_id, d.kind, d.title, d.ticker, d.source, d.published_at, d.classification, d.case_id,
                      left(d.body, 400) AS excerpt, c.title AS case_title
               FROM research_documents d LEFT JOIN research_cases c ON c.pod_id = d.pod_id AND c.case_id = d.case_id
               WHERE d.pod_id = %s AND (%s::text IS NULL OR d.kind = %s) ORDER BY d.published_at DESC LIMIT 200""",
            (ctx.active_pod_id, kind, kind),
        )
    return [_jsonable(r) for r in rows]


@router.get("/pods/{pod_id}/documents/{doc_id}")
async def get_document(doc_id: str, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn,
            """SELECT doc_id, kind, title, ticker, source, published_at, classification, case_id, body, created_by
               FROM research_documents WHERE pod_id = %s AND doc_id::text = %s""",
            (ctx.active_pod_id, doc_id),
        )
    if row is None:
        raise not_found("document")
    return _jsonable(row)


@router.post("/pods/{pod_id}/documents", status_code=201)
async def add_note(body: NoteCreate, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    if body.case_id:
        async with svc.db.pod_tx(ctx.active_pod_id) as conn:
            if await fetchone(conn, "SELECT 1 FROM research_cases WHERE pod_id = %s AND case_id::text = %s",
                              (ctx.active_pod_id, body.case_id)) is None:
                raise not_found("research case")
    row = await svc.index(ctx.active_pod_id).add_document(
        kind=body.kind, title=body.title, body=body.body, source="Analyst note", created_by=ctx.subject_id,
        ticker=body.ticker.upper() if body.ticker else None, case_id=body.case_id,
    )
    return _jsonable(row)


@router.get("/pods/{pod_id}/search")
async def search(q: str, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    snap = await load_snapshot(svc.db)
    svc.gov.require_capability(ctx, snap, "research.search", classification=Classification.pod_confidential)
    rows = await svc.index(ctx.active_pod_id).search(q, limit=10)
    return [{k: v for k, v in r.items() if k != "body"} for r in rows]


@router.get("/pods/{pod_id}/artifacts")
async def list_artifacts(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        rows = await fetchall(
            conn,
            """SELECT artifact_id, filename, mime_type, title, capability_id, classification, size_bytes, created_at,
                      created_by, run_id, case_id, provenance
               FROM artifacts WHERE pod_id = %s ORDER BY created_at DESC LIMIT 200""",
            (ctx.active_pod_id,),
        )
    return [_jsonable(r) for r in rows]


# ---------------------------------------------------------------------------
# Direct workspace export (no agent term; capability + resource + rollout still checked)
# ---------------------------------------------------------------------------
class ExportRequest(BaseModel):
    capability_id: Literal["document.export.pptx", "document.export.docx"]
    spec: DocumentSpec
    idempotency_key: str = Field(min_length=8, max_length=200)
    research_case_id: str | None = None


@router.post("/pods/{pod_id}/exports", status_code=201)
async def export(body: ExportRequest, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    snap = await load_snapshot(svc.db)
    grant = svc.gov.require_capability(ctx, snap, body.capability_id, classification=body.spec.classification)
    if body.research_case_id:
        async with svc.db.pod_tx(ctx.active_pod_id) as conn:
            if await fetchone(conn, "SELECT 1 FROM research_cases WHERE pod_id = %s AND case_id::text = %s",
                              (ctx.active_pod_id, body.research_case_id)) is None:
                raise not_found("research case")
    async with svc.db.system_tx() as conn:
        pod = await fetchone(conn, "SELECT display_name FROM pods WHERE pod_id = %s", (ctx.active_pod_id,))
    art, created = await svc.capabilities.render_document(
        grant, body.spec, idempotency_key=f"export:{ctx.subject_id}:{body.idempotency_key}",
        pod_label=pod["display_name"], case_id=body.research_case_id, provenance={"source": "workspace_export"},
    )
    return {**art.model_dump(mode="json"), "created": created}


# ---------------------------------------------------------------------------
# Artifact download: issue short-lived link (reauthorised), redeem (reauthorised again)
# ---------------------------------------------------------------------------
@router.post("/artifacts/{artifact_id}/download")
async def issue_link(artifact_id: str, ctx: PodContext = Depends(pod_ctx_from_header), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(conn, "SELECT * FROM artifacts WHERE pod_id = %s AND artifact_id::text = %s",
                             (ctx.active_pod_id, artifact_id))
    if row is None:
        raise not_found("artifact")
    if row["classification"] == "restricted" and svc.pdp.clearance(ctx) != Classification.restricted:
        raise forbidden("classification_exceeds_clearance")
    token, exp = issue_download(svc.settings, artifact_id=artifact_id, pod_id=ctx.active_pod_id, subject_id=ctx.subject_id)
    await svc.audit(actor=ctx.subject_id, action="artifact.link_issued", target=artifact_id, pod_id=ctx.active_pod_id)
    return {"url": f"{svc.settings.public_api_url}/v1/downloads/{token}", "expires_at": exp,
            "artifact": artifact_ref(row).model_dump(mode="json")}


@router.get("/downloads/{token}")
async def redeem(token: str, request: Request, svc=Depends(services)):
    try:
        claims = verify_download(svc.settings, token)
    except TokenError:
        raise Problem(410, "link_expired", "download link is invalid or expired") from None
    # Bound to the same signed-in user; membership re-checked at redemption.
    session = request.cookies.get(SESSION_COOKIE)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        session = auth[7:]
    principal = await principal_from_token(svc.db, svc.settings, session)
    if principal.subject_id != claims["sub"]:
        raise forbidden("link_not_for_you")
    if not await is_active_member(svc.pdp, claims["pod"], claims["sub"]):
        raise forbidden("not_a_pod_member", "pod membership no longer active")
    async with svc.db.pod_tx(claims["pod"]) as conn:
        row = await fetchone(conn, "SELECT * FROM artifacts WHERE pod_id = %s AND artifact_id::text = %s",
                             (claims["pod"], claims["art"]))
    if row is None:
        raise not_found("artifact")
    try:
        data = await svc.store.get(claims["pod"], row["object_key"])
    except ObjectStoreError:
        raise Problem(503, "artifact_storage_failed") from None
    await svc.audit(actor=principal.subject_id, action="artifact.downloaded", target=claims["art"], pod_id=claims["pod"])
    return Response(data, media_type=row["mime_type"], headers={
        "Content-Disposition": f'attachment; filename="{row["filename"]}"', "Cache-Control": "no-store",
    })


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------
class MonitorCreate(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    tickers: list[str] = Field(min_length=1, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    schedule_seconds: int = Field(default=3600, ge=30, le=86400)
    notify: list[str] = Field(default_factory=list, max_length=20)


class MonitorPatch(BaseModel):
    enabled: bool | None = None
    schedule_seconds: int | None = Field(default=None, ge=30, le=86400)


@router.get("/pods/{pod_id}/monitors")
async def list_monitors(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        rows = await fetchall(
            conn,
            """SELECT m.monitor_id, m.name, m.owner_user_id, m.configuration, m.enabled, m.schedule_seconds, m.next_run_at,
                      m.last_run_at, m.checkpoint, m.agent_id, m.created_at,
                      (SELECT state FROM agent_runs r WHERE r.pod_id = m.pod_id AND r.monitor_id = m.monitor_id
                       ORDER BY created_at DESC LIMIT 1) AS last_run_state,
                      (SELECT routing->>'trigger' FROM agent_runs r WHERE r.pod_id = m.pod_id AND r.monitor_id = m.monitor_id
                       ORDER BY created_at DESC LIMIT 1) AS last_trigger
               FROM monitors m WHERE m.pod_id = %s ORDER BY m.created_at""",
            (ctx.active_pod_id,),
        )
    return [_jsonable(r) for r in rows]


@router.post("/pods/{pod_id}/monitors", status_code=201)
async def create_monitor(body: MonitorCreate, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    snap = await load_snapshot(svc.db)
    svc.gov.require_capability(ctx, snap, "research.monitor.create", classification=Classification.pod_confidential)
    svc.gov.require_agent(ctx, snap, "catalyst-monitor", "background_execution")
    notify = [u for u in (body.notify or [ctx.subject_id]) if await is_active_member(svc.pdp, ctx.active_pod_id, u)]
    approver = ctx.subject_id if ctx.pod_role == "pm" else None
    if approver is None:
        pms = sorted(sub for sub, role in await svc.pdp.members(ctx.active_pod_id) if role == "pm")
        approver = pms[0] if pms else ctx.subject_id
    cfg = {"name": body.name, "tickers": [t.upper() for t in body.tickers], "event_types": ["filing"],
           "keywords": body.keywords, "approval_recipient": approver, "notify": notify or [ctx.subject_id]}
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn,
            """INSERT INTO monitors(pod_id, owner_user_id, configuration, name, schedule_seconds, checkpoint, next_run_at)
               VALUES (%s,%s,%s,%s,%s,%s, now() + make_interval(secs => %s)) RETURNING monitor_id, name""",
            (ctx.active_pod_id, ctx.subject_id, Jsonb(cfg), body.name, body.schedule_seconds,
             Jsonb({"last_seen": "2026-10-01T00:00:00+00:00"}), body.schedule_seconds),
        )
    await refresh_watchlist(svc.db, ctx.active_pod_id)
    return _jsonable(row)


async def _own_monitor(svc, ctx: PodContext, monitor_id: str) -> dict[str, Any]:
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        m = await fetchone(conn, "SELECT * FROM monitors WHERE pod_id = %s AND monitor_id::text = %s",
                           (ctx.active_pod_id, monitor_id))
    if m is None:
        raise not_found("monitor")
    if m["owner_user_id"] != ctx.subject_id and ctx.pod_role != "pm":
        raise forbidden("not_monitor_owner")
    return m


@router.patch("/pods/{pod_id}/monitors/{monitor_id}")
async def patch_monitor(monitor_id: str, body: MonitorPatch, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    m = await _own_monitor(svc, ctx, monitor_id)
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        await conn.execute(
            """UPDATE monitors SET enabled = coalesce(%s, enabled), schedule_seconds = coalesce(%s, schedule_seconds),
                   updated_at = now() WHERE monitor_id = %s""",
            (body.enabled, body.schedule_seconds, m["monitor_id"]),
        )
    await refresh_watchlist(svc.db, ctx.active_pod_id)
    return {"status": "updated"}


@router.post("/pods/{pod_id}/monitors/{monitor_id}/run", status_code=202)
async def run_monitor_now(monitor_id: str, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    await _own_monitor(svc, ctx, monitor_id)
    event_id = await background.enqueue_monitor_now(svc, ctx.active_pod_id, monitor_id, ctx.subject_id)
    return {"status": "queued", "outbox_event_id": event_id}


@router.post("/pods/{pod_id}/simulate/filing", status_code=201)
async def simulate_filing(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    """Dev-only: ask the mock SEC EDGAR MCP server to file the next scripted 8-K for a ticker this pod
    watches. It then flows through the real pipeline (connector poll -> event -> router -> every
    watching pod), so other pods watching the same ticker receive it too, each in its own vault."""
    if not svc.settings.dev_login_enabled:
        raise not_found()
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        mons = await fetchall(conn, "SELECT configuration FROM monitors WHERE pod_id = %s AND enabled ORDER BY created_at",
                              (ctx.active_pod_id,))
    tickers = [t for m in mons for t in m["configuration"].get("tickers", [])]
    edgar = load_connectors(svc).get("sec-edgar")
    if not tickers or edgar is None:
        raise not_found("feed")
    base = edgar.url.removesuffix("/mcp")
    token = os.environ.get("PODOS_CONNECTOR_CONTROL_TOKEN", "dev-connector-control-token")
    async with httpx.AsyncClient(timeout=5) as client:
        for ticker in tickers:
            r = await client.post(f"{base}/control/filings", json={"ticker": ticker}, headers={"x-control-token": token})
            if r.status_code == 201:
                return {"status": "filed", **{k: r.json()[k] for k in ("ticker", "form", "title", "accession", "accepted_at")}}
            if r.status_code != 404:
                raise Problem(502, "feed_unavailable", f"mock SEC feed refused the request ({r.status_code})")
    raise Problem(409, "feed_exhausted", "no scripted filings left for this pod's tickers (restart the mock server)")


# ---------------------------------------------------------------------------
# Inbox / work items
# ---------------------------------------------------------------------------
@router.get("/pods/{pod_id}/work-items")
async def inbox(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services), view: str = "inbox"):
    return [_jsonable(r) for r in await list_work_items(svc, ctx, view)]


@router.get("/work-items/{work_item_id}")
async def get_work_item(work_item_id: str, ctx: PodContext = Depends(pod_ctx_from_header), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        wi = await fetchone(conn, "SELECT * FROM work_items WHERE pod_id = %s AND work_item_id::text = %s",
                            (ctx.active_pod_id, work_item_id))
        if wi is None or wi["assignee_user_id"] != ctx.subject_id:
            raise not_found("work item")
        events = await fetchall(
            conn, """SELECT actor, event_type, from_state, to_state, version, occurred_at FROM work_item_events
                     WHERE pod_id = %s AND work_item_id = %s ORDER BY event_id""",
            (ctx.active_pod_id, wi["work_item_id"]),
        )
    return {**_jsonable(wi), "events": [_jsonable(e) for e in events]}


@router.post("/work-items/{work_item_id}/decisions")
async def post_decision(work_item_id: str, cmd: DecisionCommand, ctx: PodContext = Depends(pod_ctx_from_header),
                        svc=Depends(services)):
    return await decide(svc, ctx, work_item_id, cmd)


class WorkItemAction(BaseModel):
    action: Literal["read", "unread", "archive", "unarchive", "snooze"]
    snooze_hours: int = Field(default=4, ge=1, le=168)


@router.post("/work-items/{work_item_id}/state")
async def set_state(work_item_id: str, body: WorkItemAction, ctx: PodContext = Depends(pod_ctx_from_header),
                    svc=Depends(services)):
    sql = {
        "read": "read_at = coalesce(read_at, now())",
        "unread": "read_at = NULL",
        "archive": "archived_at = now(), read_at = coalesce(read_at, now())",
        "unarchive": "archived_at = NULL",
        "snooze": "snoozed_until = now() + make_interval(hours => %(h)s)",
    }[body.action]
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        cur = await conn.execute(
            f"""UPDATE work_items SET {sql}, updated_at = now()
                WHERE pod_id = %(pod)s AND work_item_id::text = %(id)s AND assignee_user_id = %(sub)s""",
            {"h": body.snooze_hours, "pod": ctx.active_pod_id, "id": work_item_id, "sub": ctx.subject_id},
        )
    if cur.rowcount == 0:
        raise not_found("work item")
    return {"status": "ok"}


@router.get("/pods/{pod_id}/inbox/summary")
async def inbox_summary(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn,
            """SELECT count(*) FILTER (WHERE read_at IS NULL AND archived_at IS NULL) AS unread,
                      count(*) FILTER (WHERE state = 'WAITING_FOR_HUMAN' AND archived_at IS NULL) AS actionable
               FROM work_items WHERE pod_id = %s AND assignee_user_id = %s""",
            (ctx.active_pod_id, ctx.subject_id),
        )
    return row


@router.get("/pods/{pod_id}/members")
async def members(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    roles = dict(await svc.pdp.members(ctx.active_pod_id))
    async with svc.db.system_tx() as conn:
        users = await fetchall(conn, "SELECT subject_id, display_name, title FROM users WHERE subject_id = ANY(%s)",
                               (list(roles),))
    return sorted(({**u, "pod_role": roles[u["subject_id"]]} for u in users), key=lambda u: (u["pod_role"], u["display_name"]))



# ---------------------------------------------------------------------------
# Today briefing (PLAN_PROACTIVE.md §2)
# ---------------------------------------------------------------------------
@router.get("/pods/{pod_id}/briefing")
async def briefing(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    if not svc.pdp.feature_enabled(ctx, "briefing"):
        raise forbidden("feature_disabled", "the Today briefing isn't enabled for you in this pod")
    return await build_briefing(svc, ctx)


class BriefingFeedback(BaseModel):
    item_key: str = Field(pattern=r"^(work|doc|case):[0-9a-f-]{36}$")
    action: Literal["opened", "asked", "dismissed", "more", "less"]
    ticker: str | None = Field(default=None, max_length=12)


@router.post("/pods/{pod_id}/briefing/feedback", status_code=204)
async def briefing_feedback(body: BriefingFeedback, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    if not svc.pdp.feature_enabled(ctx, "briefing"):
        raise forbidden("feature_disabled")
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        await conn.execute(
            """INSERT INTO attention_events(pod_id, subject_id, item_key, item_kind, action, ticker)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (ctx.active_pod_id, ctx.subject_id, body.item_key, body.item_key.split(":")[0], body.action, body.ticker),
        )
    return Response(status_code=204)


__all__ = ["router", "current_principal"]
