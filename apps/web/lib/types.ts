export type Pod = {
  pod_id: string;
  display_name: string;
  strategy: string;
  accent: string;
  pod_role: "pm" | "analyst";
  groups: string[];
};

export type Me = { subject_id: string; display_name: string; title: string; platform_roles: string[]; is_admin: boolean };

export type CatalogueAgent = {
  agent_id: string;
  name: string;
  description: string;
  kind: "specialist" | "router" | "background";
  owner_team: string;
  publication_status: string;
  version: string | null;
  release_stage: string | null;
  healthy: boolean;
  health_status: "healthy" | "unhealthy" | "not_connected";
  capabilities: string[];
  external: boolean;
  actions: { direct: boolean; auto: boolean; background: boolean };
  unavailable_reason: string | null;
};

export type Capability = { capability_id: string; description: string; result_type: string; side_effect_class: string };

export type Citation = {
  ref: number;
  doc_id: string | null;
  title: string;
  source: string;
  published_at?: string;
  kind?: string;
  ticker?: string;
  call_id?: string;
  sentence_id?: string;
  start?: number;
  speaker?: string;
  role?: string;
  text?: string;
  end?: number;
  transcript_sha256?: string;
  audio_sha256?: string;
  attribution_method?: string;
  asr_model?: string;
  review_status?: string;
  /** Citations produced by an external A2A agent: no pod document, only a public URL. */
  external?: boolean;
  url?: string | null;
  detail?: string;
};

export type ArtifactRef = {
  artifact_id: string;
  filename: string;
  mime_type: string;
  title: string;
  capability_id: string | null;
  classification: string;
  size_bytes: number;
  pod_id?: string;
  created_at?: string;
  created_by?: string;
  provenance?: Record<string, unknown>;
};

export type ThreadSummary = { thread_id: string; title: string | null; case_id: string | null; created_at: string; updated_at: string; active_run_id?: string | null };

export type StoredMessage = {
  message_id: string;
  role: "user" | "assistant";
  content: {
    text: string;
    mode?: string;
    call_id?: string | null;
    agent_id?: string | null;
    agent_name?: string;
    agent_version?: string;
    citations?: Citation[];
    artifacts?: ArtifactRef[];
    activity?: RunEvent[];
  };
  run_id: string | null;
  created_at: string;
};

export type RunEvent = { type: string; run_id: string; sequence: number; timestamp: string; [k: string]: unknown };

export type ResearchCase = {
  case_id: string;
  title: string;
  thesis: string;
  tickers: string[];
  status: string;
  owner_user_id: string;
  classification: string;
  documents: number;
  artifacts: number;
  updated_at: string;
};

export type ResearchDocument = {
  doc_id: string;
  kind: string;
  title: string;
  ticker: string | null;
  source: string;
  published_at: string;
  classification: string;
  case_id: string | null;
  excerpt?: string;
  body?: string;
  case_title?: string | null;
};

export type WorkItem = {
  work_item_id: string;
  run_id: string | null;
  title: string;
  summary: string;
  category: "approval" | "alert" | "report" | "exception";
  kind: "actionable" | "informational";
  state: string;
  priority: "low" | "normal" | "high";
  classification: string;
  version: number;
  deadline_at: string | null;
  created_at: string;
  read_at: string | null;
  archived_at: string | null;
  source_agent_id: string | null;
  evidence_artifact_id: string | null;
  required_pod_role: string | null;
  resolved_by: string | null;
  payload: {
    action?: string;
    evidence?: { doc_id: string; title: string; published_at?: string }[];
    proposed_changes?: string[];
    document_spec?: { title: string; subtitle?: string; slides?: { title: string; bullets: string[] }[] };
    matched?: string[];
    ticker?: string;
    artifact_id?: string;
  };
};

export type Monitor = {
  monitor_id: string;
  name: string;
  owner_user_id: string;
  configuration: { tickers: string[]; keywords: string[]; notify: string[]; approval_recipient: string };
  enabled: boolean;
  schedule_seconds: number;
  next_run_at: string;
  last_run_at: string | null;
  checkpoint: { last_seen?: string };
  agent_id: string;
  last_run_state: string | null;
  last_trigger: "event" | "schedule" | "manual" | null;
};

export type RunRow = {
  run_id: string;
  thread_id: string | null;
  agent_id: string;
  agent_version: string;
  mode: string;
  state: string;
  created_by: string;
  error: { code: string; message?: string } | null;
  routing: Record<string, unknown>;
  created_at: string;
  finished_at: string | null;
  thread_title: string | null;
  monitor_name: string | null;
};

export type ReleasePolicy = {
  revision: string;
  release_key: string;
  kind: "agent" | "capability";
  agent_id?: string | null;
  capability_id?: string | null;
  agent_version?: string | null;
  stage: string;
  published: boolean;
  pod_allowlist: string[];
  eligible_groups: string[];
  feature_flags: Record<string, boolean>;
  user_fraction_percent: number;
  stable_salt_id: string;
  runtime_canary?: { stable_version: string; candidate_version?: string | null; candidate_fraction_percent: number } | null;
  notes?: string;
};
