"""Release evaluation through OpenFeature.

The provider evaluates ``<release_key>/<flag>`` keys against the control-plane
snapshot of the current request (a ContextVar, loaded fresh from Postgres per
request so admin changes and emergency disables apply without any redeploy).

Ordering, per ARCHITECTURE.md 9.1/9.2: emergency off -> published/stage ->
pod allowlist -> eligible groups -> stable user cohort -> flag value.
All of these can only *reduce* availability; entitlement is checked separately.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from typing import Any

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagResolutionDetails, Reason
from openfeature.provider import AbstractProvider, Metadata
from podos_contracts.models import INACTIVE_STAGES, ReleasePolicy

DOMAIN = "podos-releases"
_current_releases: ContextVar[dict[str, ReleasePolicy] | None] = ContextVar("podos_releases", default=None)


def stable_bucket(*parts: str) -> int:
    """Deterministic 0..9999 bucket. Never Python's process-randomised hash()."""
    digest = hashlib.sha256(":".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") % 10000


def bind_releases(releases: dict[str, ReleasePolicy]) -> None:
    _current_releases.set(releases)


class ReleasePolicyProvider(AbstractProvider):
    def get_metadata(self) -> Metadata:
        return Metadata(name="podos-release-policy")

    def _gate(self, rel: ReleasePolicy, ctx: EvaluationContext | None) -> tuple[bool, str]:
        attrs: dict[str, Any] = dict(ctx.attributes) if ctx else {}
        pod_id = attrs.get("pod_id", "")
        groups = set(attrs.get("groups", []) or [])
        user = (ctx.targeting_key if ctx else None) or ""
        if rel.feature_flags.emergency_disabled:
            return False, "emergency_disabled"
        if not rel.published or rel.stage in INACTIVE_STAGES:
            return False, f"stage_{rel.stage.lower()}"
        if pod_id not in rel.pod_allowlist:
            return False, "pod_not_in_release"
        if rel.eligible_groups and not (groups & set(rel.eligible_groups)):
            return False, "group_not_eligible"
        if rel.user_fraction_percent < 100:
            if stable_bucket(rel.release_key, pod_id, user, rel.stable_salt_id) >= rel.user_fraction_percent * 100:
                return False, "outside_cohort"
        return True, "eligible"

    def _lookup(self, flag_key: str) -> tuple[ReleasePolicy | None, str]:
        release_key, _, flag = flag_key.rpartition("/")
        rel = (_current_releases.get() or {}).get(release_key)
        return rel, flag

    def resolve_boolean_details(self, flag_key, default_value, evaluation_context=None):
        rel, flag = self._lookup(flag_key)
        if rel is None:
            return FlagResolutionDetails(value=False, reason=Reason.ERROR, error_code=ErrorCode.FLAG_NOT_FOUND)
        if flag == "emergency_disabled":
            return FlagResolutionDetails(value=rel.feature_flags.emergency_disabled, reason=Reason.STATIC)
        ok, why = self._gate(rel, evaluation_context)
        if not ok:
            return FlagResolutionDetails(value=False, reason=Reason.DISABLED, variant=why)
        value = bool(getattr(rel.feature_flags, flag, False))
        return FlagResolutionDetails(value=value, reason=Reason.TARGETING_MATCH, variant="on" if value else "flag_off")

    def resolve_string_details(self, flag_key, default_value, evaluation_context=None):
        rel, flag = self._lookup(flag_key)
        if rel is None or flag != "deployment_version":
            return FlagResolutionDetails(value=default_value, reason=Reason.ERROR, error_code=ErrorCode.FLAG_NOT_FOUND)
        canary = rel.runtime_canary
        if canary is None:
            return FlagResolutionDetails(value=rel.agent_version or default_value, reason=Reason.STATIC)
        attrs = dict(evaluation_context.attributes) if evaluation_context else {}
        user = (evaluation_context.targeting_key if evaluation_context else None) or ""
        if canary.candidate_version and canary.candidate_fraction_percent > 0:
            bucket = stable_bucket(rel.release_key, "canary", attrs.get("pod_id", ""), user, rel.stable_salt_id)
            if bucket < canary.candidate_fraction_percent * 100:
                return FlagResolutionDetails(value=canary.candidate_version, reason=Reason.SPLIT, variant="candidate")
        return FlagResolutionDetails(value=canary.stable_version, reason=Reason.SPLIT, variant="stable")

    def resolve_integer_details(self, flag_key, default_value, evaluation_context=None):
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)

    def resolve_float_details(self, flag_key, default_value, evaluation_context=None):
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)

    def resolve_object_details(self, flag_key, default_value, evaluation_context=None):
        return FlagResolutionDetails(value=default_value, reason=Reason.DEFAULT)


_installed = False


def install_provider() -> None:
    global _installed
    if not _installed:
        api.set_provider(ReleasePolicyProvider(), domain=DOMAIN)
        _installed = True


class ReleaseEvaluator:
    """Thin facade over the OpenFeature client used by permission checks."""

    def __init__(self) -> None:
        install_provider()
        self.client = api.get_client(domain=DOMAIN)

    @staticmethod
    def _ctx(subject_id: str, pod_id: str, groups: list[str]) -> EvaluationContext:
        return EvaluationContext(targeting_key=subject_id, attributes={"pod_id": pod_id, "groups": list(groups)})

    def flag(self, release_key: str, flag: str, *, subject_id: str, pod_id: str, groups: list[str]) -> tuple[bool, str]:
        d = self.client.get_boolean_details(f"{release_key}/{flag}", False, self._ctx(subject_id, pod_id, groups))
        return bool(d.value), (d.variant or str(d.reason))

    def version(self, release_key: str, *, subject_id: str, pod_id: str, groups: list[str], default: str) -> tuple[str, str]:
        d = self.client.get_string_details(
            f"{release_key}/deployment_version", default, self._ctx(subject_id, pod_id, groups)
        )
        return d.value, d.variant or "stable"
