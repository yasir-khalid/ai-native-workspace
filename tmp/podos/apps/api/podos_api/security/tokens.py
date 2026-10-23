"""Signed tokens. Each token type has its own key and audience so one can never
be replayed as another (a download link is not a session, a delegation is not a
workload identity)."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from podos_contracts.models import DelegationClaims

from ..settings import Settings

ALG = "HS256"
AUD_SESSION = "podos-web"
AUD_DELEGATION = "podos-tools"
AUD_WORKLOAD = "podos-internal"
AUD_DOWNLOAD = "podos-download"


class TokenError(Exception):
    pass


def _encode(payload: dict[str, Any], secret: str, ttl: int, aud: str) -> str:
    now = int(time.time())
    return jwt.encode({**payload, "aud": aud, "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex}, secret, ALG)


def _decode(token: str, secret: str, aud: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, secret, algorithms=[ALG], audience=aud, options={"require": ["exp", "aud"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


# Sessions (mock OIDC). Production: OIDC id_token -> server session.
def issue_session(s: Settings, subject_id: str) -> str:
    return _encode({"sub": subject_id, "sid": uuid.uuid4().hex}, s.session_secret, s.session_ttl_seconds, AUD_SESSION)


def verify_session(s: Settings, token: str) -> dict[str, Any]:
    return _decode(token, s.session_secret, AUD_SESSION)


# Delegations: what a specific agent run may do on behalf of a user in one pod.
def issue_delegation(s: Settings, claims: DelegationClaims, ttl: int | None = None) -> str:
    return _encode(claims.model_dump(mode="json"), s.delegation_secret, ttl or s.delegation_ttl_seconds, AUD_DELEGATION)


def verify_delegation(s: Settings, token: str) -> DelegationClaims:
    data = _decode(token, s.delegation_secret, AUD_DELEGATION)
    return DelegationClaims.model_validate({k: v for k, v in data.items() if k in DelegationClaims.model_fields})


# Workload identity: an agent/worker proves *which service* is calling.
def issue_workload(secret: str, workload_id: str, ttl: int = 120) -> str:
    return _encode({"iss": workload_id, "sub": workload_id}, secret, ttl, AUD_WORKLOAD)


def verify_workload(s: Settings, token: str) -> str:
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise TokenError("malformed workload token") from exc
    workload_id = unverified.get("iss")
    secret = s.workload_secrets().get(workload_id or "")
    if not secret or workload_id.startswith("_"):
        raise TokenError("unknown workload")
    data = _decode(token, secret, AUD_WORKLOAD)
    if data.get("iss") != workload_id:
        raise TokenError("issuer mismatch")
    return workload_id


# Download grants: short-lived, bound to subject + pod + artifact. Re-authorised at redemption.
def issue_download(s: Settings, *, artifact_id: str, pod_id: str, subject_id: str) -> tuple[str, int]:
    token = _encode({"art": artifact_id, "pod": pod_id, "sub": subject_id}, s.download_secret, s.download_ttl_seconds, AUD_DOWNLOAD)
    return token, int(time.time()) + s.download_ttl_seconds


def verify_download(s: Settings, token: str) -> dict[str, Any]:
    return _decode(token, s.download_secret, AUD_DOWNLOAD)
