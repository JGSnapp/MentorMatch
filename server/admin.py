import os
from typing import Optional, List, Dict, Any, Callable

import psycopg2.extras
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from parse_gform import fetch_normalized_rows


def create_admin_router(get_conn: Callable, templates) -> APIRouter:
    router = APIRouter()

    @router.get('/', response_class=HTMLResponse)
    def index(request: Request, kind: str = 'topics', offset: int = 0, msg: Optional[str] = None):
        items: List[Dict[str, Any]] = []
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            if kind == 'students':
                cur.execute(
                    '''
                    SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                           sp.program, sp.skills, sp.interests
                    FROM users u
                    LEFT JOIN student_profiles sp ON sp.user_id = u.id
                    WHERE u.role = 'student'
                    ORDER BY u.created_at DESC
                    OFFSET %s LIMIT 10
                    ''', (max(0, offset),),
                )
                items = cur.fetchall()
            elif kind == 'supervisors':
                cur.execute(
                    '''
                    SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                           sup.position, sup.degree, sup.capacity, sup.interests
                    FROM users u
                    LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
                    WHERE u.role = 'supervisor'
                    ORDER BY u.created_at DESC
                    OFFSET %s LIMIT 10
                    ''', (max(0, offset),),
                )
                items = cur.fetchall()
            else:
                cur.execute(
                    '''
                    SELECT t.id, t.title, t.seeking_role, t.created_at, u.full_name AS author
                    FROM topics t
                    JOIN users u ON u.id = t.author_user_id
                    ORDER BY t.created_at DESC
                    OFFSET %s LIMIT 10
                    ''', (max(0, offset),),
                )
                items = cur.fetchall()

        return templates.TemplateResponse(
            'index.html',
            {
                'request': request,
                'msg': msg,
                'kind': kind,
                'offset': offset,
                'items': items,
                'env_spreadsheet_id': os.getenv('SPREADSHEET_ID', ''),
            },
        )

    @router.post('/import-sheet')
    def import_sheet(request: Request, spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
        try:
            service_account_file = os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
            rows = fetch_normalized_rows(
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                service_account_file=service_account_file,
            )

            inserted_users = 0
            upserted_profiles = 0
            inserted_topics = 0

            with get_conn() as conn, conn.cursor() as cur:
                for r in rows:
                    full_name = (r.get('full_name') or '').strip()
                    email = (r.get('email') or '').strip()
                    if not (full_name or email):
                        continue

                # Find or create user
                if email:
                    cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND role='student' LIMIT 1", (email,))
                else:
                    cur.execute("SELECT id FROM users WHERE full_name=%s AND role='student' LIMIT 1", (full_name,))
                row = cur.fetchone()
                if row:
                    user_id = row[0]
                else:
                    cur.execute(
                        '''
                        INSERT INTO users(full_name, email, role, created_at, updated_at)
                        VALUES (%s, %s, 'student', now(), now())
                        RETURNING id
                        ''', (full_name, (email or None)),
                    )
                    user_id = cur.fetchone()[0]
                    inserted_users += 1

                # Update telegram username and consents if present
                updates = []
                params: List[Any] = []
                if r.get('telegram'):
                    updates.append('username=%s')
                    params.append(r['telegram'])
                if r.get('consent_personal') is not None:
                    updates.append('consent_personal=%s')
                    params.append(r['consent_personal'])
                if r.get('consent_private') is not None:
                    updates.append('consent_private=%s')
                    params.append(r['consent_private'])
                if updates:
                    params.append(user_id)
                    cur.execute(f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s", params)

                # Upsert student profile
                cur.execute('SELECT 1 FROM student_profiles WHERE user_id=%s', (user_id,))
                exists = cur.fetchone() is not None
                skills_have = ', '.join(r.get('hard_skills_have') or []) or None
                skills_want = ', '.join(r.get('hard_skills_want') or []) or None
                interests = ', '.join(r.get('interests') or []) or None
                requirements = r.get('supervisor_preference')

                if exists:
                    cur.execute(
                        '''
                        UPDATE student_profiles
                        SET program=%s, skills=%s, interests=%s, cv=%s, requirements=%s,
                            skills_to_learn=%s, achievements=%s, supervisor_pref=%s, groundwork=%s,
                            wants_team=%s, team_role=%s, team_needs=%s,
                            apply_master=%s, workplace=%s,
                            preferred_team_track=%s, dev_track=%s, science_track=%s, startup_track=%s,
                            final_work_pref=%s
                        WHERE user_id=%s
                        ''', (
                            r.get('program'),
                            skills_have,
                            interests,
                            r.get('cv'),
                            requirements,
                            skills_want,
                            r.get('achievements'),
                            r.get('supervisor_preference'),
                            r.get('groundwork'),
                            r.get('wants_team'),
                            r.get('team_role'),
                            r.get('team_needs'),
                            r.get('apply_master'),
                            r.get('workplace'),
                            r.get('preferred_team_track'),
                            r.get('dev_track'),
                            r.get('science_track'),
                            r.get('startup_track'),
                            r.get('final_work_preference'),
                            user_id,
                        ),
                    )
                else:
                    cur.execute(
                        '''
                        INSERT INTO student_profiles(
                            user_id, program, skills, interests, cv, requirements,
                            skills_to_learn, achievements, supervisor_pref, groundwork,
                            wants_team, team_role, team_needs, apply_master, workplace,
                            preferred_team_track, dev_track, science_track, startup_track, final_work_pref
                        )
                        VALUES (%s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s)
                        ''', (
                            user_id,
                            r.get('program'),
                            skills_have,
                            interests,
                            r.get('cv'),
                            requirements,
                            skills_want,
                            r.get('achievements'),
                            r.get('supervisor_preference'),
                            r.get('groundwork'),
                            r.get('wants_team'),
                            r.get('team_role'),
                            r.get('team_needs'),
                            r.get('apply_master'),
                            r.get('workplace'),
                            r.get('preferred_team_track'),
                            r.get('dev_track'),
                            r.get('science_track'),
                            r.get('startup_track'),
                            r.get('final_work_preference'),
                        ),
                    )
                upserted_profiles += 1

                # Create student's own topic if provided
                topic = r.get('topic')
                if r.get('has_own_topic') and topic and (topic.get('title') or '').strip():
                    title = topic.get('title').strip()
                    cur.execute('SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s', (user_id, title))
                    if not cur.fetchone():
                        desc = topic.get('description') or ''
                        groundwork = r.get('groundwork')
                        if groundwork:
                            desc = (desc or '').strip()
                            tail = f"\n\nИмеющийся задел: {groundwork}".strip()
                            desc = f"{desc}\n{tail}" if desc else tail
                        cur.execute(
                            '''
                            INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                               required_skills, seeking_role, is_active, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, 'supervisor', TRUE, now(), now())
                            ''', (
                                user_id,
                                title,
                                desc,
                                topic.get('expected_outcomes'),
                                skills_have,
                            ),
                        )
                        inserted_topics += 1

            return RedirectResponse(url=f'/?msg=Импорт: users+{inserted_users}, profiles~{upserted_profiles}, topics+{inserted_topics}&kind=topics', status_code=303)
        except Exception as e:
            msg = f"Ошибка импорта: {type(e).__name__}: {e}" if str(e) else f"Ошибка импорта: {type(e).__name__}"
            return RedirectResponse(url=f'/?msg={msg}&kind=topics', status_code=303)

    @router.post('/add-supervisor')
    def add_supervisor(request: Request,
        full_name: str = Form(...),
        email: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
        position: Optional[str] = Form(None),
        degree: Optional[str] = Form(None),
        capacity: Optional[int] = Form(None),
        requirements: Optional[str] = Form(None),
        interests: Optional[str] = Form(None),
    ):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT id FROM users WHERE full_name=%s AND role=\'supervisor\' LIMIT 1', (full_name,))
            row = cur.fetchone()
            if row:
                user_id = row[0]
            else:
                cur.execute(
                    '''
                    INSERT INTO users(full_name, email, username, role, created_at, updated_at)
                    VALUES (%s, %s, %s, 'supervisor', now(), now())
                    RETURNING id
                    ''', (full_name, email, username),
                )
                user_id = cur.fetchone()[0]

            cur.execute('SELECT 1 FROM supervisor_profiles WHERE user_id=%s', (user_id,))
            if cur.fetchone():
                cur.execute(
                    '''
                    UPDATE supervisor_profiles
                    SET position=%s, degree=%s, capacity=%s, requirements=%s, interests=%s
                    WHERE user_id=%s
                    ''', (position, degree, capacity, requirements, interests, user_id),
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, requirements, interests)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (user_id, position, degree, capacity, requirements, interests),
                )

        return RedirectResponse(url='/?msg=Руководитель добавлен&kind=supervisors', status_code=303)

    @router.post('/add-topic')
    def add_topic(request: Request,
        title: str = Form(...),
        author_user_id: Optional[int] = Form(None),
        author_full_name: Optional[str] = Form(None),
        description: Optional[str] = Form(None),
        expected_outcomes: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        seeking_role: str = Form('student'),
    ):
        with get_conn() as conn, conn.cursor() as cur:
            uid: Optional[int] = None
            if author_user_id:
                uid = int(author_user_id)
            elif author_full_name:
                cur.execute('SELECT id FROM users WHERE full_name=%s LIMIT 1', (author_full_name.strip(),))
                row = cur.fetchone()
                if row:
                    uid = row[0]
                else:
                    cur.execute("INSERT INTO users(full_name, role, created_at, updated_at) VALUES (%s, 'supervisor', now(), now()) RETURNING id", (author_full_name.strip(),))
                    uid = cur.fetchone()[0]
            else:
                cur.execute("INSERT INTO users(full_name, role, created_at, updated_at) VALUES (%s, 'supervisor', now(), now()) RETURNING id", ('Unknown Supervisor',))
                uid = cur.fetchone()[0]

            cur.execute('SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s', (uid, title.strip()))
            if not cur.fetchone():
                cur.execute(
                    '''
                    INSERT INTO topics(author_user_id, title, description, expected_outcomes, required_skills,
                                       seeking_role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, now(), now())
                    ''', (uid, title.strip(), description, expected_outcomes, required_skills, seeking_role),
                )

        return RedirectResponse(url='/?msg=Тема добавлена&kind=topics', status_code=303)

    return router
