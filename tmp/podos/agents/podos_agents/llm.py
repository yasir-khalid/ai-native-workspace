"""Model access for PodOS agents via OpenRouter (OpenAI-compatible API).

The model reasons and chooses tools; it never holds authority. Every tool call
it makes is executed through the Workspace API with the run's delegation, so
scope, entitlements, releases and kill switches are enforced server-side no
matter what the model asks for.

Config (agent process environment):
    OPENROUTER_API_KEY     enables LLM mode
    PODOS_LLM_MODE         "auto" (default: LLM when a key is present) | "off" (deterministic)
    PODOS_LLM_MODEL        research model     (default anthropic/claude-sonnet-5.5)
    PODOS_LLM_FAST_MODEL   routing/summaries  (default anthropic/claude-haiku-5.5)

Requests ask OpenRouter to route only to providers that do not retain/train on
prompts (``provider.data_collection = "deny"``). Production use with real MNPI
still requires a firm-approved model endpoint (ARCHITECTURE.md 7.3).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger("podos.llm")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(Exception):
    pass


def enabled() -> bool:
    return os.environ.get("PODOS_LLM_MODE", "auto") != "off" and bool(os.environ.get("OPENROUTER_API_KEY"))


def model(fast: bool = False) -> str:
    if fast:
        return os.environ.get("PODOS_LLM_FAST_MODEL", "anthropic/claude-haiku-5.5")
    return os.environ.get("PODOS_LLM_MODEL", "anthropic/claude-sonnet-5.5")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "HTTP-Referer": "http://localhost:3400",
        "X-Title": "PodOS",
    }


def _body(messages: list[dict], *, fast: bool, tools: list[dict] | None, max_tokens: int, stream: bool) -> dict:
    body: dict[str, Any] = {
        "model": model(fast),
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
        "temperature": 0.2,
        "provider": {"data_collection": "deny"},
    }
    if tools:
        body["tools"] = tools
    return body


async def stream_chat(
    messages: list[dict], *, tools: list[dict] | None = None, fast: bool = False, max_tokens: int = 1800,
) -> AsyncIterator[tuple[str, Any]]:
    """Yields ("text", str) deltas, then one ("tool_calls", [...]) if the model called tools, then ("done", reason)."""
    calls: dict[int, dict[str, Any]] = {}
    finish = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as hc:
            async with hc.stream("POST", OPENROUTER_URL, headers=_headers(),
                                 json=_body(messages, fast=fast, tools=tools, max_tokens=max_tokens, stream=True)) as r:
                if r.status_code >= 400:
                    detail = (await r.aread())[:300].decode(errors="replace")
                    raise LLMError(f"openrouter {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    data = json.loads(line[6:])
                    if "error" in data:
                        raise LLMError(str(data["error"])[:300])
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield "text", delta["content"]
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                    finish = choice.get("finish_reason") or finish
    except httpx.HTTPError as exc:
        raise LLMError(type(exc).__name__) from exc
    if calls:
        out = []
        for i in sorted(calls):
            c = calls[i]
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {}
            out.append({"id": c["id"] or f"call_{i}", "name": c["name"], "args": args, "raw": c["arguments"] or "{}"})
        yield "tool_calls", out
    yield "done", finish


async def complete_json(messages: list[dict], *, fast: bool = True, max_tokens: int = 800) -> dict | None:
    """Single non-streamed completion expected to return a JSON object. None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as hc:
            r = await hc.post(OPENROUTER_URL, headers=_headers(),
                              json=_body(messages, fast=fast, tools=None, max_tokens=max_tokens, stream=False))
        if r.status_code >= 400:
            log.warning("openrouter %s", r.status_code)
            return None
        text = r.json()["choices"][0]["message"]["content"] or ""
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None
