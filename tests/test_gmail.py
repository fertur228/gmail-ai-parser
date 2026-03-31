"""Gmail parsing tests with mocks (no live API)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from src.gmail_client import (
    GmailClient,
    plain_text_from_gmail_payload,
    strip_html_to_text,
    subject_from_message,
)


def test_strip_html_to_text_keeps_readable_content():
    html = (
        "<html><body>"
        "<p>Hello &amp; welcome</p>"
        "<script>alert(1)</script>"
        "<style>.x{color:red}</style>"
        "</body></html>"
    )
    text = strip_html_to_text(html)
    assert "alert" not in text
    assert "Hello & welcome" in text or ("Hello" in text and "welcome" in text)


def test_plain_text_from_multipart_prefers_plain():
    plain = "Plain body line."
    html = "<b>Bold</b> only"
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {
                    "data": base64.urlsafe_b64encode(plain.encode()).decode().rstrip("=")
                },
            },
            {
                "mimeType": "text/html",
                "body": {
                    "data": base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")
                },
            },
        ],
    }
    assert "Plain body" in plain_text_from_gmail_payload(payload)


def test_plain_text_from_html_only_part():
    html = "<div>Meeting at <span>3pm</span></div>"
    b64 = base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/html",
                "body": {"data": b64},
            },
        ],
    }
    text = plain_text_from_gmail_payload(payload)
    assert "Meeting" in text and "3pm" in text


def test_subject_from_message_headers():
    msg = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Re: Sprint planning"},
            ],
        },
    }
    assert subject_from_message(msg) == "Re: Sprint planning"


@patch("src.gmail_client.build")
@patch("src.gmail_client.GmailClient._credentials")
def test_fetch_unread_emails_uses_mocked_api(
    mock_creds: MagicMock,
    mock_build: MagicMock,
    tmp_path,
):
    secrets = tmp_path / "credentials.json"
    secrets.write_text(
        '{"installed":{"client_id":"x.apps.googleusercontent.com",'
        '"client_secret":"secret","redirect_uris":["http://localhost"]}}',
        encoding="utf-8",
    )

    list_exec = MagicMock()
    list_exec.execute.return_value = {"messages": [{"id": "msg1"}]}
    get_exec = MagicMock()
    html = "<p>Invoice attached</p>"
    raw_b64 = base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")
    get_exec.execute.return_value = {
        "id": "msg1",
        "threadId": "t1",
        "payload": {
            "mimeType": "text/html",
            "body": {"data": raw_b64},
            "headers": [{"name": "Subject", "value": "Invoice #9"}],
        },
    }

    messages_api = MagicMock()
    messages_api.list.return_value = list_exec
    messages_api.get.return_value = get_exec
    users_api = MagicMock()
    users_api.messages.return_value = messages_api
    service = MagicMock()
    service.users.return_value = users_api
    mock_build.return_value = service

    client = GmailClient("fake-refresh", client_secrets_path=secrets)
    rows = client.fetch_unread_emails(max_results=10)

    assert len(rows) == 1
    assert rows[0]["id"] == "msg1"
    assert rows[0]["subject"] == "Invoice #9"
    assert "Invoice" in rows[0]["body_text"]
    messages_api.list.assert_called_once()
    q = messages_api.list.call_args.kwargs.get("q", "")
    assert "is:unread" in q
    assert "newer_than:1d" in q
    assert "-from:linkedin.com" in q
