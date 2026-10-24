"""PostgreSQL access with pod-scoped transactions.

Every pod-content query runs inside ``pod_tx(pod_id)``, which sets the
transaction-local ``app.pod_id`` used by the RLS policies. The pod id passed in
must come from a server-validated ``PodContext`` (or, for workers, from a row
that was itself read under RLS). RLS is defence in depth: application code also
filters by pod_id explicitly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

__all__ = ["Database", "Jsonb"]


class Database:
    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self.url = url
        self.pool = AsyncConnectionPool(
            url,
            open=False,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True)

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def pod_tx(self, pod_id: str) -> AsyncIterator[AsyncConnection[Any]]:
        if not pod_id:
            raise ValueError("pod_tx requires a validated pod id")
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.pod_id', %s, true)", (pod_id,))
                yield conn

    @asynccontextmanager
    async def system_tx(self) -> AsyncIterator[AsyncConnection[Any]]:
        """Control-plane transaction (registry, IAM mirror, audit).

        ``app.pod_id`` is explicitly empty, so any accidental read of a pod-content
        table returns zero rows under FORCE RLS.
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.pod_id', '', true)")
                yield conn


async def fetchone(conn: AsyncConnection[Any], sql: str, params: Any = None) -> dict[str, Any] | None:
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def fetchall(conn: AsyncConnection[Any], sql: str, params: Any = None) -> list[dict[str, Any]]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()
