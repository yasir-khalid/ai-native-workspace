"""Mock OIDC login, /me, pod enumeration and the per-pod catalogue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from podos_contracts.models import PodContext
from pydantic import BaseModel

from ..db import fetchall, fetchone
from ..errors import Problem
from ..governance import load_snapshot
from ..security.context import SESSION_COOKIE, Principal, current_principal, pod_ctx_from_path, services
from ..security.tokens import issue_session

router = APIRouter(prefix="/v1")


class DevLogin(BaseModel):
    subject_id: str


@router.get("/auth/dev-users")
async def dev_users(svc=Depends(services)) -> list[dict[str, Any]]:
    if not svc.settings.dev_login_enabled:
        raise Problem(404, "not_found")
    async with svc.db.system_tx() as conn:
        users = await fetchall(conn, "SELECT subject_id, display_name, title, platform_roles FROM users WHERE active ORDER BY subject_id")
        mems = await fetchall(
            conn,
            """SELECT m.subject_id, p.display_name, m.pod_role FROM pod_memberships m JOIN pods p USING (pod_id)
               WHERE m.revoked_at IS NULL ORDER BY p.pod_id""",
        )
    for u in users:
        u["pods"] = [f"{m['display_name']} · {m['pod_role'].upper()}" for m in mems if m["subject_id"] == u["subject_id"]]
    return users


@router.post("/auth/dev-login")
async def dev_login(body: DevLogin, response: Response, svc=Depends(services)) -> dict[str, Any]:
    """Stand-in for the OIDC callback. Disabled outside dev/test."""
    if not svc.settings.dev_login_enabled:
        raise Problem(404, "not_found")
    async with svc.db.system_tx() as conn:
        user = await fetchone(conn, "SELECT subject_id FROM users WHERE subject_id = %s AND active", (body.subject_id,))
    if user is None:
        raise Problem(401, "unauthenticated", "unknown user")
    token = issue_session(svc.settings, body.subject_id)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=False,
                        max_age=svc.settings.session_ttl_seconds, path="/")
    await svc.audit(actor=body.subject_id, action="auth.login", target=body.subject_id)
    return {"subject_id": body.subject_id, "token": token}


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "signed_out"}


@router.get("/me")
async def me(p: Principal = Depends(current_principal), svc=Depends(services)) -> dict[str, Any]:
    return {"subject_id": p.subject_id, "display_name": p.display_name, "title": p.title,
            "platform_roles": p.platform_roles, "is_admin": await svc.pdp.is_platform_admin(p.subject_id)}


@router.get("/me/pods")
async def my_pods(p: Principal = Depends(current_principal), svc=Depends(services)) -> list[dict[str, Any]]:
    """Pods the subject is a live member of, according to OpenFGA."""
    pod_ids = sorted(o.removeprefix("pod:") for o in await svc.fga.list_objects(f"user:{p.subject_id}", "member", "pod"))
    async with svc.db.system_tx() as conn:
        pods = await fetchall(conn, "SELECT pod_id, display_name, strategy, accent FROM pods WHERE pod_id = ANY(%s) ORDER BY pod_id",
                              (pod_ids,))
    out = []
    for pod in pods:
        res = await svc.pdp.resolve(p.subject_id, pod["pod_id"])
        if res is not None:
            out.append({**pod, "pod_role": res.pod_role, "groups": res.groups})
    return out


@router.get("/pods/{pod_id}/agents")
async def pod_agents(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)) -> list[dict[str, Any]]:
    snap = await load_snapshot(svc.db)
    out = []
    for av in svc.gov.catalogue(ctx, snap):
        direct_ok, direct_why = av.allows("direct_invoke")
        auto_ok, _ = av.allows("auto_route")
        bg_ok, bg_why = av.allows("background_execution")
        available, why = (bg_ok, bg_why) if av.kind == "background" else (direct_ok, direct_why)
        out.append({
            "agent_id": av.agent_id, "name": av.name, "description": av.description, "kind": av.kind,
            "owner_team": av.owner_team, "publication_status": av.publication_status, "version": av.version,
            "release_stage": av.release_stage, "healthy": av.healthy, "capabilities": av.capabilities,
            "health_status": "not_connected" if av.deployment is None else "healthy" if av.healthy else "unhealthy",
            # External A2A agents receive only the user's question (no pod research or identifiers).
            "external": bool(av.deployment and av.deployment.trust == "external"),
            "actions": {"direct": direct_ok, "auto": auto_ok, "background": bg_ok},
            "unavailable_reason": None if available else why,
        })
    return out


@router.get("/pods/{pod_id}/capabilities")
async def pod_capabilities(ctx: PodContext = Depends(pod_ctx_from_path), svc=Depends(services)) -> list[dict[str, Any]]:
    snap = await load_snapshot(svc.db)
    out = []
    for cap_id, cap in sorted(snap.capabilities.items()):
        ok, why = svc.gov.capability_status(ctx, snap, cap_id)
        if ok:
            out.append({"capability_id": cap_id, "description": cap.description, "result_type": cap.result_type,
                        "side_effect_class": cap.side_effect_class})
    return out
