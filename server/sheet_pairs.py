from __future__ import annotations
from typing import List, Tuple, Optional
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials


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
                   COALESCE(sup.full_name, author.full_name) AS supervisor_name
            FROM roles r
            JOIN topics t ON t.id = r.topic_id
            JOIN users author ON author.id = t.author_user_id
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

