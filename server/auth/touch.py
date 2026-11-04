"""'Last seen' / 'last used' stamps, written on their own short connection.

The request's own connection (deps.get_conn) stays open, mid-transaction,
until FastAPI has run the request's background tasks, and an index can take
a minute. An UPDATE there row-locks the session or token for that whole
time, so every other request on the same credential waited behind it. The
stamp is therefore committed at once on a separate pooled connection, and at
most once a minute: nothing reads it more precisely than that.

That second connection is taken while the request still holds its first. With every
connection in the pool held by a request waiting to stamp, each waited for the others
until the pool timed out. So a stamp waits at most STAMP_WAIT_S for a connection and is
otherwise skipped: the credential's next request stamps it."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from psycopg_pool import PoolTimeout

from .. import db

logger = logging.getLogger(__name__)

TOUCH_EVERY = timedelta(seconds=60)
STAMP_WAIT_S = 0.5   # seconds a request waits for a free connection before skipping its stamp


def due(last: datetime | None) -> bool:
    return last is None or last <= datetime.now(timezone.utc) - TOUCH_EVERY


async def stamp(sql: str, params: tuple) -> None:
    if not db.is_ready():
        return  # no pool: a unit test on a bare connection
    try:
        async with db.get_pool().connection(timeout=STAMP_WAIT_S) as conn:
            await conn.execute(sql, params)
    except PoolTimeout:
        logger.info("No free database connection for a last-seen stamp; skipped")
