# Task 6 report — Click a citation to open that page (cloud session, controller-implemented)

BASE b56d571.

## R4 check: do saved replies carry the document's id?
- **The prefetch note does:** `server/chat/context.py:110` saves `{"kind": "prefetch", "docId", "docName", ...}` in `doc_context.notes`. It is streamed as `data-context` and returned by `GET /v1/chat/sessions/{id}` as `docContext`.
- **The `search_document` tool summary did not:** it held `{ok, chunk_count, query, summary_text}`. So, per R4, the server now adds `docId` and `docName` to that summary:
  - `Tool.summarize(args, result, ctx)` now receives the `ToolContext` (`server/chat/tools/__init__.py`). `web_search` accepts the argument and ignores it.
  - The model-facing `result` is unchanged. The summary is only saved on the message and streamed as `tool-output-available`; it is never sent back to the model in history.
  - Test: `server/tests/test_chat_tools.py` now expects `docId`/`docName` in the summary and asserts `"docId" not in run.result`.
- **Replies saved before this change:** their search_document summaries have no docId. Their citations stay plain unless the same reply also had a prefetch note.

## SPA
- **`src/lib/citations.js`:**
  - `citationDoc(message)` returns `{docId, docName}`, taken first from the prefetch note, then from a successful search_document summary; otherwise null.
  - `remarkCitations` is a remark plugin that splits text nodes on `/\(page\s+(\d+)\)|\bpage\s+(\d+)\b/gi` into link nodes carrying `data.hProperties['data-cite-page']`.
  - It skips `link`, `linkReference`, `inlineCode`, `code`, `html` and `definition`, so code spans and links the model wrote are left alone.
  - Markdown can't set attributes and raw HTML isn't enabled, so only citations found by the plugin get the attribute.
- **`ChatView.jsx`:**
  - New prop `onOpenCitation(docId, page, docName)`.
  - `MessageBubble` memoizes `citeDoc` for assistant messages when a handler exists.
  - `AssistantMarkdown` adds `remarkCitations` only when `citeDoc` is set. Its `a` renderer shows a `data-cite-page` link as a `<button>` with aria-label "Open {docName} at page N". The text stays exactly as written.
- **`src/lib/openDoc.js`** is pure orchestration over the deps App passes in:
  - `openServerDoc(deps, docId, name)` is Task 5's open flow, moved here. It fetches, awaits processFile, and shows the reader only when that succeeds; otherwise it toasts and returns null.
  - `openCitation(deps, {docId, page, docName, openDocId, numPages})`:
    - if the cited doc is the open one (App's `currentDocId`, the open file's hash), it shows the reader and goes to the page;
    - otherwise it calls openServerDoc, then goes to the page once the file has opened and its saved reading position has been applied, clamping to the page count it returned;
    - a 404 or 409 gives the toast and no view change.
- **`App.jsx`:** `openDocDeps`, `handleOpenLibraryDoc` and `handleOpenCitation`, passed to LibraryPage and ChatView. `goToPage` sets the page and clears the sentence index.

## Tests
- `src/components/ChatView.citations.test.jsx` (8): these tests existed before the implementation, and 3 failed on it.
  - Links "(page 4)" and "page 7" with a prefetch note; clicking calls `onOpenCitation(DOC, 4, 'Thesis.pdf')`, and the text is unchanged.
  - Links from a search_document summary, including "(Page 3)".
  - Links inside list items and bold text.
  - No links without document context, on a failed search_document call, inside code or model-written links, in user messages, or without a handler.
- `src/lib/citations.test.js` (3): `citationDoc`.
- `src/lib/openDoc.test.js` (8):
  - openServerDoc: success; a refusal gives a toast and no reader; a load failure gives a toast.
  - openCitation: an already-open doc gives no fetch and goes to the page; a doc that isn't open is fetched first, and the page is set after processFile; the page is clamped to 1..numPages; a 404 gives a toast with no reader and no navigation; a missing name falls back to the "Cited document" placeholder.
- **Suites:**
  - backend: 591 passed;
  - frontend: 374 passed;
  - eslint: 1 error (baseline useAuth.js:64).

## Docs
- `LIBRARY.md`: the "(Citations are plain text today…)" sentence is replaced.
- `USER_GUIDE.md`: a "Citations" paragraph in the Chat section.
- `CHANGELOG`: an `### Added` line.

## Known limits
- The "already open" check trusts `currentDocId`. The ledger notes that it can be stale when different bytes are opened under the same name (pre-existing, from Task 5's re-review).
- `applySavedProgress` restores the saved sentence index 500 ms after opening. On a document with saved progress, that index may belong to another page than the cited one. It only moves the highlight/playback position; it doesn't change the page.
- A citation of a page beyond the document is clamped to its last page.

---

## Fix round 1 — FIX_BASE 8cacf09 (Task 6 head was 4018753; Task 7 commits sit between, untouched by this fix)

- **Important: every streaming token remounted every reply's markdown.** `AssistantMarkdown`'s `components` now depends only on `[darkMode, citeDocId, citeDocName]`. The click handler is read through `openCitationRef`, which a `useEffect` updates.
  - `MessageBubble`'s `citeDoc` memo depends on `canCite` (a boolean), not the handler's identity.
  - The plugin arrays are module constants.
  - So an App handler that is new on every render no longer rebuilds the renderer.
  - Test: rerender ChatView with a new `onOpenCitation`. The same button DOM node survives, and a click calls the latest handler. It was red before the fix.
- **Minor: "page 0" rendered as a new-tab link.** `splitText` skips pages below 1 and leaves them as text, and the `a` renderer checks `citePage != null`. Test: no button for "page 0" and no link named "page 0"; a hand-written `[page 4](#page-4)` gives no button.
- **Minor: the saved reading position took over the sentence.** `applySavedProgress`'s 500 ms timer now restores the saved sentence only if the reader is still on the saved page (`prevPageRef.current === savedProgress.page`). Two tests in `usePdfEngine.openServerDoc.test.js`:
  - a page set right after opening keeps sentence -1 (red before the fix);
  - without a page change, the saved sentence is still restored.
- **Minor: a citation click stops a streaming reply.** This is documented in USER_GUIDE. I checked that leaving chat calls `chatStopStream()` (App.jsx:333-336).
- **Minor: USER_GUIDE sentence placement.** The "Only one reply…" sentence is back after "Still generating…".
- **Minor: test gaps.** Added a 409 test at the `openCitation` level, plus the hand-written `#page` link case above.
- **Not changed:** "page N" in a reply that used both web search and the document always opens the document. This matches the brief and is noted for the walk.

Covering runs:
- `vitest run src/components/ChatView.citations.test.jsx src/lib/openDoc.test.js src/hooks/` all pass.
- Full frontend: 385 passed.
- eslint: 1 error (baseline).
