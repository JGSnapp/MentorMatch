# MentorMatch — схема БД (v2)
Экспорт: 28.08.2025

Ниже — пояснение каждой таблицы и поля. В скобках указаны типы столбцов и ключи/ссылки.

---

## 1) users — пользователи (аккаунты)
- **id** (bigint, PK): идентификатор пользователя.
- **telegram_id** (bigint, UNIQUE): ID в Telegram для связи с ботом.
- **full_name** (text): ФИО.
- **email** (text), **username** (text): контакты.
- **role** (varchar(20)): 'student' | 'supervisor' | 'admin'.
- **embeddings** (text): сериализованный вектор профиля/интересов (JSON/CSV/base64).
- **created_at**, **updated_at** (timestamp): аудит.

## 2) student_profiles — профиль студента (1:1 с users)
- **user_id** (bigint, PK, FK → users.id): владелец профиля.
- **course** (smallint): курс.
- **program**, **faculty**, **education** (text): учебная программа, факультет, образование.
- **skills** (text): навыки (JSON/CSV).
- **interests** (text): интересы (JSON/CSV).
- **cv** (text): ссылка/описание резюме (как строка).
- **requirements** (text): пожелания к темам/руководителю.
- **assignments** (text): JSON-сводка по заданиям/оценкам для быстрого UI (опционально).

## 3) supervisor_profiles — профиль научрука (1:1 с users)
- **user_id** (bigint, PK, FK → users.id)
- **position** (text), **degree** (text): должность, учёная степень.
- **capacity** (int): сколько студентов готов взять.
- **requirements** (text): требования к студентам.
- **interests** (text): научные интересы/тематики.

## 4) media_files — медиафайлы (метаданные + ключ объекта)
- **id** (bigint, PK)
- **owner_user_id** (bigint, FK → users.id, nullable): кто загрузил.
- **object_key** (text): ключ в объектном хранилище (S3/MinIO/локально).
- **provider** (varchar(20)): 's3' | 'tg' | 'local' | …
- **mime_type** (text), **size_bytes** (bigint): тип и размер.
- **created_at** (timestamp): когда загружен.
**Индексы:** (owner_user_id), (object_key).

## 5) topics — темы/проекты
- **id** (bigint, PK)
- **author_user_id** (bigint, FK → users.id): создатель темы (студент/научрук).
- **title**, **description** (text): заголовок и описание темы.
- **expected_outcomes** (text): ожидаемые результаты (артефакты/метрики).
- **required_skills** (text): требуемые навыки.
- **seeking_role** (varchar(20)): 'student' | 'supervisor' — кого ищем под тему.
- **embeddings** (text): вектор темы.
- **cover_media_id** (bigint, FK → media_files.id, nullable): обложка темы.
- **is_active** (boolean): активна ли тема.
- **created_at**, **updated_at** (timestamp).

## 6) topic_candidates — кандидаты на тему (ранжирование)
- **topic_id** (bigint, FK → topics.id)
- **user_id** (bigint, FK → users.id)
- **score** (float): коэффициент совпадения (эмбеддинги/LLM).
- **is_primary** (boolean): отмеченный «основной» кандидат.
- **approved** (boolean): решение по кандидату (одобрен/нет).
- **rank** (smallint): позиция в топе.
- **created_at** (timestamp): когда добавлен в кандидаты.
**Ключи/индексы:** (topic_id, user_id) [unique] — составной PK; индексы по (topic_id, score) и (user_id).

## 7) assignments — задания
- **id** (bigint, PK)
- **author_user_id** (bigint, FK → users.id): кто выдал (обычно научрук).
- **topic_id** (bigint, FK → topics.id, nullable): к какой теме относится.
- **title**, **description** (text): название и условие.
- **due_at** (timestamp, nullable): дедлайн.
- **max_score** (float, nullable): максимум баллов.
- **is_optional** (boolean): опциональное/обязательное.
- **attempts_limit** (int, nullable): лимит попыток (NULL = нет лимита).
- **correct_answer** (text): эталонный ответ (для 'equals' или подсказок).
- **check_type** (varchar(20)): 'compiler' | 'equals' | 'llm' | 'manual'.
- **media_file_id** (bigint, FK → media_files.id, nullable): вложение/условие задания.
- **created_at**, **updated_at** (timestamp).

## 8) completed_assignments — выполненные задания (submission + оценка)
- **assignment_id** (bigint, FK → assignments.id)
- **student_user_id** (bigint, FK → users.id)
- **solution_text** (text): текст решения (опционально).
- **solution_media_file_id** (bigint, FK → media_files.id, nullable): файл решения (репорт/архив/видео и т.п.).
- **score** (float, nullable): итоговая оценка.
- **feedback** (text): комментарий проверяющего.
- **submitted_at**, **graded_at** (timestamp): когда сдано/проверено.
- **grader_user_id** (bigint, FK → users.id, nullable): кто проверил.
- **created_at** (timestamp).
**Ключи/индексы:** составной PK (assignment_id, student_user_id); индекс по student_user_id.

## 9) chat_threads — треды чата
- **id** (bigint, PK)
- **user_a_id**, **user_b_id** (bigint, FK → users.id): участники диалога.
- **topic_id** (bigint, FK → topics.id, nullable): если обсуждается конкретная тема.
- **created_at**, **closed_at** (timestamp): время создания/закрытия.
**Индексы:** по обоим участникам и по теме.

## 10) chat_messages — сообщения чата
- **id** (bigint, PK)
- **thread_id** (bigint, FK → chat_threads.id): к какому треду относится.
- **sender_user_id** (bigint, FK → users.id): отправитель.
- **message_text** (text): текст сообщения.
- **media_file_id** (bigint, FK → media_files.id, nullable): вложение (изображение/документ/видео).
- **created_at** (timestamp).
**Индексы:** по thread_id и sender_user_id.

---

## Примечания по хранению медиа
- Файлы хранятся вне БД (S3/MinIO/локальный диск), в БД — только метаданные (media_files) и прямые ссылки из сущностей на файл (*_media_file_id).
- object_key — ключ/путь в хранилище; доступ через pre-signed URL.
- Для публичных обложек (topics.cover_media_id) можно подключить CDN.

## Кардинальности (вкратце)
- users 1↔1 student_profiles / supervisor_profiles (необязательные профили).
- users 1↔N topics (через author_user_id).
- topics N↔M users (через topic_candidates).
- assignments 1↔N completed_assignments (на одного студента — одна запись; составной PK).
- chat_threads 1↔N chat_messages.
