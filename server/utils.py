from typing import Any, Optional


def parse_optional_int(value: Optional[Any]) -> Optional[int]:
    """Convert form/query values to integers while allowing blanks.

    Returns ``None`` when the input is ``None`` or an empty string. Values
    that are already integers are returned unchanged. Non-convertible inputs
    also result in ``None`` so callers can decide how to handle validation.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    # Support floats coming from parsed JSON/form data
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
    """Return a trimmed string or ``None`` when the input is blank.

    ``None`` inputs as well as strings consisting only of whitespace are
    normalized to ``None`` so callers can safely store NULLs in the database.
    Non-string values are converted to strings before trimming.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
    else:
        stripped = str(value).strip()
    return stripped or None
