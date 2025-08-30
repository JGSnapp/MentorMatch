#!/usr/bin/env python3
"""
Тест для проверки работы MentorMatch Bot
"""

import os
import sys
from dotenv import load_dotenv

# Добавляем текущую папку в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_bot_import():
    """Тестирует импорт бота"""
    try:
        from bot import MentorMatchBot
        print("✅ Импорт MentorMatchBot успешен")
        return True
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        return False

def test_env_variables():
    """Тестирует переменные окружения"""
    load_dotenv()
    
    required_vars = [
        'TELEGRAM_BOT_TOKEN',
        'DATABASE_URL',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD',
        'POSTGRES_DB'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Отсутствуют переменные окружения: {', '.join(missing_vars)}")
        return False
    else:
        print("✅ Все необходимые переменные окружения найдены")
        return False

def test_database_connection():
    """Тестирует подключение к БД"""
    try:
        from bot import MentorMatchBot
        bot = MentorMatchBot()
        conn = bot.get_conn()
        conn.close()
        print("✅ Подключение к БД успешно")
        return True
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        return False

def main():
    """Основная функция тестирования"""
    print("🧪 Тестирование MentorMatch Bot...")
    print("=" * 50)
    
    tests = [
        ("Импорт бота", test_bot_import),
        ("Переменные окружения", test_env_variables),
        ("Подключение к БД", test_database_connection),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n🔍 Тест: {test_name}")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Ошибка выполнения теста: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("📊 Результаты тестирования:")
    
    passed = 0
    for test_name, result in results:
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\n🎯 Итого: {passed}/{len(results)} тестов пройдено")
    
    if passed == len(results):
        print("🎉 Все тесты пройдены! Бот готов к запуску.")
        print("\n🚀 Для запуска выполните:")
        print("python run_bot.py")
    else:
        print("⚠️  Некоторые тесты не пройдены. Проверьте настройки.")
        print("\n📖 См. BOT_SETUP.md для инструкций по настройке")

if __name__ == "__main__":
    main()
