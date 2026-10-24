"""Catalyst Monitor (background) and Auto router agents."""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event

from . import llm
from .common import PlatformClient, PodosAgent, ToolDenied, sentences, terms


class CatalystMonitorAgent(PodosAgent):
    """Finds new filings for the monitor's universe since the last checkpoint.

    Emits ``monitor_alert`` events for each match, and an ``approval_requested``
    event (with a typed DocumentSpec) for high-severity catalysts that warrant a
    PM-approved pod brief. The platform decides who is notified and when the
    approved workflow resumes; the agent never writes to the inbox directly.
    """

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        meta = self.podos_meta(ctx)
        mon = meta.get("monitor", {})
        client = PlatformClient("catalyst-monitor", meta)
        last_seen = (meta.get("checkpoint") or {}).get("last_seen")
        tickers = mon.get("tickers", [])
        keywords = [k.lower() for k in mon.get("keywords", [])]
        yield self.event(ctx, {"type": "tool.started", "tool": "research.search", "label": "Scanning new filings"})
        try:
            docs = await client.search(" ".join(tickers + keywords) or "filing", limit=20, kinds=["filing"],
                                       tickers=tickers or None, published_after=last_seen)
        except ToolDenied as d:
            yield self.event(ctx, {"type": "monitor.result", "error": d.code, "new_documents": 0, "alerts": 0, "approvals": 0})
            return
        if not docs:
            # Ranked search can miss docs with no keyword hit; fall back to a ticker-only scan.
            docs = await client.search(" ".join(tickers) or "filing", limit=20, kinds=["filing"],
                                       tickers=tickers or None, published_after=last_seen)
        yield self.event(ctx, {"type": "tool.completed", "tool": "research.search", "status": "ok", "count": len(docs)})

        alerts = approvals = 0
        newest = last_seen
        for d in sorted(docs, key=lambda x: x["published_at"]):
            ticker = d.get("ticker") or ""
            label = d["title"] if d["title"].startswith(ticker) else f"{ticker} {d['title']}".strip()
            newest = max(newest or d["published_at"], d["published_at"])
            body_l = d["body"].lower()
            matched = [k for k in keywords if k in body_l]
            severity = "high" if matched else "normal"
            evidence = [{"doc_id": d["doc_id"], "title": d["title"], "published_at": d["published_at"]}]
            key_sents = [s for s in sentences(d["body"]) if any(k in s.lower() for k in matched)] or sentences(d["body"])[:2]
            await client.emit_event(self._event(meta, "monitor_alert", f"alert:{d['doc_id']}", {
                "title": f"New filing: {label}",
                "summary": " ".join(key_sents[:2])[:600],
                "priority": "high" if severity == "high" else "normal",
                "ticker": d.get("ticker"), "matched": matched, "evidence": evidence,
                "dedup_key": f"alert:{mon.get('monitor_id')}:{d['doc_id']}",
            }, meta.get("recipients", [])))
            alerts += 1
            if severity == "high" and meta.get("approval_recipient"):
                drafted = await self._draft_brief(d, matched, mon) if llm.enabled() else None
                spec = {
                    "title": f"{d.get('ticker') or 'Catalyst'}: catalyst brief",
                    "subtitle": f"{d['title']} · flagged by {mon.get('name', 'monitor')}",
                    "slides": [
                        {"title": "What happened", "bullets": key_sents[:3]},
                        {"title": "Why it matters for the pod", "bullets": [
                            f"Matched watch terms: {', '.join(matched)}",
                            f"Ticker {d.get('ticker')} is in the monitored universe",
                        ]},
                        {"title": "Evidence", "bullets": [f"{d['title']} ({d['published_at'][:10]}) [1]"]},
                        {"title": "Proposed actions", "bullets": [
                            "Review position sizing against updated guidance",
                            "Refresh model assumptions",
                            "Schedule follow-up with management / IR",
                        ]},
                        {"title": "Open questions", "bullets": ["What changed versus the prior filing?",
                                                                "Is the market already pricing this?"]},
                    ],
                    "citations": [{"ref": 1, "doc_id": d["doc_id"], "title": d["title"], "source": d["source"],
                                   "published_at": d["published_at"]}],
                }
                brief_summary = "The monitor drafted a 5-slide catalyst brief. Approve to generate and file it in the vault."
                if drafted:
                    spec["slides"] = drafted["slides"]
                    brief_summary = f"{drafted['summary']} Approve to generate the 5-slide brief and file it in the vault."
                await client.emit_event(self._event(meta, "approval_requested", f"approval:{d['doc_id']}", {
                    "title": f"Approve catalyst brief: {label}",
                    "summary": brief_summary,
                    "action": "publish_pod_brief", "target": {"kind": "research_document", "id": d["doc_id"]},
                    "proposed_changes": ["Generate pod-private PowerPoint brief", "Notify pod members"],
                    "evidence": evidence, "document_spec": spec, "output_capability": "document.export.pptx",
                    "expires_in_hours": 72, "dedup_key": f"approval:{mon.get('monitor_id')}:{d['doc_id']}",
                }, [meta["approval_recipient"]]))
                approvals += 1

        summary = f"Scanned filings for {', '.join(tickers)}: {len(docs)} new, {alerts} alerts, {approvals} approvals requested."
        async for e in self.stream_text(ctx, summary):
            yield e
        yield self.event(ctx, {"type": "monitor.result", "new_documents": len(docs), "alerts": alerts,
                               "approvals": approvals, "checkpoint": {"last_seen": newest} if newest else None})

    @staticmethod
    async def _draft_brief(doc: dict[str, Any], matched: list[str], mon: dict[str, Any]) -> dict[str, Any] | None:
        out = await llm.complete_json([
            {"role": "system", "content": (
                "You draft a 5-slide catalyst brief for a portfolio manager from ONE filing. Use only facts in the filing. "
                'Reply with JSON only: {"summary": "<2 sentences>", "slides": [{"title": "...", "bullets": ["...", "..."]}]} '
                "with exactly 5 slides: What happened, Why it matters, Evidence, Proposed actions, Open questions.")},
            {"role": "user", "content": f"Watch terms matched: {', '.join(matched)}\nFiling: {doc['title']} ({doc['published_at'][:10]})\n\n{doc['body'][:3000]}"},
        ], max_tokens=1200)
        if not out or not isinstance(out.get("slides"), list) or not out.get("summary"):
            return None
        slides = [{"title": str(x.get("title", ""))[:200], "bullets": [str(b)[:300] for b in x.get("bullets", [])][:6]}
                  for x in out["slides"] if isinstance(x, dict)][:6]
        return {"summary": str(out["summary"])[:600], "slides": slides} if slides else None

    @staticmethod
    def _event(meta: dict[str, Any], etype: str, suffix: str, payload: dict[str, Any], recipients: list[str]) -> dict:
        return {
            "schema_version": "1.0",
            # Deterministic id per (run, subject): a retried run re-sends the same id -> deduplicated at ingress.
            "event_id": f"{meta['run_id']}:{suffix}",
            "pod_id": meta.get("pod_id", ""),
            "agent_id": "catalyst-monitor",
            "run_id": meta["run_id"],
            "type": etype,
            "classification": "pod_confidential",
            "recipient_user_ids": recipients,
            "subject_resource": {"kind": "research_document", "id": suffix.split(":", 1)[1]},
            "created_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }


class AutoRouterAgent(PodosAgent):
    """Picks one agent from the *pre-filtered* candidate list supplied by the platform.

    Explicit agent names and conversation follow-ups are routing hints, not authority.
    The Workspace API discards every proposal outside the current eligible set.
    """

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        route = self.podos_meta(ctx).get("route", {})
        candidates: list[dict[str, Any]] = route.get("candidates", [])
        question = self.user_text(ctx)
        q = question.lower()
        explicit = re.search(r"\b(?:use|ask|check|try|switch to|route to)\s+(?:the\s+)?([a-z][a-z0-9.-]{2,63})", q)
        if explicit and not re.search(r"\b(?:don't|do not|never)\s+(?:use|ask|check|try|switch to|route to)\b", q):
            named = next((c["agent_id"] for c in candidates
                          if c["agent_id"] == explicit.group(1)
                          or c.get("name", "").lower().startswith(explicit.group(1) + " ")), None)
            # Preserve an explicit logical ID for the API's eligibility check. Ordinary words
            # such as 'check the latest filing' must not be mistaken for an agent name.
            named = named or (explicit.group(1) if "-" in explicit.group(1) else None)
            if named:
                yield self.event(ctx, {"type": "route.selected", "agent_id": named,
                                       "rationale": "user explicitly requested this agent"})
                return
        if not candidates:
            yield self.event(ctx, {"type": "route.selected", "agent_id": None, "rationale": "no candidates"})
            return
        if llm.enabled():
            listing = "\n".join(f"- {c['agent_id']}: {c.get('name')}: {c.get('description')}" for c in candidates)
            history = route.get("history", [])[-6:]
            pick = await llm.complete_json([
                {"role": "system", "content": (
                    "You route a user's request to exactly one agent from the list provided. "
                    "Only these agents exist for this user; never name any other. "
                    "Use recent conversation to resolve follow-ups. Prefer the agent explicitly named in the latest request. "
                    "Sports court availability belongs to a court-finder agent when one is listed, not equity research. "
                    'Reply with JSON only: {"agent_id": "...", "rationale": "<12 words max>"}')},
                {"role": "user", "content": f"Agents:\n{listing}\n\nRecent conversation: {history}\n\nLatest request: {question}"},
            ])
            if pick and isinstance(pick.get("agent_id"), str):
                yield self.event(ctx, {"type": "route.selected", "agent_id": pick["agent_id"],
                                       "rationale": str(pick.get("rationale", ""))[:200]})
                return
        if re.match(r"^(?:yes[, .]+(?:please|widen|narrow|make|change|try|show|check)|(?:widen|narrow) (?:it|that))\b", q):
            previous = next((h.get("agent_id") for h in reversed(route.get("history", []))
                             if h.get("role") == "assistant"), None)
            if previous in {c["agent_id"] for c in candidates}:
                yield self.event(ctx, {"type": "route.selected", "agent_id": previous,
                                       "rationale": "continuing the previous eligible agent's task"})
                return
        qt = terms(question)
        filing_words = {"filing", "filings", "10-q", "10-k", "8-k", "risk factor", "risk factors", "disclosure"}
        best, best_score = None, -1.0
        for c in candidates:
            desc = f"{c.get('name', '')} {c.get('description', '')} {' '.join(c.get('capabilities', []))}".lower()
            score = len(qt & terms(desc)) * 1.0
            if any(w in q for w in filing_words) and "filing" in desc:
                score += 3
            if re.search(r"slides?|deck|powerpoint|memo", q) and "document.export" in desc:
                score += 2
            if c["agent_id"] == "equity-research":
                score += 0.5  # sensible default for open-ended research questions
            if score > best_score:
                best, best_score = c, score
        assert best is not None
        yield self.event(ctx, {"type": "route.selected", "agent_id": best["agent_id"],
                               "rationale": f"best capability match among {len(candidates)} eligible agents (score {best_score:.1f})"})
