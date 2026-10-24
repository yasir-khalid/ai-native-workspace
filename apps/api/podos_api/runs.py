"""Interactive run orchestration (ARCHITECTURE.md 5.1 / 5.2).

Direct: policy check at request -> repeat at dispatch -> A2A stream -> persist.
Auto:   eligible candidates filtered *before* reasoning -> ADK router picks from
        that set -> API verifies the choice is in the set -> policy re-check at
        dispatch -> deterministic fallback within the same eligible set only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from podos_contracts.models import DelegationClaims, PodContext, RunRequest

from .a2a_adapter import AgentCallError
from .db import Jsonb, fetchall, fetchone
from .errors import Problem, not_found
from .governance import AgentAvailability, Snapshot, load_snapshot
from .security.tokens import issue_delegation
from .titles import short_title

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger("podos.runs")

ALLOWED_AGENT_EVENTS = {"tool.started", "tool.completed", "citations", "sources.available", "status"}
FALLBACK_CODES = {"agent_unreachable", "agent_timeout", "agent_call_failed", "agent_card_mismatch", "agent_unhealthy"}


class RateLimiter:
    """Per-pod sliding-window limiter (fair share between pods)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, per_minute: int) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_minute:
            return False
        q.append(now)
        return True


class RunManager:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._wake: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self._seq: dict[str, int] = {}
        self.limiter = RateLimiter()
        self.tool_calls: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ events
    async def emit(self, pod_id: str, run_id: str, event_type: str, payload: dict[str, Any]) -> int:
        async with self.svc.db.pod_tx(pod_id) as conn:
            if run_id not in self._seq:
                row = await fetchone(conn, "SELECT coalesce(max(sequence), 0) AS s FROM run_events WHERE run_id = %s", (run_id,))
                self._seq[run_id] = int(row["s"]) if row else 0
            self._seq[run_id] += 1
            seq = self._seq[run_id]
            await conn.execute(
                "INSERT INTO run_events(pod_id, run_id, sequence, event_type, payload) VALUES (%s,%s,%s,%s,%s)",
                (pod_id, run_id, seq, event_type, Jsonb(payload)),
            )
        self._wake[run_id].set()
        return seq

    def waiter(self, run_id: str) -> asyncio.Event:
        return self._wake[run_id]

    # ------------------------------------------------------------------ start
    async def start(self, ctx: PodContext, thread_id: str, req: RunRequest) -> dict[str, Any]:
        svc = self.svc
        snap = await load_snapshot(svc.db)
        quotas = svc.ops.quotas(ctx.active_pod_id)

        async with svc.db.pod_tx(ctx.active_pod_id) as conn:
            thread = await fetchone(
                conn, "SELECT * FROM threads WHERE pod_id = %s AND thread_id = %s", (ctx.active_pod_id, thread_id)
            )
            if thread is None:
                raise not_found("thread")
            if thread["created_by"] != ctx.subject_id:
                raise not_found("thread")
            if req.client_request_id:
                existing = await fetchone(
                    conn,
                    "SELECT * FROM agent_runs WHERE pod_id = %s AND thread_id = %s AND request_key = %s",
                    (ctx.active_pod_id, thread_id, req.client_request_id),
                )
                if existing:
                    return existing
            if "call_id" not in req.model_fields_set:
                previous = await fetchone(
                    conn, """SELECT content FROM messages WHERE pod_id = %s AND thread_id = %s
                             AND role = 'user' ORDER BY created_at DESC LIMIT 1""",
                    (ctx.active_pod_id, thread_id),
                )
                req = req.model_copy(update={"call_id": previous["content"].get("call_id") if previous else None})
            if req.call_id:
                call = await fetchone(
                    conn, "SELECT 1 FROM call_events WHERE pod_id = %s AND call_id = %s",
                    (ctx.active_pod_id, req.call_id),
                )
                if call is None:
                    raise not_found("call")
            if req.research_case_id:
                case = await fetchone(
                    conn, "SELECT 1 FROM research_cases WHERE pod_id = %s AND case_id::text = %s",
                    (ctx.active_pod_id, req.research_case_id),
                )
                if case is None:
                    raise not_found("research case")
            active = await fetchone(
                conn,
                "SELECT count(*) AS n FROM agent_runs WHERE pod_id = %s AND state IN ('QUEUED','RUNNING') AND mode IN ('direct','auto')",
                (ctx.active_pod_id,),
            )

        if active and active["n"] >= quotas.get("max_concurrent_runs", 4):
            raise Problem(429, "pod_concurrency_limit", "too many active runs for this pod")
        if not self.limiter.allow(ctx.active_pod_id, quotas.get("runs_per_minute", 30)):
            raise Problem(429, "pod_rate_limit", "run rate limit exceeded for this pod")

        if req.mode == "direct":
            if not req.agent_id:
                raise Problem(422, "agent_required", "direct mode requires agent_id")
            av = svc.gov.require_agent(ctx, snap, req.agent_id, "direct_invoke")
            if av.kind != "specialist":
                raise Problem(422, "agent_not_directly_invocable")
            run_agent, run_version = av.agent_id, av.version or ""
        else:
            av = svc.gov.require_agent(ctx, snap, "auto-router", "direct_invoke")
            run_agent, run_version = av.agent_id, av.version or ""

        exec_ctx = ctx.model_copy(update={"release_revision": snap.revision}).model_dump(mode="json")
        async with svc.db.pod_tx(ctx.active_pod_id) as conn:
            await conn.execute(
                "INSERT INTO messages(pod_id, thread_id, role, content, created_by) VALUES (%s,%s,'user',%s,%s)",
                (ctx.active_pod_id, thread_id, Jsonb({"text": req.message, "mode": req.mode, "agent_id": req.agent_id,
                                                   "call_id": req.call_id}),
                 ctx.subject_id),
            )
            run = await fetchone(
                conn,
                """INSERT INTO agent_runs(pod_id, thread_id, agent_id, agent_version, mode, state, created_by,
                        execution_context, release_revision, request_key, deployment_id)
                   VALUES (%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s) RETURNING *""",
                (ctx.active_pod_id, thread_id, run_agent, run_version, req.mode, ctx.subject_id, Jsonb(exec_ctx),
                 snap.revision, req.client_request_id, av.deployment.deployment_id if av.deployment else None),
            )
            title_needed = not thread["title"]
            await conn.execute(
                "UPDATE threads SET updated_at = now(), title = coalesce(title, %s) WHERE pod_id = %s AND thread_id = %s",
                (short_title(req.message) if title_needed else None, ctx.active_pod_id, thread_id),
            )
        assert run is not None
        run_id = str(run["run_id"])
        self._tasks[run_id] = asyncio.create_task(self._execute(ctx, run_id, thread_id, req))
        return run

    # ---------------------------------------------------------------- execute
    async def _execute(self, ctx: PodContext, run_id: str, thread_id: str, req: RunRequest) -> None:
        svc, pod = self.svc, ctx.active_pod_id
        try:
            await self._set_state(pod, run_id, "RUNNING")
            await self.emit(pod, run_id, "run.started", {"mode": req.mode})
            snap = await load_snapshot(svc.db)  # fresh snapshot: dispatch-time re-check
            if req.mode == "direct":
                candidates = [svc.gov.require_agent(ctx, snap, req.agent_id or "", "direct_invoke")]
                routing: dict[str, Any] = {"mode": "direct", "selected": req.agent_id}
            else:
                candidates, routing = await self._route(ctx, snap, run_id, req)
                if not candidates:
                    await self._fail(pod, run_id, "no_eligible_agent",
                                     "No agent is available for Auto in this pod. Choose an agent directly or contact the platform team.")
                    return
            await self._dispatch(ctx, snap, run_id, thread_id, req, candidates, routing)
        except Problem as p:
            await self._fail(pod, run_id, p.code, p.detail or "request was denied by policy")
        except asyncio.CancelledError:
            await self._fail(pod, run_id, "cancelled", "Run cancelled")
        except Exception:
            log.exception("run %s crashed", run_id)
            await self._fail(pod, run_id, "internal_error", "The run failed unexpectedly")
        finally:
            self._tasks.pop(run_id, None)
            self.tool_calls.pop(run_id, None)

    async def _route(
        self, ctx: PodContext, snap: Snapshot, run_id: str, req: RunRequest,
    ) -> tuple[list[AgentAvailability], dict[str, Any]]:
        svc = self.svc
        eligible = svc.gov.auto_candidates(ctx, snap)
        if not eligible:
            return [], {"mode": "auto", "eligible": []}
        router = svc.gov.require_agent(ctx, snap, "auto-router", "direct_invoke")
        payload = {
            "candidates": [
                {"agent_id": a.agent_id, "name": a.name, "description": a.description, "capabilities": a.capabilities}
                for a in eligible
            ],
            "run_id": run_id,
        }
        selected, rationale, source = None, "", "router"
        try:
            assert router.deployment is not None
            async for kind, value in svc.a2a.stream(router.deployment, req.message, {"route": payload}, timeout=10):
                if kind == "event" and value.get("type") == "route.selected":
                    selected, rationale = value.get("agent_id"), str(value.get("rationale", ""))[:300]
        except AgentCallError as exc:
            source, rationale = "fallback", f"router unavailable ({exc.code})"
        ids = [a.agent_id for a in eligible]
        if selected not in ids:
            # Router output is advisory: anything outside the pre-filtered set is discarded.
            if selected is not None:
                rationale = "router proposed an ineligible agent; deterministic fallback applied"
            source = "fallback"
            selected = "equity-research" if "equity-research" in ids else ids[0]
        ordered = [a for a in eligible if a.agent_id == selected] + [a for a in eligible if a.agent_id != selected]
        return ordered, {"mode": "auto", "eligible": ids, "selected": selected, "source": source, "rationale": rationale}

    async def _dispatch(
        self, ctx: PodContext, snap: Snapshot, run_id: str, thread_id: str, req: RunRequest,
        candidates: list[AgentAvailability], routing: dict[str, Any],
    ) -> None:
        svc, pod = self.svc, ctx.active_pod_id
        history, case_info, pod_label = await self._context_for(ctx, thread_id, req)
        call_context = None
        if req.call_id:
            # Read again under the run's pod scope; never accept transcript text from the client.
            from .routes.calls import _build_chat_context, _flatten_sentences

            async with svc.db.pod_tx(pod) as conn:
                call = await fetchone(conn, "SELECT * FROM call_events WHERE pod_id = %s AND call_id = %s",
                                      (pod, req.call_id))
            if call is None:
                raise not_found("call")
            evidence = _build_chat_context(call["transcript"], call["chapters"], call["qa"], req.message)
            call_context = {
                "call_id": req.call_id, "doc_id": f"call:{req.call_id}",
                "title": f"{call['company']} — {call['event_title']}", "ticker": call["ticker"],
                "source": "Call transcript", "kind": "transcript", "published_at": str(call["event_date"]),
                "text": evidence,
                "transcript_sha256": hashlib.sha256(json.dumps(call["transcript"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
                "audio_sha256": call.get("audio_sha256"),
                "asr_model": (call["transcript"].get("asr") or {}).get("model"),
                "attribution_method": (call["transcript"].get("asr") or {}).get("speaker_attribution", "not_recorded"),
                "review_status": "unreviewed",
                "sentences": [s for s in _flatten_sentences(call["transcript"])
                              if s["sentence_id"] and f"[{s['sentence_id']}]" in evidence],
                "speakers": call["transcript"].get("speakers", []),
            }
        attempts = candidates[:2] if req.mode == "auto" else candidates[:1]
        last_error: AgentCallError | None = None
        for idx, cand in enumerate(attempts):
            action = "direct_invoke" if req.mode == "direct" else "auto_route"
            av = svc.gov.require_agent(ctx, snap, cand.agent_id, action)  # repeat policy check at invocation
            assert av.deployment is not None
            permitted_caps = sorted(
                c for c in av.capabilities
                if svc.gov.capability_status(ctx, snap, c)[0] and svc.pdp.agent_service_may_call(av.agent_id, c)
            )
            preferred = [c for c in req.allowed_output_capabilities if c in permitted_caps]
            claims = DelegationClaims(
                sub=ctx.subject_id, pod=pod, run=run_id, agent=av.agent_id, caps=permitted_caps,
                depth=ctx.delegation_depth + 1, purpose=ctx.purpose, cls=ctx.data_classification, rev=snap.revision,
            )
            async with svc.db.pod_tx(pod) as conn:
                await conn.execute(
                    """UPDATE agent_runs SET agent_id = %s, agent_version = %s, deployment_id = %s, routing = %s,
                           updated_at = now() WHERE pod_id = %s AND run_id = %s""",
                    (av.agent_id, av.version, av.deployment.deployment_id, Jsonb({**routing, "attempt": idx + 1}), pod, run_id),
                )
            await self.emit(pod, run_id, "agent.selected", {
                "agent_id": av.agent_id, "name": av.name, "version": av.version, "variant": av.version_variant,
                "mode": req.mode, "rationale": routing.get("rationale", ""), "source": routing.get("source", "direct"),
                "fallback": idx > 0,
            })
            external = av.deployment.trust == "external"
            # External A2A agents get the question only (no delegation, internal URL, pod or research context).
            metadata = {} if external else {
                "delegation_token": issue_delegation(svc.settings, claims),
                "api_base": svc.settings.internal_api_url,
                "run_id": run_id,
                "mode": "interactive",
                "pod_label": pod_label,
                "permitted_capabilities": permitted_caps,
                "preferred_outputs": preferred,
                "history": history,
                "research_case": case_info,
                "call_context": call_context,
                "max_tool_calls": svc.ops.quotas(pod).get("max_tool_calls_per_run", 6),
                "agent_version": av.version,
            }
            text_parts: list[str] = []
            citations: list[dict[str, Any]] = []
            produced = False
            try:
                async for kind, value in svc.a2a.stream(av.deployment, req.message, metadata,
                                                        conversation_key=thread_id if external else None):
                    if kind == "delta":
                        produced = True
                        text_parts.append(value)
                        await self.emit(pod, run_id, "message.delta", {"text": value})
                    elif kind == "event" and value.get("type") in ALLOWED_AGENT_EVENTS:
                        if value["type"] == "citations":
                            citations = [c for c in value.get("items", []) if isinstance(c, dict)][:200]
                        else:
                            await self.emit(pod, run_id, value["type"], {k: v for k, v in value.items() if k != "type"})
                    elif kind == "state" and value == "failed":
                        raise AgentCallError("agent_reported_failure", "agent reported a failure")
            except AgentCallError as exc:
                last_error = exc
                await svc.audit(actor="workspace-api", action="agent.call_failed", target=av.agent_id, pod_id=pod,
                                outcome="error", detail={"code": exc.code, "run_id": run_id})
                if produced or exc.code not in FALLBACK_CODES or req.mode != "auto":
                    break
                continue
            await self._complete(ctx, run_id, thread_id, av, "".join(text_parts), citations)
            return
        assert last_error is not None
        await self._fail(pod, run_id, last_error.code, "The agent is unavailable right now. No other pod's resources were used.")

    async def _context_for(self, ctx: PodContext, thread_id: str, req: RunRequest) -> tuple[list[dict], dict | None, str]:
        async with self.svc.db.pod_tx(ctx.active_pod_id) as conn:
            msgs = await fetchall(
                conn,
                """SELECT role, content FROM messages WHERE pod_id = %s AND thread_id = %s
                   ORDER BY created_at DESC LIMIT 7""",
                (ctx.active_pod_id, thread_id),
            )
            case_info = None
            if req.research_case_id:
                c = await fetchone(
                    conn, "SELECT case_id::text, title, thesis, tickers FROM research_cases WHERE pod_id = %s AND case_id::text = %s",
                    (ctx.active_pod_id, req.research_case_id),
                )
                case_info = dict(c) if c else None
        async with self.svc.db.system_tx() as conn:
            pod = await fetchone(conn, "SELECT display_name FROM pods WHERE pod_id = %s", (ctx.active_pod_id,))
        history = [{"role": m["role"], "text": str(m["content"].get("text", ""))[:1500]} for m in reversed(msgs[1:])]
        return history, case_info, pod["display_name"] if pod else ctx.active_pod_id

    async def _complete(
        self, ctx: PodContext, run_id: str, thread_id: str, av: AgentAvailability, text: str, citations: list[dict],
    ) -> None:
        pod = ctx.active_pod_id
        async with self.svc.db.pod_tx(pod) as conn:
            arts = await fetchall(
                conn,
                """SELECT artifact_id::text, filename, mime_type, title, capability_id, classification, size_bytes
                   FROM artifacts WHERE pod_id = %s AND run_id = %s ORDER BY created_at""",
                (pod, run_id),
            )
            msg = await fetchone(
                conn,
                """INSERT INTO messages(pod_id, thread_id, role, content, run_id, created_by)
                   VALUES (%s,%s,'assistant',%s,%s,%s) RETURNING message_id::text""",
                (pod, thread_id, Jsonb({
                    "text": text, "citations": citations, "artifacts": arts, "agent_id": av.agent_id,
                    "agent_name": av.name, "agent_version": av.version,
                }), run_id, av.agent_id),
            )
            await conn.execute(
                "UPDATE agent_runs SET state = 'COMPLETED', finished_at = now(), updated_at = now() WHERE pod_id = %s AND run_id = %s",
                (pod, run_id),
            )
            await conn.execute("UPDATE threads SET updated_at = now() WHERE pod_id = %s AND thread_id = %s", (pod, thread_id))
        await self.emit(pod, run_id, "run.completed", {"message_id": msg["message_id"] if msg else None,
                                                       "citations": citations, "artifacts": arts})

    async def _set_state(self, pod: str, run_id: str, state: str) -> None:
        async with self.svc.db.pod_tx(pod) as conn:
            await conn.execute(
                "UPDATE agent_runs SET state = %s, updated_at = now() WHERE pod_id = %s AND run_id = %s", (state, pod, run_id)
            )

    async def _fail(self, pod: str, run_id: str, code: str, message: str) -> None:
        state = "CANCELLED" if code == "cancelled" else "FAILED"
        async with self.svc.db.pod_tx(pod) as conn:
            await conn.execute(
                """UPDATE agent_runs SET state = %s, error = %s, finished_at = now(), updated_at = now()
                   WHERE pod_id = %s AND run_id = %s AND state NOT IN ('COMPLETED','FAILED','CANCELLED')""",
                (state, Jsonb({"code": code, "message": message}), pod, run_id),
            )
        await self.emit(pod, run_id, "run.failed", {"code": code, "message": message})

    async def cancel(self, ctx: PodContext, run_id: str) -> bool:
        async with self.svc.db.pod_tx(ctx.active_pod_id) as conn:
            run = await fetchone(
                conn, "SELECT created_by, state FROM agent_runs WHERE pod_id = %s AND run_id::text = %s",
                (ctx.active_pod_id, run_id),
            )
        if run is None:
            raise not_found("run")
        if run["created_by"] != ctx.subject_id and ctx.pod_role != "pm":
            raise Problem(403, "not_run_owner")
        task = self._tasks.get(run_id)
        if task:
            task.cancel()
            return True
        return False

    async def recover_orphans(self) -> int:
        """On API start, interactive runs whose process died are failed explicitly."""
        n = 0
        async with self.svc.db.system_tx() as conn:
            pods = await fetchall(conn, "SELECT pod_id FROM pods")
        for p in pods:
            async with self.svc.db.pod_tx(p["pod_id"]) as conn:
                rows = await fetchall(
                    conn,
                    """UPDATE agent_runs SET state = 'FAILED', error = %s, finished_at = now()
                       WHERE pod_id = %s AND mode IN ('direct','auto') AND state IN ('QUEUED','RUNNING') RETURNING run_id::text""",
                    (Jsonb({"code": "interrupted", "message": "API restarted during run"}), p["pod_id"]),
                )
            for r in rows:
                await self.emit(p["pod_id"], r["run_id"], "run.failed", {"code": "interrupted", "message": "Run interrupted"})
            n += len(rows)
        return n


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())
