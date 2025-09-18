import os
import logging
from typing import Optional, Dict, Any, List

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)


class MentorMatchBot:
    def __init__(self) -> None:
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not token:
            # Message fixed to proper Russian to avoid mojibake in logs
            raise ValueError('TELEGRAM_BOT_TOKEN не задан в окружении')
        self.server_url = os.getenv('SERVER_URL', 'http://localhost:8000')
        self.admin_ids: set[int] = set()
        self.admin_usernames: set[str] = set()  # lower-case without @
        self._load_admins()
        self.app = Application.builder().token(token).build()
        self._setup_handlers()

    # --- Text/keyboard fixing helpers ---
    def _fix_text(self, s: Optional[str]) -> Optional[str]:
        """Best-effort fix for mojibake produced when UTF-8 text (e.g., "Студенты")
        was decoded as cp1252. Converts it back to readable UTF-8 while leaving normal
        ASCII and Cyrillic untouched.
        """
        if not isinstance(s, str):
            return s
        # Quick check to avoid touching normal text
        if not any(ch in s for ch in ('Ð', 'Ñ', 'Ã', 'Â', 'â', 'ð')):
            return s
        for enc in ('cp1252', 'latin1'):
            try:
                return s.encode(enc).decode('utf-8')
            except Exception:
                continue
        return s

    def _mk(self, kb: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
        """Fix button texts in a 2D list and return InlineKeyboardMarkup."""
        for row in kb:
            for btn in row:
                try:
                    btn.text = self._fix_text(btn.text)
                except Exception:
                    pass
        return InlineKeyboardMarkup(kb)

    def run(self) -> None:
        self.app.run_polling()

    def _setup_handlers(self) -> None:
        self.app.add_handler(CommandHandler('start', self.cmd_start2))
        self.app.add_handler(CommandHandler('help', self.cmd_help))

        # Lists (menu with add buttons)
        # Support pagination via optional suffix _<offset>
        self.app.add_handler(CallbackQueryHandler(self.cb_list_students_nav, pattern=r'^list_students(?:_\d+)?$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_list_supervisors_nav, pattern=r'^list_supervisors(?:_\d+)?$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_list_topics_nav, pattern=r'^list_topics(?:_\d+)?$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_import_students, pattern=r'^import_students$'))
        # Add flows (callbacks)
        self.app.add_handler(CallbackQueryHandler(self.cb_add_student_info, pattern=r'^add_student$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_add_supervisor_start, pattern=r'^add_supervisor$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_add_topic_start, pattern=r'^add_topic$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_add_topic_choose, pattern=r'^add_topic_role_(student|supervisor)$'))

        # Identity & Profiles
        self.app.add_handler(CallbackQueryHandler(self.cb_confirm_me, pattern=r'^confirm_me_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_not_me, pattern=r'^not_me$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_register_role, pattern=r'^register_role_(student|supervisor)$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_student_me, pattern=r'^student_me$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_supervisor_me, pattern=r'^supervisor_me$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_my_topics, pattern=r'^my_topics$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_match_topics_for_me, pattern=r'^match_topics_for_me$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_view_student, pattern=r'^student_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_view_supervisor, pattern=r'^supervisor_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_view_topic, pattern=r'^topic_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_view_role, pattern=r'^role_\d+$'))

    
    async def cb_view_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        try:
            rid = int(q.data.split('_')[1])
        except Exception:
            await q.edit_message_text(self._fix_text('Некорректный идентификатор роли'))
            return
        r = await self._api_get(f'/api/roles/{rid}')
        if not r:
            await q.edit_message_text(self._fix_text('Роль не найдена'))
            return
        lines: List[str] = [
            f"Роль: {r.get('name') or ''}",
            f"Тема: {r.get('topic_title') or ''}",
            f"Описание: {(r.get('description') or '')[:500]}",
            f"Требуемые навыки: {r.get('required_skills') or ''}",
            f"Вместимость: {r.get('capacity') or ''}",
            f"ID: {r.get('id')}",
        ]
        # Candidates
        candidates = await self._api_get(f"/api/role-candidates/{rid}") or []
        if candidates:
            lines.append('')
            lines.append('Лучшие кандидаты:')
            for it in candidates:
                uname = it.get('username')
                uname_str = f" ({uname})" if uname else ""
                lines.append(f"#{it.get('rank')}. {it.get('full_name','')}" + uname_str + f" (балл={it.get('score')})")
        text = '\n'.join(lines)
        kb: List[List[InlineKeyboardButton]] = [
            [InlineKeyboardButton('🧠 Подобрать студентов', callback_data=f'match_role_{rid}')]
        ]
        topic_id = r.get('topic_id')
        if topic_id:
            kb.append([InlineKeyboardButton('⬅️ К теме', callback_data=f'topic_{topic_id}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
    # Matching actions
        self.app.add_handler(CallbackQueryHandler(self.cb_match_student, pattern=r'^match_student_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_match_supervisor, pattern=r'^match_supervisor_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_match_students_for_topic, pattern=r'^match_students_topic_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_match_students_for_role, pattern=r'^match_role_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_match_topics_for_supervisor, pattern=r'^match_topics_for_supervisor_\d+$'))

        # Back to main
        self.app.add_handler(CallbackQueryHandler(self.cb_back, pattern=r'^back_to_main$'))
        # Error handler
        self.app.add_error_handler(self.on_error)
        # Text input handler for simple add flows
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
    async def cmd_start2(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await self.cmd_start(update, context)

    def _load_admins(self) -> None:
        try:
            p = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates', 'admins.txt')
            if not os.path.exists(p):
                return
            with open(p, 'r', encoding='utf-8') as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith('#'):
                        continue
                    if s.isdigit():
                        try:
                            self.admin_ids.add(int(s))
                        except Exception:
                            pass
                        continue
                    if s.startswith('@'):
                        s = s[1:]
                    if s.lower().startswith('https://t.me/'):
                        s = s.split('/')[-1]
                    self.admin_usernames.add(s.lower())
        except Exception as e:
            logger.warning('Failed to load admins.txt: %s', e)

    def _is_admin(self, update: Update) -> bool:
        u = update.effective_user
        if not u:
            return False
        if getattr(u, 'id', None) in self.admin_ids:
            return True
        uname = (getattr(u, 'username', '') or '').lower()
        if uname and uname.lower() in self.admin_usernames:
            return True
        return False

    async def _api_get(self, path: str) -> Optional[Dict[str, Any]]:
        url = f'{self.server_url}{path}'
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=20) as r:
                    if r.status == 200:
                        return await r.json()
                    logger.error('GET %s -> %s', url, r.status)
        except Exception as e:
            logger.exception('GET %s failed: %s', url, e)
        return None

    async def _api_post(self, path: str, data: Dict[str, Any], timeout: int = 60) -> Optional[Dict[str, Any]]:
        url = f'{self.server_url}{path}'
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=data, timeout=timeout) as r:
                    if r.status == 200:
                        return await r.json()
                    if r.status == 303:
                        return {'status': 'success'}
                    logger.error('POST %s -> %s', url, r.status)
        except Exception as e:
            logger.exception('POST %s failed: %s', url, e)
        return None

    # Commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Admins: старое меню целиком
        if self._is_admin(update):
            kb = [
                [InlineKeyboardButton('👨‍🎓 Студенты', callback_data='list_students')],
                [InlineKeyboardButton('🧑‍🏫 Научные руководители', callback_data='list_supervisors')],
                [InlineKeyboardButton('📚 Темы', callback_data='list_topics')],
            ]
            text = 'Админ‑меню: выберите раздел'
            if update.message:
                await update.message.reply_text(self._fix_text(text), reply_markup=self._mk(kb))
            elif update.callback_query:
                await update.callback_query.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
            return

        # Идентификация по Telegram
        u = update.effective_user
        tg_id = getattr(u, 'id', None)
        uname = getattr(u, 'username', None)
        who = await self._api_get(f"/api/whoami?tg_id={tg_id or ''}&username={uname or ''}") or {}
        matches = who.get('matches') or []
        if not matches:
            # Не нашли — спросим роль
            text = 'Мы не нашли вашу запись из формы. Вы студент или научный руководитель?'
            kb = [
                [InlineKeyboardButton('👨‍🎓 Студент', callback_data='register_role_student')],
                [InlineKeyboardButton('🧑‍🏫 Научный руководитель', callback_data='register_role_supervisor')],
            ]
            if update.message:
                await update.message.reply_text(self._fix_text(text), reply_markup=self._mk(kb))
            else:
                await update.callback_query.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
            return

        # Есть совпадения — предложим подтвердить
        lines = ['Найдены записи. Это вы?']
        kb: List[List[InlineKeyboardButton]] = []
        for m in matches:
            uid = m.get('id')
            fn = m.get('full_name')
            role = m.get('role')
            lines.append(f"• {fn} — {role} (id={uid})")
            kb.append([InlineKeyboardButton(f"Да, я: {fn}", callback_data=f"confirm_me_{uid}")])
        kb.append([InlineKeyboardButton('Нет, это не я', callback_data='not_me')])
        text = '\n'.join(lines)
        if update.message:
            await update.message.reply_text(self._fix_text(text), reply_markup=self._mk(kb))
        else:
            await update.callback_query.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._fix_text('Разделы: Студенты, Научные руководители, Темы. В профиле студента — кнопка Подобрать тему. В профиле темы (где нужен научный руководитель) — Подобрать научного руководителя.'))

    # Identity callbacks
    async def cb_confirm_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        uid = int(q.data.split('_')[2])
        u = update.effective_user
        payload = {
            'user_id': uid,
            'tg_id': getattr(u, 'id', None),
            'username': (getattr(u, 'username', None) or ''),
        }
        await self._api_post('/api/bind-telegram', data=payload)
        # Determine role
        role = 'student'
        prof = await self._api_get(f'/api/students/{uid}')
        if not prof or prof.get('error'):
            role = 'supervisor'
        context.user_data['uid'] = uid
        context.user_data['role'] = role
        await self._show_role_menu(update, context)

    async def cb_not_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        kb = [
            [InlineKeyboardButton('👨‍🎓 Я студент', callback_data='register_role_student')],
            [InlineKeyboardButton('🧑‍🏫 Я научный руководитель', callback_data='register_role_supervisor')],
        ]
        await q.edit_message_text(self._fix_text('Выберите роль для регистрации:'), reply_markup=self._mk(kb))

    async def cb_register_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        role = q.data.split('_')[-1]
        u = update.effective_user
        full_name = getattr(u, 'full_name', None) or getattr(u, 'first_name', '')
        payload = {
            'role': role,
            'full_name': full_name,
            'username': getattr(u, 'username', None) or '',
            'tg_id': getattr(u, 'id', None),
        }
        res = await self._api_post('/api/self-register', data=payload)
        if not res or res.get('status') != 'ok':
            await q.edit_message_text(self._fix_text('Не удалось зарегистрироваться. Попробуйте позже.'))
            return
        context.user_data['uid'] = int(res.get('user_id'))
        context.user_data['role'] = res.get('role')
        await self._show_role_menu(update, context)

    async def _show_role_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        role = context.user_data.get('role')
        uid = context.user_data.get('uid')
        if role == 'student':
            kb = [
                [InlineKeyboardButton('👤 Мой профиль', callback_data='student_me')],
                [InlineKeyboardButton('📚 Мои темы', callback_data='my_topics')],
                [InlineKeyboardButton('➕ Добавить тему', callback_data='add_topic')],
                [InlineKeyboardButton('🧠 Подобрать роли для меня', callback_data=f'match_student_{uid}')],
            ]
            text = 'Студент: выберите действие'
        else:
            kb = [
                [InlineKeyboardButton('👤 Мой профиль', callback_data='supervisor_me')],
                [InlineKeyboardButton('📚 Мои темы', callback_data='my_topics')],
                [InlineKeyboardButton('➕ Добавить тему', callback_data='add_topic')],
                [InlineKeyboardButton('🧠 Подобрать темы для меня', callback_data='match_topics_for_me')],
            ]
            text = 'Научный руководитель: выберите действие'
        if update.callback_query:
            await update.callback_query.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
        else:
            await update.message.reply_text(self._fix_text(text), reply_markup=self._mk(kb))

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
        q = update.callback_query; await q.answer()
        uid = context.user_data.get('uid')
        data = await self._api_get(f'/api/user-topics/{uid}?limit=20') or []
        lines = ['Мои темы:']
        kb: List[List[InlineKeyboardButton]] = []
        for t in data:
            title = (t.get('title') or '–')[:40]
            kb.append([InlineKeyboardButton(title, callback_data=f'topic_{t.get("id")}')])
            kb.append([InlineKeyboardButton('👨‍🎓 Подобрать студентов', callback_data=f'match_students_topic_{t.get("id")}')])
        if not kb:
            lines.append('— пока нет тем —')
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_match_topics_for_me(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        uid = context.user_data.get('uid')
        if not uid:
            return await self.cmd_start(update, context)
        # Delegate without altering callback data
        await self.cb_match_topics_for_supervisor(update, context)

    # Lists
    async def cb_list_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/students?limit=10') or []
        lines: List[str] = ['Студенты:']
        kb: List[List[InlineKeyboardButton]] = []
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton(s.get('full_name','–')[:30], callback_data=f"student_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_supervisors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['Научные руководители:']
        kb: List[List[InlineKeyboardButton]] = []
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton(s.get('full_name','–')[:30], callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
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
        q = update.callback_query; await q.answer()
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
        kb = [
            [InlineKeyboardButton('🧠 Подобрать роль', callback_data=f'match_student_{sid}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')],
        ]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_view_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
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
        kb = [
            [InlineKeyboardButton('🧠 Подобрать тему', callback_data=f'match_topics_for_supervisor_{uid}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')],
        ]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_view_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        tid = int(q.data.split('_')[1])
        t = await self._api_get(f'/api/topics/{tid}')
        if not t:
            await q.edit_message_text(self._fix_text('Не удалось загрузить тему'))
            return
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
        kb.append([InlineKeyboardButton('🧑‍🏫 Подобрать научного руководителя', callback_data=f'match_supervisor_{tid}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines2)), reply_markup=self._mk(kb))

    # Matching
    async def cb_match_student(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        sid = int(q.data.split('_')[2])
        res = await self._api_post('/match-student', data={'student_user_id': sid})
        if not res or res.get('status') != 'ok':
            await q.edit_message_text(self._fix_text('Ошибка подбора ролей для студента'))
            return
        items = res.get('items', [])
        lines = [f'Топ‑5 ролей для студента #{sid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('role_name','–')} — {it.get('topic_title','–')} — {it.get('reason','')}")
        kb = [[InlineKeyboardButton('⬅️ К студенту', callback_data=f'student_{sid}')]]
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Import students from Google Sheets
    async def cb_import_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
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
        q = update.callback_query; await q.answer()
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
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['Научные руководители:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('➕ Научный руководитель', callback_data='add_supervisor')]]
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','–')[:30]), callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_topics_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
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
        q = update.callback_query; await q.answer()
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
        q = update.callback_query; await q.answer()
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
        q = update.callback_query; await q.answer()
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
        q = update.callback_query; await q.answer()
        text = 'Добавление студентов выполняется через Google-форму и импорт в админке.'
        kb = [[InlineKeyboardButton('👨‍🎓 К студентам', callback_data='list_students')]]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_add_supervisor_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        context.user_data['awaiting'] = 'add_supervisor_name'
        await q.edit_message_text(self._fix_text('Введите ФИО научного руководителя сообщением. Для отмены — /start'))

    async def cb_add_topic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        kb = [
            [InlineKeyboardButton('🎓 Ищу студента', callback_data='add_topic_role_student')],
            [InlineKeyboardButton('🧑‍🏫 Ищу научного руководителя', callback_data='add_topic_role_supervisor')],
            [InlineKeyboardButton('📚 К темам', callback_data='list_topics')],
        ]
        await q.edit_message_text(self._fix_text('Выберите, кого ищет тема:'), reply_markup=self._mk(kb))

    async def cb_add_topic_choose(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        role = 'student' if q.data.endswith('_student') else 'supervisor'
        context.user_data['awaiting'] = 'add_topic_title'
        context.user_data['topic_role'] = role
        await q.edit_message_text(self._fix_text('Введите название темы сообщением. Для отмены — /start'))

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        awaiting = context.user_data.get('awaiting')
        if not awaiting:
            return
        text = (update.message.text or '').strip()
        if awaiting == 'add_supervisor_name':
            payload = {
                'full_name': text,
                'email': None,
                'username': getattr(update.effective_user, 'username', None) or None,
            }
            res = await self._api_post('/add-supervisor', data=payload)
            context.user_data['awaiting'] = None
            if res and res.get('status', 'success') in ('success', 'ok'):
                await update.message.reply_text(self._fix_text('Научный руководитель добавлен.'), reply_markup=self._mk([[InlineKeyboardButton('🧑‍🏫 К научным руководителям', callback_data='list_supervisors')]]))
            else:
                await update.message.reply_text(self._fix_text('Не удалось добавить научного руководителя. Попробуйте ещё раз или используйте веб-админку.'))
        elif awaiting == 'add_topic_title':
            role = context.user_data.get('topic_role') or 'student'
            payload = {
                'title': text,
                'seeking_role': role,
                'author_full_name': (getattr(update.effective_user, 'full_name', None) or 'Неизвестный руководитель'),
            }
            res = await self._api_post('/add-topic', data=payload)
            context.user_data['awaiting'] = None
            context.user_data.pop('topic_role', None)
            if res and res.get('status', 'success') in ('success', 'ok'):
                await update.message.reply_text(self._fix_text('Тема добавлена.'), reply_markup=self._mk([[InlineKeyboardButton('📚 К темам', callback_data='list_topics')]]))
            else:
                await update.message.reply_text(self._fix_text('Не удалось добавить тему. Попробуйте ещё раз или используйте веб-админку.'))

    async def cb_match_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        tid = int(q.data.split('_')[2])
        res = await self._api_post('/match-topic', data={'topic_id': tid, 'target_role': 'supervisor'})
        if not res or res.get('status') not in ('ok', 'success'):
            await q.edit_message_text(self._fix_text('Ошибка подбора руководителя для темы'))
            return
        items = res.get('items', [])
        lines = [f'Топ‑5 руководителей для темы #{tid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('full_name','–')} — {it.get('reason','')}")
        kb = [[InlineKeyboardButton('⬅️ К теме', callback_data=f'topic_{tid}')]]
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_match_students_for_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Back-compat: предложим выбрать роль
        q = update.callback_query; await q.answer()
        tid = int(q.data.rsplit('_', 1)[1])
        roles = await self._api_get(f'/api/topics/{tid}/roles') or []
        if not roles:
            await q.edit_message_text(self._fix_text('Для темы не добавлены роли. Добавьте их в админке.'))
            return
        kb: List[List[InlineKeyboardButton]] = []
        for r in roles:
            kb.append([InlineKeyboardButton(f"🎭 {r.get('name','–')}", callback_data=f"match_role_{r.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ К теме', callback_data=f'topic_{tid}')])
        await q.edit_message_text(self._fix_text('Выберите роль для подбора студентов:'), reply_markup=self._mk(kb))

    async def cb_match_students_for_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        rid = int(q.data.rsplit('_', 1)[1])
        res = await self._api_post('/match-role', data={'role_id': rid})
        if not res or res.get('status') not in ('ok', 'success'):
            await q.edit_message_text(self._fix_text('Ошибка подбора студентов для роли'))
            return
        items = res.get('items', [])
        lines = [f'Топ‑5 студентов для роли #{rid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('full_name','–')} — {it.get('reason','')}")
        kb = [[InlineKeyboardButton('⬅️ К роли', callback_data=f'role_{rid}')]]
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_match_topics_for_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        # Parse user id from callback data; when invoked from 'for me' fallback to current user
        try:
            uid = int(q.data.rsplit('_', 1)[1])
        except Exception:
            uid = context.user_data.get('uid')
            if not uid:
                return await self.cmd_start(update, context)
        res = await self._api_post('/match-supervisor', data={'supervisor_user_id': uid})
        if not res or res.get('status') not in ('ok', 'success'):
            await q.edit_message_text(self._fix_text('Ошибка подбора тем для руководителя'))
            return
        items = res.get('items', [])
        lines = [f'Топ‑5 тем для руководителя #{uid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('title','–')} — {it.get('reason','')}")
        kb = [[InlineKeyboardButton('⬅️ К профилю', callback_data=f'supervisor_{uid}')]]
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Back
    async def cb_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        await self.cmd_start(update, context)

    # Global error handler (чтобы не сыпались stacktrace в логи без обработки)
    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception('Ошибка бота: %s', getattr(context, 'error', 'unknown'))


if __name__ == '__main__':
    bot = MentorMatchBot()
    bot.run()


