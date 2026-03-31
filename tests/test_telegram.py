"""Telegram message text and inline keyboard layout (no Bot API calls)."""

from __future__ import annotations

from src.telegram_bot import (
    build_inline_keyboard,
    format_notification_text,
    CATEGORY_EMOJI,
)


def test_format_notification_event_shows_calendar_emoji_and_subject():
    text = format_notification_text(
        "All-hands Friday",
        "event",
        "Company-wide meeting next week.",
    )
    assert CATEGORY_EMOJI["event"] in text
    assert "Событие" in text
    assert "All-hands Friday" in text
    assert "Company-wide" in text


def test_format_notification_task_emoji():
    text = format_notification_text("Please approve", "task", "Sign off on budget.")
    assert "✅" in text or CATEGORY_EMOJI["task"] in text
    assert "Задача" in text
    assert "approve" in text


def test_format_notification_info_emoji_and_empty_subject_fallback():
    text = format_notification_text("", "info", "Newsletter body.")
    assert "ℹ️" in text
    assert "(без темы)" in text


def test_keyboard_event_includes_calendar_and_interest_buttons():
    kb = build_inline_keyboard("event", "deadbeefcafebabe")
    rows = kb.inline_keyboard
    flat = [b for row in rows for b in row]
    texts = [b.text for b in flat]
    callbacks = [b.callback_data for b in flat]
    assert "📅 В календарь" in texts
    assert "👍 Интересно" in texts
    assert "👎 Не интересно" in texts
    assert any(c.startswith("cal|deadbeefcafebabe") for c in callbacks if c)
    assert any(c.startswith("up|deadbeefcafebabe") for c in callbacks if c)
    assert any(c.startswith("dn|deadbeefcafebabe") for c in callbacks if c)
    assert not any("В задачи" in t for t in texts)


def test_keyboard_task_includes_tasks_and_thumbs():
    kb = build_inline_keyboard("task", "tok123")
    texts = [b.text for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "✅ В задачи" in texts
    assert "👍 Интересно" in texts
    assert any(c and c.startswith("tsk|tok123") for c in callbacks)
    assert not any("В календарь" in t for t in texts)


def test_keyboard_info_includes_tasks_and_thumbs():
    kb = build_inline_keyboard("info", "x")
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert len(texts) == 3
    assert "✅ В задачи" in texts
    assert "👍 Интересно" in texts
    assert "👎 Не интересно" in texts


def test_callback_data_respects_telegram_length_limit():
    token = "a" * 50
    kb = build_inline_keyboard("info", token)
    for row in kb.inline_keyboard:
        for b in row:
            assert len((b.callback_data or "").encode("utf-8")) <= 64
