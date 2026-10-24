-- PodOS POC extensions to the starter schema (001_initial.sql).
-- Every pod-content table carries a NOT NULL pod_id and FORCE ROW LEVEL SECURITY.
-- Deviations from the starter schema are listed in docs/DEVIATIONS.md.

-- ---------------------------------------------------------------------------
-- Identity directory (mirror of the mock IdP / firm IAM). Not pod content.
-- ---------------------------------------------------------------------------
CREATE TABLE users (
  subject_id text PRIMARY KEY,
  display_name text NOT NULL,
  email text NOT NULL,
  title text NOT NULL DEFAULT '',
  platform_roles text[] NOT NULL DEFAULT '{}',
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE pod_memberships (
  pod_id text NOT NULL REFERENCES pods(pod_id),
  subject_id text NOT NULL REFERENCES users(subject_id),
  pod_role text NOT NULL CHECK (pod_role IN ('pm','analyst')),
  groups text[] NOT NULL DEFAULT '{}',
  granted_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  PRIMARY KEY (pod_id, subject_id)
);

ALTER TABLE pods ADD COLUMN accent text NOT NULL DEFAULT 'blue';

-- ---------------------------------------------------------------------------
-- Registry / release extensions (control plane; not pod content).
-- ---------------------------------------------------------------------------
ALTER TABLE agent_definitions
  ADD COLUMN manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN kind text NOT NULL DEFAULT 'specialist' CHECK (kind IN ('specialist','router','background')),
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE agent_deployments
  ADD COLUMN card_sha256 text,
  ADD COLUMN last_health_at timestamptz,
  ADD COLUMN last_health_error text,
  ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE capability_definitions
  ADD COLUMN owner_team text NOT NULL DEFAULT 'platform',
  ADD COLUMN result_type text NOT NULL DEFAULT 'json',
  ADD COLUMN manifest jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Capability releases have no agent; release kind distinguishes them.
ALTER TABLE release_policies
  ALTER COLUMN agent_id DROP NOT NULL,
  ADD COLUMN kind text NOT NULL DEFAULT 'agent' CHECK (kind IN ('agent','capability')),
  ADD COLUMN capability_id text REFERENCES capability_definitions(capability_id),
  ADD CONSTRAINT release_target_chk CHECK (
    (kind = 'agent' AND agent_id IS NOT NULL AND capability_id IS NULL) OR
    (kind = 'capability' AND capability_id IS NOT NULL AND agent_id IS NULL));

CREATE TABLE audit_events (
  audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  action text NOT NULL,
  target text NOT NULL,
  pod_id text,
  outcome text NOT NULL DEFAULT 'ok',
  -- Sanitised metadata only: never prompts, research text or document bodies.
  detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_events_time_idx ON audit_events (occurred_at DESC);
CREATE INDEX audit_events_action_idx ON audit_events (action, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Research vault + per-pod search index substitute.
-- ---------------------------------------------------------------------------
ALTER TABLE research_cases
  ADD COLUMN thesis text NOT NULL DEFAULT '',
  ADD COLUMN tickers text[] NOT NULL DEFAULT '{}',
  ADD COLUMN status text NOT NULL DEFAULT 'active',
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE research_documents (
  doc_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  -- Logical index name; always 'research-' || pod_id. Kept so the search adapter
  -- contract (index-scoped retrieval) is identical to an OpenSearch deployment.
  index_name text NOT NULL,
  case_id uuid,
  kind text NOT NULL CHECK (kind IN ('note','filing','model','transcript','news')),
  title text NOT NULL,
  ticker text,
  body text NOT NULL,
  source text NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now(),
  classification text NOT NULL DEFAULT 'pod_confidential',
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
    setweight(to_tsvector('english', coalesce(ticker,'')), 'A') ||
    setweight(to_tsvector('english', body), 'B')) STORED,
  UNIQUE (pod_id, doc_id),
  CHECK (index_name = 'research-' || pod_id),
  FOREIGN KEY (pod_id, case_id) REFERENCES research_cases(pod_id, case_id)
);
CREATE INDEX research_documents_tsv_idx ON research_documents USING gin (tsv);
CREATE INDEX research_documents_pod_pub_idx ON research_documents (pod_id, published_at DESC);

-- ---------------------------------------------------------------------------
-- Conversations and runs.
-- ---------------------------------------------------------------------------
ALTER TABLE threads ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE messages
  ADD COLUMN run_id uuid,
  ADD COLUMN created_by text,
  ADD CONSTRAINT messages_run_fk FOREIGN KEY (pod_id, run_id) REFERENCES agent_runs(pod_id, run_id);
CREATE INDEX messages_thread_idx ON messages (pod_id, thread_id, created_at);

ALTER TABLE agent_runs
  ADD COLUMN deployment_id uuid REFERENCES agent_deployments(deployment_id),
  ADD COLUMN request_key text,
  ADD COLUMN monitor_id uuid,
  ADD COLUMN routing jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN error jsonb,
  ADD COLUMN finished_at timestamptz,
  ADD CONSTRAINT agent_runs_state_chk CHECK (state IN
    ('CREATED','QUEUED','RUNNING','WAITING_FOR_HUMAN','COMPLETED','FAILED','CANCELLED','EXPIRED','REJECTED'));
CREATE UNIQUE INDEX agent_runs_request_key_uq ON agent_runs (pod_id, thread_id, request_key) WHERE request_key IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Artifacts.
-- ---------------------------------------------------------------------------
ALTER TABLE artifacts
  ADD COLUMN filename text NOT NULL DEFAULT 'artifact.bin',
  ADD COLUMN size_bytes bigint NOT NULL DEFAULT 0,
  ADD COLUMN sha256 text NOT NULL DEFAULT '',
  ADD COLUMN capability_id text REFERENCES capability_definitions(capability_id),
  ADD COLUMN idempotency_key text,
  ADD COLUMN provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN title text NOT NULL DEFAULT '';
CREATE UNIQUE INDEX artifacts_idem_uq ON artifacts (pod_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Work items (inbox), approvals and their audit trail.
-- ---------------------------------------------------------------------------
ALTER TABLE work_items
  ADD COLUMN kind text NOT NULL DEFAULT 'informational' CHECK (kind IN ('actionable','informational')),
  ADD COLUMN priority text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
  ADD COLUMN summary text NOT NULL DEFAULT '',
  ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN dedup_key text,
  ADD COLUMN required_pod_role text,
  ADD COLUMN source_agent_id text,
  ADD COLUMN source_event_id text,
  ADD COLUMN read_at timestamptz,
  ADD COLUMN archived_at timestamptz,
  ADD COLUMN snoozed_until timestamptz,
  ADD COLUMN resolved_by text,
  ADD CONSTRAINT work_items_state_chk CHECK (state IN
    ('OPEN','WAITING_FOR_HUMAN','APPROVED','RUNNING','COMPLETED','REJECTED','CHANGES_REQUESTED','EXPIRED','CANCELLED','FAILED'));
CREATE UNIQUE INDEX work_items_dedup_uq ON work_items (pod_id, dedup_key) WHERE dedup_key IS NOT NULL;

CREATE TABLE work_item_events (
  event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pod_id text NOT NULL,
  work_item_id uuid NOT NULL,
  actor text NOT NULL,
  event_type text NOT NULL,
  from_state text,
  to_state text,
  version integer NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (pod_id, work_item_id) REFERENCES work_items(pod_id, work_item_id)
);

-- Proactive event ingress de-duplication (event_id is globally unique).
CREATE TABLE ingested_agent_events (
  event_id text PRIMARY KEY,
  pod_id text NOT NULL REFERENCES pods(pod_id),
  agent_id text NOT NULL,
  run_id uuid NOT NULL,
  event_type text NOT NULL,
  work_item_id uuid,
  received_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Monitors.
-- ---------------------------------------------------------------------------
ALTER TABLE monitors
  ADD COLUMN name text NOT NULL DEFAULT 'Monitor',
  ADD COLUMN agent_id text NOT NULL DEFAULT 'catalyst-monitor' REFERENCES agent_definitions(agent_id),
  ADD COLUMN schedule_seconds integer NOT NULL DEFAULT 3600 CHECK (schedule_seconds >= 30),
  ADD COLUMN next_run_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN last_run_at timestamptz,
  ADD COLUMN checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- ---------------------------------------------------------------------------
-- Notifications.
-- ---------------------------------------------------------------------------
CREATE TABLE notification_preferences (
  pod_id text NOT NULL REFERENCES pods(pod_id),
  subject_id text NOT NULL,
  email_enabled boolean NOT NULL DEFAULT true,
  digest_low_priority boolean NOT NULL DEFAULT true,
  PRIMARY KEY (pod_id, subject_id)
);

CREATE TABLE notification_deliveries (
  delivery_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  work_item_id uuid NOT NULL,
  recipient_user_id text NOT NULL,
  channel text NOT NULL CHECK (channel IN ('in_app','email')),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','delivered','failed','suppressed')),
  preview text NOT NULL,
  attempts integer NOT NULL DEFAULT 0,
  last_error text,
  delivered_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (work_item_id, recipient_user_id, channel),
  FOREIGN KEY (pod_id, work_item_id) REFERENCES work_items(pod_id, work_item_id)
);

-- ---------------------------------------------------------------------------
-- Outbox: leases for crash-safe claiming, dead-lettering.
-- ---------------------------------------------------------------------------
ALTER TABLE outbox_events
  ADD COLUMN locked_until timestamptz,
  ADD COLUMN locked_by text,
  ADD COLUMN last_error text,
  ADD COLUMN processed_at timestamptz,
  ADD CONSTRAINT outbox_state_chk CHECK (state IN ('pending','processing','done','dead'));
DROP INDEX outbox_pending_idx;
CREATE INDEX outbox_claim_idx ON outbox_events (pod_id, next_attempt_at, outbox_id) WHERE state IN ('pending','processing');

-- ---------------------------------------------------------------------------
-- RLS for the new pod-content tables.
-- ---------------------------------------------------------------------------
DO $$
DECLARE tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['research_documents','work_item_events','ingested_agent_events',
      'notification_preferences','notification_deliveries']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY pod_scope ON %I USING (pod_id = current_setting(''app.pod_id'', true)) WITH CHECK (pod_id = current_setting(''app.pod_id'', true))', tbl);
  END LOOP;
END $$;

-- Runtime role privileges (tables created by podos_owner get default grants; be explicit anyway).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO podos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO podos_app;
-- Audit is append-only for the runtime role.
REVOKE UPDATE, DELETE ON audit_events FROM podos_app;
