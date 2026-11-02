"""Migration 024 (message box inputs): chat_attachments.kind also accepts 'text',
and the Down SQL in the file's header puts the schema back."""
import re
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

FILE = SQL_DIR / "024_text_attachments.sql"


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _scratch():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = url_for(name)
    await apply_migrations(url)
    return admin, name, await AsyncConnection.connect(url, autocommit=True)


async def _drop(admin, name):
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _message(conn):
    uid = (await _one(
        conn, "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
              "VALUES ('i','a','a@x.io','member','active') RETURNING id"))[0]
    await conn.execute("INSERT INTO chat_sessions (id, title, model, user_id) VALUES ('s','t','m',%s)", (uid,))
    await conn.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, timestamp, status) "
        "VALUES ('m1','s','user','',1,'complete')")


async def _attach(conn, kind, ordinal):
    await conn.execute(
        "INSERT INTO chat_attachments (message_id, ordinal, kind, mime, size, data) "
        "VALUES ('m1', %s, %s, 'x/y', 1, 'a')", (ordinal, kind))


def _down_sql() -> str:
    lines = []
    for line in FILE.read_text().splitlines():
        m = re.match(r"^--   (.*)$", line)
        if m:
            lines.append(m.group(1))
    return "\n".join(lines)


async def test_024_accepts_text_and_still_refuses_other_kinds():
    admin, name, conn = await _scratch()
    try:
        await _message(conn)
        for i, kind in enumerate(("image", "audio", "text")):
            await _attach(conn, kind, i)
        with pytest.raises(CheckViolation):
            await _attach(conn, "video", 9)
        assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 24") == (1,)
    finally:
        await conn.close()
        await _drop(admin, name)


async def test_024_down_removes_text_rows_restores_the_check_and_forgets_the_version():
    admin, name, conn = await _scratch()
    try:
        await _message(conn)
        await _attach(conn, "image", 0)
        await _attach(conn, "text", 1)
        down = _down_sql()
        assert down.startswith("BEGIN;") and down.endswith("COMMIT;")
        await conn.execute(down)
        assert (await _one(conn, "SELECT count(*) FROM chat_attachments"))[0] == 1
        assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 24") is None
        with pytest.raises(CheckViolation):
            await _attach(conn, "text", 2)
        await conn.execute(FILE.read_text())          # the up runs again
        await _attach(conn, "text", 2)
    finally:
        await conn.close()
        await _drop(admin, name)
