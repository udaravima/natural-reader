"""The two ends of "web search asks first" that the orchestrator calls (C2.1 spec 5.8).

The offer: when a round's calls include a successful offer_web_search, the turn ends there with the offer
(`web_offer`, below) and the model takes no further step.

The yes: the next turn carries `context.web = {query}`. The app runs that search itself before the model's first
step, and the model finds the result as a tool exchange already in the prompt, call id `app-web-1`, the way it
finds the document prefetch (context.py). The query is the user's: they typed it, or read it on the offer card
and approved it. So it skips the guard that keeps document text off the web, which applies to queries the
model writes. Its results are numbered into the turn's web results like any search, so read_web_pages can read
them.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from ..llm.types import Message, ToolCall
from .tools import ToolContext, ToolRun, result_text, run_tool
from .tools import web_search as web_search_tool

APP_WEB_CALL_ID = "app-web-1"
OFFER_TOOL = "offer_web_search"


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
    """The note for the first successful offer in a round's runs: {kind: "web_offer", query, reason}."""
    for run in runs:
        if run.name == OFFER_TOOL and run.ok:
            return {"kind": "web_offer", "query": run.result["query"], "reason": run.result["reason"]}
    return None
