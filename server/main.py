import os
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, Form, Query
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from parse_gform import fetch_normalized_rows
from matching import handle_match, handle_match_student
from admin import create_admin_router


load_dotenv()


def build_db_dsn() -> str:
    dsn = os.getenv('DATABASE_URL')
    if dsn:
        return dsn
    user = os.getenv('POSTGRES_USER', 'mentormatch')
    password = os.getenv('POSTGRES_PASSWORD', 'secret')
    host = os.getenv('POSTGRES_HOST', 'localhost')
    port = os.getenv('POSTGRES_PORT', '5432')
    db = os.getenv('POSTGRES_DB', 'mentormatch')
    return f'postgresql://{user}:{password}@{host}:{port}/{db}'


def get_conn():
    return psycopg2.connect(build_db_dsn())


app = FastAPI(title='MentorMatch Admin MVP')
templates = Jinja2Templates(directory=str((Path(__file__).parent.parent / 'templates').resolve()))
app.include_router(create_admin_router(get_conn, templates))


def _truthy(val: Optional[str]) -> bool:
    return str(val or '').strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _read_csv_rows(p: Path) -> List[Dict[str, str]]:
    import csv
    if not p.exists():
        return []
    with p.open('r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return [ {k: (v or '').strip() for k,v in row.items()} for row in reader ]


def _maybe_test_import():
    if not _truthy(os.getenv('TEST_IMPORT')):
        return
    base = Path(__file__).parent.parent / 'templates'
    sup_csv = base / 'test_supervisors.csv'
    top_csv = base / 'test_topics.csv'
    sup_rows = _read_csv_rows(sup_csv)
    top_rows = _read_csv_rows(top_csv)
    if not (sup_rows or top_rows):
        return
    try:
        with get_conn() as conn, conn.cursor() as cur:
            # Supervisors
            for r in sup_rows:
                full_name = (r.get('full_name') or '').strip()
                email = (r.get('email') or '').strip() or None
                username = (r.get('username') or '').strip() or None
                if not full_name:
                    continue
                if email:
                    cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND role='supervisor' LIMIT 1", (email,))
                else:
                    cur.execute("SELECT id FROM users WHERE full_name=%s AND role='supervisor' LIMIT 1", (full_name,))
                row = cur.fetchone()
                if row:
                    user_id = row[0]
                else:
                    cur.execute(
                        """
                        INSERT INTO users(full_name, email, username, role, created_at, updated_at)
                        VALUES (%s, %s, %s, 'supervisor', now(), now())
                        RETURNING id
                        """, (full_name, email, username),
                    )
                    user_id = cur.fetchone()[0]
                # upsert supervisor profile
                cur.execute('SELECT 1 FROM supervisor_profiles WHERE user_id=%s', (user_id,))
                if cur.fetchone():
                    cur.execute(
                        """
                        UPDATE supervisor_profiles
                        SET position=%s, degree=%s, capacity=%s, interests=%s, requirements=%s
                        WHERE user_id=%s
                        """,
                        (
                            (r.get('position') or None),
                            (r.get('degree') or None),
                            int(r.get('capacity') or 0) or None,
                            (r.get('interests') or None),
                            (r.get('requirements') or None),
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
                            (r.get('position') or None),
                            (r.get('degree') or None),
                            int(r.get('capacity') or 0) or None,
                            (r.get('interests') or None),
                            (r.get('requirements') or None),
                        ),
                    )
            # Topics
            for r in top_rows:
                title = (r.get('title') or '').strip()
                if not title:
                    continue
                author_full_name = (r.get('author_full_name') or '').strip() or 'Unknown Supervisor'
                # ensure author exists (as supervisor)
                cur.execute("SELECT id FROM users WHERE full_name=%s AND role='supervisor' LIMIT 1", (author_full_name,))
                row = cur.fetchone()
                if row:
                    author_id = row[0]
                else:
                    cur.execute(
                        "INSERT INTO users(full_name, role, created_at, updated_at) VALUES (%s,'supervisor', now(), now()) RETURNING id",
                        (author_full_name,),
                    )
                    author_id = cur.fetchone()[0]
                # check topic exists
                cur.execute('SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s', (author_id, title))
                if cur.fetchone():
                    continue
                cur.execute(
                    """
                    INSERT INTO topics(author_user_id, title, description, expected_outcomes, required_skills,
                                       seeking_role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, now(), now())
                    """,
                    (
                        author_id,
                        title,
                        (r.get('description') or None),
                        (r.get('expected_outcomes') or None),
                        (r.get('required_skills') or None),
                        (r.get('seeking_role') or 'student'),
                    ),
                )
    except Exception as e:
        print(f"TEST_IMPORT failed: {e}")


@app.on_event('startup')
async def _startup_event():
    _maybe_test_import()


@app.get('/api/topics', response_class=JSONResponse)
def api_get_topics(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT t.id, t.title, t.description, t.seeking_role, t.created_at,
                   u.full_name AS author, t.expected_outcomes, t.required_skills
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.is_active = TRUE
            ORDER BY t.created_at DESC
            OFFSET %s LIMIT %s
            ''', (offset, limit),
        )
        topics = cur.fetchall()
        return [dict(topic) for topic in topics]


@app.get('/api/topics/{topic_id}', response_class=JSONResponse)
def api_get_topic(topic_id: int):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT t.id, t.title, t.description, t.seeking_role, t.created_at,
                   u.full_name AS author, t.expected_outcomes, t.required_skills
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.id = %s AND t.is_active = TRUE
            ''', (topic_id,),
        )
        topic = cur.fetchone()
        if not topic:
            return JSONResponse({'error': 'Not found'}, status_code=404)
        return dict(topic)


@app.get('/api/supervisors', response_class=JSONResponse)
def api_get_supervisors(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sup.position, sup.degree, sup.capacity, sup.interests, sup.requirements
            FROM users u
            LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
            WHERE u.role = 'supervisor'
            ORDER BY u.created_at DESC
            OFFSET %s LIMIT %s
            ''', (offset, limit),
        )
        supervisors = cur.fetchall()
        return [dict(supervisor) for supervisor in supervisors]


@app.get('/api/supervisors/{supervisor_id}', response_class=JSONResponse)
def api_get_supervisor(supervisor_id: int):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sup.position, sup.degree, sup.capacity, sup.interests, sup.requirements
            FROM users u
            LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
            WHERE u.role = 'supervisor' AND u.id = %s
            ''', (supervisor_id,),
        )
        row = cur.fetchone()
        if not row:
            return JSONResponse({'error': 'Not found'}, status_code=404)
        return dict(row)


@app.get('/api/students', response_class=JSONResponse)
def api_get_students(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sp.program, sp.skills, sp.interests, sp.cv
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.role = 'student'
            ORDER BY u.created_at DESC
            OFFSET %s LIMIT %s
            ''', (offset, limit),
        )
        students = cur.fetchall()
        return [dict(student) for student in students]


@app.get('/api/students/{student_id}', response_class=JSONResponse)
def api_get_student(student_id: int):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sp.program, sp.skills, sp.interests, sp.cv
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.role = 'student' AND u.id = %s
            ''', (student_id,),
        )
        row = cur.fetchone()
        if not row:
            return JSONResponse({'error': 'Not found'}, status_code=404)
        return dict(row)


@app.get('/api/sheets-status', response_class=JSONResponse)
def api_get_sheets_status():
    spreadsheet_id = os.getenv('SPREADSHEET_ID')
    service_account_file = os.getenv('SERVICE_ACCOUNT_FILE')
    if spreadsheet_id and service_account_file:
        return {
            'status': 'configured',
            'spreadsheet_id': spreadsheet_id[:20] + '...' if len(spreadsheet_id) > 20 else spreadsheet_id,
            'service_account_file': service_account_file,
        }
    missing_vars = []
    if not spreadsheet_id:
        missing_vars.append('SPREADSHEET_ID')
    if not service_account_file:
        missing_vars.append('SERVICE_ACCOUNT_FILE')
    return {'status': 'not_configured', 'missing_vars': missing_vars}


@app.get('/api/sheets-config', response_class=JSONResponse)
def api_get_sheets_config():
    spreadsheet_id = os.getenv('SPREADSHEET_ID')
    service_account_file = os.getenv('SERVICE_ACCOUNT_FILE')
    if spreadsheet_id and service_account_file:
        return {'status': 'configured', 'spreadsheet_id': spreadsheet_id, 'service_account_file': service_account_file}
    return {'status': 'not_configured', 'error': 'Missing env vars'}


@app.post('/api/import-sheet', response_class=JSONResponse)
def api_import_sheet(spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
    try:
        service_account_file = os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
        rows = fetch_normalized_rows(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            service_account_file=service_account_file,
        )

        inserted_users = 0
        inserted_profiles = 0
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
                inserted_profiles += 1

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

        return {
            'status': 'success',
            'message': f'Импорт: users+{inserted_users}, profiles~{inserted_profiles}, topics+{inserted_topics}',
            'stats': {
                'inserted_users': inserted_users,
                'inserted_profiles': inserted_profiles,
                'inserted_topics': inserted_topics,
                'total_rows_in_sheet': len(rows) if rows else 0,
            }
        }
    except Exception as e:
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        return {'status': 'error', 'message': err}


@app.get('/latest', response_class=JSONResponse)
def latest(kind: str = Query('topics', enum=['students', 'supervisors', 'topics']), offset: int = 0):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
        rows = cur.fetchall()

        serializable_rows = []
        for row in rows:
            row_dict = dict(row)
            if 'created_at' in row_dict and row_dict['created_at']:
                row_dict['created_at'] = row_dict['created_at'].isoformat()
            serializable_rows.append(row_dict)

    return JSONResponse(serializable_rows)


@app.post('/match-topic', response_class=JSONResponse)
def match_topic(topic_id: int = Form(...), target_role: str = Form('student')):
    with get_conn() as conn:
        result = handle_match(conn, topic_id=topic_id, target_role=target_role)
    return JSONResponse(result)


@app.post('/match-student', response_class=JSONResponse)
def match_student(student_user_id: int = Form(...)):
    with get_conn() as conn:
        result = handle_match_student(conn, student_user_id=student_user_id)
    return JSONResponse(result)
