# Agent integration and governed tools

[Documentation](README.md) · [Architecture](ARCHITECTURE.md) · [Security](SECURITY.md)

An agent reasons about work; a capability performs an authorized operation.
Publishing an agent does not grant its users or service identity every capability.

## Internal invocation

```mermaid
---
config:
  theme: "base"
  themeVariables:
    fontFamily: "Arial, Helvetica, sans-serif"
    fontSize: "15px"
    primaryColor: "#dbeafe"
    primaryTextColor: "#172554"
    primaryBorderColor: "#3b82f6"
    secondaryColor: "#ccfbf1"
    secondaryTextColor: "#134e4a"
    secondaryBorderColor: "#14b8a6"
    tertiaryColor: "#ede9fe"
    tertiaryTextColor: "#4c1d95"
    tertiaryBorderColor: "#8b5cf6"
    lineColor: "#64748b"
    textColor: "#0f172a"
    mainBkg: "#eff6ff"
    clusterBkg: "#f8fafc"
    clusterBorder: "#cbd5e1"
    edgeLabelBackground: "#ffffff"
    actorBkg: "#ede9fe"
    actorBorder: "#8b5cf6"
    actorTextColor: "#4c1d95"
    actorLineColor: "#94a3b8"
    signalColor: "#475569"
    signalTextColor: "#0f172a"
    noteBkgColor: "#fef3c7"
    noteBorderColor: "#f59e0b"
    noteTextColor: "#78350f"
    activationBkgColor: "#ccfbf1"
    activationBorderColor: "#14b8a6"
  flowchart:
    curve: "basis"
    htmlLabels: false
    nodeSpacing: 30
    rankSpacing: 45
  sequence:
    mirrorActors: false
    showSequenceNumbers: true
  fontFamily: "Arial, Helvetica, sans-serif"
---
flowchart TB
    accTitle: Internal governed agent
    accDescr: The API filters and authorizes a service, issues a scoped delegation, and rechecks every tool operation. The agent returns text and source records without widening authority.
User["Question in an active pod"]:::interface --> Eligible["Filter authorized and released agents"]:::guard
    Eligible --> Route["Auto choice or direct selection"]:::service
    Route --> Delegate["Signed pod / run / agent / capability delegation"]:::guard
    Delegate --> Agent["ADK service over A2A"]:::service
    Agent --> Tool["Internal tool API rechecks authority"]:::guard
    Tool --> Data["Pod-scoped retrieval or deterministic export"]:::store
    Data --> Agent
    Agent --> Answer["Text, tool status and citation records"]:::success
    %% podos-theme:start
    classDef interface fill:#dbeafe,stroke:#3b82f6,color:#172554,stroke-width:1.5px,rx:8px,ry:8px
    classDef service fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:1.5px,rx:8px,ry:8px
    classDef store fill:#ccfbf1,stroke:#14b8a6,color:#134e4a,stroke-width:1.5px,rx:8px,ry:8px
    classDef guard fill:#fef3c7,stroke:#f59e0b,color:#78350f,stroke-width:1.5px,rx:8px,ry:8px
    classDef success fill:#dcfce7,stroke:#22c55e,color:#14532d,stroke-width:1.5px,rx:8px,ry:8px
    classDef risk fill:#ffe4e6,stroke:#f43f5e,color:#881337,stroke-width:1.5px,rx:8px,ry:8px
    classDef neutral fill:#f1f5f9,stroke:#94a3b8,color:#334155,stroke-width:1.5px,rx:8px,ry:8px
    %% podos-theme:end
```
Internal agent metadata may contain a delegation token, internal API address,
permitted capabilities, recent conversation, research case and selected call
context. The API reads call evidence under pod scope; the client supplies an ID,
not trusted source text.

`agentic.py` exposes only delegated tools to the model. `research_search` calls the
internal governed endpoint. Export tools submit typed specifications; deterministic
renderers produce DOCX/PPTX artifacts and keep binaries out of model context.

## Contracts and registration

External agents may return a named `court_availability` artifact containing a JSON
array of venues and slots. `podos_api.presentation` validates/bounds it, and the A2A
adapter emits a deduplicated `ui.result`. The API stamps the registered agent name
as its source and persists it in the ordinary run log. Unknown result types are
not executable UI. See [chat rendering](CHAT_EVIDENCE_IMPLEMENTATION.md#structured-results-and-retained-activity)
for the component contract and saved-answer behavior.

- Agent manifests: `config/manifests/agents/` and the contracts package.
- Deployment endpoint mapping: `config/endpoints.local.json` for local services.
- A2A serving: `agents/podos_agents/serve.py` and the Agent Card.
- Release policy: database-backed, with stage, cohort, flags and emergency disable.
- User/service access: pod-scoped OpenFGA grants, distinct from release policy.

The registry validates contracts and pins the Agent Card digest. Health and release
status affect availability. Auto cannot choose an agent outside the filtered set;
the API rechecks the chosen deployment before dispatch.

## External A2A deployment

External agent integration is supported through the admin API; the current web app
has no Admin console. Start the service independently and confirm its standard
`/.well-known/agent-card.json` is available with streaming capability. Then use
`POST /v1/admin/agents/connect`, publish/release the registered agent as appropriate,
and grant user access through `/v1/admin/access/*`. Follow the live OpenAPI schema
at [localhost:8400/docs](http://localhost:8400/docs) for request fields.

For local seed-time integration, inspect `config/external-agents.dev.json` before
setup. Registrations begin with their configured health/release state; a running
process alone does not imply user access.

```mermaid
---
config:
  theme: "base"
  themeVariables:
    fontFamily: "Arial, Helvetica, sans-serif"
    fontSize: "15px"
    primaryColor: "#dbeafe"
    primaryTextColor: "#172554"
    primaryBorderColor: "#3b82f6"
    secondaryColor: "#ccfbf1"
    secondaryTextColor: "#134e4a"
    secondaryBorderColor: "#14b8a6"
    tertiaryColor: "#ede9fe"
    tertiaryTextColor: "#4c1d95"
    tertiaryBorderColor: "#8b5cf6"
    lineColor: "#64748b"
    textColor: "#0f172a"
    mainBkg: "#eff6ff"
    clusterBkg: "#f8fafc"
    clusterBorder: "#cbd5e1"
    edgeLabelBackground: "#ffffff"
    actorBkg: "#ede9fe"
    actorBorder: "#8b5cf6"
    actorTextColor: "#4c1d95"
    actorLineColor: "#94a3b8"
    signalColor: "#475569"
    signalTextColor: "#0f172a"
    noteBkgColor: "#fef3c7"
    noteBorderColor: "#f59e0b"
    noteTextColor: "#78350f"
    activationBkgColor: "#ccfbf1"
    activationBorderColor: "#14b8a6"
  flowchart:
    curve: "basis"
    htmlLabels: false
    nodeSpacing: 30
    rankSpacing: 45
  sequence:
    mirrorActors: false
    showSequenceNumbers: true
  fontFamily: "Arial, Helvetica, sans-serif"
---
flowchart TB
    accTitle: External transport boundary
    accDescr: An external deployment receives the question and an opaque conversation context, without internal delegation or private pod evidence. Public source records can be inspected in chat.
API["Workspace API"]:::service --> Question["User question + opaque A2A context ID"]:::interface
    Question --> External["External service"]:::neutral
    External --> Public["Answer and public source metadata"]:::store
    Public --> Inspect["Evidence inspector with original-source link"]:::success
    Private["Pod evidence and internal delegation"]:::guard -. "excluded from external metadata" .-> Boundary["Transport boundary"]:::risk
    %% podos-theme:start
    classDef interface fill:#dbeafe,stroke:#3b82f6,color:#172554,stroke-width:1.5px,rx:8px,ry:8px
    classDef service fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:1.5px,rx:8px,ry:8px
    classDef store fill:#ccfbf1,stroke:#14b8a6,color:#134e4a,stroke-width:1.5px,rx:8px,ry:8px
    classDef guard fill:#fef3c7,stroke:#f59e0b,color:#78350f,stroke-width:1.5px,rx:8px,ry:8px
    classDef success fill:#dcfce7,stroke:#22c55e,color:#14532d,stroke-width:1.5px,rx:8px,ry:8px
    classDef risk fill:#ffe4e6,stroke:#f43f5e,color:#881337,stroke-width:1.5px,rx:8px,ry:8px
    classDef neutral fill:#f1f5f9,stroke:#94a3b8,color:#334155,stroke-width:1.5px,rx:8px,ry:8px
    %% podos-theme:end
```
External calls receive empty internal metadata and an opaque UUID-derived A2A
conversation identifier. Transport authentication may also be configured. They do
not receive the pod ID, internal API URL, private history/call context or internal
delegation token. The user's question itself is still sent to that service.

External progress and self-reported verification render as agent activity. They
are not independent platform verification. Public citations open the same evidence
inspector, with an explicit **Open original** action when an HTTP(S) URL exists.

## Event handling

| Agent output | Workspace treatment |
|---|---|
| Text delta | Persist and forward as `message.delta` |
| Tool progress/completion | Render activity; capability execution remains governed |
| `sources.available` | Early per-answer source registry for streaming references |
| Final `citations` | Used to persist the final citation snapshot on completion |
| External verification status | Display as the external agent's own reported check |
| `court_availability` JSON artifact | Validated `ui.result`, controlled OpenUI cards, persisted for reload |
| Failed transport/task | Run failure; only internal agents can fall back to another eligible internal agent |

The API normalizes A2A output into its own SSE protocol. This is not a certified
AG-UI adapter. ADK's A2A task store remains in memory; application runs/messages
and replay events are separately persisted by the API.

## Validate an integration

Use T2, T3 and T11 tests for routing, capabilities and the external boundary.
Verify denied access as well as successful rendering. Do not infer permission from
an Agent Card's advertised skill or a model's tool request. [Testing](TESTING.md)
contains commands and current UI navigation.
