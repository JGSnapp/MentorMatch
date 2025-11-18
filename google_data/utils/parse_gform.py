from __future__ import annotations
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import gspread
from google.oauth2.service_account import Credentials



                                                    
def _simplify(s: str) -> str:
    """Приводит строку к нижнему регистру и удаляет посторонние символы."""
    s = (s or '').strip().lower()
    s = re.sub(r"[^0-9a-z\u0430-\u044f\u0451]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


                                                         
def _split_list(s: str) -> Optional[List[str]]:
    """Разбивает строку по разделителям на список значений."""
    if not s or not s.strip():
        return None
    parts = re.split(r"[;,/|]\s*|\s{2,}", s.strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts or None


                                                        
def _to_bool_ru(s: str) -> Optional[bool]:
    """Интерпретирует русские ответы «да/нет» как булевы значения."""
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
    """Извлекает оценку от 0 до 5 из строкового ответа."""
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


                                                    



def _to_int_value(s: str) -> Optional[int]:
    """Преобразует произвольную ячейку в целое число."""
    value = (s or '').strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        match = re.search(r'-?\d+', value)
        if match:
            try:
                return int(match.group(0))
            except ValueError:
                return None
    return None

def _parse_timestamp(ts: str) -> Optional[str]:
    """Преобразует отметку времени из формы в формат ISO."""
    if not ts:
        return None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts, fmt).isoformat()
        except ValueError:
            pass
    return ts


                                                        
def _extract_first_url(s: str) -> Optional[str]:
    """Находит первую URL-ссылку в произвольной строке."""
    if not s:
        return None
    m = re.search(r"(https?://\S+)", s)
    return m.group(1) if m else None


                                                           
def _extract_telegram_username(s: str) -> Optional[str]:
    """Извлекает username Telegram из текста или ссылки."""
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


HEADER_ALIASES: Dict[str, List[str]] = {
    'timestamp': ["отметка времени"],
    'email': ["адрес электронной почты", "email", "e-mail"],
    'full_name': ["фамилия имя отчество", "фио", "введите фио"],
    'telegram': ["telegram", "телеграм", "ник telegram"],
    'isu_number': ["номер ису"],
    'subdivision': ["подразделение мф тинт", "подразделение"],
    'direction': ["ваше направление"],
    'status': ["статус"],
    'course': ["курс"],
    'group_number': ["номер группы"],
    'education_program': ["название образовательной программы", "образовательная программа"],
    'phone': ["контактный телефон", "телефон"],
    'dev_track': ["разработка - трек вашего развития"],
    'science_track': ["наука - трек вашего развития"],
    'startup_track': ["стартап - трек вашего развития"],
    'hard_skills_have': ["hard skills (знаю)", "ваши hard skills знаю"],
    'hard_skills_want': ["hard skills (хочу изучить)", "hard skills хочу"],
    'interests': ["область научного", "область профессионального интереса", "интересы"],
    'dislikes': ["чем вы точно не хотели бы заниматься"],
    'commercial_experience': ["коммерческий опыт"],
    'noncommercial_experience': ["некоммерческий опыт", "академический опыт"],
    'portfolio': ["ссылка на репозиторий", "pet project"],
    'achievements': ["информация о своих достижениях", "достижения"],
    'hobbies': ["хобби", "внеучебные интересы"],
    'cv': ["резюме", "cv"],
    'customer_discovery': ["customer discovery"],
    'sales': ["sales", "negotiation"],
    'tech_execution': ["tech execution"],
    'data_analytics': ["data & analytics", "data analytics"],
    'marketing_design': ["marketing & design"],
    'finance_business': ["finance & business"],
    'team_leadership': ["team leadership"],
    'apply_master': ["планируете поступать в магистратуру", "планируете поступать в аспирантуру"],
    'hours_per_week': ["сколько вы готовы тратить времени"],
    'thematic_choice': ["выберите тематику"],
    'team_role': ["желаемая командная роль"],
    'plan_for_lab': ["что планируете выполнить"],
    'motivation_letter': ["мотивационное письмо"],
    'police_clearance': ["справка об отсутствии судимости"],
    'consent_personal': ["согласие на обработку персональных данных"],
}


                                                            
def _normalize_for_match(value: str) -> str:
    """Return lowercase header/alias stripped of spaces for resilient comparisons."""
    return _simplify(value).replace(" ", "")


def _build_col_index(headers: List[str]) -> Dict[str, int]:
    """Сопоставляет заголовки таблицы с ожидаемыми ключами анкеты студентов."""
    idx_map: Dict[str, int] = {}
    sim_raw = [_simplify(h) for h in headers]
    sim = [h.replace(" ", "") for h in sim_raw]
    for key, aliases in HEADER_ALIASES.items():
        alias_norms = [_normalize_for_match(a) for a in aliases]
        for i, h in enumerate(sim):
            if not h:
                continue
            if any(a and a in h for a in alias_norms):
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
    """Возвращает значение ячейки, аккуратно обрабатывая отсутствующие индексы."""
    if j is None or j < 0:
        return ''
    return (row[j] or '').strip()


                                                    
def _normalize_row(row: List[str], cols: Dict[str, int]) -> Dict[str, Any]:
    """Преобразует строку формы студента в словарь с нормализованными полями."""
    hard_have = _cell(row, cols.get('hard_skills_have'))
    hard_want = _cell(row, cols.get('hard_skills_want'))
    interests = _cell(row, cols.get('interests'))
    portfolio_raw = _cell(row, cols.get('portfolio'))
    cv_raw = _cell(row, cols.get('cv'))

    telegram_link = _format_telegram_link(_cell(row, cols.get('telegram')))
    cv_link = _extract_first_url(cv_raw)
    portfolio_link = _extract_first_url(portfolio_raw)

    dev_track = _to_level_0_5(_cell(row, cols.get('dev_track')))
    science_track = _to_level_0_5(_cell(row, cols.get('science_track')))
    startup_track = _to_level_0_5(_cell(row, cols.get('startup_track')))

    result: Dict[str, Any] = {
        'timestamp': _parse_timestamp(_cell(row, cols.get('timestamp'))),
        'full_name': _cell(row, cols.get('full_name')) or None,
        'email': _cell(row, cols.get('email')) or None,
        'telegram': telegram_link,
        'isu_number': _cell(row, cols.get('isu_number')) or None,
        'subdivision': _cell(row, cols.get('subdivision')) or None,
        'direction': _cell(row, cols.get('direction')) or None,
        'status': _cell(row, cols.get('status')) or None,
        'course': _to_int_value(_cell(row, cols.get('course'))),
        'group_number': _cell(row, cols.get('group_number')) or None,
        'education_program': _cell(row, cols.get('education_program')) or None,
        'phone': _cell(row, cols.get('phone')) or None,
        'dev_track': dev_track,
        'science_track': science_track,
        'startup_track': startup_track,
        'hard_skills_have': _split_list(hard_have) if hard_have else None,
        'hard_skills_want': _split_list(hard_want) if hard_want else None,
        'interests': _split_list(interests) if interests else None,
        'dislikes': _cell(row, cols.get('dislikes')) or None,
        'commercial_experience': _cell(row, cols.get('commercial_experience')) or None,
        'noncommercial_experience': _cell(row, cols.get('noncommercial_experience')) or None,
        'portfolio': portfolio_link or portfolio_raw or None,
        'achievements': _cell(row, cols.get('achievements')) or None,
        'hobbies': _cell(row, cols.get('hobbies')) or None,
        'cv': cv_link or cv_raw or None,
        'customer_discovery_level': _to_level_0_5(_cell(row, cols.get('customer_discovery'))),
        'sales_level': _to_level_0_5(_cell(row, cols.get('sales'))),
        'tech_execution_level': _to_level_0_5(_cell(row, cols.get('tech_execution'))),
        'data_analytics_level': _to_level_0_5(_cell(row, cols.get('data_analytics'))),
        'marketing_design_level': _to_level_0_5(_cell(row, cols.get('marketing_design'))),
        'finance_business_level': _to_level_0_5(_cell(row, cols.get('finance_business'))),
        'team_leadership_level': _to_level_0_5(_cell(row, cols.get('team_leadership'))),
        'apply_master': _to_bool_ru(_cell(row, cols.get('apply_master'))),
        'hours_per_week': _to_int_value(_cell(row, cols.get('hours_per_week'))),
        'thematic_choice': _cell(row, cols.get('thematic_choice')) or None,
        'team_role': _cell(row, cols.get('team_role')) or None,
        'plan_for_lab': _cell(row, cols.get('plan_for_lab')) or None,
        'motivation_letter': _cell(row, cols.get('motivation_letter')) or None,
        'police_clearance': _cell(row, cols.get('police_clearance')) or None,
        'consent_personal': _to_bool_ru(_cell(row, cols.get('consent_personal'))),
    }

    return result



                                                
def _select_worksheet(sh, sheet_name: Optional[str]):
    """Выбирает лист Google Sheets по имени или возвращает основной."""
    titles = [ws.title for ws in sh.worksheets()]
                                       
    def norm(s: Optional[str]) -> str:
        """Нормализует название листа для нечувствительного сравнения."""
        return (s or '').strip().lower()

    if not sheet_name or norm(sheet_name) in ('none', 'null', ''):
        try:
            return sh.sheet1
        except Exception:
            return sh.worksheets()[0]

    for ws in sh.worksheets():
        if ws.title == sheet_name:
            return ws
    target = norm(sheet_name)
    for ws in sh.worksheets():
        if norm(ws.title) == target:
            return ws
    return sh.worksheets()[0]


                                                                           
def fetch_normalized_rows(
    spreadsheet_id: str,
    sheet_name: Optional[str],
    service_account_file: Union[str, Path] = 'service-account.json'
) -> List[Dict[str, Any]]:
    """Загружает и нормализует ответы студентов из Google Forms."""
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



SUP_HEADER_ALIASES: Dict[str, List[str]] = {
    'timestamp': ["отметка времени"],
    'email': ["адрес электронной почты", "email", "e-mail"],
    'full_name': ["фио", "введите фио"],
    'topics_text': ["перечень тем", "темы", "перечень тем для студентов", "предлагаемые темы"],
    'area': ["область научного интереса", "область интереса", "область"],
    'extra_info': ["дополнительная информация", "доп информация", "прочее"],
    'telegram': ["ник telegram", "введите ник telegram", "telegram", "телеграм"],
}


                                                                    
def _build_col_index_sup(headers: List[str]) -> Dict[str, int]:
    """Определяет индексы колонок в анкете наставников по известным заголовкам."""
    idx_map: Dict[str, Any] = {}
    sim_raw = [_simplify(h) for h in headers]
    sim = [h.replace(" ", "") for h in sim_raw]
    for key, aliases in SUP_HEADER_ALIASES.items():
        alias_norms = [_normalize_for_match(a) for a in aliases]
        for i, h in enumerate(sim):
            if not h:
                continue
            if any(a and a in h for a in alias_norms):
                idx_map[key] = i
                break

    topics_cols = []
    for i, h in enumerate(sim_raw):
        if (('темы' in h or 'тематики' in h) and 'вкр' in h):
            topics_cols.append(i)
            if '45' in h:
                idx_map['topics_45'] = i
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
    """Преобразует строку наставника в структурированный словарь."""
    telegram_link = _format_telegram_link(_cell(row, cols.get('telegram')))
    area = _cell(row, cols.get('area')) or None

    parts: List[str] = []
    first = _cell(row, cols.get('topics_text')) if isinstance(cols.get('topics_text'), int) else ''
    if first:
        parts.append(first)
    multi = cols.get('topics_multi')
    if isinstance(multi, list):
        for i in multi:
            if isinstance(cols.get('topics_text'), int) and i == cols.get('topics_text'):
                continue
            val = _cell(row, i)
            if val:
                parts.append(val)
    topics_text = '\n'.join([p for p in parts if p and p.strip()]) or None

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
    """Возвращает второй лист таблицы, используя первый как запасной вариант."""
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
    """Загружает и нормализует ответы наставников из Google Forms."""
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
