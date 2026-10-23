"""Agent-initiated work: event ingress, inbox items, approvals, resume.

Ingress (ARCHITECTURE.md 6.3): the event's agent must equal the authenticated
workload, the run must exist *in the claimed pod* (looked up under that pod's
RLS scope) and belong to that agent, recipients must be live pod members.
Work item + dedupe record + outbox row commit in one transaction.

Approvals (6.4): versioned command, idempotency key, approver + role check,
pending + expiry check, compare-and-set; the resume outbox row has a unique
event id so the workflow resumes exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from podos_contracts.models import AgentEvent, Classification, DecisionCommand, DocumentSpec, PodContext
from psycopg import AsyncConnection

from .db import Jsonb, fetchall, fetchone
from .errors import Problem, conflict, forbidden, not_found
from .governance import load_snapshot
from .security.context import Principal, build_pod_context, is_active_member

if TYPE_CHECKING:
    from .services import Services

GENERIC_PREVIEW = "You have a new item in PodOS. Sign in to view it."


async def add_outbox(conn: AsyncConnection, pod_id: str, event_id: str, event_type: str, payload: dict[str, Any]) -> bool:
    cur = await conn.execute(
        """INSERT INTO outbox_events(pod_id, event_id, event_type, payload) VALUES (%s,%s,%s,%s)
           ON CONFLICT (event_id) DO NOTHING""",
        (pod_id, event_id, event_type, Jsonb(payload)),
    )
    return cur.rowcount == 1


async def wi_event(conn: AsyncConnection, pod_id: str, wid: str, actor: str, etype: str,
                   from_state: str | None, to_state: str | None, version: int, detail: dict | None = None) -> None:
    await conn.execute(
        """INSERT INTO work_item_events(pod_id, work_item_id, actor, event_type, from_state, to_state, version, detail)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (pod_id, wid, actor, etype, from_state, to_state, version, Jsonb(detail or {})),
    )


async def create_work_item(
    conn: AsyncConnection, *, pod_id: str, assignee: str, title: str, summary: str, category: str, kind: str,
    state: str, priority: str = "normal", classification: str = "pod_confidential", run_id: str | None = None,
    payload: dict | None = None, dedup_key: str | None = None, required_pod_role: str | None = None,
    source_agent_id: str | None = None, source_event_id: str | None = None, deadline_at: datetime | None = None,
    evidence_artifact_id: str | None = None, actor: str = "system",
) -> str | None:
    row = await fetchone(
        conn,
        """INSERT INTO work_items(pod_id, run_id, owner_user_id, assignee_user_id, title, category, state, classification,
               kind, priority, summary, payload, dedup_key, required_pod_role, source_agent_id, source_event_id,
               deadline_at, evidence_artifact_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (pod_id, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
           RETURNING work_item_id::text""",
        (pod_id, run_id, assignee, assignee, title[:200], category, state, classification, kind, priority, summary[:2000],
         Jsonb(payload or {}), dedup_key, required_pod_role, source_agent_id, source_event_id, deadline_at,
         evidence_artifact_id),
    )
    if row is None:
        return None
    wid = row["work_item_id"]
    await wi_event(conn, pod_id, wid, actor, "created", None, state, 0)
    await add_outbox(conn, pod_id, f"notify:{wid}", "inbox.notify", {"work_item_id": wid})
    return wid


# ---------------------------------------------------------------------------
# Ingress
# ---------------------------------------------------------------------------
async def ingest_agent_event(svc: Services, workload_id: str, ev: AgentEvent) -> dict[str, Any]:
    if ev.agent_id != workload_id:
        await svc.audit(actor=workload_id, action="ingress.rejected", target=ev.event_id, pod_id=None,
                        outcome="denied", detail={"reason": "workload_agent_mismatch"})
        raise forbidden("workload_agent_mismatch", "event agent does not match the calling workload")
    async with svc.db.pod_tx(ev.pod_id) as conn:
        run = await fetchone(
            conn, "SELECT * FROM agent_runs WHERE pod_id = %s AND run_id::text = %s", (ev.pod_id, ev.run_id)
        )
    if run is None or run["agent_id"] != ev.agent_id:
        await svc.audit(actor=workload_id, action="ingress.rejected", target=ev.event_id, pod_id=None,
                        outcome="denied", detail={"reason": "run_not_found_for_pod_and_agent"})
        raise forbidden("event_run_mismatch", "event does not match a run of this agent in this pod")
    if run["state"] not in ("RUNNING", "WAITING_FOR_HUMAN"):
        raise conflict("run_not_active", "run is not accepting events")

    # Release gate: proactive alerts must be enabled for the run owner in this pod.
    owner = run["created_by"]
    snap = await load_snapshot(svc.db)
    principal = Principal(subject_id=owner, display_name=owner, title="", platform_roles=[], session_id="workflow")
    try:
        owner_ctx = await build_pod_context(svc.db, svc.pdp, principal, ev.pod_id)
    except Problem:
        raise forbidden("run_owner_not_member", "run owner is no longer a pod member") from None
    av = svc.gov.require_agent(owner_ctx, snap, ev.agent_id, "proactive_alerts")
    del av

    recipients = [r for r in ev.recipient_user_ids if await is_active_member(svc.pdp, ev.pod_id, r)]
    if not recipients:
        raise Problem(422, "no_valid_recipients", "no recipient is a member of the pod")

    p = ev.payload
    created: list[str] = []
    async with svc.db.pod_tx(ev.pod_id) as conn:
        cur = await conn.execute(
            """INSERT INTO ingested_agent_events(event_id, pod_id, agent_id, run_id, event_type)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING""",
            (ev.event_id, ev.pod_id, ev.agent_id, ev.run_id, ev.type),
        )
        if cur.rowcount == 0:
            return {"status": "duplicate", "work_item_ids": []}
        base = dict(
            pod_id=ev.pod_id, run_id=ev.run_id, classification=ev.classification.value, source_agent_id=ev.agent_id,
            source_event_id=ev.event_id, actor=ev.agent_id,
        )
        title = str(p.get("title", ev.type.replace("_", " ").title()))
        summary = str(p.get("summary", ""))
        dedup = str(p.get("dedup_key") or ev.event_id)
        if ev.type == "approval_requested":
            spec = DocumentSpec.model_validate(p.get("document_spec") or {"title": title})
            deadline = datetime.now(UTC) + timedelta(hours=float(p.get("expires_in_hours", 72)))
            for r in recipients:
                wid = await create_work_item(
                    conn, assignee=r, title=title, summary=summary, category="approval", kind="actionable",
                    state="WAITING_FOR_HUMAN", priority="high", required_pod_role="pm", deadline_at=deadline,
                    dedup_key=f"{dedup}:{r}",
                    payload={
                        "action": p.get("action", "publish_pod_brief"),
                        "target": p.get("target"),
                        "proposed_changes": p.get("proposed_changes", []),
                        "evidence": p.get("evidence", []),
                        "document_spec": spec.model_dump(mode="json"),
                        "output_capability": p.get("output_capability", "document.export.pptx"),
                    },
                    **base,
                )
                if wid:
                    created.append(wid)
            await conn.execute(
                "UPDATE agent_runs SET state = 'WAITING_FOR_HUMAN', updated_at = now() WHERE pod_id = %s AND run_id::text = %s",
                (ev.pod_id, ev.run_id),
            )
        else:
            category = {"monitor_alert": "alert", "run_failed": "exception"}.get(ev.type, "report")
            priority = p.get("priority", "normal") if p.get("priority") in ("low", "normal", "high") else "normal"
            for r in recipients:
                wid = await create_work_item(
                    conn, assignee=r, title=title, summary=summary, category=category, kind="informational",
                    state="OPEN", priority=priority, dedup_key=f"{dedup}:{r}",
                    payload={"evidence": p.get("evidence", []), "ticker": p.get("ticker"), "matched": p.get("matched", [])},
                    **base,
                )
                if wid:
                    created.append(wid)
        await conn.execute("UPDATE ingested_agent_events SET work_item_id = %s WHERE event_id = %s",
                           (created[0] if created else None, ev.event_id))
    return {"status": "accepted", "work_item_ids": created}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
async def decide(svc: Services, ctx: PodContext, work_item_id: str, cmd: DecisionCommand) -> dict[str, Any]:
    pod = ctx.active_pod_id
    async with svc.db.pod_tx(pod) as conn:
        wi = await fetchone(
            conn, "SELECT * FROM work_items WHERE pod_id = %s AND work_item_id::text = %s FOR UPDATE", (pod, work_item_id)
        )
        if wi is None:
            raise not_found("work item")
        prior = await fetchone(
            conn, "SELECT * FROM approval_decisions WHERE work_item_id = %s AND idempotency_key = %s",
            (wi["work_item_id"], cmd.idempotency_key),
        )
        if prior is not None:
            if prior["decided_by"] != ctx.subject_id or prior["decision"] != cmd.decision:
                raise conflict("idempotency_key_reused", "idempotency key already used for a different decision")
            return {"status": "replayed", "state": wi["state"], "version": wi["version"]}
        if wi["kind"] != "actionable":
            raise conflict("not_actionable", "item does not accept decisions")
        if wi["assignee_user_id"] != ctx.subject_id:
            raise forbidden("not_assigned_approver", "you are not the assigned approver")
        if not svc.pdp.action_entitled(ctx, "work_item.approve") or (
            wi["required_pod_role"] and wi["required_pod_role"] != ctx.pod_role
        ):
            raise forbidden("approver_not_entitled", "your role cannot approve this item")
        if wi["state"] != "WAITING_FOR_HUMAN":
            raise conflict("not_pending", "item is no longer awaiting a decision", state=wi["state"])
        expired = bool(wi["deadline_at"] and wi["deadline_at"] < datetime.now(UTC))
        if expired:
            # Persist the expiry (commit), then report it outside the transaction.
            await conn.execute(
                "UPDATE work_items SET state = 'EXPIRED', version = version + 1, updated_at = now() WHERE work_item_id = %s",
                (wi["work_item_id"],),
            )
            await wi_event(conn, pod, work_item_id, "system", "expired", wi["state"], "EXPIRED", wi["version"] + 1)
            if wi["run_id"]:
                await conn.execute(
                    "UPDATE agent_runs SET state = 'EXPIRED', finished_at = now() WHERE pod_id = %s AND run_id = %s",
                    (pod, wi["run_id"]),
                )
    if expired:
        raise conflict("expired", "approval window has expired")
    async with svc.db.pod_tx(pod) as conn:
        wi = await fetchone(
            conn, "SELECT * FROM work_items WHERE pod_id = %s AND work_item_id::text = %s FOR UPDATE", (pod, work_item_id)
        )
        if wi is None or wi["state"] != "WAITING_FOR_HUMAN":
            raise conflict("not_pending", "item is no longer awaiting a decision")
        if wi["version"] != cmd.expected_version:
            raise conflict("stale_version", "item changed since you loaded it", current_version=wi["version"])
        to_state = {"approve": "APPROVED", "reject": "REJECTED", "request_changes": "CHANGES_REQUESTED"}[cmd.decision]
        updated = await fetchone(
            conn,
            """UPDATE work_items SET state = %s, version = version + 1, resolved_by = %s, read_at = coalesce(read_at, now()),
                   updated_at = now()
               WHERE work_item_id = %s AND version = %s AND state = 'WAITING_FOR_HUMAN' RETURNING version""",
            (to_state, ctx.subject_id, wi["work_item_id"], cmd.expected_version),
        )
        if updated is None:
            raise conflict("stale_version", "item changed concurrently")
        await conn.execute(
            """INSERT INTO approval_decisions(pod_id, work_item_id, decided_by, decision, expected_version, idempotency_key,
                   decision_payload) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (pod, wi["work_item_id"], ctx.subject_id, cmd.decision, cmd.expected_version, cmd.idempotency_key,
             Jsonb({"comment": cmd.comment[:2000]})),
        )
        await wi_event(conn, pod, work_item_id, ctx.subject_id, f"decision.{cmd.decision}", wi["state"], to_state,
                       updated["version"], {"comment_len": len(cmd.comment)})
        if cmd.decision == "approve":
            await add_outbox(conn, pod, f"resume:{work_item_id}", "workflow.resume", {"work_item_id": work_item_id})
        elif wi["run_id"]:
            await conn.execute(
                "UPDATE agent_runs SET state = %s, finished_at = now(), updated_at = now() WHERE pod_id = %s AND run_id = %s",
                ("REJECTED" if cmd.decision == "reject" else "COMPLETED", pod, wi["run_id"]),
            )
    await svc.audit(actor=ctx.subject_id, action=f"work_item.{cmd.decision}", target=work_item_id, pod_id=pod)
    return {"status": "recorded", "state": to_state, "version": updated["version"]}


# ---------------------------------------------------------------------------
# Resume (called by the worker for 'workflow.resume' outbox rows)
# ---------------------------------------------------------------------------
async def resume_workflow(svc: Services, pod_id: str, work_item_id: str) -> str:
    async with svc.db.pod_tx(pod_id) as conn:
        wi = await fetchone(
            conn, "SELECT * FROM work_items WHERE pod_id = %s AND work_item_id::text = %s FOR UPDATE", (pod_id, work_item_id)
        )
        if wi is None:
            return "missing"
        if wi["state"] not in ("APPROVED", "RUNNING"):
            return "noop"  # already completed / rejected: idempotent no-op
        if wi["state"] == "APPROVED":
            await conn.execute(
                "UPDATE work_items SET state = 'RUNNING', version = version + 1, updated_at = now() WHERE work_item_id = %s",
                (wi["work_item_id"],),
            )
            await wi_event(conn, pod_id, work_item_id, "podos-worker", "resumed", "APPROVED", "RUNNING", wi["version"] + 1)
        run = await fetchone(conn, "SELECT * FROM agent_runs WHERE pod_id = %s AND run_id = %s", (pod_id, wi["run_id"]))

    owner = run["created_by"] if run else wi["owner_user_id"]
    approver = wi["resolved_by"]

    async def fail(reason: str) -> str:
        async with svc.db.pod_tx(pod_id) as conn:
            await conn.execute(
                "UPDATE work_items SET state = 'FAILED', version = version + 1, updated_at = now() WHERE work_item_id = %s AND state = 'RUNNING'",
                (wi["work_item_id"],),
            )
            await wi_event(conn, pod_id, work_item_id, "podos-worker", "failed", "RUNNING", "FAILED", wi["version"] + 2,
                           {"reason": reason})
        await svc.audit(actor="podos-worker", action="workflow.resume_denied", target=work_item_id, pod_id=pod_id,
                        outcome="denied", detail={"reason": reason})
        return f"failed:{reason}"

    if not await is_active_member(svc.pdp, pod_id, approver or ""):
        return await fail("approver_no_longer_member")
    try:
        ctx = await build_pod_context(
            svc.db, svc.pdp,
            Principal(subject_id=owner, display_name=owner, title="", platform_roles=[], session_id="workflow"), pod_id,
        )
        snap = await load_snapshot(svc.db)
        if run:
            svc.gov.require_agent(ctx, snap, run["agent_id"], "background_execution")
        cap = wi["payload"].get("output_capability", "document.export.pptx")
        grant = svc.gov.require_capability(
            ctx, snap, cap, classification=Classification(wi["classification"]), via_workload="podos-worker"
        )
    except Problem as p:
        return await fail(p.extra.get("reason") or p.code)

    async with svc.db.system_tx() as conn:
        pod = await fetchone(conn, "SELECT display_name FROM pods WHERE pod_id = %s", (pod_id,))
    spec = DocumentSpec.model_validate(wi["payload"]["document_spec"])
    art, _ = await svc.capabilities.render_document(
        grant, spec, idempotency_key=f"resume:{work_item_id}", pod_label=pod["display_name"] if pod else pod_id,
        run_id=str(wi["run_id"]) if wi["run_id"] else None,
        provenance={"work_item_id": work_item_id, "approved_by": approver},
    )
    async with svc.db.pod_tx(pod_id) as conn:
        done = await fetchone(
            conn,
            """UPDATE work_items SET state = 'COMPLETED', version = version + 1, evidence_artifact_id = %s, updated_at = now()
               WHERE work_item_id = %s AND state = 'RUNNING' RETURNING version""",
            (art.artifact_id, wi["work_item_id"]),
        )
        if done is None:
            return "noop"
        await wi_event(conn, pod_id, work_item_id, "podos-worker", "completed", "RUNNING", "COMPLETED", done["version"],
                       {"artifact_id": art.artifact_id})
        if wi["run_id"]:
            await conn.execute(
                """UPDATE agent_runs SET state = 'COMPLETED', finished_at = now(), updated_at = now()
                   WHERE pod_id = %s AND run_id = %s AND state = 'WAITING_FOR_HUMAN'""",
                (pod_id, wi["run_id"]),
            )
        for r in sorted({owner, approver} - {None}):
            await create_work_item(
                conn, pod_id=pod_id, assignee=r, title=f"Ready: {spec.title}", category="report", kind="informational",
                state="OPEN", summary="Approved brief generated and stored in the Research Vault.",
                classification=wi["classification"], run_id=str(wi["run_id"]) if wi["run_id"] else None,
                dedup_key=f"brief:{work_item_id}:{r}", evidence_artifact_id=art.artifact_id,
                payload={"artifact_id": art.artifact_id, "source_work_item_id": work_item_id}, actor="podos-worker",
            )
    return "completed"


async def list_work_items(svc: Services, ctx: PodContext, view: str) -> list[dict[str, Any]]:
    where = {
        "inbox": "archived_at IS NULL AND (snoozed_until IS NULL OR snoozed_until < now())",
        "actionable": "archived_at IS NULL AND kind = 'actionable' AND state = 'WAITING_FOR_HUMAN'",
        "archived": "archived_at IS NOT NULL",
        "all": "true",
    }.get(view)
    if where is None:
        raise Problem(422, "invalid_view")
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        return await fetchall(
            conn,
            f"""SELECT work_item_id::text, run_id::text, title, summary, category, kind, state, priority, classification,
                       version, deadline_at, created_at, updated_at, read_at, archived_at, snoozed_until, source_agent_id,
                       evidence_artifact_id::text, required_pod_role, assignee_user_id, resolved_by, payload
                FROM work_items WHERE pod_id = %s AND assignee_user_id = %s AND {where}
                ORDER BY (state = 'WAITING_FOR_HUMAN') DESC, created_at DESC LIMIT 200""",
            (ctx.active_pod_id, ctx.subject_id),
        )
