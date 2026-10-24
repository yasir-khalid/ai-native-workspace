"""T7: OpenFGA authorization and the access admin API.

Every decision comes from OpenFGA; these tests change the relationship graph through the admin API
(or directly, to simulate expiry and outages) and check that the very next request reflects it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import conftest
import httpx
from podos_api.authz import tuples as T
from podos_api.authz.fga import Tuple


async def caps(u, pod: str) -> set[str]:
    return {c["capability_id"] for c in (await u.get(f"/v1/pods/{pod}/capabilities")).json()}


async def grant(pat, **body):
    return await pat.post("/v1/admin/access/grants", json={"reason": "T7 test", **body})


async def test_group_removal_revokes_on_next_request(arjun, pat):
    assert "document.export.pptx" in await caps(arjun, "pod_alpha")
    r = await pat.post("/v1/admin/access/groups/members", json={
        "pod_id": "pod_alpha", "group": "deck_builders", "subject_id": "arjun.patel", "remove": True, "reason": "T7 revoke"})
    assert r.status_code == 200
    assert "document.export.pptx" not in await caps(arjun, "pod_alpha")
    _, ev = await arjun.ask("pod_alpha", "Make a 5-slide PowerPoint on our NWSC thesis.")
    assert not [e for e in ev if e["type"] == "tool.completed" and e.get("tool") == "document.export.pptx"
                and e.get("status") == "succeeded"]


async def test_direct_grant_is_pod_scoped_and_needs_membership(ben, pat):
    assert "document.export.pptx" not in await caps(ben, "pod_beta")
    r = await grant(pat, pod_id="pod_beta", kind="capability", item_id="document.export.pptx",
                    subject_kind="user", subject_id="ben.okafor")
    assert r.status_code == 201, r.text
    assert "document.export.pptx" in await caps(ben, "pod_beta")
    # A grant in Alpha for someone who isn't an Alpha member is refused outright...
    r = await grant(pat, pod_id="pod_alpha", kind="agent", item_id="equity-research", subject_kind="user",
                    subject_id="ben.okafor")
    assert r.status_code == 422 and r.json()["code"] == "not_a_pod_member"
    # ...and Beta access never leaks into Alpha.
    assert (await ben.get("/v1/pods/pod_alpha/capabilities")).status_code == 403


async def test_time_bound_grant_expires(ben, pat, svc):
    r = await grant(pat, pod_id="pod_beta", kind="capability", item_id="document.export.pptx", subject_kind="user",
                    subject_id="ben.okafor", expires_at=(datetime.now(UTC) + timedelta(days=2)).isoformat())
    assert r.status_code == 201, r.text
    assert "document.export.pptx" in await caps(ben, "pod_beta")
    eff = (await pat.get("/v1/admin/access/effective", params={"subject_id": "ben.okafor", "pod_id": "pod_beta"})).json()
    row = [i for i in eff["items"] if i["id"] == "document.export.pptx"][0]
    assert row["entitled"] and row["via"][0]["kind"] == "user" and row["via"][0]["expires_at"]
    # Simulate the clock passing the expiry: same tuple, expiry in the past.
    obj = T.scoped("capability", "pod_beta", "document.export.pptx")
    await svc.fga.write(deletes=[Tuple("user:ben.okafor", "grantee", obj)])
    await svc.fga.write(writes=[Tuple("user:ben.okafor", "grantee", obj, datetime.now(UTC) - timedelta(minutes=1))])
    assert "document.export.pptx" not in await caps(ben, "pod_beta")
    r = await grant(pat, pod_id="pod_beta", kind="capability", item_id="document.export.pptx", subject_kind="user",
                    subject_id="ben.okafor", expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    assert r.status_code == 422 and r.json()["code"] == "invalid_expiry"


async def test_effective_access_explains_and_matches_enforcement(arjun, pat):
    eff = (await pat.get("/v1/admin/access/effective", params={"subject_id": "arjun.patel", "pod_id": "pod_alpha"})).json()
    by_id = {i["id"]: i for i in eff["items"]}
    assert by_id["document.export.pptx"]["via"] == [{"kind": "group", "id": "deck_builders", "expires_at": None}]
    assert by_id["work_item.approve"]["entitled"] is False
    assert by_id["briefing"]["via"][0]["kind"] == "pod"
    enforced = await caps(arjun, "pod_alpha")
    assert {i["id"] for i in eff["items"] if i["kind"] == "capability" and i["available"]} == enforced
    # Not a member: nothing to explain.
    eff = (await pat.get("/v1/admin/access/effective", params={"subject_id": "ben.okafor", "pod_id": "pod_alpha"})).json()
    assert eff["member"] is False and eff["items"] == []


async def test_approver_action_via_group(arjun, pat):
    r = await pat.post("/v1/admin/access/groups/members", json={
        "pod_id": "pod_alpha", "group": "approvers", "subject_id": "arjun.patel", "reason": "cover for Alice"})
    assert r.status_code == 200
    eff = (await pat.get("/v1/admin/access/effective", params={"subject_id": "arjun.patel", "pod_id": "pod_alpha"})).json()
    assert [i for i in eff["items"] if i["id"] == "work_item.approve"][0]["entitled"] is True
    r = await grant(pat, pod_id="pod_alpha", kind="action", item_id="work_item.approve", subject_kind="pod")
    assert r.status_code == 422


async def test_ledger_and_admin_only(alice, pat):
    await grant(pat, pod_id="pod_alpha", kind="feature", item_id="memory", subject_kind="group", subject_id="research_users")
    r = await pat.post("/v1/admin/access/grants/revoke", json={
        "pod_id": "pod_alpha", "kind": "feature", "item_id": "memory", "subject_kind": "group",
        "subject_id": "research_users", "reason": "rollback"})
    assert r.status_code == 200
    rows = (await pat.get("/v1/admin/access/changes", params={"pod_id": "pod_alpha"})).json()
    assert [r["action"] for r in rows[:2]] == ["revoke", "grant"] and rows[0]["actor"] == "pat.morgan"
    for path in ("/v1/admin/access/people", "/v1/admin/access/catalogue", "/v1/admin/access/changes"):
        assert (await alice.get(path)).status_code == 403
    assert (await grant(alice, pod_id="pod_alpha", kind="agent", item_id="esg-screener", subject_kind="pod")).status_code == 403


async def test_platform_admin_role_lives_in_openfga(pat, svc):
    assert (await pat.get("/v1/admin/access/people")).status_code == 200
    await svc.fga.write(deletes=[T.platform_admin_tuple("pat.morgan")])
    assert (await pat.get("/v1/admin/access/people")).status_code == 403
    assert (await pat.get("/v1/me")).json()["is_admin"] is False


async def test_openfga_outage_fails_closed(alice):
    fga = conftest.API_APP.state.services.fga
    real = fga._http
    fga._http = httpx.AsyncClient(base_url="http://127.0.0.1:9", timeout=1)
    try:
        r = await alice.get("/v1/pods/pod_alpha/agents")
        assert r.status_code == 503 and r.json()["code"] == "authorization_unavailable"
        r = await alice.get("/v1/pods/pod_alpha/search", params={"q": "NWSC"})
        assert r.status_code == 503
    finally:
        await fga._http.aclose()
        fga._http = real
    assert (await alice.get("/v1/pods/pod_alpha/agents")).status_code == 200
