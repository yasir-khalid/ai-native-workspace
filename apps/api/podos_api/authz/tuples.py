"""How PodOS concepts map onto the OpenFGA graph (see infra/openfga/model.fga).

Object ids for grantable things are pod-scoped: ``agent:<pod_id>/<agent_id>``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .fga import Tuple

PLATFORM = "platform:podos"
GrantKind = Literal["agent", "capability", "dataset", "feature"]
GRANT_KINDS: tuple[GrantKind, ...] = ("agent", "capability", "dataset", "feature")
DECISION_RELATION: dict[str, str] = {
    "agent": "can_use", "capability": "can_invoke", "dataset": "can_read", "feature": "enabled_for",
}
# Pod-level actions that are relations on the pod object itself.
POD_ACTIONS: dict[str, str] = {"work_item.approve": "approver", "data.read_restricted": "restricted_reader"}


def user(subject_id: str) -> str:
    return f"user:{subject_id}"


def service(workload_id: str) -> str:
    return f"service:{workload_id}"


def pod(pod_id: str) -> str:
    return f"pod:{pod_id}"


def group(pod_id: str, name: str) -> str:
    return f"group:{pod_id}/{name}"


def scoped(kind: str, pod_id: str, item_id: str) -> str:
    return f"{kind}:{pod_id}/{item_id}"


def local_id(obj: str, pod_id: str) -> str | None:
    """'agent:pod_alpha/equity-research' -> 'equity-research' if it belongs to pod_id."""
    _, _, rest = obj.partition(":")
    prefix = f"{pod_id}/"
    return rest[len(prefix):] if rest.startswith(prefix) else None


def subject_ref(kind: str, pod_id: str, subject_id: str) -> str:
    """Grantee reference: a person, a pod group's members, or every member of the pod."""
    if kind == "user":
        return user(subject_id)
    if kind == "group":
        return f"{group(pod_id, subject_id)}#member"
    if kind == "pod":
        return f"{pod(pod_id)}#member"
    raise ValueError(f"unknown subject kind {kind}")


# ---- directory (IdP mirror) -----------------------------------------------------------------
def pod_tuples(pod_id: str) -> list[Tuple]:
    return [Tuple(PLATFORM, "platform", pod(pod_id))]


def membership_tuples(pod_id: str, subject_id: str, pod_role: str, groups: list[str]) -> list[Tuple]:
    out = [Tuple(user(subject_id), pod_role, pod(pod_id))]
    for g in groups:
        out += [Tuple(pod(pod_id), "pod", group(pod_id, g)), Tuple(user(subject_id), "member", group(pod_id, g))]
    return out


def platform_admin_tuple(subject_id: str) -> Tuple:
    return Tuple(user(subject_id), "admin", PLATFORM)


# ---- grants ---------------------------------------------------------------------------------
def grant_tuples(kind: GrantKind, pod_id: str, item_id: str, grantee: str, expires_at: datetime | None = None) -> list[Tuple]:
    obj = scoped(kind, pod_id, item_id)
    return [Tuple(pod(pod_id), "pod", obj), Tuple(grantee, "grantee", obj, expires_at)]


def action_tuple(action: str, pod_id: str, grantee: str, expires_at: datetime | None = None) -> Tuple:
    return Tuple(grantee, POD_ACTIONS[action], pod(pod_id), expires_at)


def service_grant_tuple(workload_id: str, capability_id: str) -> Tuple:
    return Tuple(service(workload_id), "caller", f"tool:{capability_id}")


def template_tuples(template: dict, pod_ids: list[str]) -> list[Tuple]:
    """Expand the seed template (config/access-template.json) into tuples for every pod."""
    out: list[Tuple] = []
    for pod_id in pod_ids:
        for gname, g in template.get("group_grants", {}).items():
            out.append(Tuple(pod(pod_id), "pod", group(pod_id, gname)))
            ref = subject_ref("group", pod_id, gname)
            for a in g.get("agents", []):
                out += grant_tuples("agent", pod_id, a, ref)
            for c in g.get("capabilities", []):
                out += grant_tuples("capability", pod_id, c, ref)
            for f in g.get("features", []):
                out += grant_tuples("feature", pod_id, f, ref)
            for d in g.get("datasets", []):
                out += grant_tuples("dataset", pod_id, d, ref)
            for act in g.get("actions", []):
                out.append(action_tuple(act, pod_id, ref))
        for f in template.get("pod_wide_features", []):
            out += grant_tuples("feature", pod_id, f, subject_ref("pod", pod_id, ""))
    # Person-specific grants stay pod-scoped and still require live pod membership.
    for grant in template.get("user_grants", []):
        if grant["pod_id"] not in pod_ids:
            raise ValueError(f"unknown pod in user grant: {grant['pod_id']}")
        for kind in GRANT_KINDS:
            for item_id in grant.get("capabilities" if kind == "capability" else f"{kind}s", []):
                out += grant_tuples(kind, grant["pod_id"], item_id, user(grant["subject_id"]))
    for wid, caps in {**template.get("agent_service_grants", {}), **template.get("workflow_service_grants", {})}.items():
        out += [service_grant_tuple(wid, c) for c in caps]
    return out
