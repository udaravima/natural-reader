import logging

from server.services import docling_convert


def test_enabled_but_missing_logs_one_error(monkeypatch, caplog):
    monkeypatch.setattr(docling_convert, "DOCLING_ENABLED", True)
    monkeypatch.setattr(docling_convert.importlib.util, "find_spec", lambda name: None)
    with caplog.at_level(logging.ERROR, logger="server.services.docling_convert"):
        assert docling_convert.check_available() is False
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "./startup.sh rebuild" in errors[0].getMessage()


def test_disabled_is_silent(monkeypatch, caplog):
    monkeypatch.setattr(docling_convert, "DOCLING_ENABLED", False)
    monkeypatch.setattr(docling_convert.importlib.util, "find_spec", lambda name: None)
    with caplog.at_level(logging.DEBUG, logger="server.services.docling_convert"):
        assert docling_convert.check_available() is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_enabled_and_present_is_silent(monkeypatch, caplog):
    monkeypatch.setattr(docling_convert, "DOCLING_ENABLED", True)
    monkeypatch.setattr(docling_convert.importlib.util, "find_spec", lambda name: object())
    with caplog.at_level(logging.DEBUG, logger="server.services.docling_convert"):
        assert docling_convert.check_available() is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
