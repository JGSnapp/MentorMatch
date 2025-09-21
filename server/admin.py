import os
import urllib.parse
from typing import Optional, List, Dict, Any, Callable

import psycopg2.extras
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from parse_gform import fetch_normalized_rows
from media_store import persist_media_from_url
from utils import parse_optional_int

def _normalize_telegram_link(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = (raw or '').strip()
    # Remove leading '@'
    if s.startswith('@'):
        s = s[1:]
    # If already full link
    if s.lower().startswith(('http://t.me/', 'https://t.me/', 'http://telegram.me/', 'https://telegram.me/')):
        return s
    # Bare username -> full link
    # Extract username part from possible partial links
    import re
    m = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", s)
    if m:
        return f"https://t.me/{m.group(1)}"
    s = re.sub(r"[^A-Za-z0-9_]", "", s)
    return f"https://t.me/{s}" if s else None


def _is_http_url(s: Optional[str]) -> bool:
    return bool(s) and str(s).strip().lower().startswith(('http://', 'https://'))


def _process_cv(conn, user_id: int, cv_val: Optional[str]) -> Optional[str]:
    val = (cv_val or '').strip()
    if not val:
        return None
    # Already our media link
    if val.startswith('/media/'):
        return val
    # External link -> download and persist
    if _is_http_url(val):
        try:
            _mid, public = persist_media_from_url(conn, user_id, val, category='cv')
            return public
        except Exception as e:
            print(f'CV download failed for user {user_id}: {e}')
            return cv_val
    return cv_val


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
                    SELECT t.id, t.title, t.seeking_role, t.direction, t.created_at, u.full_name AS author
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
                'env_pairs_spreadsheet_id': os.getenv('PAIRS_SPREADSHEET_ID', ''),
            },
        )

    # --- Profiles (HTML views) ---
    @router.get('/user/{user_id}')
    def view_user(request: Request, user_id: int, msg: Optional[str] = None):
        import psycopg2.extras
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            user = cur.fetchone()
            if not user:
                return RedirectResponse(url='/?msg=Пользователь не найден', status_code=303)
            user = dict(user)
            student = None
            supervisor = None
            recommended_roles: List[Dict[str, Any]] = []
            recommended_topics: List[Dict[str, Any]] = []
            if user.get('role') == 'student':
                cur.execute("SELECT * FROM student_profiles WHERE user_id=%s", (user_id,))
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
                cur.execute("SELECT * FROM supervisor_profiles WHERE user_id=%s", (user_id,))
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
        return templates.TemplateResponse('view_user.html', {
            'request': request,
            'user': user,
            'student': student,
            'supervisor': supervisor,
            'recommended_roles': recommended_roles,
            'recommended_topics': recommended_topics,
            'msg': msg,
        })

    @router.get('/supervisor/{user_id}')
    def view_supervisor(request: Request, user_id: int, msg: Optional[str] = None):
        import psycopg2.extras
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                       sp.position, sp.degree, sp.capacity, sp.interests, sp.requirements
                FROM users u
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE u.id = %s AND u.role = 'supervisor'
                ''', (user_id,),
            )
            sup = cur.fetchone()
            if not sup:
                return RedirectResponse(url='/?msg=Руководитель не найден&kind=supervisors', status_code=303)
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
        return templates.TemplateResponse('view_supervisor.html', {
            'request': request,
            'sup': sup,
            'recommended_topics': recommended_topics,
            'msg': msg,
        })

    @router.get('/topic/{topic_id}')
    def view_topic(request: Request, topic_id: int, msg: Optional[str] = None):
        import psycopg2.extras
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT t.*, u.full_name AS author
                FROM topics t
                JOIN users u ON u.id = t.author_user_id
                WHERE t.id = %s
                ''', (topic_id,),
            )
            topic = cur.fetchone()
            if not topic:
                return RedirectResponse(url='/?msg=Тема не найдена&kind=topics', status_code=303)
            topic = dict(topic)
            cur.execute(
                "SELECT * FROM roles WHERE topic_id=%s ORDER BY created_at DESC, id DESC",
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
        return templates.TemplateResponse('view_topic.html', {
            'request': request,
            'topic': topic,
            'roles': roles,
            'supervisor_candidates': supervisor_candidates,
            'msg': msg,
        })

    @router.get('/role/{role_id}')
    def view_role(request: Request, role_id: int, msg: Optional[str] = None):
        import psycopg2.extras
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT r.*, t.title AS topic_title, t.author_user_id, u.full_name AS author
                FROM roles r
                JOIN topics t ON t.id = r.topic_id
                JOIN users u ON u.id = t.author_user_id
                WHERE r.id = %s
                ''', (role_id,),
            )
            role = cur.fetchone()
            if not role:
                return RedirectResponse(url='/?msg=Роль не найдена&kind=topics', status_code=303)
            role = dict(role)
            cur.execute(
                '''
                SELECT rc.rank, rc.score, u.id AS user_id, u.full_name, u.username
                FROM role_candidates rc
                JOIN users u ON u.id = rc.user_id
                WHERE rc.role_id = %s
                ORDER BY rc.rank ASC NULLS LAST, rc.score DESC NULLS LAST, u.created_at DESC
                LIMIT 10
                ''', (role_id,),
            )
            candidates = [dict(r) for r in cur.fetchall()]
            role_title = role.get('name') or f"Роль #{role_id}"
            topic_title = role.get('topic_title') or f"Тема #{role.get('topic_id')}"
            for cand in candidates:
                name = cand.get('full_name')
                greeting = f"Здравствуйте{', ' + name if name else ''}!"
                cand['default_message'] = (
                    f"{greeting} Приглашаю присоединиться к роли «{role_title}» по теме «{topic_title}»."
                )
        return templates.TemplateResponse('view_role.html', {
            'request': request,
            'role': role,
            'candidates': candidates,
            'msg': msg,
        })

    # --- Action wrappers (redirect back instead of raw JSON) ---
    @router.post('/add-role')
    def add_role(
        topic_id: int = Form(...),
        name: str = Form(...),
        description: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
    ):
        try:
            capacity_val: Optional[int]
            if capacity is None:
                capacity_val = None
            else:
                capacity_str = str(capacity).strip()
                if not capacity_str:
                    capacity_val = None
                else:
                    try:
                        capacity_val = int(capacity_str)
                    except ValueError:
                        return RedirectResponse(
                            url=f'/topic/{topic_id}?msg=Некорректная вместимость',
                            status_code=303,
                        )
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    '''
                    INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now(), now())
                    ''',
                    (
                        topic_id,
                        name.strip(),
                        (description or None),
                        (required_skills or None),
                        capacity_val,
                    ),
                )
                conn.commit()
            return RedirectResponse(url=f'/topic/{topic_id}', status_code=303)
        except Exception as e:
            return RedirectResponse(url=f'/topic/{topic_id}?msg=Ошибка добавления роли: {type(e).__name__}', status_code=303)

    @router.post('/do-match-role')
    def do_match_role(role_id: int = Form(...)):
        try:
            from matching import handle_match_role
            with get_conn() as conn:
                handle_match_role(conn, role_id=role_id)
            return RedirectResponse(url=f'/role/{role_id}', status_code=303)
        except Exception as e:
            return RedirectResponse(url=f'/role/{role_id}?msg=Ошибка подбора: {type(e).__name__}', status_code=303)

    @router.post('/do-match-topic')
    def do_match_topic(topic_id: int = Form(...), target_role: Optional[str] = Form(None)):
        try:
            from matching import handle_match
            with get_conn() as conn:
                handle_match(conn, topic_id=topic_id, target_role=target_role)
            return RedirectResponse(url=f'/topic/{topic_id}', status_code=303)
        except Exception as e:
            return RedirectResponse(url=f'/topic/{topic_id}?msg=Ошибка подбора: {type(e).__name__}', status_code=303)

    @router.post('/do-match-student')
    def do_match_student(student_user_id: int = Form(...)):
        try:
            from matching import handle_match_student
            with get_conn() as conn:
                handle_match_student(conn, student_user_id=student_user_id)
            return RedirectResponse(url=f'/user/{student_user_id}', status_code=303)
        except Exception as e:
            return RedirectResponse(url=f'/user/{student_user_id}?msg=Ошибка подбора: {type(e).__name__}', status_code=303)

    @router.post('/do-match-supervisor')
    def do_match_supervisor(supervisor_user_id: int = Form(...)):
        try:
            from matching import handle_match_supervisor_user
            with get_conn() as conn:
                handle_match_supervisor_user(conn, supervisor_user_id=supervisor_user_id)
            return RedirectResponse(url=f'/supervisor/{supervisor_user_id}', status_code=303)
        except Exception as e:
            return RedirectResponse(url=f'/supervisor/{supervisor_user_id}?msg=Ошибка подбора: {type(e).__name__}', status_code=303)

    @router.post('/send-request')
    def send_request(
        sender_user_id: int = Form(...),
        receiver_user_id: int = Form(...),
        topic_id: int = Form(...),
        body: str = Form(...),
        role_id: Optional[str] = Form(None),
        return_url: str = Form('/'),
    ):
        def _redirect(target: Optional[str], message: str) -> RedirectResponse:
            base = (target or '/').strip() or '/'
            anchor = ''
            if '#' in base:
                base, anchor = base.split('#', 1)
                anchor = f'#{anchor}'
            sep = '&' if '?' in base else '?'
            quoted = urllib.parse.quote(message)
            return RedirectResponse(url=f'{base}{sep}msg={quoted}{anchor}', status_code=303)

        text = (body or '').strip()
        if not text:
            return _redirect(return_url, 'Текст заявки не может быть пустым')
        try:
            with get_conn() as conn, conn.cursor() as cur:
                role_id_val = parse_optional_int(role_id)
                topic_id_int = int(topic_id)
                cur.execute('SELECT role FROM users WHERE id=%s', (sender_user_id,))
                sender_row = cur.fetchone()
                sender_role = (sender_row[0] or '').strip().lower() if sender_row else None
                if not sender_role:
                    return _redirect(return_url, 'Не удалось определить роль отправителя заявки')
                if sender_role == 'student' and role_id_val is None:
                    return _redirect(return_url, 'Студент должен выбрать конкретную роль для заявки')
                if role_id_val is not None:
                    cur.execute('SELECT 1 FROM roles WHERE id=%s AND topic_id=%s', (role_id_val, topic_id_int))
                    if not cur.fetchone():
                        return _redirect(return_url, 'Роль не принадлежит выбранной теме')
                cur.execute(
                    '''
                    INSERT INTO messages(sender_user_id, receiver_user_id, topic_id, role_id, body, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, 'pending', now())
                    RETURNING id
                    ''',
                    (sender_user_id, receiver_user_id, topic_id_int, role_id_val, text),
                )
                msg_id = cur.fetchone()[0]
                conn.commit()
            return _redirect(return_url, f'Заявка отправлена (#{msg_id})')
        except Exception as e:
            return _redirect(return_url, f'Ошибка отправки заявки: {type(e).__name__}')

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
                    tg = _normalize_telegram_link(r.get('telegram'))
                    if tg:
                        updates.append('username=%s')
                        params.append(tg)
                if r.get('consent_personal') is not None:
                    updates.append('consent_personal=%s')
                    params.append(r['consent_personal'])
                if r.get('consent_private') is not None:
                    updates.append('consent_private=%s')
                    params.append(r['consent_private'])
                if updates:
                    params.append(user_id)
                    cur.execute(
                        f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s",
                        tuple(params),
                    )

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
                            wants_team=%s, team_role=%s, team_has=%s, team_needs=%s,
                            apply_master=%s, workplace=%s,
                            preferred_team_track=%s, dev_track=%s, science_track=%s, startup_track=%s,
                            final_work_pref=%s
                        WHERE user_id=%s
                        ''', (
                            r.get('program'),
                            skills_have,
                            interests, _process_cv(conn, user_id, r.get('cv')),
                            requirements,
                            skills_want,
                            r.get('achievements'),
                            r.get('supervisor_preference'),
                            r.get('groundwork'),
                            r.get('wants_team'),
                            r.get('team_role'),
                            r.get('team_has'),
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
                            wants_team, team_role, team_has, team_needs, apply_master, workplace,
                            preferred_team_track, dev_track, science_track, startup_track, final_work_pref
                        )
                        VALUES (%s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s)
                        ''', (
                            user_id,
                            r.get('program'),
                            skills_have,
                            interests, _process_cv(conn, user_id, r.get('cv')),
                            requirements,
                            skills_want,
                            r.get('achievements'),
                            r.get('supervisor_preference'),
                            r.get('groundwork'),
                            r.get('wants_team'),
                            r.get('team_role'),
                            r.get('team_has'),
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
                        practical = (topic.get('practical_importance') or None)
                        if practical:
                            desc = (desc or '').strip()
                            tail2 = f"\n\nПрактическая значимость: {practical}".strip()
                            desc = f"{desc}\n{tail2}" if desc else tail2
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

    @router.post('/export-pairs')
    def export_pairs(request: Request, pairs_spreadsheet_id: Optional[str] = Form(None)):
        from sheet_pairs import export_pairs_from_db
        sid = pairs_spreadsheet_id or os.getenv('PAIRS_SPREADSHEET_ID')
        if not sid:
            return RedirectResponse(url='/?msg=Не указан PAIRS_SPREADSHEET_ID&kind=topics', status_code=303)
        service_account_file = os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
        try:
            with get_conn() as conn:
                n = export_pairs_from_db(conn, sid, service_account_file)
            return RedirectResponse(url=f'/?msg=Экспортированo строк: {n}&kind=topics', status_code=303)
        except Exception as e:
            return RedirectResponse(url=f'/?msg=Ошибка экспорта: {type(e).__name__}: {e}&kind=topics', status_code=303)

    @router.post('/add-supervisor')
    def add_supervisor(request: Request,
        full_name: str = Form(...),
        email: Optional[str] = Form(None),
        username: Optional[str] = Form(None),
        position: Optional[str] = Form(None),
        degree: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
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

            capacity_val = parse_optional_int(capacity)
            cur.execute('SELECT 1 FROM supervisor_profiles WHERE user_id=%s', (user_id,))
            if cur.fetchone():
                cur.execute(
                    '''
                    UPDATE supervisor_profiles
                    SET position=%s, degree=%s, capacity=%s, requirements=%s, interests=%s
                    WHERE user_id=%s
                    ''', (position, degree, capacity_val, requirements, interests, user_id),
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, requirements, interests)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (user_id, position, degree, capacity_val, requirements, interests),
                )

        return RedirectResponse(url='/?msg=Руководитель добавлен&kind=supervisors', status_code=303)

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
        with get_conn() as conn, conn.cursor() as cur:
            uid: Optional[int] = None
            author_uid = parse_optional_int(author_user_id)
            if author_uid is not None:
                uid = author_uid
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
                    ''', (
                        uid,
                        title.strip(),
                        description,
                        expected_outcomes,
                        required_skills,
                        direction_val,
                        seeking_role,
                    ),
                )

        return RedirectResponse(url='/?msg=Тема добавлена&kind=topics', status_code=303)

    # =============================
    # Edit forms and updates
    # =============================

    @router.get('/edit-user/{user_id}', response_class=HTMLResponse)
    def edit_user(request: Request, user_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, full_name, email, username, role, consent_personal, consent_private FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            if not row:
                return RedirectResponse(url='/?msg=Пользователь не найден', status_code=303)
        return templates.TemplateResponse('edit_user.html', {'request': request, 'user': dict(row)})

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
        cp = str(consent_personal or '').lower() in ('1','true','on','yes','y')
        cpr = str(consent_private or '').lower() in ('1','true','on','yes','y')
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET full_name=%s, email=%s, username=%s, role=%s,
                    consent_personal=%s, consent_private=%s, updated_at=now()
                WHERE id=%s
                """,
                (full_name.strip(), (email or None), (username or None), role, cp, cpr, user_id),
            )
        kind = 'supervisors' if role == 'supervisor' else ('students' if role == 'student' else 'topics')
        return RedirectResponse(url=f'/?msg=Пользователь обновлён&id={user_id}&kind={kind}', status_code=303)

    @router.get('/edit-supervisor/{user_id}', response_class=HTMLResponse)
    def edit_supervisor(request: Request, user_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT u.id, u.full_name, u.email, u.username, u.role,
                       sp.position, sp.degree, sp.capacity, sp.interests, sp.requirements
                FROM users u
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE u.id = %s
                ''', (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return RedirectResponse(url='/?msg=Руководитель не найден&kind=supervisors', status_code=303)
        return templates.TemplateResponse('edit_supervisor.html', {'request': request, 'sup': dict(row)})

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
        with get_conn() as conn, conn.cursor() as cur:
            # Update user basic fields
            capacity_val = parse_optional_int(capacity)
            cur.execute(
                "UPDATE users SET full_name=%s, email=%s, username=%s, role='supervisor', updated_at=now() WHERE id=%s",
                (full_name.strip(), (email or None), (username or None), user_id),
            )
            # Upsert supervisor profile
            cur.execute('SELECT 1 FROM supervisor_profiles WHERE user_id=%s', (user_id,))
            if cur.fetchone():
                cur.execute(
                    '''
                    UPDATE supervisor_profiles
                    SET position=%s, degree=%s, capacity=%s, interests=%s, requirements=%s
                    WHERE user_id=%s
                    ''', (position, degree, capacity_val, interests, requirements, user_id),
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (user_id, position, degree, capacity_val, interests, requirements),
                )
        return RedirectResponse(url='/?msg=Руководитель обновлён&kind=supervisors', status_code=303)

    @router.get('/edit-topic/{topic_id}', response_class=HTMLResponse)
    def edit_topic(request: Request, topic_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT t.*, u.full_name AS author
                FROM topics t
                JOIN users u ON u.id = t.author_user_id
                WHERE t.id = %s
                ''', (topic_id,)
            )
            row = cur.fetchone()
            if not row:
                return RedirectResponse(url='/?msg=Тема не найдена&kind=topics', status_code=303)
        return templates.TemplateResponse('edit_topic.html', {'request': request, 'topic': dict(row)})

    @router.post('/update-topic')
    def update_topic(
        request: Request,
        topic_id: int = Form(...),
        title: str = Form(...),
        description: Optional[str] = Form(None),
        expected_outcomes: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        direction: Optional[str] = Form(None),
        seeking_role: str = Form('student'),
        is_active: Optional[str] = Form(None),
    ):
        active = str(is_active or '').lower() in ('1','true','on','yes','y')
        with get_conn() as conn, conn.cursor() as cur:
            direction_val = parse_optional_int(direction)
            cur.execute(
                '''
                UPDATE topics
                SET title=%s, description=%s, expected_outcomes=%s, required_skills=%s,
                    direction=%s, seeking_role=%s, is_active=%s, updated_at=now()
                WHERE id=%s
                ''', (
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
        return RedirectResponse(url='/?msg=Тема обновлена&kind=topics', status_code=303)

    # =============================
    # View profile pages with "Изменить параметры" button
    # =============================

    @router.get('/user/{user_id}', response_class=HTMLResponse)
    def view_user(request: Request, user_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, full_name, email, username, role, created_at FROM users WHERE id=%s", (user_id,))
            user = cur.fetchone()
            if not user:
                return RedirectResponse(url='/?msg=Пользователь не найден', status_code=303)
            cur.execute("SELECT * FROM student_profiles WHERE user_id=%s", (user_id,))
            student = cur.fetchone()
            cur.execute("SELECT * FROM supervisor_profiles WHERE user_id=%s", (user_id,))
            supervisor = cur.fetchone()
        return templates.TemplateResponse('view_user.html', {
            'request': request,
            'user': dict(user),
            'student': dict(student) if student else None,
            'supervisor': dict(supervisor) if supervisor else None,
        })

    @router.get('/supervisor/{user_id}', response_class=HTMLResponse)
    def view_supervisor(request: Request, user_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT u.id, u.full_name, u.email, u.username, u.role, u.created_at,
                       sp.position, sp.degree, sp.capacity, sp.interests, sp.requirements
                FROM users u
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE u.id = %s
                ''', (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return RedirectResponse(url='/?msg=Руководитель не найден&kind=supervisors', status_code=303)
        return templates.TemplateResponse('view_supervisor.html', {'request': request, 'sup': dict(row)})

    @router.get('/topic/{topic_id}', response_class=HTMLResponse)
    def view_topic(request: Request, topic_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT t.*, u.full_name AS author
                FROM topics t
                JOIN users u ON u.id = t.author_user_id
                WHERE t.id = %s
                ''', (topic_id,)
            )
            row = cur.fetchone()
            if not row:
                return RedirectResponse(url='/?msg=Тема не найдена&kind=topics', status_code=303)
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur2:
            cur2.execute('SELECT * FROM roles WHERE topic_id=%s ORDER BY created_at DESC', (topic_id,))
            roles = cur2.fetchall()
        return templates.TemplateResponse('view_topic.html', {'request': request, 'topic': dict(row), 'roles': [dict(r) for r in roles]})

    @router.post('/add-role')
    def add_role(request: Request,
        topic_id: int = Form(...),
        name: str = Form(...),
        description: Optional[str] = Form(None),
        required_skills: Optional[str] = Form(None),
        capacity: Optional[str] = Form(None),
    ):
        with get_conn() as conn, conn.cursor() as cur:
            capacity_val = parse_optional_int(capacity)
            cur.execute(
                '''
                INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, now(), now())
                ''', (topic_id, name.strip(), (description or None), (required_skills or None), capacity_val),
            )
        # Best-effort export to pairs sheet
        try:
            from sheet_pairs import export_pairs_from_db
            sid = os.getenv('PAIRS_SPREADSHEET_ID')
            if sid:
                with get_conn() as c2:
                    export_pairs_from_db(c2, sid, os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json'))
        except Exception:
            pass
        return RedirectResponse(url=f'/topic/{topic_id}', status_code=303)

    # Removed duplicate '/match-role' HTML redirect to avoid conflict with JSON API in main.py

    @router.get('/role/{role_id}', response_class=HTMLResponse)
    def view_role(request: Request, role_id: int):
        with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT r.*, t.title AS topic_title, t.author_user_id, u.full_name AS author
                FROM roles r
                JOIN topics t ON t.id = r.topic_id
                JOIN users u ON u.id = t.author_user_id
                WHERE r.id = %s
                ''', (role_id,)
            )
            role = cur.fetchone()
            if not role:
                return RedirectResponse(url='/?msg=Роль не найдена&kind=topics', status_code=303)
            cur.execute(
                '''
                SELECT rc.user_id, u.full_name, u.username, rc.score, rc.rank
                FROM role_candidates rc
                JOIN users u ON u.id = rc.user_id
                WHERE rc.role_id = %s
                ORDER BY rc.rank ASC NULLS LAST, rc.score DESC NULLS LAST
                LIMIT 10
                ''', (role_id,)
            )
            cands = cur.fetchall()
        return templates.TemplateResponse('view_role.html', {
            'request': request,
            'role': dict(role),
            'candidates': [dict(r) for r in cands],
        })

    return router
