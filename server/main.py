import os
from typing import Optional, List, Dict, Any
from pathlib import Path
from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from parse_gform import fetch_normalized_rows
from matching import handle_match

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

# API эндпоинты для бота
@app.get('/api/topics', response_class=JSONResponse)
def api_get_topics(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    """API для получения списка тем"""
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

@app.get('/api/supervisors', response_class=JSONResponse)
def api_get_supervisors(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    """API для получения списка научных руководителей"""
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

@app.get('/api/students', response_class=JSONResponse)
def api_get_students(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    """API для получения списка студентов"""
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

@app.get('/api/topics/{topic_id}', response_class=JSONResponse)
def api_get_topic(topic_id: int):
    """API для получения конкретной темы"""
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
            return JSONResponse({'error': 'Тема не найдена'}, status_code=404)
        return dict(topic)

@app.get('/api/supervisors/{supervisor_id}', response_class=JSONResponse)
def api_get_supervisor(supervisor_id: int):
    """API для получения конкретного научного руководителя"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sup.position, sup.degree, sup.capacity, sup.interests, sup.requirements
            FROM users u
            LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
            WHERE u.id = %s AND u.role = 'supervisor'
            ''', (supervisor_id,),
        )
        supervisor = cur.fetchone()
        if not supervisor:
            return JSONResponse({'error': 'Научный руководитель не найден'}, status_code=404)
        return dict(supervisor)

@app.get('/api/students/{student_id}', response_class=JSONResponse)
def api_get_student(student_id: int):
    """API для получения конкретного студента"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id, u.full_name, u.username, u.email, u.created_at,
                   sp.program, sp.skills, sp.interests, sp.cv
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s AND u.role = 'student'
            ''', (student_id,),
        )
        student = cur.fetchone()
        if not student:
            return JSONResponse({'error': 'Студент не найден'}, status_code=404)
        return dict(student)

@app.get('/api/sheets-status', response_class=JSONResponse)
def api_get_sheets_status():
    """API для проверки статуса Google Sheets интеграции"""
    spreadsheet_id = os.getenv('SPREADSHEET_ID')
    service_account_file = os.getenv('SERVICE_ACCOUNT_FILE')
    
    if spreadsheet_id and service_account_file:
        return {
            "status": "configured",
            "spreadsheet_id": spreadsheet_id[:20] + "..." if len(spreadsheet_id) > 20 else spreadsheet_id,
            "service_account_file": service_account_file
        }
    else:
        missing_vars = []
        if not spreadsheet_id:
            missing_vars.append("SPREADSHEET_ID")
        if not service_account_file:
            missing_vars.append("SERVICE_ACCOUNT_FILE")
        
        return {
            "status": "not_configured",
            "missing_vars": missing_vars
        }

@app.get('/api/sheets-config', response_class=JSONResponse)
def api_get_sheets_config():
    """API для получения полной конфигурации Google Sheets (для импорта)"""
    spreadsheet_id = os.getenv('SPREADSHEET_ID')
    service_account_file = os.getenv('SERVICE_ACCOUNT_FILE')
    
    if spreadsheet_id and service_account_file:
        return {
            "status": "configured",
            "spreadsheet_id": spreadsheet_id,  # Полный ID без обрезки
            "service_account_file": service_account_file
        }
    else:
        return {
            "status": "not_configured",
            "error": "Переменные окружения не настроены"
        }

@app.post('/api/import-sheet', response_class=JSONResponse)
def api_import_sheet(spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
    """API для импорта данных из Google Sheets"""
    try:
        print(f"DEBUG: Начинаем импорт из spreadsheet_id={spreadsheet_id}, sheet_name={sheet_name}")
        
        service_account_file = os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
        print(f"DEBUG: Используем service_account_file={service_account_file}")
        
        rows = fetch_normalized_rows(spreadsheet_id=spreadsheet_id, sheet_name=sheet_name, service_account_file=service_account_file)
        print(f"DEBUG: Получено строк из Google Sheets: {len(rows) if rows else 0}")
        
        if rows:
            print(f"DEBUG: Первая строка: {rows[0] if rows else 'None'}")
        
        # Получаем общее количество студентов в БД
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM users WHERE role = \'student\'')
            total_students_in_db = cur.fetchone()[0]
        
        inserted_users = 0
        inserted_profiles = 0
        inserted_topics = 0
        
        # Идем с конца таблицы до первого существующего студента
        with get_conn() as conn, conn.cursor() as cur:
            for r in reversed(rows):  # reversed() - идем с конца
                full_name = (r.get('full_name') or '').strip()
                if not full_name:
                    continue
                
                # Проверяем существование пользователя
                cur.execute('SELECT id FROM users WHERE full_name=%s AND role=\'student\' LIMIT 1', (full_name,))
                row = cur.fetchone()
                if row:
                    # Нашли существующего студента - останавливаемся
                    print(f"DEBUG: Найден существующий студент '{full_name}', останавливаем импорт")
                    break
                else:
                    # Создаем нового пользователя
                    cur.execute(
                        '''
                        INSERT INTO users(full_name, role, created_at, updated_at)
                        VALUES (%s, 'student', now(), now())
                        RETURNING id
                        ''', (full_name,),
                    )
                    user_id = cur.fetchone()[0]
                    inserted_users += 1
                
                # Создаем профиль студента (всегда новый)
                cur.execute(
                    '''
                    INSERT INTO student_profiles(user_id, program, skills, interests, cv, requirements)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (
                        user_id,
                        r.get('program'),
                        ', '.join(r.get('hard_skills') or []) or None,
                        ', '.join(r.get('interests') or []) or None,
                        r.get('cv'),
                        r.get('preferences'),
                    ),
                )
                inserted_profiles += 1
                
                # Создаем тему, если есть
                topic = r.get('topic')
                if r.get('has_own_topic') and topic and (topic.get('title') or '').strip():
                    title = topic.get('title').strip()
                    cur.execute('SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s', (user_id, title))
                    if not cur.fetchone():
                        cur.execute(
                            '''
                            INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                               required_skills, seeking_role, is_active, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, 'supervisor', TRUE, now(), now())
                            ''', (
                                user_id,
                                title,
                                topic.get('description'),
                                topic.get('expected_outcomes'),
                                ', '.join(r.get('hard_skills') or []) or None,
                            ),
                        )
                        inserted_topics += 1
        
        return {
            "status": "success",
            "message": f"Импорт завершен: пользователей +{inserted_users}, профилей +{inserted_profiles}, тем +{inserted_topics}",
            "stats": {
                "inserted_users": inserted_users,
                "inserted_profiles": inserted_profiles,
                "inserted_topics": inserted_topics,
                "total_rows_in_sheet": len(rows) if rows else 0,
                "total_students_in_db": total_students_in_db
            }
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Ошибка при импорте: {str(e)}"
        }

@app.get('/', response_class=HTMLResponse)
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

@app.post('/import-sheet')
def import_sheet(request: Request, spreadsheet_id: str = Form(...), sheet_name: Optional[str] = Form(None)):
    service_account_file = os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
    rows = fetch_normalized_rows(spreadsheet_id=spreadsheet_id, sheet_name=sheet_name, service_account_file=service_account_file)

    inserted_users = 0
    upserted_profiles = 0
    inserted_topics = 0

    with get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            full_name = (r.get('full_name') or '').strip()
            if not full_name:
                continue

            cur.execute('SELECT id FROM users WHERE full_name=%s AND role=\'student\' LIMIT 1', (full_name,))
            row = cur.fetchone()
            if row:
                user_id = row[0]
            else:
                cur.execute(
                    '''
                    INSERT INTO users(full_name, role, created_at, updated_at)
                    VALUES (%s, 'student', now(), now())
                    RETURNING id
                    ''', (full_name,),
                )
                user_id = cur.fetchone()[0]
                inserted_users += 1

            cur.execute('SELECT 1 FROM student_profiles WHERE user_id=%s', (user_id,))
            if cur.fetchone():
                cur.execute(
                    '''
                    UPDATE student_profiles
                    SET program=%s, skills=%s, interests=%s, cv=%s, requirements=%s
                    WHERE user_id=%s
                    ''', (
                        r.get('program'),
                        ', '.join(r.get('hard_skills') or []) or None,
                        ', '.join(r.get('interests') or []) or None,
                        r.get('cv'),
                        r.get('preferences'),
                        user_id,
                    ),
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO student_profiles(user_id, program, skills, interests, cv, requirements)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (
                        user_id,
                        r.get('program'),
                        ', '.join(r.get('hard_skills') or []) or None,
                        ', '.join(r.get('interests') or []) or None,
                        r.get('cv'),
                        r.get('preferences'),
                    ),
                )
            upserted_profiles += 1

            topic = r.get('topic')
            if r.get('has_own_topic') and topic and (topic.get('title') or '').strip():
                title = topic.get('title').strip()
                cur.execute('SELECT 1 FROM topics WHERE author_user_id=%s AND title=%s', (user_id, title))
                if not cur.fetchone():
                    cur.execute(
                        '''
                        INSERT INTO topics(author_user_id, title, description, expected_outcomes,
                                           required_skills, seeking_role, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, 'supervisor', TRUE, now(), now())
                        ''', (
                            user_id,
                            title,
                            topic.get('description'),
                            topic.get('expected_outcomes'),
                            ', '.join(r.get('hard_skills') or []) or None,
                        ),
                    )
                    inserted_topics += 1

    return RedirectResponse(url=f'/?msg=Импорт: users+{inserted_users}, profiles~{upserted_profiles}, topics+{inserted_topics}&kind=topics', status_code=303)

@app.post('/add-supervisor')
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

    return RedirectResponse(url='/?msg=Научрук сохранён&kind=supervisors', status_code=303)

@app.post('/add-topic')
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
        
        # Конвертируем datetime объекты в строки для JSON сериализации
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