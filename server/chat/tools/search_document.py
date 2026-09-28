"""search_document: semantic search over the open, indexed document (spec §5.3).
Moved from src/lib/chatTools/searchDocument.js; same description and result shape."""
from __future__ import annotations

from typing import Any

from ...db import get_pool
from ...llm.types import ToolSpec
from ...services.doc_search import search_chunks
from ...services.embeddings import embed_one

PER_CHUNK_TEXT_CAP = 1500   # characters per passage the model reads

_SPEC = ToolSpec(
    name="search_document",
    description=(
        "Search the document the user is currently reading for passages relevant to a question. "
        "Use this when the user's question is about the document's contents and you don't already "
        "have the relevant excerpt in your context. Returns up to k chunks ranked by semantic "
        "similarity, each tagged with its page number so you can cite it."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": (
                "Natural-language search query. Be specific — concepts and phrasings from "
                "the user's question work better than single keywords.")},
            "k": {"type": "integer", "minimum": 1, "maximum": 10,
                  "description": "How many top chunks to return (1-10). Default 5."},
        },
        "required": ["query"],
    },
)


def _cap(text: str) -> str:
    return text if len(text) <= PER_CHUNK_TEXT_CAP else text[:PER_CHUNK_TEXT_CAP] + " [truncated]"


class _SearchDocument:
    name = "search_document"
    spec = _SPEC

    def available(self, ctx) -> bool:
        return ctx.doc is not None and ctx.doc.state == "indexed"

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        if ctx.doc is None:
            return {"error": "No document is currently loaded."}
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        k = args.get("k")
        k = max(1, min(10, int(k))) if isinstance(k, (int, float)) and not isinstance(k, bool) else 5
        qvec = await embed_one(query)
        async with get_pool().connection() as conn:
            rows = await search_chunks(conn, ctx.doc.doc_id, qvec, k)
        return {"query": query, "chunk_count": len(rows), "results": [
            {"index": i, "page": r["page"], "score": round(r["score"], 4), "text": _cap(r["text"] or "")}
            for i, r in enumerate(rows, start=1)]}

    def summarize(self, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "chunk_count": result["chunk_count"], "query": result["query"],
                "summary_text": None}


TOOL = _SearchDocument()
