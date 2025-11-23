from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .db import get_conn
from .embeddings import (
    refresh_role_embedding,
    refresh_student_embedding,
    refresh_supervisor_embedding,
    refresh_topic_embedding,
)
from .service import (
    handle_match,
    handle_match_role,
    handle_match_role_applicants,
    handle_match_topic_applicants,
    handle_match_student,
    handle_match_supervisor_user,
)
from .llm import MatchingLLMClient, create_matching_llm_client


def _configure_logging() -> logging.Logger:
    """Выполняет функцию _configure_logging."""
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger("matching")


load_dotenv()
logger = _configure_logging()

app = FastAPI(title="MentorMatch Matching Service")


class StudentEmbeddingPayload(BaseModel):
    student_user_id: int
    model_repo_id: Optional[str] = None


class SupervisorEmbeddingPayload(BaseModel):
    supervisor_user_id: int
    model_repo_id: Optional[str] = None


class TopicEmbeddingPayload(BaseModel):
    topic_id: int
    model_repo_id: Optional[str] = None


class RoleEmbeddingPayload(BaseModel):
    role_id: int
    model_repo_id: Optional[str] = None


class TopicMatchPayload(BaseModel):
    topic_id: int
    target_role: Optional[str] = None


class RoleMatchPayload(BaseModel):
    role_id: int


class UserMatchPayload(BaseModel):
    user_id: int


class TopicApplicantsPayload(BaseModel):
    topic_id: int


def _model_args(model_repo_id: Optional[str]) -> dict[str, object]:
    """Выполняет функцию _model_args."""
    return {"model_repo_id": model_repo_id} if model_repo_id else {}


def _llm_client() -> MatchingLLMClient | None:
    """Выполняет функцию _llm_client."""
    try:
        return create_matching_llm_client()
    except Exception as exc:                                        
        logger.warning("Failed to create LLM client: %s", exc)
        return None


@app.get("/health", response_class=JSONResponse)
def health_check() -> dict[str, str]:
    """Выполняет функцию health_check."""
    return {"status": "ok"}


@app.post("/api/embeddings/student/refresh", response_class=JSONResponse)
def refresh_student(payload: StudentEmbeddingPayload) -> JSONResponse:
    """Выполняет функцию refresh_student."""
    with get_conn() as conn:
        refresh_student_embedding(
            conn,
            payload.student_user_id,
            commit=True,
            **_model_args(payload.model_repo_id),
        )
    return JSONResponse({"status": "ok", "student_user_id": payload.student_user_id})


@app.post("/api/embeddings/supervisor/refresh", response_class=JSONResponse)
def refresh_supervisor(payload: SupervisorEmbeddingPayload) -> JSONResponse:
    """Выполняет функцию refresh_supervisor."""
    with get_conn() as conn:
        refresh_supervisor_embedding(
            conn,
            payload.supervisor_user_id,
            commit=True,
            **_model_args(payload.model_repo_id),
        )
    return JSONResponse({"status": "ok", "supervisor_user_id": payload.supervisor_user_id})


@app.post("/api/embeddings/topic/refresh", response_class=JSONResponse)
def refresh_topic(payload: TopicEmbeddingPayload) -> JSONResponse:
    """Выполняет функцию refresh_topic."""
    with get_conn() as conn:
        refresh_topic_embedding(
            conn,
            payload.topic_id,
            commit=True,
            **_model_args(payload.model_repo_id),
        )
    return JSONResponse({"status": "ok", "topic_id": payload.topic_id})


@app.post("/api/embeddings/role/refresh", response_class=JSONResponse)
def refresh_role(payload: RoleEmbeddingPayload) -> JSONResponse:
    """Выполняет функцию refresh_role."""
    with get_conn() as conn:
        refresh_role_embedding(
            conn,
            payload.role_id,
            commit=True,
            **_model_args(payload.model_repo_id),
        )
    return JSONResponse({"status": "ok", "role_id": payload.role_id})


@app.post("/api/match/topic", response_class=JSONResponse)
def match_topic(payload: TopicMatchPayload) -> JSONResponse:
    """Выполняет функцию match_topic."""
    llm = _llm_client()
    with get_conn() as conn:
        result = handle_match(
            conn,
            topic_id=payload.topic_id,
            target_role=payload.target_role,
            llm_client=llm,
        )
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return JSONResponse(result)


@app.post("/api/match/role", response_class=JSONResponse)
def match_role(payload: RoleMatchPayload) -> JSONResponse:
    """Выполняет функцию match_role."""
    llm = _llm_client()
    with get_conn() as conn:
        result = handle_match_role(conn, role_id=payload.role_id, llm_client=llm)
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return JSONResponse(result)


@app.post("/api/match/role-applicants", response_class=JSONResponse)
def match_role_applicants(payload: RoleMatchPayload) -> JSONResponse:
    """Отбирает лучших студентов среди подавших заявки."""

    llm = _llm_client()
    with get_conn() as conn:
        result = handle_match_role_applicants(
            conn, role_id=payload.role_id, llm_client=llm
        )
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return JSONResponse(result)


@app.post("/api/match/topic-applicants", response_class=JSONResponse)
def match_topic_applicants(payload: TopicApplicantsPayload) -> JSONResponse:
    """Отбирает лучших студентов среди подавших заявки на тему."""

    llm = _llm_client()
    with get_conn() as conn:
        result = handle_match_topic_applicants(
            conn, topic_id=payload.topic_id, llm_client=llm
        )
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return JSONResponse(result)


@app.post("/api/match/student", response_class=JSONResponse)
def match_student(payload: UserMatchPayload) -> JSONResponse:
    """Выполняет функцию match_student."""
    llm = _llm_client()
    with get_conn() as conn:
        result = handle_match_student(conn, student_user_id=payload.user_id, llm_client=llm)
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return JSONResponse(result)


@app.post("/api/match/supervisor", response_class=JSONResponse)
def match_supervisor(payload: UserMatchPayload) -> JSONResponse:
    """Выполняет функцию match_supervisor."""
    llm = _llm_client()
    with get_conn() as conn:
        result = handle_match_supervisor_user(conn, supervisor_user_id=payload.user_id, llm_client=llm)
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("matching.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8300")), reload=False)
