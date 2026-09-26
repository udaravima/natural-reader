"""POST /{id}/convert and DELETE /{id}/markdown (A1 spec §5): content-changing
ops change the document for everyone who uses it, so only the sole holder
(exactly one entry, theirs, and no project placements) or an admin may run
them — everyone else gets 409 content_shared with a reason (other_holders |
in_project). A caller who can't even read the doc gets the usual 404."""
from __future__ import annotations

import hashlib

import pytest

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router
from server.services import doc_content, docling_convert
from server.tests import seed
from server.tests.docs_harness import build_docs_app
from server.tests.pdfgen import make_pdf

HEX = "a" * 64


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


async def _fake_convert(*_args, **_kwargs):
    return [(1, "# P1")]


@pytest.fixture
def harness(db_conn, monkeypatch, store):
    monkeypatch.setattr(docling_convert, "is_enabled", lambda: True)
    monkeypatch.setattr(docling_convert, "convert_pdf_to_markdown_pages", _fake_convert)
    return build_docs_app(db_conn, monkeypatch, storage_dir=store)


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


async def _admin(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="admin",
                          capabilities=frozenset({"reader"}))


def _write(store, doc_id, ext, data):
    path = store / f"{doc_id}.{ext}"
    path.write_bytes(data)
    return path


async def _doc(db_conn, doc_id):
    cur = await db_conn.execute(
        "SELECT state, conversion_state, extracted_by FROM documents WHERE doc_id = %s",
        (doc_id,))
    row = await cur.fetchone()
    return dict(zip(("state", "conversion_state", "extracted_by"), row)) if row else None


async def _chunk_types(db_conn, doc_id):
    cur = await db_conn.execute(
        "SELECT chunk_type FROM doc_chunks WHERE doc_id = %s ORDER BY ord", (doc_id,))
    return [r[0] for r in await cur.fetchall()]


async def _page_count(db_conn, doc_id):
    cur = await db_conn.execute("SELECT count(*) FROM doc_pages WHERE doc_id = %s", (doc_id,))
    return (await cur.fetchone())[0]


# ---------- POST /{id}/convert ----------

async def test_sole_holder_converts(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "c-owner")
    path = _write(store, HEX, "pdf", make_pdf(["hi"]))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path)

    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})

    assert r.status_code == 202
    assert (await _doc(db_conn, HEX))["conversion_state"] == "converted"


async def test_converted_never_shows_next_to_the_old_indexed_state(
        db_conn, harness, store, monkeypatch):
    """A poller must not see conversion done while `state` still says the OLD
    'indexed' — it would report success before the converted chunks embed."""
    _, as_user = harness
    owner = await _member(db_conn, "c-m2")
    path = _write(store, HEX, "pdf", make_pdf(["hi"]))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path,
                        state="indexed")
    seen = []
    real_seed = docs_router._chunks_from_pages

    async def spy(doc_id):
        seen.append(await _doc(db_conn, doc_id))
        return await real_seed(doc_id)
    monkeypatch.setattr(docs_router, "_chunks_from_pages", spy)

    async with as_user(owner) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202
    assert [(d["state"], d["conversion_state"]) for d in seen] == [("indexing", "converted")]
    assert (await _doc(db_conn, HEX))["state"] == "indexed"


async def test_convert_failing_after_converted_does_not_strand_indexing(
        db_conn, harness, store, monkeypatch):
    _, as_user = harness
    owner = await _member(db_conn, "c-m2f")
    path = _write(store, HEX, "pdf", make_pdf(["hi"]))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path,
                        state="indexed")

    async def boom(_doc_id):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(docs_router, "_chunks_from_pages", boom)

    async with as_user(owner) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202
    doc = await _doc(db_conn, HEX)
    assert (doc["state"], doc["conversion_state"]) == ("failed", "conversion_failed")


async def test_second_holder_refuses_both(db_conn, harness, store):
    a, b = await _member(db_conn, "a-conv"), await _member(db_conn, "b-conv")
    _, as_user = harness
    path = _write(store, HEX, "pdf", make_pdf(["hi"]))
    await seed.seed_doc(db_conn, HEX, a.user_id, file_type="pdf", bytes_path=path)
    await doc_content.add_entry(db_conn, b.user_id, HEX, via="upload", verified=True)

    for who in (a, b):
        async with as_user(who) as client:
            r = await client.post(f"/v1/docs/{HEX}/convert", json={})
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "content_shared"
        assert r.json()["detail"]["reason"] == "other_holders"


async def test_admin_without_entry_via_project_converts(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "pc-owner")
    admin = await _admin(db_conn, "pc-admin")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (admin.user_id,))
    pid = (await cur.fetchone())[0]
    path = _write(store, HEX, "pdf", make_pdf(["hi"]))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path,
                        project_ids=[pid])

    async with as_user(admin) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})

    assert r.status_code == 202
    assert (await _doc(db_conn, HEX))["conversion_state"] == "converted"


async def test_sole_holder_with_own_placement_is_in_project(db_conn, harness, store):
    # A placement counts as another user of the content even when the sole
    # holder filed it into their own project (spec §5).
    _, as_user = harness
    owner = await _member(db_conn, "ip-owner")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner.user_id,))
    pid = (await cur.fetchone())[0]
    path = _write(store, HEX, "pdf", make_pdf(["hi"]))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path,
                        project_ids=[pid])

    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "content_shared"
    assert r.json()["detail"]["reason"] == "in_project"


async def test_convert_missing_bytes_is_structured_409(db_conn, harness):
    # Below the governance gate: a sole holder with no stored bytes gets the
    # new structured refusal, not the removed /pdf route's stale message.
    _, as_user = harness
    owner = await _member(db_conn, "nb-owner")
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=None)

    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})

    assert r.status_code == 409
    assert r.json()["detail"] == {
        "error": "bytes_missing", "message": "Upload the file again first."}


# ---------- DELETE /{id}/markdown ----------

async def test_sole_holder_deletes_markdown_and_native_reextracts(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "dm-owner")
    pdf_bytes = make_pdf(["Native page text"])
    doc_id = _sha256(pdf_bytes)  # must match: this falls back to real native extraction
    path = _write(store, doc_id, "pdf", pdf_bytes)
    await seed.seed_doc(db_conn, doc_id, owner.user_id, file_type="pdf", state="indexed",
                        bytes_path=path, extracted_by="client")
    await db_conn.execute(
        "UPDATE documents SET conversion_state = 'converted' WHERE doc_id = %s", (doc_id,))
    await db_conn.execute(
        "INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s, 1, '# old md')", (doc_id,))

    async with as_user(owner) as client:
        r = await client.delete(f"/v1/docs/{doc_id}/markdown")

    assert r.status_code == 200
    assert r.json() == {"ok": True, "doc_id": doc_id, "state": "extracting"}
    assert await _page_count(db_conn, doc_id) == 0
    doc = await _doc(db_conn, doc_id)
    assert doc["conversion_state"] is None
    assert doc["state"] == "indexed"  # background pipeline finished by response time
    assert doc["extracted_by"] == "server"
    chunk_types = await _chunk_types(db_conn, doc_id)
    assert chunk_types and all(t in ("page", "block") for t in chunk_types)


async def test_delete_markdown_with_other_holder_is_409(db_conn, harness, store):
    _, as_user = harness
    a, b = await _member(db_conn, "dma"), await _member(db_conn, "dmb")
    pdf_bytes = make_pdf(["text"])
    doc_id = _sha256(pdf_bytes)
    path = _write(store, doc_id, "pdf", pdf_bytes)
    await seed.seed_doc(db_conn, doc_id, a.user_id, file_type="pdf", state="indexed",
                        bytes_path=path)
    await doc_content.add_entry(db_conn, b.user_id, doc_id, via="upload", verified=True)
    await db_conn.execute(
        "UPDATE documents SET conversion_state = 'converted' WHERE doc_id = %s", (doc_id,))
    await db_conn.execute(
        "INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s, 1, '# md')", (doc_id,))

    async with as_user(a) as client:
        r = await client.delete(f"/v1/docs/{doc_id}/markdown")

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "content_shared"
    assert r.json()["detail"]["reason"] == "other_holders"
    assert await _page_count(db_conn, doc_id) == 1  # untouched


async def test_non_reader_gets_404_on_both_routes(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "nr-owner")
    stranger = await _member(db_conn, "nr-stranger")
    path = _write(store, HEX, "pdf", make_pdf(["hi"]))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path)

    async with as_user(stranger) as client:
        r1 = await client.post(f"/v1/docs/{HEX}/convert", json={})
        r2 = await client.delete(f"/v1/docs/{HEX}/markdown")

    assert r1.status_code == 404
    assert r2.status_code == 404
