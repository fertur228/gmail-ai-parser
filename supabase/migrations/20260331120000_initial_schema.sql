-- AI Email Filter: user settings and processed messages

CREATE TYPE public.email_category AS ENUM ('event', 'task', 'info');

CREATE TABLE public.user_config (
  telegram_id BIGINT PRIMARY KEY,
  interests_text TEXT,
  gmail_refresh_token TEXT,
  last_sync_at TIMESTAMPTZ
);

CREATE TABLE public.processed_emails (
  message_id TEXT PRIMARY KEY,
  subject TEXT,
  summary TEXT,
  category public.email_category NOT NULL,
  relevance_score DOUBLE PRECISION NOT NULL,
  is_interesting BOOLEAN,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.user_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.processed_emails ENABLE ROW LEVEL SECURITY;

-- Full access for API roles (tighten per-user when auth is wired)
CREATE POLICY user_config_api_all
  ON public.user_config
  FOR ALL
  TO anon, authenticated, service_role
  USING (true)
  WITH CHECK (true);

CREATE POLICY processed_emails_api_all
  ON public.processed_emails
  FOR ALL
  TO anon, authenticated, service_role
  USING (true)
  WITH CHECK (true);

GRANT USAGE ON TYPE public.email_category TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.user_config TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.processed_emails TO anon, authenticated, service_role;
