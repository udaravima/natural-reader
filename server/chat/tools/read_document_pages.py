"""read_document_pages: the exact text of whole pages of a document in scope
(v2.3 Task C): the open document, or one of a project chat's documents, named
by `document` (C2.1). A search finds where something is; this reads it whole:
a table, a section, "what's on page 12" (plan journeys J3, J4, J9).

It also owns how a page's text is put together from its chunks (`page_chunks`,
`page_texts`), which extract_from_documents shares: code is shown fenced, so a
command stays a command and can't be mistaken for the prose around it."""
from __future__ import annotations

import re
from typing import Any

from ...db import get_pool
from ...llm.types import ToolSpec
from ...services.doc_search import ReadableDoc
from ...services.extract import CHUNK_OVERLAP_CHARS, CODE, CONTINUATION_SUFFIX
from ..project_scope import ScopeDoc
from .scope import documents_listing, named_in_project, scope_docs

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
            "document": {"type": ["integer", "string"], "description": (
                "Which document: its ref number or its name, as results list them. Needed when there are "
                "several documents; leave it out for the open document.")},
            "first_page": {"type": "integer", "minimum": 1, "description": "The first page to read."},
            "last_page": {"type": "integer", "minimum": 1, "description": (
                "The last page to read (at most first_page + 2). Leave it out to read one page.")},
        },
        "required": ["first_page"],
    },
)


def page_number(value: Any) -> int | None:
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


async def page_count(conn, doc: ReadableDoc) -> int:
    """The document's pages: its page_count, else (unknown) what its chunks and doc_pages rows show."""
    if doc.page_count:
        return doc.page_count
    cur = await conn.execute(_page_count_sql(), (doc.doc_id, doc.doc_id))
    return (await cur.fetchone())[0] or 0


def _fenced(code: str) -> str:
    """A code block as Markdown: a fence of backticks one longer than the longest run inside the code (at least
    three), on its own lines, so a ``` in the code can't close ours."""
    code = code.strip("\n")
    if not code.strip():
        return ""
    longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{code}\n{ticks}"


def _page_text(chunks: list[tuple[str, str]]) -> str:
    """A page's chunks in order, as (chunk_type, text), joined by `join_chunks`. A code chunk and the
    continuations after it are joined first (a split command is one block), then fenced once; a code chunk
    after another starts a new block, and so a new fence."""
    blocks: list[tuple[str, bool]] = []
    code: list[tuple[str, bool]] = []

    def close_code() -> None:
        if code:
            fenced = _fenced(join_chunks(code))
            if fenced:
                blocks.append((fenced, False))
            code.clear()

    for chunk_type, text in chunks:
        continuation = chunk_type.endswith(CONTINUATION_SUFFIX)
        if chunk_type in (CODE, CODE + CONTINUATION_SUFFIX):
            if code and not continuation:
                close_code()
            code.append((text, continuation))
        else:
            close_code()
            blocks.append((text, continuation))
    close_code()
    return join_chunks(blocks)


async def page_chunks(conn, doc: ReadableDoc, first: int, last: int) -> list[tuple[int, str, list[int]]]:
    """(page, text, chunk ids) for every page from `first` to `last`, in order: "" and no ids for a page with no
    chunks. The text is the page's chunks joined (see `join_chunks`), code fenced. The ids are what the caller
    has once it shows the text."""
    cur = await conn.execute(
        "SELECT id, page, chunk_type, text FROM doc_chunks "
        "WHERE doc_id = %s AND page BETWEEN %s AND %s ORDER BY page, ord",
        (doc.doc_id, first, last))
    by_page: dict[int, list[tuple[int, str, str]]] = {}
    for cid, page, chunk_type, text in await cur.fetchall():
        by_page.setdefault(page, []).append((cid, chunk_type or "", text or ""))
    return [(page, _page_text([(t, x) for _, t, x in by_page.get(page, [])]),
             [cid for cid, _, _ in by_page.get(page, [])])
            for page in range(first, last + 1)]


async def page_texts(conn, doc: ReadableDoc, first: int, last: int) -> list[tuple[int, str]]:
    """`page_chunks` without the ids."""
    return [(page, text) for page, text, _ in await page_chunks(conn, doc, first, last)]


class _ReadDocumentPages:
    name = "read_document_pages"
    spec = _SPEC
    reads_documents = True   # brings the data rule (server/chat/prompt.py)
    source = "document"

    def available(self, ctx) -> bool:
        return bool(scope_docs(ctx))

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        scope = scope_docs(ctx)
        if not scope:
            return {"error": "No indexed document is open."}
        chosen = _chosen(scope, args.get("document"), ctx)
        if isinstance(chosen, dict):
            return chosen
        doc, ref = chosen.doc, chosen.ref
        first = page_number(args.get("first_page"))
        if first is None:
            return {"error": "first_page is required: a page number."}
        last = first if args.get("last_page") is None else page_number(args.get("last_page"))
        if last is None:
            return {"error": "last_page must be a page number."}
        if last < first:
            return {"error": "last_page must not be before first_page."}
        async with get_pool().connection() as conn:
            count = await page_count(conn, doc)
            if first < 1 or last > count:
                return {"error": f"This document has pages 1-{count}."}
            wanted_last = last
            last = min(last, first + PAGES_PER_CALL - 1)
            rows = await page_chunks(conn, doc, first, last)

        cap = ctx.cfg.read_pages_max_chars
        room = cap
        pages: list[dict[str, Any]] = []
        messages: list[str] = []
        cut: int | None = None
        for page, text, chunk_ids in rows:
            if room <= 0:
                break
            if len(text) > room:
                pages.append({"ref": ref, "page": page, "text": text[:room] + CUT_MARKER})
                cut = page
                break
            room -= len(text)
            pages.append({"ref": ref, "page": page, "text": text})
            if not text:
                messages.append(f"Page {page} has no text (it may be an image).")
            # Whole pages only: the model now has these chunks.
            ctx.shown.update(chunk_ids)

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
        ctx.evidence.extend((doc.doc_id, p["page"], p["text"]) for p in pages)
        # The document these pages are from (in a project, not all of them).
        result: dict[str, Any] = {"documents": documents_listing([chosen]), "pages": pages}
        if messages:
            result["message"] = " ".join(messages)
        result["_pages"] = [p["page"] for p in pages]   # for summarize() only
        result["_doc"] = {"docId": doc.doc_id, "docName": doc.name}
        return result

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        read = result.get("_pages", [])
        what = (f"page {read[0]}" if len(read) == 1
                else f"pages {read[0]}-{read[-1]}" if read else "no pages")
        summary = {"ok": True, "chunk_count": None, "query": None, "summary_text": f"Read {what}."}
        # docId lets the reply's "(page N)" citations open this document. A
        # project reply names no single document (its other citations may be
        # to other documents): the document read is saved under its own key.
        if ctx.project is None:
            return {**summary, **result["_doc"], "pages": read}
        return {**summary, "document": result["_doc"], "pages": read}


def _chosen(scope: list[ScopeDoc], value: Any, ctx) -> ScopeDoc | dict[str, str]:
    """The ScopeDoc `document` names, or the error the model reads. In document
    scope it is the open document whatever `document` says: a small model
    fills the argument in with a near-name, and one wrong guess must not cost
    it a round. In a project, leaving it out means the document open in the reader, if it is one of the
    project's searchable documents, else it is an error."""
    if ctx.project is None:
        return scope[0]
    return named_in_project(scope, value, ctx, open_if_missing=True)


TOOL = _ReadDocumentPages()
