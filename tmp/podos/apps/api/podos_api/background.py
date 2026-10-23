"""Durable background execution: Postgres outbox + worker (ARCHITECTURE.md 6.3/6.4).

- Claiming uses ``FOR UPDATE SKIP LOCKED`` with a lease: a worker that crashes
  mid-handler leaves a ``processing`` row whose lease expires and is reclaimed.
- Handlers are idempotent (unique event ids, artifact idempotency keys, state
  compare-and-set), giving at-least-once delivery with effectively-once effects.
- Each pod is processed inside its own pod_tx: a worker never holds a
  cross-pod transaction and RLS applies to background work as well.
- Exponential backoff; poison rows are dead-lettered after N attempts.

Interface reserved for Temporal: ``run_monitor`` / ``resume_workflow`` are the
activity boundaries; the outbox is the signal mechanism.
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import time
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

from podos_contracts.models import DelegationClaims

from .a2a_adapter import AgentCallError
from .db import Jsonb, fetchall, fetchone
from .errors import Problem
from .governance import load_snapshot
from .security.context import Principal, build_pod_context, is_active_member
from .security.tokens import issue_delegation
from .workitems import GENERIC_PREVIEW, add_outbox, create_work_item, resume_workflow

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger("podos.background")


class EmailProviderError(Exception):
    pass


async def pods(svc: Services) -> list[str]:
    async with svc.db.system_tx() as conn:
        return [r["pod_id"] for r in await fetchall(conn, "SELECT pod_id FROM pods ORDER BY pod_id")]


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
async def schedule_due_monitors(svc: Services) -> int:
    n = 0
    for pod in await pods(svc):
        async with svc.db.pod_tx(pod) as conn:
            due = await fetchall(
                conn,
                """SELECT monitor_id::text, schedule_seconds FROM monitors
                   WHERE pod_id = %s AND enabled AND next_run_at <= now() FOR UPDATE SKIP LOCKED""",
                (pod,),
            )
            for m in due:
                slot = int(datetime.now(UTC).timestamp()) // m["schedule_seconds"]
                if await add_outbox(conn, pod, f"monitor:{m['monitor_id']}:{slot}", "monitor.run",
                                    {"monitor_id": m["monitor_id"], "trigger": "schedule"}):
                    n += 1
                await conn.execute(
                    "UPDATE monitors SET next_run_at = now() + make_interval(secs => schedule_seconds) WHERE monitor_id::text = %s",
                    (m["monitor_id"],),
                )
    return n


async def enqueue_monitor_now(svc: Services, pod_id: str, monitor_id: str, actor: str) -> str:
    event_id = f"monitor:{monitor_id}:manual:{uuid.uuid4().hex[:12]}"
    async with svc.db.pod_tx(pod_id) as conn:
        await add_outbox(conn, pod_id, event_id, "monitor.run", {"monitor_id": monitor_id, "trigger": "manual", "actor": actor})
    return event_id


# ---------------------------------------------------------------------------
# Outbox processing
# ---------------------------------------------------------------------------
async def claim(svc: Services, pod_id: str, worker_id: str, limit: int = 10) -> list[dict[str, Any]]:
    async with svc.db.pod_tx(pod_id) as conn:
        return await fetchall(
            conn,
            """UPDATE outbox_events SET state = 'processing', attempts = attempts + 1, locked_by = %s,
                   locked_until = now() + make_interval(secs => %s)
               WHERE outbox_id IN (
                   SELECT outbox_id FROM outbox_events
                   WHERE pod_id = %s AND (
                       (state = 'pending' AND next_attempt_at <= now()) OR
                       (state = 'processing' AND locked_until < now()))
                   ORDER BY outbox_id FOR UPDATE SKIP LOCKED LIMIT %s)
               RETURNING *""",
            (worker_id, svc.settings.outbox_lease_seconds, pod_id, limit),
        )


async def _finish(svc: Services, row: dict[str, Any], error: str | None) -> None:
    async with svc.db.pod_tx(row["pod_id"]) as conn:
        if error is None:
            await conn.execute(
                "UPDATE outbox_events SET state = 'done', processed_at = now(), locked_until = NULL WHERE outbox_id = %s",
                (row["outbox_id"],),
            )
        elif row["attempts"] >= svc.settings.outbox_max_attempts:
            await conn.execute(
                "UPDATE outbox_events SET state = 'dead', last_error = %s, locked_until = NULL WHERE outbox_id = %s",
                (error[:500], row["outbox_id"]),
            )
        else:
            backoff = min(2 ** row["attempts"], 300)
            await conn.execute(
                """UPDATE outbox_events SET state = 'pending', last_error = %s, locked_until = NULL,
                       next_attempt_at = now() + make_interval(secs => %s) WHERE outbox_id = %s""",
                (error[:500], backoff, row["outbox_id"]),
            )


HANDLERS: dict[str, Any] = {}


def handler(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


async def process_outbox(svc: Services, worker_id: str = "worker-1", limit: int = 10) -> dict[str, int]:
    stats = {"done": 0, "retry": 0, "dead": 0}
    for pod in await pods(svc):
        for row in await claim(svc, pod, worker_id, limit):
            fn = HANDLERS.get(row["event_type"])
            try:
                if fn is None:
                    raise ValueError(f"no handler for {row['event_type']}")
                await fn(svc, row["pod_id"], row["payload"])
                await _finish(svc, row, None)
                stats["done"] += 1
            except Exception as exc:  # noqa: BLE001 - every failure is recorded and retried/dead-lettered
                log.warning("outbox %s failed: %s", row["event_id"], type(exc).__name__)
                await _finish(svc, row, f"{type(exc).__name__}: {exc}")
                stats["dead" if row["attempts"] >= svc.settings.outbox_max_attempts else "retry"] += 1
    return stats


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@handler("workflow.resume")
async def _h_resume(svc: Services, pod_id: str, payload: dict[str, Any]) -> None:
    await resume_workflow(svc, pod_id, payload["work_item_id"])


@handler("inbox.notify")
async def _h_notify(svc: Services, pod_id: str, payload: dict[str, Any]) -> None:
    async with svc.db.pod_tx(pod_id) as conn:
        wi = await fetchone(conn, "SELECT * FROM work_items WHERE pod_id = %s AND work_item_id::text = %s",
                            (pod_id, payload["work_item_id"]))
        if wi is None:
            return
        recipient = wi["assignee_user_id"]
        prefs = await fetchone(conn, "SELECT * FROM notification_preferences WHERE pod_id = %s AND subject_id = %s",
                               (pod_id, recipient))
        # In-app inbox is the system of record; the delivery row records that it was surfaced.
        await conn.execute(
            """INSERT INTO notification_deliveries(pod_id, work_item_id, recipient_user_id, channel, status, preview, attempts, delivered_at)
               VALUES (%s,%s,%s,'in_app','delivered',%s,1,now()) ON CONFLICT DO NOTHING""",
            (pod_id, wi["work_item_id"], recipient, wi["title"]),
        )
        email_wanted = (prefs is None or prefs["email_enabled"]) and wi["priority"] != "low"
        confidential = wi["classification"] in ("pod_confidential", "restricted")
        preview = GENERIC_PREVIEW if confidential else wi["title"]
        if not email_wanted:
            await conn.execute(
                """INSERT INTO notification_deliveries(pod_id, work_item_id, recipient_user_id, channel, status, preview)
                   VALUES (%s,%s,%s,'email','suppressed',%s) ON CONFLICT DO NOTHING""",
                (pod_id, wi["work_item_id"], recipient, "digest"),
            )
            return
        delivery = await fetchone(
            conn,
            """INSERT INTO notification_deliveries(pod_id, work_item_id, recipient_user_id, channel, status, preview)
               VALUES (%s,%s,%s,'email','pending',%s)
               ON CONFLICT (work_item_id, recipient_user_id, channel) DO UPDATE SET attempts = notification_deliveries.attempts
               RETURNING *""",
            (pod_id, wi["work_item_id"], recipient, preview),
        )
    if delivery["status"] == "delivered":
        return
    async with svc.db.system_tx() as conn:
        user = await fetchone(conn, "SELECT email FROM users WHERE subject_id = %s", (recipient,))
    try:
        await send_email(svc, to=user["email"] if user else recipient, subject="PodOS notification", body=preview,
                   delivery_id=str(delivery["delivery_id"]))
        status, err = "delivered", None
    except EmailProviderError as exc:
        status, err = "failed", str(exc)
    async with svc.db.pod_tx(pod_id) as conn:
        await conn.execute(
            """UPDATE notification_deliveries SET status = %s, attempts = attempts + 1, last_error = %s,
                   delivered_at = CASE WHEN %s = 'delivered' THEN now() END WHERE delivery_id = %s""",
            (status, err, status, delivery["delivery_id"]),
        )
    if err:
        raise EmailProviderError(err)  # outbox retries with backoff, then dead-letters


async def send_email(svc: Services, *, to: str, subject: str, body: str, delivery_id: str) -> None:
    """Email channel adapter (SMTP; Mailpit locally). Body is already sanitised:
    confidential items only ever carry the generic preview."""
    st = svc.settings
    if st.email_provider_broken:
        raise EmailProviderError("email provider unavailable")
    if st.email_backend != "smtp":
        st.mail_root.mkdir(parents=True, exist_ok=True)
        (st.mail_root / f"{delivery_id}.json").write_text(
            json.dumps({"to": to, "subject": subject, "body": body, "sent_at": datetime.now(UTC).isoformat()}))
        return
    msg = EmailMessage()
    msg["From"] = "PodOS <podos-notifications@fund.example>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["X-PodOS-Delivery"] = delivery_id
    msg.set_content(f"{body}\n\nOpen PodOS: http://localhost:3400/inbox\n")

    def _send() -> None:
        with smtplib.SMTP(st.smtp_host, st.smtp_port, timeout=5) as smtp:
            smtp.send_message(msg)

    try:
        await asyncio.to_thread(_send)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailProviderError(f"smtp: {type(exc).__name__}") from exc


@handler("monitor.run")
async def _h_monitor(svc: Services, pod_id: str, payload: dict[str, Any]) -> None:
    await run_monitor(svc, pod_id, payload["monitor_id"], trigger=payload.get("trigger", "schedule"))


async def run_monitor(svc: Services, pod_id: str, monitor_id: str, *, trigger: str = "schedule") -> dict[str, Any]:
    async with svc.db.pod_tx(pod_id) as conn:
        mon = await fetchone(conn, "SELECT * FROM monitors WHERE pod_id = %s AND monitor_id::text = %s", (pod_id, monitor_id))
    if mon is None or not mon["enabled"]:
        return {"status": "skipped"}
    owner = mon["owner_user_id"]
    # Entitlements are resolved now, not cached from when the monitor was created.
    try:
        ctx = await build_pod_context(
            svc.db, svc.pdp, Principal(subject_id=owner, display_name=owner, title="", platform_roles=[], session_id="monitor"),
            pod_id,
        )
    except Problem:
        async with svc.db.pod_tx(pod_id) as conn:
            await conn.execute("UPDATE monitors SET enabled = false, updated_at = now() WHERE monitor_id = %s", (mon["monitor_id"],))
        await svc.audit(actor="podos-worker", action="monitor.disabled", target=monitor_id, pod_id=pod_id,
                        outcome="denied", detail={"reason": "owner_not_member"})
        return {"status": "owner_revoked"}

    snap = await load_snapshot(svc.db)
    try:
        av = svc.gov.require_agent(ctx, snap, mon["agent_id"], "background_execution")
    except Problem as p:
        await svc.audit(actor="podos-worker", action="monitor.blocked", target=monitor_id, pod_id=pod_id,
                        outcome="denied", detail={"reason": p.extra.get("reason", p.code)})
        return {"status": "blocked", "reason": p.extra.get("reason", p.code)}
    assert av.deployment is not None

    cfg = mon["configuration"]
    recipients = [r for r in cfg.get("notify", [owner]) if await is_active_member(svc.pdp, pod_id, r)]
    approver = cfg.get("approval_recipient", owner)
    async with svc.db.pod_tx(pod_id) as conn:
        run = await fetchone(
            conn,
            """INSERT INTO agent_runs(pod_id, agent_id, agent_version, mode, state, created_by, execution_context,
                   release_revision, monitor_id, deployment_id, routing)
               VALUES (%s,%s,%s,'scheduled','RUNNING',%s,%s,%s,%s,%s,%s) RETURNING run_id::text""",
            (pod_id, av.agent_id, av.version, owner, Jsonb(ctx.model_dump(mode="json")), snap.revision, mon["monitor_id"],
             av.deployment.deployment_id, Jsonb({"mode": "scheduled", "trigger": trigger})),
        )
    run_id = run["run_id"]
    caps = [c for c in av.capabilities if svc.gov.capability_status(ctx, snap, c)[0] and svc.pdp.agent_service_may_call(av.agent_id, c)]
    claims = DelegationClaims(sub=owner, pod=pod_id, run=run_id, agent=av.agent_id, caps=caps, depth=1,
                              purpose="monitoring", cls=ctx.data_classification, rev=snap.revision)
    metadata = {
        "delegation_token": issue_delegation(svc.settings, claims, ttl=600),
        "api_base": svc.settings.internal_api_url,
        "run_id": run_id,
        "pod_id": pod_id,  # informational: the ingress re-derives and verifies the pod from the run
        "mode": "background",
        "monitor": {"monitor_id": monitor_id, "name": mon["name"], **cfg},
        "checkpoint": mon["checkpoint"],
        "recipients": recipients or [owner],
        "approval_recipient": approver,
    }
    summary: dict[str, Any] = {}
    error: str | None = None
    try:
        async for kind, value in svc.a2a.stream(av.deployment, f"Run monitor {mon['name']}", metadata, timeout=60):
            if kind == "event" and value.get("type") == "monitor.result":
                summary = value
    except AgentCallError as exc:
        error = exc.code

    async with svc.db.pod_tx(pod_id) as conn:
        if error:
            await conn.execute(
                "UPDATE agent_runs SET state = 'FAILED', error = %s, finished_at = now() WHERE pod_id = %s AND run_id::text = %s",
                (Jsonb({"code": error}), pod_id, run_id),
            )
            await create_work_item(
                conn, pod_id=pod_id, assignee=owner, title=f"Monitor run failed: {mon['name']}", category="exception",
                kind="informational", state="OPEN", priority="normal", run_id=run_id,
                summary="The background agent was unavailable. The monitor will retry on its next schedule.",
                dedup_key=f"monfail:{monitor_id}:{datetime.now(UTC).date()}", actor="podos-worker",
            )
        else:
            await conn.execute(
                """UPDATE agent_runs SET state = CASE WHEN state = 'RUNNING' THEN 'COMPLETED' ELSE state END,
                       finished_at = CASE WHEN state = 'RUNNING' THEN now() END, updated_at = now(), routing = routing || %s
                   WHERE pod_id = %s AND run_id::text = %s""",
                (Jsonb({"result": {k: summary.get(k) for k in ("new_documents", "alerts", "approvals")}}), pod_id, run_id),
            )
        cp = summary.get("checkpoint")
        await conn.execute(
            """UPDATE monitors SET last_run_at = now(), checkpoint = CASE WHEN %s::jsonb IS NULL THEN checkpoint ELSE %s::jsonb END,
                   updated_at = now() WHERE monitor_id::text = %s""",
            (Jsonb(cp) if cp else None, Jsonb(cp) if cp else None, monitor_id),
        )
    return {"status": "failed" if error else "completed", "run_id": run_id, "error": error, **summary}


_WATCHLIST_REFRESH_SECONDS = 60.0
_last_watchlist_refresh = 0.0
_HEALTH_SECONDS = 30.0
_last_health = 0.0


async def refresh_external_health(svc: Services) -> int:
    """External A2A agents come and go independently: re-read each card, mark healthy/unhealthy, and treat a
    card that changed from the pinned digest as unhealthy (re-register to accept a new card)."""
    async with svc.db.system_tx() as conn:
        rows = await fetchall(
            conn,
            """SELECT d.deployment_id::text AS id, d.service_ref, d.card_sha256, d.healthy, a.manifest
               FROM agent_deployments d JOIN agent_definitions a USING (agent_id)
               WHERE a.manifest->'deployment'->>'trust' = 'external'""",
        )
    changed = 0
    for r in rows:
        secret_ref = (r["manifest"] or {}).get("deployment", {}).get("auth_secret_ref")
        healthy, err, digest = True, None, r["card_sha256"]
        try:
            _, got = await svc.a2a.fetch_card(r["service_ref"], timeout=3, secret_ref=secret_ref)
            if digest and got != digest:
                healthy, err = False, "agent_card_mismatch"
            digest = digest or got
        except AgentCallError as exc:
            healthy, err = False, exc.code
        async with svc.db.system_tx() as conn:
            await conn.execute(
                """UPDATE agent_deployments SET healthy = %s, last_health_error = %s, last_health_at = now(),
                       card_sha256 = %s WHERE deployment_id::text = %s""",
                (healthy, err, digest, r["id"]),
            )
        changed += int(healthy != r["healthy"])
    return changed


async def tick(svc: Services, worker_id: str) -> dict[str, Any]:
    """One worker pass: refresh watchlists (periodically), pull connectors, route events into pods,
    schedule due monitors, then drain the outbox (which runs any monitors the events triggered)."""
    global _last_watchlist_refresh, _last_health
    from . import connectors, events

    if time.monotonic() - _last_health > _HEALTH_SECONDS:
        await refresh_external_health(svc)
        _last_health = time.monotonic()

    if time.monotonic() - _last_watchlist_refresh > _WATCHLIST_REFRESH_SECONDS:
        await events.refresh_all_watchlists(svc)
        _last_watchlist_refresh = time.monotonic()
    polled = await connectors.poll_due(svc)
    routed = await events.route_events(svc)
    scheduled = await schedule_due_monitors(svc)
    stats = await process_outbox(svc, worker_id)
    return {"ingested": sum(v for v in polled.values() if v > 0), "routed": routed["deliveries"],
            "scheduled": scheduled, **stats}
