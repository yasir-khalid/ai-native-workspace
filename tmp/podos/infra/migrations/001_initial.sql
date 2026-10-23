-- Reference migration. Adjust types/indexes/roles to the implementation environment.
-- SECURITY: Run application traffic through a non-owner DB role without BYPASSRLS.
-- In each request transaction, SET LOCAL app.pod_id = '<server-validated pod id>'.
-- RLS is defence-in-depth, not a substitute for authorization or parameterized SQL.

-- pgcrypto is created by infra/postgres/init (requires superuser).

CREATE TABLE pods (
  pod_id text PRIMARY KEY,
  display_name text NOT NULL,
  strategy text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_definitions (
  agent_id text PRIMARY KEY,
  name text NOT NULL,
  owner_team text NOT NULL,
  description text NOT NULL DEFAULT '',
  publication_status text NOT NULL CHECK (publication_status IN ('draft','validating','approved','pilot','ga','deprecated','retired')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agent_deployments (
  deployment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id text NOT NULL REFERENCES agent_definitions(agent_id),
  implementation_version text NOT NULL,
  environment text NOT NULL,
  service_ref text NOT NULL,
  protocol text NOT NULL CHECK (protocol IN ('a2a','local_adk')),
  image_digest text,
  healthy boolean NOT NULL DEFAULT false,
  UNIQUE(agent_id, implementation_version, environment)
);
CREATE TABLE capability_definitions (
  capability_id text PRIMARY KEY,
  description text NOT NULL DEFAULT '',
  implementation_ref text NOT NULL,
  side_effect_class text NOT NULL CHECK (side_effect_class IN ('read_only','local_artifact','external_write','irreversible')),
  requires_approval boolean NOT NULL DEFAULT false
);
CREATE TABLE agent_capability_support (
  agent_id text NOT NULL REFERENCES agent_definitions(agent_id),
  capability_id text NOT NULL REFERENCES capability_definitions(capability_id),
  PRIMARY KEY (agent_id, capability_id)
);
CREATE TABLE release_policies (
  release_key text PRIMARY KEY,
  agent_id text NOT NULL REFERENCES agent_definitions(agent_id),
  config jsonb NOT NULL,
  revision text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by text NOT NULL
);

CREATE TABLE research_cases (
  case_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  title text NOT NULL,
  classification text NOT NULL DEFAULT 'pod_confidential',
  owner_user_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (pod_id, case_id)
);
CREATE TABLE threads (
  thread_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  case_id uuid,
  created_by text NOT NULL,
  title text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (pod_id, thread_id),
  FOREIGN KEY (pod_id, case_id) REFERENCES research_cases(pod_id, case_id)
);
CREATE TABLE messages (
  message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL,
  thread_id uuid NOT NULL,
  role text NOT NULL CHECK (role IN ('user','assistant','tool','system')),
  content jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (pod_id, thread_id) REFERENCES threads(pod_id, thread_id)
);
CREATE TABLE agent_runs (
  run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL,
  thread_id uuid,
  agent_id text NOT NULL REFERENCES agent_definitions(agent_id),
  agent_version text NOT NULL,
  mode text NOT NULL CHECK (mode IN ('auto','direct','background','scheduled')),
  state text NOT NULL,
  created_by text NOT NULL,
  execution_context jsonb NOT NULL,
  release_revision text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (pod_id, run_id),
  FOREIGN KEY (pod_id, thread_id) REFERENCES threads(pod_id, thread_id)
);
CREATE TABLE run_events (
  run_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pod_id text NOT NULL,
  run_id uuid NOT NULL,
  sequence bigint NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, sequence),
  FOREIGN KEY (pod_id, run_id) REFERENCES agent_runs(pod_id, run_id)
);
CREATE TABLE artifacts (
  artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  run_id uuid,
  case_id uuid,
  object_key text NOT NULL,
  mime_type text NOT NULL,
  classification text NOT NULL DEFAULT 'pod_confidential',
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (pod_id, artifact_id),
  FOREIGN KEY (pod_id, run_id) REFERENCES agent_runs(pod_id, run_id),
  FOREIGN KEY (pod_id, case_id) REFERENCES research_cases(pod_id, case_id)
);
CREATE TABLE work_items (
  work_item_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  run_id uuid,
  owner_user_id text,
  assignee_user_id text,
  title text NOT NULL,
  category text NOT NULL,
  state text NOT NULL,
  classification text NOT NULL DEFAULT 'pod_confidential',
  evidence_artifact_id uuid,
  version integer NOT NULL DEFAULT 0,
  deadline_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (pod_id, work_item_id),
  FOREIGN KEY (pod_id, run_id) REFERENCES agent_runs(pod_id, run_id),
  FOREIGN KEY (pod_id, evidence_artifact_id) REFERENCES artifacts(pod_id, artifact_id)
);
CREATE TABLE approval_decisions (
  decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL,
  work_item_id uuid NOT NULL,
  decided_by text NOT NULL,
  decision text NOT NULL CHECK (decision IN ('approve','reject','request_changes')),
  expected_version integer NOT NULL,
  idempotency_key text NOT NULL,
  decision_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  decided_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (work_item_id, idempotency_key),
  FOREIGN KEY (pod_id, work_item_id) REFERENCES work_items(pod_id, work_item_id)
);
CREATE TABLE monitors (
  monitor_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  owner_user_id text NOT NULL,
  configuration jsonb NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(pod_id, monitor_id)
);
CREATE TABLE outbox_events (
  outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pod_id text NOT NULL REFERENCES pods(pod_id),
  event_id text NOT NULL UNIQUE,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  state text NOT NULL DEFAULT 'pending',
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX threads_pod_created_idx ON threads (pod_id, created_at DESC);
CREATE INDEX work_items_pod_assignee_idx ON work_items (pod_id, assignee_user_id, state, updated_at DESC);
CREATE INDEX runs_pod_state_idx ON agent_runs (pod_id, state, created_at DESC);
CREATE INDEX outbox_pending_idx ON outbox_events (next_attempt_at, outbox_id) WHERE state = 'pending';

-- Example RLS policy applied only to tenant-owned tables.
-- Extend with per-user/per-resource checks at API/policy layer.
DO $$
DECLARE tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['research_cases','threads','messages','agent_runs','run_events',
      'artifacts','work_items','approval_decisions','monitors','outbox_events']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY pod_scope ON %I USING (pod_id = current_setting(''app.pod_id'', true)) WITH CHECK (pod_id = current_setting(''app.pod_id'', true))', tbl);
  END LOOP;
END $$;

-- IMPORTANT: Never use the table-owning migration role for ordinary API traffic.
-- Avoid a connection leak: SET LOCAL inside an explicit transaction per request.
-- Background workers must run with an explicitly validated pod context as well.
