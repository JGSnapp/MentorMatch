"""Database access helpers for matching workflows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2.extras
from psycopg2.extensions import connection

logger = logging.getLogger(__name__)


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
    if not row:
        return None
    data = dict(row)
    data["topic"] = {
        "id": data.get("topic_id"),
        "title": data.get("topic_title"),
        "description": data.get("topic_description"),
        "expected_outcomes": data.get("topic_expected_outcomes"),
        "required_skills": data.get("topic_required_skills"),
        "direction": data.get("direction"),
        "seeking_role": data.get("seeking_role"),
        "author_user_id": data.get("author_user_id"),
        "author_name": data.get("author_name"),
    }
    return data


def fetch_candidates(
    conn: connection, topic_id: int, target_role: str, *, limit: int = 20
) -> List[Dict[str, Any]]:
    role = (target_role or "student").lower()
    role = role if role in ("student", "supervisor") else "student"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if role == "student":
            cur.execute(
                """
                SELECT
                    u.id AS user_id,
                    u.full_name,
                    u.username,
                    u.email,
                    u.created_at,
                    (u.embeddings <=> t.embeddings) AS distance,
                    sp.program,
                    sp.skills,
                    sp.interests,
                    sp.cv,
                    sp.skills_to_learn,
                    sp.preferred_team_track,
                    sp.team_has AS team_role,
                    sp.team_needs,
                    sp.dev_track,
                    sp.science_track,
                    sp.startup_track
                FROM topics t
                JOIN users u ON LOWER(u.role) = 'student' AND u.embeddings IS NOT NULL
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE t.id = %s
                  AND t.embeddings IS NOT NULL
                ORDER BY u.embeddings <=> t.embeddings ASC
                LIMIT %s
                """,
                (topic_id, limit),
            )
        else:
            cur.execute(
                """
                SELECT
                    u.id AS user_id,
                    u.full_name,
                    u.username,
                    u.email,
                    u.created_at,
                    (u.embeddings <=> t.embeddings) AS distance,
                    sp.position,
                    sp.degree,
                    sp.capacity,
                    sp.interests
                FROM topics t
                JOIN users u ON LOWER(u.role) = 'supervisor'
                    AND u.embeddings IS NOT NULL
                    AND u.id <> t.author_user_id
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE t.id = %s
                  AND t.embeddings IS NOT NULL
                ORDER BY u.embeddings <=> t.embeddings ASC
                LIMIT %s
                """,
                (topic_id, limit),
            )
        rows = cur.fetchall()

    candidates: List[Dict[str, Any]] = []
    log_payload: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        distance = data.pop("distance", None)
        score: Optional[float] = None
        if distance is not None:
            distance = float(distance)
            score = 1.0 - distance
        data["score"] = score
        candidates.append(data)
        log_payload.append(
            {
                "id": data.get("user_id"),
                "full_name": data.get("full_name"),
                "score": score,
                "distance": distance,
            }
        )

    if log_payload:
        logger.info(
            "Top %s %s candidates for topic %s by cosine distance: %s",
            len(log_payload),
            role,
            topic_id,
            log_payload,
        )

    return candidates


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


def fetch_roles_needing_students(
    conn: connection, student_user_id: int, limit: int = 40
) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                r.id,
                r.name,
                r.description,
                r.required_skills,
                r.capacity,
                t.id AS topic_id,
                t.title AS topic_title,
                t.direction,
                t.author_user_id,
                author.full_name AS author_name,
                (r.embeddings <=> su.embeddings) AS distance
            FROM users su
            JOIN roles r ON r.embeddings IS NOT NULL
            JOIN topics t ON t.id = r.topic_id
                AND t.is_active = TRUE
                AND t.seeking_role = 'student'
            JOIN users author ON author.id = t.author_user_id
            WHERE su.id = %s
              AND su.embeddings IS NOT NULL
              AND LOWER(su.role) = 'student'
            ORDER BY r.embeddings <=> su.embeddings ASC
            LIMIT %s
            """,
            (student_user_id, limit),
        )
        rows = cur.fetchall()

    roles: List[Dict[str, Any]] = []
    log_payload: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        distance = data.pop("distance", None)
        score: Optional[float] = None
        if distance is not None:
            distance = float(distance)
            score = 1.0 - distance
        data["score"] = score
        roles.append(data)
        log_payload.append(
            {
                "role_id": data.get("id"),
                "topic_id": data.get("topic_id"),
                "score": score,
                "distance": distance,
            }
        )

    if log_payload:
        logger.info(
            "Top %s role matches for student %s by cosine distance: %s",
            len(log_payload),
            student_user_id,
            log_payload,
        )

    return roles


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


def fetch_topics_needing_supervisors(
    conn: connection, supervisor_user_id: int, limit: int = 20
) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.title,
                t.description,
                t.required_skills,
                t.expected_outcomes,
                t.author_user_id,
                author.full_name AS author_name,
                (t.embeddings <=> sup.embeddings) AS distance
            FROM users sup
            JOIN topics t ON t.embeddings IS NOT NULL
                AND t.is_active = TRUE
                AND t.seeking_role = 'supervisor'
            JOIN users author ON author.id = t.author_user_id
            WHERE sup.id = %s
              AND sup.embeddings IS NOT NULL
              AND LOWER(sup.role) = 'supervisor'
            ORDER BY t.embeddings <=> sup.embeddings ASC
            LIMIT %s
            """,
            (supervisor_user_id, limit),
        )
        rows = cur.fetchall()

    topics: List[Dict[str, Any]] = []
    log_payload: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        distance = data.pop("distance", None)
        score: Optional[float] = None
        if distance is not None:
            distance = float(distance)
            score = 1.0 - distance
        data["score"] = score
        topics.append(data)
        log_payload.append(
            {
                "topic_id": data.get("id"),
                "title": data.get("title"),
                "score": score,
                "distance": distance,
            }
        )

    if log_payload:
        logger.info(
            "Top %s topic matches for supervisor %s by cosine distance: %s",
            len(log_payload),
            supervisor_user_id,
            log_payload,
        )

    return topics


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
