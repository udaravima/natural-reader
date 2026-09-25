"""POST /v1/docs — multipart upload with proof of possession (A1 spec §3, §4).

The server hashes the uploaded bytes; that hash IS the doc id. Existing content
is a 200 dedupe with no rework; new content is a 202 and a background
extract + embed job. Doc ids in these tests are therefore real sha256 values of
the bytes being uploaded — a seeded row only matches an upload when its id is
the hash of those bytes.
"""
from __future__ import annotations

import hashlib
import logging

import pytest

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router
from server.services import doc_pipeline
from server.tests import seed
from server.tests.docs_harness import build_docs_app, fake_embed
from server.tests.pdfgen import make_pdf

TEXT = b"The first sentence is here. The second one follows! Is this the third one?"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


class _CountingEmbed:
    def __init__(self):
        self.calls = 0

    async def __call__(self, texts):
        self.calls += 1
        return await fake_embed(texts)


@pytest.fixture
def embed():
    return _CountingEmbed()


@pytest.fixture
def as_user(db_conn, monkeypatch, store, embed):
    _app, as_user = build_docs_app(db_conn, monkeypatch, storage_dir=store, embed=embed)
    return as_user


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


async def _upload(client, name, data, **form):
    return await client.post("/v1/docs", files={"file": (name, data)}, data=form)


async def _doc(db_conn, doc_id):
    cur = await db_conn.execute(
        "SELECT state, extracted_by, bytes_path, conversion_state FROM documents "
        "WHERE doc_id = %s", (doc_id,))
    row = await cur.fetchone()
    return dict(zip(("state", "extracted_by", "bytes_path", "conversion_state"), row)) if row else None


async def _entries(db_conn, doc_id):
    cur = await db_conn.execute(
        "SELECT user_id::text, added_via, tags, file_name FROM library_entries "
        "WHERE doc_id = %s ORDER BY added_at", (doc_id,))
    return await cur.fetchall()


async def _chunks(db_conn, doc_id):
    cur = await db_conn.execute(
        "SELECT ord, page, chunk_type, text FROM doc_chunks WHERE doc_id = %s ORDER BY ord",
        (doc_id,))
    return await cur.fetchall()


async def _count(db_conn, table):
    cur = await db_conn.execute(f"SELECT count(*) FROM {table}")
    return (await cur.fetchone())[0]


async def _add_old_chunk(db_conn, doc_id, text="OLD", chunk_type="page"):
    vec = "[" + ",".join(["1"] + ["0"] * (doc_pipeline.EMBEDDING_DIM - 1)) + "]"
    await db_conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, "
        "embedding_model) VALUES (%s, 0, 1, %s, %s, %s, %s::vector, 'm')",
        (doc_id, chunk_type, text, doc_pipeline.text_hash(text), vec))


# ---------- new content and dedupe ----------

async def test_new_text_file_is_202_and_indexed_by_server(db_conn, as_user, store):
    alice = await _member(db_conn, "u-alice")
    doc_id = _sha(TEXT)
    async with as_user(alice) as c:
        r = await _upload(c, "notes.txt", TEXT)
        assert r.status_code == 202
        body = r.json()
        assert body["doc_id"] == doc_id and body["dedup"] is False
        assert (await c.get(f"/v1/docs/{doc_id}")).json()["state"] == "indexed"
    assert (store / f"{doc_id}.txt").read_bytes() == TEXT
    doc = await _doc(db_conn, doc_id)
    assert doc["extracted_by"] == "server"
    assert doc["bytes_path"] == str((store / f"{doc_id}.txt").resolve())
    assert list((store / ".staging").iterdir()) == []


async def test_same_bytes_from_second_user_is_200_dedupe_without_rework(db_conn, as_user, embed):
    alice = await _member(db_conn, "d-alice")
    bob = await _member(db_conn, "d-bob")
    doc_id = _sha(TEXT)
    async with as_user(alice) as c:
        assert (await _upload(c, "a.txt", TEXT)).status_code == 202
    calls, chunks = embed.calls, await _chunks(db_conn, doc_id)
    assert calls >= 1 and chunks

    async with as_user(bob) as c:
        r = await _upload(c, "mine.txt", TEXT)
    assert r.status_code == 200
    assert r.json() == {"doc_id": doc_id, "dedup": True, "state": "indexed"}
    assert embed.calls == calls  # nothing re-embedded
    assert await _chunks(db_conn, doc_id) == chunks
    entries = await _entries(db_conn, doc_id)
    assert [(u, v) for u, v, _t, _n in entries] == [
        (alice.user_id, "upload"), (bob.user_id, "upload")]
    # Bob's entry carries his own name for the file.
    assert entries[1][3] == "mine.txt"


async def test_server_hash_wins_over_client_hint(db_conn, as_user, caplog):
    alice = await _member(db_conn, "h-alice")
    with caplog.at_level(logging.WARNING, logger=docs_router.logger.name):
        async with as_user(alice) as c:
            r = await _upload(c, "a.txt", TEXT, client_doc_id="0" * 64)
    assert r.status_code == 202
    assert r.json()["doc_id"] == _sha(TEXT)
    assert await _doc(db_conn, "0" * 64) is None
    assert any("hint mismatch" in rec.getMessage() and rec.levelno == logging.WARNING
               for rec in caplog.records)


async def test_free_text_hint_is_never_logged(db_conn, as_user, caplog):
    # WARNING lines carry ids only; a hint that isn't id-shaped (here, a file
    # name) is replaced, not echoed.
    alice = await _member(db_conn, "h2-alice")
    with caplog.at_level(logging.WARNING, logger=docs_router.logger.name):
        async with as_user(alice) as c:
            r = await _upload(c, "a.txt", TEXT, client_doc_id="Secret Report.pdf")
    assert r.status_code == 202
    msgs = [rec.getMessage() for rec in caplog.records if "hint mismatch" in rec.getMessage()]
    assert msgs and all("Secret" not in m and "(malformed)" in m for m in msgs)


async def test_same_user_uploading_twice_keeps_one_entry_and_replaces_tags(db_conn, as_user):
    # Review Focus 5
    alice = await _member(db_conn, "t-alice")
    doc_id = _sha(TEXT)
    async with as_user(alice) as c:
        assert (await _upload(c, "a.txt", TEXT, tags=["a"])).status_code == 202
        r = await _upload(c, "a.txt", TEXT, tags=["b"])
    assert r.status_code == 200 and r.json()["dedup"] is True
    entries = await _entries(db_conn, doc_id)
    assert len(entries) == 1
    assert entries[0][2] == ["b"]


async def test_shared_entry_holder_upload_upgrades_to_upload(db_conn, as_user):
    # Review Focus 5
    alice = await _member(db_conn, "s-alice")
    bob = await _member(db_conn, "s-bob")
    doc_id = _sha(TEXT)
    async with as_user(alice) as c:
        assert (await _upload(c, "a.txt", TEXT)).status_code == 202
    await seed.share_doc(db_conn, doc_id, alice.user_id, bob.user_id)
    async with as_user(bob) as c:
        r = await _upload(c, "b.txt", TEXT)
        assert r.status_code == 200
        assert (await c.get(f"/v1/docs/{doc_id}")).json()["added_via"] == "upload"
    cur = await db_conn.execute(
        "SELECT added_via, shared_by FROM library_entries WHERE user_id = %s AND doc_id = %s",
        (bob.user_id, doc_id))
    assert await cur.fetchone() == ("upload", None)


# ---------- refusals ----------

async def test_empty_file_is_422_and_leaves_nothing(db_conn, as_user, store):
    # Review Focus 1
    alice = await _member(db_conn, "e-alice")
    docs_before, entries_before = await _count(db_conn, "documents"), await _count(db_conn, "library_entries")
    async with as_user(alice) as c:
        r = await _upload(c, "empty.txt", b"")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "empty_file"
    assert await _count(db_conn, "documents") == docs_before
    assert await _count(db_conn, "library_entries") == entries_before
    assert list((store / ".staging").iterdir()) == []


async def test_text_with_nul_is_415(db_conn, as_user):
    alice = await _member(db_conn, "n-alice")
    async with as_user(alice) as c:
        r = await _upload(c, "bin.txt", b"hello\x00world")
    assert r.status_code == 415
    assert r.json()["detail"]["error"] == "unsupported_type"
    assert await _doc(db_conn, _sha(b"hello\x00world")) is None


async def test_pdf_without_magic_is_415(db_conn, as_user):
    alice = await _member(db_conn, "m-alice")
    async with as_user(alice) as c:
        r = await _upload(c, "fake.pdf", b"not a pdf at all")
    assert r.status_code == 415
    assert r.json()["detail"]["error"] == "unsupported_type"


async def test_oversize_text_is_413_with_limit(db_conn, as_user, monkeypatch):
    monkeypatch.setenv("TEXT_UPLOAD_MAX_MB", "1")
    alice = await _member(db_conn, "o-alice")
    async with as_user(alice) as c:
        r = await _upload(c, "big.txt", b"a" * (1024 * 1024 + 1))
    assert r.status_code == 413
    assert r.json()["detail"]["error"] == "too_large"
    assert r.json()["detail"]["limit_mb"] == 1


# ---------- proof of possession ----------

async def test_an_id_is_not_access(db_conn, as_user):
    alice = await _member(db_conn, "p-alice")
    mallory = await _member(db_conn, "p-mallory")
    doc_id = _sha(TEXT)
    async with as_user(alice) as c:
        assert (await _upload(c, "a.txt", TEXT)).status_code == 202
    async with as_user(mallory) as c:
        assert (await c.get(f"/v1/docs/{doc_id}")).status_code == 404
        assert (await c.post(f"/v1/docs/{doc_id}/search", json={"query": "x"})).status_code == 404
        assert (await c.get(f"/v1/docs/{doc_id}/markdown")).status_code == 404
        r = await c.put(f"/v1/docs/{doc_id}/shares/{mallory.user_id}")
        assert r.status_code == 404
    assert [u for u, *_ in await _entries(db_conn, doc_id)] == [alice.user_id]


@pytest.mark.parametrize("method,suffix", [
    ("POST", "chunks"), ("POST", "pdf"), ("DELETE", "pdf")])
async def test_client_chunk_and_byte_routes_are_gone(db_conn, as_user, method, suffix):
    alice = await _member(db_conn, f"g-alice-{method}-{suffix}")
    doc_id = _sha(TEXT)
    async with as_user(alice) as c:
        assert (await _upload(c, "a.txt", TEXT)).status_code == 202
        if method == "POST" and suffix == "chunks":
            r = await c.post(f"/v1/docs/{doc_id}/chunks",
                             json={"chunks": [{"ord": 0, "text": "poison"}]})
        elif method == "POST":
            r = await c.post(f"/v1/docs/{doc_id}/pdf", files={"file": ("x.pdf", b"%PDF-1.4")})
        else:
            r = await c.delete(f"/v1/docs/{doc_id}/pdf")
    assert r.status_code in (404, 405)


# ---------- legacy content ----------

async def test_legacy_indexed_is_200_and_reextracted_once(db_conn, as_user, embed):
    alice = await _member(db_conn, "l-alice")
    doc_id = _sha(TEXT)
    await seed.seed_doc(db_conn, doc_id, alice.user_id, file_name="a.txt", file_type="text",
                        state="indexed", extracted_by="client")
    await _add_old_chunk(db_conn, doc_id)
    async with as_user(alice) as c:
        r = await _upload(c, "a.txt", TEXT)
    assert r.status_code == 200
    assert r.json() == {"doc_id": doc_id, "dedup": True, "state": "indexed"}
    doc = await _doc(db_conn, doc_id)
    assert doc["extracted_by"] == "server" and doc["state"] == "indexed"
    texts = [t for *_x, t in await _chunks(db_conn, doc_id)]
    assert texts and "OLD" not in texts
    assert embed.calls >= 1


async def test_legacy_not_indexed_takes_new_content_path(db_conn, as_user):
    alice = await _member(db_conn, "ln-alice")
    doc_id = _sha(TEXT)
    await seed.seed_doc(db_conn, doc_id, alice.user_id, file_name="a.txt", file_type="text",
                        state="extracted", extracted_by="client")
    await _add_old_chunk(db_conn, doc_id)
    async with as_user(alice) as c:
        r = await _upload(c, "a.txt", TEXT)
    assert r.status_code == 202
    assert r.json()["dedup"] is True
    doc = await _doc(db_conn, doc_id)
    assert doc["state"] == "indexed" and doc["extracted_by"] == "server"
    assert "OLD" not in [t for *_x, t in await _chunks(db_conn, doc_id)]


async def test_legacy_converted_with_verified_bytes_flips_without_rework(
        db_conn, as_user, store, embed):
    alice = await _member(db_conn, "lc-alice")
    data = make_pdf(["Hello converted world."])
    doc_id = _sha(data)
    path = store / f"{doc_id}.pdf"
    path.write_bytes(data)
    await seed.seed_doc(db_conn, doc_id, alice.user_id, file_name="c.pdf", file_type="pdf",
                        state="indexed", extracted_by="client", bytes_path=path)
    await db_conn.execute(
        "UPDATE documents SET conversion_state = 'converted' WHERE doc_id = %s", (doc_id,))
    await db_conn.execute(
        "INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s, 1, '# Converted')", (doc_id,))
    await _add_old_chunk(db_conn, doc_id, text="# Converted", chunk_type="page-md")
    chunks = await _chunks(db_conn, doc_id)
    async with as_user(alice) as c:
        r = await _upload(c, "c.pdf", data)
    assert r.status_code == 200
    assert r.json() == {"doc_id": doc_id, "dedup": True, "state": "indexed"}
    doc = await _doc(db_conn, doc_id)
    assert doc["extracted_by"] == "server"
    assert doc["conversion_state"] == "converted"
    assert await _chunks(db_conn, doc_id) == chunks
    assert embed.calls == 0


# ---------- races and self-heal ----------

async def test_missing_bytes_are_restored_by_the_next_upload(db_conn, as_user, store, caplog):
    alice = await _member(db_conn, "sh-alice")
    bob = await _member(db_conn, "sh-bob")
    doc_id = _sha(TEXT)
    gone = store / f"{doc_id}.txt"  # the path a failed GC unlinked
    await seed.seed_doc(db_conn, doc_id, alice.user_id, file_name="a.txt", file_type="text",
                        state="indexed", extracted_by="server", bytes_path=gone)
    await _add_old_chunk(db_conn, doc_id, text="SERVER")
    with caplog.at_level(logging.WARNING, logger=docs_router.logger.name):
        async with as_user(bob) as c:
            r = await _upload(c, "a.txt", TEXT)
    assert r.status_code == 200 and r.json()["dedup"] is True
    assert gone.read_bytes() == TEXT
    assert (await _doc(db_conn, doc_id))["bytes_path"] == str(gone.resolve())
    assert [t for *_x, t in await _chunks(db_conn, doc_id)] == ["SERVER"]  # no rework
    assert any("missing bytes" in rec.getMessage() and rec.levelno == logging.WARNING
               for rec in caplog.records)


async def test_lost_race_with_gc_retries_once_as_new_content(db_conn, as_user, monkeypatch, caplog):
    alice = await _member(db_conn, "r-alice")
    bob = await _member(db_conn, "r-bob")
    doc_id = _sha(TEXT)
    await seed.seed_doc(db_conn, doc_id, alice.user_id, file_name="a.txt", file_type="text",
                        state="indexed", extracted_by="server")
    real = docs_router._lock_existing
    calls = []

    async def gc_wins_once(conn, did):
        calls.append(did)
        if len(calls) == 1:
            # A GC committed between our conflicting INSERT and our lock.
            await conn.execute("DELETE FROM documents WHERE doc_id = %s", (did,))
            return None
        return await real(conn, did)

    monkeypatch.setattr(docs_router, "_lock_existing", gc_wins_once)
    with caplog.at_level(logging.WARNING, logger=docs_router.logger.name):
        async with as_user(bob) as c:
            r = await _upload(c, "a.txt", TEXT)
    assert r.status_code == 202
    assert r.json()["dedup"] is False
    assert calls == [doc_id]  # the retry's INSERT created the row; no second lock
    assert [(u, v) for u, v, *_ in await _entries(db_conn, doc_id)] == [(bob.user_id, "upload")]
    assert (await _doc(db_conn, doc_id))["state"] == "indexed"
    assert any("retry" in rec.getMessage() and rec.levelno == logging.WARNING
               for rec in caplog.records)


# ---------- file names ----------

async def test_traversal_file_name_is_display_only(db_conn, as_user, store):
    # Review Focus 4
    alice = await _member(db_conn, "f-alice")
    data = make_pdf(["A page of text."])
    doc_id = _sha(data)
    async with as_user(alice) as c:
        r = await _upload(c, "../../x.pdf", data)
        assert r.status_code == 202
        assert (await c.get(f"/v1/docs/{doc_id}")).json()["file_name"] == "../../x.pdf"
    assert (store / f"{doc_id}.pdf").read_bytes() == data
    assert sorted(p.name for p in store.iterdir()) == [".staging", f"{doc_id}.pdf"]
    assert not (store.parent.parent / "x.pdf").exists()
    assert (await _doc(db_conn, doc_id))["bytes_path"] == str((store / f"{doc_id}.pdf").resolve())
