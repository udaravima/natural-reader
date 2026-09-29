# Task 5 report — Open a Library document (cloud session, controller-implemented)

BASE d0f79fe. Implemented by the controller (no other implementer in the tree).

## Backend
- `GET /v1/docs/{doc_id}/file` (`server/routers/docs.py`, `get_document_file`):
  - The gate is `_require_doc_reader`: the `reader` capability, then `assert_can_read_doc`, the same `can_read` SQL predicate as `GET /v1/docs/{id}`. Unreadable → 404.
  - The name is `_DISPLAY_NAME` + `_ENTRY_JOIN`, the same expression the list and status use. It is the caller's own entry name. A share recipient's entry carries the name the sharer gave. For a project-only row it is the filer's entry name; otherwise the canonical name.
  - Media types: pdf → `application/pdf`, text → `text/plain; charset=utf-8`, markdown → `text/markdown; charset=utf-8`.
  - `FileResponse(..., content_disposition_type="inline")`. Starlette writes `filename="…"` for plain names and RFC 6266 `filename*=utf-8''…` for names that need encoding, including spaces and quotes. So a quote in a name can't break the header. This differs from the plan's literal `filename="<name>"`, deliberately.
  - `bytes_path` NULL, or the file gone from disk → `409 bytes_missing` with the existing message "Upload the file again first."
- Tests: `server/tests/test_docs_file.py` has 12 tests:
  - owner 200 with bytes, type and disposition;
  - share recipient 200 under the sharer's name;
  - the name comes from the caller's own entry;
  - project owner and member 200;
  - stranger 404;
  - unknown doc 404;
  - unverified pre-A1 holder 404;
  - 409 for NULL bytes_path and for a file missing on disk;
  - md/txt MIME types;
  - a hostile name is encoded;
  - no reader capability → 403.
  They were red before the route existed (9 failing with 404) and are green after.

## SPA
- `src/lib/serverDocFile.js` `fetchDocFile(host, port, docId, name)` returns a `File` named with the Library name. Its type is set from the response's Content-Type (pdf/markdown/plain), so `detectFileType` works even for a name without an extension. A refusal throws `Error(describeRefusal(res))`: 404 gives the no-access notice, 409 the server message.
- `usePdfEngine.processFile` now returns a Promise: true once the document is open, false on a load error. Existing callers ignore it. Task 6 will await it before going to a page.
- `App.jsx` `openServerDoc(docId, name)`: it fetches the file. A failure shows a toast and stays in the Library. On success it switches to the reader, then `processFile(file)` saves the file to the local library under the Task 4 owner and opens it. This is the one helper Task 6 must reuse.
- `LibraryPage` has an **Open** button on every row (`onOpen(doc)` prop). Only one open runs at a time: the buttons are disabled while an open is in flight. There is no button without `onOpen`.
- Tests:
  - `src/lib/serverDocFile.test.js` (6);
  - `src/components/library/LibraryPage.open.test.jsx` (5);
  - `src/hooks/usePdfEngine.openServerDoc.test.js` (2): the fetched File opens through processFile with the right name and type and is saved locally.

  Under R3, the App handler itself isn't mounted: it is a thin join of `fetchDocFile` and `processFile`, each tested.

## Docs
- `LIBRARY.md`:
  - Sharing: what a recipient can do now;
  - Projects: members can Open;
  - the API table: new route row;
  - Frontend: the Open button.
- `USER_GUIDE.md`: new "Library: documents on the server" section.
- `CHANGELOG`: an `### Added` line.

## Suites
- backend: 591 passed (579 + 12);
- frontend: 346 passed;
- eslint: 1 error (baseline, useAuth.js:64).

## Known limits
- Opening overwrites a local-library record of the same name for this user. The local library is a cache keyed by name, so this is the same as picking a same-named file.
- A 409 tells a share recipient to "Upload the file again first", which they can do only if they have the file.
