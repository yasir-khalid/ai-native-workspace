"""SEC EDGAR as an MCP server.

Two backends behind the same tools:

* ``mock`` (dev/test): fictional issuers from config/seed/edgar-mock.json. ``POST /control/filings``
  releases the next scripted filing for a ticker (or a custom one), so tests control exactly when an
  8-K "lands".
* ``sec`` (live, read-only): data.sec.gov submissions JSON for real tickers. The SEC requires a
  descriptive User-Agent with a contact address; set PODOS_SEC_USER_AGENT (no default is sent).

Tools:
  latest_filings(since, tickers, forms, limit) -> {"filings": [...], "cursor": "<iso>"}
  get_filing(accession) -> one filing
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "config" / "seed" / "edgar-mock.json"
DEFAULT_FORMS = ["8-K", "10-Q", "10-K", "4"]


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class MockEdgar:
    entities: dict[str, dict[str, str]] = field(default_factory=dict)
    filings: list[dict[str, Any]] = field(default_factory=list)
    scripted: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    seq: int = 0

    @classmethod
    def load(cls) -> MockEdgar:
        m = cls()
        m.reset()
        return m

    def reset(self) -> None:
        data = json.loads(FIXTURES.read_text())
        self.entities = {e["ticker"]: e for e in data["entities"]}
        self.scripted = {k: list(v) for k, v in data["scripted"].items()}
        self.filings = []
        self.seq = 0
        for h in data["history"]:
            self._add(h["ticker"], h, accepted_at=_parse(h["accepted_at"]))

    def _add(self, ticker: str, f: dict[str, Any], accepted_at: datetime | None = None) -> dict[str, Any]:
        self.seq += 1
        ent = self.entities.get(ticker, {"name": ticker, "cik": "0000000000"})
        accepted = accepted_at or datetime.now(UTC)
        accession = f"{ent['cik']}-26-{self.seq:06d}"
        row = {
            "accession": accession, "ticker": ticker, "cik": ent["cik"], "company": ent["name"],
            "form": f.get("form", "8-K"), "accepted_at": _iso(accepted), "title": f.get("title", ""),
            "summary": f.get("summary", ""), "url": f"https://www.sec.gov/Archives/edgar/data/{int(ent['cik'])}/{accession}.htm",
        }
        self.filings.append(row)
        return row

    def release(self, ticker: str, custom: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if custom and custom.get("title"):
            return self._add(ticker, custom)
        queue = self.scripted.get(ticker) or []
        return self._add(ticker, queue.pop(0)) if queue else None

    async def latest(self, since: str, tickers: list[str] | None, forms: list[str] | None, limit: int) -> list[dict[str, Any]]:
        t = {x.upper() for x in tickers} if tickers else None
        f = set(forms) if forms else None
        rows = [r for r in self.filings if (not since or r["accepted_at"] > since)
                and (t is None or r["ticker"] in t) and (f is None or r["form"] in f)]
        return sorted(rows, key=lambda r: r["accepted_at"])[:limit]

    async def get(self, accession: str) -> dict[str, Any] | None:
        return next((r for r in self.filings if r["accession"] == accession), None)


class SecEdgar:
    """Live, read-only. Respects the SEC fair-access guidance (descriptive UA, <= 10 req/s)."""

    def __init__(self, user_agent: str) -> None:
        if not user_agent:
            raise RuntimeError("PODOS_SEC_USER_AGENT is required for the live SEC backend")
        self.http = httpx.AsyncClient(timeout=15, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"})
        self._tickers: dict[str, dict[str, Any]] | None = None
        self._last = 0.0

    async def _get(self, url: str) -> Any:
        wait = 0.12 - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()
        r = await self.http.get(url)
        r.raise_for_status()
        return r.json()

    async def _cik(self, ticker: str) -> dict[str, Any] | None:
        if self._tickers is None:
            data = await self._get("https://www.sec.gov/files/company_tickers.json")
            self._tickers = {v["ticker"].upper(): v for v in data.values()}
        return self._tickers.get(ticker.upper())

    async def latest(self, since: str, tickers: list[str] | None, forms: list[str] | None, limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ticker in tickers or []:
            ent = await self._cik(ticker)
            if ent is None:
                continue
            cik = f"{int(ent['cik_str']):010d}"
            sub = await self._get(f"https://data.sec.gov/submissions/CIK{cik}.json")
            recent = sub.get("filings", {}).get("recent", {})
            for i, form in enumerate(recent.get("form", [])):
                if forms and form not in forms:
                    continue
                accepted = recent["acceptanceDateTime"][i]
                accepted_iso = _iso(_parse(accepted if accepted.endswith("Z") else accepted + "Z"))
                if since and accepted_iso <= since:
                    continue
                acc = recent["accessionNumber"][i]
                doc = recent.get("primaryDocument", [""] * (i + 1))[i]
                out.append({
                    "accession": acc, "ticker": ticker.upper(), "cik": cik, "company": sub.get("name", ticker),
                    "form": form, "accepted_at": accepted_iso, "title": recent.get("primaryDocDescription", [""] * (i + 1))[i] or form,
                    "summary": f"Items: {recent.get('items', [''] * (i + 1))[i]}".strip() if recent.get("items") else "",
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}",
                })
        return sorted(out, key=lambda r: r["accepted_at"])[:limit]

    async def get(self, accession: str) -> dict[str, Any] | None:
        return None  # full-text fetch is a Phase D connector capability


def build_server(mode: str = "mock", control_token: str = "") -> MCPServer:
    backend: Any = MockEdgar.load() if mode == "mock" else SecEdgar(os.environ.get("PODOS_SEC_USER_AGENT", ""))
    srv = MCPServer(
        "sec-edgar", version="1.0.0",
        instructions="Read-only SEC EDGAR filings. Use latest_filings with a cursor to poll for new filings.",
    )

    @srv.tool()
    async def latest_filings(since: str = "", tickers: list[str] | None = None, forms: list[str] | None = None,
                             limit: int = 50) -> dict[str, Any]:
        """Filings accepted after `since` (ISO 8601), optionally filtered by tickers and form types.
        Returns {"filings": [...], "cursor": newest accepted_at, to pass back as `since` next time}."""
        rows = await backend.latest(since, tickers, forms or DEFAULT_FORMS, max(1, min(limit, 200)))
        return {"filings": rows, "cursor": rows[-1]["accepted_at"] if rows else since}

    @srv.tool()
    async def get_filing(accession: str) -> dict[str, Any]:
        """One filing by accession number."""
        return await backend.get(accession) or {"error": "not_found"}

    @srv.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "mode": mode})

    if mode == "mock":
        def _authorised(request: Request) -> bool:
            return bool(control_token) and request.headers.get("x-control-token") == control_token

        @srv.custom_route("/control/filings", methods=["POST"])
        async def control_release(request: Request) -> JSONResponse:
            if not _authorised(request):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = await request.json()
            row = backend.release(str(body["ticker"]).upper(), body if body.get("title") else None)
            return JSONResponse(row or {"error": "no scripted filings left"}, status_code=201 if row else 404)

        @srv.custom_route("/control/reset", methods=["POST"])
        async def control_reset(request: Request) -> JSONResponse:
            if not _authorised(request):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            backend.reset()
            return JSONResponse({"status": "reset"})

    return srv


def build_app(mode: str = "mock", control_token: str = ""):
    return build_server(mode, control_token).streamable_http_app(stateless_http=True, json_response=True)
