# Document versions (V1)

**Date:** 2026-10-12 (brainstormed 2026-10-11/12)
**Status:** Built. This spec has been brought in line with what was built; places where the build differs from the first design are marked "(changed in the build: …)".
**Branch:** `feat/v1-document-versions` (from `development` at c4dc6f5)
**Program:** the second of five, in this order: A0 → **V1 document versions** → C2.1 project chat → V2 compare and review → C2.2 digests (agreed 2026-10-06).
**Depends on:** A1 (content vs library entries, migrations 011–012) and A0 (project roles, activity feed, migration 017), both built.
**Open-source rule:** nothing deployment-specific is hardcoded. V1 adds no env vars.

## 1. Goal and user journeys

A revised file becomes a **new version of an existing document in a project**, not a second document. A project shows each document once, with its history. Search and chat see only the current version. Old versions stay openable and restorable. This comes before C2.1 so that project chat never sees duplicates. A file name mentioned in one document ("see design.pdf") must lead the model to exactly one document, the current one.

Every journey works from the UI (controls in §6) and is checked in the running app (§9):

1. A Contributor uploads a revised file as the new version of a project document. The project still shows one row: "v2 · 2 versions".
2. A member opens the history and opens any old version.
3. A Contributor restores an old version. It becomes current again, and the history keeps everything.
4. Someone files a file whose name matches an existing document. The app asks: add as a new version, or keep both.
5. A Maintainer merges two documents that are really one ("Mark as newer version of…"). This is how duplicates that already exist get cleaned up.
6. A Maintainer deletes one old version permanently.
7. A member whose personal copy is older than the project's current version sees that in the Library and can open the current one.
8. An old chat's citation opens the exact version it cited, labelled as an old version.
9. The project's Activity tab shows who added, restored, merged or deleted versions.

**Still not possible after V1, by design:**

- **Chatting across a project's documents** (C2.1), including the "find the README, then follow it to the next document" hop. After V1, chat still reads one document per turn (the open one). Until C2.1, that hop is manual: open the next document and ask again.
- **Comparing two versions, and review before a version becomes current** (V2). In V1 a new version is current the moment it's uploaded.
- **Versions of documents in a personal library.** History exists only inside projects (user decision, 2026-10-11).

## 2. The model, and how it maps onto git

Since A1, a document is spread across three tables. `documents` is content owned by nobody, keyed by the SHA-256 of its bytes. `library_entries` says "this user has it". `project_documents` says "this project has it". That is already git's object store: content-addressed blobs, stored once, shared across repos. V1 adds the other half of git: **a path and its history.**

| Git | Natural Reader after V1 |
|---|---|
| blob (content-addressed) | `documents` row: **unchanged** |
| a path in a repo's tree | a **project document** (`project_docs`, new): one per document per project |
| a commit that changes that path | a **version**: a `project_documents` row, now numbered |
| `git log -- report.pdf` | the document's history |
| `git checkout v1 -- report.pdf` + commit | **Restore**: a new version on top, never a pointer moved back |
| reachable blobs survive `git gc` | content referenced by any version is never collected |
| protected `main` + merge requests | V2's review gate (in V1, "main" is unprotected) |

Deliberate departures from git:

- **The history belongs to a project, not to the content.** Content is shared. If Alice's library and Project X hold the same file and Bob uploads v2 into X, Alice's library must not change. Five projects with a README have five independent histories. Byte-identical READMEs are still stored once, and diverge only when one project uploads a new version.
- **The same name doesn't silently make a new version.** In git, a path is unique within a folder. Our projects have no folders, and two vendors can both send `Invoice.pdf`. So a name match is a suggestion the user confirms (§4.2).
- **A merge is recorded, not inferred.** `git log --follow` detects renames from byte similarity (≥ 50%). That fails for PDFs, whose compressed streams change almost entirely after a small edit. V1's merge is an explicit, recorded action.
- **Versions are per document, not snapshots of the whole project.** V1 can't show "the project as of 1 October" (§10).

### Readable vs listed

Members can read old versions (journey 2). The Library lists *everything the caller can read* ([docs.py:265](../../../server/routers/docs.py)). Left as it is, every old version would become a Library row, recreating the duplicates V1 removes. So the one rule becomes two, both in [authz.py](../../../server/auth/authz.py):

- **Readable** (`readable_docs_where`, **unchanged**): an entry, or a verified placement in a project the user belongs to. Every version is a placement row, so history is readable automatically.
- **Listed** (`listed_docs_where`, new): an entry, or a verified **current** version in a project the user belongs to. It's used by the Library listing and by every count of "a project's documents".

## 3. Data model: migration `018_document_versions.sql`

### 3.1 Schema

```sql
project_docs                      -- "a document in a project"
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
  project_id    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  created_by    UUID REFERENCES users(id) ON DELETE SET NULL
  last_version  INT NOT NULL      -- the highest number ever issued; never decreases
  UNIQUE (id, project_id)         -- target of the composite FK below

project_documents                 -- existing; every row is now ONE VERSION
  kept:  project_id, doc_id, added_by, added_at, verified
  + project_doc_id  UUID NOT NULL
  + version         INT  NOT NULL   -- 1, 2, 3…; never reused; gaps after deletes
  + file_name       TEXT NOT NULL   -- this version's name, fixed when it was added
  + note            TEXT            -- optional "what changed", ≤ 500 chars
  + origin          TEXT NOT NULL CHECK (origin IN ('upload','restore','merge'))
  + origin_version  INT             -- restore: the version copied; merge: its number in the absorbed document
  + is_current      BOOLEAN NOT NULL
  PRIMARY KEY (project_doc_id, version)               -- was (project_id, doc_id)
  FOREIGN KEY (project_doc_id, project_id)
      REFERENCES project_docs(id, project_id) ON DELETE CASCADE
  UNIQUE (project_doc_id)     WHERE is_current        -- one current version per document
  UNIQUE (project_id, doc_id) WHERE is_current        -- content is current in ≤ 1 document per project
  INDEX project_documents_doc_idx (doc_id)            -- kept: serves the read predicate
  INDEX project_documents_project_idx (project_id)    -- replaces the old key's project_id-first lookup (project deletion)
```

### 3.2 What each part guarantees

- **The composite FK:** a version's `project_id` always equals its document's. A merge or restore across projects is impossible at the schema level.
- **The second partial unique index is the duplicate guard.** Two documents in one project can't both currently be the same file, so C2.1's project search can never return the same content twice.
- **The `is_current` flag, not "highest number wins".** In V1 the current version is always the highest-numbered one: restore and merge only append, and the current version can't be deleted. V2 will add versions that are higher-numbered but waiting for review. The flag means V2 needs no migration, and it's also the only way to express the duplicate guard as an index.
- **`last_version`:** a new version gets `last_version + 1`, set under the document's row lock. Numbers are never reused, even if a future version (V2) deletes the highest one. People refer to "v2" in comments and chats, and a reused number would silently change what "v2" means.
- **A document's displayed name** is its current version's `file_name`. Storing the name per version fixes a latent problem. Today a project *borrows* a document's name live from the filer's personal entry ([projects.py:206](../../../server/routers/projects.py) `_placement_name`). If the filer renames their copy, the project's name changes for everyone. If they remove their copy, the project falls back to the content's canonical name, which is the first uploader anywhere's file name and possibly a stranger's.
- **Unverified versions** (legacy placements, migration 012) behave exactly as unverified placements do today: invisible. They're left out of listings, history, counts, `versions_in` and `newer_in`, and they can't be restored or merged. `_verify_holdings` ([doc_content.py:68](../../../server/services/doc_content.py)) still verifies them by `added_by` when their filer uploads the bytes. `versions_count` counts only verified versions.
- **Known limit: a version upload verifies only this project's legacy row.** Uploading a version's bytes verifies this project's unverified *current* row for that content (`verify_legacy_current`, §4.2 step 0), and nothing else. It does not verify the uploader's other legacy holdings of the same content (their library entry, or their placements in other projects), which `_verify_holdings` would verify on a plain `POST /v1/docs` upload. Those stay invisible until the uploader uploads the file the ordinary way. This is deliberate: a version upload is a project action and shouldn't change what is visible elsewhere.

### 3.3 Unchanged

- `documents`, `doc_chunks`, `doc_pages`, `library_entries`;
- indexing, conversion, embedding profiles;
- GC (`gc_content_if_orphaned`, `sweep_orphans`). Every version is a placement row, so content in any version's history stays alive with no change to GC.
- Content-changing ops (convert, re-index) still treat any verified placement as another holder, and therefore so is every version (A1 §5).

### 3.4 Every query on `project_documents`, classified

There are 14 query sites today. Every row is now a version, so each site either wants **all versions** (history) or **current versions** (documents). A site in the wrong group fails in a way you can predict: a history site that filters to current versions breaks reads of old versions, or deletes their content; a listing site that forgets to filter shows duplicates or inflated counts.

| Group | Sites | Change |
|---|---|---|
| **History: all versions (correct as is)** | read predicate [authz.py:40](../../../server/auth/authz.py) · `_verify_holdings` [doc_content.py:78](../../../server/services/doc_content.py) · content-ops `in_project` [:128](../../../server/services/doc_content.py) · GC [:157](../../../server/services/doc_content.py) · startup sweep [:177](../../../server/services/doc_content.py) · delete-project enumeration [projects.py:157](../../../server/routers/projects.py) | none, except that the delete-project enumeration gains `DISTINCT` (restore can put one content in one history twice) |
| **Documents: current only (add `is_current`)** | project `doc_count` [projects.py:69](../../../server/routers/projects.py) · admin console `doc_count` [admin.py:167](../../../server/routers/admin.py) · Library chips `_doc_projects_sql` [docs.py:124](../../../server/routers/docs.py) · Library `?project_id=` filter [docs.py:292](../../../server/routers/docs.py) | filter to `is_current` (and `verified`, as today). Without this, a project with one document and three versions reports `doc_count: 3` |
| **Replaced** | `_placement_name` [projects.py:210](../../../server/routers/projects.py) · `_DISPLAY_NAME` [docs.py:147](../../../server/routers/docs.py) · filing `INSERT` [projects.py:230](../../../server/routers/projects.py) · unlink `DELETE` [projects.py:251](../../../server/routers/projects.py) | the display-name helper (§7) · the §4.2 filing flow · `DELETE …/documents/{id}` |

Each new query added by V1 states its group in a comment, so a later reader can check it against this table.

## 4. Actions and rules

### 4.1 Actions

Roles map onto GitLab's (Contributor ≈ Developer). They reuse the `can` flags every project response already carries ([authz.py](../../../server/auth/authz.py) `can_for`): `file_docs` (Contributor+) and `remove_docs` (Maintainer+). There are no new flags. As with A0's document routes, admins act only through a member role (`admin_ok=False`).

| Action | Who | What happens | Activity event (`doc_id` = the version's content) |
|---|---|---|---|
| See history, open an old version | Reader+ | read only | — |
| **Upload new version** | Contributor+ (`file_docs`) | The bytes are uploaded, or a file from your library that you uploaded yourself is picked. The server applies the same-bytes rules (§4.3). The new version gets `last_version + 1`, an optional note, `origin = upload`, and **becomes current immediately**. New content is indexed in the background, as for any upload. | `document.version_added` `{name, version, note}` |
| **Restore v*k*** | Contributor+ (`file_docs`) | Appends a version with v*k*'s content and `file_name`: `origin = restore`, `origin_version = k`, current. Nothing is re-indexed. | `document.version_restored` `{name, version, from_version}` |
| **Mark as newer version of…** (absorb B into A) | Maintainer+ (`remove_docs`) | All of B's versions move into A in B's version order, numbered from A's `last_version + 1`, with `origin = merge` and `origin_version` = their number in B. They keep their own `added_by`, `added_at`, `note` and `file_name`. B's current version becomes A's current. B's `project_docs` row is deleted. **One-way.** | `document.merged` `{from_name, into_name, versions}` |
| **Delete old version** | Maintainer+ (`remove_docs`) | Deletes one version that isn't current, then GCs its content if nothing else references it. The current version: `409 is_current`. | `document.version_deleted` `{name, version}` |
| **Remove document** (exists today) | Maintainer+ (`remove_docs`) | Deletes the document and its whole history. Every version's `doc_id` is collected first and GC'd after (A1's rule: a cascade never runs GC itself). | `document.removed` `{name, versions}` |

The four new kinds join `KINDS` in [project_events.py](../../../server/services/project_events.py), which needs no migration. Each event is written in the same transaction as its change. Audit lines follow the existing `placement.*` pattern: `version.added`, `version.restored`, `document.merged`, `version.deleted`.

**After a merge, A shows B's name.** A document's name is its current version's `file_name`, and B's current version is now A's. So merging `report-final.pdf` into `report.pdf` leaves one row called `report-final.pdf`, with `report.pdf`'s history underneath. The confirmation text says which name the row will show.

**A merge can make dates look out of order.** If A's versions are from January and March and B's are from February and April, A reads v1 Jan, v2 Mar, v3 Feb, v4 Apr. A's versions are not renumbered, for the reason given under `last_version`. The history marks merged versions "merged (was v*n*)", and the version's own `file_name` shows where it came from.

**A merge can't be undone, but it loses nothing.** Every file stays in A's history, and old chats still open them. To get B back as its own document, file its version again with *Keep both*.

### 4.2 Filing from a library: the name-match prompt

`PUT /v1/projects/{pid}/docs/{doc_id}` files a document from your library: Contributor+, holding a verified upload entry, as today. It serves all three filing paths: the reader's upload-into-project, the Documents tab's "File a document…", and the Library's "+ project" chip. The prompt is decided on the server, so all three get it. The **filed name** is the filer's display name for their entry, `COALESCE(entry.file_name, documents.file_name)`, and it becomes the new version's `file_name`. The checks run in this order; the first that applies wins (changed in the build: the explicit `as_version_of` choice is its own step, ahead of the two prompts):

0. **Legacy: an *unverified* current version in this project holds this content** → verify it in place (`verified = true`, `added_by` = the filer), as today's `ON CONFLICT … WHERE NOT verified` does. Then continue with step 1. This step exists because the duplicate-guard index ignores `verified`. Without it, a new current row for the same content would hit a `unique_violation`.
1. **The content is already current in this project** → idempotent `200 {document_id, version}`, with no event. This is today's behaviour. (If the request names `as_version_of` and that is a *different* document from the one holding the content, it is `409 current_elsewhere` instead: the content can't be current in two documents.)
2. **An explicit choice wins.** If the request says `as_version_of=<id>`, the file becomes a new version of that document under the same-bytes rules of §4.3 (`201 {document_id, version, restored_from}`; event `document.version_added`, or `document.version_restored` when §4.3 made it a restore). This holds even when the bytes are an old version of a *different* document D: the user chose that document, so it gets the version and D is left alone. Steps 3 to 5 are not consulted.
3. **The content is an old version of a document D in this project** → `409 older_version {document, version}`. *Unless* the request says `as_version_of=D` (step 2: a restore of that version) or `keep_both=true`. The byte match is checked before the name match because it's the stronger evidence.
4. **A current document has the same name**, compared with `lower(btrim(...))` against verified current versions → `409 name_exists {document}`. *Unless* the request says `as_version_of=<id>` (step 2) or `keep_both=true`.
5. Otherwise → a **new document**, v1, `origin = upload`, `201 {document_id, version: 1}`, event `document.added`.

The same in-place verification (step 0) applies when a version upload's content matches an unverified current version elsewhere in the project. After it, §4.3's `current_elsewhere` applies normally.

The name match is **advisory, not a constraint.** Two people filing the same name at the same moment can both create documents. *Mark as newer version of…* fixes that afterwards. Matching considers only the project being filed into: the same name in another project never prompts. If the user cancels the dialog, the file stays in their library, unfiled, as when filing fails today.

### 4.3 Same-bytes rules (for every new version)

| The new version's content equals… | Result |
|---|---|
| this document's current version | `409 already_current` |
| one of this document's older (verified) versions *k* | accepted **as a restore of *k*** (`origin = restore`), and the response says `restored_from: k` |
| another document's current version in this project | `409 current_elsewhere {document}`: "merge them instead" |

Restore applies the same rules. Restoring content that is current in another document gives `409 current_elsewhere`. Restoring a version whose bytes are already the current version's bytes gives `409 already_current`. That covers the chosen row being the current one, and also an older row (say v1) whose bytes a later version (say v3, itself a restore of v1) already holds as current. Without this, restore would append a version identical to the one on top.

### 4.4 Transactions, locking and the upload pipeline

- **Lock order is always `projects` → `project_docs` → `documents`.** Every action first takes its project's row `FOR KEY SHARE`, then locks its document's `project_docs` row `FOR UPDATE`, then touches content. GC locks `documents` and never touches `project_docs`.
- **Why the `projects` lock comes first.** Inserting a version row takes `FOR KEY SHARE` on the project anyway, through its foreign key. `delete_project` locks the project `FOR UPDATE` and then cascades into `project_docs`. If the version write took `project_docs` first and the project's key share only at its `INSERT`, the two would wait on each other: a deadlock, which Postgres breaks by aborting one side. Taken first, the key share makes a project deletion wait for the write to commit. It doesn't block a rename, which takes `FOR NO KEY UPDATE`. As a safety net, any `DeadlockDetected` that still reaches a route is answered `503 busy` "Please try again." (both sides roll back fully, and a retry succeeds).
- **A merge locks both `project_docs` rows in `id` order** (after the project's key share), so two crossed merges (A←B and B←A at once) can't deadlock. One wins, and the other gets 404 because its document is gone.
- **Two simultaneous version uploads become v3 and v4, never two v3s.** If anything slips past the locks, the unique indexes refuse it, and the route maps a `unique_violation` to `409` (the matching code from §4.3), never a 500.
- **Uploading bytes reuses A1's pipeline** ([docs.py:366](../../../server/routers/docs.py) `_add_upload_entry`, `_after_upload`). `_add_upload_entry` is generalised so its single transaction inserts **either** a library entry **or** a version row as "the holding". That keeps A1's GC-race guarantees: the `FOR SHARE` lock on existing content, the one retry after losing to a GC, and bytes placed only after the `INSERT` commits. "Upload new version" adds **no** library entry. Otherwise every uploader would collect a stale personal copy that becomes a duplicate row as soon as the next version arrives.
- **A refused version upload creates nothing.** A refusal (§4.3) can only happen when the content already exists. The transaction rolls back, and `doc_storage.discard` drops the staged file.
- **Proof of possession (A1 §4) holds.** A version comes from bytes the caller sent (so `verified = true`) or from a file the caller holds a **verified upload** entry for (`assert_holds_upload`). Restore and merge only rearrange verified content already in the project, and the moved or copied rows keep `verified`.
- **Upload limits are as for `POST /v1/docs`:** 413 too large, 415 unsupported type (extension + magic bytes), 422 empty. The note is trimmed: an empty note is stored as `NULL`, and more than 500 characters is a 422.
- **A failed index doesn't block the version.** The new version stays current with state `failed`. Anyone with Contributor or above can retry indexing (A1's resume) or restore the previous version.

## 5. API

### 5.1 New: a project's documents

These are addressed by the project document's id. A content hash no longer identifies one document, because one hash can appear in several of a project's histories.

| Route | Role | Request → response |
|---|---|---|
| `GET /v1/projects/{pid}/documents` | Reader+ | Current, verified versions only: `[{id, name, version, versions_count, doc_id, state, updated_at, updated_by: {id, name}}]`, sorted by name |
| `GET /v1/projects/{pid}/documents/{id}/versions` | Reader+ | Verified versions, newest first: `[{version, doc_id, file_name, note, origin, origin_version, added_by: {id, name}, added_at, is_current, state}]` |
| `POST /v1/projects/{pid}/documents/{id}/versions` | Contributor+ | Multipart `file` + optional `file_name` and `note` (the name defaults to the uploaded file's, trimmed, ≤ 255 characters, as for `POST /v1/docs`) → `201 {version, doc_id, state, restored_from}` (`restored_from` is `null` unless §4.3 made it a restore). Starts indexing for new content. |
| `POST /v1/projects/{pid}/documents/{id}/versions/from-library` | Contributor+ | JSON `{doc_id, note}` for a file you uploaded to your library (the name is your entry's display name) → `201`, same shape. (Changed in the build: its own route, because FastAPI validates one body type per route, so the multipart and JSON forms can't share one.) |
| `POST /v1/projects/{pid}/documents/{id}/versions/{n}/restore` | Contributor+ | → `201`, same shape |
| `DELETE /v1/projects/{pid}/documents/{id}/versions/{n}` | Maintainer+ | → `204` |
| `POST /v1/projects/{pid}/documents/{id}/merge` `{from}` | Maintainer+ | `{id}` = A, the one that stays; `from` = B, the one absorbed → `200` with A's row, shaped as in the listing |
| `DELETE /v1/projects/{pid}/documents/{id}` | Maintainer+ | → `204`. Removes the document and its whole history |

### 5.2 Changed. These are API breaks, recorded in the CHANGELOG

Nothing outside the SPA calls these routes: checked across `extension/`, `scripts/` and `deploy/`. The SPA is updated in the same change.

| Route | Change |
|---|---|
| `PUT /v1/projects/{pid}/docs/{doc_id}` | Returns `200`/`201 {document_id, version}` (was an empty `204`). Accepts `as_version_of` or `keep_both` query parameters. Can return `409 name_exists` / `older_version` (§4.2). |
| `DELETE /v1/projects/{pid}/docs/{doc_id}` | **Removed.** Use `DELETE …/documents/{id}`. |
| `GET /v1/projects`, `GET /v1/projects/{pid}`, `GET /v1/admin/projects` | `doc_count` counts **documents** (current versions), not version rows (§3.4). The shape is unchanged. |
| `GET /v1/docs` (Library) | Rows come from `listed_docs_where` (§2), so rows reached through a project are current versions only. The `projects` chips become `[{id, name, document_id, version, versions_count}]` and list only projects where this content is current (`versions_count` is the document's verified versions, which the chip's × confirmation names). New `newer_in` lists visible project documents where this content is an old version and not current; its items have the same shape as `versions_in` below, and `current_doc_id` and `current_name` are what *Open current* opens. (Changed in the build: the items carry `current_doc_id` and `current_name`, and the chips carry `versions_count`.) `?project_id=` filters to content current in that project. |
| `GET /v1/docs/{doc_id}` | New `versions_in: [{project: {id, name}, document_id, version, current_version, current_doc_id, current_name, is_current}]`: every visible project document whose verified history holds this content. `version` is its highest number there (restore can put the same content in one history twice). |

`versions_in` and `newer_in` only ever name **projects the caller is a member of**. Showing others would leak project names (the same rule as `_doc_projects_sql`).

### 5.3 Refusals

- **`404 not_found`:** anything the caller can't see, including a non-member's project, another project's document id, a missing version number, an unverified version, or a `from` document in another project. A merge checks that **both** documents are visible in the project *before* it checks for a self-merge: merging an invisible document into itself is a 404, and only a visible document gives `409 merge_self`. Otherwise the 409 would confirm that an id exists. "Not yours" and "not there" are indistinguishable, as everywhere since A1.
- **`403 insufficient_role`** (A0): a member below the route's role.
- **`409`:** a conflict with current state. Every `409` body names the document involved (`{document: {id, name, version}}`), so the app can say *which* one:

| Code | Meaning |
|---|---|
| `already_current` | the uploaded or restored content is already this document's current version (§4.3) |
| `current_elsewhere` | the content is another document's current version in this project |
| `older_version` | filing content that is an old version of a document (§4.2); the body also carries that version's number as `version` |
| `name_exists` | filing a name that matches a current document (§4.2) |
| `is_current` | deleting the current version: remove the document instead |
| `merge_self` | merging a document into itself |

## 6. Screens

**Project → Documents tab** ([ProjectDocsTab.jsx](../../../src/components/projects/ProjectDocsTab.jsx)). The listing switches from `GET /v1/docs?project_id=` to `GET …/documents`. Each row gains a version count that expands into an inline history. Actions that change history sit in a "⋯" menu, so the row fits at 390 px:

```
report-final.pdf  [indexed]  v3 · 3 versions ▾            [Open] [⋯] [×]
  ├ v3  current   report-final.pdf   Bob · 11 Oct   "fixed table 4"     Open
  ├ v2            report-final.pdf   Carol · 2 Oct   merged (was v1)    Open · Restore · Delete
  └ v1            report.pdf         Alice · 28 Sep                     Open · Restore · Delete

⋯ menu:  Upload new version          (can.file_docs)
         Mark as newer version of…   (can.remove_docs)
```

- **Upload new version** opens a strip under the row: *Choose file…* **or** *From my library ▾* (your own verified uploads), an optional *What changed?* note, then **Upload**. Toast: "report.pdf is now v4 (indexing…)", or "…restored v1" when §4.3 made it a restore. The state badge follows indexing as today.
- **Mark as newer version of…** asks you to pick another document in the project, then confirms inline: *"report-final.pdf becomes v2 of report.pdf, and the row will be called report-final.pdf. This can't be undone; every file stays in the history."*
- **Restore** (`can.file_docs`) and **Delete** (`can.remove_docs`, inline confirm, today's Remove pattern) sit on history lines. Neither is offered on the current version.
- **An open history stays current.** It refetches whenever its document's version or version count changes, whichever action or row caused it (this row's upload, another row's merge into it, the filing dialog). A history never shows a stale list under a header that has moved on.
- **Remove (×)** works as today, but its confirmation adds "…and its *N* versions" when *N* > 1.
- A refusal (`409`/`403`) is a toast with the server's message, followed by a reload, like the tab's existing `run` helper.

**Name-match dialog** (`NameMatchDialog.jsx`, new). This is an in-app dialog, never `window.confirm`. It's used by all three filing paths: the reader's upload-into-project ([docMeta.js](../../../src/lib/docMeta.js) `linkDocToProject`), the Documents tab's "File a document…", and the Library's "+ project" chip:

```
A document called report.pdf is already in Project X (v3).
[Add as new version]  [Keep both]  [Cancel]
```

For `older_version`, the first button reads **Restore v1 as current**. The chosen button retries the `PUT` with `as_version_of` or `keep_both`.

**Library** ([LibraryPage.jsx](../../../src/components/library/LibraryPage.jsx)):

- Chips read `[Project X · v3]`. The project's name opens the project's page; the version is plain text. (Changed in the build: only the name is the link.)
- Chip **×** (shown where you may remove documents from that project) removes the project's document *with its history*; with more than one version it first asks *Remove … and its N versions from …?*. With one version it removes straight away, as before.
- A row with `newer_in` shows a muted line: *Project X has a newer version (v3) · **Open current***. This is the agreed "two rows, clearly labelled" behaviour (option A, 2026-10-11): your personal copy stays yours, nobody else's action changes it, and you can remove it or open the current version.
- Version actions are **not** in the Library. They live on each project's Documents tab, so "which project's README am I changing?" always has exactly one answer.

**Activity tab** ([ActivityTab.jsx](../../../src/components/projects/ActivityTab.jsx)). The new kinds render as:

- *Bob uploaded v3 of report-final.pdf — "fixed table 4"*
- *Alice restored v1 of report.pdf (now v4)*
- *Carol merged report-final.pdf into report.pdf (2 versions)*
- *Carol deleted v2 of report.pdf*

## 7. Reader and chat

The reader and chat already address content by hash, and every version has its own hash. So "which version am I reading?" and "which version is chat answering from?" are already answered: whichever hash is open. V1 adds labels, not new plumbing.

- **Opening.** *Open* on a Documents row opens the current version's hash. *Open* on a history line opens that version. A citation opens the hash it was made against: [openDoc.js](../../../src/lib/openDoc.js) is unchanged.
- **The old-version banner.** A new hook, `useVersionStatus(docId)`, fetches `GET /v1/docs/{id}` when a document opens and again after a version upload, and reads `versions_in`. **Rule: show the banner when the content is an old version in at least one visible project and current in none.**

  ```
  Old version — v1 of report.pdf in Project X (current is v3) · Open current
  ```

  The banner is a strip in normal page flow **above** the viewer toolbar, so it never covers a control, and it wraps at phone width. It stays visible in distraction-free mode, because the warning matters most when nothing else is on screen. If it's old in several projects, the banner names the first by project name. The README case: content that's current in Project Y and old in Project X shows **no banner**, because it *is* a current document (Y's). X having moved on shows on your Library row instead.
- **Chat follows what you're reading.** With v1 open, "This document" chat answers from v1, and citations point at v1's pages. To stop the model presenting v1's facts as current, the open document's `ReadableDoc` gains a `version_note`, set under the banner's rule. The model then sees it in the tools' document listing ([scope.py](../../../server/chat/tools/scope.py) `documents_listing`):

  ```json
  {"ref": 1, "name": "report.pdf", "version_note": "version 1 in Project X; not the current version, which is version 3"}
  ```

  When the note is present, the system rules get one sentence: an older version's facts may be out of date, so say so when it matters. No note means current, and the prompt is unchanged, which keeps the common case's prefix cache. The chat's "using this document" label reads *report.pdf · v1 (old version)*.
- **One display name everywhere.** Today chat names a document by your entry's name, or else the *canonical* name ([doc_search.py:24](../../../server/services/doc_search.py) `readable_doc`), which is the stranger-name trap from §3.2. V1 adds one SQL helper, used by the Library listing, the reader's download name (`GET /v1/docs/{id}/file`) and chat, so they can't disagree. It tries, in order:
  1. your entry's name;
  2. a visible verified version's `file_name`, the current one first, then the newest;
  3. the canonical name.

  It replaces today's `_DISPLAY_NAME` and `_placement_name`, which borrow the filer's entry name.
- **Unchanged, deliberately:**
  - **Pins** carry the hash and the pinned text, so a pin keeps its meaning.
  - **Reading position** is stored per hash, so a new version opens at page 1. Carrying the page over would be wrong: page 12 of v2 isn't page 12 of v1.
  - **Index, convert and state** stay per content.
  - **The browser's local library** is unchanged.
- **Setup for C2.1.** The query behind `GET …/documents` (a project's current, verified versions) is a named function in one place. C2.1's project scope reuses it, so "project chat sees only current versions" has one definition, written once, not re-derived later.

## 8. Migration and upgrade

`018_document_versions.sql` runs in one transaction (the runner wraps each file), so the database is never half-converted. **No data is dropped.**

1. Create `project_docs`.
2. For each existing `project_documents` row:
   - create one `project_docs` row, with `created_at` = its `added_at`, `created_by` = its `added_by` and `last_version = 1`;
   - set `project_doc_id`, `version = 1`, `origin = 'upload'`, `is_current = true` on the placement, with `verified` unchanged.
3. `file_name` = **the name the project shows today**: `COALESCE(filer's entry file_name, documents.file_name)`, the same expression as `_placement_name`. Nothing visibly renames during the upgrade.
4. Set the new columns `NOT NULL`. Swap the primary key `(project_id, doc_id)` → `(project_doc_id, version)`. Add the composite FK and the two partial unique indexes. These can't fail on existing data, because the old primary key (one row per content per project) is stricter than "one *current* per content per project".
5. Keep `project_documents_doc_idx`, and add `project_documents_project_idx (project_id)`: the old primary key was the only index with `project_id` first. Record version 18.

**Existing duplicates are not merged automatically.** Same name doesn't mean same document, and only a person can tell. After the upgrade, existing duplicates have to be spotted by eye and merged with *Mark as newer version of…* (a "same name" marker was considered and deferred; §10).

**CHANGELOG upgrade note:** the API breaks from §5.2, and **take a `pg_dump` before upgrading**. The migration changes a primary key and has no down step, so the dump is the only rollback.

## 9. Testing

**Backend** (pytest against real Postgres; [docs/TESTING.md](../../TESTING.md) "How to run").

- **`test_migration_018.py`.** Seed a pre-018 database with: a normal placement; an unverified legacy placement; a placement whose filer renamed their entry; and one whose filer removed their entry. Each must come out as a current v1:
  - its `file_name` equals what the project displayed *before* the upgrade;
  - `verified` is preserved and `last_version = 1`;
  - the new keys and indexes exist.
- **`test_doc_versions.py`:**
  - uploading a version, with new content and with deduped content, from bytes and from your library;
  - `already_current`; an older match becoming a restore; `current_elsewhere`;
  - restore, including `current_elsewhere`;
  - deleting an old version: content GC'd when orphaned, kept when referenced elsewhere; `is_current`;
  - merge: order, numbering from `last_version`, `origin`/`origin_version`, B gone, B's hashes still readable, `merge_self`, `from` in another project → 404;
  - removing a document: every version's content GC'd;
  - numbers are never reused.
- **Roles.** Each action × reader / contributor / maintainer / owner / non-member / admin-non-member, in `server/tests/test_project_permission_matrix.py` (changed in the build: not `test_library_access_matrix.py`).
- **Leaks:**
  - `versions_in` and `newer_in` never name a project the caller isn't in;
  - a non-member gets 404 on every new route;
  - unverified versions appear nowhere.
- **Readable vs listed.** A member can open and search an old version. It never appears as a Library row, in `GET …/documents`, or in any `doc_count` (project, project list, admin console).
- **Legacy unverified rows.** Filing content that is an unverified current version verifies it in place. There's no `unique_violation`, and the row then behaves as in §4.2 step 1.
- **Concurrency:**
  - two simultaneous version uploads → v3 and v4, with no 500;
  - crossed merges → one wins, the other 404s, no deadlock;
  - a version write overlapping a project deletion → both complete, no deadlock (the `projects` key share is taken first);
  - a `unique_violation` is mapped to a 409, and a `DeadlockDetected` to `503 busy`.
- **Name match:**
  - `name_exists`, `as_version_of`, `keep_both`, `older_version`, and the precedence order of §4.2;
  - letter case and surrounding spaces are ignored;
  - the same name in another project doesn't prompt.
- **Upload pipeline.** A version upload adds no library entry. The GC-race retry still holds for the version holding.
- **Chat:**
  - an old version's listing carries `version_note`, and the system rules gain the sentence;
  - a current version has neither;
  - the display-name helper never returns a stranger's canonical name when a project version name exists.
- **Activity.** Each new kind is recorded. A refused or rolled-back action writes no event.

**Frontend** (vitest):

- **Documents tab:** expanding history; the upload strip (file, from library, note); the role gating of every control; the confirmation texts including "…and its *N* versions".
- **`NameMatchDialog`:** on all three filing paths, both 409 codes, and each button's retry.
- **Library:** chips with versions, and the `newer_in` line with *Open current*.
- **The banner rule:** including old-in-X-current-in-Y → no banner.
- **The chat label:** "v1 (old version)".

**Running-app walk.** Uses a throwaway Keycloak on :18081 and a scratch database, never the compose Keycloak. Two or three browser users, all from the UI:

| # | Journey |
|---|---|
| J1 | A Contributor uploads v2 from the row → one row, "v2 · 2 versions", indexing → indexed |
| J2 | Chat on the current version answers from v2. Open v1 from history → banner. Chat answers from v1 with the old-version label |
| J3 | Restore v1 → v3, with an Activity line |
| J4 | File `report.pdf` from your library → dialog → *Add as new version*. A different same-named file → *Keep both* |
| J5 | A Maintainer merges two duplicates. The confirmation text and the "merged" labels are right |
| J6 | A Maintainer deletes an old version. A Contributor sees no Delete |
| J7 | A Reader sees history and can Open, with no Upload or Restore |
| J8 | Alice's personal copy has fallen behind → Library label → *Open current* |
| J9 | README in two projects: v2 in X leaves Y unchanged, and the Library shows two rows |
| J10 | An old chat's citation opens v1, with the banner |
| J11 | The Documents tab at 390 px phone width |
| J12 | A copy of a pre-018 database upgrades: project documents appear as v1 with unchanged names |

**Docs updated in the same change:**

- [LIBRARY.md](../../LIBRARY.md): versions, readable vs listed, the API tables, schema 018, upgrade notes;
- [USER_GUIDE.md](../../USER_GUIDE.md): versions in "Projects and people";
- `CHANGELOG.md`: the API breaks and the `pg_dump` note;
- [TESTING.md](../../TESTING.md): the test map.

## 10. Out of scope (each is possible later; nothing here blocks it)

| Item | Where it belongs |
|---|---|
| Comparing two versions; review before a version becomes current | V2 |
| Project chat, multi-document hops, a larger `CHAT_MAX_TOOL_ROUNDS` budget for project scope (default 3; one hop costs about 2 rounds) | C2.1 |
| Versions in personal libraries | not planned (user decision) |
| Renaming a document without uploading a new version | later |
| Whole-project snapshots ("the project as of 1 October") | later |
| Un-merge | later |
| Storage quotas / version retention limits. Every version keeps its content and index until deleted; storage grows with each new content. | later |
| A "same name as another document" marker on the Documents tab | later (deferred 2026-10-12) |
| Backup guidance in `docs/DEPLOYMENT.md` | later (deferred 2026-10-12) |

## 11. Decisions log

| Decision | By | When |
|---|---|---|
| History lives only in projects; personal libraries stay flat | user | 2026-10-11 |
| GitLab model: restore appends a new version; citations open the exact version cited | user ("like GitLab") | 2026-10-11 |
| All three ways in: row action, exact-name prompt (letter case and surrounding spaces ignored), *Mark as newer version of…* | user (option C) | 2026-10-11 |
| Roles: Reader views, Contributor uploads and restores, Maintainer merges and deletes; V1 = unprotected main | user | 2026-10-11 |
| A personal copy that falls behind is a separate, labelled Library row (option A) | user | 2026-10-11 |
| Data model: placements become version rows (Approach 1), not new tables or a supersedes chain | user | 2026-10-11 |
| Histories are per project: five READMEs, five histories, shared storage | confirmed with the user | 2026-10-12 |
| Multi-document chat hops are C2.1; V1 provides "one current document per name" | confirmed with the user | 2026-10-12 |
| No additions beyond the agreed scope: the same-name marker and the DEPLOYMENT.md line are deferred | user | 2026-10-12 |
