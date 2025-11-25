from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .routes.import_students import create_students_import_router
from .routes.import_supervisors import create_supervisors_import_router
from .services.db import get_conn
from .workflows.sheet_pairs import sync_roles_sheet


                                                     
def _configure_logging() -> logging.Logger:
    """Настраивает логирование сервиса Google Data и возвращает корневой логгер."""
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger("google_data")


load_dotenv()
logger = _configure_logging()

app = FastAPI(title="MentorMatch Google Data Service")
app.include_router(create_students_import_router(get_conn))
app.include_router(create_supervisors_import_router(get_conn))


class ExportPairsPayload(BaseModel):
    spreadsheet_id: Optional[str] = None
    service_account_file: Optional[str] = None


@app.get("/health", response_class=JSONResponse)
                                            
def health_check() -> dict[str, str]:
    """Возвращает статус готовности сервиса для проверок инфраструктуры."""
    return {"status": "ok"}


@app.post("/api/export/pairs", response_class=JSONResponse)
                                                       
def export_pairs(payload: ExportPairsPayload) -> JSONResponse:
    """Экспортирует пары студентов и наставников в Google Sheets."""
    spreadsheet_id = (
        (payload.spreadsheet_id or "").strip()
        or os.getenv("PAIRS_SPREADSHEET_ID")
        or os.getenv("STUDENT_SPREADSHEET_ID")
        or os.getenv("SPREADSHEET_ID")
    )
    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="Spreadsheet ID is not configured")
    service_account_file = payload.service_account_file or os.getenv("SERVICE_ACCOUNT_FILE", "service-account.json")
    logger.info(
        "Triggering roles export: spreadsheet=%s, service_account_file=%s",
        spreadsheet_id,
        service_account_file,
    )

    success = sync_roles_sheet(
        get_conn,
        spreadsheet_id=spreadsheet_id,
        service_account_file=service_account_file,
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to export data to Google Sheets")
    return JSONResponse({"status": "ok", "spreadsheet_id": spreadsheet_id})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("google_data.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8200")), reload=False)
