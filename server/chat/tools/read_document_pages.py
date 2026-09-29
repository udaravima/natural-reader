"""read_document_pages: the exact text of whole pages of the open document
(v2.3 Task C). A search finds where something is; this reads it whole: a
table, a section, "what's on page 12" (plan journeys J3, J4, J9)."""
from __future__ import annotations

from typing import Any

from ...db import get_pool
from ...llm.types import ToolSpec
from .scope import document_scope, documents_listing

PAGES_PER_CALL = 3
CUT_MARKER = " [cut: the rest is not shown]"
_MIN_OVERLAP = 16   # shorter repeats between chunks are coincidence, not overlap

_SPEC = ToolSpec(
    name="read_document_pages",
    description=(
        "Read whole pages of the open document: the exact text, in reading order. Up to 3 "
        "pages per call, from first_page to last_page. Use it when the user asks about a "
        "particular page, or when a passage you found is cut short or points to a table, "
        "figure or section on its page."),
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


def join_chunks(texts: list[str]) -> str:
    """A page's chunks in order. A chunk that starts with the end of the one
    before (sub-page chunks overlap, Task E) is joined without the repeat."""
    out = ""
    for text in texts:
        if not out:
            out = text
            continue
        overlap = next((n for n in range(min(len(out), len(text)), _MIN_OVERLAP - 1, -1)
                        if out.endswith(text[:n])), 0)
        out = out + text[overlap:] if overlap else out + "\n\n" + text
    return out


class _ReadDocumentPages:
    name = "read_document_pages"
    spec = _SPEC
    reads_documents = True   # brings the grounding and citation rules (server/chat/prompt.py)

    def available(self, ctx) -> bool:
        return bool(document_scope(ctx))

    def guidance(self, ctx) -> str:
        return ("read whole pages of the open document, exact text, up to 3 pages per call. Use it "
                "when the user asks what a page says, or when a passage you found is cut short or "
                "points to a table, figure or section on its page.")

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
            if not count:   # a converted document's page count isn't recorded
                cur = await conn.execute("SELECT max(page) FROM doc_chunks WHERE doc_id = %s", (doc.doc_id,))
                count = (await cur.fetchone())[0] or 0
            if first < 1 or last > count:
                return {"error": f"This document has pages 1-{count}."}
            messages = []
            if last - first + 1 > PAGES_PER_CALL:
                last = first + PAGES_PER_CALL - 1
                messages.append(f"{PAGES_PER_CALL} pages at most per call: read from page {last + 1} "
                                "next if you need more.")
            cur = await conn.execute(
                "SELECT id, page, text FROM doc_chunks WHERE doc_id = %s AND page BETWEEN %s AND %s "
                "ORDER BY page, ord", (doc.doc_id, first, last))
            rows = await cur.fetchall()

        room = ctx.cfg.read_pages_max_chars
        pages: list[dict[str, Any]] = []
        for page in range(first, last + 1):
            if room <= 0:
                break
            chunks = [(cid, text or "") for cid, p, text in rows if p == page]
            text = join_chunks([t for _, t in chunks])
            if len(text) > room:
                pages.append({"ref": 1, "page": page, "text": text[:room] + CUT_MARKER})
                messages.append(f"Cut at {ctx.cfg.read_pages_max_chars} characters: read fewer pages, "
                                "or one page at a time.")
                break
            room -= len(text)
            pages.append({"ref": 1, "page": page, "text": text})
            if not text:
                messages.append(f"Page {page} has no text (it may be an image).")
            # Whole pages only: the model now has these chunks.
            ctx.shown.update(cid for cid, _ in chunks)
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
