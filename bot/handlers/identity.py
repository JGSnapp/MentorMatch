"""Identity and registration handlers."""
from __future__ import annotations

from telegram import InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from .base import BaseHandlers


class IdentityHandlers(BaseHandlers):
    async def cb_confirm_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        query = update.callback_query
        await self._answer_callback(query)
        kb = [
            [InlineKeyboardButton("👨‍🎓 Я студент", callback_data="register_role_student")],
            [
                InlineKeyboardButton(
                    "🧑‍🏫 Я научный руководитель", callback_data="register_role_supervisor"
                )
            ],
        ]
        await query.edit_message_text(
            self._fix_text("Выберите роль для регистрации:"), reply_markup=self._mk(kb)
        )

    async def cb_register_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await self._answer_callback(query)
        role = query.data.split("_")[-1]
        user = update.effective_user
        full_name = getattr(user, "full_name", None) or getattr(user, "first_name", "")
        payload = {
            "role": role,
            "full_name": full_name,
            "username": getattr(user, "username", None) or "",
            "tg_id": getattr(user, "id", None),
        }
        result = await self._api_post("/api/self-register", data=payload)
        if not result or result.get("status") != "ok":
            await query.edit_message_text(
                self._fix_text("Не удалось зарегистрироваться. Попробуйте позже.")
            )
            return
        context.user_data["uid"] = int(result.get("user_id"))
        res_role = result.get("role")
        context.user_data["role"] = self._normalize_role_value(res_role) or res_role
        await self._show_role_menu(update, context)
