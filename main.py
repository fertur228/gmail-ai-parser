"""Load config, fetch unread Gmail, analyze with Groq, persist high-relevance results."""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from src.ai_engine import AIEngine
from src.database import DatabaseManager, DuplicateMessageIdError
from src.gmail_client import GmailClient
from src.telegram_bot import send_email_summary


def _parse_dt_any(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    s = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _should_send_daily_digest(
    db: DatabaseManager,
    telegram_id: int,
    tz_name: str,
) -> bool:
    """23:00–23:15 local: one digest per calendar day."""
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    if now.hour != 23 or not (0 <= now.minute <= 15):
        return False
    row = db.get_user_config(telegram_id)
    if not row:
        return False
    last = _parse_dt_any(row.get("last_daily_digest_at"))
    if last is None:
        return True
    return last.astimezone(tz).date() != now.date()


def _should_send_weekly_report(
    db: DatabaseManager,
    telegram_id: int,
    tz_name: str,
) -> bool:
    """Sunday evening (local): one report per calendar day."""
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    if now.weekday() != 6:
        return False
    if not (18 <= now.hour < 24):
        return False
    row = db.get_user_config(telegram_id)
    if not row:
        return False
    last = _parse_dt_any(row.get("last_weekly_report_at"))
    if last is None:
        return True
    return last.astimezone(tz).date() != now.date()


async def _run_async() -> None:
    load_dotenv()
    telegram_id_raw = os.environ.get("TELEGRAM_ID")
    if not telegram_id_raw:
        print(
            "Set TELEGRAM_ID in .env (user_config.telegram_id)",
            file=sys.stderr,
        )
        return
    telegram_id = int(telegram_id_raw)

    tg_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tg_bot_token:
        print("Set TELEGRAM_BOT_TOKEN in .env", file=sys.stderr)
        return

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        print("Set SUPABASE_URL and SUPABASE_KEY", file=sys.stderr)
        return

    cred_path = Path(os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json"))
    if not cred_path.is_file():
        print(
            f"Missing Gmail OAuth client file: {cred_path}",
            file=sys.stderr,
        )
        return

    db = DatabaseManager(url=supabase_url, key=supabase_key)
    row = db.get_user_config(telegram_id)
    if not row:
        print(f"No user_config for telegram_id={telegram_id}", file=sys.stderr)
        return

    token = row.get("gmail_refresh_token")
    if not token:
        print(
            "gmail_refresh_token is empty; run src.auth_setup --type gmail",
            file=sys.stderr,
        )
        return

    interests = row.get("interests_text") or ""
    gmail = GmailClient(token, client_secrets_path=cred_path)
    ai = AIEngine()

    bot = Bot(token=tg_bot_token)
    try:
        tz_report = os.environ.get("WEEKLY_REPORT_TZ", "Europe/Moscow")
        disliked = db.get_disliked_recent_summaries(telegram_id, limit=10)

        emails = gmail.fetch_unread_emails()
        for mail in emails:
            mid = mail["id"]
            # Дубликат в Supabase → не вызываем Groq и не шлём в Telegram.
            if db.is_message_processed(mid):
                continue

            analysis = ai.analyze_email(
                subject=mail.get("subject") or "",
                body=mail.get("body_text") or "",
                interests_text=interests,
                disliked_summaries=disliked,
            )
            score = analysis["relevance_score"]
            if score <= 0.7:
                continue

            try:
                db.save_processed_email(
                    message_id=mid,
                    subject=mail.get("subject") or "",
                    summary=analysis["summary"],
                    category=analysis["category"],
                    relevance_score=score,
                    is_interesting=analysis.get("is_interesting"),
                    event_datetime_iso=analysis.get("event_datetime_iso"),
                    owner_telegram_id=telegram_id,
                )
            except DuplicateMessageIdError:
                continue

            cb_token = db.assign_callback_token(mid)
            await send_email_summary(
                bot,
                telegram_id,
                subject=mail.get("subject") or "",
                category=analysis["category"],
                summary=analysis["summary"],
                callback_token=cb_token,
            )

        if _should_send_daily_digest(db, telegram_id, tz_report):
            day_rows = db.get_processed_emails_local_calendar_day(
                telegram_id, tz_report
            )
            try:
                digest = ai.generate_daily_digest(day_rows)
                await bot.send_message(
                    telegram_id,
                    f"📋 Итоги дня\n\n{digest}",
                )
                db.save_user_config(
                    telegram_id,
                    last_daily_digest_at=datetime.now(ZoneInfo(tz_report)),
                )
            except Exception as exc:
                print(f"Daily digest skipped: {exc}", file=sys.stderr)

        if _should_send_weekly_report(db, telegram_id, tz_report):
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            weekly_rows = db.get_processed_emails_since(telegram_id, since)
            try:
                report = ai.generate_weekly_analytics_report(weekly_rows)
                text = f"📊 Недельный отчёт\n\n{report}"
                await bot.send_message(telegram_id, text)
                db.save_user_config(
                    telegram_id,
                    last_weekly_report_at=datetime.now(ZoneInfo(tz_report)),
                )
            except Exception as exc:
                print(f"Weekly report skipped: {exc}", file=sys.stderr)
    finally:
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(_run_async())
    except Exception:
        traceback.print_exc(file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
