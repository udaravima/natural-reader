"""
Centralized logging configuration for the backend.

Without this, the app's ``logging.getLogger(__name__)`` loggers have no handler
attached, so INFO-level messages vanish and only WARNING+ leak through Python's
last-resort stderr handler — and nothing is ever written to a file. This module
wires up a single dictConfig that:

  * emits to the console (as before, for interactive ``startup.sh up``) AND to a
    size-rotating file under ``LOG_DIR`` (default ``./logs/server.log``);
  * routes the ``server.*`` and root loggers plus uvicorn's own loggers through
    the same handlers, at ``LOG_LEVEL`` (default INFO);
  * is idempotent — safe to call in ``run.py`` and again per uvicorn worker via
    ``create_app()``.

All knobs are env-driven so operators can tune verbosity and rotation without a
code change. Call :func:`configure_logging` once early in process startup.
"""
from __future__ import annotations

import logging.config
import os
from pathlib import Path


def _dict_config(logfile: Path, level: str, max_bytes: int, backups: int) -> dict:
    return {
        "version": 1,
        # Leave third-party loggers (httpx, etc.) in place instead of nuking them.
        "disable_existing_loggers": False,
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
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "filename": str(logfile),
                "maxBytes": max_bytes,
                "backupCount": backups,
                "encoding": "utf-8",
            },
        },
        # Root catches everything that propagates (our server.* loggers, httpx…).
        "root": {"level": level, "handlers": ["console", "file"]},
        "loggers": {
            "server": {"level": level, "handlers": ["console", "file"], "propagate": False},
            # uvicorn configures these itself; point them at our handlers and stop
            # propagation so lines aren't emitted twice.
            "uvicorn": {"level": level, "handlers": ["console", "file"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["console", "file"], "propagate": False},
            "uvicorn.access": {"level": level, "handlers": ["console", "file"], "propagate": False},
        },
    }


def configure_logging() -> Path:
    """Apply the logging configuration and return the resolved log file path.

    Reads ``LOG_LEVEL`` (default ``INFO``), ``LOG_DIR`` (default ``./logs``),
    ``LOG_FILE_MAX_MB`` (default 10) and ``LOG_FILE_BACKUPS`` (default 5).
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_dir = Path(os.environ.get("LOG_DIR", "./logs")).resolve()
    max_bytes = int(float(os.environ.get("LOG_FILE_MAX_MB", "10")) * 1024 * 1024)
    backups = int(os.environ.get("LOG_FILE_BACKUPS", "5"))

    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "server.log"

    logging.config.dictConfig(_dict_config(logfile, level, max_bytes, backups))
    return logfile
