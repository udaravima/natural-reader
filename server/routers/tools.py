"""
Frontend-orchestrated chat tools that need a backend. Currently: web_search.

Synchronous by design — a chat tool call blocks on the result, so there is no
202/poll pattern here (unlike doc indexing). See services/web_search.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.web_search import RESULT_COUNT, web_search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/tools", tags=["tools"])


class WebSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    count: int = Field(default=RESULT_COUNT, ge=1, le=10)


@router.post("/web_search")
async def web_search_endpoint(req: WebSearchIn) -> dict:
    try:
        return await web_search(req.query, req.count)
    except Exception as e:  # noqa: BLE001 — surface as 502 so the tool can recover
        logger.exception("web_search failed")
        raise HTTPException(status_code=502, detail=f"Web search failed: {e}") from e
