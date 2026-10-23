"""T6 - Bounded failure modes, quotas, fail-closed permissions, redaction."""

from __future__ import annotations

import asyncio
import json
import time

import conftest
import pytest
from conftest import ENDPOINTS, POLICY, delegation, internal_post, make_running_run
from podos_api.audit import record
from podos_api.db import Jsonb, fetchone
from podos_api.runs import RateLimiter


@pytest.fixture
def api_services():
    return conftest.API_APP.state.services


def set_policy(**quota) -> None:
    p = json.loads(POLICY.read_text())
    p["revision"] = f"policy-t6-{time.time_ns()}"
    p["pod_quotas"]["default"].update(quota)
    POLICY.write_text(json.dumps(p))


async def _raw_server(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _point(agent_ref: str, port: int) -> str:
    original = ENDPOINTS.read_text()
    eps = json.loads(original)
    eps[agent_ref] = f"http://127.0.0.1:{port}"
    ENDPOINTS.write_text(json.dumps(eps))
    return original


async def test_a2a_timeout_is_bounded(alice):
    async def blackhole(reader, writer):
        await asyncio.sleep(30)

    server, port = await _raw_server(blackhole)
    original = _point("agent://equity-research/1.0.0", port)
    try:
        start = time.monotonic()
        _, ev = await alice.ask("pod_alpha", "NWSC thesis")
        assert ev[-1]["type"] == "run.failed" and ev[-1]["code"] == "agent_unreachable"
        assert time.monotonic() - start < 8
    finally:
        ENDPOINTS.write_text(original)
        server.close()


async def test_invalid_agent_card_is_rejected(alice):
    async def bad_card(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        body = b'{"not": "a card"}'
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        await writer.drain()
        writer.close()

    server, port = await _raw_server(bad_card)
    original = _point("agent://equity-research/1.0.0", port)
    try:
        _, ev = await alice.ask("pod_alpha", "NWSC thesis")
        assert ev[-1]["type"] == "run.failed" and ev[-1]["code"] == "agent_card_invalid"
    finally:
        ENDPOINTS.write_text(original)
        server.close()


async def test_per_pod_rate_limit_is_fair_share(alice, ben, api_services):
    api_services.runs.limiter = RateLimiter()
    set_policy(runs_per_minute=2)
    for _ in range(2):
        await alice.ask("pod_alpha", "NWSC")
    t = await alice.new_thread("pod_alpha")
    r = await alice.post(f"/v1/threads/{t}/runs", json={"mode": "direct", "agent_id": "equity-research", "message": "x"},
                         pod="pod_alpha")
    assert r.status_code == 429 and r.json()["code"] == "pod_rate_limit"
    _, ev = await ben.ask("pod_beta", "HLXT")  # Alpha's burst does not starve Beta
    assert ev[-1]["type"] == "run.completed"
    api_services.runs.limiter = RateLimiter()


async def test_per_pod_concurrency_limit(alice, svc):
    set_policy(max_concurrent_runs=1)
    async with svc.db.pod_tx("pod_alpha") as conn:
        await conn.execute(
            """INSERT INTO agent_runs(pod_id, agent_id, agent_version, mode, state, created_by, execution_context, release_revision)
               VALUES ('pod_alpha','equity-research','1.0.0','direct','RUNNING','alice.chen',%s,'t')""", (Jsonb({}),))
    t = await alice.new_thread("pod_alpha")
    r = await alice.post(f"/v1/threads/{t}/runs", json={"mode": "direct", "agent_id": "equity-research", "message": "x"},
                         pod="pod_alpha")
    assert r.status_code == 429 and r.json()["code"] == "pod_concurrency_limit"


async def test_tool_call_budget_enforced(arjun):
    set_policy(max_tool_calls_per_run=1)
    _, ev = await arjun.ask("pod_alpha", "Prepare a deck on NWSC")
    pptx = [e for e in ev if e["type"] == "tool.completed" and e.get("tool") == "document.export.pptx"][0]
    assert pptx["status"] == "denied" and pptx["code"] == "tool_budget_exceeded"


async def test_user_without_permissions_fails_closed(dana, pat):
    # Still a Beta member, but in no groups: every entitlement comes from groups, so nothing is allowed.
    r = await pat.post("/v1/admin/access/groups/members", json={
        "pod_id": "pod_beta", "group": "research_users", "subject_id": "dana.kim", "remove": True, "reason": "T6 fail-closed"})
    assert r.status_code == 200, r.text
    assert (await dana.get("/v1/pods/pod_beta/agents")).json() == []
    assert (await dana.get("/v1/pods/pod_beta/capabilities")).json() == []
    t = await dana.new_thread("pod_beta")
    r = await dana.post(f"/v1/threads/{t}/runs", json={"mode": "auto", "message": "HLXT"}, pod="pod_beta")
    assert r.status_code == 403
    r = await dana.get("/v1/pods/pod_beta/search", params={"q": "HLXT"})
    assert r.status_code == 403


async def test_delegation_depth_bounded(svc):
    run = await make_running_run(svc, "pod_alpha", "alice.chen")
    from podos_api.security.tokens import issue_delegation
    from podos_contracts.models import DelegationClaims

    tok = issue_delegation(svc.settings, DelegationClaims(sub="alice.chen", pod="pod_alpha", run=run, agent="equity-research",
                                                          caps=["research.search"], depth=5, purpose="research",
                                                          cls="pod_confidential", rev="t"))
    r = await internal_post("/v1/internal/tools/research.search", {"query": "x"}, agent="equity-research", deleg=tok)
    assert r.status_code == 403 and r.json()["code"] == "delegation_depth_exceeded"
    assert delegation  # helper import retained for symmetry with T1


async def test_audit_and_logs_are_redacted(svc, caplog):
    await record(svc.db, actor="t", action="test.redaction", target="x",
                 detail={"message": "ALPHA-PRIVATE secret thesis", "prompt": "p", "count": 3})
    async with svc.db.system_tx() as conn:
        row = await fetchone(conn, "SELECT detail FROM audit_events WHERE action = 'test.redaction'")
    assert row["detail"] == {"message": "[redacted]", "prompt": "[redacted]", "count": 3}
    assert "ALPHA-PRIVATE" not in caplog.text


async def test_interrupted_runs_are_recovered(svc):
    async with svc.db.pod_tx("pod_alpha") as conn:
        row = await fetchone(
            conn,
            """INSERT INTO agent_runs(pod_id, agent_id, agent_version, mode, state, created_by, execution_context, release_revision)
               VALUES ('pod_alpha','equity-research','1.0.0','direct','RUNNING','alice.chen',%s,'t') RETURNING run_id::text""",
            (Jsonb({}),))
    assert await svc.runs.recover_orphans() >= 1
    async with svc.db.pod_tx("pod_alpha") as conn:
        st = await fetchone(conn, "SELECT state, error FROM agent_runs WHERE run_id::text = %s", (row["run_id"],))
    assert st["state"] == "FAILED" and st["error"]["code"] == "interrupted"


async def test_contract_schemas_validate_seed_and_example():
    import jsonschema
    from podos_api.seed import load_schema
    from podos_contracts import SCHEMA_DIR

    example = json.loads((SCHEMA_DIR / "release-policy.example.json").read_text())
    # The handoff example predates kind/stage; the extended schema requires them explicitly.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(example, load_schema("release-policy.schema.json"))
    jsonschema.validate({**example, "kind": "agent", "stage": "POD_PILOT_DIRECT"}, load_schema("release-policy.schema.json"))
