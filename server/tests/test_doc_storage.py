import hashlib
import io

import pytest
from fastapi import HTTPException, UploadFile

from server.services import doc_storage
from server.tests.pdfgen import make_pdf


@pytest.fixture(autouse=True)
def _dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("PDF_STORAGE_DIR", raising=False)
    return tmp_path / "store"


def _upload(data: bytes, name="x.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def test_storage_dir_fallbacks(monkeypatch, tmp_path):
    monkeypatch.delenv("DOC_STORAGE_DIR")
    monkeypatch.setenv("PDF_STORAGE_DIR", str(tmp_path / "legacy"))
    assert doc_storage.storage_dir() == (tmp_path / "legacy").resolve()
    monkeypatch.delenv("PDF_STORAGE_DIR")
    assert doc_storage.storage_dir().name == "pdfs"


@pytest.mark.parametrize("name,expected", [
    ("a.md", "markdown"), ("A.MARKDOWN", "markdown"), ("notes.TXT", "text"),
    ("REPORT.PDF", "pdf"), ("no_extension", "pdf"), ("../../x.pdf", "pdf"),
])
def test_type_from_name(name, expected):
    assert doc_storage.type_from_name(name) == expected


async def test_stage_hashes_and_places(_dir):
    data = make_pdf(["hello"])
    staged = await doc_storage.stage_upload(_upload(data, "../../evil.pdf"), "../../evil.pdf")
    assert staged.sha256 == hashlib.sha256(data).hexdigest() and staged.size == len(data)
    final = doc_storage.place(staged, staged.sha256)
    assert final == _dir.resolve() / f"{staged.sha256}.pdf" and final.read_bytes() == data
    doc_storage.discard(staged)  # idempotent after place


async def test_pdf_without_magic_is_415():
    with pytest.raises(HTTPException) as e:
        await doc_storage.stage_upload(_upload(b"not a pdf"), "x.pdf")
    assert e.value.status_code == 415 and e.value.detail["error"] == "unsupported_type"


async def test_text_must_be_utf8_without_nul():
    for bad in (b"\xff\xfe bad", b"ok\x00text"):
        with pytest.raises(HTTPException) as e:
            await doc_storage.stage_upload(_upload(bad, "a.txt"), "a.txt")
        assert e.value.status_code == 415


async def test_empty_file_is_422_and_leaves_no_staging(_dir):
    with pytest.raises(HTTPException) as e:
        await doc_storage.stage_upload(_upload(b"", "a.txt"), "a.txt")
    assert e.value.status_code == 422 and e.value.detail["error"] == "empty_file"
    assert not any((_dir / ".staging").glob("*"))


async def test_size_cap_413(monkeypatch):
    monkeypatch.setenv("TEXT_UPLOAD_MAX_MB", "1")
    with pytest.raises(HTTPException) as e:
        await doc_storage.stage_upload(_upload(b"a" * (1024 * 1024 + 1), "a.txt"), "a.txt")
    assert e.value.status_code == 413 and e.value.detail["limit_mb"] == 1


def test_sha256_file_missing_is_none(tmp_path):
    assert doc_storage.sha256_file(tmp_path / "nope") is None
