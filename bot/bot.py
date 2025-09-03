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
            raise ValueError('TELEGRAM_BOT_TOKEN не задан в окружении')
        self.server_url = os.getenv('SERVER_URL', 'http://localhost:8000')
        self.app = Application.builder().token(token).build()
        self._setup_handlers()

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

        # Profiles
        self.app.add_handler(CallbackQueryHandler(self.cb_view_student, pattern=r'^student_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_view_supervisor, pattern=r'^supervisor_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_view_topic, pattern=r'^topic_\d+$'))

        # Matching actions
        self.app.add_handler(CallbackQueryHandler(self.cb_match_student, pattern=r'^match_student_\d+$'))
        self.app.add_handler(CallbackQueryHandler(self.cb_match_supervisor, pattern=r'^match_supervisor_\d+$'))

        # Back to main
        self.app.add_handler(CallbackQueryHandler(self.cb_back, pattern=r'^back_to_main$'))
        # Error handler
        self.app.add_error_handler(self.on_error)
        # Text input handler for simple add flows
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

    # Compatibility wrapper used by handler registration
    async def cmd_start2(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await self.cmd_start(update, context)

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

    async def _api_post(self, path: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f'{self.server_url}{path}'
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=data, timeout=60) as r:
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
        kb = [
            [InlineKeyboardButton('👨‍🎓 Студенты', callback_data='list_students')],
            [InlineKeyboardButton('🧑‍🏫 Научные руководители', callback_data='list_supervisors')],
            [InlineKeyboardButton('📚 Темы', callback_data='list_topics')],
        ]
        text = 'Выберите раздел:'
        if update.message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
        elif update.callback_query:
            # Возврат из callback — редактируем текущее сообщение
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text('Разделы: Студенты, Научные руководители, Темы. В профиле студента — кнопка Подобрать тему. В профиле темы (где нужен научный руководитель) — Подобрать научного руководителя.')

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
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

    async def cb_list_supervisors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['Научные руководители:']
        kb: List[List[InlineKeyboardButton]] = []
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton(s.get('full_name','–')[:30], callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

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
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

    # Profiles
    async def cb_view_student(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        sid = int(q.data.split('_')[1])
        s = await self._api_get(f'/api/students/{sid}')
        if not s:
            await q.edit_message_text('Не удалось загрузить профиль студента')
            return
        text = (
            f"Студент: {s.get('full_name','–')}\n"
            f"Username: {s.get('username') or '–'}\n"
            f"Email: {s.get('email') or '–'}\n"
            f"Направление: {s.get('program') or '–'}\n"
            f"Навыки: {s.get('skills') or '–'}\n"
            f"Интересы: {s.get('interests') or '–'}\n"
            f"CV: {(s.get('cv') or '–')[:200]}\n"
            f"ID: {s.get('id')}\n"
        )
        kb = [
            [InlineKeyboardButton('🧠 Подобрать тему', callback_data=f'match_student_{sid}')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')],
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    async def cb_view_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        uid = int(q.data.split('_')[1])
        s = await self._api_get(f'/api/supervisors/{uid}')
        if not s:
            await q.edit_message_text('Не удалось загрузить профиль научного руководителя')
            return
        text = (
            f"Научный руководитель: {s.get('full_name','–')}\n"
            f"Username: {s.get('username') or '–'}\n"
            f"Email: {s.get('email') or '–'}\n"
            f"Должность: {s.get('position') or '–'}\n"
            f"Степень: {s.get('degree') or '–'}\n"
            f"Вместимость: {s.get('capacity') or '–'}\n"
            f"Интересы: {s.get('interests') or '–'}\n"
            f"ID: {s.get('id')}\n"
        )
        kb = [[InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    async def cb_view_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        tid = int(q.data.split('_')[1])
        t = await self._api_get(f'/api/topics/{tid}')
        if not t:
            await q.edit_message_text('Не удалось загрузить тему')
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
        kb: List[List[InlineKeyboardButton]] = []
        if role == 'supervisor':
            kb.append([InlineKeyboardButton('🧑‍🏫 Подобрать научного руководителя', callback_data=f'match_supervisor_{tid}')])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # Matching
    async def cb_match_student(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        sid = int(q.data.split('_')[2])
        res = await self._api_post('/match-student', data={'student_user_id': sid})
        if not res or res.get('status') != 'ok':
            await q.edit_message_text('Ошибка подбора темы для студента')
            return
        items = res.get('items', [])
        lines = [f'Топ‑5 тем для студента #{sid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('title','–')} — {it.get('reason','')}")
        kb = [[InlineKeyboardButton('⬅️ К студенту', callback_data=f'student_{sid}')]]
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

    # Import students from Google Sheets
    async def cb_import_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        cfg = await self._api_get('/api/sheets-config')
        if not cfg or cfg.get('status') != 'configured':
            text = 'Google Sheets не настроен. Укажите SPREADSHEET_ID и SERVICE_ACCOUNT_FILE на сервере.'
            kb = [[InlineKeyboardButton('👨‍🎓 К студентам', callback_data='list_students')]]
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            return
        sid = cfg.get('spreadsheet_id')
        res = await self._api_post('/api/import-sheet', data={'spreadsheet_id': sid})
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
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

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
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

    async def cb_list_supervisors_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/supervisors?limit=10') or []
        lines: List[str] = ['Научные руководители:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('➕ Научный руководитель', callback_data='add_supervisor')]]
        for s in data:
            lines.append(f"• {s.get('full_name','–')} (id={s.get('id')})")
            kb.append([InlineKeyboardButton((s.get('full_name','–')[:30]), callback_data=f"supervisor_{s.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

    async def cb_list_topics_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        data = await self._api_get('/api/topics?limit=10') or []
        lines: List[str] = ['Темы:']
        kb: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton('➕ Тема', callback_data='add_topic')]]
        for t in data:
            lines.append(f"• {t.get('title','–')} (id={t.get('id')})")
            kb.append([InlineKeyboardButton(((t.get('title') or '–')[:30]), callback_data=f"topic_{t.get('id')}")])
        kb.append([InlineKeyboardButton('⬅️ Назад', callback_data='back_to_main')])
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

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
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

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
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

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
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

    # Add flows (simple)
    async def cb_add_student_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        text = 'Добавление студентов выполняется через Google форму и импорт в админке.'
        kb = [[InlineKeyboardButton('👨‍🎓 К студентам', callback_data='list_students')]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    async def cb_add_supervisor_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        context.user_data['awaiting'] = 'add_supervisor_name'
        await q.edit_message_text('Введите ФИО научного руководителя сообщением. Для отмены — /start')

    async def cb_add_topic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        kb = [
            [InlineKeyboardButton('🎓 Ищу студента', callback_data='add_topic_role_student')],
            [InlineKeyboardButton('🧑‍🏫 Ищу научного руководителя', callback_data='add_topic_role_supervisor')],
            [InlineKeyboardButton('📚 К темам', callback_data='list_topics')],
        ]
        await q.edit_message_text('Выберите, кого ищет тема:', reply_markup=InlineKeyboardMarkup(kb))

    async def cb_add_topic_choose(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        role = 'student' if q.data.endswith('_student') else 'supervisor'
        context.user_data['awaiting'] = 'add_topic_title'
        context.user_data['topic_role'] = role
        await q.edit_message_text('Введите название темы сообщением. Для отмены — /start')

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
                await update.message.reply_text('Научный руководитель добавлен.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🧑‍🏫 К научным руководителям', callback_data='list_supervisors')]]))
            else:
                await update.message.reply_text('Не удалось добавить научного руководителя. Попробуйте ещё раз или используйте веб-админку.')
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
                await update.message.reply_text('Тема добавлена.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📚 К темам', callback_data='list_topics')]]))
            else:
                await update.message.reply_text('Не удалось добавить тему. Попробуйте ещё раз или используйте веб-админку.')

    async def cb_match_supervisor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        tid = int(q.data.split('_')[2])
        res = await self._api_post('/match-topic', data={'topic_id': tid, 'target_role': 'supervisor'})
        if not res or res.get('status') not in ('ok', 'success'):
            await q.edit_message_text('Ошибка подбора руководителя для темы')
            return
        items = res.get('items', [])
        lines = [f'Топ‑5 руководителей для темы #{tid}:']
        for it in items:
            lines.append(f"#{it.get('rank')}. {it.get('full_name','–')} — {it.get('reason','')}")
        kb = [[InlineKeyboardButton('⬅️ К теме', callback_data=f'topic_{tid}')]]
        await q.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup(kb))

    # Back
    async def cb_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        await self.cmd_start(update, context)

    # Global error handler (чтобы не сыпались stacktrace в логи без обработки)
    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception('Bot error: %s', getattr(context, 'error', 'unknown'))


if __name__ == '__main__':
    bot = MentorMatchBot()
    bot.run()
