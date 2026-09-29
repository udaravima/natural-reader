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
from ...services.doc_search import ReadableDoc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """One per turn: what the tools may read, and what this turn has seen."""
    user_id: str
    doc: ReadableDoc | None   # the open document, already checked readable
    cfg: ChatConfig = field(default_factory=ChatConfig, hash=False, compare=False)
    # Chunk ids whose text the model already has this turn (the prefetch
    # block, earlier searches): shown again only as a page reference.
    shown: set[int] = field(default_factory=set, hash=False, compare=False)
    # Every piece of document text the model has this turn (prefetch, pins,
    # tool results, cut pages too): web_search refuses to send it out.
    seen_text: list[str] = field(default_factory=list, hash=False, compare=False)


class Tool(Protocol):
    name: str
    spec: ToolSpec

    def available(self, ctx: ToolContext) -> bool: ...

    def guidance(self, ctx: ToolContext) -> str:
        """One line for the system rules (server/chat/prompt.py): when and how
        to use this tool. Only offered tools are described."""
        ...

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


from . import read_document_pages, search_documents, web_search  # noqa: E402 — after the shared types, by convention

REGISTRY: list[Tool] = [search_documents.TOOL, read_document_pages.TOOL, web_search.TOOL]


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
    round's, are not counted)."""
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

    async def run(self, call: ToolCall, offered: list[Tool]) -> ToolRun:
        key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
        if key in self._done:
            return self._note(call, self.REPEATED)
        budget = self.ctx.cfg.tool_result_budget_chars
        if self.used >= budget or key in self._too_big:
            return self._note(call, self.BUDGET_USED)
        shown, seen = set(self.ctx.shown), len(self.ctx.seen_text)
        run = await run_tool(call, self.ctx, offered)
        size = len(result_text(run.result))
        if self.used + size > budget:
            # Discarded unseen: nothing it returned counts as shown.
            self.ctx.shown.intersection_update(shown)
            del self.ctx.seen_text[seen:]
            self._too_big.add(key)
            return self._note(call, self.BUDGET_USED)
        self.used += size
        if run.ok:   # a failed call may be retried: "already searched" would be wrong
            self._done.add(key)
        return run
