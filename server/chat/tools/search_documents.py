"""search_documents: find passages in the documents in scope (spec §5.3; C2).

The scope (tools/scope.py) is the open, indexed document, or a project
chat's searchable documents (C2.1), searched as ONE ranking balanced across
the documents (doc_search.search_scope). The result names each passage's
document by its short `ref`."""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ...db import get_pool
from ...llm.types import ToolSpec
from ...services.doc_search import search_scope
from ...services.embeddings import embed_query
from ..project_scope import ScopeDoc
from .scope import choices_text, documents_listing, resolve_documents, scope_docs

logger = logging.getLogger(__name__)

PER_CHUNK_TEXT_CAP = 1500   # characters per passage the model reads
# No relevance label (final v2.3 review, measured 2026-10-05 with
# nomic-embed-text and its prefixes): on a 600-passage book, questions the
# book doesn't answer found passages scoring 0.67-0.73, as close as for
# questions it does answer (0.66-0.78), so a "strong" label would tell the
# model a passage answers when it doesn't. The order is the only signal; the
# model judges by reading. The floor below only drops plain noise.
MAX_ROWS = 100   # the word rank of a row no word search found: past WORD_BYPASS_RANKS
# Only the best matches of EVERY word of the query may skip the floor: a common
# word ("method") matches many chunks, and weak ones would crowd out passages
# near in meaning. A partial word match (some of the words, doc_search) is no
# such evidence: it must pass the floor like any other row, and is labelled.
WORD_BYPASS_RANKS = 3
NONE_FOUND = "No passages about this in the document."
NONE_FOUND_PROJECT = "No passages about this in the project's documents."
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
            "documents": {"type": "array", "items": {"type": ["integer", "string"]}, "description": (
                "Search only these documents: their numbers or exact names, as results list them. Leave it "
                "out to search them all.")},
        },
        "required": ["query"],
    },
)


def passages_result(query: str, scope: list[ScopeDoc], passages: list[dict[str, Any]],
                    shown_refs: Iterable[int] = ()) -> dict[str, Any]:
    """The result as the model reads it; the prefetch's tool exchange
    (server/chat/context.py) builds the same shape, so the two never drift.
    `documents` names only the documents the passages (and `shown_refs`, those
    of the "already shown" notes) are from: the project map names them all."""
    named = {p["ref"] for p in passages} | set(shown_refs)
    return {"query": query, "documents": documents_listing([d for d in scope if d.ref in named]),
            "passages": passages}


def _cap(text: str) -> str:
    return text if len(text) <= PER_CHUNK_TEXT_CAP else text[:PER_CHUNK_TEXT_CAP] + " [truncated]"


class _SearchDocuments:
    name = "search_documents"
    spec = _SPEC
    reads_documents = True   # brings the data rule (server/chat/prompt.py)
    source = "document"

    def available(self, ctx) -> bool:
        return bool(scope_docs(ctx))

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        scope = scope_docs(ctx)
        if not scope:
            return {"error": "No indexed document is open."}
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        k = args.get("k")
        k = max(1, min(10, int(k))) if isinstance(k, (int, float)) and not isinstance(k, bool) else 5
        if ctx.project is not None:
            # `documents` narrows a project's search. In document scope there is only the open document, and a
            # near-name a small model fills in must not cost it a round (as for read_document_pages).
            named = args.get("documents")
            if named not in (None, "", []):
                found, unknown = resolve_documents(scope, named if isinstance(named, list) else [named])
                if unknown:
                    return {"error": f"Unknown document: {', '.join(unknown)}. "
                                     f"Use a number or name from the list: {choices_text(scope)}."}
                scope = [d for d in scope if d in found]   # the project's order, whatever the model's
        qvec = await embed_query(query)
        # What the model already has is handed to the search as `exclude`: it is set aside BEFORE the per-document
        # balance (so it doesn't use up a document's allowance and starve its deeper passages in a follow-up
        # search), and the search looks past it to find k new passages. The excluded passages that rank among
        # them come back in place, for the "already shown" note below.
        by_doc = {d.doc.doc_id: d for d in scope}
        async with get_pool().connection() as conn:
            if ctx.project is not None:
                # One ranking over the project, a document giving a few passages at a time (spec 5.5).
                rows = await search_scope(conn, list(by_doc), qvec, k, text=query, exclude=ctx.shown,
                                          per_doc=ctx.cfg.search_per_doc, depth=ctx.cfg.project_fusion_depth)
            else:
                rows = await search_scope(conn, list(by_doc), qvec, k, text=query, exclude=ctx.shown)
        found_rows = [(by_doc[r["doc_id"]], r) for r in rows]
        floor = ctx.cfg.search_min_score
        # One of the best matches of every word stays even when weak in meaning (Task F). A partial match
        # (some of the words) gets no such pass.
        kept = [(d, r) for d, r in found_rows
                if r["score"] >= floor
                or (r.get("word_match") == "all" and (r.get("word_rank") or MAX_ROWS) <= WORD_BYPASS_RANKS)]

        passages: list[dict[str, Any]] = []
        new: list[tuple[ScopeDoc, dict[str, Any]]] = []
        seen_pages: dict[int, set[int]] = {}   # ref -> pages of passages the model already has
        for d, r in kept:
            if len(new) == k:
                break
            if r["id"] in ctx.shown:
                if r["page"] is not None:
                    seen_pages.setdefault(d.ref, set()).add(r["page"])
            else:
                new.append((d, r))
                passage = {"ref": d.ref, "page": r["page"]}
                if r.get("word_match") == "all":
                    # "words": it has the query's words but not its meaning.
                    passage["match"] = "both" if r["score"] >= floor else "words"
                elif r.get("word_match") == "partial":
                    passage["match"] = "some words"   # only here if its meaning passed the floor
                passage["text"] = _cap(r["text"] or "")
                passages.append(passage)
        ctx.shown.update(r["id"] for _, r in new)
        ctx.seen_text.extend(p["text"] for p in passages)
        ctx.evidence.extend((d.doc.doc_id, p["page"], p["text"]) for (d, _), p in zip(new, passages))
        logger.debug("search_documents docs=%d found=%d kept=%d new=%d floor=%s best_cosine=%s",
                     len(scope), len(found_rows), len(kept), len(new), ctx.cfg.search_min_score,
                     round(max(r["score"] for _, r in found_rows), 4) if found_rows else None)
        result = passages_result(query, scope, passages, seen_pages)
        if seen_pages:
            # One short list, however many rounds re-find the same passages.
            result["already_shown"] = [{"ref": ref, "pages": sorted(pages)}
                                       for ref, pages in sorted(seen_pages.items())]
        if not new:
            result["message"] = (NOTHING_NEW if seen_pages
                                 else NONE_FOUND_PROJECT if ctx.project is not None else NONE_FOUND)
        # For summarize() only: stripped before the model reads the result.
        # In a project each passage says which document it came from.
        result["_saved"] = [{**({"docId": d.doc.doc_id} if ctx.project is not None else {}),
                             "page": r["page"], "score": round(r["score"], 4)} for d, r in new]
        return result

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        # docId/docName are saved with the reply so its page citations can
        # open this document later; the raw scores are for tuning. The model
        # reads neither. A project reply names no single document: its
        # "(page N)" mustn't open one (per-document citations come later).
        saved = result.get("_saved", [])
        summary = {"ok": True, "chunk_count": len(saved), "query": result["query"], "summary_text": None}
        if ctx.project is None:
            summary |= {"docId": ctx.doc.doc_id, "docName": ctx.doc.name}
        return {**summary, "passages": saved}


TOOL = _SearchDocuments()
