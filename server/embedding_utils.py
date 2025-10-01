import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

PROXY_API_KEY = os.getenv('PROXY_API_KEY')
PROXY_BASE_URL = os.getenv('PROXY_BASE_URL')
EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small')
try:
    EMBEDDING_DIM = int(os.getenv('EMBEDDING_DIM', '1536'))
except ValueError:
    EMBEDDING_DIM = 1536
MAX_EMBED_TEXT = int(os.getenv('EMBEDDING_TEXT_LIMIT', '6000'))

_client: Optional[OpenAI] = None
if PROXY_API_KEY:
    try:
        if PROXY_BASE_URL:
            _client = OpenAI(api_key=PROXY_API_KEY, base_url=PROXY_BASE_URL)
        else:
            _client = OpenAI(api_key=PROXY_API_KEY)
    except Exception as exc:  # pragma: no cover - best effort logging
        print(f"WARN: failed to init embedding client: {exc}")
        _client = None

_WARNED: Set[str] = set()


def _log_once(key: str, message: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        print(message)
    except Exception:
        pass


def get_embedding_client() -> Optional[OpenAI]:
    return _client


def _combine_parts(parts: Iterable[Optional[str]]) -> Optional[str]:
    lines: List[str] = []
    for part in parts:
        if not part:
            continue
        text = str(part).strip()
        if not text:
            continue
        lines.append(text)
    if not lines:
        return None
    combined = '\n'.join(lines)
    if len(combined) > MAX_EMBED_TEXT:
        return combined[:MAX_EMBED_TEXT]
    return combined


def _format_vector(values: Sequence[float]) -> str:
    return '[' + ','.join(f"{v:.8f}" for v in values) + ']'


def _safe_execute(conn, sql: str, params: Sequence[Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def _compute_embedding(text: str) -> Optional[List[float]]:
    client = get_embedding_client()
    if client is None:
        return None
    clean = (text or '').strip()
    if not clean:
        return None
    try:
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=clean)
    except Exception as exc:
        _log_once('embedding_error', f"WARN: embedding generation failed: {exc}")
        return None
    if not resp.data:
        return None
    vec = resp.data[0].embedding
    if not isinstance(vec, list):
        try:
            vec = list(vec)
        except Exception:
            return None
    if EMBEDDING_DIM and len(vec) != EMBEDDING_DIM:
        # Allow mismatched dimensions but warn once
        _log_once(
            'embedding_dim',
            f"WARN: embedding dimension {len(vec)} differs from expected {EMBEDDING_DIM}",
        )
    return vec


def refresh_user_embedding(conn, user_id: int) -> None:
    if get_embedding_client() is None:
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT u.id, u.full_name, u.role, u.email, u.username,
                   sp.program, sp.skills, sp.interests, sp.skills_to_learn,
                   sp.achievements, sp.requirements AS student_requirements,
                   sp.team_role, sp.team_has, sp.team_needs, sp.workplace,
                   sup.position, sup.degree, sup.capacity, sup.interests AS supervisor_interests,
                   sup.requirements AS supervisor_requirements
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
            WHERE u.id = %s
            ''',
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return
    role = (row.get('role') or '').strip().lower()
    common = [
        f"Full name: {row.get('full_name') or ''}",
        f"Role: {role}",
        f"Email: {row.get('email') or ''}",
        f"Telegram: {row.get('username') or ''}",
    ]
    text_parts: List[Optional[str]] = ['\n'.join(common)]
    if role == 'student':
        text_parts.extend(
            [
                f"Program: {row.get('program') or ''}",
                f"Skills: {row.get('skills') or ''}",
                f"Interests: {row.get('interests') or ''}",
                f"Wants to learn: {row.get('skills_to_learn') or ''}",
                f"Achievements: {row.get('achievements') or ''}",
                f"Preferred supervisor: {row.get('student_requirements') or ''}",
                f"Team role: {row.get('team_role') or ''}",
                f"Team already has: {row.get('team_has') or ''}",
                f"Team needs: {row.get('team_needs') or ''}",
                f"Workplace: {row.get('workplace') or ''}",
            ]
        )
    elif role == 'supervisor':
        text_parts.extend(
            [
                f"Position: {row.get('position') or ''}",
                f"Degree: {row.get('degree') or ''}",
                f"Capacity: {row.get('capacity') or ''}",
                f"Interests: {row.get('supervisor_interests') or ''}",
                f"Requirements: {row.get('supervisor_requirements') or ''}",
            ]
        )
    text = _combine_parts(text_parts)
    if text is None:
        _safe_execute(conn, 'UPDATE users SET embeddings=NULL WHERE id=%s', (user_id,))
        return
    vector = _compute_embedding(text)
    if not vector:
        return
    _safe_execute(conn, 'UPDATE users SET embeddings=%s WHERE id=%s', (_format_vector(vector), user_id))


def refresh_topic_embedding(conn, topic_id: int, *, cascade_roles: bool = False) -> None:
    if get_embedding_client() is None:
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT t.id, t.title, t.description, t.expected_outcomes, t.required_skills,
                   t.direction, t.seeking_role, u.full_name AS author_name
            FROM topics t
            JOIN users u ON u.id = t.author_user_id
            WHERE t.id = %s
            ''',
            (topic_id,),
        )
        topic = cur.fetchone()
    if not topic:
        return
    text = _combine_parts(
        [
            f"Topic title: {topic.get('title') or ''}",
            f"Direction: {topic.get('direction') or ''}",
            f"Target role: {topic.get('seeking_role') or ''}",
            f"Author: {topic.get('author_name') or ''}",
            f"Description: {topic.get('description') or ''}",
            f"Expected outcomes: {topic.get('expected_outcomes') or ''}",
            f"Required skills: {topic.get('required_skills') or ''}",
        ]
    )
    if text is None:
        _safe_execute(conn, 'UPDATE topics SET embeddings=NULL WHERE id=%s', (topic_id,))
    else:
        vector = _compute_embedding(text)
        if vector:
            _safe_execute(conn, 'UPDATE topics SET embeddings=%s WHERE id=%s', (_format_vector(vector), topic_id))
    if cascade_roles:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM roles WHERE topic_id=%s', (topic_id,))
            role_ids = [rid for (rid,) in cur.fetchall()]
        for rid in role_ids:
            refresh_role_embedding(conn, rid)


def refresh_role_embedding(conn, role_id: int) -> None:
    if get_embedding_client() is None:
        return
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            SELECT r.id, r.name, r.description, r.required_skills, r.capacity,
                   t.title AS topic_title, t.description AS topic_description,
                   t.required_skills AS topic_required_skills,
                   t.expected_outcomes AS topic_expected_outcomes,
                   t.direction AS topic_direction,
                   u.full_name AS author_name
            FROM roles r
            JOIN topics t ON t.id = r.topic_id
            JOIN users u ON u.id = t.author_user_id
            WHERE r.id = %s
            ''',
            (role_id,),
        )
        role = cur.fetchone()
    if not role:
        return
    text = _combine_parts(
        [
            f"Role name: {role.get('name') or ''}",
            f"Capacity: {role.get('capacity') or ''}",
            f"Role description: {role.get('description') or ''}",
            f"Role required skills: {role.get('required_skills') or ''}",
            f"Topic title: {role.get('topic_title') or ''}",
            f"Topic author: {role.get('author_name') or ''}",
            f"Topic direction: {role.get('topic_direction') or ''}",
            f"Topic description: {role.get('topic_description') or ''}",
            f"Topic expected outcomes: {role.get('topic_expected_outcomes') or ''}",
            f"Topic required skills: {role.get('topic_required_skills') or ''}",
        ]
    )
    if text is None:
        _safe_execute(conn, 'UPDATE roles SET embeddings=NULL WHERE id=%s', (role_id,))
        return
    vector = _compute_embedding(text)
    if not vector:
        return
    _safe_execute(conn, 'UPDATE roles SET embeddings=%s WHERE id=%s', (_format_vector(vector), role_id))
