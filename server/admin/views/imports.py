from __future__ import annotations

import os
import urllib.parse
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from parse_gform import fetch_normalized_rows
from sheet_pairs import sync_roles_sheet

from ..context import AdminContext
from ..utils import normalize_telegram_link, process_cv


def register(router: APIRouter, ctx: AdminContext) -> None:
    templates = ctx.templates

    @router.get('/import-sheet', response_class=HTMLResponse)
    def import_form(request: Request, msg: Optional[str] = None, target: Optional[str] = None):
        return templates.TemplateResponse(
            'admin/import_form.html',
            {
                'request': request,
                'msg': msg,
                'spreadsheet_id': os.getenv('SPREADSHEET_ID', ''),
                'target': target or 'students',
            },
        )

    @router.post('/import-sheet')
    def import_sheet(
        request: Request,
        spreadsheet_id: str = Form(...),
        sheet_name: Optional[str] = Form(None),
    ):
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

            with ctx.get_conn() as conn, conn.cursor() as cur:
                for r in rows:
                    full_name = (r.get('full_name') or '').strip()
                    email = (r.get('email') or '').strip()
                    if not (full_name or email):
                        continue

                    if email:
                        cur.execute(
                            "SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND role='student' LIMIT 1",
                            (email,),
                        )
                    else:
                        cur.execute(
                            "SELECT id FROM users WHERE full_name=%s AND role='student' LIMIT 1",
                            (full_name,),
                        )
                    row = cur.fetchone()
                    if row:
                        user_id = row[0]
                    else:
                        cur.execute(
                            '''
                            INSERT INTO users(full_name, email, role, created_at, updated_at)
                            VALUES (%s, %s, 'student', now(), now())
                            RETURNING id
                            ''',
                            (full_name, (email or None)),
                        )
                        user_id = cur.fetchone()[0]
                        inserted_users += 1

                    updates: List[str] = []
                    params: List[Any] = []
                    telegram = normalize_telegram_link(r.get('telegram')) if r.get('telegram') else None
                    if telegram:
                        updates.append('username=%s')
                        params.append(telegram)
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

                    cur.execute('SELECT 1 FROM student_profiles WHERE user_id=%s', (user_id,))
                    exists = cur.fetchone() is not None
                    skills_have = ', '.join(r.get('hard_skills_have') or []) or None
                    skills_want = ', '.join(r.get('hard_skills_want') or []) or None
                    interests = ', '.join(r.get('interests') or []) or None
                    requirements = r.get('supervisor_preference')

                    cv_value = process_cv(conn, user_id, r.get('cv'))

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
                            ''',
                            (
                                r.get('program'),
                                skills_have,
                                interests,
                                cv_value,
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
                            ''',
                            (
                                user_id,
                                r.get('program'),
                                skills_have,
                                interests,
                                cv_value,
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
                                ''',
                                (
                                    user_id,
                                    title,
                                    desc,
                                    topic.get('expected_outcomes'),
                                    skills_have,
                                ),
                            )
                            inserted_topics += 1

            sync_roles_sheet(ctx.get_conn)
            message = f"Импорт: users+{inserted_users}, profiles~{upserted_profiles}, topics+{inserted_topics}"
            notice = urllib.parse.quote(message)
            return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
        except Exception as exc:
            detail = f"Ошибка импорта: {type(exc).__name__}: {exc}" if str(exc) else f"Ошибка импорта: {type(exc).__name__}"
            notice = urllib.parse.quote(detail)
            return RedirectResponse(url=f'/?tab=topics&msg={notice}', status_code=303)
