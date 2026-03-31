"""Supabase-backed persistence for user settings and processed emails."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Literal

from supabase import Client, create_client

_UNSET = object()

EmailCategory = Literal["event", "task", "info"]


class DuplicateMessageIdError(Exception):
    """Raised when inserting a processed email with an existing message_id."""


def _is_unique_violation(err: Exception) -> bool:
    text = str(err).lower()
    return "23505" in text or "unique" in text or "duplicate" in text


class DatabaseManager:
    """CRUD for user_config and processed_emails via supabase-py."""

    def __init__(self, url: str, key: str) -> None:
        self._client: Client = create_client(url, key)

    def save_user_config(
        self,
        telegram_id: int,
        interests_text: Any = _UNSET,
        gmail_refresh_token: Any = _UNSET,
        calendar_refresh_token: Any = _UNSET,
        last_sync_at: Any = _UNSET,
        last_weekly_report_at: Any = _UNSET,
        tasks_refresh_token: Any = _UNSET,
        last_daily_digest_at: Any = _UNSET,
    ) -> None:
        """Upsert user row; omitted keywords keep values already stored in Supabase."""
        existing = self.get_user_config(telegram_id) or {}

        def resolved(key: str, new_val: Any) -> Any:
            if new_val is not _UNSET:
                return new_val
            return existing.get(key)

        ls = resolved("last_sync_at", last_sync_at)
        if ls is not None and hasattr(ls, "isoformat"):
            ls = ls.isoformat()

        lwr = resolved("last_weekly_report_at", last_weekly_report_at)
        if lwr is not None and hasattr(lwr, "isoformat"):
            lwr = lwr.isoformat()

        ldd = resolved("last_daily_digest_at", last_daily_digest_at)
        if ldd is not None and hasattr(ldd, "isoformat"):
            ldd = ldd.isoformat()

        payload: dict[str, Any] = {
            "telegram_id": telegram_id,
            "interests_text": resolved("interests_text", interests_text),
            "gmail_refresh_token": resolved(
                "gmail_refresh_token", gmail_refresh_token
            ),
            "calendar_refresh_token": resolved(
                "calendar_refresh_token", calendar_refresh_token
            ),
            "tasks_refresh_token": resolved(
                "tasks_refresh_token", tasks_refresh_token
            ),
            "last_sync_at": ls,
            "last_weekly_report_at": lwr,
            "last_daily_digest_at": ldd,
        }
        self._client.table("user_config").upsert(
            payload,
            on_conflict="telegram_id",
        ).execute()

    def get_user_config(self, telegram_id: int) -> dict[str, Any] | None:
        res = (
            self._client.table("user_config")
            .select("*")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    def is_message_processed(self, message_id: str) -> bool:
        res = (
            self._client.table("processed_emails")
            .select("message_id")
            .eq("message_id", message_id)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def save_processed_email(
        self,
        message_id: str,
        subject: str,
        summary: str,
        category: EmailCategory,
        relevance_score: float,
        is_interesting: bool | None = None,
        event_datetime_iso: str | None = None,
        owner_telegram_id: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "message_id": message_id,
            "subject": subject,
            "summary": summary,
            "category": category,
            "relevance_score": relevance_score,
            "is_interesting": is_interesting,
            "event_datetime_iso": event_datetime_iso,
            "owner_telegram_id": owner_telegram_id,
        }
        try:
            self._client.table("processed_emails").insert(payload).execute()
        except Exception as exc:
            if _is_unique_violation(exc):
                raise DuplicateMessageIdError(message_id) from exc
            raise

    def assign_callback_token(self, message_id: str) -> str:
        """Set a short unique token for Telegram callbacks; returns the token."""
        for _ in range(12):
            token = secrets.token_hex(8)
            try:
                self._client.table("processed_emails").update(
                    {"callback_token": token}
                ).eq("message_id", message_id).execute()
                return token
            except Exception as exc:
                if _is_unique_violation(exc):
                    continue
                raise
        raise RuntimeError("Could not assign callback_token")

    def get_processed_email(self, message_id: str) -> dict[str, Any] | None:
        res = (
            self._client.table("processed_emails")
            .select("*")
            .eq("message_id", message_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    def get_disliked_recent_summaries(
        self,
        owner_telegram_id: int,
        limit: int = 10,
    ) -> list[str]:
        """Subjects/summaries the user marked not interesting (feedback loop)."""
        lim = max(1, min(limit, 20))
        res = (
            self._client.table("processed_emails")
            .select("subject,summary")
            .eq("owner_telegram_id", owner_telegram_id)
            .eq("is_interesting", False)
            .order("created_at", desc=True)
            .limit(lim)
            .execute()
        )
        out: list[str] = []
        for row in res.data or []:
            sub = (row.get("subject") or "").strip()
            summ = (row.get("summary") or "").strip()
            if sub or summ:
                out.append(f"{sub}: {summ}".strip(": ").strip())
        return out

    def get_processed_emails_since(
        self,
        owner_telegram_id: int,
        since_iso: str,
    ) -> list[dict[str, Any]]:
        res = (
            self._client.table("processed_emails")
            .select("*")
            .eq("owner_telegram_id", owner_telegram_id)
            .gte("created_at", since_iso)
            .order("created_at", desc=False)
            .execute()
        )
        return list(res.data or [])

    def get_processed_emails_local_calendar_day(
        self,
        owner_telegram_id: int,
        tz_name: str,
    ) -> list[dict[str, Any]]:
        """Rows whose created_at falls on the current local calendar day."""
        from datetime import datetime, timedelta, timezone

        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
        now_local = datetime.now(tz)
        start_local = now_local.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_local = start_local + timedelta(days=1)
        start_iso = start_local.astimezone(timezone.utc).isoformat()
        end_iso = end_local.astimezone(timezone.utc).isoformat()

        res = (
            self._client.table("processed_emails")
            .select("*")
            .eq("owner_telegram_id", owner_telegram_id)
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
            .order("created_at", desc=False)
            .execute()
        )
        return list(res.data or [])

    def get_message_id_by_callback_token(self, token: str) -> str | None:
        res = (
            self._client.table("processed_emails")
            .select("message_id")
            .eq("callback_token", token)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0]["message_id"] if rows else None

    def update_processed_email_interesting(
        self,
        message_id: str,
        is_interesting: bool,
    ) -> None:
        self._client.table("processed_emails").update(
            {"is_interesting": is_interesting}
        ).eq("message_id", message_id).execute()
