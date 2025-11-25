"""Command and menu handlers."""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, Update
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from .base import BaseHandlers

logger = logging.getLogger(__name__)


class MenuHandlers(BaseHandlers):
    async def cmd_start2(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию cmd_start2."""
        return await self.cmd_start(update, context)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию cmd_start."""
        context.user_data.pop("awaiting", None)
        context.user_data.pop("topic_role", None)
        for key in (
            "add_topic_payload",
            "add_topic_endpoint",
            "edit_student_payload",
            "edit_student_original",
            "edit_supervisor_payload",
            "edit_supervisor_original",
            "edit_topic_payload",
            "edit_topic_original",
            "edit_role_payload",
            "edit_role_original",
            "application_payload",
            "messages_cache",
            "student_match_back",
        ):
            context.user_data.pop(key, None)
        if self._is_admin(update):
            kb = [
                [InlineKeyboardButton("👨‍🎓 Студенты", callback_data="list_students")],
                [
                    InlineKeyboardButton(
                        "🧑‍🏫 Научные руководители", callback_data="list_supervisors"
                    )
                ],
                [InlineKeyboardButton("📚 Темы", callback_data="list_topics")],
            ]
            text = "Админ‑меню: выберите раздел"
            stats_line = await self._roles_stats_line()
            if stats_line:
                text = f"{text}\n\n{stats_line}"
            if update.message:
                await update.message.reply_text(
                    self._fix_text(text), reply_markup=self._mk(kb)
                )
            elif update.callback_query:
                await update.callback_query.edit_message_text(
                    self._fix_text(text), reply_markup=self._mk(kb)
                )
            return

        user = update.effective_user
        tg_id = getattr(user, "id", None)
        uname = getattr(user, "username", None)
        who = await self._api_get(
            f"/api/whoami?tg_id={tg_id or ''}&username={uname or ''}"
        ) or {}
        matches = who.get("matches") or []
        if matches:
            confirmed_match: Optional[Dict[str, Any]] = None
            for match in matches:
                try:
                    match_tid = match.get("telegram_id")
                    if match_tid is None or tg_id is None:
                        continue
                    if int(match_tid) == int(tg_id):
                        confirmed_match = match
                        break
                except Exception:
                    continue
            if not confirmed_match:
                for match in matches:
                    if match.get("is_confirmed"):
                        confirmed_match = match
                        break
            if confirmed_match:
                try:
                    context.user_data["uid"] = int(confirmed_match.get("id"))
                except Exception:
                    context.user_data["uid"] = confirmed_match.get("id")
                match_role = confirmed_match.get("role")
                context.user_data["role"] = (
                    self._normalize_role_value(match_role) or match_role
                )
                await self._show_role_menu(update, context)
                return
        if not matches:
            reg_link = os.getenv("REGISTRATION_FORM_URL")
            text = (
                "Мы не нашли вашу запись из формы. "
                "Чтобы продолжить использовать бота, зарегистрируйтесь через форму и дождитесь импорта. "
                "После регистрации нажмите /start."
            )
            if reg_link:
                text = f"{text}\n\nСсылка на форму: {reg_link}"
            if update.message:
                await update.message.reply_text(self._fix_text(text))
            else:
                await update.callback_query.edit_message_text(self._fix_text(text))
            return

        lines = ["Найдены записи. Это вы?"]
        kb: List[List[InlineKeyboardButton]] = []
        for match in matches:
            uid = match.get("id")
            full_name = match.get("full_name")
            role = match.get("role")
            lines.append(f"• {full_name} — {role} (id={uid})")
            kb.append(
                [InlineKeyboardButton(f"Да, я: {full_name}", callback_data=f"confirm_me_{uid}")]
            )
        kb.append([InlineKeyboardButton("Нет, это не я", callback_data="not_me")])
        text = "\n".join(lines)
        if update.message:
            await update.message.reply_text(
                self._fix_text(text), reply_markup=self._mk(kb)
            )
        else:
            await update.callback_query.edit_message_text(
                self._fix_text(text), reply_markup=self._mk(kb)
            )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию cmd_help."""
        await update.message.reply_text(
            self._fix_text(
                "Разделы: Студенты, Научные руководители, Темы. В профиле студента — кнопка Подобрать тему. "
                "В профиле темы (где нужен научный руководитель) — Подобрать научного руководителя."
            )
        )

    async def _show_role_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию _show_role_menu."""
        raw_role = context.user_data.get("role")
        role = self._normalize_role_value(raw_role) or raw_role
        uid = context.user_data.get("uid")
        context.user_data.pop("student_match_back", None)
        if role == "student":
            browse_rows = [
                [InlineKeyboardButton("🧑‍🏫 Научные руководители", callback_data="list_supervisors")],
                [InlineKeyboardButton("📚 Темы", callback_data="list_topics")],
            ]
            kb = [
                [InlineKeyboardButton("👤 Мой профиль", callback_data="student_me")],
                [InlineKeyboardButton("📚 Мои темы", callback_data="my_topics")],
                [InlineKeyboardButton("📜 Все роли", callback_data="list_roles")],
                [
                    InlineKeyboardButton(
                        "🧠 Подобрать роли для меня", callback_data=f"match_student_{uid}"
                    )
                ],
                [InlineKeyboardButton("📥 Входящие заявки", callback_data="messages_inbox")],
                [InlineKeyboardButton("📤 Мои заявки", callback_data="messages_outbox")],
            ]
            kb[1:1] = browse_rows
            text = "Студент: выберите действие"
            stats_line = await self._roles_stats_line()
            if stats_line:
                text = f"{text}\n\n{stats_line}"
        else:
            browse_rows = [
                [InlineKeyboardButton("👨‍🎓 Студенты", callback_data="list_students")],
                [InlineKeyboardButton("🧑‍🏫 Научные руководители", callback_data="list_supervisors")],
                [InlineKeyboardButton("📚 Темы", callback_data="list_topics")],
            ]
            kb = [
                [InlineKeyboardButton("👤 Мой профиль", callback_data="supervisor_me")],
                [InlineKeyboardButton("📚 Мои темы", callback_data="my_topics")],
                [InlineKeyboardButton("➕ Добавить тему", callback_data="add_topic")],
                [
                    InlineKeyboardButton(
                        "🧠 Подобрать темы для меня", callback_data="match_topics_for_me"
                    )
                ],
                [InlineKeyboardButton("📥 Входящие заявки", callback_data="messages_inbox")],
                [InlineKeyboardButton("📤 Мои заявки", callback_data="messages_outbox")],
            ]
            kb[1:1] = browse_rows
            text = "Научный руководитель: выберите действие"
        if update.callback_query:
            await update.callback_query.edit_message_text(
                self._fix_text(text), reply_markup=self._mk(kb)
            )
        else:
            await update.message.reply_text(
                self._fix_text(text), reply_markup=self._mk(kb)
            )

    async def _roles_stats_line(self) -> Optional[str]:
        """Выполняет функцию _roles_stats_line."""
        stats = await self._api_get("/api/roles/stats") or {}
        if not isinstance(stats, dict):
            return None
        try:
            total = int(stats.get("total", 0))
        except (TypeError, ValueError):
            return None
        return f"Всего ролей: {total}"

    async def cb_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию cb_back."""
        context.user_data.pop("messages_cache", None)
        await self.cmd_start(update, context)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Выполняет функцию on_error."""
        error = getattr(context, "error", None)
        if isinstance(error, NetworkError):
            logger.warning("Telegram network error ignored: %s", error)
            return
        logger.exception("Handler error", exc_info=error)
