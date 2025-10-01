from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from sheet_pairs import sync_roles_sheet
from utils import parse_optional_int

from ..context import AdminContext

def register(router: APIRouter, ctx: AdminContext) -> None:
    templates = ctx.templates

    @router.get('/add-topic', response_class=HTMLResponse)
    def new_topic(request: Request, msg: Optional[str] = None):
        supervisors = _load_supervisors(ctx)
        return templates.TemplateResponse(
            'admin/topic_form.html',
            {
                'request': request,
                'title': 'Добавить тему',
                'action': '/add-topic',
                'topic': {},
                'supervisors': supervisors,
                'msg': msg,
            },
        )

    @router.post('/add-topic')
    def add_topic(
        request: Request,
        title: str = Form(...),
        author_user_id: Optional[str] = Form(None),
        author_full_name: Optional[str] = Form(None),
        description: Optional[str] = Form(None),
        expected_outcomes: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        direction: Optional[str] = Form(None),
        seeking_role: str = Form('student'),
    ):
        author_full_name = (author_full_name or '').strip()
        with ctx.get_conn() as conn, conn.cursor() as cur:
            uid: Optional[int] = None
            author_uid = parse_optional_int(author_user_id)
            if author_uid is not None:
                uid = author_uid
            elif author_full_name:
                cur.execute('SELECT id FROM users WHERE full_name=%s LIMIT 1', (author_full_name,),)
                row = cur.fetchone()
                if row:
                    uid = row[0]
                else:
                    cur.execute(
                        "INSERT INTO users(full_name, role, created_at, updated_at) VALUES (%s, 'supervisor', now(), now()) RETURNING id",
                        (author_full_name,),
                    )
                    uid = cur.fetchone()[0]
            if uid is None:
                notice = urllib.parse.quote('Укажите автора темы')
                return RedirectResponse(url=f'/add-topic?msg={notice}', status_code=303)

            direction_val = parse_optional_int(direction)
            cur.execute(
                'SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s AND (direction IS NOT DISTINCT FROM %s)',
                (uid, title.strip(), direction_val),
            )
            if not cur.fetchone():
                cur.execute(
                    '''
                    INSERT INTO topics(author_user_id, title, description, expected_outcomes, required_skills, direction,
                                       seeking_role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, now(), now())
                    ''',
                    (
                        uid,
                        title.strip(),
                        description,
                        expected_outcomes,
                        required_skills,
                        direction_val,
                        seeking_role,
                    ),
                )
        notice = urllib.parse.quote('Тема добавлена')
        return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)

    @router.get('/edit-topic/{topic_id}', response_class=HTMLResponse)
    def edit_topic(request: Request, topic_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT t.*, u.full_name AS author
                FROM topics t
                JOIN users u ON u.id = t.author_user_id
                WHERE t.id = %s
                ''',
                (topic_id,),
            )
            row = cur.fetchone()
            if not row:
                notice = urllib.parse.quote('Тема не найдена')
                return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
        supervisors = _load_supervisors(ctx)
        return templates.TemplateResponse(
            'admin/topic_form.html',
            {
                'request': request,
                'title': f'Изменить тему #{topic_id}',
                'action': '/update-topic',
                'topic': dict(row),
                'supervisors': supervisors,
                'msg': msg,
            },
        )

    @router.post('/update-topic')
    def update_topic(
        request: Request,
        topic_id: int = Form(...),
        title: str = Form(...),
        author_user_id: Optional[str] = Form(None),
        author_full_name: Optional[str] = Form(None),
        description: Optional[str] = Form(None),
        expected_outcomes: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        direction: Optional[str] = Form(None),
        seeking_role: str = Form('student'),
        is_active: Optional[str] = Form(None),
    ):
        active = str(is_active or '').lower() in ('1', 'true', 'on', 'yes', 'y')
        try:
            author_id = _ensure_author(ctx, author_user_id, author_full_name)
        except ValueError:
            notice = urllib.parse.quote('Укажите автора темы')
            return RedirectResponse(url=f'/edit-topic/{topic_id}?msg={notice}', status_code=303)
        with ctx.get_conn() as conn, conn.cursor() as cur:
            direction_val = parse_optional_int(direction)
            cur.execute(
                '''
                UPDATE topics
                SET author_user_id=%s,
                    title=%s,
                    description=%s,
                    expected_outcomes=%s,
                    required_skills=%s,
                    direction=%s,
                    seeking_role=%s,
                    is_active=%s,
                    updated_at=now()
                WHERE id=%s
                ''',
                (
                    author_id,
                    title.strip(),
                    (description or None),
                    (expected_outcomes or None),
                    (required_skills or None),
                    direction_val,
                    seeking_role,
                    active,
                    topic_id,
                ),
            )
        notice = urllib.parse.quote('Тема обновлена')
        return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)

    @router.post('/topics/{topic_id}/delete')
    def delete_topic(topic_id: int):
        with ctx.get_conn() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM topics WHERE id=%s', (topic_id,))
        notice = urllib.parse.quote('Тема удалена')
        return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)

    @router.get('/topic/{topic_id}', response_class=HTMLResponse)
    def view_topic(request: Request, topic_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT t.*, u.full_name AS author
                FROM topics t
                JOIN users u ON u.id = t.author_user_id
                WHERE t.id = %s
                ''',
                (topic_id,),
            )
            topic = cur.fetchone()
            if not topic:
                notice = urllib.parse.quote('Тема не найдена')
                return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
            topic = dict(topic)
            cur.execute(
                'SELECT * FROM roles WHERE topic_id=%s ORDER BY created_at DESC, id DESC',
                (topic_id,),
            )
            roles = [dict(r) for r in cur.fetchall()]
            cur.execute(
                '''
                SELECT tc.rank, tc.score,
                       u.id AS user_id, u.full_name, u.username,
                       sp.position, sp.degree, sp.capacity, sp.interests
                FROM topic_candidates tc
                JOIN users u ON u.id = tc.user_id AND u.role = 'supervisor'
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE tc.topic_id = %s
                ORDER BY tc.rank ASC NULLS LAST, tc.score DESC NULLS LAST, u.created_at DESC
                LIMIT 10
                ''',
                (topic_id,),
            )
            supervisor_candidates = [dict(r) for r in cur.fetchall()]
            topic_title = topic.get('title') or f"Тема #{topic_id}"
            for cand in supervisor_candidates:
                name = cand.get('full_name')
                greeting = f"Здравствуйте{', ' + name if name else ''}!"
                cand['default_message'] = (
                    f"{greeting} Приглашаю вас рассмотреть тему «{topic_title}» в качестве научного руководителя."
                )
        return templates.TemplateResponse(
            'admin/view_topic.html',
            {
                'request': request,
                'topic': topic,
                'roles': roles,
                'supervisor_candidates': supervisor_candidates,
                'msg': msg,
            },
        )

    @router.get('/add-role', response_class=HTMLResponse)
    def new_role(request: Request, topic_id: int):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT id, title FROM topics WHERE id=%s', (topic_id,))
            topic = cur.fetchone()
            if not topic:
                notice = urllib.parse.quote('Тема не найдена')
                return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
        return templates.TemplateResponse(
            'admin/role_form.html',
            {
                'request': request,
                'topic': dict(topic),
            },
        )

    @router.post('/add-role')
    def add_role(
        request: Request,
        topic_id: int = Form(...),
        name: str = Form(...),
        description: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
    ):
        with ctx.get_conn() as conn, conn.cursor() as cur:
            capacity_val = parse_optional_int(capacity)
            cur.execute(
                '''
                INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, now(), now())
                ''',
                (
                    topic_id,
                    name.strip(),
                    description or None,
                    required_skills or None,
                    capacity_val,
                ),
            )
        sync_roles_sheet(ctx.get_conn)
        notice = urllib.parse.quote('Роль добавлена')
        return RedirectResponse(url=f'/topic/{topic_id}?msg={notice}', status_code=303)

    @router.get('/role/{role_id}/edit', response_class=HTMLResponse)
    def edit_role(request: Request, role_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT r.*, t.title AS topic_title
                FROM roles r
                JOIN topics t ON t.id = r.topic_id
                WHERE r.id = %s
                ''',
                (role_id,),
            )
            role = cur.fetchone()
            if not role:
                notice = urllib.parse.quote('???? ?? ???????')
                return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
        role_data = dict(role)
        topic_info = {'id': role_data['topic_id'], 'title': role_data['topic_title']}
        return templates.TemplateResponse(
            'admin/role_form.html',
            {
                'request': request,
                'topic': topic_info,
                'role': role_data,
                'form_action': '/update-role',
                'submit_label': '????????? ????',
                'page_title': f'???? #{role_id}: ??????????????',
                'msg': msg,
            },
        )

    @router.post('/update-role')
    def update_role(
        request: Request,
        role_id: int = Form(...),
        name: str = Form(...),
        description: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
    ):
        with ctx.get_conn() as conn, conn.cursor() as cur:
            capacity_val = parse_optional_int(capacity)
            cur.execute(
                '''
                UPDATE roles
                SET name=%s,
                    description=%s,
                    required_skills=%s,
                    capacity=%s,
                    updated_at=now()
                WHERE id=%s
                RETURNING topic_id
                ''',
                (name.strip(), (description or None), (required_skills or None), capacity_val, role_id),
            )
            row = cur.fetchone()
            if not row:
                notice = urllib.parse.quote('???? ?? ???????')
                return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
            topic_id_value = row[0]
        sync_roles_sheet(ctx.get_conn)
        notice = urllib.parse.quote('???? ?????????')
        return RedirectResponse(url=f'/topic/{topic_id_value}?msg={notice}', status_code=303)

    @router.post('/role/{role_id}/delete')
    def delete_role(request: Request, role_id: int):
        with ctx.get_conn() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM roles WHERE id=%s RETURNING topic_id', (role_id,))
            row = cur.fetchone()
            if not row:
                notice = urllib.parse.quote('???? ?? ???????')
                return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
            topic_id_value = row[0]
        sync_roles_sheet(ctx.get_conn)
        notice = urllib.parse.quote('???? ???????')
        return RedirectResponse(url=f'/topic/{topic_id_value}?msg={notice}', status_code=303)

    @router.get('/role/{role_id}', response_class=HTMLResponse)
    def view_role(request: Request, role_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT r.*, t.title AS topic_title, t.author_user_id, u.full_name AS author
                FROM roles r
                JOIN topics t ON t.id = r.topic_id
                JOIN users u ON u.id = t.author_user_id
                WHERE r.id = %s
                ''',
                (role_id,),
            )
            role = cur.fetchone()
            if not role:
                notice = urllib.parse.quote('Роль не найдена')
                return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
            cur.execute(
                '''
                SELECT rc.user_id, u.full_name, u.username, rc.score, rc.rank
                FROM role_candidates rc
                JOIN users u ON u.id = rc.user_id
                WHERE rc.role_id = %s
                ORDER BY rc.rank ASC NULLS LAST, rc.score DESC NULLS LAST
                LIMIT 10
                ''',
                (role_id,),
            )
            cands = cur.fetchall()
        return templates.TemplateResponse(
            'admin/view_role.html',
            {
                'request': request,
                'role': dict(role),
                'candidates': [dict(r) for r in cands],
                'msg': msg,
            },
        )


def _load_supervisors(ctx: AdminContext) -> List[Dict[str, Any]]:
    with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, full_name FROM users WHERE role='supervisor' ORDER BY full_name ASC")
        return [dict(r) for r in cur.fetchall()]


def _ensure_author(ctx: AdminContext, author_user_id: Optional[str], author_full_name: Optional[str]) -> int:
    author_uid = parse_optional_int(author_user_id)
    if author_uid is not None:
        return author_uid
    if not author_full_name:
        raise ValueError('Автор темы обязателен')
    with ctx.get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT id FROM users WHERE full_name=%s LIMIT 1', (author_full_name.strip(),))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO users(full_name, role, created_at, updated_at) VALUES (%s, 'supervisor', now(), now()) RETURNING id",
            (author_full_name.strip(),),
        )
        return cur.fetchone()[0]
