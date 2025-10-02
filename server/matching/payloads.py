"""Payload builders shared by matching services."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping


def _trimmed(text: Any, *, limit: int = 20000) -> str | None:
    if text in (None, ""):
        return None
    return str(text)[:limit]


def student_profile(raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "program": raw.get("program"),
        "skills": raw.get("skills"),
        "interests": raw.get("interests"),
        "skills_to_learn": raw.get("skills_to_learn"),
        "preferred_team_track": raw.get("preferred_team_track"),
        "team_role": raw.get("team_role"),
        "team_needs": raw.get("team_needs"),
        "dev_track": raw.get("dev_track"),
        "science_track": raw.get("science_track"),
        "startup_track": raw.get("startup_track"),
        "cv": _trimmed(raw.get("cv")),
    }


def supervisor_profile(raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "position": raw.get("position"),
        "degree": raw.get("degree"),
        "capacity": raw.get("capacity"),
        "interests": raw.get("interests"),
        "requirements": raw.get("requirements"),
    }


def _compact_topic(raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "author_id": raw.get("author_id"),
        "author_name": raw.get("author_name"),
        "seeking_role": raw.get("seeking_role"),
        "description": raw.get("description"),
        "expected_outcomes": raw.get("expected_outcomes"),
        "required_skills": raw.get("required_skills"),
        "direction": raw.get("direction"),
    }


def build_candidates_payload(
    topic: Mapping[str, Any], candidates: Iterable[Mapping[str, Any]], role: str
) -> Dict[str, Any]:
    comp: List[Dict[str, Any]] = []
    normalized_role = "student" if role not in ("student", "supervisor") else role
    for idx, candidate in enumerate(candidates, start=1):
        profile = (
            student_profile(candidate)
            if normalized_role == "student"
            else supervisor_profile(candidate)
        )
        comp.append(
            {
                "num": idx,
                "user_id": candidate.get("user_id"),
                "full_name": candidate.get("full_name"),
                "username": candidate.get("username"),
                "email": candidate.get("email"),
                "original_score": candidate.get("score"),
                "profile": profile,
            }
        )

    payload = {
        "task": "rank_candidates_for_topic",
        "target_role": normalized_role,
        "topic": _compact_topic(topic),
        "candidates": comp,
        "instruction": "Верни пятёрку лучших кандидатов и коротко объясни выбор.",
    }
    return payload


def build_role_candidates_payload(
    topic: Mapping[str, Any],
    role_row: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    comp = []
    for idx, candidate in enumerate(candidates, start=1):
        comp.append(
            {
                "num": idx,
                "user_id": candidate.get("user_id"),
                "full_name": candidate.get("full_name"),
                "username": candidate.get("username"),
                "email": candidate.get("email"),
                "original_score": candidate.get("score"),
                "profile": student_profile(candidate),
            }
        )

    topic_compact = {
        "id": topic.get("id") or role_row.get("topic_id"),
        "title": topic.get("title") or role_row.get("topic_title"),
        "direction": topic.get("direction") or role_row.get("direction"),
        "author_id": topic.get("author_id") or role_row.get("author_user_id"),
        "author_name": topic.get("author_name") or role_row.get("author_name"),
        "description": topic.get("description") or role_row.get("topic_description"),
        "expected_outcomes": topic.get("expected_outcomes")
        or role_row.get("topic_expected_outcomes"),
        "required_skills": topic.get("required_skills")
        or role_row.get("topic_required_skills"),
    }

    role_compact = {
        "id": role_row.get("id"),
        "name": role_row.get("name"),
        "description": role_row.get("description"),
        "required_skills": role_row.get("required_skills"),
        "capacity": role_row.get("capacity"),
    }

    return {
        "task": "rank_candidates_for_role",
        "topic": topic_compact,
        "role": role_compact,
        "candidates": comp,
        "instruction": "Подбери пять лучших студентов на роль и напиши, почему они подходят.",
    }


def build_topics_for_student_payload(
    student: Mapping[str, Any], topics: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    comp = []
    for idx, topic in enumerate(topics, start=1):
        comp.append(
            {
                "num": idx,
                "topic_id": topic.get("id"),
                "title": topic.get("title"),
                "description": topic.get("description"),
                "required_skills": topic.get("required_skills"),
                "expected_outcomes": topic.get("expected_outcomes"),
                "author_name": topic.get("author_name"),
            }
        )

    student_compact = {
        "user_id": student.get("user_id"),
        "full_name": student.get("full_name"),
        "username": student.get("username"),
        "email": student.get("email"),
        **student_profile(student),
    }

    return {
        "task": "rank_topics_for_student",
        "student": student_compact,
        "topics": comp,
        "instruction": "Выбери пять самых подходящих тем для студента и обоснуй выбор.",
    }


def build_roles_for_student_payload(
    student: Mapping[str, Any], roles: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    comp = []
    for idx, role in enumerate(roles, start=1):
        comp.append(
            {
                "num": idx,
                "role_id": role.get("id"),
                "role_name": role.get("name"),
                "role_required_skills": role.get("required_skills"),
                "topic_id": role.get("topic_id"),
                "topic_title": role.get("topic_title"),
                "direction": role.get("direction"),
                "author_name": role.get("author_name"),
            }
        )

    student_compact = {
        "user_id": student.get("user_id"),
        "full_name": student.get("full_name"),
        "username": student.get("username"),
        "email": student.get("email"),
        **student_profile(student),
    }

    return {
        "task": "rank_roles_for_student",
        "student": student_compact,
        "roles": comp,
        "instruction": "Подбери пять ролей для студента и коротко поясни выбор.",
    }


def build_topics_for_supervisor_payload(
    supervisor: Mapping[str, Any], topics: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    comp = []
    for idx, topic in enumerate(topics, start=1):
        comp.append(
            {
                "num": idx,
                "topic_id": topic.get("id"),
                "title": topic.get("title"),
                "description": topic.get("description"),
                "required_skills": topic.get("required_skills"),
                "expected_outcomes": topic.get("expected_outcomes"),
                "author_name": topic.get("author_name"),
            }
        )

    supervisor_compact = {
        "user_id": supervisor.get("user_id"),
        "full_name": supervisor.get("full_name"),
        "username": supervisor.get("username"),
        "email": supervisor.get("email"),
        **supervisor_profile(supervisor),
    }

    return {
        "task": "rank_topics_for_supervisor",
        "supervisor": supervisor_compact,
        "topics": comp,
        "instruction": "Выбери пять тем, которые лучше всего подходят научруку.",
    }


def dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "student_profile",
    "supervisor_profile",
    "build_candidates_payload",
    "build_role_candidates_payload",
    "build_topics_for_student_payload",
    "build_roles_for_student_payload",
    "build_topics_for_supervisor_payload",
    "dumps",
]
