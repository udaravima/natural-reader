"""The phone lines' SQL: lines and their service users, recorded clips, and calls (spec 4.3, 5.1, 5.5).

A line acts through its own service user (users.kind = 'line'), a reader member of the line's project, so access,
budgets and the chat engine work unchanged. A call is a phone_calls row plus one chat (the transcript) owned by that
user. Each writing function runs in its own `conn.transaction()`, which nests as a savepoint when the caller already
has one (the admin API wraps create/update + save_clips in a single transaction so they are all-or-nothing).

Logs carry ids only, never transcripts or the greeting text."""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from server.auth.authz import require_project_role
from server.chat.store import new_message_id
from server.services import project_events
from server.services.deployment_settings import deployment_zone
from server.services.project_members import put_member

logger = logging.getLogger(__name__)

# What update_line accepts. `language` is fixed to 'en' in phase 1.
_CHANGEABLE = frozenset({"name", "enabled", "profile_id", "project_id", "model_id", "voice", "greeting",
                         "max_call_s", "max_calls", "daily_tokens"})


@dataclass(frozen=True)
class LineVoice:
    engine: str
    voice: str
    accent: str | None


@dataclass(frozen=True)
class Line:
    id: str
    name: str
    enabled: bool
    profile_id: str | None
    project_id: str | None
    model_id: str
    voice: LineVoice
    language: str
    greeting: str
    max_call_s: int
    max_calls: int
    service_user_id: str
    daily_tokens: int | None      # the service user's inference_daily_token_budget; None = the deployment default


@dataclass(frozen=True)
class Clip:
    kind: str
    ordinal: int
    text: str
    voice_key: str                # engine:voice:accent the audio was made with
    pcm: bytes                    # s16le mono 8 kHz
    rendered_at: datetime


_LINE_SQL = (
    "SELECT l.id, l.name, l.enabled, l.profile_id, l.project_id, l.model_id, l.voice, l.language, l.greeting, "
    "l.max_call_s, l.max_calls, l.service_user_id, u.inference_daily_token_budget "
    "FROM phone_lines l JOIN users u ON u.id = l.service_user_id")


def _str_or_none(value) -> str | None:
    return None if value is None else str(value)


def _line(row) -> Line:
    (lid, name, enabled, profile_id, project_id, model_id, voice, language, greeting, max_call_s, max_calls,
     service_user_id, daily_tokens) = row
    return Line(id=str(lid), name=name, enabled=enabled, profile_id=_str_or_none(profile_id),
                project_id=_str_or_none(project_id), model_id=model_id,
                voice=LineVoice(voice["engine"], voice["voice"], voice.get("accent")),
                language=language, greeting=greeting, max_call_s=max_call_s, max_calls=max_calls,
                service_user_id=str(service_user_id), daily_tokens=daily_tokens)


def _voice_json(voice: LineVoice) -> Jsonb:
    return Jsonb({"engine": voice.engine, "voice": voice.voice, "accent": voice.accent})


async def get_line(conn, line_id: str) -> Line | None:
    try:
        uuid.UUID(str(line_id))
    except ValueError:
        return None
    cur = await conn.execute(_LINE_SQL + " WHERE l.id = %s", (str(line_id),))
    row = await cur.fetchone()
    return _line(row) if row else None


async def list_lines(conn) -> list[Line]:
    cur = await conn.execute(_LINE_SQL + " ORDER BY l.created_at, l.id")
    return [_line(r) for r in await cur.fetchall()]


async def _attach(conn, principal, project_id: str, user_id: str) -> None:
    """Make the line's user a reader of the project. Publishing a project to anonymous callers needs a real
    maintainer or owner of it: an admin who isn't a member is refused (404), as chat refuses them the project's
    documents (admin_ok=False), and a member below maintainer gets 403."""
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await put_member(conn, principal, project_id, user_id, "reader", show_email=False, line=True)


async def create_line(conn, principal, *, name: str, profile_id: str | None, project_id: str | None,
                      model_id: str, voice: LineVoice, greeting: str, max_call_s: int = 600,
                      max_calls: int = 2, daily_tokens: int | None = None) -> Line:
    """A new line, switched off, with its service user and (when a project is given) that user as the project's
    reader. Attaching a project needs a maintainer or owner (put_member's rule); a refusal rolls everything back."""
    line_id = str(uuid.uuid4())
    async with conn.transaction():
        cur = await conn.execute(
            "INSERT INTO users (email, display_name, role, status, kind, capabilities, "
            "inference_daily_token_budget) VALUES (%s, %s, 'member', 'active', 'line', '{chat}', %s) RETURNING id",
            (f"line-{line_id}@phone-line.invalid", f"Phone line: {name}", daily_tokens))
        user_id = str((await cur.fetchone())[0])
        await conn.execute(
            "INSERT INTO phone_lines (id, name, profile_id, project_id, model_id, voice, greeting, max_call_s, "
            "max_calls, service_user_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (line_id, name, profile_id, project_id, model_id, _voice_json(voice), greeting, max_call_s,
             max_calls, user_id, principal.user_id))
        if project_id is not None:
            await _attach(conn, principal, project_id, user_id)
    logger.info("phone line %s created by %s", line_id, principal.user_id)
    return await get_line(conn, line_id)


async def _drop_memberships(conn, principal, user_id: str, *, keep_project: str | None) -> None:
    """Take the line's user out of every project but `keep_project`. No maintainer check: shrinking who can hear
    a project is always allowed (R2.2)."""
    cur = await conn.execute(
        "DELETE FROM project_members WHERE user_id = %s AND (%s::uuid IS NULL OR project_id <> %s::uuid) "
        "RETURNING project_id, role", (user_id, keep_project, keep_project))
    for project_id, role in await cur.fetchall():
        await project_events.record(conn, str(project_id), principal.user_id, "member.removed",
                                    subject_user_id=user_id, details={"role": role})


async def update_line(conn, principal, line_id: str, **changes: Any) -> Line:
    """Change a line. A new project is attached first (so a non-maintainer is refused before anything moves),
    then the old membership goes. Clearing the project switches the line off. A name change renames the user."""
    unknown = set(changes) - _CHANGEABLE
    if unknown:
        raise TypeError(f"update_line: unknown field(s) {sorted(unknown)}")
    async with conn.transaction():
        cur = await conn.execute("SELECT project_id, service_user_id FROM phone_lines WHERE id = %s FOR UPDATE",
                                 (line_id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"no phone line {line_id}")
        old_project, user_id = _str_or_none(row[0]), str(row[1])
        new_project = changes.get("project_id", old_project)
        if new_project != old_project:
            if new_project is not None:
                await _attach(conn, principal, new_project, user_id)
            await _drop_memberships(conn, principal, user_id, keep_project=new_project)
            if new_project is None:
                changes["enabled"] = False
        if changes.get("enabled") and new_project is None:
            raise ValueError("a line without a project can't be switched on")
        sets, params = [], []
        for key, value in changes.items():
            if key == "daily_tokens":
                continue
            sets.append(f"{key} = %s")
            params.append(_voice_json(value) if key == "voice" else value)
        if sets:
            await conn.execute(f"UPDATE phone_lines SET {', '.join(sets)}, updated_at = now() WHERE id = %s",
                               [*params, line_id])
        if "name" in changes:
            await conn.execute("UPDATE users SET display_name = %s WHERE id = %s",
                               (f"Phone line: {changes['name']}", user_id))
        if "daily_tokens" in changes:
            await conn.execute("UPDATE users SET inference_daily_token_budget = %s WHERE id = %s",
                               (changes["daily_tokens"], user_id))
    logger.info("phone line %s updated by %s (%s)", line_id, principal.user_id, ",".join(sorted(changes)))
    return await get_line(conn, line_id)


async def restore_access(conn, principal, line_id: str) -> Line:
    """Make the line's user a reader of its current project again, after a maintainer removed it (R11.4). The same
    attach path as create and update, so only a maintainer or owner may; a line that has access keeps it unchanged.
    Raises LookupError for no such line, ValueError for a line with no project."""
    async with conn.transaction():
        cur = await conn.execute("SELECT project_id, service_user_id FROM phone_lines WHERE id = %s FOR UPDATE",
                                 (line_id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"no phone line {line_id}")
        if row[0] is None:
            raise ValueError("a line without a project has no access to restore")
        await _attach(conn, principal, str(row[0]), str(row[1]))
    logger.info("phone line %s: project access restored by %s", line_id, principal.user_id)
    return await get_line(conn, line_id)


async def delete_line(conn, principal, line_id: str) -> int:
    """Delete a line, then its service user (its call chats and project membership cascade). Returns how many
    call transcripts went. The line goes first because phone_lines.service_user_id is ON DELETE RESTRICT."""
    async with conn.transaction():
        cur = await conn.execute("DELETE FROM phone_lines WHERE id = %s RETURNING service_user_id", (line_id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"no phone line {line_id}")
        user_id = str(row[0])
        cur = await conn.execute("SELECT count(*) FROM chat_sessions WHERE user_id = %s", (user_id,))
        transcripts = (await cur.fetchone())[0]
        await conn.execute("DELETE FROM users WHERE id = %s AND kind = 'line'", (user_id,))
    logger.info("phone line %s deleted by %s (%d transcripts)", line_id, principal.user_id, transcripts)
    return transcripts


async def save_clips(conn, line_id: str, clips: list[Clip]) -> None:
    """Replace the line's whole set of clips, so a re-render always moves clips_stamp."""
    async with conn.transaction():
        await conn.execute("DELETE FROM phone_line_clips WHERE line_id = %s", (line_id,))
        for c in clips:
            await conn.execute(
                "INSERT INTO phone_line_clips (line_id, kind, ordinal, text, voice_key, pcm, rendered_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (line_id, c.kind, c.ordinal, c.text, c.voice_key, c.pcm, c.rendered_at))


async def load_clips(conn, line_id: str) -> list[Clip]:
    cur = await conn.execute(
        "SELECT kind, ordinal, text, voice_key, pcm, rendered_at FROM phone_line_clips "
        "WHERE line_id = %s ORDER BY kind, ordinal", (line_id,))
    return [Clip(k, o, t, vk, bytes(p), at) for k, o, t, vk, p, at in await cur.fetchall()]


async def clips_stamp(conn, line_id: str) -> datetime | None:
    """The newest rendered_at among the line's clips; None when it has none (the ClipCache's change check)."""
    cur = await conn.execute("SELECT max(rendered_at) FROM phone_line_clips WHERE line_id = %s", (line_id,))
    return (await cur.fetchone())[0]


async def open_call(conn, line: Line, *, session_id: str, now_ms: int) -> str:
    """Start a call: its chat (owned by the line's user, titled with the local time, scoped to the line's project),
    the greeting as the first assistant message, and the phone_calls row. Returns the call id. The chat is written
    as chat.store.begin_turn writes one, so the first turn can claim it."""
    async with conn.transaction():
        zone = await deployment_zone(conn, os.environ)
        title = "Call " + datetime.fromtimestamp(now_ms / 1000, zone).strftime("%Y-%m-%d %H:%M")
        # title_source 'person': the helper only renames 'default' and 'cut' titles, and this one should stay.
        await conn.execute(
            "INSERT INTO chat_sessions (id, title, title_source, model, pins, user_id, scope_project_id) "
            "VALUES (%s, %s, 'person', %s, %s, %s, %s)",
            (session_id, title, line.model_id, Jsonb([]), line.service_user_id, line.project_id))
        await conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, timestamp, status, model) "
            "VALUES (%s, %s, 'assistant', %s, %s, 'complete', %s)",
            (new_message_id("a"), session_id, line.greeting, now_ms, line.model_id))
        cur = await conn.execute("INSERT INTO phone_calls (line_id, session_id) VALUES (%s, %s) RETURNING id",
                                 (line.id, session_id))
        call_id = str((await cur.fetchone())[0])
    logger.info("phone call %s opened on line %s", call_id, line.id)
    return call_id


async def record_refused_call(conn, line_id: str, end_reason: str) -> str:
    """A call refused at connect ('busy' or 'closed'): a row with no chat, ended the moment it started."""
    async with conn.transaction():
        cur = await conn.execute(
            "INSERT INTO phone_calls (line_id, ended_at, end_reason, turns) VALUES (%s, now(), %s, 0) "
            "RETURNING id", (line_id, end_reason))
        call_id = str((await cur.fetchone())[0])
    logger.info("phone call %s refused on line %s (%s)", call_id, line_id, end_reason)
    return call_id


async def close_call(conn, call_id: str, *, end_reason: str, turns: int, stats: list[dict]) -> None:
    """End a call. A call already ended keeps its first end reason."""
    async with conn.transaction():
        await conn.execute(
            "UPDATE phone_calls SET ended_at = now(), end_reason = %s, turns = %s, stats = %s "
            "WHERE id = %s AND ended_at IS NULL", (end_reason, turns, Jsonb(stats), call_id))
    logger.info("phone call %s closed (%s, %d turns)", call_id, end_reason, turns)


async def close_stale_calls(conn, older_than_s: int) -> int:
    """End calls still open after `older_than_s` seconds (a crash left them): reason 'shutdown'. Returns how many."""
    async with conn.transaction():
        cur = await conn.execute(
            "UPDATE phone_calls SET ended_at = now(), end_reason = 'shutdown' "
            "WHERE ended_at IS NULL AND started_at < now() - make_interval(secs => %s::double precision) "
            "RETURNING id", (older_than_s,))
        closed = len(await cur.fetchall())
    if closed:
        logger.warning("closed %d stale phone call(s)", closed)
    return closed


_CALL_COLS = "id, line_id, session_id, started_at, ended_at, end_reason, turns, stats"


def _call(row) -> dict:
    cid, line_id, session_id, started_at, ended_at, end_reason, turns, stats = row
    return {"id": str(cid), "line_id": str(line_id), "session_id": session_id, "started_at": started_at,
            "ended_at": ended_at, "end_reason": end_reason, "turns": turns, "stats": stats}


async def list_calls(conn, line_id: str, limit: int = 50) -> list[dict]:
    cur = await conn.execute(
        f"SELECT {_CALL_COLS} FROM phone_calls WHERE line_id = %s ORDER BY started_at DESC, id LIMIT %s",
        (line_id, limit))
    return [_call(r) for r in await cur.fetchall()]


async def get_call(conn, call_id: str) -> dict | None:
    """One call with its transcript: `messages`, oldest first (empty for a refused call or a deleted chat). A
    question and its reply in the same millisecond keep that order, as in the chat store (R11.9)."""
    try:
        uuid.UUID(str(call_id))
    except ValueError:
        return None
    cur = await conn.execute(f"SELECT {_CALL_COLS} FROM phone_calls WHERE id = %s", (str(call_id),))
    row = await cur.fetchone()
    if row is None:
        return None
    call = _call(row)
    messages: list[dict] = []
    if call["session_id"]:
        cur = await conn.execute(
            "SELECT id, role, content, timestamp, status, finish_reason FROM chat_messages "
            "WHERE session_id = %s ORDER BY timestamp, CASE role WHEN 'user' THEN 1 ELSE 3 END, created_at, id",
            (call["session_id"],))
        messages = [{"id": i, "role": r, "content": c, "timestamp": t, "status": s, "finish_reason": f}
                    for i, r, c, t, s, f in await cur.fetchall()]
    call["messages"] = messages
    return call


async def delete_call(conn, call_id: str) -> None:
    """Delete a call's chat (its messages cascade), then the row."""
    async with conn.transaction():
        cur = await conn.execute("SELECT session_id FROM phone_calls WHERE id = %s", (call_id,))
        row = await cur.fetchone()
        if row and row[0]:
            await conn.execute("DELETE FROM chat_sessions WHERE id = %s", (row[0],))
        await conn.execute("DELETE FROM phone_calls WHERE id = %s", (call_id,))
