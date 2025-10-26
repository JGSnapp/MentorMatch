from __future__ import annotations

from fastapi import APIRouter

from .context import AdminContext
from .views import dashboard, imports, matching, requests, topics, users


def create_admin_router(get_conn, templates) -> APIRouter:
    ctx = AdminContext(get_conn=get_conn, templates=templates)
    router = APIRouter()

    for module in (dashboard, topics, users, imports, matching, requests):
        module.register(router, ctx)

    return router
