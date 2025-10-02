"""LLM-powered helpers for extracting topics from free-form text."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from matching.settings import LLM_TEMPERATURE, PROXY_API_KEY, PROXY_BASE_URL, PROXY_MODEL

logger = logging.getLogger(__name__)


def _create_openai_client() -> Optional[OpenAI]:
    if not (PROXY_API_KEY and PROXY_BASE_URL):
        return None
    try:
        return OpenAI(api_key=PROXY_API_KEY, base_url=PROXY_BASE_URL)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Unable to create OpenAI client for topic extraction: %s", exc)
        return None


def extract_topics_from_text(text: str) -> Optional[List[Dict[str, Any]]]:
    clean = (text or "").strip()
    if not clean:
        return None
    client = _create_openai_client()
    if client is None:
        return None

    functions = [
        {
            "name": "extract_topics",
            "description": "Верни список тем с описанием и требуемыми навыками.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topics": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "expected_outcomes": {"type": "string"},
                                "required_skills": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                        "minItems": 1,
                    }
                },
                "required": ["topics"],
            },
        }
    ]

    messages = [
        {
            "role": "system",
            "content": (
                "Ты аналитик и помогаешь выделить темы для студентов."
                " Найди самостоятельные темы, сформулируй короткое описание,"
                " ожидаемые результаты и необходимые навыки. Отвечай по-русски."
            ),
        },
        {
            "role": "user",
            "content": (
                "Исходный текст анкеты:\n"
                f"{clean}\n\n"
                "Вызови функцию extract_topics и передай список тем."
            ),
        },
    ]

    try:
        response = client.chat.completions.create(
            model=PROXY_MODEL,
            messages=messages,
            functions=functions,
            function_call={"name": "extract_topics"},
            temperature=LLM_TEMPERATURE,
        )
    except Exception as exc:  # pragma: no cover - remote failure
        logger.warning("Topic extraction request failed: %s", exc)
        return None

    if not response.choices or not response.choices[0].message:
        return None
    call = getattr(response.choices[0].message, "function_call", None)
    arguments = getattr(call, "arguments", None)
    if not arguments:
        return None

    try:
        parsed = json.loads(arguments)
    except Exception:
        logger.debug("Failed to decode topics payload: %s", arguments)
        return None

    raw_topics = parsed.get("topics") if isinstance(parsed, dict) else None
    if not isinstance(raw_topics, list):
        return None

    normalised: List[Dict[str, Any]] = []
    for raw in raw_topics:
        if not isinstance(raw, dict):
            continue
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        normalised.append(
            {
                "title": title,
                "description": (raw.get("description") or "").strip() or None,
                "expected_outcomes": (raw.get("expected_outcomes") or "").strip() or None,
                "required_skills": (raw.get("required_skills") or "").strip() or None,
            }
        )

    return normalised or None


def fallback_extract_topics(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    parts = re.split(r"[\n;\-\u2022]+|\s{2,}", text)
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for part in parts:
        title = (part or "").strip(" \t\r\n.-")
        if len(title) < 3:
            continue
        lowered = title.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(
            {
                "title": title,
                "description": None,
                "expected_outcomes": None,
                "required_skills": None,
            }
        )
    return result


__all__ = ["extract_topics_from_text", "fallback_extract_topics"]
