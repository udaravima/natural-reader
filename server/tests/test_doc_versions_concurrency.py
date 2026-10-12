"""Spec §4.4 on real, separately committing connections: the document lock
makes a second upload of the same bytes see the first one's commit (409, not
a duplicate version), crossed merges can't deadlock, and the unique index
turns a lost race into a 409, never a 500.

The lock tests force the overlap instead of hoping for it: a third
connection holds the row lock(s), both actions are started, and only once
pg_stat_activity shows them waiting on a lock is the holder committed. (The
plain v2/v3 test below does NOT prove the lock: the numbering UPDATE
serialises on its own.)"""
import asyncio
import secrets

import pytest
from fastapi import HTTPException
from psycopg import AsyncConnection

from server.services import doc_versions
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

C = {i: str(i) * 64 for i in range(1, 6)}


@pytest.fixture
async def scratch():
    name = f"natural_reader_conc_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    conn = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        cur = await conn.execute("INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                                 "VALUES ('i','c','c@x.io','member','active') RETURNING id")
        uid = str((await cur.fetchone())[0])
        cur = await conn.execute("INSERT INTO projects (created_by, name) VALUES (%s,'P') RETURNING id",
                                 (uid,))
        pid = str((await cur.fetchone())[0])
        await conn.execute("INSERT INTO project_members (project_id, user_id, role) "
                           "VALUES (%s,%s,'owner')", (pid, uid))
        for doc in C.values():
            await conn.execute("INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
                               "extracted_by) VALUES (%s,%s,'pdf',1,'indexed','server')", (doc, doc[:4]))
            await conn.execute("INSERT INTO library_entries (user_id, doc_id, added_via, verified) "
                               "VALUES (%s,%s,'upload',true)", (uid, doc))
        yield url, conn, uid, pid
    finally:
        # Dropped even when setup or the test failed; FORCE evicts any
        # connection a hung test left behind.
        try:
            if conn is not None:
                await conn.close()
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _blocked_backends(probe) -> int:
    cur = await probe.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
        "AND wait_event_type = 'Lock'")
    return (await cur.fetchone())[0]


async def _until_blocked(probe, n: int) -> None:
    while await _blocked_backends(probe) < n:
        await asyncio.sleep(0.02)


async def _race(url, probe, lock_sql, lock_params, *calls):
    """Run `calls` (each `fn(conn)`, in its own committing transaction) at
    the same moment: a blocker connection holds `lock_sql`, every call is
    started, and only when all of them are waiting on a lock (per
    pg_stat_activity) does the blocker commit. Returns the results and
    exceptions. Every await is time-boxed so a hang fails, not stalls."""
    blocker = await AsyncConnection.connect(url)
    tasks = []
    try:
        async with blocker.transaction():
            await blocker.execute(lock_sql, lock_params)
            tasks = [asyncio.ensure_future(_in_tx(url, c)) for c in calls]
            await asyncio.wait_for(_until_blocked(probe, len(calls)), timeout=10)
        return await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=20)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await blocker.close()


async def _in_tx(url, fn):
    conn = await AsyncConnection.connect(url)
    try:
        async with conn.transaction():
            return await fn(conn)
    finally:
        await conn.close()


async def _new_doc(url, uid, pid, doc_id):
    return await _in_tx(url, lambda c: doc_versions.create_document(c, uid, pid, doc_id,
                                                                    file_name=doc_id[:4]))


async def test_two_simultaneous_uploads_become_consecutive_versions(scratch):
    url, _conn, uid, pid = scratch
    x = await _new_doc(url, uid, pid, C[1])
    results = await asyncio.gather(
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[2], file_name="b")),
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[3], file_name="c")))
    assert sorted(r["version"] for r in results) == [2, 3]


async def test_same_bytes_into_one_document_at_once_is_one_version_and_one_409(scratch):
    """What the document lock decides: the loser must re-check §4.3 after the
    winner commits (409 already_current), not append a duplicate version."""
    url, conn, uid, pid = scratch
    x = await _new_doc(url, uid, pid, C[1])
    results = await _race(
        url, conn, "SELECT 1 FROM project_docs WHERE id = %s FOR UPDATE", (x,),
        lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[2], file_name="b"),
        lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[2], file_name="b"))
    wins = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, Exception)]
    assert [w["version"] for w in wins] == [2]
    assert len(errors) == 1 and isinstance(errors[0], HTTPException)
    assert (errors[0].status_code, errors[0].detail["error"]) == (409, "already_current")
    cur = await conn.execute(
        "SELECT count(*) FILTER (WHERE is_current), count(*), "
        "(SELECT last_version FROM project_docs WHERE id = %s) "
        "FROM project_documents WHERE project_doc_id = %s", (x, x))
    assert await cur.fetchone() == (1, 2, 2)


async def test_crossed_merges_one_wins_one_404s_no_deadlock(scratch):
    url, conn, uid, pid = scratch
    a = await _new_doc(url, uid, pid, C[1])
    b = await _new_doc(url, uid, pid, C[2])
    results = await _race(
        url, conn, "SELECT 1 FROM project_docs WHERE id = ANY(%s::uuid[]) FOR UPDATE", ([a, b],),
        lambda c: doc_versions.merge_documents(c, uid, pid, a, b),
        lambda c: doc_versions.merge_documents(c, uid, pid, b, a))
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], HTTPException), results
    assert errors[0].status_code == 404


async def test_same_bytes_into_two_documents_at_once_is_a_409_not_a_500(scratch):
    url, _conn, uid, pid = scratch
    x = await _new_doc(url, uid, pid, C[1])
    y = await _new_doc(url, uid, pid, C[2])
    results = await asyncio.gather(
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[5], file_name="e")),
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, y, doc_id=C[5], file_name="e")),
        return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], HTTPException)
    assert (errors[0].status_code, errors[0].detail["error"]) == (409, "current_elsewhere")
