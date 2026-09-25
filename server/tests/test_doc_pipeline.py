"""Background pipeline (A1 spec §4): extract from verified bytes → embed, the
one-time legacy swap, and POST /index's resume-vs-re-index rule."""
from __future__ import annotations

import pytest

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.services import doc_pipeline, extract
from server.services.embeddings import EMBEDDING_DIM
from server.tests import seed
from server.tests.docs_harness import build_docs_app
from server.tests.pdfgen import make_pdf

DOC = "e" * 64
TEXT = b"The first sentence is here. The second one follows! Is this the third one?"


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def harness(db_conn, monkeypatch, store):
    return build_docs_app(db_conn, monkeypatch, storage_dir=store)


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


def _write(store, doc_id, ext, data):
    path = store / f"{doc_id}.{ext}"
    path.write_bytes(data)
    return path


async def _doc(db_conn, doc_id=DOC):
    cur = await db_conn.execute(
        "SELECT state, extracted_by, page_count, error_message, conversion_state "
        "FROM documents WHERE doc_id = %s", (doc_id,))
    row = await cur.fetchone()
    return dict(zip(("state", "extracted_by", "page_count", "error_message",
                     "conversion_state"), row)) if row else None


async def _chunks(db_conn, doc_id=DOC):
    cur = await db_conn.execute(
        "SELECT ord, page, chunk_type, text, embedding IS NOT NULL FROM doc_chunks "
        "WHERE doc_id = %s ORDER BY ord", (doc_id,))
    return await cur.fetchall()


async def _entry_count(db_conn, doc_id=DOC):
    cur = await db_conn.execute("SELECT count(*) FROM library_entries WHERE doc_id = %s", (doc_id,))
    return (await cur.fetchone())[0]


# ---------- run_pipeline ----------

async def test_pipeline_extracts_stored_text_and_embeds(db_conn, harness, store):
    owner = await _member(db_conn, "p-owner")
    path = _write(store, DOC, "txt", TEXT)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="text", state="stored",
                        bytes_path=path)

    await doc_pipeline.run_pipeline(DOC)

    expected = extract.extract_file(path, "text")
    doc = await _doc(db_conn)
    assert doc["state"] == "indexed"
    assert doc["extracted_by"] == "server"
    assert doc["page_count"] == expected.page_count
    rows = await _chunks(db_conn)
    assert [r[:4] for r in rows] == [(c.ord, c.page, c.chunk_type, c.text) for c in expected.chunks]
    assert rows and all(r[4] for r in rows)


async def test_pipeline_corrupt_pdf_fails_readably_and_keeps_entry(db_conn, harness, store):
    # Review Focus 2: passes the %PDF- magic check but pdfium can't open it.
    owner = await _member(db_conn, "p-corrupt")
    path = _write(store, DOC, "pdf", b"%PDF-1.4 garbage")
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="pdf", state="stored",
                        bytes_path=path)

    await doc_pipeline.run_pipeline(DOC)  # must not raise

    doc = await _doc(db_conn)
    assert doc["state"] == "failed"
    assert doc["error_message"] and doc["error_message"].startswith("Could not extract text")
    assert await _chunks(db_conn) == []
    assert await _entry_count(db_conn) == 1


async def test_pipeline_without_bytes_asks_for_reupload(db_conn, harness):
    owner = await _member(db_conn, "p-nobytes")
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="text", state="stored",
                        bytes_path=None)

    await doc_pipeline.run_pipeline(DOC)

    doc = await _doc(db_conn)
    assert doc["state"] == "failed"
    assert "upload the file again" in doc["error_message"]


async def test_pipeline_on_converted_doc_seeds_from_pages(db_conn, harness, store):
    owner = await _member(db_conn, "p-conv")
    path = _write(store, DOC, "pdf", make_pdf(["Native page text"]))
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="pdf", state="stored",
                        bytes_path=path)
    await db_conn.execute(
        "UPDATE documents SET conversion_state = 'converted' WHERE doc_id = %s", (DOC,))
    for page, md in ((1, "# Page one markdown"), (2, "Page two markdown")):
        await db_conn.execute(
            "INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s,%s,%s)", (DOC, page, md))

    await doc_pipeline.run_pipeline(DOC)

    rows = await _chunks(db_conn)
    assert [(r[1], r[2], r[3]) for r in rows] == [
        (1, "page-md", "# Page one markdown"), (2, "page-md", "Page two markdown")]
    assert all(r[4] for r in rows)
    doc = await _doc(db_conn)
    assert doc["state"] == "indexed"
    assert doc["extracted_by"] == "client"  # converted pages aren't native extraction


# ---------- run_legacy_swap ----------

async def _seed_legacy(db_conn, store, sub):
    owner = await _member(db_conn, sub)
    path = _write(store, DOC, "txt", TEXT)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="text", state="indexed",
                        bytes_path=path, extracted_by="client")
    await db_conn.execute(
        "UPDATE documents SET conversion_state = 'converted' WHERE doc_id = %s", (DOC,))
    await db_conn.execute(
        "INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s, 1, 'old md')", (DOC,))
    vec = "[" + ",".join(["1"] + ["0"] * (EMBEDDING_DIM - 1)) + "]"
    await db_conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, "
        "embedding_model) VALUES (%s, 0, 1, 'page', 'OLD', %s, %s::vector, 'm')",
        (DOC, doc_pipeline.text_hash("OLD"), vec))
    return path


async def test_legacy_swap_replaces_client_chunks(db_conn, harness, store):
    path = await _seed_legacy(db_conn, store, "l-owner")

    await doc_pipeline.run_legacy_swap(DOC)

    rows = await _chunks(db_conn)
    expected = extract.extract_file(path, "text").chunks
    assert [r[3] for r in rows] == [c.text for c in expected]
    assert "OLD" not in [r[3] for r in rows]
    assert all(r[4] for r in rows)
    doc = await _doc(db_conn)
    assert doc["state"] == "indexed"
    assert doc["extracted_by"] == "server"
    assert doc["conversion_state"] is None
    cur = await db_conn.execute("SELECT count(*) FROM doc_pages WHERE doc_id = %s", (DOC,))
    assert (await cur.fetchone())[0] == 0


async def test_legacy_swap_keeps_old_index_when_embedding_fails(db_conn, monkeypatch, store):
    async def broken_embed(texts):
        raise RuntimeError("embedding service down")

    build_docs_app(db_conn, monkeypatch, storage_dir=store, embed=broken_embed)
    await _seed_legacy(db_conn, store, "l-broken")

    await doc_pipeline.run_legacy_swap(DOC)

    rows = await _chunks(db_conn)
    assert [(r[3], r[4]) for r in rows] == [("OLD", True)]
    doc = await _doc(db_conn)
    assert doc["extracted_by"] == "client"
    assert doc["state"] == "indexed"


# ---------- POST /v1/docs/{id}/index ----------

async def test_index_resumes_failed_doc_for_shared_holder(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "i-owner")
    recipient = await _member(db_conn, "i-recipient")
    path = _write(store, DOC, "txt", TEXT)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="text", state="failed",
                        bytes_path=path)
    await seed.share_doc(db_conn, DOC, owner.user_id, recipient.user_id)

    async with as_user(recipient) as client:
        r = await client.post(f"/v1/docs/{DOC}/index")
    assert r.status_code == 202
    assert (await _doc(db_conn))["state"] == "indexed"


async def test_index_reindex_by_shared_holder_is_content_shared(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "r-owner")
    recipient = await _member(db_conn, "r-recipient")
    path = _write(store, DOC, "txt", TEXT)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="text", state="indexed",
                        bytes_path=path)
    await seed.share_doc(db_conn, DOC, owner.user_id, recipient.user_id)

    async with as_user(recipient) as client:
        r = await client.post(f"/v1/docs/{DOC}/index")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "content_shared"
    assert r.json()["detail"]["reason"] == "other_holders"
    assert (await _doc(db_conn))["state"] == "indexed"


async def test_index_reindex_by_sole_holder(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "s-owner")
    path = _write(store, DOC, "txt", TEXT)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="text", state="indexed",
                        bytes_path=path)

    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{DOC}/index")
    assert r.status_code == 202
    doc = await _doc(db_conn)
    assert doc["state"] == "indexed"
    assert doc["extracted_by"] == "server"
    rows = await _chunks(db_conn)
    assert rows and all(r[4] for r in rows)


async def test_index_by_project_only_reader_is_404(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "pr-owner")
    member = await _member(db_conn, "pr-member")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner.user_id,))
    pid = (await cur.fetchone())[0]
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, member.user_id))
    path = _write(store, DOC, "txt", TEXT)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_type="text", state="failed",
                        bytes_path=path, project_ids=[pid])

    async with as_user(member) as client:
        assert (await client.get(f"/v1/docs/{DOC}")).status_code == 200  # readable...
        r = await client.post(f"/v1/docs/{DOC}/index")
    assert r.status_code == 404  # ...but no entry, so nothing to resume
    assert (await _doc(db_conn))["state"] == "failed"
