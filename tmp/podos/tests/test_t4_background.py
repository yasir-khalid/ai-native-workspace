"""T4 - Background monitors, durable work items, inbox, approvals and resumption."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
from conftest import internal_post, make_running_run, revoke
from podos_api import background
from podos_api.db import fetchall, fetchone
from podos_api.workitems import GENERIC_PREVIEW, resume_workflow

MAILPIT = "http://127.0.0.1:58025"


async def drain(svc, rounds: int = 8) -> None:
    for _ in range(rounds):
        stats = await background.tick(svc, "test-worker")
        if not any(stats.values()):
            return


async def alpha_monitor_id(alice) -> str:
    return (await alice.get("/v1/pods/pod_alpha/monitors")).json()[0]["monitor_id"]


async def trigger_alpha_catalyst(alice, svc) -> list[dict]:
    """New filing arrives in Alpha's feed; the monitor runs in the worker (no browser involved)."""
    assert (await alice.post("/v1/pods/pod_alpha/simulate/filing")).status_code == 201
    mid = await alpha_monitor_id(alice)
    assert (await alice.post(f"/v1/pods/pod_alpha/monitors/{mid}/run")).status_code == 202
    await drain(svc)
    return (await alice.get("/v1/pods/pod_alpha/work-items")).json()


async def approval_item(alice, svc) -> dict:
    items = await trigger_alpha_catalyst(alice, svc)
    return [i for i in items if i["kind"] == "actionable"][0]


async def decide(user, wi: dict, decision: str = "approve", *, version: int | None = None, key: str | None = None,
                 pod: str = "pod_alpha") -> httpx.Response:
    return await user.post(f"/v1/work-items/{wi['work_item_id']}/decisions", pod=pod, json={
        "decision": decision, "expected_version": wi["version"] if version is None else version,
        "idempotency_key": key or f"k-{uuid.uuid4().hex}", "comment": "reviewed"})


async def test_scheduled_monitor_creates_pod_scoped_inbox_items(alice, arjun, bianca, svc):
    async with svc.db.pod_tx("pod_alpha") as conn:  # make the monitor due via the scheduler path
        await conn.execute("UPDATE monitors SET next_run_at = now() - interval '1 second' WHERE pod_id = 'pod_alpha'")
    await alice.post("/v1/pods/pod_alpha/simulate/filing")
    await drain(svc)
    alice_items = (await alice.get("/v1/pods/pod_alpha/work-items")).json()
    arjun_items = (await arjun.get("/v1/pods/pod_alpha/work-items")).json()
    assert any(i["category"] == "alert" for i in alice_items) and any(i["category"] == "alert" for i in arjun_items)
    approvals = [i for i in alice_items if i["kind"] == "actionable"]
    assert len(approvals) == 1 and approvals[0]["state"] == "WAITING_FOR_HUMAN"
    assert not [i for i in arjun_items if i["kind"] == "actionable"], "approval goes to the PM only"
    assert (await bianca.get("/v1/pods/pod_beta/work-items")).json() == []
    runs = (await alice.get("/v1/pods/pod_alpha/runs", params={"scope": "pod"})).json()
    # The filing triggers the monitor immediately (event) and the scheduler also runs it as a backstop:
    # exactly one of those runs owns the approval, the other finds nothing new.
    bg = [r for r in runs if r["mode"] == "scheduled"]
    waiting = [r for r in bg if r["state"] == "WAITING_FOR_HUMAN"]
    assert len(waiting) == 1 and waiting[0]["monitor_name"]
    assert all(r["state"] in ("WAITING_FOR_HUMAN", "COMPLETED") for r in bg)
    # A second scheduler pass over the same filing creates no duplicates (checkpoint + dedup keys).
    async with svc.db.pod_tx("pod_alpha") as conn:
        await conn.execute("UPDATE monitors SET next_run_at = now() - interval '1 second', checkpoint = '{}'::jsonb")
    await drain(svc)
    assert len((await alice.get("/v1/pods/pod_alpha/work-items")).json()) == len(alice_items)


async def test_approval_resumes_exactly_once(alice, svc):
    wi = await approval_item(alice, svc)
    key = "approve-once-0001"
    r = await decide(alice, wi, key=key)
    assert r.status_code == 200 and r.json()["state"] == "APPROVED"
    replay = await decide(alice, wi, key=key)
    assert replay.status_code == 200 and replay.json()["status"] == "replayed"
    await drain(svc)
    await drain(svc)
    detail = (await alice.get(f"/v1/work-items/{wi['work_item_id']}", pod="pod_alpha")).json()
    assert detail["state"] == "COMPLETED" and detail["evidence_artifact_id"]
    assert [e["event_type"] for e in detail["events"]] == ["created", "decision.approve", "resumed", "completed"]
    arts = (await alice.get("/v1/pods/pod_alpha/artifacts")).json()
    assert len([a for a in arts if a["provenance"].get("work_item_id") == wi["work_item_id"]]) == 1
    ready = [i for i in (await alice.get("/v1/pods/pod_alpha/work-items")).json() if i["title"].startswith("Ready:")]
    assert len(ready) == 1
    runs = (await alice.get("/v1/pods/pod_alpha/runs", params={"scope": "pod"})).json()
    assert [r for r in runs if r["mode"] == "scheduled"][0]["state"] == "COMPLETED"


async def test_concurrent_and_crashed_resume_is_idempotent(alice, svc):
    wi = await approval_item(alice, svc)
    assert (await decide(alice, wi)).status_code == 200
    # Worker A claims the resume row and "crashes" (never finishes).
    claimed = await background.claim(svc, "pod_alpha", "worker-A")
    assert any(c["event_type"] == "workflow.resume" for c in claimed)
    async with svc.db.pod_tx("pod_alpha") as conn:  # its lease expires
        await conn.execute("UPDATE outbox_events SET locked_until = now() - interval '1 second' WHERE state = 'processing'")
    # Meanwhile two resumes race (e.g. a duplicate delivery) and worker B reclaims the expired lease.
    results = await asyncio.gather(resume_workflow(svc, "pod_alpha", wi["work_item_id"]),
                                   resume_workflow(svc, "pod_alpha", wi["work_item_id"]))
    await drain(svc)
    assert sorted(results).count("completed") == 1
    async with svc.db.pod_tx("pod_alpha") as conn:
        arts = await fetchall(conn, "SELECT 1 FROM artifacts WHERE idempotency_key = %s", (f"resume:{wi['work_item_id']}",))
        rows = await fetchall(conn, "SELECT state, attempts FROM outbox_events WHERE event_id = %s", (f"resume:{wi['work_item_id']}",))
        ready = await fetchall(conn, "SELECT 1 FROM work_items WHERE dedup_key LIKE %s", (f"brief:{wi['work_item_id']}:%",))
    assert len(arts) == 1 and len(ready) == 1
    assert rows[0]["state"] == "done" and rows[0]["attempts"] == 2


async def test_invalid_decisions_rejected(alice, arjun, bianca, svc):
    wi = await approval_item(alice, svc)
    r = await decide(arjun, wi)
    assert r.status_code == 403 and r.json()["code"] == "not_assigned_approver"
    r = await decide(bianca, wi, pod="pod_beta")
    assert r.status_code == 404
    r = await decide(alice, wi, version=wi["version"] + 3)
    assert r.status_code == 409 and r.json()["code"] == "stale_version"
    async with svc.db.pod_tx("pod_alpha") as conn:
        await conn.execute("UPDATE work_items SET deadline_at = now() - interval '1 minute' WHERE work_item_id::text = %s",
                           (wi["work_item_id"],))
    r = await decide(alice, wi)
    assert r.status_code == 409 and r.json()["code"] == "expired"
    r = await decide(alice, {**wi, "version": wi["version"] + 1})
    assert r.status_code == 409 and r.json()["code"] == "not_pending"


async def test_reject_does_not_resume(alice, svc):
    wi = await approval_item(alice, svc)
    assert (await decide(alice, wi, "reject")).json()["state"] == "REJECTED"
    await drain(svc)
    arts = (await alice.get("/v1/pods/pod_alpha/artifacts")).json()
    assert not [a for a in arts if a["provenance"].get("work_item_id") == wi["work_item_id"]]


async def test_revocation_blocks_approval_and_resume(alice, pat, svc):
    wi = await approval_item(alice, svc)
    assert (await decide(alice, wi)).status_code == 200
    await revoke(pat, "pod_alpha", "alice.chen")  # approver leaves before the worker resumes
    await drain(svc)
    async with svc.db.pod_tx("pod_alpha") as conn:
        row = await fetchone(conn, "SELECT state FROM work_items WHERE work_item_id::text = %s", (wi["work_item_id"],))
    assert row["state"] == "FAILED"


async def test_revoked_member_cannot_decide(alice, pat, svc):
    wi = await approval_item(alice, svc)
    await revoke(pat, "pod_alpha", "alice.chen")
    r = await decide(alice, wi)
    assert r.status_code == 403 and r.json()["code"] == "not_a_pod_member"


async def test_duplicate_agent_events_deduplicated(alice, svc):
    run = await make_running_run(svc, "pod_alpha", "alice.chen", agent="catalyst-monitor")
    ev = {"schema_version": "1.0", "event_id": f"{run}:dup-test", "pod_id": "pod_alpha", "agent_id": "catalyst-monitor",
          "run_id": run, "type": "monitor_alert", "classification": "pod_confidential",
          "recipient_user_ids": ["alice.chen", "ben.okafor"], "created_at": datetime.now(UTC).isoformat(),
          "payload": {"title": "Duplicate check", "summary": "x"}}
    first = await internal_post("/v1/internal/agent-events", ev, agent="catalyst-monitor")
    second = await internal_post("/v1/internal/agent-events", ev, agent="catalyst-monitor")
    assert first.json()["status"] == "accepted" and len(first.json()["work_item_ids"]) == 1, "non-member ben dropped"
    assert second.json()["status"] == "duplicate"
    titles = [i["title"] for i in (await alice.get("/v1/pods/pod_alpha/work-items")).json()]
    assert titles.count("Duplicate check") == 1


async def test_email_previews_are_generic(alice, svc):
    since = datetime.now(UTC)
    await trigger_alpha_catalyst(alice, svc)
    async with svc.db.pod_tx("pod_alpha") as conn:
        rows = await fetchall(conn, "SELECT preview, status FROM notification_deliveries WHERE channel = 'email'")
    assert rows and all(r["preview"] == GENERIC_PREVIEW for r in rows)
    assert all(r["status"] == "delivered" for r in rows)
    async with httpx.AsyncClient(base_url=MAILPIT) as c:
        msgs = (await c.get("/api/v1/messages", params={"limit": 50})).json()["messages"]
    recent = [m for m in msgs if datetime.fromisoformat(m["Created"].replace("Z", "+00:00")) >= since.replace(microsecond=0)]
    assert recent, "emails reached the SMTP channel"
    for m in recent:
        assert "NWSC" not in m["Snippet"] and "guidance" not in m["Snippet"].lower()
        assert GENERIC_PREVIEW.split(".")[0] in m["Snippet"]


async def test_broken_email_provider_retries_then_dead_letters(alice, svc):
    s = svc.settings
    s.email_provider_broken, s.outbox_max_attempts = True, 2
    try:
        await trigger_alpha_catalyst(alice, svc)
        for _ in range(3):
            async with svc.db.pod_tx("pod_alpha") as conn:
                await conn.execute("UPDATE outbox_events SET next_attempt_at = now() WHERE state = 'pending'")
            await drain(svc)
    finally:
        s.email_provider_broken, s.outbox_max_attempts = False, 5
    async with svc.db.pod_tx("pod_alpha") as conn:
        dead = await fetchall(conn, "SELECT event_type, attempts, last_error FROM outbox_events WHERE state = 'dead'")
        inapp = await fetchall(conn, "SELECT status FROM notification_deliveries WHERE channel = 'in_app'")
    assert dead and all(d["event_type"] == "inbox.notify" and "EmailProviderError" in d["last_error"] for d in dead)
    assert inapp and all(r["status"] == "delivered" for r in inapp), "inbox is independent of the email provider"
    assert len((await alice.get("/v1/pods/pod_alpha/work-items")).json()) >= 2


async def test_poison_outbox_row_is_dead_lettered(svc):
    async with svc.db.pod_tx("pod_alpha") as conn:
        await conn.execute("""INSERT INTO outbox_events(pod_id, event_id, event_type, payload, attempts)
                              VALUES ('pod_alpha', 'poison-1', 'unknown.type', '{}'::jsonb, 4)""")
    await drain(svc)
    async with svc.db.pod_tx("pod_alpha") as conn:
        row = await fetchone(conn, "SELECT state FROM outbox_events WHERE event_id = 'poison-1'")
    assert row["state"] == "dead"


async def test_monitor_with_revoked_owner_is_disabled(alice, pat, svc):
    mid = await alpha_monitor_id(alice)
    await revoke(pat, "pod_alpha", "alice.chen")
    res = await background.run_monitor(svc, "pod_alpha", mid)
    assert res["status"] == "owner_revoked"
    async with svc.db.pod_tx("pod_alpha") as conn:
        row = await fetchone(conn, "SELECT enabled FROM monitors WHERE monitor_id::text = %s", (mid,))
    assert row["enabled"] is False


async def test_beta_monitor_is_independent(bianca, alice, svc):
    user, pod = bianca, "pod_beta"
    await user.post(f"/v1/pods/{pod}/simulate/filing")
    mid = (await user.get(f"/v1/pods/{pod}/monitors")).json()[0]["monitor_id"]
    await user.post(f"/v1/pods/{pod}/monitors/{mid}/run")
    await drain(svc)
    beta_items = (await user.get(f"/v1/pods/{pod}/work-items")).json()
    assert beta_items and all("HLXT" in i["title"] or "Approve" in i["title"] for i in beta_items)
    assert (await alice.get("/v1/pods/pod_alpha/work-items")).json() == []
