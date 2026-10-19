"""The two ends of "web search asks first" that the orchestrator calls (C2.1 spec 5.8).

The offer: when a round's calls include a real offer_web_search (its own result, `offered: true`), the turn ends there with the offer
(`web_offer`, below) and the model takes no further step.

The yes: the next turn carries `context.web = {query}`. The app runs that search itself before the model's first
step, and the model finds the result as a tool exchange already in the prompt, call id `app-web-1`, the way it
finds the document prefetch (context.py). The query is the user's: they typed it, or read it on the offer card
and approved it. So it skips the guard that keeps document text off the web, which applies to queries the
model writes. Its results are numbered into the turn's web results like any search, so read_web_pages can read
them.

The query the app runs is the one the user SAW: a model can put characters in it that render as nothing (Tag
characters, variation selectors, zero-width and direction controls), so `clean_query` removes them first, exactly
as the offer card does (src/components/chat/WebOfferCard.jsx `plain()`), and a query with nothing visible in it is
no approval at all.

A yes is for that one search. In the same turn the model may read its results (read_web_pages) or offer a different
search (offer_web_search, which ends the turn again); it is never offered web_search, so no query the user didn't
see leaves the server.
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from typing import Any

from ..llm.types import Message, ToolCall
from ..services import web_search as web_search_service
from .tools import ToolContext, ToolRun, result_text, run_tool
from .tools import web_search as web_search_tool

APP_WEB_CALL_ID = "app-web-1"
OFFER_TOOL = "offer_web_search"


# Line breaks become a space, so words don't run together; then what can't be seen is dropped.
_BREAKS = re.compile("[\t\n\r\u0085\u2028\u2029]+")
# ZWNJ and ZWJ are format characters too, but emoji sequences and Persian or Indic text need them.
_KEPT = frozenset("\u200c\u200d")
# Not Cf, but they join, select or fill and render as nothing (the same ranges as the card):
# the combining grapheme joiner, the Hangul fillers, variation selectors 1-15 (U+FE0F, the emoji
# presentation selector, stays), the variation selectors supplement and the Tag block.
_INVISIBLE = ((0x034F, 0x034F), (0x115F, 0x1160), (0x3164, 0x3164), (0xFFA0, 0xFFA0),
              (0xFE00, 0xFE0E), (0xE0100, 0xE01EF),
              (0xE0000, 0xE007F))   # the whole Tag block: its unassigned code points are not Cf


def _hidden(ch: str) -> bool:
    if ch in _KEPT:
        return False
    # Cf: every format character (bidi controls, zero-width characters, soft hyphen, the Tag block, ...).
    # Cc: the control characters (U+0000-U+001F, U+007F-U+009F), as the card strips them.
    if unicodedata.category(ch) in ("Cf", "Cc"):
        return True
    cp = ord(ch)
    return any(low <= cp <= high for low, high in _INVISIBLE)


def clean_query(text: str) -> str:
    """The query as the user saw it, trimmed. Empty when nothing visible is left."""
    return "".join(ch for ch in _BREAKS.sub(" ", text) if not _hidden(ch)).strip()


def search_possible() -> bool:
    """The app can run an approved search: the search engine is set up. (In an approved turn the model is not
    offered web_search, so this can't be read off the turn's tools.)"""
    return bool(web_search_service.SEARXNG_URL)


async def app_web_search(ctx: ToolContext, query: str) -> ToolRun:
    """The approved search, run as the tool would run it (same numbering, same error shape: a search engine
    that fails is {"error": ...} for the model to read, never an exception)."""
    call = ToolCall(APP_WEB_CALL_ID, "web_search", {"query": query})
    # The guard compares a query with the document text the turn has seen; with none to compare, it passes. The
    # copy shares ctx.web_results, so the numbering lands in the turn's own.
    unguarded = dataclasses.replace(ctx, seen_text=[])
    return await run_tool(call, unguarded, [web_search_tool.TOOL])


def app_web_exchange(run: ToolRun) -> tuple[Message, Message]:
    """The search as the model finds it: its own call, then the result."""
    return (Message("assistant", "", tool_calls=(ToolCall(run.call_id, run.name, run.arguments),)),
            Message("tool", result_text(run.result), tool_call_id=run.call_id, name=run.name))


def web_offer(runs: list[ToolRun]) -> dict[str, Any] | None:
    """The note for the first real offer in a round's runs: {kind: "web_offer", query, reason}. Real means the
    tool's own result: a guard-rail's note ({"message": ...}) has no "error" key either, but offers nothing."""
    for run in runs:
        if run.name == OFFER_TOOL and run.result.get("offered") is True:
            return {"kind": "web_offer", "query": run.result["query"], "reason": run.result["reason"]}
    return None
