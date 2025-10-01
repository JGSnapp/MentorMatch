"""Telegram notification helpers."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import get_bot_base_url

logger = logging.getLogger(__name__)


def shorten(text: Optional[str], limit: int = 60) -> str:
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)].rstrip() + "…"


def display_name(name: Optional[str], fallback_id: Optional[Any]) -> str:
    if name:
        stripped = str(name).strip()
        if stripped:
            return stripped
    if fallback_id not in (None, ""):
        return f"#{fallback_id}"
    return "Пользователь"


def send_telegram_notification(
    telegram_id: Optional[Any],
    text: str,
    *,
    button_text: Optional[str] = None,
    callback_data: Optional[str] = None,
) -> bool:
    base_url = get_bot_base_url()
    if not base_url:
        logger.warning("Skipping telegram notification: BOT_API_URL not configured")
        return False
    endpoint = str(base_url).rstrip("/") + "/notify"
    if telegram_id in (None, "", 0):
        return False
    try:
        chat_id = int(str(telegram_id).strip())
    except Exception:
        logger.warning("Invalid telegram_id value: %s", telegram_id)
        return False

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if button_text and callback_data:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {
                        "text": button_text,
                        "callback_data": callback_data,
                    }
                ]
            ]
        }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if 200 <= status < 300:
                resp.read()
                return True
            logger.warning("Bot notification endpoint %s returned HTTP %s", endpoint, status)
            return False
    except urllib_error.HTTPError as exc:
        logger.warning(
            "Bot notification failed with HTTP %s for chat %s: %s",
            getattr(exc, "code", "unknown"),
            chat_id,
            exc,
        )
    except urllib_error.URLError as exc:
        logger.warning("Bot notification request error for chat %s: %s", chat_id, exc)
    except Exception as exc:  # pragma: no cover - unexpected but logged
        logger.warning("Unexpected bot notification error for chat %s: %s", chat_id, exc)
    return False
