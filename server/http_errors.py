"""One shape for every refusal (A1 spec §7): FastAPI returns
{"detail": {"error": <code>, "message": <human text>, ...extra}}. The SPA's
src/lib/apiErrors.js maps `error` (and `reason`) to the notice it shows."""
from __future__ import annotations

from fastapi import HTTPException


def refusal(status: int, error: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error, "message": message, **extra})
