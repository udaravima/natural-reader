"""The queue of indexing and conversion jobs (bulk upload spec, phase 1; table doc_index_jobs, migration 027):
one row per document, claimed with FOR UPDATE SKIP LOCKED, a dead worker's claim taken over, every claim counted."""
import asyncio
import secrets

import pytest
from psycopg import AsyncConnection

from server.auth.users import resolve_or_provision_user
from server.services import index_queue, rebuild_queue
from server.tests import seed
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for

A, B, C, D = "a" * 64, "b" * 64, "c" * 64, "d" * 64
INDEX, CONVERT = index_queue.INDEX_JOBS, index_queue.CONVERT_JOBS


async def _user(conn, sub):
    return str((await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"])


async def _docs(conn, *ids, state="stored"):
    for i, doc in enumerate(ids):
        await seed.seed_doc(conn, doc, None, state=state)
    return ids


async def _rows(conn):
    cur = await conn.execute(
        "SELECT doc_id, job, requested_by::text, state, attempts FROM doc_index_jobs ORDER BY queued_at, doc_id")
    return await cur.fetchall()


async def _age(conn, doc_id, minutes=11):
    await conn.execute("UPDATE doc_index_jobs SET claimed_at = now() - make_interval(mins => %s) WHERE doc_id = %s",
                       (minutes, doc_id))


async def _queue(conn, *ids, job="pipeline", **kw):
    """Enqueue in order, each a second after the one before, so the order doesn't hang on the clock."""
    for i, doc in enumerate(ids):
        await index_queue.enqueue(conn, doc, job=job, **kw)
        await conn.execute("UPDATE doc_index_jobs SET queued_at = now() + make_interval(secs => %s) WHERE doc_id = %s",
                           (i, doc))


async def test_enqueue_twice_is_one_row_and_keeps_the_first_requester(db_conn):
    await _docs(db_conn, A)
    ann, bob = await _user(db_conn, "ann"), await _user(db_conn, "bob")
    assert await index_queue.enqueue(db_conn, A, requested_by=ann) is True
    assert await index_queue.enqueue(db_conn, A, requested_by=bob) is False
    assert await _rows(db_conn) == [(A, "pipeline", ann, "queued", 0)]


async def test_claims_go_oldest_first_and_never_twice(db_conn):
    await _docs(db_conn, A, B)
    await _queue(db_conn, A, B)
    first = await index_queue.claim_next(db_conn, "w1", INDEX)
    second = await index_queue.claim_next(db_conn, "w2", INDEX)
    assert (first.doc_id, second.doc_id) == (A, B)
    assert await index_queue.claim_next(db_conn, "w3", INDEX) is None
    assert first == index_queue.Claim(A, "pipeline", None, 1, None)


async def test_each_lane_claims_only_its_jobs(db_conn):
    await _docs(db_conn, A, B)
    await index_queue.enqueue(db_conn, A)
    await index_queue.enqueue(db_conn, B, job="convert", options={"preset": "fast"})
    got = await index_queue.claim_next(db_conn, "w", CONVERT)
    assert (got.doc_id, got.job, got.options) == (B, "convert", {"preset": "fast"})
    assert await index_queue.claim_next(db_conn, "w", CONVERT) is None
    assert (await index_queue.claim_next(db_conn, "w", INDEX)).doc_id == A


async def test_a_lapsed_claim_is_taken_over_and_counts_an_attempt(db_conn):
    await _docs(db_conn, A)
    await index_queue.enqueue(db_conn, A)
    await index_queue.claim_next(db_conn, "w1", INDEX)
    await _age(db_conn, A)
    taken = await index_queue.claim_next(db_conn, "w2", INDEX)
    assert (taken.doc_id, taken.attempts) == (A, 2)
    assert await index_queue.finish(db_conn, A, "w1") is False    # the dead worker's late finish
    assert len(await _rows(db_conn)) == 1
    assert await index_queue.finish(db_conn, A, "w2") is True
    assert await _rows(db_conn) == []


async def test_a_fresh_claim_is_not_taken_over(db_conn):
    await _docs(db_conn, A)
    await index_queue.enqueue(db_conn, A)
    await index_queue.claim_next(db_conn, "w1", INDEX)
    assert await index_queue.claim_next(db_conn, "w2", INDEX) is None


async def test_heartbeat_keeps_a_claim(db_conn):
    await _docs(db_conn, A)
    await index_queue.enqueue(db_conn, A)
    await index_queue.claim_next(db_conn, "w1", INDEX)
    await _age(db_conn, A)
    await index_queue.heartbeat(db_conn, A, "w1")
    assert await index_queue.claim_next(db_conn, "w2", INDEX) is None


async def test_live_claim_is_true_only_for_a_running_unlapsed_row(db_conn):
    await _docs(db_conn, A)
    assert await index_queue.live_claim(db_conn, A) is False      # no row
    await index_queue.enqueue(db_conn, A)
    assert await index_queue.live_claim(db_conn, A) is False      # queued
    await index_queue.claim_next(db_conn, "w1", INDEX)
    assert await index_queue.live_claim(db_conn, A) is True
    await _age(db_conn, A)
    assert await index_queue.live_claim(db_conn, A) is False      # lapsed


async def test_convert_replaces_a_queued_indexing_job(db_conn):
    await _docs(db_conn, A)
    ann, bob = await _user(db_conn, "ann"), await _user(db_conn, "bob")
    await index_queue.enqueue(db_conn, A, requested_by=ann)
    cur = await db_conn.execute("SELECT queued_at FROM doc_index_jobs WHERE doc_id = %s", (A,))
    queued_at = (await cur.fetchone())[0]
    assert await index_queue.enqueue_convert(db_conn, A, requested_by=bob, options={"preset": "fast"}) is True
    assert await _rows(db_conn) == [(A, "convert", bob, "queued", 0)]
    cur = await db_conn.execute("SELECT queued_at, options FROM doc_index_jobs WHERE doc_id = %s", (A,))
    assert await cur.fetchone() == (queued_at, {"preset": "fast"})


async def test_convert_on_a_document_with_no_job_adds_one_and_a_second_replaces_its_options(db_conn):
    await _docs(db_conn, A)
    assert await index_queue.enqueue_convert(db_conn, A, requested_by=None, options={"preset": "fast"}) is True
    assert await index_queue.enqueue_convert(db_conn, A, requested_by=None, options={"preset": "accurate"}) is True
    cur = await db_conn.execute("SELECT job, options FROM doc_index_jobs")
    assert await cur.fetchall() == [("convert", {"preset": "accurate"})]


async def test_convert_is_refused_while_the_document_runs(db_conn):
    await _docs(db_conn, A)
    await index_queue.enqueue(db_conn, A)
    await index_queue.claim_next(db_conn, "w1", INDEX)
    assert await index_queue.enqueue_convert(db_conn, A, requested_by=None, options={"preset": "fast"}) is False
    assert await _rows(db_conn) == [(A, "pipeline", None, "running", 1)]


async def test_a_deleted_document_leaves_the_queue(db_conn):
    await _docs(db_conn, A)
    await index_queue.enqueue(db_conn, A)
    await index_queue.claim_next(db_conn, "w1", INDEX)
    await db_conn.execute("DELETE FROM documents WHERE doc_id = %s", (A,))
    assert await _rows(db_conn) == []
    assert await index_queue.finish(db_conn, A, "w1") is False


async def test_a_deleted_requester_keeps_the_job(db_conn):
    await _docs(db_conn, A)
    ann = await _user(db_conn, "ann")
    await index_queue.enqueue(db_conn, A, requested_by=ann)
    await db_conn.execute("DELETE FROM users WHERE id = %s", (ann,))
    assert await _rows(db_conn) == [(A, "pipeline", None, "queued", 0)]


async def test_positions_count_the_lane_and_the_waiting_rebuilds(db_conn):
    await _docs(db_conn, A, B, C, D)
    for doc in ("e" * 64, "f" * 64):
        await seed.seed_doc(db_conn, doc, None, state="indexed")
        await rebuild_queue.enqueue(db_conn, doc, "p1")
    await _queue(db_conn, A, B, C)
    await index_queue.enqueue(db_conn, D, job="convert", options={})
    await index_queue.claim_next(db_conn, "w1", INDEX)            # A runs
    assert await index_queue.positions(db_conn) == {A: 0, B: 3, C: 4, D: 0}
    assert await index_queue.queue_counts(db_conn) == {"queued": 3, "running": 1}


async def test_an_unknown_job_is_refused(db_conn):
    await _docs(db_conn, A)
    with pytest.raises(ValueError):
        await index_queue.enqueue(db_conn, A, job="rebuild")


@pytest.fixture
async def scratch():
    name = f"natural_reader_index_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        url = url_for(name)
        await apply_migrations(url)
        async with await AsyncConnection.connect(url, autocommit=True) as conn:
            await _docs(conn, A, B)
            await _queue(conn, A, B)
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_two_connections_never_claim_one_job(scratch):
    first = await AsyncConnection.connect(scratch)        # its claim stays uncommitted, holding the row
    second = await AsyncConnection.connect(scratch)
    try:
        got_a = await index_queue.claim_next(first, "w1", INDEX)
        got_b = await asyncio.wait_for(index_queue.claim_next(second, "w2", INDEX), 5)   # skips, doesn't wait
        assert {got_a.doc_id, got_b.doc_id} == {A, B}
        assert await asyncio.wait_for(index_queue.claim_next(second, "w2", INDEX), 5) is None
        await first.commit()
        await second.commit()
    finally:
        await first.close()
        await second.close()


async def test_release_hands_a_running_job_back_without_counting_the_claim(db_conn):
    await _docs(db_conn, A)
    await index_queue.enqueue(db_conn, A)
    await index_queue.claim_next(db_conn, "w1", INDEX)
    assert await index_queue.release(db_conn, A, "w2") is False   # not w2's to hand back
    assert await index_queue.release(db_conn, A, "w1") is True
    assert await _rows(db_conn) == [(A, "pipeline", None, "queued", 0)]
    assert (await index_queue.claim_next(db_conn, "w3", INDEX)).attempts == 1
