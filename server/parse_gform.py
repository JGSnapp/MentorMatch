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


# ============
# Normalizers
# ============

def _simplify(s: str) -> str:
    s = (s or '').strip().lower()
    # Keep latin/cyrillic letters and digits; collapse everything else to spaces
    s = re.sub(r"[^a-zа-яё0-9]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _split_list(s: str) -> Optional[List[str]]:
    if not s or not s.strip():
        return None
    parts = re.split(r"[;,/|]\s*|\s{2,}", s.strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts or None


def _to_bool_ru(s: str) -> Optional[bool]:
    v = (s or '').strip().lower()
    if not v:
        return None
    truthy = {"да", "true", "yes", "y", "1", "on", "планирую", "буду", "хочу"}
    falsy = {"нет", "false", "no", "n", "0", "off", "не планирую"}
    if v in truthy:
        return True
    if v in falsy:
        return False
    return None


def _to_level_0_5(s: str) -> Optional[int]:
    v = (s or '').strip()
    if not v:
        return None
    m = re.search(r"-?\d+", v)
    if not m:
        return None
    try:
        n = int(m.group(0))
        if 0 <= n <= 5:
            return n
        if n < 0:
            return 0
        if n > 5:
            return 5
    except ValueError:
        return None
    return None


def _parse_timestamp(ts: str) -> Optional[str]:
    if not ts:
        return None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts, fmt).isoformat()
        except ValueError:
            pass
    return ts


def _extract_first_url(s: str) -> Optional[str]:
    if not s:
        return None
    m = re.search(r"(https?://\S+)", s)
    return m.group(1) if m else None


def _extract_telegram_username(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if s.startswith('@'):
        return s[1:]
    m = re.search(r"(?:https?://)?t(?:elegram)?\.me/([A-Za-z0-9_]+)", s)
    if m:
        return m.group(1)
    return re.sub(r"[^A-Za-z0-9_]", "", s) or None


def _format_telegram_link(raw: Optional[str]) -> Optional[str]:
    """Вернуть ссылку вида https://t.me/<username> с учётом @ и неполных ссылок."""
    if not raw:
        return None
    raw = raw.strip()
    if re.match(r"^https?://t(?:elegram)?\.me/", raw, flags=re.IGNORECASE):
        return raw
    username = _extract_telegram_username(raw)
    return f"https://t.me/{username}" if username else None


# =============================
# Header aliases (student form)
# =============================
HEADER_ALIASES: Dict[str, List[str]] = {
    # Блок 1
    'timestamp': ["отметка времени"],
    'email': ["адрес электронной почты", "email", "e-mail"],
    'full_name': ["введите фио", "фио", "фамилия имя отчество"],
    'telegram': ["введите ник telegram", "ник telegram", "telegram", "телеграм"],
    'program': ["ваше направление", "образовательная программа"],
    'dev_track': ["разработка трек вашего развития", "разработка - трек вашего развития"],
    'science_track': ["наука трек вашего развития", "наука - трек вашего развития"],
    'startup_track': ["стартап трек вашего развития", "стартап - трек вашего развития"],
    'hard_skills_have': ["ваши hard skills знаю", "hard skills знаю"],
    'hard_skills_want': ["hard skills хочу изучить", "хочу изучить hard skills"],
    'interests': ["область научного профессионального интереса", "область профессионального интереса", "интересы"],
    'workplace': ["ваше место работы", "место работы", "должность"],
    'apply_master': ["планируете поступать в магистратуру"],
    'achievements': ["дополнительная информация о себе", "достижения", "награды"],
    'cv': ["загрузите файл", "cv", "резюме"],
    'final_work_pref': ["в качестве вариативного задания я предпочитаю"],
    'supervisor_pref': ["фио предполагаемого научного руководителя", "пожелания научного руководителя"],
    'has_own_topic': ["есть ли у вас предполагаемая тема для вкр"],

    # Блок 2 (условный)
    'topic_title': ["название"],
    'topic_description': ["описание"],
    'topic_practical': ["практическая значимость"],
    'groundwork': ["имеющийся задел по теме", "задел по теме"],
    'topic_expected': ["ожидаемый результат", "ожидаемый результат по выполнении работы"],

    # Блок 3
    'wants_team': ["планируете ли вы работать в команде"],

    # Блок 4 (если Блок 3 не «нет»)
    'team_role': ["желаемая роль в команде"],
    'team_has': ["у вас уже есть в команде"],
    'team_needs': ["кто дополнительно требуется в команду"],
    'preferred_team_track': ["наиболее предпочтительный трек команды", "предпочтительный трек команды"],

    # Согласия (опционально)
    'consent_private': ["согласие на обработку закрытых данных", "согласие закрытые", "согласие приват"],
    'consent_personal': ["согласие на обработку персональных данных", "согласие персональные"],
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
    return (row[j] or '').strip()


def _normalize_row(row: List[str], cols: Dict[str, int]) -> Dict[str, Any]:
    hard_have = _cell(row, cols.get('hard_skills_have'))
    hard_want = _cell(row, cols.get('hard_skills_want'))
    interests = _cell(row, cols.get('interests'))

    # Блок 2 — собственная тема студента
    topic: Dict[str, Optional[str]] = {
        'title': _cell(row, cols.get('topic_title')) or None,
        'description': _cell(row, cols.get('topic_description')) or None,
        'expected_outcomes': _cell(row, cols.get('topic_expected')) or None,
    }
    practical = _cell(row, cols.get('topic_practical')) or None
    if practical:
        topic['practical_importance'] = practical

    telegram_link = _format_telegram_link(_cell(row, cols.get('telegram')))
    cv_link = _extract_first_url(_cell(row, cols.get('cv')))

    wants_team_raw = _cell(row, cols.get('wants_team'))
    wants_team = _to_bool_ru(wants_team_raw)
    apply_master = _to_bool_ru(_cell(row, cols.get('apply_master')))

    # Есть ли своя тема
    has_own_topic_raw = _cell(row, cols.get('has_own_topic'))
    has_own_topic = None
    if has_own_topic_raw:
        v = _simplify(has_own_topic_raw)
        if v == 'нет':
            has_own_topic = False
        elif any(topic.values()):
            has_own_topic = True
    elif any(topic.values()):
        has_own_topic = True

    # Уровни треков 0..5
    dev_track = _to_level_0_5(_cell(row, cols.get('dev_track')))
    science_track = _to_level_0_5(_cell(row, cols.get('science_track')))
    startup_track = _to_level_0_5(_cell(row, cols.get('startup_track')))

    consent_private = _to_bool_ru(_cell(row, cols.get('consent_private')))
    consent_personal = _to_bool_ru(_cell(row, cols.get('consent_personal')))

    result: Dict[str, Any] = {
        'timestamp': _parse_timestamp(_cell(row, cols.get('timestamp'))),
        'full_name': _cell(row, cols.get('full_name')) or None,
        'email': _cell(row, cols.get('email')) or None,
        'telegram': telegram_link,
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
        'team_has': _cell(row, cols.get('team_has')) or None,
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

    # Если Блок 4 заполнен, но ответ на Блок 3 не дан — считаем, что команда нужна
    if result['wants_team'] is None and any((result.get('team_role'), result.get('team_has'), result.get('team_needs'), result.get('preferred_team_track'))):
        result['wants_team'] = True

    # Добавляем тему, если заполнены какие-то поля
    if any(v for k, v in topic.items() if k in ('title','description','expected_outcomes') and v):
        result['topic'] = topic

    return result


# Select worksheet by name or by index

def _select_worksheet(sh, sheet_name: Optional[str]):
    titles = [ws.title for ws in sh.worksheets()]
    def norm(s: Optional[str]) -> str:
        return (s or '').strip().lower()

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
    target = norm(sheet_name)
    for ws in sh.worksheets():
        if norm(ws.title) == target:
            return ws
    # Fallback
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


# =============================
# Supervisors (2nd sheet) parser
# =============================

SUP_HEADER_ALIASES: Dict[str, List[str]] = {
    'timestamp': ["отметка времени"],
    'email': ["адрес электронной почты", "email", "e-mail"],
    'full_name': ["фио", "введите фио"],
    # Для совместимости оставляем общий ключ, если один столбец с темами
    'topics_text': ["перечень тем", "темы", "перечень тем для студентов", "предлагаемые темы"],
    # Остальные поля
    'area': ["область научного интереса", "область интереса", "область"],
    'extra_info': ["дополнительная информация", "доп информация", "прочее"],
    'telegram': ["ник telegram", "введите ник telegram", "telegram", "телеграм"],
}


def _build_col_index_sup(headers: List[str]) -> Dict[str, int]:
    idx_map: Dict[str, Any] = {}
    sim = [_simplify(h) for h in headers]
    for key, aliases in SUP_HEADER_ALIASES.items():
        for i, h in enumerate(sim):
            if any(a in h for a in aliases):
                idx_map[key] = i
                break

    # Дополнительно: собрать все столбцы с темами (например, 45/09/11 направления)
    topics_cols = []
    for i, h in enumerate(sim):
        # Считаем тематическими любые столбцы, где встречаются 'темы'/'тематики' и 'вкр'
        if (('темы' in h or 'тематики' in h) and 'вкр' in h):
            topics_cols.append(i)
            # Определяем направление по числам в заголовке
            if '45' in h:
                idx_map['topics_45'] = i
            # 09 иногда пишут как '09', '9', '09 го'
            if '09' in h or ' 9 ' in f' {h} ':
                idx_map['topics_09'] = i
            if '11' in h:
                idx_map['topics_11'] = i
    if topics_cols:
        idx_map['topics_multi'] = topics_cols

    if (os.getenv('LOG_LEVEL') or '').upper() == 'DEBUG':
        try:
            print('parse_gform/supervisors: headers ->', headers)
            print('parse_gform/supervisors: resolved cols ->', idx_map)
        except Exception:
            pass
    return idx_map


def _normalize_supervisor_row(row: List[str], cols: Dict[str, Any]) -> Dict[str, Any]:
    telegram_link = _format_telegram_link(_cell(row, cols.get('telegram')))
    area = _cell(row, cols.get('area')) or None

    # Собираем темы из одного или нескольких столбцов
    parts: List[str] = []
    first = _cell(row, cols.get('topics_text')) if isinstance(cols.get('topics_text'), int) else ''
    if first:
        parts.append(first)
    multi = cols.get('topics_multi')
    if isinstance(multi, list):
        for i in multi:
            # избегаем дублирования первой колонки
            if isinstance(cols.get('topics_text'), int) and i == cols.get('topics_text'):
                continue
            val = _cell(row, i)
            if val:
                parts.append(val)
    topics_text = '\n'.join([p for p in parts if p and p.strip()]) or None

    # Отдельные поля по направлениям (если есть)
    topics_45 = _cell(row, cols.get('topics_45')) or None
    topics_09 = _cell(row, cols.get('topics_09')) or None
    topics_11 = _cell(row, cols.get('topics_11')) or None

    extra_info = _cell(row, cols.get('extra_info')) or None

    return {
        'timestamp': _parse_timestamp(_cell(row, cols.get('timestamp'))),
        'full_name': _cell(row, cols.get('full_name')) or None,
        'email': _cell(row, cols.get('email')) or None,
        'telegram': telegram_link,
        'area': area,
        'topics_text': topics_text,
        'topics_45': topics_45,
        'topics_09': topics_09,
        'topics_11': topics_11,
        'extra_info': extra_info,
    }


def _select_worksheet_second(sh) -> Any:
    try:
        wss = sh.worksheets()
        if len(wss) >= 2:
            return wss[1]
        return wss[-1]
    except Exception:
        return sh.sheet1


def fetch_supervisor_rows(
    spreadsheet_id: str,
    sheet_name: Optional[str] = None,
    service_account_file: Union[str, Path] = 'service-account.json'
) -> List[Dict[str, Any]]:
    scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
    creds = Credentials.from_service_account_file(str(service_account_file), scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(spreadsheet_id)
    ws = _select_worksheet(sh, sheet_name) if sheet_name else _select_worksheet_second(sh)

    values: List[List[str]] = ws.get_all_values()
    if not values:
        return []

    headers = values[0]
    data_rows = [r for r in values[1:] if any((c or '').strip() for c in r)]
    cols = _build_col_index_sup(headers)

    normalized = [_normalize_supervisor_row(r, cols) for r in data_rows]
    return normalized
