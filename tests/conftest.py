"""Test harness: real Postgres (podos_test DB, runtime role under RLS), every ADK
agent served over real A2A HTTP in its own server, and the Workspace API on a
real port. Worker ticks are invoked directly for determinism."""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="podos-test-"))
# Broader authorization scenarios live only in the isolated test database, never in demo IAM.
TEST_CONFIG = TMP / "config"
shutil.copytree(ROOT / "config", TEST_CONFIG, ignore=shutil.ignore_patterns("data_plane.dev.json"))
shutil.copyfile(ROOT / "tests/fixtures/authorization-directory.json", TEST_CONFIG / "iam.json")
shutil.copyfile(ROOT / "tests/fixtures/authorization-research.json", TEST_CONFIG / "seed/research.json")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


AGENTS = [
    ("auto-router", "1.0.0"), ("equity-research", "1.0.0"), ("filing-analyst", "1.0.0"),
    ("filing-analyst", "1.1.0"), ("catalyst-monitor", "1.0.0"), ("esg-screener", "1.0.0"),
]
PORTS = {f"agent://{a}/{v}": _free_port() for a, v in AGENTS}
API_PORT = _free_port()
EDGAR_PORT = _free_port()
EDGAR = f"http://127.0.0.1:{EDGAR_PORT}"
CONTROL = {"x-control-token": "test-control-token"}

# Policy copy with generous rate limits; individual tests rewrite it to test hot reload / quotas.
POLICY = TMP / "policy.json"
_policy = json.loads((ROOT / "config" / "policy.json").read_text())
_policy["pod_quotas"]["default"].update({"runs_per_minute": 10_000, "max_concurrent_runs": 50})
POLICY.write_text(json.dumps(_policy))
CONNECTORS = TMP / "connectors.json"
_connectors = json.loads((ROOT / "config" / "connectors.local.json").read_text())
for _c in _connectors:
    if _c["connector_id"] == "sec-edgar":
        _c.update(url=f"{EDGAR}/mcp", interval_seconds=0)
CONNECTORS.write_text(json.dumps(_connectors))
ENDPOINTS = TMP / "endpoints.json"
ENDPOINTS.write_text(json.dumps({k: f"http://127.0.0.1:{p}" for k, p in PORTS.items()}))

os.environ.update({
    "PODOS_ENV": "test",
    "PODOS_CONFIG_DIR": str(TEST_CONFIG),
    "PODOS_DATABASE_URL": "postgresql://podos_app:podos_app@127.0.0.1:55432/podos_test",
    "PODOS_OWNER_DATABASE_URL": "postgresql://podos_owner:podos_owner@127.0.0.1:55432/podos_test",
    "PODOS_POLICY_PATH": str(POLICY),
    "PODOS_ENDPOINTS": str(ENDPOINTS),
    "PODOS_OBJECT_ROOT": str(TMP / "objects"),
    "PODOS_MAIL_ROOT": str(TMP / "mail"),
    "PODOS_PUBLIC_API_URL": f"http://127.0.0.1:{API_PORT}",
    "PODOS_INTERNAL_API_URL": f"http://127.0.0.1:{API_PORT}",
    "PODOS_AGENT_STREAM_DELAY": "0",
    # Deterministic agents for the acceptance suite; tests/test_llm_live.py opts back in.
    "PODOS_LLM_MODE": os.environ.get("PODOS_TEST_LLM_MODE", "off"),
    "PODOS_AGENT_TIMEOUT": "15",
    # Same docker data plane as dev, separate namespaces: test-research-<pod> indexes, test bucket.
    "PODOS_INDEX_NAMESPACE": "test-",
    "PODOS_S3_BUCKET": "podos-artifacts-test",
    # Separate OpenFGA store; seeding wipes and rebuilds it for every test.
    "PODOS_FGA_STORE": "podos-test",
    "PODOS_CONNECTORS": str(CONNECTORS),
    "PODOS_CONNECTOR_CONTROL_TOKEN": CONTROL["x-control-token"],
})

from podos_agents.serve import build_app  # noqa: E402
from podos_api.main import create_app  # noqa: E402
from podos_api.migrate import migrate, truncate_all  # noqa: E402
from podos_api.seed import seed  # noqa: E402
from podos_api.services import Services  # noqa: E402
from podos_api.settings import get_settings, reset_settings  # noqa: E402
from podos_connectors import edgar as edgar_mcp  # noqa: E402

BASE = f"http://127.0.0.1:{API_PORT}"
API_APP = None  # the in-process API app, for fault injection in tests


class _Server(threading.Thread):
    def __init__(self, app, port: int) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def run(self) -> None:
        self.server.run()

    def wait(self) -> None:
        deadline = time.time() + 20
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)


@pytest.fixture(scope="session", autouse=True)
def stack():
    reset_settings()
    migrate()
    truncate_all()
    servers = []
    for (agent, version) in AGENTS:
        port = PORTS[f"agent://{agent}/{version}"]
        s = _Server(build_app(agent, version, "127.0.0.1", port), port)
        s.start()
        servers.append(s)
    edgar_srv = _Server(edgar_mcp.build_app("mock", CONTROL["x-control-token"]), EDGAR_PORT)
    edgar_srv.start()
    servers.append(edgar_srv)
    global API_APP
    API_APP = create_app(get_settings())
    api = _Server(API_APP, API_PORT)
    api.start()
    servers.append(api)
    for s in servers:
        s.wait()
    yield
    for s in servers:
        s.server.should_exit = True
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
async def fresh_data(stack):
    """Every test starts from the canonical seed (pods, users, registry, releases, research)."""
    POLICY.write_text(json.dumps(_policy))
    CONNECTORS.write_text(json.dumps(_connectors))
    truncate_all()
    await seed(get_settings())
    async with httpx.AsyncClient() as c:
        await c.post(f"{EDGAR}/control/reset", headers=CONTROL)
    yield


@pytest.fixture(scope="session")
async def svc():
    s = Services(get_settings())
    await s.open()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------
class User:
    def __init__(self, subject: str, token: str) -> None:
        self.subject = subject
        self.token = token
        self.http = httpx.AsyncClient(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {token}"})

    def _h(self, pod: str | None) -> dict[str, str]:
        return {"X-Active-Pod": pod} if pod else {}

    async def get(self, path: str, pod: str | None = None, **kw: Any) -> httpx.Response:
        return await self.http.get(path, headers=self._h(pod), **kw)

    async def post(self, path: str, json: Any = None, pod: str | None = None, **kw: Any) -> httpx.Response:
        return await self.http.post(path, json=json, headers=self._h(pod), **kw)

    async def patch(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.http.patch(path, json=json, **kw)

    async def new_thread(self, pod: str, **body: Any) -> str:
        r = await self.post(f"/v1/pods/{pod}/threads", json=body)
        assert r.status_code == 201, r.text
        return r.json()["thread_id"]

    async def events(self, run_id: str, pod: str, timeout: float = 30) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async with self.http.stream("GET", f"/v1/runs/{run_id}/events", headers=self._h(pod), timeout=timeout) as r:
            assert r.status_code == 200, await r.aread()
            etype = None
            async for line in r.aiter_lines():
                if line.startswith("event: "):
                    etype = line[7:]
                elif line.startswith("data: "):
                    out.append({"type": etype, **json.loads(line[6:])})
                    if etype in ("run.completed", "run.failed", "stream.terminated"):
                        break
        return out

    async def ask(self, pod: str, message: str, *, mode: str = "direct", agent_id: str | None = "equity-research",
                  thread_id: str | None = None, **extra: Any) -> tuple[str, list[dict[str, Any]]]:
        thread_id = thread_id or await self.new_thread(pod)
        body = {"mode": mode, "message": message, **extra}
        if mode == "direct":
            body["agent_id"] = agent_id
        r = await self.post(f"/v1/threads/{thread_id}/runs", json=body, pod=pod)
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]
        return run_id, await self.events(run_id, pod)


async def login(subject: str) -> User:
    async with httpx.AsyncClient(base_url=BASE) as c:
        r = await c.post("/v1/auth/dev-login", json={"subject_id": subject})
        assert r.status_code == 200, r.text
        return User(subject, r.json()["token"])


def text_of(events: list[dict[str, Any]]) -> str:
    return "".join(e.get("text", "") for e in events if e["type"] == "message.delta")


def last(events: list[dict[str, Any]]) -> dict[str, Any]:
    return events[-1]


@pytest.fixture
async def alice():
    return await login("alice.chen")


@pytest.fixture
async def arjun():
    return await login("arjun.patel")


@pytest.fixture
async def bianca():
    return await login("bianca.rossi")


@pytest.fixture
async def ben():
    return await login("ben.okafor")


@pytest.fixture
async def dana():
    return await login("dana.kim")


@pytest.fixture
async def pat():
    return await login("pat.morgan")


# ---------------------------------------------------------------------------
# Low-level helpers for adversarial tests (forging / replaying credentials)
# ---------------------------------------------------------------------------
async def make_running_run(svc, pod: str, subject: str, agent: str = "equity-research") -> str:
    from podos_api.db import Jsonb, fetchone

    async with svc.db.pod_tx(pod) as conn:
        row = await fetchone(
            conn,
            """INSERT INTO agent_runs(pod_id, agent_id, agent_version, mode, state, created_by, execution_context, release_revision)
               VALUES (%s,%s,'1.0.0','background','RUNNING',%s,%s,'test') RETURNING run_id::text""",
            (pod, agent, subject, Jsonb({})),
        )
    return row["run_id"]


def delegation(pod: str, run: str, agent: str = "equity-research", sub: str = "alice.chen",
               caps: list[str] | None = None, secret: str | None = None) -> str:
    from podos_api.security.tokens import issue_delegation
    from podos_contracts.models import DelegationClaims

    s = get_settings()
    if secret:
        s = type(s)(**{**s.__dict__, "delegation_secret": secret})
    return issue_delegation(s, DelegationClaims(
        sub=sub, pod=pod, run=run, agent=agent, caps=caps or ["research.search", "document.export.pptx", "document.export.docx"],
        depth=1, purpose="research", cls="pod_confidential", rev="test"))


def workload(agent: str) -> str:
    from podos_agents.common import workload_token

    return workload_token(agent)


async def internal_post(path: str, body: dict, *, agent: str | None, deleg: str | None = None) -> httpx.Response:
    headers = {}
    if agent:
        headers["Authorization"] = f"Workload {workload(agent)}"
    if deleg:
        headers["X-PodOS-Delegation"] = deleg
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as c:
        return await c.post(path, json=body, headers=headers)


async def revoke(pat: User, pod: str, subject: str, revoke: bool = True) -> None:
    r = await pat.post("/v1/admin/iam/memberships", json={"pod_id": pod, "subject_id": subject, "revoke": revoke})
    assert r.status_code == 200, r.text
