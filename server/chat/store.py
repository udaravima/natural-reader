"""Every chat read and write a turn makes (spec §5.5, §7.3).

Each function opens its own SHORT pooled connection: a reply can stream for
minutes, and holding a pooled connection that long would drain the pool.
Writes aimed at a session that was deleted mid-turn simply match no rows.
"""
from __future__ import annotations

import base64
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from psycopg.types.json import Jsonb

from ..db import get_pool
from ..llm.types import Attachment

logger = logging.getLogger(__name__)

HEARTBEAT_S = 15.0     # a running turn refreshes its claim this often (seconds)
STALE_AFTER_S = 60     # a claim older than this belongs to a dead worker (seconds)
FLUSH_EVERY_S = 2.0    # partial content is saved at most this often (seconds)


class NotOwner(Exception):
    """The session exists and belongs to someone else."""


class TurnInProgress(Exception):
    """Another turn holds this session's claim and its heartbeat is fresh."""


@dataclass(frozen=True)
class NewAttachment:
    kind: str
    mime: str
    name: str
    data: bytes = field(repr=False)   # never let a stray repr/f-string dump image bytes into logs
    client_id: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class TurnClaim:
    session_id: str
    user_message_id: str
    assistant_message_id: str
    created_session: bool

    @property
    def turn_id(self) -> str:
        # ruling R8: the claim IS the streaming assistant message's id
        return self.assistant_message_id


@dataclass(frozen=True)
class StoredMessage:
    id: str
    role: str
    content: str
    attachments: list[dict[str, Any]]   # metadata with "ordinal" (C1 rows only)


@dataclass(frozen=True)
class SessionInfo:
    owner_id: str
    pins: list[dict[str, Any]]


def now_ms() -> int:
    return int(time.time() * 1000)


def new_message_id(prefix: str) -> str:
    return f"{prefix}-{now_ms()}-{secrets.token_hex(4)}"


def title_from_prompt(text: str) -> str:
    """The SPA's old titleFromPrompt: one line, at most 60 characters."""
    one = " ".join((text or "").split())
    if not one:
        return "New chat"
    return one if len(one) <= 60 else one[:59] + "…"


# Clears claims whose heartbeat stopped and marks their message aborted.
# %(sid)s NULL = every session (the startup pass); otherwise just that one.
_RECOVER_SQL = """
WITH stale AS (
    SELECT id, active_turn_id FROM chat_sessions
    WHERE active_turn_id IS NOT NULL
      AND active_turn_heartbeat_at < now() - make_interval(secs => %(stale_s)s::double precision)
      AND (%(sid)s::text IS NULL OR id = %(sid)s::text)
    FOR UPDATE
), cleared AS (
    UPDATE chat_sessions s SET active_turn_id = NULL, active_turn_heartbeat_at = NULL
    FROM stale WHERE s.id = stale.id
    RETURNING stale.active_turn_id AS turn_id
), aborted AS (
    UPDATE chat_messages m SET status = 'aborted', finish_reason = 'aborted'
    FROM cleared WHERE m.id = cleared.turn_id AND m.status = 'streaming'
    RETURNING m.id
)
SELECT (SELECT count(*) FROM cleared), (SELECT count(*) FROM aborted)
"""


async def _recover(conn, session_id: str | None) -> tuple[int, int]:
    cur = await conn.execute(_RECOVER_SQL, {"stale_s": STALE_AFTER_S, "sid": session_id})
    claims, aborted = await cur.fetchone()
    return claims, aborted


async def recover_stale(session_id: str | None = None) -> int:
    """Clear claims whose worker stopped heartbeating, and abort their reply
    if it was still streaming. Safe with WORKERS > 1: a live turn's heartbeat
    is never older than HEARTBEAT_S (spec §5.5). Returns the claims cleared."""
    async with get_pool().connection() as conn:
        claims, aborted = await _recover(conn, session_id)
    if claims:
        logger.warning("Cleared %d stale chat claim(s); %d streaming repl(ies) marked aborted",
                       claims, aborted)
    return claims


async def session_info(session_id: str) -> SessionInfo | None:
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT user_id, pins FROM chat_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
    return SessionInfo(str(row[0]), row[1] or []) if row else None


async def begin_turn(*, session_id: str, user_id: str, model_id: str, text: str,
                     attachments: list[NewAttachment],
                     new_session_pins: list[dict] | None) -> TurnClaim:
    """Create the session if new, claim it, and write the user message (with
    attachment bytes) and the streaming assistant message — one transaction,
    committed before the first byte streams (spec §7.3)."""
    user_mid, asst_mid = new_message_id("u"), new_message_id("a")
    async with get_pool().connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO chat_sessions (id, title, model, pins, user_id) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                (session_id, title_from_prompt(text), model_id, Jsonb(new_session_pins or []), user_id))
            created = await cur.fetchone() is not None
            cur = await conn.execute("SELECT user_id FROM chat_sessions WHERE id = %s FOR UPDATE", (session_id,))
            row = await cur.fetchone()
            if row is None or str(row[0]) != str(user_id):
                raise NotOwner(session_id)
            await _recover(conn, session_id)
            cur = await conn.execute(
                "UPDATE chat_sessions SET active_turn_id = %s, active_turn_heartbeat_at = now(), "
                "model = %s, updated_at = now() WHERE id = %s AND active_turn_id IS NULL RETURNING id",
                (asst_mid, model_id, session_id))
            if await cur.fetchone() is None:
                raise TurnInProgress(session_id)
            # Monotonic timestamps: the UI sorts by them, and two fast turns can
            # land in the same millisecond.
            cur = await conn.execute(
                "SELECT COALESCE(MAX(timestamp), 0) FROM chat_messages WHERE session_id = %s", (session_id,))
            ts = max(now_ms(), (await cur.fetchone())[0] + 1)
            meta = [{"id": a.client_id or f"att-{i}", "kind": a.kind, "name": a.name,
                     "mimeType": a.mime, "size": a.size, "ordinal": i}   # ruling R11
                    for i, a in enumerate(attachments)]
            await conn.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, attachments, timestamp, status, model) "
                "VALUES (%s, %s, 'user', %s, %s, %s, 'complete', %s)",
                (user_mid, session_id, text, Jsonb(meta), ts, model_id))
            for i, a in enumerate(attachments):
                await conn.execute(
                    "INSERT INTO chat_attachments (message_id, ordinal, kind, mime, name, size, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (user_mid, i, a.kind, a.mime, a.name, a.size, a.data))
            await conn.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, timestamp, status, model) "
                "VALUES (%s, %s, 'assistant', '', %s, 'streaming', %s)",
                (asst_mid, session_id, ts + 1, model_id))
    return TurnClaim(session_id, user_mid, asst_mid, created)


async def heartbeat(session_id: str, turn_id: str) -> bool:
    """False when this turn no longer holds the claim (taken over, or the chat was deleted)."""
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "UPDATE chat_sessions SET active_turn_heartbeat_at = now() "
            "WHERE id = %s AND active_turn_id = %s RETURNING id", (session_id, turn_id))
        return await cur.fetchone() is not None


def _json_or_null(value: Any) -> Jsonb | None:
    return Jsonb(value) if value else None


async def save_progress(message_id: str, *, content: str, thinking: str,
                        tool_calls: list | None, doc_context: dict | None) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE chat_messages SET content = %s, thinking = %s, tool_calls = %s, doc_context = %s "
            "WHERE id = %s",
            (content, thinking or None, _json_or_null(tool_calls), _json_or_null(doc_context), message_id))


async def finish_turn(claim: TurnClaim, *, status: str, finish_reason: str | None, content: str,
                      thinking: str, tool_calls: list | None, doc_context: dict | None,
                      stats: dict | None) -> None:
    async with get_pool().connection() as conn:
        async with conn.transaction():
            # Session before message, always — begin_turn's claim (SELECT ...
            # FOR UPDATE on the session, then _recover's message update),
            # recover_stale's CTE, and a chat DELETE's cascade all take that
            # same order. Taking message-then-session here would invert it and
            # deadlock against any of those under concurrency.
            await conn.execute(
                "UPDATE chat_sessions SET active_turn_id = NULL, active_turn_heartbeat_at = NULL, "
                "updated_at = now() WHERE id = %s AND active_turn_id = %s",
                (claim.session_id, claim.turn_id))
            # If the claim was already taken over, the UPDATE above matches
            # nothing — but this write is still this turn's own message, and
            # its content is still truthful, so writing it late is fine; it
            # just won't touch a claim that has since moved on.
            await conn.execute(
                "UPDATE chat_messages SET status = %s, finish_reason = %s, content = %s, thinking = %s, "
                "tool_calls = %s, doc_context = %s, stats = %s WHERE id = %s",
                (status, finish_reason, content, thinking or None, _json_or_null(tool_calls),
                 _json_or_null(doc_context), _json_or_null(stats), claim.assistant_message_id))


async def add_event(session_id: str, kind: str, message: str) -> None:
    """One line of the chat's event log (the sidebar's Log). Written by the
    server now that the browser no longer saves whole sessions."""
    async with get_pool().connection() as conn:
        await conn.execute(
            "INSERT INTO chat_events (session_id, kind, message, ts) "
            "SELECT %s, %s, %s, %s WHERE EXISTS (SELECT 1 FROM chat_sessions WHERE id = %s)",
            (session_id, kind, message, now_ms(), session_id))


async def load_turn_context(session_id: str, *, exclude: tuple[str, ...]
                            ) -> tuple[list[dict], list[StoredMessage]]:
    """The session's pins and its finished history, oldest first, without the
    current turn's own two messages. Attachment METADATA only; bytes are
    fetched later for the attachments that survive trimming (spec §5.2)."""
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT pins FROM chat_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
        pins = (row[0] if row else None) or []
        cur = await conn.execute(
            "SELECT id, role, content, attachments FROM chat_messages "
            "WHERE session_id = %s AND role IN ('user', 'assistant') AND status <> 'streaming' "
            "AND NOT (id = ANY(%s)) "
            "ORDER BY timestamp, CASE role WHEN 'user' THEN 1 ELSE 3 END, created_at, id",
            (session_id, list(exclude)))
        rows = await cur.fetchall()
    history: list[StoredMessage] = []
    for mid, role, content, atts in rows:
        if role == "assistant" and not (content or "").strip():
            continue   # a failed or empty reply adds nothing the model can use
        meta = [a for a in (atts or []) if isinstance(a, dict) and "ordinal" in a]
        history.append(StoredMessage(mid, role, content or "", meta))
    return pins, history


async def load_attachment_bytes(wanted: list[tuple[str, int]]) -> dict[tuple[str, int], Attachment]:
    if not wanted:
        return {}
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT a.message_id, a.ordinal, a.kind, a.mime, a.name, a.data FROM chat_attachments a "
            "JOIN unnest(%s::text[], %s::int[]) AS w(mid, ord) "
            "ON a.message_id = w.mid AND a.ordinal = w.ord",
            ([w[0] for w in wanted], [w[1] for w in wanted]))
        rows = await cur.fetchall()
    return {(mid, ordinal): Attachment(kind, mime, base64.b64encode(bytes(data)).decode("ascii"), name)
            for mid, ordinal, kind, mime, name, data in rows}
