"""Endpoints dealing with topics and roles."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.responses import JSONResponse

from sheet_pairs import sync_roles_sheet
from utils import normalize_optional_str, parse_optional_int

from ..db import get_conn
from ..notifications import shorten

logger = logging.getLogger(__name__)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/add-topic", response_class=JSONResponse)
    def api_add_topic(
        author_user_id: str = Form(...),
        title: str = Form(...),
        description: Optional[str] = Form(None),
        expected_outcomes: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        seeking_role: str = Form("student"),
        direction: Optional[str] = Form(None),
    ):
        author_id_val = parse_optional_int(author_user_id)
        if author_id_val is None:
            raise HTTPException(status_code=400, detail="author_user_id must be an integer")
        title_clean = (title or "").strip()
        if not title_clean:
            raise HTTPException(status_code=400, detail="title is required")
        description_val = normalize_optional_str(description)
        expected_val = normalize_optional_str(expected_outcomes)
        required_val = normalize_optional_str(required_skills)
        direction_val = parse_optional_int(direction)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s AND (direction IS NOT DISTINCT FROM %s)",
                (author_id_val, title_clean, direction_val),
            )
            if cur.fetchone():
                return {"status": "ok", "message": "duplicate"}
            cur.execute(
                """
                INSERT INTO topics(author_user_id, title, description, expected_outcomes, required_skills, direction, seeking_role, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, now(), now())
                RETURNING id
                """,
                (author_id_val, title_clean, description_val, expected_val, required_val, direction_val, seeking_role),
            )
            topic_id = cur.fetchone()[0]
            conn.commit()
        return {"status": "ok", "topic_id": topic_id}

    @router.post("/api/add-role", response_class=JSONResponse)
    def api_add_role(
        topic_id: int = Form(...),
        name: str = Form(...),
        description: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
    ):
        logger.info(
            "api_add_role request: topic_id=%s, name=%s, description_len=%s, required_len=%s, capacity_raw=%s",
            topic_id,
            shorten(name, 80),
            len(description or ""),
            len(required_skills or ""),
            capacity,
        )
        with get_conn() as conn, conn.cursor() as cur:
            capacity_val = parse_optional_int(capacity)
            name_clean = (name or "").strip()
            if not name_clean:
                raise HTTPException(status_code=400, detail="name is required")
            description_val = normalize_optional_str(description)
            required_val = normalize_optional_str(required_skills)
            cur.execute(
                """
                INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, now(), now())
                RETURNING id
                """,
                (topic_id, name_clean, description_val, required_val, capacity_val),
            )
            role_id = cur.fetchone()[0]
            conn.commit()
        sync_roles_sheet(get_conn)
        return {"status": "ok", "role_id": role_id}

    @router.post("/api/update-topic", response_class=JSONResponse)
    def api_update_topic(
        topic_id: int = Form(...),
        editor_user_id: Optional[str] = Form(None),
        title: Optional[str] = Form(None),
        description: Optional[str] = Form(None),
        expected_outcomes: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        direction: Optional[str] = Form(None),
        seeking_role: Optional[str] = Form(None),
        is_active: Optional[str] = Form(None),
    ):
        editor_id = parse_optional_int(editor_user_id)
        direction_val = parse_optional_int(direction)
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT author_user_id, title, description, expected_outcomes, required_skills,
                       direction, seeking_role, is_active
                FROM topics
                WHERE id=%s
                """,
                (topic_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "not_found"}
            author_id = row["author_user_id"]
            if editor_id is not None and author_id is not None and author_id != editor_id:
                return {"status": "error", "message": "forbidden"}

            title_val = normalize_optional_str(title) if title is not None else row["title"]
            if not title_val:
                return {"status": "error", "message": "title_required"}
            description_val = normalize_optional_str(description) if description is not None else row["description"]
            expected_val = normalize_optional_str(expected_outcomes) if expected_outcomes is not None else row["expected_outcomes"]
            required_val = normalize_optional_str(required_skills) if required_skills is not None else row["required_skills"]
            direction_value = direction_val if direction is not None else row["direction"]
            seeking_role_val = seeking_role if seeking_role is not None else row["seeking_role"]
            is_active_val = row["is_active"] if is_active is None else (is_active.strip().lower() in ("true", "1", "yes"))

            cur.execute(
                """
                UPDATE topics
                SET title=%s, description=%s, expected_outcomes=%s, required_skills=%s,
                    direction=%s, seeking_role=%s, is_active=%s, updated_at=now()
                WHERE id=%s
                """,
                (
                    title_val,
                    description_val,
                    expected_val,
                    required_val,
                    direction_value,
                    seeking_role_val,
                    is_active_val,
                    topic_id,
                ),
            )
            conn.commit()
        return {"status": "ok"}

    @router.post("/api/update-role", response_class=JSONResponse)
    def api_update_role(
        role_id: int = Form(...),
        editor_user_id: Optional[str] = Form(None),
        name: Optional[str] = Form(None),
        description: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
    ):
        editor_id = parse_optional_int(editor_user_id)
        capacity_val = parse_optional_int(capacity)
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT r.topic_id, r.name, r.description, r.required_skills, r.capacity, t.author_user_id
                FROM roles r
                JOIN topics t ON t.id = r.topic_id
                WHERE r.id=%s
                """,
                (role_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "not_found"}
            author_id = row["author_user_id"]
            if editor_id is not None and author_id is not None and author_id != editor_id:
                return {"status": "error", "message": "forbidden"}
            name_val = normalize_optional_str(name) if name is not None else row["name"]
            if not name_val:
                return {"status": "error", "message": "name_required"}
            description_val = normalize_optional_str(description) if description is not None else row["description"]
            required_val = normalize_optional_str(required_skills) if required_skills is not None else row["required_skills"]
            capacity_value = capacity_val if capacity is not None else row["capacity"]
            cur.execute(
                """
                UPDATE roles
                SET name=%s, description=%s, required_skills=%s, capacity=%s, updated_at=now()
                WHERE id=%s
                """,
                (
                    name_val,
                    description_val,
                    required_val,
                    capacity_value,
                    role_id,
                ),
            )
            conn.commit()
        return {"status": "ok", "topic_id": row["topic_id"]}

    @router.get("/api/user-topics/{user_id}", response_class=JSONResponse)
    def api_user_topics(user_id: int, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        params = {"uid": user_id, "offset": offset, "limit": limit}
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    t.id,
                    t.title,
                    t.description,
                    t.expected_outcomes,
                    t.required_skills,
                    t.seeking_role,
                    t.direction,
                    t.is_active,
                    t.created_at,
                    t.author_user_id,
                    (t.author_user_id = %(uid)s) AS is_author,
                    (t.approved_supervisor_user_id = %(uid)s) AS is_approved_supervisor,
                    EXISTS(
                        SELECT 1
                        FROM roles rs
                        WHERE rs.topic_id = t.id AND rs.approved_student_user_id = %(uid)s
                    ) AS is_approved_student,
                    COALESCE(
                        (
                            SELECT ARRAY_AGG(DISTINCT rs.name)
                            FROM roles rs
                            WHERE rs.topic_id = t.id
                              AND rs.approved_student_user_id = %(uid)s
                              AND rs.name IS NOT NULL
                              AND rs.name <> ''
                        ),
                        ARRAY[]::text[]
                    ) AS approved_role_names,
                    COALESCE(
                        (
                            SELECT ARRAY_AGG(DISTINCT rs.id)
                            FROM roles rs
                            WHERE rs.topic_id = t.id AND rs.approved_student_user_id = %(uid)s
                        ),
                        ARRAY[]::bigint[]
                    ) AS approved_role_ids
                FROM topics t
                WHERE t.author_user_id = %(uid)s
                   OR t.approved_supervisor_user_id = %(uid)s
                   OR EXISTS (
                        SELECT 1
                        FROM roles r2
                        WHERE r2.topic_id = t.id AND r2.approved_student_user_id = %(uid)s
                   )
                ORDER BY t.created_at DESC
                OFFSET %(offset)s LIMIT %(limit)s
                """,
                params,
            )
            rows = cur.fetchall()
            normalized: List[Dict[str, Any]] = []
            for row in rows:
                data = dict(row)
                role_names = data.get("approved_role_names") or []
                if isinstance(role_names, list):
                    data["approved_role_names"] = [str(name) for name in role_names if name]
                elif role_names in (None, ""):
                    data["approved_role_names"] = []
                else:
                    data["approved_role_names"] = [str(role_names)]
                role_ids = data.get("approved_role_ids") or []
                if isinstance(role_ids, list):
                    cleaned_ids = []
                    for rid in role_ids:
                        if rid in (None, ""):
                            continue
                        try:
                            cleaned_ids.append(int(rid))
                        except Exception:
                            continue
                    data["approved_role_ids"] = cleaned_ids
                else:
                    data["approved_role_ids"] = []
                normalized.append(data)
            return normalized

    @router.get("/api/roles/{role_id}", response_class=JSONResponse)
    def api_get_role(role_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT r.*, t.title AS topic_title, t.author_user_id, u.full_name AS author
                FROM roles r
                JOIN topics t ON t.id = r.topic_id
                JOIN users u ON u.id = t.author_user_id
                WHERE r.id = %s
                """,
                (role_id,),
            )
            row = cur.fetchone()
            if not row:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return dict(row)

    @router.get("/api/topics/{topic_id}/roles", response_class=JSONResponse)
    def api_get_topic_roles(topic_id: int, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT r.*
                FROM roles r
                WHERE r.topic_id = %s
                ORDER BY r.created_at DESC
                OFFSET %s LIMIT %s
                """,
                (topic_id, offset, limit),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    @router.get("/api/topic-candidates/{topic_id}", response_class=JSONResponse)
    def api_topic_candidates(
        topic_id: int,
        role: Optional[str] = Query(None, pattern="^(student|supervisor)$"),
        limit: int = Query(5, ge=1, le=50),
    ):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT tc.user_id, u.full_name, u.username, u.role, tc.score, tc.rank
                FROM topic_candidates tc
                JOIN users u ON u.id = tc.user_id AND u.role = 'supervisor'
                WHERE tc.topic_id = %s
                ORDER BY tc.rank ASC NULLS LAST, tc.score DESC NULLS LAST, u.created_at DESC
                LIMIT %s
                """,
                (topic_id, limit),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    @router.get("/api/user-candidates/{user_id}", response_class=JSONResponse)
    def api_user_candidates(user_id: int, limit: int = Query(5, ge=1, le=50)):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT role FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            role = row.get("role") if row else None
            if role == "student":
                cur.execute(
                    """
                    SELECT sc.role_id, r.name AS role_name, sc.score, sc.rank, r.topic_id, t.title AS topic_title
                    FROM student_candidates sc
                    JOIN roles r ON r.id = sc.role_id
                    JOIN topics t ON t.id = r.topic_id
                    WHERE sc.user_id = %s
                    ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT sc.topic_id, t.title, sc.score, sc.rank
                    FROM supervisor_candidates sc
                    JOIN topics t ON t.id = sc.topic_id
                    WHERE sc.user_id = %s
                    ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    @router.get("/api/role-candidates/{role_id}", response_class=JSONResponse)
    def api_role_candidates(role_id: int, limit: int = Query(5, ge=1, le=50)):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT rc.user_id, u.full_name, u.username, rc.score, rc.rank
                FROM role_candidates rc
                JOIN users u ON u.id = rc.user_id AND u.role = 'student'
                WHERE rc.role_id = %s
                ORDER BY rc.rank ASC NULLS LAST, rc.score DESC NULLS LAST, u.created_at DESC
                LIMIT %s
                """,
                (role_id, limit),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    @router.get("/latest", response_class=JSONResponse)
    def latest(kind: str = Query("topics", enum=["students", "supervisors", "topics"]), offset: int = 0):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if kind == "students":
                cur.execute(
                    """
                    SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                           sp.program, sp.skills, sp.interests
                    FROM users u
                    LEFT JOIN student_profiles sp ON sp.user_id = u.id
                    WHERE u.role = 'student'
                    ORDER BY u.created_at DESC
                    OFFSET %s LIMIT 10
                    """,
                    (max(0, offset),),
                )
            elif kind == "supervisors":
                cur.execute(
                    """
                    SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                           sup.position, sup.degree, sup.capacity, sup.interests
                    FROM users u
                    LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
                    WHERE u.role = 'supervisor'
                    ORDER BY u.created_at DESC
                    OFFSET %s LIMIT 10
                    """,
                    (max(0, offset),),
                )
            else:
                cur.execute(
                    """
                    SELECT t.id, t.title, t.seeking_role, t.direction, t.created_at, u.full_name AS author
                    FROM topics t
                    JOIN users u ON u.id = t.author_user_id
                    ORDER BY t.created_at DESC
                    OFFSET %s LIMIT 10
                    """,
                    (max(0, offset),),
                )
            rows = cur.fetchall()
            serializable_rows = []
            for row in rows:
                row_dict = dict(row)
                if "created_at" in row_dict and row_dict["created_at"]:
                    row_dict["created_at"] = row_dict["created_at"].isoformat()
                serializable_rows.append(row_dict)
        return JSONResponse(serializable_rows)

    @router.post("/api/roles/{role_id}/clear-approved", response_class=JSONResponse)
    def api_clear_role_approved(role_id: int, by_user_id: int = Form(...)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT r.approved_student_user_id, t.author_user_id FROM roles r JOIN topics t ON t.id = r.topic_id WHERE r.id=%s",
                (role_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "role not found"}
            approved_student_id, author_id = row
            if (approved_student_id is None) or (by_user_id not in (approved_student_id, author_id)):
                return {"status": "error", "message": "not allowed"}
            cur.execute("UPDATE roles SET approved_student_user_id=NULL WHERE id=%s", (role_id,))
            conn.commit()
        sync_roles_sheet(get_conn)
        return {"status": "ok"}

    @router.post("/api/topics/{topic_id}/clear-approved-supervisor", response_class=JSONResponse)
    def api_clear_topic_supervisor(topic_id: int, by_user_id: int = Form(...)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT approved_supervisor_user_id, author_user_id FROM topics WHERE id=%s", (topic_id,))
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "topic not found"}
            approved_supervisor_id, author_id = row
            if (approved_supervisor_id is None) or (by_user_id not in (approved_supervisor_id, author_id)):
                return {"status": "error", "message": "not allowed"}
            cur.execute("UPDATE topics SET approved_supervisor_user_id=NULL WHERE id=%s", (topic_id,))
            conn.commit()
        sync_roles_sheet(get_conn)
        return {"status": "ok"}

    return router
