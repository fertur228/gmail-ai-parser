"""Gmail API client: unread messages with HTML stripped to plain text."""

from __future__ import annotations

import base64
import json
import re
from html import unescape
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Gmail evaluates newer_than:1d at query time (last 24h from “now” in the mailbox).
GMAIL_UNREAD_LIST_QUERY = "is:unread newer_than:1d -from:linkedin.com"


def strip_html_to_text(html: str) -> str:
    """Remove tags/script noise and collapse whitespace for LLM-friendly text."""
    if not html:
        return ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _b64url_decode(data: str) -> str:
    if not data:
        return ""
    pad = 4 - len(data) % 4
    if pad != 4:
        data += "=" * pad
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _header_map(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in payload.get("headers") or []:
        name = (h.get("name") or "").lower()
        if name:
            out[name] = h.get("value") or ""
    return out


def subject_from_message(message: dict[str, Any]) -> str:
    payload = message.get("payload") or {}
    return _header_map(payload).get("subject", "")


def plain_text_from_gmail_payload(payload: dict[str, Any]) -> str:
    """Extract readable plain text from a Gmail API `payload` (message format=full)."""
    mime = (payload.get("mimeType") or "").lower()
    body = payload.get("body") or {}
    data = body.get("data")
    if data and "multipart" not in mime:
        raw = _b64url_decode(data)
        if "text/html" in mime:
            return strip_html_to_text(raw)
        return raw.strip()

    plain_chunks: list[str] = []
    html_chunks: list[str] = []

    def walk(parts: list[dict[str, Any]]) -> None:
        for part in parts:
            pmime = (part.get("mimeType") or "").lower()
            b = part.get("body") or {}
            d = b.get("data")
            if d and "multipart" not in pmime:
                chunk = _b64url_decode(d)
                if "text/plain" in pmime:
                    plain_chunks.append(chunk)
                elif "text/html" in pmime:
                    html_chunks.append(chunk)
            sub = part.get("parts")
            if sub:
                walk(sub)

    parts = payload.get("parts") or []
    if parts:
        walk(parts)
    if plain_chunks:
        return "\n".join(c.strip() for c in plain_chunks if c.strip()).strip()
    if html_chunks:
        return strip_html_to_text("\n".join(html_chunks))
    return ""


def _client_id_secret_from_credentials_file(path: str | Path) -> tuple[str, str]:
    raw = Path(path).read_text(encoding="utf-8")
    doc = json.loads(raw)
    if "installed" in doc:
        cfg = doc["installed"]
    elif "web" in doc:
        cfg = doc["web"]
    else:
        raise ValueError("credentials.json must contain 'installed' or 'web' OAuth client")
    return cfg["client_id"], cfg["client_secret"]


class GmailClient:
    """Read-only Gmail access using a stored refresh token."""

    def __init__(
        self,
        gmail_refresh_token: str,
        client_secrets_path: str | Path = "credentials.json",
    ) -> None:
        self._refresh_token = gmail_refresh_token
        self._secrets_path = Path(client_secrets_path)
        client_id, client_secret = _client_id_secret_from_credentials_file(
            self._secrets_path
        )
        self._client_id = client_id
        self._client_secret = client_secret
        self._creds = self._credentials()
        self._service = build(
            "gmail", "v1", credentials=self._creds, cache_discovery=False
        )

    def _credentials(self) -> Credentials:
        creds = Credentials(
            token=None,
            refresh_token=self._refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=[GMAIL_READONLY_SCOPE],
        )
        creds.refresh(Request())
        return creds

    def fetch_unread_emails(self, max_results: int = 50) -> list[dict[str, Any]]:
        user = self._service.users()
        listed = (
            user.messages()
            .list(
                userId="me",
                q=GMAIL_UNREAD_LIST_QUERY,
                maxResults=max_results,
            )
            .execute()
        )
        refs = listed.get("messages") or []
        out: list[dict[str, Any]] = []
        for ref in refs:
            mid = ref["id"]
            msg = (
                user.messages()
                .get(userId="me", id=mid, format="full")
                .execute()
            )
            payload = msg.get("payload") or {}
            body_text = plain_text_from_gmail_payload(payload)
            out.append(
                {
                    "id": mid,
                    "thread_id": msg.get("threadId"),
                    "subject": subject_from_message(msg),
                    "body_text": body_text,
                }
            )
        print(
            f"[Gmail] Найдено новых писем: {len(out)} (после фильтрации LinkedIn).",
            flush=True,
        )
        return out
