import os
import json
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
from urllib import request as urllib_request

from fastapi import FastAPI, Form, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from media_store import persist_media_from_url, MEDIA_ROOT
from utils import parse_optional_int, normalize_optional_str

from parse_gform import fetch_normalized_rows, fetch_supervisor_rows
from matching import handle_match, handle_match_student, handle_match_supervisor_user
from matching import handle_match_role
from matching import client as LLM_CLIENT  # reuse configured OpenAI client
from matching import PROXY_MODEL
from admin import create_admin_router
from sheet_pairs import export_pairs_from_db


load_dotenv()

logger = logging.getLogger(__name__)

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
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.debug('Skipping telegram notification: TELEGRAM_BOT_TOKEN not set')
        return False
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
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    req = urllib_request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except Exception as exc:
        logger.warning('Failed to send Telegram notification to %s: %s', chat_id, exc)
        return False




app = FastAPI(title='MentorMatch Admin MVP')
templates = Jinja2Templates(directory=str((Path(__file__).parent.parent / 'templates').resolve()))
app.include_router(create_admin_router(get_conn, templates))


def _truthy(val: Optional[str]) -> bool:
    return str(val or '').strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _normalize_telegram_link(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = (raw or '').strip()
    if s.startswith('@'):
        s = s[1:]
    if s.lower().startswith(('http://t.me/', 'https://t.me/', 'http://telegram.me/', 'https://telegram.me/')):
        return s
    import re
    m = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", s)
    if m:
        return f"https://t.me/{m.group(1)}"
    s = re.sub(r"[^A-Za-z0-9_]", "", s)
    return f"https://t.me/{s}" if s else None


def _extract_tg_username(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith('@'):
        s = s[1:]
    import re
    m = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", s)
    if m:
        return m.group(1)
    s = re.sub(r"[^A-Za-z0-9_]", "", s)
    return s or None


def _resolve_service_account_path(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    try:
        path = Path(p)
        if path.is_absolute() and path.exists():
            return str(path)
        candidates = [
            Path(p),
            Path(__file__).parent / p,              # /app/<p>
            Path(__file__).parent.parent / p,       # repo root candidate
        ]
        for c in candidates:
            if c.exists():
                return str(c)
    except Exception:
        pass
    return p


def _read_csv_rows(p: Path) -> List[Dict[str, str]]:
    import csv
    if not p.exists():
        return []
    with p.open('r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return [ {k: (v or '').strip() for k,v in row.items()} for row in reader ]




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
    service_account_file = _resolve_service_account_path(os.getenv('SERVICE_ACCOUNT_FILE'))
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
    uname = _extract_tg_username(username)
    link = _normalize_telegram_link(username) if username else None
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
    link = _normalize_telegram_link(username) if username else None
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
    # Best-effort export after status change (especially on accept)
    try:
        if act == 'accept':
            pairs_sheet = os.getenv('PAIRS_SPREADSHEET_ID')
            service_account_file = _resolve_service_account_path(os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json'))
            if pairs_sheet:
                with get_conn() as conn2:
                    export_pairs_from_db(conn2, pairs_sheet, service_account_file)
    except Exception:
        pass
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
    link = _normalize_telegram_link(username) if username else None
    tg_id_val = parse_optional_int(tg_id)
    tg_id_for_name = _extract_tg_username(username) or (str(tg_id).strip() if tg_id else '')
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
            cv_val = _process_cv(conn, user_id, normalize_optional_str(cv))
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
    with get_conn() as conn, conn.cursor() as cur:
        capacity_val = parse_optional_int(capacity)
        name_clean = (name or '').strip()
        if not name_clean:
            raise HTTPException(status_code=400, detail='name is required')
        description_val = normalize_optional_str(description)
        required_val = normalize_optional_str(required_skills)
        cur.execute(
            '''
            INSERT INTO roles(topic_id, name, description, required_skills, capacity, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, now(), now())
            RETURNING id
            ''', (topic_id, name_clean, description_val, required_val, capacity_val),
        )
        rid = cur.fetchone()[0]
        conn.commit()
    # Best-effort export
    try:
        pairs_sheet = os.getenv('PAIRS_SPREADSHEET_ID')
        service_account_file = _resolve_service_account_path(os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json'))
        if pairs_sheet:
            with get_conn() as conn2:
                export_pairs_from_db(conn2, pairs_sheet, service_account_file)
    except Exception as _e:
        pass
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


@app.post('/api/import-sheet', response_class=JSONResponse)
def api_import_sheet(spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
    try:
        service_account_file = _resolve_service_account_path(os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json'))
        # Fast TLS reachability check for clearer SSL errors
        try:
            import requests as _requests
            _requests.get('https://www.googleapis.com/generate_204', timeout=5)
        except Exception as _tls_e:
            # Continue, but log for diagnostics
            try:
                print(f"/api/import-sheet TLS preflight warning: {type(_tls_e).__name__}: {_tls_e}")
            except Exception:
                pass
        try:
            import os as _os
            if not _os.path.exists(service_account_file):
                return {'status': 'error', 'message': f'SERVICE_ACCOUNT_FILE not found: {service_account_file}'}
        except Exception:
            pass
        rows = fetch_normalized_rows(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            service_account_file=service_account_file,
        )
        if (os.getenv('LOG_LEVEL') or '').upper() == 'DEBUG':
            try:
                print(f"/api/import-sheet: rows={len(rows)} sheet_id={spreadsheet_id} sheet_name={sheet_name}")
            except Exception:
                pass

        inserted_users = 0
        inserted_profiles = 0
        inserted_topics = 0

        with get_conn() as conn, conn.cursor() as cur:
            for idx, r in enumerate(rows):
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
                    try:
                        cur.execute(
                            f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s",
                            tuple(params),
                        )
                    except TypeError as te:
                        try:
                            print(f"/api/import-sheet TypeError at row {idx} during users UPDATE: {te}. updates={updates} params={params}")
                        except Exception:
                            pass
                        raise RuntimeError(f"row {idx}: {type(te).__name__}: {te}")

                # Upsert student profile
                cur.execute('SELECT 1 FROM student_profiles WHERE user_id=%s', (user_id,))
                exists = cur.fetchone() is not None
                skills_have = ', '.join(r.get('hard_skills_have') or []) or None
                skills_want = ', '.join(r.get('hard_skills_want') or []) or None
                interests = ', '.join(r.get('interests') or []) or None
                requirements = r.get('supervisor_preference')

                if exists:
                    try:
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
                    except TypeError as te:
                        try:
                            print(f"/api/import-sheet TypeError at row {idx} during student_profiles UPDATE: {te}. row={r}")
                        except Exception:
                            pass
                        raise RuntimeError(f"row {idx}: {type(te).__name__}: {te}")
                else:
                    try:
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
                    except TypeError as te:
                        try:
                            print(f"/api/import-sheet TypeError at row {idx} during student_profiles INSERT: {te}. row={r}")
                        except Exception:
                            pass
                        raise RuntimeError(f"row {idx}: {type(te).__name__}: {te}")
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
                            tail = f"\n\n????????? ?????: {groundwork}".strip()
                            desc = f"{desc}\n{tail}" if desc else tail
                        practical = (topic.get('practical_importance') or None)
                        if practical:
                            desc = (desc or '').strip()
                            tail2 = f"\n\n???????????? ??????????: {practical}".strip()
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

        return {
            'status': 'success',
            'message': f'Ð¿â‰¤Ð¿â•ªÐ¿Â©Ð¿â•¬Ñâ”€Ñâ”Œ: users+{inserted_users}, profiles~{inserted_profiles}, topics+{inserted_topics}',
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


# =============================
# Import Supervisors (2nd sheet)
# =============================

def _llm_extract_topics(text: str) -> Optional[List[Dict[str, Any]]]:
    if not text or not text.strip():
        return None
    if LLM_CLIENT is None:
        return None

    functions = [
        {
            'name': 'extract_topics',
            'description': 'Ð¿â‰¤Ð¿â•¥Ð¿â•¡Ð¿â•©Ð¿â•£Ð¿â•¨Ð¿â•¦ Ñâ”‚Ð¿Â©Ð¿â•¦Ñâ”‚Ð¿â•¬Ð¿â•¨ Ñâ”ŒÐ¿â•£Ð¿â•ª Ð¿â•¦Ð¿â•¥ Ñâ”ŒÐ¿â•£Ð¿â•¨Ñâ”‚Ñâ”ŒÐ¿â•Ÿ Ñâ”‚ Ð¿â•¨Ñâ”€Ð¿â•ŸÑâ”ŒÐ¿â•¨Ð¿â•¦Ð¿â•ªÐ¿â•¦ Ð¿Â©Ð¿â•¬Ð¿â•©Ñâ–Ð¿â•ªÐ¿â•¦.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'topics': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'title': {'type': 'string'},
                                'description': {'type': 'string'},
                                'expected_outcomes': {'type': 'string'},
                                'required_skills': {'type': 'string'}
                            },
                            'required': ['title']
                        },
                        'minItems': 1
                    }
                },
                'required': ['topics']
            }
        }
    ]

    messages = [
        {
            'role': 'system',
            'content': 'Ð¿â•’Ñâ–€ Ð¿â•ŸÑâ”‚Ñâ”‚Ð¿â•¦Ñâ”‚Ñâ”ŒÐ¿â•£Ð¿â•«Ñâ”Œ, Ð¿â•¨Ð¿â•¬Ñâ”ŒÐ¿â•¬Ñâ”€Ñâ–€Ð¿â•§ Ñâ”‚Ñâ”ŒÑâ”€Ñâ”Ð¿â•¨Ñâ”ŒÑâ”Ñâ”€Ð¿â•¦Ñâ”€Ñâ”Ð¿â•£Ñâ”Œ Ñâ”‚Ð¿Â©Ð¿â•¦Ñâ”‚Ð¿â•¬Ð¿â•¨ Ñâ”ŒÐ¿â•£Ð¿â•ª Ð¿â–“Ð¿Â Ð¿â•. Ð¿â–“Ñâ”‚Ð¿â•£Ð¿ÐÐ¿â•¢Ð¿â•Ÿ Ð¿â•¬Ñâ”ŒÐ¿â•¡Ð¿â•£Ñâ”¤Ð¿â•ŸÐ¿â•§ Ð¿Â©Ð¿â•¬-Ñâ”€Ñâ”Ñâ”‚Ñâ”‚Ð¿â•¨Ð¿â•¦. Ð¿â–“Ð¿â•£Ñâ”€Ð¿â•«Ð¿â•¦ Ñâ”ŒÐ¿â•¬Ð¿â•©Ñâ–„Ð¿â•¨Ð¿â•¬ Ñâ”‚Ñâ”ŒÑâ”€Ñâ”Ð¿â•¨Ñâ”ŒÑâ”Ñâ”€Ð¿â•¦Ñâ”€Ð¿â•¬Ð¿â•¡Ð¿â•ŸÐ¿â•«Ð¿â•«Ñâ–€Ð¿â•§ Ñâ”€Ð¿â•£Ð¿â•¥Ñâ”Ð¿â•©Ñâ–„Ñâ”ŒÐ¿â•ŸÑâ”Œ Ñâ”¤Ð¿â•£Ñâ”€Ð¿â•£Ð¿â•¥ Ñâ””Ñâ”Ð¿â•«Ð¿â•¨Ñâ”œÐ¿â•¦Ñâ–Œ.'
        },
        {
            'role': 'user',
            'content': f'Ð¿â•’Ð¿â•£Ð¿â•¨Ñâ”‚Ñâ”Œ, Ñâ”‚Ð¿â•¬Ð¿â•¢Ð¿â•£Ñâ”€Ð¿â•¤Ð¿â•ŸÑâ”´Ð¿â•¦Ð¿â•§ Ñâ”ŒÐ¿â•£Ð¿â•ªÑâ–€ Ð¿â•¦Ð¿â•©Ð¿â•¦ Ñâ”ŒÐ¿â•£Ð¿â•ªÐ¿â•ŸÑâ”ŒÐ¿â•¦Ð¿â•¨Ð¿â•¦ Ð¿â•¢Ð¿â•©Ñâ– Ð¿â–“Ð¿Â Ð¿â• (Ð¿â•ªÐ¿â•¬Ð¿ÐÑâ”Ñâ”Œ Ð¿â• Ñâ–€Ñâ”ŒÑâ–„ Ð¿Â©Ð¿â•£Ñâ”€Ð¿â•£Ñâ”¤Ð¿â•¦Ñâ”‚Ð¿â•©Ð¿â•£Ð¿â•«Ñâ–€ Ñâ”¤Ð¿â•£Ñâ”€Ð¿â•£Ð¿â•¥ Ð¿â•¥Ð¿â•ŸÐ¿Â©Ñâ–Ñâ”ŒÑâ–€Ð¿â•£, Ñâ”ŒÐ¿â•¬Ñâ”¤Ð¿â•¨Ð¿â•¦ Ñâ”‚ Ð¿â•¥Ð¿â•ŸÐ¿Â©Ñâ–Ñâ”ŒÐ¿â•¬Ð¿â•§, Ñâ”‚Ð¿Â©Ð¿â•¦Ñâ”‚Ð¿â•¨Ð¿â•¬Ð¿â•ª):\n\n{text}\n\nÐ¿â–“Ñâ–€Ð¿â•¢Ð¿â•£Ð¿â•©Ð¿â•¦ Ð¿â•¬Ñâ”ŒÐ¿â•¢Ð¿â•£Ð¿â•©Ñâ–„Ð¿â•«Ñâ–€Ð¿â•£ Ñâ”ŒÐ¿â•£Ð¿â•ªÑâ–€. Ð¿âˆ™Ñâ”‚Ð¿â•©Ð¿â•¦ Ð¿â•¢Ð¿â•£Ñâ”ŒÐ¿â•ŸÐ¿â•©Ð¿â•£Ð¿â•§ Ð¿â•ªÐ¿â•ŸÐ¿â•©Ð¿â•¬ Ð‘â”€â–  Ñâ”‚Ñâ””Ð¿â•¬Ñâ”€Ð¿â•ªÑâ”Ð¿â•©Ð¿â•¦Ñâ”€Ñâ”Ð¿â•§ Ð¿â•¨Ñâ”€Ð¿â•ŸÑâ”ŒÐ¿â•¨Ð¿â•¬Ð¿â•£ Ð¿â•¬Ð¿Â©Ð¿â•¦Ñâ”‚Ð¿â•ŸÐ¿â•«Ð¿â•¦Ð¿â•£, Ð¿â•¬Ð¿â•¤Ð¿â•¦Ð¿â•¢Ð¿â•ŸÐ¿â•£Ð¿â•ªÑâ–€Ð¿â•£ Ñâ”€Ð¿â•£Ð¿â•¥Ñâ”Ð¿â•©Ñâ–„Ñâ”ŒÐ¿â•ŸÑâ”ŒÑâ–€ Ð¿â•¦ Ñâ”ŒÑâ”€Ð¿â•£Ð¿â• Ñâ”Ð¿â•£Ð¿â•ªÑâ–€Ð¿â•£ Ð¿â•«Ð¿â•ŸÐ¿â•¡Ñâ–€Ð¿â•¨Ð¿â•¦ Ð¿Â©Ð¿â•¬ Ñâ”‚Ð¿â•ªÑâ–€Ñâ”‚Ð¿â•©Ñâ”.'
        }
    ]

    try:
        resp = LLM_CLIENT.chat.completions.create(
            model=PROXY_MODEL,
            messages=messages,
            functions=functions,
            function_call={'name': 'extract_topics'},
            temperature=0.2,
        )
    except Exception as e:
        print(f"LLM extract error: {e}")
        return None

    if not resp.choices or not resp.choices[0].message:
        return None
    msg = resp.choices[0].message
    fc = getattr(msg, 'function_call', None)
    if not fc or not getattr(fc, 'arguments', None):
        return None
    try:
        import json
        parsed = json.loads(fc.arguments)
        topics = parsed.get('topics', [])
        norm = []
        for t in topics:
            title = (t.get('title') or '').strip()
            if not title:
                continue
            norm.append({
                'title': title,
                'description': (t.get('description') or '').strip() or None,
                'expected_outcomes': (t.get('expected_outcomes') or '').strip() or None,
                'required_skills': (t.get('required_skills') or '').strip() or None,
            })
        return norm or None
    except Exception:
        return None


def _fallback_extract_topics(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    import re
    parts = re.split(r'[\n;Ð‘â”€â•’\-\u2022]+|\s{2,}', text)
    res = []
    for p in parts:
        title = (p or '').strip(' \t\r\n.-Ð‘â”€â•’')
        if not title:
            continue
        # Avoid overly short tokens
        if len(title) < 3:
            continue
        res.append({'title': title, 'description': None, 'expected_outcomes': None, 'required_skills': None})
    # Deduplicate by title
    seen = set()
    uniq = []
    for t in res:
        if t['title'].lower() in seen:
            continue
        seen.add(t['title'].lower())
        uniq.append(t)
    return uniq


@app.post('/api/import-supervisors', response_class=JSONResponse)
def api_import_supervisors(spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
    try:
        service_account_file = _resolve_service_account_path(os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json'))
        rows = fetch_supervisor_rows(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,  # default: 2Ð¿â•§ Ð¿â•©Ð¿â•¦Ñâ”‚Ñâ”Œ, Ð¿â•£Ñâ”‚Ð¿â•©Ð¿â•¦ None
            service_account_file=service_account_file,
        )

        inserted_users = 0
        upserted_profiles = 0
        inserted_topics = 0

        with get_conn() as conn, conn.cursor() as cur:
            for r in rows:
                full_name = (r.get('full_name') or '').strip()
                email = (r.get('email') or '').strip() or None
                if not (full_name or email):
                    continue

                # Find or create supervisor user
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
                        INSERT INTO users(full_name, email, role, created_at, updated_at)
                        VALUES (%s, %s, 'supervisor', now(), now())
                        RETURNING id
                        """, (full_name, email),
                    )
                    user_id = cur.fetchone()[0]
                    inserted_users += 1

                # Update telegram username if provided
                updates = []
                params: List[Any] = []
                if r.get('telegram'):
                    tg = _normalize_telegram_link(r.get('telegram'))
                    if tg:
                        updates.append('username=%s')
                        params.append(tg)
                if updates:
                    params.append(user_id)
                    cur.execute(f"UPDATE users SET {', '.join(updates)}, updated_at=now() WHERE id=%s", tuple(params))

                # Upsert supervisor profile (interests <- area; requirements <- extra_info)
                cur.execute('SELECT 1 FROM supervisor_profiles WHERE user_id=%s', (user_id,))
                exists = cur.fetchone() is not None
                if exists:
                    cur.execute(
                        """
                        UPDATE supervisor_profiles
                        SET interests=%s, requirements=%s
                        WHERE user_id=%s
                        """,
                        (r.get('area') or None, r.get('extra_info') or None, user_id),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO supervisor_profiles(user_id, position, degree, capacity, interests, requirements)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (user_id, None, None, None, r.get('area') or None, r.get('extra_info') or None),
                    )
                upserted_profiles += 1

                # Extract and insert supervisor's topics (with direction when available)
                def _insert_from_text(txt: Optional[str], direction: Optional[int]):
                    nonlocal inserted_topics
                    if not txt or not (txt.strip()):
                        return
                    topics = _llm_extract_topics(txt) or _fallback_extract_topics(txt)
                    for t in topics:
                        title = (t.get('title') or '').strip()
                        if not title:
                            continue
                        # Avoid duplicates per direction
                        cur.execute(
                            'SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s AND (direction IS NOT DISTINCT FROM %s)',
                            (user_id, title, direction),
                        )
                        if cur.fetchone():
                            continue
                        cur.execute(
                            """
                            INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                               required_skills, direction, seeking_role, is_active, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, 'student', TRUE, now(), now())
                            """,
                            (
                                user_id,
                                title,
                                t.get('description'),
                                t.get('expected_outcomes'),
                                t.get('required_skills'),
                                direction,
                            ),
                        )
                        inserted_topics += 1

                # Try per-direction fields first
                _insert_from_text(r.get('topics_09'), 9)
                _insert_from_text(r.get('topics_11'), 11)
                _insert_from_text(r.get('topics_45'), 45)
                # Fallback to unified text (no direction)
                if not any((r.get('topics_09'), r.get('topics_11'), r.get('topics_45'))):
                    _insert_from_text(r.get('topics_text'), None)

        return {
            'status': 'success',
            'message': f'Ð¿â‰¤Ð¿â•ªÐ¿Â©Ð¿â•¬Ñâ”€Ñâ”Œ Ñâ”€Ñâ”Ð¿â•¨Ð¿â•¬Ð¿â•¡Ð¿â•¬Ð¿â•¢Ð¿â•¦Ñâ”ŒÐ¿â•£Ð¿â•©Ð¿â•£Ð¿â•§: users+{inserted_users}, profiles~{upserted_profiles}, topics+{inserted_topics}',
            'stats': {
                'inserted_users': inserted_users,
                'upserted_profiles': upserted_profiles,
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


@app.post('/match-supervisor', response_class=JSONResponse)
def match_supervisor_user(supervisor_user_id: int = Form(...)):
    with get_conn() as conn:
        result = handle_match_supervisor_user(conn, supervisor_user_id=supervisor_user_id)
    return JSONResponse(result)


@app.post('/match-role', response_class=JSONResponse)
def match_role(role_id: int = Form(...)):
    with get_conn() as conn:
        result = handle_match_role(conn, role_id=role_id)
    return JSONResponse(result)


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
            sender.telegram_id AS sender_telegram_id,
            receiver.full_name AS receiver_name,
            receiver.telegram_id AS receiver_telegram_id,
            t.title AS topic_title,
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
    text = f"📨 Новая заявка от {sender_name} по теме «{topic_label}»."
    role_name = message.get('role_name')
    if role_name:
        text += f"\nРоль: {role_name}"
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
    if action == 'accept':
        chat_id = message.get('sender_telegram_id')
        if not chat_id:
            return
        receiver_name = _display_name(message.get('receiver_name'), message.get('receiver_user_id'))
        text = f"✅ {receiver_name} принял(а) вашу заявку по теме «{topic_label}»."
        if role_name:
            text += f"\nРоль: {role_name}"
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
        text = f"❌ {receiver_name} отклонил(а) вашу заявку по теме «{topic_label}»."
        if role_name:
            text += f"\nРоль: {role_name}"
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
            if msg.get('role_id'):
                cur.execute('UPDATE roles SET approved_student_user_id=%s WHERE id=%s', (msg.get('receiver_user_id'), msg.get('role_id')))
            else:
                cur.execute('UPDATE topics SET approved_supervisor_user_id=%s WHERE id=%s', (msg.get('receiver_user_id'), msg.get('topic_id')))
        conn.commit()
        msg['status'] = status
        msg['answer'] = answer or None
        notify_ctx = msg
    if notify_ctx:
        _notify_application_update(notify_ctx, act)
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



