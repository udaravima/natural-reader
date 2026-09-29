"""Server-side chat tools (spec §5.3). One file per tool; adding a tool = one
file plus one line in REGISTRY. A tool never raises to the turn: failures come
back as {"error": ...} so the model can recover (the OpenAI Agents SDK rule)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...llm.types import ToolCall, ToolSpec
from ...services.doc_search import ReadableDoc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """One per turn: what the tools may read, and what this turn has seen."""
    user_id: str
    doc: ReadableDoc | None   # the open document, already checked readable
    search_min_score: float = 0.45   # CHAT_SEARCH_MIN_SCORE: weaker passages are noise
    # Chunk ids whose text the model already has this turn (the prefetch
    # block, earlier searches): shown again only as a page reference.
    shown: set[int] = field(default_factory=set, hash=False, compare=False)


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


from . import search_documents, web_search  # noqa: E402 — after the shared types, by convention

REGISTRY: list[Tool] = [search_documents.TOOL, web_search.TOOL]


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
