# MentorMatch: Схема БД и соответствие новой форме

Дата обновления: 2025‑09

---

## users — пользователи (студенты, научруки, админы)
- id: bigserial, PK
- telegram_id: bigint, UNIQUE
- full_name: text, NOT NULL
- email: text
- username: text — Telegram (полная ссылка вида https://t.me/<username>)
- role: varchar(20), NOT NULL — 'student' | 'supervisor' | 'admin'
- embeddings: text
- consent_personal: boolean — согласие на обработку персональных данных
- consent_private: boolean — согласие на обработку закрытых данных (если есть)
- created_at, updated_at: timestamptz, NOT NULL, DEFAULT now()

Индексы: idx_users_role(role)

## student_profiles — профиль студента (1:1 к users)
- user_id: bigint, PK, FK → users.id (ON DELETE CASCADE)
- course: smallint
- program: text — «Ваше направление»
- faculty: text
- education: text
- skills: text — «Ваши Hard Skills (знаю)», хранится CSV
- interests: text — «Область научного/профессионального интереса», CSV
- cv: text — ссылка «Загрузите файл… (CV, …)»
- requirements: text — «ФИО предполагаемого научного руководителя… / пожелания»
- assignments: text — служебное, краткая сводка заданий (для UI)
- skills_to_learn: text — «Hard Skills (хочу изучить)», CSV
- achievements: text — «Дополнительная информация о себе…»
- supervisor_pref: text — дублирует requirements (совместимость)
- groundwork: text — «Имеющийся задел по теме» (из Блока 2)
- wants_team: boolean — «Планируете ли вы работать в команде?»; если заполнены поля Блока 4, а ответа нет — true
- team_role: text — «Желаемая роль в команде»
- team_has: text — «У вас уже есть в команде:»
- team_needs: text — «Кто дополнительно требуется в команду»
- apply_master: boolean — «Планируете поступать в магистратуру?»
- workplace: text — «Ваше место работы и должность…»
- preferred_team_track: text — «Наиболее предпочтительный трек команды»
- dev_track: smallint — «Разработка — трек вашего развития?» (0..5)
- science_track: smallint — «Наука — трек вашего развития?» (0..5)
- startup_track: smallint — «Стартап — трек вашего развития?» (0..5)
- final_work_pref: text — «В качестве вариативного задания я предпочитаю»

Примечания:
- skills / interests / skills_to_learn сейчас как CSV; при необходимости можно мигрировать в jsonb.
- Поле wants_team допускает NULL при ответе «Не знаю…» и отсутствии полей Блока 4.

## supervisor_profiles — профиль научрука (1:1 к users)
- user_id: bigint, PK, FK → users.id (ON DELETE CASCADE)
- position: text
- degree: text
- capacity: int — готовность брать студентов
- requirements: text
- interests: text

## media_files — медиа (общая таблица)
- id: bigserial, PK
- owner_user_id: bigint, FK → users.id (ON DELETE SET NULL)
- object_key: text, NOT NULL — ключ/путь в хранилище (S3/MinIO/local)
- provider: varchar(20), NOT NULL — 's3' | 'tg' | 'local'
- mime_type: text, NOT NULL
- size_bytes: bigint
- width: int, height: int, duration_seconds: double precision
- created_at: timestamptz, NOT NULL, DEFAULT now()

Индексы: idx_media_owner(owner_user_id), idx_media_object_key(object_key)

## topics — темы
- id: bigserial, PK
- author_user_id: bigint, NOT NULL, FK → users.id (ON DELETE CASCADE)
- title: text, NOT NULL
- description: text — дополняется «Имеющийся задел…» и «Практическая значимость: …» из формы студента
- expected_outcomes: text
- required_skills: text — подтягиваем известные skills студента при создании его темы
- direction: smallint — направление (9/11/45), опционально
- seeking_role: varchar(20), NOT NULL — 'student' | 'supervisor' (кого ищет автор темы)
- embeddings: text
- cover_media_id: bigint, FK → media_files.id (ON DELETE SET NULL)
- approved_supervisor_user_id: bigint, FK → users.id (утверждённый руководитель)
- is_active: boolean, NOT NULL, DEFAULT true
- created_at, updated_at: timestamptz, NOT NULL, DEFAULT now()

Индексы: idx_topics_author, idx_topics_seeking_role, idx_topics_active, idx_topics_direction

## roles — роли внутри темы
- id: bigserial, PK
- topic_id: bigint, NOT NULL, FK → topics.id (ON DELETE CASCADE)
- name: text, NOT NULL — название роли (например, «дизайнер», «ML‑специалист», «любая»)
- description: text — описание роли
- required_skills: text — требования к роли
- capacity: int — сколько людей нужно на эту роль (опционально)
- approved_student_user_id: bigint, FK → users.id (утверждённый студент)
- created_at, updated_at

Индексы: idx_roles_topic(topic_id)

## role_candidates — кандидаты под роль (ранжирование)
- role_id: bigint, FK → roles.id (ON DELETE CASCADE)
- user_id: bigint, FK → users.id (ON DELETE CASCADE) — студент
- score, is_primary, approved, rank, created_at

PK: (role_id, user_id)
Индексы: idx_rc_role_score(role_id, score desc)

## student_candidates — роли, рекомендованные студенту
- user_id: bigint — студент, FK → users.id (ON DELETE CASCADE)
- role_id: bigint, FK → roles.id (ON DELETE CASCADE)
- score, is_primary, approved, rank, created_at

PK: (user_id, role_id)
Индексы: idx_sc_user_score(user_id, score desc)

## topic_candidates — кандидаты для темы (теперь только руководители)
- topic_id: bigint, FK → topics.id (ON DELETE CASCADE)
- user_id: bigint, FK → users.id (ON DELETE CASCADE)
- score: double precision
- is_primary: boolean, DEFAULT false
- approved: boolean, DEFAULT false
- rank: smallint
- created_at: timestamptz, NOT NULL, DEFAULT now()

PK: (topic_id, user_id)
Индексы: idx_tc_user(user_id), idx_tc_topic_score(topic_id, score desc)

## supervisor_candidates — темы, рекомендованные руководителю
- user_id: bigint — руководитель, FK → users.id (ON DELETE CASCADE)
- topic_id: bigint, FK → topics.id (ON DELETE CASCADE)
- score, is_primary, approved, rank, created_at

PK: (user_id, topic_id)
Индексы: idx_sc_topic(topic_id), idx_sc_user_score2(user_id, score desc)

Примечание: таблица user_candidates сохранена для обратной совместимости API, но новая логика пишет в student_candidates / supervisor_candidates.

## messages — сообщения‑заявки (запрос на курирование или участие)
- id: bigserial, PK
- sender_user_id: bigint, NOT NULL, FK → users.id — отправитель
- receiver_user_id: bigint, NOT NULL, FK → users.id — получатель
- topic_id: bigint, NOT NULL, FK → topics.id — тема (всегда указывается)
- role_id: bigint, NULL, FK → roles.id — роль (указывать, если запрос по конкретной роли)
- body: text, NOT NULL — текст сообщения
- status: varchar(20), NOT NULL, DEFAULT 'pending' — 'pending' | 'accepted' | 'rejected' | 'canceled'
- answer: text — ответ получателя (опционально)
- created_at: timestamptz, NOT NULL, DEFAULT now()
- responded_at: timestamptz — когда дан ответ

Индексы: idx_messages_receiver(receiver_user_id, status), idx_messages_sender(sender_user_id, status), idx_messages_topic(topic_id)

---

## Соответствие новой Google‑формы (студенты)

Блок 1:
- «Отметка времени» → users.created_at (только для логов), парсится в `timestamp`
- «Адрес электронной почты» → users.email
- «Введите ФИО» → users.full_name
- «Введите ник Telegram (в виде https://t.me/...)» → users.username (очистка до username)
- «Ваше направление» → student_profiles.program
- «Разработка/Наука/Стартап — трек вашего развития?» → student_profiles.dev_track / science_track / startup_track (0..5)
- «Ваши Hard Skills (знаю)» → student_profiles.skills (CSV)
- «Hard Skills (хочу изучить)» → student_profiles.skills_to_learn (CSV)
- «Область научного/профессионального интереса» → student_profiles.interests (CSV)
- «Ваше место работы и должность…» → student_profiles.workplace
- «Планируете поступать в магистратуру?» → student_profiles.apply_master (bool)
- «Дополнительная информация о себе…» → student_profiles.achievements
- «Загрузите файл… (CV, …)» → student_profiles.cv (первая ссылка)
- «В качестве вариативного задания…» → student_profiles.final_work_pref
- «ФИО предполагаемого научного руководителя… / пожелания» → student_profiles.supervisor_pref (и requirements)
- «Есть ли у вас предполагаемая тема для ВКР?» → флаг has_own_topic; если не «нет» и заполнен Блок 2 — создаём тему

Блок 2 (если ответ не «нет»):
- «Название» → topics.title
- «Описание» → topics.description
- «Практическая значимость» → добавляется в конец description
- «Имеющийся задел по теме» → добавляется в конец description и дублируется в student_profiles.groundwork
- «Ожидаемый результат…» → topics.expected_outcomes

Блок 3 (обязательный):
- «Планируете ли вы работать в команде?» → student_profiles.wants_team (да/нет/NULL)

Блок 4 (если Блок 3 не «нет»):
- «Желаемая роль в команде» → student_profiles.team_role
- «У вас уже есть в команде:» → student_profiles.team_has
- «Кто дополнительно требуется в команду» → student_profiles.team_needs
- «Выберите наиболее предпочтительный трек команды» → student_profiles.preferred_team_track

---

## Изменения в коде
- Парсер `server/parse_gform.py`:
  - Нормализация заголовков по кириллице/латинице.
  - Треки парсятся как уровни 0..5 (dev/science/startup).
  - Авто‑вывод wants_team=true, если заполнены поля Блока 4.
  - Создание темы студента: description дополнен «Практическая значимость…» и «Имеющийся задел…».
- Импортеры `server/main.py` и `server/admin.py` обновлены для поля `team_has` и треков‑уровней.
- SQL `01_schema.sql`: типы треков → SMALLINT, добавлено поле `team_has`.

