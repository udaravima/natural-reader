### Task 6: Click a citation to open that page

**Why:** Replies cite "(page 3)" or "[2] (page 3)" as plain text. A1's journey 2 expected the citation to open that reader page, and no code for that exists.

**Change:**
- In the assistant message renderer (`src/components/ChatView.jsx`), for a reply whose saved `doc_context` names a document (prefetch notes or `search_document` results carry `docId`), turn `page N` and `(page N)` in the reply text into buttons.
- A button opens that document at page N:
  - switch to the reader;
  - if the document isn't open, open it through Task 5's route;
  - then go to page N.
- If opening fails (404 or 409), show a toast and stay in chat.
- Don't linkify replies with no document context.

**Tests:**
- The renderer links "(page 4)" only when a `docId` is present.
- A click calls `onOpenCitation(docId, 4)`.
- The App handler opens and navigates, and shows a toast on 404.

**Docs:** fix `LIBRARY.md` ("a chat citation always opens to the right page" is only true after this task).

