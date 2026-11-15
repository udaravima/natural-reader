"""Helper model phase 1: the describe queue and its lane (spec 2026-11-15 §5.1-§5.3, §5.5). A job is for one holding,
a project's version of a document or a person's library entry, and what the helper writes stays on that holding:
the vocabulary it was shown is that holding's own. Generation waits behind indexing and for its content to be
indexed, runs one job at a time across workers, tries twice, and never marks a document failed."""
from __future__ import annotations

import hashlib

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import describe_queue, doc_describe, doc_pipeline, doc_versions, helper, index_queue
from server.tests import seed
from server.tests.chat_harness import shim_pool
from server.tests.docs_harness import build_docs_app
from server.tests.test_chat_reader import FakeRouter

ON = helper.HelperConfig(model="ollama:gemma4:e4b", timeout_s=5.0, input_chars=6000, num_ctx=None, local=True)
OFF = helper.HelperConfig(model="", timeout_s=5.0, input_chars=6000, num_ctx=None, local=False)
GOOD = '{"tags": ["Finance", "risk"], "description": "Yearly results and outlook."}'


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


async def _content(conn, doc_id, holder=None, *, state="indexed", file_name="report.pdf", tags=(),
                   chunks=("Alpha passage.", "Beta passage.")):
    await seed.seed_doc(conn, doc_id, holder, file_name=file_name, state=state, tags=tags)
    for i, text in enumerate(chunks):
        await conn.execute("INSERT INTO doc_chunks (doc_id, ord, page, text, text_hash) VALUES (%s, %s, 1, %s, %s)",
                           (doc_id, i, text, hashlib.sha256(text.encode()).hexdigest()))
    return doc_id


async def _version_gen(conn, pdoc, doc_id):
    cur = await conn.execute("SELECT gen_tags, gen_description, gen_model, gen_at IS NOT NULL FROM project_documents "
                             "WHERE project_doc_id = %s AND doc_id = %s", (pdoc, doc_id))
    return await cur.fetchone()


async def _entry_gen(conn, user_id, doc_id):
    cur = await conn.execute("SELECT gen_tags, gen_description, gen_model, gen_at IS NOT NULL FROM library_entries "
                             "WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
    return await cur.fetchone()


async def _queue(conn):
    cur = await conn.execute("SELECT doc_id, project_doc_id::text, user_id::text, attempts, claimed_by "
                             "FROM doc_describe_jobs ORDER BY id")
    return await cur.fetchall()


def _prompt(fake, n=0):
    return fake.calls[n][1][1].content


A, B, C = "a" * 64, "b" * 64, "c" * 64
NOTHING = (None, None, None, False)


# ---------- the queue ----------

async def test_claim_waits_for_empty_index_queue(db_conn):
    ann = await _user(db_conn, "ann")
    await _content(db_conn, A, ann)
    await seed.seed_doc(db_conn, B, None, state="stored")
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await index_queue.enqueue(db_conn, B)
    assert await describe_queue.claim_next(db_conn, "w") is None                 # indexing goes first
    await db_conn.execute("DELETE FROM doc_index_jobs")
    claim = await describe_queue.claim_next(db_conn, "w")
    assert (claim.doc_id, claim.user_id, claim.project_doc_id, claim.attempts) == (A, ann, None, 1)


async def test_a_job_waits_until_its_content_is_indexed(db_conn):
    ann = await _user(db_conn, "ann")
    await _content(db_conn, A, ann, state="stored")
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    assert await describe_queue.claim_next(db_conn, "w") is None
    assert await describe_queue.waiting_entries(db_conn, ann, [A]) == set()     # nothing to say "Writing tags" about
    await db_conn.execute("UPDATE documents SET state = 'indexed' WHERE doc_id = %s", (A,))
    assert await describe_queue.waiting_entries(db_conn, ann, [A]) == {A}
    assert (await describe_queue.claim_next(db_conn, "w")).doc_id == A


async def test_one_job_per_holding(db_conn):
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    await _content(db_conn, A, ann)
    x = await seed.place_doc(db_conn, pid, A, ann)
    assert await describe_queue.enqueue(db_conn, A, project_doc_id=x, requested_by=ann) is True
    assert await describe_queue.enqueue(db_conn, A, project_doc_id=x, requested_by=None) is False
    assert await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann) is True      # another holding
    assert await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann) is False
    assert [r[:3] for r in await _queue(db_conn)] == [(A, x, None), (A, None, ann)]


async def test_one_describe_at_a_time_across_workers(db_conn):
    ann = await _user(db_conn, "ann")
    await _content(db_conn, A, ann)
    await _content(db_conn, B, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await describe_queue.enqueue(db_conn, B, user_id=ann, requested_by=ann)
    first = await describe_queue.claim_next(db_conn, "w1")
    assert first.doc_id == A
    assert await describe_queue.claim_next(db_conn, "w2") is None                # A is running
    assert await describe_queue.finish(db_conn, first.id, "w1") is True
    assert (await describe_queue.claim_next(db_conn, "w2")).doc_id == B


async def test_a_job_goes_with_its_holding(db_conn):
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    await _content(db_conn, A, ann)
    x = await seed.place_doc(db_conn, pid, A, ann)
    await describe_queue.enqueue(db_conn, A, project_doc_id=x, requested_by=ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await db_conn.execute("DELETE FROM project_docs WHERE id = %s", (x,))
    assert [r[:3] for r in await _queue(db_conn)] == [(A, None, ann)]
    await db_conn.execute("DELETE FROM library_entries WHERE user_id = %s AND doc_id = %s", (ann, A))
    assert await _queue(db_conn) == []


async def test_a_version_no_longer_current_is_dropped_uncalled(db_conn, helper_as):
    fake = helper_as(ON, GOOD)
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    await _content(db_conn, A, ann)
    await _content(db_conn, B, ann)
    x = await seed.place_doc(db_conn, pid, A, ann)
    await describe_queue.enqueue(db_conn, A, project_doc_id=x, requested_by=ann)
    await seed.add_version(db_conn, x, B, ann)
    await describe_queue.enqueue(db_conn, B, project_doc_id=x, requested_by=ann)
    assert await doc_pipeline.run_describe_once("w") is not None
    assert len(fake.calls) == 1 and await _queue(db_conn) == []
    assert await _version_gen(db_conn, x, A) == NOTHING
    assert (await _version_gen(db_conn, x, B))[0] == ["finance", "risk"]


async def test_a_merge_takes_the_waiting_job_with_its_version(db_conn):
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    await _content(db_conn, A, ann)
    await _content(db_conn, B, ann)
    x = await seed.place_doc(db_conn, pid, A, ann)
    y = await seed.place_doc(db_conn, pid, B, ann)
    await describe_queue.enqueue(db_conn, B, project_doc_id=y, requested_by=ann)
    await doc_versions.merge_documents(db_conn, ann, pid, x, y)
    assert [r[:3] for r in await _queue(db_conn)] == [(B, x, None)]


# ---------- the job ----------

async def test_a_project_job_writes_that_version_only(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    fake = helper_as(ON, GOOD)
    await _content(db_conn, A, ann)
    x = await seed.place_doc(db_conn, pid, A, ann, file_name="Q3 plan.pdf")
    await describe_queue.enqueue(db_conn, A, project_doc_id=x, requested_by=ann)
    assert await doc_pipeline.run_describe_once("w") is not None
    assert await _version_gen(db_conn, x, A) == (["finance", "risk"], "Yearly results and outlook.",
                                                 "ollama:gemma4:e4b", True)
    assert await _entry_gen(db_conn, ann, A) == NOTHING                          # her library entry is another holding
    cur = await db_conn.execute("SELECT state, error_message FROM documents WHERE doc_id = %s", (A,))
    assert await cur.fetchone() == ("indexed", None)
    assert await _queue(db_conn) == []
    assert "File name: Q3 plan.pdf" in _prompt(fake)                             # the name the project gave it
    assert "Alpha passage.\n\nBeta passage." in _prompt(fake)


async def test_a_library_job_writes_that_entry_only(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    helper_as(ON, GOOD)
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await doc_pipeline.run_describe_once("w")
    assert (await _entry_gen(db_conn, ann, A))[:2] == (["finance", "risk"], "Yearly results and outlook.")


async def test_the_project_vocabulary_is_shown_to_the_model(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    fake = helper_as(ON, GOOD)
    await _content(db_conn, B, ann)
    b = await seed.place_doc(db_conn, pid, B, ann)
    await db_conn.execute("UPDATE project_documents SET gen_tags = ARRAY['budget', 'finance'], gen_at = now() "
                          "WHERE project_doc_id = %s", (b,))
    await _content(db_conn, C, ann)
    c = await seed.place_doc(db_conn, pid, C, ann)
    await db_conn.execute("UPDATE project_docs SET tags = ARRAY['finance', 'audit'] WHERE id = %s", (c,))
    await _content(db_conn, A, ann)
    x = await seed.place_doc(db_conn, pid, A, ann)
    await describe_queue.enqueue(db_conn, A, project_doc_id=x, requested_by=ann)
    await doc_pipeline.run_describe_once("w")
    used = [line for line in _prompt(fake).split("\n") if line.startswith("Tags in use:")]
    assert used == ["Tags in use: finance, audit, budget"]                       # most used first


async def test_a_library_job_is_shown_the_persons_own_tags(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    fake = helper_as(ON, GOOD)
    await seed.seed_doc(db_conn, B, ann, tags=("travel", "receipts"), state="indexed")
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await doc_pipeline.run_describe_once("w")
    assert "Tags in use: receipts, travel" in _prompt(fake)


async def test_a_projects_words_never_reach_another_project_holding_the_file(db_conn, helper_as):
    """The leak (b) closes: P's typed code name is in P's vocabulary, the model copies it into P's answer, and Q,
    holding the same file, sees none of it: not in its row, not in the prompt written for it."""
    ann = await _user(db_conn, "ann")
    bob = await _user(db_conn, "bob")
    p = await seed.make_project(db_conn, ann, "P")
    q = await seed.make_project(db_conn, bob, "Q")
    await _content(db_conn, C, ann)
    c = await seed.place_doc(db_conn, p, C, ann)
    await db_conn.execute("UPDATE project_docs SET tags = ARRAY['nightingale'] WHERE id = %s", (c,))
    await _content(db_conn, A, ann)
    x = await seed.place_doc(db_conn, p, A, ann)
    y = await seed.place_doc(db_conn, q, A, bob)
    fake = helper_as(ON, '{"tags": ["nightingale", "finance"], "description": "Nightingale budget."}',
                     '{"tags": ["finance"], "description": "A budget."}')
    await describe_queue.enqueue(db_conn, A, project_doc_id=x, requested_by=ann)
    await doc_pipeline.run_describe_once("w")
    assert (await _version_gen(db_conn, x, A))[:2] == (["nightingale", "finance"], "Nightingale budget.")
    assert await _version_gen(db_conn, y, A) == NOTHING
    q_rows = await doc_versions.list_project_documents(db_conn, q, show_email=False)
    assert [(r["tags"], r["description"], r["generated"]) for r in q_rows] == [([], None, False)]
    await describe_queue.enqueue(db_conn, A, project_doc_id=y, requested_by=bob)
    await doc_pipeline.run_describe_once("w")
    assert "nightingale" not in _prompt(fake, 1).lower()
    assert (await _version_gen(db_conn, y, A))[:2] == (["finance"], "A budget.")


async def test_a_persons_library_tags_never_reach_another_holder(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    bob = await _user(db_conn, "bob")
    await seed.seed_doc(db_conn, B, ann, tags=("secret-x",), state="indexed")
    await _content(db_conn, A, ann)
    await seed.share_doc(db_conn, A, ann, bob)
    fake = helper_as(ON, '{"tags": ["secret-x"], "description": "About secret-x."}', GOOD)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await doc_pipeline.run_describe_once("w")
    assert await _entry_gen(db_conn, bob, A) == NOTHING
    await describe_queue.enqueue(db_conn, A, user_id=bob, requested_by=bob)
    await doc_pipeline.run_describe_once("w")
    assert "secret-x" not in _prompt(fake, 1)
    assert (await _entry_gen(db_conn, bob, A))[0] == ["finance", "risk"]


async def test_the_prompt_names_the_file_as_its_holding_does(db_conn, helper_as):
    """The content's own name is its first uploader's, maybe in another project: never sent for another holding."""
    ann = await _user(db_conn, "ann")
    bob = await _user(db_conn, "bob")
    q = await seed.make_project(db_conn, bob, "Q")
    await _content(db_conn, A, ann, file_name="Nightingale budget.xlsx")
    y = await seed.place_doc(db_conn, q, A, bob, file_name="budget.xlsx")
    fake = helper_as(ON, GOOD)
    await describe_queue.enqueue(db_conn, A, project_doc_id=y, requested_by=bob)
    await doc_pipeline.run_describe_once("w")
    assert "File name: budget.xlsx" in _prompt(fake) and "Nightingale" not in _prompt(fake)


async def test_two_attempts_then_dropped(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    fake = helper_as(ON, "no json here")
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    assert await doc_pipeline.run_describe_once("w") is not None
    assert await _queue(db_conn) == [(A, None, ann, 1, None)]                    # back in the queue
    assert await doc_pipeline.run_describe_once("w") is not None
    assert await _queue(db_conn) == []                                           # given up
    assert len(fake.calls) == 2
    assert await _entry_gen(db_conn, ann, A) == NOTHING


async def test_lapsed_claim_is_taken_over(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    helper_as(ON, GOOD)
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await db_conn.execute("UPDATE doc_describe_jobs SET claimed_by = 'dead', attempts = 1, "
                          "claimed_at = now() - interval '11 minutes'")
    assert await doc_pipeline.run_describe_once("w") is not None
    assert (await _entry_gen(db_conn, ann, A))[0] == ["finance", "risk"]


async def test_a_job_that_killed_its_worker_twice_is_dropped_uncalled(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    fake = helper_as(ON, GOOD)
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await db_conn.execute("UPDATE doc_describe_jobs SET claimed_by = 'dead', attempts = 2, "
                          "claimed_at = now() - interval '11 minutes'")
    assert await doc_pipeline.run_describe_once("w") is not None
    assert await _queue(db_conn) == [] and fake.calls == []


async def test_helper_off_drops_waiting_jobs(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    fake = helper_as(OFF)
    await _content(db_conn, A, ann)
    await _content(db_conn, B, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await describe_queue.enqueue(db_conn, B, user_id=ann, requested_by=ann)
    assert await doc_pipeline.run_describe_once("w") is None
    assert await _queue(db_conn) == [] and fake.calls == []


async def test_document_deleted_drops_job(db_conn):
    ann = await _user(db_conn, "ann")
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await db_conn.execute("DELETE FROM documents WHERE doc_id = %s", (A,))
    assert await _queue(db_conn) == []


async def test_failure_leaves_document_state_untouched(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    helper_as(ON, RuntimeError("down"))
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    await doc_pipeline.run_describe_once("w")
    await doc_pipeline.run_describe_once("w")
    assert await _entry_gen(db_conn, ann, A) == NOTHING
    cur = await db_conn.execute("SELECT state, error_message FROM documents WHERE doc_id = %s", (A,))
    assert await cur.fetchone() == ("indexed", None)


async def test_over_budget_requester_is_skipped(db_conn, helper_as, monkeypatch):
    ann = await _user(db_conn, "ann")
    fake = helper_as(ON, GOOD)
    monkeypatch.setattr(helper, "_deployment_budget", lambda: 10)
    from server.services import inference_budget
    await inference_budget.record_usage(db_conn, ann, 50, 0, model="ollama:x", kind="chat", estimated=False)
    await _content(db_conn, A, ann)
    await describe_queue.enqueue(db_conn, A, user_id=ann, requested_by=ann)
    assert await doc_pipeline.run_describe_once("w") is not None
    assert await _queue(db_conn) == [] and fake.calls == []                      # no retry: over budget all day


# ---------- queueing a new holding (spec §5.3) ----------

async def test_a_new_version_is_queued_when_the_helper_is_on(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    await _content(db_conn, A, ann, state="stored")                              # indexing hasn't finished: it waits
    x = await seed.place_doc(db_conn, pid, A, ann)
    helper_as(OFF)
    assert await doc_describe.queue_version(db_conn, x, requested_by=ann) is False
    helper_as(ON)
    assert await doc_describe.queue_version(db_conn, x, requested_by=ann) is True
    assert [r[:3] for r in await _queue(db_conn)] == [(A, x, None)]


async def test_a_version_already_described_is_not_queued(db_conn, helper_as):
    helper_as(ON)
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    await _content(db_conn, A, ann)
    x = await seed.place_doc(db_conn, pid, A, ann)
    await db_conn.execute("UPDATE project_documents SET gen_at = now() WHERE project_doc_id = %s", (x,))
    assert await doc_describe.queue_version(db_conn, x, requested_by=ann) is False
    assert await _queue(db_conn) == []


async def test_a_restored_version_reuses_what_was_written_for_it_here(db_conn, helper_as):
    helper_as(ON)
    ann = await _user(db_conn, "ann")
    pid = await seed.make_project(db_conn, ann)
    await _content(db_conn, A, ann)
    await _content(db_conn, B, ann)
    x = await seed.place_doc(db_conn, pid, A, ann)
    await db_conn.execute("UPDATE project_documents SET gen_tags = ARRAY['v1'], gen_description = 'One.', "
                          "gen_model = 'm', gen_at = now() WHERE project_doc_id = %s", (x,))
    await seed.add_version(db_conn, x, B, ann)
    await seed.add_version(db_conn, x, A, ann, origin="restore", origin_version=1)
    assert await doc_describe.queue_version(db_conn, x, requested_by=ann) is False
    cur = await db_conn.execute("SELECT gen_tags, gen_description FROM project_documents "
                                "WHERE project_doc_id = %s AND version = 3", (x,))
    assert await cur.fetchone() == (["v1"], "One.")
    assert await _queue(db_conn) == []


async def test_another_projects_values_for_the_same_file_are_never_reused(db_conn, helper_as):
    helper_as(ON)
    ann = await _user(db_conn, "ann")
    p = await seed.make_project(db_conn, ann, "P")
    q = await seed.make_project(db_conn, ann, "Q")
    await _content(db_conn, A, ann)
    x = await seed.place_doc(db_conn, p, A, ann)
    await db_conn.execute("UPDATE project_documents SET gen_tags = ARRAY['p'], gen_at = now() "
                          "WHERE project_doc_id = %s", (x,))
    y = await seed.place_doc(db_conn, q, A, ann)
    assert await doc_describe.queue_version(db_conn, y, requested_by=ann) is True
    assert await _version_gen(db_conn, y, A) == NOTHING


async def test_a_library_entry_is_queued_when_the_helper_is_on(db_conn, helper_as):
    ann = await _user(db_conn, "ann")
    await _content(db_conn, A, ann)
    helper_as(OFF)
    assert await doc_describe.queue_entry(db_conn, ann, A) is False
    helper_as(ON)
    assert await doc_describe.queue_entry(db_conn, ann, A) is True
    await db_conn.execute("DELETE FROM doc_describe_jobs")
    await db_conn.execute("UPDATE library_entries SET gen_at = now() WHERE user_id = %s", (ann,))
    assert await doc_describe.queue_entry(db_conn, ann, A) is False              # already written for her
