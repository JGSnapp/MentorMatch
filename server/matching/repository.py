"""Database access helpers for matching workflows."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import psycopg2.extras
from psycopg2.extensions import connection


def fetch_topic(conn: connection, topic_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT t.*, u.full_name AS author_name, u.id AS author_id
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.id = %s
            """,
            (topic_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_role(conn: connection, role_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT r.*, t.title AS topic_title, t.description AS topic_description,
                   t.required_skills AS topic_required_skills, t.expected_outcomes AS topic_expected_outcomes,
                   t.seeking_role, t.direction, t.author_user_id, u.full_name AS author_name
            FROM roles r
            JOIN topics t ON t.id = r.topic_id
            JOIN users u ON u.id = t.author_user_id
            WHERE r.id = %s
            """,
            (role_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_candidates(
    conn: connection, topic_id: int, target_role: str, *, limit: int = 20
) -> List[Dict[str, Any]]:
    role = (target_role or "student").lower()
    role = role if role in ("student", "supervisor") else "student"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if role == "student":
            try:
                cur.execute(
                    """
                    SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                           tc.score,
                           sp.program, sp.skills, sp.interests, sp.cv,
                           sp.skills_to_learn, sp.preferred_team_track, sp.team_has AS team_role, sp.team_needs,
                           sp.dev_track, sp.science_track, sp.startup_track
                    FROM topic_candidates tc
                    JOIN users u ON u.id = tc.user_id
                    LEFT JOIN student_profiles sp ON sp.user_id = u.id
                    WHERE tc.topic_id = %s
                    AND (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
                    ORDER BY tc.score DESC NULLS LAST, u.created_at DESC
                    LIMIT %s
                    """,
                    (topic_id, limit),
                )
                rows = cur.fetchall()
                if rows:
                    return [dict(r) for r in rows]
            except Exception:
                pass

            cur.execute(
                """
                SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                       NULL::double precision AS score,
                       sp.program, sp.skills, sp.interests, sp.cv,
                       sp.skills_to_learn, sp.preferred_team_track, sp.team_has AS team_role, sp.team_needs,
                       sp.dev_track, sp.science_track, sp.startup_track
                FROM users u
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
                ORDER BY u.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

        try:
            cur.execute(
                """
                SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                       tc.score,
                       sp.position, sp.degree, sp.capacity, sp.interests
                FROM topic_candidates tc
                JOIN users u ON u.id = tc.user_id AND LOWER(u.role) = 'supervisor'
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE tc.topic_id = %s
                ORDER BY tc.score DESC NULLS LAST, u.created_at DESC
                LIMIT %s
                """,
                (topic_id, limit),
            )
            rows = cur.fetchall()
            if rows:
                return [dict(r) for r in rows]
        except Exception:
            pass

        cur.execute(
            """
            SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                   NULL::double precision AS score,
                   sp.position, sp.degree, sp.capacity, sp.interests
            FROM users u
            LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
            WHERE LOWER(u.role) = 'supervisor'
            ORDER BY u.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_student(conn: connection, student_user_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT u.id AS user_id, u.full_name, u.username, u.email,
                   sp.program, sp.skills, sp.interests, sp.cv,
                   sp.skills_to_learn, sp.preferred_team_track, sp.team_has AS team_role, sp.team_needs,
                   sp.dev_track, sp.science_track, sp.startup_track
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s AND (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
            """,
            (student_user_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_topics_needing_students(conn: connection, limit: int = 20) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT t.id, t.title, t.description, t.required_skills, t.expected_outcomes,
                   t.author_user_id, u.full_name AS author_name, t.created_at
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.is_active = TRUE AND t.seeking_role = 'student'
            ORDER BY t.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_roles_needing_students(conn: connection, limit: int = 40) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT r.id, r.name, r.description, r.required_skills, r.capacity,
                   t.id AS topic_id, t.title AS topic_title, t.direction,
                   t.author_user_id, u.full_name AS author_name
            FROM roles r
            JOIN topics t ON t.id = r.topic_id AND t.is_active = TRUE AND t.seeking_role = 'student'
            JOIN users u ON u.id = t.author_user_id
            ORDER BY t.created_at DESC, r.id ASC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_supervisor(conn: connection, supervisor_user_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT u.id AS user_id, u.full_name, u.username, u.email,
                   sp.position, sp.degree, sp.capacity, sp.interests, sp.requirements
            FROM users u
            LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s AND LOWER(u.role) = 'supervisor'
            """,
            (supervisor_user_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_topics_needing_supervisors(conn: connection, limit: int = 20) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT t.id, t.title, t.description, t.required_skills, t.expected_outcomes,
                   t.author_user_id, u.full_name AS author_name, t.created_at
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.is_active = TRUE AND t.seeking_role = 'supervisor'
            ORDER BY t.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


__all__ = [
    "fetch_topic",
    "fetch_role",
    "fetch_candidates",
    "fetch_student",
    "fetch_topics_needing_students",
    "fetch_roles_needing_students",
    "fetch_supervisor",
    "fetch_topics_needing_supervisors",
]
