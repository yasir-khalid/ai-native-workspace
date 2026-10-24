"""Pod-prefixed object storage.

Keys are always ``pods/<pod_id>/...``. Both backends verify the prefix against
the validated pod id before any I/O.

- ``S3ObjectStore`` (default; MinIO locally, S3/GCS-compatible in production):
  each pod has its own access key whose IAM policy only allows
  ``<bucket>/pods/<pod_id>/*``, so the storage service itself rejects
  cross-pod reads even if application code were wrong.
- ``LocalObjectStore``: filesystem fallback (``PODOS_OBJECT_BACKEND=local``).

Downloads are streamed by the API after re-authorisation rather than via
pre-signed storage URLs, because a pre-signed URL cannot re-check pod
membership at redemption time.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

from minio import Minio
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError as Urllib3Error

from .settings import Settings


class ObjectStoreError(Exception):
    pass


def check_prefix(pod_id: str, object_key: str) -> None:
    if not object_key.startswith(f"pods/{pod_id}/") or ".." in object_key.split("/"):
        raise ObjectStoreError("object key outside pod prefix")


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, pod_id: str, object_key: str) -> Path:
        check_prefix(pod_id, object_key)
        path = (self.root / object_key).resolve()
        if not str(path).startswith(str((self.root / "pods" / pod_id).resolve())):
            raise ObjectStoreError("object key outside pod prefix")
        return path

    async def put(self, pod_id: str, object_key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(pod_id, object_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise ObjectStoreError("write failed") from exc
        return hashlib.sha256(data).hexdigest()

    async def get(self, pod_id: str, object_key: str) -> bytes:
        path = self._path(pod_id, object_key)
        if not path.exists():
            raise ObjectStoreError("object missing")
        return path.read_bytes()


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._clients: dict[str, Minio] = {}

    def _client(self, pod_id: str) -> Minio:
        if pod_id not in self._clients:
            cred = self.s.data_plane_secrets().get("s3", {}).get(pod_id)
            if not cred:
                raise ObjectStoreError("no storage credential provisioned for pod")
            self._clients[pod_id] = Minio(self.s.s3_endpoint, access_key=cred["access_key"],
                                          secret_key=cred["secret_key"], secure=self.s.s3_secure,
                                          region="us-east-1")
        return self._clients[pod_id]

    async def put(self, pod_id: str, object_key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        check_prefix(pod_id, object_key)
        client = self._client(pod_id)
        try:
            await asyncio.to_thread(client.put_object, self.s.s3_bucket, object_key, io.BytesIO(data), len(data),
                                    content_type=content_type)
        except (MinioException, Urllib3Error, OSError) as exc:
            raise ObjectStoreError(f"put failed: {type(exc).__name__}") from exc
        return hashlib.sha256(data).hexdigest()

    async def get(self, pod_id: str, object_key: str) -> bytes:
        check_prefix(pod_id, object_key)
        client = self._client(pod_id)

        def _read() -> bytes:
            resp = client.get_object(self.s.s3_bucket, object_key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        try:
            return await asyncio.to_thread(_read)
        except S3Error as exc:
            raise ObjectStoreError(f"get failed: {exc.code}") from exc
        except (MinioException, Urllib3Error, OSError) as exc:
            raise ObjectStoreError(f"get failed: {type(exc).__name__}") from exc


def make_store(settings: Settings) -> LocalObjectStore | S3ObjectStore:
    return S3ObjectStore(settings) if settings.object_backend == "s3" else LocalObjectStore(settings.object_root)
