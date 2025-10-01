"""LLM-based matching endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from matching import handle_match, handle_match_role, handle_match_student, handle_match_supervisor_user

from ..db import get_conn

router = APIRouter()


@router.post("/match-topic", response_class=JSONResponse)
def match_topic(topic_id: int = Form(...), target_role: str = Form("student")):
    with get_conn() as conn:
        result = handle_match(conn, topic_id=topic_id, target_role=target_role)
    return JSONResponse(result)


@router.post("/match-student", response_class=JSONResponse)
def match_student(student_user_id: int = Form(...)):
    with get_conn() as conn:
        result = handle_match_student(conn, student_user_id=student_user_id)
    return JSONResponse(result)


@router.post("/match-supervisor", response_class=JSONResponse)
def match_supervisor_user(supervisor_user_id: int = Form(...)):
    with get_conn() as conn:
        result = handle_match_supervisor_user(conn, supervisor_user_id=supervisor_user_id)
    return JSONResponse(result)


@router.post("/match-role", response_class=JSONResponse)
def match_role(role_id: int = Form(...)):
    with get_conn() as conn:
        result = handle_match_role(conn, role_id=role_id)
    return JSONResponse(result)
