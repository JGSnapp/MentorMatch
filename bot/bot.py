import os
import logging
from typing import Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from datetime import datetime

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
        
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        
        # Временное хранилище данных пользователей
        self.user_data: Dict[int, Dict[str, Any]] = {}
        
    def build_db_dsn(self) -> str:
        """Строит строку подключения к БД"""
        dsn = os.getenv('DATABASE_URL')
        if dsn:
            return dsn
        user = os.getenv('POSTGRES_USER', 'mentormatch')
        password = os.getenv('POSTGRES_PASSWORD', 'secret')
        host = os.getenv('POSTGRES_HOST', 'localhost')
        port = os.getenv('POSTGRES_PORT', '5432')
        db = os.getenv('POSTGRES_DB', 'mentormatch')
        return f'postgresql://{user}:{password}@{host}:{port}/{db}'
    
    def get_conn(self):
        """Получает соединение с БД"""
        return psycopg2.connect(self.build_db_dsn())
    
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
        keyboard = [
            [InlineKeyboardButton("📚 Добавить тему", callback_data="add_topic")],
            [InlineKeyboardButton("👨‍🏫 Добавить научрука", callback_data="add_supervisor")],
            [InlineKeyboardButton("🔍 Найти кандидатов", callback_data="find_candidates")],
            [InlineKeyboardButton("📊 Импорт из Google Sheets", callback_data="import_sheet")],
            [InlineKeyboardButton("👥 Список студентов", callback_data="list_students")],
            [InlineKeyboardButton("👨‍🏫 Список научруков", callback_data="list_supervisors")],
            [InlineKeyboardButton("📝 Список тем", callback_data="list_topics")],
            [InlineKeyboardButton("❌ Сбросить данные", callback_data="reset_data")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🎓 Добро пожаловать в MentorMatch Bot!\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
        
        return CHOOSING_ACTION
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_text = """
🤖 **MentorMatch Bot - Помощь**

**Основные команды:**
/start - Главное меню
/help - Эта справка
/cancel - Отменить текущее действие

**Функции:**
• Добавление тем для ВКР
• Добавление научных руководителей
• Поиск кандидатов по темам
• Импорт данных из Google Sheets
• Просмотр списков пользователей и тем
• Сброс введенных данных

**Как использовать:**
1. Нажмите /start для открытия главного меню
2. Выберите нужную функцию
3. Следуйте инструкциям бота
4. Используйте /cancel для отмены
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отменяет текущее действие"""
        user_id = update.effective_user.id
        
        # Очищаем данные пользователя
        if user_id in self.user_data:
            del self.user_data[user_id]
        
        await update.message.reply_text(
            "❌ Действие отменено. Используйте /start для возврата в главное меню."
        )
        
        return ConversationHandler.END
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик callback кнопок"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "find_candidates":
            await self.find_candidates(update, context)
        elif query.data == "import_sheet":
            await self.import_sheet_start(update, context)
        elif query.data == "list_students":
            await self.list_students(update, context)
        elif query.data == "list_supervisors":
            await self.list_supervisors(update, context)
        elif query.data == "list_topics":
            await self.list_topics(update, context)
        elif query.data == "reset_data":
            await self.reset_user_data(update, context)
    
    async def add_topic_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начинает процесс добавления темы"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Инициализируем данные пользователя
        if user_id not in self.user_data:
            self.user_data[user_id] = {}
        self.user_data[user_id]['topic'] = {}
        
        await query.edit_message_text(
            "📚 **Добавление новой темы**\n\n"
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
            "🎯 Введите ожидаемые результаты:"
        )
        
        return TOPIC_OUTCOMES
    
    async def get_topic_outcomes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает ожидаемые результаты"""
        user_id = update.effective_user.id
        outcomes = update.message.text
        
        self.user_data[user_id]['topic']['expected_outcomes'] = outcomes
        
        await update.message.reply_text(
            "🛠️ Введите требуемые навыки (через запятую):"
        )
        
        return TOPIC_SKILLS
    
    async def get_topic_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает требуемые навыки"""
        user_id = update.effective_user.id
        skills = update.message.text
        
        self.user_data[user_id]['topic']['required_skills'] = skills
        
        keyboard = [
            [KeyboardButton("Студента"), KeyboardButton("Научрука")],
            [KeyboardButton("Отмена")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            "👥 Кого ищете под эту тему?",
            reply_markup=reply_markup
        )
        
        return TOPIC_ROLE
    
    async def get_topic_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает роль и сохраняет тему"""
        user_id = update.effective_user.id
        role_text = update.message.text
        
        if role_text == "Отмена":
            await update.message.reply_text(
                "❌ Добавление темы отменено. Используйте /start для возврата в главное меню.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        seeking_role = 'student' if role_text == "Студента" else 'supervisor'
        self.user_data[user_id]['topic']['seeking_role'] = seeking_role
        
        # Сохраняем тему в БД
        try:
            with self.get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    '''
                    INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                       required_skills, seeking_role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, now(), now())
                    RETURNING id
                    ''', (
                        user_id,
                        self.user_data[user_id]['topic']['title'],
                        self.user_data[user_id]['topic']['description'],
                        self.user_data[user_id]['topic']['expected_outcomes'],
                        self.user_data[user_id]['topic']['required_skills'],
                        seeking_role,
                    ),
                )
                topic_id = cur.fetchone()[0]
                conn.commit()
            
            # Очищаем данные пользователя
            del self.user_data[user_id]
            
            await update.message.reply_text(
                f"✅ **Тема успешно добавлена!**\n\n"
                f"📚 Название: {self.user_data[user_id]['topic']['title']}\n"
                f"👥 Ищем: {seeking_role}\n"
                f"🆔 ID темы: {topic_id}\n\n"
                f"Используйте /start для возврата в главное меню.",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove()
            )
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении темы: {e}")
            await update.message.reply_text(
                "❌ Ошибка при сохранении темы. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
        
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
            "👥 Введите количество студентов, которых готов взять:"
        )
        
        return SUPERVISOR_CAPACITY
    
    async def get_supervisor_capacity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает количество студентов"""
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
            "🔬 Введите научные интересы (или 'нет' для пропуска):"
        )
        
        return SUPERVISOR_INTERESTS
    
    async def get_supervisor_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получает научные интересы и сохраняет научрука"""
        user_id = update.effective_user.id
        interests = update.message.text if update.message.text != 'нет' else None
        
        self.user_data[user_id]['supervisor']['interests'] = interests
        
        # Сохраняем научрука в БД
        try:
            with self.get_conn() as conn, conn.cursor() as cur:
                # Создаем пользователя
                cur.execute(
                    '''
                    INSERT INTO users(full_name, email, username, role, created_at, updated_at)
                    VALUES (%s, %s, %s, 'supervisor', now(), now())
                    RETURNING id
                    ''', (
                        self.user_data[user_id]['supervisor']['full_name'],
                        self.user_data[user_id]['supervisor']['email'],
                        self.user_data[user_id]['supervisor']['username'],
                    ),
                )
                user_id_db = cur.fetchone()[0]
                
                # Создаем профиль научрука
                cur.execute(
                    '''
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, requirements, interests)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (
                        user_id_db,
                        self.user_data[user_id]['supervisor']['position'],
                        self.user_data[user_id]['supervisor']['degree'],
                        self.user_data[user_id]['supervisor']['capacity'],
                        self.user_data[user_id]['supervisor']['requirements'],
                        self.user_data[user_id]['supervisor']['interests'],
                    ),
                )
                conn.commit()
            
            # Очищаем данные пользователя
            del self.user_data[user_id]
            
            await update.message.reply_text(
                f"✅ **Научный руководитель успешно добавлен!**\n\n"
                f"👨‍🏫 ФИО: {self.user_data[user_id]['supervisor']['full_name']}\n"
                f"🏢 Должность: {self.user_data[user_id]['supervisor']['position']}\n"
                f"👥 Мест: {self.user_data[user_id]['supervisor']['capacity']}\n\n"
                f"Используйте /start для возврата в главное меню.",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove()
            )
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении научрука: {e}")
            await update.message.reply_text(
                "❌ Ошибка при сохранении научного руководителя. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
        
        return ConversationHandler.END
    
    async def find_candidates(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Находит кандидатов по темам"""
        query = update.callback_query
        
        try:
            with self.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Получаем список тем
                cur.execute(
                    '''
                    SELECT t.id, t.title, t.seeking_role, u.full_name AS author
                    FROM topics t
                    JOIN users u ON u.id = t.author_user_id
                    WHERE t.is_active = TRUE
                    ORDER BY t.created_at DESC
                    LIMIT 10
                    '''
                )
                topics = cur.fetchall()
                
                if not topics:
                    await query.edit_message_text("📝 Темы не найдены.")
                    return
                
                # Создаем кнопки для выбора темы
                keyboard = []
                for topic in topics:
                    role_text = "студента" if topic['seeking_role'] == 'student' else "научрука"
                    keyboard.append([
                        InlineKeyboardButton(
                            f"📚 {topic['title'][:30]}... ({role_text})",
                            callback_data=f"topic_{topic['id']}"
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
                
        except Exception as e:
            logger.error(f"Ошибка при поиске кандидатов: {e}")
            await query.edit_message_text("❌ Ошибка при поиске кандидатов.")
    
    async def import_sheet_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начинает процесс импорта из Google Sheets"""
        query = update.callback_query
        
        await query.edit_message_text(
            "📊 **Импорт из Google Sheets**\n\n"
            "Для импорта данных используйте веб-интерфейс:\n"
            "http://localhost:8000\n\n"
            "Или настройте переменные окружения:\n"
            "• SPREADSHEET_ID\n"
            "• SERVICE_ACCOUNT_FILE\n\n"
            "Используйте /start для возврата в главное меню."
        )
    
    async def list_students(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список студентов"""
        query = update.callback_query
        
        try:
            with self.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    '''
                    SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                           sp.program, sp.skills, sp.interests
                    FROM users u
                    LEFT JOIN student_profiles sp ON sp.user_id = u.id
                    WHERE u.role = 'student'
                    ORDER BY u.created_at DESC
                    LIMIT 10
                    '''
                )
                students = cur.fetchall()
                
                if not students:
                    await query.edit_message_text("👥 Студенты не найдены.")
                    return
                
                text = "👥 **Список студентов:**\n\n"
                for student in students:
                    text += f"👤 **{student['full_name']}**\n"
                    if student['program']:
                        text += f"📚 Программа: {student['program']}\n"
                    if student['skills']:
                        text += f"🛠️ Навыки: {student['skills']}\n"
                    if student['interests']:
                        text += f"🔬 Интересы: {student['interests']}\n"
                    text += f"📅 Зарегистрирован: {student['created_at'].strftime('%d.%m.%Y')}\n\n"
                
                keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            logger.error(f"Ошибка при получении списка студентов: {e}")
            await query.edit_message_text("❌ Ошибка при получении списка студентов.")
    
    async def list_supervisors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список научруков"""
        query = update.callback_query
        
        try:
            with self.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    '''
                    SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                           sup.position, sup.degree, sup.capacity, sup.interests
                    FROM users u
                    LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
                    WHERE u.role = 'supervisor'
                    ORDER BY u.created_at DESC
                    LIMIT 10
                    '''
                )
                supervisors = cur.fetchall()
                
                if not supervisors:
                    await query.edit_message_text("👨‍🏫 Научные руководители не найдены.")
                    return
                
                text = "👨‍🏫 **Список научных руководителей:**\n\n"
                for supervisor in supervisors:
                    text += f"👤 **{supervisor['full_name']}**\n"
                    if supervisor['position']:
                        text += f"🏢 Должность: {supervisor['position']}\n"
                    if supervisor['degree']:
                        text += f"🎓 Степень: {supervisor['degree']}\n"
                    if supervisor['capacity']:
                        text += f"👥 Свободных мест: {supervisor['capacity']}\n"
                    if supervisor['interests']:
                        text += f"🔬 Интересы: {supervisor['interests']}\n"
                    text += f"📅 Зарегистрирован: {supervisor['created_at'].strftime('%d.%m.%Y')}\n\n"
                
                keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            logger.error(f"Ошибка при получении списка научруков: {e}")
            await query.edit_message_text("❌ Ошибка при получении списка научных руководителей.")
    
    async def list_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список тем"""
        query = update.callback_query
        
        try:
            with self.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    '''
                    SELECT t.id, t.title, t.seeking_role, t.created_at, u.full_name AS author
                    FROM topics t
                    JOIN users u ON u.id = t.author_user_id
                    WHERE t.is_active = TRUE
                    ORDER BY t.created_at DESC
                    LIMIT 10
                    '''
                )
                topics = cur.fetchall()
                
                if not topics:
                    await query.edit_message_text("📝 Темы не найдены.")
                    return
                
                text = "📝 **Список тем:**\n\n"
                for topic in topics:
                    role_text = "студента" if topic['seeking_role'] == 'student' else "научрука"
                    text += f"📚 **{topic['title']}**\n"
                    text += f"👤 Автор: {topic['author']}\n"
                    text += f"👥 Ищем: {role_text}\n"
                    text += f"📅 Создана: {topic['created_at'].strftime('%d.%m.%Y')}\n\n"
                
                keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            logger.error(f"Ошибка при получении списка тем: {e}")
            await query.edit_message_text("❌ Ошибка при получении списка тем.")
    
    async def reset_user_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сбрасывает данные пользователя"""
        query = update.callback_query
        user_id = query.from_user.id
        
        if user_id in self.user_data:
            del self.user_data[user_id]
        
        await query.edit_message_text(
            "✅ Данные сброшены. Используйте /start для возврата в главное меню."
        )
    
    def run(self):
        """Запускает бота"""
        logger.info("Запуск MentorMatch Bot...")
        self.application.run_polling()

if __name__ == "__main__":
    try:
        bot = MentorMatchBot()
        bot.run()
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
