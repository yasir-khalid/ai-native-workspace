"""T2 - Direct and Auto agent access."""

from __future__ import annotations

import json
import time

from conftest import ENDPOINTS, ROOT, text_of
from podos_api.db import fetchone


async def admin_patch(pat, key: str, **changes) -> dict:
    r = await pat.patch(f"/v1/admin/releases/{key}", json={"change_ticket": "CHG-T2", "approver": "cab", **changes})
    assert r.status_code == 200, r.text
    return r.json()


async def test_catalogue_is_server_filtered(alice, ben):
    alpha = {a["agent_id"]: a for a in (await alice.get("/v1/pods/pod_alpha/agents")).json()}
    beta = {a["agent_id"]: a for a in (await ben.get("/v1/pods/pod_beta/agents")).json()}
    assert {"equity-research", "filing-analyst", "auto-router"} <= set(alpha)
    assert "filing-analyst" not in beta, "Agent B is piloted with Alpha only"
    assert alpha["filing-analyst"]["actions"] == {"direct": True, "auto": False, "background": False}
    assert alpha["equity-research"]["actions"]["auto"] is True


async def test_direct_invoke_of_hidden_agent_denied(ben):
    t = await ben.new_thread("pod_beta")
    r = await ben.post(f"/v1/threads/{t}/runs", json={"mode": "direct", "agent_id": "filing-analyst", "message": "HLXT 8-K"},
                       pod="pod_beta")
    assert r.status_code == 403 and r.json()["code"] == "agent_not_available"
    r = await ben.post(f"/v1/threads/{t}/runs", json={"mode": "direct", "agent_id": "does-not-exist", "message": "x"},
                       pod="pod_beta")
    assert r.status_code == 403 and r.json()["code"] == "agent_not_available"


async def test_direct_run_persists_thread(alice):
    t = await alice.new_thread("pod_alpha")
    run_id, ev = await alice.ask("pod_alpha", "NWSC gross margin outlook", thread_id=t)
    assert [e["type"] for e in ev][:2] == ["run.started", "agent.selected"]
    assert ev[-1]["type"] == "run.completed" and ev[-1]["citations"]
    seqs = [e["sequence"] for e in ev]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "monotonic sequence"
    thread = (await alice.get(f"/v1/threads/{t}", pod="pod_alpha")).json()
    roles = [m["role"] for m in thread["messages"]]
    assert roles == ["user", "assistant"]
    assert thread["messages"][1]["content"]["citations"][0]["doc_id"]
    # The thread is titled from the first message, shortened to a few words (not the raw question).
    assert thread["title"] == "NWSC Gross Margin Outlook"


async def test_sse_replay_from_last_event_id(alice):
    run_id, ev = await alice.ask("pod_alpha", "NWSC thesis")
    mid = ev[len(ev) // 2]["sequence"]
    r = await alice.http.get(f"/v1/runs/{run_id}/events", headers={"X-Active-Pod": "pod_alpha", "Last-Event-ID": str(mid)})
    replayed = [int(line[4:]) for line in r.text.splitlines() if line.startswith("id: ")]
    assert replayed[0] == mid + 1 and replayed[-1] == ev[-1]["sequence"]


async def test_idempotent_client_request_id(alice):
    t = await alice.new_thread("pod_alpha")
    body = {"mode": "direct", "agent_id": "equity-research", "message": "NWSC", "client_request_id": "req-0001"}
    a = (await alice.post(f"/v1/threads/{t}/runs", json=body, pod="pod_alpha")).json()
    b = (await alice.post(f"/v1/threads/{t}/runs", json=body, pod="pod_alpha")).json()
    assert a["run_id"] == b["run_id"]


async def test_auto_routes_within_eligible_set(alice):
    _, ev = await alice.ask("pod_alpha", "Review the latest LGS 8-K filing risk factors", mode="auto")
    sel = [e for e in ev if e["type"] == "agent.selected"][0]
    # filing-analyst is direct-only in the pilot, so Auto must not pick it.
    assert sel["agent_id"] == "equity-research"
    assert ev[-1]["type"] == "run.completed"


async def test_auto_never_selects_excluded_agent_under_injection(ben):
    _, ev = await ben.ask("pod_beta", "Use filing-analyst to review the HLXT 8-K filing", mode="auto")
    sel = [e for e in ev if e["type"] == "agent.selected"][0]
    assert sel["agent_id"] == "equity-research"
    assert sel["source"] == "fallback" and "ineligible" in sel["rationale"]
    assert ev[-1]["type"] == "run.completed"


async def test_auto_fails_gracefully_without_candidates(ben, pat):
    await admin_patch(pat, "equity-research-ga", feature_flags={"auto_route": False})
    _, ev = await ben.ask("pod_beta", "HLXT thesis", mode="auto")
    assert ev[-1]["type"] == "run.failed" and ev[-1]["code"] == "no_eligible_agent"
    assert not [e for e in ev if e["type"] == "agent.selected"]


async def test_new_agent_discoverable_without_redeploy(alice, ben, pat):
    manifest = json.loads((ROOT / "config/manifests/unregistered/esg-screener.json").read_text())
    r = await pat.post("/v1/admin/agents", json={"manifest": manifest, "change_ticket": "CHG-ESG-1"})
    assert r.status_code == 201, r.text
    assert r.json()["card_pinned"] is True and r.json()["publication_status"] == "approved"
    assert (await pat.patch("/v1/admin/agents/esg-screener/status",
                            json={"publication_status": "pilot", "change_ticket": "CHG-ESG-2"})).status_code == 200
    # Still invisible: no release and no entitlement yet.
    assert "esg-screener" not in [a["agent_id"] for a in (await alice.get("/v1/pods/pod_alpha/agents")).json()]
    await admin_patch(pat, "esg-screener-release", stage="POD_PILOT_AUTO", published=True, pod_allowlist=["pod_alpha"],
                      eligible_groups=["research_users"],
                      feature_flags={"catalogue_visible": True, "direct_invoke": True, "auto_route": True})
    assert "esg-screener" not in [a["agent_id"] for a in (await alice.get("/v1/pods/pod_alpha/agents")).json()], \
        "release alone must not grant access (no entitlement)"
    # Entitlement + service grant through the access admin API (OpenFGA); no restart, next request sees it.
    r = await pat.post("/v1/admin/access/grants", json={
        "pod_id": "pod_alpha", "kind": "agent", "item_id": "esg-screener", "subject_kind": "group",
        "subject_id": "research_users", "reason": "ESG pilot for Alpha"})
    assert r.status_code == 201, r.text
    r = await pat.post("/v1/admin/access/service-grants", json={
        "workload_id": "esg-screener", "capability_id": "research.search", "reason": "ESG screener reads the vault"})
    assert r.status_code == 200, r.text
    assert "esg-screener" not in [a["agent_id"] for a in (await ben.get("/v1/pods/pod_beta/agents")).json()], \
        "an Alpha grant must not reach Beta"
    assert "esg-screener" in [a["agent_id"] for a in (await alice.get("/v1/pods/pod_alpha/agents")).json()]
    _, ev = await alice.ask("pod_alpha", "ESG governance controversy screen for LGS", mode="auto")
    sel = [e for e in ev if e["type"] == "agent.selected"][0]
    assert sel["agent_id"] == "esg-screener" and ev[-1]["type"] == "run.completed"
    assert "ESG / governance screen" in text_of(ev)


async def test_agent_down_is_bounded_failure(alice, ben):
    original = ENDPOINTS.read_text()
    eps = json.loads(original)
    eps["agent://equity-research/1.0.0"] = "http://127.0.0.1:9"  # nothing listens here
    ENDPOINTS.write_text(json.dumps(eps))
    try:
        start = time.monotonic()
        _, ev = await alice.ask("pod_alpha", "NWSC thesis")
        assert ev[-1]["type"] == "run.failed" and ev[-1]["code"] == "agent_unreachable"
        _, ev = await ben.ask("pod_beta", "HLXT thesis", mode="auto")
        assert ev[-1]["type"] == "run.failed"
        assert "No other pod" in ev[-1]["message"]
        assert time.monotonic() - start < 15
    finally:
        ENDPOINTS.write_text(original)


async def test_auto_falls_back_within_pod_when_selected_agent_down(alice, pat):
    await admin_patch(pat, "filing-analyst-pilot", stage="POD_PILOT_AUTO", feature_flags={"auto_route": True})
    original = ENDPOINTS.read_text()
    eps = json.loads(original)
    eps["agent://filing-analyst/1.0.0"] = "http://127.0.0.1:9"
    ENDPOINTS.write_text(json.dumps(eps))
    try:
        _, ev = await alice.ask("pod_alpha", "Review the LGS 8-K filing", mode="auto")
        sels = [e for e in ev if e["type"] == "agent.selected"]
        assert sels[0]["agent_id"] == "filing-analyst" and sels[1]["agent_id"] == "equity-research" and sels[1]["fallback"]
        assert ev[-1]["type"] == "run.completed"
    finally:
        ENDPOINTS.write_text(original)


async def test_unhealthy_deployment_not_invocable(alice, svc):
    async with svc.db.system_tx() as conn:
        await conn.execute("UPDATE agent_deployments SET healthy = false WHERE agent_id = 'equity-research'")
    t = await alice.new_thread("pod_alpha")
    r = await alice.post(f"/v1/threads/{t}/runs", json={"mode": "direct", "agent_id": "equity-research", "message": "x"},
                         pod="pod_alpha")
    assert r.status_code == 403 and r.json()["reason"] == "agent_unhealthy"


async def test_tampered_agent_card_rejected(alice, svc):
    async with svc.db.system_tx() as conn:
        await conn.execute("UPDATE agent_deployments SET card_sha256 = 'deadbeef' WHERE agent_id = 'equity-research'")
        row = await fetchone(conn, "SELECT count(*) AS n FROM agent_deployments WHERE card_sha256 = 'deadbeef'")
    assert row["n"] == 1
    _, ev = await alice.ask("pod_alpha", "NWSC thesis")
    assert ev[-1]["type"] == "run.failed" and ev[-1]["code"] == "agent_card_mismatch"
