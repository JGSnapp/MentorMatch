# MentorMatch — Схема БД (актуальная)

Документ описывает текущую схему базы данных, которая применяется автоматически контейнером Postgres при первом запуске (см. `01_schema.sql`).

Версия: 2025‑09

---

## users — пользователи/аккаунты
- id (bigserial, PK)
- telegram_id (bigint, unique)
- full_name (text, not null)
- email (text)
- username (text) — Telegram username или иной ник
- role (varchar(20), not null) — 'student' | 'supervisor' | 'admin'
- embeddings (text)
- consent_personal (boolean) — согласие на обработку ПДн
- consent_private (boolean) — согласие на публикацию в приватных чатах
- created_at, updated_at (timestamptz, not null, default now())

Индексы: idx_users_role (role)

## student_profiles — профиль студента (1:1 с users)
- user_id (bigint, PK, FK → users.id, on delete cascade)
- course (smallint)
- program (text)
- faculty (text)
- education (text)
- skills (text) — перечень навыков (CSV/JSON)
- interests (text) — перечень интересов (CSV/JSON)
- cv (text) — ссылка/описание резюме
- requirements (text) — пожелания к руководителю/условия
- assignments (text) — служебное поле под агрегаты/резюме заданий
- skills_to_learn (text)
- achievements (text)
- supervisor_pref (text) — предпочтения по научному руководителю
- groundwork (text) — имеющийся задел по теме
- wants_team (boolean)
- team_role (text)
- team_needs (text)
- apply_master (boolean)
- workplace (text)
- preferred_team_track (text)
- dev_track (boolean)
- science_track (boolean)
- startup_track (boolean)
- final_work_pref (text)

## supervisor_profiles — профиль научного руководителя (1:1 с users)
- user_id (bigint, PK, FK → users.id, on delete cascade)
- position (text)
- degree (text)
- capacity (int) — планируемая загрузка/кол-во студентов
- requirements (text)
- interests (text)

## media_files — медиа/файлы
- id (bigserial, PK)
- owner_user_id (bigint, FK → users.id, on delete set null)
- object_key (text, not null) — ключ/путь в хранилище
- provider (varchar(20), not null) — 's3' | 'tg' | 'local'
- mime_type (text, not null)
- size_bytes (bigint)
- width (int), height (int), duration_seconds (double precision)
- created_at (timestamptz, not null, default now())

Индексы: idx_media_owner (owner_user_id), idx_media_object_key (object_key)

## topics — темы/направления
- id (bigserial, PK)
- author_user_id (bigint, not null, FK → users.id, on delete cascade)
- title (text, not null)
- description (text)
- expected_outcomes (text)
- required_skills (text)
- seeking_role (varchar(20), not null) — 'student' | 'supervisor' (кого ищем под тему)
- embeddings (text)
- cover_media_id (bigint, FK → media_files.id, on delete set null)
- is_active (boolean, not null, default true)
- created_at, updated_at (timestamptz, not null, default now())

Индексы: idx_topics_author, idx_topics_seeking_role, idx_topics_active

## topic_candidates — кандидатуры под темы
- topic_id (bigint, FK → topics.id, on delete cascade)
- user_id (bigint, FK → users.id, on delete cascade)
- score (double precision)
- is_primary (boolean, default false)
- approved (boolean, default false)
- rank (smallint)
- created_at (timestamptz, not null, default now())

PK: (topic_id, user_id)
Индексы: idx_tc_user (user_id), idx_tc_topic_score (topic_id, score desc)

## assignments — задания/активности
- id (bigserial, PK)
- author_user_id (bigint, not null, FK → users.id, on delete cascade)
- topic_id (bigint, FK → topics.id, on delete set null)
- title (text, not null)
- description (text)
- due_at (timestamptz)
- max_score (double precision)
- is_optional (boolean, default false)
- attempts_limit (int)
- correct_answer (text)
- check_type (varchar(20), not null) — 'compiler' | 'equals' | 'llm' | 'manual'
- media_file_id (bigint, FK → media_files.id, on delete set null)
- created_at, updated_at (timestamptz, not null, default now())

Индексы: idx_assign_author, idx_assign_topic

## completed_assignments — выполненные задания
- assignment_id (bigint, FK → assignments.id, on delete cascade)
- student_user_id (bigint, FK → users.id, on delete cascade)
- solution_text (text)
- solution_media_file_id (bigint, FK → media_files.id, on delete set null)
- score (double precision)
- feedback (text)
- submitted_at (timestamptz)
- graded_at (timestamptz)
- grader_user_id (bigint, FK → users.id, on delete set null)
- created_at (timestamptz, not null, default now())

PK: (assignment_id, student_user_id)
Индексы: idx_completed_by_student (student_user_id)

## chat_threads — чаты (диалоги)
- id (bigserial, PK)
- user_a_id (bigint, not null, FK → users.id, on delete cascade)
- user_b_id (bigint, not null, FK → users.id, on delete cascade)
- topic_id (bigint, FK → topics.id, on delete set null)
- created_at (timestamptz, not null, default now())
- closed_at (timestamptz)

Индексы: idx_threads_user_a, idx_threads_user_b, idx_threads_topic

## chat_messages — сообщения
- id (bigserial, PK)
- thread_id (bigint, not null, FK → chat_threads.id, on delete cascade)
- sender_user_id (bigint, not null, FK → users.id, on delete cascade)
- message_text (text)
- media_file_id (bigint, FK → media_files.id, on delete set null)
- created_at (timestamptz, not null, default now())

Индексы: idx_msgs_thread, idx_msgs_sender

---

Примечания
- База — PostgreSQL 16+. Схема разворачивается автоматически из `01_schema.sql`, смонтированного в `/docker-entrypoint-initdb.d/` сервисом `postgres` в `docker-compose.yml`.
- Поля со списками (skills/interests/…): хранятся как текст (CSV/JSON) для простоты, при необходимости можно заменить на jsonb.
- `seeking_role` в `topics` определяет, кого ищем под тему: студента или научного руководителя. Это влияет на логику подбора в приложении.
