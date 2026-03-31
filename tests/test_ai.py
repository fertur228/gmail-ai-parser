"""AIEngine JSON parsing and Gemini mocks — no live API calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ai_engine import (
    AIEngine,
    DEFAULT_ANALYSIS,
    extract_json_object_from_text,
    normalize_analysis,
)


def test_extract_json_clean_object():
    raw = '{"relevance_score": 0.9, "category": "task", "summary": "Do X.", "event_datetime_iso": null, "is_interesting": true}'
    data = extract_json_object_from_text(raw)
    assert data is not None
    assert data["category"] == "task"
    assert data["relevance_score"] == 0.9


def test_extract_json_strips_markdown_fence():
    raw = """Here you go:
```json
{"relevance_score": 0.5, "category": "info", "summary": "FYI.", "event_datetime_iso": null, "is_interesting": false}
```
"""
    data = extract_json_object_from_text(raw)
    assert data is not None
    assert data["category"] == "info"


def test_extract_json_from_embedded_object():
    raw = 'Sure. {"relevance_score": 0.2, "category": "info", "summary": "Noise.", "event_datetime_iso": null, "is_interesting": false} Thanks.'
    data = extract_json_object_from_text(raw)
    assert data is not None
    assert data["relevance_score"] == 0.2


def test_extract_json_malformed_returns_none():
    assert extract_json_object_from_text("") is None
    assert extract_json_object_from_text("not json") is None
    assert extract_json_object_from_text("{broken") is None


def test_normalize_analysis_bad_input_falls_back_without_raising():
    assert normalize_analysis(None) == DEFAULT_ANALYSIS
    assert normalize_analysis({})["relevance_score"] == 0.0
    assert normalize_analysis({})["category"] == "info"


def test_normalize_analysis_clamps_score_and_category():
    n = normalize_analysis(
        {
            "relevance_score": 99,
            "category": "task",
            "summary": "  OK  ",
            "event_datetime_iso": "2026-01-01T12:00:00Z",
            "is_interesting": "true",
        }
    )
    assert n["relevance_score"] == 1.0
    assert n["category"] == "task"
    assert n["summary"] == "OK"
    assert n["event_datetime_iso"] == "2026-01-01T12:00:00Z"
    assert n["is_interesting"] is True


def test_normalize_analysis_event_datetime_cleared_for_non_event():
    n = normalize_analysis(
        {
            "relevance_score": 0.5,
            "category": "info",
            "summary": "x",
            "event_datetime_iso": "2026-05-01T10:00:00+00:00",
            "is_interesting": False,
        }
    )
    assert n["category"] == "info"
    assert n["event_datetime_iso"] is None


def test_normalize_analysis_invalid_category_defaults_to_info():
    n = normalize_analysis(
        {
            "relevance_score": 0.3,
            "category": "spam",
            "summary": "s",
            "event_datetime_iso": None,
        }
    )
    assert n["category"] == "info"


@patch("src.ai_engine.genai.GenerativeModel")
def test_analyze_email_uses_mock_and_parses_json(mock_model_cls: MagicMock):
    mock_resp = MagicMock()
    mock_resp.text = '{"relevance_score": 0.85, "category": "event", "summary": "Meet Friday.", "event_datetime_iso": "2026-04-01T14:00:00+00:00", "is_interesting": true}'
    inst = MagicMock()
    inst.generate_content.return_value = mock_resp
    mock_model_cls.return_value = inst

    eng = AIEngine(api_key="dummy", model_name="gemini-test")
    result = eng.analyze_email("Invite", "We meet at 2pm UTC Friday.", "meetings")

    assert result["relevance_score"] == pytest.approx(0.85)
    assert result["category"] == "event"
    assert "Friday" in result["summary"] or "meet" in result["summary"].lower()
    assert result["event_datetime_iso"] is not None
    inst.generate_content.assert_called_once()


@patch("src.ai_engine.genai.GenerativeModel")
def test_analyze_email_bad_model_output_normalizes_gracefully(mock_model_cls: MagicMock):
    mock_resp = MagicMock()
    mock_resp.text = "%%% not valid json at all $$$"
    inst = MagicMock()
    inst.generate_content.return_value = mock_resp
    mock_model_cls.return_value = inst

    eng = AIEngine(api_key="dummy")
    result = eng.analyze_email("S", "B", "")
    assert result == DEFAULT_ANALYSIS
