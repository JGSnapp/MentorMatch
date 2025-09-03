#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import gspread
from google.oauth2.service_account import Credentials


def _simplify(s: str) -> str:
    s = (s or '').strip().lower()
    s = s.replace('ё', 'е')
    s = re.sub(r'["“”«»()\[\]:.,!?\\]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def _split_list(s: str) -> Optional[List[str]]:
    if not s or not s.strip():
        return None
    parts = re.split(r'[;,/|]\s*|\s{2,}', s.strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts or None


def _to_bool(s: str) -> Optional[bool]:
    v = (s or '').strip().lower()
    if v in ('да', 'yes', 'y', 'true', 'on', '1'):
        return True
    if v in ('нет', 'no', 'n', 'false', 'off', '0', ''):
        return False
    return None


def _parse_timestamp(ts: str) -> Optional[str]:
    if not ts:
        return None
    for fmt in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(ts, fmt).isoformat()
        except ValueError:
            pass
    return ts


def _extract_first_url(s: str) -> Optional[str]:
    if not s:
        return None
    m = re.search(r'(https?://\S+)', s)
    return m.group(1) if m else None


def _extract_telegram_username(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if s.startswith('@'):
        return s[1:]
    m = re.search(r'(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)', s)
    if m:
        return m.group(1)
    return re.sub(r'[^A-Za-z0-9_]', '', s) or None


HEADER_ALIASES: Dict[str, List[str]] = {
    'timestamp': ['отметка времени'],
    'full_name': ['введите фио', 'фио'],
    'telegram': ['ник telegram', 'telegram', 'тг', 'телеграм'],
    'program': ['ваше направление', 'направление'],
    'hard_skills_have': ['hard skills (знаю)', 'знаю'],
    'hard_skills_want': ['hard skills (хочу изучить)', 'хочу изучить'],
    'interests': ['область научного/профессионального интереса', 'интерес'],
    'achievements': ['дополнительная информация о себе', 'достижения', 'награды'],
    'supervisor_pref': ['предполагаемого научного руководителя', 'пожелания', 'научного руководителя'],
    'has_own_topic': ['своя тема', 'своя тема для вкр', 'есть ли у вас своя тема'],
    'topic_title': ['название'],
    'topic_description': ['описание'],
    'topic_expected': ['ожидаемый результат'],
    'email': ['адрес электронной почты', 'email', 'e-mail'],
    'groundwork': ['имеющийся задел по теме', 'задел по теме'],
    'wants_team': ['планируете ли вы работать в команде', 'работать в команде'],
    'team_role': ['желаемая роль в команде'],
    'team_needs': ['кто дополнительно требуется в команду', 'кто дополнительно требуется'],
    'apply_master': ['планируете поступать в магистратуру', 'магистратур'],
    'workplace': ['место работы', 'должность'],
    'preferred_team_track': ['наиболее предпочтительный трек команды', 'предпочтительный трек команды'],
    'dev_track': ['разработка - трек вашего развития'],
    'science_track': ['наука - трек вашего развития'],
    'startup_track': ['стартап - трек вашего развития'],
    'cv': ['загрузите файл', 'cv', 'резюме'],
    'final_work_pref': ['в качестве финальной работы', 'финальной работы'],
    'consent_private': ['публикации в приватных чатах', 'приватных чатах', 'в чатах фпин', 'проектный семинар'],
    'consent_personal': ['даю согласие на обработку персональных данных', 'согласие на обработку персональных данных'],
}


def _build_col_index(headers: List[str]) -> Dict[str, int]:
    idx_map: Dict[str, int] = {}
    sim = [_simplify(h) for h in headers]
    for key, aliases in HEADER_ALIASES.items():
        for i, h in enumerate(sim):
            if any(a in h for a in aliases):
                idx_map[key] = i
                break
    if (os.getenv('LOG_LEVEL') or '').upper() == 'DEBUG':
        try:
            print('parse_gform: headers ->', headers)
            print('parse_gform: resolved cols ->', idx_map)
        except Exception:
            pass
    return idx_map


def _cell(row: List[str], j: Optional[int]) -> str:
    if j is None or j < 0:
        return ''
    return (row[j].strip() if j < len(row) and row[j] is not None else '').strip()


def _normalize_row(row: List[str], cols: Dict[str, int]) -> Dict[str, Any]:
    hard_have = _cell(row, cols.get('hard_skills_have'))
    hard_want = _cell(row, cols.get('hard_skills_want'))
    interests = _cell(row, cols.get('interests'))

    topic = {
        'title': _cell(row, cols.get('topic_title')) or None,
        'description': _cell(row, cols.get('topic_description')) or None,
        'expected_outcomes': _cell(row, cols.get('topic_expected')) or None,
    }

    telegram_username = _extract_telegram_username(_cell(row, cols.get('telegram')))
    cv_link = _extract_first_url(_cell(row, cols.get('cv')))

    wants_team = _to_bool(_cell(row, cols.get('wants_team')))
    apply_master = _to_bool(_cell(row, cols.get('apply_master')))
    has_own_topic = _to_bool(_cell(row, cols.get('has_own_topic')))
    dev_track = _to_bool(_cell(row, cols.get('dev_track')))
    science_track = _to_bool(_cell(row, cols.get('science_track')))
    startup_track = _to_bool(_cell(row, cols.get('startup_track')))
    consent_private = _to_bool(_cell(row, cols.get('consent_private')))
    consent_personal = _to_bool(_cell(row, cols.get('consent_personal')))

    result: Dict[str, Any] = {
        'timestamp': _parse_timestamp(_cell(row, cols.get('timestamp'))),
        'full_name': _cell(row, cols.get('full_name')) or None,
        'email': _cell(row, cols.get('email')) or None,
        'telegram': telegram_username,
        'program': _cell(row, cols.get('program')) or None,
        'hard_skills_have': _split_list(hard_have) if hard_have else None,
        'hard_skills_want': _split_list(hard_want) if hard_want else None,
        'interests': _split_list(interests) if interests else None,
        'achievements': _cell(row, cols.get('achievements')) or None,
        'supervisor_preference': _cell(row, cols.get('supervisor_pref')) or None,
        'has_own_topic': has_own_topic,
        'topic': None,
        'groundwork': _cell(row, cols.get('groundwork')) or None,
        'wants_team': wants_team,
        'team_role': _cell(row, cols.get('team_role')) or None,
        'team_needs': _cell(row, cols.get('team_needs')) or None,
        'apply_master': apply_master,
        'workplace': _cell(row, cols.get('workplace')) or None,
        'preferred_team_track': _cell(row, cols.get('preferred_team_track')) or None,
        'dev_track': dev_track,
        'science_track': science_track,
        'startup_track': startup_track,
        'cv': cv_link or _cell(row, cols.get('cv')) or None,
        'final_work_preference': _cell(row, cols.get('final_work_pref')) or None,
        'consent_personal': consent_personal,
        'consent_private': consent_private,
    }

    if result['has_own_topic'] is True or any(topic.values()):
        result['topic'] = topic

    return result


def _select_worksheet(sh, sheet_name: Optional[str]):
    titles = [ws.title for ws in sh.worksheets()]
    def norm(s: Optional[str]) -> str:
        return (s or '').strip().lower()

    # Treat empty, None, 'none', 'null' as no explicit sheet
    if not sheet_name or norm(sheet_name) in ('none', 'null', ''):
        try:
            return sh.sheet1
        except Exception:
            return sh.worksheets()[0]

    # Exact match first
    for ws in sh.worksheets():
        if ws.title == sheet_name:
            return ws
    # Case-insensitive match
    for ws in sh.worksheets():
        if norm(ws.title) == norm(sheet_name):
            return ws

    # Common defaults synonym mapping
    syns = {
        'sheet1': ['лист1', 'лист 1', 'лист-1', 'sheet 1', 'sheet-1'],
    }
    for target, alts in syns.items():
        if norm(sheet_name) in alts:
            for ws in sh.worksheets():
                if norm(ws.title) == target:
                    return ws
            # Fallback to first
            return sh.worksheets()[0]

    # Not found: fallback to first, but log titles if debug
    if (os.getenv('LOG_LEVEL') or '').upper() == 'DEBUG':
        print(f'parse_gform: worksheet "{sheet_name}" not found; available: {titles}')
    return sh.worksheets()[0]


def fetch_normalized_rows(
    spreadsheet_id: str,
    sheet_name: Optional[str],
    service_account_file: Union[str, Path] = 'service-account.json'
) -> List[Dict[str, Any]]:
    scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
    creds = Credentials.from_service_account_file(str(service_account_file), scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(spreadsheet_id)
    ws = _select_worksheet(sh, sheet_name)

    values: List[List[str]] = ws.get_all_values()
    if not values:
        return []

    headers = values[0]
    data_rows = [r for r in values[1:] if any((c or '').strip() for c in r)]
    cols = _build_col_index(headers)

    normalized = [_normalize_row(r, cols) for r in data_rows]
    return normalized
