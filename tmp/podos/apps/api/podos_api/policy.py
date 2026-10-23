"""Operational policy: per-pod quotas, hot-reloaded from a versioned JSON file.

Authorization (who may use which agent, capability, dataset or feature) is NOT here: it lives in
OpenFGA, see ``authz/``. Quotas are operational limits, not permissions.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OpsPolicy:
    revision: str
    pod_quotas: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OpsPolicy:
        if data.get("schema_version") not in {"1.1"}:
            raise ValueError("unsupported policy schema_version")
        return cls(revision=data["revision"], pod_quotas=data.get("pod_quotas", {}))


class OpsPolicyStore:
    """Loads the policy file and hot-reloads on change (new revision, no redeploy)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mtime: float = -1
        self._doc: OpsPolicy | None = None

    def current(self) -> OpsPolicy:
        mtime = self.path.stat().st_mtime
        with self._lock:
            if self._doc is None or mtime != self._mtime:
                self._doc = OpsPolicy.from_json(json.loads(self.path.read_text()))
                self._mtime = mtime
            return self._doc

    def quotas(self, pod_id: str) -> dict[str, int]:
        q = self.current().pod_quotas
        return {**q.get("default", {}), **q.get(pod_id, {})}
