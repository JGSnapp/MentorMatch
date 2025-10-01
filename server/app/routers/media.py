"""Serve stored media files."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from media_store import MEDIA_ROOT

from ..db import get_conn

router = APIRouter()


@router.get("/media/{media_id}")
def serve_media(media_id: int):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT object_key, mime_type FROM media_files WHERE id=%s", (media_id,))
            row = cur.fetchone()
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        object_key, mime_type = row
        file_path = (MEDIA_ROOT / object_key).resolve()
        if not file_path.exists():
            return JSONResponse({"error": "File missing"}, status_code=404)
        return FileResponse(
            str(file_path),
            media_type=(mime_type or "application/octet-stream"),
            filename=file_path.name,
        )
    except Exception as exc:  # pragma: no cover - defensive path
        return JSONResponse({"error": f"Failed to serve media: {exc}"}, status_code=500)
