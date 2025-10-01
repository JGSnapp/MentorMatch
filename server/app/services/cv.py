"""Helpers for working with user CV links."""
from __future__ import annotations

from typing import Optional

from media_store import persist_media_from_url

from ..helpers import is_http_url


def process_cv(conn, user_id: int, cv_val: Optional[str]) -> Optional[str]:
    value = (cv_val or "").strip()
    if not value:
        return None
    if value.startswith("/media/"):
        return value
    if is_http_url(value):
        try:
            _mid, public = persist_media_from_url(conn, user_id, value, category="cv")
            return public
        except Exception:
            return cv_val
    return cv_val
