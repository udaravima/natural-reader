"""The routes that start indexing or a conversion queue it (bulk upload spec, phase 1) instead of running it in the
request's background: an upload, Index, re-extract, a new version, Convert."""
import hashlib
import re
from pathlib import Path

import pytest

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.services import doc_pipeline, docling_convert, index_queue
from server.tests import seed
from server.tests.chat_harness import member
from server.tests.docs_harness import build_docs_app
from server.tests.pdfgen import make_pdf
from server.tests.queue_helpers import drain
from server.tests.versions_harness import build_versions_app

TEXT = b"The first sentence is here. The second one follows! Is this the third one?"
ROUTERS = Path(__file__).parents[1] / "routers"
BEING_INDEXED = "This document is being indexed. Try again when it has finished."


def _sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def harness(db_conn, monkeypatch, store):
    return build_docs_app(db_conn, monkeypatch, storage_dir=store, run_queue=False)


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=str(u["id"]), email=u["email"], role="member",
                          capabilities=frozenset({"reader", "docling"}))


async def _jobs(db_conn):
    cur = await db_conn.execute("SELECT doc_id, job, requested_by::text, state, options FROM doc_index_jobs "
                                "ORDER BY doc_id")
    return await cur.fetchall()


async def _state(db_conn, doc_id):
    cur = await db_conn.execute("SELECT state, conversion_state FROM documents WHERE doc_id = %s", (doc_id,))
    return await cur.fetchone()


def _upload(client, data=TEXT, name="notes.txt"):
    return client.post("/v1/docs", files={"file": (name, data, "text/plain")})


async def test_an_upload_queues_and_runs_nothing_in_the_request(db_conn, harness):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    async with as_user(ann) as c:
        r = await _upload(c)
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "stored" and r.json()["queued"] is True
    assert await _jobs(db_conn) == [(_sha(TEXT), "pipeline", ann.user_id, "queued", None)]
    assert (await _state(db_conn, _sha(TEXT)))[0] == "stored"
    assert await drain() == [_sha(TEXT)]
    assert (await _state(db_conn, _sha(TEXT)))[0] == "indexed"


async def test_known_indexed_bytes_queue_nothing(db_conn, harness):
    _, as_user = harness
    ann, bob = await _member(db_conn, "ann"), await _member(db_conn, "bob")
    async with as_user(ann) as c:
        await _upload(c)
    await drain()
    async with as_user(bob) as c:
        r = await _upload(c)
    assert r.status_code == 200 and r.json()["queued"] is False
    assert await _jobs(db_conn) == []


async def test_retry_index_on_a_queued_document_is_one_job(db_conn, harness, store):
    _, as_user = harness
    ann, bob = await _member(db_conn, "ann"), await _member(db_conn, "bob")
    doc_id = _sha(TEXT)
    (store / f"{doc_id}.txt").write_bytes(TEXT)
    await seed.seed_doc(db_conn, doc_id, ann.user_id, file_type="text", state="failed",
                        bytes_path=store / f"{doc_id}.txt")
    await seed.share_doc(db_conn, doc_id, ann.user_id, bob.user_id)
    async with as_user(ann) as c:
        first = await c.post(f"/v1/docs/{doc_id}/index")
    async with as_user(bob) as c:
        second = await c.post(f"/v1/docs/{doc_id}/index")
    assert first.status_code == second.status_code == 202
    assert first.json() == {"ok": True, "doc_id": doc_id, "state": "stored", "queued": True}
    assert second.json()["queued"] is True
    assert [(j[0], j[2]) for j in await _jobs(db_conn)] == [(doc_id, ann.user_id)]


async def test_index_revives_a_document_stuck_mid_job_with_no_job(db_conn, harness, store):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    doc_id = _sha(TEXT)
    (store / f"{doc_id}.txt").write_bytes(TEXT)
    await seed.seed_doc(db_conn, doc_id, ann.user_id, file_type="text", state="extracting",
                        bytes_path=store / f"{doc_id}.txt")
    async with as_user(ann) as c:
        r = await c.post(f"/v1/docs/{doc_id}/index")
    assert r.json() == {"ok": True, "doc_id": doc_id, "state": "extracting", "queued": True}
    await drain()
    assert (await _state(db_conn, doc_id))[0] == "indexed"


async def test_reextract_queues(db_conn, harness, store):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    doc_id = _sha(TEXT)
    (store / f"{doc_id}.txt").write_bytes(TEXT)
    await seed.seed_doc(db_conn, doc_id, ann.user_id, file_type="text", state="indexed",
                        bytes_path=store / f"{doc_id}.txt", extracted_by="server")
    async with as_user(ann) as c:
        r = await c.delete(f"/v1/docs/{doc_id}/markdown")
    assert r.json() == {"ok": True, "doc_id": doc_id, "state": "stored", "queued": True}
    assert [(j[0], j[1], j[2]) for j in await _jobs(db_conn)] == [(doc_id, "pipeline", ann.user_id)]


async def test_reextract_is_refused_while_the_document_is_being_worked_on(db_conn, harness, store):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    doc_id = _sha(TEXT)
    await seed.seed_doc(db_conn, doc_id, ann.user_id, file_type="text", state="indexed", extracted_by="server")
    await index_queue.enqueue(db_conn, doc_id, job="convert", options={})
    await index_queue.claim_next(db_conn, "w", index_queue.CONVERT_JOBS)
    async with as_user(ann) as c:
        r = await c.delete(f"/v1/docs/{doc_id}/markdown")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "busy_document"


async def test_a_legacy_upload_queues_a_legacy_swap_for_nobody(db_conn, harness, store):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    doc_id = _sha(TEXT)
    await seed.seed_doc(db_conn, doc_id, ann.user_id, file_type="text", state="indexed", extracted_by="client")
    async with as_user(ann) as c:
        r = await _upload(c)
    assert r.status_code == 200
    assert [(j[1], j[2]) for j in await _jobs(db_conn)] == [("legacy_swap", None)]


async def test_a_full_pool_leaves_the_upload_stored_and_says_it_is_not_queued(db_conn, harness, monkeypatch):
    _, as_user = harness
    ann = await _member(db_conn, "ann")

    async def full(*_a, **_k):
        raise TimeoutError("pool full")
    monkeypatch.setattr(doc_pipeline, "enqueue_index", full)
    async with as_user(ann) as c:
        r = await _upload(c)
    assert r.status_code == 202, r.text                 # the file is safe; only its indexing waits
    assert r.json()["state"] == "stored" and r.json()["queued"] is False
    assert await _jobs(db_conn) == []


# ---------- Convert ----------

def _converted(pages=((1, "# Converted\n\nA converted page."),)):
    async def fake(_path, _options, on_progress=None):
        return docling_convert.Conversion(list(pages), [])
    return fake


@pytest.fixture
def docling_on(monkeypatch):
    monkeypatch.setattr(docling_convert, "is_enabled", lambda: True)
    monkeypatch.setattr(docling_convert, "convert_pdf_to_markdown_pages", _converted())


async def _pdf(db_conn, store, owner, *, state="indexed"):
    data = make_pdf(["hi"])
    doc_id = _sha(data)
    (store / f"{doc_id}.pdf").write_bytes(data)
    await seed.seed_doc(db_conn, doc_id, owner.user_id, file_type="pdf", state=state,
                        bytes_path=store / f"{doc_id}.pdf", extracted_by="server")
    return doc_id


async def test_convert_queues_with_its_options(db_conn, harness, store, docling_on):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    doc_id = await _pdf(db_conn, store, ann)
    async with as_user(ann) as c:
        r = await c.post(f"/v1/docs/{doc_id}/convert", json={"preset": "fast"})
    assert r.status_code == 202, r.text
    [(_, job, requested_by, state, options)] = await _jobs(db_conn)
    assert (job, requested_by, state, options["preset"]) == ("convert", ann.user_id, "queued", "fast")
    assert (await _state(db_conn, doc_id))[1] == "converting"
    assert await drain() == [doc_id]
    assert await _state(db_conn, doc_id) == ("indexed", "converted")


async def test_convert_replaces_a_queued_indexing_job(db_conn, harness, store, docling_on):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    doc_id = await _pdf(db_conn, store, ann, state="stored")
    await index_queue.enqueue(db_conn, doc_id, requested_by=ann.user_id)
    async with as_user(ann) as c:
        assert (await c.post(f"/v1/docs/{doc_id}/convert", json={})).status_code == 202
    assert [j[1] for j in await _jobs(db_conn)] == ["convert"]


async def test_convert_is_refused_while_the_document_is_being_indexed(db_conn, harness, store, docling_on):
    _, as_user = harness
    ann = await _member(db_conn, "ann")
    doc_id = await _pdf(db_conn, store, ann, state="stored")
    await index_queue.enqueue(db_conn, doc_id, requested_by=ann.user_id)
    await index_queue.claim_next(db_conn, "w", index_queue.INDEX_JOBS)
    async with as_user(ann) as c:
        r = await c.post(f"/v1/docs/{doc_id}/convert", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "busy_document", "message": BEING_INDEXED}
    assert (await _state(db_conn, doc_id))[1] is None
    assert [(j[1], j[3]) for j in await _jobs(db_conn)] == [("pipeline", "running")]


# ---------- a new version ----------

async def test_a_new_version_records_its_uploader(db_conn, monkeypatch, store):
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store, run_queue=False)
    alice = await member(db_conn, "v-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    v1 = b"Version one. It has two sentences."
    await seed.seed_doc(db_conn, _sha(v1), alice.user_id, file_name="notes.txt", file_type="text",
                        state="indexed", extracted_by="server")
    x = await seed.place_doc(db_conn, pid, _sha(v1), alice.user_id)
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/versions",
                         files={"file": ("notes.txt", TEXT, "text/plain")})
    assert r.status_code == 201, r.text
    assert r.json()["queued"] is True
    assert [(j[0], j[2]) for j in await _jobs(db_conn)] == [(_sha(TEXT), str(alice.user_id))]


# ---------- the guard ----------

def test_no_router_starts_indexing_or_conversion_in_a_background_task():
    jobs = re.compile(r"run_pipeline|run_legacy_swap|run_embed|run_convert_job|_run_convert_job")
    offending = [f"{path.name}:{n}" for path in sorted(ROUTERS.glob("*.py"))
                 for n, line in enumerate(path.read_text().splitlines(), 1)
                 if "add_task" in line and jobs.search(line)]
    assert offending == []
