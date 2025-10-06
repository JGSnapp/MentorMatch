"""Utilities for importing students and supervisors from spreadsheets."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from psycopg2.extensions import connection

from media_store import persist_media_from_url
from .topic_extraction import extract_topics_from_text, fallback_extract_topics

logger = logging.getLogger(__name__)


def normalize_telegram_link(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    value = str(raw).strip()
    if value.startswith("@"):  # Already username
        value = value[1:]
    if value.lower().startswith(("http://t.me/", "https://t.me/", "http://telegram.me/", "https://telegram.me/")):
        return value
    match = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", value)
    if match:
        return f"https://t.me/{match.group(1)}"
    username = re.sub(r"[^A-Za-z0-9_]", "", value)
    return f"https://t.me/{username}" if username else None


def extract_telegram_username(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    value = str(raw).strip()
    if value.startswith("@"):  # Already username
        value = value[1:]
    match = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", value)
    if match:
        return match.group(1)
    username = re.sub(r"[^A-Za-z0-9_]", "", value)
    return username or None


def _is_http_url(value: Optional[str]) -> bool:
    return bool(value) and str(value).strip().lower().startswith(("http://", "https://"))


def process_cv(conn: connection, user_id: int, cv_value: Optional[str]) -> Optional[str]:
    value = (cv_value or "").strip()
    if not value:
        return None
    if value.startswith("/media/"):
        return value
    if _is_http_url(value):
        try:
            _, public_url = persist_media_from_url(conn, user_id, value, category="cv")
            return public_url
        except Exception as exc:  # pragma: no cover - network failures are logged
            logger.warning("Failed to download CV for user %s: %s", user_id, exc)
            return cv_value
    return cv_value


def _comma_join(items: Optional[Sequence[str]]) -> Optional[str]:
    if not items:
        return None
    parts = [str(item).strip() for item in items if str(item).strip()]
    return ", ".join(parts) or None


def import_students(
    conn: connection,
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    inserted_users = 0
    inserted_profiles = 0
    inserted_topics = 0

    with conn.cursor() as cur:
        for idx, row in enumerate(rows):
            full_name = (row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip()
            if not (full_name or email):
                continue

            if email:
                cur.execute(
                    "SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND role='student' LIMIT 1",
                    (email,),
                )
            else:
                cur.execute(
                    "SELECT id FROM users WHERE full_name=%s AND role='student' LIMIT 1",
                    (full_name,),
                )
            existing = cur.fetchone()
            if existing:
                user_id = existing[0]
            else:
                cur.execute(
                    """
                    INSERT INTO users(full_name, email, role, created_at, updated_at)
                    VALUES (%s, %s, 'student', now(), now())
                    RETURNING id
                    """,
                    (full_name, email or None),
                )
                user_id = cur.fetchone()[0]
                inserted_users += 1

            updates: List[str] = []
            params: List[Any] = []
            telegram = row.get("telegram")
            if telegram:
                tg_link = normalize_telegram_link(telegram)
                if tg_link:
                    updates.append("username=%s")
                    params.append(tg_link)
            if row.get("consent_personal") is not None:
                updates.append("consent_personal=%s")
                params.append(row["consent_personal"])
            if row.get("consent_private") is not None:
                updates.append("consent_private=%s")
                params.append(row["consent_private"])
            if updates:
                params.append(user_id)
                cur.execute(
                    f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s",
                    tuple(params),
                )

            cur.execute("SELECT 1 FROM student_profiles WHERE user_id=%s", (user_id,))
            profile_exists = cur.fetchone() is not None
            skills_have = _comma_join(row.get("hard_skills_have"))
            skills_want = _comma_join(row.get("hard_skills_want"))
            interests = _comma_join(row.get("interests"))
            requirements = row.get("supervisor_preference")
            cv_value = process_cv(conn, user_id, row.get("cv"))

            profile_args = (
                row.get("program"),
                skills_have,
                interests,
                cv_value,
                requirements,
                skills_want,
                row.get("achievements"),
                row.get("supervisor_preference"),
                row.get("groundwork"),
                row.get("wants_team"),
                row.get("team_role"),
                row.get("team_has"),
                row.get("team_needs"),
                row.get("apply_master"),
                row.get("workplace"),
                row.get("preferred_team_track"),
                row.get("dev_track"),
                row.get("science_track"),
                row.get("startup_track"),
                row.get("final_work_preference"),
            )

            if profile_exists:
                cur.execute(
                    """
                    UPDATE student_profiles
                    SET program=%s, skills=%s, interests=%s, cv=%s, requirements=%s,
                        skills_to_learn=%s, achievements=%s, supervisor_pref=%s, groundwork=%s,
                        wants_team=%s, team_role=%s, team_has=%s, team_needs=%s,
                        apply_master=%s, workplace=%s,
                        preferred_team_track=%s, dev_track=%s, science_track=%s, startup_track=%s,
                        final_work_pref=%s
                    WHERE user_id=%s
                    """,
                    (*profile_args, user_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO student_profiles(
                        user_id, program, skills, interests, cv, requirements,
                        skills_to_learn, achievements, supervisor_pref, groundwork,
                        wants_team, team_role, team_has, team_needs, apply_master, workplace,
                        preferred_team_track, dev_track, science_track, startup_track, final_work_pref
                    )
                    VALUES (%s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, *profile_args),
                )
            inserted_profiles += 1

            topic_payload = row.get("topic") or {}
            has_topic = row.get("has_own_topic")
            title = (topic_payload.get("title") or "").strip()
            if has_topic and title:
                cur.execute(
                    "SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s",
                    (user_id, title),
                )
                if not cur.fetchone():
                    description = (topic_payload.get("description") or "").strip()
                    groundwork = row.get("groundwork")
                    if groundwork:
                        tail = f"\n\nИмеющийся задел: {groundwork}".strip()
                        description = f"{description}\n{tail}" if description else tail
                    practical = topic_payload.get("practical_importance") or None
                    if practical:
                        tail = f"\n\nПрактическая значимость: {practical}".strip()
                        description = f"{description}\n{tail}" if description else tail
                    cur.execute(
                        """
                        INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                           required_skills, seeking_role, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, 'supervisor', TRUE, now(), now())
                        """,
                        (
                            user_id,
                            title,
                            description or None,
                            topic_payload.get("expected_outcomes"),
                            skills_have,
                        ),
                    )
                    inserted_topics += 1

    conn.commit()
    return {
        "status": "success",
        "message": (
            "Импорт завершён: добавлено пользователей: {users}, обновлено профилей: {profiles},"
            " создано тем: {topics}."
        ).format(
            users=inserted_users,
            profiles=inserted_profiles,
            topics=inserted_topics,
        ),
        "stats": {
            "inserted_users": inserted_users,
            "inserted_profiles": inserted_profiles,
            "inserted_topics": inserted_topics,
        },
    }


def import_supervisors(
    conn: connection,
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    inserted_users = 0
    upserted_profiles = 0
    inserted_topics = 0

    with conn.cursor() as cur:
        for row in rows:
            full_name = (row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip() or None
            if not (full_name or email):
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
            existing = cur.fetchone()
            if existing:
                user_id = existing[0]
            else:
                cur.execute(
                    """
                    INSERT INTO users(full_name, email, role, created_at, updated_at)
                    VALUES (%s, %s, 'supervisor', now(), now())
                    RETURNING id
                    """,
                    (full_name, email),
                )
                user_id = cur.fetchone()[0]
                inserted_users += 1

            updates: List[str] = []
            params: List[Any] = []
            telegram = row.get("telegram")
            if telegram:
                tg_link = normalize_telegram_link(telegram)
                if tg_link:
                    updates.append("username=%s")
                    params.append(tg_link)
            if updates:
                params.append(user_id)
                cur.execute(
                    f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s",
                    tuple(params),
                )

            cur.execute("SELECT 1 FROM supervisor_profiles WHERE user_id=%s", (user_id,))
            profile_exists = cur.fetchone() is not None
            interests = row.get("area") or None
            requirements = row.get("extra_info") or None

            if profile_exists:
                cur.execute(
                    """
                    UPDATE supervisor_profiles
                    SET interests=%s, requirements=%s
                    WHERE user_id=%s
                    """,
                    (interests, requirements, user_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, None, None, None, interests, requirements),
                )
            upserted_profiles += 1

            def _insert_from_text(text: Optional[str], direction: Optional[int]) -> None:
                nonlocal inserted_topics
                if not text or not text.strip():
                    return
                topics = extract_topics_from_text(text) or fallback_extract_topics(text)
                for topic in topics:
                    title = (topic.get("title") or "").strip()
                    if not title:
                        continue
                    cur.execute(
                        "SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s AND (direction IS NOT DISTINCT FROM %s)",
                        (user_id, title, direction),
                    )
                    if cur.fetchone():
                        continue
                    cur.execute(
                        """
                        INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                           required_skills, direction, seeking_role, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, 'student', TRUE, now(), now())
                        """,
                        (
                            user_id,
                            title,
                            topic.get("description"),
                            topic.get("expected_outcomes"),
                            topic.get("required_skills"),
                            direction,
                        ),
                    )
                    inserted_topics += 1

            _insert_from_text(row.get("topics_09"), 9)
            _insert_from_text(row.get("topics_11"), 11)
            _insert_from_text(row.get("topics_45"), 45)
            if not any((row.get("topics_09"), row.get("topics_11"), row.get("topics_45"))):
                _insert_from_text(row.get("topics_text"), None)

    conn.commit()
    return {
        "status": "success",
        "message": (
            "Импорт научруков завершён: новых пользователей {users}, обновлено профилей {profiles},"
            " добавлено тем {topics}."
        ).format(
            users=inserted_users,
            profiles=upserted_profiles,
            topics=inserted_topics,
        ),
        "stats": {
            "inserted_users": inserted_users,
            "upserted_profiles": upserted_profiles,
            "inserted_topics": inserted_topics,
        },
    }


__all__ = [
    "normalize_telegram_link",
    "extract_telegram_username",
    "process_cv",
    "import_students",
    "import_supervisors",
]
