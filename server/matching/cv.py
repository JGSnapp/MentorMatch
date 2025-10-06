"""Helpers for extracting CV text stored in the media storage."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from psycopg2.extensions import connection

from media_store import MEDIA_ROOT
from text_extract import extract_text_from_file


def resolve_cv_text(conn: connection, cv_value: Optional[str]) -> Optional[str]:
    """Return textual CV representation for the stored value.

    The database stores either raw text, an URL or a ``/media/<id>`` pointer.
    When a media pointer is encountered the file content is extracted and
    prefixed with the filename to preserve context.
    """

    val = (cv_value or "").strip()
    if not val:
        return None
    if not val.startswith("/media/"):
        return val

    try:
        media_id = int(val.split("/")[-1])
    except Exception:
        return val

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT object_key, mime_type FROM media_files WHERE id=%s", (media_id,))
            row = cur.fetchone()
    except Exception:
        return val

    if not row:
        return val

    object_key, mime_type = row
    file_path = (MEDIA_ROOT / object_key).resolve()
    try:
        text = extract_text_from_file(file_path, mime_type)
    except Exception:
        return val

    if not text:
        return val

    header = f"CV (из файла {Path(file_path).name}):\n"
    return (header + text)[:20000]


__all__ = ["resolve_cv_text"]
