"""The lists and the status route say what is queued and how many jobs are ahead (bulk upload spec, phase 1), so the
screen can tell "Waiting to index (3 ahead)" from a document nothing will index."""
import pytest

from server.services import rebuild_queue
from server.tests import seed
from server.tests.chat_harness import member
from server.tests.versions_harness import build_versions_app

pytestmark = pytest.mark.asyncio
FILES = [(f"n{i}.txt", f"Document number {i}. It has words in it.".encode()) for i in range(3)]


@pytest.fixture
def app(db_conn, monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    return build_versions_app(db_conn, monkeypatch, storage_dir=store, run_queue=False)


async def _upload_three(db_conn, client):
    ids = []
    for name, data in FILES:
        r = await client.post("/v1/docs", files={"file": (name, data, "text/plain")})
        ids.append(r.json()["doc_id"])
        # one second apart, so the order doesn't hang on the clock
        await db_conn.execute("UPDATE doc_index_jobs SET queued_at = now() + make_interval(secs => %s) "
                              "WHERE doc_id = %s", (len(ids), ids[-1]))
    return ids


async def test_the_library_list_marks_queued_documents_with_what_is_ahead(db_conn, app):
    _, as_user = app
    ann = await member(db_conn, "ann", caps=("reader",))
    await seed.seed_doc(db_conn, "f" * 64, ann.user_id, state="indexed")
    async with as_user(ann) as c:
        ids = await _upload_three(db_conn, c)
        rows = {r["doc_id"]: r for r in (await c.get("/v1/docs")).json()}
    assert [(rows[d]["queued"], rows[d]["queue_ahead"]) for d in ids] == [(True, 0), (True, 1), (True, 2)]
    assert rows["f" * 64]["queued"] is False and "queue_ahead" not in rows["f" * 64]


async def test_waiting_rebuilds_count_as_ahead(db_conn, app):
    _, as_user = app
    ann = await member(db_conn, "ann", caps=("reader",))
    for doc in ("1" * 64, "2" * 64):
        await seed.seed_doc(db_conn, doc, None, state="indexed")
        await rebuild_queue.enqueue(db_conn, doc, "p1")
    async with as_user(ann) as c:
        r = await c.post("/v1/docs", files={"file": FILES[0]})
        status = (await c.get(f"/v1/docs/{r.json()['doc_id']}")).json()
    assert (status["state"], status["queued"], status["queue_ahead"]) == ("stored", True, 2)


async def test_a_projects_documents_and_a_documents_versions_report_the_queue(db_conn, app):
    _, as_user = app
    ann = await member(db_conn, "ann", caps=("reader",))
    pid = await seed.make_project(db_conn, ann.user_id, "P")
    async with as_user(ann) as c:
        ids = await _upload_three(db_conn, c)
        for doc_id in ids:
            assert (await c.put(f"/v1/projects/{pid}/docs/{doc_id}")).status_code == 201
        docs = (await c.get(f"/v1/projects/{pid}/documents")).json()
        versions = (await c.get(f"/v1/projects/{pid}/documents/{docs[2]['id']}/versions")).json()
    assert [(d["name"], d["queued"], d["queue_ahead"]) for d in docs] == [
        ("n0.txt", True, 0), ("n1.txt", True, 1), ("n2.txt", True, 2)]
    assert (versions[0]["queued"], versions[0]["queue_ahead"]) == (True, 2)


async def test_a_stored_document_with_no_job_is_not_queued(db_conn, app):
    _, as_user = app
    ann = await member(db_conn, "ann", caps=("reader",))
    await seed.seed_doc(db_conn, "e" * 64, ann.user_id, state="stored")
    async with as_user(ann) as c:
        status = (await c.get(f"/v1/docs/{'e' * 64}")).json()
    assert status["queued"] is False and "queue_ahead" not in status
