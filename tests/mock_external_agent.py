"""A standard (non-PodOS) A2A agent for tests, built with the official a2a-sdk.

Its stream mirrors the Institutional Entity Intelligence agent: progress as `working` status messages, then one
`answer` artifact with a markdown text part and a data part carrying citations, evidence and a verification
verdict. It records every request it receives so tests can assert what PodOS sent (only the question).
"""

from __future__ import annotations

from typing import Any

from a2a.helpers.proto_helpers import new_task, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part, Role, TaskState
from google.protobuf import json_format, struct_pb2
from starlette.applications import Starlette

RECEIVED: list[dict[str, Any]] = []


class EntityExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        RECEIVED.append({
            "text": "".join(p.text for p in msg.parts if p.HasField("text")),
            "metadata": json_format.MessageToDict(msg.metadata) if msg.HasField("metadata") else {},
            "request_metadata": dict(context.metadata or {}),
            "context_id": context.context_id,
        })
        if context.current_task is None:
            await event_queue.enqueue_event(new_task(context.task_id, context.context_id, TaskState.TASK_STATE_SUBMITTED, history=[msg]))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        for step in ("Planning the research…", "Searching name for “BlackRock Fund Advisors”", "Verifying the answer against the records…"):
            await updater.start_work(new_text_message(step, context_id=context.context_id, task_id=context.task_id, role=Role.ROLE_AGENT))
        data = struct_pb2.Struct()
        data.update({
            "citations": [{"evidence_id": "ev_1", "marker": 1}],
            "evidence": {"ev_1": {"evidence_id": "ev_1", "fact_type": "lookup", "source": "GLEIF relationship record",
                                  "criteria": ["Entity ID = 549300YOOGP0Y1M95C20"], "source_timestamp": "2026-09-11",
                                  "source_uri": "https://search.gleif.org/#/record/549300YOOGP0Y1M95C20"}},
            "verification": {"status": "verified", "detail": "All checks passed."},
        })
        await updater.add_artifact(
            [Part(text="The direct parent of BlackRock Fund Advisors is **BlackRock, Inc.** [1].", media_type="text/markdown"),
             Part(data=struct_pb2.Value(struct_value=data))],
            name="answer",
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:  # pragma: no cover
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def build_app(port: int) -> Starlette:
    card = AgentCard(
        name="Mock Entity Intelligence",
        description="Answers questions about legal entities from public records (test double).",
        version="0.1.0",
        supported_interfaces=[AgentInterface(url=f"http://127.0.0.1:{port}/a2a", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/markdown", "application/json"],
        skills=[AgentSkill(id="entity_research", name="Entity research", description="Resolve institutions and their parents.",
                           tags=["LEI"], examples=["Who is the parent of BlackRock Fund Advisors?"])],
    )
    handler = DefaultRequestHandler(agent_executor=EntityExecutor(), task_store=InMemoryTaskStore(), agent_card=card)
    app = Starlette()
    app.routes.extend([*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/a2a")])
    return app
