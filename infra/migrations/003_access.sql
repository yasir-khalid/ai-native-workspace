-- Access administration (PLAN_PROACTIVE.md §11). Decisions live in OpenFGA; these tables hold the
-- catalogue of grantable things that are not agents/capabilities, and an append-only ledger of
-- every access change made in the admin console (who, what, why, until when).

CREATE TABLE feature_definitions (
  feature_id text PRIMARY KEY,
  name text NOT NULL,
  description text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dataset_definitions (
  dataset_id text PRIMARY KEY,
  name text NOT NULL,
  description text NOT NULL,
  source text NOT NULL,
  classification text NOT NULL DEFAULT 'public',
  licence text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE access_changes (
  change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  action text NOT NULL CHECK (action IN ('grant', 'revoke', 'group_create', 'group_add', 'group_remove',
                                        'service_grant', 'service_revoke')),
  pod_id text REFERENCES pods(pod_id),  -- NULL for firm-wide service grants
  object_kind text NOT NULL,
  object_id text NOT NULL,
  subject_kind text,
  subject_id text,
  expires_at timestamptz,
  reason text NOT NULL DEFAULT ''
);
CREATE INDEX access_changes_pod_idx ON access_changes (pod_id, occurred_at DESC);
