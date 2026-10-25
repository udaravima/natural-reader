"""
Frontend-orchestrated chat tools that need a backend. Currently: web_search.

Synchronous by design — a chat tool call blocks on the result, so there is no
202/poll pattern here (unlike doc indexing). See services/web_search.py.

The chat's web search mode applies here too (final review M3): when it is "off" (the admin console's Off, else
CHAT_WEB_SEARCH=off) the route refuses with 403 and sends nothing. "ask" and "auto" don't change it: a call here is
the caller's own explicit search of their own query.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.deps import Principal, require_capability
from ..db import get_pool
from ..services import chat_options
from ..services.web_search import RESULT_COUNT, web_search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/tools", tags=["tools"])


class WebSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    count: int = Field(default=RESULT_COUNT, ge=1, le=10)


OFF_DETAIL = "Web search is off on this server."


async def _web_mode() -> str:
    """The chat's web search mode, resolved as a chat turn resolves it (orchestrator._chat_options): the admin's
    setting, then CHAT_WEB_SEARCH, then "ask"; if the setting can't be read, the environment's value, else "ask"."""
    try:
        async with get_pool().connection() as conn:
            return (await chat_options.load_chat_options(conn)).web_search
    except Exception:  # noqa: BLE001 — as a turn: the environment decides when the row can't be read
        logger.warning("Chat options couldn't be read: using CHAT_WEB_SEARCH, else the default", exc_info=True)
        return chat_options.from_env().web_search


@router.post("/web_search")
async def web_search_endpoint(
    req: WebSearchIn, _: Principal = Depends(require_capability("chat"))
) -> dict:
    if await _web_mode() == "off":
        raise HTTPException(status_code=403, detail=OFF_DETAIL)
    try:
        return await web_search(req.query, req.count)
    except Exception as e:  # noqa: BLE001 — surface as 502 so the tool can recover
        logger.exception("web_search failed")
        raise HTTPException(status_code=502, detail=f"Web search failed: {e}") from e
