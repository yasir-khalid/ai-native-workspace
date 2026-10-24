"""Push ingress for webhook connectors (PLAN_PROACTIVE.md §1).

Signed (HMAC-SHA256 over "timestamp.nonce.body"), replay-protected (timestamp skew + single-use nonce)
and idempotent (each event's id is its dedupe key). Events land in the firm-wide event store and are
routed to pods by the worker exactly like pulled events.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Request
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field, ValidationError

from ..connectors import load_connectors, verify_signature
from ..errors import Problem, not_found
from ..events import store_event
from ..security.context import services

router = APIRouter(prefix="/v1/connectors")


class PushedEvent(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    ticker: str = Field(min_length=1, max_length=12)
    occurred_at: datetime
    headline: str = Field(max_length=300)
    summary: str = Field(default="", max_length=4000)
    url: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=100)


class PushedBatch(BaseModel):
    events: list[PushedEvent] = Field(max_length=100)


@router.post("/{connector_id}/events", status_code=202)
async def push_events(
    connector_id: str, request: Request, svc=Depends(services),
    x_podos_timestamp: str = Header(default=""), x_podos_nonce: str = Header(default=""),
    x_podos_signature: str = Header(default=""),
):
    c = load_connectors(svc).get(connector_id)
    if c is None or c.transport != "webhook":
        raise not_found("connector")
    secret = svc.settings.workload_secrets().get(c.secret_ref, "")
    body = await request.body()
    if not secret or not verify_signature(secret, x_podos_timestamp, x_podos_nonce, body, x_podos_signature):
        await svc.audit(actor=f"connector:{connector_id}", action="connector.webhook_rejected", target=connector_id,
                        outcome="denied", detail={"reason": "bad_signature"})
        raise Problem(401, "invalid_signature", "webhook signature, timestamp or nonce invalid")
    try:
        async with svc.db.system_tx() as conn:
            await conn.execute("INSERT INTO webhook_nonces(connector_id, nonce) VALUES (%s,%s)", (connector_id, x_podos_nonce))
    except UniqueViolation:
        await svc.audit(actor=f"connector:{connector_id}", action="connector.webhook_rejected", target=connector_id,
                        outcome="denied", detail={"reason": "replay"})
        raise Problem(409, "replayed", "this webhook delivery was already received") from None
    try:
        batch = PushedBatch.model_validate(json.loads(body))
    except (ValueError, ValidationError) as exc:
        raise Problem(422, "invalid_payload", str(exc)[:200]) from None
    stored = 0
    for e in batch.events:
        if await store_event(
            svc, connector_id=connector_id, dataset_id=c.dataset_id, event_type=c.event_type, ticker=e.ticker,
            occurred_at=e.occurred_at if e.occurred_at.tzinfo else e.occurred_at.replace(tzinfo=UTC),
            dedupe_key=f"{connector_id}:{e.id}",
            payload={"title": e.headline, "summary": e.summary, "url": e.url, "source": e.source or connector_id,
                     "accepted_at": e.occurred_at.isoformat()},
        ):
            stored += 1
    return {"received": len(batch.events), "stored": stored, "duplicates": len(batch.events) - stored}
