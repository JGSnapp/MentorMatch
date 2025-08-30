import os
import logging
import aiohttp
import asyncio
from typing import Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
CHOOSING_ACTION, ADDING_TOPIC, ADDING_SUPERVISOR, IMPORTING_SHEET = range(4)

# Состояния для добавления темы
TOPIC_TITLE, TOPIC_DESCRIPTION, TOPIC_OUTCOMES, TOPIC_SKILLS, TOPIC_ROLE = range(4, 9)

# Состояния для добавления научрука
SUPERVISOR_NAME, SUPERVISOR_EMAIL, SUPERVISOR_USERNAME, SUPERVISOR_POSITION, SUPERVISOR_DEGREE, SUPERVISOR_CAPACITY, SUPERVISOR_REQUIREMENTS, SUPERVISOR_INTERESTS = range(9, 17)

class MentorMatchBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN не найден в переменных окружения")
        
        self.server_url = os.getenv('SERVER_URL', 'http://localhost:8000')
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        
        # Временное хранилище данных пользователей
        self.user_data: Dict[int, Dict[str, Any]] = {}
        
    async def api_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """Выполняет HTTP запрос к серверу"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.server_url}{endpoint}"
                
                if method.upper() == 'GET':
                    async with session.get(url) as response:
                        if response.status == 200:
                            return await response.json()
                        else:
                            logger.error(f"API GET error: {response.status}")
                            return None
                elif method.upper() == 'POST':
                    async with session.post(url, data=data) as response:
                        if response.status in [200, 303]:
                            return {'status': 'success'}
                        else:
                            logger.error(f"API POST error: {response.status}")
                            return None
        except Exception as e:
            logger.error(f"API request error: {e}")
            return None
    
    def setup_handlers(self):
        """Настраивает обработчики команд"""
        
        # Основные команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))
        
        # Conversation handler для добавления темы
        topic_conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_topic_start, pattern='^add_topic$')],
            states={
                TOPIC_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_topic_title)],
                TOPIC_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_topic_description)],
                TOPIC_OUTCOMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_topic_outcomes)],
                TOPIC_SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_topic_skills)],
                TOPIC_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_topic_role)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_command)],
        )
        self.application.add_handler(topic_conv_handler)
        
        # Conversation handler для добавления научрука
        supervisor_conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_supervisor_start, pattern='^add_supervisor$')],
            states={
                SUPERVISOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_name)],
                SUPERVISOR_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_email)],
                SUPERVISOR_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_username)],
                SUPERVISOR_POSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_position)],
                SUPERVISOR_DEGREE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_degree)],
                SUPERVISOR_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_capacity)],
                SUPERVISOR_REQUIREMENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_requirements)],
                SUPERVISOR_INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_supervisor_interests)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_command)],
        )
        self.application.add_handler(supervisor_conv_handler)
        
        # Обработчики callback кнопок
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Получаем последние темы с сервера
        topics_data = await self.api_request('GET', '/latest?kind=topics')
        
        keyboard = [
            [InlineKeyboardButton("📚 Добавить тему", callback_data="add_topic")],
            [InlineKeyboardButton("👨‍🏫 Добавить научрука", callback_data="add_supervisor")],
            [InlineKeyboardButton("🔍 Найти кандидатов", callback_data="find_candidates")],
            [InlineKeyboardButton("📊 Импорт из Google Sheets", callback_data="import_sheet")],
            [InlineKeyboardButton("👥 Просмотреть студентов", callback_data="view_students")],
            [InlineKeyboardButton("👨‍🏫 Просмотреть научруков", callback_data="view_supervisors")],
            [InlineKeyboardButton("📝 Просмотреть темы", callback_data="view_topics")],
        ]
        
        if topics_data:
            keyboard.append([InlineKeyboardButton("📋 Последние темы", callback_data="show_topics")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🤖 **Добро пожаловать в MentorMatch!**\n\n"
            "Выберите действие:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
        return CHOOSING_ACTION
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_text = (
            "📖 **Справка по командам:**\n\n"
            "/start - Главное меню\n"
            "/help - Эта справка\n"
            "/cancel - Отменить текущее действие\n\n"
            "**Возможности бота:**\n"
            "• Добавление тем исследований\n"
            "• Добавление научных руководителей\n"
            "• Поиск кандидатов по темам\n"
            "• Просмотр последних добавлений"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отменяет текущее действие"""
        user_id = update.effective_user.id
        
        # Очищаем данные пользователя
        if user_id in self.user_data:
            del self.user_data[user_id]
        
        await update.message.reply_text(
            "❌ Действие отменено. Используйте /start для возврата в главное меню.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        return ConversationHandler.END
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает callback кнопки"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "back_to_main":
            await self.start_command(update, context)
            return CHOOSING_ACTION
        elif query.data == "show_topics":
            await self.show_topics(update, context)
        elif query.data.startswith("topic_"):
            topic_id = int(query.data.split("_")[1])
            await self.show_topic_candidates(update, context, topic_id)
        elif query.data == "import_sheet":
            await self.import_sheet_info(update, context)
        elif query.data == "view_students":
            await self.view_students(update, context)
        elif query.data == "view_supervisors":
            await self.view_supervisors(update, context)
        elif query.data == "view_topics":
            await self.view_topics(update, context)
        else:
            await query.edit_message_text("❌ Неизвестное действие")
    
    async def show_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает последние темы"""
        query = update.callback_query
        
        topics_data = await self.api_request('GET', '/latest?kind=topics')
        
        if not topics_data:
            await query.edit_message_text("📝 Темы не найдены.")
            return
        
        text = "📚 **Последние темы:**\n\n"
        keyboard = []
        
        for topic in topics_data[:10]:
            role_text = "студента" if topic.get('seeking_role') == 'student' else "научрука"
            text += f"• **{topic.get('title', 'Без названия')}**\n"
            text += f"  👥 Ищем: {role_text}\n"
            text += f"  👤 Автор: {topic.get('author', 'Неизвестно')}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"🔍 {topic.get('title', 'Без названия')[:30]}...",
                    callback_data=f"topic_{topic.get('id')}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def show_topic_candidates(self, update: Update, context: ContextTypes.DEFAULT_TYPE, topic_id: int):
        """Показывает кандидатов для темы"""
        query = update.callback_query
        
        # Получаем матчинг с сервера
        match_data = await self.api_request('POST', '/match-topic', {
            'topic_id': str(topic_id),
            'target_role': 'student'
        })
        
        if not match_data or match_data.get('status') != 'ok':
            await query.edit_message_text("❌ Ошибка при поиске кандидатов.")
            return
        
        topic_title = match_data.get('topic_title', 'Неизвестная тема')
        items = match_data.get('items', [])
        
        text = f"🔍 **Кандидаты для темы:**\n**{topic_title}**\n\n"
        
        if not items:
            text += "📝 Кандидаты не найдены."
        else:
            for item in items:
                text += f"**{item.get('rank')}.** {item.get('full_name', 'Неизвестно')}\n"
                if item.get('reason'):
                    text += f"   💡 {item.get('reason')}\n"
                text += "\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад к темам", callback_data="show_topics")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def add_topic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начинает процесс добавления темы"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Инициализируем данные пользователя
        if user_id not in self.user_data:
            self.user_data[user_id] = {}
        self.user_data[user_id]['topic'] = {}
        
        await query.edit_message_text(
            "📚 **Добавление темы исследования**\n\n"
            "Введите название темы:",
            parse_mode='Markdown'
        )
        
        return TOPIC_TITLE
    
    async def get_topic_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает название темы"""
        user_id = update.effective_user.id
        title = update.message.text
        
        self.user_data[user_id]['topic']['title'] = title
        
        await update.message.reply_text(
            "📝 Введите описание темы:"
        )
        
        return TOPIC_DESCRIPTION
    
    async def get_topic_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает описание темы"""
        user_id = update.effective_user.id
        description = update.message.text
        
        self.user_data[user_id]['topic']['description'] = description
        
        await update.message.reply_text(
            "🎯 Введите ожидаемые результаты (или 'нет' для пропуска):"
        )
        
        return TOPIC_OUTCOMES
    
    async def get_topic_outcomes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает ожидаемые результаты"""
        user_id = update.effective_user.id
        outcomes = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['topic']['expected_outcomes'] = outcomes
        
        await update.message.reply_text(
            "🛠️ Введите требуемые навыки (или 'нет' для пропуска):"
        )
        
        return TOPIC_SKILLS
    
    async def get_topic_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает требуемые навыки"""
        user_id = update.effective_user.id
        skills = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['topic']['required_skills'] = skills
        
        keyboard = [
            [KeyboardButton("Студента")],
            [KeyboardButton("Научрука")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        
        await update.message.reply_text(
            "👥 Выберите, кого ищем для этой темы:",
            reply_markup=reply_markup
        )
        
        return TOPIC_ROLE
    
    async def get_topic_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает роль и сохраняет тему"""
        user_id = update.effective_user.id
        role_text = update.message.text
        
        seeking_role = 'student' if role_text == "Студента" else 'supervisor'
        
        # Отправляем данные на сервер
        topic_data = {
            'title': self.user_data[user_id]['topic']['title'],
            'description': self.user_data[user_id]['topic']['description'],
            'expected_outcomes': self.user_data[user_id]['topic']['expected_outcomes'],
            'required_skills': self.user_data[user_id]['topic']['required_skills'],
            'seeking_role': seeking_role,
            'author_full_name': f"User_{user_id}"  # Временное имя автора
        }
        
        result = await self.api_request('POST', '/add-topic', topic_data)
        
        if result and result.get('status') == 'success':
            await update.message.reply_text(
                f"✅ **Тема успешно добавлена!**\n\n"
                f"📚 Название: {self.user_data[user_id]['topic']['title']}\n"
                f"👥 Ищем: {seeking_role}\n\n"
                f"Используйте /start для возврата в главное меню.",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.message.reply_text(
                "❌ Ошибка при сохранении темы. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
        
        # Очищаем данные пользователя
        del self.user_data[user_id]
        
        return ConversationHandler.END
    
    async def add_supervisor_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начинает процесс добавления научрука"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Инициализируем данные пользователя
        if user_id not in self.user_data:
            self.user_data[user_id] = {}
        self.user_data[user_id]['supervisor'] = {}
        
        await query.edit_message_text(
            "👨‍🏫 **Добавление научного руководителя**\n\n"
            "Введите ФИО научного руководителя:",
            parse_mode='Markdown'
        )
        
        return SUPERVISOR_NAME
    
    async def get_supervisor_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает ФИО научрука"""
        user_id = update.effective_user.id
        name = update.message.text
        
        self.user_data[user_id]['supervisor']['full_name'] = name
        
        await update.message.reply_text(
            "📧 Введите email (или 'нет' для пропуска):"
        )
        
        return SUPERVISOR_EMAIL
    
    async def get_supervisor_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает email научрука"""
        user_id = update.effective_user.id
        email = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['supervisor']['email'] = email
        
        await update.message.reply_text(
            "👤 Введите username в Telegram (или 'нет' для пропуска):"
        )
        
        return SUPERVISOR_USERNAME
    
    async def get_supervisor_username(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает username научрука"""
        user_id = update.effective_user.id
        username = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['supervisor']['username'] = username
        
        await update.message.reply_text(
            "🏢 Введите должность:"
        )
        
        return SUPERVISOR_POSITION
    
    async def get_supervisor_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает должность научрука"""
        user_id = update.effective_user.id
        position = update.message.text
        
        self.user_data[user_id]['supervisor']['position'] = position
        
        await update.message.reply_text(
            "🎓 Введите ученую степень (или 'нет' для пропуска):"
        )
        
        return SUPERVISOR_DEGREE
    
    async def get_supervisor_degree(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает ученую степень научрука"""
        user_id = update.effective_user.id
        degree = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['supervisor']['degree'] = degree
        
        await update.message.reply_text(
            "👥 Введите количество мест для студентов:"
        )
        
        return SUPERVISOR_CAPACITY
    
    async def get_supervisor_capacity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает количество мест"""
        user_id = update.effective_user.id
        try:
            capacity = int(update.message.text)
            self.user_data[user_id]['supervisor']['capacity'] = capacity
        except ValueError:
            await update.message.reply_text("❌ Введите число. Попробуйте снова:")
            return SUPERVISOR_CAPACITY
        
        await update.message.reply_text(
            "📋 Введите требования к студентам (или 'нет' для пропуска):"
        )
        
        return SUPERVISOR_REQUIREMENTS
    
    async def get_supervisor_requirements(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает требования к студентам"""
        user_id = update.effective_user.id
        requirements = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['supervisor']['requirements'] = requirements
        
        await update.message.reply_text(
            "🔬 Введите научные интересы:"
        )
        
        return SUPERVISOR_INTERESTS
    
    async def get_supervisor_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает научные интересы и сохраняет научрука"""
        user_id = update.effective_user.id
        interests = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['supervisor']['interests'] = interests
        
        # Отправляем данные на сервер
        supervisor_data = {
            'full_name': self.user_data[user_id]['supervisor']['full_name'],
            'email': self.user_data[user_id]['supervisor']['email'],
            'username': self.user_data[user_id]['supervisor']['username'],
            'position': self.user_data[user_id]['supervisor']['position'],
            'degree': self.user_data[user_id]['supervisor']['degree'],
            'capacity': str(self.user_data[user_id]['supervisor']['capacity']),
            'requirements': self.user_data[user_id]['supervisor']['requirements'],
            'interests': self.user_data[user_id]['supervisor']['interests']
        }
        
        result = await self.api_request('POST', '/add-supervisor', supervisor_data)
        
        if result and result.get('status') == 'success':
            await update.message.reply_text(
                f"✅ **Научный руководитель успешно добавлен!**\n\n"
                f"👨‍🏫 ФИО: {self.user_data[user_id]['supervisor']['full_name']}\n"
                f"🏢 Должность: {self.user_data[user_id]['supervisor']['position']}\n"
                f"👥 Мест: {self.user_data[user_id]['supervisor']['capacity']}\n\n"
                f"Используйте /start для возврата в главное меню.",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.message.reply_text(
                "❌ Ошибка при сохранении научного руководителя. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
        
        # Очищаем данные пользователя
        del self.user_data[user_id]
        
        return ConversationHandler.END
    
    async def find_candidates(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Находит кандидатов по темам"""
        query = update.callback_query
        
        # Получаем темы с сервера
        topics_data = await self.api_request('GET', '/latest?kind=topics')
        
        if not topics_data:
            await query.edit_message_text("📝 Темы не найдены.")
            return
        
        # Создаем кнопки для выбора темы
        keyboard = []
        for topic in topics_data[:10]:
            role_text = "студента" if topic.get('seeking_role') == 'student' else "научрука"
            keyboard.append([
                InlineKeyboardButton(
                    f"📚 {topic.get('title', 'Без названия')[:30]}... ({role_text})",
                    callback_data=f"topic_{topic.get('id')}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔍 **Выберите тему для поиска кандидатов:**\n\n"
            "Нажмите на тему, чтобы увидеть подходящих кандидатов.",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    async def import_sheet_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает информацию об импорте из Google Sheets"""
        query = update.callback_query
        
        text = (
            "📊 **Импорт из Google Sheets**\n\n"
            "Для импорта данных используйте веб-интерфейс:\n"
            "🌐 http://localhost:8000\n\n"
            "**Что импортируется:**\n"
            "• Студенты с профилями\n"
            "• Темы исследований\n"
            "• Навыки и интересы\n\n"
            "**Настройка:**\n"
            "1. Добавьте в .env:\n"
            "   - SPREADSHEET_ID\n"
            "   - SERVICE_ACCOUNT_FILE\n"
            "2. Откройте веб-интерфейс\n"
            "3. Введите ID таблицы и импортируйте"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def view_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список студентов"""
        query = update.callback_query
        
        students_data = await self.api_request('GET', '/api/students?limit=10')
        
        if not students_data:
            await query.edit_message_text("👥 Студенты не найдены.")
            return
        
        text = "👥 **Список студентов:**\n\n"
        keyboard = []
        
        for student in students_data:
            text += f"**{student.get('full_name', 'Без имени')}**\n"
            if student.get('program'):
                text += f"📚 Программа: {student.get('program')}\n"
            if student.get('skills'):
                text += f"🛠️ Навыки: {student.get('skills')}\n"
            if student.get('interests'):
                text += f"🔬 Интересы: {student.get('interests')}\n"
            text += f"📅 ID: {student.get('id')}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"👤 {student.get('full_name', 'Без имени')[:30]}...",
                    callback_data=f"student_{student.get('id')}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def view_supervisors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список научных руководителей"""
        query = update.callback_query
        
        supervisors_data = await self.api_request('GET', '/api/supervisors?limit=10')
        
        if not supervisors_data:
            await query.edit_message_text("👨‍🏫 Научные руководители не найдены.")
            return
        
        text = "👨‍🏫 **Список научных руководителей:**\n\n"
        keyboard = []
        
        for supervisor in supervisors_data:
            text += f"**{supervisor.get('full_name', 'Без имени')}**\n"
            if supervisor.get('position'):
                text += f"🏢 Должность: {supervisor.get('position')}\n"
            if supervisor.get('degree'):
                text += f"🎓 Степень: {supervisor.get('degree')}\n"
            if supervisor.get('capacity'):
                text += f"👥 Свободных мест: {supervisor.get('capacity')}\n"
            if supervisor.get('interests'):
                text += f"🔬 Интересы: {supervisor.get('interests')}\n"
            text += f"📅 ID: {supervisor.get('id')}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"👨‍🏫 {supervisor.get('full_name', 'Без имени')[:30]}...",
                    callback_data=f"supervisor_{supervisor.get('id')}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def view_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список тем"""
        query = update.callback_query
        
        topics_data = await self.api_request('GET', '/api/topics?limit=10')
        
        if not topics_data:
            await query.edit_message_text("📝 Темы не найдены.")
            return
        
        text = "📝 **Список тем:**\n\n"
        keyboard = []
        
        for topic in topics_data:
            role_text = "студента" if topic.get('seeking_role') == 'student' else "научрука"
            text += f"**{topic.get('title', 'Без названия')}**\n"
            text += f"👤 Автор: {topic.get('author', 'Неизвестно')}\n"
            text += f"👥 Ищем: {role_text}\n"
            text += f"📅 ID: {topic.get('id')}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"📚 {topic.get('title', 'Без названия')[:30]}...",
                    callback_data=f"topic_{topic.get('id')}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    def run(self):
        """Запускает бота"""
        self.application.run_polling()

if __name__ == "__main__":
    bot = MentorMatchBot()
    bot.run()
