"""Migration 012 (A1 final review C1): entries and placements get `verified`.
Every row that exists when 012 runs came from 011's backfill — a pre-A1 owner
who only ever sent a hash, never the bytes — so every one starts unverified.
Builds a throwaway database at version 011, seeds rows on that schema, runs
the real 012 file from disk, and drops the database."""
import secrets

import pytest
from psycopg import AsyncConnection

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _user(conn, sub):
    return (await _one(conn,
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i',%s,%s,'member','active') RETURNING id", (sub, f"{sub}@x.io")))[0]


async def test_012_adds_verified_false_to_existing_entries_and_placements():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = url_for(name)
            await apply_migrations(url, max_version=11)
            conn = await AsyncConnection.connect(url, autocommit=True)
            try:
                alice, bob = await _user(conn, "alice"), await _user(conn, "bob")
                p1 = (await _one(conn,
                    "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P1') RETURNING id",
                    (alice,)))[0]
                await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes) "
                    "VALUES (%s,'a.pdf','pdf',1)", ("a" * 64,))
                await conn.execute(
                    "INSERT INTO library_entries (user_id, doc_id, added_via) "
                    "VALUES (%s,%s,'upload')", (alice, "a" * 64))
                await conn.execute(
                    "INSERT INTO library_entries (user_id, doc_id, added_via, shared_by) "
                    "VALUES (%s,%s,'shared',%s)", (bob, "a" * 64, alice))
                await conn.execute(
                    "INSERT INTO project_documents (project_id, doc_id, added_by) "
                    "VALUES (%s,%s,%s)", (p1, "a" * 64, alice))

                async with conn.transaction():  # one transaction, like server/db.py
                    await conn.execute((SQL_DIR / "012_entry_verification.sql").read_text())

                cur = await conn.execute("SELECT verified FROM library_entries")
                assert [r[0] for r in await cur.fetchall()] == [False, False]
                cur = await conn.execute("SELECT verified FROM project_documents")
                assert [r[0] for r in await cur.fetchall()] == [False]
                for table in ("library_entries", "project_documents"):
                    row = await _one(conn,
                        "SELECT is_nullable, column_default FROM information_schema.columns "
                        "WHERE table_name=%s AND column_name='verified'", (table,))
                    assert row == ("NO", "false")
                assert await _one(conn,
                    "SELECT 1 FROM schema_migrations WHERE version = 12") is not None
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
