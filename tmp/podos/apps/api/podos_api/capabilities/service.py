"""Capability execution. Requires a ``CapabilityGrant`` (proof that the full
permission algorithm passed) and is idempotent on ``(pod_id, idempotency_key)``."""

from __future__ import annotations

import re
import uuid
from typing import Any

from podos_contracts.models import ArtifactRef, DocumentSpec

from ..db import Database, Jsonb, fetchone
from ..errors import Problem
from ..governance import CapabilityGrant
from ..objectstore import LocalObjectStore, ObjectStoreError, S3ObjectStore
from .documents import RENDERERS


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "document"


def artifact_ref(row: dict[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=str(row["artifact_id"]), pod_id=row["pod_id"], filename=row["filename"],
        mime_type=row["mime_type"], classification=row["classification"], capability_id=row["capability_id"],
        title=row["title"], size_bytes=row["size_bytes"],
    )


class CapabilityService:
    def __init__(self, db: Database, store: LocalObjectStore | S3ObjectStore) -> None:
        self.db = db
        self.store = store

    async def render_document(
        self, grant: CapabilityGrant, spec: DocumentSpec, *, idempotency_key: str, pod_label: str,
        run_id: str | None = None, case_id: str | None = None, provenance: dict[str, Any] | None = None,
    ) -> tuple[ArtifactRef, bool]:
        """Returns (artifact, created). Replays return the original artifact."""
        if grant.capability_id not in RENDERERS:
            raise Problem(400, "capability_not_renderable")
        async with self.db.pod_tx(grant.pod_id) as conn:
            existing = await fetchone(
                conn, "SELECT * FROM artifacts WHERE pod_id = %s AND idempotency_key = %s",
                (grant.pod_id, idempotency_key),
            )
        if existing:
            return artifact_ref(existing), False

        renderer, mime, ext = RENDERERS[grant.capability_id]
        # Output inherits the higher of the spec and the grant classification.
        spec = spec.model_copy(update={"classification": grant.classification})
        data = renderer(spec, pod_label=pod_label)
        artifact_id = str(uuid.uuid4())
        filename = f"{_slug(spec.title)}.{ext}"
        object_key = f"pods/{grant.pod_id}/artifacts/{artifact_id}/{filename}"
        try:
            sha = await self.store.put(grant.pod_id, object_key, data, content_type=mime)
        except ObjectStoreError as exc:
            raise Problem(503, "artifact_storage_failed", "artifact storage unavailable") from exc
        prov = {
            **(provenance or {}),
            "capability_id": grant.capability_id,
            "requested_by": grant.subject_id,
            "via_agent": grant.via_agent,
            "via_workload": grant.via_workload,
            "citations": [c.doc_id for c in spec.citations],
        }
        async with self.db.pod_tx(grant.pod_id) as conn:
            row = await fetchone(
                conn,
                """INSERT INTO artifacts (artifact_id, pod_id, run_id, case_id, object_key, mime_type, classification,
                       created_by, filename, size_bytes, sha256, capability_id, idempotency_key, provenance, title)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (pod_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                   RETURNING *""",
                (artifact_id, grant.pod_id, run_id, case_id, object_key, mime, grant.classification.value,
                 grant.subject_id, filename, len(data), sha, grant.capability_id, idempotency_key, Jsonb(prov),
                 spec.title),
            )
            if row is None:  # lost a concurrent race: return the winner
                row = await fetchone(
                    conn, "SELECT * FROM artifacts WHERE pod_id = %s AND idempotency_key = %s",
                    (grant.pod_id, idempotency_key),
                )
                assert row is not None
                return artifact_ref(row), False
        return artifact_ref(row), True
