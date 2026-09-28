"""Migration 011 backfill (A1 spec §6). The shared test DB already has 011
applied, so pre-011 rows (owners, grants, `chunks_uploaded`) can't exist there.
This test builds a throwaway database at version 010, seeds it with raw SQL on
the v10 schema, runs the real 011 file from disk, and drops the database."""
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


async def test_011_backfills_entries_placements_and_drops_ownership():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = url_for(name)
            await apply_migrations(url, max_version=10)
            conn = await AsyncConnection.connect(url, autocommit=True)
            try:
                alice, bob, carol = [await _user(conn, s) for s in ("alice", "bob", "carol")]
                p1 = (await _one(conn,
                    "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P1') RETURNING id",
                    (alice,)))[0]
                await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, "
                    "user_id, tags) VALUES (%s,'a.pdf','pdf',1,%s,%s)",
                    ("a" * 64, alice, ["x", "y"]))
                await conn.execute(
                    "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)",
                    (p1, "a" * 64))
                for grantee in (bob, alice):  # alice's self-grant must not downgrade her
                    await conn.execute(
                        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s)",
                        ("a" * 64, grantee))
                await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, "
                    "user_id, state, pdf_path) "
                    "VALUES (%s,'b.pdf','pdf',1,%s,'chunks_uploaded','/tmp/b.pdf')",
                    ("b" * 64, carol))

                async with conn.transaction():  # one transaction, like server/db.py
                    await conn.execute((SQL_DIR / "011_content_entries.sql").read_text())

                cur = await conn.execute(
                    "SELECT user_id, doc_id, added_via, shared_by, tags FROM library_entries")
                entries = {(str(u), d, v, str(s) if s else None, tuple(sorted(t)))
                           for u, d, v, s, t in await cur.fetchall()}
                assert entries == {
                    (str(alice), "a" * 64, "upload", None, ("x", "y")),
                    (str(bob), "a" * 64, "shared", str(alice), ()),
                    (str(carol), "b" * 64, "upload", None, ()),
                }  # alice's self-grant did not add a second row or downgrade hers
                row = await _one(conn, "SELECT added_by FROM project_documents WHERE doc_id=%s", ("a" * 64,))
                assert str(row[0]) == str(alice)
                row = await _one(conn, "SELECT state, bytes_path, extracted_by FROM documents WHERE doc_id=%s", ("b" * 64,))
                assert row == ("extracted", "/tmp/b.pdf", "client")
                for table, col in (("documents", "user_id"), ("documents", "tags"), ("documents", "pdf_path")):
                    assert await _one(conn,
                        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                        (table, col)) is None
                assert await _one(conn, "SELECT to_regclass('doc_grants')") == (None,)
                assert await _one(conn,
                    "SELECT 1 FROM schema_migrations WHERE version = 11") is not None
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
