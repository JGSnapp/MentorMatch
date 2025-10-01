"""Application factory for MentorMatch server."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from admin import create_admin_router

from .config import configure_logging
from .db import get_conn
from .routers import register_routers
from .startup import register_startup_events

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="MentorMatch Admin MVP")
    templates = Jinja2Templates(directory=str((Path(__file__).parent.parent / "templates").resolve()))
    app.include_router(create_admin_router(get_conn, templates))
    register_routers(app)
    templates_dir = Path(__file__).parent.parent / "templates"
    register_startup_events(app, get_conn, templates_dir)
    return app


app = create_app()
