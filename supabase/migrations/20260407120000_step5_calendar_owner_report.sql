-- Event time for Calendar button; per-user rows for feedback loop & weekly report

ALTER TABLE public.processed_emails
  ADD COLUMN IF NOT EXISTS event_datetime_iso TEXT,
  ADD COLUMN IF NOT EXISTS owner_telegram_id BIGINT;

ALTER TABLE public.user_config
  ADD COLUMN IF NOT EXISTS last_weekly_report_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS processed_emails_owner_created_idx
  ON public.processed_emails (owner_telegram_id, created_at DESC);
