"""Composition root shared by the API process and the worker process."""

from __future__ import annotations

from typing import Any

from . import audit as audit_mod
from .a2a_adapter import A2AAdapter
from .authz.fga import FGAClient
from .authz.pdp import AccessDecisionPoint
from .capabilities.service import CapabilityService
from .db import Database
from .governance import Governance
from .objectstore import make_store
from .policy import OpsPolicyStore
from .releases import ReleaseEvaluator
from .search import PodResearchIndex, make_backend
from .settings import Settings


class Services:
    def __init__(self, settings: Settings) -> None:
        from .runs import RunManager

        self.settings = settings
        self.db = Database(settings.database_url)
        self.fga = FGAClient(settings.fga_url, settings.fga_api_key, settings.fga_store)
        self.pdp = AccessDecisionPoint(self.fga, settings.config_dir / "manifests" / "capabilities")
        self.ops = OpsPolicyStore(settings.policy_path)
        self.gov = Governance(self.pdp, ReleaseEvaluator())
        self.store = make_store(settings)
        self.search_backend = make_backend(settings, self.db)
        self.capabilities = CapabilityService(self.db, self.store)
        self.a2a = A2AAdapter(settings)
        self.runs = RunManager(self)

    def index(self, pod_id: str) -> PodResearchIndex:
        """Only call with a pod id taken from a validated PodContext / delegation."""
        return PodResearchIndex(self.db, self.search_backend, pod_id)

    async def audit(self, **kw: Any) -> None:
        await audit_mod.record(self.db, **kw)

    async def open(self) -> None:
        await self.db.open()
        await self.fga.ensure_ready()

    async def close(self) -> None:
        await self.db.close()
        await self.fga.close()
