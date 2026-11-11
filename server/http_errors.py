"""One shape for every refusal (A1 spec §7): FastAPI returns
{"detail": {"error": <code>, "message": <human text>, ...extra}}. The SPA's
src/lib/apiErrors.js maps `error` (and `reason`) to the notice it shows."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg import errors as pg_errors

logger = logging.getLogger(__name__)

BUSY_MESSAGE = "Please try again."


def refusal(status: int, error: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error, "message": message, **extra})


async def _deadlock_busy(request: Request, _exc: pg_errors.DeadlockDetected) -> JSONResponse:
    """Postgres aborted this request's transaction to break a lock cycle
    (V1 spec §4.4's safety net). Everything it did rolled back, and a retry
    succeeds, so it is the same 503 busy as an upload that lost to GC twice,
    not a 500."""
    logger.warning("Deadlock detected on %s %s; answered 503 busy", request.method,
                   request.url.path)
    return JSONResponse(status_code=503,
                        content={"detail": {"error": "busy", "message": BUSY_MESSAGE}})


async def _db_unavailable(request: Request, _exc: Exception) -> JSONResponse:
    """The pool isn't open (Postgres late or down): a 503 the client can retry, not a 500."""
    return JSONResponse(status_code=503, headers={"Retry-After": "15"},
                        content={"detail": {"error": "db_unavailable",
                                            "message": "The database isn't reachable yet. Please try again shortly."}})


def install_handlers(app: FastAPI) -> None:
    from .db import DatabaseUnavailable
    app.add_exception_handler(DatabaseUnavailable, _db_unavailable)
    app.add_exception_handler(pg_errors.DeadlockDetected, _deadlock_busy)
