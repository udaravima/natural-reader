"""Each worker's runners drain the indexing queue (bulk upload spec, phase 1): the index lane takes a rebuild first,
but not more than three in a row while an upload waits; the convert lane runs Docling one document at a time. A job
ends once, tells whoever asked, and a job that keeps killing its worker is given up."""
import asyncio
import hashlib

import pytest
from psycopg_pool import PoolTimeout

from server.auth.users import resolve_or_provision_user
from server.services import doc_convert_job, doc_pipeline, extract, index_queue, rebuild_queue
from server.tests import seed
from server.tests.docs_harness import build_docs_app

TEXT = b"The first sentence is here. The second one follows! Is this the third one?"
A, B = "a" * 64, "b" * 64


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def harness(db_conn, monkeypatch, store):
    build_docs_app(db_conn, monkeypatch, storage_dir=store)
    doc_pipeline._rebuilds_in_a_row.clear()


async def _user(conn, sub):
    return str((await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"])


async def _doc(conn, doc_id):
    cur = await conn.execute("SELECT state, error_message, conversion_state, conversion_error FROM documents "
                             "WHERE doc_id = %s", (doc_id,))
    return await cur.fetchone()


async def _jobs(conn):
    cur = await conn.execute("SELECT doc_id, state, attempts, claimed_by FROM doc_index_jobs ORDER BY doc_id")
    return await cur.fetchall()


async def _notices(conn, user_id):
    cur = await conn.execute("SELECT kind FROM notifications WHERE user_id = %s ORDER BY id", (user_id,))
    return [r[0] for r in await cur.fetchall()]


async def _text_doc(conn, store, *, state="stored", holder=None):
    doc_id = hashlib.sha256(TEXT).hexdigest()
    path = store / f"{doc_id}.txt"
    path.write_bytes(TEXT)
    await seed.seed_doc(conn, doc_id, holder, file_type="text", state=state, bytes_path=path)
    return doc_id


async def _lapsed_claim(conn, doc_id, *, attempts, job="pipeline", requested_by=None, options=None):
    await index_queue.enqueue(conn, doc_id, job=job, requested_by=requested_by, options=options)
    await conn.execute("UPDATE doc_index_jobs SET state = 'running', claimed_by = 'dead', attempts = %s, "
                       "claimed_at = now() - interval '11 minutes' WHERE doc_id = %s", (attempts, doc_id))


def _record(monkeypatch, name, calls, *, result=None):
    async def fake(doc_id, *args):
        calls.append((doc_id, *args))
        return result
    monkeypatch.setattr(doc_pipeline, name, fake)


async def test_a_queued_upload_is_indexed_and_tells_its_uploader(db_conn, store):
    ann = await _user(db_conn, "ann")
    doc_id = await _text_doc(db_conn, store, holder=ann)
    await index_queue.enqueue(db_conn, doc_id, requested_by=ann)
    assert await doc_pipeline.run_queue_once("w-up") == doc_id
    assert (await _doc(db_conn, doc_id))[0] == "indexed"
    assert await _jobs(db_conn) == []
    assert await _notices(db_conn, ann) == ["doc.indexed"]


async def test_a_rebuild_is_claimed_before_an_indexing_job(db_conn, monkeypatch):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await seed.seed_doc(db_conn, B, None, state="stored")
    await index_queue.enqueue(db_conn, B)
    await rebuild_queue.enqueue(db_conn, A, "p1")
    ran = []
    _record(monkeypatch, "run_rebuild", ran)
    _record(monkeypatch, "run_pipeline", ran)
    assert [await doc_pipeline.run_queue_once("w-order") for _ in range(3)] == [A, B, None]


async def test_after_three_rebuilds_in_a_row_a_waiting_indexing_job_goes_next(db_conn, monkeypatch):
    rebuilds = [str(n) * 64 for n in range(1, 6)]
    upload = "9" * 64
    for doc in rebuilds:
        await seed.seed_doc(db_conn, doc, None, state="indexed")
        await rebuild_queue.enqueue(db_conn, doc, "p1")
    await seed.seed_doc(db_conn, upload, None, state="stored")
    await index_queue.enqueue(db_conn, upload)
    _record(monkeypatch, "run_rebuild", [])
    _record(monkeypatch, "run_pipeline", [])
    order = [await doc_pipeline.run_queue_once("w-starve") for _ in range(7)]
    assert order == rebuilds[:3] + [upload] + rebuilds[3:] + [None]


async def test_a_taken_over_job_finishes_a_document_left_indexing(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexing")
    await doc_pipeline.replace_chunks(db_conn, A, [extract.Chunk(0, 1, "paragraph", "Left half done.")])
    await _lapsed_claim(db_conn, A, attempts=1)
    assert await doc_pipeline.run_queue_once("w-take") == A
    assert (await _doc(db_conn, A))[0] == "indexed"
    cur = await db_conn.execute("SELECT bool_and(embedding IS NOT NULL) FROM doc_chunks WHERE doc_id = %s", (A,))
    assert (await cur.fetchone())[0] is True
    assert await _jobs(db_conn) == []


async def test_a_taken_over_job_restarts_a_document_left_extracting(db_conn, store):
    doc_id = await _text_doc(db_conn, store, state="extracting")
    await _lapsed_claim(db_conn, doc_id, attempts=2)
    assert await doc_pipeline.run_queue_once("w-take2") == doc_id
    assert (await _doc(db_conn, doc_id))[0] == "indexed"


async def test_the_fourth_claim_gives_up_instead_of_running(db_conn, monkeypatch):
    ann = await _user(db_conn, "ann")
    await seed.seed_doc(db_conn, A, None, state="extracting")
    await _lapsed_claim(db_conn, A, attempts=index_queue.MAX_ATTEMPTS, requested_by=ann)

    async def must_not_run(doc_id):
        raise AssertionError("a job that killed its worker 3 times ran a 4th time")
    monkeypatch.setattr(doc_pipeline, "run_pipeline", must_not_run)
    assert await doc_pipeline.run_queue_once("w-giveup") == A
    assert (await _doc(db_conn, A))[:2] == (
        "failed", "The server stopped while reading this file, 3 times. The file may be damaged.")
    assert await _notices(db_conn, ann) == ["doc.index_failed"]
    assert await _jobs(db_conn) == []


async def test_a_failure_ends_the_job_and_tells_the_requester_once(db_conn):
    ann = await _user(db_conn, "ann")
    await seed.seed_doc(db_conn, A, None, state="stored")        # no stored file: the pipeline fails
    await index_queue.enqueue(db_conn, A, requested_by=ann)
    assert await doc_pipeline.run_queue_once("w-fail") == A
    assert (await _doc(db_conn, A))[0] == "failed"
    assert await _notices(db_conn, ann) == ["doc.index_failed"]
    assert await doc_pipeline.run_queue_once("w-fail") is None      # no retry by itself
    assert await _jobs(db_conn) == []


async def test_a_job_with_no_requester_sends_no_notice(db_conn):
    await seed.seed_doc(db_conn, A, None, state="stored")
    await index_queue.enqueue(db_conn, A)
    assert await doc_pipeline.run_queue_once("w-quiet") == A
    cur = await db_conn.execute("SELECT count(*) FROM notifications")
    assert (await cur.fetchone())[0] == 0


async def test_a_document_deleted_mid_job_ends_quietly(db_conn, monkeypatch, caplog):
    ann = await _user(db_conn, "ann")
    await seed.seed_doc(db_conn, A, None, state="stored")
    await index_queue.enqueue(db_conn, A, requested_by=ann)

    async def deleted_meanwhile(doc_id):
        await db_conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
    monkeypatch.setattr(doc_pipeline, "run_pipeline", deleted_meanwhile)
    with caplog.at_level("WARNING"):
        assert await doc_pipeline.run_queue_once("w-gone") == A
    assert [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")] == []
    assert await _notices(db_conn, ann) == []
    assert await _jobs(db_conn) == []


async def test_a_job_that_raises_is_ended_not_left_running(db_conn, monkeypatch):
    await seed.seed_doc(db_conn, A, None, state="stored")
    await index_queue.enqueue(db_conn, A)

    async def broken(doc_id):
        raise RuntimeError("a bug, not the file")
    monkeypatch.setattr(doc_pipeline, "run_pipeline", broken)
    assert await doc_pipeline.run_queue_once("w-bug") == A
    assert await _jobs(db_conn) == []


async def test_a_legacy_swap_job_runs_the_swap(db_conn, monkeypatch):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await index_queue.enqueue(db_conn, A, job="legacy_swap")
    swapped, piped = [], []
    _record(monkeypatch, "run_legacy_swap", swapped)
    _record(monkeypatch, "run_pipeline", piped)
    assert await doc_pipeline.run_queue_once("w-swap") == A
    assert (swapped, piped) == ([(A,)], [])


async def test_a_new_runner_finishes_what_an_old_one_left_queued(db_conn, monkeypatch):
    for doc in (A, B):
        await seed.seed_doc(db_conn, doc, None, state="stored")
        await index_queue.enqueue(db_conn, doc)
    await db_conn.execute("UPDATE doc_index_jobs SET queued_at = queued_at + interval '1 second' WHERE doc_id = %s",
                          (B,))
    _record(monkeypatch, "run_pipeline", [])
    assert await doc_pipeline.run_queue_once("w-old") == A
    assert await doc_pipeline.run_queue_once("w-new") == B
    assert await doc_pipeline.run_queue_once("w-new") is None


async def test_the_convert_lane_runs_a_conversion_with_its_options(db_conn, monkeypatch):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await index_queue.enqueue_convert(db_conn, A, requested_by=None, options={"preset": "fast"})
    calls = []

    async def convert(doc_id, options):
        calls.append((doc_id, options))
    monkeypatch.setattr(doc_convert_job, "run_convert_job", convert)
    assert await doc_pipeline.run_queue_once("w-lane") is None          # not the index lane's
    assert await doc_pipeline.run_convert_once("w-lane") == A
    assert calls == [(A, {"preset": "fast"})]
    assert await _jobs(db_conn) == []


async def test_a_conversion_that_gave_up_is_marked_failed_and_told(db_conn, monkeypatch):
    ann = await _user(db_conn, "ann")
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await db_conn.execute("UPDATE documents SET conversion_state = 'converting' WHERE doc_id = %s", (A,))
    await _lapsed_claim(db_conn, A, attempts=index_queue.MAX_ATTEMPTS, job="convert", requested_by=ann, options={})

    async def must_not_run(doc_id, options):
        raise AssertionError("ran a 4th time")
    monkeypatch.setattr(doc_convert_job, "run_convert_job", must_not_run)
    assert await doc_pipeline.run_convert_once("w-cgiveup") == A
    state, _, conversion_state, conversion_error = await _doc(db_conn, A)
    assert (state, conversion_state) == ("indexed", "conversion_failed")      # the old index keeps answering
    assert conversion_error.startswith("The server stopped while reading this file, 3 times.")
    assert await _notices(db_conn, ann) == ["doc.convert_failed"]


async def test_a_cancelled_runner_hands_its_job_back_uncounted(db_conn, monkeypatch):
    await seed.seed_doc(db_conn, A, None, state="stored")
    await index_queue.enqueue(db_conn, A)
    started = asyncio.Event()

    async def hangs(doc_id):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(doc_pipeline, "run_pipeline", hangs)
    task = asyncio.create_task(doc_pipeline.run_queue_once("w-stop"))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _jobs(db_conn) == [(A, "queued", 0, None)]


async def test_enqueue_index_raises_when_no_connection_can_be_had(db_conn, monkeypatch):
    await seed.seed_doc(db_conn, A, None, state="stored")

    class Full:
        def connection(self, timeout=None):
            raise PoolTimeout("couldn't get a connection after 30.00 sec")
    monkeypatch.setattr(doc_pipeline, "get_pool", lambda: Full())
    with pytest.raises(PoolTimeout):
        await doc_pipeline.enqueue_index(A)
    assert await _jobs(db_conn) == []
