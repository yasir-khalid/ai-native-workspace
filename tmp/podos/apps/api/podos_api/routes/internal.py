"""Internal endpoints called by agents (service-to-service).

Two credentials are required and cross-checked:
- ``Authorization: Workload <jwt>``: which *service* is calling (workload identity).
- ``X-PodOS-Delegation: <jwt>``: what that service may do for which user in which
  pod for which run (issued by the Workspace API at dispatch).

The pod, index and run scope come only from the delegation. Any scope the agent
puts in the request body (index name, pod id) is compared and rejected on
mismatch: this is the defence against prompt-injected tool parameters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header
from podos_contracts.models import AgentEvent, Classification, DelegationClaims, DocumentSpec, PodContext
from pydantic import BaseModel, Field

from ..db import fetchone
from ..errors import Problem, forbidden
from ..governance import load_snapshot
from ..search import index_name_for
from ..security.context import Principal, build_pod_context, services
from ..security.tokens import TokenError, verify_delegation, verify_workload
from ..workitems import ingest_agent_event

router = APIRouter(prefix="/v1/internal")


async def workload_identity(authorization: str = Header(default=""), svc=Depends(services)) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "workload" or not token:
        raise Problem(401, "workload_auth_required")
    try:
        return verify_workload(svc.settings, token)
    except TokenError:
        raise Problem(401, "workload_auth_invalid") from None


async def delegated_context(
    workload: str = Depends(workload_identity),
    x_podos_delegation: str = Header(default=""),
    svc=Depends(services),
) -> tuple[DelegationClaims, PodContext, str]:
    try:
        claims = verify_delegation(svc.settings, x_podos_delegation)
    except TokenError:
        raise Problem(401, "delegation_invalid", "delegation missing, invalid or expired") from None
    if claims.agent != workload:
        await svc.audit(actor=workload, action="delegation.misuse", target=claims.run, pod_id=claims.pod,
                        outcome="denied", detail={"delegated_agent": claims.agent})
        raise forbidden("delegation_agent_mismatch", "delegation was issued to a different agent")
    if claims.depth > 2:
        raise forbidden("delegation_depth_exceeded")
    # Live membership -> fresh context (revocation applies mid-run).
    ctx = await build_pod_context(
        svc.db, svc.pdp,
        Principal(subject_id=claims.sub, display_name=claims.sub, title="", platform_roles=[], session_id=f"run:{claims.run}"),
        claims.pod,
    )
    ctx = ctx.model_copy(update={"delegation_depth": claims.depth, "purpose": claims.purpose})
    async with svc.db.pod_tx(claims.pod) as conn:
        run = await fetchone(conn, "SELECT state, agent_id FROM agent_runs WHERE pod_id = %s AND run_id::text = %s",
                             (claims.pod, claims.run))
    if run is None or run["agent_id"] != claims.agent or run["state"] != "RUNNING":
        raise forbidden("run_not_active", "delegation is not valid for an active run")
    return claims, ctx, workload


async def _budget(svc, claims: DelegationClaims) -> None:
    svc.runs.tool_calls[claims.run] += 1
    if svc.runs.tool_calls[claims.run] > svc.ops.quotas(claims.pod).get("max_tool_calls_per_run", 6):
        raise Problem(429, "tool_budget_exceeded", "tool-call budget for this run is exhausted")


class SearchCall(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=6, ge=1, le=20)
    kinds: list[str] | None = None
    tickers: list[str] | None = None
    published_after: datetime | None = None
    # Scope fields are accepted only to be *checked*; they can never widen scope.
    index: str | None = None
    pod_id: str | None = None


@router.post("/tools/research.search")
async def tool_search(body: SearchCall, dc=Depends(delegated_context), svc=Depends(services)) -> dict[str, Any]:
    claims, ctx, workload = dc
    expected_index = index_name_for(claims.pod)
    if (body.index and body.index != expected_index) or (body.pod_id and body.pod_id != claims.pod):
        await svc.audit(actor=workload, action="tool.scope_violation", target="research.search", pod_id=claims.pod,
                        outcome="denied", detail={"requested_index": body.index, "requested_pod": body.pod_id,
                                                  "run_id": claims.run})
        raise forbidden("scope_violation", "requested index or pod is outside the delegated scope")
    if "research.search" not in claims.caps:
        raise forbidden("capability_denied", reason="not_in_delegation")
    snap = await load_snapshot(svc.db)
    svc.gov.require_capability(ctx, snap, "research.search", classification=Classification.pod_confidential,
                               via_agent=claims.agent)
    await _budget(svc, claims)
    rows = await svc.index(claims.pod).search(
        body.query, limit=body.limit, kinds=body.kinds, tickers=body.tickers, published_after=body.published_after,
    )
    return {"index": expected_index, "results": [
        {**{k: v for k, v in r.items() if k != "body"}, "body": r["body"][:1500]} for r in rows
    ]}


class CapabilityCall(BaseModel):
    spec: DocumentSpec
    idempotency_key: str = Field(min_length=8, max_length=200)
    case_id: str | None = None
    pod_id: str | None = None


@router.post("/tools/capabilities/{capability_id}/invoke")
async def tool_capability(capability_id: str, body: CapabilityCall, dc=Depends(delegated_context),
                          svc=Depends(services)) -> dict[str, Any]:
    claims, ctx, workload = dc
    if body.pod_id and body.pod_id != claims.pod:
        await svc.audit(actor=workload, action="tool.scope_violation", target=capability_id, pod_id=claims.pod,
                        outcome="denied", detail={"requested_pod": body.pod_id, "run_id": claims.run})
        raise forbidden("scope_violation", "requested pod is outside the delegated scope")
    snap = await load_snapshot(svc.db)
    if capability_id not in claims.caps:
        # Re-evaluate anyway so the denial reason is precise (entitlement vs release vs service grant).
        svc.gov.require_capability(ctx, snap, capability_id, classification=body.spec.classification, via_agent=claims.agent)
        raise forbidden("capability_denied", reason="not_in_delegation")
    grant = svc.gov.require_capability(ctx, snap, capability_id, classification=body.spec.classification,
                                       via_agent=claims.agent)
    await _budget(svc, claims)
    case_id = None
    if body.case_id:
        async with svc.db.pod_tx(claims.pod) as conn:
            ok = await fetchone(conn, "SELECT 1 FROM research_cases WHERE pod_id = %s AND case_id::text = %s",
                                (claims.pod, body.case_id))
        case_id = body.case_id if ok else None
    async with svc.db.system_tx() as conn:
        pod = await fetchone(conn, "SELECT display_name FROM pods WHERE pod_id = %s", (claims.pod,))
    art, created = await svc.capabilities.render_document(
        grant, body.spec, idempotency_key=f"run:{claims.run}:{body.idempotency_key}", pod_label=pod["display_name"],
        run_id=claims.run, case_id=case_id, provenance={"run_id": claims.run, "agent_id": claims.agent},
    )
    if created:
        await svc.runs.emit(claims.pod, claims.run, "artifact.created", {"artifact": art.model_dump(mode="json")})
    return {"artifact": art.model_dump(mode="json"), "created": created}


@router.post("/agent-events", status_code=202)
async def agent_events(ev: AgentEvent, workload: str = Depends(workload_identity), svc=Depends(services)) -> dict[str, Any]:
    return await ingest_agent_event(svc, workload, ev)
