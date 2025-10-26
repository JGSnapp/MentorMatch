"""Service responsible for Google Sheets import and export flows."""
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse

from app.api import (
    create_students_import_router,
    create_supervisors_import_router,
)
from app.config import configure_logging, get_conn
from app.sheet_pairs import sync_roles_sheet

configure_logging()

app = FastAPI(title="MentorMatch Google Data Service")

app.include_router(create_students_import_router(get_conn))
app.include_router(create_supervisors_import_router(get_conn))


@app.post("/export/pairs", response_class=JSONResponse)
def export_pairs(
    spreadsheet_id: str | None = Form(None),
    service_account_file: str | None = Form(None),
):
    triggered = sync_roles_sheet(
        get_conn,
        spreadsheet_id=spreadsheet_id,
        service_account_file=service_account_file,
    )
    return JSONResponse({"status": "ok", "triggered": triggered})


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}
