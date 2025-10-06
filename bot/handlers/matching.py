"""Matching-related handlers."""
from __future__ import annotations

from typing import List

from telegram import InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from .base import BaseHandlers


class MatchingHandlers(BaseHandlers):
    async def cb_match_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await self._answer_callback(q)
        tid = int(q.data.split("_")[2])
        res = await self._api_post(
            "/match-topic", data={"topic_id": tid, "target_role": "supervisor"}
        )
        if not res or res.get("status") not in ("ok", "success"):
            await q.edit_message_text(self._fix_text("Ошибка подбора руководителя для темы"))
            return
        items = res.get("items", [])
        lines = [f"Топ‑5 руководителей для темы #{tid}:"]
        kb: List[List[InlineKeyboardButton]] = []
        matched_supervisor_ids: List[str] = []
        for item in items:
            rank = item.get("rank")
            full_name = (item.get("full_name") or "–").strip() or "–"
            reason = (item.get("reason") or "").strip()
            rank_label = f"#{rank}" if rank else "#?"
            reason_suffix = f" — {reason}" if reason else ""
            lines.append(f"{rank_label}. {full_name}{reason_suffix}")
            supervisor_id = item.get("user_id")
            if supervisor_id:
                matched_supervisor_ids.append(str(supervisor_id))
                if full_name and full_name != "–":
                    btn_title = f"👨‍🏫 {full_name[:40]}"
                else:
                    btn_title = f"👨‍🏫 Руководитель {rank_label}"
                kb.append(
                    [
                        InlineKeyboardButton(
                            self._fix_text(btn_title),
                            callback_data=f"supervisor_{supervisor_id}",
                        )
                    ]
                )
        if not kb:
            lines.append("— подходящих руководителей не найдено —")
            context.user_data.pop("supervisor_invite_context", None)
        else:
            topic_info = await self._api_get(f"/api/topics/{tid}") or {}
            context.user_data["supervisor_invite_context"] = {
                "topic_id": tid,
                "topic_title": topic_info.get("title") or f"#{tid}",
                "author_user_id": topic_info.get("author_user_id"),
                "supervisor_ids": matched_supervisor_ids,
            }

        kb.append([InlineKeyboardButton("⬅️ К теме", callback_data=f"topic_{tid}")])
        await q.edit_message_text(self._fix_text("\n".join(lines)), reply_markup=self._mk(kb))

    async def cb_invite_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await self._answer_callback(q)
        parts = (q.data or "").split("_")
        if len(parts) < 4:
            await q.message.reply_text(
                self._fix_text("Не удалось подготовить приглашение. Попробуйте снова.")
            )
            return
        try:
            topic_id = int(parts[2])
            supervisor_id = int(parts[3])
        except Exception:
            await q.message.reply_text(self._fix_text("Некорректные данные приглашения."))
            return
        sender_id = context.user_data.get("uid")
        if sender_id is None:
            await q.message.reply_text(
                self._fix_text("Сначала подтвердите профиль через /start.")
            )
            return
        topic = await self._api_get(f"/api/topics/{topic_id}")
        if not topic:
            await q.message.reply_text(
                self._fix_text("Тема не найдена. Попробуйте обновить список тем.")
            )
            return
        author_id = topic.get("author_user_id")
        is_admin = self._is_admin(update)
        if not is_admin:
            if author_id in (None, ""):
                await q.message.reply_text(
                    self._fix_text(
                        "Не удалось определить автора темы для приглашения."
                    )
                )
                return
            try:
                is_author = int(author_id) == int(sender_id)
            except Exception:
                is_author = author_id == sender_id
            if not is_author:
                await q.message.reply_text(
                    self._fix_text("Предлагать участие может только автор темы.")
                )
                return
        invite_ctx = context.user_data.get("supervisor_invite_context")
        if isinstance(invite_ctx, dict) and invite_ctx.get("topic_id") == topic_id:
            invite_ctx["topic_title"] = (
                invite_ctx.get("topic_title") or topic.get("title") or f"#{topic_id}"
            )
            invite_ctx["author_user_id"] = invite_ctx.get("author_user_id") or author_id
        supervisor = await self._api_get(f"/api/supervisors/{supervisor_id}")
        if not supervisor:
            await q.message.reply_text(
                self._fix_text("Профиль руководителя не найден.")
            )
            return
        receiver_user_id = (
            supervisor.get("id") or supervisor.get("user_id") or supervisor_id
        )
        if receiver_user_id in (None, ""):
            await q.message.reply_text(
                self._fix_text("Не удалось определить получателя приглашения.")
            )
            return
        topic_title = topic.get("title") or f"#{topic_id}"
        supervisor_name = supervisor.get("full_name") or f"#{supervisor_id}"
        default_body = (
            f'Здравствуйте! Приглашаю вас стать научным руководителем темы "{topic_title}".'
        )
        prompt = (
            f"Напишите приглашение для {supervisor_name} участвовать в теме «{topic_title}».\n"
            "Кратко опишите задачи и ожидаемый вклад. Для отмены — /start. Можно отправить «-», чтобы использовать шаблон."
        )
        payload = {
            "sender_user_id": str(sender_id),
            "receiver_user_id": str(receiver_user_id),
            "topic_id": str(topic_id),
            "role_id": None,
            "topic_title": topic_title,
            "receiver_name": supervisor_name,
            "default_body": default_body,
            "return_callback": f"supervisor_{supervisor_id}",
            "source": "supervisor_invite",
        }
        context.user_data["application_payload"] = payload
        context.user_data["awaiting"] = "submit_application_body"
        await q.message.reply_text(self._fix_text(prompt))

    async def cb_match_students_for_topic(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        q = update.callback_query
        await self._answer_callback(q)
        tid = int(q.data.rsplit("_", 1)[1])
        roles = await self._api_get(f"/api/topics/{tid}/roles") or []
        if not roles:
            await q.edit_message_text(
                self._fix_text("Для темы не добавлены роли. Добавьте их в админке.")
            )
            return
        kb: List[List[InlineKeyboardButton]] = []
        for role in roles:
            kb.append(
                [
                    InlineKeyboardButton(
                        f"🎭 {role.get('name','–')}",
                        callback_data=f"match_role_{role.get('id')}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("⬅️ К теме", callback_data=f"topic_{tid}")])
        await q.edit_message_text(
            self._fix_text("Выберите роль для подбора студентов:"),
            reply_markup=self._mk(kb),
        )

    async def cb_match_students_for_role(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        q = update.callback_query
        await self._answer_callback(q)
        rid = int(q.data.rsplit("_", 1)[1])
        res = await self._api_post("/match-role", data={"role_id": rid})
        if not res or res.get("status") not in ("ok", "success"):
            await q.edit_message_text(
                self._fix_text("Ошибка подбора студентов для роли")
            )
            return
        items = res.get("items", [])
        lines = [f"Топ‑5 студентов для роли #{rid}:"]
        kb: List[List[InlineKeyboardButton]] = []
        for item in items:
            rank = item.get("rank")
            full_name = (item.get("full_name") or "–").strip() or "–"
            reason = (item.get("reason") or "").strip()
            rank_label = f"#{rank}" if rank else "#?"
            reason_suffix = f" — {reason}" if reason else ""
            lines.append(f"{rank_label}. {full_name}{reason_suffix}")
            student_id = item.get("user_id")
            if student_id:
                if full_name and full_name != "–":
                    btn_title = f"👤 {full_name[:40]}"
                else:
                    btn_title = f"👤 Студент {rank_label}"
                kb.append(
                    [
                        InlineKeyboardButton(
                            self._fix_text(btn_title),
                            callback_data=f"student_{student_id}",
                        )
                    ]
                )
        if not kb:
            lines.append("— подходящих студентов не найдено —")
        kb.append([InlineKeyboardButton("⬅️ К роли", callback_data=f"role_{rid}")])
        await q.edit_message_text(self._fix_text("\n".join(lines)), reply_markup=self._mk(kb))

    async def cb_match_topics_for_supervisor(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        q = update.callback_query
        await self._answer_callback(q)
        try:
            uid = int(q.data.rsplit("_", 1)[1])
        except Exception:
            uid = context.user_data.get("uid")
            if not uid:
                return await self.cmd_start(update, context)
        res = await self._api_post(
            "/match-supervisor", data={"supervisor_user_id": uid}
        )
        if not res or res.get("status") not in ("ok", "success"):
            await q.edit_message_text(
                self._fix_text("Ошибка подбора тем для руководителя")
            )
            return
        items = res.get("items", [])
        lines = [f"Топ‑5 тем для руководителя #{uid}:"]
        kb: List[List[InlineKeyboardButton]] = []
        for item in items:
            title = (item.get("title") or "–").strip() or "–"
            rank = item.get("rank")
            reason = (item.get("reason") or "").strip()
            rank_label = f"#{rank}" if rank else "#?"
            reason_suffix = f" — {reason}" if reason else ""
            lines.append(f"{rank_label}. {title}{reason_suffix}")
            tid = item.get("topic_id")
            if tid:
                if title and title != "–":
                    button_title = f"📄 {title[:40]}"
                else:
                    button_title = f"📄 Тема {rank_label}"
                kb.append(
                    [
                        InlineKeyboardButton(
                            self._fix_text(button_title), callback_data=f"topic_{tid}"
                        )
                    ]
                )
        kb.append([InlineKeyboardButton("⬅️ К профилю", callback_data=f"supervisor_{uid}")])
        await q.edit_message_text(self._fix_text("\n".join(lines)), reply_markup=self._mk(kb))
