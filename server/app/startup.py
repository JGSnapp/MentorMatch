"""Startup hooks and lightweight migrations."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Iterable

import psycopg2

from .helpers import read_csv_rows, truthy

logger = logging.getLogger(__name__)

ConnectionFactory = Callable[[], psycopg2.extensions.connection]


def maybe_seed_test_data(conn_factory: ConnectionFactory, templates_dir: Path) -> None:
    if not truthy(os.getenv("TEST_IMPORT")):
        return

    sup_rows = read_csv_rows(templates_dir / "test_supervisors.csv")
    top_rows = read_csv_rows(templates_dir / "test_topics.csv")
    if not (sup_rows or top_rows):
        return

    try:
        with conn_factory() as conn, conn.cursor() as cur:
            for row in sup_rows:
                full_name = (row.get("full_name") or "").strip()
                email = (row.get("email") or "").strip() or None
                username = (row.get("username") or "").strip() or None
                if not full_name:
                    continue
                if email:
                    cur.execute(
                        "SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND role='supervisor' LIMIT 1",
                        (email,),
                    )
                else:
                    cur.execute(
                        "SELECT id FROM users WHERE full_name=%s AND role='supervisor' LIMIT 1",
                        (full_name,),
                    )
                row_db = cur.fetchone()
                if row_db:
                    user_id = row_db[0]
                else:
                    cur.execute(
                        """
                        INSERT INTO users(full_name, email, username, role, created_at, updated_at)
                        VALUES (%s, %s, %s, 'supervisor', now(), now())
                        RETURNING id
                        """,
                        (full_name, email, username),
                    )
                    user_id = cur.fetchone()[0]
                cur.execute("SELECT 1 FROM supervisor_profiles WHERE user_id=%s", (user_id,))
                profile_exists = cur.fetchone()
                params = (
                    row.get("position") or None,
                    row.get("degree") or None,
                    int(row.get("capacity") or 0) or None,
                    row.get("interests") or None,
                    row.get("requirements") or None,
                    user_id,
                )
                if profile_exists:
                    cur.execute(
                        """
                        UPDATE supervisor_profiles
                        SET position=%s, degree=%s, capacity=%s, interests=%s, requirements=%s
                        WHERE user_id=%s
                        """,
                        params,
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        params,
                    )

            for row in top_rows:
                title = (row.get("title") or "").strip()
                if not title:
                    continue
                author_full_name = (row.get("author_full_name") or "").strip() or "Unknown Supervisor"
                cur.execute(
                    "SELECT id FROM users WHERE full_name=%s AND role='supervisor' LIMIT 1",
                    (author_full_name,),
                )
                author_row = cur.fetchone()
                if author_row:
                    author_id = author_row[0]
                else:
                    cur.execute(
                        "INSERT INTO users(full_name, role, created_at, updated_at) VALUES (%s,'supervisor', now(), now()) RETURNING id",
                        (author_full_name,),
                    )
                    author_id = cur.fetchone()[0]
                cur.execute(
                    "SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s",
                    (author_id, title),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """
                    INSERT INTO topics(author_user_id, title, description, expected_outcomes, required_skills,
                                       seeking_role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, now(), now())
                    """,
                    (
                        author_id,
                        title,
                        row.get("description") or None,
                        row.get("expected_outcomes") or None,
                        row.get("required_skills") or None,
                        row.get("seeking_role") or "student",
                    ),
                )
            conn.commit()
    except Exception as exc:  # pragma: no cover - best effort seed
        logger.warning("TEST_IMPORT failed: %s", exc)


def run_lightweight_migrations(conn_factory: ConnectionFactory) -> None:
    with conn_factory() as conn, conn.cursor() as cur:
        statements: Iterable[str] = [
            """
            CREATE TABLE IF NOT EXISTS user_candidates (
              user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              topic_id     BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
              score        DOUBLE PRECISION,
              is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
              approved     BOOLEAN NOT NULL DEFAULT FALSE,
              rank         SMALLINT,
              created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (user_id, topic_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_uc_topic ON user_candidates(topic_id)",
            "CREATE INDEX IF NOT EXISTS idx_uc_user_score ON user_candidates(user_id, score DESC)",
            """
            CREATE TABLE IF NOT EXISTS roles (
              id BIGSERIAL PRIMARY KEY,
              topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT,
              required_skills TEXT,
              capacity INTEGER,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_roles_topic ON roles(topic_id)",
            """
            CREATE TABLE IF NOT EXISTS role_candidates (
              role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              score DOUBLE PRECISION,
              is_primary BOOLEAN NOT NULL DEFAULT FALSE,
              approved BOOLEAN NOT NULL DEFAULT FALSE,
              rank SMALLINT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (role_id, user_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_rc_role_score ON role_candidates(role_id, score DESC)",
            """
            CREATE TABLE IF NOT EXISTS student_candidates (
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
              score DOUBLE PRECISION,
              is_primary BOOLEAN NOT NULL DEFAULT FALSE,
              approved BOOLEAN NOT NULL DEFAULT FALSE,
              rank SMALLINT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (user_id, role_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_sc_user_score ON student_candidates(user_id, score DESC)",
            """
            CREATE TABLE IF NOT EXISTS supervisor_candidates (
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
              score DOUBLE PRECISION,
              is_primary BOOLEAN NOT NULL DEFAULT FALSE,
              approved BOOLEAN NOT NULL DEFAULT FALSE,
              rank SMALLINT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (user_id, topic_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_sc_topic ON supervisor_candidates(topic_id)",
            "CREATE INDEX IF NOT EXISTS idx_sc_user_score2 ON supervisor_candidates(user_id, score DESC)",
            "ALTER TABLE topics ADD COLUMN IF NOT EXISTS direction SMALLINT",
            "CREATE INDEX IF NOT EXISTS idx_topics_direction ON topics(direction)",
            "ALTER TABLE topics ADD COLUMN IF NOT EXISTS approved_supervisor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL",
            "ALTER TABLE roles ADD COLUMN IF NOT EXISTS approved_student_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL",
            """
            CREATE TABLE IF NOT EXISTS messages (
              id BIGSERIAL PRIMARY KEY,
              sender_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              receiver_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
              role_id BIGINT REFERENCES roles(id) ON DELETE SET NULL,
              body TEXT NOT NULL,
              status VARCHAR(20) NOT NULL DEFAULT 'pending',
              answer TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              responded_at TIMESTAMPTZ
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_user_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_user_id, status)",
        ]
        for statement in statements:
            try:
                cur.execute(statement)
            except Exception as exc:  # pragma: no cover - safety first
                logger.warning("Failed to execute startup statement: %s", exc)
        conn.commit()


def register_startup_events(app, conn_factory: ConnectionFactory, templates_dir: Path) -> None:
    @app.on_event("startup")
    async def _startup_event() -> None:  # pragma: no cover - integration behaviour
        run_lightweight_migrations(conn_factory)
        maybe_seed_test_data(conn_factory, templates_dir)
