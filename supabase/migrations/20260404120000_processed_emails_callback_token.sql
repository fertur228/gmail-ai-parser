-- Short token for Telegram inline callback_data (64-byte limit); maps to message_id in app code

ALTER TABLE public.processed_emails
  ADD COLUMN IF NOT EXISTS callback_token TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS processed_emails_callback_token_key
  ON public.processed_emails (callback_token)
  WHERE callback_token IS NOT NULL;
