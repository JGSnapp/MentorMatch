from __future__ import annotations

import urllib.parse
from typing import Optional

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from ..context import AdminContext


def register(router: APIRouter, ctx: AdminContext) -> None:
    @router.post('/do-match-role')
    def do_match_role(role_id: int = Form(...)):
        try:
            from matching import handle_match_role
            with ctx.get_conn() as conn:
                handle_match_role(conn, role_id=role_id)
            return RedirectResponse(url=f'/role/{role_id}', status_code=303)
        except Exception as exc:  # pragma: no cover
            notice = urllib.parse.quote(f'Ошибка подбора: {type(exc).__name__}')
            return RedirectResponse(url=f'/role/{role_id}?msg={notice}', status_code=303)

    @router.post('/do-match-topic')
    def do_match_topic(
        topic_id: int = Form(...),
        target_role: Optional[str] = Form(None),
    ):
        try:
            from matching import handle_match
            with ctx.get_conn() as conn:
                handle_match(conn, topic_id=topic_id, target_role=target_role)
            return RedirectResponse(url=f'/topic/{topic_id}', status_code=303)
        except Exception as exc:  # pragma: no cover
            notice = urllib.parse.quote(f'Ошибка подбора: {type(exc).__name__}')
            return RedirectResponse(url=f'/topic/{topic_id}?msg={notice}', status_code=303)

    @router.post('/do-match-student')
    def do_match_student(student_user_id: int = Form(...)):
        try:
            from matching import handle_match_student
            with ctx.get_conn() as conn:
                handle_match_student(conn, student_user_id=student_user_id)
            return RedirectResponse(url=f'/user/{student_user_id}', status_code=303)
        except Exception as exc:  # pragma: no cover
            notice = urllib.parse.quote(f'Ошибка подбора: {type(exc).__name__}')
            return RedirectResponse(url=f'/user/{student_user_id}?msg={notice}', status_code=303)

    @router.post('/do-match-supervisor')
    def do_match_supervisor(supervisor_user_id: int = Form(...)):
        try:
            from matching import handle_match_supervisor_user
            with ctx.get_conn() as conn:
                handle_match_supervisor_user(conn, supervisor_user_id=supervisor_user_id)
            return RedirectResponse(url=f'/supervisor/{supervisor_user_id}', status_code=303)
        except Exception as exc:  # pragma: no cover
            notice = urllib.parse.quote(f'Ошибка подбора: {type(exc).__name__}')
            return RedirectResponse(url=f'/supervisor/{supervisor_user_id}?msg={notice}', status_code=303)
