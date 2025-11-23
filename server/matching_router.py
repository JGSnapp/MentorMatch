"""Маршруты для запуска операций сопоставления администраторами."""
from __future__ import annotations

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from clients.matching_client import (
    match_role as trigger_match_role,
    match_role_applicants as trigger_match_role_applicants,
    match_topic_applicants as trigger_match_topic_applicants,
    match_student as trigger_match_student,
    match_supervisor as trigger_match_supervisor,
    match_topic as trigger_match_topic,
)


def create_matching_router() -> APIRouter:
    """Создаёт роутер FastAPI для административных операций подбора."""
    router = APIRouter()

    @router.post("/match-topic", response_class=JSONResponse)
    def match_topic(topic_id: int = Form(...), target_role: str = Form("student")):
        """Запускает подбор по теме с указанной целевой ролью."""
        result = trigger_match_topic(topic_id, target_role=target_role)
        status = 200 if result.get("status") == "ok" else 400
        return JSONResponse(result, status_code=status)

    @router.post("/match-student", response_class=JSONResponse)
    def match_student(student_user_id: int = Form(...)):
        """Вызывает подбор наставника для выбранного студента."""
        result = trigger_match_student(student_user_id)
        status = 200 if result.get("status") == "ok" else 400
        return JSONResponse(result, status_code=status)

    @router.post("/match-supervisor", response_class=JSONResponse)
    def match_supervisor(supervisor_user_id: int = Form(...)):
        """Вызывает подбор студентов для выбранного наставника."""
        result = trigger_match_supervisor(supervisor_user_id)
        status = 200 if result.get("status") == "ok" else 400
        return JSONResponse(result, status_code=status)

    @router.post("/match-role", response_class=JSONResponse)
    def match_role(role_id: int = Form(...)):
        """Запускает подбор пользователей для выбранной роли."""
        result = trigger_match_role(role_id)
        status = 200 if result.get("status") == "ok" else 400
        return JSONResponse(result, status_code=status)

    @router.post("/match-role-applicants", response_class=JSONResponse)
    def match_role_applicants(role_id: int = Form(...)):
        """Запускает сортировку откликов на роль."""

        result = trigger_match_role_applicants(role_id)
        status = 200 if result.get("status") == "ok" else 400
        return JSONResponse(result, status_code=status)

    @router.post("/match-topic-applicants", response_class=JSONResponse)
    def match_topic_applicants(topic_id: int = Form(...)):
        """Запускает сортировку откликов на тему."""

        result = trigger_match_topic_applicants(topic_id)
        status = 200 if result.get("status") == "ok" else 400
        return JSONResponse(result, status_code=status)

    return router


__all__ = ["create_matching_router"]
