"""Chat session reads and small edits. The server writes messages itself as
each turn streams (routers/chat_turns.py, C1); this router lists, reads (with
message status), renames, pins, deletes, and imports a legacy browser-only
chat."""
from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError

from ..auth.authz import assert_owns_session
from ..auth.deps import Principal, require_capability
from ..chat import store as chat_store
from ..chat.config import get_chat_config
from ..db import get_pool, is_ready
from ..http_body import read_capped_body
from ..http_errors import refusal

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


class ImportIn(BaseModel):
    """A legacy browser-only (IndexedDB) chat being continued (ruling R6)."""
    title: str = Field(default="New chat", max_length=200)
    model: str | None = Field(default=None, max_length=300)
    # Epoch milliseconds, bounded so an out-of-range value 422s here instead of
    # blowing up to_timestamp() in the INSERT (year ~2286 at the upper bound).
    createdAt: int | None = Field(default=None, ge=0, le=10**13)
    messages: list[MessageIn] = Field(default_factory=list, max_length=2000)
    events: list[EventIn] = Field(default_factory=list, max_length=5000)
    pins: list[dict[str, Any]] = Field(default_factory=list, max_length=6)


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


# ---------- authz ----------

async def _require_session_owner(
    session_id: str,
    principal: Principal = Depends(require_capability("chat")),
) -> Principal:
    """Route dependency: 401 if unauthenticated, 403 if the caller lacks the
    `chat` capability, 404 unless the caller owns the session (missing and
    not-owned are indistinguishable to the caller)."""
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await assert_owns_session(conn, session_id, principal.user_id)
    return principal


# ---------- routes ----------

@router.get("")
async def list_sessions(
    principal: Principal = Depends(require_capability("chat")),
) -> list[dict[str, Any]]:
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
            WHERE s.user_id = %s
            ORDER BY s.updated_at DESC
            """,
            (principal.user_id,),
        )
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]
    return [_row_to_session_meta(dict(zip(cols, r))) for r in rows]


_META_KEYS = ("id", "kind", "name", "mimeType", "size")


@router.post("/import")
async def import_session(
    request: Request,
    principal: Principal = Depends(require_capability("chat")),
) -> dict[str, Any]:
    """Copy a legacy browser-only chat to the server under a NEW id, so it can
    be continued (the old PUT's fork-on-edit). Attachment bytes are never
    stored for imported messages; message ids are always new because they are
    global primary keys."""
    _ensure_ready()
    cfg = get_chat_config()
    # Hand-parsed (not a pydantic Body param): FastAPI's Body() buffers the
    # whole request before any Field(max_length=...) applies, so a hostile
    # caller could force an unbounded buffer before validation ever runs.
    # Reading through the cap first means the bytes handed to the validator
    # are already known to be within CHAT_MAX_REQUEST_MB.
    raw = await read_capped_body(request, cfg.max_request_mb, label="Import")
    try:
        payload = ImportIn.model_validate_json(raw)
    except ValidationError as e:
        raise refusal(422, "invalid_request", "The request is malformed.",
                      errors=e.errors(include_url=False, include_input=False, include_context=False))
    for m in payload.messages:
        if m.role not in ("user", "assistant"):
            raise refusal(422, "invalid_message_role",
                          "Imported messages must be user or assistant turns.")
    session_id = f"s-{chat_store.now_ms()}-{secrets.token_hex(3)}"
    pool = get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO chat_sessions (id, title, model, created_at, updated_at, pins, user_id) "
                "VALUES (%s, %s, %s, COALESCE(to_timestamp(%s::double precision / 1000.0), now()), now(), %s, %s)",
                (session_id, payload.title, payload.model, payload.createdAt, Jsonb(payload.pins),
                 principal.user_id))
            for m in payload.messages:
                meta = [{k: a[k] for k in _META_KEYS if k in a}
                        for a in m.attachments if isinstance(a, dict)]
                await conn.execute(
                    "INSERT INTO chat_messages (id, session_id, role, content, thinking, attachments, "
                    "doc_context, stats, tool_calls, timestamp) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (chat_store.new_message_id(m.role[:1]), session_id, m.role, m.content, m.thinking,
                     Jsonb(meta), Jsonb(m.docContext) if m.docContext is not None else None,
                     Jsonb(m.stats) if m.stats is not None else None,
                     Jsonb(m.toolCalls) if m.toolCalls is not None else None, m.timestamp))
            for ev in payload.events:
                await conn.execute(
                    "INSERT INTO chat_events (session_id, kind, message, ts) VALUES (%s, %s, %s, %s)",
                    (session_id, ev.kind, ev.message, ev.ts))
    return {"ok": True, "id": session_id}


@router.get("/{session_id}")
async def get_session(
    session_id: str, _owner: Principal = Depends(_require_session_owner)
) -> dict[str, Any]:
    _ensure_ready()
    try:
        # A dead worker's turn reads as 'aborted', not forever 'streaming'.
        await chat_store.recover_stale(session_id)
    except Exception:   # an optimization: the read must still work
        logger.warning("Stale-turn check failed for %s", session_id, exc_info=True)
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, title, model, created_at, updated_at, pins FROM chat_sessions WHERE id = %s",
            (session_id,),
        )
        srow = await cur.fetchone()
        if not srow:
            raise HTTPException(status_code=404, detail="Session not found")

        await cur.execute(
            """
            SELECT id, role, content, thinking, attachments, doc_context, stats, tool_calls, timestamp,
                   status, finish_reason, model
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY
                timestamp,
                -- When two messages share a Date.now() ms (the user prompt and
                -- the assistant placeholder are created in the same tick), the
                -- user message must come first. Without this CASE the fallback
                -- to `id` puts `a-<ts>` before `u-<ts>` lexicographically and
                -- the bubble pair renders flipped.
                CASE role
                    WHEN 'system'    THEN 0
                    WHEN 'user'      THEN 1
                    WHEN 'tool'      THEN 2
                    WHEN 'assistant' THEN 3
                    ELSE 4
                END,
                created_at,
                id
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
    for (mid, role, content, thinking, attachments, doc_context, stats, tool_calls, ts,
         status, finish_reason, model) in message_rows:
        msg = {
            "id": mid,
            "role": role,
            "content": content,
            "attachments": attachments or [],
            "timestamp": int(ts) if ts else 0,
            "status": status,
        }
        if thinking:
            msg["thinking"] = thinking
        if doc_context:
            msg["docContext"] = doc_context
        if stats:
            msg["stats"] = stats
        if tool_calls:
            msg["toolCalls"] = tool_calls
        if finish_reason:
            msg["finishReason"] = finish_reason
        if model:
            msg["model"] = model
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
        "pins": srow[5] or [],
        "source": "pg",
    }


@router.patch("/{session_id}")
async def patch_session(
    session_id: str,
    body: dict[str, Any],
    _owner: Principal = Depends(_require_session_owner),
) -> dict[str, Any]:
    """
    Partial update of session metadata. Only `title` and `model` are honored;
    messages are written by turns.
    """
    _ensure_ready()
    title = body.get("title")
    model = body.get("model")
    pins = body.get("pins")
    if title is None and model is None and pins is None:
        return {"ok": True, "id": session_id, "noop": True}

    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        # Only update provided fields; leave the rest untouched.
        await cur.execute(
            """
            UPDATE chat_sessions
            SET title = COALESCE(%s, title),
                model = COALESCE(%s, model),
                pins  = COALESCE(%s, pins),
                updated_at = now()
            WHERE id = %s
            RETURNING id
            """,
            (title, model, Jsonb(pins) if pins is not None else None, session_id),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "id": session_id}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str, _owner: Principal = Depends(_require_session_owner)
) -> dict[str, Any]:
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("DELETE FROM chat_sessions WHERE id = %s RETURNING id", (session_id,))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "id": session_id}
