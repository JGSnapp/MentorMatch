from __future__ import annotations

from typing import Optional, Any

from media_store import persist_media_from_url


def normalize_telegram_link(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = (raw or "").strip()
    if s.startswith("@"):
        s = s[1:]
    if s.lower().startswith(("http://t.me/", "https://t.me/", "http://telegram.me/", "https://telegram.me/")):
        return s
    import re
    match = re.search(r"(?:https?://)?t(?:elegram)?\\.me/([A-Za-z0-9_]+)", s)
    if match:
        return f"https://t.me/{match.group(1)}"
    s = re.sub(r"[^A-Za-z0-9_]", "", s)
    return f"https://t.me/{s}" if s else None


def is_http_url(value: Optional[str]) -> bool:
    return bool(value) and str(value).strip().lower().startswith(("http://", "https://"))


def process_cv(conn, user_id: int, cv_val: Optional[str]) -> Optional[str]:
    val = (cv_val or "").strip()
    if not val:
        return None
    if val.startswith("/media/"):
        return val
    if is_http_url(val):
        try:
            _mid, public = persist_media_from_url(conn, user_id, val, category="cv")
            return public
        except Exception as exc:  # pragma: no cover - logging side-effect
            print(f"CV download failed for user {user_id}: {exc}")
            return cv_val
    return cv_val
