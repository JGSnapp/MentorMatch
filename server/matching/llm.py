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
    ) -> Optional[List[ParsedItem]]:
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
        except Exception as exc:  # pragma: no cover - defensive logging
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
        for raw in raw_items[:5]:
            if not isinstance(raw, dict):
                continue
            parsed_item = parser(raw)
            if parsed_item is None:
                continue
            items.append(parsed_item)

        return items if len(items) == 5 else None

    def rank_candidates(self, payload_json: str) -> Optional[List[ParsedItem]]:
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
                    "minItems": 5,
                    "maxItems": 5,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
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
            description="Верни пять кандидатов с краткими пояснениями.",
            system_prompt=(
                "Ты ассистент, который подбирает людей к темам. Отвечай по-русски и"
                " используй функцию только с пятью элементами."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                "Вызови функцию rank_candidates и передай пять лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
        )

    def rank_topics(self, payload_json: str) -> Optional[List[ParsedItem]]:
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
                    "minItems": 5,
                    "maxItems": 5,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
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
            description="Предложи пять тем и объясни выбор.",
            system_prompt=(
                "Ты помогаешь студенту выбрать темы. Всегда отвечай по-русски и"
                " вызывай функцию только с пятью элементами."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                "Вызови функцию rank_topics и передай пять лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
        )

    def rank_roles(self, payload_json: str) -> Optional[List[ParsedItem]]:
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
                    "minItems": 5,
                    "maxItems": 5,
                }
            },
            "required": ["top"],
        }

        def _parse(raw: Dict[str, Any]) -> Optional[ParsedItem]:
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
            description="Выбери пять ролей для студента и добавь пояснения.",
            system_prompt=(
                "Ты ассистент, который помогает студенту подобрать роли."
                " Отвечай на русском и возвращай пять элементов через функцию."
            ),
            user_prompt=(
                "Входные данные (JSON):\n"
                f"{payload_json}\n\n"
                "Вызови функцию rank_roles и передай пять лучших вариантов."
            ),
            schema=schema,
            parser=_parse,
        )


def create_matching_llm_client() -> Optional[MatchingLLMClient]:
    if not (PROXY_API_KEY and PROXY_BASE_URL):
        return None
    client = OpenAI(api_key=PROXY_API_KEY, base_url=PROXY_BASE_URL)
    return MatchingLLMClient(client, PROXY_MODEL)


__all__ = ["MatchingLLMClient", "create_matching_llm_client"]
