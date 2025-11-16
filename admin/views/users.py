from __future__ import annotations

import urllib.parse
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..context import AdminContext
from ..embedding_queue import enqueue_refresh, commit_with_refresh
from ..utils import normalize_telegram_link, process_cv
from ..utils_common import parse_optional_int, normalize_optional_str


def register(router: APIRouter, ctx: AdminContext) -> None:
    """Подключает административные представления для управления пользователями."""
    templates = ctx.templates

    @router.get('/add-student', response_class=HTMLResponse)
    def new_student(request: Request, msg: Optional[str] = None):
        """Показывает форму создания новой учётной записи студента."""
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
        direction: Optional[str] = Form(None),
        education_program: Optional[str] = Form(None),
        status_value: Optional[str] = Form(None),
        course: Optional[str] = Form(None),
        group_number: Optional[str] = Form(None),
        phone: Optional[str] = Form(None),
        skills: Optional[str] = Form(None),
        skills_to_learn: Optional[str] = Form(None),
        interests: Optional[str] = Form(None),
        dislikes: Optional[str] = Form(None),
        commercial_experience: Optional[str] = Form(None),
        noncommercial_experience: Optional[str] = Form(None),
        portfolio: Optional[str] = Form(None),
        achievements: Optional[str] = Form(None),
        hobbies: Optional[str] = Form(None),
        cv: Optional[str] = Form(None),
        hours_per_week: Optional[str] = Form(None),
        team_role: Optional[str] = Form(None),
        plan_for_lab: Optional[str] = Form(None),
        motivation_letter: Optional[str] = Form(None),
        thematic_choice: Optional[str] = Form(None),
    ):
        """Создаёт студента и его профиль на основе данных формы."""
        full_name = (full_name or '').strip()
        if not full_name:
            notice = urllib.parse.quote('Укажите имя студента')
            return RedirectResponse(url=f'/add-student?msg={notice}', status_code=303)
        username_normalized = normalize_telegram_link(username)
        course_val = parse_optional_int(course)
        hours_val = parse_optional_int(hours_per_week)
        direction_val = normalize_optional_str(direction)
        education_program_val = normalize_optional_str(education_program)
        status_val = normalize_optional_str(status_value)
        group_val = normalize_optional_str(group_number)
        phone_val = normalize_optional_str(phone)
        skills_val = normalize_optional_str(skills)
        skills_to_learn_val = normalize_optional_str(skills_to_learn)
        interests_val = normalize_optional_str(interests)
        dislikes_val = normalize_optional_str(dislikes)
        commercial_val = normalize_optional_str(commercial_experience)
        noncommercial_val = normalize_optional_str(noncommercial_experience)
        portfolio_val = normalize_optional_str(portfolio)
        achievements_val = normalize_optional_str(achievements)
        hobbies_val = normalize_optional_str(hobbies)
        team_role_val = normalize_optional_str(team_role)
        plan_val = normalize_optional_str(plan_for_lab)
        motivation_val = normalize_optional_str(motivation_letter)
        thematic_val = normalize_optional_str(thematic_choice)

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
            cv_val = process_cv(conn, user_id, normalize_optional_str(cv))
            cur.execute(
                '''
                INSERT INTO student_profiles(
                    user_id, direction, education_program, status, course, group_number,
                    phone, skills, skills_to_learn, interests, dislikes,
                    commercial_experience, noncommercial_experience, portfolio,
                    achievements, hobbies, cv, hours_per_week, team_role,
                    plan_for_lab, motivation_letter, thematic_choice
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (user_id) DO UPDATE SET
                    direction = EXCLUDED.direction,
                    education_program = EXCLUDED.education_program,
                    status = EXCLUDED.status,
                    course = EXCLUDED.course,
                    group_number = EXCLUDED.group_number,
                    phone = EXCLUDED.phone,
                    skills = EXCLUDED.skills,
                    skills_to_learn = EXCLUDED.skills_to_learn,
                    interests = EXCLUDED.interests,
                    dislikes = EXCLUDED.dislikes,
                    commercial_experience = EXCLUDED.commercial_experience,
                    noncommercial_experience = EXCLUDED.noncommercial_experience,
                    portfolio = EXCLUDED.portfolio,
                    achievements = EXCLUDED.achievements,
                    hobbies = EXCLUDED.hobbies,
                    cv = EXCLUDED.cv,
                    hours_per_week = EXCLUDED.hours_per_week,
                    team_role = EXCLUDED.team_role,
                    plan_for_lab = EXCLUDED.plan_for_lab,
                    motivation_letter = EXCLUDED.motivation_letter,
                    thematic_choice = EXCLUDED.thematic_choice
                ''',
                (
                    user_id,
                    direction_val,
                    education_program_val,
                    status_val,
                    course_val,
                    group_val,
                    phone_val,
                    skills_val,
                    skills_to_learn_val,
                    interests_val,
                    dislikes_val,
                    commercial_val,
                    noncommercial_val,
                    portfolio_val,
                    achievements_val,
                    hobbies_val,
                    cv_val,
                    hours_val,
                    team_role_val,
                    plan_val,
                    motivation_val,
                    thematic_val,
                ),
            )
            enqueue_refresh(conn, "student", user_id)
        commit_with_refresh(conn)
        notice = urllib.parse.quote('Студент добавлен')
        return RedirectResponse(url=f'/?tab=students&msg={notice}', status_code=303)

    @router.get('/add-supervisor', response_class=HTMLResponse)
    def new_supervisor(request: Request, msg: Optional[str] = None):
        """Отображает форму добавления нового наставника."""
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
        """Сохраняет данные наставника, обновляя профиль и пользователя."""
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
            enqueue_refresh(conn, "supervisor", user_id)
        commit_with_refresh(conn)
        notice = urllib.parse.quote('Руководитель добавлен')
        return RedirectResponse(url=f'/?tab=supervisors&msg={notice}', status_code=303)

    @router.get('/user/{user_id}', response_class=HTMLResponse)
    def view_user(request: Request, user_id: int, msg: Optional[str] = None):
        """Показывает карточку пользователя с рекомендациями по подбору."""
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
        """Отображает страницу наставника с рекомендованными темами."""
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
        """Открывает форму редактирования базовых данных пользователя."""
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
        """Обновляет пользователя и запускает перерасчёт эмбеддингов при необходимости."""
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
            if role == 'student':
                enqueue_refresh(conn, "student", user_id)
            elif role == 'supervisor':
                enqueue_refresh(conn, "supervisor", user_id)
        commit_with_refresh(conn)
        kind = 'supervisors' if role == 'supervisor' else ('students' if role == 'student' else 'topics')
        notice = urllib.parse.quote('Пользователь обновлён')
        return RedirectResponse(url=f'/?tab={kind}&msg={notice}', status_code=303)

    @router.get('/edit-supervisor/{user_id}', response_class=HTMLResponse)
    def edit_supervisor(request: Request, user_id: int, msg: Optional[str] = None):
        """Предоставляет форму редактирования профиля наставника."""
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
        """Сохраняет изменения наставника и инициирует обновление эмбеддинга."""
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
            enqueue_refresh(conn, "supervisor", user_id)
        commit_with_refresh(conn)
        notice = urllib.parse.quote('Руководитель обновлён')
        return RedirectResponse(url=f'/?tab=supervisors&msg={notice}', status_code=303)
