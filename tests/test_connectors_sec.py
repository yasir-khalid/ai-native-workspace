"""Live SEC EDGAR backend of the EDGAR MCP server.

* Offline: parsing against the documented data.sec.gov response shapes (httpx MockTransport).
* Live (opt-in): set PODOS_SEC_USER_AGENT to a descriptive UA with YOUR contact address, e.g.
      PODOS_SEC_USER_AGENT="Acme Capital research-platform ops@acme.example" uv run pytest tests/test_connectors_sec.py
  The SEC requires this header; no default is ever sent.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from mcp import Client
from podos_connectors import edgar

TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
SUBMISSIONS = {
    "cik": "320193", "name": "Apple Inc.",
    "filings": {"recent": {
        "accessionNumber": ["0000320193-26-000101", "0000320193-26-000090", "0001140361-26-000001"],
        "form": ["8-K", "10-Q", "4"],
        "acceptanceDateTime": ["2026-10-14T16:31:05.000Z", "2026-08-01T16:05:00.000Z", "2026-07-02T18:00:00.000Z"],
        "primaryDocument": ["aapl-8k.htm", "aapl-10q.htm", "form4.xml"],
        "primaryDocDescription": ["8-K", "10-Q", "FORM 4"],
        "items": ["2.02,9.01", "", ""],
    }},
}


def _mock_backend() -> edgar.SecEdgar:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        if request.url.path.endswith("CIK0000320193.json"):
            return httpx.Response(200, json=SUBMISSIONS)
        return httpx.Response(404)

    b = edgar.SecEdgar("PodOS test suite ops@podos.example")
    b.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=b.http.headers)
    b.seen = seen  # type: ignore[attr-defined]
    return b


async def test_sec_backend_parses_submissions_and_respects_cursor():
    b = _mock_backend()
    rows = await b.latest("2026-08-01T16:05:00.000000Z", ["aapl"], ["8-K", "10-Q"], 50)
    assert [r["form"] for r in rows] == ["8-K"]  # 10-Q is at the cursor (not after it); Form 4 filtered out
    r = rows[0]
    assert r["ticker"] == "AAPL" and r["cik"] == "0000320193" and r["company"] == "Apple Inc."
    assert r["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/aapl-8k.htm"
    assert r["summary"] == "Items: 2.02,9.01"
    assert all(req.headers["user-agent"] == "PodOS test suite ops@podos.example" for req in b.seen)  # type: ignore[attr-defined]


def test_sec_backend_requires_user_agent():
    with pytest.raises(RuntimeError):
        edgar.SecEdgar("")


@pytest.mark.skipif(not os.environ.get("PODOS_SEC_USER_AGENT"), reason="live SEC test: set PODOS_SEC_USER_AGENT")
async def test_live_sec_edgar_over_mcp():
    srv = edgar.build_server("sec")
    async with Client(srv) as c:
        res = await c.call_tool("latest_filings", {"since": "2026-01-01T00:00:00Z", "tickers": ["AAPL"], "forms": ["8-K", "10-Q", "10-K"]})
    out = json.loads("".join(getattr(x, "text", "") for x in res.content))
    assert out["filings"], "expected at least one AAPL filing this year"
    assert {f["form"] for f in out["filings"]} <= {"8-K", "10-Q", "10-K"}
