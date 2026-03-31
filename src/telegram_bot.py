"""Telegram notifications (aiogram 3.x) and inline callbacks.

Production callbacks are handled by the Supabase Edge Function `telegram-webhook`
(webhook URL in docs/deploy-zero-cost.md). Long-polling here is optional for local dev only:
  python -m src.telegram_bot
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from dotenv import load_dotenv

from src.calendar_client import CalendarClient
from src.database import DatabaseManager
from src.tasks_client import TasksClient

CATEGORY_EMOJI = {
    "event": "📅",
    "task": "✅",
    "info": "ℹ️",
}

CATEGORY_LABEL_RU = {
    "event": "Событие",
    "task": "Задача",
    "info": "Информация",
}


def format_notification_text(subject: str, category: str, summary: str) -> str:
    """Human-readable body: emoji + category, subject line, Gemini summary."""
    emoji = CATEGORY_EMOJI.get(category, CATEGORY_EMOJI["info"])
    label = CATEGORY_LABEL_RU.get(category, category)
    subj = (subject or "").strip() or "(без темы)"
    summ = (summary or "").strip() or "—"
    return f"{emoji} {label}\nТема: {subj}\n\n{summ}"


def build_inline_keyboard(category: str, callback_token: str) -> InlineKeyboardMarkup:
    """Inline keyboard: calendar / task rows when applicable + 👍 / 👎 for all."""
    rows: list[list[InlineKeyboardButton]] = []
    if category == "event":
        rows.append(
            [
                InlineKeyboardButton(
                    text="📅 В календарь",
                    callback_data=_cb("cal", callback_token),
                ),
            ]
        )
    if category in ("task", "info"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ В задачи",
                    callback_data=_cb("tsk", callback_token),
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="👍 Интересно",
                callback_data=_cb("up", callback_token),
            ),
            InlineKeyboardButton(
                text="👎 Не интересно",
                callback_data=_cb("dn", callback_token),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cb(prefix: str, token: str) -> str:
    data = f"{prefix}|{token}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("callback_data exceeds Telegram 64-byte limit")
    return data


async def send_email_summary(
    bot: Bot,
    chat_id: int,
    *,
    subject: str,
    category: str,
    summary: str,
    callback_token: str,
) -> None:
    await bot.send_message(
        chat_id,
        format_notification_text(subject, category, summary),
        reply_markup=build_inline_keyboard(category, callback_token),
    )


def setup_router(
    db: DatabaseManager,
    *,
    credentials_path: Path,
) -> Router:
    router = Router()

    @router.callback_query(F.data.startswith("cal|"))
    async def on_calendar(cq: CallbackQuery) -> None:
        token = cq.data.split("|", 1)[1] if "|" in (cq.data or "") else ""
        mid = db.get_message_id_by_callback_token(token)
        if not mid:
            await cq.answer("Запись не найдена", show_alert=True)
            return
        row = db.get_processed_email(mid)
        if not row or (row.get("category") or "") != "event":
            await cq.answer("Это не событие или запись устарела", show_alert=True)
            return
        start_iso = row.get("event_datetime_iso")
        if not start_iso:
            await cq.answer(
                "В письме нет даты для календаря (event_datetime_iso пусто)",
                show_alert=True,
            )
            return
        uid = cq.from_user.id if cq.from_user else None
        if uid is None:
            await cq.answer("Не удалось определить пользователя", show_alert=True)
            return
        urow = db.get_user_config(int(uid))
        if not urow or not urow.get("calendar_refresh_token"):
            await cq.answer(
                "Нет токена Calendar. Запустите: python -m src.auth_setup --type calendar",
                show_alert=True,
            )
            return
        if not credentials_path.is_file():
            await cq.answer("Нет файла credentials.json на сервере", show_alert=True)
            return
        title = (row.get("subject") or "").strip() or "Событие"
        desc = (row.get("summary") or "").strip()
        try:
            cal = CalendarClient(
                urow["calendar_refresh_token"],
                client_secrets_path=credentials_path,
            )
            cal.add_event(title, str(start_iso), description=desc)
        except Exception as exc:
            traceback.print_exc()
            await cq.answer(
                f"Ошибка календаря: {str(exc)}"[:200],
                show_alert=True,
            )
            return
        await cq.answer("Добавлено в календарь")
        try:
            await cq.message.edit_text(
                "✅ Добавлено в календарь!",
                reply_markup=None,
            )
        except Exception:
            pass

    @router.callback_query(F.data.startswith("tsk|"))
    async def on_tasks(cq: CallbackQuery) -> None:
        token = cq.data.split("|", 1)[1] if "|" in (cq.data or "") else ""
        mid = db.get_message_id_by_callback_token(token)
        if not mid:
            await cq.answer("Запись не найдена", show_alert=True)
            return
        row = db.get_processed_email(mid)
        cat = (row.get("category") or "") if row else ""
        if not row or cat not in ("task", "info"):
            await cq.answer(
                "Задача из этого письма недоступна (категория не task/info)",
                show_alert=True,
            )
            return
        uid = cq.from_user.id if cq.from_user else None
        if uid is None:
            await cq.answer("Не удалось определить пользователя", show_alert=True)
            return
        urow = db.get_user_config(int(uid))
        if not urow or not urow.get("tasks_refresh_token"):
            await cq.answer(
                "Нет токена Tasks. Запустите: python -m src.auth_setup --type tasks",
                show_alert=True,
            )
            return
        if not credentials_path.is_file():
            await cq.answer("Нет файла credentials.json на сервере", show_alert=True)
            return
        title = (row.get("subject") or "").strip() or "Задача из почты"
        notes = (row.get("summary") or "").strip()
        try:
            tc = TasksClient(
                urow["tasks_refresh_token"],
                client_secrets_path=credentials_path,
            )
            tc.add_task(title, notes)
        except Exception as exc:
            traceback.print_exc()
            await cq.answer(
                f"Ошибка Tasks: {str(exc)}"[:200],
                show_alert=True,
            )
            return
        await cq.answer("Добавлено в задачи")
        try:
            await cq.message.edit_text(
                "✅ Добавлено в Google Tasks!",
                reply_markup=None,
            )
        except Exception:
            pass

    @router.callback_query(F.data.startswith("up|"))
    async def on_thumb_up(cq: CallbackQuery) -> None:
        await _handle_interest(cq, db, interesting=True)

    @router.callback_query(F.data.startswith("dn|"))
    async def on_thumb_down(cq: CallbackQuery) -> None:
        await _handle_interest(cq, db, interesting=False)

    return router


async def _handle_interest(
    cq: CallbackQuery,
    db: DatabaseManager,
    *,
    interesting: bool,
) -> None:
    token = cq.data.split("|", 1)[1] if "|" in cq.data else ""
    mid = db.get_message_id_by_callback_token(token)
    if not mid:
        await cq.answer("Запись не найдена", show_alert=True)
        return
    db.update_processed_email_interesting(mid, interesting)
    label = "интересно" if interesting else "не интересно"
    await cq.answer(f"Сохранено: {label}")
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def run_polling_bot() -> None:
    """Long-running process: handle inline callbacks (run alongside cron/main)."""
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not token or not url or not key:
        print(
            "Set TELEGRAM_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY",
            file=sys.stderr,
        )
        sys.exit(1)

    db = DatabaseManager(url=url, key=key)
    cred_path = Path(os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json"))
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(setup_router(db, credentials_path=cred_path))
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


def main_polling() -> None:
    asyncio.run(run_polling_bot())


if __name__ == "__main__":
    main_polling()
