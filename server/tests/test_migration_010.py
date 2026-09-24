"""Migration 010 backfill. The shared test DB already has 010 applied (the
session fixture runs every migration once), so pre-010 rows can't exist there.
This test builds a throwaway database at version 009, seeds it, runs the real
010 file from disk, and drops the database."""
import secrets

import pytest
from psycopg import AsyncConnection

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def test_010_backfills_links_and_drops_column():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = url_for(name)
            await apply_migrations(url, max_version=9)
            conn = await AsyncConnection.connect(url, autocommit=True)
            try:
                alice = (await _one(conn,
                    "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                    "VALUES ('i','a','a@x.io','member','active') RETURNING id"))[0]
                bob = (await _one(conn,
                    "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                    "VALUES ('i','b','b@x.io','member','active') RETURNING id"))[0]
                p1 = (await _one(conn,
                    "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P1') RETURNING id",
                    (alice,)))[0]
                p2 = (await _one(conn,
                    "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P2') RETURNING id",
                    (bob,)))[0]
                seeds = [("a" * 64, alice, p1), ("b" * 64, bob, p2),
                         ("c" * 64, alice, p1), ("d" * 64, alice, None)]
                for doc_id, owner, pid in seeds:
                    await conn.execute(
                        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, "
                        "user_id, project_id) VALUES (%s,'f.pdf','pdf',1,%s,%s)",
                        (doc_id, owner, pid))

                async with conn.transaction():  # one transaction, like server/db.py
                    await conn.execute((SQL_DIR / "010_project_documents.sql").read_text())

                cur = await conn.execute("SELECT project_id, doc_id FROM project_documents")
                links = {(str(p), d) for p, d in await cur.fetchall()}
                assert links == {(str(p1), "a" * 64), (str(p2), "b" * 64), (str(p1), "c" * 64)}

                assert await _one(conn,
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'documents' AND column_name = 'project_id'") is None
                assert await _one(conn,
                    "SELECT 1 FROM pg_indexes WHERE indexname = 'documents_project_idx'") is None
                assert await _one(conn,
                    "SELECT 1 FROM schema_migrations WHERE version = 10") is not None
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
