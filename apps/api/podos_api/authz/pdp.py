"""Access decision point backed by OpenFGA.

``resolve()`` is called whenever a PodContext is built (every request, stream poll, worker step and
delegated tool call). It asks OpenFGA everything the subject may do in that pod in one round of
concurrent calls and stores the answer on the context, so governance checks downstream are
synchronous and consistent within a request, and a revoked tuple is honoured on the next request.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

from podos_contracts.models import Classification, Entitlements, PodContext

from . import tuples as T
from .fga import FGAClient

SERVICE_GRANT_TTL = 2.0  # seconds; service (agent -> tool) grants change only on deployment/admin action


@dataclass
class Resolution:
    pod_role: str
    groups: list[str]
    entitlements: Entitlements


class AccessDecisionPoint:
    def __init__(self, fga: FGAClient, capability_manifest_dir: Path) -> None:
        self.fga = fga
        self._manifest_dir = capability_manifest_dir
        self._tools: set[str] | None = None
        self._service_grants: dict[str, set[str]] = {}
        self._service_loaded_at = 0.0

    @property
    def revision(self) -> str:
        return f"openfga:{self.fga.model_id or 'pending'}"

    # ---- async resolution ---------------------------------------------------------------------
    async def resolve(self, subject_id: str, pod_id: str) -> Resolution | None:
        u, p = T.user(subject_id), T.pod(pod_id)
        checks = self.fga.batch_check([(u, "member", p), (u, "pm", p), (u, "can_approve", p), (u, "can_read_restricted", p)])
        lists = [self.fga.list_objects(u, "member", "group")] + [
            self.fga.list_objects(u, T.DECISION_RELATION[k], k) for k in T.GRANT_KINDS
        ]
        flags, groups, agents, caps, datasets, features, _ = await asyncio.gather(checks, *lists, self._refresh_services())
        member, is_pm, can_approve, restricted = flags
        if not member:
            return None

        def mine(objs: list[str]) -> list[str]:
            return sorted(x for x in (T.local_id(o, pod_id) for o in objs) if x)

        actions = (["work_item.approve"] if can_approve else []) + (["data.read_restricted"] if restricted else [])
        return Resolution(
            pod_role="pm" if is_pm else "analyst",
            groups=mine(groups),
            entitlements=Entitlements(
                agents=mine(agents), capabilities=mine(caps), datasets=mine(datasets), features=mine(features),
                actions=actions,
                clearance=Classification.restricted if restricted else Classification.pod_confidential,
            ),
        )

    async def is_member(self, pod_id: str, subject_id: str) -> bool:
        return await self.fga.check(T.user(subject_id), "member", T.pod(pod_id))

    async def is_platform_admin(self, subject_id: str) -> bool:
        return await self.fga.check(T.user(subject_id), "admin", T.PLATFORM)

    async def members(self, pod_id: str) -> list[tuple[str, str]]:
        """(subject_id, pod_role) for every direct role tuple on the pod."""
        rows = await self.fga.read(object=T.pod(pod_id))
        return [(t.user.removeprefix("user:"), t.relation) for t in rows if t.relation in ("pm", "analyst")]

    async def _refresh_services(self) -> None:
        if time.monotonic() - self._service_loaded_at < SERVICE_GRANT_TTL:
            return
        grants: dict[str, set[str]] = {}
        reads = await asyncio.gather(*(self.fga.read(object=f"tool:{t}") for t in sorted(self.tools())))
        for rows in reads:
            for t in rows:
                if t.relation == "caller":
                    grants.setdefault(t.user.removeprefix("service:"), set()).add(t.object.removeprefix("tool:"))
        self._service_grants = grants
        self._service_loaded_at = time.monotonic()

    def invalidate_services(self) -> None:
        self._service_loaded_at = 0.0

    def tools(self) -> set[str]:
        if self._tools is None:
            self._tools = {
                json.loads(f.read_text())["capability_id"] for f in sorted(self._manifest_dir.glob("*.json"))
            }
        return self._tools

    # ---- synchronous checks on a resolved context ----------------------------------------------
    def agent_entitled(self, ctx: PodContext, agent_id: str) -> bool:
        return agent_id in ctx.entitlements.agents

    def capability_entitled(self, ctx: PodContext, capability_id: str) -> bool:
        return capability_id in ctx.entitlements.capabilities

    def action_entitled(self, ctx: PodContext, action: str) -> bool:
        return action in ctx.entitlements.actions

    def feature_enabled(self, ctx: PodContext, feature: str) -> bool:
        return feature in ctx.entitlements.features

    def dataset_readable(self, ctx: PodContext, dataset: str) -> bool:
        return dataset in ctx.entitlements.datasets

    def entitled_capabilities(self, ctx: PodContext) -> set[str]:
        return set(ctx.entitlements.capabilities)

    def clearance(self, ctx: PodContext) -> Classification:
        return ctx.entitlements.clearance

    def agent_service_may_call(self, agent_id: str, capability_id: str) -> bool:
        return capability_id in self._service_grants.get(agent_id, set())

    def workflow_service_may_call(self, workload_id: str, capability_id: str) -> bool:
        return capability_id in self._service_grants.get(workload_id, set())
