"""Google Calendar events (personal account refresh token)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def _client_id_secret(path: str | Path) -> tuple[str, str]:
    raw = Path(path).read_text(encoding="utf-8")
    doc = json.loads(raw)
    if "installed" in doc:
        cfg = doc["installed"]
    elif "web" in doc:
        cfg = doc["web"]
    else:
        raise ValueError("credentials.json must contain 'installed' or 'web' OAuth client")
    return cfg["client_id"], cfg["client_secret"]


def _start_end_bodies(start_datetime_iso: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build Calendar API start/end objects (all-day date or dateTime)."""
    raw = (start_datetime_iso or "").strip()
    if not raw:
        raise ValueError("start_datetime_iso is empty")

    if "T" not in raw and len(raw) <= 10:
        d = date.fromisoformat(raw[:10])
        end_d = d + timedelta(days=1)
        return {"date": d.isoformat()}, {"date": end_d.isoformat()}

    s = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    start_dt = datetime.fromisoformat(s)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(hours=1)
    return (
        {"dateTime": start_dt.isoformat()},
        {"dateTime": end_dt.isoformat()},
    )


class CalendarClient:
    """Create events in the authenticated user's primary calendar (`primary`)."""

    def __init__(
        self,
        calendar_refresh_token: str,
        client_secrets_path: str | Path = "credentials.json",
    ) -> None:
        self._refresh_token = calendar_refresh_token
        self._secrets_path = Path(client_secrets_path)
        cid, secret = _client_id_secret(self._secrets_path)
        self._creds = Credentials(
            token=None,
            refresh_token=self._refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cid,
            client_secret=secret,
            scopes=[CALENDAR_EVENTS_SCOPE],
        )
        self._creds.refresh(Request())
        self._service = build(
            "calendar",
            "v3",
            credentials=self._creds,
            cache_discovery=False,
        )

    def add_event(
        self,
        title: str,
        start_datetime_iso: str,
        description: str = "",
    ) -> str:
        """Insert event into primary calendar; returns event id."""
        start_body, end_body = _start_end_bodies(start_datetime_iso)
        body: dict[str, Any] = {
            "summary": title or "Событие",
            "description": description or "",
            "start": start_body,
            "end": end_body,
        }
        created = (
            self._service.events()
            .insert(calendarId="primary", body=body)
            .execute()
        )
        eid = created.get("id")
        if not eid:
            raise RuntimeError("Calendar API returned no event id")
        return str(eid)
