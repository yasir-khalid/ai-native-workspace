"""Call attachments use ordinary Auto runs, remain pod-scoped, and survive thread reloads."""

import conftest
from podos_agents import agentic
from podos_api.db import Jsonb
from podos_api.routes.calls import _build_chat_context


def test_chat_context_includes_later_answers_for_broad_questions():
    transcript = {"paragraphs": [{"speaker": "CEO", "sentences": [
        {"id": f"s{i}", "start": i, "end": i + 1, "text": f"Topic {i}: " + "evidence " * 30}
        for i in range(150)
    ]}]}
    evidence = _build_chat_context(transcript, [], [], "What were the three biggest takeaways?")
    assert "Complete transcript" in evidence
    assert "[s0]" in evidence and "[s149]" in evidence


async def seed_call(svc):
    async with svc.db.pod_tx("pod_alpha") as conn:
        await conn.execute(
            """INSERT INTO call_events(pod_id, call_id, company, ticker, event_title, event_date,
                   duration, audio_path, transcript) VALUES
                   ('pod_alpha','alpha-private-call','Example','EX','Earnings call','2026-10-01',60,'test.mp3',%s)""",
            (Jsonb({"company": "Example", "ticker": "EX", "event_title": "Earnings call",
                    "paragraphs": [{"speaker": "CEO", "sentences": [
                        {"id": "s1", "start": 0, "end": 10, "text": "Revenue grew 25 percent."}
                    ]}]}),),
        )


async def test_auto_receives_evidence_and_persists_context(alice, svc, monkeypatch):
    await seed_call(svc)
    observed = []

    async def stream(deployment, message, metadata, **kwargs):
        if "route" in metadata:
            yield "event", {"type": "route.selected", "agent_id": "equity-research"}
        else:
            context = metadata.get("call_context")
            observed.append(context)
            if context:
                citation = {**{k: v for k, v in context.items() if k not in {"sentences", "speakers", "text"}}, **context["sentences"][0], "ref": 1}
                yield "event", {"type": "sources.available", "items": [citation]}
            yield "delta", "Grounded answer [1]."
            if context:
                yield "event", {"type": "citations", "items": [citation]}

    monkeypatch.setattr(conftest.API_APP.state.services.a2a, "stream", stream)
    thread = await alice.new_thread("pod_alpha")
    _, events = await alice.ask("pod_alpha", "Biggest takeaways?", mode="auto", thread_id=thread,
                                call_id="alpha-private-call")
    assert events[-1]["type"] == "run.completed"
    assert observed[0]["call_id"] == "alpha-private-call"
    assert "Revenue grew 25 percent" in observed[0]["text"]
    assert observed[0]["sentences"][0]["sentence_id"] == "s1"
    assert observed[0]["sentences"][0]["end"] == 10
    assert len(observed[0]["transcript_sha256"]) == 64
    assert observed[0]["review_status"] == "unreviewed"
    stored = (await alice.get(f"/v1/threads/{thread}", pod="pod_alpha")).json()
    assert stored["messages"][0]["content"]["call_id"] == "alpha-private-call"
    citation = stored["messages"][1]["content"]["citations"][0]
    assert citation["text"] == "Revenue grew 25 percent."
    assert citation["transcript_sha256"] == observed[0]["transcript_sha256"]
    assert next(e for e in events if e["type"] == "sources.available")["sequence"] < next(e for e in events if e["type"] == "message.delta")["sequence"]
    # Follow-ups inherit the attachment even when clients omit the optional field.
    await alice.ask("pod_alpha", "What about revenue?", mode="auto", thread_id=thread)
    assert observed[-1]["call_id"] == "alpha-private-call"
    # Explicit removal stays removed on the next follow-up.
    await alice.ask("pod_alpha", "Clear context", mode="auto", thread_id=thread, call_id=None)
    await alice.ask("pod_alpha", "Continue", mode="auto", thread_id=thread)
    assert observed[-2:] == [None, None]


async def test_other_pod_cannot_attach_call(ben, svc):
    await seed_call(svc)
    thread = await ben.new_thread("pod_beta")
    response = await ben.post(f"/v1/threads/{thread}/runs", pod="pod_beta", json={
        "mode": "auto", "message": "Summarise this call", "call_id": "alpha-private-call",
    })
    assert response.status_code == 404
    stored = (await ben.get(f"/v1/threads/{thread}", pod="pod_beta")).json()
    assert stored["messages"] == []


async def test_agent_uses_attachment_as_numbered_source(monkeypatch):
    meta = {"api_base": "http://unused.test", "call_context": {
        "call_id": "call-1", "doc_id": "call:call-1", "title": "Example earnings",
        "source": "Call transcript", "kind": "transcript", "published_at": "2026-10-01",
        "text": "[s1] Revenue grew 25 percent.\n[s2] Margin was 30 percent.",
        "sentences": [
            {"sentence_id": "s1", "speaker": "CEO", "start": 1.2, "text": "Revenue grew 25 percent."},
            {"sentence_id": "s2", "speaker": "CFO", "start": 20.35, "text": "Margin was 30 percent."},
        ],
        "speakers": [{"name": "CFO", "role": "Chief Financial Officer"}],
    }}

    class Agent:
        def podos_meta(self, ctx):
            return meta

        def user_text(self, ctx):
            return "What happened?"

        def text(self, ctx, value):
            return value

        def event(self, ctx, value):
            return value

    async def chat(messages, **kwargs):
        assert "exact supporting sentence numbers" in messages[0]["content"]
        assert "[1] Revenue grew 25 percent" in messages[1]["content"]
        assert "[2] Margin was 30 percent" in messages[1]["content"]
        yield "text", "The CFO reported margin of 30 percent [2]."

    monkeypatch.setattr(agentic.llm, "stream_chat", chat)
    output = [event async for event in agentic.run_tool_loop(Agent(), None, agent_id="equity-research", system="Analyst")]
    assert output[0]["type"] == "sources.available"
    assert len(output[0]["items"]) == 2
    assert output[-1]["type"] == "citations"
    assert output[-1]["items"][0]["call_id"] == "call-1"
    assert len(output[-1]["items"]) == 1
    citation = output[-1]["items"][0]
    assert citation["ref"] == 2
    assert citation["sentence_id"] == "s2"
    assert citation["start"] == 20.35
    assert citation["role"] == "Chief Financial Officer"
    assert citation["text"] == "Margin was 30 percent."


def test_sources_do_not_present_retrieval_as_citation():
    sources = agentic.Sources()
    doc = {"doc_id": "doc-1", "title": "Release", "source": "Issuer", "published_at": "2026-10-01",
           "kind": "filing", "text": "Revenue was $25 million."}
    sources.add(doc)
    assert sources.cited("No reference") == []
    assert sources.cited("Unknown reference [999]") == []
    assert sources.cited("Revenue [1] and unknown [999]") == [{"ref": 1, "ticker": None, **doc}]


async def test_active_run_survives_navigation_and_lists_loading(alice, ben, monkeypatch):
    import asyncio

    release = asyncio.Event()

    async def stream(deployment, message, metadata, **kwargs):
        yield "delta", "Started "
        await release.wait()
        yield "delta", "and finished."

    monkeypatch.setattr(conftest.API_APP.state.services.a2a, "stream", stream)
    thread = await alice.new_thread("pod_alpha")
    response = await alice.post(f"/v1/threads/{thread}/runs", pod="pod_alpha", json={
        "mode": "direct", "agent_id": "equity-research", "message": "Background answer",
    })
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    try:
        # The page can leave without an SSE subscriber; the server owns execution.
        threads = (await alice.get("/v1/pods/pod_alpha/threads")).json()
        assert next(t for t in threads if t["thread_id"] == thread)["active_run_id"] == run_id
        reopened = (await alice.get(f"/v1/threads/{thread}", pod="pod_alpha")).json()
        assert reopened["active_run"]["run_id"] == run_id
        assert len(reopened["messages"]) == 1
        assert (await ben.get(f"/v1/threads/{thread}", pod="pod_beta")).status_code == 404
    finally:
        release.set()
    events = await alice.events(run_id, "pod_alpha")
    assert events[-1]["type"] == "run.completed"
    stored = (await alice.get(f"/v1/threads/{thread}", pod="pod_alpha")).json()
    assert stored["active_run"] is None
    assert len(stored["messages"]) == 2
    assert stored["messages"][1]["content"]["text"] == "Started and finished."
    threads = (await alice.get("/v1/pods/pod_alpha/threads")).json()
    assert next(t for t in threads if t["thread_id"] == thread)["active_run_id"] is None


async def test_source_registration_does_not_suppress_model_fallback():
    from types import SimpleNamespace

    registry = SimpleNamespace(custom_metadata={"podos_event": True}, content=SimpleNamespace(parts=[SimpleNamespace(text='{"type":"sources.available"}')]))

    async def unavailable():
        yield registry
        raise agentic.llm.LLMError("unavailable")

    async def fallback():
        yield "Fallback answer"

    output = [event async for event in agentic.llm_or_fallback(None, None, unavailable(), fallback())]
    assert output == [registry, "Fallback answer"]
