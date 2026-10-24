"""Admin control plane (platform_admin only). Operates on metadata: it has no
route that returns pod research, prompts or messages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import jsonschema
from fastapi import APIRouter, Depends
from podos_contracts.models import PodContext, ReleasePolicy
from pydantic import BaseModel, Field, ValidationError

from ..a2a_adapter import AgentCallError
from ..authz.sync import apply_membership
from ..db import Jsonb, fetchall, fetchone
from ..errors import Problem, conflict, not_found
from ..governance import load_snapshot
from ..security.context import Principal, require_platform_admin, services
from ..seed import register_agent, upsert_release, validate_agent_manifest

router = APIRouter(prefix="/v1/admin")

LIFECYCLE = {
    "draft": {"validating"},
    "validating": {"approved", "draft"},
    "approved": {"pilot", "ga", "retired"},
    "pilot": {"ga", "deprecated", "retired"},
    "ga": {"deprecated"},
    "deprecated": {"retired", "ga"},
    "retired": set(),
}


def _iso(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if k.endswith("_id") and v is not None else v)
            for k, v in row.items()}


@router.get("/overview")
async def overview(admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    out: dict[str, Any] = {"pods": []}
    async with svc.db.system_tx() as conn:
        pods = await fetchall(conn, "SELECT pod_id, display_name FROM pods ORDER BY pod_id")
        denials = await fetchall(
            conn,
            """SELECT action, coalesce(pod_id, '-') AS pod_id, count(*) AS n FROM audit_events
               WHERE outcome = 'denied' AND occurred_at > now() - interval '7 days' GROUP BY 1, 2 ORDER BY 3 DESC""",
        )
        deployments = await fetchall(
            conn, """SELECT d.agent_id, d.implementation_version, d.healthy, d.last_health_at, d.last_health_error
                     FROM agent_deployments d ORDER BY 1, 2""")
    for p in pods:
        async with svc.db.pod_tx(p["pod_id"]) as conn:  # aggregate counts only
            runs = await fetchall(conn, "SELECT state, count(*) AS n FROM agent_runs WHERE pod_id = %s GROUP BY state", (p["pod_id"],))
            outbox = await fetchall(conn, "SELECT state, count(*) AS n FROM outbox_events WHERE pod_id = %s GROUP BY state", (p["pod_id"],))
            waiting = await fetchone(conn, """SELECT count(*) AS n FROM work_items WHERE pod_id = %s
                                              AND state = 'WAITING_FOR_HUMAN'""", (p["pod_id"],))
            deliveries = await fetchall(conn, """SELECT channel, status, count(*) AS n FROM notification_deliveries
                                                 WHERE pod_id = %s GROUP BY 1, 2""", (p["pod_id"],))
        out["pods"].append({
            **p, "runs": {r["state"]: r["n"] for r in runs}, "outbox": {r["state"]: r["n"] for r in outbox},
            "waiting_for_human": waiting["n"] if waiting else 0,
            "deliveries": [dict(d) for d in deliveries],
        })
    out["denials"] = denials
    out["deployments"] = [_iso(d) for d in deployments]
    return out


@router.get("/agents")
async def list_agents(admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    snap = await load_snapshot(svc.db)
    out = []
    for a in sorted(snap.agents.values(), key=lambda a: a.agent_id):
        rk = snap.release_by_agent.get(a.agent_id)
        out.append({
            "agent_id": a.agent_id, "name": a.name, "description": a.description, "owner_team": a.owner_team,
            "publication_status": a.publication_status, "kind": a.kind, "capabilities": sorted(a.capabilities),
            "deployments": [
                {"deployment_id": d.deployment_id, "version": d.version, "service_ref": d.service_ref, "healthy": d.healthy,
                 "card_pinned": bool(d.card_sha256), "trust": d.trust} for d in a.deployments.values()
            ],
            "release_key": rk,
            "release": snap.releases[rk].model_dump(mode="json") if rk else None,
        })
    return out


class AgentRegistration(BaseModel):
    manifest: dict[str, Any]
    kind: Literal["specialist", "background"] = "specialist"
    change_ticket: str = Field(min_length=3, max_length=64)


@router.post("/agents", status_code=201)
async def register(body: AgentRegistration, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    try:
        manifest = validate_agent_manifest(body.manifest)
    except (jsonschema.ValidationError, ValidationError) as exc:
        raise Problem(422, "manifest_invalid", str(exc).splitlines()[0][:300]) from None
    return await register_manifest(svc, manifest, body.kind, admin.subject_id, body.change_ticket)


async def register_manifest(svc, manifest, kind: str, actor: str, change_ticket: str, card: dict | None = None) -> dict[str, Any]:
    """Register (or update) an agent + deployment, pin its Agent Card digest, and create a DRAFT release that
    reaches no pod until an admin releases it. Access still needs an OpenFGA grant (Admin -> Access)."""
    # Publication check: endpoint must serve a well-formed Agent Card. Pin its digest.
    status, digest, card_error = "approved", None, None
    try:
        card, digest = await svc.a2a.fetch_card(manifest.deployment.service_ref, secret_ref=manifest.deployment.auth_secret_ref)
    except AgentCallError as exc:
        status, card_error = "validating", exc.code
    async with svc.db.system_tx() as conn:
        existing = await fetchone(conn, "SELECT publication_status FROM agent_definitions WHERE agent_id = %s",
                                  (manifest.agent_id,))
        manifest = manifest.model_copy(update={"publication_status": existing["publication_status"] if existing else status})
        await register_agent(conn, manifest, kind)
        await conn.execute(
            """INSERT INTO agent_deployments(agent_id, implementation_version, environment, service_ref, protocol, healthy,
                   card_sha256, last_health_at, last_health_error)
               VALUES (%s,%s,'local',%s,%s,%s,%s,now(),%s)
               ON CONFLICT (agent_id, implementation_version, environment) DO UPDATE SET
                 service_ref = excluded.service_ref, healthy = excluded.healthy, card_sha256 = excluded.card_sha256,
                 last_health_at = now(), last_health_error = excluded.last_health_error""",
            (manifest.agent_id, manifest.implementation_version, manifest.deployment.service_ref,
             manifest.deployment.protocol, card_error is None, digest, card_error),
        )
        has_release = await fetchone(conn, "SELECT 1 FROM release_policies WHERE agent_id = %s", (manifest.agent_id,))
        if not has_release:
            rel = ReleasePolicy(
                revision=f"{datetime.now(UTC):%Y-%m-%d}.1", release_key=f"{manifest.agent_id}-release", kind="agent",
                agent_id=manifest.agent_id, agent_version=manifest.implementation_version, stage="DRAFT",
                published=False, stable_salt_id=f"{manifest.agent_id}-{datetime.now(UTC):%Y%m}",
                runtime_canary={"stable_version": manifest.implementation_version},
                notes="Created at registration; no pods until explicitly released.",
            )
            await upsert_release(conn, rel, actor)
    await svc.audit(actor=actor, action="registry.agent_registered", target=manifest.agent_id,
                    detail={"version": manifest.implementation_version, "change_ticket": change_ticket,
                            "card_pinned": bool(digest), "status": status, "trust": manifest.deployment.trust})
    skills = [{"id": sk.get("id"), "name": sk.get("name"), "description": sk.get("description", "")[:300]}
              for sk in (card or {}).get("skills", [])[:10]]
    return {"agent_id": manifest.agent_id, "publication_status": manifest.publication_status, "card_pinned": bool(digest),
            "card_error": card_error, "release_key": f"{manifest.agent_id}-release", "skills": skills}


class ConnectA2A(BaseModel):
    url: str = Field(min_length=8, max_length=300)
    agent_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9.-]{2,63}$")
    owner_team: str = Field(default="External", min_length=2, max_length=80)
    auth_secret_ref: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    change_ticket: str = Field(min_length=3, max_length=64)


def _slug(name: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return (out or "agent")[:48]


@router.post("/agents/connect", status_code=201)
async def connect_a2a(body: ConnectA2A, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    """Connect any standard A2A agent by its base URL. The card supplies name, description, version and
    skills; PodOS treats it as EXTERNAL (question-only, no delegation) and starts it as a draft release."""
    from urllib.parse import urlparse

    u = urlparse(body.url.strip())
    local = u.hostname in {"localhost", "127.0.0.1", "::1"}
    if u.scheme not in {"https", "http"} or not u.hostname or (u.scheme == "http" and not (local and svc.settings.dev_login_enabled)):
        raise Problem(422, "url_not_allowed", "use an https:// agent URL (plain http only for localhost in development)")
    if u.path.endswith("agent-card.json"):
        u = u._replace(path=u.path.rsplit("/.well-known", 1)[0])
    base = u._replace(query="", fragment="").geturl().rstrip("/")
    try:
        card, _ = await svc.a2a.fetch_card(base, timeout=5, secret_ref=body.auth_secret_ref)
    except AgentCallError as exc:
        raise Problem(422, exc.code, "PodOS couldn't read an A2A Agent Card at that URL") from None
    if not card.get("capabilities", {}).get("streaming", False):
        raise Problem(422, "agent_not_streaming", "PodOS needs agents that support streaming (capabilities.streaming)")
    manifest = validate_agent_manifest({
        "schema_version": "1.0",
        "agent_id": body.agent_id or _slug(str(card.get("name", "agent"))),
        "name": str(card.get("name", "External agent"))[:120],
        "description": str(card.get("description", ""))[:1000],
        "owner_team": body.owner_team,
        "implementation_version": str(card.get("version") or "1.0.0")[:32],
        "capabilities": [],
        "allowed_tools": [],
        "data_classification_ceiling": "internal",
        "supported_run_modes": ["interactive"],
        "deployment": {"protocol": "a2a", "service_ref": base, "trust": "external", "stream_profile": "a2a",
                       **({"auth_secret_ref": body.auth_secret_ref} if body.auth_secret_ref else {})},
    })
    return await register_manifest(svc, manifest, "specialist", admin.subject_id, body.change_ticket, card)


class StatusChange(BaseModel):
    publication_status: Literal["draft", "validating", "approved", "pilot", "ga", "deprecated", "retired"]
    change_ticket: str = Field(min_length=3, max_length=64)


@router.patch("/agents/{agent_id}/status")
async def change_status(agent_id: str, body: StatusChange, admin: Principal = Depends(require_platform_admin),
                        svc=Depends(services)):
    async with svc.db.system_tx() as conn:
        a = await fetchone(conn, "SELECT publication_status FROM agent_definitions WHERE agent_id = %s FOR UPDATE", (agent_id,))
        if a is None:
            raise not_found("agent")
        if body.publication_status not in LIFECYCLE[a["publication_status"]]:
            raise conflict("invalid_transition", f"{a['publication_status']} -> {body.publication_status} not allowed")
        await conn.execute("UPDATE agent_definitions SET publication_status = %s, updated_at = now() WHERE agent_id = %s",
                           (body.publication_status, agent_id))
    await svc.audit(actor=admin.subject_id, action="registry.status_changed", target=agent_id,
                    detail={"from": a["publication_status"], "to": body.publication_status, "change_ticket": body.change_ticket})
    return {"agent_id": agent_id, "publication_status": body.publication_status}


@router.post("/deployments/{deployment_id}/health-check")
async def health_check(deployment_id: str, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    async with svc.db.system_tx() as conn:
        d = await fetchone(conn, "SELECT * FROM agent_deployments WHERE deployment_id::text = %s", (deployment_id,))
    if d is None:
        raise not_found("deployment")
    healthy, err = True, None
    async with svc.db.system_tx() as conn:
        agent = await fetchone(conn, "SELECT manifest FROM agent_definitions WHERE agent_id = %s", (d["agent_id"],))
    secret_ref = ((agent or {}).get("manifest") or {}).get("deployment", {}).get("auth_secret_ref")
    try:
        _, digest = await svc.a2a.fetch_card(d["service_ref"], secret_ref=secret_ref)
        if d["card_sha256"] and digest != d["card_sha256"]:
            healthy, err = False, "agent_card_mismatch"
    except AgentCallError as exc:
        healthy, err = False, exc.code
    async with svc.db.system_tx() as conn:
        await conn.execute(
            """UPDATE agent_deployments SET healthy = %s, last_health_at = now(), last_health_error = %s
               WHERE deployment_id::text = %s""", (healthy, err, deployment_id))
    return {"healthy": healthy, "error": err}


@router.get("/releases")
async def releases(admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    async with svc.db.system_tx() as conn:
        rows = await fetchall(conn, "SELECT release_key, kind, config, revision, updated_at, updated_by FROM release_policies ORDER BY kind, release_key")
    return [_iso(r) for r in rows]


class ReleasePatch(BaseModel):
    change_ticket: str = Field(min_length=3, max_length=64)
    approver: str = Field(min_length=2, max_length=64)
    stage: str | None = None
    published: bool | None = None
    pod_allowlist: list[str] | None = None
    eligible_groups: list[str] | None = None
    feature_flags: dict[str, bool] | None = None
    user_fraction_percent: int | None = Field(default=None, ge=0, le=100)
    runtime_canary: dict[str, Any] | None = None
    notes: str | None = None


async def _apply_release_change(svc, key: str, actor: str, ticket: str, approver: str, changes: dict[str, Any]) -> ReleasePolicy:
    async with svc.db.system_tx() as conn:
        row = await fetchone(conn, "SELECT config FROM release_policies WHERE release_key = %s FOR UPDATE", (key,))
        if row is None:
            raise not_found("release")
        before = ReleasePolicy.model_validate(row["config"])
        data = before.model_dump(mode="json")
        if "feature_flags" in changes:
            data["feature_flags"] = {**data["feature_flags"], **changes.pop("feature_flags")}
        data.update(changes)
        base, _, n = before.revision.rpartition(".")
        today = f"{datetime.now(UTC):%Y-%m-%d}"
        data["revision"] = f"{today}.{int(n) + 1 if base == today and n.isdigit() else 1}"
        try:
            after = ReleasePolicy.model_validate(data)
        except ValidationError as exc:
            raise Problem(422, "release_invalid", str(exc).splitlines()[0][:300]) from None
        async with svc.db.system_tx() as c2:
            pods = {r["pod_id"] for r in await fetchall(c2, "SELECT pod_id FROM pods")}
        if set(after.pod_allowlist) - pods:
            raise Problem(422, "unknown_pod", "pod allowlist contains unknown pods")
        await upsert_release(conn, after, actor)
    await svc.audit(actor=actor, action="release.updated", target=key, detail={
        "change_ticket": ticket, "approver": approver, "revision": after.revision, "stage": after.stage,
        "pods": after.pod_allowlist, "flags": [k for k, v in after.feature_flags.model_dump().items() if v],
        "canary": (after.runtime_canary.candidate_fraction_percent if after.runtime_canary else 0),
    })
    return after


@router.get("/releases/{key}")
async def get_release(key: str, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    async with svc.db.system_tx() as conn:
        row = await fetchone(conn, "SELECT * FROM release_policies WHERE release_key = %s", (key,))
        history = await fetchall(
            conn, """SELECT occurred_at, actor, detail FROM audit_events WHERE action IN ('release.updated','release.emergency')
                     AND target = %s ORDER BY occurred_at DESC LIMIT 20""", (key,))
    if row is None:
        raise not_found("release")
    return {**_iso(row), "history": [_iso(h) for h in history]}


@router.patch("/releases/{key}")
async def patch_release(key: str, body: ReleasePatch, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    changes = body.model_dump(exclude_none=True, exclude={"change_ticket", "approver"})
    rel = await _apply_release_change(svc, key, admin.subject_id, body.change_ticket, body.approver, changes)
    return rel.model_dump(mode="json")


class EmergencyBody(BaseModel):
    disabled: bool
    change_ticket: str = Field(min_length=3, max_length=64)


@router.post("/releases/{key}/emergency")
async def emergency(key: str, body: EmergencyBody, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    rel = await _apply_release_change(svc, key, admin.subject_id, body.change_ticket, admin.subject_id,
                                      {"feature_flags": {"emergency_disabled": body.disabled}})
    await svc.audit(actor=admin.subject_id, action="release.emergency", target=key,
                    detail={"disabled": body.disabled, "change_ticket": body.change_ticket})
    return rel.model_dump(mode="json")


class PreviewBody(BaseModel):
    identities: list[dict[str, str]] | None = None


@router.post("/releases/{key}/preview")
async def preview(key: str, body: PreviewBody, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    """Dry-run effective eligibility for sample identities (all memberships by default)."""
    snap = await load_snapshot(svc.db)
    rel = snap.releases.get(key)
    if rel is None:
        raise not_found("release")
    async with svc.db.system_tx() as conn:
        pods = [r["pod_id"] for r in await fetchall(conn, "SELECT pod_id FROM pods ORDER BY pod_id")]
        names = {r["subject_id"]: r["display_name"] for r in await fetchall(conn, "SELECT subject_id, display_name FROM users")}
    wanted = {(i.get("subject_id"), i.get("pod_id")) for i in body.identities or []}
    out = []
    for pod_id in pods:
        for subject_id, _ in sorted(await svc.pdp.members(pod_id)):
            if wanted and (subject_id, pod_id) not in wanted:
                continue
            res = await svc.pdp.resolve(subject_id, pod_id)
            if res is None:
                continue
            ctx = PodContext(subject_id=subject_id, active_pod_id=pod_id, pod_role=res.pod_role, groups=res.groups,
                             entitlements=res.entitlements, session_id="preview", permissions_revision=svc.pdp.revision,
                             request_id="preview", trace_id="preview")
            out.append(_preview_row(svc, snap, rel, ctx, names.get(subject_id, subject_id)))
    return {"release_key": key, "revision": rel.revision, "results": out}


def _preview_row(svc, snap, rel, ctx: PodContext, display_name: str) -> dict[str, Any]:
    row: dict[str, Any] = {"subject_id": ctx.subject_id, "display_name": display_name, "pod_id": ctx.active_pod_id}
    if rel.kind == "agent" and rel.agent_id:
        av = svc.gov.agent_availability(ctx, snap, rel.agent_id)
        assert av is not None
        row.update({
            "entitled": av.entitled, "catalogue": av.catalogue_visible,
            "direct": av.allows("direct_invoke")[0], "auto": av.allows("auto_route")[0],
            "background": av.allows("background_execution")[0], "proactive": av.allows("proactive_alerts")[0],
            "version": av.version, "variant": av.version_variant, "reason": av.release_reason,
        })
    else:
        ok, why = svc.gov.capability_status(ctx, snap, rel.capability_id or "")
        row.update({"entitled": svc.pdp.capability_entitled(ctx, rel.capability_id or ""),
                    "enabled": ok, "reason": why})
    return row


@router.get("/audit")
async def audit_log(admin: Principal = Depends(require_platform_admin), svc=Depends(services), limit: int = 100):
    async with svc.db.system_tx() as conn:
        rows = await fetchall(conn, "SELECT * FROM audit_events ORDER BY audit_id DESC LIMIT %s", (min(limit, 500),))
    return [_iso(r) for r in rows]


class MembershipChange(BaseModel):
    pod_id: str
    subject_id: str
    revoke: bool = True


@router.post("/iam/memberships")
async def iam_membership(body: MembershipChange, admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    """Dev-only IAM simulator (J6 pod transfer). In production this is driven by the IdP."""
    if not svc.settings.dev_login_enabled:
        raise not_found()
    async with svc.db.system_tx() as conn:
        row = await fetchone(
            conn,
            """UPDATE pod_memberships SET revoked_at = CASE WHEN %s THEN now() END WHERE pod_id = %s AND subject_id = %s
               RETURNING pod_role, groups""",
            (body.revoke, body.pod_id, body.subject_id),
        )
    if row is None:
        raise not_found("membership")
    # The directory row is the IdP mirror; OpenFGA is what every decision reads.
    await apply_membership(svc.fga, body.pod_id, body.subject_id, row["pod_role"], list(row["groups"]), revoke=body.revoke)
    await svc.audit(actor=admin.subject_id, action="iam.membership_revoked" if body.revoke else "iam.membership_restored",
                    target=body.subject_id, pod_id=body.pod_id)
    return {"status": "revoked" if body.revoke else "restored"}


@router.get("/iam/memberships")
async def iam_list(admin: Principal = Depends(require_platform_admin), svc=Depends(services)):
    async with svc.db.system_tx() as conn:
        rows = await fetchall(conn, """SELECT m.pod_id, m.subject_id, u.display_name, m.pod_role, m.groups, m.revoked_at
                                       FROM pod_memberships m JOIN users u USING (subject_id) ORDER BY m.pod_id, m.subject_id""")
    return [_iso(r) for r in rows]


__all__ = ["router", "Jsonb"]
