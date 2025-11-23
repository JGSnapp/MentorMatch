# MentorMatch Google Data Container

## Назначение
Сервис интеграции с Google Sheets. Обрабатывает импорт данных студентов и наставников, а также экспорт пар в таблицы. Запускается как FastAPI приложение в `google_data/main.py`, где настраивается логирование, регистрируются маршруты импорта и эндпоинт экспорта `/api/export/pairs`.【F:google_data/main.py†L1-L62】

## Структура каталога
- `main.py` — конфигурация приложения, описание моделей запросов и реализация REST-эндпоинтов `/health` и `/api/export/pairs`. Экспорт вызывает workflow `sync_roles_sheet`, который формирует Google Sheet с актуальными парами.【F:google_data/main.py†L1-L73】
- `routes/` — набор роутеров FastAPI для импорта:
  - `import_students.py` принимает параметры таблицы, загружает строки через сервисы Google Sheets и вызывает workflow `import_students` для сохранения данных в Postgres.【F:google_data/routes/import_students.py†L1-L45】
  - `import_supervisors.py` аналогично обрабатывает импорт наставников.【F:google_data/routes/import_supervisors.py†L1-L45】
- `services/` — инфраструктурный слой:
  - `db.py` предоставляет соединения с Postgres, переиспользуемые во всех обработчиках.【F:google_data/services/db.py†L1-L80】
  - `google_sheets.py` содержит функции аутентификации через сервисный аккаунт, проверки TLS и загрузки строк из Google Sheets.【F:google_data/services/google_sheets.py†L1-L160】
  - `media_store.py` и `matching_client.py` повторяют логику сохранения медиа и уведомления matching сервиса об изменениях, используемые в workflow импорта тем и профилей.【F:google_data/services/media_store.py†L1-L71】【F:google_data/services/matching_client.py†L1-L80】
- `workflows/` — доменная логика:
  - `topic_import.py` реализует преобразование анкет в пользователей, профили и темы, включая нормализацию Telegram ссылок, загрузку резюме и постановку задач на обновление эмбеддингов.【F:google_data/workflows/topic_import.py†L1-L160】
  - `sheet_pairs.py` формирует и выгружает пары ментор–студент в Google Sheets (вызывается из `/api/export/pairs`).【F:google_data/workflows/sheet_pairs.py†L1-L120】
- `utils/` — вспомогательные функции:
  - `topic_extraction.py` использует LLM или резервные алгоритмы для выделения тем из текстов анкет.【F:google_data/utils/topic_extraction.py†L1-L120】
  - `cv.py`, `text_extract.py`, `utils.py` содержат парсеры и преобразователи данных форм Google, переиспользуемые в workflow импорта.【F:google_data/utils/cv.py†L1-L44】【F:google_data/utils/text_extract.py†L1-L68】【F:google_data/utils/utils.py†L1-L24】

## Как работает импорт студентов
- При каждом запуске импорт забирает все строки выбранного листа через `get_all_values()` и нормализует их в `fetch_normalized_rows()`: инкрементальной загрузки нет.【F:google_data/utils/parse_gform.py†L317-L342】
- Каждая непустая строка ищется в БД по email (без учёта регистра); если email отсутствует, создаётся новый пользователь даже при совпадении ФИО, чтобы не перезаписывать чужой профиль. При совпадении email переиспользуется существующий `user_id`, иначе создаётся новая запись пользователя.【F:google_data/workflows/topic_import.py†L57-L86】
- Профиль студента сохраняется через `INSERT ... ON CONFLICT (user_id) DO UPDATE`, поэтому новая строка перезапишет поля профиля найденного пользователя; в противном случае профиль создаётся с нуля.【F:google_data/workflows/topic_import.py†L116-L148】

## Ключевые функции
- `_configure_logging()` — читает уровень логирования из окружения и настраивает общий формат сообщений сервиса.【F:google_data/main.py†L18-L28】
- `export_pairs()` — HTTP POST обработчик, который определяет ID таблицы, вызывает `sync_roles_sheet()` и возвращает статус операции клиенту.【F:google_data/main.py†L41-L69】
- `create_students_import_router()`/`create_supervisors_import_router()` — строят роутеры для импорта и управляют загрузкой строк, обработкой ошибок аутентификации и вызовом workflow импорта.【F:google_data/routes/import_students.py†L1-L45】【F:google_data/routes/import_supervisors.py†L1-L45】
- `import_students()`/`import_supervisors()` в workflow тем превращают данные анкет в пользователей, профили и темы, включая сохранение резюме и постановку обновления эмбеддингов в matching сервис.【F:google_data/workflows/topic_import.py†L41-L160】【F:google_data/workflows/sheet_pairs.py†L1-L120】

## Интеграции
- Получает доступ к Google Sheets через сервисный аккаунт и файлы, проброшенные томами (`SERVICE_ACCOUNT_FILE`).【F:docker-compose.yml†L66-L90】
- Работает с Postgres через `services/db.py`, используя DSN из окружения контейнера.【F:google_data/services/db.py†L1-L80】
- Отправляет уведомления в matching сервис для пересчёта эмбеддингов после импорта данных (`services/matching_client.py`).【F:google_data/services/matching_client.py†L1-L80】
