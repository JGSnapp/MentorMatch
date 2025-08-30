#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import re, json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import gspread
from google.oauth2.service_account import Credentials

COL = {
    'timestamp': 1,
    'full_name': 2,
    'contacts': 3,
    'program': 4,
    'hard_skills': 5,
    'interests': 6,
    'cv': 7,
    'preferences': 8,
    'has_own_topic': 9,
    'topic_title': 10,
    'topic_description': 11,
    'topic_expected': 12,
}

def idx(n1: int) -> int:
    return max(0, n1 - 1)

def cell(row: List[str], n1: int) -> str:
    j = idx(n1)
    return (row[j].strip() if j < len(row) and row[j] is not None else '').strip()

def split_list(s: str) -> Optional[List[str]]:
    if not s or not s.strip():
        return None
    parts = re.split(r'[;,/|]\s*|\s{2,}', s.strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts or None

def to_bool_yes_no(s: str) -> Optional[bool]:
    v = (s or '').strip().lower()
    if v in ('да', 'yes', 'y', 'true', 'истина', '1'):
        return True
    if v in ('нет', 'no', 'n', 'false', 'ложь', '0', ''):
        return False
    return None

def parse_timestamp(ts: str) -> Optional[str]:
    if not ts:
        return None
    for fmt in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M'):
        try:
            return datetime.strptime(ts, fmt).isoformat()
        except ValueError:
            pass
    return ts

def normalize_row(row: List[str]) -> Dict[str, Any]:
    hskills = cell(row, COL['hard_skills'])
    intr = cell(row, COL['interests'])
    own = cell(row, COL['has_own_topic'])
    topic = {
        'title': cell(row, COL['topic_title']) or None,
        'description': cell(row, COL['topic_description']) or None,
        'expected_outcomes': cell(row, COL['topic_expected']) or None,
    }
    result = {
        'timestamp': parse_timestamp(cell(row, COL['timestamp'])),
        'full_name': cell(row, COL['full_name']) or None,
        'contacts': cell(row, COL['contacts']) or None,
        'program': cell(row, COL['program']) or None,
        'hard_skills': split_list(hskills) if hskills else None,
        'interests': split_list(intr) if intr else None,
        'cv': cell(row, COL['cv']) or None,
        'preferences': cell(row, COL['preferences']) or None,
        'has_own_topic': to_bool_yes_no(own),
        'topic': None,
    }
    if result['has_own_topic'] is True or any(topic.values()):
        result['topic'] = topic
    return result

def fetch_normalized_rows(spreadsheet_id: str, sheet_name: Optional[str], service_account_file: Union[str, Path] = 'service-account.json') -> List[Dict[str, Any]]:
    try:
        print(f"DEBUG: Подключаемся к Google Sheets...")
        scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
        creds = Credentials.from_service_account_file(str(service_account_file), scopes=scopes)
        gc = gspread.authorize(creds)
        
        print(f"DEBUG: Открываем таблицу {spreadsheet_id}")
        sh = gc.open_by_key(spreadsheet_id)
        
        print(f"DEBUG: Получаем лист: {sheet_name or 'первый'}")
        print(f"DEBUG: Доступные листы: {[ws.title for ws in sh.worksheets()]}")
        
        try:
            ws = sh.worksheet(sheet_name) if sheet_name else sh.sheet1
            print(f"DEBUG: Лист получен: {ws.title}")
        except Exception as e:
            print(f"DEBUG: Ошибка получения листа: {e}")
            # Пробуем получить первый доступный лист
            worksheets = sh.worksheets()
            if worksheets:
                ws = worksheets[0]
                print(f"DEBUG: Используем первый доступный лист: {ws.title}")
            else:
                raise Exception("В таблице нет листов")
        
        print(f"DEBUG: Читаем все значения...")
        values: List[List[str]] = ws.get_all_values()
        print(f"DEBUG: Получено {len(values)} строк (включая заголовок)")
        
        if values:
            print(f"DEBUG: Заголовок: {values[0]}")
            print(f"DEBUG: Первая строка данных: {values[1] if len(values) > 1 else 'нет'}")
        
        data_rows = values[1:] if values else []  # Пропускаем заголовок
        print(f"DEBUG: Строк данных (без заголовка): {len(data_rows)}")
        
        # Фильтруем пустые строки
        filtered_rows = [r for r in data_rows if any((c or '').strip() for c in r)]
        print(f"DEBUG: Строк после фильтрации пустых: {len(filtered_rows)}")
        
        normalized = [normalize_row(r) for r in filtered_rows]
        print(f"DEBUG: Нормализованных строк: {len(normalized)}")
        
        return normalized
        
    except Exception as e:
        print(f"ERROR в fetch_normalized_rows: {e}")
        import traceback
        traceback.print_exc()
        raise
