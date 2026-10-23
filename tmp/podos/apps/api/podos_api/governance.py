"""Control-plane snapshot + the effective permission algorithm (ARCHITECTURE.md 4.2).

    allow = verified_user_is_active_pod_member            (PodContext built from live membership)
        AND resource_belongs_to_active_pod                (RLS + explicit pod filters at call sites)
        AND agent_is_published_and_healthy
        AND user_can_invoke_agent                         (entitlement AND release flag)
        AND agent_supports_capability                     (registry)
        AND user_can_execute_capability                   (entitlement)
        AND agent_service_is_allowed_to_call_capability   (service grant)
        AND release_flag_enabled_for_user_and_pod         (capability release)
        AND resource_classification_and_purpose_policy_allows
        AND NOT emergency_disabled

Every check runs at dispatch / execution time, never only in the UI.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

from podos_contracts.models import CLASSIFICATION_RANK, Classification, PodContext, ReleasePolicy

from .authz.pdp import AccessDecisionPoint
from .db import Database, fetchall
from .errors import forbidden
from .releases import ReleaseEvaluator, bind_releases

INVOCABLE_STATUSES = {"pilot", "ga", "deprecated"}
CATALOGUE_STATUSES = {"pilot", "ga"}
AgentAction = Literal["direct_invoke", "auto_route", "background_execution", "proactive_alerts"]
AGENT_FLAGS = ["catalogue_visible", "direct_invoke", "auto_route", "background_execution", "proactive_alerts"]


@dataclass
class DeploymentRecord:
    deployment_id: str
    agent_id: str
    version: str
    service_ref: str
    protocol: str
    healthy: bool
    card_sha256: str | None
    trust: str = "platform"
    stream_profile: str = "podos"
    auth_secret_ref: str | None = None


@dataclass
class AgentRecord:
    agent_id: str
    name: str
    description: str
    owner_team: str
    publication_status: str
    kind: str
    manifest: dict
    capabilities: set[str] = field(default_factory=set)
    deployments: dict[str, DeploymentRecord] = field(default_factory=dict)


@dataclass
class CapabilityRecord:
    capability_id: str
    description: str
    side_effect_class: str
    requires_approval: bool
    result_type: str
    owner_team: str


@dataclass
class Snapshot:
    agents: dict[str, AgentRecord]
    capabilities: dict[str, CapabilityRecord]
    releases: dict[str, ReleasePolicy]
    release_by_agent: dict[str, str]
    release_by_capability: dict[str, str]

    @property
    def revision(self) -> str:
        joined = "|".join(f"{k}@{r.revision}" for k, r in sorted(self.releases.items()))
        return "rel-" + hashlib.sha256(joined.encode()).hexdigest()[:12]


async def load_snapshot(db: Database) -> Snapshot:
    async with db.system_tx() as conn:
        agents_rows = await fetchall(conn, "SELECT * FROM agent_definitions")
        deps = await fetchall(conn, "SELECT * FROM agent_deployments WHERE environment = 'local'")
        caps = await fetchall(conn, "SELECT * FROM capability_definitions")
        support = await fetchall(conn, "SELECT * FROM agent_capability_support")
        rels = await fetchall(conn, "SELECT release_key, config FROM release_policies")
    agents = {
        r["agent_id"]: AgentRecord(
            agent_id=r["agent_id"], name=r["name"], description=r["description"], owner_team=r["owner_team"],
            publication_status=r["publication_status"], kind=r["kind"], manifest=r["manifest"],
        )
        for r in agents_rows
    }
    for d in deps:
        if d["agent_id"] in agents:
            spec = (agents[d["agent_id"]].manifest or {}).get("deployment", {})
            agents[d["agent_id"]].deployments[d["implementation_version"]] = DeploymentRecord(
                deployment_id=str(d["deployment_id"]), agent_id=d["agent_id"], version=d["implementation_version"],
                service_ref=d["service_ref"], protocol=d["protocol"], healthy=d["healthy"], card_sha256=d["card_sha256"],
                trust=spec.get("trust", "platform"), stream_profile=spec.get("stream_profile", "podos"),
                auth_secret_ref=spec.get("auth_secret_ref"),
            )
    for s in support:
        if s["agent_id"] in agents:
            agents[s["agent_id"]].capabilities.add(s["capability_id"])
    capabilities = {
        c["capability_id"]: CapabilityRecord(
            capability_id=c["capability_id"], description=c["description"], side_effect_class=c["side_effect_class"],
            requires_approval=c["requires_approval"], result_type=c["result_type"], owner_team=c["owner_team"],
        )
        for c in caps
    }
    releases = {r["release_key"]: ReleasePolicy.model_validate(r["config"]) for r in rels}
    snap = Snapshot(
        agents=agents,
        capabilities=capabilities,
        releases=releases,
        release_by_agent={r.agent_id: k for k, r in releases.items() if r.kind == "agent" and r.agent_id},
        release_by_capability={r.capability_id: k for k, r in releases.items() if r.kind == "capability" and r.capability_id},
    )
    bind_releases(releases)
    return snap


@dataclass
class AgentAvailability:
    agent_id: str
    name: str
    description: str
    kind: str
    owner_team: str
    publication_status: str
    entitled: bool
    healthy: bool
    release_key: str | None
    release_stage: str | None
    release_reason: str
    emergency_disabled: bool
    flags: dict[str, bool]
    version: str | None
    version_variant: str
    deployment: DeploymentRecord | None
    capabilities: list[str]

    def allows(self, action: AgentAction) -> tuple[bool, str]:
        if self.emergency_disabled:
            return False, "agent_emergency_disabled"
        if self.publication_status not in INVOCABLE_STATUSES:
            return False, "agent_not_published"
        if not self.entitled:
            return False, "agent_not_entitled"
        if not self.flags.get(action, False):
            return False, f"release_{action}_disabled"
        if self.deployment is None:
            return False, "agent_deployment_missing"
        if not self.healthy:
            return False, "agent_unhealthy"
        return True, "ok"

    @property
    def catalogue_visible(self) -> bool:
        return (
            self.entitled
            and not self.emergency_disabled
            and self.publication_status in CATALOGUE_STATUSES
            and self.flags.get("catalogue_visible", False)
        )


@dataclass
class CapabilityGrant:
    """Proof that the full permission algorithm passed. Required by CapabilityService."""

    capability_id: str
    pod_id: str
    subject_id: str
    via_agent: str | None
    via_workload: str | None
    classification: Classification


class Governance:
    def __init__(self, pdp: AccessDecisionPoint, evaluator: ReleaseEvaluator) -> None:
        self.pdp = pdp
        self.evaluator = evaluator

    # -- agents --------------------------------------------------------------
    def agent_availability(self, ctx: PodContext, snap: Snapshot, agent_id: str) -> AgentAvailability | None:
        bind_releases(snap.releases)  # the OpenFeature provider evaluates this exact snapshot
        agent = snap.agents.get(agent_id)
        if agent is None:
            return None
        rk = snap.release_by_agent.get(agent_id)
        rel = snap.releases.get(rk) if rk else None
        flags = {f: False for f in AGENT_FLAGS}
        reason = "no_release"
        version, variant = None, "none"
        if rel is not None:
            for f in AGENT_FLAGS:
                flags[f], why = self.evaluator.flag(rk, f, subject_id=ctx.subject_id, pod_id=ctx.active_pod_id, groups=ctx.groups)
                if f == "catalogue_visible":
                    reason = why
            version, variant = self.evaluator.version(
                rk, subject_id=ctx.subject_id, pod_id=ctx.active_pod_id, groups=ctx.groups,
                default=rel.agent_version or "",
            )
        deployment = agent.deployments.get(version or "") if version else None
        return AgentAvailability(
            agent_id=agent.agent_id, name=agent.name, description=agent.description, kind=agent.kind,
            owner_team=agent.owner_team, publication_status=agent.publication_status,
            entitled=self.pdp.agent_entitled(ctx, agent_id),
            healthy=bool(deployment and deployment.healthy),
            release_key=rk, release_stage=rel.stage if rel else None, release_reason=reason,
            emergency_disabled=bool(rel and rel.feature_flags.emergency_disabled),
            flags=flags, version=version, version_variant=variant, deployment=deployment,
            capabilities=sorted(agent.capabilities),
        )

    def catalogue(self, ctx: PodContext, snap: Snapshot) -> list[AgentAvailability]:
        out = []
        for agent_id in sorted(snap.agents):
            av = self.agent_availability(ctx, snap, agent_id)
            if av and av.catalogue_visible:
                out.append(av)
        return out

    def require_agent(self, ctx: PodContext, snap: Snapshot, agent_id: str, action: AgentAction) -> AgentAvailability:
        av = self.agent_availability(ctx, snap, agent_id)
        if av is None:
            raise forbidden("agent_not_available", "agent is not available in this pod")
        ok, why = av.allows(action)
        if not ok:
            raise forbidden("agent_not_available", "agent is not available in this pod", reason=why)
        return av

    def auto_candidates(self, ctx: PodContext, snap: Snapshot) -> list[AgentAvailability]:
        """Pre-filtered eligible set handed to the Auto router (never the whole registry)."""
        out = []
        for agent_id, agent in sorted(snap.agents.items()):
            if agent.kind != "specialist":
                continue
            av = self.agent_availability(ctx, snap, agent_id)
            if av and av.allows("auto_route")[0]:
                out.append(av)
        return out

    # -- capabilities --------------------------------------------------------
    def capability_status(self, ctx: PodContext, snap: Snapshot, capability_id: str) -> tuple[bool, str]:
        bind_releases(snap.releases)
        if capability_id not in snap.capabilities:
            return False, "capability_unknown"
        if not self.pdp.capability_entitled(ctx, capability_id):
            return False, "capability_not_entitled"
        rk = snap.release_by_capability.get(capability_id)
        if not rk:
            return False, "capability_not_released"
        ok, why = self.evaluator.flag(rk, "capability_enabled", subject_id=ctx.subject_id, pod_id=ctx.active_pod_id, groups=ctx.groups)
        return (True, "ok") if ok else (False, f"capability_{why}")

    def require_capability(
        self,
        ctx: PodContext,
        snap: Snapshot,
        capability_id: str,
        *,
        classification: Classification,
        via_agent: str | None = None,
        via_workload: str | None = None,
    ) -> CapabilityGrant:
        if via_agent is not None:
            av = self.agent_availability(ctx, snap, via_agent)
            if av is None:
                raise forbidden("capability_denied", reason="agent_unknown")
            if av.emergency_disabled:
                raise forbidden("capability_denied", reason="agent_emergency_disabled")
            if av.publication_status not in INVOCABLE_STATUSES or not av.healthy:
                raise forbidden("capability_denied", reason="agent_not_published_or_unhealthy")
            if not av.entitled or not any(av.flags.get(a) for a in ("direct_invoke", "auto_route", "background_execution")):
                raise forbidden("capability_denied", reason="agent_not_invocable_by_user")
            if capability_id not in av.capabilities:
                raise forbidden("capability_denied", reason="agent_does_not_support_capability")
            if not self.pdp.agent_service_may_call(via_agent, capability_id):
                raise forbidden("capability_denied", reason="agent_service_not_granted")
        if via_workload is not None and not self.pdp.workflow_service_may_call(via_workload, capability_id):
            raise forbidden("capability_denied", reason="workload_not_granted")
        ok, why = self.capability_status(ctx, snap, capability_id)
        if not ok:
            raise forbidden("capability_denied", reason=why)
        if CLASSIFICATION_RANK[classification] > CLASSIFICATION_RANK[self.pdp.clearance(ctx)]:
            raise forbidden("capability_denied", reason="classification_exceeds_clearance")
        return CapabilityGrant(
            capability_id=capability_id, pod_id=ctx.active_pod_id, subject_id=ctx.subject_id,
            via_agent=via_agent, via_workload=via_workload, classification=classification,
        )
