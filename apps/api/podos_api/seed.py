"""Seed the mock IdP directory, registry, releases and pod research.

Pod content is written through ``pod_tx`` with the runtime role, i.e. the same
RLS path as production traffic. Manifests are validated against the JSON
Schemas before being registered.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
from podos_contracts import SCHEMA_DIR
from podos_contracts.models import AgentManifest, CapabilityManifest, ReleasePolicy

from .authz.fga import FGAClient
from .authz.sync import provision_additive, seed_store
from .db import Database, Jsonb, fetchone
from .events import refresh_watchlist
from .search import PodResearchIndex, index_name_for, make_backend
from .settings import Settings, get_settings


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text())


def validate_agent_manifest(m: dict[str, Any]) -> AgentManifest:
    jsonschema.validate(m, load_schema("agent-manifest.schema.json"), format_checker=jsonschema.FormatChecker())
    return AgentManifest.model_validate(m)


def validate_capability_manifest(m: dict[str, Any]) -> CapabilityManifest:
    jsonschema.validate(m, load_schema("capability-manifest.schema.json"))
    return CapabilityManifest.model_validate(m)


def validate_release(r: dict[str, Any]) -> ReleasePolicy:
    jsonschema.validate(r, load_schema("release-policy.schema.json"))
    return ReleasePolicy.model_validate(r)


async def register_agent(conn, manifest: AgentManifest, kind: str) -> None:
    await conn.execute(
        """INSERT INTO agent_definitions(agent_id, name, owner_team, description, publication_status, manifest, kind)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (agent_id) DO UPDATE SET name = excluded.name, description = excluded.description,
             owner_team = excluded.owner_team, manifest = excluded.manifest, kind = excluded.kind, updated_at = now()""",
        (manifest.agent_id, manifest.name, manifest.owner_team, manifest.description, manifest.publication_status,
         Jsonb(manifest.model_dump(mode="json")), kind),
    )
    for cap in manifest.capabilities:
        await conn.execute(
            "INSERT INTO agent_capability_support(agent_id, capability_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (manifest.agent_id, cap),
        )


async def upsert_release(conn, rel: ReleasePolicy, updated_by: str) -> None:
    await conn.execute(
        """INSERT INTO release_policies(release_key, agent_id, capability_id, kind, config, revision, updated_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (release_key) DO UPDATE SET config = excluded.config, revision = excluded.revision,
             updated_by = excluded.updated_by, updated_at = now()""",
        (rel.release_key, rel.agent_id, rel.capability_id, rel.kind, Jsonb(rel.model_dump(mode="json")), rel.revision,
         updated_by),
    )


async def sync_directory(db: Database, iam: dict[str, Any], *, deactivate_missing: bool = False) -> None:
    """Apply the configured directory; dev/test can retire accounts without deleting history."""
    async with db.system_tx() as conn:
        if deactivate_missing:
            await conn.execute(
                "UPDATE users SET active = false, platform_roles = '{}' WHERE NOT (subject_id = ANY(%s))",
                ([u["subject_id"] for u in iam["users"]],),
            )
            await conn.execute("UPDATE pod_memberships SET revoked_at = now() WHERE revoked_at IS NULL")
        for p in iam["pods"]:
            await conn.execute(
                """INSERT INTO pods(pod_id, display_name, strategy, accent) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (pod_id) DO UPDATE SET display_name = excluded.display_name""",
                (p["pod_id"], p["display_name"], p["strategy"], p["accent"]),
            )
        for u in iam["users"]:
            await conn.execute(
                """INSERT INTO users(subject_id, display_name, email, title, platform_roles) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (subject_id) DO UPDATE SET display_name = excluded.display_name,
                     email = excluded.email, title = excluded.title, platform_roles = excluded.platform_roles, active = true""",
                (u["subject_id"], u["display_name"], u["email"], u["title"], u["platform_roles"]),
            )
            for m in u["memberships"]:
                await conn.execute(
                    """INSERT INTO pod_memberships(pod_id, subject_id, pod_role, groups) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (pod_id, subject_id) DO UPDATE SET pod_role = excluded.pod_role,
                         groups = excluded.groups, revoked_at = NULL""",
                    (m["pod_id"], u["subject_id"], m["pod_role"], m["groups"]),
                )


async def seed(settings: Settings | None = None) -> dict[str, int]:
    s = settings or get_settings()
    cfg: Path = s.config_dir
    db = Database(s.database_url, max_size=2)
    await db.open()
    counts = {"pods": 0, "users": 0, "agents": 0, "documents": 0, "monitors": 0}
    try:
        iam = json.loads((cfg / "iam.json").read_text())
        await sync_directory(db, iam, deactivate_missing=s.env in {"dev", "test"})
        counts.update(pods=len(iam["pods"]), users=len(iam["users"]))
        async with db.system_tx() as conn:
            for path in sorted((cfg / "manifests" / "capabilities").glob("*.json")):
                cm = validate_capability_manifest(json.loads(path.read_text()))
                await conn.execute(
                    """INSERT INTO capability_definitions(capability_id, description, implementation_ref, side_effect_class,
                           requires_approval, owner_team, result_type, manifest) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (capability_id) DO UPDATE SET manifest = excluded.manifest""",
                    (cm.capability_id, cm.description, f"{cm.implementation.service_ref}#{cm.implementation.operation}",
                     cm.side_effect_class, cm.requires_approval, cm.owner_team, cm.result_type,
                     Jsonb(cm.model_dump(mode="json"))),
                )
            for f in json.loads((cfg / "features.json").read_text()):
                await conn.execute(
                    """INSERT INTO feature_definitions(feature_id, name, description) VALUES (%s,%s,%s)
                       ON CONFLICT (feature_id) DO UPDATE SET name = excluded.name, description = excluded.description""",
                    (f["feature_id"], f["name"], f["description"]),
                )
            for d in json.loads((cfg / "datasets.json").read_text()):
                await conn.execute(
                    """INSERT INTO dataset_definitions(dataset_id, name, description, source, classification, licence)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (dataset_id) DO UPDATE SET name = excluded.name,
                         description = excluded.description, licence = excluded.licence""",
                    (d["dataset_id"], d["name"], d["description"], d["source"], d["classification"], d["licence"]),
                )
            for path in sorted((cfg / "manifests" / "agents").glob("*.json")):
                doc = json.loads(path.read_text())
                am = validate_agent_manifest(doc["manifest"])
                await register_agent(conn, am, doc["kind"])
                counts["agents"] += 1
            for d in json.loads((cfg / "deployments.json").read_text()):
                await conn.execute(
                    """INSERT INTO agent_deployments(agent_id, implementation_version, environment, service_ref, protocol, healthy)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (agent_id, implementation_version, environment) DO NOTHING""",
                    (d["agent_id"], d["implementation_version"], d["environment"], d["service_ref"], d["protocol"], d["healthy"]),
                )
            for path in sorted((cfg / "releases").glob("*.json")):
                await upsert_release(conn, validate_release(json.loads(path.read_text())), "seed")

        # Dev only: external A2A agents (separate services). Registered unhealthy; the worker's health check
        # marks them available as soon as their Agent Card answers.
        ext_path = cfg / "external-agents.dev.json"
        if s.env == "dev" and ext_path.exists():
            async with db.system_tx() as conn:
                for e in json.loads(ext_path.read_text())["agents"]:
                    am = validate_agent_manifest({
                        "schema_version": "1.0", "agent_id": e["agent_id"], "name": e["name"], "description": e["description"],
                        "owner_team": e["owner_team"], "implementation_version": e["version"], "capabilities": [],
                        "allowed_tools": [], "data_classification_ceiling": "internal", "publication_status": "pilot",
                        "supported_run_modes": ["interactive"],
                        "deployment": {"protocol": "a2a", "service_ref": e["url"], "trust": "external", "stream_profile": "a2a"},
                    })
                    await register_agent(conn, am, "specialist")
                    await conn.execute(
                        """INSERT INTO agent_deployments(agent_id, implementation_version, environment, service_ref, protocol, healthy)
                           VALUES (%s,%s,'local',%s,'a2a',false) ON CONFLICT (agent_id, implementation_version, environment) DO NOTHING""",
                        (am.agent_id, am.implementation_version, e["url"]),
                    )
                    await upsert_release(conn, validate_release({
                        "schema_version": "1.0", "revision": "dev.1", "release_key": e.get("release_key", f"{am.agent_id}-pilot"), "kind": "agent",
                        "agent_id": am.agent_id, "agent_version": am.implementation_version, "stage": e["stage"], "published": True,
                        "pod_allowlist": e["pods"], "eligible_groups": ["research_users"],
                        "feature_flags": {"catalogue_visible": True, "direct_invoke": True, "auto_route": e["stage"] == "POD_PILOT_AUTO",
                                          "background_execution": False, "proactive_alerts": False, "capability_enabled": False,
                                          "emergency_disabled": False},
                        "user_fraction_percent": 100, "stable_salt_id": f"{am.agent_id}-dev",
                        "runtime_canary": {"stable_version": am.implementation_version, "candidate_fraction_percent": 0},
                        "notes": "External A2A agent (dev seed): question-only, no PodOS delegation.",
                    }), "seed")
                    counts["agents"] += 1

        # Authorization graph: directory + seed template -> OpenFGA tuples.
        fga = FGAClient(s.fga_url, s.fga_api_key, s.fga_store)
        try:
            if s.env in {"dev", "test"}:
                counts["authz_tuples"] = await seed_store(fga, db, s.access_template_path, env=s.env)
            else:
                counts["authz_tuples"] = await provision_additive(fga, db, s.access_template_path)
        finally:
            await fga.close()

        research = json.loads((cfg / "seed" / "research.json").read_text())
        for pod_id, pod_data in research["pods"].items():
            async with db.pod_tx(pod_id) as conn:
                existing = await fetchone(conn, "SELECT count(*) AS n FROM research_cases WHERE pod_id = %s", (pod_id,))
                if existing and existing["n"] > 0:
                    continue
                for case in pod_data["cases"]:
                    row = await fetchone(
                        conn,
                        """INSERT INTO research_cases(pod_id, title, thesis, tickers, owner_user_id)
                           VALUES (%s,%s,%s,%s,%s) RETURNING case_id""",
                        (pod_id, case["title"], case["thesis"], case["tickers"], case["owner"]),
                    )
                    for d in case["documents"]:
                        await conn.execute(
                            """INSERT INTO research_documents(pod_id, index_name, case_id, kind, title, ticker, body, source,
                                   published_at, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (pod_id, index_name_for(pod_id), row["case_id"], d["kind"], d["title"], d["ticker"], d["body"],
                             d["source"], datetime.fromisoformat(d["published_at"].replace("Z", "+00:00")), d["author"]),
                        )
                        counts["documents"] += 1
        for m in research["monitors"]:
            async with db.pod_tx(m["pod_id"]) as conn:
                exists = await fetchone(conn, "SELECT 1 FROM monitors WHERE pod_id = %s AND configuration->>'name' = %s",
                                        (m["pod_id"], m["name"]))
                if exists:
                    continue
                cfgm = {k: m[k] for k in ("name", "tickers", "event_types", "keywords", "approval_recipient", "notify")}
                cfgm["checkpoint_seed"] = True
                await conn.execute(
                    """INSERT INTO monitors(pod_id, owner_user_id, configuration, name, schedule_seconds, checkpoint, next_run_at)
                       VALUES (%s,%s,%s,%s,%s,%s, now() + interval '1 hour')""",
                    (m["pod_id"], m["owner"], Jsonb(cfgm), m["name"], m["schedule_seconds"],
                     Jsonb({"last_seen": "2026-10-01T00:00:00+00:00"})),
                )
                counts["monitors"] += 1
        # Routing metadata for event triggers (derived from monitors and cases), and issuer entities.
        async with db.system_tx() as conn:
            for e in json.loads((cfg / "seed" / "edgar-mock.json").read_text())["entities"]:
                await conn.execute(
                    "INSERT INTO entities(ticker, name, cik) VALUES (%s,%s,%s) ON CONFLICT (ticker) DO NOTHING",
                    (e["ticker"], e["name"], e["cik"]),
                )
        for p in iam["pods"]:
            await refresh_watchlist(db, p["pod_id"])
        # Pull connectors start watching from provisioning time (no backfill flood on first poll).
        if s.connectors_path.exists():
            async with db.system_tx() as conn:
                for c in json.loads(s.connectors_path.read_text()):
                    if c.get("transport") == "mcp" and not c.get("backfill"):
                        await conn.execute(
                            """INSERT INTO connector_cursors(connector_id, cursor)
                               VALUES (%s, to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
                               ON CONFLICT (connector_id) DO NOTHING""",
                            (c["connector_id"],),
                        )
        # (Re)build each pod's search index from the system of record, using that pod's own credential.
        backend = make_backend(s, db)
        for p in iam["pods"]:
            counts[f"indexed_{p['pod_id']}"] = await PodResearchIndex(db, backend, p["pod_id"]).reindex()
    finally:
        await db.close()
    return counts


if __name__ == "__main__":
    print(asyncio.run(seed()))
