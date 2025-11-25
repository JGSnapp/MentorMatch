from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from psycopg2.extensions import connection

from ..services.media_store import persist_media_from_url
from ..services.matching_client import (
    refresh_student_embedding,
    refresh_supervisor_embedding,
    refresh_topic_embedding,
    refresh_role_embedding,
)
logger = logging.getLogger(__name__)

MEMBER_ROLE_NAME = '%member'
MEMBER_ROLE_DESCRIPTION = (
    'Роль без конкретных обязанностей или специальности — для тех, кому интересен проект, '
    'но он ещё не определился с ролью.'
)


def _ensure_member_role(cur, topic_id: int) -> Optional[int]:
    """Создаёт роль %member для темы, если она отсутствует."""
    cur.execute(
        'SELECT id FROM roles WHERE topic_id=%s AND name=%s LIMIT 1',
        (topic_id, MEMBER_ROLE_NAME),
    )
    row = cur.fetchone()
    if row:
        return None
    cur.execute(
        '''
        INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
        VALUES (%s, %s, %s, NULL, NULL, now(), now())
        RETURNING id
        ''',
        (topic_id, MEMBER_ROLE_NAME, MEMBER_ROLE_DESCRIPTION),
    )
    inserted = cur.fetchone()
    return inserted[0] if inserted else None


def normalize_telegram_link(raw: Optional[str]) -> Optional[str]:
    """Преобразует ввод пользователя в каноническую ссылку Telegram."""
    if not raw:
        return None
    value = str(raw).strip()
    if value.startswith("@"):
        value = value[1:]
    if value.lower().startswith(("http://t.me/", "https://t.me/", "http://telegram.me/", "https://telegram.me/")):
        return value
    match = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", value)
    if match:
        return f"https://t.me/{match.group(1)}"
    username = re.sub(r"[^A-Za-z0-9_]", "", value)
    return f"https://t.me/{username}" if username else None


                                                           
def extract_telegram_username(raw: Optional[str]) -> Optional[str]:
    """Извлекает username Telegram из текста анкеты."""
    if not raw:
        return None
    value = str(raw).strip()
    if value.startswith("@"):
        value = value[1:]
    match = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", value)
    if match:
        return match.group(1)
    username = re.sub(r"[^A-Za-z0-9_]", "", value)
    return username or None


                                              
def _is_http_url(value: Optional[str]) -> bool:
    """Проверяет, начинается ли значение со схемы HTTP или HTTPS."""
    return bool(value) and str(value).strip().lower().startswith(("http://", "https://"))


                                                                          
def process_cv(conn: connection, user_id: int, cv_value: Optional[str]) -> Optional[str]:
    """Скачивает резюме по ссылке и сохраняет его в медиахранилище."""
    value = (cv_value or "").strip()
    if not value:
        return None
    if value.startswith("/media/"):
        return value
    if _is_http_url(value):
        try:
            _, public_url = persist_media_from_url(conn, user_id, value, category="cv")
            return public_url
        except Exception as exc:
            logger.warning("Failed to download CV for user %s: %s", user_id, exc)
            return cv_value
    return cv_value


                                          
def _comma_join(items: Optional[Sequence[str]]) -> Optional[str]:
    """Объединяет непустые элементы списка через запятую."""
    if not items:
        return None
    parts = [str(item).strip() for item in items if str(item).strip()]
    return ", ".join(parts) or None


def _normalize_theme(value: Optional[str]) -> Optional[str]:
    """Возвращает аккуратно обрезанное название темы или None."""
    if value is None:
        return None
    theme = str(value).strip()
    return theme or None


def _normalize_roles(values: Optional[Sequence[str]]) -> List[str]:
    """Нормализует список ролей, удаляя пустые и дубликаты (без учёта регистра)."""
    if not values:
        return []
    if isinstance(values, str):
        values = _split_roles_raw(values)
    result: List[str] = []
    seen: Set[str] = set()
    for val in values:
        name = str(val or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _split_roles_raw(raw: Optional[str]) -> List[str]:
    """Быстрый парсер списка ролей из строки (разделители: запятая, точка с запятой и пр.)."""
    if not raw:
        return []
    parts = re.split(r"[;,/|]\s*|\s{2,}", str(raw).strip())
    return [p.strip() for p in parts if p.strip()]


                                                    
def import_students(
    conn: connection,
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Импортирует студентов из анкет, создавая пользователей и профили."""
    inserted_users = 0
    inserted_profiles = 0
    student_refresh_queue: Set[int] = set()

    with conn.cursor() as cur:
        for idx, row in enumerate(rows):
            full_name = (row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip()
            if not (full_name or email):
                continue
            needs_student_refresh = False

            existing = None
            if email:
                cur.execute(
                    "SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND role='student' LIMIT 1",
                    (email,),
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
                needs_student_refresh = True

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
            if updates:
                params.append(user_id)
                cur.execute(
                    f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s",
                    tuple(params),
                )
                needs_student_refresh = True

            skills_have = _comma_join(row.get("hard_skills_have"))
            skills_want = _comma_join(row.get("hard_skills_want"))
            interests = _comma_join(row.get("interests"))
            cv_value = process_cv(conn, user_id, row.get("cv"))
            theme_name = _normalize_theme(row.get('requested_theme') or row.get('thematic_choice'))
            requested_roles = _normalize_roles(row.get('requested_roles'))
            if not requested_roles and row.get('team_role'):
                requested_roles = _normalize_roles(_split_roles_raw(row.get('team_role')))

            profile_values = {
                'submitted_at': row.get('timestamp'),
                'isu_number': row.get('isu_number'),
                'subdivision': row.get('subdivision'),
                'direction': row.get('direction'),
                'education_program': row.get('education_program'),
                'status': row.get('status'),
                'course': row.get('course'),
                'group_number': row.get('group_number'),
                'phone': row.get('phone'),
                'skills': skills_have,
                'skills_to_learn': skills_want,
                'interests': interests,
                'dislikes': row.get('dislikes'),
                'commercial_experience': row.get('commercial_experience'),
                'noncommercial_experience': row.get('noncommercial_experience'),
                'portfolio': row.get('portfolio'),
                'achievements': row.get('achievements'),
                'hobbies': row.get('hobbies'),
                'cv': cv_value,
                'customer_discovery_level': row.get('customer_discovery_level'),
                'sales_level': row.get('sales_level'),
                'tech_execution_level': row.get('tech_execution_level'),
                'data_analytics_level': row.get('data_analytics_level'),
                'marketing_design_level': row.get('marketing_design_level'),
                'finance_business_level': row.get('finance_business_level'),
                'team_leadership_level': row.get('team_leadership_level'),
                'apply_master': row.get('apply_master'),
                'hours_per_week': row.get('hours_per_week'),
                'thematic_choice': None,
                'team_role': None,
                'plan_for_lab': row.get('plan_for_lab'),
                'motivation_letter': row.get('motivation_letter'),
                'police_clearance': row.get('police_clearance'),
                'dev_track': row.get('dev_track'),
                'science_track': row.get('science_track'),
                'startup_track': row.get('startup_track'),
            }

            columns = list(profile_values.keys())
            values = [profile_values[col] for col in columns]
            placeholders = ', '.join(['%s'] * len(columns))
            updates_sql = ', '.join(f"{col}=EXCLUDED.{col}" for col in columns)
            cur.execute(
                f"""
                INSERT INTO student_profiles(user_id, {', '.join(columns)})
                VALUES (%s, {placeholders})
                ON CONFLICT (user_id) DO UPDATE SET {updates_sql}
                """,
                (user_id, *values),
            )
            needs_student_refresh = True
            inserted_profiles += 1
            if theme_name and requested_roles:
                for role_name in requested_roles:
                    cur.execute(
                        """
                        INSERT INTO requested_roles(student_id, theme_name, role_name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (student_id, theme_name, role_name) DO NOTHING
                        """,
                        (user_id, theme_name, role_name),
                    )
            if needs_student_refresh:
                student_refresh_queue.add(user_id)

    conn.commit()
    for student_id in student_refresh_queue:
        refresh_student_embedding(student_id)
    return {
        "status": "success",
        "message": "Импорт завершён: добавлено пользователей: {users}, обновлено профилей: {profiles}.".format(
            users=inserted_users,
            profiles=inserted_profiles,
        ),
        "stats": {
            "inserted_users": inserted_users,
            "inserted_profiles": inserted_profiles,
        },
    }

                                                   
def import_supervisors(
    conn: connection,
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Импортирует наставников и их темы из анкет Google Forms."""
    inserted_users = 0
    upserted_profiles = 0
    inserted_topics = 0
    inserted_roles = 0
    supervisor_refresh_queue: Set[int] = set()
    topic_refresh_queue: Set[int] = set()
    role_refresh_queue: Set[int] = set()

    with conn.cursor() as cur:
        for row in rows:
            full_name = (row.get("full_name") or "").strip()
            email = (row.get("email") or "").strip() or None
            if not (full_name or email):
                continue
            needs_supervisor_refresh = False

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
                needs_supervisor_refresh = True

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
                needs_supervisor_refresh = True

            cur.execute("SELECT 1 FROM supervisor_profiles WHERE user_id=%s", (user_id,))
            profile_exists = cur.fetchone() is not None
            capacity_val = None
            requirements_val = row.get("required_skills")

            if profile_exists:
                cur.execute(
                    """
                    UPDATE supervisor_profiles
                    SET capacity=%s, requirements=%s
                    WHERE user_id=%s
                    """,
                    (capacity_val, requirements_val, user_id),
                )
                needs_supervisor_refresh = True
            else:
                cur.execute(
                    """
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, None, None, capacity_val, None, requirements_val),
                )
                needs_supervisor_refresh = True
            upserted_profiles += 1

            title = (row.get("topic_title") or "").strip()
            if title:
                cur.execute(
                    "SELECT id FROM topics WHERE author_user_id=%s AND title=%s LIMIT 1",
                    (user_id, title),
                )
                topic_row = cur.fetchone()
                if topic_row:
                    topic_id = topic_row[0]
                    cur.execute(
                        """
                        UPDATE topics
                        SET description=%s,
                            expected_outcomes=%s,
                            required_skills=%s,
                            seeking_role='student',
                            updated_at=now()
                        WHERE id=%s
                        """,
                        (
                            row.get("topic_description"),
                            row.get("expected_outcomes"),
                            row.get("required_skills"),
                            topic_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                           required_skills, direction, seeking_role, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, 'student', now(), now())
                        RETURNING id
                        """,
                        (
                            user_id,
                            title,
                            row.get("topic_description"),
                            row.get("expected_outcomes"),
                            row.get("required_skills"),
                            None,
                        ),
                    )
                    inserted_topic_row = cur.fetchone()
                    topic_id = inserted_topic_row[0]
                    inserted_topics += 1
                topic_refresh_queue.add(topic_id)
                member_role_id = _ensure_member_role(cur, topic_id)
                if member_role_id:
                    role_refresh_queue.add(member_role_id)

                roles = _normalize_roles(row.get("required_roles"))
                for role_name in roles:
                    cur.execute(
                        "SELECT id FROM roles WHERE topic_id=%s AND name=%s LIMIT 1",
                        (topic_id, role_name),
                    )
                    role_row = cur.fetchone()
                    if role_row:
                        role_refresh_queue.add(role_row[0])
                        continue
                    cur.execute(
                        """
                        INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, now(), now())
                        RETURNING id
                        """,
                        (
                            topic_id,
                            role_name,
                            None,
                            row.get("required_skills"),
                            None,
                        ),
                    )
                    role_inserted = cur.fetchone()
                    if role_inserted:
                        inserted_roles += 1
                        role_refresh_queue.add(role_inserted[0])
            if needs_supervisor_refresh:
                supervisor_refresh_queue.add(user_id)

    conn.commit()
    for supervisor_id in supervisor_refresh_queue:
        refresh_supervisor_embedding(supervisor_id)
    for topic_id in topic_refresh_queue:
        refresh_topic_embedding(topic_id)
    for role_id in role_refresh_queue:
        refresh_role_embedding(role_id)
    return {
        "status": "success",
        "message": (
            "Импорт научруков завершён: новых пользователей {users}, обновлено профилей {profiles},"
            " добавлено тем {topics}, добавлено ролей {roles}."
        ).format(
            users=inserted_users,
            profiles=upserted_profiles,
            topics=inserted_topics,
            roles=inserted_roles,
        ),
        "stats": {
            "inserted_users": inserted_users,
            "upserted_profiles": upserted_profiles,
            "inserted_topics": inserted_topics,
            "inserted_roles": inserted_roles,
        },
    }


__all__ = [
    "normalize_telegram_link",
    "extract_telegram_username",
    "process_cv",
    "import_students",
    "import_supervisors",
]

