"""Migration 029 (chat titles, helper spec §6): who wrote a chat's title. Chats already named at the upgrade count as
named by a person, so nothing named is ever replaced; the Down in the file's header puts the schema back."""
import re
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

FILE = SQL_DIR / "029_chat_titles.sql"


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _scratch(max_version=None):
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = url_for(name)
    await apply_migrations(url, max_version=max_version)
    return admin, name, url, await AsyncConnection.connect(url, autocommit=True)


async def _drop(admin, name):
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


def _down_sql() -> str:
    return "\n".join(m.group(1) for line in FILE.read_text().splitlines() if (m := re.match(r"^--   (.*)$", line)))


async def _user(conn):
    return (await _one(conn, "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                             "VALUES ('i','a','a@x.io','member','active') RETURNING id"))[0]


async def test_named_chats_become_person_and_the_rest_default():
    admin, name, url, conn = await _scratch(max_version=28)
    try:
        uid = await _user(conn)
        await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('a', %s)", (uid,))
        await conn.execute("INSERT INTO chat_sessions (id, title, user_id) VALUES ('b', 'Budget questions', %s)",
                           (uid,))
        await apply_migrations(url)
        cur = await conn.execute("SELECT id, title_source, title_pending_at FROM chat_sessions ORDER BY id")
        assert await cur.fetchall() == [("a", "default", None), ("b", "person", None)]
        await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('c', %s)", (uid,))
        assert await _one(conn, "SELECT title_source FROM chat_sessions WHERE id = 'c'") == ("default",)
    finally:
        await conn.close()
        await _drop(admin, name)


async def test_title_source_refuses_an_unknown_value():
    admin, name, _url, conn = await _scratch()
    try:
        uid = await _user(conn)
        await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('a', %s)", (uid,))
        with pytest.raises(CheckViolation):
            await conn.execute("UPDATE chat_sessions SET title_source = 'robot'")
    finally:
        await conn.close()
        await _drop(admin, name)


async def test_029_down_drops_both_columns_and_forgets_the_version():
    admin, name, _url, conn = await _scratch()
    try:
        down = _down_sql()
        assert down.startswith("BEGIN;") and down.endswith("COMMIT;")
        await conn.execute(down)
        cols = await (await conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'chat_sessions' "
            "AND column_name IN ('title_source', 'title_pending_at')")).fetchall()
        assert cols == []
        assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 29") is None
        await conn.execute(FILE.read_text())          # the up runs again
    finally:
        await conn.close()
        await _drop(admin, name)
