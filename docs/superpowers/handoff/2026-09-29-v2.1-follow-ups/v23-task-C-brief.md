### Task C: `read_document_pages`: read whole pages once found

**Change:**
- A new tool `read_document_pages(first_page, last_page)`:
  - returns the reader's full text for up to 3 pages, joining the page's chunks in order and dropping overlaps;
  - is capped at `CHAT_READ_PAGES_MAX_CHARS` (default 12,000) with a marker;
  - rejects out-of-range pages with the page count;
  - is offered with `search_document` (the same availability rule: an indexed, readable document);
  - carries `docId` in its summary.
- This makes J3, J4 and J9 exact, and removes the 1,500-character blind spot for anything the model decides to read.

**Tests:**
- ranges;
- the cap;
- overlap removal;
- out-of-range handling;
- converted (docling) documents read their Markdown pages.


**Carried from the Task B review:** the "read the page" steering lives in this tool's own guidance and description (neither may name another tool). **Also under review:** the Task B follow-up commit 42a8dce (exact per-document search_chunks, the already_shown collapse).
