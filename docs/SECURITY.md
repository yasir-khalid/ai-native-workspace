# Security and trust boundaries

[Documentation](README.md) · [Architecture](ARCHITECTURE.md) · [Deviations](DEVIATIONS.md)

PodOS is a development proof of concept. The design enforces pod isolation at
multiple layers; it does not establish that this checkout is production hardened.

## A request can only narrow authority

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
    accTitle: Authority narrows at every boundary
    accDescr: Identity, membership, grants, releases, delegation and data-plane enforcement are intersected. Model output and interface state cannot grant access.
Identity["Authenticated subject"]:::interface --> Membership["Live OpenFGA pod membership"]:::guard
    Membership --> Grants["User and service grants"]:::guard
    Grants --> Release["Release, flag and kill-switch checks"]:::guard
    Release --> Delegation["Signed run / pod / agent / capability scope"]:::guard
    Delegation --> Tool["Tool execution re-check"]:::guard
    Tool --> Data["RLS + index role + storage prefix"]:::store
    Data --> Result["Authorized result"]:::success
    Membership -. "deny" .-> Block["No model or UI override"]:::risk
    Tool -. "scope mismatch" .-> Block
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
## Enforced controls

- OpenFGA resolves membership and grants for agents, capabilities, datasets,
  features and pod actions. A failure to resolve does not grant access.
- PostgreSQL applies forced RLS with a non-owner runtime role and a transaction-local
  pod setting. Owner credentials belong only to setup/migrations.
- OpenSearch and MinIO receive separately provisioned pod credentials. Naming an
  index or prefix is not the sole protection.
- Internal agents receive audience-restricted, short-lived delegations. Tool calls
  must match the delegated pod, run, service and permitted capabilities.
- Downloads use short-lived subject/pod/artifact-bound links and recheck membership
  on redemption. SSE rechecks membership and terminates on revocation.
- Access/release changes and denials have audit records. Notification previews are
  generic rather than private research text.
- OpenUI uses a curated component set. Markdown images, arbitrary URL actions and
  tool-backed UI queries are not an allowed exfiltration channel.

See the executable checks in `tests/test_t1_isolation.py`, `test_t3_capabilities.py`,
`test_t5_releases.py`, `test_t7_authz.py` and the OpenFGA model suite.

## Internal versus external agents

Internal services may receive approved pod research context and tool delegation.
External deployments receive the user's question, an external conversation key
and any configured transport authentication. They do not receive internal pod
research, a delegation token or the internal API URL. Attaching a call does not
send its private transcript to an external agent. A question the user writes may
itself contain sensitive text; the external-agent boundary does not redact it.
For an explicit “ask this agent for this” handoff, the immediately relevant
user-authored question can accompany the correction. Assistant answers and
attached case/call context are excluded. Automatic transport fallback stays
between internal agents; external failures do not fall through to an unrelated
research agent, and research failures do not fall through to a third-party service.

## Development credentials and artifacts

`docker-compose.yml`, `settings.py` and `config/workload_secrets.dev.json` contain
intentional local-only fixture credentials. They must be replaced before shared
use. Real provider keys, `.env`, generated `config/data_plane.dev.json`, databases,
logs, audio and experiment outputs are excluded from Git.

No live credential belongs in a screenshot, Markdown example, source URL or chat
message. Use a private secret mechanism for real deployments.

## Remaining deployment work

| Gap | Required direction |
|---|---|
| Mock login and HS256 development keys | Enterprise OIDC and workload identity; secret rotation |
| Local/demo certificates and service defaults | TLS, trusted CAs, network boundaries and managed secrets |
| In-process run execution and quotas | Durable execution and shared counters before multiple replicas |
| In-memory A2A task store | Persistent task store if agent task resumption is required |
| Model retention/approval | Approved endpoint and contractual controls for research/MNPI |
| Missing token/cost budgets | Provider usage accounting, quotas and spend enforcement |
| Financial/ASR correctness | Reviewed gold data, attribution evaluation and evidence validation |
| Installed Next.js security advisory | Resolve the reported dependency advisory before a shared deployment |
| Licensing/retention | Confirm rights for recordings, documents, derived content and retention |

These are known engineering and deployment gaps, not findings from a completed
penetration test or compliance audit. Application test success does not close them.
