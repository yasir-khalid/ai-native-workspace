"""Connector gateway (PLAN_PROACTIVE.md F2): the only place PodOS talks to data sources.

* Pull connectors are MCP servers. The worker polls each one's ``poll_tool`` with a stored cursor and
  the distinct set of watched tickers (no pod ids leave PodOS), and stores what comes back as events.
* Push connectors POST signed batches to ``/v1/connectors/{id}/events`` (routes/connectors.py).

Source credentials stay here; agents never call a connector directly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp import Client

from .db import fetchone
from .events import store_event, watched_tickers

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger("podos.connectors")
WEBHOOK_MAX_SKEW = 300  # seconds


@dataclass
class Connector:
    connector_id: str
    dataset_id: str
    transport: str  # "mcp" (pull) | "webhook" (push)
    event_type: str
    url: str = ""
    poll_tool: str = ""
    interval_seconds: int = 30
    forms: list[str] = field(default_factory=list)
    backfill: bool = False
    secret_ref: str = ""


def load_connectors(svc: Services) -> dict[str, Connector]:
    path = svc.settings.connectors_path
    if not path.exists():
        return {}
    return {c["connector_id"]: Connector(**{k: v for k, v in c.items() if not k.startswith("_")})
            for c in json.loads(path.read_text())}


def _tool_json(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        raise RuntimeError(f"connector tool error: {result.content[:1] if result.content else ''}")
    if getattr(result, "structured_content", None):
        return result.structured_content
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text or "{}")


async def call_tool(url: str, tool: str, args: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
    async with Client(url, read_timeout_seconds=timeout) as client:
        return _tool_json(await client.call_tool(tool, args))


async def poll_due(svc: Services, *, force: bool = False) -> dict[str, int]:
    stats: dict[str, int] = {}
    for c in load_connectors(svc).values():
        if c.transport != "mcp":
            continue
        async with svc.db.system_tx() as conn:
            cur = await fetchone(conn, "SELECT * FROM connector_cursors WHERE connector_id = %s", (c.connector_id,))
        now = datetime.now(UTC)
        if cur and cur["polled_at"] and not force and (now - cur["polled_at"]).total_seconds() < c.interval_seconds:
            continue
        since = cur["cursor"] if cur and cur["cursor"] else None
        if since is None and not c.backfill:
            # First contact: start from now rather than flooding pods with history.
            since = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            await _save_cursor(svc, c.connector_id, since, 0, None)
            stats[c.connector_id] = 0
            continue
        tickers = await watched_tickers(svc)
        try:
            n = 0
            if tickers:
                out = await call_tool(c.url, c.poll_tool, {"since": since or "", "tickers": tickers, "forms": c.forms or None})
                for f in out.get("filings", []):
                    if await store_event(
                        svc, connector_id=c.connector_id, dataset_id=c.dataset_id, event_type=c.event_type,
                        ticker=f.get("ticker"), occurred_at=datetime.fromisoformat(f["accepted_at"].replace("Z", "+00:00")),
                        dedupe_key=f"{c.connector_id}:{f['accession']}", payload=f,
                    ):
                        n += 1
                since = out.get("cursor") or since
            await _save_cursor(svc, c.connector_id, since, n, None)
            stats[c.connector_id] = n
        except Exception as exc:
            log.warning("connector %s poll failed: %s", c.connector_id, exc)
            await _save_cursor(svc, c.connector_id, since, 0, str(exc)[:500])
            stats[c.connector_id] = -1
    return stats


async def _save_cursor(svc: Services, connector_id: str, cursor: str | None, n: int, error: str | None) -> None:
    async with svc.db.system_tx() as conn:
        await conn.execute(
            """INSERT INTO connector_cursors(connector_id, cursor, polled_at, last_error, events_total)
               VALUES (%s,%s,now(),%s,%s)
               ON CONFLICT (connector_id) DO UPDATE SET cursor = excluded.cursor, polled_at = now(),
                 last_error = excluded.last_error, events_total = connector_cursors.events_total + excluded.events_total""",
            (connector_id, cursor, error, n),
        )


# ---- push (webhooks) -------------------------------------------------------------------------
def sign(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def verify_signature(secret: str, timestamp: str, nonce: str, body: bytes, signature: str) -> bool:
    try:
        skew = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    return skew <= WEBHOOK_MAX_SKEW and bool(nonce) and hmac.compare_digest(sign(secret, timestamp, nonce, body), signature)
