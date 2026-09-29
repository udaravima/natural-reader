# v2.3 Task C report: `read_document_pages`

## Change
- **`server/chat/tools/read_document_pages.py` (new).**
  - Arguments: `first_page`, plus an optional `last_page` (defaults to `first_page`). An int is accepted, and so are "12" and 12.0, since small models send those; a bool is refused.
  - At most 3 pages. A wider range reads the first three and says "read from page N next".
  - Out of range returns "This document has pages 1-N." N is `documents.page_count`, or `max(page)` of its chunks when unknown (converted documents).
  - A page is its chunks in `(page, ord)` order, joined by `join_chunks`. That removes a leading repeat of at least 16 characters, which is Task E's overlap, and otherwise joins with a blank line.
  - Capped at `CHAT_READ_PAGES_MAX_CHARS` (default 12,000, range 1,000-200,000), with `CUT_MARKER`. It stops after the cut page, and the message says to read fewer pages.
  - An empty page says it may be an image.
  - The chunk ids of whole pages read go into `ctx.shown`, so a later search lists those pages under `already_shown`. A cut page's ids don't.
  - Result: `{documents: [{ref, name}], pages: [{ref, page, text}], message?}`.
  - Summary: `{ok, chunk_count: None, query: None, summary_text: "Read pages 3-5.", docId, docName, pages}`. The SPA's generic `citationDoc` (Task B) makes its citations link with no SPA change.
  - `guidance()` and the description carry the "read the page" steering from the Task B review, and name no other tool.
- **`server/chat/tools/scope.py` (new).** `document_scope(ctx)` and `documents_listing(scope)` are shared by both document tools, so C2 widens the scope in one place.
- **`ToolContext` carries `cfg: ChatConfig`** instead of one field per setting. Task D's budgets will read it too.
- **Registry:** `[search_documents, read_document_pages, web_search]`.
- **Docs:** `.env.example`, CHAT_WITH_PDF, ARCHITECTURE, README, CHANGELOG Added.

## Tests
New `server/tests/test_chat_read_pages.py`, 18 tests; it failed at collection before the module existed. It covers:
- offered with the search, only for an indexed document;
- one page joined from 2 chunks, its ids marked shown, the exact summary;
- a range; more than 3 pages;
- 7 malformed or out-of-range cases;
- string and float page numbers;
- the cap and marker;
- `join_chunks` overlap;
- a converted (`page-md`) document, bounded by `max(page)`;
- an empty page;
- no other tool named;
- environment parsing.

`test_chat_prompt.py`'s truth matrix now includes `read_document_pages` in `TOOL_NAMES` and in the "all" offer.

## Suites
- backend: 686 passed;
- vitest: exit 0;
- eslint: clean.

## Fix round 1 — FIX_BASE 56b23af

- **I1: the continuation hint came from the request, not from what was returned.**
  - The message is now built after the loop:
    - a single page over the cap: "Page N is longer than C characters: only its start is shown." Resume at N+1.
    - a later page cut: "Page N was cut at the size limit." Resume at N.
    - otherwise resume after the last page returned.
  - Then "Not read: pages M-L. Read from page M next." when M ≤ the requested last page. That covers the >3 clamp, a page that ends exactly at the limit, and a cut.
  - Tests:
    - the >3 clamp;
    - a single page over the cap (with its ids kept out of `shown`, minor 7);
    - page 2 cut in 1-5;
    - a page that ends exactly at the limit.

    They failed before the fix.
- **Minor 1: `join_chunks` could merge real text.**
  - Overlap is removed only for a continuation part: `chunk_type` ends with `extract.CONTINUATION_SUFFIX` ("+cont"), the marker Task E's splitter must set on every part after a page's first.
  - Tests: a continuation is joined; a refrain between two blocks is kept.
- **Minor 2: the CTE's cost.** Only `(id, embedding)` is materialized; the top k are ranked, then the text is joined back. Order is deterministic (distance, id). The HNSW regression test still passes.
- **Minor 3.** `MAX_ROWS = 100`, commented as a cost bound (the search is exact). The over-fetch test covers 0, 15, 60 and 200 shown. The test docstring now says 300.
- **Minor 5.** For an unknown page count, the count is `GREATEST(max(chunk page), max(doc_pages page))`. Test: a trailing empty converted page reads as "no text", not out of range. A stale non-NULL count is not handled: `run_pipeline` keeps the PDF's count, which matches for converted PDFs.
- **Minor 6.** `ref` is named once with a comment; the test checks page ref equals documents ref.
- **Minor 8.** The CHAT_WITH_PDF loop diagram lists `read_document_pages`.
- **Contracts carried to later tasks (in the ledger):**
  - E sets "+cont" and round-trips `join_chunks(split(page)) == page`;
  - D's web guard keeps its own record of the text returned, not `ctx.shown`.

Suites:
- backend: 692 passed.
