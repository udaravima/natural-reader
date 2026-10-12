"""The 'last seen' touch must never leave a row lock on the request's
connection. That connection stays open, in its transaction, until FastAPI has
run the request's background tasks (an index can take a minute), so a lock
taken there froze every other request from the same session or token for
the whole index (V1 handoff, finding 1).

These run on real, separately committing connections in a scratch database:
the request connection is left open mid-transaction, exactly as it sits
during a background task."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from server import db
from server.auth import sessions, tokens
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

STALE = timedelta(hours=1)


@pytest.fixture
async def scratch(monkeypatch):
    name = f"natural_reader_touch_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    pool = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        url = url_for(name)
        await apply_migrations(url)
        pool = AsyncConnectionPool(url, min_size=1, max_size=4, open=False)
        await pool.open(wait=True, timeout=10)
        monkeypatch.setattr(db, "_pool", pool)
        async with pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                "VALUES ('i','s','s@x.io','member','active') RETURNING id")
            uid = str((await cur.fetchone())[0])
        yield url, uid
    finally:
        try:
            if pool is not None:
                await pool.close()
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _committed(url, sql, params=()):
    conn = await AsyncConnection.connect(url, autocommit=True)
    try:
        cur = await conn.execute(sql, params)
        return await cur.fetchone() if cur.description else None
    finally:
        await conn.close()


async def _session(url, uid, *, last_seen):
    async with db.get_pool().connection() as conn:
        raw = await sessions.create_session(conn, uid)
        await conn.execute("UPDATE sessions SET last_seen_at = %s WHERE user_id = %s",
                           (last_seen, uid))
    return raw


async def _token(url, uid, *, last_used):
    async with db.get_pool().connection() as conn:
        tid, raw = await tokens.create_token(conn, uid, "ext")
        await conn.execute("UPDATE personal_access_tokens SET last_used_at = %s WHERE id = %s",
                           (last_used, tid))
    return tid, raw


async def test_session_touch_commits_at_once_and_leaves_no_row_lock(scratch):
    url, uid = scratch
    old = datetime.now(timezone.utc) - STALE
    raw = await _session(url, uid, last_seen=old)
    request_conn = await AsyncConnection.connect(url)  # like a pooled request conn
    try:
        assert (await sessions.resolve_session(request_conn, raw))["id"] == uid
        # The request connection is still open mid-transaction. Its touch is
        # already visible to everyone else, and the row is free to lock.
        (seen,) = await _committed(url, "SELECT last_seen_at FROM sessions WHERE user_id = %s",
                                   (uid,))
        assert seen > old
        assert await _committed(url, "SELECT 1 FROM sessions WHERE user_id = %s "
                                     "FOR UPDATE NOWAIT", (uid,)) == (1,)
    finally:
        await request_conn.close()


async def test_token_touch_commits_at_once_and_leaves_no_row_lock(scratch):
    url, uid = scratch
    tid, raw = await _token(url, uid, last_used=None)  # never used yet
    request_conn = await AsyncConnection.connect(url)
    try:
        assert (await tokens.resolve_token(request_conn, raw))["id"] == uid
        (used,) = await _committed(url, "SELECT last_used_at FROM personal_access_tokens "
                                        "WHERE id = %s", (tid,))
        assert used is not None
        assert await _committed(url, "SELECT 1 FROM personal_access_tokens WHERE id = %s "
                                     "FOR UPDATE NOWAIT", (tid,)) == (1,)
    finally:
        await request_conn.close()


async def test_next_request_is_not_blocked_by_a_request_still_running_background_work(scratch):
    """The user-visible chain: request A authenticates, then its connection
    stays open through a long background task; request B from the same
    session must still answer."""
    url, uid = scratch
    raw = await _session(url, uid, last_seen=datetime.now(timezone.utc) - STALE)
    conn_a = await AsyncConnection.connect(url)
    conn_b = await AsyncConnection.connect(url)
    try:
        await sessions.resolve_session(conn_a, raw)
        row = await asyncio.wait_for(sessions.resolve_session(conn_b, raw), timeout=5)
        assert row["id"] == uid
    finally:
        await conn_a.close()
        await conn_b.close()


async def test_a_recent_touch_is_not_repeated(scratch):
    url, uid = scratch
    recent = datetime.now(timezone.utc) - timedelta(seconds=5)
    raw = await _session(url, uid, last_seen=recent)
    tid, raw_pat = await _token(url, uid, last_used=recent)
    conn = await AsyncConnection.connect(url)
    try:
        assert await sessions.resolve_session(conn, raw) is not None
        assert await tokens.resolve_token(conn, raw_pat) is not None
        # Read on the same connection, so an uncommitted write would show too.
        cur = await conn.execute("SELECT last_seen_at FROM sessions WHERE user_id = %s", (uid,))
        assert (await cur.fetchone())[0] == recent
        cur = await conn.execute("SELECT last_used_at FROM personal_access_tokens WHERE id = %s",
                                 (tid,))
        assert (await cur.fetchone())[0] == recent
    finally:
        await conn.close()
