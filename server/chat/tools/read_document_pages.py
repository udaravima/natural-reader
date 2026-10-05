"""read_document_pages: the exact text of whole pages of the open document
(v2.3 Task C). A search finds where something is; this reads it whole: a
table, a section, "what's on page 12" (plan journeys J3, J4, J9)."""
from __future__ import annotations

from typing import Any

from ...db import get_pool
from ...llm.types import ToolSpec
from ...services.extract import CHUNK_OVERLAP_CHARS, CONTINUATION_SUFFIX
from .scope import document_scope, documents_listing

PAGES_PER_CALL = 3
CUT_MARKER = " [cut: the rest is not shown]"
_MIN_OVERLAP = 16   # a continuation's overlap is far longer (CHUNK_OVERLAP_CHARS)

_SPEC = ToolSpec(
    name="read_document_pages",
    description=(
        "Read whole pages of the open document: the exact text, in reading order, up to 3 pages per "
        "call (first_page to last_page). Use it when the user names a page, or when a passage you "
        "found is cut short or points to a table, figure or section on its page."),
    parameters={
        "type": "object",
        "properties": {
            "first_page": {"type": "integer", "minimum": 1, "description": "The first page to read."},
            "last_page": {"type": "integer", "minimum": 1, "description": (
                "The last page to read (at most first_page + 2). Leave it out to read one page.")},
        },
        "required": ["first_page"],
    },
)


def _page_number(value: Any) -> int | None:
    """An integer page, also from "12" or 12.0 (small models send those)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def join_chunks(parts: list[tuple[str, bool]]) -> str:
    """A page's chunks in order, as (text, is_continuation). A continuation
    part (Task E's split) starts with the end of the part before it: joined
    without the repeat. Other chunks are separate blocks, never merged, even
    when their text happens to repeat."""
    out = ""
    for text, continuation in parts:
        if not out:
            out = text
            continue
        if continuation:
            # At most the chunker's overlap: in repetitive text a longer match
            # would swallow real characters.
            overlap = next((n for n in range(min(len(out), len(text), CHUNK_OVERLAP_CHARS),
                                             _MIN_OVERLAP - 1, -1) if out.endswith(text[:n])), 0)
            out += text[overlap:] if overlap else " " + text
        else:
            out += "\n\n" + text
    return out


def _page_count_sql() -> str:
    # A converted document's page_count may be unknown, and its chunks skip
    # empty pages: its doc_pages rows know every page.
    return ("SELECT GREATEST((SELECT max(page) FROM doc_chunks WHERE doc_id = %s), "
            "(SELECT max(page) FROM doc_pages WHERE doc_id = %s))")


class _ReadDocumentPages:
    name = "read_document_pages"
    spec = _SPEC
    reads_documents = True   # brings the data rule (server/chat/prompt.py)
    source = "document"

    def available(self, ctx) -> bool:
        return bool(document_scope(ctx))

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        scope = document_scope(ctx)
        if not scope:
            return {"error": "No indexed document is open."}
        doc = scope[0]
        first = _page_number(args.get("first_page"))
        if first is None:
            return {"error": "first_page is required: a page number."}
        last = first if args.get("last_page") is None else _page_number(args.get("last_page"))
        if last is None:
            return {"error": "last_page must be a page number."}
        if last < first:
            return {"error": "last_page must not be before first_page."}
        async with get_pool().connection() as conn:
            count = doc.page_count
            if not count:
                cur = await conn.execute(_page_count_sql(), (doc.doc_id, doc.doc_id))
                count = (await cur.fetchone())[0] or 0
            if first < 1 or last > count:
                return {"error": f"This document has pages 1-{count}."}
            wanted_last = last
            last = min(last, first + PAGES_PER_CALL - 1)
            cur = await conn.execute(
                "SELECT id, page, chunk_type, text FROM doc_chunks "
                "WHERE doc_id = %s AND page BETWEEN %s AND %s ORDER BY page, ord",
                (doc.doc_id, first, last))
            rows = await cur.fetchall()

        ref = 1   # the scope's first document; C2 passes a ref argument
        cap = ctx.cfg.read_pages_max_chars
        room = cap
        pages: list[dict[str, Any]] = []
        messages: list[str] = []
        cut: int | None = None
        for page in range(first, last + 1):
            if room <= 0:
                break
            chunks = [(cid, text or "", (ctype or "").endswith(CONTINUATION_SUFFIX))
                      for cid, p, ctype, text in rows if p == page]
            text = join_chunks([(t, c) for _, t, c in chunks])
            if len(text) > room:
                pages.append({"ref": ref, "page": page, "text": text[:room] + CUT_MARKER})
                cut = page
                break
            room -= len(text)
            pages.append({"ref": ref, "page": page, "text": text})
            if not text:
                messages.append(f"Page {page} has no text (it may be an image).")
            # Whole pages only: the model now has these chunks.
            ctx.shown.update(cid for cid, _, _ in chunks)

        # Where to continue, from what was actually returned (review I1).
        if cut is not None and len(pages) == 1:
            messages.append(f"Page {cut} is longer than {cap} characters: only its start is shown.")
            resume = cut + 1
        elif cut is not None:
            messages.append(f"Page {cut} was cut at the size limit: read it on its own for the rest.")
            resume = cut
        else:
            resume = pages[-1]["page"] + 1 if pages else first
        if resume <= wanted_last:
            span = f"page {resume}" if resume == wanted_last else f"pages {resume}-{wanted_last}"
            messages.append(f"Not read: {span}. Read from page {resume} next.")
        ctx.seen_text.extend(p["text"] for p in pages)   # cut pages too: the model has that text
        result: dict[str, Any] = {"documents": documents_listing(scope), "pages": pages}
        if messages:
            result["message"] = " ".join(messages)
        result["_pages"] = [p["page"] for p in pages]   # for summarize() only
        return result

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        read = result.get("_pages", [])
        what = (f"page {read[0]}" if len(read) == 1
                else f"pages {read[0]}-{read[-1]}" if read else "no pages")
        # docId lets the reply's "(page N)" citations open this document.
        return {"ok": True, "chunk_count": None, "query": None, "summary_text": f"Read {what}.",
                "docId": ctx.doc.doc_id, "docName": ctx.doc.name, "pages": read}


TOOL = _ReadDocumentPages()
