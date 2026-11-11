"""PDFium isn't thread-safe: two threads inside it at once can crash the worker. Every call goes through one
lock, the same one Docling uses when it is installed."""
import sys
import threading
import time
import types

from server.services import extract


class _FakePdf:
    inside = 0
    most = 0
    guard = threading.Lock()

    def __init__(self, path):
        with _FakePdf.guard:
            _FakePdf.inside += 1
            _FakePdf.most = max(_FakePdf.most, _FakePdf.inside)
        time.sleep(0.05)

    def __len__(self):
        return 0

    def get_toc(self):
        return []

    def close(self):
        with _FakePdf.guard:
            _FakePdf.inside -= 1


def test_only_one_thread_is_inside_pdfium_at_a_time(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pypdfium2", types.SimpleNamespace(PdfDocument=_FakePdf))
    _FakePdf.inside = _FakePdf.most = 0
    calls = [extract._pdf_page_texts, extract.pdf_page_count, extract._pdf_toc_items] * 3
    threads = [threading.Thread(target=fn, args=(tmp_path / "a.pdf",)) for fn in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _FakePdf.most == 1


def test_it_is_docling_s_lock_when_docling_is_installed(monkeypatch):
    theirs = threading.Lock()
    monkeypatch.setitem(sys.modules, "docling", types.ModuleType("docling"))
    monkeypatch.setitem(sys.modules, "docling.utils", types.ModuleType("docling.utils"))
    monkeypatch.setitem(sys.modules, "docling.utils.locks", types.SimpleNamespace(pypdfium2_lock=theirs))
    assert extract.pdfium_lock() is theirs


def test_it_is_our_own_lock_without_docling(monkeypatch):
    monkeypatch.setitem(sys.modules, "docling.utils.locks", None)     # the import fails
    assert extract.pdfium_lock() is extract._PDFIUM_LOCK
