"""OpenAI client wrapper used by matching services."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI

from .settings import LLM_TEMPERATURE, PROXY_API_KEY, PROXY_BASE_URL, PROXY_MODEL

logger = logging.getLogger(__name__)

ParsedItem = Dict[str, Any]
ItemParser = Callable[[Dict[str, Any]], Optional[ParsedItem]]


class MatchingLLMClient:
    """Thin wrapper above OpenAI Chat Completions with shared configuration."""

    def __init__(self, client: OpenAI, model: str) -> None:
        """Выполняет функцию __init__."""
        self._client = client
        self._model = model

    def _call_rank(
        self,
        *,
        function_name: str,
        description: str,
        system_prompt: str,
        user_prompt: str,
        schema: Dict[str, Any],
        parser: ItemParser,
        expected_count: int = 5,
    ) -> Optional[List[ParsedItem]]:
        """Выполняет функцию _call_rank."""
        functions = [
            {
                "name": function_name,
                "description": description,
                "parameters": schema,
            }
        ]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                functions=functions,
                function_call={"name": function_name},
                temperature=LLM_TEMPERATURE,
            )
        except Exception as exc:                                        
            logger.warning("LLM request failed: %s", exc)
            return None

        if not response.choices or not response.choices[0].message:
            return None

        message = response.choices[0].message
        function_call = getattr(message, "function_call", None)
        arguments = getattr(function_call, "arguments", None)
        if not arguments:
            return None

        try:
            parsed = json.loads(arguments)
        except Exception:
            logger.debug("Failed to decode LLM function arguments: %s", arguments)
            return None

        raw_items = parsed.get("top", []) if isinstance(parsed, dict) else []
        items: List[ParsedItem] = []
        for raw in raw_items[:expected_count]:
            if not isinstance(raw, dict):
                continue
            parsed_item = parser(raw)
            if parsed_item is None:
                continue
            items.append(parsed_item)

        return items if len(items) == expected_count else None

    def rank_candidates(
        self, payload_json: str, *, expected_count: int = 5
    ) -> Optional[List[ParsedItem]]:
        """Выполняет функцию rank_candidates."""
        expected_count = max(1, expected_count)
        schema = {
            "type": "object",
            "properties": {
                "top": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "integer"},
                            "num": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["user_id", "num", "reason"],
                    },
                    "minItems": expected_count,
                    "maxItems": expected_count,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
            """Выполняет функцию _parse."""
            try:
                return {
                    "user_id": int(raw.get("user_id")),
                    "num": int(raw.get("num")),
                    "reason": str(raw.get("reason") or ""),
                }
            except Exception:
                return None

        return self._call_rank(
            function_name="rank_candidates",
            description=(
                f"Верни {expected_count} кандидатов с краткими пояснениями."
            ),
            system_prompt=(
                "Ты ассистент, который подбирает людей к темам. Отвечай по-русски"
                f" и используй функцию только с {expected_count} элементами."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                f"Вызови функцию rank_candidates и передай {expected_count} лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
            expected_count=expected_count,
        )

    def rank_topics(self, payload_json: str) -> Optional[List[ParsedItem]]:
        """Выполняет функцию rank_topics."""
        expected_count = 5
        schema = {
            "type": "object",
            "properties": {
                "top": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic_id": {"type": "integer"},
                            "num": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["topic_id", "num", "reason"],
                    },
                    "minItems": expected_count,
                    "maxItems": expected_count,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
            """Выполняет функцию _parse."""
            try:
                return {
                    "topic_id": int(raw.get("topic_id")),
                    "num": int(raw.get("num")),
                    "reason": str(raw.get("reason") or ""),
                }
            except Exception:
                return None

        return self._call_rank(
            function_name="rank_topics",
            description=f"Предложи {expected_count} тем и объясни выбор.",
            system_prompt=(
                "Ты помогаешь студенту выбрать темы. Всегда отвечай по-русски и"
                f" вызывай функцию только с {expected_count} элементами."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                f"Вызови функцию rank_topics и передай {expected_count} лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
            expected_count=expected_count,
        )

    def rank_roles(self, payload_json: str) -> Optional[List[ParsedItem]]:
        """Выполняет функцию rank_roles."""
        expected_count = 5
        schema = {
            "type": "object",
            "properties": {
                "top": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role_id": {"type": "integer"},
                            "num": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["role_id", "num", "reason"],
                    },
                    "minItems": expected_count,
                    "maxItems": expected_count,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
            """Выполняет функцию _parse."""
            try:
                return {
                    "role_id": int(raw.get("role_id")),
                    "num": int(raw.get("num")),
                    "reason": str(raw.get("reason") or ""),
                }
            except Exception:
                return None

        return self._call_rank(
            function_name="rank_roles",
            description=(
                f"Выбери {expected_count} ролей для студента и добавь пояснения."
            ),
            system_prompt=(
                "Ты ассистент, который помогает студенту подобрать роли."
                f" Отвечай на русском и возвращай {expected_count} элементов через функцию."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                f"Вызови функцию rank_roles и передай {expected_count} лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
            expected_count=expected_count,
        )

    def rank_role_applicants(
        self, payload_json: str, *, top_n: int = 10
    ) -> Optional[List[ParsedItem]]:
        """Сортирует отклики на роль и возвращает топ кандидатов."""

        expected_count = max(1, top_n)
        schema = {
            "type": "object",
            "properties": {
                "top": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "integer"},
                            "num": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["user_id", "num", "reason"],
                    },
                    "minItems": expected_count,
                    "maxItems": expected_count,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
            """Выполняет функцию _parse."""
            try:
                return {
                    "user_id": int(raw.get("user_id")),
                    "num": int(raw.get("num")),
                    "reason": str(raw.get("reason") or ""),
                }
            except Exception:
                return None

        return self._call_rank(
            function_name="rank_applicants",
            description=(
                f"Выбери {expected_count} кандидатов из откликов на роль и"
                " объясни выбор."
            ),
            system_prompt=(
                "Ты ассистент, который помогает руководителю выбрать студента из"
                f" существующих откликов. Отвечай по-русски и возвращай {expected_count}"
                " элементов через функцию."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                f"Вызови функцию rank_applicants и передай {expected_count} лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
            expected_count=expected_count,
        )

    def rank_topic_applicants(
        self, payload_json: str, *, top_n: int = 10
    ) -> Optional[List[ParsedItem]]:
        """Сортирует отклики на тему и возвращает топ кандидатов."""

        expected_count = max(1, top_n)
        schema = {
            "type": "object",
            "properties": {
                "top": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "integer"},
                            "num": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["user_id", "num", "reason"],
                    },
                    "minItems": expected_count,
                    "maxItems": expected_count,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
            """Выполняет функцию _parse."""
            try:
                return {
                    "user_id": int(raw.get("user_id")),
                    "num": int(raw.get("num")),
                    "reason": str(raw.get("reason") or ""),
                }
            except Exception:
                return None

        return self._call_rank(
            function_name="rank_topic_applicants",
            description=(
                f"Выбери {expected_count} кандидатов из откликов на тему и"
                " объясни выбор."
            ),
            system_prompt=(
                "Ты помогаешь руководителю выбрать студента из откликов на тему."
                f" Отвечай по-русски и возвращай {expected_count} элементов через функцию."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                f"Вызови функцию rank_topic_applicants и передай {expected_count} лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
            expected_count=expected_count,
        )


def create_matching_llm_client() -> Optional[MatchingLLMClient]:
    """Выполняет функцию create_matching_llm_client."""
    if not (PROXY_API_KEY and PROXY_BASE_URL):
        return None
    client = OpenAI(api_key=PROXY_API_KEY, base_url=PROXY_BASE_URL)
    return MatchingLLMClient(client, PROXY_MODEL)


__all__ = ["MatchingLLMClient", "create_matching_llm_client"]
