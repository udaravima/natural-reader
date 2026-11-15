"""Start-up recovery with the indexing queue (bulk upload spec, phase 1): every worker runs it when its pool opens,
and uvicorn restarts a dead worker by itself, so it must leave alone what a live sibling is doing. Documents stuck
mid-way with no job (from before the queue, or a job lost some other way) are queued."""
import pytest

from server import db
from server.services import doc_content, index_queue
from server.tests import seed

pytestmark = pytest.mark.asyncio
A, B, C, D, E, F = (ch * 64 for ch in "abcdef")


async def _states(conn, *ids):
    cur = await conn.execute("SELECT doc_id, state, conversion_state, conversion_error FROM documents "
                             "WHERE doc_id = ANY(%s) ORDER BY doc_id", (list(ids),))
    return {r[0]: r[1:] for r in await cur.fetchall()}


async def _claimed(conn, doc_id, *, minutes_ago, job="pipeline"):
    await index_queue.enqueue(conn, doc_id, job=job, options={} if job == "convert" else None)
    await conn.execute("UPDATE doc_index_jobs SET state = 'running', claimed_by = 'sibling', attempts = 1, "
                       "claimed_at = now() - make_interval(mins => %s) WHERE doc_id = %s", (minutes_ago, doc_id))


async def _jobs(conn):
    cur = await conn.execute("SELECT doc_id, job, requested_by FROM doc_index_jobs ORDER BY doc_id")
    return await cur.fetchall()


async def test_recovery_leaves_a_document_with_a_live_claim_alone(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexing")
    await seed.seed_doc(db_conn, B, None, state="extracting")
    for doc in (A, B):
        await _claimed(db_conn, doc, minutes_ago=1)
    await doc_content.recover_states(db_conn)
    assert {d: s[0] for d, s in (await _states(db_conn, A, B)).items()} == {A: "indexing", B: "extracting"}


async def test_recovery_resets_a_document_whose_claim_lapsed_or_that_has_no_job(db_conn):
    await seed.seed_doc(db_conn, A, None, state="extracting")
    await seed.seed_doc(db_conn, B, None, state="indexing")
    await seed.seed_doc(db_conn, C, None, state="extracting")
    await _claimed(db_conn, A, minutes_ago=11)
    await _claimed(db_conn, B, minutes_ago=11)
    await doc_content.recover_states(db_conn)
    assert {d: s[0] for d, s in (await _states(db_conn, A, B, C)).items()} == {
        A: "stored", B: "extracted", C: "stored"}


async def test_a_live_or_waiting_conversion_is_not_stamped_interrupted(db_conn):
    for doc in (A, B, C):
        await seed.seed_doc(db_conn, doc, None, state="indexed")
        await db_conn.execute("UPDATE documents SET conversion_state = 'converting' WHERE doc_id = %s", (doc,))
    await _claimed(db_conn, A, minutes_ago=1, job="convert")       # running now, on a sibling
    await _claimed(db_conn, B, minutes_ago=11, job="convert")      # lapsed: the convert lane takes it over
    await index_queue.enqueue(db_conn, C, job="convert", options={})   # still waiting its turn
    await db.reset_interrupted_conversions(db_conn)
    assert {d: s[1] for d, s in (await _states(db_conn, A, B, C)).items()} == {
        A: "converting", B: "converting", C: "converting"}


async def test_a_conversion_with_no_job_is_interrupted_as_before(db_conn):
    await seed.seed_doc(db_conn, A, None, state="indexed")
    await db_conn.execute("UPDATE documents SET conversion_state = 'converting' WHERE doc_id = %s", (A,))
    await db.reset_interrupted_conversions(db_conn)
    assert (await _states(db_conn, A))[A] == ("indexed", None, "Interrupted by server restart")


async def test_stuck_documents_get_a_job(db_conn, tmp_path):
    stored = tmp_path / "a.txt"
    stored.write_text("x")
    await seed.seed_doc(db_conn, A, None, state="stored", bytes_path=stored)
    await seed.seed_doc(db_conn, B, None, state="extracted")              # its passages exist: only vectors lack
    await seed.seed_doc(db_conn, C, None, state="stored")                 # no file: Retry index says why, not us
    await seed.seed_doc(db_conn, D, None, state="indexed")
    await seed.seed_doc(db_conn, E, None, state="failed", bytes_path=stored)
    await seed.seed_doc(db_conn, F, None, state="stored", bytes_path=stored)
    await index_queue.enqueue(db_conn, F, job="legacy_swap")             # already has a job
    assert await doc_content.enqueue_stuck(db_conn) == 2
    assert await _jobs(db_conn) == [(A, "pipeline", None), (B, "pipeline", None), (F, "legacy_swap", None)]
    assert await doc_content.enqueue_stuck(db_conn) == 0
