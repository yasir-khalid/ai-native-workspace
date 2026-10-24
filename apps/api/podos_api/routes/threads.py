"""Threads, runs, run-event streaming (SSE) and My Work."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from podos_contracts.models import TERMINAL_RUN_EVENTS, PodContext, RunRequest
from pydantic import BaseModel, Field

from ..db import fetchall, fetchone
from ..errors import not_found
from ..security.context import is_active_member, pod_ctx_from_header, pod_ctx_from_path, services

router = APIRouter(prefix="/v1")


class ThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    research_case_id: str | None = None


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if k.endswith("_id") and v is not None else v)
            for k, v in row.items()}


@router.post("/pods/{pod_id}/threads", status_code=201)
async def create_thread(body: ThreadCreate, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        if body.research_case_id:
            ok = await fetchone(conn, "SELECT 1 FROM research_cases WHERE pod_id = %s AND case_id::text = %s",
                                (ctx.active_pod_id, body.research_case_id))
            if ok is None:
                raise not_found("research case")
        row = await fetchone(
            conn,
            "INSERT INTO threads(pod_id, case_id, created_by, title) VALUES (%s,%s,%s,%s) RETURNING *",
            (ctx.active_pod_id, body.research_case_id, ctx.subject_id, body.title),
        )
    return _jsonable(row)


@router.get("/pods/{pod_id}/threads")
async def list_threads(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        rows = await fetchall(
            conn,
            """SELECT t.thread_id, t.case_id, t.title, t.created_at, t.updated_at,
                      (SELECT r.run_id FROM agent_runs r WHERE r.pod_id = t.pod_id AND r.thread_id = t.thread_id
                       AND r.state IN ('QUEUED','RUNNING') ORDER BY r.created_at DESC LIMIT 1) AS active_run_id
               FROM threads t WHERE t.pod_id = %s AND t.created_by = %s ORDER BY t.updated_at DESC LIMIT 100""",
            (ctx.active_pod_id, ctx.subject_id),
        )
    return [_jsonable(r) for r in rows]


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str, ctx: PodContext = Depends(pod_ctx_from_header), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        t = await fetchone(conn, "SELECT * FROM threads WHERE pod_id = %s AND thread_id::text = %s",
                           (ctx.active_pod_id, thread_id))
        if t is None or t["created_by"] != ctx.subject_id:
            raise not_found("thread")
        msgs = await fetchall(
            conn,
            """SELECT message_id, role, content, run_id, created_at FROM messages
               WHERE pod_id = %s AND thread_id = %s ORDER BY created_at""",
            (ctx.active_pod_id, t["thread_id"]),
        )
        runs = await fetchall(
            conn,
            """SELECT run_id, state, agent_id, mode FROM agent_runs WHERE pod_id = %s AND thread_id = %s
               AND state IN ('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1""",
            (ctx.active_pod_id, t["thread_id"]),
        )
    return {**_jsonable(t), "messages": [_jsonable(m) for m in msgs], "active_run": _jsonable(runs[0]) if runs else None}


@router.post("/threads/{thread_id}/runs", status_code=202)
async def create_run(thread_id: str, req: RunRequest, ctx: PodContext = Depends(pod_ctx_from_header), svc=Depends(services)):
    run = await svc.runs.start(ctx, thread_id, req)
    return {"run_id": str(run["run_id"]), "state": run["state"], "agent_id": run["agent_id"], "mode": run["mode"]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, ctx: PodContext = Depends(pod_ctx_from_header), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        run = await fetchone(
            conn,
            """SELECT run_id, thread_id, agent_id, agent_version, mode, state, created_by, routing, error, release_revision,
                      created_at, finished_at FROM agent_runs WHERE pod_id = %s AND run_id::text = %s""",
            (ctx.active_pod_id, run_id),
        )
    if run is None:
        raise not_found("run")
    return _jsonable(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, ctx: PodContext = Depends(pod_ctx_from_header), svc=Depends(services)):
    return {"cancelled": await svc.runs.cancel(ctx, run_id)}


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    ctx: PodContext = Depends(pod_ctx_from_header),
    svc=Depends(services),
    last_event_id: str | None = Header(default=None),
    after: int = Query(default=0, ge=0),
):
    """SSE stream of normalised run events with replay from ``Last-Event-ID``.

    The stream is bound to the pod it was opened in. Membership is re-checked
    while streaming; revocation terminates the stream without further data.
    """
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        run = await fetchone(conn, "SELECT run_id FROM agent_runs WHERE pod_id = %s AND run_id::text = %s",
                             (ctx.active_pod_id, run_id))
    if run is None:
        raise not_found("run")
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

    async def gen():
        last = start
        last_check = time.monotonic()
        last_beat = time.monotonic()
        wake = svc.runs.waiter(run_id)
        while True:
            if await request.is_disconnected():
                return
            async with svc.db.pod_tx(ctx.active_pod_id) as conn:
                rows = await fetchall(
                    conn,
                    """SELECT sequence, event_type, payload, occurred_at FROM run_events
                       WHERE pod_id = %s AND run_id::text = %s AND sequence > %s ORDER BY sequence LIMIT 200""",
                    (ctx.active_pod_id, run_id, last),
                )
            for r in rows:
                last = r["sequence"]
                data = {"run_id": run_id, "sequence": r["sequence"], "timestamp": r["occurred_at"].isoformat(), **r["payload"]}
                yield f"id: {r['sequence']}\nevent: {r['event_type']}\ndata: {json.dumps(data)}\n\n"
                if r["event_type"] in TERMINAL_RUN_EVENTS:
                    return
            if time.monotonic() - last_check > 1.0:
                last_check = time.monotonic()
                if not await is_active_member(svc.pdp, ctx.active_pod_id, ctx.subject_id):
                    yield f"event: stream.terminated\ndata: {json.dumps({'run_id': run_id, 'code': 'membership_revoked'})}\n\n"
                    return
            if time.monotonic() - last_beat > 15:
                last_beat = time.monotonic()
                yield ": keep-alive\n\n"
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=0.5)
            except TimeoutError:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/pods/{pod_id}/runs")
async def my_work(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services), scope: str = "mine"):
    where = "r.created_by = %s" if scope == "mine" else "(r.created_by = %s OR r.mode IN ('scheduled','background'))"
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        rows = await fetchall(
            conn,
            f"""SELECT r.run_id, r.thread_id, r.agent_id, r.agent_version, r.mode, r.state, r.created_by, r.error,
                       r.routing, r.created_at, r.updated_at, r.finished_at, r.monitor_id, t.title AS thread_title,
                       m.name AS monitor_name
                FROM agent_runs r LEFT JOIN threads t ON t.pod_id = r.pod_id AND t.thread_id = r.thread_id
                LEFT JOIN monitors m ON m.pod_id = r.pod_id AND m.monitor_id = r.monitor_id
                WHERE r.pod_id = %s AND {where} ORDER BY r.created_at DESC LIMIT 100""",
            (ctx.active_pod_id, ctx.subject_id),
        )
    return [_jsonable(r) for r in rows]
