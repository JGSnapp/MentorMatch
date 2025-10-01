from __future__ import annotations

import urllib.parse
from typing import Optional

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from utils import parse_optional_int

from ..context import AdminContext


def register(router: APIRouter, ctx: AdminContext) -> None:
    @router.post('/send-request')
    def send_request(
        sender_user_id: int = Form(...),
        receiver_user_id: int = Form(...),
        topic_id: int = Form(...),
        body: str = Form(...),
        role_id: Optional[str] = Form(None),
        return_url: str = Form('/'),
    ):
        def _redirect(target: Optional[str], message: str) -> RedirectResponse:
            base = (target or '/').strip() or '/'
            anchor = ''
            if '#' in base:
                base, anchor = base.split('#', 1)
                anchor = f'#{anchor}'
            sep = '&' if '?' in base else '?'
            quoted = urllib.parse.quote(message)
            return RedirectResponse(url=f'{base}{sep}msg={quoted}{anchor}', status_code=303)

        text = (body or '').strip()
        if not text:
            return _redirect(return_url, 'Текст заявки не может быть пустым')

        try:
            with ctx.get_conn() as conn, conn.cursor() as cur:
                role_id_val = parse_optional_int(role_id)
                topic_id_int = int(topic_id)
                cur.execute('SELECT role FROM users WHERE id=%s', (sender_user_id,))
                sender_row = cur.fetchone()
                sender_role = (sender_row[0] or '').strip().lower() if sender_row else None
                if not sender_role:
                    return _redirect(return_url, 'Не удалось определить роль отправителя заявки')
                if sender_role == 'student' and role_id_val is None:
                    return _redirect(return_url, 'Студент должен выбрать конкретную роль для заявки')
                if role_id_val is not None:
                    cur.execute('SELECT 1 FROM roles WHERE id=%s AND topic_id=%s', (role_id_val, topic_id_int))
                    if not cur.fetchone():
                        return _redirect(return_url, 'Роль не принадлежит выбранной теме')
                cur.execute(
                    '''
                    INSERT INTO messages(sender_user_id, receiver_user_id, topic_id, role_id, body, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, 'pending', now())
                    RETURNING id
                    ''',
                    (sender_user_id, receiver_user_id, topic_id_int, role_id_val, text),
                )
                msg_id = cur.fetchone()[0]
                conn.commit()
            return _redirect(return_url, f'Заявка отправлена (#{msg_id})')
        except Exception as exc:  # pragma: no cover
            return _redirect(return_url, f'Ошибка отправки заявки: {type(exc).__name__}')
