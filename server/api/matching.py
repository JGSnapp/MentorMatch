"""Router exposing matching actions for administrators."""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse
from psycopg2.extensions import connection

from matching import (
    MatchingLLMClient,
    create_matching_llm_client,
    handle_match,
    handle_match_role,
    handle_match_student,
    handle_match_supervisor_user,
)


def create_matching_router(get_conn: Callable[[], connection]) -> APIRouter:
    router = APIRouter()

    def _client() -> MatchingLLMClient | None:
        return create_matching_llm_client()

    @router.post("/match-topic", response_class=JSONResponse)
    def match_topic(topic_id: int = Form(...), target_role: str = Form("student")):
        llm = _client()
        with get_conn() as conn:
            result = handle_match(
                conn,
                topic_id=topic_id,
                target_role=target_role,
                llm_client=llm,
            )
        return JSONResponse(result)

    @router.post("/match-student", response_class=JSONResponse)
    def match_student(student_user_id: int = Form(...)):
        llm = _client()
        with get_conn() as conn:
            result = handle_match_student(
                conn,
                student_user_id=student_user_id,
                llm_client=llm,
            )
        return JSONResponse(result)

    @router.post("/match-supervisor", response_class=JSONResponse)
    def match_supervisor(supervisor_user_id: int = Form(...)):
        llm = _client()
        with get_conn() as conn:
            result = handle_match_supervisor_user(
                conn,
                supervisor_user_id=supervisor_user_id,
                llm_client=llm,
            )
        return JSONResponse(result)

    @router.post("/match-role", response_class=JSONResponse)
    def match_role(role_id: int = Form(...)):
        llm = _client()
        with get_conn() as conn:
            result = handle_match_role(conn, role_id=role_id, llm_client=llm)
        return JSONResponse(result)

    return router


__all__ = ["create_matching_router"]
