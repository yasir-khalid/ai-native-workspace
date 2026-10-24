"""Per-pod research index adapter.

``PodResearchIndex`` is bound to one validated pod id; callers can never pass an
index name. Two backends share the contract:

- ``OpenSearchBackend`` (default): physical index ``<ns>research-<pod_id>``,
  queried with **that pod's own scoped credential** (provisioned by
  ``podos_api.provision``). Even a code bug that targeted another pod's index
  would be rejected by OpenSearch's security plugin (403).
- ``PostgresBackend``: full-text search on ``research_documents`` under RLS
  (kept as a no-infrastructure fallback: ``PODOS_SEARCH_BACKEND=postgres``).

Postgres ``research_documents`` stays the system of record; the OpenSearch
index is written in the same request (inside the pod transaction, so a failed
index write rolls the insert back) and can be rebuilt with ``reindex``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Protocol

import httpx

from .db import Database, fetchall, fetchone
from .settings import Settings

_STOP = {
    "the", "and", "for", "with", "what", "whats", "about", "this", "that", "our", "are", "was", "were", "how", "does",
    "into", "from", "have", "has", "please", "give", "tell", "show", "summarise", "summarize", "compare", "latest",
    "prepare", "slide", "slides", "deck", "summary", "memo", "word", "powerpoint", "pptx", "docx", "make", "create",
    "search", "index", "pod", "can", "you", "any", "all", "its", "their", "them", "who", "why", "when", "which",
}

INDEX_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "doc_id": {"type": "keyword"},
            "pod_id": {"type": "keyword"},
            "case_id": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "english"},
            "ticker": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            "body": {"type": "text", "analyzer": "english"},
            "source": {"type": "keyword"},
            "published_at": {"type": "date"},
            "classification": {"type": "keyword"},
        },
    },
}


class SearchUnavailable(Exception):
    pass


def index_name_for(pod_id: str) -> str:
    """Logical index name (also stored on each research_documents row)."""
    return f"research-{pod_id}"


def query_terms(text: str) -> list[str]:
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text)]
    return sorted({w for w in words if w not in _STOP})[:24]


class SearchBackend(Protocol):
    async def search(self, pod_id: str, query: str, *, limit: int, kinds: list[str] | None,
                     tickers: list[str] | None, published_after: datetime | None) -> list[dict[str, Any]]: ...

    async def index_doc(self, pod_id: str, doc: dict[str, Any]) -> None: ...

    async def bulk_index(self, pod_id: str, docs: list[dict[str, Any]]) -> None: ...

    async def reset(self, pod_id: str) -> None: ...


# ---------------------------------------------------------------------------
# OpenSearch
# ---------------------------------------------------------------------------
class OpenSearchBackend:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        verify: bool | str = settings.opensearch_verify
        self.verify = False if verify in ("false", "0", "") else (True if verify in ("true", "1") else verify)

    def physical_index(self, pod_id: str) -> str:
        return f"{self.s.index_namespace}{index_name_for(pod_id)}"

    def _auth(self, pod_id: str) -> tuple[str, str]:
        cred = self.s.data_plane_secrets().get("opensearch", {}).get(pod_id)
        if not cred:
            raise SearchUnavailable("no search credential provisioned for pod")
        return cred["username"], cred["password"]

    async def _req(self, pod_id: str, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(base_url=self.s.opensearch_url, auth=self._auth(pod_id), verify=self.verify,
                                         timeout=10) as hc:
                return await hc.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise SearchUnavailable(type(exc).__name__) from exc

    async def ensure_index(self, pod_id: str) -> None:
        idx = self.physical_index(pod_id)
        r = await self._req(pod_id, "HEAD", f"/{idx}")
        if r.status_code == 404:
            r = await self._req(pod_id, "PUT", f"/{idx}", json=INDEX_MAPPING)
            if r.status_code >= 400 and "resource_already_exists" not in r.text:
                raise SearchUnavailable(f"create index failed: {r.status_code}")
        elif r.status_code >= 400:
            raise SearchUnavailable(f"index check failed: {r.status_code}")

    async def search(self, pod_id, query, *, limit, kinds, tickers, published_after):
        terms = query_terms(query)
        filters: list[dict[str, Any]] = [{"term": {"pod_id": pod_id}}]
        if kinds:
            filters.append({"terms": {"kind": kinds}})
        if tickers:
            filters.append({"terms": {"ticker": tickers}})
        if published_after:
            filters.append({"range": {"published_at": {"gt": published_after.isoformat()}}})
        must = ([{"multi_match": {"query": " ".join(terms), "fields": ["title^3", "ticker.text^4", "body"],
                                  "operator": "or"}}] if terms else [{"match_all": {}}])
        body = {
            "size": max(1, min(limit, 20)),
            "query": {"bool": {"must": must, "filter": filters}},
            "highlight": {"fields": {"body": {"fragment_size": 200, "number_of_fragments": 2}},
                          "pre_tags": [""], "post_tags": [""]},
            "sort": ["_score", {"published_at": "desc"}],
        }
        r = await self._req(pod_id, "POST", f"/{self.physical_index(pod_id)}/_search", json=body)
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise SearchUnavailable(f"search failed: {r.status_code}")
        out = []
        for h in r.json()["hits"]["hits"]:
            src = h["_source"]
            if src.get("pod_id") != pod_id:  # defence in depth; cannot happen with a per-pod index
                continue
            frag = " … ".join(h.get("highlight", {}).get("body", [])) or src["body"][:280]
            out.append({
                "doc_id": src["doc_id"], "case_id": src.get("case_id"), "kind": src["kind"], "title": src["title"],
                "ticker": src.get("ticker"), "source": src["source"], "published_at": src["published_at"],
                "classification": src["classification"], "snippet": frag, "body": src["body"],
                "score": float(h.get("_score") or 0),
            })
        return out

    @staticmethod
    def _source(pod_id: str, doc: dict[str, Any]) -> dict[str, Any]:
        src = {k: (v.isoformat() if isinstance(v, datetime) else (str(v) if v is not None and k.endswith("_id") else v))
               for k, v in doc.items() if k in INDEX_MAPPING["mappings"]["properties"]}
        src["pod_id"] = pod_id
        return src

    async def index_doc(self, pod_id: str, doc: dict[str, Any]) -> None:
        await self.ensure_index(pod_id)
        src = self._source(pod_id, doc)
        # refresh=true: the new document is searchable when the request returns (low write volume).
        r = await self._req(pod_id, "PUT", f"/{self.physical_index(pod_id)}/_doc/{src['doc_id']}",
                            params={"refresh": "true"}, json=src)
        if r.status_code >= 400:
            raise SearchUnavailable(f"index failed: {r.status_code}")

    async def bulk_index(self, pod_id: str, docs: list[dict[str, Any]]) -> None:
        if not docs:
            return
        idx = self.physical_index(pod_id)
        lines = []
        for d in docs:
            src = self._source(pod_id, d)
            lines.append(json.dumps({"index": {"_index": idx, "_id": src["doc_id"]}}))
            lines.append(json.dumps(src))
        r = await self._req(pod_id, "POST", "/_bulk", params={"refresh": "true"}, content="\n".join(lines) + "\n",
                            headers={"Content-Type": "application/x-ndjson"})
        if r.status_code >= 400 or r.json().get("errors"):
            raise SearchUnavailable(f"bulk index failed: {r.status_code}")

    async def reset(self, pod_id: str) -> None:
        r = await self._req(pod_id, "DELETE", f"/{self.physical_index(pod_id)}")
        if r.status_code >= 400 and r.status_code != 404:
            raise SearchUnavailable(f"reset failed: {r.status_code}")
        await self.ensure_index(pod_id)


# ---------------------------------------------------------------------------
# Postgres fallback
# ---------------------------------------------------------------------------
class PostgresBackend:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def search(self, pod_id, query, *, limit, kinds, tickers, published_after):
        terms = query_terms(query)
        tsq = " | ".join(f"{t.replace('-', '')}:*" for t in terms if t.replace("-", "")) or None
        clauses = ["pod_id = %(pod)s", "index_name = %(index)s"]
        params: dict[str, Any] = {"pod": pod_id, "index": index_name_for(pod_id), "limit": max(1, min(limit, 20))}
        if kinds:
            clauses.append("kind = ANY(%(kinds)s)")
            params["kinds"] = kinds
        if tickers:
            clauses.append("ticker = ANY(%(tickers)s)")
            params["tickers"] = tickers
        if published_after:
            clauses.append("published_at > %(after)s")
            params["after"] = published_after
        if tsq:
            params["q"] = tsq
            rank = "ts_rank_cd(tsv, to_tsquery('english', %(q)s))"
            clauses.append("tsv @@ to_tsquery('english', %(q)s)")
            headline = ("ts_headline('english', body, to_tsquery('english', %(q)s), "
                        "'MaxWords=40, MinWords=18, MaxFragments=2, FragmentDelimiter=\" … \", StartSel=\"\", StopSel=\"\"')")
        else:
            rank, headline = "0", "left(body, 280)"
        sql = f"""SELECT doc_id::text, case_id::text, kind, title, ticker, source, published_at, classification,
                         {headline} AS snippet, body, {rank} AS score
                  FROM research_documents WHERE {' AND '.join(clauses)}
                  ORDER BY score DESC, published_at DESC LIMIT %(limit)s"""
        async with self.db.pod_tx(pod_id) as conn:
            rows = await fetchall(conn, sql, params)
        for r in rows:
            r["published_at"] = r["published_at"].isoformat()
            r["score"] = float(r["score"])
        return rows

    async def index_doc(self, pod_id: str, doc: dict[str, Any]) -> None:
        return None  # the tsvector column is maintained by Postgres

    async def bulk_index(self, pod_id: str, docs: list[dict[str, Any]]) -> None:
        return None

    async def reset(self, pod_id: str) -> None:
        return None


def make_backend(settings: Settings, db: Database) -> SearchBackend:
    return OpenSearchBackend(settings) if settings.search_backend == "opensearch" else PostgresBackend(db)


# ---------------------------------------------------------------------------
# Pod-bound handle
# ---------------------------------------------------------------------------
class PodResearchIndex:
    def __init__(self, db: Database, backend: SearchBackend, pod_id: str) -> None:
        self._db = db
        self._backend = backend
        self.pod_id = pod_id
        self.index_name = index_name_for(pod_id)

    async def search(self, query: str, *, limit: int = 6, kinds: list[str] | None = None,
                     tickers: list[str] | None = None, published_after: datetime | None = None) -> list[dict[str, Any]]:
        return await self._backend.search(self.pod_id, query, limit=limit, kinds=kinds, tickers=tickers,
                                          published_after=published_after)

    async def add_document(
        self, *, kind: str, title: str, body: str, source: str, created_by: str,
        ticker: str | None = None, case_id: str | None = None, published_at: datetime | None = None,
        classification: str = "pod_confidential",
    ) -> dict[str, Any]:
        async with self._db.pod_tx(self.pod_id) as conn:
            row = await fetchone(
                conn,
                """INSERT INTO research_documents
                     (pod_id, index_name, case_id, kind, title, ticker, body, source, published_at, classification, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s, now()),%s,%s)
                   RETURNING doc_id, case_id, kind, title, ticker, body, source, published_at, classification""",
                (self.pod_id, self.index_name, case_id, kind, title, ticker, body, source, published_at,
                 classification, created_by),
            )
            assert row is not None
            await self._backend.index_doc(self.pod_id, row)  # failure aborts the insert
        return {k: v for k, v in row.items() if k != "body"}

    async def reindex(self) -> int:
        await self._backend.reset(self.pod_id)
        async with self._db.pod_tx(self.pod_id) as conn:
            rows = await fetchall(
                conn,
                """SELECT doc_id, case_id, kind, title, ticker, body, source, published_at, classification
                   FROM research_documents WHERE pod_id = %s""",
                (self.pod_id,),
            )
        await self._backend.bulk_index(self.pod_id, rows)
        return len(rows)
