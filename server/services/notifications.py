"""Notifications for each person (spec docs/superpowers/specs/2026-11-06-notifications-design.md).

An inbox, not a popup. A sender calls `send` (named people) or `send_admins` (every active admin) where the outcome
happens, in the caller's transaction where the outcome is a database write, so a rolled-back change sends nothing.
A send runs in a savepoint and never raises at runtime: a notice that can't be written is logged, and the caller's
own write still commits.

Two levels, fixed by the kind: 'action' (something stays stuck until a person acts) sorts first; 'info' after. A
needs-action notice whose problem went away by itself is `resolve`d by its key and sorts as information from then on.

Names may be in a notice's title (it's for the person reading it); never in a log line or the audit trail."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Literal, NamedTuple

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)


class Kind(NamedTuple):
    level: Literal["action", "info"]
    plural: str  # a group's title; {n} is how many


KINDS: dict[str, Kind] = {
    "reembed.paused": Kind("action", "Re-embedding paused {n} times"),
    "reembed.done": Kind("info", "{n} re-embeds finished"),
    "rebuild.failed": Kind("action", "{n} documents keep failing to rebuild"),
    "embed.shortened": Kind("info", "Passages were shortened in {n} documents"),
    "doc.index_failed": Kind("action", "{n} documents couldn't be indexed"),
    "doc.indexed": Kind("info", "{n} documents finished indexing"),
    "doc.convert_failed": Kind("action", "{n} conversions failed"),
    "doc.converted": Kind("info", "{n} documents are converted and ready"),
}

ADMIN_MODELS = {"admin": "models"}
ADMIN_OVERVIEW = {"admin": "overview"}


def doc_target(doc_id: str, name: str) -> dict:
    """Where a document notice's click goes: that document, opened under the name the person knows it by."""
    return {"doc": doc_id, "name": name}


async def send(conn, user_ids: Iterable[str], kind: str, *, title: str, body: str | None = None,
               doc_id: str | None = None, target: dict | None = None, key: str | None = None) -> int:
    """One notice for each person in `user_ids`. With a `key`, a person's unread notice with the same key is updated
    instead (`repeats` + 1, the newer text, now, no longer resolved). Returns the rows written or updated; 0 when the
    write failed (logged)."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind: {kind}")
    ids = list(user_ids)
    if not ids:
        return 0
    level = KINDS[kind].level
    written = 0
    try:
        async with conn.transaction():   # a savepoint inside the caller's transaction
            for uid in ids:
                cur = await conn.execute(
                    "INSERT INTO notifications (user_id, kind, level, title, body, doc_id, target, key) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (user_id, key) WHERE key IS NOT NULL AND read_at IS NULL DO UPDATE SET "
                    "repeats = notifications.repeats + 1, title = EXCLUDED.title, body = EXCLUDED.body, "
                    "doc_id = EXCLUDED.doc_id, target = EXCLUDED.target, created_at = now(), resolved_at = NULL",
                    (uid, kind, level, title, body, doc_id, Jsonb(target) if target is not None else None, key))
                written += cur.rowcount
    except Exception:
        logger.warning("notification %s not sent", kind, exc_info=True)
        return 0
    return written


async def admin_ids(conn) -> list[str]:
    cur = await conn.execute("SELECT id::text FROM users WHERE 'admin' = ANY(capabilities) AND status = 'active'")
    return [r[0] for r in await cur.fetchall()]


async def send_admins(conn, kind: str, **kw) -> int:
    """`send` to every active admin as of now: an admin added later doesn't get older notices."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind: {kind}")
    try:
        ids = await admin_ids(conn)
    except Exception:
        logger.warning("notification %s not sent: admins not read", kind, exc_info=True)
        return 0
    return await send(conn, ids, kind, **kw)


async def resolve(conn, key: str) -> int:
    """The problem behind `key` went away: mark every unresolved notice with that key resolved, for everyone."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE notifications SET resolved_at = now() WHERE key = %s AND resolved_at IS NULL", (key,))
            return cur.rowcount
    except Exception:
        logger.warning("notifications not resolved", exc_info=True)
        return 0
