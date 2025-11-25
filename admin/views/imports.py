from __future__ import annotations

import os
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..clients.google_data_client import import_students, import_supervisors, sync_roles_sheet
from ..context import AdminContext


def register(router: APIRouter, ctx: AdminContext) -> None:
    """Добавляет административные маршруты для импорта данных из таблиц."""
    @router.get('/import-sheet')
    def import_sheet(request: Request, target: Optional[str] = None, sheet_name: Optional[str] = None):
        """Запускает импорт студентов или наставников и перенаправляет с результатом."""
        desired = (target or 'students').strip().lower()
        if desired == 'supervisors':
            spreadsheet_id = (os.getenv('SUPERVISOR_SPREADSHEET_ID') or '').strip()
            env_name = 'SUPERVISOR_SPREADSHEET_ID'
            tab = 'supervisors'
        else:
            spreadsheet_id = (os.getenv('STUDENT_SPREADSHEET_ID') or '').strip()
            env_name = 'STUDENT_SPREADSHEET_ID'
            tab = 'students'

        if not spreadsheet_id:
            notice = urllib.parse.quote(f'Не указан идентификатор таблицы {env_name}')
            return RedirectResponse(url=f'/?tab={tab}&msg={notice}', status_code=303)

        try:
            if desired == 'supervisors':
                result = import_supervisors(spreadsheet_id, sheet_name)
            else:
                result = import_students(spreadsheet_id, sheet_name)
        except Exception as exc:                                       
            detail = urllib.parse.quote(f'Ошибка импорта: {type(exc).__name__}: {exc}')
            return RedirectResponse(url=f'/?tab={tab}&msg={detail}', status_code=303)

        sync_roles_sheet()
        message = result.get('message') or 'Импорт завершён'
        quoted = urllib.parse.quote(message)
        return RedirectResponse(url=f'/?tab={tab}&msg={quoted}', status_code=303)
