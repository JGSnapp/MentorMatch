"""Identity and self-service endpoints."""
from __future__ import annotations

from typing import Any, List, Optional

import psycopg2.extras
from fastapi import APIRouter, Form, Query
from fastapi.responses import JSONResponse

from ..db import get_conn
from ..telegram_utils import extract_tg_username, normalize_telegram_link
from utils import normalize_optional_str, parse_optional_int


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/whoami", response_class=JSONResponse)
    def api_whoami(tg_id: Optional[int] = Query(None), username: Optional[str] = Query(None)):
        uname = extract_tg_username(username)
        link = normalize_telegram_link(username) if username else None
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if tg_id:
                cur.execute(
                    "SELECT id, full_name, role, email, username, telegram_id, is_confirmed FROM users WHERE telegram_id=%s",
                    (int(tg_id),),
                )
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    return {"status": "ok", "matches": rows}
            params: List[Any] = []
            clauses: List[str] = []
            if link:
                clauses.append("LOWER(username)=LOWER(%s)")
                params.append(link)
            if uname:
                clauses.append("LOWER(username)=LOWER(%s)")
                params.append(f"https://t.me/{uname}")
            if not clauses:
                return {"status": "ok", "matches": []}
            sql = (
                "SELECT id, full_name, role, email, username, telegram_id, is_confirmed FROM users WHERE ("
                + " OR ".join(clauses)
                + ") LIMIT 5"
            )
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            return {"status": "ok", "matches": rows}

    @router.post("/api/bind-telegram", response_class=JSONResponse)
    def api_bind_telegram(
        user_id: int = Form(...),
        tg_id: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
    ):
        link = normalize_telegram_link(username) if username else None
        tg_id_val = parse_optional_int(tg_id)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET telegram_id=COALESCE(%s, telegram_id),
                    username=COALESCE(%s, username),
                    is_confirmed=TRUE,
                    updated_at=now()
                WHERE id=%s
                """,
                (tg_id_val, link, user_id),
            )
            conn.commit()
        return {"status": "ok"}

    @router.post("/api/self-register", response_class=JSONResponse)
    def api_self_register(
        role: str = Form(...),
        full_name: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
        tg_id: Optional[str] = Form(None),
        email: Optional[str] = Form(None),
    ):
        r = (role or "").strip().lower()
        if r not in ("student", "supervisor"):
            return {"status": "error", "message": "role must be student or supervisor"}
        link = normalize_telegram_link(username) if username else None
        tg_id_val = parse_optional_int(tg_id)
        tg_id_for_name = extract_tg_username(username) or (str(tg_id).strip() if tg_id else "")
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users(full_name, email, username, telegram_id, role, is_confirmed, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, TRUE, now(), now())
                RETURNING id
                """,
                (
                    (full_name or f"Telegram user {tg_id_for_name}").strip(),
                    (email or None),
                    link,
                    tg_id_val,
                    r,
                ),
            )
            user_id = cur.fetchone()[0]
            if r == "student":
                cur.execute("INSERT INTO student_profiles(user_id) VALUES (%s)", (user_id,))
            else:
                cur.execute("INSERT INTO supervisor_profiles(user_id) VALUES (%s)", (user_id,))
            conn.commit()
        return {"status": "ok", "user_id": user_id, "role": r}

    @router.post("/api/update-supervisor-profile", response_class=JSONResponse)
    def api_update_supervisor_profile(
        user_id: int = Form(...),
        position: Optional[str] = Form(None),
        degree: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
        interests: Optional[str] = Form(None),
        requirements: Optional[str] = Form(None),
    ):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            capacity_val = parse_optional_int(capacity)
            cur.execute(
                "SELECT position, degree, capacity, interests, requirements FROM supervisor_profiles WHERE user_id=%s",
                (user_id,),
            )
            existing = cur.fetchone()
            position_val = (
                normalize_optional_str(position)
                if position is not None
                else (existing.get("position") if existing else None)
            )
            degree_val = (
                normalize_optional_str(degree)
                if degree is not None
                else (existing.get("degree") if existing else None)
            )
            interests_val = (
                normalize_optional_str(interests)
                if interests is not None
                else (existing.get("interests") if existing else None)
            )
            requirements_val = (
                normalize_optional_str(requirements)
                if requirements is not None
                else (existing.get("requirements") if existing else None)
            )

            if existing:
                cur.execute(
                    """
                    UPDATE supervisor_profiles
                    SET position=%s, degree=%s, capacity=%s, interests=%s, requirements=%s
                    WHERE user_id=%s
                    """,
                    (
                        position_val,
                        degree_val,
                        capacity_val,
                        interests_val,
                        requirements_val,
                        user_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        position_val,
                        degree_val,
                        capacity_val,
                        interests_val,
                        requirements_val,
                    ),
                )
            conn.commit()
        return {"status": "ok"}

    return router
