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
    samples: List[List[str]] = []
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
            row = [
                topic_title or '',
                role_name or '',
                student_name or '',
                supervisor_name or '',
            ]
            rows.append(row)
            if len(samples) < 5:
                samples.append(row)

    data_rows = max(0, len(rows) - 1)
    logger.info('Preparing roles export: rows=%s', data_rows)
    if samples:
        for idx, sample_row in enumerate(samples, start=1):
            logger.debug(
                'Export sample %s: topic=%s | role=%s | student=%s | supervisor=%s',
                idx,
                sample_row[0],
                sample_row[1],
                sample_row[2],
                sample_row[3],
            )

    ws = _open_ws(spreadsheet_id, service_account_file)
    # Clear and write
    ws.clear()
    ws.update('A1', rows)
    logger.info('Roles exported to spreadsheet %s (rows=%s)', spreadsheet_id, data_rows)
    return data_rows



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
        logger.warning('Roles sheet sync skipped: spreadsheet ID is not configured')
        return False
    service_account_path = resolve_service_account_path(
        service_account_file or os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
    )
    if not service_account_path:
        logger.warning('Roles sheet sync skipped: service account file is not configured')
        return False

    reuse_conn = conn is not None
    logger.info('Starting roles sheet sync (spreadsheet=%s, reuse_conn=%s)', sid, reuse_conn)
    logger.debug('Using service account file: %s', service_account_path)
    try:
        if conn is not None:
            rows_written = export_pairs_from_db(conn, sid, service_account_path)
        else:
            with get_conn() as fresh_conn:
                rows_written = export_pairs_from_db(fresh_conn, sid, service_account_path)
        logger.info('Roles sheet sync completed (spreadsheet=%s, rows=%s)', sid, rows_written)
        return True
    except Exception as exc:  # pragma: no cover - best effort logging
        logger.warning(
            'Failed to export roles to Google Sheet %s: %s',
            sid,
            exc,
            exc_info=True,
        )
        return False


