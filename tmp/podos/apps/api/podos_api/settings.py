from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    env: str = field(default_factory=lambda: _env("PODOS_ENV", "dev"))
    database_url: str = field(
        default_factory=lambda: _env(
            "PODOS_DATABASE_URL", "postgresql://podos_app:podos_app@127.0.0.1:55432/podos"
        )
    )
    owner_database_url: str = field(
        default_factory=lambda: _env(
            "PODOS_OWNER_DATABASE_URL", "postgresql://podos_owner:podos_owner@127.0.0.1:55432/podos"
        )
    )
    # Dev-only secrets. Production: per-workload identities from the platform secret store.
    session_secret: str = field(default_factory=lambda: _env("PODOS_SESSION_SECRET", "dev-session-secret-change-me"))
    delegation_secret: str = field(
        default_factory=lambda: _env("PODOS_DELEGATION_SECRET", "dev-delegation-secret-change-me")
    )
    download_secret: str = field(default_factory=lambda: _env("PODOS_DOWNLOAD_SECRET", "dev-download-secret-change-me"))
    workload_secrets_path: Path = field(
        default_factory=lambda: Path(_env("PODOS_WORKLOAD_SECRETS", str(ROOT / "config" / "workload_secrets.dev.json")))
    )
    config_dir: Path = field(default_factory=lambda: Path(_env("PODOS_CONFIG_DIR", str(ROOT / "config"))))
    policy_path: Path = field(
        default_factory=lambda: Path(_env("PODOS_POLICY_PATH", str(ROOT / "config" / "policy.json")))
    )
    access_template_path: Path = field(
        default_factory=lambda: Path(_env("PODOS_ACCESS_TEMPLATE", str(ROOT / "config" / "access-template.json")))
    )
    # OpenFGA (authorization). Dev key matches docker-compose; production: from the secret store.
    fga_url: str = field(default_factory=lambda: _env("PODOS_FGA_URL", "http://127.0.0.1:58080"))
    fga_api_key: str = field(default_factory=lambda: _env("PODOS_FGA_API_KEY", "podos-dev-fga-key-2026"))
    fga_store: str = field(default_factory=lambda: _env("PODOS_FGA_STORE", "podos"))
    connectors_path: Path = field(
        default_factory=lambda: Path(_env("PODOS_CONNECTORS", str(ROOT / "config" / "connectors.local.json")))
    )
    endpoints_path: Path = field(
        default_factory=lambda: Path(_env("PODOS_ENDPOINTS", str(ROOT / "config" / "endpoints.local.json")))
    )
    object_root: Path = field(default_factory=lambda: Path(_env("PODOS_OBJECT_ROOT", str(ROOT / "var" / "objects"))))
    mail_root: Path = field(default_factory=lambda: Path(_env("PODOS_MAIL_ROOT", str(ROOT / "var" / "mail"))))
    public_api_url: str = field(default_factory=lambda: _env("PODOS_PUBLIC_API_URL", "http://localhost:8400"))
    internal_api_url: str = field(default_factory=lambda: _env("PODOS_INTERNAL_API_URL", "http://127.0.0.1:8400"))
    web_origins: list[str] = field(
        default_factory=lambda: _env("PODOS_WEB_ORIGINS", "http://localhost:3400,http://127.0.0.1:3400").split(",")
    )
    session_ttl_seconds: int = 8 * 3600
    delegation_ttl_seconds: int = 300
    download_ttl_seconds: int = 60
    agent_timeout_seconds: float = field(default_factory=lambda: float(_env("PODOS_AGENT_TIMEOUT", "30")))
    # --- data plane (docker-compose locally; managed services later) ---
    search_backend: str = field(default_factory=lambda: _env("PODOS_SEARCH_BACKEND", "opensearch"))
    opensearch_url: str = field(default_factory=lambda: _env("PODOS_OPENSEARCH_URL", "https://127.0.0.1:59200"))
    # Dev uses the OpenSearch demo certificate. Production: a CA bundle path here.
    opensearch_verify: str = field(default_factory=lambda: _env("PODOS_OPENSEARCH_VERIFY", "false"))
    index_namespace: str = field(default_factory=lambda: _env("PODOS_INDEX_NAMESPACE", ""))
    object_backend: str = field(default_factory=lambda: _env("PODOS_OBJECT_BACKEND", "s3"))
    s3_endpoint: str = field(default_factory=lambda: _env("PODOS_S3_ENDPOINT", "127.0.0.1:59100"))
    s3_bucket: str = field(default_factory=lambda: _env("PODOS_S3_BUCKET", "podos-artifacts"))
    s3_secure: bool = field(default_factory=lambda: _env("PODOS_S3_SECURE", "0") == "1")
    data_plane_secrets_path: Path = field(
        default_factory=lambda: Path(_env("PODOS_DATA_PLANE_SECRETS", str(ROOT / "config" / "data_plane.dev.json")))
    )
    email_backend: str = field(default_factory=lambda: _env("PODOS_EMAIL_BACKEND", "smtp"))
    smtp_host: str = field(default_factory=lambda: _env("PODOS_SMTP_HOST", "127.0.0.1"))
    smtp_port: int = field(default_factory=lambda: int(_env("PODOS_SMTP_PORT", "51025")))
    email_provider_broken: bool = field(default_factory=lambda: _env("PODOS_EMAIL_BROKEN", "0") == "1")
    outbox_max_attempts: int = 5
    outbox_lease_seconds: int = 30

    @property
    def dev_login_enabled(self) -> bool:
        return self.env in {"dev", "test"}

    def endpoints(self) -> dict[str, str]:
        data = json.loads(self.endpoints_path.read_text())
        return {k: v for k, v in data.items() if not k.startswith("_")}

    def data_plane_secrets(self) -> dict:
        return json.loads(self.data_plane_secrets_path.read_text())

    def workload_secrets(self) -> dict[str, str]:
        return json.loads(self.workload_secrets_path.read_text())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
