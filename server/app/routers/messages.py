"""Messaging endpoints for topic applications."""
from __future__ import annotations

from typing import Any, Dict, Optional

import psycopg2.extras
from fastapi import APIRouter, Form, Query
from fastapi.responses import JSONResponse

from sheet_pairs import sync_roles_sheet
from utils import parse_optional_int

from ..db import get_conn
from ..notifications import display_name, send_telegram_notification, shorten

router = APIRouter()


def create_router() -> APIRouter:
    return router


def _fetch_message_context(cur, message_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT
            m.id,
            m.sender_user_id,
            m.receiver_user_id,
            m.topic_id,
            m.role_id,
            m.status,
            sender.full_name AS sender_name,
            sender.role AS sender_role,
            sender.telegram_id AS sender_telegram_id,
            receiver.full_name AS receiver_name,
            receiver.role AS receiver_role,
            receiver.telegram_id AS receiver_telegram_id,
            t.title AS topic_title,
            t.seeking_role AS topic_seeking_role,
            r.name AS role_name
        FROM messages m
        JOIN users sender ON sender.id = m.sender_user_id
        JOIN users receiver ON receiver.id = m.receiver_user_id
        JOIN topics t ON t.id = m.topic_id
        LEFT JOIN roles r ON r.id = m.role_id
        WHERE m.id = %s
        """,
        (message_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _notify_new_application(message: Dict[str, Any]) -> None:
    message_id = message.get("id")
    if message_id is None:
        return
    chat_id = message.get("receiver_telegram_id")
    if not chat_id:
        return
    sender_name = display_name(message.get("sender_name"), message.get("sender_user_id"))
    topic_label = message.get("topic_title") or f"#{message.get('topic_id')}"
    topic_label = shorten(topic_label, 70) or f"#{message.get('topic_id')}"
    role_name = message.get("role_name")
    if role_name:
        text = f"На роль «{role_name}» новая заявка."
    else:
        text = f"На тему «{topic_label}» новая заявка."
    text += f"\nОт: {sender_name}"
    if not role_name:
        text += f"\nТема: {topic_label}"
    send_telegram_notification(
        message.get("receiver_telegram_id"),
        text,
        button_text="Открыть заявку",
        callback_data=f"message_{message_id}",
    )


def _notify_application_update(message: Dict[str, Any], action: str) -> None:
    message_id = message.get("id")
    if message_id is None:
        return
    topic_label = message.get("topic_title") or f"#{message.get('topic_id')}"
    topic_label = shorten(topic_label, 70) or f"#{message.get('topic_id')}"
    role_name = message.get("role_name")

    def _build_result_line(result_verb: str) -> str:
        if role_name:
            line = f"Вашу заявку на роль «{role_name}» {result_verb}."
            if topic_label:
                line += f"\nТема: {topic_label}"
        else:
            line = f"Вашу заявку на тему «{topic_label}» {result_verb}."
        return line

    if action == "accept":
        chat_id = message.get("sender_telegram_id")
        if not chat_id:
            return
        receiver_name = display_name(message.get("receiver_name"), message.get("receiver_user_id"))
        text = _build_result_line("приняли")
        text += f"\nРешение: {receiver_name}"
        send_telegram_notification(
            chat_id,
            text,
            button_text="Открыть заявку",
            callback_data=f"message_{message_id}",
        )
    elif action == "reject":
        chat_id = message.get("sender_telegram_id")
        if not chat_id:
            return
        receiver_name = display_name(message.get("receiver_name"), message.get("receiver_user_id"))
        text = _build_result_line("отклонили")
        text += f"\nРешение: {receiver_name}"
        send_telegram_notification(
            chat_id,
            text,
            button_text="Открыть заявку",
            callback_data=f"message_{message_id}",
        )
    elif action == "cancel":
        chat_id = message.get("receiver_telegram_id")
        if not chat_id:
            return
        sender_name = display_name(message.get("sender_name"), message.get("sender_user_id"))
        text = f"🚫 {sender_name} отменил(а) заявку по теме «{topic_label}»."
        if role_name:
            text += f"\nРоль: {role_name}"
        send_telegram_notification(
            chat_id,
            text,
            button_text="Открыть заявку",
            callback_data=f"message_{message_id}",
        )


@router.post("/api/messages/send", response_class=JSONResponse)
def api_messages_send(
    sender_user_id: int = Form(...),
    receiver_user_id: int = Form(...),
    topic_id: int = Form(...),
    body: str = Form(...),
    role_id: Optional[str] = Form(None),
):
    msg_id: Optional[int] = None
    message_ctx: Optional[Dict[str, Any]] = None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT role FROM users WHERE id=%s", (sender_user_id,))
        sender_row = cur.fetchone() or {}
        sender_role = (sender_row.get("role") or "").strip().lower() if sender_row else None
        if not sender_role:
            return {"status": "error", "message": "sender not found or role undefined"}
        role_id_val = parse_optional_int(role_id) if role_id is not None else None
        if sender_role == "student" and role_id_val is None:
            return {"status": "error", "message": "role_id is required for student applications"}
        if sender_role == "student":
            cur.execute(
                """
                SELECT 1
                FROM roles
                WHERE topic_id = %s AND approved_student_user_id = %s
                LIMIT 1
                """,
                (int(topic_id), sender_user_id),
            )
            if cur.fetchone():
                return {"status": "error", "message": "Вы уже утверждены на роль в этой теме."}
        if role_id_val is not None:
            cur.execute("SELECT 1 FROM roles WHERE id=%s AND topic_id=%s", (role_id_val, int(topic_id)))
            if not cur.fetchone():
                return {"status": "error", "message": "role does not belong to topic"}
        cur.execute(
            """
            INSERT INTO messages(sender_user_id, receiver_user_id, topic_id, role_id, body, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', now())
            RETURNING id
            """,
            (sender_user_id, receiver_user_id, topic_id, role_id_val, body.strip()),
        )
        inserted = cur.fetchone() or {}
        msg_id_raw = inserted.get("id")
        if msg_id_raw is not None:
            try:
                msg_id = int(msg_id_raw)
            except Exception:
                msg_id = msg_id_raw
            else:
                message_ctx = _fetch_message_context(cur, msg_id)
        conn.commit()
    if message_ctx:
        _notify_new_application(message_ctx)
    return {"status": "ok", "message_id": msg_id}


@router.get("/api/messages/inbox", response_class=JSONResponse)
def api_messages_inbox(user_id: int = Query(...), status: Optional[str] = Query(None)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if status:
            cur.execute(
                """
                SELECT m.*, t.title AS topic_title, r.name AS role_name, su.full_name AS sender_name
                FROM messages m
                JOIN users su ON su.id = m.sender_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.receiver_user_id = %s AND m.status = %s
                ORDER BY m.created_at DESC
                """,
                (user_id, status),
            )
        else:
            cur.execute(
                """
                SELECT m.*, t.title AS topic_title, r.name AS role_name, su.full_name AS sender_name
                FROM messages m
                JOIN users su ON su.id = m.sender_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.receiver_user_id = %s
                ORDER BY m.created_at DESC
                """,
                (user_id,),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


@router.get("/api/messages/sent", response_class=JSONResponse)
def api_messages_sent(user_id: int = Query(...), status: Optional[str] = Query(None)):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if status:
            cur.execute(
                """
                SELECT m.*, t.title AS topic_title, r.name AS role_name, ru.full_name AS receiver_name
                FROM messages m
                JOIN users ru ON ru.id = m.receiver_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.sender_user_id = %s AND m.status = %s
                ORDER BY m.created_at DESC
                """,
                (user_id, status),
            )
        else:
            cur.execute(
                """
                SELECT m.*, t.title AS topic_title, r.name AS role_name, ru.full_name AS receiver_name
                FROM messages m
                JOIN users ru ON ru.id = m.receiver_user_id
                JOIN topics t ON t.id = m.topic_id
                LEFT JOIN roles r ON r.id = m.role_id
                WHERE m.sender_user_id = %s
                ORDER BY m.created_at DESC
                """,
                (user_id,),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


@router.post("/api/messages/respond", response_class=JSONResponse)
def api_messages_respond(
    message_id: int = Form(...),
    responder_user_id: int = Form(...),
    action: str = Form(...),
    answer: Optional[str] = Form(None),
):
    act = (action or "").strip().lower()
    if act not in {"accept", "reject", "cancel"}:
        return {"status": "error", "message": "invalid_action"}
    needs_export = False
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        msg = _fetch_message_context(cur, message_id)
        if not msg:
            return {"status": "error", "message": "message not found"}
        if act in ("accept", "reject") and msg.get("receiver_user_id") != responder_user_id:
            return {"status": "error", "message": "only receiver can accept/reject"}
        if act == "cancel" and msg.get("sender_user_id") != responder_user_id:
            return {"status": "error", "message": "only sender can cancel"}
        status_value = "accepted" if act == "accept" else ("rejected" if act == "reject" else "canceled")
        cur.execute(
            "UPDATE messages SET status=%s, answer=%s, responded_at=now() WHERE id=%s",
            (status_value, (answer or None), message_id),
        )
        if act == "accept":
            sender_role = (msg.get("sender_role") or "").strip().lower()
            receiver_role = (msg.get("receiver_role") or "").strip().lower()
            if msg.get("role_id"):
                approved_student_id = None
                if sender_role == "student":
                    approved_student_id = msg.get("sender_user_id")
                elif receiver_role == "student":
                    approved_student_id = msg.get("receiver_user_id")
                else:
                    approved_student_id = msg.get("sender_user_id")
                if approved_student_id:
                    cur.execute(
                        "UPDATE roles SET approved_student_user_id=%s WHERE id=%s",
                        (approved_student_id, msg.get("role_id")),
                    )
                    needs_export = True
            else:
                approved_supervisor_id = None
                if sender_role == "supervisor":
                    approved_supervisor_id = msg.get("sender_user_id")
                elif receiver_role == "supervisor":
                    approved_supervisor_id = msg.get("receiver_user_id")
                else:
                    approved_supervisor_id = msg.get("sender_user_id")
                if approved_supervisor_id:
                    cur.execute(
                        "UPDATE topics SET approved_supervisor_user_id=%s WHERE id=%s",
                        (approved_supervisor_id, msg.get("topic_id")),
                    )
                    needs_export = True
        else:
            actor_id = responder_user_id if act == "reject" else msg.get("sender_user_id")
            actor_role_raw = msg.get("receiver_role") if act == "reject" else msg.get("sender_role")
            actor_role = (actor_role_raw or "").strip().lower()
            if msg.get("role_id") and actor_role == "student" and actor_id:
                cur.execute("SELECT approved_student_user_id FROM roles WHERE id=%s", (msg.get("role_id"),))
                row = cur.fetchone()
                if row and row.get("approved_student_user_id") == actor_id:
                    cur.execute("UPDATE roles SET approved_student_user_id=NULL WHERE id=%s", (msg.get("role_id"),))
                    needs_export = True
            elif not msg.get("role_id") and actor_role == "supervisor" and actor_id:
                cur.execute("SELECT approved_supervisor_user_id FROM topics WHERE id=%s", (msg.get("topic_id"),))
                row = cur.fetchone()
                if row and row.get("approved_supervisor_user_id") == actor_id:
                    cur.execute("UPDATE topics SET approved_supervisor_user_id=NULL WHERE id=%s", (msg.get("topic_id"),))
                    needs_export = True
        conn.commit()
        msg["status"] = status_value
        msg["answer"] = answer or None
        notify_ctx = msg
    if notify_ctx:
        _notify_application_update(notify_ctx, act)
    if needs_export:
        sync_roles_sheet(get_conn)
    return {"status": "ok"}
