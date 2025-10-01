"""Register FastAPI routers."""
from __future__ import annotations

from fastapi import FastAPI

from . import identity, matching, media, messages, students, topics
from .sheets import create_router as create_sheets_router


def register_routers(app: FastAPI) -> None:
    app.include_router(identity.create_router())
    app.include_router(students.create_router())
    app.include_router(topics.create_router())
    app.include_router(messages.create_router())
    app.include_router(matching.router)
    app.include_router(media.router)
    app.include_router(create_sheets_router())
