"""A request body read as capped bytes, refused with 413 as soon as it
crosses the limit — never buffered whole before a route gets a chance to
refuse it. Shared by any route that hand-parses a JSON body (so pydantic
validation runs on bytes already known to be within bounds) instead of
letting FastAPI's own `Body(...)` buffer an unbounded request first."""
from __future__ import annotations

from fastapi import Request

from .http_errors import refusal


async def read_capped_body(request: Request, limit_mb: int, *, label: str = "Attachments") -> bytes:
    """Stream `request`'s body, raising 413 `too_large` the moment either the
    declared Content-Length or the bytes actually read cross `limit_mb`.
    `label` only changes the human message (e.g. "Attachments", "Import");
    the refusal's `error` code and `limit_mb` field are always the same."""
    limit = limit_mb * 1024 * 1024
    too_large = refusal(413, "too_large", f"{label} too large (limit {limit_mb} MB).", limit_mb=limit_mb)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise too_large
    return bytes(buf)
