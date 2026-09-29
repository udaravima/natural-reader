### Task 5: Open a Library document in the reader (shared and project documents included)

**Why:** A person who receives a document by share or project sees it listed but can't read or ask about it: no route serves stored bytes, and Library rows don't open anything. Today sharing only changes a list.

**Change (backend):**
- Add `GET /v1/docs/{doc_id}/file`:
  - It requires the `reader` capability and uses the same `can_read` SQL predicate as `GET /v1/docs/{id}`. Unreadable → 404.
  - It streams the stored bytes (`FileResponse`) with the stored MIME type and `Content-Disposition: inline; filename="<the caller's own entry name, or the name the sharer gave>"`.
  - Stored bytes missing → `409 bytes_missing`, reusing the existing refusal and message.

**Change (SPA):**
- Each Library row gets an **Open** button.
- It fetches the bytes, saves them to the local library under the Task 4 owner, and opens them in the reader, the same as picking the file.
- The existing Index/Chat flow then just works, because the hash equals the doc id.

**Tests:**
- Route: owner 200; share recipient 200; project member 200; stranger 404; unverified pre-A1 holder 404; missing bytes 409; the filename comes from the caller's entry.
- SPA: Open loads the reader with the right file name and type.

**Docs:**
- `LIBRARY.md` "Sharing" and "Projects": say what a recipient can now do.
- USER_GUIDE: the Open button.

