"""High level orchestration for matching flows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2.extras
from psycopg2.extensions import connection

from .cv import resolve_cv_text
from .llm import MatchingLLMClient, create_matching_llm_client
from .payloads import (
    build_candidates_payload,
    build_role_candidates_payload,
    build_roles_for_student_payload,
    build_topics_for_supervisor_payload,
    dumps as dumps_payload,
)
from .repository import (
    fetch_candidates,
    fetch_role,
    fetch_roles_needing_students,
    fetch_student,
    fetch_supervisor,
    fetch_topic,
    fetch_topics_needing_supervisors,
)

logger = logging.getLogger(__name__)


def _pick_llm(llm: Optional[MatchingLLMClient]) -> Optional[MatchingLLMClient]:
    return llm or create_matching_llm_client()


def _enrich_cv(conn: connection, candidates: List[Dict[str, Any]]) -> None:
    for candidate in candidates:
        candidate["cv"] = resolve_cv_text(conn, candidate.get("cv"))


def _fallback_top5(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "user_id": candidate.get("user_id"),
            "num": idx,
            "reason": "LLM недоступен: выводим последних пяти кандидатов.",
        }
        for idx, candidate in enumerate(candidates[:5], start=1)
    ]


def _fallback_top5_topics(topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "topic_id": topic.get("id"),
            "num": idx,
            "reason": "LLM недоступен: используем последние темы.",
        }
        for idx, topic in enumerate(topics[:5], start=1)
    ]


def _fallback_top5_roles(roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "role_id": role.get("id"),
            "num": idx,
            "reason": "LLM недоступен: берём последние роли.",
        }
        for idx, role in enumerate(roles[:5], start=1)
    ]


def handle_match(
    conn: connection,
    topic_id: int,
    *,
    target_role: Optional[str] = None,
    llm_client: Optional[MatchingLLMClient] = None,
) -> Dict[str, Any]:
    topic = fetch_topic(conn, topic_id)
    if not topic:
        return {"status": "error", "message": f"Topic #{topic_id} not found"}

    role = (target_role or topic.get("seeking_role") or "student").lower()
    if role not in ("student", "supervisor"):
        role = "student"

    candidates = fetch_candidates(conn, topic_id, role, limit=20)
    _enrich_cv(conn, candidates)

    ranked = _fallback_top5(candidates)
    if len(candidates) >= 5:
        payload_json = dumps_payload(build_candidates_payload(topic, candidates, role))
        llm = _pick_llm(llm_client)
        if llm:
            ranked = llm.rank_candidates(payload_json) or ranked

    by_id = {c.get("user_id"): c for c in candidates}
    items: List[Dict[str, Any]] = []
    for position, result in enumerate(ranked, start=1):
        candidate = by_id.get(result.get("user_id"))
        if not candidate and isinstance(result.get("num"), int):
            idx = result["num"] - 1
            if 0 <= idx < len(candidates):
                candidate = candidates[idx]
        if not candidate:
            continue
        items.append(
            {
                "rank": position,
                "user_id": candidate.get("user_id"),
                "full_name": candidate.get("full_name"),
                "role": role,
                "reason": result.get("reason"),
                "original_score": candidate.get("score"),
            }
        )

    if role == "supervisor" and items:
        try:
            with conn.cursor() as cur:
                for row in items:
                    score = float(6 - row["rank"])
                    cur.execute(
                        """
                        INSERT INTO topic_candidates(topic_id, user_id, score, is_primary, approved, rank, created_at)
                        VALUES (%s, %s, %s, %s, FALSE, %s, now())
                        ON CONFLICT (topic_id, user_id)
                        DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                        """,
                        (
                            topic_id,
                            row["user_id"],
                            score,
                            row["rank"] == 1,
                            row["rank"],
                        ),
                    )
            conn.commit()
        except Exception as exc:  # pragma: no cover - database failure is logged
            logger.warning("Failed to persist supervisor candidates: %s", exc)

    return {
        "status": "ok",
        "topic_id": topic_id,
        "target_role": role,
        "topic_title": topic.get("title"),
        "items": items,
    }


def handle_match_role(
    conn: connection,
    role_id: int,
    *,
    llm_client: Optional[MatchingLLMClient] = None,
) -> Dict[str, Any]:
    role_row = fetch_role(conn, role_id)
    if not role_row:
        return {"status": "error", "message": f"Role #{role_id} not found"}

    topic = fetch_topic(conn, role_row["topic_id"])
    if not topic:
        return {"status": "error", "message": f"Topic #{role_row['topic_id']} not found"}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
            (20,),
        )
        candidates = [dict(row) for row in cur.fetchall()]

    _enrich_cv(conn, candidates)
    ranked = _fallback_top5(candidates)
    if len(candidates) >= 5:
        payload_json = dumps_payload(
            build_role_candidates_payload(topic, role_row, candidates)
        )
        llm = _pick_llm(llm_client)
        if llm:
            ranked = llm.rank_candidates(payload_json) or ranked

    by_id = {c.get("user_id"): c for c in candidates}
    items: List[Dict[str, Any]] = []
    for position, result in enumerate(ranked, start=1):
        candidate = by_id.get(result.get("user_id"))
        if not candidate and isinstance(result.get("num"), int):
            idx = result["num"] - 1
            if 0 <= idx < len(candidates):
                candidate = candidates[idx]
        if not candidate:
            continue
        items.append(
            {
                "rank": position,
                "user_id": candidate.get("user_id"),
                "full_name": candidate.get("full_name"),
                "reason": result.get("reason"),
                "original_score": candidate.get("score"),
            }
        )

    if items:
        try:
            with conn.cursor() as cur:
                for row in items:
                    score = float(6 - row["rank"])
                    cur.execute(
                        """
                        INSERT INTO role_candidates(role_id, user_id, score, is_primary, approved, rank, created_at)
                        VALUES (%s, %s, %s, %s, FALSE, %s, now())
                        ON CONFLICT (role_id, user_id)
                        DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                        """,
                        (
                            role_id,
                            row["user_id"],
                            score,
                            row["rank"] == 1,
                            row["rank"],
                        ),
                    )
            conn.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to persist role candidates: %s", exc)

    return {"status": "ok", "role_id": role_id, "items": items}


def handle_match_student(
    conn: connection,
    student_user_id: int,
    *,
    llm_client: Optional[MatchingLLMClient] = None,
) -> Dict[str, Any]:
    student = fetch_student(conn, student_user_id)
    if not student:
        return {"status": "error", "message": f"Student #{student_user_id} not found"}

    student["cv"] = resolve_cv_text(conn, student.get("cv"))
    roles = fetch_roles_needing_students(conn, limit=40)
    if not roles:
        return {"status": "ok", "student_user_id": student_user_id, "items": []}

    payload_json = dumps_payload(build_roles_for_student_payload(student, roles))
    llm = _pick_llm(llm_client)
    ranked = (llm.rank_roles(payload_json) if llm else None) or _fallback_top5_roles(roles)

    by_id = {role.get("id"): role for role in roles}
    items: List[Dict[str, Any]] = []
    for position, result in enumerate(ranked, start=1):
        role_row = None
        role_id = result.get("role_id")
        if role_id in by_id:
            role_row = by_id[role_id]
        elif isinstance(result.get("num"), int):
            idx = result["num"] - 1
            if 0 <= idx < len(roles):
                role_row = roles[idx]
        if not role_row:
            continue
        items.append(
            {
                "rank": position,
                "role_id": role_row.get("id"),
                "role_name": role_row.get("name"),
                "topic_id": role_row.get("topic_id"),
                "topic_title": role_row.get("topic_title"),
                "reason": result.get("reason"),
            }
        )

    if items:
        try:
            with conn.cursor() as cur:
                for row in items:
                    score = float(6 - row["rank"])
                    cur.execute(
                        """
                        INSERT INTO student_candidates(user_id, role_id, score, is_primary, approved, rank, created_at)
                        VALUES (%s, %s, %s, %s, FALSE, %s, now())
                        ON CONFLICT (user_id, role_id)
                        DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                        """,
                        (
                            student_user_id,
                            row["role_id"],
                            score,
                            row["rank"] == 1,
                            row["rank"],
                        ),
                    )
            conn.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to persist roles for student %s: %s", student_user_id, exc)

    return {"status": "ok", "student_user_id": student_user_id, "items": items}


def handle_match_supervisor_user(
    conn: connection,
    supervisor_user_id: int,
    *,
    llm_client: Optional[MatchingLLMClient] = None,
) -> Dict[str, Any]:
    supervisor = fetch_supervisor(conn, supervisor_user_id)
    if not supervisor:
        return {"status": "error", "message": f"Supervisor #{supervisor_user_id} not found"}

    topics = fetch_topics_needing_supervisors(conn, limit=20)
    if not topics:
        return {"status": "ok", "supervisor_user_id": supervisor_user_id, "items": []}

    payload_json = dumps_payload(build_topics_for_supervisor_payload(supervisor, topics))
    llm = _pick_llm(llm_client)
    ranked = (llm.rank_topics(payload_json) if llm else None) or _fallback_top5_topics(topics)

    by_id = {topic.get("id"): topic for topic in topics}
    items: List[Dict[str, Any]] = []
    for position, result in enumerate(ranked, start=1):
        topic_row = None
        topic_id = result.get("topic_id")
        if topic_id in by_id:
            topic_row = by_id[topic_id]
        elif isinstance(result.get("num"), int):
            idx = result["num"] - 1
            if 0 <= idx < len(topics):
                topic_row = topics[idx]
        if not topic_row:
            continue
        items.append(
            {
                "rank": position,
                "topic_id": topic_row.get("id"),
                "title": topic_row.get("title"),
                "reason": result.get("reason"),
            }
        )

    if items:
        try:
            with conn.cursor() as cur:
                for row in items:
                    score = float(6 - row["rank"])
                    cur.execute(
                        """
                        INSERT INTO supervisor_candidates(user_id, topic_id, score, is_primary, approved, rank, created_at)
                        VALUES (%s, %s, %s, %s, FALSE, %s, now())
                        ON CONFLICT (user_id, topic_id)
                        DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                        """,
                        (
                            supervisor_user_id,
                            row["topic_id"],
                            score,
                            row["rank"] == 1,
                            row["rank"],
                        ),
                    )
            conn.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to persist topics for supervisor %s: %s", supervisor_user_id, exc
            )

    return {"status": "ok", "supervisor_user_id": supervisor_user_id, "items": items}


__all__ = [
    "handle_match",
    "handle_match_role",
    "handle_match_student",
    "handle_match_supervisor_user",
    "create_matching_llm_client",
    "MatchingLLMClient",
]
