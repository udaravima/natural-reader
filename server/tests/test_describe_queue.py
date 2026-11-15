"""Helper model phase 1, Task 4: the describe queue and its lane (spec 2026-11-15 §5.1, §5.3). Generation waits
behind indexing, runs one document at a time across workers, tries twice, and never marks a document failed."""
from __future__ import annotations

import hashlib

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import describe_queue, doc_describe, doc_pipeline, helper, index_queue
from server.tests import seed
from server.tests.chat_harness import shim_pool
from server.tests.docs_harness import build_docs_app
from server.tests.test_chat_reader import FakeRouter

ON = helper.HelperConfig(model="ollama:gemma4:e4b", timeout_s=5.0, input_chars=6000, num_ctx=None, local=True)
OFF = helper.HelperConfig(model="", timeout_s=5.0, input_chars=6000, num_ctx=None, local=False)
GOOD = '{"tags": ["Finance", "risk"], "description": "Yearly results and outlook."}'
TEXT = b"The first sentence is here. The second one follows! Is this the third one?"


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def harness(db_conn, monkeypatch, store):
    build_docs_app(db_conn, monkeypatch, storage_dir=store)
    shim_pool(monkeypatch, db_conn, doc_describe, helper)
    doc_pipeline._rebuilds_in_a_row.clear()


@pytest.fixture
def helper_as(monkeypatch):
    """Set the helper's config and its model's replies; returns the fake router (its calls)."""
    def install(cfg=ON, *replies):
        async def load(conn, env=None):
            return cfg
        monkeypatch.setattr(helper, "load_config", load)
        fake = FakeRouter(*(replies or (GOOD,)))
        monkeypatch.setattr(helper, "get_router", lambda: fake)
        return fake
    return install


async def _user(conn, sub):
    return str((await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"])


async def _indexed(conn, doc_id, holder=None, *, chunks=("Alpha passage.", "Beta passage."), project_ids=()):
    await seed.seed_doc(conn, doc_id, holder, file_name="report.pdf", state="indexed", project_ids=project_ids)
    for i, text in enumerate(chunks):
        await conn.execute("INSERT INTO doc_chunks (doc_id, ord, page, text, text_hash) VALUES (%s, %s, 1, %s, %s)",
                           (doc_id, i, text, hashlib.sha256(text.encode()).hexdigest()))
    return doc_id


async def _gen(conn, doc_id):
    cur = await conn.execute("SELECT gen_tags, gen_description, gen_model, gen_at IS NOT NULL, state, error_message "
                             "FROM documents WHERE doc_id = %s", (doc_id,))
    return await cur.fetchone()


async def _queue(conn):
    cur = await conn.execute("SELECT doc_id, attempts, claimed_by FROM doc_describe_jobs ORDER BY doc_id")
    return await cur.fetchall()


A, B, C = "a" * 64, "b" * 64, "c" * 64


async def test_claim_waits_for_empty_index_queue(db_conn):
    await _indexed(db_conn, A)
    await seed.seed_doc(db_conn, B, None, state="stored")
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await index_queue.enqueue(db_conn, B)
    assert await describe_queue.claim_next(db_conn, "w") is None                 # indexing goes first
    await db_conn.execute("DELETE FROM doc_index_jobs")
    claim = await describe_queue.claim_next(db_conn, "w")
    assert (claim.doc_id, claim.attempts) == (A, 1)


async def test_one_describe_at_a_time_across_workers(db_conn):
    await _indexed(db_conn, A)
    await _indexed(db_conn, B)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await describe_queue.enqueue(db_conn, B, requested_by=None, project_id=None)
    assert (await describe_queue.claim_next(db_conn, "w1")).doc_id == A
    assert await describe_queue.claim_next(db_conn, "w2") is None                # A is running
    assert await describe_queue.finish(db_conn, A, "w1") is True
    assert (await describe_queue.claim_next(db_conn, "w2")).doc_id == B


async def test_describe_writes_gen_columns(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    fake = helper_as(ON, GOOD)
    await _indexed(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, requested_by=ann, project_id=None)
    assert await doc_pipeline.run_describe_once("w") == A
    assert await _gen(db_conn, A) == (["finance", "risk"], "Yearly results and outlook.", "ollama:gemma4:e4b",
                                      True, "indexed", None)
    assert await _queue(db_conn) == []
    _, messages, _ = fake.calls[0]
    assert "File name: report.pdf" in messages[1].content
    assert "Alpha passage.\n\nBeta passage." in messages[1].content


async def test_the_project_vocabulary_is_shown_to_the_model(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    fake = helper_as(ON, GOOD)
    await _indexed(db_conn, B, ann, project_ids=(pid,))
    await db_conn.execute("UPDATE documents SET gen_tags = ARRAY['budget', 'finance'], gen_at = now() "
                          "WHERE doc_id = %s", (B,))
    await _indexed(db_conn, C, ann, project_ids=(pid,))
    await db_conn.execute("UPDATE project_docs SET tags = ARRAY['finance', 'audit'] WHERE id = "
                          "(SELECT project_doc_id FROM project_documents WHERE doc_id = %s)", (C,))
    await _indexed(db_conn, A, ann, project_ids=(pid,))
    await describe_queue.enqueue(db_conn, A, requested_by=ann, project_id=pid)
    await doc_pipeline.run_describe_once("w")
    used = [line for line in fake.calls[0][1][1].content.split("\n") if line.startswith("Tags in use:")]
    assert used == ["Tags in use: finance, audit, budget"]                       # most used first


async def test_a_library_upload_is_shown_the_persons_own_tags(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    fake = helper_as(ON, GOOD)
    await seed.seed_doc(db_conn, B, ann, tags=("travel", "receipts"), state="indexed")
    await _indexed(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, requested_by=ann, project_id=None)
    await doc_pipeline.run_describe_once("w")
    assert "Tags in use: receipts, travel" in fake.calls[0][1][1].content


async def test_two_attempts_then_dropped(db_conn, helper_as):
    fake = helper_as(ON, "no json here")
    await _indexed(db_conn, A)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    assert await doc_pipeline.run_describe_once("w") == A
    assert await _queue(db_conn) == [(A, 1, None)]                               # back in the queue
    assert await doc_pipeline.run_describe_once("w") == A
    assert await _queue(db_conn) == []                                           # given up
    assert len(fake.calls) == 2
    assert (await _gen(db_conn, A))[3] is False


async def test_lapsed_claim_is_taken_over(db_conn, helper_as):
    helper_as(ON, GOOD)
    await _indexed(db_conn, A)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await db_conn.execute("UPDATE doc_describe_jobs SET claimed_by = 'dead', attempts = 1, "
                          "claimed_at = now() - interval '11 minutes'")
    assert await doc_pipeline.run_describe_once("w") == A
    assert (await _gen(db_conn, A))[0] == ["finance", "risk"]


async def test_a_job_that_killed_its_worker_twice_is_dropped_uncalled(db_conn, helper_as):
    fake = helper_as(ON, GOOD)
    await _indexed(db_conn, A)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await db_conn.execute("UPDATE doc_describe_jobs SET claimed_by = 'dead', attempts = 2, "
                          "claimed_at = now() - interval '11 minutes'")
    assert await doc_pipeline.run_describe_once("w") == A
    assert await _queue(db_conn) == [] and fake.calls == []


async def test_helper_off_drops_waiting_jobs(db_conn, helper_as):
    fake = helper_as(OFF)
    await _indexed(db_conn, A)
    await _indexed(db_conn, B)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await describe_queue.enqueue(db_conn, B, requested_by=None, project_id=None)
    assert await doc_pipeline.run_describe_once("w") is None
    assert await _queue(db_conn) == [] and fake.calls == []


async def test_document_deleted_drops_job(db_conn):
    await _indexed(db_conn, A)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await db_conn.execute("DELETE FROM documents WHERE doc_id = %s", (A,))
    assert await _queue(db_conn) == []


async def test_a_document_no_longer_indexed_is_dropped_uncalled(db_conn, helper_as):
    fake = helper_as(ON, GOOD)
    await _indexed(db_conn, A)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await db_conn.execute("UPDATE documents SET state = 'stored' WHERE doc_id = %s", (A,))
    assert await doc_pipeline.run_describe_once("w") == A
    assert await _queue(db_conn) == [] and fake.calls == []


async def test_failure_leaves_document_state_untouched(db_conn, helper_as):
    helper_as(ON, RuntimeError("down"))
    await _indexed(db_conn, A)
    await describe_queue.enqueue(db_conn, A, requested_by=None, project_id=None)
    await doc_pipeline.run_describe_once("w")
    await doc_pipeline.run_describe_once("w")
    assert await _gen(db_conn, A) == (None, None, None, False, "indexed", None)


async def test_over_budget_requester_is_skipped(db_conn, helper_as, monkeypatch):
    ann = await _user(db_conn, "ann")
    fake = helper_as(ON, GOOD)
    monkeypatch.setattr(helper, "_deployment_budget", lambda: 10)
    from server.services import inference_budget
    await inference_budget.record_usage(db_conn, ann, 50, 0, model="ollama:x", kind="chat", estimated=False)
    await _indexed(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, requested_by=ann, project_id=None)
    assert await doc_pipeline.run_describe_once("w") == A
    assert await _queue(db_conn) == [] and fake.calls == []                      # no retry: over budget all day


async def test_index_finish_enqueues_when_on(db_conn, store, helper_as):
    helper_as(ON)
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    doc_id = hashlib.sha256(TEXT).hexdigest()
    (store / f"{doc_id}.txt").write_bytes(TEXT)
    await seed.seed_doc(db_conn, doc_id, ann, file_type="text", state="stored", bytes_path=store / f"{doc_id}.txt")
    await index_queue.enqueue(db_conn, doc_id, requested_by=ann, options={"project_id": str(pid)})
    assert await doc_pipeline.run_queue_once("w") == doc_id
    cur = await db_conn.execute("SELECT doc_id, requested_by::text, project_id::text FROM doc_describe_jobs")
    assert await cur.fetchall() == [(doc_id, ann, str(pid))]


async def test_index_finish_skips_when_off_or_already_generated(db_conn, store, helper_as):
    doc_id = hashlib.sha256(TEXT).hexdigest()
    (store / f"{doc_id}.txt").write_bytes(TEXT)
    await seed.seed_doc(db_conn, doc_id, None, file_type="text", state="stored", bytes_path=store / f"{doc_id}.txt")
    helper_as(OFF)
    await index_queue.enqueue(db_conn, doc_id)
    await doc_pipeline.run_queue_once("w")
    assert await _queue(db_conn) == []
    helper_as(ON)
    await db_conn.execute("UPDATE documents SET state = 'stored', gen_at = now() WHERE doc_id = %s", (doc_id,))
    await index_queue.enqueue(db_conn, doc_id)
    await doc_pipeline.run_queue_once("w")
    assert await _queue(db_conn) == []                                           # already has tags
    await db_conn.execute("UPDATE documents SET state = 'stored', gen_at = NULL WHERE doc_id = %s", (doc_id,))
    await index_queue.enqueue(db_conn, doc_id, options={"describe": False})      # the upload's tick was cleared
    await doc_pipeline.run_queue_once("w")
    assert await _queue(db_conn) == []


async def test_a_project_upload_without_options_uses_its_placement(db_conn, store, helper_as):
    helper_as(ON)
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    doc_id = hashlib.sha256(TEXT).hexdigest()
    (store / f"{doc_id}.txt").write_bytes(TEXT)
    await seed.seed_doc(db_conn, doc_id, ann, file_type="text", state="stored", bytes_path=store / f"{doc_id}.txt",
                        project_ids=(pid,))
    await index_queue.enqueue(db_conn, doc_id, requested_by=ann)
    await doc_pipeline.run_queue_once("w")
    cur = await db_conn.execute("SELECT project_id::text FROM doc_describe_jobs")
    assert await cur.fetchall() == [(str(pid),)]
