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
        """Best-effort fix for mojibake like 'Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚Ñ‹' -> 'Студенты'.
        It converts strings that look like UTF‑8 decoded as cp1252 back to UTF‑8.
        Safe for normal ASCII/English; applied only when suspicious patterns found.
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
            await q.edit_message_text(self._fix_text('Invalid role id'))
            return
        r = await self._api_get(f'/api/roles/{rid}')
        if not r:
            await q.edit_message_text(self._fix_text('Role not found'))
            return
        lines: List[str] = [
            f"Role: {r.get('name') or ''}",
            f"Topic: {r.get('topic_title') or ''}",
            f"Description: {(r.get('description') or '')[:500]}",
            f"Required skills: {r.get('required_skills') or ''}",
            f"Capacity: {r.get('capacity') or ''}",
            f"ID: {r.get('id')}",
        ]
        # Candidates
        candidates = await self._api_get(f"/api/role-candidates/{rid}") or []
        if candidates:
            lines.append('')
            lines.append('Top candidates:')
            for it in candidates:
                uname = it.get('username')
                uname_str = f" ({uname})" if uname else ""
                lines.append(f"#{it.get('rank')}. {it.get('full_name','')}" + uname_str + f" (score={it.get('score')})")
        text = '\n'.join(lines)
        kb: List[List[InlineKeyboardButton]] = [
            [InlineKeyboardButton('Match students', callback_data=f'match_role_{rid}')]
        ]
        topic_id = r.get('topic_id')
        if topic_id:
            kb.append([InlineKeyboardButton('Back to topic', callback_data=f'topic_{topic_id}')])
        kb.append([InlineKeyboardButton('Back', callback_data='back_to_main')])
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
        # Admins: ÑÑ‚Ð°Ñ€Ð¾Ðµ Ð¼ÐµÐ½ÑŽ Ñ†ÐµÐ»Ð¸ÐºÐ¾Ð¼
        if self._is_admin(update):
            kb = [
                [InlineKeyboardButton('ðŸ‘¨â€ðŸŽ“ Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚Ñ‹', callback_data='list_students')],
                [InlineKeyboardButton('ðŸ§‘â€ðŸ« ÐÐ°ÑƒÑ‡Ð½Ñ‹Ðµ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ð¸', callback_data='list_supervisors')],
                [InlineKeyboardButton('ðŸ“š Ð¢ÐµÐ¼Ñ‹', callback_data='list_topics')],
            ]
            text = 'ÐÐ´Ð¼Ð¸Ð½â€‘Ð¼ÐµÐ½ÑŽ: Ð²Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ñ€Ð°Ð·Ð´ÐµÐ»'
            if update.message:
                await update.message.reply_text(self._fix_text(text), reply_markup=self._mk(kb))
            elif update.callback_query:
                await update.callback_query.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
            return

        # Ð˜Ð´ÐµÐ½Ñ‚Ð¸Ñ„Ð¸ÐºÐ°Ñ†Ð¸Ñ Ð¿Ð¾ Telegram
        u = update.effective_user
        tg_id = getattr(u, 'id', None)
        uname = getattr(u, 'username', None)
        who = await self._api_get(f"/api/whoami?tg_id={tg_id or ''}&username={uname or ''}") or {}
        matches = who.get('matches') or []
        if not matches:
            # ÐÐµ Ð½Ð°ÑˆÐ»Ð¸ â€” ÑÐ¿Ñ€Ð¾ÑÐ¸Ð¼ Ñ€Ð¾Ð»ÑŒ
            text = 'ÐœÑ‹ Ð½Ðµ Ð½Ð°ÑˆÐ»Ð¸ Ð²Ð°ÑˆÑƒ Ð·Ð°Ð¿Ð¸ÑÑŒ Ð¸Ð· Ñ„Ð¾Ñ€Ð¼Ñ‹. Ð’Ñ‹ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚ Ð¸Ð»Ð¸ Ð½Ð°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ?'
            kb = [
                [InlineKeyboardButton('ðŸ‘¨â€ðŸŽ“ Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚', callback_data='register_role_student')],
                [InlineKeyboardButton('ðŸ§‘â€ðŸ« ÐÐ°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ', callback_data='register_role_supervisor')],
            ]
            if update.message:
                await update.message.reply_text(self._fix_text(text), reply_markup=self._mk(kb))
            else:
                await update.callback_query.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))
            return

        # Ð•ÑÑ‚ÑŒ ÑÐ¾Ð²Ð¿Ð°Ð´ÐµÐ½Ð¸Ñ â€” Ð¿Ñ€ÐµÐ´Ð»Ð¾Ð¶Ð¸Ð¼ Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð´Ð¸Ñ‚ÑŒ
        lines = ['ÐÐ°Ð¹Ð´ÐµÐ½Ñ‹ Ð·Ð°Ð¿Ð¸ÑÐ¸. Ð­Ñ‚Ð¾ Ð²Ñ‹?']
        kb: List[List[InlineKeyboardButton]] = []
        for m in matches:
            uid = m.get('id')
            fn = m.get('full_name')
            role = m.get('role')
            lines.append(f"â€¢ {fn} â€” {role} (id={uid})")
            kb.append([InlineKeyboardButton(f"Ð”Ð°, Ñ: {fn}", callback_data=f"confirm_me_{uid}")])
        kb.append([InlineKeyboardButton('ÐÐµÑ‚, ÑÑ‚Ð¾ Ð½Ðµ Ñ', callback_data='not_me')])
        text = '\n'.join(lines)
        if update.message:
            await update.message.reply_text(self._fix_text(text), reply_markup=self._mk(kb))
        else:
            await update.callback_query.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._fix_text('Ð Ð°Ð·Ð´ÐµÐ»Ñ‹: Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚Ñ‹, ÐÐ°ÑƒÑ‡Ð½Ñ‹Ðµ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ð¸, Ð¢ÐµÐ¼Ñ‹. Ð’ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»Ðµ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð° â€” ÐºÐ½Ð¾Ð¿ÐºÐ° ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ. Ð’ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»Ðµ Ñ‚ÐµÐ¼Ñ‹ (Ð³Ð´Ðµ Ð½ÑƒÐ¶ÐµÐ½ Ð½Ð°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ) â€” ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ Ð½Ð°ÑƒÑ‡Ð½Ð¾Ð³Ð¾ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ.'))

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
            [InlineKeyboardButton('ðŸ‘¨â€ðŸŽ“ Ð¯ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚', callback_data='register_role_student')],
            [InlineKeyboardButton('ðŸ§‘â€ðŸ« Ð¯ Ð½Ð°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ', callback_data='register_role_supervisor')],
        ]
        await q.edit_message_text(self._fix_text('Ð’Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ñ€Ð¾Ð»ÑŒ Ð´Ð»Ñ Ñ€ÐµÐ³Ð¸ÑÑ‚Ñ€Ð°Ñ†Ð¸Ð¸:'), reply_markup=self._mk(kb))

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
            await q.edit_message_text(self._fix_text('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ñ€ÐµÐ³Ð¸ÑÑ‚Ñ€Ð¸Ñ€Ð¾Ð²Ð°Ñ‚ÑŒÑÑ. ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ Ð¿Ð¾Ð·Ð¶Ðµ.'))
            return
        context.user_data['uid'] = int(res.get('user_id'))
        context.user_data['role'] = res.get('role')
        await self._show_role_menu(update, context)

    async def _show_role_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        role = context.user_data.get('role')
        uid = context.user_data.get('uid')
        if role == 'student':
            kb = [
                [InlineKeyboardButton('ðŸ‘¤ ÐœÐ¾Ð¹ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑŒ', callback_data='student_me')],
                [InlineKeyboardButton('ðŸ“š ÐœÐ¾Ð¸ Ñ‚ÐµÐ¼Ñ‹', callback_data='my_topics')],
                [InlineKeyboardButton('âž• Ð”Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ', callback_data='add_topic')],
                [InlineKeyboardButton('ðŸ§  ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ Ñ€Ð¾Ð»Ð¸ Ð´Ð»Ñ Ð¼ÐµÐ½Ñ', callback_data=f'match_student_{uid}')],
            ]
            text = 'Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚: Ð²Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ'
        else:
            kb = [
                [InlineKeyboardButton('ðŸ‘¤ ÐœÐ¾Ð¹ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑŒ', callback_data='supervisor_me')],
                [InlineKeyboardButton('ðŸ“š ÐœÐ¾Ð¸ Ñ‚ÐµÐ¼Ñ‹', callback_data='my_topics')],
                [InlineKeyboardButton('âž• Ð”Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ', callback_data='add_topic')],
                [InlineKeyboardButton('ðŸ§  ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñ‹ Ð´Ð»Ñ Ð¼ÐµÐ½Ñ', callback_data='match_topics_for_me')],
            ]
            text = 'ÐÐ°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ: Ð²Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ'
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
        lines = ['ÐœÐ¾Ð¸ Ñ‚ÐµÐ¼Ñ‹:']
        kb: List[List[InlineKeyboardButton]] = []
        for t in data:
            title = (t.get('title') or 'â€“')[:40]
            kb.append([InlineKeyboardButton(title, callback_data=f'topic_{t.get("id")}')])
            kb.append([InlineKeyboardButton('ðŸ‘¨â€ðŸŽ“ ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð¾Ð²', callback_data=f'match_students_topic_{t.get("id")}')])
        if not kb:
            lines.append('â€” Ð¿Ð¾ÐºÐ° Ð½ÐµÑ‚ Ñ‚ÐµÐ¼ â€”')
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
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
        lines: List[str] = ['Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚Ñ‹:']
        kb: List[List[InlineKeyboardButton]] = []
        for s in data:
            lines.append(f"â€¢ {s.get('full_name','â€“')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton(s.get('full_name','â€“')[:30], callback_data=f"student_{s.get('id')}")])
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_supervisors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['ÐÐ°ÑƒÑ‡Ð½Ñ‹Ðµ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ð¸:']
        kb: List[List[InlineKeyboardButton]] = []
        for s in data:
            lines.append(f"â€¢ {s.get('full_name','â€“')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton(s.get('full_name','â€“')[:30], callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/topics?limit=10') or []
        lines: List[str] = ['Ð¢ÐµÐ¼Ñ‹:']
        kb: List[List[InlineKeyboardButton]] = []
        for t in data:
            title = (t.get('title') or 'â€“')[:30]
            lines.append(f"â€¢ {t.get('title','â€“')} (id={t.get('id')})")
            kb.append([InlineKeyboardButton(title, callback_data=f"topic_{t.get('id')}")])
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
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
            await q.edit_message_text(self._fix_text('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð³Ñ€ÑƒÐ·Ð¸Ñ‚ÑŒ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑŒ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°'))
            return
        # Header
        lines = [
            f"Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚: {s.get('full_name','â€“')}",
            f"Username: {s.get('username') or 'â€“'}",
            f"Email: {s.get('email') or 'â€“'}",
            f"ÐÐ°Ð¿Ñ€Ð°Ð²Ð»ÐµÐ½Ð¸Ðµ: {s.get('program') or 'â€“'}",
            f"ÐÐ°Ð²Ñ‹ÐºÐ¸: {s.get('skills') or 'â€“'}",
            f"Ð˜Ð½Ñ‚ÐµÑ€ÐµÑÑ‹: {s.get('interests') or 'â€“'}",
            f"CV: {(s.get('cv') or 'â€“')[:200]}",
            f"ID: {s.get('id')}",
        ]
        # Existing recommendations from DB
        rec = await self._api_get(f'/api/user-candidates/{sid}?limit=5') or []
        if rec:
            lines.append('')
            # Back-compat: endpoint Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ Ñ€Ð¾Ð»Ð¸ Ð´Ð»Ñ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°
            if rec and 'role_name' in (rec[0] or {}):
                lines.append('Ð ÐµÐºÐ¾Ð¼ÐµÐ½Ð´Ð¾Ð²Ð°Ð½Ð½Ñ‹Ðµ Ñ€Ð¾Ð»Ð¸:')
                for it in rec:
                    lines.append(f"â€¢ #{it.get('rank')}. {it.get('role_name','â€“')} â€” {it.get('topic_title','â€“')} (score={it.get('score')})")
            else:
                lines.append('Ð ÐµÐºÐ¾Ð¼ÐµÐ½Ð´Ð¾Ð²Ð°Ð½Ð½Ñ‹Ðµ Ñ‚ÐµÐ¼Ñ‹:')
                for it in rec:
                    lines.append(f"â€¢ #{it.get('rank')}. {it.get('title','â€“')} (score={it.get('score')})")
        text = '\n'.join(lines)
        kb = [
            [InlineKeyboardButton('ðŸ§  ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ Ñ€Ð¾Ð»ÑŒ', callback_data=f'match_student_{sid}')],
            [InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')],
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
            await q.edit_message_text(self._fix_text('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð³Ñ€ÑƒÐ·Ð¸Ñ‚ÑŒ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑŒ Ð½Ð°ÑƒÑ‡Ð½Ð¾Ð³Ð¾ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ'))
            return
        lines = [
            f"ÐÐ°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ: {s.get('full_name','â€“')}",
            f"Username: {s.get('username') or 'â€“'}",
            f"Email: {s.get('email') or 'â€“'}",
            f"Ð”Ð¾Ð»Ð¶Ð½Ð¾ÑÑ‚ÑŒ: {s.get('position') or 'â€“'}",
            f"Ð¡Ñ‚ÐµÐ¿ÐµÐ½ÑŒ: {s.get('degree') or 'â€“'}",
            f"Ð’Ð¼ÐµÑÑ‚Ð¸Ð¼Ð¾ÑÑ‚ÑŒ: {s.get('capacity') or 'â€“'}",
            f"Ð˜Ð½Ñ‚ÐµÑ€ÐµÑÑ‹: {s.get('interests') or 'â€“'}",
            f"ID: {s.get('id')}",
        ]
        rec = await self._api_get(f'/api/user-candidates/{uid}?limit=5') or []
        if rec:
            lines.append('')
            lines.append('ÐŸÐ¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ðµ Ñ‚ÐµÐ¼Ñ‹:')
            for it in rec:
                lines.append(f"â€¢ #{it.get('rank')}. {it.get('title','â€“')} (score={it.get('score')})")
        text = '\n'.join(lines)
        kb = [
            [InlineKeyboardButton('ðŸ§  ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ', callback_data=f'match_topics_for_supervisor_{uid}')],
            [InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')],
        ]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_view_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        tid = int(q.data.split('_')[1])
        t = await self._api_get(f'/api/topics/{tid}')
        if not t:
            await q.edit_message_text(self._fix_text('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð³Ñ€ÑƒÐ·Ð¸Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ'))
            return
        role = t.get('seeking_role')
        text = (
            f"Ð¢ÐµÐ¼Ð°: {t.get('title','â€“')}\n"
            f"ÐÐ²Ñ‚Ð¾Ñ€: {t.get('author','â€“')}\n"
            f"ÐšÐ¾Ð³Ð¾ Ð¸Ñ‰ÐµÐ¼: {role}\n"
            f"ÐžÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ: {(t.get('description') or 'â€“')[:500]}\n"
            f"ÐžÐ¶Ð¸Ð´Ð°ÐµÐ¼Ñ‹Ðµ Ñ€ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚Ñ‹: {(t.get('expected_outcomes') or 'â€“')[:400]}\n"
            f"Ð¢Ñ€ÐµÐ±ÑƒÐµÐ¼Ñ‹Ðµ Ð½Ð°Ð²Ñ‹ÐºÐ¸: {t.get('required_skills') or 'â€“'}\n"
            f"ID: {t.get('id')}\n"
        )
        # Roles for this topic
        roles = await self._api_get(f'/api/topics/{tid}/roles') or []
        lines2: List[str] = [text, '', 'Ð Ð¾Ð»Ð¸:']
        kb: List[List[InlineKeyboardButton]] = []
        for r in roles:
            name = (r.get('name') or 'â€“')[:40]
            lines2.append(f"â€¢ {name} (role_id={r.get('id')})")
            kb.append([InlineKeyboardButton(f"ðŸŽ­ {name}", callback_data=f"role_{r.get('id')}")])
        if not roles:
            lines2.append('â€” Ð½ÐµÑ‚ Ñ€Ð¾Ð»ÐµÐ¹ â€”')
        kb.append([InlineKeyboardButton('ðŸ§‘â€ðŸ« ÐŸÐ¾Ð´Ð¾Ð±Ñ€Ð°Ñ‚ÑŒ Ð½Ð°ÑƒÑ‡Ð½Ð¾Ð³Ð¾ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ', callback_data=f'match_supervisor_{tid}')])
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines2)), reply_markup=self._mk(kb))

    # Matching
    async def cb_match_student(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        sid = int(q.data.split('_')[2])
        res = await self._api_post('/match-student', data={'student_user_id': sid})
        if not res or res.get('status') != 'ok':
            await q.edit_message_text(self._fix_text('ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ð¾Ð´Ð±Ð¾Ñ€Ð° Ñ€Ð¾Ð»ÐµÐ¹ Ð´Ð»Ñ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°'))
            return
        items = res.get('items', [])
        lines = [f'Ð¢Ð¾Ð¿â€‘5 Ñ€Ð¾Ð»ÐµÐ¹ Ð´Ð»Ñ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð° #{sid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('role_name','â€“')} â€” {it.get('topic_title','â€“')} â€” {it.get('reason','')}")
        kb = [[InlineKeyboardButton('â¬…ï¸ Ðš ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ñƒ', callback_data=f'student_{sid}')]]
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Import students from Google Sheets
    async def cb_import_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        cfg = await self._api_get('/api/sheets-config')
        if not cfg or cfg.get('status') != 'configured':
            text = 'Google Sheets Ð½Ðµ Ð½Ð°ÑÑ‚Ñ€Ð¾ÐµÐ½. Ð£ÐºÐ°Ð¶Ð¸Ñ‚Ðµ SPREADSHEET_ID Ð¸ SERVICE_ACCOUNT_FILE Ð½Ð° ÑÐµÑ€Ð²ÐµÑ€Ðµ.'
            kb = [[InlineKeyboardButton('ðŸ‘¨â€ðŸŽ“ Ðš ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°Ð¼', callback_data='list_students')]]
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
            msg = (res or {}).get('message') or 'ÐžÑˆÐ¸Ð±ÐºÐ° Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚Ð°'
            text = f'âŒ Ð˜Ð¼Ð¿Ð¾Ñ€Ñ‚ Ð½Ðµ Ð²Ñ‹Ð¿Ð¾Ð»Ð½ÐµÐ½: {msg}'
        else:
            stats = res.get('stats', {})
            text = (
                'âœ… Ð˜Ð¼Ð¿Ð¾Ñ€Ñ‚ Ð²Ñ‹Ð¿Ð¾Ð»Ð½ÐµÐ½.\n'
                f"ÐŸÐ¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»Ð¸: +{stats.get('inserted_users', 0)}\n"
                f"ÐŸÑ€Ð¾Ñ„Ð¸Ð»Ð¸: +{stats.get('inserted_profiles', stats.get('upserted_profiles', 0))}\n"
                f"Ð¢ÐµÐ¼Ñ‹: +{stats.get('inserted_topics', 0)}"
            )
        kb = [[InlineKeyboardButton('ðŸ‘¨â€ðŸŽ“ Ðš ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°Ð¼', callback_data='list_students')]]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    # List menus with add buttons (new handlers)
    async def cb_list_students_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/students?limit=10') or []
        lines: List[str] = ['Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚Ñ‹:']
        kb: List[List[InlineKeyboardButton]] = [
            [InlineKeyboardButton('âž• Ð”Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°', callback_data='add_student')],
            [InlineKeyboardButton('ðŸ“¥ Ð˜Ð¼Ð¿Ð¾Ñ€Ñ‚ Ð¸Ð· Google-Ñ‚Ð°Ð±Ð»Ð¸Ñ†', callback_data='import_students')],
        ]
        for s in data:
            lines.append(f"â€¢ {s.get('full_name','â€“')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','â€“')[:30]), callback_data=f"student_{s.get('id')}")])
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_supervisors_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['ÐÐ°ÑƒÑ‡Ð½Ñ‹Ðµ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ð¸:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('âž• ÐÐ°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ', callback_data='add_supervisor')]]
        for s in data:
            lines.append(f"â€¢ {s.get('full_name','â€“')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','â€“')[:30]), callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_list_topics_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/topics?limit=10') or []
        lines: List[str] = ['Ð¢ÐµÐ¼Ñ‹:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('âž• Ð¢ÐµÐ¼Ð°', callback_data='add_topic')]]
        for t in data:
            lines.append(f"â€¢ {t.get('title','â€“')} (id={t.get('id')})")
            kb.append([InlineKeyboardButton(((t.get('title') or 'â€“')[:30]), callback_data=f"topic_{t.get('id')}")])
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
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
        lines: List[str] = ['Ð¡Ñ‚ÑƒÐ´ÐµÐ½Ñ‚Ñ‹:']
        kb: List[List[InlineKeyboardButton]] = [
            [InlineKeyboardButton('âž• Ð”Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°', callback_data='add_student')],
            [InlineKeyboardButton('ðŸ“¥ Ð˜Ð¼Ð¿Ð¾Ñ€Ñ‚ Ð¸Ð· Google-Ñ‚Ð°Ð±Ð»Ð¸Ñ†', callback_data='import_students')],
        ]
        for s in data:
            lines.append(f"â€¢ {s.get('full_name','â€“')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','â€“')[:30]), callback_data=f"student_{s.get('id')}")])
        nav: List[InlineKeyboardButton] = []
        if offset > 0:
            prev_off = max(0, offset - limit)
            nav.append(InlineKeyboardButton('â—€ï¸', callback_data=f'list_students_{prev_off}'))
        if len(data) == limit:
            next_off = offset + limit
            nav.append(InlineKeyboardButton('â–¶ï¸', callback_data=f'list_students_{next_off}'))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
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
        lines: List[str] = ['ÐÐ°ÑƒÑ‡Ð½Ñ‹Ðµ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ð¸:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('âž• ÐÐ°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ', callback_data='add_supervisor')]]
        for s in data:
            lines.append(f"â€¢ {s.get('full_name','â€“')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','â€“')[:30]), callback_data=f"supervisor_{s.get('id')}")])
        nav: List[InlineKeyboardButton] = []
        if offset > 0:
            prev_off = max(0, offset - limit)
            nav.append(InlineKeyboardButton('â—€ï¸', callback_data=f'list_supervisors_{prev_off}'))
        if len(data) == limit:
            next_off = offset + limit
            nav.append(InlineKeyboardButton('â–¶ï¸', callback_data=f'list_supervisors_{next_off}'))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
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
        lines: List[str] = ['Ð¢ÐµÐ¼Ñ‹:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('âž• Ð¢ÐµÐ¼Ð°', callback_data='add_topic')]]
        for t in data:
            title = (t.get('title') or 'â€“')[:30]
            lines.append(f"â€¢ {t.get('title','â€“')} (id={t.get('id')})")
            kb.append([InlineKeyboardButton(title, callback_data=f"topic_{t.get('id')}")])
        nav: List[InlineKeyboardButton] = []
        if offset > 0:
            prev_off = max(0, offset - limit)
            nav.append(InlineKeyboardButton('â—€ï¸', callback_data=f'list_topics_{prev_off}'))
        if len(data) == limit:
            next_off = offset + limit
            nav.append(InlineKeyboardButton('â–¶ï¸', callback_data=f'list_topics_{next_off}'))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton('â¬…ï¸ ÐÐ°Ð·Ð°Ð´', callback_data='back_to_main')])
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Add flows (simple)
    async def cb_add_student_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        text = 'Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¸Ðµ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð¾Ð² Ð²Ñ‹Ð¿Ð¾Ð»Ð½ÑÐµÑ‚ÑÑ Ñ‡ÐµÑ€ÐµÐ· Google Ñ„Ð¾Ñ€Ð¼Ñƒ Ð¸ Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚ Ð² Ð°Ð´Ð¼Ð¸Ð½ÐºÐµ.'
        kb = [[InlineKeyboardButton('ðŸ‘¨â€ðŸŽ“ Ðš ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°Ð¼', callback_data='list_students')]]
        await q.edit_message_text(self._fix_text(text), reply_markup=self._mk(kb))

    async def cb_add_supervisor_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        context.user_data['awaiting'] = 'add_supervisor_name'
        await q.edit_message_text(self._fix_text('Ð’Ð²ÐµÐ´Ð¸Ñ‚Ðµ Ð¤Ð˜Ðž Ð½Ð°ÑƒÑ‡Ð½Ð¾Ð³Ð¾ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÐµÐ¼. Ð”Ð»Ñ Ð¾Ñ‚Ð¼ÐµÐ½Ñ‹ â€” /start'))

    async def cb_add_topic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        kb = [
            [InlineKeyboardButton('ðŸŽ“ Ð˜Ñ‰Ñƒ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°', callback_data='add_topic_role_student')],
            [InlineKeyboardButton('ðŸ§‘â€ðŸ« Ð˜Ñ‰Ñƒ Ð½Ð°ÑƒÑ‡Ð½Ð¾Ð³Ð¾ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ', callback_data='add_topic_role_supervisor')],
            [InlineKeyboardButton('ðŸ“š Ðš Ñ‚ÐµÐ¼Ð°Ð¼', callback_data='list_topics')],
        ]
        await q.edit_message_text(self._fix_text('Ð’Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ, ÐºÐ¾Ð³Ð¾ Ð¸Ñ‰ÐµÑ‚ Ñ‚ÐµÐ¼Ð°:'), reply_markup=self._mk(kb))

    async def cb_add_topic_choose(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        role = 'student' if q.data.endswith('_student') else 'supervisor'
        context.user_data['awaiting'] = 'add_topic_title'
        context.user_data['topic_role'] = role
        await q.edit_message_text(self._fix_text('Ð’Ð²ÐµÐ´Ð¸Ñ‚Ðµ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ Ñ‚ÐµÐ¼Ñ‹ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÐµÐ¼. Ð”Ð»Ñ Ð¾Ñ‚Ð¼ÐµÐ½Ñ‹ â€” /start'))

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
                await update.message.reply_text(self._fix_text('ÐÐ°ÑƒÑ‡Ð½Ñ‹Ð¹ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½.'), reply_markup=self._mk([[InlineKeyboardButton('ðŸ§‘â€ðŸ« Ðš Ð½Ð°ÑƒÑ‡Ð½Ñ‹Ð¼ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑÐ¼', callback_data='list_supervisors')]]))
            else:
                await update.message.reply_text(self._fix_text('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ð½Ð°ÑƒÑ‡Ð½Ð¾Ð³Ð¾ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ. ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ ÐµÑ‰Ñ‘ Ñ€Ð°Ð· Ð¸Ð»Ð¸ Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐ¹Ñ‚Ðµ Ð²ÐµÐ±-Ð°Ð´Ð¼Ð¸Ð½ÐºÑƒ.'))
        elif awaiting == 'add_topic_title':
            role = context.user_data.get('topic_role') or 'student'
            payload = {
                'title': text,
                'seeking_role': role,
                'author_full_name': (getattr(update.effective_user, 'full_name', None) or 'Unknown Supervisor'),
            }
            res = await self._api_post('/add-topic', data=payload)
            context.user_data['awaiting'] = None
            context.user_data.pop('topic_role', None)
            if res and res.get('status', 'success') in ('success', 'ok'):
                await update.message.reply_text(self._fix_text('Ð¢ÐµÐ¼Ð° Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð°.'), reply_markup=self._mk([[InlineKeyboardButton('ðŸ“š Ðš Ñ‚ÐµÐ¼Ð°Ð¼', callback_data='list_topics')]]))
            else:
                await update.message.reply_text(self._fix_text('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ. ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ ÐµÑ‰Ñ‘ Ñ€Ð°Ð· Ð¸Ð»Ð¸ Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐ¹Ñ‚Ðµ Ð²ÐµÐ±-Ð°Ð´Ð¼Ð¸Ð½ÐºÑƒ.'))

    async def cb_match_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        tid = int(q.data.split('_')[2])
        res = await self._api_post('/match-topic', data={'topic_id': tid, 'target_role': 'supervisor'})
        if not res or res.get('status') not in ('ok', 'success'):
            await q.edit_message_text(self._fix_text('ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ð¾Ð´Ð±Ð¾Ñ€Ð° Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ Ð´Ð»Ñ Ñ‚ÐµÐ¼Ñ‹'))
            return
        items = res.get('items', [])
        lines = [f'Ð¢Ð¾Ð¿â€‘5 Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÐµÐ¹ Ð´Ð»Ñ Ñ‚ÐµÐ¼Ñ‹ #{tid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('full_name','â€“')} â€” {it.get('reason','')}")
        kb = [[InlineKeyboardButton('â¬…ï¸ Ðš Ñ‚ÐµÐ¼Ðµ', callback_data=f'topic_{tid}')]]
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    async def cb_match_students_for_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Back-compat: Ð¿Ñ€ÐµÐ´Ð»Ð¾Ð¶Ð¸Ð¼ Ð²Ñ‹Ð±Ñ€Ð°Ñ‚ÑŒ Ñ€Ð¾Ð»ÑŒ
        q = update.callback_query; await q.answer()
        tid = int(q.data.rsplit('_', 1)[1])
        roles = await self._api_get(f'/api/topics/{tid}/roles') or []
        if not roles:
            await q.edit_message_text(self._fix_text('Ð”Ð»Ñ Ñ‚ÐµÐ¼Ñ‹ Ð½Ðµ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ñ‹ Ñ€Ð¾Ð»Ð¸. Ð”Ð¾Ð±Ð°Ð²ÑŒÑ‚Ðµ Ð¸Ñ… Ð² Ð°Ð´Ð¼Ð¸Ð½ÐºÐµ.'))
            return
        kb: List[List[InlineKeyboardButton]] = []
        for r in roles:
            kb.append([InlineKeyboardButton(f"ðŸŽ­ {r.get('name','â€“')}", callback_data=f"match_role_{r.get('id')}")])
        kb.append([InlineKeyboardButton('â¬…ï¸ Ðš Ñ‚ÐµÐ¼Ðµ', callback_data=f'topic_{tid}')])
        await q.edit_message_text(self._fix_text('Ð’Ñ‹Ð±ÐµÑ€Ð¸Ñ‚Ðµ Ñ€Ð¾Ð»ÑŒ Ð´Ð»Ñ Ð¿Ð¾Ð´Ð±Ð¾Ñ€Ð° ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð¾Ð²:'), reply_markup=self._mk(kb))

    async def cb_match_students_for_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        rid = int(q.data.rsplit('_', 1)[1])
        res = await self._api_post('/match-role', data={'role_id': rid})
        if not res or res.get('status') not in ('ok', 'success'):
            await q.edit_message_text(self._fix_text('ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ð¾Ð´Ð±Ð¾Ñ€Ð° ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð¾Ð² Ð´Ð»Ñ Ñ€Ð¾Ð»Ð¸'))
            return
        items = res.get('items', [])
        lines = [f'Ð¢Ð¾Ð¿â€‘5 ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð¾Ð² Ð´Ð»Ñ Ñ€Ð¾Ð»Ð¸ #{rid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('full_name','â€“')} â€” {it.get('reason','')}")
        kb = [[InlineKeyboardButton('â¬…ï¸ Ðš Ñ€Ð¾Ð»Ð¸', callback_data=f'role_{rid}')]]
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
            await q.edit_message_text(self._fix_text('ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ð¾Ð´Ð±Ð¾Ñ€Ð° Ñ‚ÐµÐ¼ Ð´Ð»Ñ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ'))
            return
        items = res.get('items', [])
        lines = [f'Ð¢Ð¾Ð¿â€‘5 Ñ‚ÐµÐ¼ Ð´Ð»Ñ Ñ€ÑƒÐºÐ¾Ð²Ð¾Ð´Ð¸Ñ‚ÐµÐ»Ñ #{uid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('title','â€“')} â€” {it.get('reason','')}")
        kb = [[InlineKeyboardButton('â¬…ï¸ Ðš Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑŽ', callback_data=f'supervisor_{uid}')]]
        await q.edit_message_text(self._fix_text('\n'.join(lines)), reply_markup=self._mk(kb))

    # Back
    async def cb_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        await self.cmd_start(update, context)

    # Global error handler (Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ð½Ðµ ÑÑ‹Ð¿Ð°Ð»Ð¸ÑÑŒ stacktrace Ð² Ð»Ð¾Ð³Ð¸ Ð±ÐµÐ· Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚ÐºÐ¸)
    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception('Bot error: %s', getattr(context, 'error', 'unknown'))


if __name__ == '__main__':
    bot = MentorMatchBot()
    bot.run()


