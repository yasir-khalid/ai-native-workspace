-- Today briefing feedback (PLAN_PROACTIVE.md §2, feeds §5 ranking later). Pod content: FORCE RLS.
CREATE TABLE attention_events (
  attention_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pod_id text NOT NULL REFERENCES pods(pod_id),
  subject_id text NOT NULL,
  item_key text NOT NULL,
  item_kind text NOT NULL,
  action text NOT NULL CHECK (action IN ('opened', 'asked', 'dismissed', 'more', 'less')),
  ticker text,
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attention_events_subject_idx ON attention_events (pod_id, subject_id, occurred_at DESC);
ALTER TABLE attention_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE attention_events FORCE ROW LEVEL SECURITY;
CREATE POLICY pod_scope ON attention_events
  USING (pod_id = current_setting('app.pod_id', true))
  WITH CHECK (pod_id = current_setting('app.pod_id', true));
