"""'Last seen' / 'last used' stamps, written on their own short connection.

The request's own connection (deps.get_conn) stays open, mid-transaction,
until FastAPI has run the request's background tasks, and an index can take
a minute. An UPDATE there row-locks the session or token for that whole
time, so every other request on the same credential waited behind it. The
stamp is therefore committed at once on a separate pooled connection, and at
most once a minute: nothing reads it more precisely than that."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import db

TOUCH_EVERY = timedelta(seconds=60)


def due(last: datetime | None) -> bool:
    return last is None or last <= datetime.now(timezone.utc) - TOUCH_EVERY


async def stamp(sql: str, params: tuple) -> None:
    if not db.is_ready():
        return  # no pool: a unit test on a bare connection
    async with db.get_pool().connection() as conn:
        await conn.execute(sql, params)
