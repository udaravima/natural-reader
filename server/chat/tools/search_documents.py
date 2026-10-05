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
# Relevance buckets over cosine similarity. Measured on nomic-embed-text:
# matching questions scored 0.563-0.823, unrelated ones 0.428-0.513
# (server/chat/config.py), so the default floor (0.45) still lets the upper
# part of that noise through, labelled "weak". The model sees the bucket,
# never the number.
STRONG, MODERATE = 0.7, 0.55
MAX_ROWS = 100   # rows read per document per search: past the shown ones, to k new
# Only the best word matches may skip the floor: a common word ("method")
# matches many chunks, and weak ones would crowd out passages near in meaning.
WORD_BYPASS_RANKS = 3
NONE_FOUND = "No passages about this in the document."
NOTHING_NEW = ("Nothing new: every passage found was already shown above. "
               "Search with different words, or answer from what you have.")

_SPEC = ToolSpec(
    name="search_documents",
    description=(
        "Search the open document for passages about something, by meaning (vector search over "
        "its indexed text) and by exact words: a passage with every word of the query is found "
        "even when its meaning is far. Returns up to k passages, best first, each with its page "
        "and a relevance of strong, moderate or weak; passages far in meaning are left out "
        "unless they have the query's words. The user's question was already searched by "
        "meaning before you ran: search with different words, or with a label, name or number "
        "from it or from earlier passages (Table 4.2, MIMIC-IV). A passage already shown this "
        "turn is listed by page only, under already_shown."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": (
                "What to find: a topic, name, term or claim, in words likely to appear near it.")},
            "k": {"type": "integer", "minimum": 1, "maximum": 10, "description": (
                "How many passages (1-10). Default 5; up to 10 for broad questions.")},
        },
        "required": ["query"],
    },
)


def relevance(score: float) -> str:
    return "strong" if score >= STRONG else "moderate" if score >= MODERATE else "weak"


def _cap(text: str) -> str:
    return text if len(text) <= PER_CHUNK_TEXT_CAP else text[:PER_CHUNK_TEXT_CAP] + " [truncated]"


class _SearchDocuments:
    name = "search_documents"
    spec = _SPEC
    reads_documents = True   # brings the grounding and citation rules (server/chat/prompt.py)

    def available(self, ctx) -> bool:
        return bool(document_scope(ctx))

    def guidance(self, ctx) -> str:
        # C1 spec §5 "steering is load-bearing": without "answer from them when
        # they are enough", a model handed passages searches anyway. Worded for
        # both cases (the rules are stable for the turn; the prefetch varies).
        return ("find passages in the open document by meaning and by exact words. If the user's "
                "message has a <document_passages> block, those passages were found by meaning for "
                "their question: answer from them when they are enough, and search only for what "
                "they don't cover, with different words or with a label, name or number from the "
                "question or the passages (those are matched exactly). If it has none, nothing "
                "matched yet: search before saying the document doesn't cover the question.")

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
                passage = {"ref": ref, "page": r["page"], "relevance": relevance(r["score"])}
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
        result: dict[str, Any] = {
            "query": query,
            "documents": documents_listing(scope),
            "passages": passages,
        }
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
