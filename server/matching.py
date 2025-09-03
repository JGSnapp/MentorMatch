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
            # Try ranked candidates first
            try:
                cur.execute(
                    '''
                    SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                           tc.score,
                           sp.program, sp.skills, sp.interests, sp.cv,
                           sp.skills_to_learn, sp.preferred_team_track, sp.team_role, sp.team_needs,
                           sp.dev_track, sp.science_track, sp.startup_track
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
            except Exception as e:
                print(f"WARN: topic_candidates unavailable for students: {e}")

            # Fallback: latest students
            cur.execute(
                '''
                SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                       NULL::double precision AS score,
                       sp.program, sp.skills, sp.interests, sp.cv,
                       sp.skills_to_learn, sp.preferred_team_track, sp.team_role, sp.team_needs,
                       sp.dev_track, sp.science_track, sp.startup_track
                FROM users u
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE u.role = 'student'
                ORDER BY u.created_at DESC
                LIMIT %s
                ''', (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        else:
            # Supervisors
            try:
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
            except Exception as e:
                print(f"WARN: topic_candidates unavailable for supervisors: {e}")

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
                'skills_to_learn': c.get('skills_to_learn'),
                'preferred_team_track': c.get('preferred_team_track'),
                'team_role': c.get('team_role'),
                'team_needs': c.get('team_needs'),
                'dev_track': c.get('dev_track'),
                'science_track': c.get('science_track'),
                'startup_track': c.get('startup_track'),
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
        'instruction': 'Return top-5 most suitable candidates for the topic with brief reasons.'
    }
    return json.dumps(payload, ensure_ascii=False)


def call_llm_rank(payload_json: str) -> Optional[List[Dict[str, Any]]]:
    if client is None:
        return None

    functions = [
        {
            'name': 'rank_candidates',
            'description': 'Return top-5 candidates with brief reasons.',
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
        {
            'role': 'system',
            'content': 'Ты ассистент, который ранжирует кандидатов для научных тем по профилям. Всегда отвечай по‑русски. Все текстовые поля и объяснения — на русском языке.'
        },
        {
            'role': 'user',
            'content': f'Входные данные (JSON):\n{payload_json}\n\nВызови функцию rank_candidates и передай топ‑5.'
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=PROXY_MODEL,
            messages=messages,
            functions=functions,
            function_call={'name': 'rank_candidates'},
            temperature=0.2,
        )
    except Exception as e:
        print(f"LLM error: {e}")
        return None

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
            'reason': 'LLM unavailable: simple top-5 by recency.'
        })
    return res


def handle_match(conn, topic_id: int, target_role: Optional[str] = None) -> Dict[str, Any]:
    topic = get_topic(conn, topic_id)
    if not topic:
        return {'status': 'error', 'message': f'Topic #{topic_id} not found'}

    role = (target_role or topic.get('seeking_role') or 'student').lower()
    if role not in ('student', 'supervisor'):
        role = 'student'

    # New behavior: for topics that seek students, matching is inverted and
    # should be done via handle_match_student (by student id), not here.
    if role == 'student':
        return {
            'status': 'error',
            'message': 'This topic seeks students. Use /match-student with a student_user_id to get suitable topics.'
        }

    candidates = get_candidates(conn, topic_id, role, limit=20)
    if not candidates or len(candidates) < 5:
        ranked = fallback_top5(candidates)
    else:
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

    # Persist top-5 supervisor candidates into topic_candidates
    try:
        with conn.cursor() as cur:
            for it in items:
                score = float(6 - it['rank'])
                cur.execute(
                    '''
                    INSERT INTO topic_candidates(topic_id, user_id, score, is_primary, approved, rank, created_at)
                    VALUES (%s, %s, %s, %s, FALSE, %s, now())
                    ON CONFLICT (topic_id, user_id)
                    DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                    ''', (topic_id, it['user_id'], score, it['rank'] == 1, it['rank'])
                )
        conn.commit()
    except Exception as e:
        print(f"WARN: failed to persist supervisor candidates: {e}")

    return {
        'status': 'ok',
        'topic_id': topic_id,
        'target_role': role,
        'topic_title': topic.get('title'),
        'items': items,
    }


def get_student(conn, student_user_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id AS user_id, u.full_name, u.username, u.email,
                   sp.program, sp.skills, sp.interests, sp.cv,
                   sp.skills_to_learn, sp.preferred_team_track, sp.team_role, sp.team_needs,
                   sp.dev_track, sp.science_track, sp.startup_track
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s AND u.role = 'student'
            ''', (student_user_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_topics_needing_students(conn, limit: int = 20) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT t.id, t.title, t.description, t.required_skills, t.expected_outcomes,
                   t.author_user_id, u.full_name AS author_name, t.created_at
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.is_active = TRUE AND t.seeking_role = 'student'
            ORDER BY t.created_at DESC
            LIMIT %s
            ''', (limit,)
        )
        return [dict(r) for r in cur.fetchall()]


def build_payload_for_student(student: Dict[str, Any], topics: List[Dict[str, Any]]) -> str:
    comp = []
    for i, t in enumerate(topics, start=1):
        comp.append({
            'num': i,
            'topic_id': t.get('id'),
            'title': t.get('title'),
            'description': t.get('description'),
            'required_skills': t.get('required_skills'),
            'expected_outcomes': t.get('expected_outcomes'),
            'author_name': t.get('author_name'),
        })

    student_compact = {
        'user_id': student.get('user_id'),
        'full_name': student.get('full_name'),
        'username': student.get('username'),
        'email': student.get('email'),
        'program': student.get('program'),
        'skills': student.get('skills'),
        'interests': student.get('interests'),
        'skills_to_learn': student.get('skills_to_learn'),
        'preferred_team_track': student.get('preferred_team_track'),
        'team_role': student.get('team_role'),
        'team_needs': student.get('team_needs'),
        'dev_track': student.get('dev_track'),
        'science_track': student.get('science_track'),
        'startup_track': student.get('startup_track'),
        'cv': (student.get('cv') or '')[:2000],
    }

    payload = {
        'task': 'rank_topics_for_student',
        'student': student_compact,
        'topics': comp,
        'instruction': 'Return top-5 most suitable topics for the student with brief reasons.'
    }
    return json.dumps(payload, ensure_ascii=False)


def call_llm_rank_topics(payload_json: str) -> Optional[List[Dict[str, Any]]]:
    if client is None:
        return None

    functions = [
        {
            'name': 'rank_topics',
            'description': 'Return top-5 topics for the student with brief reasons.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'top': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'topic_id': {'type': 'integer'},
                                'num': {'type': 'integer'},
                                'reason': {'type': 'string'}
                            },
                            'required': ['topic_id', 'num', 'reason']
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
        {
            'role': 'system',
            'content': 'Ты помогаешь выбирать лучшие темы для профиля студента. Всегда отвечай по‑русски. Все текстовые поля и объяснения — на русском языке.'
        },
        {
            'role': 'user',
            'content': f'Входные данные (JSON):\n{payload_json}\n\nВызови функцию rank_topics и передай топ‑5.'
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=PROXY_MODEL,
            messages=messages,
            functions=functions,
            function_call={'name': 'rank_topics'},
            temperature=0.2,
        )
    except Exception as e:
        print(f"LLM error: {e}")
        return None

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
                'topic_id': int(t.get('topic_id')),
                'num': int(t.get('num')),
                'reason': str(t.get('reason') or ''),
            })
        return norm if len(norm) == 5 else None
    except Exception:
        return None


def fallback_top5_topics(topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    res = []
    for i, t in enumerate(topics[:5], start=1):
        res.append({
            'topic_id': t.get('id'),
            'num': i,
            'reason': 'LLM unavailable: simple top-5 by recency.'
        })
    return res


def handle_match_student(conn, student_user_id: int) -> Dict[str, Any]:
    student = get_student(conn, student_user_id)
    if not student:
        return {'status': 'error', 'message': f'Student #{student_user_id} not found'}

    topics = get_topics_needing_students(conn, limit=20)
    if not topics:
        return {'status': 'ok', 'student_user_id': student_user_id, 'items': []}

    payload_json = build_payload_for_student(student, topics)
    ranked = call_llm_rank_topics(payload_json) or fallback_top5_topics(topics)

    by_id = {t['id']: t for t in topics}
    items = []
    for rank, r in enumerate(ranked, start=1):
        t = by_id.get(r['topic_id']) or (topics[r['num']-1] if 1 <= r['num'] <= len(topics) else None)
        if not t:
            continue
        items.append({
            'rank': rank,
            'topic_id': t['id'],
            'title': t.get('title'),
            'reason': r.get('reason'),
        })

    # Persist top-5 topics for this student into topic_candidates
    try:
        with conn.cursor() as cur:
            for it in items:
                score = float(6 - it['rank'])
                cur.execute(
                    '''
                    INSERT INTO topic_candidates(topic_id, user_id, score, is_primary, approved, rank, created_at)
                    VALUES (%s, %s, %s, %s, FALSE, %s, now())
                    ON CONFLICT (topic_id, user_id)
                    DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                    ''', (it['topic_id'], student_user_id, score, it['rank'] == 1, it['rank'])
                )
        conn.commit()
    except Exception as e:
        print(f"WARN: failed to persist topics for student: {e}")

    return {
        'status': 'ok',
        'student_user_id': student_user_id,
        'items': items,
    }
