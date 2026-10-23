"""Append-only audit trail with sanitised metadata (never prompt or research text)."""

from __future__ import annotations

import logging
from typing import Any

from .db import Database, Jsonb

log = logging.getLogger("podos.audit")

_SAFE_KEYS_MAX = 40


def _sanitise(detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in list(detail.items())[:_SAFE_KEYS_MAX]:
        if k in {"message", "prompt", "body", "text", "content"}:
            out[k] = "[redacted]"
        elif isinstance(v, str):
            out[k] = v[:200]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = [str(x)[:100] for x in v[:20]]
        else:
            out[k] = str(v)[:200]
    return out


async def record(
    db: Database, *, actor: str, action: str, target: str, pod_id: str | None = None,
    outcome: str = "ok", detail: dict[str, Any] | None = None,
) -> None:
    safe = _sanitise(detail or {})
    async with db.system_tx() as conn:
        await conn.execute(
            "INSERT INTO audit_events(actor, action, target, pod_id, outcome, detail) VALUES (%s,%s,%s,%s,%s,%s)",
            (actor, action, target, pod_id, outcome, Jsonb(safe)),
        )
    log.info("audit action=%s target=%s pod=%s outcome=%s", action, target, pod_id, outcome)
