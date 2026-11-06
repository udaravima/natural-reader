"""Notifications for each person (spec docs/superpowers/specs/2026-11-06-notifications-design.md).

An inbox, not a popup. A sender calls `send` (named people) or `send_admins` (every active admin) where the outcome
happens, in the caller's transaction where the outcome is a database write, so a rolled-back change sends nothing.
A send runs in a savepoint and never raises at runtime: a notice that can't be written is logged, and the caller's
own write still commits.

Two levels, fixed by the kind: 'action' (something stays stuck until a person acts) sorts first; 'info' after. A
needs-action notice whose problem went away by itself is `resolve`d by its key and sorts as information from then on.

Names may be in a notice's title (it's for the person reading it); never in a log line or the audit trail."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, NamedTuple

from psycopg.types.json import Jsonb

from ..db import get_pool

logger = logging.getLogger(__name__)

GROUP_GAP = timedelta(minutes=30)   # a notice joins its kind's group when it came within this of the one before
GROUP_DETAILS = 50                  # a group lists its newest 50 members; the rest are counted ("and N more")


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


@dataclass(frozen=True)
class Notice:
    id: int
    kind: str
    level: str
    title: str
    body: str | None
    doc_id: str | None
    target: dict | None
    repeats: int
    created_at: datetime
    read_at: datetime | None
    resolved_at: datetime | None

    @property
    def needs_action(self) -> bool:
        return self.level == "action" and self.resolved_at is None


_COLUMNS = "id, kind, level, title, body, doc_id, target, repeats, created_at, read_at, resolved_at"


def _item(n: Notice) -> dict[str, Any]:
    return {"id": n.id, "title": n.title, "body": n.body, "at": n.created_at, "unread": n.read_at is None,
            "resolved": n.resolved_at is not None, "repeats": n.repeats, "target": n.target}


def _row(members: list[Notice]) -> dict[str, Any]:
    """One inbox row from a group's members (oldest first)."""
    newest = members[-1]
    count = len(members)
    single = count == 1
    ordered = list(reversed(members))
    return {
        "kind": newest.kind,
        "level": newest.level,
        "needs_action": any(m.needs_action for m in members),
        "unread": newest.read_at is None,
        "title": newest.title if single else KINDS[newest.kind].plural.format(n=count),
        "body": newest.body if single else None,
        "at": newest.created_at,
        "count": count,
        "resolved": sum(1 for m in members if m.resolved_at is not None),
        "ids": [m.id for m in ordered],
        "items": [_item(m) for m in ordered[:GROUP_DETAILS]],
        "more": max(0, count - GROUP_DETAILS),
        "target": newest.target if single else None,
    }


def group_rows(notices: list[Notice], *, gap: timedelta = GROUP_GAP) -> list[dict[str, Any]]:
    """The inbox's rows, in inbox order. A group is one kind, all unread or all read, each member within `gap` of
    the one before it; a kind fixes its level, so failures and finishes never share a group. Order: unread before
    read; among unread, rows needing action first; newest first within each part. A read needs-action row gets no
    priority: the top of the list means "new to you"."""
    ordered = sorted(notices, key=lambda n: (n.kind, n.read_at is None, n.created_at, n.id))
    groups: list[list[Notice]] = []
    for n in ordered:
        last = groups[-1][-1] if groups else None
        if (last is not None and last.kind == n.kind and (last.read_at is None) == (n.read_at is None)
                and n.created_at - last.created_at <= gap):
            groups[-1].append(n)
        else:
            groups.append([n])
    rows = [_row(g) for g in groups]
    rows.sort(key=lambda r: (not r["unread"], not (r["unread"] and r["needs_action"]), -r["at"].timestamp()))
    return rows


async def _notices(conn, user_id, *, unread_only: bool = False) -> list[Notice]:
    where = " AND read_at IS NULL" if unread_only else ""
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM notifications WHERE user_id = %s{where}", (user_id,))
    return [Notice(*r) for r in await cur.fetchall()]


async def inbox(conn, user_id, *, offset: int = 0, limit: int = 30) -> dict[str, Any]:
    """A page of the person's rows (groups and single notices), in inbox order. Every notice of theirs is read:
    retention bounds how many there are."""
    rows = group_rows(await _notices(conn, user_id))
    return {"rows": rows[offset:offset + limit], "total": len(rows)}


async def unread_counts(conn, user_id) -> dict[str, int]:
    """The bell: unread rows as the list shows them (a group counts once), and how many of those need action."""
    rows = group_rows(await _notices(conn, user_id, unread_only=True))
    return {"count": len(rows), "action": sum(1 for r in rows if r["needs_action"])}


async def mark_read(conn, user_id, ids: list[int]) -> int:
    """Mark the person's own unread notices among `ids` read. Anyone else's ids are ignored, never reported."""
    if not ids:
        return 0
    cur = await conn.execute("UPDATE notifications SET read_at = now() WHERE user_id = %s AND id = ANY(%s) "
                             "AND read_at IS NULL", (user_id, list(ids)))
    return cur.rowcount


async def mark_all_read(conn, user_id) -> int:
    cur = await conn.execute("UPDATE notifications SET read_at = now() WHERE user_id = %s AND read_at IS NULL",
                             (user_id,))
    return cur.rowcount


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


# ---- cleanup ----

READ_DAYS_DEFAULT = 30
UNREAD_DAYS_DEFAULT = 90
PURGE_EVERY_S = 24 * 3600


def _days(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        days = int(raw)
    except ValueError:
        days = -1
    if days < 0:
        logger.warning("%s=%r is not a whole number of days >= 0; keeping %d", name, raw, default)
        return default
    return days


def retention(env: Mapping[str, str] = os.environ) -> tuple[int, int]:
    """(days a read notice is kept after it was read, days an unread one is kept after it arrived); 0 = forever."""
    return (_days(env, "NOTIFICATIONS_READ_DAYS", READ_DAYS_DEFAULT),
            _days(env, "NOTIFICATIONS_UNREAD_DAYS", UNREAD_DAYS_DEFAULT))


async def purge(conn, read_days: int, unread_days: int) -> int:
    """Delete what is past its time. Idempotent, so several workers purging at once is harmless."""
    if read_days <= 0 and unread_days <= 0:
        return 0
    cur = await conn.execute(
        "DELETE FROM notifications WHERE "
        "(%(r)s > 0 AND read_at IS NOT NULL AND read_at < now() - make_interval(days => %(r)s)) OR "
        "(%(u)s > 0 AND read_at IS NULL AND created_at < now() - make_interval(days => %(u)s))",
        {"r": read_days, "u": unread_days})
    return cur.rowcount


_task: asyncio.Task | None = None


async def _purge_loop(read_days: int, unread_days: int) -> None:
    while True:
        try:
            async with get_pool().connection() as conn:
                n = await purge(conn, read_days, unread_days)
            if n:
                logger.info("notifications: cleaned up %d", n)
        except Exception:
            logger.warning("notification cleanup failed", exc_info=True)
        await asyncio.sleep(PURGE_EVERY_S)


def start_retention() -> None:
    """Called from the app's startup hook once the database is up: at once, then every 24 hours."""
    global _task
    read_days, unread_days = retention()
    if (read_days > 0 or unread_days > 0) and _task is None:
        _task = asyncio.create_task(_purge_loop(read_days, unread_days))


async def stop_retention() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
