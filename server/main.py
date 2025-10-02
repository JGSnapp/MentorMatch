import os
import json
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
from urllib import request as urllib_request
from urllib import error as urllib_error

from fastapi import FastAPI, Form, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from media_store import MEDIA_ROOT
from utils import parse_optional_int, normalize_optional_str, resolve_service_account_path

from admin import create_admin_router
from sheet_pairs import sync_roles_sheet

from api import (
    create_matching_router,
    create_students_import_router,
    create_supervisors_import_router,
)
from services.topic_import import (
    normalize_telegram_link,
    extract_telegram_username,
    process_cv,
)

def _configure_logging() -> int:
    level_name = (os.getenv('LOG_LEVEL') or 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        root_logger.addHandler(handler)
    root_logger.setLevel(level)
    return level


load_dotenv()
LOG_LEVEL = _configure_logging()

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

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


def _shorten(text: Optional[str], limit: int = 60) -> str:
    if text is None:
        return ''
    s = str(text).strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)].rstrip() + '…'


def _display_name(name: Optional[str], fallback_id: Optional[Any]) -> str:
    if name:
        stripped = str(name).strip()
        if stripped:
            return stripped
    if fallback_id not in (None, ''):
        return f'#{fallback_id}'
    return 'Пользователь'


def _send_telegram_notification(telegram_id: Optional[Any], text: str, *, button_text: Optional[str] = None, callback_data: Optional[str] = None) -> bool:
    base_url = (
        os.getenv('BOT_API_URL')
        or os.getenv('BOT_INTERNAL_URL')
        or os.getenv('BOT_BASE_URL')
        or 'http://bot:5000'
    )
    if not base_url or not str(base_url).strip():
        logger.warning('Skipping telegram notification: BOT_API_URL not configured')
        return False
    endpoint = str(base_url).rstrip('/') + '/notify'
    if telegram_id in (None, '', 0):
        return False
    try:
        chat_id = int(str(telegram_id).strip())
    except Exception:
        logger.warning('Invalid telegram_id value: %s', telegram_id)
        return False
    payload: Dict[str, Any] = {
        'chat_id': chat_id,
        'text': text,
        'disable_web_page_preview': True,
    }
    if button_text and callback_data:
        payload['reply_markup'] = {
            'inline_keyboard': [
                [
                    {
                        'text': button_text,
                        'callback_data': callback_data,
                    }
                ]
            ]
        }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib_request.Request(endpoint, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            status = getattr(resp, 'status', None) or resp.getcode()
            if 200 <= status < 300:
                resp.read()
                return True
            logger.warning('Bot notification endpoint %s returned HTTP %s', endpoint, status)
            return False
    except urllib_error.HTTPError as exc:
        logger.warning(
            'Bot notification failed with HTTP %s for chat %s: %s',
            getattr(exc, 'code', 'unknown'),
            chat_id,
            exc,
        )
    except urllib_error.URLError as exc:
        logger.warning('Bot notification request error for chat %s: %s', chat_id, exc)
    except Exception as exc:
        logger.warning('Unexpected bot notification error for chat %s: %s', chat_id, exc)
    return False




app = FastAPI(title='MentorMatch Admin MVP')
templates = Jinja2Templates(directory=str((Path(__file__).parent.parent / 'templates').resolve()))
app.include_router(create_admin_router(get_conn, templates))
app.include_router(create_students_import_router(get_conn))
app.include_router(create_supervisors_import_router(get_conn))
app.include_router(create_matching_router(get_conn))

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
    # Ensure new tables (lightweight migration for environments with existing DB)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS user_candidates (
                  user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  topic_id     BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                  score        DOUBLE PRECISION,
                  is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
                  approved     BOOLEAN NOT NULL DEFAULT FALSE,
                  rank         SMALLINT,
                  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                  PRIMARY KEY (user_id, topic_id)
                )
                '''
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_uc_topic ON user_candidates(topic_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_uc_user_score ON user_candidates(user_id, score DESC)")
            # Roles tables
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS roles (
                  id BIGSERIAL PRIMARY KEY,
                  topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                  name TEXT NOT NULL,
                  description TEXT,
                  required_skills TEXT,
                  capacity INTEGER,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                '''
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_roles_topic ON roles(topic_id)")
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS role_candidates (
                  role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  score DOUBLE PRECISION,
                  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                  approved BOOLEAN NOT NULL DEFAULT FALSE,
                  rank SMALLINT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  PRIMARY KEY (role_id, user_id)
                )
                '''
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rc_role_score ON role_candidates(role_id, score DESC)")
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS student_candidates (
                  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                  score DOUBLE PRECISION,
                  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                  approved BOOLEAN NOT NULL DEFAULT FALSE,
                  rank SMALLINT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  PRIMARY KEY (user_id, role_id)
                )
                '''
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sc_user_score ON student_candidates(user_id, score DESC)")
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS supervisor_candidates (
                  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                  score DOUBLE PRECISION,
                  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                  approved BOOLEAN NOT NULL DEFAULT FALSE,
                  rank SMALLINT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  PRIMARY KEY (user_id, topic_id)
                )
                '''
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sc_topic ON supervisor_candidates(topic_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sc_user_score2 ON supervisor_candidates(user_id, score DESC)")
            # Add topics.direction if missing
            try:
                cur.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS direction SMALLINT")
            except Exception as _e:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_topics_direction ON topics(direction)")
            except Exception:
                pass
            # student_profiles schema is defined in 01_schema.sql; no runtime migration for team_role
            # Approved links
            try:
                cur.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS approved_supervisor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE roles ADD COLUMN IF NOT EXISTS approved_student_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL")
            except Exception:
                pass
            # Messages table
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS messages (
                  id BIGSERIAL PRIMARY KEY,
                  sender_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  receiver_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                  role_id BIGINT REFERENCES roles(id) ON DELETE SET NULL,
                  body TEXT NOT NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  answer TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  responded_at TIMESTAMPTZ
                )
                '''
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_user_id, status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_user_id, status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic_id)")
            conn.commit()
    except Exception as e:
        print(f"Startup migration warning (user_candidates): {e}")
    _maybe_test_import()
    sync_roles_sheet(get_conn)


@app.get('/api/topics', response_class=JSONResponse)
def api_get_topics(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT t.id, t.title, t.description, t.seeking_role, t.created_at,
                   u.full_name AS author, t.expected_outcomes, t.required_skills, t.direction,
                   t.author_user_id
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
                   u.full_name AS author, t.expected_outcomes, t.required_skills, t.direction,
                   t.author_user_id
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


@app.get('/api/user-topics/{user_id}', response_class=JSONResponse)
def api_user_topics(user_id: int, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    params = {'uid': user_id, 'offset': offset, 'limit': limit}
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
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
            ''', params,
        )
        rows = cur.fetchall()
        normalized: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            role_names = data.get('approved_role_names') or []
            if isinstance(role_names, list):
                data['approved_role_names'] = [str(name) for name in role_names if name]
            elif role_names in (None, ''):
                data['approved_role_names'] = []
            else:
                data['approved_role_names'] = [str(role_names)]
            role_ids = data.get('approved_role_ids') or []
            if isinstance(role_ids, list):
                cleaned_ids = []
                for rid in role_ids:
                    if rid in (None, ''):
                        continue
                    try:
                        cleaned_ids.append(int(rid))
                    except Exception:
                        continue
                data['approved_role_ids'] = cleaned_ids
            else:
                data['approved_role_ids'] = []
            normalized.append(data)
        return normalized


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
    service_account_file = resolve_service_account_path(os.getenv('SERVICE_ACCOUNT_FILE'))
    if spreadsheet_id and service_account_file:
        # Validate that the service account file actually exists in the container
        try:
            import os as _os
            if not _os.path.exists(service_account_file):
                return {
                    'status': 'not_configured',
                    'error': 'SERVICE_ACCOUNT_FILE not found',
                    'service_account_file': service_account_file,
                    'spreadsheet_id': spreadsheet_id,
                }
        except Exception:
            # If validation fails for any reason, fall back to best effort
            pass
        return {'status': 'configured', 'spreadsheet_id': spreadsheet_id, 'service_account_file': service_account_file}
    return {'status': 'not_configured', 'error': 'Missing env vars'}


# =============================
# Identity & self service
# =============================


@app.get('/api/whoami', response_class=JSONResponse)
def api_whoami(tg_id: Optional[int] = Query(None), username: Optional[str] = Query(None)):
    uname = extract_telegram_username(username)
    link = normalize_telegram_link(username) if username else None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if tg_id:
            cur.execute(
                "SELECT id, full_name, role, email, username, telegram_id, is_confirmed FROM users WHERE telegram_id=%s",
                (int(tg_id),),
            )
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                return {'status': 'ok', 'matches': rows}
        params = []
        clauses = []
        if link:
            clauses.append("LOWER(username)=LOWER(%s)")
            params.append(link)
        if uname:
            clauses.append("LOWER(username)=LOWER(%s)")
            params.append(f"https://t.me/{uname}")
        if not clauses:
            return {'status': 'ok', 'matches': []}
        sql = (
            "SELECT id, full_name, role, email, username, telegram_id, is_confirmed FROM users WHERE ("
            + " OR ".join(clauses)
            + ") LIMIT 5"
        )
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        return {'status': 'ok', 'matches': rows}


@app.post('/api/bind-telegram', response_class=JSONResponse)
def api_bind_telegram(user_id: int = Form(...), tg_id: Optional[str] = Form(None), username: Optional[str] = Form(None)):
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
    return {'status': 'ok'}


@app.post('/api/self-register', response_class=JSONResponse)
def api_self_register(
    role: str = Form(...),
    full_name: Optional[str] = Form(None),
    username: Optional[str] = Form(None),
    tg_id: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
):
    r = (role or '').strip().lower()
    if r not in ('student', 'supervisor'):
        return {'status': 'error', 'message': 'role must be student or supervisor'}
    link = normalize_telegram_link(username) if username else None
    tg_id_val = parse_optional_int(tg_id)
    tg_id_for_name = extract_telegram_username(username) or (str(tg_id).strip() if tg_id else '')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''
            INSERT INTO users(full_name, email, username, telegram_id, role, is_confirmed, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, now(), now())
            RETURNING id
            ''', (
                (full_name or f'Telegram user {tg_id_for_name}').strip(),
                (email or None),
                link,
                tg_id_val,
                r,
            ),
        )
        uid = cur.fetchone()[0]
        if r == 'student':
            cur.execute("INSERT INTO student_profiles(user_id) VALUES (%s)", (uid,))
        else:
            cur.execute("INSERT INTO supervisor_profiles(user_id) VALUES (%s)", (uid,))
        conn.commit()
    return {'status': 'ok', 'user_id': uid, 'role': r}


@app.post('/api/update-student-profile', response_class=JSONResponse)
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
            '''
            SELECT program, skills, interests, cv, skills_to_learn, achievements, workplace
            FROM student_profiles
            WHERE user_id=%s
            ''',
            (user_id,),
        )
        existing = cur.fetchone()
        program_val = (
            normalize_optional_str(program)
            if program is not None
            else (existing.get('program') if existing else None)
        )
        skills_val = (
            normalize_optional_str(skills)
            if skills is not None
            else (existing.get('skills') if existing else None)
        )
        interests_val = (
            normalize_optional_str(interests)
            if interests is not None
            else (existing.get('interests') if existing else None)
        )
        if cv is None:
            cv_val = existing.get('cv') if existing else None
        else:
            cv_val = process_cv(conn, user_id, normalize_optional_str(cv))
        skills_to_learn_val = (
            normalize_optional_str(skills_to_learn)
            if skills_to_learn is not None
            else (existing.get('skills_to_learn') if existing else None)
        )
        achievements_val = (
            normalize_optional_str(achievements)
            if achievements is not None
            else (existing.get('achievements') if existing else None)
        )
        workplace_val = (
            normalize_optional_str(workplace)
            if workplace is not None
            else (existing.get('workplace') if existing else None)
        )

        if existing:
            cur.execute(
                '''
                UPDATE student_profiles
                SET program=%s, skills=%s, interests=%s, cv=%s, skills_to_learn=%s, achievements=%s, workplace=%s
                WHERE user_id=%s
                ''',
                (
                    program_val,
                    skills_val,
                    interests_val,
                    cv_val,
                    skills_to_learn_val,
                    achievements_val,
                    workplace_val,
                    user_id,
                ),
            )
        else:
            cur.execute(
                '''
                INSERT INTO student_profiles(user_id, program, skills, interests, cv, skills_to_learn, achievements, workplace)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''',
                (
                    user_id,
                    program_val,
                    skills_val,
                    interests_val,
                    cv_val,
                    skills_to_learn_val,
                    achievements_val,
                    workplace_val,
                ),
            )
        conn.commit()
    return {'status': 'ok'}


@app.post('/api/update-supervisor-profile', response_class=JSONResponse)
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
            'SELECT position, degree, capacity, interests, requirements FROM supervisor_profiles WHERE user_id=%s',
            (user_id,),
        )
        existing = cur.fetchone()
        position_val = (
            normalize_optional_str(position)
            if position is not None
            else (existing.get('position') if existing else None)
        )
        degree_val = (
            normalize_optional_str(degree)
            if degree is not None
            else (existing.get('degree') if existing else None)
        )
        interests_val = (
            normalize_optional_str(interests)
            if interests is not None
            else (existing.get('interests') if existing else None)
        )
        requirements_val = (
            normalize_optional_str(requirements)
            if requirements is not None
            else (existing.get('requirements') if existing else None)
        )

        if existing:
            cur.execute(
                '''
                UPDATE supervisor_profiles
                SET position=%s, degree=%s, capacity=%s, interests=%s, requirements=%s
                WHERE user_id=%s
                ''',
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
                '''
                INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                VALUES (%s, %s, %s, %s, %s, %s)
                ''',
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
    return {'status': 'ok'}


@app.post('/api/add-topic', response_class=JSONResponse)
def api_add_topic(
    author_user_id: str = Form(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    expected_outcomes: Optional[str] = Form(None),
    required_skills: Optional[str] = Form(None),
    seeking_role: str = Form('student'),
    direction: Optional[str] = Form(None),
):
    author_id_val = parse_optional_int(author_user_id)
    if author_id_val is None:
        raise HTTPException(status_code=400, detail='author_user_id must be an integer')
    title_clean = (title or '').strip()
    if not title_clean:
        raise HTTPException(status_code=400, detail='title is required')
    description_val = normalize_optional_str(description)
    expected_val = normalize_optional_str(expected_outcomes)
    required_val = normalize_optional_str(required_skills)
    direction_val = parse_optional_int(direction)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s AND (direction IS NOT DISTINCT FROM %s)',
            (author_id_val, title_clean, direction_val),
        )
        if cur.fetchone():
            return {'status': 'ok', 'message': 'duplicate'}
        cur.execute(
            '''
            INSERT INTO topics(author_user_id, title, description, expected_outcomes, required_skills, direction, seeking_role, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, now(), now())
            RETURNING id
            ''', (author_id_val, title_clean, description_val, expected_val, required_val, direction_val, seeking_role),
        )
        tid = cur.fetchone()[0]
        conn.commit()
    return {'status': 'ok', 'topic_id': tid}


@app.post('/api/add-role', response_class=JSONResponse)
def api_add_role(
    topic_id: int = Form(...),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    required_skills: Optional[str] = Form(None),
    capacity: Optional[str] = Form(None),
):
    logger.info(
        'api_add_role request: topic_id=%s, name=%s, description_len=%s, required_len=%s, capacity_raw=%s',
        topic_id,
        _shorten(name, 80),
        len(description or ''),
        len(required_skills or ''),
        capacity,
    )
    with get_conn() as conn, conn.cursor() as cur:
        capacity_val = parse_optional_int(capacity)
        name_clean = (name or '').strip()
        if not name_clean:
            raise HTTPException(status_code=400, detail='name is required')
        description_val = normalize_optional_str(description)
        required_val = normalize_optional_str(required_skills)
        logger.debug(
            'api_add_role normalized: name=%s, capacity=%s, description_len=%s, required_len=%s',
            name_clean,
            capacity_val,
            len(description_val or ''),
            len(required_val or ''),
        )
        cur.execute(
            '''
            INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, now(), now())
            RETURNING id
            ''', (topic_id, name_clean, description_val, required_val, capacity_val),
        )
        rid = cur.fetchone()[0]
        conn.commit()
        logger.info(
            'api_add_role inserted role_id=%s for topic=%s (capacity=%s)',
            rid,
            topic_id,
            capacity_val,
        )
    sync_result = sync_roles_sheet(get_conn)
    logger.info('api_add_role: roles sheet sync triggered=%s', sync_result)
    return {'status': 'ok', 'role_id': rid}


@app.post('/api/update-topic', response_class=JSONResponse)
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
            '''
            SELECT author_user_id, title, description, expected_outcomes, required_skills,
                   direction, seeking_role, is_active
            FROM topics
            WHERE id=%s
            ''',
            (topic_id,),
        )
        row = cur.fetchone()
        if not row:
            return {'status': 'error', 'message': 'not_found'}
        author_id = row['author_user_id']
        if editor_id is not None and author_id is not None and author_id != editor_id:
            return {'status': 'error', 'message': 'forbidden'}

        title_val = normalize_optional_str(title) if title is not None else row['title']
        if not title_val:
            return {'status': 'error', 'message': 'title_required'}
        description_val = (
            normalize_optional_str(description)
            if description is not None
            else row['description']
        )
        expected_val = (
            normalize_optional_str(expected_outcomes)
            if expected_outcomes is not None
            else row['expected_outcomes']
        )
        required_val = (
            normalize_optional_str(required_skills)
            if required_skills is not None
            else row['required_skills']
        )
        direction_value = direction_val if direction is not None else row['direction']

        if seeking_role is None:
            seeking_role_val = row['seeking_role']
        else:
            sr = (seeking_role or '').strip().lower()
            if sr in {'student', 'студент'}:
                seeking_role_val = 'student'
            elif sr in {'supervisor', 'руководитель', 'научный руководитель'}:
                seeking_role_val = 'supervisor'
            else:
                return {'status': 'error', 'message': 'invalid_seeking_role'}

        if is_active is None:
            active_val = row['is_active']
        else:
            active_val = _truthy(is_active)

        cur.execute(
            '''
            UPDATE topics
            SET title=%s, description=%s, expected_outcomes=%s, required_skills=%s,
                direction=%s, seeking_role=%s, is_active=%s, updated_at=now()
            WHERE id=%s
            ''',
            (
                title_val,
                description_val,
                expected_val,
                required_val,
                direction_value,
                seeking_role_val,
                active_val,
                topic_id,
            ),
        )
        conn.commit()
    return {'status': 'ok', 'topic_id': topic_id}


@app.post('/api/update-role', response_class=JSONResponse)
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
            '''
            SELECT r.topic_id, r.name, r.description, r.required_skills, r.capacity, t.author_user_id
            FROM roles r
            JOIN topics t ON t.id = r.topic_id
            WHERE r.id=%s
            ''',
            (role_id,),
        )
        row = cur.fetchone()
        if not row:
            return {'status': 'error', 'message': 'not_found'}
        author_id = row['author_user_id']
        if editor_id is not None and author_id is not None and author_id != editor_id:
            return {'status': 'error', 'message': 'forbidden'}

        name_val = normalize_optional_str(name) if name is not None else row['name']
        if not name_val:
            return {'status': 'error', 'message': 'name_required'}
        description_val = (
            normalize_optional_str(description)
            if description is not None
            else row['description']
        )
        required_val = (
            normalize_optional_str(required_skills)
            if required_skills is not None
            else row['required_skills']
        )
        capacity_value = capacity_val if capacity is not None else row['capacity']

        cur.execute(
            '''
            UPDATE roles
            SET name=%s, description=%s, required_skills=%s, capacity=%s, updated_at=now()
            WHERE id=%s
            ''',
            (
                name_val,
                description_val,
                required_val,
                capacity_value,
                role_id,
            ),
        )
        conn.commit()
    return {'status': 'ok', 'topic_id': row['topic_id']}


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
                SELECT t.id, t.title, t.seeking_role, t.direction, t.created_at, u.full_name AS author
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


@app.get('/media/{media_id}')
def serve_media(media_id: int):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT object_key, mime_type FROM media_files WHERE id=%s', (media_id,))
            row = cur.fetchone()
        if not row:
            return JSONResponse({'error': 'Not found'}, status_code=404)
        object_key, mime_type = row
        file_path = (MEDIA_ROOT / object_key).resolve()
        if not file_path.exists():
            return JSONResponse({'error': 'File missing'}, status_code=404)
        return FileResponse(str(file_path), media_type=(mime_type or 'application/octet-stream'), filename=file_path.name)
    except Exception as e:
        return JSONResponse({'error': f'Failed to serve media: {e}'}, status_code=500)


@app.get('/api/topic-candidates/{topic_id}', response_class=JSONResponse)
def api_topic_candidates(topic_id: int, role: Optional[str] = Query(None, pattern='^(student|supervisor)$'), limit: int = Query(5, ge=1, le=50)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # topic_candidates ?????? ?????? ??? ?????????????
        cur.execute(
            '''
            SELECT tc.user_id, u.full_name, u.username, u.role, tc.score, tc.rank
            FROM topic_candidates tc
            JOIN users u ON u.id = tc.user_id AND u.role = 'supervisor'
            WHERE tc.topic_id = %s
            ORDER BY tc.rank ASC NULLS LAST, tc.score DESC NULLS LAST, u.created_at DESC
            LIMIT %s
            ''', (topic_id, limit),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


@app.get('/api/user-candidates/{user_id}', response_class=JSONResponse)
def api_user_candidates(user_id: int, limit: int = Query(5, ge=1, le=50)):
    # Back-compat: ??? ???????? ?????????? ???? (student_candidates), ??? ???????????? â‰ˆ ???? (supervisor_candidates)
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT role FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        role = (row.get('role') if row else None)
        if role == 'student':
            cur.execute(
                '''
                SELECT sc.role_id, r.name AS role_name, sc.score, sc.rank, r.topic_id, t.title AS topic_title
                FROM student_candidates sc
                JOIN roles r ON r.id = sc.role_id
                JOIN topics t ON t.id = r.topic_id
                WHERE sc.user_id = %s
                ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
                LIMIT %s
                ''', (user_id, limit),
            )
        else:
            cur.execute(
                '''
                SELECT sc.topic_id, t.title, sc.score, sc.rank
                FROM supervisor_candidates sc
                JOIN topics t ON t.id = sc.topic_id
                WHERE sc.user_id = %s
                ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
                LIMIT %s
                ''', (user_id, limit),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


@app.get('/api/roles/{role_id}', response_class=JSONResponse)
def api_get_role(role_id: int):
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
        row = cur.fetchone()
        if not row:
            return JSONResponse({'error': 'Not found'}, status_code=404)
        return dict(row)


@app.get('/api/topics/{topic_id}/roles', response_class=JSONResponse)
def api_get_topic_roles(topic_id: int, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT r.*
            FROM roles r
            WHERE r.topic_id = %s
            ORDER BY r.created_at DESC
            OFFSET %s LIMIT %s
            ''', (topic_id, offset, limit),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


@app.get('/api/role-candidates/{role_id}', response_class=JSONResponse)
def api_role_candidates(role_id: int, limit: int = Query(5, ge=1, le=50)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT rc.user_id, u.full_name, u.username, rc.score, rc.rank
            FROM role_candidates rc
            JOIN users u ON u.id = rc.user_id AND u.role = 'student'
            WHERE rc.role_id = %s
            ORDER BY rc.rank ASC NULLS LAST, rc.score DESC NULLS LAST, u.created_at DESC
            LIMIT %s
            ''', (role_id, limit),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


# =============================
# Messages (requests)
# =============================


def _fetch_message_context(cur, message_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(
        '''
        SELECT
            m.id,
            m.sender_user_id,
            m.receiver_user_id,
            m.topic_id,
            m.role_id,
            m.status,
            sender.full_name AS sender_name,
            sender.role AS sender_role,
            sender.telegram_id AS sender_telegram_id,
            receiver.full_name AS receiver_name,
            receiver.role AS receiver_role,
            receiver.telegram_id AS receiver_telegram_id,
            t.title AS topic_title,
            t.seeking_role AS topic_seeking_role,
            r.name AS role_name
        FROM messages m
        JOIN users sender ON sender.id = m.sender_user_id
        JOIN users receiver ON receiver.id = m.receiver_user_id
        JOIN topics t ON t.id = m.topic_id
        LEFT JOIN roles r ON r.id = m.role_id
        WHERE m.id = %s
        ''',
        (message_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _notify_new_application(message: Dict[str, Any]) -> None:
    message_id = message.get('id')
    if message_id is None:
        return
    chat_id = message.get('receiver_telegram_id')
    if not chat_id:
        return
    sender_name = _display_name(message.get('sender_name'), message.get('sender_user_id'))
    topic_label = message.get('topic_title') or f"#{message.get('topic_id')}"
    topic_label = _shorten(topic_label, 70) or f"#{message.get('topic_id')}"
    role_name = message.get('role_name')
    if role_name:
        text = f"На роль «{role_name}» новая заявка."
    else:
        text = f"На тему «{topic_label}» новая заявка."
    text += f"\nОт: {sender_name}"
    if not role_name:
        text += f"\nТема: {topic_label}"
    _send_telegram_notification(
        message.get('receiver_telegram_id'),
        text,
        button_text='Открыть заявку',
        callback_data=f'message_{message_id}',
    )


def _notify_application_update(message: Dict[str, Any], action: str) -> None:
    message_id = message.get('id')
    if message_id is None:
        return
    topic_label = message.get('topic_title') or f"#{message.get('topic_id')}"
    topic_label = _shorten(topic_label, 70) or f"#{message.get('topic_id')}"
    role_name = message.get('role_name')

    def _build_result_line(result_verb: str) -> str:
        if role_name:
            line = f"Вашу заявку на роль «{role_name}» {result_verb}."
            if topic_label:
                line += f"\nТема: {topic_label}"
        else:
            line = f"Вашу заявку на тему «{topic_label}» {result_verb}."
        return line

    if action == 'accept':
        chat_id = message.get('sender_telegram_id')
        if not chat_id:
            return
        receiver_name = _display_name(message.get('receiver_name'), message.get('receiver_user_id'))
        text = _build_result_line('приняли')
        text += f"\nРешение: {receiver_name}"
        _send_telegram_notification(
            chat_id,
            text,
            button_text='Открыть заявку',
            callback_data=f'message_{message_id}',
        )
    elif action == 'reject':
        chat_id = message.get('sender_telegram_id')
        if not chat_id:
            return
        receiver_name = _display_name(message.get('receiver_name'), message.get('receiver_user_id'))
        text = _build_result_line('отклонили')
        text += f"\nРешение: {receiver_name}"
        _send_telegram_notification(
            chat_id,
            text,
            button_text='Открыть заявку',
            callback_data=f'message_{message_id}',
        )
    elif action == 'cancel':
        chat_id = message.get('receiver_telegram_id')
        if not chat_id:
            return
        sender_name = _display_name(message.get('sender_name'), message.get('sender_user_id'))
        text = f"🚫 {sender_name} отменил(а) заявку по теме «{topic_label}»."
        if role_name:
            text += f"\nРоль: {role_name}"
        _send_telegram_notification(
            chat_id,
            text,
            button_text='Открыть заявку',
            callback_data=f'message_{message_id}',
        )


@app.post('/api/messages/send', response_class=JSONResponse)
def api_messages_send(
    sender_user_id: int = Form(...),
    receiver_user_id: int = Form(...),
    topic_id: int = Form(...),
    body: str = Form(...),
    role_id: Optional[str] = Form(None),
):
    msg_id: Optional[int] = None
    message_ctx: Optional[Dict[str, Any]] = None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT role FROM users WHERE id=%s', (sender_user_id,))
        sender_row = cur.fetchone() or {}
        sender_role = (sender_row.get('role') or '').strip().lower() if sender_row else None
        if not sender_role:
            return {'status': 'error', 'message': 'sender not found or role undefined'}
        role_id_val = parse_optional_int(role_id)
        if sender_role == 'student' and role_id_val is None:
            return {'status': 'error', 'message': 'role_id is required for student applications'}
        if sender_role == 'student':
            cur.execute(
                '''
                SELECT 1
                FROM roles
                WHERE topic_id = %s AND approved_student_user_id = %s
                LIMIT 1
                ''',
                (int(topic_id), sender_user_id),
            )
            if cur.fetchone():
                return {'status': 'error', 'message': 'Вы уже утверждены на роль в этой теме.'}
        if role_id_val is not None:
            cur.execute('SELECT 1 FROM roles WHERE id=%s AND topic_id=%s', (role_id_val, int(topic_id)))
            if not cur.fetchone():
                return {'status': 'error', 'message': 'role does not belong to topic'}
        cur.execute(
            '''
            INSERT INTO messages(sender_user_id, receiver_user_id, topic_id, role_id, body, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', now())
            RETURNING id
            ''', (sender_user_id, receiver_user_id, topic_id, role_id_val, body.strip()),
        )
        inserted = cur.fetchone() or {}
        msg_id_raw = inserted.get('id')
        msg_id = None
        if msg_id_raw is not None:
            try:
                msg_id = int(msg_id_raw)
            except Exception:
                msg_id = msg_id_raw
            else:
                message_ctx = _fetch_message_context(cur, msg_id)
        conn.commit()
    if message_ctx:
        _notify_new_application(message_ctx)
    return {'status': 'ok', 'message_id': msg_id}


@app.get('/api/messages/inbox', response_class=JSONResponse)
def api_messages_inbox(user_id: int = Query(...), status: Optional[str] = Query(None)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if status:
            cur.execute(
                '''
                SELECT m.*, t.title AS topic_title, r.name AS role_name, su.full_name AS sender_name
                FROM messages m
                JOIN users su ON su.id = m.sender_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.receiver_user_id = %s AND m.status = %s
                ORDER BY m.created_at DESC
                ''', (user_id, status),
            )
        else:
            cur.execute(
                '''
                SELECT m.*, t.title AS topic_title, r.name AS role_name, su.full_name AS sender_name
                FROM messages m
                JOIN users su ON su.id = m.sender_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.receiver_user_id = %s
                ORDER BY m.created_at DESC
                ''', (user_id,),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


@app.get('/api/messages/outbox', response_class=JSONResponse)
def api_messages_outbox(user_id: int = Query(...), status: Optional[str] = Query(None)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if status:
            cur.execute(
                '''
                SELECT m.*, t.title AS topic_title, r.name AS role_name, ru.full_name AS receiver_name
                FROM messages m
                JOIN users ru ON ru.id = m.receiver_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.sender_user_id = %s AND m.status = %s
                ORDER BY m.created_at DESC
                ''', (user_id, status),
            )
        else:
            cur.execute(
                '''
                SELECT m.*, t.title AS topic_title, r.name AS role_name, ru.full_name AS receiver_name
                FROM messages m
                JOIN users ru ON ru.id = m.receiver_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.sender_user_id = %s
                ORDER BY m.created_at DESC
                ''', (user_id,),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


@app.post('/api/messages/respond', response_class=JSONResponse)
def api_messages_respond(message_id: int = Form(...), responder_user_id: int = Form(...), action: str = Form('accept'), answer: Optional[str] = Form(None)):
    act = (action or 'accept').strip().lower()
    if act not in ('accept', 'reject', 'cancel'):
        return {'status': 'error', 'message': 'invalid action'}
    notify_ctx: Optional[Dict[str, Any]] = None
    needs_export = False
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        msg = _fetch_message_context(cur, message_id)
        if not msg:
            return {'status': 'error', 'message': 'message not found'}
        # Permissions: accept/reject by receiver, cancel by sender
        if act in ('accept', 'reject') and msg.get('receiver_user_id') != responder_user_id:
            return {'status': 'error', 'message': 'only receiver can accept/reject'}
        if act == 'cancel' and msg.get('sender_user_id') != responder_user_id:
            return {'status': 'error', 'message': 'only sender can cancel'}
        status = 'accepted' if act == 'accept' else ('rejected' if act == 'reject' else 'canceled')
        cur.execute('UPDATE messages SET status=%s, answer=%s, responded_at=now() WHERE id=%s', (status, (answer or None), message_id))
        if act == 'accept':
            sender_role = (msg.get('sender_role') or '').strip().lower()
            receiver_role = (msg.get('receiver_role') or '').strip().lower()
            if msg.get('role_id'):
                approved_student_id = None
                if sender_role == 'student':
                    approved_student_id = msg.get('sender_user_id')
                elif receiver_role == 'student':
                    approved_student_id = msg.get('receiver_user_id')
                else:
                    approved_student_id = msg.get('sender_user_id')
                if approved_student_id:
                    cur.execute(
                        'UPDATE roles SET approved_student_user_id=%s WHERE id=%s',
                        (approved_student_id, msg.get('role_id')),
                    )
                    needs_export = True
            else:
                approved_supervisor_id = None
                if sender_role == 'supervisor':
                    approved_supervisor_id = msg.get('sender_user_id')
                elif receiver_role == 'supervisor':
                    approved_supervisor_id = msg.get('receiver_user_id')
                else:
                    approved_supervisor_id = msg.get('sender_user_id')
                if approved_supervisor_id:
                    cur.execute(
                        'UPDATE topics SET approved_supervisor_user_id=%s WHERE id=%s',
                        (approved_supervisor_id, msg.get('topic_id')),
                    )
                    needs_export = True
        else:
            actor_id = responder_user_id if act == 'reject' else msg.get('sender_user_id')
            actor_role_raw = msg.get('receiver_role') if act == 'reject' else msg.get('sender_role')
            actor_role = (actor_role_raw or '').strip().lower()
            if msg.get('role_id') and actor_role == 'student' and actor_id:
                cur.execute('SELECT approved_student_user_id FROM roles WHERE id=%s', (msg.get('role_id'),))
                row = cur.fetchone()
                if row and row.get('approved_student_user_id') == actor_id:
                    cur.execute('UPDATE roles SET approved_student_user_id=NULL WHERE id=%s', (msg.get('role_id'),))
                    needs_export = True
            elif not msg.get('role_id') and actor_role == 'supervisor' and actor_id:
                cur.execute('SELECT approved_supervisor_user_id FROM topics WHERE id=%s', (msg.get('topic_id'),))
                row = cur.fetchone()
                if row and row.get('approved_supervisor_user_id') == actor_id:
                    cur.execute('UPDATE topics SET approved_supervisor_user_id=NULL WHERE id=%s', (msg.get('topic_id'),))
                    needs_export = True
        conn.commit()
        msg['status'] = status
        msg['answer'] = answer or None
        notify_ctx = msg
    if notify_ctx:
        _notify_application_update(notify_ctx, act)
    if needs_export:
        sync_roles_sheet(get_conn)
    return {'status': 'ok'}


@app.post('/api/roles/{role_id}/clear-approved', response_class=JSONResponse)
def api_clear_role_approved(role_id: int, by_user_id: int = Form(...)):
    with get_conn() as conn, conn.cursor() as cur:
        # Check who is allowed: topic author or approved student
        cur.execute('SELECT r.approved_student_user_id, t.author_user_id FROM roles r JOIN topics t ON t.id = r.topic_id WHERE r.id=%s', (role_id,))
        row = cur.fetchone()
        if not row:
            return {'status': 'error', 'message': 'role not found'}
        approved_student_id, author_id = row
        if (approved_student_id is None) or (by_user_id not in (approved_student_id, author_id)):
            return {'status': 'error', 'message': 'not allowed'}
        cur.execute('UPDATE roles SET approved_student_user_id=NULL WHERE id=%s', (role_id,))
        conn.commit()
    sync_roles_sheet(get_conn)
    return {'status': 'ok'}


@app.post('/api/topics/{topic_id}/clear-approved-supervisor', response_class=JSONResponse)
def api_clear_topic_supervisor(topic_id: int, by_user_id: int = Form(...)):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT approved_supervisor_user_id, author_user_id FROM topics WHERE id=%s', (topic_id,))
        row = cur.fetchone()
        if not row:
            return {'status': 'error', 'message': 'topic not found'}
        approved_supervisor_id, author_id = row
        if (approved_supervisor_id is None) or (by_user_id not in (approved_supervisor_id, author_id)):
            return {'status': 'error', 'message': 'not allowed'}
        cur.execute('UPDATE topics SET approved_supervisor_user_id=NULL WHERE id=%s', (topic_id,))
        conn.commit()
    sync_roles_sheet(get_conn)
    return {'status': 'ok'}


@app.get('/api/student-candidates/{user_id}', response_class=JSONResponse)
def api_student_candidates(user_id: int, limit: int = Query(5, ge=1, le=50)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT sc.role_id, r.name AS role_name, sc.score, sc.rank, r.topic_id, t.title AS topic_title
            FROM student_candidates sc
            JOIN roles r ON r.id = sc.role_id
            JOIN topics t ON t.id = r.topic_id
            WHERE sc.user_id = %s
            ORDER BY sc.rank ASC NULLS LAST, sc.score DESC NULLS LAST, t.created_at DESC
            LIMIT %s
            ''', (user_id, limit),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]



