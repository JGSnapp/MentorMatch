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
    build_topic_applicants_payload,
    build_roles_for_student_payload,
    build_topics_for_supervisor_payload,
    dumps as dumps_payload,
)
from .repository import (
    STUDENT_PROFILE_COLUMNS_SQL,
    fetch_candidates,
    fetch_role,
    fetch_role_applicants,
    fetch_topic_applicants,
    fetch_roles_needing_students,
    fetch_student,
    fetch_supervisor,
    fetch_topic,
    fetch_topics_needing_supervisors,
)

logger = logging.getLogger(__name__)


def _pick_llm(llm: Optional[MatchingLLMClient]) -> Optional[MatchingLLMClient]:
    """Выполняет функцию _pick_llm."""
    picked = llm or create_matching_llm_client()
    if picked is None:
        logger.warning("LLM недоступен: используем фолбэк ранжирования")
    return picked


def _enrich_cv(conn: connection, candidates: List[Dict[str, Any]]) -> None:
    """Выполняет функцию _enrich_cv."""
    for candidate in candidates:
        candidate["cv"] = resolve_cv_text(conn, candidate.get("cv"))


def _fallback_top5(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Выполняет функцию _fallback_top5."""
    return [
        {
            "user_id": candidate.get("user_id"),
            "num": idx,
            "reason": "LLM недоступен: выводим последних пяти кандидатов.",
        }
        for idx, candidate in enumerate(candidates[:5], start=1)
    ]


def _fallback_top_n(
    candidates: List[Dict[str, Any]], count: int, reason: str
) -> List[Dict[str, Any]]:
    """Возвращает верхние N кандидатов, если LLM недоступен."""

    return [
        {
            "user_id": candidate.get("user_id"),
            "num": idx,
            "reason": reason,
        }
        for idx, candidate in enumerate(candidates[:count], start=1)
    ]


def _fallback_top5_topics(topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Выполняет функцию _fallback_top5_topics."""
    return [
        {
            "topic_id": topic.get("id"),
            "num": idx,
            "reason": "LLM недоступен: используем последние темы.",
        }
        for idx, topic in enumerate(topics[:5], start=1)
    ]


def _fallback_top5_roles(roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Выполняет функцию _fallback_top5_roles."""
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
    """Выполняет функцию handle_match."""
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
        except Exception as exc:                                                 
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
    """Выполняет функцию handle_match_role."""
    role_row = fetch_role(conn, role_id)
    if not role_row:
        return {"status": "error", "message": f"Role #{role_id} not found"}

    topic = fetch_topic(conn, role_row["topic_id"])
    if not topic:
        return {"status": "error", "message": f"Topic #{role_row['topic_id']} not found"}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                   NULL::double precision AS score,
                   {STUDENT_PROFILE_COLUMNS_SQL}
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
        except Exception as exc:                    
            logger.warning("Failed to persist role candidates: %s", exc)

    return {"status": "ok", "role_id": role_id, "items": items}


def handle_match_role_applicants(
    conn: connection,
    role_id: int,
    *,
    llm_client: Optional[MatchingLLMClient] = None,
    top_n: int = 10,
) -> Dict[str, Any]:
    """Отбирает лучших студентов из поданных заявок на роль."""

    role_row = fetch_role(conn, role_id)
    if not role_row:
        return {"status": "error", "message": f"Role #{role_id} not found"}

    topic = role_row.get("topic") or fetch_topic(conn, role_row.get("topic_id"))
    applicants = fetch_role_applicants(conn, role_id, limit=100)
    if not applicants:
        return {"status": "ok", "role_id": role_id, "items": []}

    _enrich_cv(conn, applicants)
    top_n = max(1, min(top_n, len(applicants)))
    ranked = _fallback_top_n(
        applicants,
        top_n,
        "LLM недоступен: показываем ближайших по откликам.",
    )

    payload_json = dumps_payload(
        build_role_candidates_payload(topic or {}, role_row, applicants, top_n=top_n)
    )
    llm = _pick_llm(llm_client)
    if llm:
        ranked = llm.rank_role_applicants(payload_json, top_n=top_n) or ranked

    by_id = {c.get("user_id"): c for c in applicants}
    items: List[Dict[str, Any]] = []
    for position, result in enumerate(ranked, start=1):
        candidate = by_id.get(result.get("user_id"))
        if not candidate and isinstance(result.get("num"), int):
            idx = result["num"] - 1
            if 0 <= idx < len(applicants):
                candidate = applicants[idx]
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

    return {"status": "ok", "role_id": role_id, "items": items}


def handle_match_topic_applicants(
    conn: connection,
    topic_id: int,
    *,
    llm_client: Optional[MatchingLLMClient] = None,
    top_n: int = 10,
) -> Dict[str, Any]:
    """Отбирает лучших студентов среди откликнувшихся на тему."""

    topic = fetch_topic(conn, topic_id)
    if not topic:
        return {"status": "error", "message": f"Topic #{topic_id} not found"}

    applicants = fetch_topic_applicants(conn, topic_id, limit=100)
    if not applicants:
        return {"status": "ok", "topic_id": topic_id, "items": []}

    _enrich_cv(conn, applicants)
    top_n = max(1, min(top_n, len(applicants)))
    ranked = _fallback_top_n(
        applicants,
        top_n,
        "LLM недоступен: показываем ближайших по откликам.",
    )

    payload_json = dumps_payload(
        build_topic_applicants_payload(topic, applicants, top_n=top_n)
    )
    llm = _pick_llm(llm_client)
    if llm:
        ranked = llm.rank_topic_applicants(payload_json, top_n=top_n) or ranked

    by_id = {c.get("user_id"): c for c in applicants}
    items: List[Dict[str, Any]] = []
    for position, result in enumerate(ranked, start=1):
        candidate = by_id.get(result.get("user_id"))
        if not candidate and isinstance(result.get("num"), int):
            idx = result["num"] - 1
            if 0 <= idx < len(applicants):
                candidate = applicants[idx]
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

    return {"status": "ok", "topic_id": topic_id, "items": items}


def handle_match_student(
    conn: connection,
    student_user_id: int,
    *,
    llm_client: Optional[MatchingLLMClient] = None,
) -> Dict[str, Any]:
    """Выполняет функцию handle_match_student."""
    student = fetch_student(conn, student_user_id)
    if not student:
        return {"status": "error", "message": f"Student #{student_user_id} not found"}

    student["cv"] = resolve_cv_text(conn, student.get("cv"))
    roles = fetch_roles_needing_students(conn, student_user_id, limit=40)
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
        except Exception as exc:                    
            logger.warning("Failed to persist roles for student %s: %s", student_user_id, exc)

    return {"status": "ok", "student_user_id": student_user_id, "items": items}


def handle_match_supervisor_user(
    conn: connection,
    supervisor_user_id: int,
    *,
    llm_client: Optional[MatchingLLMClient] = None,
) -> Dict[str, Any]:
    """Выполняет функцию handle_match_supervisor_user."""
    supervisor = fetch_supervisor(conn, supervisor_user_id)
    if not supervisor:
        return {"status": "error", "message": f"Supervisor #{supervisor_user_id} not found"}

    topics = fetch_topics_needing_supervisors(conn, supervisor_user_id, limit=20)
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
        except Exception as exc:                    
            logger.warning(
                "Failed to persist topics for supervisor %s: %s", supervisor_user_id, exc
            )

    return {"status": "ok", "supervisor_user_id": supervisor_user_id, "items": items}


__all__ = [
    "handle_match",
    "handle_match_role",
    "handle_match_role_applicants",
    "handle_match_topic_applicants",
    "handle_match_student",
    "handle_match_supervisor_user",
    "create_matching_llm_client",
    "MatchingLLMClient",
]
