"""Migration runner. Runs as the owner role only; never used by request traffic."""

from __future__ import annotations

import sys

import psycopg

from .settings import ROOT, get_settings

MIGRATIONS = ROOT / "infra" / "migrations"

TENANT_TABLES = [
    "call_events", "attention_events",
    "notification_deliveries", "notification_preferences", "ingested_agent_events", "work_item_events",
    "approval_decisions", "outbox_events", "work_items", "artifacts", "run_events", "messages", "agent_runs",
    "monitors", "threads", "research_documents", "research_cases",
]
CONTROL_TABLES = [
    "webhook_nonces", "event_deliveries", "events", "connector_cursors", "watchlists", "entities",
    "access_changes", "dataset_definitions", "feature_definitions", "audit_events", "release_policies", "agent_capability_support", "agent_deployments", "capability_definitions",
    "agent_definitions", "pod_memberships", "users", "pods",
]


def migrate(owner_url: str | None = None) -> list[str]:
    url = owner_url or get_settings().owner_database_url
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def truncate_all(owner_url: str | None = None) -> None:
    """Test/demo reset. TRUNCATE is not subject to RLS, hence owner-only."""
    url = owner_url or get_settings().owner_database_url
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TENANT_TABLES + CONTROL_TABLES)} RESTART IDENTITY CASCADE")


if __name__ == "__main__":
    names = migrate()
    print("applied:", names or "nothing (up to date)")
    if "--reset" in sys.argv:
        truncate_all()
        print("truncated all tables")
