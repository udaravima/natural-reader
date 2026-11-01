"""A converted document's pictures: GET /v1/docs/{id}/figures/{n}.png answers
exactly who may read the document, and the files live and die with the conversion."""
from __future__ import annotations

import pytest

from server.services import doc_content, doc_storage, docling_convert
from server.tests import seed
from server.tests.docs_harness import build_docs_app
from server.tests.pdfgen import make_pdf
from server.tests.test_content_ops import _member, _write

HEX = "b" * 64
PNG = b"\x89PNG\r\n\x1a\n-one"


def _converted(pages=None, pictures=None):
    async def convert(*_args, **_kwargs):
        return docling_convert.Conversion(
            pages=pages if pages is not None else [(1, "# P1\n\nBefore\n\n![Image](figures/1.png)\n\nAfter")],
            pictures=[PNG] if pictures is None else pictures)
    return convert


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def harness(db_conn, monkeypatch, store):
    monkeypatch.setattr(docling_convert, "is_enabled", lambda: True)
    monkeypatch.setattr(docling_convert, "convert_pdf_to_markdown_pages", _converted())
    return build_docs_app(db_conn, monkeypatch, storage_dir=store)


async def _convert(db_conn, store, as_user, owner, sub_doc=HEX):
    path = _write(store, sub_doc, "pdf", make_pdf(["one"]))
    await seed.seed_doc(db_conn, sub_doc, owner.user_id, file_type="pdf", bytes_path=path)
    async with as_user(owner) as client:
        res = await client.post(f"/v1/docs/{sub_doc}/convert", json={"images": "embed"})
    assert res.status_code == 202


async def test_a_reader_gets_the_picture_and_nobody_caches_it(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "fig-owner")
    await _convert(db_conn, store, as_user, owner)
    async with as_user(owner) as client:
        res = await client.get(f"/v1/docs/{HEX}/figures/1.png")
    assert res.status_code == 200 and res.content == PNG
    assert res.headers["content-type"] == "image/png"
    assert "no-store" in res.headers["cache-control"]


async def test_someone_who_cant_read_the_document_gets_404(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "fig-owner2")
    stranger = await _member(db_conn, "fig-stranger")
    await _convert(db_conn, store, as_user, owner)
    async with as_user(stranger) as client:
        assert (await client.get(f"/v1/docs/{HEX}/figures/1.png")).status_code == 404


@pytest.mark.parametrize("name, status", [("2.png", 404), ("0.png", 422), ("x.png", 422)])
async def test_a_picture_that_isnt_there(db_conn, harness, store, name, status):
    _, as_user = harness
    owner = await _member(db_conn, "fig-owner3")
    await _convert(db_conn, store, as_user, owner)
    async with as_user(owner) as client:
        assert (await client.get(f"/v1/docs/{HEX}/figures/{name}")).status_code == status


async def test_the_reference_stays_in_the_markdown_and_out_of_the_chunks(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "fig-owner4")
    await _convert(db_conn, store, as_user, owner)
    async with as_user(owner) as client:
        assert "![Image](figures/1.png)" in (await client.get(f"/v1/docs/{HEX}/markdown")).text
    cur = await db_conn.execute("SELECT text FROM doc_chunks WHERE doc_id = %s", (HEX,))
    texts = [r[0] for r in await cur.fetchall()]
    assert texts and not any("figures/" in t for t in texts)
    assert any("Before" in t and "After" in t for t in texts)


async def test_a_new_conversion_replaces_the_pictures(db_conn, harness, store, monkeypatch):
    _, as_user = harness
    owner = await _member(db_conn, "fig-owner5")
    await _convert(db_conn, store, as_user, owner)
    assert doc_storage.figure_path(HEX, 1).exists()
    monkeypatch.setattr(docling_convert, "convert_pdf_to_markdown_pages",
                        _converted(pages=[(1, "# Again")], pictures=[]))
    async with as_user(owner) as client:
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={})).status_code == 202
        assert (await client.get(f"/v1/docs/{HEX}/figures/1.png")).status_code == 404
    assert not doc_storage.figures_dir(HEX).exists()


async def test_deleting_the_markdown_deletes_the_pictures(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "fig-owner6")
    await _convert(db_conn, store, as_user, owner)
    async with as_user(owner) as client:
        assert (await client.delete(f"/v1/docs/{HEX}/markdown")).status_code in (200, 202, 204)
    assert not doc_storage.figures_dir(HEX).exists()


async def test_a_document_nobody_holds_takes_its_pictures_with_it(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "fig-owner7")
    await _convert(db_conn, store, as_user, owner)
    assert doc_storage.figures_dir(HEX).exists()
    await db_conn.execute("DELETE FROM library_entries WHERE doc_id = %s", (HEX,))
    assert await doc_content.gc_content_if_orphaned(db_conn, HEX, trigger="test")
    assert not doc_storage.figures_dir(HEX).exists()


def test_replacing_with_nothing_leaves_no_folder(store, monkeypatch):
    monkeypatch.setenv("DOC_STORAGE_DIR", str(store))
    doc_storage.replace_figures(HEX, [b"a", b"b"])
    assert doc_storage.figure_path(HEX, 2).read_bytes() == b"b"
    doc_storage.replace_figures(HEX, [])
    assert not doc_storage.figures_dir(HEX).exists()
    doc_storage.remove_figures(HEX)   # nothing there: no error
