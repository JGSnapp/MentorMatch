"""Service exposing matching and embedding operations."""
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse

from app.api import create_matching_router
from app.config import configure_logging, get_conn
from app.matching.embeddings import (
    refresh_role_embedding,
    refresh_student_embedding,
    refresh_supervisor_embedding,
    refresh_topic_embedding,
)

configure_logging()

app = FastAPI(title="MentorMatch Matching Service")

app.include_router(create_matching_router(get_conn))


@app.post("/embeddings/refresh", response_class=JSONResponse)
def refresh_embedding(entity_type: str = Form(...), entity_id: int = Form(...)):
    normalized = (entity_type or "").strip().lower()
    with get_conn() as conn:
        if normalized == "role":
            refresh_role_embedding(conn, entity_id)
        elif normalized == "student":
            refresh_student_embedding(conn, entity_id)
        elif normalized == "supervisor":
            refresh_supervisor_embedding(conn, entity_id)
        elif normalized == "topic":
            refresh_topic_embedding(conn, entity_id)
        else:
            raise HTTPException(status_code=400, detail="unsupported entity_type")
        conn.commit()
    return JSONResponse({"status": "ok", "entity_type": normalized, "entity_id": entity_id})


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}
