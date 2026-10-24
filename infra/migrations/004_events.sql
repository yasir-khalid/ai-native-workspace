-- Event bus, connectors and routing (PLAN_PROACTIVE.md F1-F4, §1).
--
-- Firm-wide external events (public filings, licensed news) are stored ONCE here with no pod id.
-- The router copies an event into each watching pod's own vault (copy-per-pod: pod search indexes
-- and credentials stay exactly as they are) and records one event_deliveries row per pod, in the same
-- pod transaction as the copy, so delivery is exactly-once per pod.
--
-- watchlists is control-plane routing metadata (pod_id, ticker, why). It is written only from inside
-- each pod's own transaction and read only by the router; it is never exposed through any API.

CREATE TABLE entities (
  ticker text PRIMARY KEY,
  name text NOT NULL,
  cik text
);

CREATE TABLE watchlists (
  pod_id text NOT NULL REFERENCES pods(pod_id),
  ticker text NOT NULL,
  reason text NOT NULL CHECK (reason IN ('monitor', 'case')),
  ref_id text NOT NULL,
  owner_user_id text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (pod_id, ticker, reason, ref_id)
);
CREATE INDEX watchlists_ticker_idx ON watchlists (ticker);

CREATE TABLE connector_cursors (
  connector_id text PRIMARY KEY,
  cursor text,
  polled_at timestamptz,
  last_error text,
  events_total bigint NOT NULL DEFAULT 0
);

CREATE TABLE events (
  event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id text NOT NULL,
  dataset_id text NOT NULL REFERENCES dataset_definitions(dataset_id),
  event_type text NOT NULL,
  ticker text,
  occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  dedupe_key text NOT NULL UNIQUE,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  routed_at timestamptz,
  route_attempts int NOT NULL DEFAULT 0,
  last_error text
);
CREATE INDEX events_unrouted_idx ON events (received_at) WHERE routed_at IS NULL;

CREATE TABLE event_deliveries (
  event_id uuid NOT NULL REFERENCES events(event_id),
  pod_id text NOT NULL REFERENCES pods(pod_id),
  delivered_at timestamptz NOT NULL DEFAULT now(),
  doc_id uuid,
  monitors_triggered int NOT NULL DEFAULT 0,
  outcome text NOT NULL DEFAULT 'delivered' CHECK (outcome IN ('delivered', 'not_licensed')),
  PRIMARY KEY (event_id, pod_id)
);

-- Push sources: replay protection for signed webhooks.
CREATE TABLE webhook_nonces (
  connector_id text NOT NULL,
  nonce text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (connector_id, nonce)
);

-- Defence in depth: a pod transaction can only read and write its own watchlist and deliveries; the
-- router (system transaction, app.pod_id = '') can read across pods but cannot write rows for a pod.
DO $$
DECLARE tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['watchlists', 'event_deliveries'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY route_read ON %I FOR SELECT USING (current_setting(''app.pod_id'', true) IN ('''', pod_id))', tbl);
    EXECUTE format('CREATE POLICY pod_write ON %I FOR INSERT WITH CHECK (pod_id = current_setting(''app.pod_id'', true))', tbl);
    EXECUTE format('CREATE POLICY pod_update ON %I FOR UPDATE USING (pod_id = current_setting(''app.pod_id'', true)) WITH CHECK (pod_id = current_setting(''app.pod_id'', true))', tbl);
    EXECUTE format('CREATE POLICY pod_delete ON %I FOR DELETE USING (pod_id = current_setting(''app.pod_id'', true))', tbl);
  END LOOP;
END $$;
