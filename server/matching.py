import os
import json
from typing import List, Dict, Any, Optional
import psycopg2
import psycopg2.extras
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

PROXY_API_KEY = os.getenv('PROXY_API_KEY')
PROXY_BASE_URL = os.getenv('PROXY_BASE_URL')
PROXY_MODEL = os.getenv('PROXY_MODEL', 'gpt-4o-mini')

client: Optional[OpenAI] = None
if PROXY_API_KEY and PROXY_BASE_URL:
    client = OpenAI(api_key=PROXY_API_KEY, base_url=PROXY_BASE_URL)

def get_topic(conn, topic_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT t.*, u.full_name AS author_name, u.id AS author_id
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.id = %s
            ''', (topic_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

def get_candidates(conn, topic_id: int, target_role: str, limit: int = 20) -> List[Dict[str, Any]]:
    role = (target_role or 'student').lower()
    role = role if role in ('student', 'supervisor') else 'student'

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if role == 'student':
            cur.execute(
                '''
                SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                       tc.score,
                       sp.program, sp.skills, sp.interests, sp.cv
                FROM topic_candidates tc
                JOIN users u ON u.id = tc.user_id AND u.role = 'student'
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE tc.topic_id = %s
                ORDER BY tc.score DESC NULLS LAST, u.created_at DESC
                LIMIT %s
                ''', (topic_id, limit),
            )
            rows = cur.fetchall()
            if rows:
                return [dict(r) for r in rows]

            cur.execute(
                '''
                SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                       NULL::double precision AS score,
                       sp.program, sp.skills, sp.interests, sp.cv
                FROM users u
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE u.role = 'student'
                ORDER BY u.created_at DESC
                LIMIT %s
                ''', (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        else:
            cur.execute(
                '''
                SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                       tc.score,
                       sp.position, sp.degree, sp.capacity, sp.interests
                FROM topic_candidates tc
                JOIN users u ON u.id = tc.user_id AND u.role = 'supervisor'
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE tc.topic_id = %s
                ORDER BY tc.score DESC NULLS LAST, u.created_at DESC
                LIMIT %s
                ''', (topic_id, limit),
            )
            rows = cur.fetchall()
            if rows:
                return [dict(r) for r in rows]

            cur.execute(
                '''
                SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                       NULL::double precision AS score,
                       sp.position, sp.degree, sp.capacity, sp.interests
                FROM users u
                LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
                WHERE u.role = 'supervisor'
                ORDER BY u.created_at DESC
                LIMIT %s
                ''', (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

def build_payload(topic: Dict[str, Any], candidates: List[Dict[str, Any]], role: str) -> str:
    comp = []
    for i, c in enumerate(candidates, start=1):
        entry = {
            'num': i,
            'user_id': c.get('user_id'),
            'full_name': c.get('full_name'),
            'username': c.get('username'),
            'email': c.get('email'),
            'original_score': c.get('score'),
            'profile': {},
        }
        if role == 'student':
            entry['profile'] = {
                'program': c.get('program'),
                'skills': c.get('skills'),
                'interests': c.get('interests'),
                'cv': (c.get('cv') or '')[:2000],
            }
        else:
            entry['profile'] = {
                'position': c.get('position'),
                'degree': c.get('degree'),
                'capacity': c.get('capacity'),
                'interests': c.get('interests'),
            }
        comp.append(entry)

    topic_compact = {
        'id': topic.get('id'),
        'title': topic.get('title'),
        'author_id': topic.get('author_id'),
        'author_name': topic.get('author_name'),
        'seeking_role': topic.get('seeking_role'),
        'description': topic.get('description'),
        'expected_outcomes': topic.get('expected_outcomes'),
        'required_skills': topic.get('required_skills'),
    }

    payload = {
        'task': 'rank_candidates_for_topic',
        'target_role': role,
        'topic': topic_compact,
        'candidates': comp,
        'instruction': 'Выбери 5 лучших кандидатов по соответствию теме и верни ровно 5 элементов через функцию rank_candidates.'
    }
    return json.dumps(payload, ensure_ascii=False)

def call_llm_rank(payload_json: str) -> Optional[List[Dict[str, Any]]]:
    if client is None:
        return None

    functions = [
        {
            'name': 'rank_candidates',
            'description': 'Верни 5 лучших кандидатов с объяснениями',
            'parameters': {
                'type': 'object',
                'properties': {
                    'top': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'user_id': {'type': 'integer'},
                                'num': {'type': 'integer'},
                                'reason': {'type': 'string'}
                            },
                            'required': ['user_id', 'num', 'reason']
                        },
                        'minItems': 5,
                        'maxItems': 5
                    }
                },
                'required': ['top']
            }
        }
    ]

    messages = [
        {'role': 'system', 'content': 'Ты ассистент по подбору студентов/научруков под тему ВКР. Отвечай строго структурированно.'},
        {'role': 'user', 'content': f'Данные (JSON):\n{payload_json}\n\nВерни результат через функцию rank_candidates.'}
    ]

    resp = client.chat.completions.create(
        model=PROXY_MODEL,
        messages=messages,
        functions=functions,
        function_call={'name': 'rank_candidates'},
        temperature=0.2,
    )

    if not resp.choices or not resp.choices[0].message:
        return None
    msg = resp.choices[0].message
    fc = getattr(msg, 'function_call', None)
    if not fc or not getattr(fc, 'arguments', None):
        return None

    try:
        parsed = json.loads(fc.arguments)
        top = parsed.get('top', [])
        norm = []
        for t in top[:5]:
            norm.append({
                'user_id': int(t.get('user_id')),
                'num': int(t.get('num')),
                'reason': str(t.get('reason') or ''),
            })
        return norm if len(norm) == 5 else None
    except Exception:
        return None

def fallback_top5(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    res = []
    for i, c in enumerate(candidates[:5], start=1):
        res.append({
            'user_id': c.get('user_id'),
            'num': i,
            'reason': 'LLM недоступен: выбрано по базовому скорингу/дате.'
        })
    return res

def handle_match(conn, topic_id: int, target_role: Optional[str] = None) -> Dict[str, Any]:
    topic = get_topic(conn, topic_id)
    if not topic:
        return {'status': 'error', 'message': f'Тема #{topic_id} не найдена'}

    role = (target_role or topic.get('seeking_role') or 'student').lower()
    if role not in ('student', 'supervisor'):
        role = 'student'

    candidates = get_candidates(conn, topic_id, role, limit=20)
    payload_json = build_payload(topic, candidates, role)
    ranked = call_llm_rank(payload_json) or fallback_top5(candidates)

    by_id = {c['user_id']: c for c in candidates}
    items = []
    for rank, r in enumerate(ranked, start=1):
        c = by_id.get(r['user_id']) or (candidates[r['num']-1] if 1 <= r['num'] <= len(candidates) else None)
        if not c:
            continue
        items.append({
            'rank': rank,
            'user_id': c['user_id'],
            'full_name': c.get('full_name'),
            'role': role,
            'reason': r.get('reason'),
            'original_score': c.get('score'),
        })

    return {
        'status': 'ok',
        'topic_id': topic_id,
        'target_role': role,
        'topic_title': topic.get('title'),
        'items': items,
    }