"""Keep OpenFGA in step with the directory (IdP mirror) and the seed template.

* ``seed_store`` (dev/test only): wipe the store and write directory + template tuples, so every
  test and ``make reset`` starts from the canonical graph.
* ``provision_additive`` (any env): write missing directory + template tuples, never delete. Grants
  made later in the admin console are not touched.
* ``apply_membership``: the IdP-sync path for one membership (join, change groups, revoke).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..db import Database, fetchall
from . import tuples as T
from .fga import FGAClient, Tuple


async def directory_tuples(db: Database) -> list[Tuple]:
    async with db.system_tx() as conn:
        pods = await fetchall(conn, "SELECT pod_id FROM pods ORDER BY pod_id")
        mems = await fetchall(
            conn, "SELECT pod_id, subject_id, pod_role, groups FROM pod_memberships WHERE revoked_at IS NULL"
        )
        admins = await fetchall(
            conn, "SELECT subject_id FROM users WHERE active AND 'platform_admin' = ANY(platform_roles)"
        )
    out: list[Tuple] = []
    for p in pods:
        out += T.pod_tuples(p["pod_id"])
    for m in mems:
        out += T.membership_tuples(m["pod_id"], m["subject_id"], m["pod_role"], list(m["groups"]))
    out += [T.platform_admin_tuple(a["subject_id"]) for a in admins]
    return out


async def pod_ids(db: Database) -> list[str]:
    async with db.system_tx() as conn:
        return [r["pod_id"] for r in await fetchall(conn, "SELECT pod_id FROM pods ORDER BY pod_id")]


def load_template(path: Path) -> dict:
    return json.loads(path.read_text())


async def desired_tuples(db: Database, template_path: Path) -> list[Tuple]:
    return await directory_tuples(db) + T.template_tuples(load_template(template_path), await pod_ids(db))


async def seed_store(fga: FGAClient, db: Database, template_path: Path, *, env: str) -> int:
    if env not in {"dev", "test"}:
        raise RuntimeError("seed_store wipes the authorization store; use provision_additive outside dev/test")
    existing = await fga.read()
    await fga.write(deletes=existing)
    wanted = _dedupe(await desired_tuples(db, template_path))
    await fga.write(writes=wanted)
    return len(wanted)


async def provision_additive(fga: FGAClient, db: Database, template_path: Path) -> int:
    wanted = _dedupe(await desired_tuples(db, template_path))
    await fga.write(writes=wanted)
    return len(wanted)


async def apply_membership(
    fga: FGAClient, pod_id: str, subject_id: str, pod_role: str | None, groups: list[str], *, revoke: bool
) -> None:
    """Mirror one directory membership into OpenFGA. Revocation removes every role and group tuple
    the subject holds in that pod; direct grants stay but are inert without membership."""
    current = [t for t in await fga.read(user=T.user(subject_id), object="group:")
               if t.object.startswith(f"group:{pod_id}/")]
    roles = [Tuple(T.user(subject_id), r, T.pod(pod_id)) for r in ("pm", "analyst")]
    if revoke:
        await fga.write(deletes=current + roles)
        return
    wanted = T.membership_tuples(pod_id, subject_id, pod_role or "analyst", groups)
    keep = {t.ident() for t in wanted}
    await fga.write(writes=wanted, deletes=[t for t in current + roles if t.ident() not in keep])


def _dedupe(ts: list[Tuple]) -> list[Tuple]:
    seen: dict[tuple[str, str, str], Tuple] = {}
    for t in ts:
        seen.setdefault(t.ident(), t)
    return list(seen.values())
