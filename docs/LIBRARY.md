# Document library (RAG Phase 0 → A1)

The library turns the single-owner document store into a **shared,
organizable library**: documents can be grouped into projects, shared with
other users, and tagged, while every read stays access-controlled. Today,
creating projects, adding members and sharing a document with one person
are **API-only** until the project-management UI (subsystem A0) ships; in
the app you can tag your documents and file them into projects you can
already see (a project's owner can also remove them there). This is Phase 0
of the RAG roadmap — it builds the sharing/organization substrate
that later phases (cross-document search, descriptions + routing,
multi-round retrieval) sit on top of. It adds no search intelligence itself.

Lives on `development`. Migrations **009**, **010**, **011** and **012**. Not
yet on `master`.

## The model: content, library entries, placements

A document's bytes are stored once, as **content**, and belong to nobody.
What belongs to someone is a **library entry** ("I have this") or a
**project placement** ("this project has this"):

| Thing | Table | Belongs to | Holds |
|---|---|---|---|
| **Content** | `documents` | nobody | bytes on disk, canonical `file_name`, type, size, page count, chunks + embeddings, extraction/indexing state, `extracted_by` |
| **Library entry** | `library_entries` | one user | "I have this": optional personal `file_name`/`tags`, `added_at`, `added_via` (`upload` \| `shared`), `shared_by` |
| **Project placement** | `project_documents` | a project | "this project has this content", `added_by`, `added_at` |

The content's id (`doc_id`) is the **SHA-256 of the bytes, computed by the
server** on upload — never trusted from the client. Uploading a file whose
hash already exists just adds you an entry; nothing is re-extracted or
re-embedded (see "Uploading," below).

**Read access** reduces to one predicate: a user can read a document iff
they hold a library entry for it, **or** the content is placed in a project
they own or belong to — and that entry or placement is **verified**: it
traces back to someone who uploaded the bytes to this server (a holding
from before A1, which only ever claimed a hash, counts until verified bytes
arrive; see "Upgrade notes (migration 012)"). It's resolved entirely in SQL (`readable_docs_where` /
`readable_docs_params` in `server/auth/authz.py`) — never filtered in
Python — so a document nobody gave you can never appear in a result, not
even one that happens to match your search or tag filter.

**Garbage collection.** Content — its row, chunks, pages and the bytes file
on disk — is deleted the moment nothing references it any more: no library
entry and no project placement. Removing your own entry, revoking a share,
unlinking from a project, and deleting a project or a user all trigger a
check right after the removal; a startup sweep also removes anything a code
path forgot to check, logged at WARNING with a count.

### 404, not 403 — the indistinguishability posture

A user who can't read a document gets **404**, identical to a document that
doesn't exist. A 403 would confirm the doc exists; 404 leaks nothing. This is
enforced structurally: `assert_can_read_doc()` and the list query's
`readable_docs_where()` filter build the `can_read` predicate into the SQL
`WHERE`, so a stranger's document **can never appear in a result set** — not
even one that happens to match the caller's `q`/`tag`/`project_id` filters.
There is no Python-side post-filter that could be bypassed. A1 keeps this
posture for every refusal it adds too: not being allowed to share, file into
a project, or change shared content is also a 404, never a 403.

## Uploading

You upload a file the same way whether it's brand new or something someone
else already has — pick it, and the server takes it from there.

- The browser sends the file's bytes to the server (`POST /v1/docs`,
  multipart). The server computes the SHA-256 itself; that hash is the
  document's id, never something the client can assert (a `client_doc_id`
  hint is accepted but only ever advisory).
- **Known bytes** (someone, anyone, already uploaded this exact file): you
  get an entry **instantly**. Nothing is re-extracted, re-embedded, or
  re-indexed — you just start reading, searching and chatting with a
  document that's already ready.
- **New bytes**: the server stores them, then extracts and indexes the text
  in the background (the same "Extracting…" → "Indexing n/m" → "Indexed"
  progression as before). Extraction happens **on the server**, from the
  verified bytes, replicating the reader's own page/section rules exactly —
  so a chat citation always opens to the right page.
- Caps: PDFs up to `PDF_UPLOAD_MAX_MB` (50 MB by default), text/Markdown up
  to `TEXT_UPLOAD_MAX_MB` (10 MB by default). Over the cap, an unsupported
  type, or an empty file is refused with a clear reason rather than a
  generic error.

## Removing

**Remove from my library** only removes *your* entry. Other people who hold
an entry for the same content, and any project that has it placed, keep
their copies untouched — the content itself is deleted only once the very
last entry and the very last placement are both gone.

## Sharing

Sharing a single document with one other person works today, but **only
through the API** — there's no share button in the app yet. The same goes
for creating a project and adding members to it: both are API-only
(`POST /v1/projects`, `PUT /v1/projects/{id}/members/{user_id}`) until the
project-management UI (subsystem A0) ships. The only project actions in the
app today are filing a document you uploaded into a project you can already
see, and — for that project's owner — removing it again.

Sharing needs curl (or a script) and a personal access token (see
[USER_GUIDE.md](USER_GUIDE.md) for creating one), plus two ids:

- **`doc_id`** — `GET /v1/docs` lists your documents with their `doc_id`
  (`curl https://reader.example.com/v1/docs -H "Authorization: Bearer nrp_…"`;
  add `?q=<part of the name>` to narrow it down).
- **the recipient's `user_id`** — there's no people directory yet, so ask
  the recipient: they read their own id from `GET /v1/auth/me` (the `id`
  field), with their own token or signed in to the app in the same browser.

```bash
# Share a document you uploaded with another user (idempotent, 204 either way):
curl -X PUT https://reader.example.com/v1/docs/<doc_id>/shares/<user_id> \
  -H "Authorization: Bearer nrp_your_token_here"

# Revoke a share you created (204 if one was removed, 404 otherwise):
curl -X DELETE https://reader.example.com/v1/docs/<doc_id>/shares/<user_id> \
  -H "Authorization: Bearer nrp_your_token_here"
```

Once shared, the recipient's Library shows the document with a "shared by"
badge naming you, under the name *you* gave it (never the name some other
uploader chose). The rules:

- Only someone holding a verified **upload** entry for the document — i.e.
  someone who has proved possession by uploading the bytes to this server —
  can share it. A
  recipient of a share, or someone who only sees the document through a
  project, **cannot re-share** it.
- Revoking a share removes only the entries **you** created; it never
  touches the recipient's own upload entry, or a share someone else granted.
  Removing your own upload entry does not revoke shares you made earlier —
  revoke them first if that's what you want.

## Projects govern their documents

Filing a document into a project needs a verified **upload** entry for it —
the same proof of possession sharing needs. Members who see it only through
the project see it under the name its filer gave it. Once it's filed, though, ownership
of the document stops mattering: only **the project** can remove it from
itself again. Today (before A0) that means the **project's owner**; once A0
ships, any Maintainer will be able to. The person who filed it has no
special standing over a placement they made — if the project owner wants it
gone, it's gone, for everyone who reads that project through that
placement.

## "Why can't I re-convert?"

Docling conversion, deleting the converted Markdown, and re-indexing an
already-indexed document all **change the content itself** — everyone who
reads that document sees the result. So each is refused unless you're the
**sole holder**: exactly one entry for the document, and it's yours, and it
isn't placed in any project. An admin can always do it.

That includes a project **you filed the document into yourself** — a
placement counts as another reader, even one you created. If you see the
refusal, remove the document from the project first (if you can), or ask an
admin. The server distinguishes two reasons:

- **"Other people also use this document"** — someone else holds an entry
  for it (shared with them, or they uploaded it themselves).
- **"This document is in a project"** — no other *person* holds it, but a
  project does.

## Schema

### Migration 009 (`projects`, `project_members`, `doc_grants`, tags)

```
projects(id, owner_user_id→users, name, description, created_at)
project_members(project_id→projects, user_id→users)          PK(project_id, user_id)
doc_grants(doc_id→documents, grantee_user_id→users)           PK(doc_id, grantee_user_id)
project_documents(project_id→projects ON DELETE CASCADE,
                   doc_id→documents ON DELETE CASCADE, added_at) PK(project_id, doc_id)
documents.tags        TEXT[] NOT NULL DEFAULT '{}'
```

Indexes back the four access paths + the two filters: `project_documents_doc_idx`
on `project_documents(doc_id)` (the PK covers project→docs), GIN on
`documents(tags)`, `doc_grants(grantee_user_id)`, `project_members(user_id)`.

### Migration 010 (many-to-many projects)

Replaced `documents.project_id` with `project_documents(project_id, doc_id,
added_at)`, PK `(project_id, doc_id)`. A document can be in any number of
projects.

### Migration 011 (content vs. entries — A1)

`doc_grants` and per-document ownership are gone, replaced by the
content/entries/placements model described above:

- `documents`: `pdf_path` → `bytes_path` (it now holds any type's bytes, not
  just PDFs); adds `extracted_by` (`'client'` \| `'server'`); `state =
  'chunks_uploaded'` becomes `'extracted'`; `user_id` and `tags` are
  dropped — a document's tags now live per-entry, not on the content.
- New `library_entries(user_id, doc_id, file_name, tags, added_at,
  added_via, shared_by)`, PK `(user_id, doc_id)`.
- `project_documents` gains `added_by`, so different holders filing the
  same content is now visible instead of silently collapsing.
- `doc_grants`, `documents.user_id`, `documents.tags` and their indexes are
  dropped after the backfill below. The old admin "reassign document owner"
  path is gone with them — content no longer has an owner to reassign.

### Upgrade notes (migration 011)

- **Back up first** (`pg_dump`) — this migration drops columns and a table.
- **The SPA and backend must deploy together.** The upload protocol changes
  shape (multipart with server-side hashing, replacing the old
  register-then-upload-client-chunks flow); an old SPA talking to a new
  backend gets a clear 404/405 rather than silently losing data, but there's
  no reason to run the two mismatched.
- **Existing owners become upload entries; grants become shared entries.**
  Every document's current owner gets an `upload` library entry carrying its
  existing tags; every `doc_grants` row becomes a `shared` entry attributed
  to that same owner (grants had no separate grantor column — only owners
  could ever grant, so nothing is lost in the backfill).
- **Documents indexed before A1 are re-extracted once**, the first time
  anyone uploads that content's bytes again after the upgrade. That's the
  *only* exception to "known bytes are never re-processed": the old text was
  extracted client-side and may not match the server's page/section rules,
  so it's redone from the newly-verified bytes and swapped in atomically —
  search keeps serving the old chunks until the swap commits. A conversion
  made from bytes that turn out not to hash to the document's id (a stale or
  tampered local copy) is discarded, and the old index is kept rather than
  overwriting good data with bad.

### Migration 012 (verified holdings)

`library_entries` and `project_documents` each gain `verified BOOLEAN NOT
NULL DEFAULT false`. `verified` means the holding traces back to someone
who uploaded the bytes to this server: an upload entry whose user sent
them, or a share or placement made by such a user. An entry or placement
grants read only if it is verified **or** the content is still legacy
(`extracted_by = 'client'`); sharing and filing need a verified upload
entry. Uploading a document's bytes verifies your entry and every share and
placement you made for it.

### Upgrade notes (migration 012)

Before A1 the browser sent only a document's hash, so an "owner" never
proved they had the file — someone could register a hash they never had.
Migration 011 still turned every pre-A1 owner into an upload entry, so 012
marks every existing entry and placement as unverified. What that means
after upgrading:

- **A document registered before A1 stays readable by its old holders only
  until someone uploads the real file.** Once verified bytes arrive, the
  server re-extracts the text itself, and old, unverified holders stop
  seeing it (404, like any document they were never given).
- **Old holders get access back by uploading the file** — the Index button
  in the reader does this from their local copy. That also re-activates the
  shares they made and the project placements they added for it.
- **The same goes for old shares and project placements**: a pre-A1 share
  or placement keeps working on legacy content and stops once verified
  bytes arrive, until the person who made it uploads the file.
- Until an old holder re-uploads, they also can't share the document or
  file it into a project (404), even while it's still readable.

## API surface

### Documents (`server/routers/docs.py`)

| Route | Guard | Notes |
|-------|-------|-------|
| `POST /v1/docs` | any signed-in user (`reader`) | Multipart: `file` + optional `file_name`, `tags`, `client_doc_id` (hint only — the server's own SHA-256 always wins, with a logged WARNING on mismatch). Known bytes → `200 {doc_id, dedup: true, state}`. New bytes → `202 {doc_id, state: "extracting"}` plus a background job. `413`/`415`/`422` for oversized, unsupported, or empty files. |
| `GET /v1/docs?q=&project_id=&tag=` | any signed-in user | Lists documents you hold an entry for, plus documents placed in projects you can see. Returns `{doc_id, file_name, state, tags, projects: [{id, name}], in_library, added_via, shared_by: {id, name} \| null}`. `file_name`/`tags` come from **your** entry when you have one; a row you see only through a project shows the name its filer gave it (else the canonical name) and no tags. |
| `GET /v1/docs/{id}` · `POST /v1/docs/{id}/search` · `GET /v1/docs/{id}/markdown` | reader (`can_read`) | Unchanged read gate, resolved by the entries-or-placement predicate above. |
| `PATCH /v1/docs/{id}` | your own entry, on a doc you can read | Sets **your** `file_name`/`tags` only — there's no more `owner_user_id` to reassign. |
| `DELETE /v1/docs/{id}` | your own entry | Removes your entry (204), then garbage-collects the content if nothing else holds it. Never touches other people's entries or any project. |
| `PUT` / `DELETE /v1/docs/{id}/shares/{user_id}` | a verified upload-entry holder (PUT) · the sharer (DELETE) | Replaces the old `/grants/{user_id}`. See "Sharing," above. |
| `POST /v1/docs/{id}/index` | any entry holder (resume) · sole holder or admin (re-index) | Resumes a document that isn't `indexed` yet from wherever it stopped; re-runs extraction and embedding on one that already is `indexed`. `409 bytes_missing` when there are no stored bytes to rebuild from — upload the file (the app's Index button does). |
| `POST /v1/docs/{id}/convert` · `DELETE /v1/docs/{id}/markdown` | sole holder or admin | `409 content_shared` otherwise (see "Why can't I re-convert?"). |
| ~~`POST /v1/docs/{id}/chunks`~~ · ~~`POST`/`DELETE /v1/docs/{id}/pdf`~~ | — | **Removed.** Bytes arrive at registration; chunks are always server-derived, never client-supplied. |

### Projects (`server/routers/projects.py`)

| Route | Guard | Notes |
|-------|-------|-------|
| `POST /v1/projects` | any signed-in user | Creates a project you own. API-only until A0. |
| `GET /v1/projects` | any signed-in user | Lists projects you **own or are a member of**; each row carries `is_owner`. |
| `GET` · `PATCH` · `DELETE /v1/projects/{id}` | owner (404 otherwise) | Read/rename/delete your own project. Delete removes its doc links (garbage-collecting any content that was only reachable through it); the documents themselves survive if anyone else holds them. |
| `PUT` · `DELETE /v1/projects/{id}/members/{user_id}` | owner | Idempotent (204). Add/remove a read-member. Unknown `user_id` → 404. API-only until A0. |
| `PUT /v1/projects/{id}/docs/{doc_id}` | a verified upload-entry holder who can see the project (admins: any project) | Link (204, idempotent). Invisible project → 404; a document you don't hold an upload entry for → 404. |
| `DELETE /v1/projects/{id}/docs/{doc_id}` | the project (A1: its owner; A0: Maintainer+) | Unlink (204). The uploader has no special standing here — see "Projects govern their documents," above. |

Every refusal in the tables above carries `{"error": <code>, "message": …}`
in the response body (and, for `content_shared`, a `reason` of
`other_holders` or `in_project`).

## Frontend

A **Library** view (`viewMode: 'library'`, `src/components/library/LibraryPage.jsx`)
in the reader window's view switcher, available to any active user. It lists
readable docs with search-as-you-type (`?q=`), a project filter, and — on
rows you hold an entry for — inline tag editing and a **Remove from my
library** control (with a confirmation that says people and projects who
have it keep their copies). A row you only see through a project shows a
purple **"via project"** badge and has no remove control; a row someone
shared with you shows a blue **"shared by …"** badge naming the sharer. A
doc you uploaded gets a project-chip selector to file it into any project
you can see; the **×** on a chip only appears for that project's owner,
matching "projects govern their documents" above.

The **upload/register surface** (`src/components/PdfViewer.jsx` toolbar)
sends the file's bytes directly in the registration request — there's no
separate client chunk-upload step any more, since the server derives chunks
itself from the verified bytes. A 200 response (known bytes) shows "Already
indexed — added to your library" immediately; a 202 response drives the
same progress polling as before, just against the new state names (below).
Re-indexing a document the server holds no bytes for (most documents
indexed before A1) uploads your local copy instead of stopping at "Upload
the file again first." Deleting converted Markdown follows the server's
re-extraction from the file until the document is indexed again.

## States

| State | Meaning |
|---|---|
| `stored` | bytes verified and stored, extraction not started |
| `extracting` | the server is extracting text |
| `extracted` | chunks exist, embeddings not yet |
| `indexing` | embedding in progress |
| `indexed` | searchable |
| `failed` | extraction or embedding failed (error recorded) |

A document whose file has no extractable text (a scanned PDF with no text
layer) ends `failed` with "No text found — try Convert (OCR).", not
`indexed` with nothing to search.

`registered` is legacy only: a pre-A1 row with neither bytes nor chunks,
left as-is until its first verified upload. On startup, any document caught
mid-job by a crash is rewound one step (`extracting` → `stored`, `indexing`
→ `extracted`) so it resumes cleanly instead of getting stuck; a follow-up
sweep then removes any orphaned content (see "Garbage collection," above).

## Configuration

`DOC_STORAGE_DIR` (falling back to the pre-A1 `PDF_STORAGE_DIR`, then
`./data/pdfs`) is where uploaded bytes live on disk, one file per document
id. `PDF_UPLOAD_MAX_MB` (50) and `TEXT_UPLOAD_MAX_MB` (10) cap upload size
by type. All three are documented with their defaults in `.env.example`.

## Audit log

Every change to who holds what — an entry added or removed, a share created
or revoked, a placement added or removed, content garbage-collected — is
logged through `server.audit`, one line per event, **IDs only** (never
emails, names, file names or search text). It goes to the normal server log
and to its own rotating file, `LOG_AUDIT_FILE` (default
`<LOG_DIR>/audit.log`).

## Capability coordination with the auth track

The read routes here are guarded by **both** `require_capability("reader")`
and `can_read` on the specific document — a user needs the `reader`
capability from their Keycloak realm roles *and* an entry or placement for
that particular document.

## What's next (out of scope here)

Phase 1 = `docScope` cross-document search; Phase 2 = document descriptions +
`find_documents` routing; Phase 3 = multi-round retrieval loop. Each gets its
own spec → plan → build. The "Ask about these" affordance in the Library is a
Phase 1 feature and is intentionally absent. Subsystem A0 (projects, roles &
directory) adds the people picker that turns per-document sharing into a UI
flow, Maintainer-level project roles, and a proper project-management screen.
