# A1 running-app walk (content vs library entries)

Date: 2026-09-28. Branch `development` at `2d394f1` (A1 plus C0 and C1 on top).

## Setup

- A second backend with auth **on**: `AUTH_ENABLED=true COOKIE_SECURE=false HOST=127.0.0.1 PORT=8001`. It had no OIDC settings, so it never reached a real identity provider.
- Two throwaway users, `walk-a@example.com` and `walk-b@example.com`, with the `reader` and `chat` capabilities. Each got a DB session row; the browser switched between them by setting the `nr_session` cookie.
- The SPA ran on a second Vite dev server proxying `/v1` to `:8001`, driven with Playwright (Chromium).
- One browser profile served both users. That matters for the "welcome screen" finding below.
- Ollama with `nomic-embed-text` for embeddings. Chat used `llama3.2:3b` and `qwen3.5`, on CPU only.
- Documents: a generated 2-page PDF (`a1-walk-kestrel.pdf`) and two Markdown files.

## Journeys (plan Task 13, Step 6)

| # | Control | Seen | Result |
|---|---|---|---|
| 1 | A: Reader → Choose File → **Index**. B: same file → **Index** | A: Indexed; the server logged "Extracted 2 chunks … Indexed 2 chunks". B: "Already indexed — added to your library." at once. The server logged `entry.added … via=upload` and nothing was extracted or embedded again. | ✅ |
| 2 | New `.md` → **Index** → chat | Index → Extracting (0.1 s) → Indexed (2.1 s), with the toast "Indexed 2 chunks." A 6-chunk doc finished between two 2 s polls, so "Indexing n/m" never showed. Chat said "Used 4 passages from a1-walk-lighthouse.md", and `qwen3.5` answered correctly, quoting "[3] (page 1)". | ◐ See findings 1 and 2 |
| 3 | B: Library → **Remove from my library** → **Confirm remove** | The confirmation read "Remove from your library? People and projects that have it keep their copies." The row disappeared for B, and A still had it (`GET /v1/docs` as A). | ✅ |
| 4 | B shares with A **via curl** (`PUT /v1/docs/{id}/shares/{A}` returned 204) | A's Library showed the row "shared by Walk B". A shared doc has no "+ project" picker for A. | ✅ API-only until A0 |
| 5 | A: Library → **New project** → **Create**, then the row's "+ project" → the project. B added as a member **via curl** | A saw the chip with its × ("Remove … from Walk project"). B saw the row "via project" with no remove control and no chip ×. | ✅ Member management is API-only until A0 |
| 6 | Reader → **Convert**, first while A and B both hold the doc, then with A alone and the doc in A's project | Both held it: "Other people also use this document, so it can't be changed here. Ask an admin." A alone with the doc in the project: "This document is in a project, so changing it would change it for the project too. Remove it from the project first, or ask an admin." | ✅ |

## Findings

1. **The Index button can stay on "Index" after the server has finished indexing (intermittent).**
   - Seen on the first open of a new Markdown file: the server indexed it in about 1 s, but the button still read "Index" 30 s later.
   - No status `GET` was ever sent for that doc on open. So the doc's hash wasn't available when the "document changed" effect ran, `currentDocId` stayed empty, and the progress the poller wrote under the real hash was never read.
   - The loaders set the file name before `saveBook` finishes writing the file to IndexedDB (`usePdfEngine.js` 268/273 and 248/303), and the effect reads the bytes back from IndexedDB to hash them. A lost race leaves the button dead for that document.
   - Reopening the doc fixed it, and a second fresh file didn't reproduce it.
2. **Chat citations aren't clickable.**
   - The reply cites "[3] (page 1)" as plain text, and there's no control that opens the reader at that page.
   - The page numbers are right: server extraction matches the reader's segmentation (the parity fixtures).
   - The plan's journey 2 expected "the citation opens the right reader page". No code for that exists, so it was never built rather than broken.
3. **A shared document, or one seen through a project, can't be opened or asked about unless you have the file yourself.**
   - The Library lists it, but a Library row doesn't open anything, and no API route serves the stored bytes.
   - Chat about a document works through the Reader's open document.
   - So today, sharing and projects decide who *sees the document in their Library list*. They don't give anyone a way to read it or chat about it in the app.
   - `LibraryPage.jsx` says so ("No chat wiring — that's Phase 1").
4. **The Reader's welcome-screen "Your Library" list is per-browser, not per-user.**
   - It's kept in IndexedDB, so on a shared browser the next person to sign in sees the previous user's recent files, and can open them, because the bytes are stored locally.
5. **Console noise found along the way:**
   - pdf.js "Cannot use the same canvas during multiple render() operations" (`usePdfEngine.js:128`), on nearly every PDF open;
   - a nested `<button>` inside `<button>` in `WelcomeScreen` (the recent-book row contains a remove button);
   - a revoked `blob:` URL (`ERR_FILE_NOT_FOUND`) when reopening a PDF.
6. **`llama3.2:3b` wrote its `search_document` call as JSON text, and that text became the reply.** This is the same model behaviour the C1 walk recorded, and it's the most visible failure a user of a small model sees.

## Can the user do what they asked for, start to finish, without curl?

- Journeys 1, 2, 3, 5 (filing and viewing) and 6: yes.
- Sharing a single document (journey 4) and adding project members (journey 5): API-only until the project-management UI (A0).
- A person who receives a document by share or project can see it listed but can't read it or ask about it in the app (finding 3).
