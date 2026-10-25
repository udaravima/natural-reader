"""Server-side chat tools (spec §5.3). One file per tool; adding a tool = one
file plus one line in REGISTRY. A tool never raises to the turn: failures come
back as {"error": ...} so the model can recover (the OpenAI Agents SDK rule)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...llm.types import ToolCall, ToolSpec
from ..config import ChatConfig
from ..project_scope import ProjectScope
from ..reader import Charge
from ...services.doc_outlines import Outline
from ...services.doc_search import ReadableDoc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """One per turn: what the tools may read, and what this turn has seen."""
    user_id: str
    doc: ReadableDoc | None   # the open document, already checked readable (document scope only)
    cfg: ChatConfig = field(default_factory=ChatConfig, hash=False, compare=False)
    # A project chat (C2.1): the project's current documents, access checked
    # this turn. The document tools read these instead of the open document.
    project: ProjectScope | None = None
    question: str = ""   # the turn's user text
    # Chunk ids whose text the model already has this turn (the prefetch
    # block, earlier searches): shown again only as a page reference.
    shown: set[int] = field(default_factory=set, hash=False, compare=False)
    # Every piece of document text the model has this turn (prefetch, pins,
    # tool results, cut pages too): web_search refuses to send it out.
    seen_text: list[str] = field(default_factory=list, hash=False, compare=False)
    # (doc_id, page, text) of every passage or page of a document the model
    # has this turn: which pages the answer drew on (v2.4 Task D, chat/sources.py).
    evidence: list[tuple[str, int | None, str]] = field(default_factory=list, hash=False, compare=False)
    # The project map (chat/project_map.py) had to leave outlines out: document_outline is offered. And the
    # stored outlines of the project's indexed documents, by doc_id, that the map was built from.
    map_shortened: bool = False
    outlines: dict[str, Outline] = field(default_factory=dict, hash=False, compare=False)
    # The turn's chat model, canonical "provider:name": a tool that makes a model call of its own (the reader,
    # chat/reader.py) can read with it when READER_MODEL=chat.
    model: str = ""
    # Every web search result of this turn by its number (web_search numbers across the turn: the first search's
    # results are 1..k, the next continue from the highest). Each: title, url, site, the query of the search
    # that found it, and `read` once read_web_pages returned facts from it (Task 15 builds the web sources
    # from that). Kept apart from seen_text and evidence, which are document text only.
    web_results: dict[int, dict] = field(default_factory=dict, hash=False, compare=False)
    # How web search asks (services/chat_options.py): "ask" (the model may only offer a search), "auto" (web_search
    # and read_web_pages are offered) or "off" (none). `web_approved`: the user said yes to one search this turn
    # (context.web); the app ran it, and the model may read its results (read_web_pages) but not search again.
    # The default is the safe one: nothing leaves the server until a person approves it.
    web_mode: str = "ask"
    web_approved: bool = False
    # Records each reader call (extract_from_documents, read_web_pages) against the turn's user, so it counts toward
    # the daily budget and the usage figures (orchestrator._charge_reader wires it; chat/reader.py BatchCall). None:
    # nothing is recorded (a tool run outside a turn, as in tests).
    charge: Charge | None = field(default=None, hash=False, compare=False)


class Tool(Protocol):
    name: str
    spec: ToolSpec   # what it does and how to use it; never contradicts the strategy (server/chat/prompt.py)
    source: str      # "document" | "web": where its results come from
    reads_documents: bool   # its results are document text (brings the data rule)

    def available(self, ctx: ToolContext) -> bool: ...

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]: ...

    def summarize(self, args: dict[str, Any], result: dict[str, Any],
                  ctx: ToolContext) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ToolRun:
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]    # what the model reads
    summary: dict[str, Any]   # what is saved on the message: {name, arguments, result_summary}

    @property
    def ok(self) -> bool:
        return "error" not in self.result


from . import (document_outline, extract_from_documents, offer_web_search, read_document_pages,  # noqa: E402 — after the shared types, by convention
               read_web_pages, search_documents, web_search)

REGISTRY: list[Tool] = [search_documents.TOOL, read_document_pages.TOOL, extract_from_documents.TOOL,
                        document_outline.TOOL, web_search.TOOL, read_web_pages.TOOL, offer_web_search.TOOL]


def available_tools(ctx: ToolContext) -> list[Tool]:
    return [t for t in REGISTRY if t.available(ctx)]


async def run_tool(call: ToolCall, ctx: ToolContext, offered: list[Tool]) -> ToolRun:
    tool = next((t for t in offered if t.name == call.name), None)
    if tool is None:
        result: dict[str, Any] = {"error": f"Unknown tool: {call.name}"}
    else:
        try:
            result = await tool.execute(call.arguments, ctx)
        except Exception as e:  # noqa: BLE001 — a tool failure is the model's to handle
            # The exception's message can quote the user's query: only its
            # type at WARNING, the full exception at DEBUG (spec §10).
            logger.warning("Tool %s failed: %s", call.name, type(e).__name__)
            logger.debug("Tool %s failure detail", call.name, exc_info=True)
            result = {"error": f"{call.name} failed: {type(e).__name__}"}
    summary = ({"error": result["error"]} if "error" in result or tool is None
               else tool.summarize(call.arguments, result, ctx))
    # Keys starting with "_" are for summarize() only; the model never reads them.
    result = {k: v for k, v in result.items() if not k.startswith("_")}
    return ToolRun(call.id, call.name, call.arguments, result,
                   {"name": call.name, "arguments": call.arguments, "result_summary": summary})


def result_text(result: dict[str, Any]) -> str:
    """A tool result as the model reads it (the tool message's content)."""
    return json.dumps(result, indent=2, ensure_ascii=False)


class TurnTools:
    """Runs one turn's tool calls with its guard-rails (v2.3 Task D): a call
    identical to an earlier successful one isn't run again, and the tool
    payloads the turn adds to the prompt stay within
    CHAT_TOOL_RESULT_BUDGET_CHARS (the short notes, this class's and the last
    round's, are not counted). A tool marked `unmetered` is neither held to
    the budget nor stopped as a repeat: its result is small and must always
    reach the turn (offer_web_search, whose result ends it)."""
    REPEATED = "Already searched: see the results above."
    BUDGET_USED = "Search budget for this answer used up: answer from what you have."

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.used = 0
        self._done: set[tuple[str, str]] = set()       # ran and succeeded
        self._too_big: set[tuple[str, str]] = set()    # ran, didn't fit: won't fit later either

    @staticmethod
    def _note(call: ToolCall, note: str) -> ToolRun:
        summary = {"ok": True, "chunk_count": None, "query": call.arguments.get("query"),
                   "summary_text": note}
        return ToolRun(call.id, call.name, call.arguments, {"message": note},
                       {"name": call.name, "arguments": call.arguments, "result_summary": summary})

    def _forget_web(self, before: dict[int, bool]) -> None:
        """Web results numbered, and pages marked read, by a result that was thrown away: the model never saw
        them, so the next search reuses the numbers and no source is built from a page it didn't get."""
        results = self.ctx.web_results
        for n in [n for n in results if n not in before]:
            del results[n]
        for n, was_read in before.items():
            if bool(results[n].get("read")) != was_read:
                results[n]["read"] = was_read

    async def run(self, call: ToolCall, offered: list[Tool]) -> ToolRun:
        if any(t.name == call.name and getattr(t, "unmetered", False) for t in offered):
            return await run_tool(call, self.ctx, offered)
        key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
        if key in self._done:
            return self._note(call, self.REPEATED)
        budget = self.ctx.cfg.tool_result_budget_chars
        if self.used >= budget or key in self._too_big:
            return self._note(call, self.BUDGET_USED)
        shown, seen, evidence = set(self.ctx.shown), len(self.ctx.seen_text), len(self.ctx.evidence)
        web = {n: bool(found.get("read")) for n, found in self.ctx.web_results.items()}
        run = await run_tool(call, self.ctx, offered)
        size = len(result_text(run.result))
        if self.used + size > budget:
            # Discarded unseen: nothing it returned counts as shown, or as evidence.
            self.ctx.shown.intersection_update(shown)
            del self.ctx.seen_text[seen:]
            del self.ctx.evidence[evidence:]
            self._forget_web(web)
            self._too_big.add(key)
            return self._note(call, self.BUDGET_USED)
        self.used += size
        if run.ok:   # a failed call may be retried: "already searched" would be wrong
            self._done.add(key)
        return run
