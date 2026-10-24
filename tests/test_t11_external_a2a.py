"""T11 - External (third-party) A2A agents.

PodOS can connect any standard A2A agent that is not one of its own ADK agents. Those run as separate
services, are treated as EXTERNAL, and receive only the user's question: no delegation token, no pod
research, no identifiers. Their stream is normalised onto the same run events (progress, answer, citations,
verification). This suite drives the whole path against a real a2a-sdk agent (tests/mock_external_agent.py).
"""

from __future__ import annotations

import pytest
from conftest import ENDPOINTS, _free_port, _Server, last, login, text_of

EXT_PORT = _free_port()
SPORT_PORT = _free_port()


@pytest.fixture(scope="session", autouse=True)
def external_agent():
    """The third-party agent, started as its own service (not part of the PodOS stack)."""
    import mock_external_agent
    from test_result_presentation import COURTS

    server = _Server(mock_external_agent.build_app(EXT_PORT), EXT_PORT)
    server.start()
    server.wait()
    sport_server = _Server(mock_external_agent.build_app(
        SPORT_PORT, name="Sportscanner Court Finder",
        description="Find publicly bookable badminton, squash, pickleball, padel and tennis courts in London. "
                    "Search available sports court slots by location, date and time, with prices and booking links.",
        result_data=COURTS), SPORT_PORT)
    sport_server.start()
    sport_server.wait()
    yield mock_external_agent
    server.server.should_exit = True
    sport_server.server.should_exit = True


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
    # The research group grants an entitlement in both pods, but this release is Alpha-only.
    # Beta can discover its entitlement without gaining invocation access.
    beta = {a["agent_id"]: a for a in (await ben.get("/v1/pods/pod_beta/agents")).json()}
    assert beta["entity-intelligence"]["health_status"] == "healthy"
    assert not any(beta["entity-intelligence"]["actions"].values())
    thread = await ben.new_thread("pod_beta")
    denied = await ben.post(f"/v1/threads/{thread}/runs", json={
        "mode": "direct", "agent_id": "entity-intelligence", "message": "Who owns this entity?",
    }, pod="pod_beta")
    assert denied.status_code == 403


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


async def connect_sportscanner(pat):
    r = await pat.post("/v1/admin/agents/connect", json={
        "url": f"http://127.0.0.1:{SPORT_PORT}", "agent_id": "sportscanner",
        "owner_team": "Sportscanner", "change_ticket": "CHG-SPORT-1"})
    assert r.status_code == 201, r.text
    reg = r.json()
    r = await pat.patch("/v1/admin/agents/sportscanner/status",
                        json={"publication_status": "pilot", "change_ticket": "CHG-SPORT-2"})
    assert r.status_code == 200, r.text
    await admin_patch(pat, reg["release_key"], stage="POD_PILOT_AUTO", published=True,
                      pod_allowlist=["pod_alpha"], eligible_groups=["research_users"],
                      feature_flags={"catalogue_visible": True, "direct_invoke": True, "auto_route": True})



async def test_sportscanner_is_visible_and_invocable_only_by_yasir(pat, external_agent):
    """A person-specific seed grant protects both catalogue and direct API dispatch."""
    await connect_sportscanner(pat)
    yasir = await login("yasir.khalid")
    try:
        me = (await yasir.get("/v1/me")).json()
        assert me["display_name"] == "Yasir Khalid"
        pods = (await yasir.get("/v1/me/pods")).json()
        assert [(p["pod_id"], p["pod_role"]) for p in pods] == [("pod_alpha", "pm")]
        agents = (await yasir.get("/v1/pods/pod_alpha/agents")).json()
        sport = next(a for a in agents if a["agent_id"] == "sportscanner")
        assert sport["external"] and sport["actions"]["direct"]
        assert sport["actions"]["auto"] and not sport["actions"]["background"]
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


async def test_auto_routes_courts_and_named_handoff_without_sending_private_answers(pat, external_agent, svc):
    await connect_sportscanner(pat)
    yasir = await login("yasir.khalid")
    try:
        question = "Um can you find pickleball slots for Wednesday near Central London?"
        external_agent.RECEIVED.clear()
        _, events = await yasir.ask("pod_alpha", question, mode="auto")
        assert next(e for e in events if e["type"] == "agent.selected")["agent_id"] == "sportscanner"
        assert external_agent.RECEIVED[0]["text"] == question

        # Reproduce a conversation that was already misrouted before this fix.
        thread = await yasir.new_thread("pod_alpha")
        await yasir.ask("pod_alpha", question, agent_id="equity-research", thread_id=thread)
        async with svc.db.pod_tx("pod_alpha") as conn:
            await conn.execute(
                """UPDATE messages SET content = jsonb_set(content, '{text}', '"PRIVATE-ANSWER-CANARY"')
                   WHERE thread_id = %s AND role = 'assistant'""", (thread,))
        external_agent.RECEIVED.clear()
        _, events = await yasir.ask("pod_alpha", "Um no check the Sportscanner um agent for this",
                                    mode="auto", thread_id=thread)
        assert last(events)["type"] == "run.completed"
        assert next(e for e in events if e["type"] == "agent.selected")["agent_id"] == "sportscanner"
        sent = external_agent.RECEIVED[0]
        assert question in sent["text"]
        assert "PRIVATE-ANSWER-CANARY" not in str(sent)
        assert not sent["metadata"] and not sent["request_metadata"]
        context = sent["context_id"]
        _, events = await yasir.ask("pod_alpha", "Yes, widen it to five miles", mode="auto", thread_id=thread)
        assert next(e for e in events if e["type"] == "agent.selected")["agent_id"] == "sportscanner"
        assert external_agent.RECEIVED[-1]["context_id"] == context
        assert external_agent.RECEIVED[-1]["text"] == "Yes, widen it to five miles"
    finally:
        await yasir.http.aclose()


async def test_auto_does_not_substitute_research_for_an_unreachable_external_agent(pat, svc):
    await connect_sportscanner(pat)
    async with svc.db.system_tx() as conn:
        await conn.execute("UPDATE agent_deployments SET service_ref='http://127.0.0.1:9' WHERE agent_id='sportscanner'")
    yasir = await login("yasir.khalid")
    try:
        _, events = await yasir.ask("pod_alpha", "Find pickleball court slots in London", mode="auto")
        assert [e["agent_id"] for e in events if e["type"] == "agent.selected"] == ["sportscanner"]
        assert last(events)["type"] == "run.failed"
    finally:
        await yasir.http.aclose()


async def test_private_research_is_not_sent_to_external_agents_on_fallback(pat, external_agent):
    import json

    await connect_sportscanner(pat)
    original = ENDPOINTS.read_text()
    endpoints = json.loads(original)
    endpoints["agent://equity-research/1.0.0"] = "http://127.0.0.1:9"
    ENDPOINTS.write_text(json.dumps(endpoints))
    yasir = await login("yasir.khalid")
    external_agent.RECEIVED.clear()
    try:
        _, events = await yasir.ask("pod_alpha", "NWSC thesis and private target price", mode="auto")
        assert last(events)["type"] == "run.failed"
        assert not external_agent.RECEIVED
    finally:
        ENDPOINTS.write_text(original)
        await yasir.http.aclose()


def test_external_input_required_reply_is_rendered_without_artifact():
    from podos_api.a2a_adapter import _GenericStream

    stream = _GenericStream()
    status = {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": "Which London postcode?"}]}}
    events = stream.feed({"statusUpdate": {"status": status}})
    assert ("delta", "Which London postcode?") in events
    assert ("state", "input_required") in events
    assert not [e for e in stream.feed({"task": {"status": status}}) if e[0] == "delta"]


async def test_result_cards_and_activity_are_available_after_reload(pat, external_agent, alice, ben):
    await connect_sportscanner(pat)
    yasir = await login("yasir.khalid")
    try:
        thread = await yasir.new_thread("pod_alpha")
        _, events = await yasir.ask("pod_alpha", "Find badminton courts near E1 2AD", agent_id="sportscanner", thread_id=thread)
        results = [e for e in events if e["type"] == "ui.result"]
        assert len(results) == 1
        assert results[0]["result"]["source"] == "Sportscanner Court Finder"
        saved = (await yasir.get(f"/v1/threads/{thread}", pod="pod_alpha")).json()["messages"][-1]
        activity = saved["content"]["activity"]
        assert {"agent.selected", "tool.started", "tool.completed", "ui.result", "run.completed"} <= {e["type"] for e in activity}
        assert next(e for e in activity if e["type"] == "ui.result")["result"] == results[0]["result"]
        assert (await alice.get(f"/v1/threads/{thread}", pod="pod_alpha")).status_code == 404
        assert (await ben.get(f"/v1/threads/{thread}", pod="pod_beta")).status_code == 404
    finally:
        await yasir.http.aclose()
