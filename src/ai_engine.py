"""LLM-based email analysis (Groq): relevance, category, summary, optional event ISO time."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, TypedDict

from groq import Groq

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are an expert email triage assistant for a busy professional.

Your job: read the email subject and body together with the user's stated interests, then respond with ONE JSON object only (no markdown fences, no prose before or after).

User interests describe what matters to them (topics, people, projects, languages, etc.). Use them to judge relevance objectively: strong topical match → higher score; generic spam or unrelated mail → low score.

Learning from negative feedback:
- The user prompt may include recent emails they explicitly marked as NOT interesting (thumbs down).
- Treat these as anti-patterns: topics, senders, tone, or categories they do not want surfaced again.
- If the new email resembles those disliked examples (same domain, same newsletter type, same vague promo style, etc.), lower relevance_score and prefer "info" or a low score rather than pushing it as a task/event.
- Do not copy phrasing from examples; use them only to infer what to filter out.

Scoring:
- relevance_score: a number from 0.0 to 1.0 (float). How useful or on-topic this message is given the interests. 1.0 = clearly aligned; 0.0 = irrelevant or noise.

Classification (pick exactly one):
- "event": meetings, invites, webinars, deadlines with a concrete calendar moment, scheduled calls, "join us on Tuesday at 3pm", conference dates, etc.
- "task": something the user should do (approve, pay, reply, sign, submit, review, follow up) without necessarily being a calendar block.
- "info": FYI, newsletters, announcements, receipts without action, etc.

Summary:
- summary: 2–3 short sentences maximum, plain language, no bullet points.

If and ONLY IF category is "event":
- event_datetime_iso: date-time in ISO 8601 (e.g. "2026-04-15T15:30:00+02:00" or "2026-04-15"). Infer timezone from the email text if stated; otherwise use a sensible date-only or local offset only if unambiguous. If no datetime can be extracted, use null.

If category is NOT "event":
- event_datetime_iso must be null.

Also set:
- is_interesting: true if relevance_score >= 0.5 and the message is worth a human glance; false if clearly low value; null only if truly impossible to tell (rare).

Required JSON shape (all keys must appear):
{"relevance_score": <float>, "category": "event"|"task"|"info", "summary": "<string>", "event_datetime_iso": "<string|null>", "is_interesting": <true|false|null>}
"""

USER_PROMPT_TEMPLATE = """User interests (may be empty):
{interests}

Recently marked NOT interesting by the user (avoid similar noise; empty if none):
{disliked}

Email subject:
{subject}

Email body:
{body}
"""

WEEKLY_REPORT_USER_PROMPT = """Ниже — обработанные за последние ~7 дней письма (категория, тема, краткое содержание).
Сформируй развёрнутый **недельный отчёт о продуктивности** на русском для одного читателя:
1) Объём и характер потока: сколько событий / задач / информационных писем, что перетягивало внимание.
2) Продуктивность: что похоже на реальные обязательства vs шум; были ли «узкие места» (срочное, дедлайны, переносы).
3) Паттерны: отправители/типы писем, повторяющиеся темы, риск перегруза.
4) **Три конкретных действия** на следующую неделю (короткие формулировки, без воды).
5) Один абзац «что уменьшить или делегировать».

Ориентир ~350–450 слов, связный текст, не список из 20 буллетов.

Данные:
{lines}
"""

DAILY_DIGEST_USER_PROMPT = """Ниже — письма, отфильтрованные ассистентом за **текущие календарные сутки** (локальное время пользователя уже учтено в выборке).
Сформируй **одно итоговое сообщение «Итоги дня»** на русском: что важного пришло, какие темы и действия выделить, без пересказа каждого письма дословно.
Объём ~120–200 слов, дружелюбный тон.

Данные:
{lines}
"""


class EmailAnalysis(TypedDict):
    relevance_score: float
    category: Literal["event", "task", "info"]
    summary: str
    event_datetime_iso: str | None
    is_interesting: bool | None


def extract_json_object_from_text(raw: str) -> dict[str, Any] | None:
    """Parse first JSON object from model output; tolerate markdown fences and trailing junk."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()

    fence = re.match(
        r"^```(?:json)?\s*([\s\S]*?)\s*```",
        s,
        re.IGNORECASE,
    )
    if fence:
        s = fence.group(1).strip()

    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", s):
        frag = m.group()
        try:
            data = json.loads(frag)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        frag = s[start : end + 1]
        try:
            data = json.loads(frag)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass

    return None


DEFAULT_ANALYSIS: EmailAnalysis = {
    "relevance_score": 0.0,
    "category": "info",
    "summary": "",
    "event_datetime_iso": None,
    "is_interesting": None,
}


def normalize_analysis(data: dict[str, Any] | None) -> EmailAnalysis:
    """Coerce model JSON into EmailAnalysis; never raises — bad input maps to safe defaults."""
    if not data:
        return dict(DEFAULT_ANALYSIS)

    out: EmailAnalysis = dict(DEFAULT_ANALYSIS)  # type: ignore[assignment]

    raw_score = data.get("relevance_score", 0)
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    out["relevance_score"] = max(0.0, min(1.0, score))

    cat = data.get("category", "info")
    if isinstance(cat, str) and cat.lower() in ("event", "task", "info"):
        out["category"] = cat.lower()  # type: ignore[assignment]
    else:
        out["category"] = "info"

    summ = data.get("summary", "")
    if summ is None:
        out["summary"] = ""
    elif isinstance(summ, str):
        out["summary"] = summ.strip()
    else:
        out["summary"] = str(summ).strip()

    ev = data.get("event_datetime_iso")
    if out["category"] == "event":
        if isinstance(ev, str) and ev.strip():
            out["event_datetime_iso"] = ev.strip()
        elif ev is None:
            out["event_datetime_iso"] = None
        else:
            out["event_datetime_iso"] = str(ev).strip() or None
    else:
        out["event_datetime_iso"] = None

    ii = data.get("is_interesting")
    if ii is True or ii is False:
        out["is_interesting"] = ii
    elif isinstance(ii, str):
        low = ii.strip().lower()
        if low in ("true", "1", "yes"):
            out["is_interesting"] = True
        elif low in ("false", "0", "no"):
            out["is_interesting"] = False
        else:
            out["is_interesting"] = None
    else:
        out["is_interesting"] = None

    return out


class AIEngine:
    """Groq (Llama) client for structured email analysis and digests."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> None:
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise ValueError(
                "Missing API key: pass api_key= or set GROQ_API_KEY in the environment"
            )
        self.client = Groq(api_key=key)
        self._model_id = (
            (model_name or os.getenv("GROQ_MODEL") or "").strip()
            or DEFAULT_GROQ_MODEL
        )

    def _chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        model: str | None = None,
    ) -> str:
        mid = model or self._model_id
        chat_completion = self.client.chat.completions.create(
            model=mid,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        choice = chat_completion.choices[0]
        content = choice.message.content if choice and choice.message else None
        return (content or "").strip()

    def analyze_email(
        self,
        subject: str,
        body: str,
        interests_text: str,
        disliked_summaries: list[str] | None = None,
    ) -> EmailAnalysis:
        interests_text = interests_text or ""
        disliked_summaries = disliked_summaries or []
        if disliked_summaries:
            disliked_block = "\n".join(
                f"- {line}" for line in disliked_summaries[:10]
            )
        else:
            disliked_block = "(none)"
        payload = USER_PROMPT_TEMPLATE.format(
            interests=interests_text,
            disliked=disliked_block,
            subject=subject or "",
            body=body or "",
        )
        raw = self._chat(
            SYSTEM_PROMPT,
            payload,
            temperature=0.2,
            model="llama-3.3-70b-versatile",
        )
        parsed = extract_json_object_from_text(raw)
        return normalize_analysis(parsed)

    def generate_weekly_analytics_report(
        self,
        entries: list[dict[str, Any]],
    ) -> str:
        """Free-form Russian analytics for Telegram; no JSON."""
        if not entries:
            return (
                "За последние 7 дней нет сохранённых обработанных писем "
                "для отчёта."
            )
        lines: list[str] = []
        for e in entries:
            cat = e.get("category", "?")
            sub = (e.get("subject") or "")[:120]
            summ = (e.get("summary") or "")[:300]
            lines.append(f"[{cat}] {sub}\n  {summ}")
        blob = "\n".join(lines)
        prompt = WEEKLY_REPORT_USER_PROMPT.format(lines=blob)
        text = self._chat(
            "You follow instructions precisely and write fluent Russian.",
            prompt,
            temperature=0.35,
        )
        return text or "Не удалось сформировать отчёт."

    def generate_daily_digest(self, entries: list[dict[str, Any]]) -> str:
        """«Итоги дня» — одно сообщение для Telegram."""
        if not entries:
            return "Итоги дня: за сегодня нет сохранённых отобранных писем."
        lines: list[str] = []
        for e in entries:
            cat = e.get("category", "?")
            sub = (e.get("subject") or "")[:100]
            summ = (e.get("summary") or "")[:240]
            lines.append(f"[{cat}] {sub}\n  {summ}")
        blob = "\n".join(lines)
        prompt = DAILY_DIGEST_USER_PROMPT.format(lines=blob)
        text = self._chat(
            "You follow instructions precisely and write fluent Russian.",
            prompt,
            temperature=0.35,
        )
        return text or "Не удалось сформировать итоги дня."
