"""Routers interacting with Google Sheets exports."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from parse_gform import fetch_normalized_rows, fetch_supervisor_rows

from ..db import get_conn
from ..services.cv import process_cv
from ..telegram_utils import normalize_telegram_link
from utils import resolve_service_account_path

logger = logging.getLogger(__name__)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/sheets-status", response_class=JSONResponse)
    def api_get_sheets_status():
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
        service_account_file = os.getenv("SERVICE_ACCOUNT_FILE")
        if spreadsheet_id and service_account_file:
            return {
                "status": "configured",
                "spreadsheet_id": spreadsheet_id[:20] + "..." if len(spreadsheet_id) > 20 else spreadsheet_id,
                "service_account_file": service_account_file,
            }
        missing_vars = []
        if not spreadsheet_id:
            missing_vars.append("SPREADSHEET_ID")
        if not service_account_file:
            missing_vars.append("SERVICE_ACCOUNT_FILE")
        return {"status": "not_configured", "missing_vars": missing_vars}

    @router.get("/api/sheets-config", response_class=JSONResponse)
    def api_get_sheets_config():
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
        service_account_file = resolve_service_account_path(os.getenv("SERVICE_ACCOUNT_FILE"))
        if spreadsheet_id and service_account_file:
            try:
                if not os.path.exists(service_account_file):
                    return {
                        "status": "not_configured",
                        "error": "SERVICE_ACCOUNT_FILE not found",
                        "service_account_file": service_account_file,
                        "spreadsheet_id": spreadsheet_id,
                    }
            except Exception:
                pass
            return {
                "status": "configured",
                "spreadsheet_id": spreadsheet_id,
                "service_account_file": service_account_file,
            }
        return {"status": "not_configured", "error": "Missing env vars"}

    def _merge_student_profile(cur, conn, user_id: int, row: Dict[str, Any]) -> None:
        cur.execute("SELECT 1 FROM student_profiles WHERE user_id=%s", (user_id,))
        exists = cur.fetchone() is not None
        skills_have = ", ".join(row.get("hard_skills_have") or []) or None
        skills_want = ", ".join(row.get("hard_skills_want") or []) or None
        interests = ", ".join(row.get("interests") or []) or None
        requirements = row.get("supervisor_preference")
        cv_value = process_cv(conn, user_id, row.get("cv"))

        if exists:
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
                (
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
                    user_id,
                ),
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
                (
                    user_id,
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
                ),
            )

    def _extract_topics_from_fc(row: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        fc = row.get("form_control")
        if not fc or not getattr(fc, "arguments", None):
            return None
        try:
            parsed = json.loads(fc.arguments)
            topics = parsed.get("topics", [])
            normalized: List[Dict[str, Any]] = []
            for topic in topics:
                title = (topic.get("title") or "").strip()
                if not title:
                    continue
                normalized.append(
                    {
                        "title": title,
                        "description": (topic.get("description") or "").strip() or None,
                        "expected_outcomes": (topic.get("expected_outcomes") or "").strip() or None,
                        "required_skills": (topic.get("required_skills") or "").strip() or None,
                    }
                )
            return normalized or None
        except Exception:
            return None

    def _fallback_extract_topics(text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        parts = re.split(r"[\n;\-\u2022]+|\s{2,}", text)
        seen = set()
        result: List[Dict[str, Any]] = []
        for part in parts:
            title = (part or "").strip(" \t\r\n.-")
            if not title or len(title) < 3:
                continue
            lower = title.lower()
            if lower in seen:
                continue
            seen.add(lower)
            result.append(
                {
                    "title": title,
                    "description": None,
                    "expected_outcomes": None,
                    "required_skills": None,
                }
            )
        return result

    def _insert_topics_from_text(cur, user_id: int, text: Optional[str], direction: Optional[int]) -> int:
        if not text:
            return 0
        count = 0
        for topic in _fallback_extract_topics(text):
            title = topic["title"]
            cur.execute("SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s", (user_id, title))
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
            count += 1
        return count

    @router.post("/api/import-sheet", response_class=JSONResponse)
    def api_import_sheet(spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
        try:
            service_account_file = resolve_service_account_path(
                os.getenv("SERVICE_ACCOUNT_FILE", "service-account.json")
            )
            try:
                import requests

                requests.get("https://www.googleapis.com/generate_204", timeout=5)
            except Exception as tls_exc:
                logger.warning("/api/import-sheet TLS preflight warning: %s", tls_exc)
            if service_account_file and not os.path.exists(service_account_file):
                return {
                    "status": "error",
                    "message": f"SERVICE_ACCOUNT_FILE not found: {service_account_file}",
                }
            rows = fetch_normalized_rows(
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                service_account_file=service_account_file,
            )
            inserted_users = 0
            inserted_profiles = 0
            inserted_topics = 0

            with get_conn() as conn, conn.cursor() as cur:
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
                    row_db = cur.fetchone()
                    if row_db:
                        user_id = row_db[0]
                    else:
                        cur.execute(
                            """
                            INSERT INTO users(full_name, email, role, created_at, updated_at)
                            VALUES (%s, %s, 'student', now(), now())
                            RETURNING id
                            """,
                            (full_name, (email or None)),
                        )
                        user_id = cur.fetchone()[0]
                        inserted_users += 1

                    updates = []
                    params: List[Any] = []
                    if row.get("telegram"):
                        tg = normalize_telegram_link(row.get("telegram"))
                        if tg:
                            updates.append("username=%s")
                            params.append(tg)
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

                    _merge_student_profile(cur, conn, user_id, row)
                    inserted_profiles += 1

                    topic_payload = row.get("topic")
                    if row.get("has_own_topic") and topic_payload and (topic_payload.get("title") or "").strip():
                        title = topic_payload.get("title").strip()
                        cur.execute(
                            "SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s",
                            (user_id, title),
                        )
                        if not cur.fetchone():
                            desc = topic_payload.get("description") or ""
                            groundwork = row.get("groundwork")
                            if groundwork:
                                desc = (desc or "").strip()
                                tail = f"\n\nПодготовка: {groundwork}".strip()
                                desc = f"{desc}\n{tail}" if desc else tail
                            practical = topic_payload.get("practical_importance") or None
                            if practical:
                                desc = (desc or "").strip()
                                tail2 = f"\n\nПрактическая значимость: {practical}".strip()
                                desc = f"{desc}\n{tail2}" if desc else tail2
                            skills_have = ", ".join(row.get("hard_skills_have") or []) or None
                            cur.execute(
                                """
                                INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                                   required_skills, seeking_role, is_active, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, %s, 'supervisor', TRUE, now(), now())
                                """,
                                (
                                    user_id,
                                    title,
                                    desc,
                                    topic_payload.get("expected_outcomes"),
                                    skills_have,
                                ),
                            )
                            inserted_topics += 1
            return {
                "status": "success",
                "message": f"Импорт завершён: users+{inserted_users}, profiles~{inserted_profiles}, topics+{inserted_topics}",
                "stats": {
                    "inserted_users": inserted_users,
                    "inserted_profiles": inserted_profiles,
                    "inserted_topics": inserted_topics,
                },
            }
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            return {"status": "error", "message": err}

    @router.post("/api/import-supervisors", response_class=JSONResponse)
    def api_import_supervisors(spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
        try:
            service_account_file = resolve_service_account_path(
                os.getenv("SERVICE_ACCOUNT_FILE", "service-account.json")
            )
            rows = fetch_supervisor_rows(
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                service_account_file=service_account_file,
            )
            inserted_users = 0
            upserted_profiles = 0
            inserted_topics = 0

            with get_conn() as conn, conn.cursor() as cur:
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
                    row_db = cur.fetchone()
                    if row_db:
                        user_id = row_db[0]
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

                    updates = []
                    params: List[Any] = []
                    if row.get("telegram"):
                        tg = normalize_telegram_link(row.get("telegram"))
                        if tg:
                            updates.append("username=%s")
                            params.append(tg)
                    if updates:
                        params.append(user_id)
                        cur.execute(
                            f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s",
                            tuple(params),
                        )

                    cur.execute("SELECT 1 FROM supervisor_profiles WHERE user_id=%s", (user_id,))
                    exists = cur.fetchone() is not None
                    if exists:
                        cur.execute(
                            """
                            UPDATE supervisor_profiles
                            SET interests=%s, requirements=%s
                            WHERE user_id=%s
                            """,
                            (row.get("area") or None, row.get("extra_info") or None, user_id),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (user_id, None, None, None, row.get("area") or None, row.get("extra_info") or None),
                        )
                    upserted_profiles += 1

                    total_topics = 0
                    for direction_key, direction in (("topics_09", 9), ("topics_11", 11), ("topics_45", 45)):
                        total_topics += _insert_topics_from_text(cur, user_id, row.get(direction_key), direction)
                    if not any((row.get("topics_09"), row.get("topics_11"), row.get("topics_45"))):
                        total_topics += _insert_topics_from_text(cur, user_id, row.get("topics_text"), None)
                    inserted_topics += total_topics
            return {
                "status": "success",
                "message": f"Импорт завершён: users+{inserted_users}, profiles~{upserted_profiles}, topics+{inserted_topics}",
                "stats": {
                    "inserted_users": inserted_users,
                    "upserted_profiles": upserted_profiles,
                    "inserted_topics": inserted_topics,
                    "total_rows_in_sheet": len(rows) if rows else 0,
                },
            }
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            return {"status": "error", "message": err}

    return router
