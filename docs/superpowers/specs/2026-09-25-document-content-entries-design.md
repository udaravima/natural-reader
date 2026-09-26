# Document content vs library entries (A1)

**Date:** 2026-09-25
**Status:** Design, awaiting review (external review findings F1–F5 and minors folded in)
**Branch:** `development`
**Order:** A1 (this) → A0 ([projects, roles & directory](2026-09-25-projects-roles-directory-design.md)) → C1 → C2
**Open-source rule:** nothing deployment-specific is hardcoded; every new knob is an env var in `.env.example` with a safe default.

## 1. Why

A document's ID is the SHA-256 of its bytes, and a `documents` row belongs to whoever uploaded that file first. Three problems follow:

1. **The real author gets blocked.** When someone else uploaded the same file first, the author's `POST /v1/docs` returns 404 (`server/routers/docs.py` `register_document`). Their own file "does not exist" for them.
2. **One uploader can remove a document for everyone.** `DELETE /v1/docs/{id}` cascades through `project_documents` and `doc_grants`. The first uploader, who is not necessarily the author, silently removes the document from every project and share.
3. **The server trusts the client's hash.** The browser computes the ID (`src/utils/docHash.js`). The normal Index path uploads browser-extracted chunks (`src/App.jsx` → `POST /{id}/chunks`), so the server never sees the bytes. Today the 404 in (1) is the only thing preventing: knowing an ID = read access, and a second "uploader" poisoning the chunks.

GitLab's answer, adopted here: **content belongs to nobody; placements belong to people and projects.** The content is stored once, verified by the server, and shared.

## 2. User journeys (each must work from the UI, and is checked in the running app)

1. Upload a file someone else already uploaded: it's added to my library **instantly**, already indexed, with no re-processing.
2. Upload a new file: the server verifies and indexes it; progress shows as today.
3. Remove a document from my library: only my copy goes. Projects and other people keep theirs.
4. Share a document I uploaded with one person: it appears in their library marked "shared by …". **A1 ships the API only.** The share dialog needs A0's people picker, so until A0 ships, sharing is API-only. A1's summary and docs must say so.
5. File my document into a project: after that, only the project (A0: Maintainer+; until then, the project owner) can remove it there.
6. Try to re-convert a document others also use: refused with a clear notice that says why and what to do.

## 3. Model

| Thing | Table | Belongs to | Holds |
|---|---|---|---|
| **Content** | `documents` (kept; keyed by `doc_id` = SHA-256 computed by the **server**) | nobody | bytes (on disk), canonical `file_name`, type, size, pages, chunks + embeddings, conversion + indexing state, `extracted_by` |
| **Library entry** | `library_entries` (new) | one user | "I have this": optional personal `file_name`, personal `tags`, `added_at`, `added_via` (`upload` \| `shared`), `shared_by` |
| **Project placement** | `project_documents` (from C0) | the project | "this project has this content", `added_by`, `added_at` |

- **Read access** (`readable_docs_where`, still one predicate with `readable_docs_params`): the user has a library entry for the content, **or** the content is placed in a project where the user is owner/member. The grants branch disappears: shares become entries.
- **Which project links a row shows** (`_doc_projects_sql`): **only projects the caller can see**, for everyone. C0 let the doc's owner see links to projects they had left, so they could withdraw them. That rule keyed on `documents.user_id`, which this migration drops, and its reason is gone: under A1 the uploader can't unlink from a project anyway. Showing invisible projects would only leak their names.
- **Chat pins, sessions and the reader** keep using `doc_id`, which is still the content hash, so nothing changes there. Chat references documents only through JSONB (`doc_context`, pins), with no foreign key. After content is garbage-collected they dangle exactly as they do today after an owner deletes, and the reader already handles a missing doc.

### Garbage collection

Content (row, chunks, pages, bytes file) is deleted when no entry and no placement references it. One helper does it: `gc_content_if_orphaned(conn, doc_id)`, in a new `server/services/doc_content.py`.

**Every path that removes entries or placements collects the affected `doc_id`s first and GCs each one afterwards.** A SQL `ON DELETE CASCADE` never runs app code, so any cascade that removes an entry or placement skips GC unless its caller enumerates the docs first. The paths:

| Path | What it removes | How the docs are found |
|---|---|---|
| `DELETE /v1/docs/{id}` | my entry | the one doc |
| revoke a share | the recipient's entry | the one doc |
| unlink from a project | a placement | the one doc |
| delete a project | all its placements (cascade) | its placements, read before the delete |
| **admin deletes a user** (`admin.py` `delete_user`) | the user's entries (cascade) **and**, until A0, every placement in projects they own (`projects.owner_user_id ON DELETE CASCADE`) | the user's entries plus the placements of their owned projects, read before the delete. This replaces today's `pdf_path` pre-collection in the same function. |

- **Safety net:** at startup, one query removes any content with no entry and no placement, logged at WARNING with a count. It catches a path someone adds later and forgets to wire. It's safe on existing data: `documents.user_id` has been `NOT NULL` since migration 005, so the backfill gives every document an entry.
- **Side effect worth knowing:** today, deleting a user deletes every document they uploaded, including ones others were granted. Under A1 that content survives as long as anyone else holds an entry or a placement.

**Races.** GC takes `SELECT … FOR UPDATE` on the content row. The upload path is ordered so that a GC and an upload of the same hash can't corrupt each other:

1. **Dedupe hit:** the upload locks the content row `FOR SHARE`, then inserts the entry, in the same transaction. If the row has vanished because a GC won, the upload retries **once** as new content. The caller never sees a 5xx from this race.
2. **Bytes file:** GC unlinks the file **while holding the row lock, before commit**. An upload writes to a temp file and moves it into place only **after** its `INSERT` succeeds, and that insert waits for GC's commit. So GC can never delete a file a newer upload has just written.
3. **Self-heal:** if a GC transaction fails after its unlink, the row remains but its bytes are missing. The next upload of that hash, which carries the bytes anyway, finds the file missing and writes it.

## 4. Proof of possession and integrity

- **Every registration uploads the file bytes.** The server computes the SHA-256; its value is the ID. A client-supplied ID is only a hint. If the hint disagrees, the server's hash wins and a WARNING is logged.
- **Hash matches existing content → no rework** (user decision). The caller gets an entry and a 200 `{doc_id, dedup: true, state}`. Nothing is re-extracted or re-embedded.
- **New content:**
  - the bytes are stored;
  - an entry is created;
  - a background job extracts and embeds the text, and the response is 202 `{doc_id, state: "extracting"}`.
  - Two concurrent uploads of the same new file: `INSERT … ON CONFLICT DO NOTHING` makes the second one a dedupe hit, and the existing per-doc lock serializes the job. That lock (`_doc_job_locks`) is per process, so two server workers could both extract the same new hash. They converge through `UNIQUE(doc_id)` and chunk upserts: wasted work, not wrong data, same as today.
- **The server extracts the text itself** from the verified bytes. Client-supplied chunks are **never** accepted again: `POST /{id}/chunks` is removed. Dedupe hands later uploaders the first uploader's derived data, so this is what makes "no rework" safe from poisoning. (Precedent: Dropbox 2011, "Dropship": client-trusted hashes let anyone fetch any file.)

### Extraction must match the reader exactly

A citation carries a chunk's `page`, and the reader jumps to its **own** page with that number. If the server splits text differently from the browser, the highlight lands on the wrong page. So server extraction replicates the reader's rules in `src/hooks/usePdfEngine.js`. It does not approximate them:

| Type | Server extraction | Chunks | Reader rule it must match |
|---|---|---|---|
| PDF | `pypdfium2`, Apache-2.0/BSD, MIT-compatible. Already installed via Docling; add it to `requirements.txt` explicitly. **Not** PyMuPDF (AGPL). | one per non-empty page, `chunk_type='page'`, `page` = PDF page number | pdf.js page numbering (`extractAllChunks`) |
| Text | decode UTF-8; collapse whitespace; split on `(?<=[.!?])\s+`; drop sentences of ≤ 5 characters; group 40 sentences per page | one per page, `chunk_type='page'` | `segmentSentences` + `paginateSentences`, `SENTENCES_PER_TEXT_PAGE = 40` |
| Markdown | parse **CommonMark + GFM into mdast**, the same model the reader uses (micromark + GFM); take top-level children; skip `definition` and `footnoteDefinition`; code blocks count 0 sentences and make no chunk; pack blocks into pages greedily, closing a page when the next block would push it past 40 sentences | one per non-code block, `chunk_type='block'`, `page` = that packed page | `segmentMarkdown` + `paginateMarkdownBlocks` + `splitSentences` |
| Docling conversion | unchanged (already server-side) | one per converted page, `'page-md'` | — |

- **Markdown is not "split on blank lines".** A loose list is one mdast node that spans blank lines, so a blank-line splitter would put the highlight on the wrong page. The plan picks a Python CommonMark + GFM parser whose top-level nodes match mdast, and the parity fixtures prove it.
- **Parity fixtures** (committed): PDFs, `.txt`, and `.md` files covering loose lists, tables, code fences, footnotes and link definitions. For each one, the server's `(ord, page, chunk_type)` sequence must equal what the browser's `extractAllChunks` produces, captured once from the browser and committed as expected output.

### States

| State | Meaning |
|---|---|
| `stored` | bytes verified and stored, extraction not started |
| `extracting` | the server is extracting text |
| `extracted` | chunks exist, embeddings not yet |
| `indexing` | embedding in progress |
| `indexed` | searchable |
| `failed` | extraction or embedding failed (error recorded) |

- `chunks_uploaded` is retired. Migration 011 maps it to `extracted`. `registered` rows have neither bytes nor chunks; they stay as they are until their first verified upload, which runs the new-content path.
- **Startup recovery** (`server/db.py`, which today resets `indexing` → `chunks_uploaded`): `extracting` → `stored`, `indexing` → `extracted`.
- **Resuming vs re-indexing:** `POST /{id}/index` on content that isn't `indexed` **resumes** it, and any entry holder may do that. On `indexed` content it **re-indexes**, which is a content-changing op (§5).
- Frontend label maps (`IndexButton.jsx`, the state list in `App.jsx`, `useChatEngine.js`'s gating comment) move to these states.

### Legacy content

Content indexed before A1 from browser chunks is marked `extracted_by='client'`. The first server-verified upload of that hash stores the bytes and re-extracts it **once**, setting `extracted_by='server'`. This is the only exception to "no rework".

- **If the legacy content is `indexed`:** the upload gets 200 `{dedup: true, state: "indexed"}`, and the re-extraction runs in the background. The new chunks are extracted and embedded **first**, outside any transaction. Then one transaction deletes the old chunks, inserts the new ones and sets `extracted_by='server'`. Search keeps serving the old index until that commit and never sees a half-swapped one. Only one re-extraction runs per doc: the per-doc lock, plus a re-check of `extracted_by` under it.
- **If it isn't indexed** (`registered`, `extracted`, `failed`): the upload takes the normal new-content path and gets a 202.

**Type check:** extension plus magic bytes (`%PDF-` for PDF; valid UTF-8 for text/markdown). Anything else → 415.

## 5. Governance

| Action | Who | Otherwise |
|---|---|---|
| Upload / add to my library | anyone with `reader` | — |
| Rename / retag **my** entry | the entry's user | 404 |
| Remove from my library | the entry's user; affects only their entry | 404 |
| Share with a user (creates their entry, `added_via='shared'`) | a user holding an **upload** entry for it (they proved possession) | 404 |
| Revoke a share | the sharer (removes only entries they created); the recipient can remove their own | 404 |
| File into a project | holds an **upload** entry and can see the project (A0: role ≥ Contributor) | 404 |
| Remove from a project | **the project** (A1: project owner; A0: Maintainer+). The uploader has no special power. | 404 (see below) |
| Content-changing ops (Docling convert/re-convert, delete converted markdown, re-index an `indexed` doc) | the **sole holder** (exactly one entry, theirs, and no placements), or an **admin** | 409 `content_shared` |

- **Refusal codes in A1 are 404 only**, the repo's convention for projects (`projects.py` docstring, C0). A0 introduces 403 `insufficient_role` for members whose role is too low. Until then, "not allowed" and "not there" look the same.
- **Share semantics, pinned to SQL:**
  - Share: `INSERT … ON CONFLICT (user_id, doc_id) DO NOTHING`. A recipient who already holds the content, including their own **upload** entry, keeps that entry unchanged; nothing is ever downgraded to `shared`. The call returns 204 either way.
  - Revoke: `DELETE FROM library_entries WHERE user_id = :recipient AND doc_id = :doc AND added_via = 'shared' AND shared_by = :me`, then GC. It returns 204 if a row went, 404 otherwise, so it can never remove someone's own upload or another sharer's share.
  - Removing my own upload entry does **not** revoke the shares I created. Recipients keep their entries; `shared_by` stays pointing at me. Revoke first if that is the intent.
- Recipients of a share and project members **cannot re-share**. Only people who proved possession can grant access.
- **Sole holder vs your own project placement:** a placement counts as another user of the content, even when you filed it yourself. That's deliberate: re-converting changes what every project member reads. The user-facing docs say so, and the 409 carries a `reason` so the notice can say what to do (§8): `other_holders` or `in_project`.
- The admin's "reassign document owner" path is removed. Content has no owner, and entries are personal.
- `can_manage_project_docs(conn, user, project_id)` in `authz.py` is the one seam for "may remove from project". A1 implements it as "project owner"; A0 swaps its body to "role ≥ Maintainer".

## 6. Schema — migration `011_content_entries.sql` (A0's becomes `013`; see §6b for `012`)

1. `documents`:
   - add `extracted_by TEXT NOT NULL DEFAULT 'client' CHECK (extracted_by IN ('client','server'))`;
   - rename `pdf_path` → `bytes_path` (it now holds any type's bytes);
   - map `state = 'chunks_uploaded'` → `'extracted'`;
   - `file_name` stays as the canonical name.
2. Create `library_entries(user_id → users ON DELETE CASCADE, doc_id → documents ON DELETE CASCADE, file_name TEXT NULL, tags TEXT[] NOT NULL DEFAULT '{}', added_at, added_via CHECK ('upload','shared'), shared_by → users ON DELETE SET NULL, PK (user_id, doc_id))`, with an index on `doc_id`.
3. Backfill:
   - each document's owner → an `upload` entry carrying the document's `tags`;
   - each `doc_grants` row → a `shared` entry with `shared_by` = the document's owner. Nothing is lost: `doc_grants` has no grantor column, and only owners could grant.
4. `project_documents`: add `added_by → users ON DELETE SET NULL`, backfilled with the document's owner. It now carries information, because different holders can file the same content.
5. Drop `doc_grants`, `documents.user_id`, `documents.tags` (after the backfill), and `documents_user_idx`. Dropping `documents.tags` also drops `documents_tags_gin`. No GIN index goes on `library_entries(tags)` for now, because personal libraries are small. Add one if tag filtering shows up in slow queries.

Tested in a scratch database, the same way as migration 010's test: build to v10, seed owners, grants, placements and a `chunks_uploaded` doc, run the real file, and assert every entry, placement and state.

## 6b. Migration `012_entry_verification.sql` (final-review fix C1)

Added after the final review. Before A1 the browser sent only a hash, so 011's backfilled `upload` entries — and the shares and placements those owners made — record who *claimed* a document, not who had its bytes. Without a fix, someone who registered a hash they never had would read the real author's text as soon as the author uploaded the file, breaking §1's "knowing an ID is never access" across the upgrade.

- `library_entries.verified` and `project_documents.verified`, `BOOLEAN NOT NULL DEFAULT false`. Every row that exists at upgrade came from 011's backfill, so all start false.
- **Read** (`readable_docs_where`, still one predicate): an entry or placement grants read only if it is `verified` **or** the content is still legacy (`extracted_by = 'client'`, today's trust). Once verified bytes make the content server-derived, unverified holdings stop granting read. Project links shown on a row and the `project_id` filter use the same rule.
- **Share and file** need a **verified** upload entry. A share or placement made by such a holder is verified (a verified share or filing replaces an unverified legacy one in the same slot; upload entries are never touched).
- **Verification**: a user's multipart upload of the bytes verifies their entry (created, upgraded from shared, or an existing unverified upload entry) and every share they created (`shared_by = me`) and placement they added (`added_by = me`) for that doc. Nothing else verifies.
- Old holders regain access by uploading the file (the Index button does it from their local copy); `docs/LIBRARY.md` upgrade notes say so.

## 7. API

| Route | Change |
|---|---|
| `POST /v1/docs` | **multipart**: `file` (bytes) + optional `file_name`, `tags`, `client_doc_id` (hint). Returns 200 (dedupe) or 202 (new). `PDF_UPLOAD_MAX_MB` for PDFs, `TEXT_UPLOAD_MAX_MB` (new, default 10) for text/markdown → 413. |
| `GET /v1/docs` | my entries **plus** documents placed in my projects. Each row: `doc_id, file_name` (entry name, else canonical), `state, tags` (mine), `projects[]` (visible projects only, §3), `in_library, added_via, shared_by: {id, name}` |
| `GET /{id}`, `POST /{id}/search`, `GET /{id}/markdown` | read gate = the new predicate |
| `PATCH /{id}` | my entry's `file_name` / `tags` only (`owner_user_id` removed) |
| `DELETE /{id}` | remove **my entry** (204), then GC. Never touches other people or projects. |
| `PUT` / `DELETE /{id}/shares/{user_id}` | replaces `/grants/{user_id}`; semantics in §5 |
| `POST /{id}/index` | resume (any entry holder) or re-index (sole holder or admin), §4 States |
| `POST /{id}/convert`, `DELETE /{id}/markdown` | sole holder or admin, else 409 `content_shared` with `reason` |
| `POST /{id}/chunks`, `POST /{id}/pdf`, `DELETE /{id}/pdf` | **removed** (bytes arrive at registration; chunks are server-derived) |
| `PUT` / `DELETE /v1/projects/{id}/docs/{doc}` | link: caller holds an upload entry. Unlink: `can_manage_project_docs` only (C0's doc-owner branch removed), then GC. |
| `DELETE /v1/admin/users/{id}` | enumerates entry docs and owned-project placements first, GCs them after (§3) |

- Every refusal carries `{"error": <code>, "message": …}`.

## 8. Frontend

- **Upload and register:**
  - `registerDocument` sends the file (FormData) instead of JSON + chunks;
  - 200 dedupe → notice "Already indexed — added to your library";
  - 202 → the existing progress polling, with the new state names.
  - The client chunk upload (App.jsx Index path) and `src/lib/uploadPdf.js` are removed. Convert no longer uploads bytes, because the server already has them.
- **IndexedDB:** the local library still keys by the client hash. It's the same value for the same bytes; the server's `doc_id` from the response is authoritative if they ever differ.
- **Library:**
  - "Delete" becomes **"Remove from my library"**, with confirmation text saying others and projects keep their copies;
  - a "shared by <name>" badge;
  - rows visible only through a project show "via project" and have no remove-from-library control.
- **One central refusal-to-notice mapper**, `src/lib/apiErrors.js`, is introduced here. A0 adds its codes to it.

| Code | Notice |
|---|---|
| 409 `content_shared`, `reason: other_holders` | "Other people also use this document, so it can't be changed here. Ask an admin." |
| 409 `content_shared`, `reason: in_project` | "This document is in a project, so changing it would change it for the project too. Remove it from the project first, or ask an admin." |
| 413 | "File too large (limit N MB)." |
| 415 | "Unsupported file type." |
| 404 | "This document doesn't exist or you don't have access." |
| 5xx | "Something went wrong on the server. Try again." |

## 9. Configuration (`.env.example`)

| Key | Default | Meaning |
|---|---|---|
| `DOC_STORAGE_DIR` | falls back to `PDF_STORAGE_DIR`, then `./data/pdfs` | where content bytes live (`<dir>/<doc_id>.<ext>`); existing PDF files stay where they are |
| `TEXT_UPLOAD_MAX_MB` | `10` | size cap for text/markdown uploads |
| `PDF_UPLOAD_MAX_MB` | `50` (existing) | size cap for PDFs |
| `PDF_STORAGE_DIR` | `./data/pdfs` (existing) | legacy name, kept as `DOC_STORAGE_DIR`'s fallback |

`PDF_STORAGE_DIR` and `PDF_UPLOAD_MAX_MB` are read in `docs.py` today but missing from `.env.example`. A1 documents them alongside the new keys.

## 10. Logging

Introduced here and reused by A0:
- a `server.audit` logger with its own rotating file (`LOG_AUDIT_FILE`, default `<LOG_DIR>/audit.log`, same rotation settings as `server.log`);
- no personal data at INFO or above: IDs only.

| Level | Events |
|---|---|
| DEBUG | extraction details (pages, chunk counts) |
| INFO (and audit) | entry added (upload or dedupe), entry removed, share created or revoked, placement added or removed, content GC'd (with the path that triggered it) |
| WARNING | client hash hint mismatch; `content_shared` refusal; legacy content re-extracted; startup sweep removed orphans (count); dedupe retried after losing a GC race; missing bytes file self-healed |
| ERROR | extraction or embedding job failure |

## 11. Testing

- **Dedupe:** same bytes from two users → one content row, two entries. The second upload returns 200 without new chunks or an embedding call.
- **Server hash wins:** a lying `client_doc_id` → the server's hash is used and a WARNING logged.
- **An ID is not access:** knowing a `doc_id` without the bytes gives no entry and no read access (GET/search → 404).
- **No poisoning path:** `POST /{id}/chunks` no longer exists.
- **Removal is personal:** removing my entry leaves other entries and placements intact.
- **GC, every path in §3's table:** entry removal, revoke, unlink, project delete, and **admin user deletion** (sole-held content and bytes file gone; content others hold survives). Startup sweep removes a planted orphan.
- **Races:** GC and a same-hash upload interleaved → both callers succeed, no 5xx, the upload ends with an entry and a bytes file. Concurrent same-hash uploads → exactly one content row. Missing bytes file → rewritten by the next upload.
- **Governance:** the whole table in §5, including:
  - re-share by a recipient → 404;
  - sharing with someone who holds an upload entry leaves it `upload`;
  - revoke never removes another sharer's share or an upload entry;
  - convert on shared content → 409 `other_holders`; on content with only my placement → 409 `in_project`; allowed for the sole holder and for an admin.
- **Project links:** a row never lists a project the caller can't see, including for the uploader.
- **Migration 011:** scratch-DB test (owners → upload entries, grants → shared entries, placements get `added_by`, `chunks_uploaded` → `extracted`, columns gone).
- **States:** startup recovery maps `extracting` → `stored` and `indexing` → `extracted`; resume is allowed for any holder, re-index is not.
- **Legacy upgrade:** client-extracted indexed content is re-extracted once on the first verified upload; search returns the old chunks until the swap commits, then only the new ones.
- **Extraction parity:** the §4 fixtures (PDF, `.txt`, `.md`) produce the committed `(ord, page, chunk_type)` sequences.
- **Frontend:** upload paths (200 / 202 / 413 / 415), new state labels, the Library remove copy and badges, and the error mapper including both 409 reasons.
- **Journeys 1–6 are checked in the running app.** Tests alone don't count.

## 12. Non-goals / deferred

- Range-challenge proof of possession (avoiding the full re-upload for existing content). Add it if uploads become a burden.
- Recognizing the "same document with different bytes" (a re-saved PDF is new content).
- Per-user storage quotas; project-level tags; per-entry conversion variants; a GIN index on entry tags.
- A UI for sharing (A0) and for listing the shares I created.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Migration 011 drops `doc_grants` and moves columns | Backfill in one transaction plus the scratch-DB test; upgrade notes say to back up first |
| The upload protocol changes | SPA and backend deploy together (upgrade notes); an old SPA gets clear 404/405s rather than silent loss |
| Full re-upload for existing content (≤ `PDF_UPLOAD_MAX_MB`) | Acceptable now; range challenge deferred (§12) |
| Server segmentation drifts from the reader's, so citations land on the wrong page | Rules replicated exactly (§4) and pinned by committed parity fixtures for all three types |
| `pypdfium2` text differs slightly from pdf.js | Only search wording changes; page numbering is pinned by the fixtures |
| A future code path removes entries or placements by cascade and skips GC | The rule in §3, plus the startup orphan sweep |
| Legacy client-extracted content stays until its first verified upload | Same trust as today; marked `extracted_by='client'` |
