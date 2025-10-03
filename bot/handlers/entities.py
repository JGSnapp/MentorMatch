"""Entity and profile handlers."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .base import BaseHandlers


class EntityHandlers(BaseHandlers):
    async def cb_student_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = context.user_data.get('uid')
        if not uid:
            return await self.cmd_start(update, context)
        # Reuse existing handler without mutating Telegram objects
        await self.cb_view_student(update, context)

    async def cb_supervisor_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = context.user_data.get('uid')
        if not uid:
            return await self.cmd_start(update, context)
        # Reuse existing handler without mutating Telegram objects
        await self.cb_view_supervisor(update, context)

    async def cb_my_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        uid = context.user_data.get('uid')
        if not uid:
            return await self.cmd_start(update, context)
        raw = await self._api_get(f'/api/user-topics/{uid}?limit=20') or []
        topics: List[Dict[str, Any]] = raw if isinstance(raw, list) else []
        lines = ['Мои темы:']
        kb: List[List[InlineKeyboardButton]] = []
        any_topics = False
        for t in topics:
            tid = self._parse_positive_int(t.get('id'))
            if tid is None:
                continue
            title_raw = (t.get('title') or '').strip()
            if not title_raw:
                title_raw = f'Тема #{tid}'
            note_parts: List[str] = []
            if t.get('is_author'):
                note_parts.append('моя тема')
            if t.get('is_approved_student'):
                role_names_val = t.get('approved_role_names') or []
                if isinstance(role_names_val, list):
                    role_names = [str(name) for name in role_names_val if name]
                elif role_names_val:
                    role_names = [str(role_names_val)]
                else:
                    role_names = []
                if role_names:
                    display_roles = ', '.join(role_names[:3])
                    if len(role_names) > 3:
                        display_roles += '…'
                    note_parts.append(f'мои роли: {display_roles}')
                else:
                    note_parts.append('утверждён(а) на роль')
            if t.get('is_approved_supervisor'):
                note_parts.append('я научный руководитель')
            summary_line = title_raw
            if note_parts:
                summary_line += f" ({'; '.join(note_parts)})"
            lines.append(f'• {summary_line}')
            button_label = title_raw
            if t.get('is_author'):
                button_label = f'⭐ {button_label}'
            elif t.get('is_approved_supervisor'):
                button_label = f'🧑‍🏫 {button_label}'
            elif t.get('is_approved_student'):
                button_label = f'🎓 {button_label}'
            button_label = (button_label or '')[:60]
            kb.append([InlineKeyboardButton(self._fix_text(button_label or f'Тема #{tid}'), callback_data=f'topic_{tid}')])
            if t.get('is_author') or t.get('is_approved_supervisor'):
                kb.append([InlineKeyboardButton('👨‍🎓 Подобрать студентов', callback_data=f'match_students_topic_{tid}')])
            any_topics = True
        if not any_topics:
            lines.append('— пока нет тем —')
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_match_topics_for_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        uid = context.user_data.get('uid')
        if not uid:
            return await self.cmd_start(update, context)
        # Delegate without altering callback data
        await self.cb_match_topics_for_supervisor(update, context)

    # Lists
    async def cb_list_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        data = await self._api_get('/api/students?limit=10') or []
        lines: List[str] = ['Студенты:']
        kb: List[List[InlineKeyboardButton]] = []
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton(s.get('full_name','–')[:30], callback_data=f"student_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_supervisors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['Научные руководители:']
        kb: List[List[InlineKeyboardButton]] = []
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton(s.get('full_name','–')[:30], callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        data = await self._api_get('/api/topics?limit=10') or []
        lines: List[str] = ['Темы:']
        kb: List[List[InlineKeyboardButton]] = []
        for t in data:
            title = (t.get('title') or '–')[:30]
            lines.append(f"• {t.get('title','–')} (id={t.get('id')})")
            kb.append([InlineKeyboardButton(title, callback_data=f"topic_{t.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Profiles
    async def cb_view_student(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        # Parse student id from callback data, or fallback to current user
        try:
            sid = int(q.data.split('_')[1])
        except Exception:
            sid = context.user_data.get('uid')
            if not sid:
                return await self.cmd_start(update, context)
        s = await self._api_get(f'/api/students/{sid}')
        if not s:
            await q.edit_message_text(self._fix_text('Не удалось загрузить профиль студента'))
            return
        viewer_id = context.user_data.get('uid')
        can_edit = self._is_admin(update)
        if not can_edit and viewer_id is not None and sid is not None:
            try:
                can_edit = int(viewer_id) == int(sid)
            except Exception:
                can_edit = viewer_id == sid
        # Header
        lines = [
            f"Студент: {s.get('full_name','–')}",
            f"Username: {s.get('username') or '–'}",
            f"Email: {s.get('email') or '–'}",
            f"Направление: {s.get('program') or '–'}",
            f"Навыки: {s.get('skills') or '–'}",
            f"Интересы: {s.get('interests') or '–'}",
            f"CV: {(s.get('cv') or '–')[:200]}",
            f"ID: {s.get('id')}",
        ]
        # Existing recommendations from DB
        rec = await self._api_get(f'/api/user-candidates/{sid}?limit=5') or []
        if rec:
            lines.append('')
            # Back-compat: endpoint возвращает роли для студента
            if rec and 'role_name' in (rec[0] or {}):
                lines.append('Рекомендованные роли:')
                for it in rec:
                    lines.append(f"• #{it.get('rank')}. {it.get('role_name','–')} — {it.get('topic_title','–')} (балл={it.get('score')})")
            else:
                lines.append('Рекомендованные темы:')
                for it in rec:
                    lines.append(f"• #{it.get('rank')}. {it.get('title','–')} (балл={it.get('score')})")
        text = '\n'.join(lines)
        kb: List[List[InlineKeyboardButton]] = []
        if can_edit:
            kb.append([InlineKeyboardButton('✏️ Редактировать профиль', callback_data=f'edit_student_{sid}')])
        kb.append([InlineKeyboardButton('🧠 Подобрать роль', callback_data=f'match_student_{sid}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_edit_student_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            sid = int(q.data.split('_')[2])
        except Exception:
            await self._answer_callback(q, text=self._fix_text('Некорректный идентификатор студента.'), show_alert=True)
            return
        viewer_id = context.user_data.get('uid')
        if not self._is_admin(update):
            if viewer_id is None:
                await self._answer_callback(q, text=self._fix_text('Вы не авторизованы для редактирования.'), show_alert=True)
                return
            try:
                if int(viewer_id) != int(sid):
                    await self._answer_callback(q, text=self._fix_text('Можно редактировать только свой профиль.'), show_alert=True)
                    return
            except Exception:
                if viewer_id != sid:
                    await self._answer_callback(q, text=self._fix_text('Можно редактировать только свой профиль.'), show_alert=True)
                    return
        student = await self._api_get(f'/api/students/{sid}')
        if not student:
            await q.edit_message_text(self._fix_text('Профиль студента не найден.'))
            return
        context.user_data['awaiting'] = 'edit_student_program'
        context.user_data['edit_student_payload'] = {'user_id': sid}
        context.user_data['edit_student_original'] = student
        prompt = (
            f"Редактирование профиля студента.\n"
            f"Текущая программа: {student.get('program') or '–'}.\n"
            "Введите новое значение. Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
        )
        await q.message.reply_text(self._fix_text(prompt))

    async def cb_view_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        # Parse supervisor id from callback data, or fallback to current user
        try:
            uid = int(q.data.split('_')[1])
        except Exception:
            uid = context.user_data.get('uid')
            if not uid:
                return await self.cmd_start(update, context)
        s = await self._api_get(f'/api/supervisors/{uid}')
        if not s:
            await q.edit_message_text(self._fix_text('Не удалось загрузить профиль научного руководителя'))
            return
        viewer_id = context.user_data.get('uid')
        can_edit = self._is_admin(update)
        if not can_edit and viewer_id is not None and uid is not None:
            try:
                can_edit = int(viewer_id) == int(uid)
            except Exception:
                can_edit = viewer_id == uid
        lines = [
            f"Научный руководитель: {s.get('full_name','–')}",
            f"Username: {s.get('username') or '–'}",
            f"Email: {s.get('email') or '–'}",
            f"Должность: {s.get('position') or '–'}",
            f"Степень: {s.get('degree') or '–'}",
            f"Вместимость: {s.get('capacity') or '–'}",
            f"Интересы: {s.get('interests') or '–'}",
            f"ID: {s.get('id')}",
        ]
        rec = await self._api_get(f'/api/user-candidates/{uid}?limit=5') or []
        if rec:
            lines.append('')
            lines.append('Подходящие темы:')
            for it in rec:
                lines.append(f"• #{it.get('rank')}. {it.get('title','–')} (балл={it.get('score')})")
        text = '\n'.join(lines)
        kb: List[List[InlineKeyboardButton]] = []
        if can_edit:
            kb.append([InlineKeyboardButton('✏️ Редактировать профиль', callback_data=f'edit_supervisor_{uid}')])
        kb.append([InlineKeyboardButton('🧠 Подобрать тему', callback_data=f'match_topics_for_supervisor_{uid}')])
        invite_ctx = context.user_data.get('supervisor_invite_context') or {}
        topic_id_for_invite = invite_ctx.get('topic_id')
        supervisor_ids = {str(x) for x in (invite_ctx.get('supervisor_ids') or [])}
        can_invite = False
        if topic_id_for_invite and str(uid) in supervisor_ids:
            if self._is_admin(update):
                can_invite = True
            else:
                viewer_id = context.user_data.get('uid')
                author_id = invite_ctx.get('author_user_id')
                if viewer_id is not None and author_id not in (None, ''):
                    try:
                        can_invite = int(author_id) == int(viewer_id)
                    except Exception:
                        can_invite = author_id == viewer_id
                elif viewer_id is not None:
                    topic_info = await self._api_get(f'/api/topics/{topic_id_for_invite}')
                    if topic_info:
                        invite_ctx['author_user_id'] = topic_info.get('author_user_id')
                        invite_ctx['topic_title'] = invite_ctx.get('topic_title') or topic_info.get('title') or f'#{topic_id_for_invite}'
                        refreshed_author = invite_ctx.get('author_user_id')
                        if refreshed_author not in (None, ''):
                            try:
                                can_invite = int(refreshed_author) == int(viewer_id)
                            except Exception:
                                can_invite = refreshed_author == viewer_id
        if can_invite:
            kb.append([
                InlineKeyboardButton(
                    '🤝 Предложить участие',
                    callback_data=f'invite_supervisor_{topic_id_for_invite}_{uid}',
                )
            ])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_edit_supervisor_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            uid = int(q.data.split('_')[2])
        except Exception:
            await self._answer_callback(q, text=self._fix_text('Некорректный идентификатор профиля.'), show_alert=True)
            return
        viewer_id = context.user_data.get('uid')
        if not self._is_admin(update):
            if viewer_id is None:
                await self._answer_callback(q, text=self._fix_text('Вы не авторизованы для редактирования.'), show_alert=True)
                return
            try:
                if int(viewer_id) != int(uid):
                    await self._answer_callback(q, text=self._fix_text('Можно редактировать только свой профиль.'), show_alert=True)
                    return
            except Exception:
                if viewer_id != uid:
                    await self._answer_callback(q, text=self._fix_text('Можно редактировать только свой профиль.'), show_alert=True)
                    return
        supervisor = await self._api_get(f'/api/supervisors/{uid}')
        if not supervisor:
            await q.edit_message_text(self._fix_text('Профиль руководителя не найден.'))
            return
        context.user_data['awaiting'] = 'edit_supervisor_position'
        context.user_data['edit_supervisor_payload'] = {'user_id': uid}
        context.user_data['edit_supervisor_original'] = supervisor
        prompt = (
            f"Редактирование профиля руководителя.\n"
            f"Текущая должность: {supervisor.get('position') or '–'}.\n"
            "Введите новое значение. Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
        )
        await q.message.reply_text(self._fix_text(prompt))

    async def cb_view_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        tid = int(q.data.split('_')[1])
        t = await self._api_get(f'/api/topics/{tid}')
        if not t:
            await q.edit_message_text(self._fix_text('Не удалось загрузить тему'))
            return
        author_id = t.get('author_user_id')
        uid = context.user_data.get('uid')
        viewer_role_name = self._normalize_role_value(context.user_data.get('role')) or ''
        can_add_role = False
        if self._is_admin(update):
            can_add_role = True
        elif uid is not None and author_id is not None:
            try:
                can_add_role = int(author_id) == int(uid)
            except Exception:
                can_add_role = author_id == uid
        role = t.get('seeking_role')
        text = (
            f"Тема: {t.get('title','–')}\n"
            f"Автор: {t.get('author','–')}\n"
            f"Кого ищем: {role}\n"
            f"Описание: {(t.get('description') or '–')[:500]}\n"
            f"Ожидаемые результаты: {(t.get('expected_outcomes') or '–')[:400]}\n"
            f"Требуемые навыки: {t.get('required_skills') or '–'}\n"
            f"ID: {t.get('id')}\n"
        )
        # Roles for this topic
        roles = await self._api_get(f'/api/topics/{tid}/roles') or []
        lines2: List[str] = [text, '', 'Роли:']
        kb: List[List[InlineKeyboardButton]] = []
        for r in roles:
            name = (r.get('name') or '–')[:40]
            lines2.append(f"• {name} (role_id={r.get('id')})")
            kb.append([InlineKeyboardButton(f"🎭 {name}", callback_data=f"role_{r.get('id')}")])
        if not roles:
            lines2.append('— нет ролей —')
        if can_add_role:
            kb.insert(0, [InlineKeyboardButton('✏️ Редактировать тему', callback_data=f'edit_topic_{tid}')])
            kb.append([InlineKeyboardButton('➕ Добавить роль', callback_data=f'add_role_{tid}')])
        can_apply_topic = False
        if uid is not None and author_id is not None:
            try:
                same_author = int(author_id) == int(uid)
            except Exception:
                same_author = author_id == uid
        else:
            same_author = False
        target_role = (role or 'student').lower()
        if uid is not None and not same_author and target_role in {'student', 'supervisor'}:
            viewer_matches = target_role == viewer_role_name
            can_apply_topic = viewer_matches and bool(author_id)
        if can_apply_topic:
            apply_text = '📨 Подать заявку на тему' if target_role == 'student' else '📨 Откликнуться на тему'
            kb.append([InlineKeyboardButton(apply_text, callback_data=f'apply_topic_{tid}')])
        kb.append([InlineKeyboardButton('🧑‍🏫 Подобрать научного руководителя', callback_data=f'match_supervisor_{tid}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines2)), reply_markup=self._mk(kb))

    async def cb_view_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            rid = int(q.data.split('_')[1])
        except Exception:
            await q.edit_message_text(self._fix_text('Некорректный идентификатор роли.'))
            return
        role = await self._api_get(f'/api/roles/{rid}')
        if not role:
            await q.edit_message_text(self._fix_text('Роль не найдена.'))
            return

        topic_id = role.get('topic_id')
        topic_title = role.get('topic_title') or (f'Тема #{topic_id}' if topic_id else '—')
        role_name = (role.get('name') or '–').strip() or '–'
        author_name = (role.get('author') or '–').strip() or '–'
        description = (role.get('description') or '–').strip()
        required_skills = (role.get('required_skills') or '–').strip() or '–'
        capacity = role.get('capacity')
        capacity_display = '–'
        if capacity not in (None, ''):
            try:
                capacity_display = str(int(capacity))
            except Exception:
                capacity_display = str(capacity)

        lines: List[str] = [
            f'Роль: {role_name}',
            f'Тема: {topic_title}',
            f'Автор темы: {author_name}',
            f'Описание: {(description or "–")[:500] or "–"}',
            f'Навыки: {required_skills}',
            f'Вместимость: {capacity_display}',
            f'ID роли: {rid}',
        ]

        candidates_resp = await self._api_get(f'/api/role-candidates/{rid}?limit=5')
        candidates: List[Dict[str, Any]] = []
        if isinstance(candidates_resp, list):
            candidates = [c for c in candidates_resp if isinstance(c, dict)]
        if candidates:
            lines.append('')
            lines.append('Последние рекомендации:')

        kb: List[List[InlineKeyboardButton]] = []
        candidate_buttons: List[List[InlineKeyboardButton]] = []
        for item in candidates:
            rank = item.get('rank')
            rank_label = f'#{rank}' if rank else '#?'
            full_name = (item.get('full_name') or '–').strip() or '–'
            score = item.get('score')
            score_suffix = ''
            if score not in (None, ''):
                score_suffix = f' (балл={score})'
            lines.append(f'{rank_label}. {full_name}{score_suffix}')
            sid = self._parse_positive_int(item.get('user_id'))
            if sid is not None:
                candidate_buttons.append([
                    InlineKeyboardButton(
                        self._fix_text(f'👤 {full_name[:40]}'), callback_data=f'student_{sid}'
                    )
                ])

        uid = context.user_data.get('uid')
        author_id = role.get('author_user_id')
        viewer_role = self._normalize_role_value(context.user_data.get('role'))
        same_author = self._ids_equal(uid, author_id)
        is_admin = self._is_admin(update)

        if is_admin or same_author:
            kb.append([InlineKeyboardButton('✏️ Редактировать роль', callback_data=f'edit_role_{rid}')])
            kb.append([InlineKeyboardButton('👥 Подобрать студентов', callback_data=f'match_role_{rid}')])

        can_apply = (
            viewer_role == 'student'
            and not same_author
            and uid is not None
            and author_id is not None
        )
        if can_apply:
            kb.append([InlineKeyboardButton('📨 Подать заявку на роль', callback_data=f'apply_role_{rid}')])

        kb.extend(candidate_buttons)

        if topic_id:
            kb.append([InlineKeyboardButton('📚 К теме', callback_data=f'topic_{topic_id}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])

        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_apply_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            rid = int(q.data.rsplit('_', 1)[1])
        except Exception:
            await q.edit_message_text(self._fix_text('Некорректный идентификатор роли.'))
            return

        role = await self._api_get(f'/api/roles/{rid}')
        if not role:
            await q.edit_message_text(self._fix_text('Роль не найдена.'))
            return

        uid = context.user_data.get('uid')
        if uid is None:
            await q.message.reply_text(self._fix_text('Сначала подтвердите профиль через /start.'))
            return

        viewer_role = self._normalize_role_value(context.user_data.get('role'))
        if viewer_role != 'student':
            await q.message.reply_text(self._fix_text('Заявку на роль может отправить только студент.'))
            return

        author_id = role.get('author_user_id')
        if not author_id:
            await q.message.reply_text(self._fix_text('Не удалось определить получателя заявки.'))
            return

        if self._ids_equal(uid, author_id):
            await q.message.reply_text(self._fix_text('Нельзя откликаться на собственную роль.'))
            return

        topic_id = role.get('topic_id')
        topic_title = role.get('topic_title') or (f'Тема #{topic_id}' if topic_id else 'тема')
        role_name = (role.get('name') or '').strip() or f'Роль #{rid}'

        default_body = (
            f'Здравствуйте! Хотел(а) бы присоединиться к роли "{role_name}" '
            f'по теме "{topic_title}".'
        )
        prompt = (
            f'Напишите сообщение для автора темы «{topic_title}» по роли «{role_name}».\n'
            'Расскажите о себе и мотивации. Для отмены — /start. Можно отправить «-», чтобы использовать шаблон.'
        )

        payload: Dict[str, Any] = {
            'sender_user_id': str(uid),
            'receiver_user_id': str(author_id),
            'role_id': str(rid),
            'topic_title': topic_title,
            'role_name': role_name,
            'receiver_name': role.get('author'),
            'default_body': default_body,
            'return_callback': f'role_{rid}',
            'source': 'role',
        }
        if topic_id is not None:
            payload['topic_id'] = str(topic_id)

        context.user_data['application_payload'] = payload
        context.user_data['awaiting'] = 'submit_application_body'
        await q.message.reply_text(self._fix_text(prompt))

    async def cb_apply_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            tid = int(q.data.rsplit('_', 1)[1])
        except Exception:
            await q.edit_message_text(self._fix_text('Некорректный идентификатор темы.'))
            return
        uid = context.user_data.get('uid')
        viewer_role = self._normalize_role_value(context.user_data.get('role'))
        if not uid:
            await q.edit_message_text(self._fix_text('Сначала подтвердите профиль через /start.'))
            return
        topic = await self._api_get(f'/api/topics/{tid}')
        if not topic:
            await q.edit_message_text(self._fix_text('Тема не найдена.'))
            return
        author_id = topic.get('author_user_id')
        if not author_id:
            await q.edit_message_text(self._fix_text('Не удалось определить получателя заявки.'))
            return
        try:
            same_author = int(author_id) == int(uid)
        except Exception:
            same_author = author_id == uid
        if same_author:
            await q.edit_message_text(self._fix_text('Нельзя откликаться на собственную тему.'))
            return
        target_role = (topic.get('seeking_role') or 'student').lower()
        if target_role not in {'student', 'supervisor'} or viewer_role != target_role:
            await q.edit_message_text(self._fix_text('Эта тема ищет другую роль.'))
            return
        title = topic.get('title') or f'#{tid}'
        if target_role == 'student':
            roles = await self._api_get(f'/api/topics/{tid}/roles') or []
            role_choices: List[tuple[int, str]] = []
            for r in roles:
                rid = self._parse_positive_int(r.get('id'))
                if rid is None:
                    continue
                name = (r.get('name') or '').strip()
                label = name or f'Роль #{rid}'
                role_choices.append((rid, label))
            if not role_choices:
                kb = [[InlineKeyboardButton('⬅️ К теме', callback_data=f'topic_{tid}')]]
                kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
                await q.message.reply_text(
                    self._fix_text(
                        'В этой теме пока нет ролей. Попросите автора добавить роли и попробуйте ещё раз.'
                    ),
                    reply_markup=self._mk(kb),
                )
                return
            lines = [f'Чтобы подать заявку на тему «{title}», выберите конкретную роль:']
            if role_choices:
                lines.append('')
                lines.append('Доступные роли:')
                for _, label in role_choices:
                    lines.append(f'• {label}')
            kb = [
                [InlineKeyboardButton(f'📨 {label[:40]}', callback_data=f'apply_role_{rid}')]
                for rid, label in role_choices
            ]
            kb.append([InlineKeyboardButton('⬅️ К теме', callback_data=f'topic_{tid}')])
            kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
            await q.message.reply_text(
                self._fix_text('\n'.join(lines)),
                reply_markup=self._mk(kb),
            )
            return
        if target_role == 'supervisor':
            default_body = f'Здравствуйте! Готов(а) стать научным руководителем по теме "{title}".'
            prompt = (
                f'Напишите сообщение для автора темы «{title}».\n'
                'Расскажите о своём опыте руководства. Для отмены — /start. Можно отправить «-», чтобы использовать шаблон.'
            )
        else:
            default_body = f'Здравствуйте! Хотел(а) бы присоединиться к теме "{title}".'
            prompt = (
                f'Напишите сообщение для автора темы «{title}».\n'
                'Расскажите о себе и мотивации. Для отмены — /start. Можно отправить «-», чтобы использовать шаблон.'
            )
        payload = {
            'sender_user_id': str(uid),
            'receiver_user_id': str(author_id),
            'topic_id': str(tid),
            'role_id': None,
            'topic_title': title,
            'target_role': target_role,
            'default_body': default_body,
            'return_callback': f'topic_{tid}',
            'source': 'topic',
        }
        context.user_data['application_payload'] = payload
        context.user_data['awaiting'] = 'submit_application_body'
        await q.message.reply_text(self._fix_text(prompt))

    async def cb_edit_topic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            tid = int(q.data.split('_')[2])
        except Exception:
            await self._answer_callback(q, text=self._fix_text('Некорректный идентификатор темы.'), show_alert=True)
            return
        topic = await self._api_get(f'/api/topics/{tid}')
        if not topic:
            await q.edit_message_text(self._fix_text('Тема не найдена.'))
            return
        author_id = topic.get('author_user_id')
        viewer_id = context.user_data.get('uid')
        is_admin = self._is_admin(update)
        if not is_admin:
            if viewer_id is None or author_id is None:
                await self._answer_callback(q, text=self._fix_text('У вас нет прав редактировать эту тему.'), show_alert=True)
                return
            try:
                if int(viewer_id) != int(author_id):
                    await self._answer_callback(q, text=self._fix_text('У вас нет прав редактировать эту тему.'), show_alert=True)
                    return
            except Exception:
                if viewer_id != author_id:
                    await self._answer_callback(q, text=self._fix_text('У вас нет прав редактировать эту тему.'), show_alert=True)
                    return
        context.user_data['awaiting'] = 'edit_topic_title'
        payload: Dict[str, Any] = {'topic_id': tid}
        if viewer_id is not None and not is_admin:
            payload['editor_user_id'] = str(viewer_id)
        context.user_data['edit_topic_payload'] = payload
        context.user_data['edit_topic_original'] = topic
        prompt = (
            f"Редактирование темы.\n"
            f"Текущее название: {topic.get('title') or '–'}.\n"
            "Введите новое название. Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
        )
        await q.message.reply_text(self._fix_text(prompt))

    async def cb_edit_role_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            rid = int(q.data.split('_')[2])
        except Exception:
            await self._answer_callback(q, text=self._fix_text('Некорректный идентификатор роли.'), show_alert=True)
            return
        role = await self._api_get(f'/api/roles/{rid}')
        if not role:
            await q.edit_message_text(self._fix_text('Роль не найдена.'))
            return
        author_id = role.get('author_user_id')
        viewer_id = context.user_data.get('uid')
        is_admin = self._is_admin(update)
        if not is_admin:
            if viewer_id is None or author_id is None or not self._ids_equal(viewer_id, author_id):
                await self._answer_callback(q, text=self._fix_text('У вас нет прав редактировать эту роль.'), show_alert=True)
                return
        context.user_data['awaiting'] = 'edit_role_name'
        payload: Dict[str, Any] = {'role_id': rid}
        if viewer_id is not None and not is_admin:
            payload['editor_user_id'] = str(viewer_id)
        context.user_data['edit_role_payload'] = payload
        context.user_data['edit_role_original'] = role
        prompt = (
            f"Редактирование роли.\n"
            f"Текущее название: {role.get('name') or '–'}.\n"
            "Введите новое название. Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
        )
        await q.message.reply_text(self._fix_text(prompt))

    # Matching
    async def cb_match_student(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        sid = int(q.data.split('_')[2])
        viewer_id = context.user_data.get('uid')
        same_user = self._ids_equal(viewer_id, sid)
        is_admin = self._is_admin(update)
        res = await self._api_post('/match-student', data={'student_user_id': sid})
        if not res or res.get('status') != 'ok':
            await q.edit_message_text(self._fix_text('Ошибка подбора ролей для студента'))
            return
        items = res.get('items', [])
        lines = [f'Подходящие роли для студента #{sid}:']
        kb: List[List[InlineKeyboardButton]] = []
        context.user_data['student_match_back'] = f'match_student_{sid}'
        for it in items:
            rank = it.get('rank')
            role_name = (it.get('role_name') or '–').strip() or '–'
            topic_title = (it.get('topic_title') or '–').strip() or '–'
            reason_raw = (it.get('reason') or '').strip()
            reason = ' '.join(reason_raw.split())
            rank_label = f"#{rank}" if rank else '#?'
            lines.append(f"{rank_label}. {role_name} — {topic_title}")
            if reason:
                lines.append(f"   Почему подходит: {reason}")
            rid = it.get('role_id')
            if rid:
                btn_title_source = role_name if role_name and role_name != '–' else ''
                if not btn_title_source and topic_title and topic_title != '–':
                    btn_title_source = topic_title
                if not btn_title_source:
                    btn_title_source = f'Роль {rank_label}'
                btn_title = btn_title_source[:40]
                kb.append([InlineKeyboardButton(self._fix_text(btn_title), callback_data=f'role_{rid}')])
        if not kb:
            lines.append('— подходящих ролей не найдено —')
        if is_admin or not same_user:
            kb.append([InlineKeyboardButton('К профилю студента', callback_data=f'student_{sid}')])
        kb.append([InlineKeyboardButton('Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Messages (applications)
    async def cb_messages_inbox(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        uid = context.user_data.get('uid')
        if uid is None:
            kb = [[InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')]]
            await q.edit_message_text(
                self._fix_text('Не удалось определить пользователя. Запустите /start.'),
                reply_markup=self._mk(kb),
            )
            return
        data_tag = (q.data or 'messages_inbox')
        status_filter = 'pending'
        if data_tag.endswith('_all'):
            status_filter = None
        elif data_tag.endswith('_pending'):
            status_filter = 'pending'
        url = f'/api/messages/inbox?user_id={uid}'
        if status_filter:
            url += f'&status={status_filter}'
        res = await self._api_get(url)
        messages = res if isinstance(res, list) else []
        list_callback = 'messages_inbox' if status_filter == 'pending' else 'messages_inbox_all'
        self._store_messages_cache(context, messages, source='inbox', list_callback=list_callback)
        status_labels = {
            'pending': 'ожидает решения',
            'accepted': 'принята',
            'rejected': 'отклонена',
            'canceled': 'отменена',
        }
        header = 'Входящие заявки'
        header += ' (ожидают решения)' if status_filter == 'pending' else ' (все статусы)'
        lines: List[str] = [header]
        display_items = messages[:10]
        if not display_items:
            lines.append('— пока нет заявок —')
        else:
            for msg in display_items:
                msg_id = msg.get('id')
                sender = msg.get('sender_name') or f"#{msg.get('sender_user_id')}" or '—'
                topic = msg.get('topic_title') or f"Тема #{msg.get('topic_id')}" or '—'
                role_name = msg.get('role_name')
                status_label = status_labels.get((msg.get('status') or '').lower(), msg.get('status') or '')
                line = f"• #{msg_id} от {sender} — {topic}"
                if role_name:
                    line += f" — роль: {role_name}"
                if status_label:
                    line += f" — {status_label}"
                lines.append(line)
        if len(messages) > len(display_items):
            lines.append(f'Показаны {len(display_items)} из {len(messages)} последних заявок.')
        kb: List[List[InlineKeyboardButton]] = []
        if status_filter == 'pending':
            kb.append([InlineKeyboardButton('📜 Все заявки', callback_data='messages_inbox_all')])
        else:
            kb.append([InlineKeyboardButton('⏳ Ожидающие', callback_data='messages_inbox')])
        for msg in display_items:
            msg_id = msg.get('id')
            if msg_id is None:
                continue
            sender = msg.get('sender_name') or f"#{msg.get('sender_user_id')}" or '—'
            label = f"#{msg_id}: {sender}"[:60]
            kb.append([InlineKeyboardButton(label, callback_data=f'message_{msg_id}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_messages_outbox(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        uid = context.user_data.get('uid')
        if uid is None:
            kb = [[InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')]]
            await q.edit_message_text(
                self._fix_text('Не удалось определить пользователя. Запустите /start.'),
                reply_markup=self._mk(kb),
            )
            return
        data_tag = (q.data or 'messages_outbox')
        status_filter = 'pending'
        if data_tag.endswith('_all'):
            status_filter = None
        elif data_tag.endswith('_pending'):
            status_filter = 'pending'
        url = f'/api/messages/outbox?user_id={uid}'
        if status_filter:
            url += f'&status={status_filter}'
        res = await self._api_get(url)
        messages = res if isinstance(res, list) else []
        list_callback = 'messages_outbox' if status_filter == 'pending' else 'messages_outbox_all'
        self._store_messages_cache(context, messages, source='outbox', list_callback=list_callback)
        status_labels = {
            'pending': 'ожидает решения',
            'accepted': 'принята',
            'rejected': 'отклонена',
            'canceled': 'отменена',
        }
        header = 'Отправленные заявки'
        header += ' (ожидают решения)' if status_filter == 'pending' else ' (все статусы)'
        lines: List[str] = [header]
        display_items = messages[:10]
        if not display_items:
            lines.append('— пока нет заявок —')
        else:
            for msg in display_items:
                msg_id = msg.get('id')
                receiver = msg.get('receiver_name') or f"#{msg.get('receiver_user_id')}" or '—'
                topic = msg.get('topic_title') or f"Тема #{msg.get('topic_id')}" or '—'
                role_name = msg.get('role_name')
                status_label = status_labels.get((msg.get('status') or '').lower(), msg.get('status') or '')
                line = f"• #{msg_id} → {receiver} — {topic}"
                if role_name:
                    line += f" — роль: {role_name}"
                if status_label:
                    line += f" — {status_label}"
                lines.append(line)
        if len(messages) > len(display_items):
            lines.append(f'Показаны {len(display_items)} из {len(messages)} последних заявок.')
        kb: List[List[InlineKeyboardButton]] = []
        if status_filter == 'pending':
            kb.append([InlineKeyboardButton('📜 Все заявки', callback_data='messages_outbox_all')])
        else:
            kb.append([InlineKeyboardButton('⏳ Ожидающие', callback_data='messages_outbox')])
        for msg in display_items:
            msg_id = msg.get('id')
            if msg_id is None:
                continue
            receiver = msg.get('receiver_name') or f"#{msg.get('receiver_user_id')}" or '—'
            label = f"#{msg_id}: {receiver}"[:60]
            kb.append([InlineKeyboardButton(label, callback_data=f'message_{msg_id}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_message_view(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, message_id: Optional[int] = None, refresh: bool = False, notice: Optional[str] = None):
        q = update.callback_query; await self._answer_callback(q)
        try:
            mid = message_id if message_id is not None else int(q.data.rsplit('_', 1)[1])
        except Exception:
            await q.edit_message_text(self._fix_text('Некорректный идентификатор заявки.'))
            return
        uid = context.user_data.get('uid')
        if uid is None:
            kb = [[InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')]]
            await q.edit_message_text(
                self._fix_text('Не удалось определить пользователя. Запустите /start.'),
                reply_markup=self._mk(kb),
            )
            return
        msg = await self._get_message_details(context, uid, mid, refresh=refresh)
        if not msg:
            await q.edit_message_text(self._fix_text('Заявка не найдена. Обновите список.'))
            return
        text, kb = self._build_message_view(msg, uid, notice=notice)
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_message_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        data = (q.data or '').split('_')
        if len(data) < 3:
            await self._answer_callback(q)
            await q.edit_message_text(self._fix_text('Некорректный запрос.'))
            return
        action = data[1]
        try:
            mid = int(data[2])
        except Exception:
            await self._answer_callback(q, text=self._fix_text('Некорректный идентификатор заявки.'), show_alert=True)
            return
        uid = context.user_data.get('uid')
        if uid is None:
            await self._answer_callback(q, text=self._fix_text('Не удалось определить пользователя. Запустите /start.'), show_alert=True)
            return
        payload = {
            'message_id': str(mid),
            'responder_user_id': str(uid),
            'action': action,
        }
        res = await self._api_post('/api/messages/respond', data=payload)
        if not res or res.get('status') != 'ok':
            msg_text = (res or {}).get('message') or 'Не удалось обновить заявку.'
            await self._answer_callback(q, text=self._fix_text(msg_text), show_alert=True)
            return
        await self._answer_callback(q)
        notice_map = {
            'accept': '✅ Заявка принята.',
            'reject': '❌ Заявка отклонена.',
            'cancel': '🚫 Заявка отменена.',
        }
        msg = await self._get_message_details(context, uid, mid, refresh=True)
        if msg:
            text, kb = self._build_message_view(msg, uid, notice=notice_map.get(action))
            await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
        else:
            fallback = notice_map.get(action) or 'Заявка обновлена.'
            kb = [[InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')]]
            await q.edit_message_text(self._fix_text(fallback), reply_markup=self._mk(kb))

    def _store_messages_cache(self, context: ContextTypes.DEFAULT_TYPE, messages: List[Dict[str, Any]], *, source: str, list_callback: str) -> None:
        cache = context.user_data.setdefault('messages_cache', {})
        for msg in messages or []:
            mid = msg.get('id')
            if mid is None:
                continue
            entry = dict(msg)
            entry['__source'] = source
            entry['__list_callback'] = list_callback
            cache[str(mid)] = entry

    async def _get_message_details(self, context: ContextTypes.DEFAULT_TYPE, user_id: Any, message_id: int, *, refresh: bool = False) -> Optional[Dict[str, Any]]:
        cache = context.user_data.setdefault('messages_cache', {})
        key = str(message_id)
        if not refresh and key in cache:
            return cache.get(key)
        if user_id is None:
            return cache.get(key)
        uid_str = str(user_id)
        for endpoint in ('inbox', 'outbox'):
            url = f'/api/messages/{endpoint}?user_id={uid_str}'
            res = await self._api_get(url)
            rows = res if isinstance(res, list) else []
            list_callback = f'messages_{endpoint}_all'
            self._store_messages_cache(context, rows, source=endpoint, list_callback=list_callback)
            cached = cache.get(key)
            if cached:
                return cached
        return cache.get(key)

    def _build_message_view(
        self,
        message: Dict[str, Any],
        viewer_id: Any,
        notice: Optional[str] = None,
    ) -> tuple[str, List[List[InlineKeyboardButton]]]:
        status_labels = {
            'pending': 'ожидает решения',
            'accepted': 'принята',
            'rejected': 'отклонена',
            'canceled': 'отменена',
        }
        lines: List[str] = []
        if notice:
            lines.append(notice)
            lines.append('')
        msg_id = message.get('id')
        status_val = (message.get('status') or '').lower()
        status_label = status_labels.get(status_val, message.get('status') or '')
        lines.append(f'Заявка #{msg_id}')
        if status_label:
            lines.append(f'Статус: {status_label}')
        sender_name = message.get('sender_name') or message.get('sender_full_name') or ''
        receiver_name = message.get('receiver_name') or message.get('receiver_full_name') or ''
        sender_id = message.get('sender_user_id')
        receiver_id = message.get('receiver_user_id')
        sender_line = sender_name or f'#{sender_id}'
        receiver_line = receiver_name or f'#{receiver_id}'
        lines.append(f'От: {sender_line} (id={sender_id})')
        lines.append(f'Кому: {receiver_line} (id={receiver_id})')
        topic_title = message.get('topic_title') or f"Тема #{message.get('topic_id')}"
        lines.append(f'Тема: {topic_title}')
        role_name = message.get('role_name')
        if role_name:
            lines.append(f'Роль: {role_name}')
        body = message.get('body') or '—'
        lines.append('')
        lines.append('Сообщение:')
        lines.append(body)
        answer = message.get('answer')
        if answer:
            lines.append('')
            lines.append('Ответ:')
            lines.append(answer)
        kb: List[List[InlineKeyboardButton]] = []
        def _same_user(a: Any, b: Any) -> bool:
            try:
                return int(a) == int(b)
            except Exception:
                return a == b
        if status_val == 'pending':
            if _same_user(receiver_id, viewer_id):
                kb.append([
                    InlineKeyboardButton('✅ Принять', callback_data=f'message_accept_{msg_id}'),
                    InlineKeyboardButton('❌ Отклонить', callback_data=f'message_reject_{msg_id}')
                ])
            elif _same_user(sender_id, viewer_id):
                kb.append([InlineKeyboardButton('🚫 Отменить', callback_data=f'message_cancel_{msg_id}')])
        source = message.get('__source') or ('inbox' if _same_user(receiver_id, viewer_id) else 'outbox')
        back_cb = message.get('__list_callback')
        if not back_cb:
            back_cb = 'messages_inbox' if source == 'inbox' else 'messages_outbox'
        back_label = '⬅️ К входящим' if source == 'inbox' else '⬅️ К моим заявкам'
        kb.append([InlineKeyboardButton(back_label, callback_data=back_cb)])
        return '\n'.join(lines), kb

    # Import students from Google Sheets
    async def cb_import_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        cfg = await self._api_get('/api/sheets-config')
        if not cfg or cfg.get('status') != 'configured':
            text = 'Google Sheets не настроен. Укажите SPREADSHEET_ID и SERVICE_ACCOUNT_FILE на сервере.'
            kb = [[InlineKeyboardButton('👨‍🎓 К студентам', callback_data='list_students')]]
            await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
            return
        sid = cfg.get('spreadsheet_id')
        # Provide immediate feedback so UI doesn't look frozen
        try:
            await q.edit_message_text(self._fix_text('Import started... This may take up to 2-3 minutes.'))
        except Exception:
            pass
        # Allow longer timeout for imports (downloads + DB work)
        res = await self._api_post('/api/import-sheet', data={'spreadsheet_id': sid}, timeout=300)
        if not res or res.get('status') != 'success':
            msg = (res or {}).get('message') or 'Ошибка импорта'
            text = f'❌ Импорт не выполнен: {msg}'
        else:
            stats = res.get('stats', {})
            text = (
                '✅ Импорт выполнен.\n'
                f"Пользователи: +{stats.get('inserted_users', 0)}\n"
                f"Профили: +{stats.get('inserted_profiles', stats.get('upserted_profiles', 0))}\n"
                f"Темы: +{stats.get('inserted_topics', 0)}"
            )
        kb = [[InlineKeyboardButton('👨‍🎓 К студентам', callback_data='list_students')]]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    # List menus with add buttons (new handlers)
    async def cb_list_students_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        data = await self._api_get('/api/students?limit=10') or []
        lines: List[str] = ['Студенты:']
        kb: List[List[InlineKeyboardButton]] = [
            [InlineKeyboardButton('➕ Добавить студента', callback_data='add_student')],
            [InlineKeyboardButton('📥 Импорт из Google-таблиц', callback_data='import_students')],
        ]
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','–')[:30]), callback_data=f"student_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_supervisors_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['Научные руководители:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('➕ Научный руководитель', callback_data='add_supervisor')]]
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','–')[:30]), callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_topics_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        data = await self._api_get('/api/topics?limit=10') or []
        lines: List[str] = ['Темы:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('➕ Тема', callback_data='add_topic')]]
        for t in data:
            lines.append(f"• {t.get('title','–')} (id={t.get('id')})")
            kb.append([InlineKeyboardButton(((t.get('title') or '–')[:30]), callback_data=f"topic_{t.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # List menus with pagination navigation
    async def cb_list_students_nav(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        offset = 0
        try:
            if '_' in (q.data or '') and q.data != 'list_students':
                offset = int(q.data.rsplit('_', 1)[1])
        except Exception:
            offset = 0
        limit = 10
        data = await self._api_get(f'/api/students?limit={limit}&offset={max(0, offset)}') or []
        lines: List[str] = ['Студенты:']
        kb: List[List[InlineKeyboardButton]] = [
            [InlineKeyboardButton('➕ Добавить студента', callback_data='add_student')],
            [InlineKeyboardButton('📥 Импорт из Google-таблиц', callback_data='import_students')],
        ]
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','–')[:30]), callback_data=f"student_{s.get('id')}")])
        nav: List[InlineKeyboardButton] = []
        if offset > 0:
            prev_off = max(0, offset - limit)
            nav.append(InlineKeyboardButton('◀️', callback_data=f'list_students_{prev_off}'))
        if len(data) == limit:
            next_off = offset + limit
            nav.append(InlineKeyboardButton('▶️', callback_data=f'list_students_{next_off}'))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_supervisors_nav(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        offset = 0
        try:
            if '_' in (q.data or '') and q.data != 'list_supervisors':
                offset = int(q.data.rsplit('_', 1)[1])
        except Exception:
            offset = 0
        limit = 10
        data = await self._api_get(f'/api/supervisors?limit={limit}&offset={max(0, offset)}') or []
        lines: List[str] = ['Научные руководители:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('➕ Научный руководитель', callback_data='add_supervisor')]]
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','–')[:30]), callback_data=f"supervisor_{s.get('id')}")])
        nav: List[InlineKeyboardButton] = []
        if offset > 0:
            prev_off = max(0, offset - limit)
            nav.append(InlineKeyboardButton('◀️', callback_data=f'list_supervisors_{prev_off}'))
        if len(data) == limit:
            next_off = offset + limit
            nav.append(InlineKeyboardButton('▶️', callback_data=f'list_supervisors_{next_off}'))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_topics_nav(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        offset = 0
        try:
            if '_' in (q.data or '') and q.data != 'list_topics':
                offset = int(q.data.rsplit('_', 1)[1])
        except Exception:
            offset = 0
        limit = 10
        data = await self._api_get(f'/api/topics?limit={limit}&offset={max(0, offset)}') or []
        lines: List[str] = ['Темы:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('➕ Тема', callback_data='add_topic')]]
        for t in data:
            title = (t.get('title') or '–')[:30]
            lines.append(f"• {t.get('title','–')} (id={t.get('id')})")
            kb.append([InlineKeyboardButton(title, callback_data=f"topic_{t.get('id')}")])
        nav: List[InlineKeyboardButton] = []
        if offset > 0:
            prev_off = max(0, offset - limit)
            nav.append(InlineKeyboardButton('◀️', callback_data=f'list_topics_{prev_off}'))
        if len(data) == limit:
            next_off = offset + limit
            nav.append(InlineKeyboardButton('▶️', callback_data=f'list_topics_{next_off}'))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Add flows (simple)
    async def cb_add_student_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        text = 'Добавление студентов выполняется через Google-форму и импорт в админке.'
        kb = [[InlineKeyboardButton('👨‍🎓 К студентам', callback_data='list_students')]]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_add_supervisor_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        context.user_data['awaiting'] = 'add_supervisor_name'
        await q.edit_message_text(self._fix_text('Введите ФИО научного руководителя сообщением. Для отмены — /start'))

    async def cb_add_topic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        context.user_data['add_topic_payload'] = {}
        context.user_data['add_topic_endpoint'] = None
        kb = [
            [InlineKeyboardButton('🎓 Ищу студента', callback_data='add_topic_role_student')],
            [InlineKeyboardButton('🧑‍🏫 Ищу научного руководителя', callback_data='add_topic_role_supervisor')],
            [InlineKeyboardButton('📚 К темам', callback_data='list_topics')],
        ]
        await q.edit_message_text(self._fix_text('Выберите, кого ищет тема:'), reply_markup=self._mk(kb))

    async def cb_add_topic_choose(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        role = 'student' if q.data.endswith('_student') else 'supervisor'
        context.user_data['awaiting'] = 'add_topic_title'
        context.user_data['topic_role'] = role
        payload = context.user_data.get('add_topic_payload') or {}
        payload['seeking_role'] = role
        context.user_data['add_topic_payload'] = payload
        await q.edit_message_text(
            self._fix_text('Введите название темы сообщением. После этого мы уточним описание и другие поля. Для отмены — /start')
        )

    async def cb_add_role_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await self._answer_callback(q)
        try:
            tid = int(q.data.rsplit('_', 1)[1])
        except Exception:
            await q.edit_message_text(self._fix_text('Некорректный идентификатор темы для добавления роли.'))
            return
        topic = await self._api_get(f'/api/topics/{tid}')
        if not topic:
            await q.edit_message_text(self._fix_text('Тема не найдена.'))
            return
        author_id = topic.get('author_user_id')
        uid = context.user_data.get('uid')
        allowed = False
        if self._is_admin(update):
            allowed = True
        elif uid is not None and author_id is not None:
            try:
                allowed = int(author_id) == int(uid)
            except Exception:
                allowed = author_id == uid
        if not allowed:
            try:
                await self._answer_callback(q, text=self._fix_text('У вас нет прав добавлять роли к этой теме.'), show_alert=True)
            except Exception:
                pass
            return
        context.user_data['awaiting'] = 'add_role_name'
        context.user_data['add_role_topic_id'] = tid
        context.user_data['add_role_payload'] = {}
        context.user_data['add_role_topic_title'] = topic.get('title')
        prompt = f"Введите название роли для темы «{topic.get('title','–')}». Для отмены — /start"
        await q.edit_message_text(self._fix_text(prompt))

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        awaiting = context.user_data.get('awaiting')
        if not awaiting:
            return
        text = (update.message.text or '').strip()

        if awaiting == 'submit_application_body':
            payload = context.user_data.get('application_payload') or {}
            if not payload:
                context.user_data['awaiting'] = None
                await update.message.reply_text(
                    self._fix_text('Состояние заявки сброшено. Попробуйте снова открыть роль или тему.')
                )
                return
            body_text = text
            if self._should_skip_optional(body_text):
                body_text = payload.get('default_body') or ''
            if not body_text or not body_text.strip():
                await update.message.reply_text(
                    self._fix_text('Сообщение не может быть пустым. Напишите текст или /start для отмены.')
                )
                return
            data = {
                'sender_user_id': payload.get('sender_user_id'),
                'receiver_user_id': payload.get('receiver_user_id'),
                'body': body_text.strip(),
            }
            topic_id_value = payload.get('topic_id')
            if topic_id_value is not None:
                data['topic_id'] = topic_id_value
            role_id = payload.get('role_id')
            if role_id:
                data['role_id'] = role_id
            res = await self._api_post('/api/messages/send', data=data)
            if not res or res.get('status') != 'ok':
                msg = (res or {}).get('message') or 'Не удалось отправить заявку. Попробуйте позже.'
                await update.message.reply_text(self._fix_text(msg))
                return
            context.user_data['awaiting'] = None
            payload_copy = dict(payload)
            context.user_data.pop('application_payload', None)
            message_id = res.get('message_id')
            try:
                uid = context.user_data.get('uid')
                if uid is not None and message_id is not None:
                    mid_int = int(message_id)
                    await self._get_message_details(context, uid, mid_int, refresh=True)
            except Exception:
                pass
            source = payload_copy.get('source')
            if source == 'supervisor_invite':
                success_lines = ['✅ Приглашение отправлено.']
            else:
                success_lines = ['✅ Заявка отправлена.']
            if message_id is not None:
                success_lines.append(f'Номер: #{message_id}')
            receiver_name = payload_copy.get('receiver_name')
            if receiver_name:
                success_lines.append(f'Получатель: {receiver_name}')
            role_name = payload_copy.get('role_name')
            if role_name:
                success_lines.append(f'Роль: {role_name}')
            topic_title = payload_copy.get('topic_title')
            if topic_title:
                success_lines.append(f'Тема: {topic_title}')
            kb: List[List[InlineKeyboardButton]] = []
            return_cb = payload_copy.get('return_callback')
            if return_cb:
                if source == 'role':
                    label = '⬅️ К роли'
                elif source == 'supervisor_invite':
                    label = '⬅️ К руководителю'
                else:
                    label = '⬅️ К теме'
                kb.append([InlineKeyboardButton(label, callback_data=return_cb)])
            kb.append([InlineKeyboardButton('📤 Мои заявки', callback_data='messages_outbox')])
            await update.message.reply_text(
                self._fix_text('\n'.join(success_lines)),
                reply_markup=self._mk(kb),
            )
            return

        if awaiting == 'add_supervisor_name':
            payload = {
                'full_name': text,
                'email': None,
                'username': getattr(update.effective_user, 'username', None) or None,
            }
            res = await self._api_post('/add-supervisor', data=payload)
            context.user_data['awaiting'] = None
            if res and res.get('status', 'success') in ('success', 'ok'):
                await update.message.reply_text(
                    self._fix_text('Научный руководитель добавлен.'),
                    reply_markup=self._mk([[InlineKeyboardButton('🧑‍🏫 К научным руководителям', callback_data='list_supervisors')]]),
                )
            else:
                await update.message.reply_text(
                    self._fix_text('Не удалось добавить научного руководителя. Попробуйте ещё раз или используйте веб-админку.')
                )
            return

        if awaiting == 'add_topic_title':
            if not text:
                await update.message.reply_text(
                    self._fix_text('Название темы не может быть пустым. Введите название или /start для отмены.')
                )
                return
            payload: Dict[str, Any] = context.user_data.get('add_topic_payload') or {}
            payload['title'] = text
            role = context.user_data.get('topic_role') or 'student'
            payload['seeking_role'] = role
            uid = context.user_data.get('uid')
            if uid is not None:
                payload['author_user_id'] = str(uid)
                context.user_data['add_topic_endpoint'] = '/api/add-topic'
            else:
                context.user_data['add_topic_endpoint'] = '/add-topic'
                payload['author_full_name'] = getattr(update.effective_user, 'full_name', None) or 'Неизвестный автор'
            context.user_data['add_topic_payload'] = payload
            context.user_data['awaiting'] = 'add_topic_description'
            await update.message.reply_text(
                self._fix_text('Введите описание темы (или "-" чтобы пропустить).')
            )
            return

        if awaiting == 'add_topic_description':
            payload = context.user_data.get('add_topic_payload') or {}
            payload['description'] = '' if self._should_skip_optional(text) else text
            context.user_data['add_topic_payload'] = payload
            context.user_data['awaiting'] = 'add_topic_expected'
            await update.message.reply_text(
                self._fix_text('Укажите ожидаемые результаты (или "-" чтобы пропустить).')
            )
            return

        if awaiting == 'add_topic_expected':
            payload = context.user_data.get('add_topic_payload') or {}
            payload['expected_outcomes'] = '' if self._should_skip_optional(text) else text
            context.user_data['add_topic_payload'] = payload
            context.user_data['awaiting'] = 'add_topic_skills'
            await update.message.reply_text(
                self._fix_text('Перечислите требуемые навыки (или "-" чтобы пропустить).')
            )
            return

        if awaiting == 'add_topic_skills':
            payload = context.user_data.get('add_topic_payload') or {}
            payload['required_skills'] = '' if self._should_skip_optional(text) else text
            context.user_data['add_topic_payload'] = payload
            context.user_data['awaiting'] = 'add_topic_direction'
            await update.message.reply_text(
                self._fix_text('Укажите направление (цифрой, например 9, или "-" чтобы пропустить).')
            )
            return

        if awaiting == 'add_topic_direction':
            payload = context.user_data.get('add_topic_payload') or {}
            if self._should_skip_optional(text):
                payload['direction'] = ''
            else:
                if not text.isdigit():
                    await update.message.reply_text(
                        self._fix_text('Направление должно быть числом. Введите номер или "-".')
                    )
                    return
                payload['direction'] = text
            context.user_data['add_topic_payload'] = payload
            await self._finish_add_topic(update, context)
            return

        if awaiting == 'add_role_name':
            if not text:
                await update.message.reply_text(
                    self._fix_text('Название роли не может быть пустым. Введите название или /start для отмены.')
                )
                return
            payload = context.user_data.get('add_role_payload') or {}
            payload['name'] = text
            context.user_data['add_role_payload'] = payload
            context.user_data['awaiting'] = 'add_role_description'
            await update.message.reply_text(self._fix_text('Введите описание роли (или "-" чтобы пропустить).'))
            return

        if awaiting == 'add_role_description':
            payload = context.user_data.get('add_role_payload') or {}
            payload['description'] = None if self._should_skip_optional(text) else text
            context.user_data['add_role_payload'] = payload
            context.user_data['awaiting'] = 'add_role_skills'
            await update.message.reply_text(self._fix_text('Укажите требуемые навыки (или "-" чтобы пропустить).'))
            return

        if awaiting == 'add_role_skills':
            payload = context.user_data.get('add_role_payload') or {}
            payload['required_skills'] = None if self._should_skip_optional(text) else text
            context.user_data['add_role_payload'] = payload
            context.user_data['awaiting'] = 'add_role_capacity'
            await update.message.reply_text(
                self._fix_text('Укажите вместимость роли числом (или "-" чтобы пропустить).')
            )
            return

        if awaiting == 'add_role_capacity':
            payload = context.user_data.get('add_role_payload') or {}
            if self._should_skip_optional(text):
                capacity_val: Optional[int] = None
            else:
                try:
                    capacity_val = int(text)
                    if capacity_val < 0:
                        raise ValueError('negative capacity')
                except Exception:
                    await update.message.reply_text(
                        self._fix_text('Вместимость должна быть числом. Введите число или "-" чтобы пропустить.')
                    )
                    return
            payload['capacity'] = capacity_val
            context.user_data['add_role_payload'] = payload
            topic_id = context.user_data.get('add_role_topic_id')
            topic_title = context.user_data.get('add_role_topic_title')
            if not topic_id or not payload.get('name'):
                context.user_data['awaiting'] = None
                context.user_data.pop('add_role_payload', None)
                context.user_data.pop('add_role_topic_id', None)
                context.user_data.pop('add_role_topic_title', None)
                await update.message.reply_text(
                    self._fix_text('Не удалось определить тему для роли. Начните заново /start.')
                )
                return
            data = {
                'topic_id': str(topic_id),
                'name': payload.get('name').strip(),
            }
            if payload.get('description'):
                data['description'] = payload['description']
            if payload.get('required_skills'):
                data['required_skills'] = payload['required_skills']
            if payload.get('capacity') is not None:
                data['capacity'] = str(payload['capacity'])
            res = await self._api_post('/api/add-role', data=data)
            context.user_data['awaiting'] = None
            context.user_data.pop('add_role_payload', None)
            context.user_data.pop('add_role_topic_id', None)
            context.user_data.pop('add_role_topic_title', None)
            if not res or res.get('status') not in ('ok', 'success'):
                await update.message.reply_text(
                    self._fix_text('Не удалось добавить роль. Попробуйте позже или используйте веб-админку.')
                )
                return
            kb = [[InlineKeyboardButton('📚 К теме', callback_data=f'topic_{topic_id}')]]
            role_name = payload.get('name')
            topic_str = topic_title or f'#{topic_id}'
            msg = f'Роль "{role_name}" добавлена к теме «{topic_str}».'
            await update.message.reply_text(self._fix_text(msg), reply_markup=self._mk(kb))
            return

        if awaiting == 'edit_student_program':
            payload = context.user_data.get('edit_student_payload') or {}
            value = self._normalize_edit_input(text)
            payload['program'] = value
            context.user_data['edit_student_payload'] = payload
            context.user_data['awaiting'] = 'edit_student_skills'
            original = context.user_data.get('edit_student_original') or {}
            prompt = (
                f"Навыки (сейчас: {original.get('skills') or '–'}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_student_skills':
            payload = context.user_data.get('edit_student_payload') or {}
            value = self._normalize_edit_input(text)
            payload['skills'] = value
            context.user_data['edit_student_payload'] = payload
            context.user_data['awaiting'] = 'edit_student_interests'
            original = context.user_data.get('edit_student_original') or {}
            prompt = (
                f"Интересы (сейчас: {original.get('interests') or '–'}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_student_interests':
            payload = context.user_data.get('edit_student_payload') or {}
            value = self._normalize_edit_input(text)
            payload['interests'] = value
            context.user_data['edit_student_payload'] = payload
            context.user_data['awaiting'] = 'edit_student_cv'
            original = context.user_data.get('edit_student_original') or {}
            prompt = (
                f"Ссылка на CV (сейчас: {(original.get('cv') or '–')[:200]}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_student_cv':
            payload = context.user_data.get('edit_student_payload') or {}
            value = self._normalize_edit_input(text)
            payload['cv'] = value
            context.user_data['edit_student_payload'] = payload
            await self._finish_edit_student(update, context)
            return

        if awaiting == 'edit_supervisor_position':
            payload = context.user_data.get('edit_supervisor_payload') or {}
            value = self._normalize_edit_input(text)
            payload['position'] = value
            context.user_data['edit_supervisor_payload'] = payload
            context.user_data['awaiting'] = 'edit_supervisor_degree'
            original = context.user_data.get('edit_supervisor_original') or {}
            prompt = (
                f"Учёная степень (сейчас: {original.get('degree') or '–'}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_supervisor_degree':
            payload = context.user_data.get('edit_supervisor_payload') or {}
            value = self._normalize_edit_input(text)
            payload['degree'] = value
            context.user_data['edit_supervisor_payload'] = payload
            context.user_data['awaiting'] = 'edit_supervisor_capacity'
            original = context.user_data.get('edit_supervisor_original') or {}
            prompt = (
                f"Лимит студентов (сейчас: {original.get('capacity') or '–'}).\n"
                "Напишите число, «пропустить» или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_supervisor_capacity':
            payload = context.user_data.get('edit_supervisor_payload') or {}
            value = self._normalize_edit_input(text)
            if value not in (self.EDIT_KEEP, None):
                try:
                    int(str(value))
                except Exception:
                    await update.message.reply_text(
                        self._fix_text('Вместимость должна быть числом. Введите число, «пропустить» или «-».')
                    )
                    return
            payload['capacity'] = value
            context.user_data['edit_supervisor_payload'] = payload
            context.user_data['awaiting'] = 'edit_supervisor_interests'
            original = context.user_data.get('edit_supervisor_original') or {}
            prompt = (
                f"Интересы (сейчас: {original.get('interests') or '–'}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_supervisor_interests':
            payload = context.user_data.get('edit_supervisor_payload') or {}
            value = self._normalize_edit_input(text)
            payload['interests'] = value
            context.user_data['edit_supervisor_payload'] = payload
            context.user_data['awaiting'] = 'edit_supervisor_requirements'
            original = context.user_data.get('edit_supervisor_original') or {}
            prompt = (
                f"Требования (сейчас: {original.get('requirements') or '–'}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_supervisor_requirements':
            payload = context.user_data.get('edit_supervisor_payload') or {}
            value = self._normalize_edit_input(text)
            payload['requirements'] = value
            context.user_data['edit_supervisor_payload'] = payload
            await self._finish_edit_supervisor(update, context)
            return

        if awaiting == 'edit_topic_title':
            payload = context.user_data.get('edit_topic_payload') or {}
            value = self._normalize_edit_input(text)
            if value is None:
                await update.message.reply_text(
                    self._fix_text('Название темы не может быть пустым. Введите текст или «пропустить».')
                )
                return
            payload['title'] = value
            context.user_data['edit_topic_payload'] = payload
            context.user_data['awaiting'] = 'edit_topic_description'
            original = context.user_data.get('edit_topic_original') or {}
            prompt = (
                f"Описание (сейчас: {(original.get('description') or '–')[:300]}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_topic_description':
            payload = context.user_data.get('edit_topic_payload') or {}
            value = self._normalize_edit_input(text)
            payload['description'] = value
            context.user_data['edit_topic_payload'] = payload
            context.user_data['awaiting'] = 'edit_topic_expected'
            original = context.user_data.get('edit_topic_original') or {}
            prompt = (
                f"Ожидаемые результаты (сейчас: {(original.get('expected_outcomes') or '–')[:300]}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_topic_expected':
            payload = context.user_data.get('edit_topic_payload') or {}
            value = self._normalize_edit_input(text)
            payload['expected_outcomes'] = value
            context.user_data['edit_topic_payload'] = payload
            context.user_data['awaiting'] = 'edit_topic_skills'
            original = context.user_data.get('edit_topic_original') or {}
            prompt = (
                f"Требуемые навыки (сейчас: {original.get('required_skills') or '–'}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_topic_skills':
            payload = context.user_data.get('edit_topic_payload') or {}
            value = self._normalize_edit_input(text)
            payload['required_skills'] = value
            context.user_data['edit_topic_payload'] = payload
            context.user_data['awaiting'] = 'edit_topic_direction'
            original = context.user_data.get('edit_topic_original') or {}
            prompt = (
                f"Направление (сейчас: {original.get('direction') or '–'}).\n"
                "Введите число, «пропустить» или «-»/«очистить», чтобы удалить значение."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_topic_direction':
            payload = context.user_data.get('edit_topic_payload') or {}
            value = self._normalize_edit_input(text)
            if value not in (self.EDIT_KEEP, None):
                if not str(value).isdigit():
                    await update.message.reply_text(
                        self._fix_text('Направление должно быть числом. Введите номер, «пропустить» или «-».')
                    )
                    return
            payload['direction'] = value
            context.user_data['edit_topic_payload'] = payload
            context.user_data['awaiting'] = 'edit_topic_seeking_role'
            original = context.user_data.get('edit_topic_original') or {}
            prompt = (
                f"Кого ищет тема (сейчас: {original.get('seeking_role') or 'student'}).\n"
                "Введите student/supervisor или напишите «пропустить», чтобы оставить без изменений."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_topic_seeking_role':
            payload = context.user_data.get('edit_topic_payload') or {}
            lowered = text.strip().lower()
            if not lowered or lowered in {'пропустить', 'skip', 'оставить', 'не менять'}:
                payload['seeking_role'] = self.EDIT_KEEP
            else:
                role_val = self._normalize_role_value(text)
                if not role_val:
                    await update.message.reply_text(
                        self._fix_text('Укажите «student» или «supervisor», либо напишите «пропустить».')
                    )
                    return
                payload['seeking_role'] = role_val
            context.user_data['edit_topic_payload'] = payload
            await self._finish_edit_topic(update, context)
            return

        if awaiting == 'edit_role_name':
            payload = context.user_data.get('edit_role_payload') or {}
            value = self._normalize_edit_input(text)
            if value is None:
                await update.message.reply_text(
                    self._fix_text('Название роли не может быть пустым. Введите текст или «пропустить».')
                )
                return
            payload['name'] = value
            context.user_data['edit_role_payload'] = payload
            context.user_data['awaiting'] = 'edit_role_description'
            original = context.user_data.get('edit_role_original') or {}
            prompt = (
                f"Описание (сейчас: {(original.get('description') or '–')[:300]}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_role_description':
            payload = context.user_data.get('edit_role_payload') or {}
            value = self._normalize_edit_input(text)
            payload['description'] = value
            context.user_data['edit_role_payload'] = payload
            context.user_data['awaiting'] = 'edit_role_required'
            original = context.user_data.get('edit_role_original') or {}
            prompt = (
                f"Требуемые навыки (сейчас: {original.get('required_skills') or '–'}).\n"
                "Напишите «пропустить», чтобы оставить без изменений, или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_role_required':
            payload = context.user_data.get('edit_role_payload') or {}
            value = self._normalize_edit_input(text)
            payload['required_skills'] = value
            context.user_data['edit_role_payload'] = payload
            context.user_data['awaiting'] = 'edit_role_capacity'
            original = context.user_data.get('edit_role_original') or {}
            prompt = (
                f"Вместимость (сейчас: {original.get('capacity') or '–'}).\n"
                "Введите число, «пропустить» или «-»/«очистить», чтобы удалить."
            )
            await update.message.reply_text(self._fix_text(prompt))
            return

        if awaiting == 'edit_role_capacity':
            payload = context.user_data.get('edit_role_payload') or {}
            value = self._normalize_edit_input(text)
            if value not in (self.EDIT_KEEP, None):
                try:
                    int(str(value))
                except Exception:
                    await update.message.reply_text(
                        self._fix_text('Вместимость должна быть числом. Введите число, «пропустить» или «-».')
                    )
                    return
            payload['capacity'] = value
            context.user_data['edit_role_payload'] = payload
            await self._finish_edit_role(update, context)
            return

        context.user_data['awaiting'] = None
        await update.message.reply_text(self._fix_text('Действие отменено. Начните заново /start.'))

    async def _finish_add_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload: Dict[str, Any] = context.user_data.get('add_topic_payload') or {}
        endpoint = context.user_data.get('add_topic_endpoint') or '/api/add-topic'
        data: Dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, int):
                data[key] = str(value)
            else:
                data[key] = value
        res = await self._api_post(endpoint, data=data)
        context.user_data['awaiting'] = None
        context.user_data.pop('topic_role', None)
        context.user_data.pop('add_topic_payload', None)
        context.user_data.pop('add_topic_endpoint', None)
        if not res:
            await update.message.reply_text(
                self._fix_text('Не удалось добавить тему. Попробуйте ещё раз или используйте веб-админку.')
            )
            return
        status = (res.get('status') or '').lower()
        if status in {'ok', 'success'}:
            duplicate = (res.get('message') == 'duplicate')
            topic_id_raw = res.get('topic_id')
            topic_id: Optional[int]
            if isinstance(topic_id_raw, int):
                topic_id = topic_id_raw
            else:
                try:
                    topic_id = int(str(topic_id_raw))
                except Exception:
                    topic_id = None
            kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('📚 Мои темы', callback_data='my_topics')]]
            if topic_id:
                kb.insert(0, [InlineKeyboardButton('🔍 Открыть тему', callback_data=f'topic_{topic_id}')])
            elif endpoint == '/add-topic':
                kb.insert(0, [InlineKeyboardButton('📚 К темам', callback_data='list_topics')])
            msg = 'Такая тема у вас уже есть.' if duplicate else 'Тема добавлена.'
            await update.message.reply_text(self._fix_text(msg), reply_markup=self._mk(kb))
        else:
            await update.message.reply_text(
                self._fix_text('Не удалось добавить тему. Попробуйте ещё раз или используйте веб-админку.')
            )

    async def _finish_edit_student(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = context.user_data.get('edit_student_payload') or {}
        user_id = payload.get('user_id')
        if user_id is None:
            context.user_data['awaiting'] = None
            return
        data: Dict[str, Any] = {'user_id': str(user_id)}
        for key in ('program', 'skills', 'interests', 'cv'):
            value = payload.get(key, self.EDIT_KEEP)
            if value == self.EDIT_KEEP:
                continue
            if value is None:
                data[key] = ''
            else:
                data[key] = value
        res = await self._api_post('/api/update-student-profile', data=data)
        context.user_data['awaiting'] = None
        context.user_data.pop('edit_student_payload', None)
        context.user_data.pop('edit_student_original', None)
        if not res or res.get('status') != 'ok':
            await update.message.reply_text(self._fix_text('Не удалось обновить профиль студента. Попробуйте позже.'))
            return
        kb = [[InlineKeyboardButton('👤 К профилю', callback_data=f'student_{user_id}')]]
        await update.message.reply_text(
            self._fix_text('Профиль студента обновлён.'), reply_markup=self._mk(kb)
        )

    async def _finish_edit_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = context.user_data.get('edit_supervisor_payload') or {}
        user_id = payload.get('user_id')
        if user_id is None:
            context.user_data['awaiting'] = None
            return
        data: Dict[str, Any] = {'user_id': str(user_id)}
        for key in ('position', 'degree', 'interests', 'requirements'):
            value = payload.get(key, self.EDIT_KEEP)
            if value == self.EDIT_KEEP:
                continue
            if value is None:
                data[key] = ''
            else:
                data[key] = value
        capacity_value = payload.get('capacity', self.EDIT_KEEP)
        if capacity_value != self.EDIT_KEEP:
            if capacity_value is None:
                data['capacity'] = ''
            else:
                data['capacity'] = str(capacity_value)
        res = await self._api_post('/api/update-supervisor-profile', data=data)
        context.user_data['awaiting'] = None
        context.user_data.pop('edit_supervisor_payload', None)
        context.user_data.pop('edit_supervisor_original', None)
        if not res or res.get('status') != 'ok':
            await update.message.reply_text(self._fix_text('Не удалось обновить профиль руководителя. Попробуйте позже.'))
            return
        kb = [[InlineKeyboardButton('👤 К профилю', callback_data=f'supervisor_{user_id}')]]
        await update.message.reply_text(
            self._fix_text('Профиль руководителя обновлён.'), reply_markup=self._mk(kb)
        )

    async def _finish_edit_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = context.user_data.get('edit_topic_payload') or {}
        original = context.user_data.get('edit_topic_original') or {}
        topic_id = payload.get('topic_id')
        if topic_id is None:
            context.user_data['awaiting'] = None
            return
        data: Dict[str, Any] = {'topic_id': str(topic_id)}
        editor = payload.get('editor_user_id')
        if editor:
            data['editor_user_id'] = str(editor)
        title_value = payload.get('title', self.EDIT_KEEP)
        if title_value == self.EDIT_KEEP:
            data['title'] = original.get('title') or ''
        elif title_value is None:
            await update.message.reply_text(self._fix_text('Название темы не может быть пустым.'))
            return
        else:
            data['title'] = title_value
        if not data['title']:
            await update.message.reply_text(self._fix_text('Название темы не может быть пустым.'))
            return
        for key in ('description', 'expected_outcomes', 'required_skills'):
            value = payload.get(key, self.EDIT_KEEP)
            if value == self.EDIT_KEEP:
                continue
            if value is None:
                data[key] = ''
            else:
                data[key] = value
        direction_value = payload.get('direction', self.EDIT_KEEP)
        if direction_value != self.EDIT_KEEP:
            data['direction'] = '' if direction_value is None else str(direction_value)
        role_value = payload.get('seeking_role', self.EDIT_KEEP)
        if role_value != self.EDIT_KEEP and role_value:
            data['seeking_role'] = role_value
        res = await self._api_post('/api/update-topic', data=data)
        context.user_data['awaiting'] = None
        context.user_data.pop('edit_topic_payload', None)
        context.user_data.pop('edit_topic_original', None)
        if not res or res.get('status') != 'ok':
            await update.message.reply_text(self._fix_text('Не удалось обновить тему. Попробуйте позже.'))
            return
        kb = [[InlineKeyboardButton('📚 К теме', callback_data=f'topic_{topic_id}')]]
        await update.message.reply_text(self._fix_text('Тема обновлена.'), reply_markup=self._mk(kb))

    async def _finish_edit_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = context.user_data.get('edit_role_payload') or {}
        original = context.user_data.get('edit_role_original') or {}
        role_id = payload.get('role_id')
        if role_id is None:
            context.user_data['awaiting'] = None
            return
        data: Dict[str, Any] = {'role_id': str(role_id)}
        editor = payload.get('editor_user_id')
        if editor:
            data['editor_user_id'] = str(editor)
        name_value = payload.get('name', self.EDIT_KEEP)
        if name_value == self.EDIT_KEEP:
            data['name'] = original.get('name') or ''
        elif name_value is None:
            await update.message.reply_text(self._fix_text('Название роли не может быть пустым.'))
            return
        else:
            data['name'] = name_value
        if not data['name']:
            await update.message.reply_text(self._fix_text('Название роли не может быть пустым.'))
            return
        for key in ('description', 'required_skills'):
            value = payload.get(key, self.EDIT_KEEP)
            if value == self.EDIT_KEEP:
                continue
            if value is None:
                data[key] = ''
            else:
                data[key] = value
        capacity_value = payload.get('capacity', self.EDIT_KEEP)
        if capacity_value != self.EDIT_KEEP:
            data['capacity'] = '' if capacity_value is None else str(capacity_value)
        res = await self._api_post('/api/update-role', data=data)
        context.user_data['awaiting'] = None
        context.user_data.pop('edit_role_payload', None)
        context.user_data.pop('edit_role_original', None)
        if not res or res.get('status') != 'ok':
            await update.message.reply_text(self._fix_text('Не удалось обновить роль. Попробуйте позже.'))
            return
        topic_id = original.get('topic_id')
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('🎭 К роли', callback_data=f'role_{role_id}')]]
        if topic_id:
            kb.append([InlineKeyboardButton('📚 К теме', callback_data=f'topic_{topic_id}')])
        await update.message.reply_text(
            self._fix_text('Роль обновлена.'), reply_markup=self._mk(kb)
        )

