from __future__ import annotations
import logging
import os
from typing import Any, Callable, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from utils import resolve_service_account_path


HEADERS_RU = ['Тема', 'Роль', 'Студент', 'Руководитель']


def _open_ws(spreadsheet_id: str, service_account_file: str):
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_file(str(service_account_file), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.sheet1
    except Exception:
        ws = sh.worksheets()[0]
    return ws


def export_pairs_from_db(conn, spreadsheet_id: str, service_account_file: str) -> int:
    """Build pairs from DB and write to the Google Sheet.
    Returns number of data rows written (excluding header).
    """
    rows: List[List[str]] = [HEADERS_RU]
    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT t.title AS topic_title,
                   r.name AS role_name,
                   stu.full_name AS student_name,
                   sup.full_name AS supervisor_name
            FROM roles r
            JOIN topics t ON t.id = r.topic_id
            LEFT JOIN users stu ON stu.id = r.approved_student_user_id
            LEFT JOIN users sup ON sup.id = t.approved_supervisor_user_id
            ORDER BY t.created_at DESC, r.id ASC
            '''
        )
        for topic_title, role_name, student_name, supervisor_name in cur.fetchall():
            rows.append([
                topic_title or '',
                role_name or '',
                (student_name or ''),
                (supervisor_name or ''),
            ])

    ws = _open_ws(spreadsheet_id, service_account_file)
    # Clear and write
    ws.clear()
    ws.update('A1', rows)
    return max(0, len(rows) - 1)


logger = logging.getLogger(__name__)


def sync_roles_sheet(
    get_conn: Callable[[], Any],
    spreadsheet_id: Optional[str] = None,
    service_account_file: Optional[str] = None,
    *,
    conn=None,
) -> bool:
    """Export the latest roles data to Google Sheets.

    Returns ``True`` when an export was triggered. Missing configuration or
    errors are treated as a soft failure and reported via logging while
    returning ``False`` to the caller.
    """

    sid = (spreadsheet_id or os.getenv('PAIRS_SPREADSHEET_ID') or '').strip()
    if not sid:
        logger.debug('Skipping roles sheet sync: spreadsheet ID is not configured')
        return False
    service_account_path = resolve_service_account_path(
        service_account_file or os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
    )
    if not service_account_path:
        logger.debug('Skipping roles sheet sync: service account file is not configured')
        return False
    try:
        if conn is not None:
            export_pairs_from_db(conn, sid, service_account_path)
        else:
            with get_conn() as fresh_conn:
                export_pairs_from_db(fresh_conn, sid, service_account_path)
        return True
    except Exception as exc:  # pragma: no cover - best effort logging
        logger.warning('Не удалось обновить Google Sheet с ролями: %s', exc)
        return False

