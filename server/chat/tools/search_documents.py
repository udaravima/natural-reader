"""search_documents: find passages in the documents in scope (spec §5.3; C2).

This slice's scope is the one open, indexed document. C2 widens it (a project,
the library) with a `scope` argument: the result shape already names each
passage's document by a short `ref`, so nothing here is renamed then."""
from __future__ import annotations

import logging
from typing import Any

from ...db import get_pool
from ...llm.types import ToolSpec
from ...services.doc_search import search_chunks
from ...services.embeddings import embed_query
from .scope import document_scope, documents_listing

logger = logging.getLogger(__name__)

PER_CHUNK_TEXT_CAP = 1500   # characters per passage the model reads
# No relevance label (final v2.3 review, measured 2026-10-05 with
# nomic-embed-text and its prefixes): on a 600-passage book, questions the
# book doesn't answer found passages scoring 0.67-0.73, as close as for
# questions it does answer (0.66-0.78), so a "strong" label would tell the
# model a passage answers when it doesn't. The order is the only signal; the
# model judges by reading. The floor below only drops plain noise.
MAX_ROWS = 100   # rows read per document per search: past the shown ones, to k new
# Only the best word matches may skip the floor: a common word ("method")
# matches many chunks, and weak ones would crowd out passages near in meaning.
WORD_BYPASS_RANKS = 3
NONE_FOUND = "No passages about this in the document."
# The id of the search the app runs before the model (v2.4 Task B), shown to
# the model as its own call. Nine alphanumerics: some OpenAI-compatible
# backends (Mistral's API, as reported) accept only that shape.
PREFETCH_CALL_ID = "prefetch1"
NOTHING_NEW = ("Nothing new: every passage found was already shown above. "
               "Search with different words, or answer from what you have.")

_SPEC = ToolSpec(
    name="search_documents",
    # v2.4: says how THIS search behaves, because models search the way they'd
    # use a web search engine. Its exact-word half is plainto_tsquery('simple'):
    # every query word must be in the passage, unstemmed — "What does Table 7.3
    # report?" doesn't match "Table 7.3 reports…", "Table 7.3" does (checked on
    # Postgres 16, 2026-10-05). No claim that a search already ran: it's false
    # whenever prefetch is off or found nothing.
    description=(
        "Search the open document. Not a web search engine: it compares your query with the document's "
        "own text in two ways. By meaning (vector search): a short phrase in the document's words and "
        "language finds passages about it, even worded differently. By exact words: a passage containing "
        "every word of the query, spelled the same, is found even when its meaning is far, so a short "
        "query with a distinctive name, label or number (ZEPHYR-9, Table 4.2) finds it, while a long "
        "question rarely does. Search for one thing at a time, without quotes or operators. Returns up "
        "to k passages, closest first, each with its page. The closest passages may still not answer the "
        "question: read them before relying on them. Passages you already have this turn come back as "
        "page numbers only, under already_shown."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": (
                "One thing to find, in the document's words and language: a short phrase, or a "
                "distinctive name, label or number. Not a whole question.")},
            "k": {"type": "integer", "minimum": 1, "maximum": 10, "description": (
                "How many passages, 1-10. Default 5; more for broad questions.")},
        },
        "required": ["query"],
    },
)


def passages_result(query: str, scope: list, passages: list[dict[str, Any]]) -> dict[str, Any]:
    """The result as the model reads it; the prefetch's tool exchange
    (server/chat/context.py) builds the same shape, so the two never drift."""
    return {"query": query, "documents": documents_listing(scope), "passages": passages}


def _cap(text: str) -> str:
    return text if len(text) <= PER_CHUNK_TEXT_CAP else text[:PER_CHUNK_TEXT_CAP] + " [truncated]"


class _SearchDocuments:
    name = "search_documents"
    spec = _SPEC
    reads_documents = True   # brings the data rule (server/chat/prompt.py)
    source = "document"

    def available(self, ctx) -> bool:
        return bool(document_scope(ctx))

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        scope = document_scope(ctx)
        if not scope:
            return {"error": "No indexed document is open."}
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        k = args.get("k")
        k = max(1, min(10, int(k))) if isinstance(k, (int, float)) and not isinstance(k, bool) else 5
        qvec = await embed_query(query)
        # Look past everything already shown, so a later round still finds k
        # new passages. The search is exact (doc_search.search_chunks), so
        # MAX_ROWS is only a cost bound.
        want = min(k + len(ctx.shown), MAX_ROWS)
        found: list[tuple[int, dict[str, Any]]] = []
        async with get_pool().connection() as conn:
            for ref, doc in enumerate(scope, start=1):
                found += [(ref, r) for r in await search_chunks(conn, doc.doc_id, qvec, want, text=query)]
        # Each document's list is already fused best-first; across documents
        # (C2) there is no common rank, so cosine orders them.
        if len(scope) > 1:
            found.sort(key=lambda fr: fr[1]["score"], reverse=True)
        floor = ctx.cfg.search_min_score
        # One of the best word matches stays even when weak in meaning (Task F).
        kept = [(ref, r) for ref, r in found
                if r["score"] >= floor or (r.get("word_rank") or MAX_ROWS) <= WORD_BYPASS_RANKS]

        passages: list[dict[str, Any]] = []
        new: list[tuple[int, dict[str, Any]]] = []
        seen_pages: dict[int, set[int]] = {}   # ref -> pages of passages the model already has
        for ref, r in kept:
            if len(new) == k:
                break
            if r["id"] in ctx.shown:
                if r["page"] is not None:
                    seen_pages.setdefault(ref, set()).add(r["page"])
            else:
                new.append((ref, r))
                passage = {"ref": ref, "page": r["page"]}
                if r.get("by_words"):
                    # "words": it has the query's words but not its meaning.
                    passage["match"] = "both" if r["score"] >= floor else "words"
                passage["text"] = _cap(r["text"] or "")
                passages.append(passage)
        ctx.shown.update(r["id"] for _, r in new)
        ctx.seen_text.extend(p["text"] for p in passages)
        logger.debug("search_documents docs=%d found=%d kept=%d new=%d floor=%s best_cosine=%s",
                     len(scope), len(found), len(kept), len(new), ctx.cfg.search_min_score,
                     round(max(r["score"] for _, r in found), 4) if found else None)
        result = passages_result(query, scope, passages)
        if seen_pages:
            # One short list, however many rounds re-find the same passages.
            result["already_shown"] = [{"ref": ref, "pages": sorted(pages)}
                                       for ref, pages in sorted(seen_pages.items())]
        if not new:
            result["message"] = NOTHING_NEW if seen_pages else NONE_FOUND
        # For summarize() only: stripped before the model reads the result.
        result["_saved"] = [{"page": r["page"], "score": round(r["score"], 4)} for _, r in new]
        return result

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        # docId/docName are saved with the reply so its page citations can
        # open this document later; the raw scores are for tuning. The model
        # reads neither.
        saved = result.get("_saved", [])
        return {"ok": True, "chunk_count": len(saved), "query": result["query"],
                "summary_text": None, "docId": ctx.doc.doc_id, "docName": ctx.doc.name,
                "passages": saved}


TOOL = _SearchDocuments()
