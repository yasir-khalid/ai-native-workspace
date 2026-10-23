"""Access administration (PLAN_PROACTIVE.md §11): grant people, groups or whole pods access to agents,
capabilities, datasets, features and pod actions. Writes go to OpenFGA (the decision source) and to
an append-only ledger; the "effective access" view uses the same resolution as enforcement, so the
explanation can never disagree with what the API actually allows.

Platform admins manage access *metadata* only: nothing here reads pod research.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from podos_contracts.models import PodContext
from pydantic import BaseModel, Field, field_validator

from ..authz import tuples as T
from ..authz.fga import Tuple
from ..db import fetchall
from ..errors import Problem, not_found
from ..governance import load_snapshot
from ..security.context import Principal, require_platform_admin, services

router = APIRouter(prefix="/v1/admin/access")

ACTIONS = {
    "work_item.approve": ("Approve work", "Approve, reject or request changes on briefs and other work items."),
    "data.read_restricted": ("Read restricted research", "Open research classified as restricted (PMs have this by default)."),
}
Kind = Literal["agent", "capability", "dataset", "feature", "action"]
SubjectKind = Literal["user", "group", "pod"]
MAX_GRANT_DAYS = 365


# ---------------------------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------------------------
async def _catalogue(svc) -> dict[str, Any]:
    async with svc.db.system_tx() as conn:
        pods = await fetchall(conn, "SELECT pod_id, display_name, strategy FROM pods ORDER BY pod_id")
        agents = await fetchall(
            conn, "SELECT agent_id, name, description, kind FROM agent_definitions WHERE kind <> 'router' ORDER BY name"
        )
        router_agents = await fetchall(conn, "SELECT agent_id, name, description, kind FROM agent_definitions WHERE kind = 'router'")
        caps = await fetchall(
            conn, "SELECT capability_id, description, side_effect_class FROM capability_definitions ORDER BY capability_id"
        )
        features = await fetchall(conn, "SELECT feature_id, name, description FROM feature_definitions ORDER BY name")
        datasets = await fetchall(
            conn, "SELECT dataset_id, name, description, source, classification, licence FROM dataset_definitions ORDER BY name"
        )
    groups: dict[str, list[str]] = {}
    for p in pods:
        rows = await svc.fga.read(user=T.pod(p["pod_id"]), relation="pod", object="group:")
        groups[p["pod_id"]] = sorted(r.object.split("/", 1)[1] for r in rows)
    return {
        "pods": pods, "groups": groups,
        "items": {
            "agent": [{"id": a["agent_id"], "name": a["name"], "description": a["description"]} for a in router_agents + agents],
            "capability": [{"id": c["capability_id"], "name": c["capability_id"], "description": c["description"],
                            "side_effect_class": c["side_effect_class"]} for c in caps],
            "feature": [{"id": f["feature_id"], "name": f["name"], "description": f["description"]} for f in features],
            "dataset": [{"id": d["dataset_id"], "name": d["name"], "description": d["description"],
                         "classification": d["classification"], "licence": d["licence"]} for d in datasets],
            "action": [{"id": k, "name": v[0], "description": v[1]} for k, v in ACTIONS.items()],
        },
    }


@router.get("/catalogue")
async def catalogue(admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    return await _catalogue(svc)


def _object_for(kind: str, pod_id: str, item_id: str) -> tuple[str, str]:
    """(object, relation-holding-grantees) for a grantable item."""
    if kind == "action":
        return T.pod(pod_id), T.POD_ACTIONS[item_id]
    return T.scoped(kind, pod_id, item_id), "grantee"


def _describe_subject(user: str, pod_id: str) -> dict[str, str]:
    if user.startswith("user:"):
        return {"kind": "user", "id": user.removeprefix("user:")}
    if user == f"{T.pod(pod_id)}#member":
        return {"kind": "pod", "id": pod_id}
    if user.startswith("group:") and user.endswith("#member"):
        return {"kind": "group", "id": user.removeprefix(f"group:{pod_id}/").removesuffix("#member")}
    return {"kind": "other", "id": user}


async def _grants_for(svc, pod_id: str, kind: str, item_id: str) -> list[dict[str, Any]]:
    obj, rel = _object_for(kind, pod_id, item_id)
    rows = [t for t in await svc.fga.read(object=obj) if t.relation == rel]
    return [{**_describe_subject(t.user, pod_id), "expires_at": t.expires_at.isoformat() if t.expires_at else None}
            for t in rows]


@router.get("/grants")
async def list_grants(pod_id: str, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    cat = await _catalogue(svc)
    if pod_id not in {p["pod_id"] for p in cat["pods"]}:
        raise not_found("pod")
    jobs = [(k, i["id"]) for k, items in cat["items"].items() for i in items]
    found = await asyncio.gather(*(_grants_for(svc, pod_id, k, i) for k, i in jobs))
    return [{"kind": k, "item_id": i, "grantees": g} for (k, i), g in zip(jobs, found, strict=True)]


# ---------------------------------------------------------------------------------------------
# People and effective access
# ---------------------------------------------------------------------------------------------
@router.get("/people")
async def people(admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    async with svc.db.system_tx() as conn:
        users = await fetchall(conn, "SELECT subject_id, display_name, title, email FROM users WHERE active ORDER BY display_name")
        pods = [r["pod_id"] for r in await fetchall(conn, "SELECT pod_id FROM pods ORDER BY pod_id")]
    roles = {p: dict(await svc.pdp.members(p)) for p in pods}
    out = []
    for u in users:
        memberships = []
        for p in pods:
            if u["subject_id"] in roles[p]:
                res = await svc.pdp.resolve(u["subject_id"], p)
                memberships.append({"pod_id": p, "pod_role": roles[p][u["subject_id"]], "groups": res.groups if res else []})
        out.append({**u, "memberships": memberships, "is_admin": await svc.pdp.is_platform_admin(u["subject_id"])})
    return out


@router.get("/effective")
async def effective(subject_id: str, pod_id: str, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    """Everything a person can do in a pod, and why (which grant), plus what rollout still hides."""
    res = await svc.pdp.resolve(subject_id, pod_id)
    if res is None:
        return {"subject_id": subject_id, "pod_id": pod_id, "member": False, "items": []}
    ctx = PodContext(subject_id=subject_id, active_pod_id=pod_id, pod_role=res.pod_role, groups=res.groups,
                     entitlements=res.entitlements, session_id="admin-effective", permissions_revision=svc.pdp.revision,
                     request_id="admin-effective", trace_id="admin-effective")
    cat = await _catalogue(svc)
    snap = await load_snapshot(svc.db)
    granted = {
        "agent": set(res.entitlements.agents), "capability": set(res.entitlements.capabilities),
        "dataset": set(res.entitlements.datasets), "feature": set(res.entitlements.features),
        "action": set(res.entitlements.actions),
    }
    jobs = [(k, i) for k, items in cat["items"].items() for i in items]
    all_grants = await asyncio.gather(*(_grants_for(svc, pod_id, k, i["id"]) for k, i in jobs))
    items = []
    now = datetime.now(UTC)
    for (kind, item), grants in zip(jobs, all_grants, strict=True):
        via = []
        for g in grants:
            exp = datetime.fromisoformat(g["expires_at"]) if g["expires_at"] else None
            if exp and exp <= now:
                continue
            if (g["kind"] == "user" and g["id"] == subject_id) or g["kind"] == "pod" or \
               (g["kind"] == "group" and g["id"] in res.groups):
                via.append({**g})
        if kind == "action" and item["id"] == "data.read_restricted" and res.pod_role == "pm":
            via.append({"kind": "role", "id": "pm", "expires_at": None})
        entitled = item["id"] in granted[kind]
        row: dict[str, Any] = {"kind": kind, "id": item["id"], "name": item["name"], "entitled": entitled, "via": via}
        if kind == "agent":
            av = svc.gov.agent_availability(ctx, snap, item["id"])
            row["available"] = bool(av and av.catalogue_visible)
            row["release"] = av.release_reason if av else "unknown"
        elif kind == "capability":
            ok, why = svc.gov.capability_status(ctx, snap, item["id"])
            row["available"], row["release"] = ok, why
        else:
            row["available"] = entitled
        items.append(row)
    return {"subject_id": subject_id, "pod_id": pod_id, "member": True, "pod_role": res.pod_role, "groups": res.groups,
            "items": items}


# ---------------------------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------------------------
class GrantBody(BaseModel):
    pod_id: str
    kind: Kind
    item_id: str
    subject_kind: SubjectKind
    subject_id: str = ""
    expires_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("expires_at")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        return v.replace(tzinfo=UTC) if v and v.tzinfo is None else v


class RevokeBody(BaseModel):
    pod_id: str
    kind: Kind
    item_id: str
    subject_kind: SubjectKind
    subject_id: str = ""
    reason: str = Field(min_length=3, max_length=500)


async def _validate(svc, pod_id: str, kind: str, item_id: str, subject_kind: str, subject_id: str) -> str:
    cat = await _catalogue(svc)
    if pod_id not in {p["pod_id"] for p in cat["pods"]}:
        raise not_found("pod")
    if item_id not in {i["id"] for i in cat["items"][kind]}:
        raise Problem(422, "unknown_item", f"no {kind} called {item_id}")
    if subject_kind == "user":
        if not await svc.pdp.is_member(pod_id, subject_id):
            raise Problem(422, "not_a_pod_member", "grant access to people who are members of this pod")
    elif subject_kind == "group":
        if subject_id not in cat["groups"][pod_id]:
            raise Problem(422, "unknown_group", f"{pod_id} has no group called {subject_id}")
    elif kind == "action":
        raise Problem(422, "invalid_subject", "pod actions are granted to people or groups, not the whole pod")
    return T.subject_ref(subject_kind, pod_id, subject_id)


async def _ledger(svc, actor: str, action: str, pod_id: str, kind: str, item: str, sk: str | None, sid: str | None,
                  expires_at: datetime | None, reason: str) -> None:
    async with svc.db.system_tx() as conn:
        await conn.execute(
            """INSERT INTO access_changes(actor, action, pod_id, object_kind, object_id, subject_kind, subject_id, expires_at, reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (actor, action, pod_id, kind, item, sk, sid, expires_at, reason),
        )
    await svc.audit(actor=actor, action=f"access.{action}", target=f"{kind}:{item}", pod_id=pod_id,
                    detail={"subject_kind": sk, "subject_id": sid, "expires_at": expires_at.isoformat() if expires_at else None})


@router.post("/grants", status_code=201)
async def grant(body: GrantBody, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    sid = body.pod_id if body.subject_kind == "pod" else body.subject_id
    ref = await _validate(svc, body.pod_id, body.kind, body.item_id, body.subject_kind, sid)
    if body.expires_at is not None:
        if body.subject_kind != "user":
            raise Problem(422, "expiry_users_only", "time-limited access can be given to people, not groups")
        now = datetime.now(UTC)
        if not now < body.expires_at <= now + timedelta(days=MAX_GRANT_DAYS):
            raise Problem(422, "invalid_expiry", f"expiry must be in the future and within {MAX_GRANT_DAYS} days")
    if body.kind == "action":
        writes = [T.action_tuple(body.item_id, body.pod_id, ref, body.expires_at)]
    else:
        writes = T.grant_tuples(body.kind, body.pod_id, body.item_id, ref, body.expires_at)
    # Replace any existing grant for the same subject so the expiry is exactly what was asked for.
    obj, rel = _object_for(body.kind, body.pod_id, body.item_id)
    await svc.fga.write(deletes=[Tuple(ref, rel, obj)])
    await svc.fga.write(writes=writes)
    await _ledger(svc, admin.subject_id, "grant", body.pod_id, body.kind, body.item_id, body.subject_kind, sid,
                  body.expires_at, body.reason)
    return {"status": "granted"}


@router.post("/grants/revoke")
async def revoke(body: RevokeBody, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    sid = body.pod_id if body.subject_kind == "pod" else body.subject_id
    ref = T.subject_ref(body.subject_kind, body.pod_id, sid)
    obj, rel = _object_for(body.kind, body.pod_id, body.item_id)
    if not [t for t in await svc.fga.read(user=ref, relation=rel, object=obj)]:
        raise not_found("grant")
    await svc.fga.write(deletes=[Tuple(ref, rel, obj)])
    await _ledger(svc, admin.subject_id, "revoke", body.pod_id, body.kind, body.item_id, body.subject_kind, sid, None,
                  body.reason)
    return {"status": "revoked"}


class ServiceGrantBody(BaseModel):
    workload_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,60}$")
    capability_id: str
    revoke: bool = False
    reason: str = Field(min_length=3, max_length=500)


@router.post("/service-grants")
async def service_grant(body: ServiceGrantBody, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    """Which tools an agent deployment or background workload may call at all (firm-wide). A person still
    needs their own entitlement: a run can only use capabilities both the user and the agent have."""
    async with svc.db.system_tx() as conn:
        known_agent = await fetchall(conn, "SELECT 1 FROM agent_definitions WHERE agent_id = %s", (body.workload_id,))
        known_cap = await fetchall(conn, "SELECT 1 FROM capability_definitions WHERE capability_id = %s", (body.capability_id,))
    if not known_cap:
        raise Problem(422, "unknown_item", f"no capability called {body.capability_id}")
    if not known_agent and body.workload_id not in svc.settings.workload_secrets():
        raise Problem(422, "unknown_workload", f"no agent or workload called {body.workload_id}")
    t = T.service_grant_tuple(body.workload_id, body.capability_id)
    await svc.fga.write(**({"deletes": [t]} if body.revoke else {"writes": [t]}))
    svc.pdp.invalidate_services()
    action = "service_revoke" if body.revoke else "service_grant"
    async with svc.db.system_tx() as conn:
        await conn.execute(
            """INSERT INTO access_changes(actor, action, pod_id, object_kind, object_id, subject_kind, subject_id, reason)
               VALUES (%s,%s,NULL,'tool',%s,'service',%s,%s)""",
            (admin.subject_id, action, body.capability_id, body.workload_id, body.reason),
        )
    await svc.audit(actor=admin.subject_id, action=f"access.{action}", target=f"tool:{body.capability_id}",
                    detail={"workload_id": body.workload_id})
    return {"status": "revoked" if body.revoke else "granted"}


class GroupBody(BaseModel):
    pod_id: str
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    reason: str = Field(min_length=3, max_length=500)


@router.post("/groups", status_code=201)
async def create_group(body: GroupBody, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    cat = await _catalogue(svc)
    if body.pod_id not in cat["groups"]:
        raise not_found("pod")
    if body.name in cat["groups"][body.pod_id]:
        raise Problem(409, "group_exists", "that group already exists")
    await svc.fga.write(writes=[Tuple(T.pod(body.pod_id), "pod", T.group(body.pod_id, body.name))])
    await _ledger(svc, admin.subject_id, "group_create", body.pod_id, "group", body.name, None, None, None, body.reason)
    return {"status": "created"}


class GroupMemberBody(BaseModel):
    pod_id: str
    group: str
    subject_id: str
    remove: bool = False
    reason: str = Field(min_length=3, max_length=500)


@router.post("/groups/members")
async def group_member(body: GroupMemberBody, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    cat = await _catalogue(svc)
    if body.group not in cat["groups"].get(body.pod_id, []):
        raise not_found("group")
    if not body.remove and not await svc.pdp.is_member(body.pod_id, body.subject_id):
        raise Problem(422, "not_a_pod_member", "add people to groups in pods they belong to")
    t = Tuple(T.user(body.subject_id), "member", T.group(body.pod_id, body.group))
    await svc.fga.write(**({"deletes": [t]} if body.remove else {"writes": [t]}))
    # Keep the directory mirror in step so an IdP re-sync reproduces the same groups.
    async with svc.db.system_tx() as conn:
        await conn.execute(
            f"""UPDATE pod_memberships SET groups = {"array_remove(groups, %s)" if body.remove else
                "(SELECT array_agg(DISTINCT g) FROM unnest(array_append(groups, %s)) g)"}
                WHERE pod_id = %s AND subject_id = %s""",
            (body.group, body.pod_id, body.subject_id),
        )
    await _ledger(svc, admin.subject_id, "group_remove" if body.remove else "group_add", body.pod_id, "group", body.group,
                  "user", body.subject_id, None, body.reason)
    return {"status": "removed" if body.remove else "added"}


@router.get("/changes")
async def changes(pod_id: str | None = None, limit: int = 100, admin: Principal = Depends(require_platform_admin),
                  svc=Depends(services)):
    async with svc.db.system_tx() as conn:
        rows = await fetchall(
            conn,
            f"""SELECT c.*, u.display_name AS actor_name FROM access_changes c LEFT JOIN users u ON u.subject_id = c.actor
                {"WHERE c.pod_id = %s" if pod_id else ""} ORDER BY c.change_id DESC LIMIT %s""",
            ((pod_id, min(limit, 500)) if pod_id else (min(limit, 500),)),
        )
    return [{**r, "occurred_at": r["occurred_at"].isoformat(),
             "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None} for r in rows]


__all__ = ["router"]
