# MentorMatch

Платформа для подбора студентов и научных руководителей: FastAPI backend, веб‑админка, Telegram‑бот, импорт из Google Sheets и ранжирование кандидатов (LLM с фолбэком).

## Возможности

- Админка: импорт из Google Sheets, добавление научных руководителей/тем, списки последних записей, простой подбор.
- Telegram‑бот: просмотр списков, импорт из Sheets, подбор кандидатов по теме.
- Мэтчинг: топ‑5 кандидатов через LLM (через прокси), при <5 записях или ошибке LLM — корректный фолбэк.
- БД: PostgreSQL, готовая схема (`01_schema.sql`).

## Развёртывание (Docker)

1) Подготовьте окружение:
   - Скопируйте файл окружения: `cp env.example .env`
   - Заполните переменные в `.env`:
     - `TELEGRAM_BOT_TOKEN` — токен бота
     - `SPREADSHEET_ID` — ID таблицы Google Sheets
     - `SERVICE_ACCOUNT_FILE=service-account.json` — путь к JSON сервисного аккаунта внутри контейнера сервера. По умолчанию положите файл в `server/service-account.json` (смонтируется как `/app/service-account.json`).
     - `PROXY_API_KEY` и `PROXY_BASE_URL` — для LLM‑ранжирования; без них будет фолбэк на простой топ‑N.

2) Запуск:
```bash
docker-compose up -d --build
```

3) Доступ:
- Админка/API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- pgAdmin: `http://localhost:8080` (логин/пароль из `.env`), добавьте сервер вручную: host `postgres`, port `5432`, db `mentormatch`, user `mentormatch`, password `secret`.

4) Импорт из Google Sheets:
- В админке на главной странице заполните форму «Обновить данные из Google Sheets» (Spreadsheet ID подставится из `.env`).
- Либо через Telegram‑бота кнопкой «Импорт из Google Sheets».

Примечания:
- Схема БД применяется автоматически из `01_schema.sql` при первом старте PostgreSQL.
- Если видите предупреждение о `python-multipart`, пересоберите контейнер сервера (`--build`) — зависимость уже указана в `server/requirements.txt`.
- Дедупликация студентов при импорте идёт по email (без учёта регистра), при отсутствии email — по ФИО.

## Структура

```
MentorMatch/
├── server/
│   ├── main.py            # API (JSON), интеграции и матчинг
│   ├── admin.py           # Маршруты веб‑админки (HTML)
│   ├── matching.py        # Логика подбора (LLM + фолбэк)
│   ├── parse_gform.py     # Парсер Google Sheets
│   └── requirements.txt   # Зависимости сервера
├── bot/
│   ├── bot.py             # Логика бота
│   ├── run_bot.py         # Точка входа
│   └── requirements.txt   # Зависимости бота
├── templates/              # HTML шаблоны админки
├── 01_schema.sql          # SQL‑схема Postgres (автоинициализация)
├── schema.md              # Описание схемы
├── docker-compose.yml     # Оркестрация (server, bot, postgres, pgadmin)
├── env.example            # Пример .env
└── README.md
```

## Полезное
- Сброс данных БД: `docker compose down -v` (удалит том с данными Postgres).
- Если образы не тянутся с Docker Hub (таймауты) — перезапустите Docker Desktop/WSL, выполните `docker login`, или потяните образы вручную (`docker pull postgres:16`, `docker pull dpage/pgadmin4`).
