from __future__ import annotations

import os
import urllib.parse
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2.extras
from fastapi import APIRouter, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..clients.google_data_client import sync_roles_sheet
from ..context import AdminContext
from ..embedding_queue import enqueue_refresh, commit_with_refresh
from ..utils_common import parse_optional_int

PAGE_LIMIT = 20


def _fetch_students(conn, offset: int, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Получает страницу студентов и признак наличия следующих записей."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sp.direction, sp.education_program, sp.skills, sp.skills_to_learn,
                   sp.interests, sp.hours_per_week, sp.team_role
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.role = 'student'
            ORDER BY u.created_at DESC
            OFFSET %s LIMIT %s
            """,
            (offset, limit + 1),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return rows[:limit], len(rows) > limit


def _fetch_supervisors(conn, offset: int, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Возвращает страницу наставников вместе с флагом продолжения."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sup.position, sup.degree, sup.capacity, sup.interests
            FROM users u
            LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
            WHERE u.role = 'supervisor'
            ORDER BY u.created_at DESC
            OFFSET %s LIMIT %s
            """,
            (offset, limit + 1),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return rows[:limit], len(rows) > limit


def _fetch_topics(conn, offset: int, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Загружает темы для панели администрирования с пагинацией."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT t.id, t.title, t.seeking_role, t.direction, t.created_at,
                   u.full_name AS author
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            ORDER BY t.created_at DESC
            OFFSET %s LIMIT %s
            """,
            (offset, limit + 1),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return rows[:limit], len(rows) > limit


def _fetch_role_topics(conn, topic_ids: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
    """Собирает темы и связанные роли для отображения в интерфейсе."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        params: Tuple[Any, ...]
        query = (
            """
            SELECT r.id AS role_id,
                   r.name AS role_name,
                   t.id AS topic_id,
                   COALESCE(
                       json_agg(
                           json_build_object(
                               'student_id', aps.student_id,
                               'full_name', stu.full_name
                           )
                           ORDER BY stu.full_name
                       ) FILTER (WHERE aps.student_id IS NOT NULL),
                       '[]'::json
                   ) AS approved_students,
                   t.title AS topic_title,
                   t.author_user_id,
                   author.full_name AS author_name,
                   t.approved_supervisor_user_id,
                   sup.full_name AS approved_supervisor_name
            FROM topics t
            LEFT JOIN roles r ON r.topic_id = t.id
            JOIN users author ON author.id = t.author_user_id
            LEFT JOIN approved_students aps ON aps.role_id = r.id
            LEFT JOIN users stu ON stu.id = aps.student_id
            LEFT JOIN users sup ON sup.id = t.approved_supervisor_user_id
            {where_clause}
            GROUP BY r.id, t.id, t.title, t.author_user_id, author.full_name, t.approved_supervisor_user_id, sup.full_name
            ORDER BY t.created_at DESC, r.id ASC NULLS LAST
            """
        )
        if topic_ids:
            where_clause = "WHERE t.id = ANY(%s)"
            params = (list(topic_ids),)
        else:
            where_clause = ""
            params = tuple()
        cur.execute(query.format(where_clause=where_clause), params)
        rows = cur.fetchall()

    topic_map: Dict[int, Dict[str, Any]] = {}
    topic_order: List[int] = []
    for raw in rows:
        row = dict(raw)
        topic_id = row["topic_id"]
        topic = topic_map.get(topic_id)
        if not topic:
            topic = {
                "id": topic_id,
                "title": row.get("topic_title"),
                "author_user_id": row.get("author_user_id"),
                "author_name": row.get("author_name"),
                "approved_supervisor_user_id": row.get("approved_supervisor_user_id"),
                "approved_supervisor_name": row.get("approved_supervisor_name"),
                "supervisor_locked": (
                    row.get("approved_supervisor_user_id") is not None
                    and row.get("approved_supervisor_user_id") == row.get("author_user_id")
                ),
                "roles": [],
            }
            topic_map[topic_id] = topic
            topic_order.append(topic_id)
        role_id = row.get("role_id")
        if role_id is not None:
            approved_students: List[Dict[str, Any]]
            raw_approved = row.get("approved_students")
            if isinstance(raw_approved, list):
                approved_students = [
                    {"id": item.get("student_id"), "full_name": item.get("full_name")}
                    for item in raw_approved
                    if isinstance(item, dict)
                ]
            else:
                approved_students = []
            topic["roles"].append(
                {
                    "id": role_id,
                    "name": row.get("role_name"),
                    "approved_students": approved_students,
                }
            )
    if topic_ids:
        ordered = [topic_map[tid] for tid in topic_ids if tid in topic_map]
        return ordered
    return [topic_map[tid] for tid in topic_order]


def _fetch_people(conn, role: str) -> List[Dict[str, Any]]:
    """Возвращает список пользователей определённой роли по алфавиту."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, full_name FROM users WHERE role=%s ORDER BY full_name ASC", (role,))
        return [dict(r) for r in cur.fetchall()]


def register(router: APIRouter, ctx: AdminContext) -> None:
    """Регистрирует страницы административной панели мониторинга."""
    templates = ctx.templates

    @router.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        tab: str = "topics",
        page: int = 0,
        msg: Optional[str] = None,
    ):
        """Отображает главную страницу админки с выбранной вкладкой."""
        allowed_tabs = {"topics", "students", "supervisors"}
        current_tab = tab if tab in allowed_tabs else "topics"
        current_page = max(page, 0)
        offset = current_page * PAGE_LIMIT
        items: List[Dict[str, Any]] = []
        role_topics: List[Dict[str, Any]] = []
        all_students: List[Dict[str, Any]] = []
        all_supervisors: List[Dict[str, Any]] = []
        has_next = False

        with ctx.get_conn() as conn:
            if current_tab == "students":
                items, has_next = _fetch_students(conn, offset, PAGE_LIMIT)
            elif current_tab == "supervisors":
                items, has_next = _fetch_supervisors(conn, offset, PAGE_LIMIT)
            else:
                items, has_next = _fetch_topics(conn, offset, PAGE_LIMIT)
                topic_ids = [topic["id"] for topic in items]
                if topic_ids:
                    role_topics = _fetch_role_topics(conn, topic_ids)
                else:
                    role_topics = []
                all_students = _fetch_people(conn, "student")
                all_supervisors = _fetch_people(conn, "supervisor")
        role_topics_map = {t["id"]: t for t in role_topics}
        has_prev = current_page > 0

        return templates.TemplateResponse(
            "admin/dashboard.html",
            {
                "request": request,
                "tab": current_tab,
                "page": current_page,
                "items": items,
                "role_topics": role_topics,
                "role_topics_map": role_topics_map,
                "all_students": all_students,
                "all_supervisors": all_supervisors,
                "msg": msg,
                "limit": PAGE_LIMIT,
                "has_prev": has_prev,
                "has_next": has_next,
                "spreadsheet_id": os.getenv("STUDENT_SPREADSHEET_ID", "") or os.getenv("SPREADSHEET_ID", ""),
            },
        )

    @router.post("/save-approvals")
    async def save_approvals(request: Request):
        """Обрабатывает форму утверждений студентов и наставников."""
        form = await request.form()
        role_updates: Dict[int, Optional[int]] = {}
        topic_updates: Dict[int, Optional[int]] = {}
        for key, value in form.multi_items():
            if key.startswith("role_student_"):
                try:
                    role_id = int(key[len("role_student_"):])
                except ValueError:
                    continue
                role_updates[role_id] = parse_optional_int(value)
            elif key.startswith("topic_supervisor_"):
                try:
                    topic_id = int(key[len("topic_supervisor_"):])
                except ValueError:
                    continue
                topic_updates[topic_id] = parse_optional_int(value)

        message = _apply_assignment_updates(ctx, role_updates, topic_updates)
        quoted = urllib.parse.quote(message)
        return RedirectResponse(url=f"/?msg={quoted}&tab=topics", status_code=303)

    @router.post("/assignments", response_class=JSONResponse)
    async def update_assignment(payload: Dict[str, Any] = Body(...)):
        """Принимает AJAX-запрос для обновления назначений по ролям и темам."""
        role_updates: Dict[int, Optional[int]] = {}
        role_additions: List[Tuple[int, int]] = []
        role_removals: List[Tuple[int, int]] = []
        topic_updates: Dict[int, Optional[int]] = {}
        if "role_id" in payload:
            role_id = int(payload["role_id"])
            student_id = parse_optional_int(payload.get("student_id"))
            action = str(payload.get("action") or "add").lower()
            if action == "remove" and student_id is not None:
                role_removals.append((role_id, student_id))
            elif action == "set":
                role_updates[role_id] = student_id
            elif student_id is not None:
                role_additions.append((role_id, student_id))
        if "topic_id" in payload:
            topic_updates[int(payload["topic_id"])] = parse_optional_int(payload.get("supervisor_id"))
        message = _apply_assignment_updates(ctx, role_updates, topic_updates, role_additions, role_removals)
        return JSONResponse({"status": "ok", "message": message})


def _apply_assignment_updates(
    ctx: AdminContext,
    role_updates: Dict[int, Optional[int]],
    topic_updates: Dict[int, Optional[int]],
    role_additions: Optional[Sequence[Tuple[int, int]]] = None,
    role_removals: Optional[Sequence[Tuple[int, int]]] = None,
) -> str:
    """Сохраняет выбранных студентов и наставников и запускает обновление эмбеддингов."""
    updated_roles = 0
    updated_topics = 0
    role_additions = role_additions or []
    role_removals = role_removals or []

    if role_updates or topic_updates or role_additions or role_removals:
        with ctx.get_conn() as conn, conn.cursor() as cur:
            for role_id, student_id in role_updates.items():
                cur.execute("SELECT 1 FROM roles WHERE id=%s", (role_id,))
                if not cur.fetchone():
                    continue
                cur.execute("DELETE FROM approved_students WHERE role_id=%s", (role_id,))
                if student_id is not None:
                    cur.execute("SELECT 1 FROM users WHERE id=%s AND role='student'", (student_id,))
                    if not cur.fetchone():
                        continue
                    cur.execute(
                        "INSERT INTO approved_students(student_id, role_id) VALUES (%s, %s) ON CONFLICT (role_id, student_id) DO NOTHING",
                        (student_id, role_id),
                    )
                updated_roles += 1
                enqueue_refresh(conn, "role", role_id)

            for role_id, student_id in role_removals:
                cur.execute(
                    "DELETE FROM approved_students WHERE role_id=%s AND student_id=%s",
                    (role_id, student_id),
                )
                if cur.rowcount:
                    updated_roles += 1
                    enqueue_refresh(conn, "role", role_id)

            for role_id, student_id in role_additions:
                cur.execute("SELECT 1 FROM roles WHERE id=%s", (role_id,))
                if not cur.fetchone():
                    continue
                cur.execute("SELECT 1 FROM users WHERE id=%s AND role='student'", (student_id,))
                if not cur.fetchone():
                    continue
                cur.execute(
                    "INSERT INTO approved_students(student_id, role_id) VALUES (%s, %s) ON CONFLICT (role_id, student_id) DO NOTHING",
                    (student_id, role_id),
                )
                updated_roles += 1
                enqueue_refresh(conn, "role", role_id)

            for topic_id, supervisor_id in topic_updates.items():
                cur.execute(
                    "SELECT approved_supervisor_user_id, author_user_id FROM topics WHERE id=%s",
                    (topic_id,),
                )
                row = cur.fetchone()
                if not row:
                    continue
                current_supervisor_id, author_id = row
                if current_supervisor_id == author_id and supervisor_id != author_id:
                    continue
                if supervisor_id is not None:
                    cur.execute("SELECT 1 FROM users WHERE id=%s AND role='supervisor'", (supervisor_id,))
                    if not cur.fetchone():
                        continue
                if current_supervisor_id == supervisor_id:
                    continue
                cur.execute(
                    "UPDATE topics SET approved_supervisor_user_id=%s, updated_at=now() WHERE id=%s",
                    (supervisor_id, topic_id),
                )
                updated_topics += 1
                enqueue_refresh(conn, "topic", topic_id)
            commit_with_refresh(conn)

    sheet_synced = sync_roles_sheet()
    msg_parts = [
        f"обновлено ролей: {updated_roles}",
        f"обновлено руководителей: {updated_topics}",
    ]
    if sheet_synced:
        msg_parts.append("данные выгружены в Google Sheets")
    else:
        msg_parts.append("не удалось обновить Google Sheets (проверьте настройки)")
    return "; ".join(msg_parts)
