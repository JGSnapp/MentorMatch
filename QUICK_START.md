# 🚀 Быстрый старт MentorMatch

## ⚡ За 5 минут

### 1. Клонирование и настройка
```bash
git clone <repository>
cd MentorMatch
cp env.example .env
# Отредактируйте .env файл
```

### 2. Запуск через Docker (рекомендуется)
```bash
docker-compose up -d
```

### 3. Доступ к сервисам
- **API**: http://localhost:8000
- **pgAdmin**: http://localhost:8080
- **PostgreSQL**: localhost:5432

## 🤖 Запуск Telegram бота

### 1. Создайте бота
- Найдите @BotFather в Telegram
- Отправьте `/newbot`
- Скопируйте токен

### 2. Настройте токен
```bash
# В .env файле добавьте:
TELEGRAM_BOT_TOKEN=your_token_here
```

### 3. Запустите бота
```bash
cd server
pip install -r requirements.txt
python run_bot.py
```

## 🧪 Тестирование

```bash
cd server
python test_bot.py
```

## 📚 Подробная документация

- [README.md](README.md) - Основная документация
- [BOT_SETUP.md](BOT_SETUP.md) - Настройка бота
- [SETUP.md](SETUP.md) - Локальная установка

---

**Готово!** 🎉 Система MentorMatch полностью функциональна!
