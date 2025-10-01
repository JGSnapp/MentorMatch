"""Student facing API endpoints."""
from __future__ import annotations

from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Form, Query
from fastapi.responses import JSONResponse

from ..db import get_conn
from utils import normalize_optional_str

from ..services.cv import process_cv


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/students", response_class=JSONResponse)
    def api_get_students(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                       sp.program, sp.skills, sp.interests, sp.cv
                FROM users u
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE u.role = 'student'
                ORDER BY u.created_at DESC
                OFFSET %s LIMIT %s
                """,
                (offset, limit),
            )
            students = cur.fetchall()
            return [dict(student) for student in students]

    @router.get("/api/students/{student_id}", response_class=JSONResponse)
    def api_get_student(student_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                       sp.program, sp.skills, sp.interests, sp.cv
                FROM users u
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE u.role = 'student' AND u.id = %s
                """,
                (student_id,),
            )
            row = cur.fetchone()
            if not row:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return dict(row)

    @router.post("/api/update-student-profile", response_class=JSONResponse)
    def api_update_student_profile(
        user_id: int = Form(...),
        program: Optional[str] = Form(None),
        skills: Optional[str] = Form(None),
        interests: Optional[str] = Form(None),
        cv: Optional[str] = Form(None),
        skills_to_learn: Optional[str] = Form(None),
        achievements: Optional[str] = Form(None),
        workplace: Optional[str] = Form(None),
    ):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT program, skills, interests, cv, skills_to_learn, achievements, workplace FROM student_profiles WHERE user_id=%s",
                (user_id,),
            )
            existing = cur.fetchone()
            program_val = normalize_optional_str(program) if program is not None else (existing.get("program") if existing else None)
            skills_val = normalize_optional_str(skills) if skills is not None else (existing.get("skills") if existing else None)
            interests_val = normalize_optional_str(interests) if interests is not None else (existing.get("interests") if existing else None)
            skills_to_learn_val = normalize_optional_str(skills_to_learn) if skills_to_learn is not None else (existing.get("skills_to_learn") if existing else None)
            achievements_val = normalize_optional_str(achievements) if achievements is not None else (existing.get("achievements") if existing else None)
            workplace_val = normalize_optional_str(workplace) if workplace is not None else (existing.get("workplace") if existing else None)
            cv_val = normalize_optional_str(cv) if cv is not None else (existing.get("cv") if existing else None)

            stored_cv = process_cv(conn, user_id, cv_val)

            if existing:
                cur.execute(
                    """
                    UPDATE student_profiles
                    SET program=%s, skills=%s, interests=%s, cv=%s, skills_to_learn=%s, achievements=%s, workplace=%s
                    WHERE user_id=%s
                    """,
                    (
                        program_val,
                        skills_val,
                        interests_val,
                        stored_cv,
                        skills_to_learn_val,
                        achievements_val,
                        workplace_val,
                        user_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO student_profiles(user_id, program, skills, interests, cv, skills_to_learn, achievements, workplace)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        program_val,
                        skills_val,
                        interests_val,
                        stored_cv,
                        skills_to_learn_val,
                        achievements_val,
                        workplace_val,
                    ),
                )
            conn.commit()
        return {"status": "ok"}

    @router.get("/api/student-candidates/{user_id}", response_class=JSONResponse)
    def api_student_candidates(user_id: int, limit: int = Query(5, ge=1, le=50)):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    return router
