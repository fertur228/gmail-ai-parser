"""Database CRUD tests (TDD) — run against Supabase with migrations applied."""

from __future__ import annotations

import os
import random
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"),
    reason="SUPABASE_URL and SUPABASE_KEY required",
)


@pytest.fixture
def db():
    from src.database import DatabaseManager

    return DatabaseManager(
        url=os.environ["SUPABASE_URL"],
        key=os.environ["SUPABASE_KEY"],
    )


@pytest.fixture
def unique_telegram_id() -> int:
    """Avoid collisions between test runs."""
    return 9_000_000_000_000 + random.randint(0, 999_999_999)


def test_user_config_save_and_get(db, unique_telegram_id: int):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db.save_user_config(
        telegram_id=unique_telegram_id,
        interests_text="python, ml, conferences",
        gmail_refresh_token="refresh-test-token",
        last_sync_at=now,
    )

    row = db.get_user_config(unique_telegram_id)
    assert row is not None
    assert row["telegram_id"] == unique_telegram_id
    assert row["interests_text"] == "python, ml, conferences"
    assert row["gmail_refresh_token"] == "refresh-test-token"
    assert row["last_sync_at"] is not None
    raw_ts = row["last_sync_at"]
    if isinstance(raw_ts, str):
        parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    else:
        parsed = raw_ts
    if getattr(parsed, "tzinfo", None) is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    assert abs((parsed - now).total_seconds()) < 120

    missing = db.get_user_config(1_111_111_111_111_111_111)
    assert missing is None


def test_processed_email_insert_and_duplicate_rejected(db):
    from src.database import DuplicateMessageIdError

    message_id = f"test-msg-{uuid.uuid4()}"

    db.save_processed_email(
        message_id=message_id,
        subject="Hello",
        summary="Test summary",
        category="info",
        relevance_score=0.85,
        is_interesting=True,
    )

    with pytest.raises(DuplicateMessageIdError):
        db.save_processed_email(
            message_id=message_id,
            subject="Other subject",
            summary="Other",
            category="task",
            relevance_score=0.1,
            is_interesting=False,
        )
