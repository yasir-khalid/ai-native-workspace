"""Trusted execution context (ARCHITECTURE.md 3.1).

The active pod is a *selection* (URL path or ``X-Active-Pod``) that is validated
against live membership on every request. Membership, role, groups and every
entitlement are resolved from OpenFGA each time (no long-lived cache), so
revocation applies to the very next request, stream poll, download redemption
or approval.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from podos_contracts.models import PodContext

from ..authz.pdp import AccessDecisionPoint
from ..db import Database, fetchone
from ..errors import Problem, forbidden
from ..settings import Settings
from .tokens import TokenError, verify_session

SESSION_COOKIE = "podos_session"


@dataclass
class Principal:
    subject_id: str
    display_name: str
    title: str
    platform_roles: list[str]
    session_id: str


async def principal_from_token(db: Database, settings: Settings, token: str | None) -> Principal:
    if not token:
        raise Problem(401, "unauthenticated", "sign in required")
    try:
        claims = verify_session(settings, token)
    except TokenError:
        raise Problem(401, "unauthenticated", "invalid or expired session") from None
    async with db.system_tx() as conn:
        user = await fetchone(conn, "SELECT * FROM users WHERE subject_id = %s AND active", (claims["sub"],))
    if user is None:
        raise Problem(401, "unauthenticated", "unknown or disabled user")
    return Principal(
        subject_id=user["subject_id"], display_name=user["display_name"], title=user["title"],
        platform_roles=list(user["platform_roles"]), session_id=claims["sid"],
    )


async def build_pod_context(
    db: Database, pdp: AccessDecisionPoint, principal: Principal, pod_id: str, *,
    request_id: str | None = None, trace_id: str | None = None, release_revision: str = "",
) -> PodContext:
    if not pod_id:
        raise forbidden("pod_selection_required", "an active pod must be selected")
    res = await pdp.resolve(principal.subject_id, pod_id)
    if res is None:
        # Same response for "pod doesn't exist" and "not a member".
        raise forbidden("not_a_pod_member", "you are not a member of the selected pod")
    return PodContext(
        subject_id=principal.subject_id,
        active_pod_id=pod_id,
        pod_role=res.pod_role,
        groups=res.groups,
        entitlements=res.entitlements,
        platform_roles=principal.platform_roles,
        session_id=principal.session_id,
        permissions_revision=pdp.revision,
        release_revision=release_revision,
        request_id=request_id or uuid.uuid4().hex,
        trace_id=trace_id or uuid.uuid4().hex,
    )


async def is_active_member(pdp: AccessDecisionPoint, pod_id: str, subject_id: str) -> bool:
    return await pdp.is_member(pod_id, subject_id)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
def services(request: Request):
    return request.app.state.services


async def current_principal(request: Request, svc=Depends(services)) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    return await principal_from_token(svc.db, svc.settings, token)


async def pod_ctx_from_path(
    pod_id: str, request: Request, principal: Principal = Depends(current_principal), svc=Depends(services)
) -> PodContext:
    return await build_pod_context(
        svc.db, svc.pdp, principal, pod_id,
        request_id=request.state.request_id, trace_id=request.state.trace_id,
    )


async def pod_ctx_from_header(
    request: Request,
    x_active_pod: str | None = Header(default=None),
    principal: Principal = Depends(current_principal),
    svc=Depends(services),
) -> PodContext:
    return await build_pod_context(
        svc.db, svc.pdp, principal, x_active_pod or "",
        request_id=request.state.request_id, trace_id=request.state.trace_id,
    )


async def require_platform_admin(principal: Principal = Depends(current_principal), svc=Depends(services)) -> Principal:
    if not await svc.pdp.is_platform_admin(principal.subject_id):
        raise forbidden("admin_required", "platform administrator role required")
    return principal
