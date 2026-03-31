"""Google Tasks: create tasks using stored refresh token (e.g. fertur account)."""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"


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


class TasksClient:
    """Insert tasks into the first available task list (primary list for most users)."""

    def __init__(
        self,
        tasks_refresh_token: str,
        client_secrets_path: str | Path = "credentials.json",
    ) -> None:
        self._refresh_token = tasks_refresh_token
        self._secrets_path = Path(client_secrets_path)
        cid, secret = _client_id_secret(self._secrets_path)
        creds = Credentials(
            token=None,
            refresh_token=self._refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cid,
            client_secret=secret,
            scopes=[TASKS_SCOPE],
        )
        creds.refresh(Request())
        self._service = build("tasks", "v1", credentials=creds, cache_discovery=False)

    def add_task(self, title: str, notes: str = "") -> str:
        """Create task; returns task id."""
        lists = self._service.tasklists().list(maxResults=10).execute()
        items = lists.get("items") or []
        if not items:
            raise RuntimeError("No Google Task lists found for this account")
        task_list_id = items[0]["id"]
        body = {"title": title or "Без названия", "notes": notes or ""}
        created = (
            self._service.tasks()
            .insert(tasklist=task_list_id, body=body)
            .execute()
        )
        tid = created.get("id")
        if not tid:
            raise RuntimeError("Tasks API returned no task id")
        return str(tid)
