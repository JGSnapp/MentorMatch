from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

MATCHING_SERVICE_URL = os.getenv("MATCHING_SERVICE_URL", "http://matching:8300")
logger = logging.getLogger(__name__)


def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Выполняет функцию _post."""
    url = f"{MATCHING_SERVICE_URL.rstrip('/')}{path}"
    try:
        response = httpx.post(url, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()
    except Exception as exc:                                        
        logger.warning("Matching service POST %s failed: %s", url, exc)
        return {"status": "error", "message": str(exc)}


def refresh_student_embedding(student_user_id: int, *, model_repo_id: Optional[str] = None) -> None:
    """Выполняет функцию refresh_student_embedding."""
    payload: Dict[str, Any] = {"student_user_id": student_user_id}
    if model_repo_id:
        payload["model_repo_id"] = model_repo_id
    _post("/api/embeddings/student/refresh", payload)


def refresh_supervisor_embedding(supervisor_user_id: int, *, model_repo_id: Optional[str] = None) -> None:
    """Выполняет функцию refresh_supervisor_embedding."""
    payload: Dict[str, Any] = {"supervisor_user_id": supervisor_user_id}
    if model_repo_id:
        payload["model_repo_id"] = model_repo_id
    _post("/api/embeddings/supervisor/refresh", payload)


def refresh_topic_embedding(topic_id: int, *, model_repo_id: Optional[str] = None) -> None:
    """Выполняет функцию refresh_topic_embedding."""
    payload: Dict[str, Any] = {"topic_id": topic_id}
    if model_repo_id:
        payload["model_repo_id"] = model_repo_id
    _post("/api/embeddings/topic/refresh", payload)


def refresh_role_embedding(role_id: int, *, model_repo_id: Optional[str] = None) -> None:
    """Выполняет функцию refresh_role_embedding."""
    payload: Dict[str, Any] = {"role_id": role_id}
    if model_repo_id:
        payload["model_repo_id"] = model_repo_id
    _post("/api/embeddings/role/refresh", payload)


def match_topic(topic_id: int, *, target_role: Optional[str] = None) -> Dict[str, Any]:
    """Выполняет функцию match_topic."""
    payload: Dict[str, Any] = {"topic_id": topic_id}
    if target_role:
        payload["target_role"] = target_role
    return _post("/api/match/topic", payload)


def match_role(role_id: int) -> Dict[str, Any]:
    """Выполняет функцию match_role."""
    return _post("/api/match/role", {"role_id": role_id})


def match_role_applicants(role_id: int) -> Dict[str, Any]:
    """Возвращает ранжированный список заявок на роль."""

    return _post("/api/match/role-applicants", {"role_id": role_id})


def match_topic_applicants(topic_id: int) -> Dict[str, Any]:
    """Возвращает ранжированный список заявок на тему."""

    return _post("/api/match/topic-applicants", {"topic_id": topic_id})


def match_student(student_user_id: int) -> Dict[str, Any]:
    """Выполняет функцию match_student."""
    return _post("/api/match/student", {"user_id": student_user_id})


def match_supervisor(supervisor_user_id: int) -> Dict[str, Any]:
    """Выполняет функцию match_supervisor."""
    return _post("/api/match/supervisor", {"user_id": supervisor_user_id})


__all__ = [
    "refresh_student_embedding",
    "refresh_supervisor_embedding",
    "refresh_topic_embedding",
    "refresh_role_embedding",
    "match_topic",
    "match_role",
    "match_role_applicants",
    "match_topic_applicants",
    "match_student",
    "match_supervisor",
]
