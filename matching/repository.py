"""Database access helpers for matching workflows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2.extras
from psycopg2.extensions import connection

logger = logging.getLogger(__name__)

STUDENT_PROFILE_COLUMNS_SQL = """
        sp.isu_number,
        sp.subdivision,
        sp.direction,
        sp.status,
        sp.course,
        sp.group_number,
        sp.education_program,
        sp.phone,
        sp.dev_track,
        sp.science_track,
        sp.startup_track,
        sp.interests,
        sp.dislikes,
        sp.skills,
        sp.skills_to_learn,
        sp.commercial_experience,
        sp.noncommercial_experience,
        sp.portfolio,
        sp.achievements,
        sp.hobbies,
        sp.cv,
        sp.customer_discovery_level,
        sp.sales_level,
        sp.tech_execution_level,
        sp.data_analytics_level,
        sp.marketing_design_level,
        sp.finance_business_level,
        sp.team_leadership_level,
        sp.apply_master,
        sp.hours_per_week,
        sp.thematic_choice,
        sp.team_role,
        sp.plan_for_lab,
        sp.motivation_letter,
        sp.police_clearance
    """


def fetch_topic(conn: connection, topic_id: int) -> Optional[Dict[str, Any]]:
    """Выполняет функцию fetch_topic."""
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
    """Выполняет функцию fetch_role."""
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


def fetch_role_applicants(
    conn: connection,
    role_id: int,
    *,
    statuses: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Возвращает студентов, подавших заявки на роль, отсортированных по расстоянию."""

    statuses = statuses or ["pending", "accepted"]

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT * FROM (
                SELECT
                    u.id AS user_id,
                    u.full_name,
                    u.username,
                    u.email,
                    u.created_at,
                    (u.embeddings <=> r.embeddings) AS distance,
                    m.created_at AS last_applied_at,
                    {STUDENT_PROFILE_COLUMNS_SQL},
                    ROW_NUMBER() OVER (PARTITION BY u.id ORDER BY m.created_at DESC) AS rn
                FROM roles r
                JOIN messages m ON m.role_id = r.id
                JOIN users u ON u.id = m.sender_user_id
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE r.id = %s
                  AND r.embeddings IS NOT NULL
                  AND u.embeddings IS NOT NULL
                  AND (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
                  AND m.status = ANY(%s)
            ) ranked
            WHERE rn = 1
            ORDER BY distance ASC NULLS LAST, last_applied_at DESC
            LIMIT %s
            """,
            (role_id, statuses, limit),
        )
        rows = cur.fetchall()

    applicants: List[Dict[str, Any]] = []
    log_payload: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        distance = data.pop("distance", None)
        score: Optional[float] = None
        if distance is not None:
            distance = float(distance)
            score = 1.0 - distance
        data["score"] = score
        applicants.append(data)
        log_payload.append(
            {
                "id": data.get("user_id"),
                "full_name": data.get("full_name"),
                "score": score,
                "distance": distance,
                "applied_at": data.get("last_applied_at"),
            }
        )

    if log_payload:
        logger.info(
            "Top %s applicants for role %s by cosine distance: %s",
            len(log_payload),
            role_id,
            log_payload,
        )

    return applicants


def fetch_topic_applicants(
    conn: connection,
    topic_id: int,
    *,
    statuses: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Возвращает студентов, подавших заявки на тему, отсортированных по расстоянию."""

    statuses = statuses or ["pending", "accepted"]

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT * FROM (
                SELECT
                    u.id AS user_id,
                    u.full_name,
                    u.username,
                    u.email,
                    u.created_at,
                    (u.embeddings <=> t.embeddings) AS distance,
                    m.created_at AS last_applied_at,
                    {STUDENT_PROFILE_COLUMNS_SQL},
                    ROW_NUMBER() OVER (PARTITION BY u.id ORDER BY m.created_at DESC) AS rn
                FROM topics t
                JOIN messages m ON m.topic_id = t.id
                JOIN users u ON u.id = m.sender_user_id
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE t.id = %s
                  AND t.embeddings IS NOT NULL
                  AND u.embeddings IS NOT NULL
                  AND (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
                  AND m.status = ANY(%s)
            ) ranked
            WHERE rn = 1
            ORDER BY distance ASC NULLS LAST, last_applied_at DESC
            LIMIT %s
            """,
            (topic_id, statuses, limit),
        )
        rows = cur.fetchall()

    applicants: List[Dict[str, Any]] = []
    log_payload: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        distance = data.pop("distance", None)
        score: Optional[float] = None
        if distance is not None:
            distance = float(distance)
            score = 1.0 - distance
        data["score"] = score
        applicants.append(data)
        log_payload.append(
            {
                "id": data.get("user_id"),
                "full_name": data.get("full_name"),
                "score": score,
                "distance": distance,
                "applied_at": data.get("last_applied_at"),
            }
        )

    if log_payload:
        logger.info(
            "Top %s applicants for topic %s by cosine distance: %s",
            len(log_payload),
            topic_id,
            log_payload,
        )

    return applicants


def fetch_candidates(
    conn: connection, topic_id: int, target_role: str, *, limit: int = 20
) -> List[Dict[str, Any]]:
    """Выполняет функцию fetch_candidates."""
    role = (target_role or "student").lower()
    role = role if role in ("student", "supervisor") else "student"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if role == "student":
            cur.execute(
                f"""
                SELECT
                    u.id AS user_id,
                    u.full_name,
                    u.username,
                    u.email,
                    u.created_at,
                    (u.embeddings <=> t.embeddings) AS distance,
                    {STUDENT_PROFILE_COLUMNS_SQL}
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
    """Выполняет функцию fetch_student."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT u.id AS user_id, u.full_name, u.username, u.email,
                   {STUDENT_PROFILE_COLUMNS_SQL}
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s AND (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
            """,
            (student_user_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_topics_needing_students(conn: connection, limit: int = 20) -> List[Dict[str, Any]]:
    """Выполняет функцию fetch_topics_needing_students."""
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
    """Выполняет функцию fetch_roles_needing_students."""
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
    """Выполняет функцию fetch_supervisor."""
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
    """Выполняет функцию fetch_topics_needing_supervisors."""
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
