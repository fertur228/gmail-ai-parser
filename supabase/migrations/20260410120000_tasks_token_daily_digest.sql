-- Google Tasks OAuth (fertur account) + daily digest dedup

ALTER TABLE public.user_config
  ADD COLUMN IF NOT EXISTS tasks_refresh_token TEXT,
  ADD COLUMN IF NOT EXISTS last_daily_digest_at TIMESTAMPTZ;
