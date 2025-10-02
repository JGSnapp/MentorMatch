"""Shared helper methods for bot handlers."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError, TimedOut
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class BaseHandlers:
    """Utility mixin with helper methods reused across handlers."""

    def _fix_text(self, s: Optional[str]) -> Optional[str]:
        if not isinstance(s, str):
            return s
        if not any(ch in s for ch in ("Ð", "Ñ", "Ã", "Â", "â", "ð")):
            return s
        for enc in ("cp1252", "latin1"):
            try:
                return s.encode(enc).decode("utf-8")
            except Exception:
                continue
        return s

    def _mk(self, kb: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
        for row in kb:
            for btn in row:
                try:
                    btn.text = self._fix_text(btn.text)
                except Exception:
                    pass
        return InlineKeyboardMarkup(kb)

    def _build_reply_markup(self, payload: Dict[str, Any]) -> Optional[InlineKeyboardMarkup]:
        keyboard: List[List[InlineKeyboardButton]] = []
        markup_payload = payload.get("reply_markup")
        if isinstance(markup_payload, str):
            try:
                markup_payload = json.loads(markup_payload)
            except Exception:
                logger.warning("Invalid reply_markup payload (not JSON): %s", markup_payload)
                markup_payload = None
        if isinstance(markup_payload, InlineKeyboardMarkup):
            return markup_payload
        if isinstance(markup_payload, dict):
            raw_keyboard = markup_payload.get("inline_keyboard")
            if isinstance(raw_keyboard, list):
                for row in raw_keyboard:
                    if not isinstance(row, list):
                        continue
                    row_buttons: List[InlineKeyboardButton] = []
                    for btn in row:
                        if not isinstance(btn, dict):
                            continue
                        text_val = btn.get("text")
                        if text_val is None:
                            continue
                        callback_data = btn.get("callback_data")
                        url = btn.get("url")
                        try:
                            if callback_data is not None:
                                row_buttons.append(
                                    InlineKeyboardButton(
                                        str(text_val), callback_data=str(callback_data)
                                    )
                                )
                            elif url is not None:
                                row_buttons.append(
                                    InlineKeyboardButton(str(text_val), url=str(url))
                                )
                        except Exception:
                            continue
                    if row_buttons:
                        keyboard.append(row_buttons)
        if not keyboard and payload.get("button_text") and payload.get("callback_data"):
            try:
                keyboard = [
                    [
                        InlineKeyboardButton(
                            str(payload.get("button_text")),
                            callback_data=str(payload.get("callback_data")),
                        )
                    ]
                ]
            except Exception:
                keyboard = []
        if keyboard:
            return self._mk(keyboard)
        return None

    async def _answer_callback(self, q, **kwargs) -> None:
        if not q:
            return
        try:
            await q.answer(**kwargs)
        except TimedOut:
            logger.warning("Timeout while answering callback %s", getattr(q, "data", None))
        except TelegramError as exc:
            logger.warning(
                "Failed to answer callback %s: %s", getattr(q, "data", None), exc
            )
        except Exception:
            logger.exception(
                "Unexpected error answering callback %s", getattr(q, "data", None)
            )

    def _ids_equal(self, left: Any, right: Any) -> bool:
        if left is None or right is None:
            return False
        left_int = self._parse_positive_int(left)
        right_int = self._parse_positive_int(right)
        if left_int is not None and right_int is not None:
            return left_int == right_int
        try:
            return str(left).strip() == str(right).strip()
        except Exception:
            return False

    def _should_skip_optional(self, text: Optional[str]) -> bool:
        if text is None:
            return True
        stripped = text.strip()
        if not stripped:
            return True
        lowered = stripped.lower()
        return lowered in {"-", "пропустить", "skip", "нет"}

    def _normalize_edit_input(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return self.EDIT_KEEP
        stripped = text.strip()
        if not stripped:
            return None
        lowered = stripped.lower()
        if lowered in {"пропустить", "оставить", "skip", "keep", "оставь", "не менять"}:
            return self.EDIT_KEEP
        if lowered in {"очистить", "удалить", "clear", "-", "нет"}:
            return None
        return text

    def _normalize_role_value(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        mapping = {
            "student": "student",
            "студент": "student",
            "студенты": "student",
            "supervisor": "supervisor",
            "руководитель": "supervisor",
            "научный руководитель": "supervisor",
        }
        return mapping.get(text.strip().lower())

    def _is_admin(self, update: Update) -> bool:
        user = update.effective_user
        if not user:
            return False
        if getattr(user, "id", None) in self.admin_ids:
            return True
        username = (getattr(user, "username", "") or "").lower()
        if username and username in self.admin_usernames:
            return True
        return False

    def _store_messages_cache(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        messages: List[Dict[str, Any]],
        *,
        source: str,
        list_callback: str,
    ) -> None:
        context.user_data["messages_cache"] = {
            "messages": messages,
            "source": source,
            "list_callback": list_callback,
        }

    def _build_message_view(
        self,
        message: Dict[str, Any],
        *,
        include_receiver: bool = False,
        include_sender: bool = False,
    ) -> str:
        lines = []
        sender = message.get("sender") or {}
        receiver = message.get("receiver") or {}
        if include_sender:
            lines.append(
                f"Отправитель: {sender.get('full_name') or sender.get('username') or sender.get('email') or '—'}"
            )
        if include_receiver:
            lines.append(
                f"Получатель: {receiver.get('full_name') or receiver.get('username') or receiver.get('email') or '—'}"
            )
        lines.append(f"Тема: {message.get('subject') or '—'}")
        lines.append("")
        body = message.get("body") or ""
        lines.append(body)
        return self._fix_text("\n".join(lines)) or ""
