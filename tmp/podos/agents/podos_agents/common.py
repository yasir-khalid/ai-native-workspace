"""Shared plumbing for PodOS ADK agents.

Agents are deterministic ADK ``BaseAgent`` subclasses (no LLM credentials needed
for CI). They receive a platform-issued delegation via A2A request metadata and
call back into the Workspace API with their *own* workload identity. They hold
no pod data, no universal credentials and no cross-pod state.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import jwt
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.genai import types

ROOT = Path(__file__).resolve().parents[2]
STREAM_DELAY = float(os.environ.get("PODOS_AGENT_STREAM_DELAY", "0.012"))


class ToolDenied(Exception):
    def __init__(self, status: int, code: str, reason: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.reason = reason


def workload_token(agent_id: str) -> str:
    path = Path(os.environ.get("PODOS_WORKLOAD_SECRETS", ROOT / "config" / "workload_secrets.dev.json"))
    secret = json.loads(path.read_text())[agent_id]
    import time
    import uuid

    now = int(time.time())
    return jwt.encode({"iss": agent_id, "sub": agent_id, "aud": "podos-internal", "iat": now, "exp": now + 120,
                       "jti": uuid.uuid4().hex}, secret, "HS256")


class PlatformClient:
    """Calls the Workspace API's internal tool/event endpoints."""

    def __init__(self, agent_id: str, meta: dict[str, Any]) -> None:
        self.agent_id = agent_id
        self.api = meta["api_base"].rstrip("/")
        self.delegation = meta.get("delegation_token", "")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Workload {workload_token(self.agent_id)}", "X-PodOS-Delegation": self.delegation}

    async def _post(self, path: str, body: dict[str, Any], *, delegated: bool = True) -> dict[str, Any]:
        headers = self._headers() if delegated else {"Authorization": f"Workload {workload_token(self.agent_id)}"}
        async with httpx.AsyncClient(timeout=20) as hc:
            r = await hc.post(f"{self.api}{path}", json=body, headers=headers)
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = {}
            raise ToolDenied(r.status_code, data.get("code", "error"), data.get("reason"))
        return r.json()

    async def search(self, query: str, **kw: Any) -> list[dict[str, Any]]:
        body = {"query": query, **{k: v for k, v in kw.items() if v is not None}}
        return (await self._post("/v1/internal/tools/research.search", body))["results"]

    async def invoke_capability(self, capability_id: str, spec: dict[str, Any], idempotency_key: str, **kw: Any) -> dict:
        body = {"spec": spec, "idempotency_key": idempotency_key, **{k: v for k, v in kw.items() if v is not None}}
        return await self._post(f"/v1/internal/tools/capabilities/{capability_id}/invoke", body)

    async def emit_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1/internal/agent-events", event, delegated=False)


class PodosAgent(BaseAgent):
    """Base class with helpers for streaming text and structured PodOS events."""

    def podos_meta(self, ctx: InvocationContext) -> dict[str, Any]:
        md = (ctx.run_config.custom_metadata or {}) if ctx.run_config else {}
        return dict((md.get("a2a_metadata") or {}).get("podos") or {})

    @staticmethod
    def user_text(ctx: InvocationContext) -> str:
        if not ctx.user_content or not ctx.user_content.parts:
            return ""
        return "".join(p.text or "" for p in ctx.user_content.parts)

    def text(self, ctx: InvocationContext, text: str) -> Event:
        return Event(author=self.name, invocation_id=ctx.invocation_id, partial=True,
                     content=types.Content(role="model", parts=[types.Part(text=text)]))

    def event(self, ctx: InvocationContext, payload: dict[str, Any]) -> Event:
        return Event(author=self.name, invocation_id=ctx.invocation_id, partial=True,
                     content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload))]),
                     custom_metadata={"podos_event": True})

    async def stream_text(self, ctx: InvocationContext, text: str) -> AsyncGenerator[Event, None]:
        for chunk in re.findall(r"\S+\s*|\s+", text):
            yield self.text(ctx, chunk)
            if STREAM_DELAY:
                await asyncio.sleep(STREAM_DELAY)


# ---------------------------------------------------------------------------
# Small deterministic text utilities used by the research agents.
# ---------------------------------------------------------------------------
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_RE.split(text) if len(s.strip()) > 45]


def terms(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "what", "about", "this", "that", "our", "are", "how", "does", "please", "give",
            "tell", "show", "from", "into", "latest", "make", "create", "prepare", "summary", "summarise", "summarize"}
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text)} - stop


def tickers_in(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b[A-Z]{2,5}\b", text)) - {"PM", "AI", "NAV", "EPS", "FDA", "SEC", "CFO", "CEO", "USD", "PPT", "ESG"})


def top_sentences(body: str, query_terms: set[str], n: int = 2) -> list[str]:
    scored = []
    for i, s in enumerate(sentences(body)):
        words = terms(s)
        score = len(words & query_terms) * 2 + (1 if re.search(r"\d", s) else 0) - i * 0.05
        scored.append((score, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, _, s in scored[:n]]
