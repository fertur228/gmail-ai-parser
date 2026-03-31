-- Separate OAuth refresh tokens: corporate Gmail vs personal Calendar

ALTER TABLE public.user_config
  ADD COLUMN IF NOT EXISTS calendar_refresh_token TEXT;
