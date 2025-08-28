# Настройка MentorMatch без Docker

## 🚀 Быстрый старт (Windows)

### 1. Установка зависимостей

```bash
# Установите Python 3.10+ с официального сайта
# https://www.python.org/downloads/

# Установите PostgreSQL 16+ с официального сайта
# https://www.postgresql.org/download/windows/

# Создайте виртуальное окружение
python -m venv venv
venv\Scripts\activate

# Установите зависимости
cd server
pip install -r requirements.txt
```

### 2. Настройка базы данных

```bash
# Создайте базу данных в PostgreSQL
psql -U postgres
CREATE DATABASE mentormatch;
CREATE USER mentormatch WITH PASSWORD 'secret';
GRANT ALL PRIVILEGES ON DATABASE mentormatch TO mentormatch;
\q

# Примените схему
psql -U mentormatch -d mentormatch -f ..\01_schema.sql
```

### 3. Настройка переменных окружения

```bash
# Скопируйте .env.example в .env
copy env.example .env

# Отредактируйте .env файл:
DATABASE_URL=postgresql://mentormatch:secret@localhost:5432/mentormatch
PROXY_API_KEY=your_openai_api_key
PROXY_BASE_URL=https://api.openai.com/v1
```

### 4. Запуск сервера

```bash
# Активируйте виртуальное окружение
venv\Scripts\activate

# Запустите сервер
cd server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Доступ к приложению

- **API**: http://localhost:8000
- **Документация API**: http://localhost:8000/docs
- **Админ панель**: http://localhost:8000

## 🔧 Альтернативный запуск через Python

```bash
# В папке server
python main.py
```

## 📝 Примечания

- Убедитесь, что PostgreSQL запущен как служба Windows
- Для работы с OpenAI API нужен действующий API ключ
- Google Sheets интеграция требует настройки сервисного аккаунта

## 🐛 Решение проблем

### Ошибка подключения к БД
- Проверьте, что PostgreSQL запущен
- Убедитесь в правильности DATABASE_URL
- Проверьте права доступа пользователя

### Ошибка импорта модулей
- Убедитесь, что находитесь в папке server
- Проверьте, что все файлы на месте
- Активируйте виртуальное окружение
