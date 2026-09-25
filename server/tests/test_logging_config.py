import logging
from logging.handlers import RotatingFileHandler

import pytest

from server import logging_config


@pytest.fixture
def isolate_logging():
    """Snapshot and restore logging state so this test's handlers (which point at
    a tmp dir that gets deleted) don't leak into other tests."""
    names = ["", "server", "uvicorn", "uvicorn.error", "uvicorn.access"]
    saved = {
        n: (logging.getLogger(n).handlers[:], logging.getLogger(n).level,
            logging.getLogger(n).propagate)
        for n in names
    }
    yield
    for n, (handlers, level, prop) in saved.items():
        lg = logging.getLogger(n)
        for h in lg.handlers[:]:
            if h not in handlers:
                h.close()
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = prop


def test_configure_logging_writes_to_rotating_file(tmp_path, monkeypatch, isolate_logging):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    logfile = logging_config.configure_logging()

    assert logfile.name == "server.log"
    assert logfile.parent == tmp_path.resolve()

    # A server.* logger's INFO message must land in the file (proves both the
    # file handler AND that server.* INFO is no longer swallowed).
    logging.getLogger("server.somewhere").info("hello-file-log")
    for h in logging.getLogger().handlers:
        h.flush()

    content = (tmp_path / "server.log").read_text()
    assert "hello-file-log" in content

    # Root carries a size-based rotating file handler.
    assert any(isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers)


def test_audit_logger_writes_its_own_file(tmp_path, monkeypatch, isolate_logging):
    from server.audit import audit

    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.delenv("LOG_AUDIT_FILE", raising=False)
    logging_config.configure_logging()
    audit("entry.added", user="u1", doc="d1", via="upload")
    for h in logging.getLogger("server.audit").handlers:
        h.flush()
    line = (tmp_path / "audit.log").read_text()
    assert "entry.added user=u1 doc=d1 via=upload" in line


def test_audit_file_override(tmp_path, monkeypatch, isolate_logging):
    from server.audit import audit

    target = tmp_path / "elsewhere" / "trail.log"
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_AUDIT_FILE", str(target))
    logging_config.configure_logging()
    audit("content.gc", doc="d1", trigger="entry_removed")
    for h in logging.getLogger("server.audit").handlers:
        h.flush()
    assert "content.gc doc=d1 trigger=entry_removed" in target.read_text()
