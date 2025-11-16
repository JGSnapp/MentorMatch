from pathlib import Path
from typing import Any, Optional


def parse_optional_int(value: Optional[Any]) -> Optional[int]:
    """Преобразует значение в целое число или возвращает ``None``."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def normalize_optional_str(value: Optional[Any]) -> Optional[str]:
    """Очищает строку от пробелов и возвращает ``None`` для пустых значений."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
    else:
        stripped = str(value).strip()
    return stripped or None


def parse_optional_bool(value: Optional[Any]) -> Optional[bool]:
    """Преобразует произвольный ввод в булево значение."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    truthy = {'1', 'true', 't', 'yes', 'y', 'да', 'ok', 'on', 'истина'}
    falsy = {'0', 'false', 'f', 'no', 'n', 'нет', 'off'}
    if lowered in truthy:
        return True
    if lowered in falsy:
        return False
    return None


def resolve_service_account_path(path: Optional[str]) -> Optional[str]:
    """Ищет путь к файлу сервисного аккаунта и возвращает абсолютный путь."""
    if not path:
        return None
    try:
        candidate = Path(path)
        if candidate.is_absolute() and candidate.exists():
            return str(candidate)
        potential_locations = [
            candidate,
            Path(__file__).parent / path,
            Path(__file__).parent.parent / path,
        ]
        for option in potential_locations:
            if option.exists():
                return str(option)
    except Exception:
        pass
    return path
