"""Typed contracts (schema_version 1.0).

These mirror ``schemas/*.json``. The JSON schemas are the wire contract for
manifests and agent events; tests validate seed manifests against them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Classification(StrEnum):
    public = "public"
    internal = "internal"
    pod_confidential = "pod_confidential"
    restricted = "restricted"


CLASSIFICATION_RANK = {c: i for i, c in enumerate(Classification)}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Execution context (ARCHITECTURE.md section 3.1)
# ---------------------------------------------------------------------------
class Entitlements(Strict):
    """What the subject may do in the active pod, resolved from OpenFGA when the context is built.

    Resolved per request (no long-lived cache), so a revoked grant stops working on the next call.
    Rollout/release flags are evaluated separately and can only narrow these.
    """

    agents: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    clearance: Classification = Classification.pod_confidential


class PodContext(Strict):
    """Trusted, server-derived context. Never constructed from client input."""

    subject_id: str
    active_pod_id: str
    pod_role: Literal["pm", "analyst"]
    groups: list[str]
    platform_roles: list[str] = Field(default_factory=list)
    session_id: str
    permissions_revision: str
    release_revision: str = ""
    request_id: str
    trace_id: str
    data_classification: Classification = Classification.pod_confidential
    purpose: str = "research"
    delegation_depth: int = 0
    expiry: datetime | None = None
    entitlements: Entitlements = Field(default_factory=Entitlements)


class DelegationClaims(Strict):
    """Signed, audience-restricted subset of PodContext passed to agents."""

    sub: str
    pod: str
    run: str
    agent: str
    caps: list[str]
    depth: int
    purpose: str
    cls: Classification
    rev: str


# ---------------------------------------------------------------------------
# Registry manifests (contracts/agent-manifest.schema.json etc.)
# ---------------------------------------------------------------------------
class AgentDeploymentSpec(Strict):
    protocol: Literal["a2a", "local_adk"]
    service_ref: str
    agent_card_url: str | None = None
    image_digest: str | None = None
    runtime_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    # "platform": a PodOS-built agent (gets a scoped delegation and may call PodOS tools).
    # "external": any other A2A agent. It receives only the user's question: no delegation, no internal URL,
    # no pod identifiers, no research context.
    trust: Literal["platform", "external"] = "platform"
    # How its stream is read: "podos" (status text = answer deltas, PodOS events) or "a2a" (status text =
    # progress, the answer arrives as an artifact with optional citations/verification data).
    stream_profile: Literal["podos", "a2a"] = "podos"
    # Name of a secret holding a bearer token for the agent's A2A endpoint (env PODOS_SECRET_<NAME>).
    auth_secret_ref: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


PublicationStatus = Literal["draft", "validating", "approved", "pilot", "ga", "deprecated", "retired"]


class AgentManifest(Strict):
    schema_version: Literal["1.0"]
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,63}$")
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=1000)
    owner_team: str
    implementation_version: str
    deployment: AgentDeploymentSpec
    capabilities: list[str]
    allowed_tools: list[str] = Field(default_factory=list)
    data_classification_ceiling: Classification
    publication_status: PublicationStatus = "draft"
    supported_run_modes: list[Literal["interactive", "background", "scheduled"]] = Field(
        default_factory=lambda: ["interactive"]
    )
    health_endpoint_ref: str | None = None


class CapabilityImplementation(Strict):
    protocol: Literal["mcp", "http", "python_internal", "workflow"]
    service_ref: str
    operation: str


class CapabilityManifest(Strict):
    schema_version: Literal["1.0"]
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    description: str = ""
    owner_team: str
    implementation: CapabilityImplementation
    input_schema_ref: str
    result_type: Literal["json", "artifact", "event", "none"]
    side_effect_class: Literal["read_only", "local_artifact", "external_write", "irreversible"]
    requires_approval: bool = False
    supports_idempotency: bool = True
    default_timeout_seconds: int | None = None


# ---------------------------------------------------------------------------
# Release policy (contracts/release-policy.example.json)
# ---------------------------------------------------------------------------
RolloutStage = Literal[
    "DRAFT", "SHADOW", "POD_PILOT_DIRECT", "POD_PILOT_AUTO", "MULTIPOD", "GA", "PAUSED", "DISABLED", "ROLLED_BACK"
]
INACTIVE_STAGES = {"DRAFT", "SHADOW", "PAUSED", "DISABLED", "ROLLED_BACK"}


class FeatureFlags(Strict):
    catalogue_visible: bool = False
    direct_invoke: bool = False
    auto_route: bool = False
    background_execution: bool = False
    proactive_alerts: bool = False
    capability_enabled: bool = False
    emergency_disabled: bool = False


class RuntimeCanary(Strict):
    stable_version: str
    candidate_version: str | None = None
    candidate_fraction_percent: int = Field(default=0, ge=0, le=100)


class ReleasePolicy(Strict):
    schema_version: Literal["1.0"] = "1.0"
    revision: str
    release_key: str
    kind: Literal["agent", "capability"] = "agent"
    agent_id: str | None = None
    capability_id: str | None = None
    agent_version: str | None = None
    stage: RolloutStage = "DRAFT"
    published: bool = False
    pod_allowlist: list[str] = Field(default_factory=list)
    eligible_groups: list[str] = Field(default_factory=list)
    feature_flags: FeatureFlags = Field(default_factory=FeatureFlags)
    user_fraction_percent: int = Field(default=100, ge=0, le=100)
    stable_salt_id: str
    runtime_canary: RuntimeCanary | None = None
    notes: str = ""


# ---------------------------------------------------------------------------
# Agent events (contracts/agent-event.schema.json)
# ---------------------------------------------------------------------------
AgentEventType = Literal[
    "research_completed", "monitor_alert", "approval_requested", "run_failed", "workflow_completed"
]


class SubjectResource(Strict):
    kind: str
    id: str


class AgentEvent(Strict):
    schema_version: Literal["1.0"]
    event_id: str = Field(min_length=8)
    pod_id: str = Field(min_length=2)
    agent_id: str = Field(min_length=3)
    run_id: str = Field(min_length=8)
    correlation_id: str | None = None
    type: AgentEventType
    classification: Classification
    recipient_user_ids: list[str] = Field(min_length=1)
    subject_resource: SubjectResource | None = None
    created_at: datetime
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Work items / approvals / artifacts / documents
# ---------------------------------------------------------------------------
class DecisionCommand(Strict):
    decision: Literal["approve", "reject", "request_changes"]
    expected_version: int
    idempotency_key: str = Field(min_length=8, max_length=200)
    comment: str = Field(default="", max_length=2000)


class ArtifactRef(Strict):
    artifact_id: str
    pod_id: str
    filename: str
    mime_type: str
    classification: Classification
    capability_id: str | None = None
    title: str = ""
    size_bytes: int = 0


class Citation(Strict):
    ref: int
    doc_id: str
    title: str
    source: str
    published_at: str | None = None


class SlideSpec(Strict):
    title: str = Field(max_length=200)
    bullets: list[str] = Field(default_factory=list, max_length=12)


class DocumentSpec(Strict):
    """Typed output contract. Agents produce this, never binary file bodies."""

    title: str = Field(max_length=200)
    subtitle: str = Field(default="", max_length=300)
    slides: list[SlideSpec] = Field(default_factory=list, max_length=30)
    sections: list[SlideSpec] = Field(default_factory=list, max_length=50)
    citations: list[Citation] = Field(default_factory=list)
    classification: Classification = Classification.pod_confidential


class RunRequest(Strict):
    mode: Literal["auto", "direct"]
    agent_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    research_case_id: str | None = None
    call_id: str | None = Field(default=None, max_length=200)
    allowed_output_capabilities: list[str] = Field(default_factory=list)
    client_request_id: str | None = Field(default=None, max_length=100)


# SSE event types (ARCHITECTURE.md 8.2)
RunEventType = Literal[
    "run.started",
    "message.delta",
    "agent.selected",
    "tool.started",
    "tool.completed",
    "artifact.created",
    "approval.requested",
    "run.completed",
    "run.failed",
    "stream.terminated",
]
TERMINAL_RUN_EVENTS = {"run.completed", "run.failed", "stream.terminated"}
