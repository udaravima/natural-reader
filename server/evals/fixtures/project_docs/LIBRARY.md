# Document library (RAG Phase 0 → A1)

The library turns the single-owner document store into a **shared,
organizable library**: documents can be grouped into projects, shared with
other users, and tagged, while every read stays access-controlled. Today,
adding members to a project and sharing a document with one person are
**API-only** until the project-management UI (subsystem A0) ships. In the
app you can create a project (Library → **New project**), tag your
documents, and file them into projects you can see (a project's owner can
also remove them there). This is Phase 0
of the RAG roadmap — it builds the sharing/organization substrate
that later phases (cross-document search, descriptions + routing,
multi-round retrieval) sit on top of. It adds no search intelligence itself.

Lives on `development`. Migrations **009**, **010**, **011** and **012**, plus
**018** for document versions. Not yet on `master`.

## The model: content, library entries, placements

A document's bytes are stored once, as **content**, and belong to nobody.
What belongs to someone is a **library entry** ("I have this") or a
**project placement** ("this project has this"):

| Thing | Table | Belongs to | Holds |
|---|---|---|---|
| **Content** | `documents` | nobody | bytes on disk, canonical `file_name`, type, size, page count, chunks + embeddings, extraction/indexing state, `extracted_by` |
| **Library entry** | `library_entries` | one user | "I have this": optional personal `file_name`/`tags`, `added_at`, `added_via` (`upload` \| `shared`), `shared_by` |
| **Project placement** | `project_documents` | a project | "this project has this content", `added_by`, `added_at`. Since migration 018 each row is one **version** of a project document; see "Versions (V1)" |

The content's id (`doc_id`) is the **SHA-256 of the bytes, computed by the
server** on upload — never trusted from the client. Uploading a file whose
hash already exists just adds you an entry; nothing is re-extracted or
re-embedded (see "Uploading," below).

**Read access** reduces to one predicate: a user can read a document iff
they hold a library entry for it, **or** the content is placed in a project
they own or belong to — and that entry or placement is **verified**: it
traces back to someone who uploaded the bytes to this server (a holding
from before A1, which only ever claimed a hash, doesn't count until its
holder uploads the file; see "Upgrade notes (migration 012)"). It's resolved entirely in SQL (`readable_docs_where` /
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
  re-indexed — you have the file open, and searching and chatting with it
  work at once because it's already indexed.
- **New bytes**: the server stores them, then extracts and indexes the text
  in the background (the same "Extracting…" → "Indexing n/m" → "Indexed"
  progression as before). Extraction happens **on the server**, from the
  verified bytes, replicating the reader's own page/section rules exactly —
  so the page numbers in chat citations match the reader's pages. Clicking a
  "(page N)" citation in a reply about a document opens that document at
  that page (through `GET /v1/docs/{id}/file` if it isn't open), or says why
  it can't — for example, when you can no longer read it.
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
for adding members to a project: it's API-only
(`PUT /v1/projects/{id}/members/{user_id}`) until the project-management UI
(subsystem A0) ships, because the app has no way yet to find another
person's user id. The project actions in the app today are creating a
project (Library → **New project**: a name and an optional description; you
own what you create), filing a document you uploaded into a project you can
see, and, for that project's owner, removing it again.

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
uploader chose).

**What a recipient can do with it:** everything a reader of the document
can. Library → **Open** on the row fetches the stored bytes
(`GET /v1/docs/{id}/file`), saves them to the recipient's own local library
and opens them in the reader, exactly as if they had picked the file. Index
and chat then work as for their own uploads, because the bytes hash to the
same `doc_id`. The same holds for a document you only see through a project.
A document nobody has uploaded since A1 (no stored bytes) can't be opened
this way; **Open** then says "Upload the file again first" in a notice.

The rules:

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
the project see it under the name its filer gave it, and can open it from
their Library (**Open**) like any document they can read. Once it's filed, though, ownership
of the document stops mattering: only **the project** can remove it from
itself again. Today (before A0) that means the **project's owner**; once A0
ships, any Maintainer will be able to. The person who filed it has no
special standing over a placement they made — if the project owner wants it
gone, it's gone, for everyone who reads that project through that
placement.

## Versions (V1)

A revised file becomes a **new version of a document in a project**, not a
second document. A project shows each document once, with its history; the
Library and project listings show only current versions; old versions stay
openable and restorable, and chat answers from whichever version is open,
saying so when it's an old one. History exists **only inside projects**: a personal library stays
flat. (Design: [superpowers/specs/2026-10-12-document-versions-design.md](superpowers/specs/2026-10-12-document-versions-design.md).)

### The model

Content stays shared and content-addressed; V1 adds a path and its history on
top, per project:

| Thing | Table | Meaning |
|---|---|---|
| **Content** | `documents` | the bytes, keyed by SHA-256. Unchanged. |
| **Project document** | `project_docs` | "a document in a project": one per document per project, with `last_version`, the highest number ever issued |
| **Version** | `project_documents` | one row per version: `version` (1, 2, 3…, never reused, gaps after deletes), the content (`doc_id`), the `file_name` it was filed under, an optional `note` (up to 500 characters), `origin` (`upload` \| `restore` \| `merge`), `origin_version`, `is_current` |

The history belongs to the project, not to the content. Five projects with
the same README have five independent histories over one stored file, and a
new version in one project changes nothing in the others or in anyone's
library. **A document's name is stored on each version, as filed**, and no
longer follows the filer's own copy: renaming or removing your library entry
doesn't rename the project's document.

### Readable vs. listed

Old versions must be readable (a citation in an old chat opens the exact
version it quoted) but must not be Library rows (that would recreate the
duplicates V1 removes). So the access rule has two forms, both in
`server/auth/authz.py`:

- **Readable** (`readable_docs_where`, unchanged): an entry, or a verified
  placement in a project you belong to. Every version is a placement row, so
  history is readable automatically.
- **Listed** (`listed_docs_where`): an entry, or a verified **current**
  version in a project you belong to. The Library listing and every "how many
  documents does this project have" count use it, so `doc_count` counts
  documents, not versions.

Unverified versions (legacy placements, see "Migration 012") behave as
unverified placements always did: invisible, and left out of every listing,
history and count.

### Name match, in order

Filing from a library (`PUT /v1/projects/{id}/docs/{doc_id}`) decides on the
server, so the reader's upload-into-project, the Documents tab and the
Library's "+ project" chip all get the same behaviour. The first rule that
applies wins:

1. A legacy *unverified* current version of this content in the project is
   verified in place.
2. The content is already current in the project: `200`, nothing changes.
3. The request says `as_version_of=<document>`: the file becomes a new
   version of that document under the same-bytes table below (an explicit
   choice wins over the two prompts, even when the bytes are an old version of
   a *different* document).
4. The content is an old version of a document in this project:
   `409 older_version`. The app offers *Restore vN as current*.
5. A current document in this project has the same name (letter case and
   surrounding spaces ignored): `409 name_exists`. The app offers *Add as new
   version*.
6. Otherwise a new document, v1.

`keep_both=true` skips rules 4 and 5. The name match is advice, not a
constraint: two people filing the same name at once can both create
documents, and *Mark as newer version of…* merges them afterwards. Only the
project being filed into is considered.

### Same bytes, for every new version

| The new version's content equals… | Result |
|---|---|
| this document's current version | `409 already_current` |
| one of this document's older versions *k* | accepted **as a restore of *k*** (`origin = restore`), and the response says `restored_from: k` |
| another document's current version in this project | `409 current_elsewhere`: merge them instead |

Restore follows the same table: restoring bytes that are already the current
version's (including through a later version that is itself a restore) is
`409 already_current`.

### Actions and roles

| Action | Role | Notes |
|---|---|---|
| See history, open an old version | Reader+ | |
| Upload new version | Contributor+ | Bytes, or a file from your own library; becomes current immediately. Adds **no** library entry for the uploader. New content is indexed in the background |
| Restore | Contributor+ | Appends a copy of an old version on top; nothing is re-indexed |
| Delete an old version | Maintainer+ | Never the current version (`409 is_current`); the content is deleted if nothing else uses it |
| Mark as newer version of… (merge) | Maintainer+ | One-way: all of B's versions move into A, numbered after A's, and B's current becomes A's. The row then shows B's name. Every file stays in the history |
| Remove document | Maintainer+ | Deletes the document with its whole history |

As everywhere in projects, admins act only through a member role. A
non-member gets `404` on every one of these routes.

**Known limit.** Uploading a version's bytes verifies only this project's
legacy unverified row for that content. It does not verify the uploader's
other legacy holdings of the same content (their library entry, their
placements in other projects); those wait for an ordinary upload of the file.

## "Why can't I re-convert?"

Docling conversion, deleting the converted Markdown, and re-indexing an
already-indexed document all **change the content itself** — everyone who
reads that document sees the result. So each is refused unless you're the
**sole holder**: exactly one verified entry for the document, and it's
yours, and it isn't placed (verified) in any project. Unverified pre-A1 rows
don't count; see "Migration 012". An admin can always do it.

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
them, or a share or placement made by such a user. Only a verified entry
or placement grants read, and sharing and filing need a verified upload
entry. An unverified one grants nothing: it doesn't count as another holder
either, so it never blocks the uploader's convert or re-index. Uploading a document's bytes verifies your entry and every share and
placement you made for it.

### Upgrade notes (migration 012)

Before A1 the browser sent only a document's hash, so an "owner" never
proved they had the file — someone could register a hash they never had.
Migration 011 still turned every pre-A1 owner into an upload entry, so 012
marks every existing entry and placement as unverified. What that means
after upgrading:

- **Every document registered before A1 disappears from its old holders'
  libraries, search and chat right after the upgrade** (404, like any
  document they were never given). Nothing is deleted: the entries, shares,
  placements and the text already indexed all stay.
- **Old holders get access back by uploading the file**: open it in the
  reader and press Index, which uploads the local copy. The server checks
  the bytes against the document's ID, and if the old text came from the
  browser it re-extracts it from the file. That upload also re-activates
  the shares the holder made and the project placements they added for it.
- **A recipient of an old share, or a member of a project an old document
  was filed into, gets it back when the person who shared or filed it
  uploads the file**, or by uploading their own copy.
- Why so strict: a pre-A1 holding proves only that someone once knew the
  document's ID, and IDs show up in shares, project lists and URLs.
  Trusting those holdings "until the real file arrives" was tried and
  leaked: when the author's upload produced no text, the content still
  looked legacy and a squatter kept reading it.

### Migration 018 (document versions)

Every placement becomes version 1 of its own project document:

- New `project_docs(id, project_id, created_at, created_by, last_version)`,
  `UNIQUE (id, project_id)`.
- `project_documents` gains `project_doc_id`, `version`, `file_name`, `note`
  (up to 500 characters), `origin` (`upload` \| `restore` \| `merge`),
  `origin_version` and `is_current`. Its primary key changes from
  `(project_id, doc_id)` to `(project_doc_id, version)`, with a composite
  foreign key `(project_doc_id, project_id)` so a version's project always
  matches its document's.
- Two partial unique indexes: one current version per document, and
  (`project_id`, `doc_id`) current in at most one document per project. The
  second is the duplicate guard.
- **Backfill.** Each existing row gets its own `project_docs` row
  (`last_version = 1`) and becomes version 1, current, `origin = upload`.
  `file_name` is the name the project showed before: the filer's entry name,
  else the content's canonical name, so nothing visibly renames. `verified`
  is unchanged.

The whole file runs in one transaction. No data is dropped.

### Upgrade notes (migration 018)

- **Take a `pg_dump` first.** The primary key of `project_documents` changes
  and there is no down step, so the dump is the only way back.
- **Existing duplicates are not merged.** The same name doesn't mean the same
  document, and only a person can tell. Spot them in a project's Documents tab
  and merge them with **⋯ → Mark as newer version of…**.
- **API breaks** (also in the CHANGELOG): `PUT /v1/projects/{id}/docs/{doc_id}`
  now answers with a body and can answer `409`; `DELETE
  /v1/projects/{id}/docs/{doc_id}` is gone. Nothing outside the app calls
  them (checked in `extension/`, `scripts/`, `deploy/`), but custom scripts
  would break.

## API surface

### Documents (`server/routers/docs.py`)

| Route | Guard | Notes |
|-------|-------|-------|
| `POST /v1/docs` | any signed-in user (`reader`) | Multipart: `file` + optional `file_name`, `tags`, `client_doc_id` (hint only — the server's own SHA-256 always wins, with a logged WARNING on mismatch). Known bytes → `200 {doc_id, dedup: true, state}`. New bytes → `202 {doc_id, state: "extracting"}` plus a background job. `413`/`415`/`422` for oversized, unsupported, or empty files. |
| `GET /v1/docs?q=&project_id=&tag=` | any signed-in user | Lists documents you hold an entry for, plus documents whose **current** version is in a project you can see (old versions are readable but never listed). Returns `{doc_id, file_name, state, tags, projects: [{id, name, document_id, version, versions_count}], newer_in: [{project: {id, name}, document_id, version, current_version, current_doc_id, current_name, is_current}], in_library, added_via, shared_by: {id, name} \| null}`. The `projects` chips name only projects where this content is current; `newer_in` names visible project documents where it is an older version. `?project_id=` keeps content that is current in that project. `file_name`/`tags` come from **your** entry when you have one; a row you see only through a project shows the file name of a visible version (the current one first) and no tags. |
| `GET /v1/docs/{id}` | reader (`can_read`) | The row as in the list, plus `versions_in: [{project: {id, name}, document_id, version, current_version, current_doc_id, current_name, is_current}]`: every project you belong to whose history holds this content (`version` is its highest number there). The reader's old-version banner is built from it. Only your own projects are ever named. |
| `POST /v1/docs/{id}/search` · `GET /v1/docs/{id}/markdown` | reader (`can_read`) | Unchanged read gate, resolved by the entries-or-placement predicate above. |
| `GET /v1/docs/{id}/file` | reader (`can_read`) | The stored bytes, `inline`, with the stored type (`application/pdf`, `text/plain`, `text/markdown`) and **your** name for the document (your entry's, else the name of a visible project version). `404` to anyone who can't read it, including an unverified pre-A1 holder; `409 bytes_missing` when the server has no bytes for it. What the Library's **Open** button uses. |
| `PATCH /v1/docs/{id}` | your own entry, on a doc you can read | Sets **your** `file_name`/`tags` only — there's no more `owner_user_id` to reassign. |
| `DELETE /v1/docs/{id}` | your own entry | Removes your entry (204), then garbage-collects the content if nothing else holds it. Never touches other people's entries or any project. |
| `PUT` / `DELETE /v1/docs/{id}/shares/{user_id}` | a verified upload-entry holder (PUT) · the sharer (DELETE) | Replaces the old `/grants/{user_id}`. See "Sharing," above. |
| `POST /v1/docs/{id}/index` | an entry holder who can read it (resume) · sole holder or admin (re-index) | Resumes a document that isn't `indexed` yet from wherever it stopped; re-runs extraction and embedding on one that already is `indexed`. `409 bytes_missing` when there are no stored bytes to rebuild from — upload the file (the app's Index button does). |
| `POST /v1/docs/{id}/convert` · `DELETE /v1/docs/{id}/markdown` | sole holder or admin | `409 content_shared` otherwise (see "Why can't I re-convert?"). |
| ~~`POST /v1/docs/{id}/chunks`~~ · ~~`POST`/`DELETE /v1/docs/{id}/pdf`~~ | — | **Removed.** Bytes arrive at registration; chunks are always server-derived, never client-supplied. |

### Projects (`server/routers/projects.py`)

| Route | Guard | Notes |
|-------|-------|-------|
| `POST /v1/projects` | any signed-in user | Creates a project you own. In the app: Library → **New project**. |
| `GET /v1/projects` | any signed-in user | Lists projects you **own or are a member of**; each row carries `is_owner`. |
| `GET` · `PATCH` · `DELETE /v1/projects/{id}` | owner (404 otherwise) | Read/rename/delete your own project. Delete removes its doc links (garbage-collecting any content that was only reachable through it); the documents themselves survive if anyone else holds them. |
| `PUT` · `DELETE /v1/projects/{id}/members/{user_id}` | owner | Idempotent (204). Add/remove a read-member. Unknown `user_id` → 404. API-only until A0. |
| `PUT /v1/projects/{id}/docs/{doc_id}` | Contributor+ holding a verified upload entry (admins only through a member role) | File a document from your library. `201 {document_id, version}` when something was filed, `200` (same body) when that content was already current. Query `as_version_of=<document id>` files it as a new version of that document; `keep_both=true` files it as a separate document anyway. `409 name_exists` / `409 older_version` ask the caller to choose (see "Name match, in order"). Invisible project → 404; a document you don't hold an upload entry for → 404. |
| ~~`DELETE /v1/projects/{id}/docs/{doc_id}`~~ | — | **Removed.** Use `DELETE /v1/projects/{id}/documents/{document_id}` below. |

### Project documents (`server/routers/project_documents.py`)

Addressed by the **project document's id** (`document_id`), not by a content
hash, because one hash can appear in several of a project's histories. A
non-member gets `404` before anything else is looked at; a member below the
route's role gets `403 insufficient_role`.

| Route | Role | Notes |
|-------|------|-------|
| `GET /v1/projects/{id}/documents` | Reader+ | Current, verified versions only: `[{id, name, version, versions_count, doc_id, state, updated_at, updated_by}]`, sorted by name. |
| `GET /v1/projects/{id}/documents/{document_id}/versions` | Reader+ | Verified versions, newest first: `[{version, doc_id, file_name, note, origin, origin_version, added_by, added_at, is_current, state}]`. |
| `POST /v1/projects/{id}/documents/{document_id}/versions` | Contributor+ | Multipart `file` + optional `file_name`, `note` → `201 {version, doc_id, state, restored_from}`. `restored_from` is `null` unless the bytes matched an older version, which makes it a restore. `413`/`415`/`422` as for `POST /v1/docs`; a note over 500 characters is `422 note_too_long`. |
| `POST /v1/projects/{id}/documents/{document_id}/versions/from-library` | Contributor+ | JSON `{doc_id, note}`, for a file you hold a verified upload entry for → `201`, same shape. |
| `POST /v1/projects/{id}/documents/{document_id}/versions/{n}/restore` | Contributor+ | Appends a copy of version *n* as the new current version → `201`, same shape. |
| `DELETE /v1/projects/{id}/documents/{document_id}/versions/{n}` | Maintainer+ | `204`. The current version is `409 is_current`. |
| `POST /v1/projects/{id}/documents/{document_id}/merge` | Maintainer+ | JSON `{"from": <document id>}`: that document is absorbed into `{document_id}`, which stays. One-way. → `200` with the surviving row. Merging a document into itself is `409 merge_self`. |
| `DELETE /v1/projects/{id}/documents/{document_id}` | Maintainer+ | `204`. Removes the document and its whole history. |

`409` bodies name the document involved as `{document: {id, name, version}}`.
The codes: `already_current`, `current_elsewhere`, `older_version`,
`name_exists`, `is_current`, `merge_self`. Changes are recorded in the
project's Activity feed.

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
shared with you shows a blue **"shared by …"** badge naming the sharer.
Every row has an **Open** button that opens the document in the reader (see
"Sharing," above). A
doc you uploaded gets a project-chip selector to file it into any project
you can see; the **×** on a chip only appears where you may remove documents
from that project, matching "projects govern their documents" above. A chip
reads `Project X · v3`: the name opens the project's page, and **×** removes
the project's document with its whole history (with more than one version it
asks *Remove … and its N versions from …?* first). A row whose content a
project has moved past shows a muted line, *Project X has a newer version
(v3)*, with **Open current**; your own copy stays yours. Version actions live
on each project's Documents tab, never in the Library.

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
