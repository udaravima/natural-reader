"""Migration 018 (V1 spec §8): every placement becomes version 1 of its own
project document, current, named as the project showed it before the
upgrade. No data is dropped."""
import secrets

import pytest
from psycopg import AsyncConnection

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

A, B, C, L = "a" * 64, "b" * 64, "c" * 64, "e" * 64


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


async def test_018_turns_every_placement_into_a_current_v1_named_as_before():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url, max_version=17)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            alice, bob = await _user(conn, "alice"), await _user(conn, "bob")
            pid = (await _one(conn, "INSERT INTO projects (created_by, name) "
                                    "VALUES (%s,'P') RETURNING id", (alice,)))[0]
            for doc in (A, B, C, L):
                await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
                    "extracted_by) VALUES (%s,%s,'pdf',1,'indexed','server')",
                    (doc, f"{doc[0]}-canonical.pdf"))
            # A: alice's entry keeps the canonical name (file_name NULL).
            # B: bob renamed his copy before filing it: the project showed his name.
            # C: nobody holds an entry any more: the project showed the canonical name.
            # L: a legacy placement nobody proved (migration 012).
            await conn.execute("INSERT INTO library_entries (user_id, doc_id, added_via, verified) "
                               "VALUES (%s,%s,'upload',true)", (alice, A))
            await conn.execute("INSERT INTO library_entries (user_id, doc_id, file_name, added_via, "
                               "verified) VALUES (%s,%s,'Bob notes.pdf','upload',true)", (bob, B))
            for doc, by, verified in ((A, alice, True), (B, bob, True), (C, alice, True),
                                      (L, alice, False)):
                await conn.execute(
                    "INSERT INTO project_documents (project_id, doc_id, added_by, verified) "
                    "VALUES (%s,%s,%s,%s)", (pid, doc, by, verified))

            async with conn.transaction():  # one transaction, like server/db.py
                await conn.execute((SQL_DIR / "018_document_versions.sql").read_text())

            cur = await conn.execute(
                "SELECT doc_id, version, file_name, origin, is_current, verified, project_doc_id "
                "FROM project_documents")
            rows = {r[0]: r[1:] for r in await cur.fetchall()}
            assert {d: r[:5] for d, r in rows.items()} == {
                A: (1, "a-canonical.pdf", "upload", True, True),
                B: (1, "Bob notes.pdf", "upload", True, True),
                C: (1, "c-canonical.pdf", "upload", True, True),
                L: (1, "e-canonical.pdf", "upload", True, False),
            }
            assert len({r[5] for r in rows.values()}) == 4  # one document per placement
            cur = await conn.execute(
                "SELECT id, project_id, created_by, last_version FROM project_docs")
            docs = {r[0]: r[1:] for r in await cur.fetchall()}
            assert set(docs) == {r[5] for r in rows.values()}
            assert all(p == pid and lv == 1 for p, _by, lv in docs.values())
            assert docs[rows[B][5]][1] == bob  # created_by = who filed it
            assert await _one(conn, "SELECT indexdef FROM pg_indexes "
                                    "WHERE indexname = 'project_documents_project_idx'") == (
                "CREATE INDEX project_documents_project_idx ON public.project_documents "
                "USING btree (project_id)",)
            assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 18") == (1,)
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)
