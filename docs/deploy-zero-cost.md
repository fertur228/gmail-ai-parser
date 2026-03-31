# Zero-cost Telegram callbacks (Supabase Edge Function)

Долгий процесс `python -m src.telegram_bot` можно не запускать: нажатия на кнопки обрабатывает **Edge Function** `telegram-webhook`, а сканирование почты остаётся на **GitHub Actions** (`main.py` по cron).

## 1. Миграции и токены

Примените SQL-миграции (в том числе `tasks_refresh_token`, `last_daily_digest_at`).

Получите refresh-токен для Google Tasks (аккаунт **fertur** / нужный Google-аккаунт):

```bash
python -m src.auth_setup --type tasks --telegram-id YOUR_TELEGRAM_ID
```

Заполните в `user_config` также `calendar_refresh_token` и при необходимости `gmail_refresh_token`.

## 2. Секреты Edge Function

В Supabase: **Project Settings → Edge Functions → Secrets** (или CLI):

| Secret | Назначение |
|--------|------------|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
| `SUPABASE_SERVICE_ROLE_KEY` | **service_role** (не anon): чтение/запись `user_config`, `processed_emails` |
| `GOOGLE_OAUTH_CLIENT_ID` | `client_id` из вашего `credentials.json` |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `client_secret` из того же файла |
| `TELEGRAM_WEBHOOK_SECRET` | (необязательно) случайная строка для проверки заголовка |

`SUPABASE_URL` для функции обычно подставляется окружением Supabase автоматически; при деплое через CLI убедитесь, что URL доступен.

## 3. Деплой функции

```bash
npx supabase functions deploy telegram-webhook --no-verify-jwt
```

В `supabase/config.toml` для функции уже задано `verify_jwt = false`, чтобы Telegram мог POST без JWT Supabase.

## 4. Установка Webhook в Telegram

Подставьте токен бота и URL проекта (регион может отличаться; возьмите из **Supabase Dashboard → Edge Functions → telegram-webhook**):

```bash
curl -s "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" ^
  -d "url=https://<PROJECT_REF>.supabase.co/functions/v1/telegram-webhook" ^
  -d "secret_token=<SAME_AS_TELEGRAM_WEBHOOK_SECRET_OPTIONAL>"
```

В PowerShell без `^` используйте обратные кавычки или одну строку:

```powershell
curl.exe -s "https://api.telegram.org/bot$BOT/setWebhook" -d "url=https://$REF.supabase.co/functions/v1/telegram-webhook"
```

Проверка:

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

## 5. GitHub Actions

Workflow `.github/workflows/check_emails.yml` по-прежнему запускает `python main.py` каждые **15 минут** (UTC): загрузка писем, уведомления, «Итоги дня» (23:00–23:15 по `WEEKLY_REPORT_TZ`), воскресный отчёт. Кнопки в чате обрабатывает уже **webhook**, а не Python-бот.

## 6. Локальная отладка бота (опционально)

```bash
python -m src.telegram_bot
```

Используйте только если Edge Function недоступна; не включайте одновременно polling и webhook на одном боте без `deleteWebhook`.
