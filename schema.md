# MentorMatch: Схема БД и соответствие новой форме

Дата обновления: 2025‑09

---

## users — пользователи (студенты, научруки, админы)
- id: bigserial, PK
- telegram_id: bigint, UNIQUE
- full_name: text, NOT NULL
- email: text
- username: text — Telegram (полная ссылка вида https://t.me/<username>)
- is_confirmed: boolean — подтверждён ли пользователь в Telegram
- role: varchar(20), NOT NULL — 'student' | 'supervisor' | 'admin'
- embeddings: vector (pgvector)
- consent_personal: boolean — согласие на обработку персональных данных
- consent_private: boolean — согласие на обработку закрытых данных (если есть)
- created_at, updated_at: timestamptz, NOT NULL, DEFAULT now()

Индексы: idx_users_role(role)

## student_profiles — профиль студента (1:1 к users)
- user_id: bigint, PK, FK → users.id (ON DELETE CASCADE)
- submitted_at: timestamptz — отметка времени из формы
- isu_number: text — «Номер ИСУ»
- subdivision: text — «Подразделение МФ ТИнТ»
- direction: text — «Ваше направление»
- status: text — «Статус» (бакалавр/магистр и пр.)
- course: smallint — «Курс»
- group_number: text — «Номер группы»
- education_program: text — «Название образовательной программы»
- phone: text — «Контактный телефон»
- dev_track / science_track / startup_track: smallint — оценки треков развития (0..5)
- interests: text — «Область научного/профессионального интереса» (CSV)
- dislikes: text — «Чем вы точно не хотели бы заниматься?»
- skills: text — «Hard Skills (знаю)», CSV
- skills_to_learn: text — «Hard Skills (хочу изучить)», CSV
- commercial_experience: text — «Коммерческий опыт (компании/фриланс/ИП)»
- noncommercial_experience: text — «Некоммерческий/академический опыт»
- portfolio: text — ссылка на репозиторий или pet‑project
- achievements: text — «Информация о своих достижениях»
- hobbies: text — «Хобби и внеучебные интересы»
- cv: text — ссылка на «Резюме (CV, .pdf)»
- customer_discovery_level / sales_level / tech_execution_level / data_analytics_level /
  marketing_design_level / finance_business_level / team_leadership_level: smallint — самооценки компетенций (0..5)
- apply_master: boolean — «Планируете поступать в магистратуру / аспирантуру?»
- hours_per_week: smallint — «Сколько времени готовы тратить (часы в неделю)»
- thematic_choice: text — выбранные тематики лаборатории
- team_role: text — «Желаемая командная роль»
- plan_for_lab: text — «Что планируете выполнить за время работы…»
- motivation_letter: text — поле «Мотивационное письмо»
- police_clearance: text — ссылка/пометка «Справка об отсутствии судимости»

Примечание: skills / interests / skills_to_learn продолжают храниться в виде CSV‑строк; при необходимости их можно мигрировать в jsonb.

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
- embeddings: vector (pgvector)
- cover_media_id: bigint, FK → media_files.id (ON DELETE SET NULL)
- approved_supervisor_user_id: bigint, FK → users.id (утверждённый руководитель)
- created_at, updated_at: timestamptz, NOT NULL, DEFAULT now()

Индексы: idx_topics_author, idx_topics_seeking_role, idx_topics_direction

## roles — роли внутри темы
- id: bigserial, PK
- topic_id: bigint, NOT NULL, FK → topics.id (ON DELETE CASCADE)
- name: text, NOT NULL — название роли (например, «дизайнер», «ML‑специалист», «любая»)
- description: text — описание роли
- required_skills: text — требования к роли
- capacity: int — сколько людей нужно на эту роль (опционально)
- embeddings: vector (pgvector)
- created_at, updated_at

Индексы: idx_roles_topic(topic_id)

## approved_students — утверждённые студенты по ролям
- id: bigserial, PK
- student_id: bigint, FK → users.id (ON DELETE CASCADE)
- role_id: bigint, FK → roles.id (ON DELETE CASCADE)

PK: id
Уникальность: (role_id, student_id)
Индексы: idx_approved_students_role(role_id), idx_approved_students_student(student_id)

## requested_roles — заявки студентов на роли
- id: bigserial, PK
- student_id: bigint, FK → users.id (ON DELETE CASCADE)
- theme_name: text — название темы
- role_name: text — название роли
Уникальность: (student_id, theme_name, role_name)
Индексы: idx_requested_roles_student(student_id)

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

Все поля идут в порядке таблицы анкеты 2025/2026 и автоматически раскладываются по колонкам:

- «Отметка времени» → users.created_at (используется и для student_profiles.submitted_at). Парсится в ISO 8601.
- «Фамилия Имя Отчество» → users.full_name.
- «Номер ИСУ» → student_profiles.isu_number.
- «Подразделение МФ ТИнТ» → student_profiles.subdivision.
- «Ваше направление» → student_profiles.direction.
- «Статус» → student_profiles.status.
- «Курс» → student_profiles.course (целое число).
- «Номер группы» → student_profiles.group_number.
- «Название образовательной программы» → student_profiles.education_program.
- «E-mail» → users.email.
- «Telegram» → users.username (нормализуем до https://t.me/<username>).
- «Контактный телефон» → student_profiles.phone.
- «Разработка / Наука / Стартап — трек вашего развития?» → student_profiles.dev_track / science_track / startup_track (0..5).
- «Область научного/профессионального интереса» → student_profiles.interests (CSV).
- «Чем вы точно не хотели бы заниматься?» → student_profiles.dislikes.
- «Hard Skills (знаю)» → student_profiles.skills (CSV).
- «Hard Skills (хочу изучить)» → student_profiles.skills_to_learn (CSV).
- «Коммерческий опыт (компании/фриланс/ИП)» → student_profiles.commercial_experience.
- «Некоммерческий/академический опыт (лаборатории/НИИ/open-source)» → student_profiles.noncommercial_experience.
- «Ссылка на репозиторий (GitHub/GitLab и др.) или на pet project» → student_profiles.portfolio (берём первую ссылку).
- «Информация о своих достижениях (участие в хакатонах/конкурсах…)» → student_profiles.achievements.
- «Хобби и внеучебные интересы» → student_profiles.hobbies.
- «Резюме (CV, в формате .pdf)» → student_profiles.cv (через media_store, если это HTTP-ссылка).
- Блок самооценок «Насколько разбираешься в теме …» → customer_discovery_level, sales_level, tech_execution_level, data_analytics_level, marketing_design_level, finance_business_level, team_leadership_level (значения 0..5).
- «Планируете поступать в магистратуру / аспирантуру?» → student_profiles.apply_master (bool).
- «Сколько вы готовы тратить времени (часы в неделю)…» → student_profiles.hours_per_week.
- «Выберите тематику» → student_profiles.thematic_choice.
- «Желаемая командная роль» → student_profiles.team_role.
- «Что планируете выполнить за время работы в лаборатории LISA?» → student_profiles.plan_for_lab.
- «Мотивационное письмо» → student_profiles.motivation_letter.
- «Справка об отсутствии судимости» → student_profiles.police_clearance (ссылка/пометка).
- «Согласие на обработку персональных данных» → users.consent_personal.

Если в форме появятся дополнительные вопросы, их можно безопасно игнорировать — автоматическое создание тем из анкеты отключено.

