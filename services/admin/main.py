"""FastAPI application exposing the MentorMatch admin UI."""
from fastapi import FastAPI

from common.admin import create_admin_router
from common.config import configure_logging, get_conn, get_templates

configure_logging()

app = FastAPI(title="MentorMatch Admin Service")

templates = get_templates()
app.include_router(create_admin_router(get_conn, templates))


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}
