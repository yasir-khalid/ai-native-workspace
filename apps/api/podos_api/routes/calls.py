"""Earnings-call and interview library: transcripts, chapters, Q&A, search and grounded answers."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from podos_contracts.models import PodContext
from pydantic import BaseModel, Field

from ..db import fetchall, fetchone
from ..errors import Problem, not_found
from ..security.context import pod_ctx_from_path, services
from ..settings import ROOT
from .threads import _jsonable

router = APIRouter(prefix="/v1")

CALLS_ROOT = ROOT / "var" / "calls"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TOKEN_RE = re.compile(r"[a-z0-9]+")
COUNT_RE = re.compile(r"how many times|\bcount\b|\bmentions?\b")
CITATION_RE = re.compile(r"\[([^\]]+)\]")

STOPWORDS = {
    "about", "after", "again", "against", "also", "among", "and", "any", "are", "because", "been", "before",
    "being", "between", "both", "but", "call", "can", "comment", "commented", "company", "could", "count",
    "did", "discuss", "discussed", "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "get", "going", "got", "had", "has", "have", "having", "here", "how", "into", "its", "itself", "just",
    "kind", "know", "like", "look", "made", "make", "management", "many", "maybe", "mention", "mentioned",
    "mentions", "more", "most", "much", "must", "need", "nor", "not", "now", "off", "once", "one", "only",
    "other", "our", "out", "over", "own", "question", "really", "said", "same", "say", "says", "see", "she",
    "should", "some", "sort", "such", "talk", "talked", "tell", "than", "that", "the", "their", "theirs",
    "them", "then", "there", "these", "they", "think", "this", "those", "three", "through", "times", "too",
    "two", "under", "until", "use", "used", "using", "very", "want", "was", "way", "well", "were", "what",
    "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours",
}

SYSTEM_PROMPT = (
    "You are PodOS's earnings-call analyst. Answer the analyst's question using ONLY the supplied transcript "
    "excerpts. Cite every factual sentence with its bracketed sentence id, for example [s123]; you may add the "
    "timestamp. Never invent numbers, figures, dates, quotes or sources. If the excerpts do not cover something, "
    "say it was not discussed in this call. Use the chapter and Q&A indexes only for orientation; the transcript "
    "excerpts are the evidence. If a deterministic count is supplied, treat it as authoritative. Keep a concise, "
    "professional analyst tone and answer in at most 180 words."
)


class AskTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[AskTurn] = Field(default_factory=list, max_length=8)


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _question_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(t for t in _tokens(text) if len(t) >= 3 and t not in STOPWORDS))


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def _mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _flatten_sentences(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for paragraph in transcript.get("paragraphs") or []:
        for sentence in paragraph.get("sentences") or []:
            out.append({
                "sentence_id": sentence.get("id"),
                "paragraph_id": sentence.get("paragraph_id") or paragraph.get("id"),
                "speaker": sentence.get("speaker") or paragraph.get("speaker"),
                "start": float(sentence.get("start") or 0.0),
                "end": float(sentence.get("end") or sentence.get("start") or 0.0),
                "text": sentence.get("text") or "",
            })
    return out


def _score_sentences(sentences: list[dict[str, Any]], question: str) -> list[tuple[float, int]]:
    tokens = _question_tokens(question)
    if not tokens or not sentences:
        return []
    token_sets = [set(_tokens(s["text"])) for s in sentences]
    df = {t: sum(1 for toks in token_sets if t in toks) for t in tokens}
    total = len(sentences)
    weights = {t: math.log((total + 1) / (df[t] + 1)) + 1.0 for t in tokens}
    phrase = _normalise(question)
    scored: list[tuple[float, int]] = []
    for idx, sentence in enumerate(sentences):
        score = sum(weight for token, weight in weights.items() if token in token_sets[idx])
        if phrase and phrase in _normalise(sentence["text"]):
            score += 5.0
        if score > 0:
            scored.append((score, idx))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored


def _build_ask_context(
    transcript: dict[str, Any], chapters: list[dict[str, Any]], qa: list[dict[str, Any]], question: str,
) -> tuple[str, dict[str, dict[str, Any]]]:
    sentences = _flatten_sentences(transcript)
    sentence_map = {s["sentence_id"]: s for s in sentences if s["sentence_id"]}
    token_sets = [set(_tokens(s["text"])) for s in sentences]
    question_tokens = _question_tokens(question)
    ranked = _score_sentences(sentences, question)
    selected = {idx for _, idx in ranked[:10]}
    if len(selected) < 4:
        # Broad questions ("biggest takeaways", "what matters here") often have no lexical overlap
        # with the transcript. Fall back to the editor's insights and chapter openings so the model
        # still receives citable evidence instead of only the chapter index.
        by_id = {s["sentence_id"]: idx for idx, s in enumerate(sentences) if s["sentence_id"]}
        for insight in transcript.get("insights") or []:
            for sentence_id in insight.get("sentence_ids") or []:
                idx = by_id.get(sentence_id)
                if idx is not None:
                    selected.add(idx)
        for chapter in chapters or []:
            idx = by_id.get(chapter.get("start_sentence"))
            if idx is not None:
                selected.add(idx)
        if not selected:
            selected.update(range(min(12, len(sentences))))
    for idx in list(selected):
        if idx + 1 < len(sentences):
            selected.add(idx + 1)
        elif idx > 0:
            selected.add(idx - 1)

    header = (
        f"Call: {transcript.get('company')} ({transcript.get('ticker')}) — "
        f"{transcript.get('event_title')} ({transcript.get('event_date')})"
    )
    parts = [header]
    chapter_lines = [f"- {_mmss(float(c.get('start') or 0))} {c.get('title', '')}" for c in chapters or []]
    if chapter_lines:
        parts.append("Chapter index:\n" + "\n".join(chapter_lines))

    def qa_rank(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        idx, entry = item
        words = set(_tokens(f"{entry.get('question', '')} {entry.get('answer', '')}"))
        return (-sum(1 for t in question_tokens if t in words), idx)

    qa_headers = [
        f"- {_mmss(float(entry.get('start') or 0))} {str(entry.get('question', '')).strip()}"
        for _, entry in sorted(enumerate(qa or []), key=qa_rank)[:8]
    ]
    if qa_headers:
        parts.append("Most relevant Q&A topics:\n" + "\n".join(qa_headers))

    if COUNT_RE.search(question.lower()) and question_tokens:
        term = max(question_tokens, key=lambda t: (len(t), -question_tokens.index(t)))
        hits = sum(1 for toks in token_sets if term in toks)
        parts.append(f"Deterministic count: '{term}' appears in {hits} sentences.")

    base = "\n\n".join(parts)
    intro = "Transcript excerpts (cite ids exactly as shown):"
    budget = 7000 - len(base) - len(intro) - 2
    lines: list[str] = []
    for idx in sorted(selected):
        sentence = sentences[idx]
        line = (
            f"[{sentence['sentence_id']}] {_mmss(sentence['start'])} "
            f"{sentence['speaker']}: {sentence['text']}"
        )
        if len(line) > budget:
            if budget > 40:
                lines.append(line[: budget - 1] + "…")
            break
        lines.append(line)
        budget -= len(line) + 1
    if lines:
        base = f"{base}\n\n{intro}\n" + "\n".join(lines)
    return base[:7000], sentence_map


def _build_chat_context(
    transcript: dict[str, Any], chapters: list[dict[str, Any]], qa: list[dict[str, Any]], question: str,
) -> str:
    """Attach a complete ordinary-length call; use retrieved excerpts for unusually long calls."""
    header = (f"Call: {transcript.get('company')} ({transcript.get('ticker')}) — "
              f"{transcript.get('event_title')} ({transcript.get('event_date')})")
    lines = [f"[{s['sentence_id']}] {_mmss(s['start'])} {s['speaker']}: {s['text']}"
             for s in _flatten_sentences(transcript)]
    full = header + "\n\nComplete transcript:\n" + "\n".join(lines)
    if len(full) <= 100_000:
        return full
    excerpts, _ = _build_ask_context(transcript, chapters, qa, question)
    return excerpts


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _resolve_audio(audio_path: str) -> Path:
    root = CALLS_ROOT.resolve()
    candidate = (root / audio_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise not_found("audio")
    return candidate


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None | str:
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value:
        return "invalid"
    spec = value.removeprefix("bytes=").strip()
    if "-" not in spec:
        return "invalid"
    start_text, end_text = spec.split("-", 1)
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                return "invalid"
            start = max(size - suffix, 0)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
    except ValueError:
        return "invalid"
    if start >= size or start > end:
        return "invalid"
    return start, min(end, size - 1)


def _iter_file(path: Path, start: int, length: int):
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _audio_response(request: Request, path: Path, size: int, *, include_body: bool):
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{path.name}"',
        "Cache-Control": "private, max-age=3600",
    }
    parsed = _parse_range(request.headers.get("range"), size)
    if parsed == "invalid":
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Content-Length": "0",
                                                  "Accept-Ranges": "bytes"})
    if parsed is None:
        start, length, status = 0, size, 200
        headers["Content-Length"] = str(size)
    else:
        start, end = parsed
        length = end - start + 1
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(length)
    if not include_body:
        return Response(status_code=status, headers=headers, media_type="audio/mpeg")
    return StreamingResponse(_iter_file(path, start, length), status_code=status, headers=headers,
                             media_type="audio/mpeg")


async def _call_audio_path(svc, ctx: PodContext, call_id: str) -> tuple[Path, int]:
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn, "SELECT audio_path FROM call_events WHERE pod_id = %s AND call_id = %s",
            (ctx.active_pod_id, call_id),
        )
    if row is None:
        raise not_found("call")
    path = _resolve_audio(row["audio_path"])
    return path, path.stat().st_size


@router.get("/pods/{pod_id}/calls")
async def list_calls(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        rows = await fetchall(
            conn,
            """SELECT call_id, company, ticker, exchange, event_title, event_date, duration, tags, summary_short, asr,
                      jsonb_array_length(chapters) AS chapter_count, jsonb_array_length(qa) AS qa_count
               FROM call_events WHERE pod_id = %s ORDER BY event_date DESC NULLS LAST, event_title""",
            (ctx.active_pod_id,),
        )
    out = []
    for row in rows:
        item = _jsonable(row)
        item["audio_url"] = f"/v1/pods/{ctx.active_pod_id}/calls/{row['call_id']}/audio"
        out.append(item)
    return out


@router.get("/pods/{pod_id}/calls/{call_id}")
async def get_call(call_id: str, ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn, "SELECT * FROM call_events WHERE pod_id = %s AND call_id = %s", (ctx.active_pod_id, call_id),
        )
    if row is None:
        raise not_found("call")
    item = _jsonable(row)
    item["audio_url"] = f"/v1/pods/{ctx.active_pod_id}/calls/{call_id}/audio"
    return item


@router.head("/pods/{pod_id}/calls/{call_id}/audio")
async def head_call_audio(call_id: str, request: Request, ctx: PodContext = Depends(pod_ctx_from_path),
                          svc=Depends(services)):
    path, size = await _call_audio_path(svc, ctx, call_id)
    return _audio_response(request, path, size, include_body=False)


@router.get("/pods/{pod_id}/calls/{call_id}/audio")
async def get_call_audio(call_id: str, request: Request, ctx: PodContext = Depends(pod_ctx_from_path),
                         svc=Depends(services)):
    path, size = await _call_audio_path(svc, ctx, call_id)
    return _audio_response(request, path, size, include_body=True)


@router.get("/pods/{pod_id}/calls/{call_id}/search")
async def search_call(call_id: str, q: str = "", ctx: PodContext = Depends(pod_ctx_from_path),
                      svc=Depends(services)):
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn, "SELECT transcript FROM call_events WHERE pod_id = %s AND call_id = %s",
            (ctx.active_pod_id, call_id),
        )
    if row is None:
        raise not_found("call")
    tokens = _question_tokens(q)
    if not tokens:
        return []
    phrase = _normalise(q)
    sentences = _flatten_sentences(row["transcript"])
    hits = []
    for sentence in sentences:
        words = set(_tokens(sentence["text"]))
        overlap = sum(1 for token in tokens if token in words)
        if not overlap:
            continue
        score = float(overlap)
        if phrase and phrase in _normalise(sentence["text"]):
            score += 5.0
        hits.append({
            "sentence_id": sentence["sentence_id"],
            "paragraph_id": sentence["paragraph_id"],
            "speaker": sentence["speaker"],
            "start": sentence["start"],
            "text": sentence["text"],
            "score": score,
        })
    hits.sort(key=lambda hit: (-hit["score"], hit["start"]))
    return hits[:20]


@router.post("/pods/{pod_id}/calls/{call_id}/ask")
async def ask_call(call_id: str, body: AskRequest, request: Request, ctx: PodContext = Depends(pod_ctx_from_path),
                   svc=Depends(services)):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise Problem(503, "llm_unavailable", "call analysis is not configured")
    async with svc.db.pod_tx(ctx.active_pod_id) as conn:
        row = await fetchone(
            conn,
            """SELECT transcript, chapters, qa FROM call_events WHERE pod_id = %s AND call_id = %s""",
            (ctx.active_pod_id, call_id),
        )
    if row is None:
        raise not_found("call")

    context, sentence_map = _build_ask_context(row["transcript"], row["chapters"], row["qa"], body.question)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": turn.role, "content": turn.content} for turn in body.history)
    messages.append({"role": "user", "content": f"{context}\n\nQuestion: {body.question}"})
    payload = {
        "model": os.environ.get("PODOS_LLM_MODEL", "anthropic/claude-sonnet-5.5"),
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def gen():
        chunks: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", OPENROUTER_URL, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        yield _sse("error", {"message": f"model request failed ({response.status_code})"})
                        return
                    async for line in response.aiter_lines():
                        if await request.is_disconnected():
                            return
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            yield _sse("error", {"message": "model stream error"})
                            return
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        text = (choices[0].get("delta") or {}).get("content")
                        if text:
                            chunks.append(text)
                            yield _sse("token", {"text": text})
        except asyncio.CancelledError:
            return
        except httpx.HTTPError:
            yield _sse("error", {"message": "model stream unavailable"})
            return
        except Exception:
            yield _sse("error", {"message": "call analysis failed"})
            return

        citations = []
        seen: set[str] = set()
        for match in CITATION_RE.finditer("".join(chunks)):
            for number in re.findall(r"\bs(\d+)\b", match.group(1)):
                sentence_id = f"s{number}"
                if sentence_id in seen:
                    continue
                seen.add(sentence_id)
                sentence = sentence_map.get(sentence_id)
                if sentence is not None:
                    citations.append({
                        "sentence_id": sentence_id,
                        "speaker": sentence["speaker"],
                        "start": sentence["start"],
                        "text": sentence["text"],
                    })
        yield _sse("citations", {"citations": citations})
        yield _sse("done", {})

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
