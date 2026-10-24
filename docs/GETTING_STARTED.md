# Getting started

[Documentation](README.md) · [Configuration](CONFIGURATION.md) · [Troubleshooting](OPERATIONS.md)

All commands below run from the **repository root** (`ai-native-workspace`), unless noted.

## Prerequisites

- Docker with Compose and enough memory for OpenSearch and the other local services.
- `uv` with Python **3.12**; `pyproject.toml` excludes Python 3.13 and later.
- Node.js and npm compatible with the locked frontend. The documentation renderer
  requires Node.js **22.13 or later**.
- Free local ports listed in the [application README](../README.md).

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
    accTitle: Local startup
    accDescr: Setup provisions the data plane and databases before the application services and browser are started.
Tools["Docker, uv and Node"]:::interface --> Setup["make setup"]:::service
    Setup --> Plane["Start data plane"]:::store
    Plane --> Schema["Migrate dev and test databases"]:::service
    Schema --> Seed["Provision pod credentials and seed fixtures"]:::guard
    Seed --> Dev["make dev"]:::success
    Dev --> Browser["Open localhost:3400"]:::interface
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
## First run

```sh
make setup
make dev
```

`make setup` starts Docker services, synchronizes Python dependencies, migrates the
`podos` and `podos_test` databases, provisions per-pod search/storage credentials,
seeds development records and installs web packages. OpenSearch and OpenFGA may
need time to become healthy. Setup seeds data; do not treat it as a production
migration command.

`make dev` runs the agents, MCP connector, API, worker and web process. Stop that
foreground stack with Ctrl+C. Docker data services remain running. Logs are in
`var/log/`; the script prints the local service URLs.

Open [the workspace](http://localhost:3400), select an identity, and choose a pod.
Without a model key, agents use the deterministic implementation. This is suitable
for the core isolation demo and acceptance tests.

## Development identities

| Identity | Pod access | Useful scenario |
|---|---|---|
| Yasir Khalid | Alpha, PM | Main external demo; research, exports, approvals and exclusive Sportscanner access |
| Alice Chen | Alpha, PM | Research, exports and approvals |
| Arjun Patel | Alpha, analyst | Analyst workflow and exports |
| Bianca Rossi | Beta, PM | Beta decisions |
| Ben Okafor | Beta, analyst | Research without PowerPoint entitlement |
| Dana Kim | Alpha and Beta | Pod switching and cache separation |
| Pat Morgan | Platform administration; no pod membership | Admin API; no research access |

For an external presentation, use Yasir as the main account, Bianca to demonstrate
pod isolation, and Pat for platform administration. The other accounts remain
available as permission and workflow test fixtures.

Sportscanner is connected as an external A2A agent at `http://127.0.0.1:8001`.
Only `yasir.khalid` has its pod-scoped grant in `config/access-template.json`;
other users cannot discover or invoke it, including by sending its ID directly.
Select Sportscanner in the Ask composer and try “Badminton near E1 2AD tomorrow
evening.” The separate Sportscanner service must be running. This local endpoint
currently has authentication disabled for development; shared deployments need
a Sportscanner bearer credential configured through an agent secret reference.
PodOS sends the question and a conversation key, without its session token,
pod research or permission metadata.

These accounts come from the development identity directory. The login picker is
disabled outside `dev`/`test`; this is not an enterprise SSO implementation.

## Optional live models

Create the ignored `.env` in the application root, set `OPENROUTER_API_KEY` privately,
and restart `make dev`. Model overrides and the data-handling boundary are in
[Configuration](CONFIGURATION.md). Do not commit the file or copy a real key into
a documentation example.

## Optional call library

The prepared audio/transcript files and ASR model caches live in ignored `var/`
paths. They are not distributed with the source. A fresh install may have an empty
Calls library until locally authorized artifacts are prepared and seeded.
[Calls](CALLS.md) explains the expected paths, available scripts and limitations.
The core workspace does not require ASR tooling or a GPU.

## First checks

```sh
make test
make lint
(cd apps/web && npx --yes tsx --test tests/chat-sessions.test.ts)
```

Tests use a separate database and service namespaces. Never point the test harness
at production. See [Testing](TESTING.md) before running a reset or older browser
walkthrough against data you care about.

## Next steps

Follow the [current demo](DEMO.md), inspect the [architecture](ARCHITECTURE.md), or
use the [operations guide](OPERATIONS.md) for production builds and recovery.
