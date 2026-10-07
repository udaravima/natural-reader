"""Project activity feed (A0 §3.2). One `project_events` row per change,
written by the request that makes the change (same transaction), so a
rolled-back change leaves no event. People are stored by id and shown by
their CURRENT names; document names are copied because the document may be
gone. The audit log (server.audit) gets IDs and roles only — never names.

Retention: PROJECT_EVENTS_RETENTION_DAYS (days; 0 = keep forever, the
default). When positive, older events are deleted at startup and then every
24 hours; the DELETE is idempotent, so several workers purging is harmless."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping

from psycopg.types.json import Jsonb

from ..audit import audit
from ..db import get_pool
from . import people

logger = logging.getLogger(__name__)

KINDS = frozenset({
    "project.created", "project.renamed", "project.described",
    "member.added", "member.role_changed", "member.removed", "member.left",
    "document.added", "document.removed",
})
_AUDITED_DETAILS = ("role", "from", "to", "via")  # roles only; never names
MAX_PAGE = 100
PURGE_EVERY_S = 24 * 3600


async def record(conn, project_id, actor_user_id, kind: str, *, subject_user_id=None,
                 doc_id: str | None = None, details: dict | None = None) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown project event kind: {kind}")
    details = details or {}
    await conn.execute(
        "INSERT INTO project_events (project_id, actor_user_id, kind, subject_user_id, doc_id, "
        "details) VALUES (%s,%s,%s,%s,%s,%s)",
        (project_id, actor_user_id, kind, subject_user_id, doc_id, Jsonb(details)))
    if kind.startswith("document."):
        return  # the placement routes audit these as placement.added/removed
    fields: dict[str, object] = {"project": project_id, "by": actor_user_id}
    if subject_user_id:
        fields["user"] = subject_user_id
    if kind != "project.renamed":  # a rename's from/to are names
        fields.update({k: details[k] for k in _AUDITED_DETAILS if k in details})
    audit(kind, **fields)


async def page(conn, project_id, *, before: int | None = None, limit: int = 50,
               show_email: bool = False) -> dict:
    limit = max(1, min(limit, MAX_PAGE))
    cur = await conn.execute(
        "SELECT e.id, e.at, e.kind, e.doc_id, e.details, "
        f"{people.person_cols('a')}, {people.person_cols('s')} "
        "FROM project_events e LEFT JOIN users a ON a.id = e.actor_user_id "
        "LEFT JOIN users s ON s.id = e.subject_user_id "
        "WHERE e.project_id = %s AND (%s::bigint IS NULL OR e.id < %s) "
        "ORDER BY e.id DESC LIMIT %s",
        (project_id, before, before, limit + 1))
    rows = await cur.fetchall()
    events = []
    for r in rows[:limit]:
        details = r[4] or {}
        events.append({
            "id": r[0], "at": r[1], "kind": r[2],
            "actor": people.person_from(r[5:11], show_email),
            "subject": people.person_from(r[11:17], show_email),
            "doc": {"id": r[3], "name": details.get("name")} if r[3] else None,
            "details": details,
        })
    next_before = events[-1]["id"] if len(rows) > limit else None
    return {"events": events, "next_before": next_before}


def retention_days(env: Mapping[str, str] = os.environ) -> int:
    raw = env.get("PROJECT_EVENTS_RETENTION_DAYS", "").strip() or "0"
    try:
        days = int(raw)
    except ValueError:
        days = -1
    if days < 0:
        logger.warning("PROJECT_EVENTS_RETENTION_DAYS=%r is not a whole number of days >= 0; "
                       "keeping events forever", raw)
        return 0
    return days


async def purge(conn, days: int) -> int:
    if days <= 0:
        return 0
    cur = await conn.execute(
        "DELETE FROM project_events WHERE at < now() - make_interval(days => %s)", (days,))
    return cur.rowcount


_task: asyncio.Task | None = None


async def _purge_loop(days: int) -> None:
    while True:
        try:
            async with get_pool().connection() as conn:
                n = await purge(conn, days)
            if n:
                logger.info("project events: purged %d older than %d days", n, days)
        except Exception:
            logger.warning("project event purge failed", exc_info=True)
        await asyncio.sleep(PURGE_EVERY_S)


def start_retention() -> None:
    """Called from the app's startup hook once the database is up."""
    global _task
    days = retention_days()
    if days > 0 and _task is None:
        _task = asyncio.create_task(_purge_loop(days))


async def stop_retention() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
