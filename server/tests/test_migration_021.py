"""Migration 021 (C2.1 project chat): a chat session can be scoped to one
project, and each document gets a cached outline (headings or bookmarks plus
an opening excerpt). Nothing existing changes: old sessions stay unscoped."""
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

A = "a" * 64


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _user(conn, sub):
    return (await _one(
        conn,
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i', %s, %s, 'member', 'active') RETURNING id", (sub, f"{sub}@x.io")))[0]


async def _scratch_db():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    await admin.execute(f'CREATE DATABASE "{name}"')
    return admin, name


async def _drop(admin, name):
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _session(conn, sid, user, scope=None):
    await conn.execute(
        "INSERT INTO chat_sessions (id, user_id, scope_project_id) VALUES (%s,%s,%s)",
        (sid, user, scope))


async def _document(conn, doc_id=A):
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
        "extracted_by) VALUES (%s,'a.pdf','pdf',1,'indexed','server')", (doc_id,))


async def test_021_adds_a_nullable_session_scope_and_leaves_old_sessions_unscoped():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url, max_version=20)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            alice = await _user(conn, "alice")
            await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('old', %s)", (alice,))
            await apply_migrations(url)

            assert await _one(
                conn,
                "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'chat_sessions' AND column_name = 'scope_project_id'",
            ) == ("uuid", "YES", None)
            # A chat that existed before the upgrade is not tied to any project.
            assert await _one(
                conn, "SELECT scope_project_id FROM chat_sessions WHERE id = 'old'") == (None,)
            assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 21") == (1,)
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)


async def test_deleting_a_project_unscopes_its_chats_but_keeps_them():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            alice = await _user(conn, "alice")
            p1 = (await _one(conn, "INSERT INTO projects (created_by, name) "
                                   "VALUES (%s,'P1') RETURNING id", (alice,)))[0]
            p2 = (await _one(conn, "INSERT INTO projects (created_by, name) "
                                   "VALUES (%s,'P2') RETURNING id", (alice,)))[0]
            await _session(conn, "s1", alice, p1)
            await _session(conn, "s2", alice, p2)
            await _session(conn, "s3", alice)

            await conn.execute("DELETE FROM projects WHERE id = %s", (p1,))

            cur = await conn.execute("SELECT id, scope_project_id FROM chat_sessions ORDER BY id")
            assert await cur.fetchall() == [("s1", None), ("s2", p2), ("s3", None)]
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)


async def test_doc_outlines_store_one_outline_per_document_with_defaults():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            await _document(conn)
            await conn.execute("INSERT INTO doc_outlines (doc_id, source) VALUES (%s,'none')", (A,))
            row = await _one(
                conn, "SELECT entries, opening, source, created_at IS NOT NULL "
                      "FROM doc_outlines WHERE doc_id = %s", (A,))
            assert row == ([], "", "none", True)
            assert await _one(
                conn,
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'doc_outlines' AND column_name = 'doc_id'") == ("text",)
            await conn.execute(
                "UPDATE doc_outlines SET entries = %s::jsonb, source = 'headings' WHERE doc_id = %s",
                ('[{"title": "Intro", "page": 1}]', A))
            assert (await _one(
                conn, "SELECT entries FROM doc_outlines WHERE doc_id = %s", (A,)))[0] == [
                {"title": "Intro", "page": 1}]
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)


async def test_deleting_a_document_deletes_its_outline():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            await _document(conn)
            await conn.execute("INSERT INTO doc_outlines (doc_id, source) VALUES (%s,'bookmarks')", (A,))
            await conn.execute("DELETE FROM documents WHERE doc_id = %s", (A,))
            assert (await _one(conn, "SELECT count(*) FROM doc_outlines"))[0] == 0
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)


async def test_doc_outline_source_accepts_only_the_three_known_origins():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            await _document(conn)
            with pytest.raises(CheckViolation):
                await conn.execute("INSERT INTO doc_outlines (doc_id, source) VALUES (%s,'other')", (A,))
            for source in ("headings", "bookmarks", "none"):
                await conn.execute("DELETE FROM doc_outlines")
                await conn.execute(
                    "INSERT INTO doc_outlines (doc_id, source) VALUES (%s,%s)", (A, source))
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)


async def test_021_is_safe_to_run_twice():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            await conn.execute((SQL_DIR / "021_project_chat.sql").read_text())
            assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 21") == (1,)
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)
