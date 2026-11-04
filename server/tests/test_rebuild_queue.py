"""The per-document rebuild queue lives in the database (phase 2b Task 4, spec §4.2.4): workers claim rows with
FOR UPDATE SKIP LOCKED, a dead worker's claim lapses, a failure waits before it is retried, and a restart picks
up where the queue was."""
import asyncio
import secrets

import pytest
from psycopg import AsyncConnection

from server.services import doc_pipeline, rebuild_queue
from server.tests import seed
from server.tests.chat_harness import shim_pool
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for

A, B = "a" * 64, "b" * 64


async def _rows(conn):
    cur = await conn.execute("SELECT doc_id, profile, state, attempts, claimed_by FROM doc_rebuilds ORDER BY doc_id")
    return await cur.fetchall()


async def test_enqueue_twice_is_one_row(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await rebuild_queue.enqueue(db_conn, A, "p1")
    await rebuild_queue.enqueue(db_conn, A, "p1")
    assert await _rows(db_conn) == [(A, "p1", "queued", 0, None)]
    await rebuild_queue.enqueue(db_conn, A, "p2")   # a newer profile: queued again, for it
    assert await _rows(db_conn) == [(A, "p2", "queued", 0, None)]


async def test_a_deleted_document_leaves_the_queue(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await rebuild_queue.enqueue(db_conn, A, "p1")
    await db_conn.execute("DELETE FROM documents WHERE doc_id = %s", (A,))
    assert await _rows(db_conn) == []


async def test_a_failed_rebuild_waits_before_retrying(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await rebuild_queue.enqueue(db_conn, A, "p1")
    assert await rebuild_queue.claim_next(db_conn, "w1") == A
    await rebuild_queue.fail(db_conn, A, "w1", "the embedding service skipped some chunks")
    assert await rebuild_queue.claim_next(db_conn, "w1") is None          # not on the next turn
    assert (await rebuild_queue.queue_counts(db_conn))["failed"] == 1
    await db_conn.execute("UPDATE doc_rebuilds SET retry_after = now() - interval '1 second'")
    assert await rebuild_queue.claim_next(db_conn, "w2") == A             # 15 minutes later
    assert (await _rows(db_conn))[0][2:] == ("running", 2, "w2")


async def test_a_claim_held_too_long_is_taken_over(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await rebuild_queue.enqueue(db_conn, A, "p1")
    assert await rebuild_queue.claim_next(db_conn, "dead") == A
    assert await rebuild_queue.claim_next(db_conn, "alive") is None       # a live claim is left alone
    await db_conn.execute("UPDATE doc_rebuilds SET claimed_at = now() - interval '11 minutes'")
    assert await rebuild_queue.claim_next(db_conn, "alive") == A
    await rebuild_queue.finish(db_conn, A, "dead")                        # the dead worker's late finish: ignored
    assert [r[4] for r in await _rows(db_conn)] == ["alive"]
    await rebuild_queue.finish(db_conn, A, "alive")
    assert await _rows(db_conn) == []


async def test_a_heartbeat_keeps_a_long_rebuild_from_being_taken_over(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await rebuild_queue.enqueue(db_conn, A, "p1")
    await rebuild_queue.claim_next(db_conn, "w1")
    await db_conn.execute("UPDATE doc_rebuilds SET claimed_at = now() - interval '11 minutes'")
    await rebuild_queue.heartbeat(db_conn, A, "w1")
    assert await rebuild_queue.claim_next(db_conn, "w2") is None


async def test_a_reenqueue_while_running_runs_it_again(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await rebuild_queue.enqueue(db_conn, A, "p1")
    await rebuild_queue.claim_next(db_conn, "w1")
    await rebuild_queue.enqueue(db_conn, A, "p2")       # the profile moved on while p1 was being built
    await rebuild_queue.finish(db_conn, A, "w1")        # w1's finish must not drop the p2 request
    assert await _rows(db_conn) == [(A, "p2", "queued", 0, None)]


@pytest.fixture
async def scratch():
    name = f"natural_reader_queue_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        url = url_for(name)
        await apply_migrations(url)
        async with await AsyncConnection.connect(url, autocommit=True) as conn:
            for doc in (A, B):
                await seed.seed_doc(conn, doc, None, state="indexed")
                await rebuild_queue.enqueue(conn, doc, "p1")
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_two_claimers_never_get_the_same_document(scratch):
    first = await AsyncConnection.connect(scratch)        # its claim stays uncommitted, holding the row
    second = await AsyncConnection.connect(scratch)
    try:
        got_a = await rebuild_queue.claim_next(first, "w1")
        got_b = await asyncio.wait_for(rebuild_queue.claim_next(second, "w2"), 5)   # skips, doesn't wait
        assert {got_a, got_b} == {A, B}
        assert await asyncio.wait_for(rebuild_queue.claim_next(second, "w2"), 5) is None
        await first.commit()
        await second.commit()
    finally:
        await first.close()
        await second.close()


async def test_a_restart_resumes_the_queue(db_conn, monkeypatch):
    for doc in (A, B):
        await seed.seed_doc(db_conn, doc, None, state="indexed")
        await rebuild_queue.enqueue(db_conn, doc, "p1")
    shim_pool(monkeypatch, db_conn, doc_pipeline)
    built = []

    async def fake_rebuild(doc_id):
        built.append(doc_id)
        return "it broke" if doc_id == B else None
    monkeypatch.setattr(doc_pipeline, "run_rebuild", fake_rebuild)
    # A fresh worker drains what an earlier one left: one done, one failed and waiting.
    assert await doc_pipeline.run_queue_once("fresh") == A
    assert await doc_pipeline.run_queue_once("fresh") == B
    assert await doc_pipeline.run_queue_once("fresh") is None
    assert built == [A, B]
    assert [(r[0], r[2]) for r in await _rows(db_conn)] == [(B, "failed")]
    cur = await db_conn.execute("SELECT error FROM doc_rebuilds")
    assert (await cur.fetchone())[0] == "it broke"
