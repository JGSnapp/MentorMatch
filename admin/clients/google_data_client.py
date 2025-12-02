from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

GOOGLE_DATA_SERVICE_URL = os.getenv('GOOGLE_DATA_SERVICE_URL', 'http://google_data:8200')
logger = logging.getLogger(__name__)


def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Отправляет POST-запрос в сервис Google Data и возвращает ответ как JSON."""
    url = f"{GOOGLE_DATA_SERVICE_URL.rstrip('/')}{path}"
    try:
        response = httpx.post(url, json=payload, timeout=300)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.warning('Google Data service request %s failed: %s', url, exc)
        return {'status': 'error', 'message': str(exc)}


def import_students(spreadsheet_id: str, sheet_name: Optional[str] = None) -> Dict[str, Any]:
    """Просит сервис Google Data импортировать студентов из указанного листа."""
    payload: Dict[str, Any] = {'spreadsheet_id': spreadsheet_id}
    if sheet_name:
        payload['sheet_name'] = sheet_name
    return _post('/api/import/students', payload)


def import_supervisors(spreadsheet_id: str, sheet_name: Optional[str] = None) -> Dict[str, Any]:
    """Запускает импорт наставников из таблицы Google Sheet."""
    payload: Dict[str, Any] = {'spreadsheet_id': spreadsheet_id}
    if sheet_name:
        payload['sheet_name'] = sheet_name
    return _post('/api/import/supervisors', payload)


def sync_roles_sheet(*, spreadsheet_id: Optional[str] = None, service_account_file: Optional[str] = None) -> bool:
    """Экспортирует пары наставник-студент в Google Sheet и возвращает успешность."""
    payload: Dict[str, Any] = {}
    if spreadsheet_id:
        payload['spreadsheet_id'] = spreadsheet_id
    if service_account_file:
        payload['service_account_file'] = service_account_file
    result = _post('/api/export/pairs', payload)
    return result.get('status') == 'ok'


__all__ = ['import_students', 'import_supervisors', 'sync_roles_sheet']
