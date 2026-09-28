# Library data model — many-to-many projects + one read predicate (C0)

**Date:** 2026-09-24
**Status:** Design — awaiting review
**Branch:** `development`
**Type:** backend (migration `010`) + frontend (Library, register flow) + docs
**Part of:** subsystem C, "agentic multi-document retrieval", decomposed 2026-09-24 into:

| Sub-project | Scope | Depends on |
|---|---|---|
| **C0 — this spec** | Many-to-many projects↔documents, one shared read predicate, project-owner read fix, link/unlink API, Library UI | — |
| C1 — orchestrator foundation | Provider adapter, backend chat orchestrator, typed event stream, SPA event renderer, parity with today's tools, Local Ollama mode removed | — |
| C2 — agentic retrieval | Doc descriptions (auto + owner override), per-user integer ref table, `list_library`, `search_documents` (scoped / project / library-wide), "docs used in this chat" note, bounded multi-round | C0, C1 |

C0 and C1 are independent. C2 builds on both, so its tools are written against the many-to-many
model from day one.

## 1. Summary

Today a document belongs to **at most one** project (`documents.project_id`, migration 009). This
spec replaces that column with a `project_documents` join table so one document can live in
several projects (e.g. a shared README used by two teams). While doing so it fixes a read-access
gap and collapses two hand-maintained copies of the access SQL into one.

## 2. The bug this fixes

**Project owners cannot read documents other members file into their project.**

- Read access is `owner ∨ project member ∨ grantee` (`server/auth/authz.py`).
- "Project member" means a row in `project_members`.
- `create_project` (`server/routers/projects.py`) inserts only into `projects`. The owner never
  gets a `project_members` row.

So if Bob, a member of Alice's project, files his doc there, every member can read it except
Alice, who owns the project. No test covers this case (`server/tests/test_library_authz.py`
tests owner-of-doc, member, grantee and stranger, but never owner-of-project). The 2026-09-17
library spec states `can_read = owner ∨ project member ∨ grantee` without considering project
owners, so this is an oversight, not intended behaviour.

**Fix:** the predicate treats **owning a project** that holds the doc as reading it. We do *not*
auto-insert the owner into `project_members`. That would store ownership twice, and the two copies
would drift if project ownership ever became transferable.

## 3. Goals / non-goals

**Goals**
- A document can be linked to zero, one or many projects.
- Exactly one SQL definition of "can read this document", used by every read path.
- Project owners read every document in their projects.
- Project owners can **remove** any document from their project, including ones other members
  filed. They cannot **add** someone else's document (see §6 — deliberate).
- API and UI for linking/unlinking; Library shows every project a doc is in.

**Non-goals (subsystem A or later)**
- Read/write permission tiers for project members. Membership and grants stay **read-only**.
- Admin managing every project.
- Multi-select at upload time. The upload picker stays single-select; more projects are added
  in the Library.
- Searching the Library by project name through `q`.

## 4. Schema — `server/sql/010_project_documents.sql`

```sql
CREATE TABLE IF NOT EXISTS project_documents (
    project_id UUID NOT NULL REFERENCES projects(id)        ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id)   ON DELETE CASCADE,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, doc_id)
);
-- PK serves project → docs lookups; this serves doc → projects (the read predicate).
CREATE INDEX IF NOT EXISTS project_documents_doc_idx ON project_documents(doc_id);

-- Carry every existing single-project assignment across.
INSERT INTO project_documents (project_id, doc_id)
SELECT project_id, doc_id FROM documents WHERE project_id IS NOT NULL
ON CONFLICT DO NOTHING;

DROP INDEX IF EXISTS documents_project_idx;
ALTER TABLE documents DROP COLUMN IF EXISTS project_id;

INSERT INTO schema_migrations(version) VALUES (10) ON CONFLICT DO NOTHING;
```

- The runner (`server/db.py`) applies each file in its own transaction, and Postgres DDL is
  transactional. If the backfill or the drop fails, the whole migration rolls back and
  `project_id` survives.
- The column is dropped **in the same migration**. Keeping it alongside the join table would
  leave two versions of the truth for code to disagree about.
- Forward-only, like every existing migration. The drop is irreversible, so a backfill test
  (§9) is required before this ships.
- Deleting a project removes its links; the documents survive. Deleting a document removes its
  links.
- **No `added_by` column.** Only a doc's owner can link it (§6), so "who linked this" would
  always equal `documents.user_id`: a copy, not information. If the link rule is ever relaxed
  so project owners can add others' documents, `added_by` becomes meaningful and arrives then
  as a small additive migration. `added_at` stays: when a link was made can't be derived from
  anything else.
- `ON CONFLICT DO NOTHING` can never fire (each doc has at most one `project_id`), and dropping
  the column would drop its index anyway. Both are kept on purpose, as explicit statements of
  intent.

## 5. Access model — one predicate

### 5.1 Definition

A user **can read** a document iff any of the following holds:

1. they own it (`documents.user_id`);
2. they hold a grant for it (`doc_grants`);
3. it is linked to a project they **own**;
4. it is linked to a project they are a **member** of.

```sql
(d.user_id = %s
 OR EXISTS (SELECT 1 FROM doc_grants g
            WHERE g.doc_id = d.doc_id AND g.grantee_user_id = %s)
 OR EXISTS (SELECT 1 FROM project_documents pd
            JOIN projects p ON p.id = pd.project_id
            WHERE pd.doc_id = d.doc_id
              AND (p.owner_user_id = %s
                   OR EXISTS (SELECT 1 FROM project_members pm
                              WHERE pm.project_id = p.id AND pm.user_id = %s))))
```

### 5.2 One definition, parameters that can't drift

Today `readable_docs_where()` returns the SQL and every caller appends the user id **three
times by hand** (`params = [uid, uid, uid]` in `list_documents`). `assert_can_read_doc` carries
its own copy of the same SQL. With the new predicate taking **four** user ids, a hand-counted
list is how a caller ends up binding the wrong value to the wrong placeholder: at best a
psycopg error, at worst a silent access check against the wrong column.

`server/auth/authz.py` therefore exposes:

- `readable_docs_where(alias="d") -> str` — the predicate above;
- `readable_docs_params(user_id) -> list` — exactly the parameters it needs, in order.

Every caller uses both together. `assert_can_read_doc` becomes a thin wrapper:
`SELECT 1 FROM documents d WHERE d.doc_id = %s AND <predicate>` with
`[doc_id, *readable_docs_params(user_id)]`. `CAN_READ_DOCS_SQL` stays as an alias for existing
imports. A unit test asserts the placeholder count equals `len(readable_docs_params(...))`, and
a second one does the same for `assert_can_read_doc`'s full query, whose leading `doc_id`
comes before the four user ids. That ordering is exactly what a hand-built list gets wrong.

### 5.3 Projects visible to a user

Used by the Library response and the link rules below:
`p.owner_user_id = uid OR EXISTS (member row)`. Exposed as `visible_projects_where(alias="p")`
plus `visible_projects_params(user_id)` in `authz.py`, following the same pattern.

## 6. Write rules

| Action | Allowed when | Otherwise |
|---|---|---|
| **Link** doc → project | Caller **owns the doc** AND the project is **visible** to them (owner or member). Admins skip the visibility check but must still own the doc, same as today's `PATCH` escape hatch. | Project not visible → `404 Project not found`. Doc not owned → `404 Document not found`. The project is checked first, so an invisible project can't be probed through doc ids. |
| **Unlink** doc from project | See the resolution rule below | `404` (no existence leak) |

**Unlink resolution** — "idempotent" and "no existence leak" only fit together if the answer
depends on who is asking and whether the link exists:

| Link row | Caller owns the doc | Caller owns the project (not the doc) | Anyone else |
|---|---|---|---|
| exists | `204`, row deleted | `204`, row deleted | `404` |
| missing | `204` (idempotent) | `404` | `404` |

- The doc owner always gets `204`, so nothing is learned about the project.
- A project owner learns only whether a doc is linked to *their own* project, which they can
  already see in the Library.
- A plain project member **cannot** unlink someone else's document.
- Linking is idempotent the same way: re-linking an existing pair is `204`.
- Neither operation changes the document's ownership, tags or chunks.

**Curation is deliberately asymmetric.** A project owner can remove others' documents from their
project but cannot add a document they merely read (e.g. one granted to them). A relaxed rule —
link when the caller owns the doc, **or** owns the project **and** can read the doc — would be
coherent, since read access already spreads through project membership. C0 keeps the
least-privilege rule, which matches today's `PATCH` rule exactly. `docs/LIBRARY.md` records
this as a decision so it isn't later filed as a bug. Relaxing it is a subsystem A question and
brings `added_by` back (§4).

## 7. API changes

### 7.1 New — `server/routers/projects.py`

- `PUT /v1/projects/{project_id}/docs/{doc_id}` → link (204).
- `DELETE /v1/projects/{project_id}/docs/{doc_id}` → unlink (204).
- `doc_id` is validated with the same `^[0-9a-f]{64}$` pattern as the docs router, and
  `project_id` as a UUID. A malformed value is `422`, never `500`.
- Both require the `reader` capability, the same gate the docs router uses for writes.

### 7.2 Changed — `server/routers/docs.py`

- **`PATCH /v1/docs/{doc_id}` loses `project_id`.** `DocPatchIn` is `extra="forbid"`, so
  sending it becomes `422`. Tags and admin owner reassignment are unchanged. **This is a
  breaking API change** and goes in the CHANGELOG. The only in-repo callers are the SPA
  (`src/lib/docMeta.js`, `LibraryPage.jsx`), both updated here. The extension does not use it.
- **`GET /v1/docs`**
  - The `project_id` filter becomes `EXISTS (… project_documents … = %s)` **and** requires the
    project to be visible to the caller (§5.3). Without that gate, filtering a granted doc by a
    guessed project UUID would reveal whether its owner linked it there. UUIDs make that
    near-impossible, but "you can only filter by projects you can see" is the cleaner rule
    anyway. An invisible project id returns an empty list, not an error.
  - Each row returns `projects: [{id, name}]` (sorted by name) in place of `project_id` and
    `project_name`.
  - **Only projects visible to the caller are listed — except that a doc's owner sees every
    project their doc is linked to.** A doc shared by grant may also sit in projects the caller
    has no access to. Listing those would leak their names. The owner exception closes a
    management gap: if the owner is removed from a project, their doc stays linked to it (and
    readable by its members). Hiding that link would leave the owner unable to see, or remove
    in the UI, where their own document is shared. It leaks nothing new: every link on the doc
    was made by them or a previous owner.
- **`GET /v1/docs/{doc_id}`, register, chunk upload, PATCH responses**
  - `_fetch_doc_status` returns `projects` instead of `project_id`, filtered the same way.
  - Its signature gains `user_id`. All four call sites already hold the principal.

## 8. Frontend

- **`LibraryPage.jsx`** — each doc row shows one chip per project.
  - If the caller owns the doc, an "add to project" select lists the visible projects not
    already linked (link on change).
  - A chip gets a remove (×) button when the caller owns the doc **or** owns that project. That
    means using `is_owner` from `GET /v1/projects`, which the page already loads.
  - The project filter keeps its `project_id` query parameter.
- **`src/lib/docMeta.js` `registerDocument`** — a chosen project is now linked with
  `PUT /v1/projects/{pid}/docs/{doc_id}` after register. Tags still go through `PATCH`. A failed
  link keeps today's rule: logged, not thrown, because indexing must not be blocked by a
  metadata failure.
- The upload picker (`useDocMetaPicker`, `App.jsx`) stays single-select.

## 9. Testing

Backend (pytest, table-driven where it's a matrix):

- **Access matrix:** doc owner · grantee · project owner · project member · stranger ×
  (doc in 0 / 1 / 2 projects).
  - Includes the regression case: **project owner reads a member-filed doc**.
  - Includes: a member of *either* of a doc's two projects can read it.
  - Every case goes through both `assert_can_read_doc` and the `GET /v1/docs` list, so the two
    read paths are proven to agree.
- **Link rules:** own doc + visible project ✓; own doc + invisible project → 404; foreign doc →
  404; granted (readable, not owned) doc into own project → 404, which pins the asymmetry in
  §6; admin cross-link of own doc ✓; idempotent re-link; malformed ids → 422.
- **Unlink rules:** every cell of the §6 resolution table (link exists/missing × doc owner /
  project owner / other).
- **Visibility:** `GET /v1/docs` never lists a project name a non-owner can't see; a doc's
  owner sees all its links, including projects they have left; `?project_id=` with an
  invisible project returns an empty list.
- **PATCH:** `project_id` in the body → 422; tags and owner reassignment unchanged.
- **Placeholder guard:** placeholder count == `len(readable_docs_params(uid))`; the same for
  `assert_can_read_doc`'s full query (leading `doc_id`) and for the project helpers.
- Existing files updated for the new shape: `test_docs_patch.py`, `test_docs_list.py`,
  `test_library_authz.py`, `test_library_schema.py`.

**Migration 010 needs its own scratch database.** The normal harness can't test it.
`server/tests/conftest.py` applies every migration to the permanent `natural_reader_test`
database once per session and records them in `schema_migrations`. By the time a test runs,
010 has already dropped `project_id`, so pre-010 rows can't be seeded. The existing migration
tests (`test_migration_005.py` etc.) only check end state, which works for "column exists" but
not for a backfill, whose correctness depends on the state the migration destroys. So
`test_migration_010.py`:

1. creates a uniquely named scratch database (`natural_reader_mig_<random>`) through the same
   autocommit admin connection `_ensure_test_db()` uses. The random suffix keeps parallel or
   crashed runs from colliding;
2. applies migrations **001–009 only**. `conftest._apply_migrations` is refactored to take a
   database URL and an optional `max_version`, so the test reuses it instead of copying the
   loop;
3. seeds users, two projects, and docs with `project_id` set, plus one with `NULL`;
4. runs `server/sql/010_project_documents.sql` **read from disk**, the real file and not a
   retyped copy;
5. asserts one link per non-null assignment, none for the `NULL` doc, that
   `documents.project_id` is gone, and that `documents_project_idx` is gone;
6. drops the scratch database in a `finally` block.

Frontend (vitest):

- `LibraryPage.test.jsx`: chips render; the add select links; × appears only for the doc owner
  or project owner; unlink refreshes the list.
- `docMeta.test.js`: register + link path; link failure is logged, not thrown.

## 10. Docs

- `docs/LIBRARY.md`: the access rule (with project owners), the join table, and the link/unlink
  endpoints.
- `docs/ARCHITECTURE.md`: migration 010 and the `authz.py` helpers.
- `docs/LIBRARY.md` also records the curation asymmetry (§6) as a decision.
- `CHANGELOG.md` `[Unreleased]`: added (many-to-many), fixed (project-owner read), and the
  breaking change: `PATCH /v1/docs` rejects `project_id` with 422, **which also fails any tag
  update sent in the same request**.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Dropping `project_id` loses data if the backfill is wrong | Single-transaction migration + the scratch-database backfill test in §9 |
| Parameter-count drift in the read predicate | `*_params()` helpers + the placeholder-count tests |
| Leaking foreign project names through `projects[]` or `?project_id=` | Visibility filter on both (doc owners excepted, §7.2) + dedicated tests |
| Old SPA build sends `{project_id, tags}` in one `PATCH` → 422, so **the tags are lost too**, not just the project | SPA and backend ship together (same origin, same deploy). The register flow logs the failure rather than throwing, so indexing still proceeds. *Assumed, not verified:* that every deployment updates both at once. That depends on your deploy setup |
| Read predicate cost (two `EXISTS` with a join) on large libraries | Covered by `project_documents_doc_idx`, the `projects` PK and `project_members` PK / `project_members_user_idx`; check `EXPLAIN` on `GET /v1/docs` with a seeded library during implementation |
