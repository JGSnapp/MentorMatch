-- MentorMatch DB schema (PostgreSQL 16+)
-- This file is executed automatically by the official postgres image
-- when mounted into /docker-entrypoint-initdb.d on first container start.

BEGIN;

-- =====================
-- Users & Profiles
-- =====================

CREATE TABLE users (
  id              BIGSERIAL PRIMARY KEY,
  telegram_id     BIGINT UNIQUE,
  full_name       TEXT NOT NULL,
  email           TEXT,
  username        TEXT,
  role            VARCHAR(20) NOT NULL, -- 'student' | 'supervisor' | 'admin'
  embeddings      TEXT,
  consent_personal BOOLEAN,
  consent_private  BOOLEAN,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_role ON users(role);

CREATE TABLE student_profiles (
  user_id         BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  course          SMALLINT,
  program         TEXT,
  faculty         TEXT,
  education       TEXT,
  skills          TEXT, -- JSON/CSV
  interests       TEXT, -- JSON/CSV
  cv              TEXT,
  requirements    TEXT,
  assignments     TEXT,  -- JSON summary for UI
  skills_to_learn TEXT,
  achievements    TEXT,
  supervisor_pref TEXT,
  groundwork      TEXT,
  wants_team      BOOLEAN,
  team_role       TEXT,

  team_has        TEXT,

  team_needs      TEXT,
  apply_master    BOOLEAN,
  workplace       TEXT,
  preferred_team_track TEXT,
  dev_track       SMALLINT,
  science_track   SMALLINT,
  startup_track   SMALLINT,
  final_work_pref TEXT
);

CREATE TABLE supervisor_profiles (
  user_id         BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  position        TEXT,
  degree          TEXT,
  capacity        INTEGER,
  requirements    TEXT,
  interests       TEXT
);

-- =====================
-- Media
-- =====================

CREATE TABLE media_files (
  id               BIGSERIAL PRIMARY KEY,
  owner_user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
  object_key       TEXT NOT NULL,                     -- storage key/path (S3/MinIO/local)
  provider         VARCHAR(20) NOT NULL,              -- 's3' | 'tg' | 'local' | ...
  mime_type        TEXT NOT NULL,
  size_bytes       BIGINT,
  width            INTEGER,
  height           INTEGER,
  duration_seconds DOUBLE PRECISION,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_media_provider CHECK (provider IN ('s3','tg','local'))
);

CREATE INDEX idx_media_owner ON media_files(owner_user_id);
CREATE INDEX idx_media_object_key ON media_files(object_key);

-- =====================
-- Topics & Candidates
-- =====================

CREATE TABLE topics (
  id                BIGSERIAL PRIMARY KEY,
  author_user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title             TEXT NOT NULL,
  description       TEXT,
  expected_outcomes TEXT,
  required_skills   TEXT,
  direction         SMALLINT,                      -- 9 | 11 | 45 (опционально)
  seeking_role      VARCHAR(20) NOT NULL, -- 'student' | 'supervisor'
  embeddings        TEXT,
  cover_media_id    BIGINT REFERENCES media_files(id) ON DELETE SET NULL,
  approved_supervisor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
  is_active         BOOLEAN NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_topics_author ON topics(author_user_id);
CREATE INDEX idx_topics_seeking_role ON topics(seeking_role);
CREATE INDEX idx_topics_active ON topics(is_active);
CREATE INDEX idx_topics_direction ON topics(direction);

CREATE TABLE topic_candidates (
  topic_id      BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  score         DOUBLE PRECISION,
  is_primary    BOOLEAN NOT NULL DEFAULT FALSE,
  approved      BOOLEAN NOT NULL DEFAULT FALSE,
  rank          SMALLINT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (topic_id, user_id)
);

CREATE INDEX idx_tc_user ON topic_candidates(user_id);
CREATE INDEX idx_tc_topic_score ON topic_candidates(topic_id, score DESC);

-- Mirror table: topics recommended to users (student or supervisor)
-- Stores top-N topics per user (result of matching initiated by user profile)
CREATE TABLE user_candidates (
  user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id     BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  score        DOUBLE PRECISION,
  is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
  approved     BOOLEAN NOT NULL DEFAULT FALSE,
  rank         SMALLINT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, topic_id)
);

CREATE INDEX idx_uc_topic ON user_candidates(topic_id);
CREATE INDEX idx_uc_user_score ON user_candidates(user_id, score DESC);

-- ============
-- Roles
-- ============

-- Roles per topic
CREATE TABLE roles (
  id              BIGSERIAL PRIMARY KEY,
  topic_id        BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  description     TEXT,
  required_skills TEXT,
  capacity        INTEGER,
  approved_student_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_roles_topic ON roles(topic_id);

-- Students recommended for a role (matching: role -> students)
CREATE TABLE role_candidates (
  role_id     BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- student
  score       DOUBLE PRECISION,
  is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
  approved    BOOLEAN NOT NULL DEFAULT FALSE,
  rank        SMALLINT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (role_id, user_id)
);

CREATE INDEX idx_rc_role_score ON role_candidates(role_id, score DESC);

-- Roles recommended for a student (matching: student -> roles)
CREATE TABLE student_candidates (
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- student
  role_id     BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  score       DOUBLE PRECISION,
  is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
  approved    BOOLEAN NOT NULL DEFAULT FALSE,
  rank        SMALLINT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role_id)
);

CREATE INDEX idx_sc_user_score ON student_candidates(user_id, score DESC);

-- Topics recommended for a supervisor (matching: supervisor -> topics)
CREATE TABLE supervisor_candidates (
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- supervisor
  topic_id    BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  score       DOUBLE PRECISION,
  is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
  approved    BOOLEAN NOT NULL DEFAULT FALSE,
  rank        SMALLINT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, topic_id)
);

CREATE INDEX idx_sc_topic ON supervisor_candidates(topic_id);
CREATE INDEX idx_sc_user_score2 ON supervisor_candidates(user_id, score DESC);

-- =====================
-- Messages (Requests)
-- =====================

CREATE TABLE messages (
  id               BIGSERIAL PRIMARY KEY,
  sender_user_id   BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  receiver_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id         BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  role_id          BIGINT REFERENCES roles(id) ON DELETE SET NULL,
  body             TEXT NOT NULL,
  status           VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending|accepted|rejected|canceled
  answer           TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  responded_at     TIMESTAMPTZ
);

CREATE INDEX idx_messages_receiver ON messages(receiver_user_id, status);
CREATE INDEX idx_messages_sender ON messages(sender_user_id, status);
CREATE INDEX idx_messages_topic ON messages(topic_id);

-- =====================
-- Assignments & Submissions
-- =====================

CREATE TABLE assignments (
  id               BIGSERIAL PRIMARY KEY,
  author_user_id   BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id         BIGINT REFERENCES topics(id) ON DELETE SET NULL,
  title            TEXT NOT NULL,
  description      TEXT,
  due_at           TIMESTAMPTZ,
  max_score        DOUBLE PRECISION,
  is_optional      BOOLEAN NOT NULL DEFAULT FALSE,
  attempts_limit   INTEGER,                        -- NULL = unlimited
  correct_answer   TEXT,                           -- for 'equals' or hints
  check_type       VARCHAR(20) NOT NULL,           -- 'compiler' | 'equals' | 'llm' | 'manual'
  media_file_id    BIGINT REFERENCES media_files(id) ON DELETE SET NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_assign_check_type CHECK (check_type IN ('compiler','equals','llm','manual'))
);

CREATE INDEX idx_assign_author ON assignments(author_user_id);
CREATE INDEX idx_assign_topic ON assignments(topic_id);

CREATE TABLE completed_assignments (
  assignment_id          BIGINT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  student_user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  solution_text          TEXT,
  solution_media_file_id BIGINT REFERENCES media_files(id) ON DELETE SET NULL,
  score                  DOUBLE PRECISION,
  feedback               TEXT,
  submitted_at           TIMESTAMPTZ,
  graded_at              TIMESTAMPTZ,
  grader_user_id         BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (assignment_id, student_user_id)
);

CREATE INDEX idx_completed_by_student ON completed_assignments(student_user_id);

-- =====================
-- Chat
-- =====================

CREATE TABLE chat_threads (
  id            BIGSERIAL PRIMARY KEY,
  user_a_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  user_b_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id      BIGINT REFERENCES topics(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at     TIMESTAMPTZ
);

CREATE INDEX idx_threads_user_a ON chat_threads(user_a_id);
CREATE INDEX idx_threads_user_b ON chat_threads(user_b_id);
CREATE INDEX idx_threads_topic ON chat_threads(topic_id);

CREATE TABLE chat_messages (
  id              BIGSERIAL PRIMARY KEY,
  thread_id       BIGINT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  sender_user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  message_text    TEXT,
  media_file_id   BIGINT REFERENCES media_files(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_msgs_thread ON chat_messages(thread_id);
CREATE INDEX idx_msgs_sender ON chat_messages(sender_user_id);

COMMIT;
