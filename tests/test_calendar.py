"""Calendar API payload helpers (no Google calls)."""

from __future__ import annotations

import pytest

from src.calendar_client import _start_end_bodies


def test_all_day_event_uses_date_fields():
    start, end = _start_end_bodies("2026-06-01")
    assert start == {"date": "2026-06-01"}
    assert end == {"date": "2026-06-02"}


def test_datetime_event_uses_date_time_and_one_hour_end():
    start, end = _start_end_bodies("2026-06-01T15:30:00+02:00")
    assert "dateTime" in start and "15:30" in start["dateTime"]
    assert "dateTime" in end


def test_empty_iso_raises():
    with pytest.raises(ValueError):
        _start_end_bodies("")
