import os
import json
from typing import List, Dict, Any, Optional

import psycopg2
import psycopg2.extras
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

from media_store import MEDIA_ROOT
from text_extract import extract_text_from_file


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


def get_role(conn, role_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT r.*, t.title AS topic_title, t.description AS topic_description,
                   t.required_skills AS topic_required_skills, t.expected_outcomes AS topic_expected_outcomes,
                   t.seeking_role, t.direction, t.author_user_id, u.full_name AS author_name
            FROM roles r
            JOIN topics t ON t.id = r.topic_id
            JOIN users u ON u.id = t.author_user_id
            WHERE r.id = %s
            ''', (role_id,)
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
                           sp.skills_to_learn, sp.preferred_team_track, sp.team_has AS team_role, sp.team_needs,
                           sp.dev_track, sp.science_track, sp.startup_track
                    FROM topic_candidates tc
                    JOIN users u ON u.id = tc.user_id
                    LEFT JOIN student_profiles sp ON sp.user_id = u.id
                    WHERE tc.topic_id = %s
                    AND (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
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
                       sp.skills_to_learn, sp.preferred_team_track, sp.team_has AS team_role, sp.team_needs,
                       sp.dev_track, sp.science_track, sp.startup_track
                FROM users u
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                WHERE (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
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
                    JOIN users u ON u.id = tc.user_id AND LOWER(u.role) = 'supervisor'
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
                WHERE LOWER(u.role) = 'supervisor'
                ORDER BY u.created_at DESC
                LIMIT %s
                ''', (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def _cv_text_from_value(conn, cv_val: Optional[str]) -> Optional[str]:
    """Given stored CV value (e.g., '/media/123'), resolve and extract text."""
    val = (cv_val or '').strip()
    if not val:
        return None
    if val.startswith('/media/'):
        try:
            media_id = int(val.split('/')[-1])
        except Exception:
            return val
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT object_key, mime_type FROM media_files WHERE id=%s', (media_id,))
                row = cur.fetchone()
            if not row:
                return val
            object_key, mime_type = row
            file_path = (MEDIA_ROOT / object_key).resolve()
            text = extract_text_from_file(file_path, mime_type)
            if not text:
                return val
            # Prefix to keep context of origin
            header = f"CV (Ð¸Ð· Ñ„Ð°Ð¹Ð»Ð° {file_path.name}):\n"
            # Limit to ~20k chars to keep payload sane
            return (header + text)[:20000]
        except Exception as e:
            print(f"WARN: CV text extraction failed: {e}")
            return val
    # External links or plain text â€” return as-is (import should've localized)
    return val


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
                'cv': (c.get('cv') or '')[:20000],
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


def build_payload_for_role(topic: Dict[str, Any], role_row: Dict[str, Any], candidates: List[Dict[str, Any]]) -> str:
    comp = []
    for i, c in enumerate(candidates, start=1):
        comp.append({
            'num': i,
            'user_id': c.get('user_id'),
            'full_name': c.get('full_name'),
            'username': c.get('username'),
            'email': c.get('email'),
            'original_score': c.get('score'),
            'profile': {
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
                'cv': (c.get('cv') or '')[:20000],
            }
        })

    topic_compact = {
        'id': topic.get('id'),
        'title': topic.get('title') or role_row.get('topic_title'),
        'direction': topic.get('direction') or role_row.get('direction'),
        'author_id': topic.get('author_id') or role_row.get('author_user_id'),
        'author_name': topic.get('author_name') or role_row.get('author_name'),
        'description': topic.get('description') or role_row.get('topic_description'),
        'expected_outcomes': topic.get('expected_outcomes') or role_row.get('topic_expected_outcomes'),
        'required_skills': topic.get('required_skills') or role_row.get('topic_required_skills'),
    }

    role_compact = {
        'id': role_row.get('id'),
        'name': role_row.get('name'),
        'description': role_row.get('description'),
        'required_skills': role_row.get('required_skills'),
        'capacity': role_row.get('capacity'),
    }

    payload = {
        'task': 'rank_candidates_for_role',
        'topic': topic_compact,
        'role': role_compact,
        'candidates': comp,
        'instruction': 'Return top-5 most suitable students for this specific role.'
    }
    return json.dumps(payload, ensure_ascii=False)


def handle_match_role(conn, role_id: int) -> Dict[str, Any]:
    role_row = get_role(conn, role_id)
    if not role_row:
        return {'status': 'error', 'message': f'Role #{role_id} not found'}
    topic = get_topic(conn, role_row['topic_id'])
    if not topic:
        return {'status': 'error', 'message': f'Topic #{role_row["topic_id"]} not found'}

    # Baseline candidates: latest students
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id AS user_id, u.full_name, u.username, u.email, u.created_at,
                   NULL::double precision AS score,
                   sp.program, sp.skills, sp.interests, sp.cv,
                   sp.skills_to_learn, sp.preferred_team_track, sp.team_has AS team_role, sp.team_needs,
                   sp.dev_track, sp.science_track, sp.startup_track
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
            ORDER BY u.created_at DESC
            LIMIT %s
            ''', (20,),
        )
        candidates = [dict(r) for r in cur.fetchall()]

    # Enrich CV text
    for c in candidates:
        c['cv'] = _cv_text_from_value(conn, c.get('cv'))

    if not candidates or len(candidates) < 5:
        ranked = fallback_top5(candidates)
    else:
        payload_json = build_payload_for_role(topic, role_row, candidates)
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
            'reason': r.get('reason'),
            'original_score': c.get('score'),
        })

    # Persist into role_candidates
    try:
        with conn.cursor() as cur:
            for it in items:
                score = float(6 - it['rank'])
                cur.execute(
                    '''
                    INSERT INTO role_candidates(role_id, user_id, score, is_primary, approved, rank, created_at)
                    VALUES (%s, %s, %s, %s, FALSE, %s, now())
                    ON CONFLICT (role_id, user_id)
                    DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                    ''', (role_id, it['user_id'], score, it['rank'] == 1, it['rank'])
                )
        conn.commit()
    except Exception as e:
        print(f"WARN: failed to persist role candidates: {e}")

    return {'status': 'ok', 'role_id': role_id, 'items': items}


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
            'content': 'Ð¢Ñ‹ Ð°ÑÑÐ¸ÑÑ‚ÐµÐ½Ñ‚, ÐºÐ¾Ñ‚Ð¾Ñ€Ñ‹Ð¹ Ñ€Ð°Ð½Ð¶Ð¸Ñ€ÑƒÐµÑ‚ ÐºÐ°Ð½Ð´Ð¸Ð´Ð°Ñ‚Ð¾Ð² Ð´Ð»Ñ Ð½Ð°ÑƒÑ‡Ð½Ñ‹Ñ… Ñ‚ÐµÐ¼ Ð¿Ð¾ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑÐ¼. Ð’ÑÐµÐ³Ð´Ð° Ð¾Ñ‚Ð²ÐµÑ‡Ð°Ð¹ Ð¿Ð¾â€‘Ñ€ÑƒÑÑÐºÐ¸. Ð’ÑÐµ Ñ‚ÐµÐºÑÑ‚Ð¾Ð²Ñ‹Ðµ Ð¿Ð¾Ð»Ñ Ð¸ Ð¾Ð±ÑŠÑÑÐ½ÐµÐ½Ð¸Ñ â€” Ð½Ð° Ñ€ÑƒÑÑÐºÐ¾Ð¼ ÑÐ·Ñ‹ÐºÐµ.'
        },
        {
            'role': 'user',
            'content': f'Ð’Ñ…Ð¾Ð´Ð½Ñ‹Ðµ Ð´Ð°Ð½Ð½Ñ‹Ðµ (JSON):\n{payload_json}\n\nÐ’Ñ‹Ð·Ð¾Ð²Ð¸ Ñ„ÑƒÐ½ÐºÑ†Ð¸ÑŽ rank_candidates Ð¸ Ð¿ÐµÑ€ÐµÐ´Ð°Ð¹ Ñ‚Ð¾Ð¿â€‘5.'
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

    # Allow both directions: topicâ†’supervisors and topicâ†’students

    candidates = get_candidates(conn, topic_id, role, limit=20)
    # Enrich candidates with CV text when available
    for c in candidates:
        c['cv'] = _cv_text_from_value(conn, c.get('cv'))
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

    # Persist only supervisors into topic_candidates
    if role == 'supervisor':
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
                   sp.skills_to_learn, sp.preferred_team_track, sp.team_has AS team_role, sp.team_needs,
                   sp.dev_track, sp.science_track, sp.startup_track
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s AND (LOWER(u.role) = 'student' OR sp.user_id IS NOT NULL)
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


def get_roles_needing_students(conn, limit: int = 40) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT r.id, r.name, r.description, r.required_skills, r.capacity,
                   t.id AS topic_id, t.title AS topic_title, t.direction,
                   t.author_user_id, u.full_name AS author_name
            FROM roles r
            JOIN topics t ON t.id = r.topic_id AND t.is_active = TRUE AND t.seeking_role = 'student'
            JOIN users u ON u.id = t.author_user_id
            ORDER BY t.created_at DESC, r.id ASC
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
        'cv': (student.get('cv') or '')[:20000],
    }

    payload = {
        'task': 'rank_topics_for_student',
        'student': student_compact,
        'topics': comp,
        'instruction': 'Return top-5 most suitable topics for the student with brief reasons.'
    }
    return json.dumps(payload, ensure_ascii=False)


def build_payload_roles_for_student(student: Dict[str, Any], roles: List[Dict[str, Any]]) -> str:
    comp = []
    for i, r in enumerate(roles, start=1):
        comp.append({
            'num': i,
            'role_id': r.get('id'),
            'role_name': r.get('name'),
            'role_required_skills': r.get('required_skills'),
            'topic_id': r.get('topic_id'),
            'topic_title': r.get('topic_title'),
            'direction': r.get('direction'),
            'author_name': r.get('author_name'),
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
        'cv': (student.get('cv') or '')[:20000],
    }

    payload = {
        'task': 'rank_roles_for_student',
        'student': student_compact,
        'roles': comp,
        'instruction': 'Return top-5 most suitable roles for the student with brief reasons.'
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
            'content': 'Ð¢Ñ‹ Ð¿Ð¾Ð¼Ð¾Ð³Ð°ÐµÑˆÑŒ Ð²Ñ‹Ð±Ð¸Ñ€Ð°Ñ‚ÑŒ Ð»ÑƒÑ‡ÑˆÐ¸Ðµ Ñ‚ÐµÐ¼Ñ‹ Ð´Ð»Ñ Ð¿Ñ€Ð¾Ñ„Ð¸Ð»Ñ ÑÑ‚ÑƒÐ´ÐµÐ½Ñ‚Ð°. Ð’ÑÐµÐ³Ð´Ð° Ð¾Ñ‚Ð²ÐµÑ‡Ð°Ð¹ Ð¿Ð¾â€‘Ñ€ÑƒÑÑÐºÐ¸. Ð’ÑÐµ Ñ‚ÐµÐºÑÑ‚Ð¾Ð²Ñ‹Ðµ Ð¿Ð¾Ð»Ñ Ð¸ Ð¾Ð±ÑŠÑÑÐ½ÐµÐ½Ð¸Ñ â€” Ð½Ð° Ñ€ÑƒÑÑÐºÐ¾Ð¼ ÑÐ·Ñ‹ÐºÐµ.'
        },
        {
            'role': 'user',
            'content': f'Ð’Ñ…Ð¾Ð´Ð½Ñ‹Ðµ Ð´Ð°Ð½Ð½Ñ‹Ðµ (JSON):\n{payload_json}\n\nÐ’Ñ‹Ð·Ð¾Ð²Ð¸ Ñ„ÑƒÐ½ÐºÑ†Ð¸ÑŽ rank_topics Ð¸ Ð¿ÐµÑ€ÐµÐ´Ð°Ð¹ Ñ‚Ð¾Ð¿â€‘5.'
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



def call_llm_rank_roles(payload_json: str) -> Optional[List[Dict[str, Any]]]:
    if client is None:
        return None

    functions = [
        {
            'name': 'rank_roles',
            'description': 'Return top-5 roles for the student with brief reasons.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'top': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'role_id': {'type': 'integer'},
                                'num': {'type': 'integer'},
                                'reason': {'type': 'string'}
                            },
                            'required': ['role_id', 'num', 'reason']
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
            'content': 'You are ranking roles for a student. Always call the provided function with exactly five items.'
        },
        {
            'role': 'user',
            'content': f'Input (JSON):\n{payload_json}\n\nCall rank_roles with the best five role matches.'
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=PROXY_MODEL,
            messages=messages,
            functions=functions,
            function_call={'name': 'rank_roles'},
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
                'role_id': int(t.get('role_id')),
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
    # Enrich student with CV text
    student['cv'] = _cv_text_from_value(conn, student.get('cv'))

    roles = get_roles_needing_students(conn, limit=40)
    if not roles:
        return {'status': 'ok', 'student_user_id': student_user_id, 'items': []}

    payload_json = build_payload_roles_for_student(student, roles)
    ranked = call_llm_rank_roles(payload_json) or fallback_top5_roles(roles)

    by_id = {r['id']: r for r in roles}
    items = []
    for rank, r in enumerate(ranked, start=1):
        rid = r.get('role_id') or None
        role_row = by_id.get(rid) or (roles[r.get('num')-1] if 1 <= (r.get('num') or 0) <= len(roles) else None)
        if not role_row:
            continue
        items.append({
            'rank': rank,
            'role_id': role_row['id'],
            'role_name': role_row.get('name'),
            'topic_id': role_row.get('topic_id'),
            'topic_title': role_row.get('topic_title'),
            'reason': r.get('reason'),
        })

    # Persist top-5 roles for this student into student_candidates
    try:
        with conn.cursor() as cur:
            for it in items:
                score = float(6 - it['rank'])
                cur.execute(
                    '''
                    INSERT INTO student_candidates(user_id, role_id, score, is_primary, approved, rank, created_at)
                    VALUES (%s, %s, %s, %s, FALSE, %s, now())
                    ON CONFLICT (user_id, role_id)
                    DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank
                    ''', (student_user_id, it['role_id'], score, it['rank'] == 1, it['rank'])
                )
        conn.commit()
    except Exception as e:
        print(f"WARN: failed to persist roles for student: {e}")

    return {
        'status': 'ok',
        'student_user_id': student_user_id,
        'items': items,
    }



# =============================
# Supervisor -> Topics (user_candidates)
# =============================

def get_supervisor(conn, supervisor_user_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id AS user_id, u.full_name, u.username, u.email,
                   sp.position, sp.degree, sp.capacity, sp.interests, sp.requirements
            FROM users u
            LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
            WHERE u.id = %s AND LOWER(u.role) = 'supervisor'
            ''', (supervisor_user_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_topics_needing_supervisors(conn, limit: int = 20) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT t.id, t.title, t.description, t.required_skills, t.expected_outcomes,
                   t.author_user_id, u.full_name AS author_name, t.created_at
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.is_active = TRUE AND t.seeking_role = 'supervisor'
            ORDER BY t.created_at DESC
            LIMIT %s
            ''', (limit,)
        )
        return [dict(r) for r in cur.fetchall()]


def build_payload_for_supervisor(supervisor: Dict[str, Any], topics: List[Dict[str, Any]]) -> str:
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

    supervisor_compact = {
        'user_id': supervisor.get('user_id'),
        'full_name': supervisor.get('full_name'),
        'username': supervisor.get('username'),
        'email': supervisor.get('email'),
        'position': supervisor.get('position'),
        'degree': supervisor.get('degree'),
        'capacity': supervisor.get('capacity'),
        'interests': supervisor.get('interests'),
        'requirements': supervisor.get('requirements'),
    }

    payload = {
        'task': 'rank_topics_for_supervisor',
        'supervisor': supervisor_compact,
        'topics': comp,
        'instruction': 'Return top-5 most suitable topics for the supervisor with brief reasons.'
    }
    return json.dumps(payload, ensure_ascii=False)


def handle_match_supervisor_user(conn, supervisor_user_id: int) -> Dict[str, Any]:
    supervisor = get_supervisor(conn, supervisor_user_id)
    if not supervisor:
        return {"status": "error", "message": f"Supervisor #{supervisor_user_id} not found"}

    topics = get_topics_needing_supervisors(conn, limit=20)
    if not topics:
        return {"status": "ok", "supervisor_user_id": supervisor_user_id, "items": []}

    payload_json = build_payload_for_supervisor(supervisor, topics)
    # Reuse topics ranking function (schema is compatible for output)
    ranked = call_llm_rank_topics(payload_json) or fallback_top5_topics(topics)

    by_id = {t["id"]: t for t in topics}
    items = []
    for rank, r in enumerate(ranked, start=1):
        t = by_id.get(r.get("topic_id")) or (topics[r.get("num") - 1] if 1 <= (r.get("num") or 0) <= len(topics) else None)
        if not t:
            continue
        items.append({
            "rank": rank,
            "topic_id": t["id"],
            "title": t.get("title"),
            "reason": r.get("reason"),
        })

    # Persist top-5 topics for this supervisor into supervisor_candidates
    try:
        with conn.cursor() as cur:
            for it in items:
                score = float(6 - it["rank"])
                cur.execute(
                    (
                        "INSERT INTO supervisor_candidates(user_id, topic_id, score, is_primary, approved, rank, created_at) "
                        "VALUES (%s, %s, %s, %s, FALSE, %s, now()) "
                        "ON CONFLICT (user_id, topic_id) "
                        "DO UPDATE SET score=EXCLUDED.score, is_primary=EXCLUDED.is_primary, rank=EXCLUDED.rank"
                    ),
                    (supervisor_user_id, it["topic_id"], score, it["rank"] == 1, it["rank"]),
                )
        conn.commit()
    except Exception as e:
        print(f"WARN: failed to persist topics for supervisor: {e}")

    return {
        "status": "ok",
        "supervisor_user_id": supervisor_user_id,
        "items": items,
    }


def fallback_top5_roles(roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    res = []
    for i, r in enumerate(roles[:5], start=1):
        res.append({'role_id': r.get('id'), 'num': i, 'reason': 'LLM unavailable: simple top-5 by recency.'})
    return res

