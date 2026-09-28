"""Regression for a lock-order inversion in server.chat.store.finish_turn
(fix round 1, C1 review). Every other path that touches both a session row
and its messages locks session-then-message: begin_turn's claim (an
unconditional `SELECT ... FOR UPDATE` on the session, then a possible
`_recover` message update), recover_stale's CTE, and a chat DELETE's cascade.
finish_turn used to go message-then-session, which deadlocks against any of
those under real concurrency. This test uses a REAL connection pool and a
separate raw connection (not the PoolShim used elsewhere) because a shared
single test connection can't reproduce a lock wait between two backends.
"""
from __future__ import annotations

import asyncio
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import DeadlockDetected
from psycopg_pool import AsyncConnectionPool

from server.chat import store
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def test_finish_turn_does_not_deadlock_against_a_held_session_lock(monkeypatch):
    """Connection B locks the session row first (as a takeover or a DELETE
    cascade would), then finish_turn runs concurrently on the pool, then B
    updates the assistant message row and commits. Session-then-message on
    both sides must never deadlock, whichever side runs first."""
    name = f"nr_chat_dl_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = url_for(name)
            await apply_migrations(url)
            pool = AsyncConnectionPool(url, min_size=4, max_size=8, open=False)
            await pool.open(wait=True, timeout=10)
            monkeypatch.setattr(store, "get_pool", lambda: pool)
            try:
                async with pool.connection() as c:
                    uid = (await _one(c,
                        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                        "VALUES ('i','s','a@x.io','member','active') RETURNING id"))[0]

                claim = await store.begin_turn(session_id="s-dl", user_id=str(uid), model_id="m",
                                               text="q", attachments=[], new_session_pins=None)

                # Connection B: lock the session row first, exactly like begin_turn's
                # takeover SELECT or a chat DELETE would, and hold it across an await.
                blocker = await AsyncConnection.connect(url)
                await blocker.execute("SET deadlock_timeout = '200ms'")
                await blocker.execute("SELECT user_id FROM chat_sessions WHERE id=%s FOR UPDATE",
                                      (claim.session_id,))

                fin = asyncio.create_task(store.finish_turn(
                    claim, status="complete", finish_reason="stop", content="done",
                    thinking="", tool_calls=None, doc_context=None, stats=None))
                # Give finish_turn time to take whichever lock it takes first and
                # block on the second — this is the moment the two orders collide.
                await asyncio.sleep(0.4)

                results: dict[str, str] = {}
                try:
                    await blocker.execute(
                        "UPDATE chat_messages SET content = 'blocker-touched' WHERE id = %s",
                        (claim.assistant_message_id,))
                    await blocker.commit()
                    results["blocker"] = "ok"
                except DeadlockDetected:
                    results["blocker"] = "DeadlockDetected"
                    await blocker.rollback()
                finally:
                    await blocker.close()

                try:
                    await fin
                    results["finish_turn"] = "ok"
                except DeadlockDetected:
                    results["finish_turn"] = "DeadlockDetected"

                assert results == {"blocker": "ok", "finish_turn": "ok"}

                async with pool.connection() as c:
                    assert await _one(c, "SELECT active_turn_id FROM chat_sessions WHERE id='s-dl'") == (None,)
                    assert await _one(c, "SELECT status, content FROM chat_messages WHERE id=%s",
                                      (claim.assistant_message_id,)) == ("complete", "done")
            finally:
                await pool.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
