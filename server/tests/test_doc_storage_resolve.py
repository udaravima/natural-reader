"""documents.bytes_path holds the absolute path at upload time. The bare-metal
backend and the container (/app/data/pdfs) see the same files under different
paths, so a stored path is followed when it exists and otherwise re-found by
its <doc_id>.<ext> name in the current storage folder."""
from server.services import doc_storage

DOC = "e" * 64


def test_an_existing_stored_path_is_used_as_is(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_STORAGE_DIR", str(tmp_path / "current"))
    f = tmp_path / "old" / f"{DOC}.pdf"
    f.parent.mkdir()
    f.write_bytes(b"%PDF-1.4")
    assert doc_storage.resolve_stored(str(f)) == f


def test_another_runtimes_path_is_found_in_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_STORAGE_DIR", str(tmp_path))
    assert doc_storage.resolve_stored(f"/app/data/pdfs/{DOC}.md") == tmp_path / f"{DOC}.md"
    assert doc_storage.resolve_stored(f"/home/u/nr/data/pdfs/{DOC}.txt") == tmp_path / f"{DOC}.txt"


def test_names_that_are_not_ours_are_never_redirected(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_STORAGE_DIR", str(tmp_path))
    for raw in ("/elsewhere/notes.pdf", f"/x/{DOC}.exe", "/x/../../etc/passwd", f"/x/{DOC[:-1]}.pdf"):
        assert doc_storage.resolve_stored(raw) == __import__("pathlib").Path(raw)


def test_empty_is_none():
    assert doc_storage.resolve_stored(None) is None
    assert doc_storage.resolve_stored("") is None
