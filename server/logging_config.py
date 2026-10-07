"""
Centralized logging configuration for the backend.

Without this, the app's ``logging.getLogger(__name__)`` loggers have no handler
attached, so INFO-level messages vanish and only WARNING+ leak through Python's
last-resort stderr handler — and nothing is ever written to a file. This module
wires up a single dictConfig that:

  * emits to the console (as before, for interactive ``startup.sh up``) AND to a
    time-rotating file under ``LOG_DIR`` (default ``./logs/server.log``, a new
    file at midnight, the old ones kept as ``server.log.YYYY-MM-DD``);
  * routes the ``server.*`` and root loggers plus uvicorn's own loggers through
    the same handlers, at ``LOG_LEVEL`` (default INFO);
  * is idempotent — safe to call in ``run.py`` and again per uvicorn worker via
    ``create_app()``.

All knobs are env-driven so operators can tune verbosity and rotation without a
code change. Call :func:`configure_logging` once early in process startup.

Audit trail is written to ``LOG_AUDIT_FILE`` (default ``<LOG_DIR>/audit.log``).
"""
from __future__ import annotations

import logging.config
import os
import sys
from pathlib import Path


# What TimedRotatingFileHandler accepts for `when` (any case): seconds, minutes,
# hours, days, a weekday (W0 = Monday), or midnight.
ROLL_OVER_TIMES = ("S", "M", "H", "D", "MIDNIGHT", *(f"W{d}" for d in range(7)))


class DropQueryString(logging.Filter):
    """Strip the query string from uvicorn access-log lines: search and lookup
    text (``?q=``) is personal data and must never reach the logs. Method, path
    and status stay. Records of any other shape pass through untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].partition("?")[0], *args[3:])
        return True


def _rotating_file(path: Path, backups: int, roll_over_time: str) -> dict:
    return {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "formatter": "standard",
        "filename": str(path),
        "when": roll_over_time,
        "backupCount": backups,
        "encoding": "utf-8",
    }


def _dict_config(logfile: Path, level: str, backups: int, audit_file: Path, roll_over_time: str = "midnight") -> dict:
    return {
        "version": 1,
        # Leave third-party loggers (httpx, etc.) in place instead of nuking them.
        "disable_existing_loggers": False,
        "filters": {"drop_query": {"()": DropQueryString}},
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": "ext://sys.stderr",
            },
            "file": _rotating_file(logfile, backups, roll_over_time),
            "audit_file": _rotating_file(audit_file, backups, roll_over_time),
        },
        # Root catches everything that propagates (our server.* loggers, httpx…).
        "root": {"level": level, "handlers": ["console", "file"]},
        "loggers": {
            "server.audit": {"level": "INFO", "handlers": ["console", "file", "audit_file"], "propagate": False},
            "server": {"level": level, "handlers": ["console", "file"], "propagate": False},
            # uvicorn configures these itself; point them at our handlers and stop
            # propagation so lines aren't emitted twice.
            "uvicorn": {"level": level, "handlers": ["console", "file"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["console", "file"], "propagate": False},
            "uvicorn.access": {"level": level, "handlers": ["console", "file"], "propagate": False,
                               "filters": ["drop_query"]},
            # httpx logs full request URLs at INFO (emails, search text): personal data, keep out of logs.
            "httpx": {"level": "WARNING", "handlers": ["console", "file"], "propagate": False},
            "httpcore": {"level": "WARNING", "handlers": ["console", "file"], "propagate": False},
        },
    }


def configure_logging() -> Path:
    """Apply the logging configuration and return the resolved log file path.

    Reads ``LOG_LEVEL`` (default ``INFO``), ``LOG_DIR`` (default ``./logs``),
    ``LOG_FILE_ROLL_OVER_TIME`` (default ``midnight``: when a new file starts)
    and ``LOG_FILE_BACKUPS`` (default 5: old files kept, so 5 days at midnight).
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_dir = Path(os.environ.get("LOG_DIR", "./logs")).resolve()
    backups = int(os.environ.get("LOG_FILE_BACKUPS", "5"))
    roll_over_time = (os.environ.get("LOG_FILE_ROLL_OVER_TIME") or "midnight").strip()
    if roll_over_time.upper() not in ROLL_OVER_TIMES:
        # Logging isn't configured yet, so say it on stderr; a typo in .env
        # must not stop the server starting.
        print(f"LOG_FILE_ROLL_OVER_TIME={roll_over_time!r} is not one of {', '.join(ROLL_OVER_TIMES)}; "
              "rotating at midnight", file=sys.stderr)
        roll_over_time = "midnight"

    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "server.log"

    audit_file = Path(os.environ.get("LOG_AUDIT_FILE") or (log_dir / "audit.log")).resolve()
    audit_file.parent.mkdir(parents=True, exist_ok=True)

    logging.config.dictConfig(_dict_config(logfile, level, backups, audit_file, roll_over_time))
    return logfile
