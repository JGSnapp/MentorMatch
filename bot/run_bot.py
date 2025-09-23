#!/usr/bin/env python3
"""
Скрипт для запуска MentorMatch Telegram бота
"""

import os
import sys
from dotenv import load_dotenv

# Добавляем текущую папку в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Загружаем переменные окружения
load_dotenv()

def main():
    """Основная функция запуска"""
    try:
        from bot import MentorMatchBot
        
        # Проверяем наличие токена
        if not os.getenv('TELEGRAM_BOT_TOKEN'):
            print("❌ Ошибка: TELEGRAM_BOT_TOKEN не найден в переменных окружения")
            print("Добавьте в .env файл:")
            print("TELEGRAM_BOT_TOKEN=your_bot_token_here")
            return
        
        print("🤖 Запуск MentorMatch Telegram Bot...")
        print("📱 Бот будет доступен в Telegram")
        host = os.getenv('BOT_HTTP_HOST', '0.0.0.0')
        port = os.getenv('BOT_HTTP_PORT', '5000')
        print(f"🌐 Внутренний HTTP API: http://{host}:{port}/notify")
        print("🔄 Для остановки нажмите Ctrl+C")
        print("-" * 50)
        
        # Создаем и запускаем бота
        bot = MentorMatchBot()
        bot.run()
        
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        print("Убедитесь, что все зависимости установлены:")
        print("pip install -r requirements.txt")
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        print("Проверьте настройки и попробуйте снова")

if __name__ == "__main__":
    main()
