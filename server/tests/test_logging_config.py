import logging
from logging.handlers import TimedRotatingFileHandler

import pytest

from server import logging_config


@pytest.fixture
def isolate_logging():
    """Snapshot and restore logging state so this test's handlers (which point at
    a tmp dir that gets deleted) don't leak into other tests."""
    names = ["", "server", "server.audit", "uvicorn", "uvicorn.error", "uvicorn.access"]
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

    # Root carries a time-rotating file handler: a new file at midnight by default.
    timed = [h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)]
    assert timed and timed[0].when == "MIDNIGHT"


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


def test_audit_logger_handlers_isolated(isolate_logging):
    """Verify that server.audit handlers are properly restored after tests.
    Proves the isolate_logging fixture includes server.audit in its snapshot."""
    # Before configure_logging, server.audit should have no handlers
    assert logging.getLogger("server.audit").handlers == []


def test_the_rotation_time_and_backups_are_configurable(tmp_path, monkeypatch, isolate_logging):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE_ROLL_OVER_TIME", "h")
    monkeypatch.setenv("LOG_FILE_BACKUPS", "3")
    logging_config.configure_logging()
    handlers = logging.getLogger("server.audit").handlers
    timed = [h for h in handlers if isinstance(h, TimedRotatingFileHandler)]
    assert {h.when for h in timed} == {"H"} and {h.backupCount for h in timed} == {3}
    assert {h.baseFilename.rsplit("/", 1)[-1] for h in timed} == {"server.log", "audit.log"}


def test_an_unknown_rotation_time_falls_back_to_midnight_and_says_so(tmp_path, monkeypatch, isolate_logging, capsys):
    """A typo in .env must not stop the server starting (it did, 2026-10-05)."""
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE_ROLL_OVER_TIME", "fortnightly")
    logging_config.configure_logging()
    timed = [h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)]
    assert timed[0].when == "MIDNIGHT"
    assert "LOG_FILE_ROLL_OVER_TIME" in capsys.readouterr().err


def _access_record(args):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             '%s - "%s %s HTTP/%s" %d', args, None)


def test_access_log_drops_query_string(tmp_path, monkeypatch, isolate_logging):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    logging_config.configure_logging()
    flt = logging.getLogger("uvicorn.access").filters[0]
    rec = _access_record(("127.0.0.1:5000", "GET",
                          "/v1/users/lookup?q=secret.person%40example.com", "1.1", 200))
    assert flt.filter(rec) is True
    msg = rec.getMessage()
    assert "/v1/users/lookup" in msg and "GET" in msg and "200" in msg
    assert "secret" not in msg and "?" not in msg


def test_access_log_filter_leaves_other_shapes_alone():
    flt = logging_config.DropQueryString()
    for args in (None, ("only", "two"), ("a", "b", 3, "d", 5, 6)):
        rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "plain", args, None)
        assert flt.filter(rec) is True
        assert rec.args == args


def test_httpx_and_httpcore_are_quiet_at_info(tmp_path, monkeypatch, isolate_logging):
    """Request URLs carry emails and search text; INFO lines from httpx must not be emitted."""
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    names = ("httpx", "httpcore")
    saved = {n: (logging.getLogger(n).level, logging.getLogger(n).propagate, logging.getLogger(n).handlers[:])
             for n in names}
    try:
        logging_config.configure_logging()
        for n in names:
            lg = logging.getLogger(n)
            assert lg.isEnabledFor(logging.INFO) is False
            assert lg.isEnabledFor(logging.WARNING) is True
        logging.getLogger("httpx").info("HTTP Request: GET http://kc/users?email=alice%40corp.example.com")
        for h in logging.getLogger().handlers:
            h.flush()
        assert "alice" not in (tmp_path / "server.log").read_text()
    finally:
        for n, (lvl, prop, hs) in saved.items():
            lg = logging.getLogger(n)
            lg.setLevel(lvl)
            lg.propagate = prop
            lg.handlers[:] = hs
