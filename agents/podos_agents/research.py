"""Equity Research Agent (Agent A) and Filing Analyst (Agent B).

With OPENROUTER_API_KEY set, each agent reasons with an LLM through the shared
tool loop (``agentic.run_tool_loop``). Without it (CI, or ``PODOS_LLM_MODE=off``)
the deterministic implementations below produce retrieval-grounded answers
with numbered citations. The deterministic research agent is *deliberately naive*
about prompt injection: if a user asks it to search another pod's index it
passes that through as a tool parameter, so the demo and tests show that the
platform (not the agent) enforces pod scope.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event

from . import genui, llm
from .agentic import llm_or_fallback, run_tool_loop, system_prompt
from .common import PlatformClient, PodosAgent, ToolDenied, sentences, terms, tickers_in, top_sentences

PPTX_RE = re.compile(r"\b(slides?|deck|powerpoint|pptx|presentation)\b", re.I)
DOCX_RE = re.compile(r"\b(memo|word doc(ument)?|docx|write-?up)\b", re.I)
INJECT_RE = re.compile(r"\b(?:research-)?(pod_[a-z0-9]+)\b", re.I)

DENIAL_TEXT = {
    "capability_not_entitled": "your entitlements in this pod don't include it",
    "not_in_delegation": "it wasn't included in the capabilities delegated for this run",
    "agent_service_not_granted": "this agent isn't approved to call it",
    "agent_does_not_support_capability": "this agent doesn't support it",
}


def _citations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"ref": i + 1, "doc_id": r["doc_id"], "title": r["title"], "source": r["source"],
             "published_at": r["published_at"], "kind": r["kind"], "ticker": r.get("ticker")}
            for i, r in enumerate(results)]


EQUITY_ROLE = (
    "You are the Equity Research Agent in PodOS, a research copilot for a portfolio manager and analysts. "
    "You answer questions about companies, theses, catalysts and risks from the pod's private research."
)
FILING_ROLE = (
    "You are the Filing Analyst in PodOS. You review regulatory filings (10-Q, 10-K, 8-K) in the pod's vault and "
    "report what changed: guidance, risk-factor language, governance and anything a PM must know. "
    "Lead with a compact markdown table (Filing | Date | Key change | Signal) and then a short list of risk-factor or governance deltas."
)
ESG_ROLE = (
    "You are the ESG Screener in PodOS. You screen issuers in the pod's vault for governance, controversy and "
    "sustainability red flags and summarise exposure with a severity for each flag."
)


class EquityResearchAgent(PodosAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        if llm.enabled():
            meta = self.podos_meta(ctx)
            gen = run_tool_loop(self, ctx, agent_id="equity-research", system=system_prompt(EQUITY_ROLE, meta, genui.prompt_section()))
            async for e in llm_or_fallback(self, ctx, gen, self._deterministic(ctx)):
                yield e
            return
        async for e in self._deterministic(ctx):
            yield e

    async def _deterministic(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        meta = self.podos_meta(ctx)
        question = self.user_text(ctx)
        client = PlatformClient("equity-research", meta)
        pod_label = meta.get("pod_label", "the pod")
        case = meta.get("research_case") or {}

        overrides: dict[str, Any] = {}
        m = INJECT_RE.search(question)
        if m:
            target = m.group(1).lower()
            overrides = {"index": f"research-{target}", "pod_id": target}

        query = " ".join([question, *case.get("tickers", [])])
        yield self.event(ctx, {"type": "tool.started", "tool": "research.search", "label": f"Searching {pod_label} research vault"})
        focus = sorted(set(tickers_in(question)) | set(case.get("tickers", [])))
        try:
            # Prefer documents about the tickers the question names; fall back to a broad search.
            results = await client.search(query, limit=5, tickers=focus or None, **overrides)
            if not results and focus:
                results = await client.search(query, limit=5, **overrides)
        except ToolDenied as d:
            yield self.event(ctx, {"type": "tool.completed", "tool": "research.search", "status": "denied", "code": d.code})
            if d.code == "scope_violation":
                msg = (f"I tried to search outside {pod_label}'s private research as the request asked, but the platform "
                       "blocked it (`scope_violation`). I can only access this pod's own vault, and the attempt has been audited.")
            else:
                msg = f"I couldn't search the research vault (`{d.code}`)."
            async for e in self.stream_text(ctx, msg):
                yield e
            return
        yield self.event(ctx, {"type": "tool.completed", "tool": "research.search", "status": "ok",
                               "summary": f"{len(results)} sources", "count": len(results)})
        if not results:
            async for e in self.stream_text(ctx, f"I couldn't find anything in {pod_label}'s research vault that matches this question."):
                yield e
            return

        qterms = terms(question) | {t.lower() for t in tickers_in(question)}
        cites = _citations(results)
        lead = results[0]
        subject = lead.get("ticker") or (case.get("tickers") or ["the company"])[0]

        lines: list[str] = []
        if case.get("thesis"):
            lines.append(f"**Working thesis ({case.get('title', subject)}):** {case['thesis']}\n")
        lines.append(f"Here's what {pod_label}'s research says about **{subject}**:\n")
        key_points: list[str] = []
        risks: list[str] = []
        for i, r in enumerate(results[:4]):
            for s in top_sentences(r["body"], qterms, n=2):
                if re.search(r"\b(risk|pressure|concentration|slip|delay|pause|export control|resign|adverse)", s, re.I):
                    risks.append(f"{s} [{i + 1}]")
                else:
                    key_points.append(f"{s} [{i + 1}]")
        lines.append("**Key points**")
        lines += [f"- {p}" for p in key_points[:5]] or ["- No direct evidence found."]
        if risks:
            lines.append("\n**Risks and watch items**")
            lines += [f"- {r}" for r in risks[:4]]
        answer = "\n".join(lines) + "\n" + genui.block_for(question, results, subject)
        async for e in self.stream_text(ctx, answer):
            yield e
        yield self.event(ctx, {"type": "citations", "items": cites})

        # Optional governed export. The agent asks; the platform decides.
        for cap, rx, kind_label in (("document.export.pptx", PPTX_RE, "PowerPoint deck"),
                                    ("document.export.docx", DOCX_RE, "Word memo")):
            if not rx.search(question):
                continue
            spec = self._spec(subject, case, results, key_points, risks, cites, slides=(cap.endswith("pptx")))
            yield self.event(ctx, {"type": "tool.started", "tool": cap, "label": f"Generating {kind_label}"})
            try:
                res = await client.invoke_capability(cap, spec, idempotency_key=f"{cap}:{uuid.uuid5(uuid.NAMESPACE_URL, question)}",
                                                     case_id=case.get("case_id"))
                art = res["artifact"]
                yield self.event(ctx, {"type": "tool.completed", "tool": cap, "status": "ok", "summary": art["filename"]})
                text = f"\nI generated **{art['title']}** as a {kind_label} and saved it to {pod_label}'s vault."
            except ToolDenied as d:
                yield self.event(ctx, {"type": "tool.completed", "tool": cap, "status": "denied", "code": d.reason or d.code})
                why = DENIAL_TEXT.get(d.reason or "", f"the platform denied it (`{d.reason or d.code}`)")
                text = f"\nI couldn't create the {kind_label}: {why}."
            async for e in self.stream_text(ctx, text):
                yield e

    @staticmethod
    def _spec(subject, case, results, key_points, risks, cites, *, slides: bool) -> dict[str, Any]:
        numbers = [f"{s} [{i + 1}]" for i, r in enumerate(results[:4]) for s in sentences(r["body"]) if re.search(r"\d", s)]
        blocks = [
            {"title": "Thesis", "bullets": [case["thesis"]] if case.get("thesis") else key_points[:1] or ["See evidence"]},
            {"title": "Key evidence", "bullets": key_points[:5] or ["No direct evidence found"]},
            {"title": "Numbers that matter", "bullets": numbers[:5] or ["No quantitative datapoints in sources"]},
            {"title": "Risks and watch items", "bullets": risks[:5] or ["No explicit risks flagged in sources"]},
            {"title": "Next steps", "bullets": [
                f"Re-test thesis against the next {subject} filing",
                "Refresh model assumptions with latest guidance",
                "Confirm sizing with PM before any position change",
            ]},
        ]
        return {
            "title": f"{subject}: research summary",
            "subtitle": case.get("title", "Pod research brief"),
            "slides" if slides else "sections": blocks,
            "citations": [{k: c[k] for k in ("ref", "doc_id", "title", "source", "published_at")} for c in cites],
        }


class FilingAnalystAgent(PodosAgent):
    version: str = "1.0.0"

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        if llm.enabled():
            meta = self.podos_meta(ctx)
            extra = ""
            if self.version != "1.0.0":
                extra = (f"- You are version {self.version} (canary). Add a final table column 'Tone' scoring each filing "
                         "from -2 (negative) to +2 (positive), and start your answer with the line "
                         f"'_Filing Analyst v{self.version} (canary)_'.")
            gen = run_tool_loop(self, ctx, agent_id="filing-analyst", system=system_prompt(FILING_ROLE, meta, extra + genui.prompt_section()),
                                forced_kinds=["filing"])
            async for e in llm_or_fallback(self, ctx, gen, self._deterministic(ctx)):
                yield e
            return
        async for e in self._deterministic(ctx):
            yield e

    async def _deterministic(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        meta = self.podos_meta(ctx)
        question = self.user_text(ctx)
        client = PlatformClient("filing-analyst", meta)
        tickers = tickers_in(question) or None
        yield self.event(ctx, {"type": "tool.started", "tool": "research.search", "label": "Reading filings in the pod vault"})
        try:
            results = await client.search(question, limit=6, kinds=["filing"], tickers=tickers)
            if not results and tickers:
                results = await client.search(" ".join(tickers), limit=6, kinds=["filing"], tickers=tickers)
        except ToolDenied as d:
            yield self.event(ctx, {"type": "tool.completed", "tool": "research.search", "status": "denied", "code": d.code})
            async for e in self.stream_text(ctx, f"Filing search was denied (`{d.code}`)."):
                yield e
            return
        yield self.event(ctx, {"type": "tool.completed", "tool": "research.search", "status": "ok",
                               "summary": f"{len(results)} filings", "count": len(results)})
        if not results:
            async for e in self.stream_text(ctx, "No filings in this pod's vault match that request."):
                yield e
            return
        canary = self.version != "1.0.0"  # the candidate version adds a tone score per filing
        head = ""
        cols = "| Filing | Date | Key change | Signal |" + (" Tone |" if canary else "")
        sep = "|---|---|---|---|" + ("---|" if canary else "")
        rows = []
        deltas = []
        for i, r in enumerate(results):
            sents = sentences(r["body"])
            key = next((s for s in sents if re.search(r"\d", s)), sents[0] if sents else r["title"])
            signal = "⚠️ watch" if re.search(r"resign|export control|risk|adverse|pending", r["body"], re.I) else "✅ constructive"
            row = f"| {r['title']} [{i + 1}] | {r['published_at'][:10]} | {key[:140]} | {signal} |"
            if canary:
                pos = len(re.findall(r"\b(raise|grow|met|up|reaffirm|unchanged|accelerat)", r["body"], re.I))
                neg = len(re.findall(r"\b(resign|pending|adverse|pressure|risk|decline)", r["body"], re.I))
                row += f" {pos - neg:+d} |"
            rows.append(row)
            for s in sents:
                if re.search(r"risk factor|export control|resign|interim|adverse|pending", s, re.I):
                    deltas.append(f"- {s} [{i + 1}]")
        text = head + "**Filing review**\n\n" + "\n".join([cols, sep, *rows]) + "\n"
        if deltas:
            text += "\n**Risk-factor and governance deltas**\n" + "\n".join(deltas[:6]) + "\n"
        async for e in self.stream_text(ctx, text):
            yield e
        yield self.event(ctx, {"type": "citations", "items": _citations(results)})


class ESGScreenerAgent(PodosAgent):
    """Registered at runtime from the Admin console to demonstrate discovery without redeploys."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        if llm.enabled():
            meta = self.podos_meta(ctx)
            gen = run_tool_loop(self, ctx, agent_id="esg-screener", system=system_prompt(ESG_ROLE, meta))
            async for e in llm_or_fallback(self, ctx, gen, self._deterministic(ctx)):
                yield e
            return
        async for e in self._deterministic(ctx):
            yield e

    async def _deterministic(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        meta = self.podos_meta(ctx)
        client = PlatformClient("esg-screener", meta)
        question = self.user_text(ctx)
        yield self.event(ctx, {"type": "tool.started", "tool": "research.search", "label": "Screening for controversies"})
        results = await client.search(question + " governance controversy resign export risk", limit=5)
        yield self.event(ctx, {"type": "tool.completed", "tool": "research.search", "status": "ok", "count": len(results)})
        flags = [f"- {s} [{i + 1}]" for i, r in enumerate(results) for s in sentences(r["body"])
                 if re.search(r"resign|export control|concentration|adverse|governance", s, re.I)]
        text = "**ESG / governance screen**\n" + ("\n".join(flags[:6]) if flags else "- No controversy signals found.") + "\n"
        async for e in self.stream_text(ctx, text):
            yield e
        yield self.event(ctx, {"type": "citations", "items": _citations(results)})
