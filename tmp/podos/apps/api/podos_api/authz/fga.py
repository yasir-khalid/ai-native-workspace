"""Minimal async OpenFGA HTTP client (stores, models, tuples, check, batch-check, list-objects).

Only the Workspace API and worker hold the pre-shared key; agents never talk to OpenFGA.
Every read uses HIGHER_CONSISTENCY so a revoked tuple is honoured on the very next decision,
even if OpenFGA's check cache is turned on later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

MODEL_PATH = Path(__file__).resolve().parents[4] / "infra" / "openfga" / "model.json"
MAX_TUPLES_PER_WRITE = 100
CONSISTENCY = "HIGHER_CONSISTENCY"


class FGAError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tuple:
    user: str
    relation: str
    object: str
    expires_at: datetime | None = None  # time-bound grant (not_expired condition)

    def key(self) -> dict[str, Any]:
        k: dict[str, Any] = {"user": self.user, "relation": self.relation, "object": self.object}
        if self.expires_at is not None:
            k["condition"] = {"name": "not_expired", "context": {"expires_at": _ts(self.expires_at)}}
        return k

    def ident(self) -> tuple[str, str, str]:
        return (self.user, self.relation, self.object)


def _ts(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_context() -> dict[str, str]:
    return {"current_time": _ts(datetime.now(UTC))}


class FGAClient:
    def __init__(self, url: str, api_key: str, store_name: str, timeout: float = 5.0) -> None:
        self.url = url.rstrip("/")
        self.store_name = store_name
        self._http = httpx.AsyncClient(
            base_url=self.url, timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}
        )
        self.store_id: str | None = None
        self.model_id: str | None = None

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None, params: dict | None = None) -> dict:
        try:
            r = await self._http.request(method, path, json=body, params=params)
        except httpx.HTTPError as e:
            raise FGAError(f"openfga unreachable: {e}") from e
        if r.status_code >= 400:
            raise FGAError(f"openfga {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # ---- bootstrap ----------------------------------------------------------------------------
    async def ensure_ready(self) -> None:
        """Find (or create) the store by name and make sure the latest model equals model.json."""
        if self.store_id and self.model_id:
            return
        stores, token = [], ""
        while True:
            page = await self._call("GET", "/stores", params={"page_size": 100, **({"continuation_token": token} if token else {})})
            stores += page.get("stores", [])
            token = page.get("continuation_token") or ""
            if not token:
                break
        match = [s for s in stores if s["name"] == self.store_name]
        self.store_id = match[0]["id"] if match else (await self._call("POST", "/stores", {"name": self.store_name}))["id"]
        model = json.loads(MODEL_PATH.read_text())
        latest = (await self._call("GET", f"/stores/{self.store_id}/authorization-models", params={"page_size": 1})).get(
            "authorization_models", []
        )
        if latest and _same_model(latest[0], model):
            self.model_id = latest[0]["id"]
        else:
            self.model_id = (await self._call("POST", f"/stores/{self.store_id}/authorization-models", model))[
                "authorization_model_id"
            ]

    def _base(self) -> str:
        if not self.store_id:
            raise FGAError("FGA client not initialised (call ensure_ready)")
        return f"/stores/{self.store_id}"

    # ---- tuples -------------------------------------------------------------------------------
    async def write(self, writes: list[Tuple] = (), deletes: list[Tuple] = ()) -> None:  # type: ignore[assignment]
        await self.ensure_ready()
        writes, deletes = list(writes), list(deletes)
        while writes or deletes:
            w, writes = writes[:MAX_TUPLES_PER_WRITE], writes[MAX_TUPLES_PER_WRITE:]
            room = MAX_TUPLES_PER_WRITE - len(w)
            d, deletes = deletes[:room], deletes[room:]
            body: dict[str, Any] = {"authorization_model_id": self.model_id}
            if w:
                body["writes"] = {"tuple_keys": [t.key() for t in w], "on_duplicate": "ignore"}
            if d:
                body["deletes"] = {
                    "tuple_keys": [{"user": t.user, "relation": t.relation, "object": t.object} for t in d],
                    "on_missing": "ignore",
                }
            await self._call("POST", f"{self._base()}/write", body)

    async def read(self, user: str | None = None, relation: str | None = None, object: str | None = None) -> list[Tuple]:
        """Read stored tuples. `object` may be a full id or, together with `user`, just a type ("agent:")."""
        await self.ensure_ready()
        key = {k: v for k, v in {"user": user, "relation": relation, "object": object}.items() if v}
        out: list[Tuple] = []
        token = ""
        while True:
            body: dict[str, Any] = {"page_size": 100, "consistency": CONSISTENCY}
            if key:
                body["tuple_key"] = key
            if token:
                body["continuation_token"] = token
            page = await self._call("POST", f"{self._base()}/read", body)
            for t in page.get("tuples", []):
                k = t["key"]
                exp = (k.get("condition") or {}).get("context", {}).get("expires_at")
                out.append(Tuple(k["user"], k["relation"], k["object"],
                                 datetime.fromisoformat(exp.replace("Z", "+00:00")) if exp else None))
            token = page.get("continuation_token") or ""
            if not token:
                return out

    # ---- queries ------------------------------------------------------------------------------
    async def check(self, user: str, relation: str, object: str) -> bool:
        await self.ensure_ready()
        r = await self._call("POST", f"{self._base()}/check", {
            "authorization_model_id": self.model_id, "consistency": CONSISTENCY, "context": now_context(),
            "tuple_key": {"user": user, "relation": relation, "object": object},
        })
        return bool(r.get("allowed"))

    async def batch_check(self, checks: list[tuple[str, str, str]]) -> list[bool]:
        await self.ensure_ready()
        if not checks:
            return []
        ctx = now_context()
        body = {
            "authorization_model_id": self.model_id, "consistency": CONSISTENCY,
            "checks": [{"tuple_key": {"user": u, "relation": rel, "object": o}, "context": ctx, "correlation_id": str(i)}
                       for i, (u, rel, o) in enumerate(checks)],
        }
        res = (await self._call("POST", f"{self._base()}/batch-check", body)).get("result", {})
        out = []
        for i in range(len(checks)):
            item = res.get(str(i), {})
            if item.get("error"):
                raise FGAError(f"batch-check item failed: {item['error']}")
            out.append(bool(item.get("allowed")))
        return out

    async def list_objects(self, user: str, relation: str, type: str) -> list[str]:
        await self.ensure_ready()
        r = await self._call("POST", f"{self._base()}/list-objects", {
            "authorization_model_id": self.model_id, "consistency": CONSISTENCY, "context": now_context(),
            "type": type, "relation": relation, "user": user,
        })
        return list(r.get("objects", []))


def _same_model(stored: dict[str, Any], wanted: dict[str, Any]) -> bool:
    """Compare ignoring the defaults the server fills in ("", null, [], {})."""

    def strip(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: s for k, x in v.items() if (s := strip(x)) not in ("", None, [], {})}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v

    def norm(m: dict[str, Any]) -> str:
        return json.dumps(strip({"t": m.get("type_definitions", []), "c": m.get("conditions", {})}), sort_keys=True)

    return norm(stored) == norm(wanted)
