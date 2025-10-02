"""FastAPI router handling supervisor imports from Google Sheets."""
from __future__ import annotations

import os
from typing import Callable

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse
from psycopg2.extensions import connection

from services.google_sheets import (
    ensure_service_account_file,
    google_tls_preflight,
    load_supervisor_rows,
)
from services.topic_import import import_supervisors


def create_supervisors_import_router(get_conn: Callable[[], connection]) -> APIRouter:
    router = APIRouter()

    @router.post("/api/import-supervisors", response_class=JSONResponse)
    def import_supervisors_endpoint(
        spreadsheet_id: str = Form(...),
        sheet_name: str | None = Form(None),
    ):
        try:
            service_account_file = ensure_service_account_file(
                os.getenv("SERVICE_ACCOUNT_FILE", "service-account.json")
            )
        except FileNotFoundError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

        google_tls_preflight()
        rows = load_supervisor_rows(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            service_account_file=service_account_file,
        )

        rows_list = list(rows)
        with get_conn() as conn:
            result = import_supervisors(conn, rows_list)
        result.setdefault("stats", {})["total_rows_in_sheet"] = len(rows_list)
        return JSONResponse(result)

    return router


__all__ = ["create_supervisors_import_router"]
