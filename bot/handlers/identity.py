"""Identity and registration handlers."""
from __future__ import annotations

import os

from telegram import Update
from telegram.ext import ContextTypes

from .base import BaseHandlers


class IdentityHandlers(BaseHandlers):
    def _registration_required_text(self) -> str:
        """Возвращает сообщение с инструкцией по регистрации через форму."""
        reg_link = os.getenv("REGISTRATION_FORM_URL")
        text = (
            "Чтобы продолжить использовать бота, сначала зарегистрируйтесь через форму и дождитесь импорта. "
            "После регистрации нажмите /start."
        )
        if reg_link:
            text = f"{text}\n\nСсылка на форму: {reg_link}"
        return text

    async def cb_confirm_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию cb_confirm_me."""
        query = update.callback_query
        await self._answer_callback(query)
        uid = int(query.data.split("_")[2])
        user = update.effective_user
        payload = {
            "user_id": uid,
            "tg_id": getattr(user, "id", None),
            "username": getattr(user, "username", None) or "",
        }
        await self._api_post("/api/bind-telegram", data=payload)
        role = "student"
        profile = await self._api_get(f"/api/students/{uid}")
        if not profile or profile.get("error"):
            role = "supervisor"
        context.user_data["uid"] = uid
        context.user_data["role"] = self._normalize_role_value(role) or role
        await self._show_role_menu(update, context)

    async def cb_not_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию cb_not_me."""
        query = update.callback_query
        await self._answer_callback(query)
        await query.edit_message_text(self._fix_text(self._registration_required_text()))

    async def cb_register_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет функцию cb_register_role."""
        query = update.callback_query
        await self._answer_callback(query)
        await query.edit_message_text(self._fix_text(self._registration_required_text()))
