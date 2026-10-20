"""extract_from_documents: go through many pages for one goal (C2.1 spec §5.4.1).

The chat model works with a small context, so it can't read thirty pages to find one procedure. This tool hands
the pages to the reader sub-agent (chat/reader.py) with the model's goal (`need`) and returns only the facts,
each with its document, its page and a quote the app has checked against that page. The model's context holds
the findings, not the pages.

Which pages: the ones asked for, else every page of the documents named. Over CHAT_EXTRACT_MAX_PAGES the closest
pages to the need are read (the project search's ranking), and the result lists what was not read so the model
can ask for it. A page the reader failed on is listed, without any of its text.

Labels sent to the reader are built from the ref and page number only: a document's title is someone else's text.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from ...db import get_pool
from ...llm.types import ToolSpec
from ...services.doc_search import search_scope
from ...services.embeddings import embed_query
from ..project_scope import ScopeDoc
from ..reader import Passage, ReaderResult, batches, get_reader_config, read_many
from .read_document_pages import page_count, page_number, page_texts
from .scope import choices_text, documents_listing, named_in_project, open_document, scope_docs

logger = logging.getLogger(__name__)

MAX_DOCUMENTS = 5
NEED_MAX_CHARS = 500
RANK_DEPTH = 3     # the ranking looks at this many chunks per page it may read: pages hold several
PIECE_OVERLAP = 200    # characters two consecutive pieces of a split page share, so a quote across a cut is whole in one
_WHITESPACE = re.compile(r"\s")
IN_ORDER_NOTE = "The pages were taken in order: finding the best pages failed."
NO_TEXT_NOTE = "The pages have no text (they may be images)."
PAGES_SHAPE = "pages must be a list of {document, first_page, last_page}."

_DOCUMENT = {"type": ["integer", "string"], "description": (
    "A document: its number or exact name, as results list them.")}
_SPEC = ToolSpec(
    name="extract_from_documents",
    # Names no other tool (test_no_tool_description_names_another_tool): routing between tools is the rules'.
    description=(
        "Go through many pages of the documents for one goal and get back only the facts that serve it, each "
        "with its document, its page and a quote copied from that page. A separate reader takes the pages, so "
        "none of their text fills your context: use it for a lot of text, such as every page of a document, a "
        "procedure spread over several pages, or a list to collect. State what you need, and name the exact "
        "things to copy (commands, versions, numbers). It reads a limited number of pages per call and lists "
        "the pages it did not read, or could not read, so you can ask for them."),
    parameters={
        "type": "object",
        "properties": {
            "documents": {"type": "array", "items": {"type": ["integer", "string"]}, "minItems": 1,
                          "maxItems": MAX_DOCUMENTS, "description": (
                              "Which documents, 1-5: their numbers or exact names, as results list them.")},
            "need": {"type": "string", "minLength": 1, "maxLength": NEED_MAX_CHARS, "description": (
                "What you need from them, in a sentence or two, in the documents' language: the facts to find, "
                "not the user's question.")},
            "pages": {"type": "array", "description": (
                "Read only these pages. Leave it out to go through all the pages of the documents."),
                "items": {"type": "object", "required": ["document", "first_page"], "properties": {
                    "document": _DOCUMENT,
                    "first_page": {"type": "integer", "minimum": 1, "description": "The first page to read."},
                    "last_page": {"type": "integer", "minimum": 1, "description": (
                        "The last page to read. Leave it out to read one page.")}}}},
        },
        "required": ["documents", "need"],
    },
)


class _Refused(Exception):
    """The call can't be run; the message is the error the model reads."""


def _need(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _Refused(f"need is required: say what to find, in 1-{NEED_MAX_CHARS} characters.")
    if len(text) > NEED_MAX_CHARS:
        raise _Refused(f"need is too long ({len(text)} characters): at most {NEED_MAX_CHARS}.")
    return text


def _chosen(scope: list[ScopeDoc], values: Any, ctx) -> list[ScopeDoc]:
    """The documents named, in ref order. In document scope it is the open document whatever `documents` says
    (a small model fills the argument with a near-name, and one wrong guess must not cost it a round, as for
    read_document_pages). In a project each value gets the same lookup and errors as named_in_project; with
    none named, it is the document open in the reader if that is one of the project's searchable documents."""
    if ctx.project is None:
        return [scope[0]]
    if values is None or values == "" or values == []:
        open_doc = open_document(scope)
        if open_doc is not None:
            return [open_doc]
    if isinstance(values, (int, float, str)) and not isinstance(values, bool) and values != "":
        values = [values]    # a lone value names one document (1.0 too, as inside a list)
    if not isinstance(values, list) or not values:
        raise _Refused(f"documents is required: 1-{MAX_DOCUMENTS} numbers or names from the list: "
                       f"{choices_text(scope)}.")
    if len(values) > MAX_DOCUMENTS:
        raise _Refused(f"Name at most {MAX_DOCUMENTS} documents per call (got {len(values)}).")
    found: list[ScopeDoc] = []
    for value in values:
        doc = named_in_project(scope, value, ctx)
        if isinstance(doc, dict):
            raise _Refused(doc["error"])
        if doc not in found:
            found.append(doc)
    return sorted(found, key=lambda d: d.ref)


def _asked(raw: Any, scope: list[ScopeDoc], chosen: list[ScopeDoc], ctx) -> dict[int, list[tuple[int, int]]]:
    """`pages` as {ref: [(first, last)]}; a document with no entry is read whole."""
    if raw is None or (isinstance(raw, (str, list, dict)) and not raw):
        return {}    # "", {} and [] are what a small model sends for "none"
    if not isinstance(raw, list):
        raise _Refused(PAGES_SHAPE)
    asked: dict[int, list[tuple[int, int]]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise _Refused(PAGES_SHAPE)
        doc = chosen[0] if ctx.project is None else named_in_project(scope, entry.get("document"), ctx)
        if isinstance(doc, dict):
            raise _Refused(doc["error"])
        if doc not in chosen:
            raise _Refused(f"pages names document {doc.ref} ({doc.name}), which isn't in documents. "
                           f"documents: {choices_text(chosen)}.")
        first = page_number(entry.get("first_page"))
        if first is None:
            raise _Refused("Each pages entry needs first_page: a page number.")
        last = first if entry.get("last_page") is None else page_number(entry.get("last_page"))
        if last is None:
            raise _Refused("last_page must be a page number.")
        if last < first:
            raise _Refused("last_page must not be before first_page.")
        asked.setdefault(doc.ref, []).append((first, last))
    return asked


async def _candidates(chosen: list[ScopeDoc], asked: dict[int, list[tuple[int, int]]]) -> list[tuple[int, int]]:
    """Every (ref, page) that may be read, in that order: the ranges asked for, clamped to the document's pages,
    else all of its pages. Only pages that have text: a blank page (a cover, a scan) has no chunks, is never
    sent to the reader, and so must neither take a place in the cap nor be listed as not read."""
    out: list[tuple[int, int]] = []
    async with get_pool().connection() as conn:
        for d in chosen:
            count = await page_count(conn, d.doc)
            pages: set[int] = set()
            if d.ref in asked:
                for first, last in asked[d.ref]:
                    if first < 1 or first > count:
                        raise _Refused(f"Document {d.ref} has pages 1-{count}.")
                    pages.update(range(first, min(last, count) + 1))
            else:
                pages.update(range(1, count + 1))    # none when its pages are unknown and it has no text
            cur = await conn.execute(
                "SELECT DISTINCT page FROM doc_chunks WHERE doc_id = %s AND page BETWEEN %s AND %s",
                (d.doc.doc_id, min(pages, default=1), max(pages, default=0)))
            with_text = {row[0] for row in await cur.fetchall()}
            out.extend((d.ref, page) for page in sorted(pages & with_text))
    return out


async def _pick(chosen: list[ScopeDoc], candidates: list[tuple[int, int]], need: str,
                cap: int) -> tuple[list[tuple[int, int]], bool]:
    """The pages to read, in (ref, page) order, and whether the ranking failed. Within the cap, all of them. Over
    it: the candidates among the best-ranked chunks for the need, best first up to the cap, then the rest in
    order; if the ranking fails, the first pages in order."""
    if len(candidates) <= cap:
        return candidates, False
    ref_of = {d.doc.doc_id: d.ref for d in chosen}
    try:
        qvec = await embed_query(need)
        async with get_pool().connection() as conn:
            rows = await search_scope(conn, list(ref_of), qvec, cap * RANK_DEPTH, text=need, per_doc=None)
    except Exception as e:  # noqa: BLE001 — reading the first pages is better than failing the call
        logger.warning("extract_from_documents: ranking the pages failed (%s); taking them in order", type(e).__name__)
        return candidates[:cap], True
    wanted = set(candidates)
    picked: dict[tuple[int, int], None] = {}    # distinct, in rank order
    for row in rows:
        key = (ref_of.get(row["doc_id"]), row["page"])
        if key in wanted:
            picked.setdefault(key)
        if len(picked) == cap:
            break
    for key in candidates:
        if len(picked) == cap:
            break
        picked.setdefault(key)
    return sorted(picked), False


def _runs(pages: Iterable[int]) -> list[tuple[int, int]]:
    """Sorted pages as (first, last) runs of consecutive pages."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return [(a, b) for a, b in runs]


def _listed(pairs: Iterable[tuple[int, int]]) -> list[dict[str, Any]]:
    """(ref, page) pairs as [{"ref": 1, "pages": "4-9, 12"}]."""
    by_ref: dict[int, list[int]] = {}
    for ref, page in sorted(pairs):
        by_ref.setdefault(ref, []).append(page)
    return [{"ref": ref, "pages": ", ".join(f"{a}-{b}" if b > a else str(a) for a, b in _runs(pages))}
            for ref, pages in by_ref.items()]


def _pieces(text: str, limit: int) -> list[str]:
    """`text` in pieces of at most `limit` characters. A page longer than a reader batch must not be cut silently
    (reader.batches), and a fact is quoted from one piece.
    - Each piece ends at a paragraph break, else a line break, else a space (in the window's second half), else
      at the limit.
    - The next one starts PIECE_OVERLAP characters back, at the first word inside that stretch, so a quote that
      crosses a cut is whole in the next piece. A quote in the overlap is found twice; the results are merged.
      (The overlap is kept under half the limit, so that every piece moves on.)"""
    overlap = min(PIECE_OVERLAP, limit // 2 - 1)
    pieces: list[str] = []
    start = 0
    while len(text) - start > limit:
        end = start + limit
        for sep in ("\n\n", "\n", " "):
            at = text.rfind(sep, start + limit // 2, end)
            if at >= 0:
                end = at + len(sep)
                break
        pieces.append(text[start:end])
        word = _WHITESPACE.search(text, end - overlap, end)
        start = word.end() if word else end - overlap
    pieces.append(text[start:])
    return [p for p in (piece.strip() for piece in pieces) if p]


async def _passages(chosen: list[ScopeDoc], picked: list[tuple[int, int]], limit: int) -> list[Passage]:
    """One passage per non-empty page in (ref, page) order, labelled `[ref, page n]` (more than one when the page
    is longer than `limit`). An empty page is never sent."""
    pages: dict[int, list[int]] = {}
    for ref, page in picked:
        pages.setdefault(ref, []).append(page)
    passages: list[Passage] = []
    async with get_pool().connection() as conn:
        for d in chosen:
            for first, last in _runs(pages.get(d.ref, [])):
                for page, text in await page_texts(conn, d.doc, first, last):
                    passages += [Passage(f"[{d.ref}, page {page}]", piece, d.ref, page)
                                 for piece in _pieces(text, limit)]
    return passages


def _facts(results: list[ReaderResult]) -> list[dict[str, Any]]:
    """The verified facts, each once, by (ref, page); facts on one page keep the order the reader gave."""
    out: list[dict[str, Any]] = []
    for result in results:
        for f in result.facts:
            entry = {"ref": f.doc_ref, "page": f.page, "fact": f.fact, "quote": f.quote}
            if entry not in out:
                out.append(entry)
    return sorted(out, key=lambda e: (e["ref"], e["page"]))


def _cut(result: dict[str, Any], limit: int) -> dict[str, Any]:
    """`result` with the last facts dropped until it fits `limit` characters; "more" counts the dropped ones."""
    facts = result["facts"]
    for dropped in range(len(facts) + 1):
        out = {**result, "facts": facts[:len(facts) - dropped]}
        if dropped:
            out["more"] = dropped
        if len(json.dumps(out, ensure_ascii=False)) <= limit:
            break
    return out


class _ExtractFromDocuments:
    name = "extract_from_documents"
    spec = _SPEC
    reads_documents = True   # brings the data rule (server/chat/prompt.py)
    source = "document"

    def available(self, ctx) -> bool:
        return bool(scope_docs(ctx))

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        scope = scope_docs(ctx)
        if not scope:
            return {"error": "No indexed document is open."}
        try:
            need = _need(args.get("need"))
            chosen = _chosen(scope, args.get("documents"), ctx)
            candidates = await _candidates(chosen, _asked(args.get("pages"), scope, chosen, ctx))
        except _Refused as refused:
            return {"error": str(refused)}
        picked, in_order = await _pick(chosen, candidates, need, ctx.cfg.extract_max_pages)
        reader_cfg = get_reader_config()    # per call: the knobs are read from the environment
        passages = await _passages(chosen, picked, reader_cfg.input_chars)

        messages = [IN_ORDER_NOTE] if in_order else []
        results: list[ReaderResult] = []
        failed: list[tuple[int, int]] = []
        if passages:
            groups = batches(passages, reader_cfg.input_chars)
            results = await read_many(ctx.question, need, groups, chat_model=ctx.model, cfg=reader_cfg)
            failed = sorted({(p.doc_ref, p.page) for group, res in zip(groups, results) if res.error for p in group})
        else:
            messages.append(NO_TEXT_NOTE)

        result: dict[str, Any] = {
            "need": need, "documents": documents_listing(chosen), "facts": _facts(results),
            "not_read": _listed(set(candidates) - set(picked)), "failed": _listed(failed)}
        if messages:
            result["message"] = " ".join(messages)
        result = _cut(result, ctx.cfg.extract_output_chars)

        # What the model is given is what counts: facts cut from the result are neither evidence nor seen text.
        by_ref = {d.ref: d for d in chosen}
        for f in result["facts"]:
            ctx.evidence.append((by_ref[f["ref"]].doc.doc_id, f["page"], f["quote"]))
            ctx.seen_text.extend((f["fact"], f["quote"]))
        pages: dict[int, set[int]] = {}
        for f in result["facts"]:
            pages.setdefault(f["ref"], set()).add(f["page"])
        # For summarize() only: stripped before the model reads the result.
        result["_read"] = [{"docId": by_ref[ref].doc.doc_id, "docName": by_ref[ref].doc.name, "pages": sorted(p)}
                           for ref, p in sorted(pages.items())]
        result["_facts"] = len(result["facts"])
        return result

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        read, count = result["_read"], result["_facts"]

        def on(pages: list[int]) -> str:
            return f"page {pages[0]}" if len(pages) == 1 else "pages " + ", ".join(map(str, pages))

        if not count:
            text = "Found nothing for that need."
        else:
            noun = "fact" if count == 1 else "facts"
            where = on(read[0]["pages"]) if ctx.project is None else "; ".join(
                f"{on(d['pages'])} of {d['docName']}" for d in read)
            text = f"Found {count} {noun} on {where}."
        summary = {"ok": True, "chunk_count": None, "query": None, "summary_text": text}
        # As for read_document_pages: in document scope the reply's "(page N)" opens the open document; a
        # project reply names no single document (a fact's citation names its own).
        if ctx.project is None:
            return {**summary, "docId": ctx.doc.doc_id, "docName": ctx.doc.name,
                    "pages": read[0]["pages"] if read else []}
        return {**summary, "documents": read}


TOOL = _ExtractFromDocuments()
