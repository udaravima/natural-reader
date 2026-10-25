"""document_outline: the full outline of one document of a project (C2.1 spec 5.4).

The project map (chat/project_map.py) carries each document's outline, but
when the project is too big for the map's budget it is shortened. Then this tool
is offered, and returns what the map left out: every heading, all levels,
each with its page. It reads the outlines the turn already loaded for the map
(ToolContext.outlines), so it costs no query, and only for indexed documents:
a stale outline of a document that isn't indexed is never shown."""
from __future__ import annotations

from typing import Any

from ...llm.types import ToolSpec
from ..prompt import display_name
from .scope import named_in_project, scope_docs

_SPEC = ToolSpec(
    name="document_outline",
    # Names no other tool (test_no_tool_description_names_another_tool): routing between tools is the rules'.
    description=(
        "The full outline of one document of the project: every heading of every level in reading order, each "
        "with its page. Use it when the project map says outlines are shortened and you need a document's "
        "sections to decide where to look."),
    parameters={
        "type": "object",
        "properties": {
            "document": {"type": ["integer", "string"], "description": (
                "Which document: its number or exact name, as the project map lists it.")},
        },
        "required": ["document"],
    },
)


class _DocumentOutline:
    name = "document_outline"
    spec = _SPEC
    reads_documents = True   # headings are document text (brings the data rule)
    source = "document"

    def available(self, ctx) -> bool:
        return ctx.project is not None and ctx.map_shortened

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        scope = scope_docs(ctx)
        chosen = named_in_project(scope, args.get("document"), ctx, open_if_missing=True)
        if isinstance(chosen, dict):
            return chosen
        outline = ctx.outlines.get(chosen.doc.doc_id)
        entries = [{"level": e.level, "text": display_name(e.text), "page": e.page}
                   for e in (outline.entries if outline else ())]
        result: dict[str, Any] = {"document": {"ref": chosen.ref, "name": chosen.name}, "outline": entries}
        if not entries:
            message = "This document has no outline."
            opening = display_name(outline.opening) if outline else ""
            result["message"] = f'{message} It starts: "{opening}"' if opening else message
        ctx.seen_text.extend(e["text"] for e in entries)   # document text the model now has: web_search won't send it out
        result["_doc"] = {"docId": chosen.doc.doc_id, "docName": chosen.doc.name}   # for summarize() only
        result["_count"] = len(entries)
        return result

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        n = result.get("_count", 0)
        text = f"Outline of {result['_doc']['docName']}: {n} heading{'' if n == 1 else 's'}."
        # No top-level docId: a project reply names no single document (see read_document_pages).
        return {"ok": True, "chunk_count": None, "query": None, "summary_text": text, "document": result["_doc"]}


TOOL = _DocumentOutline()
