# Earnings Call Intelligence implementation audit

[Documentation](README.md) · [Calls](CALLS.md) · [Chat evidence](CHAT_EVIDENCE_IMPLEMENTATION.md)

Updated 24 October for the evidence/composer/background implementation. The original
21 October plan is not fully implemented. UI progress does not close the gold-data,
production ingestion, attribution, numeric or scale acceptance gates.

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
flowchart LR
    accTitle: Calls plan coverage
    accDescr: The reader and evidence experience are implemented, with offline evaluation tools. Reviewed gold, durable ingestion and verified facts remain necessary before parity claims.
UI["Reader, playback and @ references"]:::success --> Evidence["Saved citations and evidence inspector"]:::success
    Evidence --> Eval["Offline evaluation tools"]:::service
    Eval --> Gold["Missing: independently reviewed gold"]:::risk
    Gold --> Production["Missing: durable ingestion and verified facts"]:::risk
    Production --> Parity["Parity requires measured acceptance"]:::guard
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
Audited 23 October 2026 against the owner's 21 October implementation plan (497 lines).

## Verdict

The full plan is **not implemented**, and industry-leading accuracy or production parity has **not been demonstrated**. The current system is a working, pod-isolated Calls prototype integrated into PodOS. Passing application tests does not establish the transcription, attribution, evidence-verification, latency or scale gates in the plan.

Two prepared calls are available: `crwv-jpm-2026` and `aur-evercore-2026`. Their stored WER-versus-reference values are **8.72%** and **8.57%**, respectively, against a plan target of **4.0%**. These are simple normalized edit-distance results from `scripts/calls_align.py`, not the plan's gold-set/Whisper-normalized benchmark. No claim of comparable benchmark performance is justified.

Speaker names are transferred from reference transcript timing in `scripts/calls_align.py`. This is not independently measured diarization, voice identification or a validated speaker-resolution pipeline. No DER or management attribution result is available.

The subsequent [accuracy research and local experiments](CALLS_ACCURACY_RESEARCH.md) add a versioned reference-disagreement evaluator, local ASR adapters, vocabulary experiments, repetition diagnostics and an experimental token vote. They also fix dropped point-timestamp words for future alignment runs. These are offline tools and development results, not completed production ingestion or human-gold acceptance gates.

## Milestone coverage

| Milestone | Status | Implemented evidence | Missing gate or implementation |
|---|---|---|---|
| M0 Skeleton | Partial | PodOS Python/FastAPI/Pydantic and Next/React apps; Postgres, OpenSearch, MinIO, OpenFGA; tests | No earnings pipeline packages, Temporal, engine/threshold/taxonomy configuration, immutable stage artifacts or Calls CI gate |
| M1 Evaluation harness | Partial | Pinned legacy/Whisper-derived normalized WER, S/D/I, artifact hashes, candidate comparisons, disagreement reports and integrity tests | Earnings21/22 loaders, human gold data, DER/number/entity/calibration metrics, 400-question QA set, nightly/prompt-change gates |
| M2 Ingest and single ASR | Partial | Cached local Whisper outputs, audio files, reference-alignment script, seed loader | Upload/register workflow, idempotent fetch/normalize/VAD/chunk pipeline, HLS ingest, context-document parsing, keyterm extraction, documented provider adapter, measured one-hour ingest SLA |
| M3 Ensemble and verification | Experimental only | Offline independent Parakeet candidate, Whisper variants, baseline-anchored token-vote experiment and repetition review gate | Production reconciliation, full ROVER, constrained adjudication, genuine forced alignment, press-release number checking, calibrated confidence; 4%/0.5% gates unproven |
| M4 Speakers | Missing as specified | Reference-timing speaker labels and names displayed in UI | Diarization votes, operator/handover parser, confidence/null policy, voiceprints, historical roles, human review; DER and attribution gates unproven |
| M5 Structure, enrichment, index | Partial | Chapters, tags, evidence-linked insights, deterministic consecutive-turn Q&A | Hierarchical chapter tree, semantic/lexical sentence-topic taxonomy, typed KPIs and number verification, linked entity mentions, sentence flags, hybrid/RRF/rerank indexes, topic-count materialized views; F1/KPI gates unproven |
| M6 Query engine and API | Partial | Pod-scoped list/detail/audio/search/Ask endpoints; persistent main Auto chat with attached transcript; sentence source references, saved excerpt/hash provenance, evidence inspector and navigation-safe background answers | Typed question planner/clarification, deterministic count/KPI/compare/trend tools, quote placeholders, numeric verifier, entailment checks, regeneration gate, full source-revision history/review API, transcript webhooks, specified export contract |
| M7 Web app | Partial | Library, cover/chapters, global player, speed/volume/seek/download, sentence follow, searchable transcript, Q&A/insights, mobile layout, main chat handoff, composer references, evidence panel/mobile sheet and playable sentence citations | Word-level karaoke, virtualization, low-confidence word review, numeric verification badges, next/previous question, waveform/chapter ticks/topic heat, watchlists/clips/calendar, topics/numbers/slides views, PDF bbox citations and full accessibility/E2E matrix |
| M8 Hardening | Missing as specified | Existing pod authorization, RLS and run/message persistence | Four-quarter corpus, 50-call load test, AWS/Temporal/GPU deployment, stage tracing and cost/SLA dashboards, signed audio distribution, retention/licensing review and complete security acceptance |

The owner's later instruction to use the existing main Auto chat supersedes the original plan's separate chat drawer. Its removal is intentional, not a missing feature. The existing global player is also intentionally preserved.

## Requirements by plan section

1. **Architecture and stack:** reuse of PodOS is established. The offline durable ingest/query planes and swappable providers are not implemented as specified. Listed model/provider names and prices in the plan have not been validated as available or appropriate.
2. **Repository conventions:** scripts and JSON seeds exist. No pure typed stage package/activity structure, prompt-file hashes, immutable raw/stage metadata or reproducible stage runner.
3. **Canonical data model:** `infra/migrations/006_calls.sql` stores a call and transcript JSON under RLS. It does not provide the normalized company/person/role/turn/sentence/KPI/review/citation schema. Words are JSON, not Parquet. `pipeline_version` and `speaker_mapping` export contracts are absent.
4. **Ingestion:** prepared local files and seeding work. File-drop/API registration, normalization, VAD, document context pack, keyterms and durable backfill are absent. Live transcription is explicitly optional phase 2 in the plan.
5. **Transcription:** production data still uses local Whisper plus reference metadata. Offline multi-candidate experiments and repetition diagnostics now exist; production reconciliation, forced alignment, number verification and calibrated error routing remain missing.
6. **Speakers:** reference metadata copied by timing; no measured identity-resolution system. Do not describe this as a completed diarization milestone.
7. **Structure and enrichment:** navigation metadata exists; typed topic/KPI/entity/flag extraction and reviewed correctness do not.
8. **Storage and indexing:** call data is Postgres JSON plus local artifacts. Existing OpenSearch research search is not the specified transcript sentence/paragraph/document-block hybrid index. No unattended rebuild from immutable Parquet.
9. **Query engine:** an LLM reads attached transcript evidence. New source links identify actual stored sentences; this establishes traceability, **not semantic entailment or numerical accuracy**. Generated answer quotes, numbers and counts are still not constrained by the plan's deterministic executor/verifier. The older standalone Ask endpoint also uses heuristic term selection for counts; it is not a production counting engine.
10. **Backend API:** implemented `/v1/pods/{pod}/calls` routes differ from the plan's `/events` contract. No claim of Quartr-compatible API/export conformance has been tested. Call audio uses authenticated cookies and Range requests, not expiring signed URLs.
11. **Frontend:** usable player/transcript/chat slice exists. See M7 for missing research and review surfaces.
12. **Evaluation:** application tests validate routing, scope, persistence and source mapping. Offline ASR comparison artifacts record WER against unreviewed published references. Neither establishes human-gold accuracy. No recorded corpus-level speaker, topic, KPI, citation-support, count, clarification or p95 latency score.
13. **Human review:** no review queue, Label Studio integration, resolution replay or gold-set feedback path.
14. **Infrastructure, cost and security:** local PodOS data plane works. No demonstrated Calls production architecture, cost measurement, stage observability, immutable retention or licensed coverage SLA. The frontend build reports an existing Next.js security advisory; upgrade remains necessary.
15. **Build sequence:** original milestone gates were not followed or passed. Starting with the visible UI did not complete upstream pipeline milestones.
16. **Whole-system definition of done:** see the checklist below; the entire system must not be marked complete.

## Whole-system acceptance checklist

| Plan acceptance criterion | Assessment |
|---|---|
| MP3 + press release upload to raw transcript within 15 minutes for a one-hour call | Missing workflow and benchmark |
| Edited transcript and at least 99.5% management attribution after review | Missing review system and gold evaluation |
| WER <=4%, number error <=0.5%, zero unflagged document mismatches | Not met/proven; stored demo WER exceeds target |
| Quartr-compatible transcript schema | Not implemented or validated |
| CFO/period/gross-margin question yields stored verbatim quotes and playable citations | Sentence playback now works; role/period resolution and guaranteed verbatim answer rendering remain missing |
| Four-quarter AI capex counts with lexical/semantic definitions and full hit lists | Missing corpus, typed query tools, taxonomy and deterministic aggregation |
| Ambiguous-question clarification >=90% | Missing planner/evaluation |
| Citation precision >=98%, recall >=90%, quote exactness 100% | No gold evaluation; source-ID validity does not prove these metrics |
| Citation playback within 0.3 seconds and slide bbox highlights | Sentence navigation/seek implemented and browser-tested; no full timing benchmark and no slide viewer |
| Unattended Parquet re-index and nightly eval publication | Missing |

## Corrections made during this audit

- Main-chat source evidence now maps numbered references to individual stored sentences with sentence ID, speaker, role, timestamp and quote preview.
- Inline citation chips route Calls sources to the call transcript rather than attempting to open a vault document ID.
- Source cards and inline chips carry both `s` and `t` in their links.
- Citation parsing accepts three/four-digit source references, needed for full-call transcripts.
- Revisited sentence/timestamp links seek again instead of being ignored after the first link.
- Existing main Auto chat, persisted call context and global-player behavior remain in use.

Coverage: `tests/test_t12_call_chat.py` verifies pod isolation, attached evidence, context persistence/removal, complete ordinary-length transcripts, and exact source-to-sentence/speaker/time mapping. The focused Calls/agent/external-A2A regression command is:

```sh
.venv/bin/pytest tests/test_t12_call_chat.py tests/test_t2_agents.py tests/test_t11_external_a2a.py -q
```

Result: **20 passed**. Frontend TypeScript, ESLint and production compilation pass; the production build also reports the existing Next.js security advisory. These checks do not replace the accuracy gates above.

Browser spot checks through a real main-chat answer validated three-digit references, inline links and re-entering the same call with a different citation. `s170` targeted 719.24 seconds and the player read 719.40 after pausing; `s315` targeted 1299.04 and read 1299.00. Both sentences were highlighted; the latter was visible after scrolling settled. This demonstrates the tested links, not a corpus-wide +/-0.3-second alignment certification. The generated answer still changed wording inside some quotations, independently confirming that quote exactness is not guaranteed by current prompting.

## What must happen before a parity claim

Proceed in the plan's gated order: M0 reproducible pipeline scaffolding, M1 actual gold/eval harness, M2 ingestion, M3 ensemble/numbers, M4 speakers/review, M5 typed enrichment/index, M6 verified query engine, then remaining M7/M8 work. Each stage must produce measured results before promotion. Keep the current UI available as a prototype while that work proceeds.

The human/external inputs still unresolved in the plan are licensed audio/IR sources, launch coverage/backfill universe, provider/runtime budget, whether live calls belong in v1, a corrected gold corpus and named reviewers. These are dependencies; simulated scores or generated labels cannot substitute for them.

Industry comparison was checked against [Quartr's published Kepler case study](https://quartr.com/customers/how-kepler-made-every-ai-answer-traceable-to-a-primary-source-powered-by-quartr), which describes deterministic retrieval/calculation, cross-document period joins and passage/phrase-level traceability. This is a vendor description, not an independently reproduced benchmark. The current prototype does not establish equivalence to that system.

## 24 October implementation delta

The old retrieved-as-cited fallback in `Sources.cited()` is removed. Early source
records resolve streaming references; final citations retain valid references used
by that answer. The UI shows missing references as unavailable. Saved excerpt,
start/end, speaker and recorded transcript/audio metadata persist with the answer.
The inspector distinguishes current surrounding text from the saved passage and
disables timestamp playback if the recording hash changes.

Workspace-owned streams, active-run list status and replay support navigation and
reloads. Unique handoff draft IDs prevent a second call inheriting an unsent first
call's context. The focused pass recorded 28 backend and five session checks;
the later documentation pass ran the full suite (131 passed, five live skips).
This supersedes the earlier UI spot-check counts above, not the missing accuracy gates.
