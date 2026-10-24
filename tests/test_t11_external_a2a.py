"""T11 - External (third-party) A2A agents.

PodOS can connect any standard A2A agent that is not one of its own ADK agents. Those run as separate
services, are treated as EXTERNAL, and receive only the user's question: no delegation token, no pod
research, no identifiers. Their stream is normalised onto the same run events (progress, answer, citations,
verification). This suite drives the whole path against a real a2a-sdk agent (tests/mock_external_agent.py).
"""

from __future__ import annotations

import pytest
from conftest import _free_port, _Server, last, login, text_of

EXT_PORT = _free_port()


@pytest.fixture(scope="session", autouse=True)
def external_agent():
    """The third-party agent, started as its own service (not part of the PodOS stack)."""
    import mock_external_agent

    server = _Server(mock_external_agent.build_app(EXT_PORT), EXT_PORT)
    server.start()
    server.wait()
    yield mock_external_agent
    server.server.should_exit = True


async def admin_patch(pat, key: str, **changes) -> dict:
    r = await pat.patch(f"/v1/admin/releases/{key}", json={"change_ticket": "CHG-T11", "approver": "cab", **changes})
    assert r.status_code == 200, r.text
    return r.json()


async def connect_entity_intelligence(pat) -> dict:
    """Register the external agent by URL, release it to Alpha, and grant the pod entitlement."""
    r = await pat.post("/v1/admin/agents/connect", json={
        "url": f"http://127.0.0.1:{EXT_PORT}", "agent_id": "entity-intelligence",
        "owner_team": "Institutional Entity Intelligence", "change_ticket": "CHG-A2A-1"})
    assert r.status_code == 201, r.text
    reg = r.json()
    assert reg["card_pinned"] is True and reg["card_error"] is None
    # A connected agent is registered "approved" but reaches no pod until it is piloted, released and granted.
    s = await pat.patch("/v1/admin/agents/entity-intelligence/status",
                        json={"publication_status": "pilot", "change_ticket": "CHG-A2A-2"})
    assert s.status_code == 200, s.text
    await admin_patch(pat, reg["release_key"], stage="POD_PILOT_AUTO", published=True,
                      pod_allowlist=["pod_alpha"], eligible_groups=["research_users"],
                      feature_flags={"catalogue_visible": True, "direct_invoke": True})
    g = await pat.post("/v1/admin/access/grants", json={
        "pod_id": "pod_alpha", "kind": "agent", "item_id": "entity-intelligence", "subject_kind": "group",
        "subject_id": "research_users", "reason": "Entity intelligence pilot for Alpha"})
    assert g.status_code == 201, g.text
    return reg


async def test_connect_pins_card_and_shows_external_in_catalogue(pat, alice, ben):
    await connect_entity_intelligence(pat)
    agents = {a["agent_id"]: a for a in (await alice.get("/v1/pods/pod_alpha/agents")).json()}
    assert "entity-intelligence" in agents
    entry = agents["entity-intelligence"]
    assert entry["external"] is True
    assert entry["actions"]["direct"] is True and entry["healthy"] is True
    # The release and entitlement are Alpha-only; Beta must not see the agent.
    assert "entity-intelligence" not in [a["agent_id"] for a in (await ben.get("/v1/pods/pod_beta/agents")).json()]


async def test_external_agent_receives_only_the_question(pat, alice, external_agent):
    await connect_entity_intelligence(pat)
    external_agent.RECEIVED.clear()
    _, ev = await alice.ask("pod_alpha", "Who is the direct parent of BlackRock Fund Advisors?",
                            agent_id="entity-intelligence")

    assert last(ev)["type"] == "run.completed"
    assert "BlackRock, Inc." in text_of(ev)

    reqs = external_agent.RECEIVED
    assert len(reqs) == 1
    assert reqs[0]["text"] == "Who is the direct parent of BlackRock Fund Advisors?"
    assert not reqs[0]["metadata"], "no A2A message metadata must leave PodOS for an external agent"
    assert not reqs[0]["request_metadata"], "no request metadata must leave PodOS for an external agent"


async def test_external_stream_maps_progress_answer_citations_and_verification(pat, alice):
    await connect_entity_intelligence(pat)
    _, ev = await alice.ask("pod_alpha", "Who is the direct parent of BlackRock Fund Advisors?",
                            agent_id="entity-intelligence")

    progress = [e for e in ev if e["type"] == "tool.started" and e.get("tool") == "agent.progress"]
    assert any("Searching name" in (e.get("label") or "") for e in progress), "status text becomes a progress step"

    verifications = [e for e in ev if e["type"] == "tool.completed" and e.get("tool") == "agent.verification"]
    assert verifications and verifications[0]["status"] == "ok"
    assert "verified" in verifications[0]["summary"].lower()

    cites = last(ev)["citations"]
    assert cites and all(c["external"] for c in cites)
    assert cites[0]["title"].startswith("LEI "), "evidence criteria become a friendly citation title"
    assert cites[0]["url"].startswith("https://"), "a source URI is kept only when it is https"
    assert cites[0]["doc_id"] is None


async def test_sportscanner_is_visible_and_invocable_only_by_yasir(pat, external_agent):
    """A person-specific seed grant protects both catalogue and direct API dispatch."""
    r = await pat.post("/v1/admin/agents/connect", json={
        "url": f"http://127.0.0.1:{EXT_PORT}", "agent_id": "sportscanner",
        "owner_team": "Sportscanner", "change_ticket": "CHG-SPORT-1"})
    assert r.status_code == 201, r.text
    reg = r.json()
    r = await pat.patch("/v1/admin/agents/sportscanner/status",
                        json={"publication_status": "pilot", "change_ticket": "CHG-SPORT-2"})
    assert r.status_code == 200, r.text
    await admin_patch(pat, reg["release_key"], stage="POD_PILOT_DIRECT", published=True,
                      pod_allowlist=["pod_alpha"], eligible_groups=["research_users"],
                      feature_flags={"catalogue_visible": True, "direct_invoke": True, "auto_route": False})

    yasir = await login("yasir.khalid")
    try:
        me = (await yasir.get("/v1/me")).json()
        assert me["display_name"] == "Yasir Khalid"
        pods = (await yasir.get("/v1/me/pods")).json()
        assert [(p["pod_id"], p["pod_role"]) for p in pods] == [("pod_alpha", "pm")]
        agents = (await yasir.get("/v1/pods/pod_alpha/agents")).json()
        sport = next(a for a in agents if a["agent_id"] == "sportscanner")
        assert sport["external"] and sport["actions"]["direct"]
        assert not sport["actions"]["auto"] and not sport["actions"]["background"]
        external_agent.RECEIVED.clear()
        _, events = await yasir.ask("pod_alpha", "Find badminton courts near E1 2AD", agent_id="sportscanner")
        assert last(events)["type"] == "run.completed"
        assert external_agent.RECEIVED[0]["text"] == "Find badminton courts near E1 2AD"
        assert not external_agent.RECEIVED[0]["metadata"]
        assert not external_agent.RECEIVED[0]["request_metadata"]
    finally:
        await yasir.http.aclose()

    for subject, pod in [("alice.chen", "pod_alpha"), ("arjun.patel", "pod_alpha"),
                         ("dana.kim", "pod_alpha"), ("bianca.rossi", "pod_beta"),
                         ("ben.okafor", "pod_beta"), ("dana.kim", "pod_beta")]:
        other = await login(subject)
        try:
            catalogue = await other.get(f"/v1/pods/{pod}/agents")
            assert catalogue.status_code == 200
            assert "sportscanner" not in {a["agent_id"] for a in catalogue.json()}
            thread = await other.new_thread(pod)
            denied = await other.post(f"/v1/threads/{thread}/runs", pod=pod,
                                      json={"mode": "direct", "agent_id": "sportscanner", "message": "Find courts"})
            assert denied.status_code == 403, (subject, denied.text)
        finally:
            await other.http.aclose()
    # Administration does not confer research access.
    assert (await pat.get("/v1/pods/pod_alpha/agents")).status_code == 403
