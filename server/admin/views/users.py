from __future__ import annotations

import urllib.parse
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from utils import parse_optional_int

from ..context import AdminContext
from ..utils import normalize_telegram_link


def register(router: APIRouter, ctx: AdminContext) -> None:
    templates = ctx.templates

    @router.get('/add-student', response_class=HTMLResponse)
    def new_student(request: Request, msg: Optional[str] = None):
        return templates.TemplateResponse(
            'admin/student_form.html',
            {
                'request': request,
                'title': 'Добавить студента',
                'action': '/add-student',
                'student': {},
                'msg': msg,
            },
        )

    @router.post('/add-student')
    def add_student(
        request: Request,
        full_name: str = Form(...),
        email: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
        program: Optional[str] = Form(None),
        skills: Optional[str] = Form(None),
        interests: Optional[str] = Form(None),
        cv: Optional[str] = Form(None),
    ):
        full_name = (full_name or '').strip()
        if not full_name:
            notice = urllib.parse.quote('Укажите имя студента')
            return RedirectResponse(url=f'/add-student?msg={notice}', status_code=303)
        username_normalized = normalize_telegram_link(username)
        with ctx.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                '''
                INSERT INTO users(full_name, email, username, role, created_at, updated_at)
                VALUES (%s, %s, %s, 'student', now(), now())
                RETURNING id
                ''',
                (full_name, email, username_normalized),
            )
            user_id = cur.fetchone()[0]
            cur.execute(
                '''
                INSERT INTO student_profiles(user_id, program, skills, interests, cv)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    program = EXCLUDED.program,
                    skills = EXCLUDED.skills,
                    interests = EXCLUDED.interests,
                    cv = EXCLUDED.cv
                ''',
                (user_id, program, skills, interests, cv),
            )
        notice = urllib.parse.quote('Студент добавлен')
        return RedirectResponse(url=f'/?tab=students&msg={notice}', status_code=303)

    @router.get('/add-supervisor', response_class=HTMLResponse)
    def new_supervisor(request: Request, msg: Optional[str] = None):
        return templates.TemplateResponse(
            'admin/supervisor_form.html',
            {
                'request': request,
                'title': 'Добавить руководителя',
                'action': '/add-supervisor',
                'supervisor': {},
                'msg': msg,
            },
        )

    @router.post('/add-supervisor')
    def add_supervisor(
        request: Request,
        full_name: str = Form(...),
        email: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
        position: Optional[str] = Form(None),
        degree: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
        requirements: Optional[str] = Form(None),
        interests: Optional[str] = Form(None),
    ):
        full_name = (full_name or '').strip()
        if not full_name:
            notice = urllib.parse.quote('Укажите имя руководителя')
            return RedirectResponse(url=f'/add-supervisor?msg={notice}', status_code=303)
        username_normalized = normalize_telegram_link(username)
        with ctx.get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT id FROM users WHERE full_name=%s AND role=\'supervisor\' LIMIT 1', (full_name,))
            row = cur.fetchone()
            if row:
                user_id = row[0]
                cur.execute(
                    '''
                    UPDATE users SET email=%s, username=%s, updated_at=now()
                    WHERE id=%s
                    ''',
                    (email, username_normalized, user_id),
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO users(full_name, email, username, role, created_at, updated_at)
                    VALUES (%s, %s, %s, 'supervisor', now(), now())
                    RETURNING id
                    ''',
                    (full_name, email, username_normalized),
                )
                user_id = cur.fetchone()[0]

            capacity_val = parse_optional_int(capacity)
            cur.execute('SELECT 1 FROM supervisor_profiles WHERE user_id=%s', (user_id,))
            if cur.fetchone():
                cur.execute(
                    '''
                    UPDATE supervisor_profiles
                    SET position=%s, degree=%s, capacity=%s, requirements=%s, interests=%s
                    WHERE user_id=%s
                    ''',
                    (position, degree, capacity_val, requirements, interests, user_id),
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, requirements, interests)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''',
                    (user_id, position, degree, capacity_val, requirements, interests),
                )
        notice = urllib.parse.quote('Руководитель добавлен')
        return RedirectResponse(url=f'/?tab=supervisors&msg={notice}', status_code=303)

    @router.get('/user/{user_id}', response_class=HTMLResponse)
    def view_user(request: Request, user_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT * FROM users WHERE id=%s', (user_id,))
            user = cur.fetchone()
            if not user:
                notice = urllib.parse.quote('Пользователь не найден')
                return RedirectResponse(url=f'/?tab=students&msg={notice}', status_code=303)
            user = dict(user)
            student = None
            supervisor = None
            recommended_roles = []
            recommended_topics = []
            if user.get('role') == 'student':
                cur.execute('SELECT * FROM student_profiles WHERE user_id=%s', (user_id,))
                row = cur.fetchone()
                student = dict(row) if row else None
                cur.execute(
                    '''
                    SELECT sc.role_id, sc.rank, sc.score,
                           r.name AS role_name, r.description, r.required_skills, r.capacity,
                           t.id AS topic_id, t.title AS topic_title, t.author_user_id,
                           u.full_name AS author_name
                    FROM student_candidates sc
                    JOIN roles r ON r.id = sc.role_id
                    JOIN topics t ON t.id = r.topic_id
                    JOIN users u ON u.id = t.author_user_id
                    WHERE sc.user_id = %s
                    ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
                    LIMIT 10
                    ''',
                    (user_id,),
                )
                recommended_roles = [dict(r) for r in cur.fetchall()]
                for r in recommended_roles:
                    role_label = r.get('role_name') or f"Роль #{r.get('role_id')}"
                    topic_label = r.get('topic_title') or f"Тема #{r.get('topic_id')}"
                    r['default_message'] = (
                        f"Здравствуйте! Хотел(а) бы присоединиться к роли «{role_label}» по теме «{topic_label}»."
                    )
            elif user.get('role') == 'supervisor':
                cur.execute('SELECT * FROM supervisor_profiles WHERE user_id=%s', (user_id,))
                row = cur.fetchone()
                supervisor = dict(row) if row else None
                cur.execute(
                    '''
                    SELECT sc.topic_id, sc.rank, sc.score,
                           t.title, t.description, t.required_skills, t.expected_outcomes, t.direction,
                           t.author_user_id, u.full_name AS author_name
                    FROM supervisor_candidates sc
                    JOIN topics t ON t.id = sc.topic_id
                    JOIN users u ON u.id = t.author_user_id
                    WHERE sc.user_id = %s
                    ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
                    LIMIT 10
                    ''',
                    (user_id,),
                )
                recommended_topics = [dict(r) for r in cur.fetchall()]
                for t in recommended_topics:
                    topic_label = t.get('title') or f"Тема #{t.get('topic_id')}"
                    t['default_message'] = (
                        f"Здравствуйте! Готов(а) обсудить тему «{topic_label}» в качестве научного руководителя."
                    )
        return templates.TemplateResponse(
            'admin/view_user.html',
            {
                'request': request,
                'user': user,
                'student': student,
                'supervisor': supervisor,
                'recommended_roles': recommended_roles,
                'recommended_topics': recommended_topics,
                'msg': msg,
            },
        )

    @router.get('/supervisor/{user_id}', response_class=HTMLResponse)
    def view_supervisor(request: Request, user_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT u.id, u.full_name, u.username, u.email, u.role, u.created_at,
                       sp.position, sp.degree, sp.capacity, sp.interests, sp.requirements
                FROM users u
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE u.id = %s AND u.role = 'supervisor'
                ''',
                (user_id,),
            )
            sup = cur.fetchone()
            if not sup:
                notice = urllib.parse.quote('Руководитель не найден')
                return RedirectResponse(url=f'/?tab=supervisors&msg={notice}', status_code=303)
            sup = dict(sup)
            cur.execute(
                '''
                SELECT sc.topic_id, sc.rank, sc.score,
                       t.title, t.description, t.required_skills, t.expected_outcomes, t.direction,
                       t.author_user_id, u.full_name AS author_name
                FROM supervisor_candidates sc
                JOIN topics t ON t.id = sc.topic_id
                JOIN users u ON u.id = t.author_user_id
                WHERE sc.user_id = %s
                ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
                LIMIT 10
                ''',
                (user_id,),
            )
            recommended_topics = [dict(r) for r in cur.fetchall()]
            for t in recommended_topics:
                topic_label = t.get('title') or f"Тема #{t.get('topic_id')}"
                t['default_message'] = (
                    f"Здравствуйте! Готов(а) обсудить тему «{topic_label}» в качестве научного руководителя."
                )
        return templates.TemplateResponse(
            'admin/view_supervisor.html',
            {
                'request': request,
                'sup': sup,
                'recommended_topics': recommended_topics,
                'msg': msg,
            },
        )

    @router.get('/edit-user/{user_id}', response_class=HTMLResponse)
    def edit_user(request: Request, user_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT id, full_name, email, username, role, consent_personal, consent_private FROM users WHERE id=%s',
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                notice = urllib.parse.quote('Пользователь не найден')
                return RedirectResponse(url=f'/?tab=students&msg={notice}', status_code=303)
        return templates.TemplateResponse(
            'admin/edit_user.html',
            {
                'request': request,
                'user': dict(row),
                'msg': msg,
            },
        )

    @router.post('/update-user')
    def update_user(
        request: Request,
        user_id: int = Form(...),
        full_name: str = Form(...),
        email: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
        role: str = Form('student'),
        consent_personal: Optional[str] = Form(None),
        consent_private: Optional[str] = Form(None),
    ):
        cp = str(consent_personal or '').lower() in ('1', 'true', 'on', 'yes', 'y')
        cpr = str(consent_private or '').lower() in ('1', 'true', 'on', 'yes', 'y')
        username_normalized = normalize_telegram_link(username)
        with ctx.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                '''
                UPDATE users
                SET full_name=%s, email=%s, username=%s, role=%s,
                    consent_personal=%s, consent_private=%s, updated_at=now()
                WHERE id=%s
                ''',
                (full_name.strip(), (email or None), username_normalized, role, cp, cpr, user_id),
            )
        kind = 'supervisors' if role == 'supervisor' else ('students' if role == 'student' else 'topics')
        notice = urllib.parse.quote('Пользователь обновлён')
        return RedirectResponse(url=f'/?tab={kind}&msg={notice}', status_code=303)

    @router.get('/edit-supervisor/{user_id}', response_class=HTMLResponse)
    def edit_supervisor(request: Request, user_id: int, msg: Optional[str] = None):
        with ctx.get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT u.id, u.full_name, u.email, u.username, u.role,
                       sp.position, sp.degree, sp.capacity, sp.interests, sp.requirements
                FROM users u
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE u.id = %s
                ''',
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                notice = urllib.parse.quote('Руководитель не найден')
                return RedirectResponse(url=f'/?tab=supervisors&msg={notice}', status_code=303)
        return templates.TemplateResponse(
            'admin/edit_supervisor.html',
            {
                'request': request,
                'sup': dict(row),
                'msg': msg,
            },
        )

    @router.post('/update-supervisor')
    def update_supervisor(
        request: Request,
        user_id: int = Form(...),
        full_name: str = Form(...),
        email: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
        position: Optional[str] = Form(None),
        degree: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
        interests: Optional[str] = Form(None),
        requirements: Optional[str] = Form(None),
    ):
        username_normalized = normalize_telegram_link(username)
        with ctx.get_conn() as conn, conn.cursor() as cur:
            capacity_val = parse_optional_int(capacity)
            cur.execute(
                '''
                UPDATE users
                SET full_name=%s, email=%s, username=%s, role='supervisor', updated_at=now()
                WHERE id=%s
                ''',
                (full_name.strip(), (email or None), username_normalized, user_id),
            )
            cur.execute('SELECT 1 FROM supervisor_profiles WHERE user_id=%s', (user_id,))
            if cur.fetchone():
                cur.execute(
                    '''
                    UPDATE supervisor_profiles
                    SET position=%s, degree=%s, capacity=%s, interests=%s, requirements=%s
                    WHERE user_id=%s
                    ''',
                    (position, degree, capacity_val, interests, requirements, user_id),
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''',
                    (user_id, position, degree, capacity_val, interests, requirements),
                )
        notice = urllib.parse.quote('Руководитель обновлён')
        return RedirectResponse(url=f'/?tab=supervisors&msg={notice}', status_code=303)
