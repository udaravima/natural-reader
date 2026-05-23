"""
Chat session CRUD against Postgres.

Mirrors the IDB session contract the frontend already speaks so we can swap the
storage layer underneath useChatEngine without changing semantics:

  record = { id, title, model, createdAt, updatedAt, messages: [...], events: [...] }

The PUT endpoint is an upsert: replace the session row + delete/reinsert the
session's messages and events in one transaction. This keeps the
"save the whole record" pattern that the frontend uses today; finer-grained
append endpoints can come later if we want to avoid resending history each turn.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..db import get_pool, is_ready

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/chat/sessions", tags=["chat-sessions"])


# ---------- request / response models ----------

class MessageIn(BaseModel):
    id: str
    role: str
    content: str = ""
    thinking: str | None = None
    attachments: list[Any] = Field(default_factory=list)
    docContext: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    toolCalls: list[Any] | None = None
    timestamp: int = 0


class EventIn(BaseModel):
    ts: int
    kind: str
    message: str = ""


class SessionIn(BaseModel):
    id: str
    title: str = "New chat"
    model: str | None = None
    createdAt: int | None = None
    updatedAt: int | None = None
    messages: list[MessageIn] = Field(default_factory=list)
    events: list[EventIn] = Field(default_factory=list)


# ---------- helpers ----------

def _row_to_session_meta(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "model": row["model"],
        "createdAt": _epoch_ms(row["created_at"]),
        "updatedAt": _epoch_ms(row["updated_at"]),
        "messageCount": row["message_count"],
        "source": "pg",
    }


def _epoch_ms(ts) -> int:
    # psycopg returns datetime.datetime; the frontend wants ms since epoch.
    return int(ts.timestamp() * 1000)


def _ensure_ready() -> None:
    if not is_ready():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — chat persistence is offline",
        )


# ---------- routes ----------

@router.get("")
async def list_sessions() -> list[dict[str, Any]]:
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT s.id, s.title, s.model, s.created_at, s.updated_at,
                   COALESCE(m.cnt, 0) AS message_count
            FROM chat_sessions s
            LEFT JOIN (
                SELECT session_id, COUNT(*) AS cnt FROM chat_messages GROUP BY session_id
            ) m ON m.session_id = s.id
            ORDER BY s.updated_at DESC
            """
        )
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]
    return [_row_to_session_meta(dict(zip(cols, r))) for r in rows]


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, title, model, created_at, updated_at FROM chat_sessions WHERE id = %s",
            (session_id,),
        )
        srow = await cur.fetchone()
        if not srow:
            raise HTTPException(status_code=404, detail="Session not found")

        await cur.execute(
            """
            SELECT id, role, content, thinking, attachments, doc_context, stats, tool_calls, timestamp
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY timestamp, created_at, id
            """,
            (session_id,),
        )
        message_rows = await cur.fetchall()

        await cur.execute(
            "SELECT ts, kind, message FROM chat_events WHERE session_id = %s ORDER BY created_at, id",
            (session_id,),
        )
        event_rows = await cur.fetchall()

    messages = []
    for mid, role, content, thinking, attachments, doc_context, stats, tool_calls, ts in message_rows:
        msg = {
            "id": mid,
            "role": role,
            "content": content,
            "attachments": attachments or [],
            "timestamp": int(ts) if ts else 0,
        }
        if thinking:
            msg["thinking"] = thinking
        if doc_context:
            msg["docContext"] = doc_context
        if stats:
            msg["stats"] = stats
        if tool_calls:
            msg["toolCalls"] = tool_calls
        messages.append(msg)

    events = [{"ts": int(ts), "kind": kind, "message": msg} for ts, kind, msg in event_rows]

    return {
        "id": srow[0],
        "title": srow[1],
        "model": srow[2],
        "createdAt": _epoch_ms(srow[3]),
        "updatedAt": _epoch_ms(srow[4]),
        "messages": messages,
        "events": events,
        "source": "pg",
    }


@router.put("/{session_id}")
async def upsert_session(session_id: str, payload: SessionIn) -> dict[str, Any]:
    """
    Upsert the entire session record: replace the session row, then
    delete-and-reinsert its messages and events in one transaction.
    """
    _ensure_ready()
    if payload.id != session_id:
        raise HTTPException(status_code=400, detail="Path id and body id must match")

    pool = get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO chat_sessions (id, title, model, created_at, updated_at)
                VALUES (
                    %s, %s, %s,
                    COALESCE(to_timestamp(%s::double precision / 1000.0), now()),
                    now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    model = EXCLUDED.model,
                    updated_at = now()
                """,
                (
                    payload.id,
                    payload.title,
                    payload.model,
                    payload.createdAt,
                ),
            )

            await conn.execute("DELETE FROM chat_messages WHERE session_id = %s", (payload.id,))
            await conn.execute("DELETE FROM chat_events WHERE session_id = %s", (payload.id,))

            if payload.messages:
                async with conn.cursor() as cur:
                    for m in payload.messages:
                        await cur.execute(
                            """
                            INSERT INTO chat_messages
                                (id, session_id, role, content, thinking, attachments, doc_context, stats, tool_calls, timestamp)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                m.id,
                                payload.id,
                                m.role,
                                m.content,
                                m.thinking,
                                Jsonb(m.attachments),
                                Jsonb(m.docContext) if m.docContext is not None else None,
                                Jsonb(m.stats) if m.stats is not None else None,
                                Jsonb(m.toolCalls) if m.toolCalls is not None else None,
                                m.timestamp,
                            ),
                        )

            if payload.events:
                async with conn.cursor() as cur:
                    for ev in payload.events:
                        await cur.execute(
                            "INSERT INTO chat_events (session_id, kind, message, ts) VALUES (%s, %s, %s, %s)",
                            (payload.id, ev.kind, ev.message, ev.ts),
                        )

    return {"ok": True, "id": payload.id}


@router.patch("/{session_id}")
async def patch_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """
    Partial update of session metadata. Only `title` and `model` are honored;
    timestamps and message data must go through PUT.
    """
    _ensure_ready()
    title = body.get("title")
    model = body.get("model")
    if title is None and model is None:
        return {"ok": True, "id": session_id, "noop": True}

    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        # Only update provided fields; leave the rest untouched.
        await cur.execute(
            """
            UPDATE chat_sessions
            SET title = COALESCE(%s, title),
                model = COALESCE(%s, model),
                updated_at = now()
            WHERE id = %s
            RETURNING id
            """,
            (title, model, session_id),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "id": session_id}


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("DELETE FROM chat_sessions WHERE id = %s RETURNING id", (session_id,))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "id": session_id}
