# MentorMatch

MentorMatch — платформа для управления программой наставничества с интеграцией Telegram и Google Sheets. Проект объединяет несколько FastAPI‑сервисов, административную панель, Telegram‑бота и модуль интеллектуального матчинга на базе LLM и эмбеддингов.

## Ключевые возможности
- Импорт и синхронизация данных о студентах, наставниках, ролях и темах из Google Sheets.
- Управление заявками, подтверждениями и уведомлениями через Telegram‑бота и админ‑панель.
- Подбор кандидатов с использованием эмбеддингов (pgvector) и LLM (через прокси API) с fallback‑логикой.
- Хранение и выдача файлов (CV, мотивационных писем) через единое медиахранилище.
- Полностью контейнеризованная инфраструктура: FastAPI‑сервисы, Telegram‑бот, PostgreSQL+pgvector, pgAdmin.

## Архитектура
```
Google Sheets --> google_data service --> Postgres (pgvector)
                                |
Telegram Bot <--> bot service --+
                                |
                            server API <--> matching service --> LLM API (proxy)
                                |
                             admin UI (FastAPI + Jinja)
```
`docker-compose.yml` поднимает все компоненты и общие тома для данных (`pgdata`, `pgadmin_data`, `media_data`).

## Сервисы docker-compose
| Сервис | Назначение | Порт |
| ------ | ---------- | ---- |
| `server` | Основной REST API: анкеты, уведомления, очередь эмбеддингов, webhooks | 8000 |
| `admin` | Административная панель (шаблоны HTML + API) | 8100 |
| `matching` | Сервис эмбеддингов и LLM‑матчинга, хранение векторов в Postgres | 8300 |
| `google_data` | Импорт/экспорт Google Sheets, запуск воркфлоу синхронизации | 8200 |
| `bot` | Telegram‑бот (Aiogram) для коммуникации со студентами и наставниками | 5000 |
| `postgres` | PostgreSQL с расширением pgvector, загрузка схемы из `schema.sql` | 5432 |
| `pgadmin` | Веб‑интерфейс для PostgreSQL (опционально) | 8080 |

## Быстрый старт
1. Установите Docker 24+ и docker compose v2.
2. Скопируйте шаблон переменных и заполните обязательные поля:
   ```bash
   cp env.example .env
   ```
   Минимально необходимы:
   - `POSTGRES_PASSWORD`
   - `PROXY_API_KEY` (ключ к прокси OpenAI API)
   - `TELEGRAM_BOT_TOKEN`
   - `STUDENT_SPREADSHEET_ID` и/или `SUPERVISOR_SPREADSHEET_ID`
   - `SERVICE_ACCOUNT_FILE` (обычно `service-account.json`)
3. Поместите JSON сервисного аккаунта в `google_data/service-account.json` и выдайте ему доступ к нужным Google Sheets.
4. Запустите инфраструктуру:
   ```bash
   docker compose up -d --build
   ```
5. Проверьте сервисы:
   - API: http://localhost:8000/docs
   - Админка: http://localhost:8100
   - Telegram‑бот: вебхук должен указывать на `BOT_API_URL` (по умолчанию `http://bot:5000`)
   - pgAdmin (опционально): http://localhost:8080 (`postgres` / `5432` / `mentormatch` / `secret`)

Контейнер `postgres` автоматически применит `schema.sql`. Для полного пересоздания БД используйте `docker compose down -v`.

## Переменные окружения
| Переменная | Описание | По умолчанию |
| ---------- | -------- | ------------ |
| `POSTGRES_USER` | Пользователь БД | `mentormatch` |
| `POSTGRES_PASSWORD` | Пароль БД | `secret` |
| `POSTGRES_DB` | Имя базы данных | `mentormatch` |
| `POSTGRES_HOST` | Хост PostgreSQL (для контейнеров) | `postgres` |
| `POSTGRES_PORT` | Порт PostgreSQL | `5432` |
| `DATABASE_URL` | Полный DSN, если требуется override | — |
| `LOG_LEVEL` | Уровень логирования сервисов | `INFO` |
| `PROXY_API_KEY` | Ключ к прокси OpenAI API | — |
| `PROXY_BASE_URL` | Базовый URL прокси | `https://api.proxyapi.ru/openai/v1` |
| `PROXY_MODEL` | Модель для LLM‑матчинга | `gpt-4o-mini` |
| `MATCHING_LLM_TEMPERATURE` | Температура LLM | `0.2` |
| `STUDENT_SPREADSHEET_ID` | ID таблицы со студентами | — |
| `SUPERVISOR_SPREADSHEET_ID` | ID таблицы с наставниками | — |
| `PAIRS_SPREADSHEET_ID` | ID таблицы с финальными парами (опция) | — |
| `SERVICE_ACCOUNT_FILE` | Путь к JSON сервисного аккаунта внутри контейнера | `service-account.json` |
| `BOT_API_URL` | HTTP API Telegram‑бота для уведомлений | `http://bot:5000` |
| `SERVER_URL` | Базовый URL сервера для бота | `http://localhost:8000` |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram‑бота | — |
| `TEST_IMPORT` | Импорт тестовых данных при старте (`true/false`) | `false` |

Дополнительно доступны настройки прокси Telegram (`TELEGRAM_*`), файлового логирования (`LOG_FILE`) и параметры модели.

## Интеграция с Google Sheets
- Поделитесь таблицами с сервисным аккаунтом (e-mail из JSON).
- Основные операции:
  - `POST /api/import/students` (`google_data`) — студенты и их предпочтения.
  - `POST /api/import/supervisors` (`google_data`) — наставники и роли.
  - `POST /api/import-sheet` (`server`) — универсальный импорт листа по имени.
  - `POST /api/export/pairs` (`google_data`) — выгрузка утвержденных пар.
- Статус и конфигурацию синхронизации можно проверить через `/api/sheets-status` и `/api/sheets-config` (`server`).
- Файлы (CV, мотивации) доступны по `GET /media/{id}`; ссылки формируются при импорте.

## Пайплайн матчинга
1. Импорт и изменения данных добавляют задачи в очередь (`server/embedding_queue.py`).
2. Сервис `matching` пересчитывает эмбеддинги (Sentence Transformers) и сохраняет векторы в Postgres (pgvector).
3. Запросы `/api/match/*` и `/api/embeddings/*` обрабатываются сервисом `matching`: выполняется поиск ближайших векторов, затем LLM ранжирует кандидатов.
4. Результаты попадают в очередь уведомлений; админы подтверждают или отклоняют пары через UI или Telegram.
5. Утвержденные решения при необходимости экспортируются обратно в Google Sheets.

## Telegram-бот
- Реализован на Aiogram, запускается через `bot/run_bot.py`.
- Общается с сервером по HTTP (`POST /notify`, `/api/messages/*`, `/api/bind-telegram`).
- Список администраторов задается в `server/admins.txt` (поддерживаются Telegram ID, `@username`, `https://t.me/...`).
- Дополнительные таймауты/прокси настраиваются переменными `TELEGRAM_CONNECT_TIMEOUT`, `TELEGRAM_PROXY_URL` и т. п.

## Структура репозитория
```
MentorMatch/
├─ admin/         # Административная панель (FastAPI + Jinja2 шаблоны)
├─ bot/           # Telegram-бот (Aiogram)
├─ google_data/   # Интеграция с Google Sheets и воркфлоу импорта
├─ matching/      # Эмбеддинги, LLM, матчинговый движок
├─ server/        # Основной REST API, медиахранилище, очереди
├─ docs/          # Дополнительная документация по контейнерам
├─ schema.sql     # Схема PostgreSQL (pgvector)
├─ schema.md      # Описание таблиц
├─ docker-compose.yml
└─ env.example
```

## Локальная разработка
- Поднимите Postgres (например, `docker compose up postgres`).
- Установите зависимости и запустите нужный сервис:
  ```bash
  pip install -r server/requirements.txt
  uvicorn server.main:app --reload --port 8000
  ```
  Для остальных сервисов: `matching/requirements.txt`, `admin/requirements.txt`, `bot/requirements.txt`.
- Экспортируйте переменные окружения или используйте `.env`.
- Примените `schema.sql` для инициализации базы.

## Частые операции
- Остановить сервисы: `docker compose down`
- Пересоздать БД (с удалением данных): `docker compose down -v`
- Смотреть логи: `docker compose logs -f server` (или другой сервис)
- Пересобрать контейнер после правок зависимостей: `docker compose build <service>`

Подробные заметки по каждому контейнеру находятся в `docs/containers/*.md`.
