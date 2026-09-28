"""Migration 013 (C1): chat message status, the one-turn-per-session claim,
and stored attachment bytes. Additive only: existing messages become
'complete' and nothing is rewritten."""
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def test_013_is_additive_and_cascades():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = url_for(name)
            await apply_migrations(url, max_version=12)
            conn = await AsyncConnection.connect(url, autocommit=True)
            try:
                uid = (await _one(conn,
                    "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                    "VALUES ('i','s','a@x.io','member','active') RETURNING id"))[0]
                await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-old', %s)", (uid,))
                await conn.execute(
                    "INSERT INTO chat_messages (id, session_id, role, content) "
                    "VALUES ('m-old', 's-old', 'assistant', 'kept')")

                async with conn.transaction():  # one transaction, like server/db.py
                    await conn.execute((SQL_DIR / "013_chat_orchestrator.sql").read_text())

                assert await _one(conn, "SELECT status, finish_reason, model, content "
                                        "FROM chat_messages WHERE id='m-old'") == ("complete", None, None, "kept")
                assert await _one(conn, "SELECT active_turn_id, active_turn_heartbeat_at "
                                        "FROM chat_sessions WHERE id='s-old'") == (None, None)
                with pytest.raises(CheckViolation):
                    await conn.execute("UPDATE chat_messages SET status='bogus' WHERE id='m-old'")
                await conn.execute(
                    "INSERT INTO chat_attachments (message_id, ordinal, kind, mime, name, size, data) "
                    "VALUES ('m-old', 0, 'image', 'image/png', 'a.png', 3, '\\x010203')")
                await conn.execute("DELETE FROM chat_sessions WHERE id='s-old'")
                assert (await _one(conn, "SELECT count(*) FROM chat_attachments"))[0] == 0
                assert (await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 13")) == (1,)
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
