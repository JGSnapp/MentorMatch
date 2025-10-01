from __future__ import annotations

from typing import List, Optional, Sequence

import psycopg2.extras

from local_embeddings import (
    EMBEDDING_DIM,
    compute_embedding_from_parts as _compute_local_embedding,
    normalize_text_parts,
)


def compute_embedding_from_parts(parts: Sequence[Optional[str]]) -> Optional[List[float]]:
    return _compute_local_embedding(parts)


def refresh_user_embedding(conn, user_id: int) -> Optional[List[float]]:
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT u.full_name, u.email, u.username, u.role,
                       sp.program, sp.skills, sp.interests, sp.cv, sp.requirements,
                       sp.skills_to_learn, sp.achievements, sp.supervisor_pref, sp.groundwork,
                       sp.team_role, sp.team_has, sp.team_needs, sp.preferred_team_track,
                       sp.dev_track, sp.science_track, sp.startup_track, sp.final_work_pref,
                       sup.position, sup.degree, sup.capacity, sup.interests AS sup_interests,
                       sup.requirements AS sup_requirements
                FROM users u
                LEFT JOIN student_profiles sp ON sp.user_id = u.id
                LEFT JOIN supervisor_profiles sup ON sup.user_id = u.id
                WHERE u.id = %s
                ''',
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        role = (row.get('role') or '').strip().lower()
        base_parts: List[Optional[str]] = [row.get('full_name'), row.get('email'), row.get('username')]
        if role == 'student':
            parts = base_parts + [
                row.get('program'),
                row.get('skills'),
                row.get('interests'),
                row.get('skills_to_learn'),
                row.get('achievements'),
                row.get('requirements') or row.get('supervisor_pref'),
                row.get('groundwork'),
                row.get('team_role'),
                row.get('team_has'),
                row.get('team_needs'),
                row.get('preferred_team_track'),
                _truncate(row.get('cv')),
                _format_track_scores(row.get('dev_track'), row.get('science_track'), row.get('startup_track')),
                row.get('final_work_pref'),
            ]
        else:
            parts = base_parts + [
                row.get('position'),
                row.get('degree'),
                str(row.get('capacity') or ''),
                row.get('sup_interests'),
                row.get('sup_requirements'),
            ]
        vector = compute_embedding_from_parts(parts)
        if vector is None and normalize_text_parts(parts) == '':
            with conn.cursor() as cur:
                cur.execute('UPDATE users SET embeddings=NULL WHERE id=%s', (user_id,))
            return None
        if vector is None:
            return None
        with conn.cursor() as cur:
            cur.execute('UPDATE users SET embeddings=%s WHERE id=%s', (vector, user_id))
        return vector
    except Exception as exc:
        try:
            print(f"WARN: refresh_user_embedding failed for user {user_id}: {exc}")
        except Exception:
            pass
        return None


def refresh_topic_embedding(conn, topic_id: int) -> Optional[List[float]]:
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT title, description, expected_outcomes, required_skills
                FROM topics
                WHERE id = %s
                ''',
                (topic_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        parts = [row.get('title'), row.get('description'), row.get('expected_outcomes'), row.get('required_skills')]
        vector = compute_embedding_from_parts(parts)
        if vector is None and normalize_text_parts(parts) == '':
            with conn.cursor() as cur:
                cur.execute('UPDATE topics SET embeddings=NULL WHERE id=%s', (topic_id,))
            return None
        if vector is None:
            return None
        with conn.cursor() as cur:
            cur.execute('UPDATE topics SET embeddings=%s WHERE id=%s', (vector, topic_id))
        return vector
    except Exception as exc:
        try:
            print(f"WARN: refresh_topic_embedding failed for topic {topic_id}: {exc}")
        except Exception:
            pass
        return None


def refresh_role_embedding(conn, role_id: int) -> Optional[List[float]]:
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT r.name, r.description, r.required_skills, r.capacity,
                       t.title AS topic_title, t.description AS topic_description,
                       t.expected_outcomes AS topic_expected_outcomes,
                       t.required_skills AS topic_required_skills
                FROM roles r
                JOIN topics t ON t.id = r.topic_id
                WHERE r.id = %s
                ''',
                (role_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        parts = [
            row.get('name'),
            row.get('description'),
            row.get('required_skills'),
            str(row.get('capacity') or ''),
            row.get('topic_title'),
            row.get('topic_description'),
            row.get('topic_expected_outcomes'),
            row.get('topic_required_skills'),
        ]
        vector = compute_embedding_from_parts(parts)
        if vector is None and normalize_text_parts(parts) == '':
            with conn.cursor() as cur:
                cur.execute('UPDATE roles SET embeddings=NULL WHERE id=%s', (role_id,))
            return None
        if vector is None:
            return None
        with conn.cursor() as cur:
            cur.execute('UPDATE roles SET embeddings=%s WHERE id=%s', (vector, role_id))
        return vector
    except Exception as exc:
        try:
            print(f"WARN: refresh_role_embedding failed for role {role_id}: {exc}")
        except Exception:
            pass
        return None


def _truncate(value: Optional[str], limit: int = 2000) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _format_track_scores(dev: Optional[int], science: Optional[int], startup: Optional[int]) -> Optional[str]:
    scores = []
    if dev is not None:
        scores.append(f"dev_track:{dev}")
    if science is not None:
        scores.append(f"science_track:{science}")
    if startup is not None:
        scores.append(f"startup_track:{startup}")
    return ', '.join(scores) if scores else None


__all__ = [
    'EMBEDDING_DIM',
    'compute_embedding_from_parts',
    'refresh_topic_embedding',
    'refresh_user_embedding',
    'refresh_role_embedding',
]
