"""A2A client adapter for ADK agents.

- Resolves logical ``service_ref`` -> endpoint through the service directory.
- Fetches the Agent Card and, when the registry pinned a digest, rejects a card
  that changed (compromised / malicious endpoint). Card fields are never used
  for authorisation.
- Streams A2A ``message/stream`` updates and normalises them into
  ``("delta", text)`` / ``("event", dict)`` / ``("state", str)`` tuples.
- Bounded by a total timeout; transport errors surface as ``AgentCallError``.

Two stream profiles:
- ``podos``: PodOS's own ADK agents (status text = answer deltas, PodOS events in custom metadata).
- ``a2a``: any standard A2A agent (status text = progress, the answer is the final artifact; an optional
  data part with ``citations``/``evidence``/``verification`` is mapped onto PodOS citations).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.helpers.proto_helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf import json_format, struct_pb2

from .governance import DeploymentRecord
from .presentation import court_result
from .settings import Settings

PODOS_EVENT_MARKER = "podos_event"


class AgentCallError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def card_digest(card: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(card, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class A2AAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def endpoint(self, service_ref: str) -> str:
        """Logical refs (agent://...) resolve through the service directory; external agents are registered
        by their base URL (validated at registration)."""
        if service_ref.startswith(("https://", "http://")):
            return service_ref.rstrip("/")
        url = self.settings.endpoints().get(service_ref)
        if not url:
            raise AgentCallError("agent_endpoint_unknown", "no endpoint registered for service")
        return url.rstrip("/")

    def auth_headers(self, secret_ref: str | None) -> dict[str, str]:
        if not secret_ref:
            return {}
        token = os.environ.get(f"PODOS_SECRET_{secret_ref}") or self.settings.workload_secrets().get(f"agent:{secret_ref}")
        if not token:
            raise AgentCallError("agent_credentials_missing", "the agent's credential is not configured")
        return {"Authorization": f"Bearer {token}"}

    async def fetch_card(self, service_ref: str, timeout: float = 3.0, secret_ref: str | None = None) -> tuple[dict[str, Any], str]:
        url = self.endpoint(service_ref)
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=self.auth_headers(secret_ref)) as hc:
                resp = await hc.get(f"{url}/.well-known/agent-card.json")
                resp.raise_for_status()
                card = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AgentCallError("agent_unreachable", "agent card could not be fetched") from exc
        if not isinstance(card, dict) or "name" not in card:
            raise AgentCallError("agent_card_invalid", "agent card is malformed")
        return card, card_digest(card)

    async def stream(
        self, deployment: DeploymentRecord, text: str, metadata: dict[str, Any], *, timeout: float | None = None,
        conversation_key: str | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        timeout = timeout or self.settings.agent_timeout_seconds
        card, digest = await self.fetch_card(deployment.service_ref, secret_ref=deployment.auth_secret_ref)
        if deployment.card_sha256 and deployment.card_sha256 != digest:
            raise AgentCallError("agent_card_mismatch", "agent card does not match the registered digest")

        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

        async def pump() -> None:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=3.0),
                                             headers=self.auth_headers(deployment.auth_secret_ref)) as hc:
                    factory = ClientFactory(ClientConfig(httpx_client=hc, streaming=True))
                    client = await factory.create_from_url(self.endpoint(deployment.service_ref))
                    if deployment.trust == "external":
                        # Only the question leaves PodOS. A stable, opaque context id (derived, not a PodOS id)
                        # lets the agent keep its own follow-up history for this conversation.
                        ctx_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"podos:{deployment.agent_id}:{conversation_key}")) \
                            if conversation_key else None
                        req = SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER, context_id=ctx_id))
                    else:
                        meta = struct_pb2.Struct()
                        meta.update({"podos": metadata})
                        req = SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER), metadata=meta)
                    reader = _GenericStream() if deployment.stream_profile == "a2a" else None
                    async for resp in client.send_message(req):
                        data = json_format.MessageToDict(resp)
                        for item in (reader.feed(data) if reader else _normalise(data)):
                            await queue.put(item)
            except AgentCallError as exc:
                await queue.put(("error", exc))
            except Exception as exc:  # transport/protocol errors are bounded, never retried cross-pod
                await queue.put(("error", AgentCallError("agent_call_failed", type(exc).__name__)))
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        try:
            async with asyncio.timeout(timeout):
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    if item[0] == "error":
                        raise item[1]
                    yield item
        except TimeoutError:
            raise AgentCallError("agent_timeout", "agent did not respond in time") from None
        finally:
            task.cancel()


def _normalise(resp: dict[str, Any]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    upd = resp.get("statusUpdate")
    if upd:
        status = upd.get("status", {})
        msg = status.get("message")
        custom = (upd.get("metadata") or {}).get("adk_custom_metadata") or {}
        if isinstance(custom, str):
            try:
                custom = json.loads(custom)
            except ValueError:
                custom = {}
        if msg:
            text = "".join(p.get("text", "") for p in msg.get("parts", []))
            if custom.get(PODOS_EVENT_MARKER):
                try:
                    out.append(("event", json.loads(text)))
                except ValueError:
                    pass
            elif text:
                out.append(("delta", text))
        state = status.get("state", "")
        if state in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}:
            out.append(("state", state.removeprefix("TASK_STATE_").lower()))
    elif "task" in resp:
        state = resp["task"].get("status", {}).get("state", "")
        if state in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"}:
            out.append(("state", state.removeprefix("TASK_STATE_").lower()))
    return out


# ---------------------------------------------------------------------------------------------------
# Generic A2A agents
# ---------------------------------------------------------------------------------------------------
FACT_LABEL = {"search_match": "Entity match", "lookup": "Record lookup"}
VERDICT_RE = re.compile(r"^(fully |partially |partly )?(verified|not verified|not checked|unverified)\.?$", re.I)


def _citation_title(criteria: list[str], fallback: str) -> str:
    """'Query name = "X"' -> 'Search: "X"', 'Entity ID = L' -> 'LEI L', 'Key = value' -> 'Key: value'."""
    if not criteria:
        return fallback
    key, _, value = criteria[0].partition(" = ")
    value = value.strip()
    if not value:
        return criteria[0]
    if key.lower() == "query name":
        return f"Search: {value}"
    if key.lower() in {"entity id", "lei"}:
        return f"LEI {value.strip(chr(34))}"
    return f"{key}: {value}"


class _GenericStream:
    """Maps a standard A2A stream onto PodOS run events.

    status message text -> a progress line (tool.started / tool.completed "agent.progress")
    artifact text parts  -> the answer (deltas)
    artifact data part   -> citations (from {citations, evidence}) and a verification line
    """

    def __init__(self) -> None:
        self.open_step: str | None = None
        self.answered = False
        self.results_seen: set[str] = set()

    def _close_step(self) -> list[tuple[str, Any]]:
        if self.open_step is None:
            return []
        label, self.open_step = self.open_step, None
        return [("event", {"type": "tool.completed", "tool": "agent.progress", "status": "ok", "summary": label})]

    def feed(self, resp: dict[str, Any]) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        upd = resp.get("statusUpdate")
        art = resp.get("artifactUpdate")
        if upd:
            status = upd.get("status", {})
            msg = status.get("message")
            text = "".join(p.get("text", "") for p in (msg or {}).get("parts", [])).strip()
            state = status.get("state", "")
            if text and state == "TASK_STATE_WORKING" and not VERDICT_RE.match(text):
                out += self._close_step()
                self.open_step = text[:160]
                out.append(("event", {"type": "tool.started", "tool": "agent.progress", "label": self.open_step}))
            if state in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
                         "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}:
                out += self._close_step()
                if text and not self.answered:
                    out.append(("delta", text))
                    self.answered = True
                out.append(("state", state.removeprefix("TASK_STATE_").lower()))
        elif art:
            out += self._close_step()
            for part in art.get("artifact", {}).get("parts", []):
                if "text" in part:
                    out.append(("delta", part["text"]))
                    self.answered = True
                elif "data" in part:
                    if art.get("artifact", {}).get("name") == "court_availability":
                        result = court_result(part["data"])
                        if result and result["id"] not in self.results_seen:
                            self.results_seen.add(result["id"])
                            out.append(("event", {"type": "ui.result", "result": result}))
                    elif isinstance(part["data"], dict):
                        out += _map_data_part(part["data"])
        elif "message" in resp:  # agents that reply with a plain message instead of a task
            text = "".join(p.get("text", "") for p in resp["message"].get("parts", []))
            if text:
                out.append(("delta", text))
                self.answered = True
            out.append(("state", "completed"))
        elif "task" in resp:
            for a in resp["task"].get("artifacts", []) or []:
                out += self.feed({"artifactUpdate": {"artifact": a}})
            out += self.feed({"statusUpdate": {"status": resp["task"].get("status", {})}})
        return out


def _map_data_part(data: dict[str, Any]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    items = []
    for c in data.get("citations") or []:
        if not isinstance(c, dict):
            continue
        ev = evidence.get(c.get("evidence_id"), {}) if isinstance(evidence, dict) else {}
        marker = int(float(c.get("marker", len(items) + 1)))
        criteria = [str(x) for x in (ev.get("criteria") or [])][:3]
        source = str(ev.get("source") or "External source")
        label = FACT_LABEL.get(str(ev.get("fact_type")), source)
        items.append({
            "ref": marker, "doc_id": None, "external": True,
            "title": _citation_title(criteria, label)[:200],
            "source": (source if source != label and len(source) < 60 else label)[:80],
            "published_at": ev.get("source_timestamp"),
            "url": ev.get("source_uri") if str(ev.get("source_uri") or "").startswith("https://") else None,
            "detail": "; ".join(criteria)[:400],
        })
    if items:
        out.append(("event", {"type": "citations", "items": items[:20]}))
    ver = data.get("verification")
    if isinstance(ver, dict) and ver.get("status"):
        status = str(ver["status"])
        label = {"verified": "Checked against the records: verified", "partial": "Checked against the records: partly verified",
                 "not_verified": "Checked against the records: not verified", "unverified": "Checked against the records: not verified"}.get(
            status, "Not checked against the records")
        out.append(("event", {"type": "tool.completed", "tool": "agent.verification",
                              "status": "ok" if status == "verified" else "warning", "summary": label,
                              "detail": str(ver.get("detail") or "")[:300]}))
    return out
