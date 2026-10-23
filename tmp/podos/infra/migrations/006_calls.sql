-- Earnings-call / interview audio with locally transcribed transcripts. Pod content: FORCE RLS.
CREATE TABLE call_events (
  pod_id text NOT NULL REFERENCES pods(pod_id),
  call_id text NOT NULL,
  company text NOT NULL,
  ticker text,
  exchange text,
  event_title text NOT NULL,
  event_date date,
  duration double precision NOT NULL,
  audio_path text NOT NULL,
  audio_sha256 text,
  transcript jsonb NOT NULL,
  chapters jsonb NOT NULL DEFAULT '[]'::jsonb,
  qa jsonb NOT NULL DEFAULT '[]'::jsonb,
  insights jsonb NOT NULL DEFAULT '[]'::jsonb,
  key_questions jsonb NOT NULL DEFAULT '[]'::jsonb,
  tags text[] NOT NULL DEFAULT '{}',
  summary_short text,
  summary_long_html text,
  asr jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (pod_id, call_id)
);
CREATE INDEX call_events_event_date_idx ON call_events (pod_id, event_date DESC);
ALTER TABLE call_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_events FORCE ROW LEVEL SECURITY;
CREATE POLICY pod_scope ON call_events
  USING (pod_id = current_setting('app.pod_id', true))
  WITH CHECK (pod_id = current_setting('app.pod_id', true));
