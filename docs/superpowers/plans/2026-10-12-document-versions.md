# V1 Document Versions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A revised file becomes a new version of a document in a project. The project shows each document once with its history; listings and chat see only the current version; old versions stay openable, restorable, mergeable and deletable from the UI.

**Architecture:** Migration 018 turns every `project_documents` row into a *version* of a new `project_docs` row ("a document in a project"). An `is_current` flag plus two partial unique indexes enforce one current version per document, and one current document per content per project. A new service, `server/services/doc_versions.py`, owns every write to a history and the queries that read one. A new router, `server/routers/project_documents.py`, exposes them. The read predicate and GC are unchanged, because history rows are still placements. Listings and counts filter to `is_current`. The SPA gains a history view on the project Documents tab, a name-match dialog on every filing path, version chips and a "newer version" line in the Library, an old-version banner in the reader, and a version note for chat.

**Tech Stack:** Python 3.13, FastAPI 0.142, psycopg 3 (async), Postgres 16 + pgvector; React 18, Vite, vitest + Testing Library, Tailwind, lucide-react.

**Spec:** `docs/superpowers/specs/2026-10-12-document-versions-design.md`. Read it before any task: this plan argues from it. Section numbers below (§n) are the spec's.

## Global Constraints

- **No new env vars** (spec header). Nothing deployment-specific is hardcoded.
- **Refusals:**
  - `404 not_found` for anything the caller can't see: "not yours" and "not there" are indistinguishable;
  - `403 insufficient_role` for a member below the route's role;
  - `409` with the codes `already_current`, `current_elsewhere`, `older_version`, `name_exists`, `is_current`, `merge_self`. Every 409 body carries `document: {id, name, version}` (§5.3).
- **Roles** (§4.1). Reader+ reads history. Contributor+ (`can.file_docs`) uploads and restores. Maintainer+ (`can.remove_docs`) merges, deletes versions and removes documents. Admins act **only through a member role**: every document and version route calls `require_project_role(..., admin_ok=False)`.
- **Check, then write.** Every refusal is raised before the first write. A write that can fail on a constraint runs inside `async with conn.transaction():` (a savepoint), and the caught `pg_errors.UniqueViolation` becomes a 409. This follows the pattern at `server/routers/docs.py` `add_share`.
- **Lock order is `projects` → `project_docs` → `documents`, everywhere** (§4.4). The project's row is taken `FOR KEY SHARE` first (corrected after the final review: without it, a version write and `delete_project` can deadlock), then the document's row `FOR UPDATE`, before any content row is touched. A merge locks both documents in id order.
- **Query groups** (§3.4). Every new or changed query on `project_documents` carries a comment, `-- HISTORY` (every verified version) or `-- CURRENT` (the verified current version).
- **Audit and INFO logs** carry only IDs, roles, counts and version numbers: **never** file names, notes, emails or project names. This is enforced by `test_no_personal_data_at_info_or_above` in `server/tests/test_project_permission_matrix.py`.
- **UI:**
  - no `window.confirm`, `alert` or `prompt`: confirmations are inline, dialogs are in-app;
  - every new control works at **390 px** phone width;
  - every control has an `aria-label` or visible text that the tests query by.
- **Commits: never commit without the user's explicit permission for that commit** (the user's global rule overrides the skill). Each task's last step says: stop, show the diff summary, ask, and commit only on a yes.
- **Running tests.** The backend tests need Postgres:

  ```bash
  env -u XDG_DATA_HOME podman-compose up -d postgres
  .venv/bin/python -m pytest server/tests -q
  npx vitest run
  npm run lint
  ```
- **Never use the compose Keycloak for walks:** it is the user's real identity server. Task 16 uses a throwaway one.

## Review Focus

Five inputs the spec implies but no other task's tests exercise. These are the ones most likely to bite a real user. Each has a test in the task named:

1. **The filer renames or removes their personal copy after filing.** The project keeps showing the name as filed. This changes today's behaviour, where the project name followed the filer's entry. → Task 3, `test_project_name_stays_as_filed_after_the_filer_renames_their_copy`.
2. **"Upload new version" with a file named like a *different* document in the project.** It's accepted with no name prompt: the row action is explicit, and the prompt belongs only to filing. → Task 6, `test_row_upload_ignores_another_documents_name`.
3. **Cancel on the name-match dialog.** Nothing is filed, no error toast appears, and the file stays in the library. → Task 11, `cancel files nothing and shows no error`.
4. **A whitespace-only note** is stored as no note. **A 501-character note** is a 422, raised before any bytes are stored, so no staged file is left behind. → Task 6, `test_note_rules_apply_before_bytes_are_stored`.
5. **Deleting an old version whose content someone still holds in their library.** The content survives and stays readable to them; only the version row goes. → Task 4, `test_delete_keeps_content_someone_still_holds`.

## File map

| File | Responsibility | Task |
|---|---|---|
| `server/sql/018_document_versions.sql` (new) | schema: `project_docs`, versioned `project_documents`, indexes, backfill | 1 |
| `server/tests/seed.py` | `place_doc` creates a v1 document and returns its id; `add_version` builds histories | 1 |
| `server/services/doc_versions.py` (new) | every history write and read, the display-name and `versions_in` SQL | 2, 4, 7 |
| `server/auth/authz.py` | `listed_docs_where` next to `readable_docs_where` | 2 |
| `server/routers/docs.py` | Library listing (listed, chips, `newer_in`), `versions_in`, display name, `_store_and_hold` | 3, 6 |
| `server/routers/projects.py` | `doc_count`, delete-project GC, the filing route | 1, 3, 7 |
| `server/routers/admin.py` | admin `doc_count` | 3 |
| `server/services/project_events.py` | four new event kinds | 4 |
| `server/routers/project_documents.py` (new) | `/v1/projects/{pid}/documents…` routes | 5, 6 |
| `server/app.py` | mount the new router | 5 |
| `server/services/doc_search.py`, `server/chat/tools/scope.py`, `server/chat/prompt.py` | chat's display name and version note | 9 |
| `src/lib/projectsApi.js`, `src/lib/docMeta.js` | new API calls; filing returns its outcome | 10 |
| `src/components/projects/NameMatchDialog.jsx`, `src/hooks/useProjectFiling.js` (new) | the name-match prompt | 11 |
| `src/components/projects/ProjectDocsTab.jsx`, `ProjectDocRow.jsx`, `VersionHistory.jsx` (new) | the Documents tab | 12 |
| `src/components/library/LibraryPage.jsx` | chips, chip ×, `newer_in`, filing via the dialog | 13 |
| `src/lib/versions.js`, `src/hooks/useVersionStatus.js`, `src/components/OldVersionBanner.jsx` (new), `src/App.jsx`, `src/components/ChatView.jsx`, `src/components/projects/ActivityTab.jsx` | reader banner, chat label, activity lines, App wiring | 11, 14 |
| `docs/LIBRARY.md`, `docs/USER_GUIDE.md`, `CHANGELOG.md`, `docs/TESTING.md`, the spec | docs | 15 |

---

### Task 1: Migration 018, seed helpers, and keeping today's filing routes working

After this task every placement is v1 of its own document. The existing link and unlink routes still behave exactly as before; Task 7 replaces them.

**Files:**
- Create: `server/sql/018_document_versions.sql`
- Create: `server/tests/test_migration_018.py`
- Create: `server/tests/test_document_versions_schema.py`
- Modify: `server/tests/seed.py` (`place_doc`, new `add_version`)
- Modify: `server/routers/projects.py:196-261` (`link_doc`, `unlink_doc`; delete `_placement_name`)

**Interfaces:**
- Produces:
  - tables `project_docs(id, project_id, created_at, created_by, last_version)` and `project_documents(+ project_doc_id, version, file_name, note, origin, origin_version, is_current)`;
  - indexes `project_documents_one_current` and `project_documents_current_content`;
  - `seed.place_doc(conn, project_id, doc_id, added_by, *, verified=True, file_name=None) -> str` (the project document id);
  - `seed.add_version(conn, project_doc_id, doc_id, added_by, *, file_name=None, verified=True, origin="upload", origin_version=None, note=None, current=True) -> int` (the new version number).

- [ ] **Step 1: Write the failing migration test**

`server/tests/test_migration_018.py`:

```python
"""Migration 018 (V1 spec §8): every placement becomes version 1 of its own
project document, current, named as the project showed it before the
upgrade. No data is dropped."""
import secrets

import pytest
from psycopg import AsyncConnection

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

A, B, C, L = "a" * 64, "b" * 64, "c" * 64, "e" * 64


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _user(conn, sub):
    return (await _one(
        conn,
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i', %s, %s, 'member', 'active') RETURNING id", (sub, f"{sub}@x.io")))[0]


async def _scratch_db():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    await admin.execute(f'CREATE DATABASE "{name}"')
    return admin, name


async def _drop(admin, name):
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def test_018_turns_every_placement_into_a_current_v1_named_as_before():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url, max_version=17)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            alice, bob = await _user(conn, "alice"), await _user(conn, "bob")
            pid = (await _one(conn, "INSERT INTO projects (created_by, name) "
                                    "VALUES (%s,'P') RETURNING id", (alice,)))[0]
            for doc in (A, B, C, L):
                await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
                    "extracted_by) VALUES (%s,%s,'pdf',1,'indexed','server')",
                    (doc, f"{doc[0]}-canonical.pdf"))
            # A: alice's entry keeps the canonical name (file_name NULL).
            # B: bob renamed his copy before filing it: the project showed his name.
            # C: nobody holds an entry any more: the project showed the canonical name.
            # L: a legacy placement nobody proved (migration 012).
            await conn.execute("INSERT INTO library_entries (user_id, doc_id, added_via, verified) "
                               "VALUES (%s,%s,'upload',true)", (alice, A))
            await conn.execute("INSERT INTO library_entries (user_id, doc_id, file_name, added_via, "
                               "verified) VALUES (%s,%s,'Bob notes.pdf','upload',true)", (bob, B))
            for doc, by, verified in ((A, alice, True), (B, bob, True), (C, alice, True),
                                      (L, alice, False)):
                await conn.execute(
                    "INSERT INTO project_documents (project_id, doc_id, added_by, verified) "
                    "VALUES (%s,%s,%s,%s)", (pid, doc, by, verified))

            async with conn.transaction():  # one transaction, like server/db.py
                await conn.execute((SQL_DIR / "018_document_versions.sql").read_text())

            cur = await conn.execute(
                "SELECT doc_id, version, file_name, origin, is_current, verified, project_doc_id "
                "FROM project_documents")
            rows = {r[0]: r[1:] for r in await cur.fetchall()}
            assert {d: r[:5] for d, r in rows.items()} == {
                A: (1, "a-canonical.pdf", "upload", True, True),
                B: (1, "Bob notes.pdf", "upload", True, True),
                C: (1, "c-canonical.pdf", "upload", True, True),
                L: (1, "e-canonical.pdf", "upload", True, False),
            }
            assert len({r[5] for r in rows.values()}) == 4  # one document per placement
            cur = await conn.execute(
                "SELECT id, project_id, created_by, last_version FROM project_docs")
            docs = {r[0]: r[1:] for r in await cur.fetchall()}
            assert set(docs) == {r[5] for r in rows.values()}
            assert all(p == pid and lv == 1 for p, _by, lv in docs.values())
            assert docs[rows[B][5]][1] == bob  # created_by = who filed it
            assert await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 18") == (1,)
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)
```

- [ ] **Step 2: Write the failing schema tests**

`server/tests/test_document_versions_schema.py`:

```python
"""The guarantees migration 018's constraints make (V1 spec §3.2)."""
import pytest
from psycopg import errors as pg_errors

from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2 = "1" * 64, "2" * 64


async def _setup(db_conn):
    alice = await member(db_conn, "sch-alice")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="a.pdf")
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name="b.pdf")
    return alice, pid


async def test_a_document_has_one_current_version(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    with pytest.raises(pg_errors.UniqueViolation):
        async with db_conn.transaction():
            await db_conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, origin, is_current, added_by, verified) "
                "VALUES (%s,%s,2,%s,'b.pdf','upload',true,%s,true)",
                (pid, pdoc, C2, alice.user_id))


async def test_content_is_current_in_at_most_one_document_per_project(db_conn):
    alice, pid = await _setup(db_conn)
    await seed.place_doc(db_conn, pid, C1, alice.user_id)
    with pytest.raises(pg_errors.UniqueViolation):
        async with db_conn.transaction():
            await seed.place_doc(db_conn, pid, C1, alice.user_id)
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    await seed.place_doc(db_conn, other, C1, alice.user_id)  # another project: fine


async def test_a_version_cannot_belong_to_another_projects_document(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    with pytest.raises(pg_errors.ForeignKeyViolation):
        async with db_conn.transaction():
            await db_conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, origin, is_current, added_by, verified) "
                "VALUES (%s,%s,2,%s,'b.pdf','upload',false,%s,true)",
                (other, pdoc, C2, alice.user_id))


async def test_a_note_is_at_most_500_characters(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    with pytest.raises(pg_errors.CheckViolation):
        async with db_conn.transaction():
            await seed.add_version(db_conn, pdoc, C2, alice.user_id, note="x" * 501)


async def test_removing_a_document_removes_its_versions_not_held_content(db_conn):
    alice, pid = await _setup(db_conn)
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, pdoc, C2, alice.user_id)
    await db_conn.execute("DELETE FROM project_docs WHERE id = %s", (pdoc,))
    cur = await db_conn.execute("SELECT count(*) FROM project_documents WHERE project_id = %s", (pid,))
    assert (await cur.fetchone())[0] == 0
    cur = await db_conn.execute("SELECT count(*) FROM documents WHERE doc_id IN (%s,%s)", (C1, C2))
    assert (await cur.fetchone())[0] == 2  # alice's entries still hold both


async def test_seed_names_a_placement_like_migration_018(db_conn):
    alice, pid = await _setup(db_conn)
    await db_conn.execute("UPDATE library_entries SET file_name = 'Mine.pdf' "
                          "WHERE user_id = %s AND doc_id = %s", (alice.user_id, C1))
    pdoc = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    cur = await db_conn.execute(
        "SELECT version, file_name, is_current FROM project_documents WHERE project_doc_id = %s",
        (pdoc,))
    assert await cur.fetchone() == (1, "Mine.pdf", True)
    n = await seed.add_version(db_conn, pdoc, C2, alice.user_id)
    cur = await db_conn.execute(
        "SELECT version, is_current FROM project_documents WHERE project_doc_id = %s "
        "ORDER BY version", (pdoc,))
    assert n == 2 and await cur.fetchall() == [(1, False), (2, True)]
```

- [ ] **Step 3: Run both to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_migration_018.py server/tests/test_document_versions_schema.py -q`
Expected: FAIL. The migration file doesn't exist, and the `project_docs` table doesn't exist.

- [ ] **Step 4: Write the migration**

`server/sql/018_document_versions.sql`:

```sql
-- V1 (spec docs/superpowers/specs/2026-10-12-document-versions-design.md §3, §8):
-- every placement becomes version 1 of its own project document. A project
-- document (project_docs) is "a document in a project"; each of its versions
-- is a project_documents row. One transaction (the runner wraps each file).
-- No data is dropped, but the primary key of project_documents changes and
-- there is no down step: take a pg_dump before upgrading.

CREATE TABLE IF NOT EXISTS project_docs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    -- The highest version number ever issued (spec §3.2): never reused.
    last_version INT NOT NULL CHECK (last_version >= 1),
    UNIQUE (id, project_id)
);
CREATE INDEX IF NOT EXISTS project_docs_project_idx ON project_docs(project_id);

ALTER TABLE project_documents
    ADD COLUMN IF NOT EXISTS project_doc_id UUID,
    ADD COLUMN IF NOT EXISTS version        INT,
    ADD COLUMN IF NOT EXISTS file_name      TEXT,
    ADD COLUMN IF NOT EXISTS note           TEXT CHECK (char_length(note) <= 500),
    ADD COLUMN IF NOT EXISTS origin         TEXT CHECK (origin IN ('upload', 'restore', 'merge')),
    ADD COLUMN IF NOT EXISTS origin_version INT,
    ADD COLUMN IF NOT EXISTS is_current     BOOLEAN;

-- One document per existing placement: v1, current, named as the project
-- shows it today (projects.py _placement_name): the filer's entry name, else
-- the canonical name. Nothing visibly renames during the upgrade.
UPDATE project_documents
   SET project_doc_id = gen_random_uuid(), version = 1, origin = 'upload', is_current = true
 WHERE project_doc_id IS NULL;
UPDATE project_documents pd
   SET file_name = COALESCE(
       (SELECT e.file_name FROM library_entries e
         WHERE e.user_id = pd.added_by AND e.doc_id = pd.doc_id),
       (SELECT d.file_name FROM documents d WHERE d.doc_id = pd.doc_id))
 WHERE pd.file_name IS NULL;
INSERT INTO project_docs (id, project_id, created_at, created_by, last_version)
SELECT project_doc_id, project_id, added_at, added_by, 1 FROM project_documents;

ALTER TABLE project_documents
    ALTER COLUMN project_doc_id SET NOT NULL,
    ALTER COLUMN version        SET NOT NULL,
    ALTER COLUMN file_name      SET NOT NULL,
    ALTER COLUMN origin         SET NOT NULL,
    ALTER COLUMN is_current     SET NOT NULL;

-- The old key (one row per content per project) is stricter than "one
-- CURRENT row per content per project", so these can't fail on existing data.
ALTER TABLE project_documents DROP CONSTRAINT IF EXISTS project_documents_pkey;
ALTER TABLE project_documents ADD CONSTRAINT project_documents_pkey
    PRIMARY KEY (project_doc_id, version);
ALTER TABLE project_documents ADD CONSTRAINT project_documents_project_doc_fkey
    FOREIGN KEY (project_doc_id, project_id)
    REFERENCES project_docs(id, project_id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS project_documents_one_current
    ON project_documents (project_doc_id) WHERE is_current;
-- The duplicate guard (spec §3.2): content is current in ≤ 1 document per project.
CREATE UNIQUE INDEX IF NOT EXISTS project_documents_current_content
    ON project_documents (project_id, doc_id) WHERE is_current;
-- project_documents_doc_idx (doc_id) stays: it serves the read predicate.

INSERT INTO schema_migrations(version) VALUES (18) ON CONFLICT DO NOTHING;
```

- [ ] **Step 5: Update the seed helpers**

In `server/tests/seed.py`, replace `place_doc` and add `add_version` right after it:

```python
async def place_doc(conn, project_id, doc_id, added_by, *, verified=True, file_name=None):
    """File content into a project as a NEW document: version 1, current
    (V1 spec §3). Named like migration 018 names a placement: `file_name`,
    else the filer's entry name, else the canonical name. Returns the
    project document's id."""
    cur = await conn.execute(
        "INSERT INTO project_docs (project_id, created_by, last_version) "
        "VALUES (%s,%s,1) RETURNING id", (project_id, added_by))
    pdoc = str((await cur.fetchone())[0])
    await conn.execute(
        "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, file_name, "
        "origin, is_current, added_by, verified) VALUES (%s,%s,1,%s,COALESCE(%s,"
        "(SELECT e.file_name FROM library_entries e WHERE e.user_id = %s AND e.doc_id = %s),"
        "(SELECT d.file_name FROM documents d WHERE d.doc_id = %s)),'upload',true,%s,%s)",
        (project_id, pdoc, doc_id, file_name, added_by, doc_id, doc_id, added_by, verified))
    return pdoc


async def add_version(conn, project_doc_id, doc_id, added_by, *, file_name=None, verified=True,
                      origin="upload", origin_version=None, note=None, current=True):
    """Append a version to a project document WITHOUT the service's rules,
    for building histories in tests. `current=True` makes it the current one.
    Named `file_name`, else the content's canonical name. Returns its number."""
    cur = await conn.execute(
        "UPDATE project_docs SET last_version = last_version + 1 WHERE id = %s "
        "RETURNING last_version, project_id", (project_doc_id,))
    n, project_id = await cur.fetchone()
    if current:
        await conn.execute("UPDATE project_documents SET is_current = false "
                           "WHERE project_doc_id = %s AND is_current", (project_doc_id,))
    await conn.execute(
        "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, file_name, "
        "note, origin, origin_version, is_current, added_by, verified) VALUES (%s,%s,%s,%s,"
        "COALESCE(%s,(SELECT d.file_name FROM documents d WHERE d.doc_id = %s)),%s,%s,%s,%s,%s,%s)",
        (project_id, project_doc_id, n, doc_id, file_name, doc_id, note, origin, origin_version,
         current, added_by, verified))
    return n
```

Also update the module docstring's last paragraph by appending: `A placement is a project document's version (V1, migration 018): place_doc makes a new document at v1; add_version appends to one.`

- [ ] **Step 6: Keep the two filing routes working on the new schema**

The old `INSERT … ON CONFLICT (project_id, doc_id)` has no unique constraint to target any more. In `server/routers/projects.py`:
- delete `_placement_name` (lines 206–215);
- replace `link_doc` and `unlink_doc` (lines 218–259) with the versions below.

Task 7 replaces both. Until then they behave exactly as before for single-version documents.

```python
@router.put("/{project_id}/docs/{doc_id}", status_code=204)
async def link_doc(project_id: uuid.UUID, doc_id: DocId,
                   principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """File a doc into a project: a Contributor or above (admins only through
    a member role, A0 §5) who holds a VERIFIED upload entry for it (A1 §5).
    The project is checked first, so an invisible project 404s before doc
    ids can be used to probe it. Filing creates a new project document at v1
    (migration 018). Idempotent: content already current in the project
    writes no event — unless that version is an UNVERIFIED legacy one, which
    this verified filing proves. (V1 Task 7 replaces this with the
    name-match flow.)"""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    await assert_holds_upload(conn, doc_id, principal.user_id)
    # CURRENT, verified or not: the content's current version in this project.
    cur = await conn.execute(
        "UPDATE project_documents SET added_by = %s, verified = true "
        "WHERE project_id = %s AND doc_id = %s AND is_current AND NOT verified RETURNING 1",
        (principal.user_id, project_id, doc_id))
    changed = await cur.fetchone() is not None
    if not changed:
        cur = await conn.execute(  # CURRENT
            "SELECT 1 FROM project_documents WHERE project_id = %s AND doc_id = %s AND is_current",
            (project_id, doc_id))
        if await cur.fetchone() is None:
            cur = await conn.execute(
                "INSERT INTO project_docs (project_id, created_by, last_version) "
                "VALUES (%s,%s,1) RETURNING id", (project_id, principal.user_id))
            pdoc = (await cur.fetchone())[0]
            await conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, origin, is_current, added_by, verified) "
                "VALUES (%s,%s,1,%s,%s,'upload',true,%s,true)",
                (project_id, pdoc, doc_id, await _filed_name(conn, doc_id, principal.user_id),
                 principal.user_id))
            changed = True
    if changed:
        audit("placement.added", project=project_id, doc=doc_id, by=principal.user_id)
        await project_events.record(
            conn, project_id, principal.user_id, "document.added", doc_id=doc_id,
            details={"name": await _filed_name(conn, doc_id, principal.user_id)})
    return Response(status_code=204)


@router.delete("/{project_id}/docs/{doc_id}", status_code=204)
async def unlink_doc(project_id: uuid.UUID, doc_id: DocId,
                     principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """Remove a doc from a project: a Maintainer or above (A0 §5; admins only
    through a member role). Removes the project document whose history holds
    this content, with every version, then GCs each version's content. A
    missing link is 404 — after the role check. (V1 Task 7 removes this
    route for DELETE …/documents/{id}.)"""
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    cur = await conn.execute(  # HISTORY: the document holding this content
        "SELECT v.project_doc_id, c.file_name FROM project_documents v "
        "JOIN project_documents c ON c.project_doc_id = v.project_doc_id AND c.is_current "
        "WHERE v.project_id = %s AND v.doc_id = %s LIMIT 1", (project_id, doc_id))
    row = await cur.fetchone()
    if row is None:
        raise refusal(404, "not_found", "Not found")
    pdoc, name = row
    cur = await conn.execute(  # HISTORY, verified or not: everything the delete drops
        "SELECT DISTINCT doc_id FROM project_documents WHERE project_doc_id = %s ORDER BY 1",
        (pdoc,))
    doc_ids = [r[0] for r in await cur.fetchall()]
    await conn.execute("DELETE FROM project_docs WHERE id = %s", (pdoc,))
    audit("placement.removed", project=project_id, doc=doc_id, by=principal.user_id)
    await project_events.record(conn, project_id, principal.user_id, "document.removed",
                                doc_id=doc_id, details={"name": name})
    for d in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, d, trigger="placement_removed")
    return Response(status_code=204)
```

- [ ] **Step 7: Run the new tests, then the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests/test_migration_018.py server/tests/test_document_versions_schema.py -q`
Expected: PASS (7 tests).

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: every test passes. The count is the pre-task baseline + 7. Record both numbers in the task report.

- [ ] **Step 8: Ask, then commit**

Stop. Show `git status --short` and a one-line summary, and ask the user for permission to commit. Only on a yes:

```bash
git add server/sql/018_document_versions.sql server/tests/test_migration_018.py \
  server/tests/test_document_versions_schema.py server/tests/seed.py server/routers/projects.py
git commit -m "feat(versions): migration 018 — every placement is v1 of a project document; filing keeps working"
```

---

### Task 2: Read side of `doc_versions`, and "listed" vs "readable"

**Files:**
- Create: `server/services/doc_versions.py`
- Modify: `server/auth/authz.py` (add `listed_docs_where`, `listed_docs_params` after `readable_docs_params`)
- Test: `server/tests/test_doc_versions_read.py`

**Interfaces:**
- Consumes: Task 1's schema and seed helpers.
- Produces (all in `server.services.doc_versions`):
  - `DOCUMENT_NOT_FOUND: str`
  - `display_name_sql(doc_alias: str, entry_alias: str) -> str` and `display_name_params(user_id) -> list`
  - `versions_in_sql(doc_alias: str, *, only_old: bool = False) -> str` and `versions_in_params(user_id) -> list`
  - `async versions_in(conn, doc_id, user_id) -> list[dict]`. Each item is `{project: {id, name}, document_id, version, current_version, current_doc_id, current_name, is_current}`.
  - `old_version_of(items: list[dict]) -> dict | None`
  - `async list_project_documents(conn, project_id, *, show_email: bool) -> list[dict]`
  - `async document_row(conn, project_id, document_id, *, show_email: bool) -> dict | None`
  - `async list_versions(conn, project_id, document_id, *, show_email: bool) -> list[dict]`
  - `async document_ref(conn, document_id) -> dict | None`, returning `{id, name, version}`
  - `async lock_document(conn, project_id, document_id) -> int`, returning `last_version`
- Also produces `authz.listed_docs_where(alias="d") -> str` and `authz.listed_docs_params(user_id) -> list`.

A document row (from `list_project_documents` / `document_row`) is `{id, name, version, versions_count, doc_id, state, updated_at, updated_by: {id, name} | None}`.

A version row (from `list_versions`) is `{version, doc_id, file_name, note, origin, origin_version, added_at, added_by: {id, name} | None, is_current, state}`.

- [ ] **Step 1: Write the failing tests**

`server/tests/test_doc_versions_read.py`:

```python
"""doc_versions' read side (V1 spec §2, §3.4, §5, §7): CURRENT listings,
HISTORY lists, versions_in, the display name, and listed vs readable."""
import uuid

import pytest
from fastapi import HTTPException

from server.auth import authz
from server.services import doc_versions
from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2, C3, C4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64


async def _content(db_conn, holder, *ids, names=None):
    for i, doc in enumerate(ids):
        name = names[i] if names else f"{doc[0]}.pdf"
        await seed.seed_doc(db_conn, doc, holder, file_name=name, state="indexed")


async def test_lists_one_row_per_document_its_current_version_sorted_by_name(db_conn):
    alice = await member(db_conn, "r-alice")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _content(db_conn, alice.user_id, C1, C2, C3, C4)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="old.pdf")
    await seed.add_version(db_conn, x, C2, alice.user_id, file_name="b-report.pdf")
    await seed.place_doc(db_conn, pid, C3, alice.user_id, file_name="Z.pdf")
    await seed.place_doc(db_conn, pid, C4, alice.user_id, file_name="legacy.pdf", verified=False)
    rows = await doc_versions.list_project_documents(db_conn, pid, show_email=False)
    assert [(r["name"], r["version"], r["versions_count"], r["doc_id"]) for r in rows] == [
        ("b-report.pdf", 2, 2, C2), ("Z.pdf", 1, 1, C3)]
    assert rows[0]["id"] == x and rows[0]["state"] == "indexed"
    assert rows[0]["updated_by"]["id"] == alice.user_id


async def test_history_is_newest_first_and_hides_unverified_versions(db_conn):
    alice = await member(db_conn, "r-alice2")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _content(db_conn, alice.user_id, C1, C2, C3)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, x, C3, alice.user_id, current=False, verified=False)
    await seed.add_version(db_conn, x, C2, alice.user_id, note="fixed table 4")
    rows = await doc_versions.list_versions(db_conn, pid, x, show_email=False)
    assert [(r["version"], r["is_current"], r["note"]) for r in rows] == [
        (3, True, "fixed table 4"), (1, False, None)]


async def test_history_of_another_projects_document_is_404(db_conn):
    alice = await member(db_conn, "r-alice3")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    await _content(db_conn, alice.user_id, C1)
    x = await seed.place_doc(db_conn, other, C1, alice.user_id)
    for document_id in (x, str(uuid.uuid4())):
        with pytest.raises(HTTPException) as e:
            await doc_versions.list_versions(db_conn, pid, document_id, show_email=False)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException):
            await doc_versions.lock_document(db_conn, pid, document_id)


async def test_versions_in_names_only_visible_projects_and_flags_current(db_conn):
    alice, bob = await member(db_conn, "r-alice4"), await member(db_conn, "r-bob4")
    p1 = await seed.make_project(db_conn, alice.user_id, "Alpha")
    p2 = await seed.make_project(db_conn, alice.user_id, "Beta")
    p3 = await seed.make_project(db_conn, bob.user_id, "Hidden")
    await _content(db_conn, alice.user_id, C1, C2)
    await seed.place_doc(db_conn, p1, C1, alice.user_id)                       # current in Alpha
    y = await seed.place_doc(db_conn, p2, C1, alice.user_id)
    await seed.add_version(db_conn, y, C2, alice.user_id, file_name="new.pdf")  # old in Beta
    await seed.place_doc(db_conn, p3, C1, alice.user_id)                       # alice isn't in Hidden
    items = await doc_versions.versions_in(db_conn, C1, alice.user_id)
    assert [(i["project"]["name"], i["version"], i["is_current"], i["current_version"],
             i["current_doc_id"], i["current_name"]) for i in items] == [
        ("Alpha", 1, True, 1, C1, "1.pdf"), ("Beta", 1, False, 2, C2, "new.pdf")]
    assert doc_versions.old_version_of(items) is None  # current somewhere: no banner
    assert doc_versions.old_version_of(items[1:])["project"]["name"] == "Beta"
    assert doc_versions.old_version_of([]) is None


async def test_versions_in_reports_the_highest_number_when_content_repeats(db_conn):
    alice = await member(db_conn, "r-alice5")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _content(db_conn, alice.user_id, C1, C2)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, x, C2, alice.user_id)
    await seed.add_version(db_conn, x, C1, alice.user_id, origin="restore", origin_version=1)
    [item] = await doc_versions.versions_in(db_conn, C1, alice.user_id)
    assert (item["version"], item["is_current"]) == (3, True)


async def test_display_name_is_my_entry_then_a_version_name_then_canonical(db_conn):
    alice, bob = await member(db_conn, "r-alice6"), await member(db_conn, "r-bob6")
    pid = await seed.make_project(db_conn, alice.user_id, "P", members=[(bob.user_id, "reader")])
    await _content(db_conn, alice.user_id, C1, names=["canonical.pdf"])
    await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="As filed.pdf")

    async def name_for(user_id):
        cur = await db_conn.execute(
            f"SELECT {doc_versions.display_name_sql('d', '_me')} FROM documents d "
            "LEFT JOIN library_entries _me ON _me.doc_id = d.doc_id AND _me.user_id = %s "
            "WHERE d.doc_id = %s",
            [*doc_versions.display_name_params(user_id), user_id, C1])
        return (await cur.fetchone())[0]

    assert await name_for(alice.user_id) == "canonical.pdf"   # my entry, NULL name = canonical
    assert await name_for(bob.user_id) == "As filed.pdf"      # no entry: the version's name
    await db_conn.execute("UPDATE library_entries SET file_name = 'Mine.pdf' WHERE user_id = %s",
                          (alice.user_id,))
    assert await name_for(alice.user_id) == "Mine.pdf"


async def test_an_old_version_is_readable_but_not_listed(db_conn):
    alice, bob = await member(db_conn, "r-alice7"), await member(db_conn, "r-bob7")
    pid = await seed.make_project(db_conn, alice.user_id, "P", members=[(bob.user_id, "reader")])
    await _content(db_conn, alice.user_id, C1, C2)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, x, C2, alice.user_id)

    async def where(sql, params, doc):
        cur = await db_conn.execute(f"SELECT 1 FROM documents d WHERE d.doc_id = %s AND {sql}",
                                    [doc, *params])
        return await cur.fetchone() is not None

    readable = (authz.readable_docs_where("d"), authz.readable_docs_params(bob.user_id))
    listed = (authz.listed_docs_where("d"), authz.listed_docs_params(bob.user_id))
    assert await where(*readable, C1) and not await where(*listed, C1)
    assert await where(*readable, C2) and await where(*listed, C2)
    # alice's own entry for C1 lists it for her even though it's old in the project
    assert await where(authz.listed_docs_where("d"), authz.listed_docs_params(alice.user_id), C1)


def test_sql_placeholders_match_their_params():
    assert doc_versions.display_name_sql("d", "_me").count("%s") == len(doc_versions.display_name_params("u"))
    for only_old in (False, True):
        assert doc_versions.versions_in_sql("d", only_old=only_old).count("%s") == len(
            doc_versions.versions_in_params("u"))
    assert authz.listed_docs_where("d").count("%s") == len(authz.listed_docs_params("u"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_doc_versions_read.py -q`
Expected: FAIL with `ImportError: cannot import name 'doc_versions'`.

- [ ] **Step 3: Add `listed_docs_where` to `server/auth/authz.py`** (right after `readable_docs_params`)

```python
def listed_docs_where(alias: str = "d") -> str:
    """`readable_docs_where` narrowed to what is LISTED (V1 spec §2): the
    user's verified entry, or a verified CURRENT version in a project they
    belong to. Old versions stay readable (history) but are never a Library
    row, or every revision would come back as a duplicate. Bind with
    `listed_docs_params(user_id)`."""
    return (
        f"(EXISTS (SELECT 1 FROM library_entries _le "
        f"WHERE _le.doc_id = {alias}.doc_id AND _le.user_id = %s "
        f"AND {effective_holding_sql('_le')}) "
        f"OR EXISTS (SELECT 1 FROM project_documents _lpd "  # CURRENT
        f"JOIN project_members _lpm ON _lpm.project_id = _lpd.project_id "
        f"WHERE _lpd.doc_id = {alias}.doc_id AND _lpd.is_current "
        f"AND {effective_holding_sql('_lpd')} AND _lpm.user_id = %s))"
    )


def listed_docs_params(user_id: str) -> list[str]:
    return [user_id, user_id]
```

- [ ] **Step 4: Write `server/services/doc_versions.py` (the read side)**

```python
"""Versions of a project's documents (V1 spec
docs/superpowers/specs/2026-10-12-document-versions-design.md).

A project document (`project_docs`) is "a document in a project"; each of its
versions is a `project_documents` row pointing at content. This module owns
every write that changes a history and the queries that read one; routes
never write project_documents themselves.

Two query groups (spec §3.4), named on every query here:
  HISTORY  every verified version — reading old versions, versions_in;
  CURRENT  the verified current version — listings and counts.
A query in the wrong group fails predictably: HISTORY narrowed to current
breaks reading old versions; CURRENT left unfiltered lists duplicates.

Lock order (spec §4.4): projects → project_docs → documents. A write first
takes its project's row FOR KEY SHARE, then the document's project_docs row
FOR UPDATE, then content. GC locks content only. (Corrected after the final
review; the text first written here missed the projects step.)
"""
from __future__ import annotations

from typing import Any

from ..auth.authz import visible_projects_params, visible_projects_where
from ..http_errors import refusal
from . import people

DOCUMENT_NOT_FOUND = "This document isn't in the project, or it was removed."


def display_name_sql(doc_alias: str, entry_alias: str) -> str:
    """The name the caller sees for content (spec §7), in order: their entry's
    name — an entry with no name of its own means the canonical name, the
    entry holder's own upload name; else (no entry) a visible verified
    version's file_name, the current one first, then the newest; else the
    canonical name. Needs `LEFT JOIN library_entries <entry_alias>` on the
    caller. Bind `display_name_params(user_id)`."""
    return (
        f"COALESCE({entry_alias}.file_name, CASE WHEN {entry_alias}.user_id IS NULL THEN ("
        "SELECT _nv.file_name FROM project_documents _nv "  # HISTORY
        "JOIN projects _np ON _np.id = _nv.project_id "
        f"WHERE _nv.doc_id = {doc_alias}.doc_id AND _nv.verified "
        f"AND {visible_projects_where('_np')} "
        "ORDER BY _nv.is_current DESC, _nv.added_at DESC, _nv.project_id LIMIT 1) END, "
        f"{doc_alias}.file_name)"
    )


def display_name_params(user_id: str) -> list[str]:
    return visible_projects_params(user_id)


def versions_in_sql(doc_alias: str, *, only_old: bool = False) -> str:
    """JSON array, one item per visible project document whose verified
    history holds `<doc_alias>`'s content (spec §5.2), sorted by project name:
    {project: {id, name}, document_id, version (its highest number there),
    current_version, current_doc_id, current_name, is_current}. HISTORY.
    `only_old`: just the documents where it isn't current (the Library's
    `newer_in`). Only projects the caller is a member of are ever named.
    Bind `versions_in_params(user_id)`."""
    having = "HAVING NOT bool_or(_vv.is_current) " if only_old else ""
    return (
        "COALESCE((SELECT json_agg(json_build_object("
        "'project', json_build_object('id', _x.pid, 'name', _x.pname), "
        "'document_id', _x.pdoc, 'version', _x.version, 'current_version', _x.cur_version, "
        "'current_doc_id', _x.cur_doc, 'current_name', _x.cur_name, 'is_current', _x.is_current) "
        "ORDER BY _x.pname, _x.pid, _x.pdoc) FROM ("
        "SELECT _vp.id AS pid, _vp.name AS pname, _vv.project_doc_id AS pdoc, "
        "max(_vv.version) AS version, bool_or(_vv.is_current) AS is_current, "
        "_vc.version AS cur_version, _vc.doc_id AS cur_doc, _vc.file_name AS cur_name "
        "FROM project_documents _vv JOIN projects _vp ON _vp.id = _vv.project_id "
        "JOIN project_documents _vc ON _vc.project_doc_id = _vv.project_doc_id "
        "AND _vc.is_current AND _vc.verified "
        f"WHERE _vv.doc_id = {doc_alias}.doc_id AND _vv.verified "
        f"AND {visible_projects_where('_vp')} "
        "GROUP BY _vp.id, _vp.name, _vv.project_doc_id, _vc.version, _vc.doc_id, _vc.file_name "
        f"{having}) _x), '[]'::json)"
    )


def versions_in_params(user_id: str) -> list[str]:
    return visible_projects_params(user_id)


async def versions_in(conn, doc_id: str, user_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {versions_in_sql('d')} FROM documents d WHERE d.doc_id = %s",
        [*versions_in_params(user_id), doc_id])
    row = await cur.fetchone()
    return list(row[0]) if row else []


def old_version_of(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The banner rule (spec §7): the first item when the content is an old
    version in at least one visible project and current in none; else None."""
    if not items or any(i["is_current"] for i in items):
        return None
    return items[0]


_DOCUMENT_SELECT = (
    "SELECT v.project_doc_id, v.file_name, v.version, v.doc_id, d.state, v.added_at, "
    f"{people.person_cols('u')}, "
    "(SELECT count(*) FROM project_documents _c "  # HISTORY: how many versions it has
    "WHERE _c.project_doc_id = v.project_doc_id AND _c.verified) "
    "FROM project_documents v JOIN documents d ON d.doc_id = v.doc_id "
    "LEFT JOIN users u ON u.id = v.added_by "
    "WHERE v.project_id = %s AND v.is_current AND v.verified "  # CURRENT
)


def _document(r, show_email: bool) -> dict[str, Any]:
    return {"id": str(r[0]), "name": r[1], "version": r[2], "doc_id": r[3], "state": r[4],
            "updated_at": r[5], "updated_by": people.person_from(r[6:12], show_email),
            "versions_count": r[12]}


async def list_project_documents(conn, project_id, *, show_email: bool) -> list[dict[str, Any]]:
    """A project's documents (spec §5.1): one row per document — its verified
    current version — sorted by name. CURRENT. C2.1's project scope reuses
    this, so "project chat sees only current versions" has one definition."""
    cur = await conn.execute(_DOCUMENT_SELECT + "ORDER BY lower(v.file_name), v.project_doc_id",
                             (project_id,))
    return [_document(r, show_email) for r in await cur.fetchall()]


async def document_row(conn, project_id, document_id, *, show_email: bool) -> dict | None:
    cur = await conn.execute(_DOCUMENT_SELECT + "AND v.project_doc_id = %s",
                             (project_id, document_id))
    row = await cur.fetchone()
    return _document(row, show_email) if row else None


async def list_versions(conn, project_id, document_id, *, show_email: bool) -> list[dict]:
    """A document's verified versions, newest first (spec §5.1). HISTORY.
    404 unless it is a visible document of this project (a verified current
    version: a document whose current version is unverified is invisible, as
    an unverified placement is)."""
    cur = await conn.execute(
        "SELECT v.version, v.doc_id, v.file_name, v.note, v.origin, v.origin_version, "
        f"v.added_at, v.is_current, d.state, {people.person_cols('u')} "
        "FROM project_documents v JOIN documents d ON d.doc_id = v.doc_id "
        "LEFT JOIN users u ON u.id = v.added_by "
        "WHERE v.project_doc_id = %s AND v.project_id = %s AND v.verified "  # HISTORY
        "ORDER BY v.version DESC", (document_id, project_id))
    rows = await cur.fetchall()
    if not any(r[7] for r in rows):
        raise refusal(404, "not_found", DOCUMENT_NOT_FOUND)
    return [{"version": r[0], "doc_id": r[1], "file_name": r[2], "note": r[3], "origin": r[4],
             "origin_version": r[5], "added_at": r[6], "is_current": r[7], "state": r[8],
             "added_by": people.person_from(r[9:15], show_email)} for r in rows]


async def document_ref(conn, document_id) -> dict[str, Any] | None:
    """`{id, name, version}` of the document's verified current version: how
    a 409 names the document involved (spec §5.3). CURRENT."""
    cur = await conn.execute(
        "SELECT file_name, version FROM project_documents "
        "WHERE project_doc_id = %s AND is_current AND verified", (document_id,))
    row = await cur.fetchone()
    return {"id": str(document_id), "name": row[0], "version": row[1]} if row else None


async def lock_document(conn, project_id, document_id) -> int:
    """Lock the document's row — always the first lock (spec §4.4) — and
    return its last_version. 404 unless it is a visible document of this
    project."""
    cur = await conn.execute(
        "SELECT last_version FROM project_docs WHERE id = %s AND project_id = %s FOR UPDATE",
        (document_id, project_id))
    row = await cur.fetchone()
    if row is None or await document_ref(conn, document_id) is None:
        raise refusal(404, "not_found", DOCUMENT_NOT_FOUND)
    return row[0]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_doc_versions_read.py -q`
Expected: PASS (8 tests).

- [ ] **Step 6: Ask, then commit**

Ask for permission. On a yes:

```bash
git add server/services/doc_versions.py server/auth/authz.py server/tests/test_doc_versions_read.py
git commit -m "feat(versions): read side — current listings, history, versions_in, display name, listed vs readable"
```

---

### Task 3: Every listing and count sees current versions only (§3.4)

**Files:**
- Modify: `server/routers/docs.py`:
  - `_doc_projects_sql` (113–127);
  - `_DISPLAY_NAME` / `_display_name_params` (138–157);
  - `_fetch_doc_status` (175–217);
  - `list_documents` (265–313).
- Modify: `server/routers/projects.py:_project_sql` (`doc_count`), `delete_project` (`DISTINCT`)
- Modify: `server/routers/admin.py:167` (`doc_count`)
- Test: `server/tests/test_docs_list.py`, `server/tests/test_projects_router.py`, `server/tests/test_admin_projects.py`

**Interfaces:**
- Consumes: `doc_versions.display_name_sql/params`, `versions_in_sql/params`, and `authz.listed_docs_where/params` (Task 2).
- Produces:
  - `GET /v1/docs` rows gain `newer_in: [...]`, and their `projects` chips become `[{id, name, document_id, version, versions_count}]`;
  - `GET /v1/docs/{id}` gains `versions_in: [...]`;
  - every `doc_count` counts documents.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_docs_list.py`:

```python
DOC_V1, DOC_V2 = "7" * 64, "8" * 64


async def test_an_old_version_is_readable_but_never_a_library_row(db_conn, docs_app):
    owner = await _member(db_conn, "v-owner")
    reader = await _member(db_conn, "v-reader")
    pid = await _project(db_conn, owner.user_id, "P")
    await seed.add_member(db_conn, pid, reader.user_id, "reader")
    await _doc(db_conn, DOC_V1, owner.user_id, file_name="r1.pdf")
    await _doc(db_conn, DOC_V2, owner.user_id, file_name="r2.pdf")
    pdoc = await seed.place_doc(db_conn, pid, DOC_V1, owner.user_id)
    await seed.add_version(db_conn, pdoc, DOC_V2, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: reader
    async with _client(docs_app) as c:
        rows = (await c.get("/v1/docs")).json()
        assert [r["doc_id"] for r in rows] == [DOC_V2]
        assert rows[0]["projects"] == [{"id": pid, "name": "P", "document_id": pdoc,
                                        "version": 2, "versions_count": 2}]
        assert (await c.get(f"/v1/docs/{DOC_V1}")).status_code == 200  # still readable
        filtered = (await c.get("/v1/docs", params={"project_id": pid})).json()
        assert [r["doc_id"] for r in filtered] == [DOC_V2]


async def test_newer_in_names_the_projects_that_moved_on(db_conn, docs_app):
    owner = await _member(db_conn, "n-owner")
    pa = await _project(db_conn, owner.user_id, "Alpha")
    pb = await _project(db_conn, owner.user_id, "Beta")
    await _doc(db_conn, DOC_V1, owner.user_id, file_name="readme.md")
    await _doc(db_conn, DOC_V2, owner.user_id, file_name="readme-2.md")
    await seed.place_doc(db_conn, pa, DOC_V1, owner.user_id)          # current in Alpha
    pdoc = await seed.place_doc(db_conn, pb, DOC_V1, owner.user_id)
    await seed.add_version(db_conn, pdoc, DOC_V2, owner.user_id)       # Beta moved on
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as c:
        rows = {r["doc_id"]: r for r in (await c.get("/v1/docs")).json()}
        mine = rows[DOC_V1]  # owner's own entry: listed, with Alpha's chip
        assert [p["name"] for p in mine["projects"]] == ["Alpha"]
        assert [(n["project"]["name"], n["current_version"], n["current_doc_id"])
                for n in mine["newer_in"]] == [("Beta", 2, DOC_V2)]
        status = (await c.get(f"/v1/docs/{DOC_V1}")).json()
        assert [(v["project"]["name"], v["is_current"]) for v in status["versions_in"]] == [
            ("Alpha", True), ("Beta", False)]


async def test_project_name_stays_as_filed_after_the_filer_renames_their_copy(db_conn, docs_app):
    """Review Focus 1: the name is stored on the version (spec §3.2), no
    longer borrowed live from the filer's entry."""
    filer = await _member(db_conn, "f-filer")
    reader = await _member(db_conn, "f-reader")
    pid = await _project(db_conn, filer.user_id, "P")
    await seed.add_member(db_conn, pid, reader.user_id, "reader")
    await _doc(db_conn, DOC_V1, filer.user_id, file_name="Q3 report.pdf")
    await seed.place_doc(db_conn, pid, DOC_V1, filer.user_id)
    await db_conn.execute("UPDATE library_entries SET file_name = 'my scratch copy.pdf' "
                          "WHERE user_id = %s", (filer.user_id,))
    docs_app.dependency_overrides[deps.get_current_user] = lambda: reader
    async with _client(docs_app) as c:
        assert [r["file_name"] for r in (await c.get("/v1/docs")).json()] == ["Q3 report.pdf"]
    await db_conn.execute("DELETE FROM library_entries WHERE user_id = %s", (filer.user_id,))
    async with _client(docs_app) as c:
        assert [r["file_name"] for r in (await c.get("/v1/docs")).json()] == ["Q3 report.pdf"]


def test_listing_placeholders_match_params():
    from server.services import doc_versions
    assert docs_router._doc_projects_sql("d").count("%s") == len(docs_router._doc_projects_params("u"))
    assert doc_versions.versions_in_sql("d", only_old=True).count("%s") == len(
        doc_versions.versions_in_params("u"))
```

Append to `server/tests/test_projects_router.py`:

```python
async def test_doc_count_counts_documents_not_versions(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="dc1", email="dc1@x.io")
    pid = await _mk_project(db_conn, u["id"])
    for d in (DOC_A, "b" * 64, "c" * 64):
        await _mk_doc(db_conn, d, u["id"])
    pdoc = await seed.place_doc(db_conn, pid, DOC_A, u["id"])
    await seed.add_version(db_conn, pdoc, "b" * 64, u["id"])
    await seed.add_version(db_conn, pdoc, "c" * 64, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.get(f"/v1/projects/{pid}")).json()["doc_count"] == 1
        assert [p["doc_count"] for p in (await c.get("/v1/projects")).json()] == [1]


async def test_deleting_a_project_gcs_every_versions_content(db_conn, tmp_path):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="dv1", email="dv1@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await seed.seed_doc(db_conn, DOC_A, None)
    await seed.seed_doc(db_conn, "b" * 64, None)
    pdoc = await seed.place_doc(db_conn, pid, DOC_A, owner["id"])
    await seed.add_version(db_conn, pdoc, "b" * 64, owner["id"])
    await seed.add_version(db_conn, pdoc, DOC_A, owner["id"], origin="restore", origin_version=1)
    async with _client_for(db_conn, _reader(owner)) as c:
        assert (await c.delete(f"/v1/projects/{pid}")).status_code == 204
    assert not await _content_exists(db_conn, DOC_A)
    assert not await _content_exists(db_conn, "b" * 64)
```

Append to `server/tests/test_admin_projects.py`:

```python
async def test_admin_doc_count_counts_documents_not_versions(db_conn):
    admin, olu = await _user(db_conn, "ad2"), await _user(db_conn, "ol2", "Olu")
    pid = await seed.make_project(db_conn, olu, "Versioned")
    await seed.seed_doc(db_conn, "a" * 64, olu)
    await seed.seed_doc(db_conn, "b" * 64, olu)
    pdoc = await seed.place_doc(db_conn, pid, "a" * 64, olu)
    await seed.add_version(db_conn, pdoc, "b" * 64, olu)
    async with _client(db_conn, admin) as c:
        rows = {r["name"]: r for r in (await c.get("/v1/admin/projects")).json()}
    assert rows["Versioned"]["doc_count"] == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_docs_list.py server/tests/test_projects_router.py server/tests/test_admin_projects.py -q`
Expected: FAIL. Old versions are listed, chips lack `document_id`, `newer_in` and `versions_in` are missing, and `doc_count` is 3.

- [ ] **Step 3: Change `server/routers/docs.py`**

Add these imports:
- `listed_docs_params, listed_docs_where` to the `..auth.authz` import list;
- `doc_versions` to `from ..services import …`.

Replace `_doc_projects_sql`:

```python
def _doc_projects_sql(alias: str) -> str:
    """JSON array `[{id, name, document_id, version, versions_count}]`: the
    visible projects where `<alias>` is a document's verified CURRENT version
    (V1 spec §5.2). An old version is never a chip — `newer_in` covers it.
    Everyone, including whoever uploaded it, sees only projects they belong
    to: listing others would leak their names. Sorted by name, then id. Bind
    with `_doc_projects_params(user_id)`; SELECT-list params come BEFORE join
    and WHERE params."""
    return (
        "COALESCE((SELECT json_agg(json_build_object('id', _vp.id, 'name', _vp.name, "
        "'document_id', _vpd.project_doc_id, 'version', _vpd.version, "
        "'versions_count', (SELECT count(*) FROM project_documents _vc "  # HISTORY
        "WHERE _vc.project_doc_id = _vpd.project_doc_id AND _vc.verified)) "
        "ORDER BY _vp.name, _vp.id) "
        "FROM project_documents _vpd JOIN projects _vp ON _vp.id = _vpd.project_id "  # CURRENT
        f"WHERE _vpd.doc_id = {alias}.doc_id AND _vpd.is_current "
        f"AND {effective_holding_sql('_vpd')} AND {visible_projects_where('_vp')}), '[]'::json)"
    )
```

Replace the `_DISPLAY_NAME` block (its comment, the constant and `_display_name_params`):

```python
# The name the caller sees (V1 spec §7): their entry's; for a row they see
# only through a project, the name stored on a visible version (as filed —
# never borrowed live from the filer's entry); else the canonical name.
# Needs `_ENTRY_JOIN`; bind with `_display_name_params`.
_DISPLAY_NAME = doc_versions.display_name_sql("d", "_me")
_display_name_params = doc_versions.display_name_params
```

In `_fetch_doc_status`, add one column after `{_doc_projects_sql('d')} AS projects,`:

```python
                   {doc_versions.versions_in_sql('d')} AS versions_in,
```

and change its parameter list to:

```python
            [*_display_name_params(user_id), *_doc_projects_params(user_id),
             *doc_versions.versions_in_params(user_id), user_id, doc_id],
```

In `list_documents`:
- the docstring's first sentence becomes "List docs in my library (uploaded or shared with me) plus the CURRENT versions of documents in projects I belong to (V1 spec §2: old versions are readable, never listed)";
- `where = [readable_docs_where("d")]` → `where = [listed_docs_where("d")]`;
- `where_params: list[Any] = readable_docs_params(uid)` → `listed_docs_params(uid)`;
- in the `project_id` filter, `f"WHERE _fpd.doc_id = d.doc_id AND _fpd.project_id = %s "` → `f"WHERE _fpd.doc_id = d.doc_id AND _fpd.project_id = %s AND _fpd.is_current "  # CURRENT`.

Then replace the `sql`/`params` lines:

```python
    sql = (
        f"SELECT d.doc_id, d.state, {_ENTRY_COLS}, {_doc_projects_sql('d')} AS projects, "
        f"{doc_versions.versions_in_sql('d', only_old=True)} AS newer_in "
        f"FROM documents d {_ENTRY_JOIN} "
        f"WHERE {' AND '.join(where)} ORDER BY d.updated_at DESC"
    )
    # psycopg binds by position: SELECT-list params, then the join, then WHERE.
    params = [*_display_name_params(uid), *_doc_projects_params(uid),
              *doc_versions.versions_in_params(uid), uid, *where_params]
```

`readable_docs_where` / `readable_docs_params` are still imported by other routes in this file. Leave those imports.

- [ ] **Step 4: Change the counts and the delete-project GC**

In `server/routers/projects.py` `_project_sql`:

```python
        "(SELECT count(*) FROM project_documents "  # CURRENT: documents, not versions
        "WHERE project_id = p.id AND verified AND is_current) "
```

In `delete_project`:

```python
    cur = await conn.execute(  # HISTORY, verified or not: everything the cascade drops
        "SELECT DISTINCT doc_id FROM project_documents WHERE project_id = %s ORDER BY doc_id",
        (project_id,))
```

In `server/routers/admin.py:167`:

```python
        "(SELECT count(*) FROM project_documents d "  # CURRENT
        "WHERE d.project_id = p.id AND d.verified AND d.is_current), "
```

- [ ] **Step 5: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest server/tests/test_docs_list.py server/tests/test_projects_router.py server/tests/test_admin_projects.py -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: all pass. `test_list_project_only_row_shows_the_filers_name_not_the_first_uploaders` still passes, because its filer renames before filing.

- [ ] **Step 6: Ask, then commit**

```bash
git add server/routers/docs.py server/routers/projects.py server/routers/admin.py \
  server/tests/test_docs_list.py server/tests/test_projects_router.py server/tests/test_admin_projects.py
git commit -m "feat(versions): listings, chips and doc_count see current versions; versions_in and newer_in; names stored as filed"
```

---

### Task 4: Write side of `doc_versions`: add, restore, delete, merge, remove

**Files:**
- Modify: `server/services/doc_versions.py` (append)
- Modify: `server/services/project_events.py` (`KINDS`)
- Test: `server/tests/test_doc_versions_write.py`

**Interfaces:**
- Consumes: Task 2's reads, `doc_content.gc_content_if_orphaned`, `project_events.record`, `audit`.
- Produces (in `doc_versions`):
  - `NOTE_MAX = 500`, and `clean_note(note: str | None) -> str | None` (raises 422 `note_too_long`);
  - `async verify_legacy_current(conn, project_id, doc_id, user_id) -> str | None`;
  - `async create_document(conn, user_id, project_id, doc_id, *, file_name) -> str`;
  - `async add_version(conn, user_id, project_id, document_id, *, doc_id, file_name, note=None) -> dict`, returning `{version, doc_id, restored_from}`;
  - `async restore_version(conn, user_id, project_id, document_id, version) -> dict` (same shape);
  - `async delete_version(conn, user_id, project_id, document_id, version) -> None`;
  - `async merge_documents(conn, user_id, project_id, into_id, from_id) -> None`;
  - `async remove_document(conn, user_id, project_id, document_id) -> None`;
  - `async filed_name(conn, doc_id, user_id) -> str | None`, moved here from `projects._filed_name`.
- Also produces event kinds `document.version_added`, `document.version_restored`, `document.merged` and `document.version_deleted`.

- [ ] **Step 1: Write the failing tests**

`server/tests/test_doc_versions_write.py`:

```python
"""doc_versions' writes (V1 spec §4): every change appends or removes a
version under the document's lock, applies the same-bytes rules, records
one event, and refuses before writing anything."""
import pytest
from fastapi import HTTPException

from server.auth import authz
from server.services import doc_versions
from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2, C3, C4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64


@pytest.fixture
async def proj(db_conn):
    alice = await member(db_conn, "w-alice")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    for i, doc in enumerate((C1, C2, C3, C4), start=1):
        await seed.seed_doc(db_conn, doc, alice.user_id, file_name=f"f{i}.pdf", state="indexed")
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="report.pdf")
    return alice, pid, x


async def _versions(db_conn, pdoc):
    cur = await db_conn.execute(
        "SELECT version, doc_id, file_name, origin, origin_version, is_current FROM project_documents "
        "WHERE project_doc_id = %s ORDER BY version", (pdoc,))
    return [tuple(r) for r in await cur.fetchall()]


async def _events(db_conn, pid):
    cur = await db_conn.execute(
        "SELECT kind, doc_id, details FROM project_events WHERE project_id = %s "
        "AND kind LIKE 'document.%%' ORDER BY id", (pid,))
    return [tuple(r) for r in await cur.fetchall()]


async def _refused(coro, status, code=None):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status
    if code:
        assert e.value.detail["error"] == code
    return e.value.detail


async def test_add_version_appends_a_current_version_and_records_it(db_conn, proj):
    alice, pid, x = proj
    out = await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2,
                                         file_name="report-v2.pdf", note="  fixed table 4 ")
    assert out == {"version": 2, "doc_id": C2, "restored_from": None}
    assert await _versions(db_conn, x) == [
        (1, C1, "report.pdf", "upload", None, False), (2, C2, "report-v2.pdf", "upload", None, True)]
    assert await _events(db_conn, pid) == [("document.version_added", C2,
                                            {"name": "report-v2.pdf", "version": 2,
                                             "note": "fixed table 4"})]


async def test_the_current_files_bytes_are_refused_and_nothing_changes(db_conn, proj):
    alice, pid, x = proj
    detail = await _refused(doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C1,
                                                     file_name="again.pdf"), 409, "already_current")
    assert detail["document"] == {"id": x, "name": "report.pdf", "version": 1}
    assert len(await _versions(db_conn, x)) == 1 and await _events(db_conn, pid) == []


async def test_an_older_versions_bytes_become_a_restore_named_like_it(db_conn, proj):
    alice, pid, x = proj
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    out = await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C1,
                                         file_name="whatever.pdf")
    assert out == {"version": 3, "doc_id": C1, "restored_from": 1}
    assert (await _versions(db_conn, x))[-1] == (3, C1, "report.pdf", "restore", 1, True)


async def test_another_documents_current_bytes_are_refused_naming_it(db_conn, proj):
    alice, pid, x = proj
    y = await seed.place_doc(db_conn, pid, C3, alice.user_id, file_name="other.pdf")
    detail = await _refused(doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C3,
                                                     file_name="o.pdf"), 409, "current_elsewhere")
    assert detail["document"]["id"] == y
    await _refused(doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2,
                                            file_name="x.pdf", note="n" * 501), 422, "note_too_long")


async def test_restore_appends_a_copy_of_the_old_version(db_conn, proj):
    alice, pid, x = proj
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    out = await doc_versions.restore_version(db_conn, alice.user_id, pid, x, 1)
    assert out == {"version": 3, "doc_id": C1, "restored_from": 1}
    assert (await _events(db_conn, pid))[-1] == (
        "document.version_restored", C1, {"name": "report.pdf", "version": 3, "from_version": 1})
    await _refused(doc_versions.restore_version(db_conn, alice.user_id, pid, x, 3), 409, "already_current")
    await _refused(doc_versions.restore_version(db_conn, alice.user_id, pid, x, 9), 404, "not_found")


async def test_delete_old_version_gcs_content_nobody_else_holds(db_conn, proj, tmp_path):
    alice, pid, x = proj
    f = tmp_path / "c4.pdf"
    f.write_bytes(b"%PDF-1.4")
    await db_conn.execute("UPDATE documents SET bytes_path = %s WHERE doc_id = %s", (str(f), C4))
    await db_conn.execute("DELETE FROM library_entries WHERE doc_id = %s", (C4,))
    await seed.add_version(db_conn, x, C4, alice.user_id, current=False)
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v3.pdf")
    await doc_versions.delete_version(db_conn, alice.user_id, pid, x, 2)
    assert [v[0] for v in await _versions(db_conn, x)] == [1, 3]
    cur = await db_conn.execute("SELECT 1 FROM documents WHERE doc_id = %s", (C4,))
    assert await cur.fetchone() is None and not f.exists()
    assert (await _events(db_conn, pid))[-1][0] == "document.version_deleted"
    await _refused(doc_versions.delete_version(db_conn, alice.user_id, pid, x, 3), 409, "is_current")


async def test_delete_keeps_content_someone_still_holds(db_conn, proj):
    """Review Focus 5: only the version row goes; the holder still reads it."""
    alice, pid, x = proj
    bob = await member(db_conn, "w-bob")
    await seed.share_doc(db_conn, C1, alice.user_id, bob.user_id)
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    await doc_versions.delete_version(db_conn, alice.user_id, pid, x, 1)
    await authz.assert_can_read_doc(db_conn, C1, bob.user_id)  # no 404


async def test_merge_moves_the_history_after_the_targets(db_conn, proj):
    alice, pid, x = proj
    y = await seed.place_doc(db_conn, pid, C2, alice.user_id, file_name="report-final.pdf")
    await seed.add_version(db_conn, y, C3, alice.user_id, file_name="report-final-2.pdf")
    await seed.add_version(db_conn, y, C4, alice.user_id, current=False, verified=False)
    await doc_versions.merge_documents(db_conn, alice.user_id, pid, x, y)
    assert await _versions(db_conn, x) == [
        (1, C1, "report.pdf", "upload", None, False),
        (2, C2, "report-final.pdf", "merge", 1, False),
        (3, C3, "report-final-2.pdf", "merge", 2, True),
        (4, C4, "f4.pdf", "merge", 3, False)]
    cur = await db_conn.execute("SELECT count(*) FROM project_docs WHERE id = %s", (y,))
    assert (await cur.fetchone())[0] == 0
    assert (await _events(db_conn, pid))[-1] == (
        "document.merged", C3,
        {"from_name": "report-final-2.pdf", "into_name": "report.pdf", "versions": 2})
    cur = await db_conn.execute("SELECT last_version FROM project_docs WHERE id = %s", (x,))
    assert (await cur.fetchone())[0] == 4


async def test_merge_refusals(db_conn, proj):
    alice, pid, x = proj
    await _refused(doc_versions.merge_documents(db_conn, alice.user_id, pid, x, x), 409, "merge_self")
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    z = await seed.place_doc(db_conn, other, C2, alice.user_id)
    await _refused(doc_versions.merge_documents(db_conn, alice.user_id, pid, x, z), 404, "not_found")


async def test_remove_document_gcs_every_versions_content(db_conn, proj):
    alice, pid, x = proj
    await db_conn.execute("DELETE FROM library_entries WHERE doc_id IN (%s,%s)", (C1, C2))
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    await doc_versions.remove_document(db_conn, alice.user_id, pid, x)
    cur = await db_conn.execute("SELECT count(*) FROM documents WHERE doc_id IN (%s,%s)", (C1, C2))
    assert (await cur.fetchone())[0] == 0
    assert (await _events(db_conn, pid))[-1] == (
        "document.removed", C2, {"name": "v2.pdf", "versions": 2})


async def test_numbers_are_never_reused(db_conn, proj):
    alice, pid, x = proj
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C3, file_name="v3.pdf")
    await doc_versions.delete_version(db_conn, alice.user_id, pid, x, 2)
    out = await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C4, file_name="v4.pdf")
    assert out["version"] == 4


async def test_a_legacy_unverified_current_version_is_verified_in_place(db_conn, proj):
    alice, pid, _x = proj
    bob = await member(db_conn, "w-bob2")
    y = await seed.place_doc(db_conn, pid, C3, bob.user_id, verified=False)
    assert await doc_versions.verify_legacy_current(db_conn, pid, C3, alice.user_id) == y
    assert await doc_versions.document_ref(db_conn, y) is not None
    assert await doc_versions.verify_legacy_current(db_conn, pid, C3, alice.user_id) is None


def test_clean_note():
    assert doc_versions.clean_note(None) is None
    assert doc_versions.clean_note("   ") is None
    assert doc_versions.clean_note(" ok ") == "ok"
    assert doc_versions.clean_note("x" * 500) == "x" * 500
    with pytest.raises(HTTPException) as e:
        doc_versions.clean_note("x" * 501)
    assert e.value.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_doc_versions_write.py -q`
Expected: FAIL with `AttributeError: module 'server.services.doc_versions' has no attribute 'add_version'`.

- [ ] **Step 3: Register the event kinds**

In `server/services/project_events.py`:

```python
KINDS = frozenset({
    "project.created", "project.renamed", "project.described",
    "member.added", "member.role_changed", "member.removed", "member.left",
    "document.added", "document.removed",
    # V1 (spec §4.1): a document's history changed.
    "document.version_added", "document.version_restored", "document.merged",
    "document.version_deleted",
})
```

- [ ] **Step 4: Append the write side to `server/services/doc_versions.py`**

Extend the imports at the top:

```python
from psycopg import errors as pg_errors

from ..audit import audit
from ..auth.authz import visible_projects_params, visible_projects_where
from ..http_errors import refusal
from . import doc_content, people, project_events
```

Append:

```python
NOTE_MAX = 500
VERSION_NOT_FOUND = "That version doesn't exist."
ALREADY_CURRENT = "This file is already the current version."
CURRENT_ELSEWHERE = ("This file is already the current version of another document in this "
                     "project. Merge the two instead.")
IS_CURRENT = "This is the current version. Remove the document instead."
MERGE_SELF = "A document can't be merged into itself."


def _conflict(code: str, message: str, document: dict | None, **extra):
    """A 409 that names the document involved (spec §5.3)."""
    return refusal(409, code, message, document=document, **extra)


def clean_note(note: str | None) -> str | None:
    """Trimmed; empty → None; over NOTE_MAX characters → 422 (spec §4.4)."""
    if note is None or not note.strip():
        return None
    note = note.strip()
    if len(note) > NOTE_MAX:
        raise refusal(422, "note_too_long", f"A note can be at most {NOTE_MAX} characters.")
    return note


async def filed_name(conn, doc_id: str, user_id: str) -> str | None:
    """The name the filer sees for content (their entry's, else the
    content's): what a version filed from their library is called."""
    cur = await conn.execute(
        "SELECT COALESCE(e.file_name, d.file_name) FROM library_entries e "
        "JOIN documents d ON d.doc_id = e.doc_id WHERE e.user_id = %s AND e.doc_id = %s",
        (user_id, doc_id))
    row = await cur.fetchone()
    return row[0] if row else None


async def verify_legacy_current(conn, project_id, doc_id: str, user_id: str) -> str | None:
    """Spec §4.2 step 0: an UNVERIFIED current version holding this content is
    proved by this user (who sent the bytes or holds a verified upload), as
    the pre-V1 filing route did. The duplicate-guard index ignores `verified`,
    so without this a new current row for the same content would violate it.
    Returns the document it made visible, or None."""
    cur = await conn.execute(
        "UPDATE project_documents SET verified = true, added_by = %s "
        "WHERE project_id = %s AND doc_id = %s AND is_current AND NOT verified "
        "RETURNING project_doc_id", (user_id, project_id, doc_id))
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def _same_bytes(conn, project_id, document_id, doc_id: str) -> tuple[str | None, Any]:
    """Spec §4.3, in its order: ('already_current', None), ('current_elsewhere',
    other document id), ('older', highest version k), or (None, None)."""
    cur = await conn.execute(  # HISTORY in this project
        "SELECT project_doc_id, version, is_current FROM project_documents "
        "WHERE project_id = %s AND doc_id = %s AND verified", (project_id, doc_id))
    rows = [(str(r[0]), r[1], r[2]) for r in await cur.fetchall()]
    did = str(document_id)
    if any(p == did and current for p, _v, current in rows):
        return "already_current", None
    other = next((p for p, _v, current in rows if p != did and current), None)
    if other:
        return "current_elsewhere", other
    older = [v for p, v, _c in rows if p == did]
    return ("older", max(older)) if older else (None, None)


async def _version_row(conn, document_id, version: int):
    cur = await conn.execute(  # HISTORY
        "SELECT doc_id, file_name, is_current FROM project_documents "
        "WHERE project_doc_id = %s AND version = %s AND verified", (document_id, version))
    return await cur.fetchone()


async def _current_holder(conn, project_id, doc_id: str) -> str | None:
    cur = await conn.execute(  # CURRENT
        "SELECT project_doc_id FROM project_documents "
        "WHERE project_id = %s AND doc_id = %s AND is_current", (project_id, doc_id))
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def _append(conn, project_id, document_id, *, doc_id, file_name, note, origin,
                  origin_version, added_by) -> int:
    """Make `doc_id` the document's new current version, numbered
    last_version + 1. The caller holds the document's lock and has checked
    §4.3. The savepoint makes the unique indexes spec §4.4's last net: a
    concurrent action that made this content current elsewhere first becomes
    a 409, never a 500."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE project_docs SET last_version = last_version + 1 WHERE id = %s "
                "RETURNING last_version", (document_id,))
            n = (await cur.fetchone())[0]
            await conn.execute("UPDATE project_documents SET is_current = false "
                               "WHERE project_doc_id = %s AND is_current", (document_id,))
            await conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, note, origin, origin_version, is_current, added_by, verified) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s,true)",
                (project_id, document_id, n, doc_id, file_name, note, origin, origin_version,
                 added_by))
    except pg_errors.UniqueViolation:
        other = await _current_holder(conn, project_id, doc_id)
        raise _conflict("current_elsewhere", CURRENT_ELSEWHERE,
                        await document_ref(conn, other) if other else None)
    return n


async def create_document(conn, user_id: str, project_id, doc_id: str, *, file_name: str) -> str:
    """A new project document with `doc_id` as its v1 (spec §4.2 step 4).
    Returns its id. Content already current here (a concurrent filing) → 409
    current_elsewhere."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO project_docs (project_id, created_by, last_version) "
                "VALUES (%s,%s,1) RETURNING id", (project_id, user_id))
            pdoc = str((await cur.fetchone())[0])
            await conn.execute(
                "INSERT INTO project_documents (project_id, project_doc_id, version, doc_id, "
                "file_name, origin, is_current, added_by, verified) "
                "VALUES (%s,%s,1,%s,%s,'upload',true,%s,true)",
                (project_id, pdoc, doc_id, file_name, user_id))
    except pg_errors.UniqueViolation:
        other = await _current_holder(conn, project_id, doc_id)
        raise _conflict("current_elsewhere", CURRENT_ELSEWHERE,
                        await document_ref(conn, other) if other else None)
    audit("placement.added", project=project_id, doc=doc_id, document=pdoc, by=user_id)
    await project_events.record(conn, project_id, user_id, "document.added", doc_id=doc_id,
                                details={"name": file_name})
    return pdoc


async def add_version(conn, user_id: str, project_id, document_id, *, doc_id: str,
                      file_name: str, note: str | None = None) -> dict[str, Any]:
    """Append `doc_id` as the document's new current version (spec §4.1,
    §4.3). The current version's bytes → 409 already_current; an older
    version's → recorded as a restore of it, under its name; another
    document's current bytes here → 409 current_elsewhere. The caller proved
    possession (bytes sent, or a verified upload entry)."""
    note = clean_note(note)
    await lock_document(conn, project_id, document_id)
    await verify_legacy_current(conn, project_id, doc_id, user_id)
    kind, value = await _same_bytes(conn, project_id, document_id, doc_id)
    if kind == "already_current":
        raise _conflict("already_current", ALREADY_CURRENT, await document_ref(conn, document_id))
    if kind == "current_elsewhere":
        raise _conflict("current_elsewhere", CURRENT_ELSEWHERE, await document_ref(conn, value))
    restored_from = value if kind == "older" else None
    if restored_from is not None:
        file_name = (await _version_row(conn, document_id, restored_from))[1]
    n = await _append(conn, project_id, document_id, doc_id=doc_id, file_name=file_name,
                      note=note, origin="restore" if restored_from else "upload",
                      origin_version=restored_from, added_by=user_id)
    details: dict[str, Any] = {"name": file_name, "version": n}
    if restored_from is not None:
        details["from_version"] = restored_from
    if note:
        details["note"] = note
    kind_name = "document.version_restored" if restored_from else "document.version_added"
    await project_events.record(conn, project_id, user_id, kind_name, doc_id=doc_id,
                                details=details)
    audit("version.restored" if restored_from else "version.added", project=project_id,
          document=document_id, version=n, doc=doc_id, by=user_id)
    return {"version": n, "doc_id": doc_id, "restored_from": restored_from}


async def restore_version(conn, user_id: str, project_id, document_id,
                          version: int) -> dict[str, Any]:
    """Append a copy of version `version` — its content and name — as the new
    current version (spec §4.1: restore never moves a pointer back)."""
    await lock_document(conn, project_id, document_id)
    src = await _version_row(conn, document_id, version)
    if src is None:
        raise refusal(404, "not_found", VERSION_NOT_FOUND)
    doc_id, file_name, is_current = src
    if is_current:
        raise _conflict("already_current", ALREADY_CURRENT, await document_ref(conn, document_id))
    kind, value = await _same_bytes(conn, project_id, document_id, doc_id)
    if kind == "current_elsewhere":
        raise _conflict("current_elsewhere", CURRENT_ELSEWHERE, await document_ref(conn, value))
    n = await _append(conn, project_id, document_id, doc_id=doc_id, file_name=file_name,
                      note=None, origin="restore", origin_version=version, added_by=user_id)
    await project_events.record(conn, project_id, user_id, "document.version_restored",
                                doc_id=doc_id, details={"name": file_name, "version": n,
                                                        "from_version": version})
    audit("version.restored", project=project_id, document=document_id, version=n,
          doc=doc_id, by=user_id)
    return {"version": n, "doc_id": doc_id, "restored_from": version}


async def delete_version(conn, user_id: str, project_id, document_id, version: int) -> None:
    """Delete one old version (spec §4.1), then GC its content if nothing
    else references it. The current version → 409 is_current."""
    await lock_document(conn, project_id, document_id)
    src = await _version_row(conn, document_id, version)
    if src is None:
        raise refusal(404, "not_found", VERSION_NOT_FOUND)
    doc_id, _name, is_current = src
    ref = await document_ref(conn, document_id)
    if is_current:
        raise _conflict("is_current", IS_CURRENT, ref)
    await conn.execute("DELETE FROM project_documents WHERE project_doc_id = %s AND version = %s",
                       (document_id, version))
    await project_events.record(conn, project_id, user_id, "document.version_deleted",
                                doc_id=doc_id, details={"name": ref["name"], "version": version})
    audit("version.deleted", project=project_id, document=document_id, version=version,
          doc=doc_id, by=user_id)
    await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="version_deleted")


async def merge_documents(conn, user_id: str, project_id, into_id, from_id) -> None:
    """Absorb document `from_id` into `into_id` (spec §4.1): every version of
    `from_id` moves, in its own order, numbered from into's last_version + 1,
    origin 'merge' with its old number; from's current becomes into's
    current; from's row goes. One-way. Both rows are locked in id order, so
    two crossed merges can't deadlock: the loser finds its document gone."""
    if str(into_id) == str(from_id):
        raise _conflict("merge_self", MERGE_SELF, await document_ref(conn, into_id))
    cur = await conn.execute(
        "SELECT id, last_version FROM project_docs WHERE id = ANY(%s::uuid[]) AND project_id = %s "
        "ORDER BY id FOR UPDATE", ([str(into_id), str(from_id)], project_id))
    last = {str(r[0]): r[1] for r in await cur.fetchall()}
    into_ref, from_ref = await document_ref(conn, into_id), await document_ref(conn, from_id)
    if len(last) != 2 or into_ref is None or from_ref is None:
        raise refusal(404, "not_found", DOCUMENT_NOT_FOUND)
    cur = await conn.execute(  # HISTORY, verified or not: everything moves
        "SELECT version, verified FROM project_documents WHERE project_doc_id = %s "
        "ORDER BY version", (from_id,))
    moving = await cur.fetchall()
    start = last[str(into_id)]
    async with conn.transaction():
        await conn.execute("UPDATE project_documents SET is_current = false "
                           "WHERE project_doc_id = %s AND is_current", (into_id,))
        for i, (old, _verified) in enumerate(moving, start=1):
            await conn.execute(
                "UPDATE project_documents SET project_doc_id = %s, version = %s, origin = 'merge', "
                "origin_version = %s WHERE project_doc_id = %s AND version = %s",
                (into_id, start + i, old, from_id, old))
        await conn.execute("UPDATE project_docs SET last_version = %s WHERE id = %s",
                           (start + len(moving), into_id))
        await conn.execute("DELETE FROM project_docs WHERE id = %s", (from_id,))
    cur = await conn.execute("SELECT doc_id FROM project_documents "
                             "WHERE project_doc_id = %s AND is_current", (into_id,))
    current_doc = (await cur.fetchone())[0]
    moved_verified = sum(1 for _v, verified in moving if verified)
    await project_events.record(
        conn, project_id, user_id, "document.merged", doc_id=current_doc,
        details={"from_name": from_ref["name"], "into_name": into_ref["name"],
                 "versions": moved_verified})
    audit("document.merged", project=project_id, into=into_id, absorbed=from_id,
          versions=moved_verified, by=user_id)


async def remove_document(conn, user_id: str, project_id, document_id) -> None:
    """Remove a document and its whole history (spec §4.1). Every version's
    content is collected FIRST and GC'd after: a cascade never runs GC."""
    await lock_document(conn, project_id, document_id)
    ref = await document_ref(conn, document_id)
    cur = await conn.execute(  # HISTORY, verified or not: everything the delete drops
        "SELECT DISTINCT doc_id FROM project_documents WHERE project_doc_id = %s ORDER BY 1",
        (document_id,))
    doc_ids = [r[0] for r in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT doc_id, (SELECT count(*) FROM project_documents _c "
        "WHERE _c.project_doc_id = %s AND _c.verified) FROM project_documents "
        "WHERE project_doc_id = %s AND is_current", (document_id, document_id))
    current_doc, versions = await cur.fetchone()
    await conn.execute("DELETE FROM project_docs WHERE id = %s", (document_id,))
    await project_events.record(conn, project_id, user_id, "document.removed",
                                doc_id=current_doc,
                                details={"name": ref["name"], "versions": versions})
    audit("placement.removed", project=project_id, document=document_id, doc=current_doc,
          versions=versions, by=user_id)
    for d in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, d, trigger="placement_removed")
```

Then in `server/routers/projects.py`:
- delete `_filed_name`;
- replace its two uses in `link_doc` with `doc_versions.filed_name(conn, doc_id, principal.user_id)`;
- add `doc_versions` to `from ..services import …`.

- [ ] **Step 5: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest server/tests/test_doc_versions_write.py -q`
Expected: PASS (14 tests).

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: all pass.

- [ ] **Step 6: Ask, then commit**

```bash
git add server/services/doc_versions.py server/services/project_events.py server/routers/projects.py \
  server/tests/test_doc_versions_write.py
git commit -m "feat(versions): add, restore, delete, merge and remove — same-bytes rules, one event each, refusals before writes"
```

---

### Task 5: The `/v1/projects/{pid}/documents` routes

**Files:**
- Create: `server/routers/project_documents.py`
- Modify: `server/app.py` (import and `include_router`)
- Test: `server/tests/test_project_documents_routes.py`, `server/tests/test_project_permission_matrix.py` (new rows)

**Interfaces:**
- Consumes: Task 4's service.
- Produces:
  - `GET ""`;
  - `GET /{document_id}/versions`;
  - `POST /{document_id}/versions/from-library` with JSON `{doc_id, note?}` → `201 {version, doc_id, restored_from, state}`;
  - `POST /{document_id}/versions/{version}/restore` → `201`;
  - `DELETE /{document_id}/versions/{version}` → `204`;
  - `POST /{document_id}/merge` with JSON `{from}` → `200` document row;
  - `DELETE /{document_id}` → `204`.
- `router` is exported as `server.routers.project_documents.router`.

- [ ] **Step 1: Write the failing route tests**

`server/tests/test_project_documents_routes.py`:

```python
"""/v1/projects/{pid}/documents (V1 spec §5.1): shapes, refusals and the
admin-only-through-a-role rule. The rules themselves are tested in
test_doc_versions_write.py; this file tests the HTTP layer."""
import uuid

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import project_documents as pd_router
from server.routers import projects as projects_router
from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2, C3 = "1" * 64, "2" * 64, "3" * 64


def _client(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    app.include_router(pd_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
async def proj(db_conn):
    alice = await member(db_conn, "rt-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    for doc, name in ((C1, "report.pdf"), (C2, "report-2.pdf"), (C3, "other.pdf")):
        await seed.seed_doc(db_conn, doc, alice.user_id, file_name=name, state="indexed")
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    return alice, pid, x


async def test_list_history_add_restore_delete_merge_remove(db_conn, proj):
    alice, pid, x = proj
    base = f"/v1/projects/{pid}/documents"
    async with _client(db_conn, alice) as c:
        [row] = (await c.get(base)).json()
        assert (row["id"], row["name"], row["version"], row["versions_count"]) == (x, "report.pdf", 1, 1)
        r = await c.post(f"{base}/{x}/versions/from-library", json={"doc_id": C2, "note": "v2"})
        assert r.status_code == 201
        assert r.json() == {"version": 2, "doc_id": C2, "restored_from": None, "state": "indexed"}
        assert [v["version"] for v in (await c.get(f"{base}/{x}/versions")).json()] == [2, 1]
        r = await c.post(f"{base}/{x}/versions/1/restore")
        assert r.status_code == 201 and r.json()["restored_from"] == 1
        assert (await c.delete(f"{base}/{x}/versions/2")).status_code == 204
        y = await seed.place_doc(db_conn, pid, C3, alice.user_id)
        r = await c.post(f"{base}/{x}/merge", json={"from": y})
        assert r.status_code == 200 and r.json()["name"] == "other.pdf"
        assert (await c.delete(f"{base}/{x}")).status_code == 204
        assert (await c.get(base)).json() == []


async def test_refusal_shapes(db_conn, proj):
    alice, pid, x = proj
    base = f"/v1/projects/{pid}/documents"
    async with _client(db_conn, alice) as c:
        r = await c.post(f"{base}/{x}/versions/from-library", json={"doc_id": C1})
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "already_current"
        assert r.json()["detail"]["document"] == {"id": x, "name": "report.pdf", "version": 1}
        assert (await c.delete(f"{base}/{x}/versions/1")).json()["detail"]["error"] == "is_current"
        assert (await c.get(f"{base}/{uuid.uuid4()}/versions")).status_code == 404
        assert (await c.get(f"{base}/not-a-uuid/versions")).status_code == 422
        r = await c.post(f"{base}/{x}/versions/from-library", json={"doc_id": "NOT-HEX"})
        assert r.status_code == 422
        r = await c.post(f"{base}/{x}/merge", json={"from": x})
        assert r.json()["detail"]["error"] == "merge_self"


async def test_from_library_needs_my_verified_upload(db_conn, proj):
    alice, pid, x = proj
    bob = await member(db_conn, "rt-bob", caps=("reader",))
    await seed.add_member(db_conn, pid, bob.user_id, "contributor")
    async with _client(db_conn, bob) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/versions/from-library",
                         json={"doc_id": C2})
    assert r.status_code == 404  # bob holds no upload entry for C2
```

- [ ] **Step 2: Add the matrix rows**

In `server/tests/test_project_permission_matrix.py`:
- add `from server.routers import project_documents as pd_router` to the imports;
- add `app.include_router(pd_router.router)` after the projects router in `test_permission_matrix`;
- extend `MATRIX`, `_call` and the setup as follows:

```python
DOC_V2 = "3" * 64       # v2 of the placed document, filed by the base owner
DOC_OTHER = "4" * 64    # a second document in the project (merge source)

# (add to MATRIX, keeping the column order)
    "see documents":      (404, 200, 200, 200, 200, 404, 200),
    "see history":        (404, 200, 200, 200, 200, 404, 200),
    "add a version":      (404, 403, 201, 201, 201, 404, 201),
    "restore a version":  (404, 403, 201, 201, 201, 404, 201),
    "delete old version": (404, 403, 403, 204, 204, 404, 403),
    "merge documents":    (404, 403, 403, 200, 200, 404, 403),
    "remove a document":  (404, 403, 403, 204, 204, 404, 403),
```

`_call` gains a `docs` parameter, a dict with keys `placed` and `other` (the project document ids). Its new entries:

```python
        "see documents": lambda: c.get(f"{base}/documents"),
        "see history": lambda: c.get(f"{base}/documents/{docs['placed']}/versions"),
        "add a version": lambda: c.post(f"{base}/documents/{docs['placed']}/versions/from-library",
                                        json={"doc_id": DOC_MINE}),
        "restore a version": lambda: c.post(f"{base}/documents/{docs['placed']}/versions/1/restore"),
        "delete old version": lambda: c.delete(f"{base}/documents/{docs['placed']}/versions/1"),
        "merge documents": lambda: c.post(f"{base}/documents/{docs['placed']}/merge",
                                          json={"from": docs["other"]}),
        "remove a document": lambda: c.delete(f"{base}/documents/{docs['placed']}"),
```

In `test_permission_matrix`, replace the `DOC_PLACED` seeding line and the call:

```python
    await seed.seed_doc(db_conn, DOC_PLACED, base_owner, file_name="placed.pdf")
    await seed.seed_doc(db_conn, DOC_V2, base_owner, file_name="placed-2.pdf")
    await seed.seed_doc(db_conn, DOC_OTHER, base_owner, file_name="other.pdf")
    placed = await seed.place_doc(db_conn, pid, DOC_PLACED, base_owner)
    await seed.add_version(db_conn, placed, DOC_V2, base_owner)
    other = await seed.place_doc(db_conn, pid, DOC_OTHER, base_owner)
    docs = {"placed": placed, "other": other}
    ...
        r = await _call(c, action, pid, target, second_owner, docs)
```

The existing "remove any doc" row uses the old route with `DOC_PLACED`, which is now an *old* version. Task 1's unlink still finds it through the history, so the row stays green until Task 7 replaces it.

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_project_documents_routes.py server/tests/test_project_permission_matrix.py -q`
Expected: FAIL with `ImportError: cannot import name 'project_documents'`.

- [ ] **Step 4: Write the router**

`server/routers/project_documents.py`:

```python
"""/v1/projects/{project_id}/documents — a project's documents and their
versions (V1 spec §5.1). Every route's first check is require_project_role:
a non-member gets 404 before any other id is examined, a member below the
route's role gets 403 insufficient_role, and — as for A0's document routes —
admins act only through a member role. The rules live in
services/doc_versions.py."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps
from ..auth.authz import assert_holds_upload, require_project_role
from ..services import doc_versions, people
from .docs import DOC_ID_PATTERN

router = APIRouter(prefix="/v1/projects/{project_id}/documents", tags=["projects"])
_reader = deps.require_capability("reader")


class FromLibraryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(pattern=DOC_ID_PATTERN)
    note: str | None = Field(default=None, max_length=doc_versions.NOTE_MAX)


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_: uuid.UUID = Field(alias="from")


def _show_email() -> bool:
    return people.load_directory_config().show_email


async def _state(conn, doc_id: str) -> str:
    cur = await conn.execute("SELECT state FROM documents WHERE doc_id = %s", (doc_id,))
    return (await cur.fetchone())[0]


@router.get("")
async def list_documents(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None, admin_ok=False)
    return await doc_versions.list_project_documents(conn, project_id, show_email=_show_email())


@router.get("/{document_id}/versions")
async def list_versions(project_id: uuid.UUID, document_id: uuid.UUID,
                        principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None, admin_ok=False)
    return await doc_versions.list_versions(conn, project_id, document_id,
                                            show_email=_show_email())


@router.post("/{document_id}/versions/from-library", status_code=201)
async def version_from_library(project_id: uuid.UUID, document_id: uuid.UUID,
                               body: FromLibraryIn, principal: deps.Principal = Depends(_reader),
                               conn=Depends(deps.get_conn)):
    """A file the caller uploaded to their library becomes the new version
    (proof of possession: a verified upload entry, A1 §4)."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    await assert_holds_upload(conn, body.doc_id, principal.user_id)
    out = await doc_versions.add_version(
        conn, principal.user_id, project_id, document_id, doc_id=body.doc_id,
        file_name=await doc_versions.filed_name(conn, body.doc_id, principal.user_id),
        note=body.note)
    return {**out, "state": await _state(conn, out["doc_id"])}


@router.post("/{document_id}/versions/{version}/restore", status_code=201)
async def restore_version(project_id: uuid.UUID, document_id: uuid.UUID, version: int,
                          principal: deps.Principal = Depends(_reader),
                          conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    out = await doc_versions.restore_version(conn, principal.user_id, project_id, document_id,
                                             version)
    return {**out, "state": await _state(conn, out["doc_id"])}


@router.delete("/{document_id}/versions/{version}", status_code=204)
async def delete_version(project_id: uuid.UUID, document_id: uuid.UUID, version: int,
                         principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.delete_version(conn, principal.user_id, project_id, document_id, version)
    return Response(status_code=204)


@router.post("/{document_id}/merge")
async def merge(project_id: uuid.UUID, document_id: uuid.UUID, body: MergeIn,
                principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """`document_id` stays; `from` is absorbed into its history (one-way)."""
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.merge_documents(conn, principal.user_id, project_id, document_id,
                                       body.from_)
    return await doc_versions.document_row(conn, project_id, document_id,
                                           show_email=_show_email())


@router.delete("/{document_id}", status_code=204)
async def remove_document(project_id: uuid.UUID, document_id: uuid.UUID,
                          principal: deps.Principal = Depends(_reader),
                          conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    await doc_versions.remove_document(conn, principal.user_id, project_id, document_id)
    return Response(status_code=204)
```

In `server/app.py`, add `from .routers.project_documents import router as project_documents_router` next to the projects import, and `app.include_router(project_documents_router)` right after `app.include_router(projects_router)`.

- [ ] **Step 5: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest server/tests/test_project_documents_routes.py server/tests/test_project_permission_matrix.py -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: all pass, including `test_app_wiring.py`.

- [ ] **Step 6: Ask, then commit**

```bash
git add server/routers/project_documents.py server/app.py \
  server/tests/test_project_documents_routes.py server/tests/test_project_permission_matrix.py
git commit -m "feat(versions): /v1/projects/{pid}/documents routes — list, history, add from library, restore, delete, merge, remove"
```

---

### Task 6: Upload a new version as bytes

**Files:**
- Modify: `server/routers/docs.py` (`_add_upload_entry` → `_store_and_hold` + a thin `_add_upload_entry`)
- Modify: `server/routers/project_documents.py` (add `POST /{document_id}/versions`)
- Create: `server/tests/versions_harness.py`
- Test: `server/tests/test_version_upload.py`

**Interfaces:**
- Consumes: `doc_storage.stage_upload`, `docs._after_upload`, and `doc_versions.add_version`, `lock_document`, `clean_note`.
- Produces:
  - `docs._store_and_hold(staged, name, hold, *, prelock=None) -> (created, existing, outcome)`;
  - `POST /v1/projects/{pid}/documents/{id}/versions`, multipart `file` + optional `file_name` and `note` → `201 {version, doc_id, restored_from, state}`.

- [ ] **Step 1: Write the harness**

`server/tests/versions_harness.py`:

```python
"""docs_harness plus the project routers, for version uploads: the upload
route stores bytes through docs.py's pool (shimmed to the test conn) and the
other routes use deps.get_conn (overridden to the same conn)."""
from __future__ import annotations

from server.auth import deps
from server.routers import project_documents as pd_router
from server.routers import projects as projects_router
from server.tests.docs_harness import build_docs_app


def build_versions_app(db_conn, monkeypatch, *, storage_dir, embed=None):
    app, as_user = build_docs_app(db_conn, monkeypatch, storage_dir=storage_dir, embed=embed)

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.include_router(projects_router.router)
    app.include_router(pd_router.router)
    return app, as_user
```

- [ ] **Step 2: Write the failing tests**

`server/tests/test_version_upload.py`:

```python
"""POST /v1/projects/{pid}/documents/{id}/versions — a revised file's bytes
become the new version (V1 spec §4.4): A1's upload pipeline with a version
row as the holding, the document locked first, no library entry."""
import hashlib

import pytest

from server.routers import docs as docs_router
from server.tests import seed
from server.tests.chat_harness import member
from server.tests.docs_harness import fake_embed
from server.tests.versions_harness import build_versions_app

pytestmark = pytest.mark.asyncio

V1 = b"Version one. It has two sentences."
V2 = b"Version two. It has different words in it."


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class _CountingEmbed:
    def __init__(self):
        self.calls = 0

    async def __call__(self, texts):
        self.calls += 1
        return await fake_embed(texts)


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def embed():
    return _CountingEmbed()


@pytest.fixture
async def setup(db_conn, monkeypatch, store, embed):
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store, embed=embed)
    alice = await member(db_conn, "up-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await seed.seed_doc(db_conn, _sha(V1), alice.user_id, file_name="notes.txt", file_type="text",
                        state="indexed", extracted_by="server")
    x = await seed.place_doc(db_conn, pid, _sha(V1), alice.user_id)
    return as_user, alice, pid, x


def _upload(c, pid, x, data, name="notes.txt", **form):
    return c.post(f"/v1/projects/{pid}/documents/{x}/versions",
                  files={"file": (name, data, "text/plain")}, data=form)


async def test_new_bytes_become_the_indexed_current_version_without_a_library_entry(db_conn, setup):
    as_user, alice, pid, x = setup
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2, name="notes v2.txt", note="rewrote it")
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 2 and r.json()["doc_id"] == _sha(V2)
    cur = await db_conn.execute("SELECT state FROM documents WHERE doc_id = %s", (_sha(V2),))
    assert (await cur.fetchone())[0] == "indexed"  # the background job ran
    cur = await db_conn.execute("SELECT 1 FROM library_entries WHERE doc_id = %s", (_sha(V2),))
    assert await cur.fetchone() is None
    cur = await db_conn.execute("SELECT file_name, note, is_current FROM project_documents "
                                "WHERE project_doc_id = %s AND version = 2", (x,))
    assert await cur.fetchone() == ("notes v2.txt", "rewrote it", True)


async def test_known_bytes_are_not_reprocessed(db_conn, setup, embed):
    as_user, alice, pid, x = setup
    await seed.seed_doc(db_conn, _sha(V2), alice.user_id, file_name="v2.txt", file_type="text",
                        state="indexed", extracted_by="server")
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2)
    assert r.status_code == 201 and embed.calls == 0


async def test_same_bytes_rules(db_conn, setup):
    as_user, alice, pid, x = setup
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V1)
        assert r.status_code == 409 and r.json()["detail"]["error"] == "already_current"
        assert (await _upload(c, pid, x, V2)).status_code == 201
        r = await _upload(c, pid, x, V1, name="renamed.txt")
        assert r.status_code == 201 and r.json()["restored_from"] == 1


async def test_row_upload_ignores_another_documents_name(db_conn, setup):
    """Review Focus 2: the row action is explicit, so no name prompt."""
    as_user, alice, pid, x = setup
    other = b"Another document. Different words."
    await seed.seed_doc(db_conn, _sha(other), alice.user_id, file_name="taken.txt",
                        file_type="text", state="indexed", extracted_by="server")
    await seed.place_doc(db_conn, pid, _sha(other), alice.user_id)
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2, name="taken.txt")
    assert r.status_code == 201


async def test_note_rules_apply_before_bytes_are_stored(db_conn, setup, store):
    """Review Focus 4."""
    as_user, alice, pid, x = setup
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2, note="n" * 501)
        assert r.status_code == 422
        assert list(store.iterdir()) == []  # nothing staged or placed
        r = await _upload(c, pid, x, V2, note="   ")
    assert r.status_code == 201
    cur = await db_conn.execute("SELECT note FROM project_documents "
                                "WHERE project_doc_id = %s AND version = 2", (x,))
    assert (await cur.fetchone())[0] is None


async def test_role_and_visibility_before_any_bytes(db_conn, setup, store):
    as_user, _alice, pid, x = setup
    reader = await member(db_conn, "up-reader", caps=("reader",))
    stranger = await member(db_conn, "up-stranger", caps=("reader",))
    await seed.add_member(db_conn, pid, reader.user_id, "reader")
    async with as_user(reader) as c:
        assert (await _upload(c, pid, x, V2)).status_code == 403
    async with as_user(stranger) as c:
        assert (await _upload(c, pid, x, V2)).status_code == 404
    assert list(store.iterdir()) == []


async def test_a_lost_race_with_gc_still_retries_once_for_a_version(db_conn, setup, monkeypatch):
    """Spec §9: A1's retry holds when the holding is a version, not an entry."""
    as_user, alice, pid, x = setup
    bob = await member(db_conn, "up-bob", caps=("reader",))
    await seed.seed_doc(db_conn, _sha(V2), bob.user_id, file_name="v2.txt", file_type="text",
                        state="indexed", extracted_by="server")
    real = docs_router._lock_existing
    calls = []

    async def gc_wins_once(conn, did):
        calls.append(did)
        if len(calls) == 1:
            # A GC committed between our conflicting INSERT and our lock.
            await conn.execute("DELETE FROM documents WHERE doc_id = %s", (did,))
            return None
        return await real(conn, did)

    monkeypatch.setattr(docs_router, "_lock_existing", gc_wins_once)
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2)
    assert r.status_code == 201 and r.json()["version"] == 2
    assert calls == [_sha(V2)]  # the retry's INSERT created the row; no second lock


async def test_the_document_is_locked_before_the_content_row(db_conn, setup, monkeypatch):
    """Spec §4.4's lock order: prelock runs before the content INSERT, so
    new content doesn't exist yet when the document is locked."""
    as_user, alice, pid, x = setup
    seen = {}
    original = docs_router._store_and_hold

    async def spy(staged, name, hold, *, prelock=None):
        async def checking(conn):
            cur = await conn.execute("SELECT 1 FROM documents WHERE doc_id = %s", (staged.sha256,))
            seen["content_existed"] = await cur.fetchone() is not None
            await prelock(conn)
        return await original(staged, name, hold, prelock=checking)

    monkeypatch.setattr(docs_router, "_store_and_hold", spy)
    async with as_user(alice) as c:
        assert (await _upload(c, pid, x, V2)).status_code == 201
    assert seen == {"content_existed": False}
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_version_upload.py -q`
Expected: FAIL. The POST to `…/versions` is 405 Method Not Allowed: only `…/versions/from-library` exists.

- [ ] **Step 4: Generalise the upload transaction in `server/routers/docs.py`**

Replace `_add_upload_entry` with:

```python
async def _store_and_hold(staged, name: str, hold, *, prelock=None):
    """Create-or-find the content and insert ONE holding for it, in one
    transaction (A1 spec §3, races): the content row is inserted, or locked
    FOR SHARE if it exists, then `hold(conn, created)` inserts the holding —
    a library entry, or (V1) a project document's version — and returns its
    outcome; raising rolls it all back. `prelock(conn)` runs FIRST in the
    same transaction: a version upload locks its document there, so the lock
    order is project_docs → documents everywhere (V1 spec §4.4). Retries ONCE
    if a GC removed the row between our conflicting INSERT and our lock — the
    caller never sees a 5xx. Returns (created, existing, outcome)."""
    for attempt in (1, 2):
        async with get_pool().connection() as conn:
            async with conn.transaction():
                if prelock is not None:
                    await prelock(conn)
                cur = await conn.execute(
                    "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
                    "extracted_by) VALUES (%s,%s,%s,%s,'extracting','server') "
                    "ON CONFLICT (doc_id) DO NOTHING RETURNING doc_id",
                    (staged.sha256, name, staged.file_type, staged.size))
                created = await cur.fetchone() is not None
                existing = None
                if not created:
                    existing = await _lock_existing(conn, staged.sha256)
                    if existing is None:
                        if attempt == 1:
                            logger.warning("Upload of %s lost a race with GC; retrying once",
                                           staged.sha256)
                            continue
                        raise refusal(503, "busy", "Please try again.")
                outcome = await hold(conn, created)
        return created, existing, outcome
    raise AssertionError("unreachable")


async def _add_upload_entry(user_id: str, staged, name: str, tags: list[str]):
    """The library holding: an upload entry — the one caller that verifies,
    because the bytes are in hand."""
    async def hold(conn, created):
        return await doc_content.add_entry(
            conn, user_id, staged.sha256, via="upload", verified=True, tags=tags,
            file_name=None if created else name)

    created, existing, outcome = await _store_and_hold(staged, name, hold)
    if outcome != "exists":
        audit("entry.added", user=user_id, doc=staged.sha256, via="upload",
              dedup=str(not created).lower())
    return created, existing
```

- [ ] **Step 5: Add the upload route to `server/routers/project_documents.py`**

Extend the imports:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile

from . import docs as docs_router
from ..services import doc_storage, doc_versions, people
```

Add the route **before** `version_from_library`:

```python
@router.post("/{document_id}/versions", status_code=201)
async def upload_version(project_id: uuid.UUID, document_id: uuid.UUID,
                         background: BackgroundTasks, file: UploadFile = File(...),
                         file_name: str | None = Form(None), note: str | None = Form(None),
                         principal: deps.Principal = Depends(_reader)):
    """A revised file's bytes become the document's new version (spec §4.4).
    The role and the note are checked before any bytes are stored; A1's
    pipeline stores or dedupes the content and inserts the version as its
    holding, the document locked first; no library entry is added. New
    content is extracted and embedded in the background, as for any upload."""
    docs_router._ensure_ready()
    async with docs_router.get_pool().connection() as conn:
        await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    note = doc_versions.clean_note(note)
    name = (file_name or file.filename or "").strip()[:255] or "document"
    staged = await doc_storage.stage_upload(file, name)

    async def prelock(conn):
        await doc_versions.lock_document(conn, project_id, document_id)

    async def hold(conn, _created):
        return await doc_versions.add_version(conn, principal.user_id, project_id, document_id,
                                              doc_id=staged.sha256, file_name=name, note=note)

    try:
        created, existing, out = await docs_router._store_and_hold(staged, name, hold,
                                                                  prelock=prelock)
        job = await docs_router._after_upload(staged.sha256, staged, created, existing)
    finally:
        doc_storage.discard(staged)  # no-op once placed
    if job is not None:
        background.add_task(job, staged.sha256)
    async with docs_router.get_pool().connection() as conn:
        return {**out, "state": await _state(conn, staged.sha256)}
```

`docs_router.get_pool` is read through the module on purpose: the test harness patches it there.

- [ ] **Step 6: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest server/tests/test_version_upload.py server/tests/test_docs_upload.py -q`
Expected: PASS. The A1 upload tests pass unchanged, including the GC-race retry.

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: all pass.

- [ ] **Step 7: Ask, then commit**

```bash
git add server/routers/docs.py server/routers/project_documents.py \
  server/tests/versions_harness.py server/tests/test_version_upload.py
git commit -m "feat(versions): upload a revised file's bytes as the new version — A1 pipeline, document locked first, no library entry"
```

---

### Task 7: Filing from a library with the name-match flow; the old unlink route goes

**Files:**
- Modify: `server/services/doc_versions.py` (add `file_from_library`)
- Modify: `server/routers/projects.py` (rewrite `link_doc`; delete `unlink_doc`)
- Test: `server/tests/test_project_filing.py` (new), `server/tests/test_projects_router.py` (rewrite the doc-route tests), `server/tests/test_project_permission_matrix.py` (two rows)

**Interfaces:**
- Consumes: Task 4's `verify_legacy_current`, `create_document`, `add_version`, `document_ref`.
- Produces:
  - `async doc_versions.file_from_library(conn, user_id, project_id, doc_id, *, file_name, as_version_of=None, keep_both=False) -> tuple[int, dict]`;
  - `PUT /v1/projects/{pid}/docs/{doc_id}?as_version_of=&keep_both=` → `201`/`200 {document_id, version[, restored_from]}` | `409 name_exists` | `409 older_version {document, version}` | `409 current_elsewhere`.
  - `DELETE /v1/projects/{pid}/docs/{doc_id}` no longer exists: the server answers `405`.

- [ ] **Step 1: Write the failing filing tests**

`server/tests/test_project_filing.py`:

```python
"""PUT /v1/projects/{pid}/docs/{doc_id} — filing from a library (V1 spec
§4.2): the checks in order 0–4, and the two choices."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import projects as projects_router
from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2, C3 = "1" * 64, "2" * 64, "3" * 64


def _client(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
async def ctx(db_conn):
    alice = await member(db_conn, "fl-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    return alice, pid


async def _events(db_conn, pid):
    cur = await db_conn.execute("SELECT kind FROM project_events WHERE project_id = %s "
                                "AND kind LIKE 'document.%%' ORDER BY id", (pid,))
    return [r[0] for r in await cur.fetchall()]


async def test_a_new_file_is_a_new_document_and_refiling_is_idempotent(db_conn, ctx):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="Q3.pdf")
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}")
        assert r.status_code == 201 and r.json()["version"] == 1
        again = await c.put(f"/v1/projects/{pid}/docs/{C1}")
        assert again.status_code == 200 and again.json() == r.json()
    assert await _events(db_conn, pid) == ["document.added"]


@pytest.mark.parametrize("filed_as", ["report.pdf", "  Report.PDF ", "REPORT.pdf"])
async def test_a_matching_name_asks_and_both_choices_work(db_conn, ctx, filed_as):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="report.pdf")
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name=filed_as)
    await seed.seed_doc(db_conn, C3, alice.user_id, file_name=filed_as)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C2}")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "name_exists"
        assert r.json()["detail"]["document"] == {"id": x, "name": "report.pdf", "version": 1}
        r = await c.put(f"/v1/projects/{pid}/docs/{C2}", params={"as_version_of": x})
        assert r.status_code == 201 and r.json() == {"document_id": x, "version": 2,
                                                     "restored_from": None}
        r = await c.put(f"/v1/projects/{pid}/docs/{C3}", params={"keep_both": "true"})
        assert r.status_code == 201 and r.json()["document_id"] != x


async def test_the_same_name_in_another_project_does_not_ask(db_conn, ctx):
    alice, pid = ctx
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="README.md")
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name="README.md")
    await seed.place_doc(db_conn, other, C1, alice.user_id)
    async with _client(db_conn, alice) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{C2}")).status_code == 201


async def test_an_older_versions_bytes_offer_a_restore_and_win_over_a_name_match(db_conn, ctx):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="taken.pdf")  # same name as E's
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name="d2.pdf")
    await seed.seed_doc(db_conn, C3, alice.user_id, file_name="taken.pdf")
    d = await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="doc.pdf")
    await seed.add_version(db_conn, d, C2, alice.user_id)  # C1 is now v1, old
    await seed.place_doc(db_conn, pid, C3, alice.user_id)  # E: current, named taken.pdf
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}")
        assert r.status_code == 409 and r.json()["detail"]["error"] == "older_version"
        assert (r.json()["detail"]["document"]["id"], r.json()["detail"]["version"]) == (d, 1)
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}", params={"as_version_of": d})
        assert r.status_code == 201 and r.json()["restored_from"] == 1


async def test_content_current_elsewhere_cannot_become_another_documents_version(db_conn, ctx):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id)
    await seed.seed_doc(db_conn, C2, alice.user_id)
    await seed.place_doc(db_conn, pid, C1, alice.user_id)
    y = await seed.place_doc(db_conn, pid, C2, alice.user_id)
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}", params={"as_version_of": y})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "current_elsewhere"


async def test_a_legacy_unverified_current_version_is_proved_not_duplicated(db_conn, ctx):
    alice, pid = ctx
    bob = await member(db_conn, "fl-bob")
    await seed.seed_doc(db_conn, C1, alice.user_id)
    x = await seed.place_doc(db_conn, pid, C1, bob.user_id, verified=False)
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}")
    assert r.status_code == 200 and r.json()["document_id"] == x
    assert await _events(db_conn, pid) == ["document.added"]
```

- [ ] **Step 2: Rewrite the doc-route tests in `server/tests/test_projects_router.py`**

Add a helper next to `_linked`:

```python
async def _document_of(db_conn, pid, doc_id):
    cur = await db_conn.execute(  # HISTORY
        "SELECT project_doc_id FROM project_documents WHERE project_id=%s AND doc_id=%s",
        (pid, doc_id))
    row = await cur.fetchone()
    return str(row[0]) if row else None
```

Then make these changes:

1. **Successful filings return a status, not 204.** Every `assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204` that *creates* a filing becomes `== 201`. A repeat filing becomes `== 200`. The affected tests:
   - `test_link_own_doc_into_own_project`: the first PUT is 201, the second 200;
   - `test_link_own_doc_into_project_where_member`: 201;
   - `test_admins_file_documents_only_as_members`: the second PUT is 201;
   - `test_deleting_project_keeps_doc_and_drops_link`: 201.
2. **`test_filing_and_removing_record_events_with_the_document_name`:**
   - the first PUT is `201` and the second `200`;
   - the delete becomes `c.delete(f"/v1/projects/{pid}/documents/{await _document_of(db_conn, pid, DOC_A)}")`. Mount the documents router by adding `app.include_router(pd_router.router)` to `_app`, with `from server.routers import project_documents as pd_router` at the top;
   - the expected events become `[("document.added", DOC_A, "Q3 report.pdf"), ("document.removed", DOC_A, "Q3 report.pdf")]`, unchanged.
3. **`test_link_malformed_ids_are_422`:** delete the `c.delete(path)` assertion and keep the PUT one. Add a case for the new route: `assert (await c.delete("/v1/projects/00000000-0000-0000-0000-000000000001/documents/not-a-uuid")).status_code == 422`.
4. **`test_unlink_resolution_table`** becomes `test_remove_resolution_table`, with the same parameters. The DELETE goes to `f"/v1/projects/{pid}/documents/{document_id}"`, where `document_id = await _document_of(db_conn, pid, DOC_A) or str(uuid.uuid4())` (add `import uuid`). Expected 404 detail: `{"error": "not_found", "message": doc_versions.DOCUMENT_NOT_FOUND}`, with `from server.services import doc_versions`.
5. **`test_uploader_who_left_the_project_cannot_unlink` and `test_placement_outlives_uploaders_entry_and_last_unlink_gcs`:** the DELETE goes to `/v1/projects/{pid}/documents/{await _document_of(db_conn, pid, DOC_A)}`, with the same expected codes. **`test_link_routes_require_reader_capability`** files nothing, so `_document_of` would return `None` and build `/documents/None`. Use `/v1/projects/{pid}/documents/{uuid.uuid4()}` there: the capability check (403) runs before the path is examined.

In `server/tests/test_project_permission_matrix.py`:
- the "file own doc" row becomes `(404, 403, 201, 201, 201, 404, 201)`;
- delete the "remove any doc" row and its `_call` entry. "remove a document" from Task 5 covers it.

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_project_filing.py server/tests/test_projects_router.py server/tests/test_project_permission_matrix.py -q`
Expected: FAIL. PUT still returns 204, and there's no `name_exists`.

- [ ] **Step 4: Add `file_from_library` to `server/services/doc_versions.py`**

```python
OLDER_VERSION = "This file is an older version of a document in this project."
NAME_EXISTS = "A document with this name is already in this project."


async def _older_version_of(conn, project_id, doc_id: str):
    cur = await conn.execute(  # HISTORY, not current anywhere here (step 1 ran first)
        "SELECT project_doc_id, max(version) FROM project_documents "
        "WHERE project_id = %s AND doc_id = %s AND verified GROUP BY project_doc_id "
        "ORDER BY project_doc_id LIMIT 1", (project_id, doc_id))
    row = await cur.fetchone()
    return (str(row[0]), row[1]) if row else None


async def _same_name(conn, project_id, file_name: str) -> str | None:
    cur = await conn.execute(  # CURRENT
        "SELECT project_doc_id FROM project_documents WHERE project_id = %s AND is_current "
        "AND verified AND lower(btrim(file_name)) = lower(btrim(%s)) "
        "ORDER BY added_at, project_doc_id LIMIT 1", (project_id, file_name))
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def file_from_library(conn, user_id: str, project_id, doc_id: str, *, file_name: str,
                            as_version_of=None, keep_both: bool = False) -> tuple[int, dict]:
    """Spec §4.2, first match wins: 0 prove a legacy unverified current
    version; 1 already current here → 200 (or 409 current_elsewhere when the
    caller asked for another document); `as_version_of` → a new version of
    that document under §4.3; 2 an old version of D → 409 older_version;
    3 a current document with this name → 409 name_exists; 4 a new document.
    `keep_both` skips 2 and 3. The name match is advisory, not a constraint."""
    proved = await verify_legacy_current(conn, project_id, doc_id, user_id)
    if proved:
        audit("placement.added", project=project_id, doc=doc_id, document=proved, by=user_id)
        await project_events.record(conn, project_id, user_id, "document.added", doc_id=doc_id,
                                    details={"name": (await document_ref(conn, proved))["name"]})
    current = await _current_holder(conn, project_id, doc_id)
    if current is not None:
        if as_version_of is not None and str(as_version_of) != current:
            raise _conflict("current_elsewhere", CURRENT_ELSEWHERE,
                            await document_ref(conn, current))
        ref = await document_ref(conn, current)
        return 200, {"document_id": current, "version": ref["version"]}
    if as_version_of is not None:
        out = await add_version(conn, user_id, project_id, as_version_of, doc_id=doc_id,
                                file_name=file_name)
        return 201, {"document_id": str(as_version_of), "version": out["version"],
                     "restored_from": out["restored_from"]}
    if not keep_both:
        older = await _older_version_of(conn, project_id, doc_id)
        if older:
            raise _conflict("older_version", OLDER_VERSION, await document_ref(conn, older[0]),
                            version=older[1])
        same = await _same_name(conn, project_id, file_name)
        if same:
            raise _conflict("name_exists", NAME_EXISTS, await document_ref(conn, same))
    pdoc = await create_document(conn, user_id, project_id, doc_id, file_name=file_name)
    return 201, {"document_id": pdoc, "version": 1}
```

- [ ] **Step 5: Rewrite `link_doc` and delete `unlink_doc` in `server/routers/projects.py`**

```python
@router.put("/{project_id}/docs/{doc_id}")
async def link_doc(project_id: uuid.UUID, doc_id: DocId, response: Response,
                   as_version_of: uuid.UUID | None = Query(None),
                   keep_both: bool = Query(False),
                   principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """File a doc from my library into a project (V1 spec §4.2): a
    Contributor or above (admins only through a member role, A0 §5) who holds
    a VERIFIED upload entry for it (A1 §5). The project is checked first, so
    an invisible project 404s before doc ids can be used to probe it. A name
    or bytes the project already has → 409 name_exists / older_version; the
    caller retries with `as_version_of` or `keep_both`. 201 when something
    was filed, 200 when it already was."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    await assert_holds_upload(conn, doc_id, principal.user_id)
    status, body = await doc_versions.file_from_library(
        conn, principal.user_id, project_id, doc_id,
        file_name=await doc_versions.filed_name(conn, doc_id, principal.user_id),
        as_version_of=as_version_of, keep_both=keep_both)
    response.status_code = status
    return body
```

Delete `unlink_doc` entirely. Update the module docstring's last sentence to: `Documents (A1 §5, V1): a Contributor who holds an upload entry files them in (§4.2's name-match flow); versions, merges and removal live in routers/project_documents.py.` Remove the imports that are now unused (`refusal` if nothing else uses it: check with `ruff`/grep before removing).

- [ ] **Step 6: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest server/tests/test_project_filing.py server/tests/test_projects_router.py server/tests/test_project_permission_matrix.py -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: all pass. `test_entry_verification.py` still passes: its filing assertions read `verified` and don't check the status code. If one does assert 204, change it to 201 and note it in the report.

- [ ] **Step 7: Ask, then commit**

```bash
git add server/services/doc_versions.py server/routers/projects.py server/tests/test_project_filing.py \
  server/tests/test_projects_router.py server/tests/test_project_permission_matrix.py
git commit -m "feat(versions)!: filing asks on a known name or bytes (as_version_of / keep_both); DELETE …/docs/{doc_id} removed"
```

---

### Task 8: Concurrency: the locks and the unique-index net, on real connections

**Files:**
- Test: `server/tests/test_doc_versions_concurrency.py`

**Interfaces:**
- Consumes: `doc_versions.add_version`, `merge_documents`. No production code changes, unless a test exposes a real race. In that case, fix it in `doc_versions.py` within this task.

- [ ] **Step 1: Write the tests**

These need separate connections that each commit, so they use a scratch database, as the migration tests do:

```python
"""Spec §4.4 on real, separately committing connections: the document lock
serialises version numbers, crossed merges can't deadlock, and the unique
index turns a lost race into a 409, never a 500."""
import asyncio
import secrets

import pytest
from fastapi import HTTPException
from psycopg import AsyncConnection

from server.services import doc_versions
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for

pytestmark = pytest.mark.asyncio

C = {i: str(i) * 64 for i in range(1, 6)}


@pytest.fixture
async def scratch():
    name = f"natural_reader_conc_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = url_for(name)
    await apply_migrations(url)
    conn = await AsyncConnection.connect(url, autocommit=True)
    cur = await conn.execute("INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                             "VALUES ('i','c','c@x.io','member','active') RETURNING id")
    uid = str((await cur.fetchone())[0])
    cur = await conn.execute("INSERT INTO projects (created_by, name) VALUES (%s,'P') RETURNING id",
                             (uid,))
    pid = str((await cur.fetchone())[0])
    await conn.execute("INSERT INTO project_members (project_id, user_id, role) "
                       "VALUES (%s,%s,'owner')", (pid, uid))
    for doc in C.values():
        await conn.execute("INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, "
                           "extracted_by) VALUES (%s,%s,'pdf',1,'indexed','server')", (doc, doc[:4]))
        await conn.execute("INSERT INTO library_entries (user_id, doc_id, added_via, verified) "
                           "VALUES (%s,%s,'upload',true)", (uid, doc))
    try:
        yield url, conn, uid, pid
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _in_tx(url, fn):
    conn = await AsyncConnection.connect(url)
    try:
        async with conn.transaction():
            return await fn(conn)
    finally:
        await conn.close()


async def _new_doc(url, uid, pid, doc_id):
    return await _in_tx(url, lambda c: doc_versions.create_document(c, uid, pid, doc_id,
                                                                    file_name=doc_id[:4]))


async def test_two_simultaneous_uploads_become_consecutive_versions(scratch):
    url, _conn, uid, pid = scratch
    x = await _new_doc(url, uid, pid, C[1])
    results = await asyncio.gather(
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[2], file_name="b")),
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[3], file_name="c")))
    assert sorted(r["version"] for r in results) == [2, 3]


async def test_crossed_merges_one_wins_one_404s_no_deadlock(scratch):
    url, _conn, uid, pid = scratch
    a = await _new_doc(url, uid, pid, C[1])
    b = await _new_doc(url, uid, pid, C[2])
    results = await asyncio.wait_for(asyncio.gather(
        _in_tx(url, lambda c: doc_versions.merge_documents(c, uid, pid, a, b)),
        _in_tx(url, lambda c: doc_versions.merge_documents(c, uid, pid, b, a)),
        return_exceptions=True), timeout=20)
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], HTTPException)
    assert errors[0].status_code == 404


async def test_same_bytes_into_two_documents_at_once_is_a_409_not_a_500(scratch):
    url, _conn, uid, pid = scratch
    x = await _new_doc(url, uid, pid, C[1])
    y = await _new_doc(url, uid, pid, C[2])
    results = await asyncio.gather(
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, x, doc_id=C[5], file_name="e")),
        _in_tx(url, lambda c: doc_versions.add_version(c, uid, pid, y, doc_id=C[5], file_name="e")),
        return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], HTTPException)
    assert (errors[0].status_code, errors[0].detail["error"]) == (409, "current_elsewhere")
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest server/tests/test_doc_versions_concurrency.py -q`
Expected: PASS (3 tests).

If one fails, it is a real race. Use `superpowers:systematic-debugging`, fix it in `doc_versions.py`, and report it. Never weaken a test.

- [ ] **Step 3: Ask, then commit**

```bash
git add server/tests/test_doc_versions_concurrency.py
git commit -m "test(versions): concurrent uploads, crossed merges and the unique-index net on real connections"
```

---

### Task 9: Chat names documents consistently and labels an old version

**Files:**
- Modify: `server/services/doc_search.py` (`ReadableDoc`, `readable_doc`)
- Modify: `server/chat/prompt.py` (`version_note_text`, `_document_line`)
- Modify: `server/chat/tools/scope.py` (`documents_listing`)
- Test: `server/tests/test_chat_tools.py`, `server/tests/test_chat_prompt.py`

**Interfaces:**
- Consumes: `doc_versions.display_name_sql/params`, `versions_in`, `old_version_of`.
- Produces:
  - `ReadableDoc.version_note: dict | None` (the `old_version_of` item);
  - `prompt.version_note_text(note: dict) -> str`;
  - listing items gain `"version_note": str` only when the open document is an old version.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_chat_tools.py`:

```python
async def test_an_old_version_carries_a_version_note_into_the_listing(indexed):
    conn, alice = indexed
    pid = await seed.make_project(conn, alice.user_id, "Thesis group")
    newer = "e" * 64
    await seed.seed_doc(conn, newer, alice.user_id, file_name="Thesis v2.pdf", state="indexed")
    pdoc = await seed.place_doc(conn, pid, DOC, alice.user_id)
    await seed.add_version(conn, pdoc, newer, alice.user_id)
    doc = await doc_search.readable_doc(conn, DOC, alice.user_id)
    assert doc.version_note["project"]["name"] == "Thesis group"
    [item] = scope.documents_listing([doc])
    assert item["version_note"] == ('version 1 in project "Thesis group"; '
                                    "not the current version, which is version 2")
    current = await doc_search.readable_doc(conn, newer, alice.user_id)
    assert current.version_note is None
    assert "version_note" not in scope.documents_listing([current])[0]


async def test_a_project_only_reader_sees_the_name_as_filed_not_the_canonical_one(indexed):
    conn, alice = indexed
    bob = await member(conn, "bob-chat")
    pid = await seed.make_project(conn, alice.user_id, "P", members=[(bob.user_id, "reader")])
    await seed.place_doc(conn, pid, DOC, alice.user_id, file_name="Group thesis.pdf")
    doc = await doc_search.readable_doc(conn, DOC, bob.user_id)
    assert doc.name == "Group thesis.pdf"
```

(Add `from server.chat.tools import scope` to that file's imports.)

Append to `server/tests/test_chat_prompt.py`:

```python
def test_an_old_version_is_named_as_one_in_the_rules():
    from server.chat.prompt import system_rules
    from server.services.doc_search import ReadableDoc
    note = {"project": {"id": "p", "name": "Q3 <team>"}, "version": 1, "current_version": 3}
    doc = ReadableDoc("d" * 64, "report.pdf", "indexed", 4, version_note=note)
    rules = system_rules(doc, [], has_pins=False)
    assert 'It is an older version: version 1 in project "Q3 ‹team›"' in rules
    assert "say so when that matters" in rules
    plain = system_rules(ReadableDoc("d" * 64, "report.pdf", "indexed", 4), [], has_pins=False)
    assert "older version" not in plain
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_chat_tools.py server/tests/test_chat_prompt.py -q`
Expected: FAIL with `TypeError: unexpected keyword argument 'version_note'` / `AttributeError`.

- [ ] **Step 3: Implement**

In `server/services/doc_search.py`, add `from . import doc_versions` to the imports. There's no cycle: `doc_versions` never imports `doc_search`. Then add the field to `ReadableDoc` (last, with a default):

```python
    version_note: dict | None = None  # V1 §7: an old version and nowhere current (old_version_of)
```

Replace `readable_doc`:

```python
async def readable_doc(conn, doc_id: str, user_id: str) -> ReadableDoc | None:
    """The document if `user_id` may read it (the one SQL definition in
    authz.readable_docs_where), else None. Never trusts the id alone. Named
    as the Library names it (doc_versions.display_name_sql), and carrying a
    version note when it is an old version (V1 spec §7)."""
    cur = await conn.execute(
        f"SELECT d.doc_id, {doc_versions.display_name_sql('d', '_me')}, d.state, d.page_count, "
        "d.embedding_profile, d.embedding_model FROM documents d "
        "LEFT JOIN library_entries _me ON _me.doc_id = d.doc_id AND _me.user_id = %s "
        f"WHERE d.doc_id = %s AND {readable_docs_where('d')}",
        [*doc_versions.display_name_params(user_id), user_id, doc_id,
         *readable_docs_params(user_id)])
    row = await cur.fetchone()
    if not row:
        return None
    note = doc_versions.old_version_of(await doc_versions.versions_in(conn, doc_id, user_id))
    return ReadableDoc(row[0], row[1] or "document", *row[2:], version_note=note)
```

In `server/chat/prompt.py`, add after `display_name`:

```python
def version_note_text(note: dict) -> str:
    """How an old version is described to the model (V1 spec §7). The
    project's name is someone else's text: one inert line, like any name."""
    return (f'version {note["version"]} in project "{display_name(note["project"]["name"])}"; '
            f'not the current version, which is version {note["current_version"]}')
```

At the end of `_document_line`, before each `return` of a non-None line, append the note. Simplest: rename the existing function body to `_document_line_base(doc)` and add:

```python
def _document_line(doc: ReadableDoc | None) -> str | None:
    line = _document_line_base(doc)
    if line and doc is not None and doc.version_note:
        line += (f" It is an older version: {version_note_text(doc.version_note)}. "
                 "Its facts may be out of date, so say so when that matters.")
    return line
```

In `server/chat/tools/scope.py`:

```python
from ..prompt import display_name, version_note_text


def documents_listing(scope: list[ReadableDoc]) -> list[dict]:
    """How a result names its documents: a short ref plus the reader's name,
    and a version note when the document is an old version (V1 spec §7)."""
    items = []
    for ref, d in enumerate(scope, start=1):
        item = {"ref": ref, "name": display_name(d.name)}
        if d.version_note:
            item["version_note"] = version_note_text(d.version_note)
        items.append(item)
    return items
```

- [ ] **Step 4: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest server/tests/test_chat_tools.py server/tests/test_chat_prompt.py server/tests/test_chat_context.py -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: all pass.

- [ ] **Step 5: Ask, then commit**

```bash
git add server/services/doc_search.py server/chat/prompt.py server/chat/tools/scope.py \
  server/tests/test_chat_tools.py server/tests/test_chat_prompt.py
git commit -m "feat(versions): chat names a document as the Library does and tells the model when it is an old version"
```

---

### Task 10: Frontend API layer

**Files:**
- Modify: `src/lib/projectsApi.js`
- Modify: `src/lib/docMeta.js` (`linkDocToProject`, `registerDocument`)
- Create: `src/lib/projectsApi.test.js`
- Modify: `src/lib/docMeta.test.js`

**Interfaces:**
- Produces (on the `projectsApi(host, port)` object):
  - `documents(pid)`;
  - `versions(pid, docId)`;
  - `uploadVersion(pid, docId, file, note)`;
  - `versionFromLibrary(pid, docId, contentId, note)`;
  - `restoreVersion(pid, docId, n)`;
  - `deleteVersion(pid, docId, n)`;
  - `mergeDocuments(pid, intoId, fromId)`;
  - `removeDocument(pid, docId)`;
  - `fileDoc(pid, contentId, choice?)` → `{ filed } | { conflict }`, throwing on any other refusal. `choice` is `{ asVersionOf }` or `{ keepBoth: true }`.
- Produces (in `docMeta`):
  - `linkDocToProject({..., choice})` → `{ filed } | { conflict } | { error }` (never throws);
  - `registerDocument` adds `link` to its result when it linked.

- [ ] **Step 1: Write the failing tests**

`src/lib/projectsApi.test.js`:

```js
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../utils/apiFetch';
import { projectsApi } from './projectsApi';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const api = projectsApi('', '');

describe('projectsApi — documents and versions (V1)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('fileDoc returns the filing, PUTting with no query by default', async () => {
    apiFetch.mockResolvedValue(json(201, { document_id: 'x', version: 1 }));
    expect(await api.fileDoc('p1', 'd1')).toEqual({ filed: { document_id: 'x', version: 1 } });
    expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p1/docs/d1', { method: 'PUT' });
  });

  it('fileDoc sends the choice as a query', async () => {
    apiFetch.mockResolvedValue(json(201, { document_id: 'x', version: 2 }));
    await api.fileDoc('p1', 'd1', { asVersionOf: 'x' });
    expect(apiFetch.mock.calls[0][2]).toBe('/v1/projects/p1/docs/d1?as_version_of=x');
    await api.fileDoc('p1', 'd1', { keepBoth: true });
    expect(apiFetch.mock.calls[1][2]).toBe('/v1/projects/p1/docs/d1?keep_both=true');
  });

  it.each(['name_exists', 'older_version'])('fileDoc returns a %s conflict instead of throwing', async (error) => {
    const detail = { error, message: 'm', document: { id: 'x', name: 'a.pdf', version: 2 }, version: 1 };
    apiFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail }) });
    expect(await api.fileDoc('p1', 'd1')).toEqual({ conflict: detail });
  });

  it('fileDoc throws the notice for any other refusal', async () => {
    apiFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: { error: 'current_elsewhere', message: 'Merge the two instead.' } }) });
    await expect(api.fileDoc('p1', 'd1')).rejects.toThrow('Merge the two instead.');
  });

  it('uploadVersion posts multipart with the note', async () => {
    apiFetch.mockResolvedValue(json(201, { version: 2, doc_id: 'h', restored_from: null, state: 'indexing' }));
    const file = new File(['x'], 'r.pdf');
    await api.uploadVersion('p1', 'x', file, 'fixed');
    const [, , path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/v1/projects/p1/documents/x/versions');
    expect(opts.method).toBe('POST');
    expect(opts.body.get('file')).toBeInstanceOf(File);
    expect(opts.body.get('note')).toBe('fixed');
    expect(opts.headers).toBeUndefined(); // the browser sets the multipart boundary
  });

  it('the other calls hit the documented routes', async () => {
    apiFetch.mockResolvedValue(json(200, []));
    await api.documents('p1');
    await api.versions('p1', 'x');
    await api.versionFromLibrary('p1', 'x', 'h', '');
    await api.restoreVersion('p1', 'x', 1);
    await api.mergeDocuments('p1', 'x', 'y');
    apiFetch.mockResolvedValue(json(204, null));
    await api.deleteVersion('p1', 'x', 1);
    await api.removeDocument('p1', 'x');
    expect(apiFetch.mock.calls.map(([, , path, o]) => `${o?.method || 'GET'} ${path}`)).toEqual([
      'GET /v1/projects/p1/documents',
      'GET /v1/projects/p1/documents/x/versions',
      'POST /v1/projects/p1/documents/x/versions/from-library',
      'POST /v1/projects/p1/documents/x/versions/1/restore',
      'POST /v1/projects/p1/documents/x/merge',
      'DELETE /v1/projects/p1/documents/x/versions/1',
      'DELETE /v1/projects/p1/documents/x',
    ]);
    expect(JSON.parse(apiFetch.mock.calls[2][3].body)).toEqual({ doc_id: 'h', note: null });
    expect(JSON.parse(apiFetch.mock.calls[4][3].body)).toEqual({ from: 'y' });
  });
});
```

Append to `src/lib/docMeta.test.js`, inside `describe('registerDocument', …)`:

```js
  it('returns the filing conflict so the app can ask', async () => {
    const detail = { error: 'name_exists', document: { id: 'x', name: 'a.pdf', version: 1 } };
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(202, { doc_id: 'd1', dedup: false, state: 'stored' });
      if (opts?.method === 'PUT') return { ok: false, status: 409, json: async () => ({ detail }) };
      return json(404, {});
    });
    const result = await registerDocument({ ...baseArgs, tags: [], projectId: 'p1' });
    expect(result.link).toEqual({ conflict: detail });
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/lib/projectsApi.test.js src/lib/docMeta.test.js`
Expected: FAIL. `api.documents is not a function`, and `result.link` is undefined.

- [ ] **Step 3: Implement `src/lib/projectsApi.js`**

Change the import to `import { describeRefusal, noticeFor, PROJECT_NOT_FOUND } from './apiErrors';`. Inside `projectsApi`, after `call`, add:

```js
  const notice = { serverNotFound: true, notFound: PROJECT_NOT_FOUND };
  // Multipart: no Content-Type header, so the browser sets the boundary.
  const upload = async (path, file, note) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (note) form.append('note', note);
    const res = await apiFetch(host, port, path, { method: 'POST', body: form });
    if (!res.ok) throw new Error(await describeRefusal(res, notice));
    return res.json();
  };
  // Filing (V1 spec §4.2): a name or bytes the project already has is a
  // question for the person, not an error — returned as { conflict }.
  const FILING_CONFLICTS = new Set(['name_exists', 'older_version']);
  const fileInto = async (projectId, contentId, choice) => {
    const query = choice?.asVersionOf ? `?as_version_of=${enc(choice.asVersionOf)}`
      : choice?.keepBoth ? '?keep_both=true' : '';
    const res = await apiFetch(host, port, `${project(projectId)}/docs/${enc(contentId)}${query}`, { method: 'PUT' });
    if (!res.ok) {
      let detail = null;
      try {
        detail = (await res.json())?.detail ?? null;
      } catch {
        // non-JSON body — fall back to the status
      }
      if (res.status === 409 && FILING_CONFLICTS.has(detail?.error)) return { conflict: detail };
      throw new Error(noticeFor(res.status, detail, notice));
    }
    return { filed: res.status === 204 ? null : await res.json() };
  };
  const documents = (id) => `${project(id)}/documents`;
  const document = (id, docId) => `${documents(id)}/${enc(docId)}`;
```

In the returned object, replace `fileDoc`, `unfileDoc` and `projectDocs` with:

```js
    fileDoc: (id, contentId, choice) => fileInto(id, contentId, choice),
    documents: (id) => call(documents(id)),
    versions: (id, docId) => call(`${document(id, docId)}/versions`),
    uploadVersion: (id, docId, file, note) => upload(`${document(id, docId)}/versions`, file, note),
    versionFromLibrary: (id, docId, contentId, note) => call(`${document(id, docId)}/versions/from-library`,
      { method: 'POST', body: { doc_id: contentId, note: note || null } }),
    restoreVersion: (id, docId, n) => call(`${document(id, docId)}/versions/${n}/restore`, { method: 'POST' }),
    deleteVersion: (id, docId, n) => call(`${document(id, docId)}/versions/${n}`, { method: 'DELETE' }),
    mergeDocuments: (id, intoId, fromId) => call(`${document(id, intoId)}/merge`, { method: 'POST', body: { from: fromId } }),
    removeDocument: (id, docId) => call(document(id, docId), { method: 'DELETE' }),
```

Update the file's top comment to say it also covers "a project's documents and their versions (V1)".

- [ ] **Step 4: Implement `src/lib/docMeta.js`**

Add `import { projectsApi } from './projectsApi';`. Replace `linkDocToProject`:

```js
// Fail-soft: the document is in the library either way; a failed link is
// logged and must not block the indexing/conversion that follows. Returns
// the outcome (V1): { filed }, { conflict } — the project already has this
// name or these bytes, so the app asks (NameMatchDialog) — or { error }.
export async function linkDocToProject({ apiHost, apiPort, projectId, docId, choice }) {
    try {
        return await projectsApi(apiHost, apiPort).fileDoc(projectId, docId, choice);
    } catch (e) {
        console.error('Doc project link failed:', e);
        return { error: e.message };
    }
}
```

In `registerDocument`, change the link block to:

```js
    if (projectId && linkProject) {
        result.link = await linkDocToProject({ apiHost, apiPort, projectId, docId: result.docId });
    }
```

and add "`link` (when it linked): the filing's outcome, see linkDocToProject" to its doc comment.

- [ ] **Step 5: Run the tests**

Run: `npx vitest run src/lib`
Expected: PASS. The existing docMeta tests still hold: `toEqual` ignores a missing `link`, and the fail-soft test still sees `console.error`.

- [ ] **Step 6: Ask, then commit**

```bash
git add src/lib/projectsApi.js src/lib/projectsApi.test.js src/lib/docMeta.js src/lib/docMeta.test.js
git commit -m "feat(versions-ui): API calls for documents and versions; filing returns name/bytes conflicts to ask about"
```

---

### Task 11: The name-match dialog, its hook, and the reader's filing paths

**Files:**
- Create: `src/components/projects/NameMatchDialog.jsx`, `src/components/projects/NameMatchDialog.test.jsx`
- Create: `src/hooks/useProjectFiling.js`, `src/hooks/useProjectFiling.test.jsx`
- Modify: `src/App.jsx` (index and convert flows; render the dialog)

**Interfaces:**
- Consumes: `projectsApi(...).fileDoc` and `registerDocument(...).link` (Task 10).
- Produces:
  - `<NameMatchDialog theme projectName conflict busy onChoose onCancel />`. `onChoose` receives `{ asVersionOf }` or `{ keepBoth: true }`.
  - `useProjectFiling({ fileDoc, onFiled, onError })` → `{ file(target), prompt(target, conflict), busy, dialog }`. `target` is `{ projectId, projectName, docId, label }`. `dialog` is null or the props for `NameMatchDialog` (minus `theme`).

- [ ] **Step 1: Write the failing tests**

`src/components/projects/NameMatchDialog.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import NameMatchDialog from './NameMatchDialog';

const theme = { bg: '', border: '', textSecondary: '' };
const doc = { id: 'x', name: 'report.pdf', version: 3 };

describe('NameMatchDialog', () => {
  it('offers a new version, keep both, or cancel for a name match', () => {
    const onChoose = vi.fn();
    const onCancel = vi.fn();
    render(<NameMatchDialog theme={theme} projectName="Q3" conflict={{ error: 'name_exists', document: doc }} onChoose={onChoose} onCancel={onCancel} />);
    expect(screen.getByRole('dialog').textContent).toContain('A document called report.pdf is already in Q3 (v3).');
    fireEvent.click(screen.getByRole('button', { name: 'Add as new version' }));
    expect(onChoose).toHaveBeenCalledWith({ asVersionOf: 'x' });
    fireEvent.click(screen.getByRole('button', { name: 'Keep both' }));
    expect(onChoose).toHaveBeenCalledWith({ keepBoth: true });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('offers a restore for an older version', () => {
    const onChoose = vi.fn();
    render(<NameMatchDialog theme={theme} projectName="Q3" conflict={{ error: 'older_version', document: doc, version: 1 }} onChoose={onChoose} onCancel={vi.fn()} />);
    expect(screen.getByRole('dialog').textContent).toContain('This file is an older version (v1) of report.pdf in Q3.');
    fireEvent.click(screen.getByRole('button', { name: 'Restore v1 as current' }));
    expect(onChoose).toHaveBeenCalledWith({ asVersionOf: 'x' });
  });

  it('Escape cancels', () => {
    const onCancel = vi.fn();
    render(<NameMatchDialog theme={theme} projectName="Q3" conflict={{ error: 'name_exists', document: doc }} onChoose={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onCancel).toHaveBeenCalled();
  });
});
```

`src/hooks/useProjectFiling.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useProjectFiling } from './useProjectFiling';

const target = { projectId: 'p1', projectName: 'Q3', docId: 'd1', label: 'a.pdf' };
const conflict = { error: 'name_exists', document: { id: 'x', name: 'a.pdf', version: 1 } };

describe('useProjectFiling', () => {
  it('files straight through when there is no conflict', async () => {
    const fileDoc = vi.fn(async () => ({ filed: { document_id: 'n', version: 1 } }));
    const onFiled = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled, onError: vi.fn() }));
    await act(() => result.current.file(target));
    expect(fileDoc).toHaveBeenCalledWith('p1', 'd1');
    expect(onFiled).toHaveBeenCalledWith({ document_id: 'n', version: 1 }, target);
    expect(result.current.dialog).toBeNull();
  });

  it('opens the dialog on a conflict and retries with the choice', async () => {
    const fileDoc = vi.fn()
      .mockResolvedValueOnce({ conflict })
      .mockResolvedValueOnce({ filed: { document_id: 'x', version: 2 } });
    const onFiled = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled, onError: vi.fn() }));
    await act(() => result.current.file(target));
    expect(result.current.dialog.conflict).toBe(conflict);
    expect(result.current.dialog.projectName).toBe('Q3');
    await act(() => result.current.dialog.onChoose({ asVersionOf: 'x' }));
    expect(fileDoc).toHaveBeenLastCalledWith('p1', 'd1', { asVersionOf: 'x' });
    expect(onFiled).toHaveBeenCalled();
    expect(result.current.dialog).toBeNull();
  });

  it('cancel files nothing and shows no error', async () => {
    const fileDoc = vi.fn(async () => ({ conflict }));
    const onFiled = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled, onError }));
    await act(() => result.current.file(target));
    act(() => result.current.dialog.onCancel());
    expect(result.current.dialog).toBeNull();
    expect(fileDoc).toHaveBeenCalledTimes(1);
    expect(onFiled).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('prompt opens the dialog for a conflict found elsewhere (the reader upload)', () => {
    const { result } = renderHook(() => useProjectFiling({ fileDoc: vi.fn(), onFiled: vi.fn(), onError: vi.fn() }));
    act(() => result.current.prompt(target, conflict));
    expect(result.current.dialog.conflict).toBe(conflict);
  });

  it('reports other refusals through onError and closes', async () => {
    const fileDoc = vi.fn(async () => { throw new Error('Only Contributors, Maintainers or Owners can do that.'); });
    const onError = vi.fn();
    const { result } = renderHook(() => useProjectFiling({ fileDoc, onFiled: vi.fn(), onError }));
    await act(() => result.current.file(target));
    expect(onError).toHaveBeenCalledWith('Only Contributors, Maintainers or Owners can do that.', target);
    expect(result.current.dialog).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/components/projects/NameMatchDialog.test.jsx src/hooks/useProjectFiling.test.jsx`
Expected: FAIL with "Failed to resolve import".

- [ ] **Step 3: Implement the dialog**

`src/components/projects/NameMatchDialog.jsx`:

```jsx
import { useEffect, useRef } from 'react';

/**
 * Filing a file a project already knows (V1 spec §4.2, §6): by name
 * (`name_exists`) or by its exact bytes (`older_version`). One in-app dialog
 * for all three filing paths — never window.confirm. The choice is what the
 * caller retries with: { asVersionOf } or { keepBoth: true }.
 */
export default function NameMatchDialog({ theme, projectName, conflict, busy = false, onChoose, onCancel }) {
  const doc = conflict.document || {};
  const older = conflict.error === 'older_version';
  const first = useRef(null);
  useEffect(() => { first.current?.focus(); }, []);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Already in this project"
        onKeyDown={(e) => { if (e.key === 'Escape') onCancel(); }}
        className={`w-full max-w-sm flex flex-col gap-3 p-4 rounded-lg border ${theme.border} ${theme.bg}`}
      >
        <p className="text-sm">
          {older
            ? `This file is an older version (v${conflict.version}) of ${doc.name} in ${projectName}.`
            : `A document called ${doc.name} is already in ${projectName} (v${doc.version}).`}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          <button ref={first} disabled={busy} onClick={() => onChoose({ asVersionOf: doc.id })}
            className="px-3 py-1 text-xs rounded bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50">
            {older ? `Restore v${conflict.version} as current` : 'Add as new version'}
          </button>
          <button disabled={busy} onClick={() => onChoose({ keepBoth: true })}
            className={`px-3 py-1 text-xs rounded border ${theme.border} disabled:opacity-50`}>
            Keep both
          </button>
          <button disabled={busy} onClick={onCancel} className={`px-3 py-1 text-xs underline ${theme.textSecondary}`}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement the hook**

`src/hooks/useProjectFiling.js`:

```js
import { useCallback, useState } from 'react';

/**
 * Filing a library document into a project, with the name-match prompt (V1
 * spec §4.2, §6). `file(target)` files it; a name_exists / older_version
 * conflict opens the dialog, and the person's choice retries with
 * as_version_of or keep_both. `prompt(target, conflict)` opens the dialog
 * for a conflict found elsewhere (the reader's upload-then-link). Cancel
 * files nothing and reports nothing: the document stays in the library.
 * `dialog` is null, or NameMatchDialog's props without `theme`.
 * target: { projectId, projectName, docId, label }.
 */
export function useProjectFiling({ fileDoc, onFiled, onError }) {
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);

  const attempt = useCallback(async (target, choice) => {
    setBusy(true);
    try {
      const out = choice
        ? await fileDoc(target.projectId, target.docId, choice)
        : await fileDoc(target.projectId, target.docId);
      if (out?.conflict) {
        setPending({ target, conflict: out.conflict });
        return out;
      }
      setPending(null);
      onFiled?.(out?.filed ?? null, target);
      return out;
    } catch (e) {
      setPending(null);
      onError?.(e.message, target);
      return { error: e.message };
    } finally {
      setBusy(false);
    }
  }, [fileDoc, onFiled, onError]);

  const file = useCallback((target) => attempt(target), [attempt]);
  const prompt = useCallback((target, conflict) => setPending({ target, conflict }), []);

  const dialog = pending ? {
    projectName: pending.target.projectName,
    conflict: pending.conflict,
    busy,
    onChoose: (choice) => attempt(pending.target, choice),
    onCancel: () => setPending(null),
  } : null;

  return { file, prompt, busy, dialog };
}
```

- [ ] **Step 5: Wire the reader's two filing paths in `src/App.jsx`**

Imports:

```js
import NameMatchDialog from './components/projects/NameMatchDialog';
import { useProjectFiling } from './hooks/useProjectFiling';
import { projectsApi } from './lib/projectsApi';
```

Skip `projectsApi` if `App.jsx` already imports it: check with grep first. After the `projects` state and `apiHost`/`apiPort` are defined (near line 230), add:

```js
  // V1 §4.2: filing from the reader can hit a name or bytes the project already has.
  const projectApi = useMemo(() => projectsApi(apiHost, apiPort), [apiHost, apiPort]);
  const projectFiling = useProjectFiling({
    fileDoc: projectApi.fileDoc,
    onFiled: (_filed, t) => showToast(`Filed ${t.label} into ${t.projectName}.`, 3000),
    onError: (message) => showToast(`Could not file into the project: ${message}`, 5000),
  });
  const askAboutFiling = useCallback((projectId, docId, label, conflict) => {
    const projectName = (projects || []).find((p) => p.id === projectId)?.name || 'the project';
    projectFiling.prompt({ projectId, projectName, docId, label }, conflict);
  }, [projects, projectFiling]);
```

`showToast` must be defined above this point. If it's declared later, place this block after it.

In `handleIndexDocument`, right after `const serverId = result.docId;`:

```js
    if (result.link?.conflict) askAboutFiling(docProjectId, serverId, pdfFileName, result.link.conflict);
```

Add `askAboutFiling` to that callback's dependency array.

In `handleConvertDocument`, replace the fire-and-forget link:

```js
    // Now that the kick-off succeeded, link the project (fail-soft — see linkDocToProject).
    if (docProjectId) {
      linkDocToProject({ apiHost, apiPort, projectId: docProjectId, docId: convertDocId })
        .then((out) => { if (out?.conflict) askAboutFiling(docProjectId, convertDocId, pdfFileName, out.conflict); });
    }
```

Add `askAboutFiling` to its dependency array.

Next to `<DoclingConvertDialog …/>`, render:

```jsx
      {projectFiling.dialog && <NameMatchDialog theme={theme} {...projectFiling.dialog} />}
```

- [ ] **Step 6: Run the tests and lint**

Run: `npx vitest run src/components/projects/NameMatchDialog.test.jsx src/hooks/useProjectFiling.test.jsx && npm run lint`
Expected: PASS. Lint clean.

Run: `npx vitest run`
Expected: all pass, apart from the ProjectDocsTab tests that reference `api.projectDocs`/`unfileDoc`, which Task 12 rewrites. If those fail here, say so in the report and continue: Task 12 owns them.

- [ ] **Step 7: Ask, then commit**

```bash
git add src/components/projects/NameMatchDialog.jsx src/components/projects/NameMatchDialog.test.jsx \
  src/hooks/useProjectFiling.js src/hooks/useProjectFiling.test.jsx src/App.jsx
git commit -m "feat(versions-ui): the name-match dialog and its hook; the reader's upload-into-project asks before duplicating"
```

---

### Task 12: The project Documents tab: history, upload, restore, delete, merge, remove

**Files:**
- Rewrite: `src/components/projects/ProjectDocsTab.jsx`
- Create: `src/components/projects/ProjectDocRow.jsx`, `src/components/projects/VersionHistory.jsx`
- Rewrite: `src/components/projects/ProjectDocsTab.test.jsx`

**Interfaces:**
- Consumes: the Task 10 API (`documents`, `versions`, `uploadVersion`, `versionFromLibrary`, `restoreVersion`, `deleteVersion`, `mergeDocuments`, `removeDocument`, `fileDoc`, `myDocs`) and the Task 11 dialog and hook.
- Produces: the same component props as today, `{ theme, api, project, showToast, onOpenDoc, onRefused, onChanged }`. `onOpenDoc` receives `{ doc_id, file_name }`.

- [ ] **Step 1: Write the failing tests**

Replace `src/components/projects/ProjectDocsTab.test.jsx` with:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import ProjectDocsTab from './ProjectDocsTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: false,
  remove_docs: false, delete: false, leave: true, ...over });
const project = (over = {}) => ({ id: 'p1', name: 'Q3', my_role: 'contributor', can: can({ file_docs: true }), ...over });
const maintainer = () => project({ my_role: 'maintainer', can: can({ file_docs: true, remove_docs: true }) });
const report = { id: 'x', name: 'report.pdf', version: 2, versions_count: 2, doc_id: 'h2', state: 'indexed' };
const final = { id: 'y', name: 'report-final.pdf', version: 1, versions_count: 1, doc_id: 'h3', state: 'indexed' };
const history = [
  { version: 2, doc_id: 'h2', file_name: 'report.pdf', note: 'fixed table 4', origin: 'upload', origin_version: null, is_current: true, added_by: { id: 'u', name: 'Bob' }, added_at: '2026-10-11T00:00:00Z', state: 'indexed' },
  { version: 1, doc_id: 'h1', file_name: 'report.pdf', note: null, origin: 'upload', origin_version: null, is_current: false, added_by: { id: 'u2', name: 'Alice' }, added_at: '2026-09-28T00:00:00Z', state: 'indexed' },
];
const mineFree = { doc_id: 'd2', file_name: 'Mine.pdf', state: 'indexed', projects: [], added_via: 'upload' };

function api(over = {}) {
  return {
    documents: vi.fn(async () => [report, final]),
    versions: vi.fn(async () => history),
    myDocs: vi.fn(async () => [mineFree]),
    fileDoc: vi.fn(async () => ({ filed: { document_id: 'n', version: 1 } })),
    uploadVersion: vi.fn(async () => ({ version: 3, doc_id: 'h9', restored_from: null, state: 'indexing' })),
    versionFromLibrary: vi.fn(async () => ({ version: 3, doc_id: 'd2', restored_from: null, state: 'indexed' })),
    restoreVersion: vi.fn(async () => ({ version: 3, doc_id: 'h1', restored_from: 1, state: 'indexed' })),
    deleteVersion: vi.fn(async () => null),
    mergeDocuments: vi.fn(async () => report),
    removeDocument: vi.fn(async () => null),
    ...over,
  };
}

function mount(p, a = api()) {
  const props = { theme, api: a, project: p, showToast: vi.fn(), onRefused: vi.fn(), onChanged: vi.fn(), onOpenDoc: vi.fn() };
  render(<ProjectDocsTab {...props} />);
  return props;
}

describe('ProjectDocsTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists one row per document with its version count', async () => {
    mount(project());
    expect(await screen.findByText('report.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'History of report.pdf' }).textContent).toBe('v2 · 2 versions');
    expect(screen.getByRole('button', { name: 'History of report-final.pdf' }).textContent).toBe('v1 · 1 version');
  });

  it('shows a failed load inline with Retry', async () => {
    const a = api({ documents: vi.fn().mockRejectedValueOnce(new Error('Could not load documents.')).mockResolvedValue([report]) });
    mount(project(), a);
    expect((await screen.findByRole('alert')).textContent).toContain('Could not load documents.');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('report.pdf')).toBeTruthy();
  });

  it('expands the history newest first; old lines offer Open, Restore and Delete by role', async () => {
    const props = mount(maintainer());
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    const v1 = await screen.findByTestId('version-1');
    const v2 = screen.getByTestId('version-2');
    expect(v2.textContent).toContain('current');
    expect(v2.textContent).toContain('"fixed table 4"');
    expect(within(v2).queryByRole('button', { name: 'Restore v2' })).toBeNull();
    fireEvent.click(within(v1).getByRole('button', { name: 'Open v1' }));
    expect(props.onOpenDoc).toHaveBeenCalledWith({ doc_id: 'h1', file_name: 'report.pdf' });
    expect(within(v1).getByRole('button', { name: 'Restore v1' })).toBeTruthy();
    expect(within(v1).getByRole('button', { name: 'Delete v1' })).toBeTruthy();
  });

  it('a Contributor can restore but not delete or merge', async () => {
    const a = api();
    const props = mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    const v1 = await screen.findByTestId('version-1');
    expect(within(v1).queryByRole('button', { name: 'Delete v1' })).toBeNull();
    fireEvent.click(within(v1).getByRole('button', { name: 'Restore v1' }));
    await waitFor(() => expect(a.restoreVersion).toHaveBeenCalledWith('p1', 'x', 1));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Restored v1 as v3.', 3000));
    fireEvent.click(screen.getByRole('button', { name: 'More actions for report.pdf' }));
    expect(screen.queryByRole('menuitem', { name: 'Mark as newer version of…' })).toBeNull();
  });

  it('deletes an old version after an inline confirmation', async () => {
    const a = api();
    mount(maintainer(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'History of report.pdf' }));
    fireEvent.click(within(await screen.findByTestId('version-1')).getByRole('button', { name: 'Delete v1' }));
    expect(screen.getByText(/Delete v1 for good\?/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(a.deleteVersion).toHaveBeenCalledWith('p1', 'x', 1));
  });

  it('uploads a new version from a file with a note', async () => {
    const a = api();
    const props = mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload new version' }));
    const file = new File(['x'], 'report v3.pdf');
    fireEvent.change(screen.getByLabelText('Choose a file for the new version of report.pdf'), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText('What changed?'), { target: { value: 'new numbers' } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(a.uploadVersion).toHaveBeenCalledWith('p1', 'x', file, 'new numbers'));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('report v3.pdf is now v3 (indexing…).', 3000));
  });

  it('uploads a new version from my library', async () => {
    const a = api();
    mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload new version' }));
    fireEvent.change(screen.getByLabelText('New version from my library'), { target: { value: 'd2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(a.versionFromLibrary).toHaveBeenCalledWith('p1', 'x', 'd2', ''));
  });

  it('says so when an uploaded file matched an older version', async () => {
    const a = api({ uploadVersion: vi.fn(async () => ({ version: 3, doc_id: 'h1', restored_from: 1, state: 'indexed' })) });
    const props = mount(project(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload new version' }));
    fireEvent.change(screen.getByLabelText('Choose a file for the new version of report.pdf'), { target: { files: [new File(['x'], 'old.pdf')] } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('That file matched v1, so it was restored as v3.', 3000));
  });

  it('merges after a confirmation that names the result', async () => {
    const a = api();
    mount(maintainer(), a);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions for report-final.pdf' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mark as newer version of…' }));
    fireEvent.change(screen.getByLabelText('Mark report-final.pdf as a newer version of'), { target: { value: 'x' } });
    expect(screen.getByText(/report-final\.pdf becomes v3 of report\.pdf, and the row will be called report-final\.pdf\. This can't be undone/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm merge' }));
    await waitFor(() => expect(a.mergeDocuments).toHaveBeenCalledWith('p1', 'x', 'y'));
  });

  it('asks before removing, naming the versions, then removes', async () => {
    const a = api();
    mount(maintainer(), a);
    fireEvent.click(await screen.findByLabelText('Remove report.pdf from Q3'));
    expect(screen.getByText(/Remove report\.pdf and its 2 versions from Q3\? If nobody else has it in their library, it is deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(a.removeDocument).toHaveBeenCalledWith('p1', 'x'));
  });

  it('files one of my uploads through the name-match flow', async () => {
    const conflict = { error: 'name_exists', document: { id: 'x', name: 'report.pdf', version: 2 } };
    const a = api({ fileDoc: vi.fn().mockResolvedValueOnce({ conflict }).mockResolvedValueOnce({ filed: { document_id: 'x', version: 3 } }) });
    mount(project(), a);
    const select = await screen.findByLabelText('File a document into this project');
    fireEvent.change(select, { target: { value: 'd2' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Add as new version' }));
    await waitFor(() => expect(a.fileDoc).toHaveBeenLastCalledWith('p1', 'd2', { asVersionOf: 'x' }));
  });

  it('has no file, upload or remove controls for a Reader', async () => {
    mount(project({ my_role: 'reader', can: can() }));
    await screen.findByText('report.pdf');
    expect(screen.queryByLabelText('File a document into this project')).toBeNull();
    expect(screen.queryByRole('button', { name: 'More actions for report.pdf' })).toBeNull();
    expect(screen.queryByLabelText('Remove report.pdf from Q3')).toBeNull();
  });

  it('shows the notice and asks the page to reload when the server refuses', async () => {
    const a = api({ removeDocument: vi.fn(async () => { throw new Error('Only Maintainers or Owners can do that.'); }) });
    const props = mount(maintainer(), a);
    fireEvent.click(await screen.findByLabelText('Remove report.pdf from Q3'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Only Maintainers or Owners can do that.', 5000));
    expect(props.onRefused).toHaveBeenCalled();
  });

  it('admin non-member sees the members-only note instead of documents', async () => {
    const a = api();
    mount(project({ my_role: null, can: can() }), a);
    expect(await screen.findByText("Only members can see this project's documents.")).toBeTruthy();
    expect(a.documents).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/components/projects/ProjectDocsTab.test.jsx`
Expected: FAIL. `api.documents` is never called, and there's no "History of" button.

- [ ] **Step 3: Write `VersionHistory.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { BookOpen } from 'lucide-react';

const originLabel = (v) => (v.origin === 'restore' ? `restored from v${v.origin_version}`
  : v.origin === 'merge' ? `merged (was v${v.origin_version})` : null);

/**
 * A document's versions, newest first (V1 spec §6). Every line opens in the
 * reader; an old line offers Restore (Contributor+) and Delete (Maintainer+,
 * after an inline confirmation). `reloadKey` changes after an action on the
 * row; `run(fn, ok)` is the tab's one-action-at-a-time runner.
 */
export default function VersionHistory({ theme, api, project, doc, reloadKey, busy, run, onOpenDoc }) {
  const { can } = project;
  const [versions, setVersions] = useState(null);
  const [error, setError] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    api.versions(project.id, doc.id)
      .then((v) => { if (live) { setVersions(v); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [api, project.id, doc.id, reloadKey, tick]);

  const act = async (fn, ok) => {
    if (await run(fn, ok)) {
      setConfirm(null);
      setTick((t) => t + 1);
    }
  };

  if (error) return <p role="alert" className="text-[10px] text-red-500">{error}</p>;
  if (versions === null) return <p className={`text-[10px] ${theme.textMuted}`}>Loading history…</p>;
  return (
    <ul className="flex flex-col gap-1 pl-2 border-l border-dashed">
      {versions.map((v) => (
        <li key={v.version} data-testid={`version-${v.version}`} className="flex flex-col gap-0.5 text-[10px]">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-bold">v{v.version}</span>
            {v.is_current && <span className="px-1 rounded bg-green-500/10 text-green-600">current</span>}
            <span className="truncate max-w-[12rem]">{v.file_name}</span>
            <span className={theme.textSecondary}>
              {v.added_by?.name || 'a former member'} · {new Date(v.added_at).toLocaleDateString()}
            </span>
            {v.note && <span className={theme.textSecondary}>&quot;{v.note}&quot;</span>}
            {originLabel(v) && <span className={theme.textMuted}>{originLabel(v)}</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => onOpenDoc?.({ doc_id: v.doc_id, file_name: v.file_name })}
              aria-label={`Open v${v.version}`} className="flex items-center gap-1 underline">
              <BookOpen size={10} /> Open
            </button>
            {!v.is_current && can.file_docs && (
              <button disabled={busy} aria-label={`Restore v${v.version}`} className="underline disabled:opacity-50"
                onClick={() => act(() => api.restoreVersion(project.id, doc.id, v.version),
                  (out) => `Restored v${v.version} as v${out.version}.`)}>
                Restore
              </button>
            )}
            {!v.is_current && can.remove_docs && (confirm === v.version ? (
              <span className="flex flex-wrap items-center gap-2">
                <span className={theme.textSecondary}>Delete v{v.version} for good? Its file goes if nothing else uses it.</span>
                <button className="underline text-red-500"
                  onClick={() => act(() => api.deleteVersion(project.id, doc.id, v.version), `Deleted v${v.version}.`)}>
                  Confirm delete
                </button>
                <button className="underline" onClick={() => setConfirm(null)}>Cancel</button>
              </span>
            ) : (
              <button disabled={busy} aria-label={`Delete v${v.version}`} className="underline text-red-500 disabled:opacity-50"
                onClick={() => setConfirm(v.version)}>
                Delete
              </button>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Write `ProjectDocRow.jsx`**

```jsx
import { useState } from 'react';
import { BookOpen, ChevronDown, ChevronRight, MoreHorizontal, X } from 'lucide-react';
import VersionHistory from './VersionHistory';

const count = (n) => (n === 1 ? '1 version' : `${n} versions`);

/**
 * One document in a project (V1 spec §6): its current version, a version
 * count that expands into the history, and a ⋯ menu with Upload new version
 * (Contributor+) and Mark as newer version of… (Maintainer+). × removes the
 * document with its history (Maintainer+), after a confirmation.
 */
export default function ProjectDocRow({ theme, api, project, doc, others, mine, busy, run, onOpenDoc }) {
  const { can } = project;
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const [panel, setPanel] = useState(null); // 'upload' | 'merge' | 'remove'
  const [file, setFile] = useState(null);
  const [fromLibrary, setFromLibrary] = useState('');
  const [note, setNote] = useState('');
  const [mergeInto, setMergeInto] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  const close = () => { setPanel(null); setFile(null); setFromLibrary(''); setNote(''); setMergeInto(''); };
  const after = (ok) => { if (ok) { close(); setReloadKey((k) => k + 1); } };
  const choose = (p) => { setMenu(false); setPanel(p); };
  const libraryChoices = mine.filter((m) => m.doc_id !== doc.doc_id);
  const target = others.find((o) => o.id === mergeInto);

  const upload = async () => {
    const label = file ? file.name : libraryChoices.find((m) => m.doc_id === fromLibrary)?.file_name || doc.name;
    after(await run(
      () => (file ? api.uploadVersion(project.id, doc.id, file, note) : api.versionFromLibrary(project.id, doc.id, fromLibrary, note)),
      (out) => (out.restored_from
        ? `That file matched v${out.restored_from}, so it was restored as v${out.version}.`
        : `${label} is now v${out.version}${out.state === 'indexed' ? '' : ' (indexing…)'}.`),
    ));
  };

  return (
    <div className={`flex flex-col gap-1 p-2 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-bold truncate">{doc.name}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}>{doc.state}</span>
          <button onClick={() => setOpen(!open)} aria-label={`History of ${doc.name}`} aria-expanded={open}
            className={`flex items-center text-[10px] ${theme.textSecondary} hover:text-blue-500`}>
            {open ? <ChevronDown size={10} /> : <ChevronRight size={10} />}v{doc.version} · {count(doc.versions_count)}
          </button>
        </span>
        <span className="relative flex items-center gap-2 shrink-0">
          {onOpenDoc && (
            <button onClick={() => onOpenDoc({ doc_id: doc.doc_id, file_name: doc.name })} aria-label={`Open ${doc.name}`}
              className="flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-blue-500 text-white hover:bg-blue-600">
              <BookOpen size={10} /> Open
            </button>
          )}
          {can.file_docs && (
            <button onClick={() => setMenu(!menu)} aria-label={`More actions for ${doc.name}`} aria-haspopup="menu"
              disabled={busy} className={`hover:text-blue-500 disabled:opacity-50 ${theme.textSecondary}`}>
              <MoreHorizontal size={12} />
            </button>
          )}
          {menu && (
            <span role="menu" className={`absolute right-0 top-5 z-10 flex flex-col min-w-[12rem] p-1 rounded border shadow ${theme.border} ${theme.bg}`}>
              <button role="menuitem" onClick={() => choose('upload')} className="text-left text-[11px] px-2 py-1 hover:text-blue-500">Upload new version</button>
              {can.remove_docs && others.length > 0 && (
                <button role="menuitem" onClick={() => choose('merge')} className="text-left text-[11px] px-2 py-1 hover:text-blue-500">Mark as newer version of…</button>
              )}
            </span>
          )}
          {can.remove_docs && (
            <button onClick={() => setPanel('remove')} disabled={busy} aria-label={`Remove ${doc.name} from ${project.name}`}
              className={`hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
              <X size={12} />
            </button>
          )}
        </span>
      </div>

      {panel === 'upload' && (
        <div className="flex flex-col gap-1 text-[10px]">
          <div className="flex flex-wrap items-center gap-2">
            <input type="file" accept=".pdf,.txt,.md,.markdown" aria-label={`Choose a file for the new version of ${doc.name}`}
              onChange={(e) => { setFile(e.target.files?.[0] || null); setFromLibrary(''); }} className="max-w-full text-[10px]" />
            {libraryChoices.length > 0 && (
              <select value={fromLibrary} aria-label="New version from my library"
                onChange={(e) => { setFromLibrary(e.target.value); setFile(null); }}
                className={`max-w-[12rem] px-1.5 py-0.5 rounded border ${theme.border} ${theme.bg}`}>
                <option value="">…or from my library</option>
                {libraryChoices.map((m) => <option key={m.doc_id} value={m.doc_id}>{m.file_name}</option>)}
              </select>
            )}
          </div>
          <input type="text" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="What changed? (optional)" aria-label="What changed?"
            className={`w-full px-2 py-1 rounded border ${theme.border} ${theme.bg}`} />
          <span className="flex gap-2">
            <button disabled={busy || (!file && !fromLibrary)} onClick={upload}
              className="px-2 py-0.5 rounded bg-blue-500 text-white disabled:opacity-50">Upload</button>
            <button onClick={close} className="underline">Cancel</button>
          </span>
        </div>
      )}

      {panel === 'merge' && (
        <div className="flex flex-col gap-1 text-[10px]">
          <select value={mergeInto} onChange={(e) => setMergeInto(e.target.value)}
            aria-label={`Mark ${doc.name} as a newer version of`}
            className={`self-start max-w-full px-1.5 py-0.5 rounded border ${theme.border} ${theme.bg}`}>
            <option value="">Pick the document it replaces…</option>
            {others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          {target && (
            <>
              <span className={theme.textSecondary}>
                {doc.name} becomes v{target.version + doc.versions_count} of {target.name}, and the row will be called {doc.name}.
                {' '}This can&apos;t be undone; every file stays in the history.
              </span>
              <span className="flex gap-2">
                <button disabled={busy} className="underline text-red-500"
                  onClick={async () => after(await run(() => api.mergeDocuments(project.id, target.id, doc.id), `Merged ${doc.name} into ${target.name}.`))}>
                  Confirm merge
                </button>
                <button onClick={close} className="underline">Cancel</button>
              </span>
            </>
          )}
        </div>
      )}

      {panel === 'remove' && (
        <div className="flex flex-wrap items-center gap-2 text-[10px]">
          <span className={theme.textSecondary}>
            {doc.versions_count > 1
              ? `Remove ${doc.name} and its ${doc.versions_count} versions from ${project.name}?`
              : `Remove ${doc.name} from ${project.name}?`}
            {' '}If nobody else has it in their library, it is deleted.
          </span>
          <button className="underline text-red-500"
            onClick={async () => after(await run(() => api.removeDocument(project.id, doc.id), `Removed ${doc.name}.`))}>
            Confirm remove
          </button>
          <button onClick={close} className="underline">Cancel</button>
        </div>
      )}

      {open && (
        <VersionHistory theme={theme} api={api} project={project} doc={doc} reloadKey={reloadKey}
          busy={busy} run={run} onOpenDoc={onOpenDoc} />
      )}
    </div>
  );
}
```

- [ ] **Step 5: Rewrite `ProjectDocsTab.jsx`**

```jsx
import { useCallback, useEffect, useState } from 'react';
import ProjectDocRow from './ProjectDocRow';
import NameMatchDialog from './NameMatchDialog';
import { useProjectFiling } from '../../hooks/useProjectFiling';

/**
 * A project's documents (A0 §10, V1 §6): one row per document — its current
 * version — with the history underneath. Contributors file their own uploads
 * in, upload new versions and restore old ones; Maintainers and Owners also
 * merge documents, delete old versions and remove documents, after a
 * confirmation. Filing a name or file the project already has asks first.
 * An admin who isn't a member sees counts only (A0 §5), so this says so.
 */
export default function ProjectDocsTab({ theme, api, project, showToast, onOpenDoc, onRefused, onChanged }) {
  const [docs, setDocs] = useState(null);
  const [error, setError] = useState(null);
  const [mine, setMine] = useState([]);
  const [busy, setBusy] = useState(false);
  const member = project.my_role !== null;
  const { can } = project;

  const load = useCallback(async () => {
    if (!member) return;
    try {
      const [inProject, all] = await Promise.all([
        api.documents(project.id),
        can.file_docs ? api.myDocs() : Promise.resolve([]),
      ]);
      setDocs(inProject);
      setMine(all.filter((d) => d.added_via === 'upload'));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [api, project.id, can.file_docs, member]);

  useEffect(() => { load(); }, [load]);

  // One action at a time across the tab. Any refusal reloads, because our
  // role may have changed; `ok` is a message or a function of the result.
  const run = useCallback(async (fn, ok) => {
    setBusy(true);
    try {
      const out = await fn();
      const message = typeof ok === 'function' ? ok(out) : ok;
      if (message) showToast(message, 3000);
      onChanged?.();
      return true;
    } catch (e) {
      showToast(e.message, 5000);
      onRefused?.();
      return false;
    } finally {
      setBusy(false);
      await load();
    }
  }, [load, onChanged, onRefused, showToast]);

  const filing = useProjectFiling({
    fileDoc: api.fileDoc,
    onFiled: (_filed, t) => { showToast(`Filed ${t.label} into ${project.name}.`, 3000); onChanged?.(); load(); },
    onError: (message) => { showToast(message, 5000); onRefused?.(); load(); },
  });

  if (!member) {
    return <p className={`text-xs ${theme.textMuted}`}>Only members can see this project&apos;s documents.</p>;
  }
  const unfiled = mine.filter((d) => !(d.projects || []).some((p) => p.id === project.id));
  return (
    <div className="flex flex-col gap-2">
      {can.file_docs && unfiled.length > 0 && (
        <select
          value=""
          onChange={(e) => {
            const d = unfiled.find((x) => x.doc_id === e.target.value);
            if (d) filing.file({ projectId: project.id, projectName: project.name, docId: d.doc_id, label: d.file_name });
          }}
          disabled={busy || filing.busy}
          aria-label="File a document into this project"
          className={`self-start px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
        >
          <option value="">File a document…</option>
          {unfiled.map((d) => <option key={d.doc_id} value={d.doc_id}>{d.file_name}</option>)}
        </select>
      )}
      {error && (
        <div role="alert" className="flex items-center gap-2 text-xs text-red-500">
          <span>{error}</span>
          <button onClick={load} className="underline">Retry</button>
        </div>
      )}
      {docs === null ? (
        error ? null : <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : docs.length === 0 ? (
        <p className={`text-xs ${theme.textMuted}`}>No documents in this project yet.</p>
      ) : docs.map((d) => (
        <ProjectDocRow key={d.id} theme={theme} api={api} project={project} doc={d}
          others={docs.filter((o) => o.id !== d.id)} mine={mine} busy={busy} run={run} onOpenDoc={onOpenDoc} />
      ))}
      {filing.dialog && <NameMatchDialog theme={theme} {...filing.dialog} />}
    </div>
  );
}
```

Check `ProjectPage.jsx`'s `onOpenDoc` wiring: it must accept `{ doc_id, file_name }`, which is the same shape the Library's `openDoc` already takes. If `ProjectPage.test.jsx` mocks `projectDocs`, rename that mock to `documents`.

- [ ] **Step 6: Run the tests and lint**

Run: `npx vitest run src/components/projects && npm run lint`
Expected: PASS. Lint clean.

- [ ] **Step 7: Ask, then commit**

```bash
git add src/components/projects/ProjectDocsTab.jsx src/components/projects/ProjectDocRow.jsx \
  src/components/projects/VersionHistory.jsx src/components/projects/ProjectDocsTab.test.jsx \
  src/components/projects/ProjectPage.test.jsx
git commit -m "feat(versions-ui): Documents tab — history, upload new version, restore, delete, merge and remove with confirmations"
```

---

### Task 13: Library: version chips, the chip ×, "newer version" lines, filing through the dialog

**Files:**
- Modify: `src/components/library/LibraryPage.jsx`
- Modify: `src/components/library/LibraryPage.test.jsx`

**Interfaces:**
- Consumes:
  - `GET /v1/docs` rows with `projects: [{id, name, document_id, version, versions_count}]` and `newer_in` (Task 3);
  - `api.fileDoc` (Task 10);
  - the dialog and hook (Task 11).
- Produces: no new exports.

This task closes a gap the spec left open. Today the chip × removes a document in one click, and after V1 that click would wipe its whole history. So the × now asks first when `versions_count > 1`, using the same rule and wording as the Documents tab. A single-version document still removes in one click, as today. Task 15 records this in the spec.

- [ ] **Step 1: Update the fixtures and write the failing tests**

In `src/components/library/LibraryPage.test.jsx`:

1. Change `docs[1].projects` (row d2) to:

   ```js
   projects: [{ id: 'p1', name: 'Project A', document_id: 'pa-2', version: 1, versions_count: 1 },
              { id: 'p2', name: 'Project B', document_id: 'pb-2', version: 1, versions_count: 1 }],
   ```

   and `docs[2].projects` (row d3) to `[{ id: 'p1', name: 'Project A', document_id: 'pa-3', version: 1, versions_count: 1 }]`.
2. In the tests "DELETEs the link and reloads…", "guards against a double-click…", "reloads projects after a refused unlink…" and "shows the describeRefusal notice…", replace every `'/v1/projects/p1/docs/d2'` with `'/v1/projects/p1/documents/pa-2'`.

Then append these tests:

```js
  it('shows each chip with the version the project is on, and the chip opens the project', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getAllByText('· v1')).toHaveLength(2);
    fireEvent.click(within(row).getByRole('button', { name: 'Open project Project A' }));
    await waitFor(() => expect(apiFetch.mock.calls.some(([, , path]) => path === '/v1/projects/p1')).toBe(true));
  });

  it('asks before × removes a document that has several versions', async () => {
    const versioned = docs.map((d) => (d.doc_id === 'd2'
      ? { ...d, projects: [{ id: 'p1', name: 'Project A', document_id: 'pa-2', version: 3, versions_count: 3 }] } : d));
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path.startsWith('/v1/docs')) return json(200, versioned);
      if (path === '/v1/projects') return json(200, projects);
      if (opts?.method === 'DELETE') return json(204, {});
      return json(404, {});
    });
    render(<LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    fireEvent.click(within(row).getByLabelText(/remove teammate\.pdf from project a/i));
    expect(within(row).getByText('Remove Teammate.pdf and its 3 versions from Project A?')).toBeInTheDocument();
    expect(apiFetch.mock.calls.some(([, , , o]) => o?.method === 'DELETE')).toBe(false);
    fireEvent.click(within(row).getByRole('button', { name: 'Confirm remove from Project A' }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p1/documents/pa-2', { method: 'DELETE' }));
  });

  it('says when a project has a newer version of my copy and opens the current one', async () => {
    const onOpen = vi.fn(async () => {});
    const behind = docs.map((d) => (d.doc_id === 'd1' ? { ...d, newer_in: [{
      project: { id: 'p2', name: 'Project B' }, document_id: 'pb-9', version: 1, current_version: 3,
      current_doc_id: 'h3', current_name: 'Owned v3.pdf', is_current: false }] } : d));
    apiFetch.mockImplementation(async (host, port, path) => {
      if (path.startsWith('/v1/docs')) return json(200, behind);
      if (path === '/v1/projects') return json(200, projects);
      return json(404, {});
    });
    render(<LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} onOpen={onOpen} />);
    const row = await screen.findByTestId('doc-row-d1');
    expect(within(row).getByText(/Project B has a newer version \(v3\)/)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: 'Open current version from Project B' }));
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith({ doc_id: 'h3', file_name: 'Owned v3.pdf' }));
  });

  it('files through the name-match dialog when the project already has the name', async () => {
    const detail = { error: 'name_exists', message: 'm', document: { id: 'pb-1', name: 'Owned.pdf', version: 1 } };
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path.startsWith('/v1/docs')) return json(200, docs);
      if (path === '/v1/projects') return json(200, projects);
      if (path === '/v1/projects/p2/docs/d1' && opts?.method === 'PUT') return { ok: false, status: 409, json: async () => ({ detail }) };
      if (path === '/v1/projects/p2/docs/d1?keep_both=true') return json(201, { document_id: 'n', version: 1 });
      return json(404, {});
    });
    render(<LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    const row = await screen.findByTestId('doc-row-d1');
    fireEvent.change(within(row).getByLabelText(/add owned\.pdf to project/i), { target: { value: 'p2' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Keep both' }));
    await waitFor(() => expect(apiFetch.mock.calls.some(([, , path]) => path === '/v1/projects/p2/docs/d1?keep_both=true')).toBe(true));
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/components/library/LibraryPage.test.jsx`
Expected: FAIL. DELETE goes to `/docs/d2`, there's no "· v1", no "Open project" button and no dialog.

- [ ] **Step 3: Implement in `LibraryPage.jsx`**

Imports: add `import NameMatchDialog from '../projects/NameMatchDialog';` and `import { useProjectFiling } from '../../hooks/useProjectFiling';`.

Replace `ProjectChips` with:

```jsx
// One chip per project where this content is the CURRENT version (V1 §5.2),
// with the version that project is on; the name opens the project. Projects
// govern their documents (A0 §5): × where the caller may remove documents —
// it removes the document with its history, so several versions are named
// first (V1 §6); "+ project" offers only projects the caller may file into.
function ProjectChips({ doc, projects, theme, onLink, onUnlink, onOpenProject, busy }) {
  const [confirm, setConfirm] = useState(null);
  const linked = doc.projects || [];
  const linkedIds = new Set(linked.map((p) => p.id));
  const removableIds = new Set((projects || []).filter((p) => p.can?.remove_docs).map((p) => p.id));
  const addable = (projects || []).filter((p) => p.can?.file_docs && !linkedIds.has(p.id));
  const remove = (p) => { setConfirm(null); onUnlink(doc, p); };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1">
        <FolderOpen size={10} className={theme.textSecondary} />
        {linked.length === 0 && <span className={theme.textSecondary}>No project</span>}
        {linked.map((p) => (
          <span key={p.id} className={`flex items-center gap-1 px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}>
            <button onClick={() => onOpenProject?.(p.id)} aria-label={`Open project ${p.name}`} className="hover:text-blue-500">
              {p.name}
            </button>
            {p.version != null && <span>· v{p.version}</span>}
            {removableIds.has(p.id) && (
              <button
                onClick={() => ((p.versions_count || 1) > 1 ? setConfirm(p.id) : remove(p))}
                aria-label={`Remove ${doc.file_name} from ${p.name}`}
                disabled={busy}
                className="hover:text-red-500 disabled:opacity-50 disabled:cursor-default"
              >
                <X size={10} />
              </button>
            )}
          </span>
        ))}
        {doc.added_via === 'upload' && addable.length > 0 && (
          <select
            value=""
            onChange={(e) => { if (e.target.value) onLink(doc, e.target.value); }}
            aria-label={`Add ${doc.file_name} to project`}
            disabled={busy}
            className={`max-w-[10rem] px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg}`}
          >
            <option value="">+ project</option>
            {addable.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        )}
      </div>
      {linked.filter((p) => p.id === confirm).map((p) => (
        <span key={p.id} className="flex flex-wrap items-center gap-2">
          <span className={theme.textSecondary}>{`Remove ${doc.file_name} and its ${p.versions_count} versions from ${p.name}?`}</span>
          <button onClick={() => remove(p)} aria-label={`Confirm remove from ${p.name}`} className="underline text-red-500">Confirm remove</button>
          <button onClick={() => setConfirm(null)} className="underline">Cancel</button>
        </span>
      ))}
    </div>
  );
}
```

Replace `changeLink`, `linkDoc` and `unlinkDoc` with:

```js
  // Filing (V1 §4.2): a name or file the project already has asks first.
  const filing = useProjectFiling({
    fileDoc: api.fileDoc,
    onFiled: async () => { await loadDocs(search, projectFilter); },
    onError: async (message) => {
      showToast(`Update failed: ${message}`, 5000);
      await loadProjects(); // our role may have changed; refresh `can` so stale controls go away
    },
  });
  const linkDoc = async (doc, projectId) => {
    const p = (projects || []).find((x) => x.id === projectId);
    setLinkingId(doc.doc_id);
    try {
      await filing.file({ projectId, projectName: p?.name || 'the project', docId: doc.doc_id, label: doc.file_name });
    } finally {
      setLinkingId(null);
    }
  };
  // × removes the project's document — with its history (V1 §5.2).
  const unlinkDoc = async (doc, chip) => {
    setLinkingId(doc.doc_id);
    try {
      const res = await apiFetch(apiHost, apiPort,
        `/v1/projects/${chip.id}/documents/${chip.document_id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await describeRefusal(res));
      await loadDocs(search, projectFilter);
    } catch (e) {
      showToast(`Update failed: ${e.message}`, 5000);
      await loadProjects();
    } finally {
      setLinkingId(null);
    }
  };
```

In the row, pass `onOpenProject={setOpenProjectId}` to `ProjectChips`. Right after the chips `<div className="text-[10px]">…</div>`, add:

```jsx
                  {(doc.newer_in || []).map((n) => (
                    <div key={n.document_id} className={`flex flex-wrap items-center gap-1 text-[10px] ${theme.textMuted}`}>
                      <span>{n.project.name} has a newer version (v{n.current_version})</span>
                      {onOpen && (
                        <button onClick={() => openDoc({ doc_id: n.current_doc_id, file_name: n.current_name })}
                          aria-label={`Open current version from ${n.project.name}`} className="underline font-bold">
                          Open current
                        </button>
                      )}
                    </div>
                  ))}
```

Before the closing tags of the outer `<div>`, render `{filing.dialog && <NameMatchDialog theme={theme} {...filing.dialog} />}`. Add one sentence to the component's doc comment: "A project shows only its current version; a row of mine that a project has moved past says so (V1 §6)."

- [ ] **Step 4: Run the tests and lint**

Run: `npx vitest run src/components/library && npm run lint`
Expected: PASS. Lint clean.

- [ ] **Step 5: Ask, then commit**

```bash
git add src/components/library/LibraryPage.jsx src/components/library/LibraryPage.test.jsx
git commit -m "feat(versions-ui): Library chips show the project's version and open it; × asks before removing a history; newer-version line; filing asks"
```

---

### Task 14: Reader banner, chat label, Activity lines

**Files:**
- Create: `src/lib/versions.js`, `src/lib/versions.test.js`
- Create: `src/hooks/useVersionStatus.js`
- Create: `src/components/OldVersionBanner.jsx`, `src/components/OldVersionBanner.test.jsx`
- Modify: `src/App.jsx` (the hook, the banner, the ChatView prop)
- Modify: `src/components/ChatView.jsx` (`DocUseSwitch` label)
- Modify: `src/components/projects/ActivityTab.jsx`, `src/components/projects/ActivityTab.test.jsx`

**Interfaces:**
- Consumes: `GET /v1/docs/{id}`'s `versions_in` (Task 3).
- Produces:
  - `oldVersionOf(versionsIn) -> item | null` (the same rule as the server's `old_version_of`);
  - `useVersionStatus({ apiHost, apiPort, docId, enabled, refreshKey }) -> item | null`. It refetches when `refreshKey` changes. App passes `projectsVersion`, which the Library bumps after any project change, so a version uploaded on the project page updates the banner for the document already open (spec §7: "and again after a version upload");
  - `<OldVersionBanner old onOpenCurrent />`;
  - `ChatView` prop `openDocVersion: number | null`.

- [ ] **Step 1: Write the failing tests**

`src/lib/versions.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { oldVersionOf } from './versions';

const item = (name, isCurrent) => ({ project: { id: name, name }, document_id: `${name}-d`, version: 1,
  current_version: 3, current_doc_id: 'h3', current_name: 'r.pdf', is_current: isCurrent });

describe('oldVersionOf (V1 §7 banner rule)', () => {
  it('is the first item when the content is old everywhere it appears', () => {
    expect(oldVersionOf([item('Alpha', false), item('Beta', false)]).project.name).toBe('Alpha');
  });
  it('is null when it is current somewhere (the README case)', () => {
    expect(oldVersionOf([item('X', false), item('Y', true)])).toBeNull();
  });
  it('is null for nothing, or a non-list', () => {
    expect(oldVersionOf([])).toBeNull();
    expect(oldVersionOf(undefined)).toBeNull();
  });
});
```

`src/components/OldVersionBanner.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import OldVersionBanner from './OldVersionBanner';

const old = { project: { id: 'p', name: 'Project X' }, document_id: 'x', version: 1, current_version: 3,
  current_doc_id: 'h3', current_name: 'report.pdf', is_current: false };

describe('OldVersionBanner', () => {
  it('names the version, the project and the current one, and opens it', () => {
    const onOpenCurrent = vi.fn();
    render(<OldVersionBanner old={old} onOpenCurrent={onOpenCurrent} />);
    expect(screen.getByRole('status').textContent).toContain('Old version — v1 of report.pdf in Project X (current is v3)');
    fireEvent.click(screen.getByRole('button', { name: 'Open current' }));
    expect(onOpenCurrent).toHaveBeenCalledWith(old);
  });
  it('renders nothing when the document is current', () => {
    const { container } = render(<OldVersionBanner old={null} onOpenCurrent={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
});
```

Add these rows to the `it.each` table in `src/components/projects/ActivityTab.test.jsx`:

```js
    [ev('document.version_added', { name: 'r.pdf', version: 3, note: 'fixed table 4' }, { doc: { id: 'd', name: 'r.pdf' } }), 'Asha uploaded v3 of "r.pdf" — "fixed table 4"'],
    [ev('document.version_added', { name: 'r.pdf', version: 2 }, { doc: { id: 'd', name: 'r.pdf' } }), 'Asha uploaded v2 of "r.pdf"'],
    [ev('document.version_restored', { name: 'r.pdf', version: 4, from_version: 1 }, { doc: { id: 'd', name: 'r.pdf' } }), 'Asha restored v1 of "r.pdf" (now v4)'],
    [ev('document.merged', { from_name: 'r-final.pdf', into_name: 'r.pdf', versions: 2 }), 'Asha merged "r-final.pdf" into "r.pdf" (2 versions)'],
    [ev('document.merged', { from_name: 'b.pdf', into_name: 'a.pdf', versions: 1 }), 'Asha merged "b.pdf" into "a.pdf" (1 version)'],
    [ev('document.version_deleted', { name: 'r.pdf', version: 2 }, { doc: { id: 'd', name: 'r.pdf' } }), 'Asha deleted v2 of "r.pdf"'],
    [ev('document.removed', { name: 'r.pdf', versions: 3 }, { doc: { id: 'd', name: 'r.pdf' } }), 'Asha removed "r.pdf" and its 3 versions'],
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/lib/versions.test.js src/components/OldVersionBanner.test.jsx src/components/projects/ActivityTab.test.jsx`
Expected: FAIL, with unresolved imports and unknown event kinds.

- [ ] **Step 3: Implement**

`src/lib/versions.js`:

```js
/**
 * The old-version banner rule (V1 spec §7), the same as the server's
 * doc_versions.old_version_of: the first item when the content is an old
 * version in at least one project the reader can see and current in none;
 * else null. Content current in one project and old in another is a current
 * document — its Library row says the other project moved on.
 */
export function oldVersionOf(versionsIn) {
  const list = Array.isArray(versionsIn) ? versionsIn : [];
  if (list.length === 0 || list.some((v) => v.is_current)) return null;
  return list[0];
}
```

`src/hooks/useVersionStatus.js`:

```js
import { useEffect, useState } from 'react';
import { apiFetch } from '../utils/apiFetch';
import { oldVersionOf } from '../lib/versions';

/**
 * Where the open document stands in the reader's projects (V1 spec §7):
 * fetches GET /v1/docs/{id} when the document changes and returns the
 * banner's item, or null. Quiet on failure — a local-only file, a 404, no
 * backend — because then there is simply nothing to say.
 */
export function useVersionStatus({ apiHost, apiPort, docId, enabled = true, refreshKey = 0 }) {
  const [old, setOld] = useState(null);
  useEffect(() => {
    setOld(null);
    if (!docId || !enabled) return undefined;
    let live = true;
    (async () => {
      try {
        const res = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}`);
        if (!res.ok) return;
        const body = await res.json();
        if (live) setOld(oldVersionOf(body.versions_in));
      } catch {
        // offline or no backend: no banner
      }
    })();
    return () => { live = false; };
  }, [apiHost, apiPort, docId, enabled, refreshKey]);
  return old;
}
```

`src/components/OldVersionBanner.jsx`:

```jsx
/**
 * Shown over the reader when the open document is an old version (V1 spec
 * §7): which version, of which document, in which project, and a way to the
 * current one. Absolutely positioned like the folder-reconnect notice, so
 * it never shifts the page layout; wraps at phone width.
 */
export default function OldVersionBanner({ old, onOpenCurrent }) {
  if (!old) return null;
  return (
    <div role="status"
      className="absolute top-2 left-1/2 -translate-x-1/2 z-20 w-max max-w-[calc(100%-2rem)] flex flex-wrap items-center gap-2 px-3 py-1.5 rounded-lg shadow text-xs bg-amber-50 text-amber-900 border border-amber-300 dark:bg-amber-900/40 dark:text-amber-100 dark:border-amber-700">
      <span>Old version — v{old.version} of {old.current_name} in {old.project.name} (current is v{old.current_version})</span>
      <button onClick={() => onOpenCurrent(old)} className="underline font-bold">Open current</button>
    </div>
  );
}
```

In `src/components/projects/ActivityTab.jsx` `describeEvent`, replace the `document.removed` case and add the new ones:

```js
    case 'document.removed': return d.versions > 1 ? `${actor} removed "${doc}" and its ${d.versions} versions` : `${actor} removed "${doc}"`;
    case 'document.version_added': return `${actor} uploaded v${d.version} of "${doc}"${d.note ? ` — "${d.note}"` : ''}`;
    case 'document.version_restored': return `${actor} restored v${d.from_version} of "${doc}" (now v${d.version})`;
    case 'document.merged': return `${actor} merged "${d.from_name}" into "${d.into_name}" (${d.versions} version${d.versions === 1 ? '' : 's'})`;
    case 'document.version_deleted': return `${actor} deleted v${d.version} of "${doc}"`;
```

In `src/components/ChatView.jsx`:
- add the prop `openDocVersion = null` next to `openDocName`;
- pass `version={openDocVersion}` to `DocUseSwitch`;
- in `DocUseSwitch({ name, docUse, theme, darkMode, version })`, after the name `<span>`, add:

```jsx
      {version != null && <span className={theme.textMuted}>· v{version} (old version)</span>}
```

In `src/App.jsx`:
- imports: `import OldVersionBanner from './components/OldVersionBanner';` and `import { useVersionStatus } from './hooks/useVersionStatus';`;
- after `currentDocId` is defined: `const versionOld = useVersionStatus({ apiHost, apiPort, docId: currentDocId, enabled: Boolean(canReader), refreshKey: projectsVersion });`. If `canReader` is defined later, place the hook after it. `projectsVersion` is the existing state bumped by the Library's `onProjectsChanged`;
- on `<ChatView …>`, add `openDocVersion={currentDocId && versionOld ? versionOld.version : null}`;
- inside `<WorkspaceProvider …>`, immediately before `<PdfViewer`, add:

```jsx
          <OldVersionBanner old={versionOld}
            onOpenCurrent={(o) => openServerDoc(openDocDeps, o.current_doc_id, o.current_name)} />
```

- [ ] **Step 4: Run the tests and lint**

Run: `npx vitest run && npm run lint`
Expected: the whole frontend suite passes. Lint clean.

- [ ] **Step 5: Ask, then commit**

```bash
git add src/lib/versions.js src/lib/versions.test.js src/hooks/useVersionStatus.js \
  src/components/OldVersionBanner.jsx src/components/OldVersionBanner.test.jsx \
  src/components/projects/ActivityTab.jsx src/components/projects/ActivityTab.test.jsx \
  src/components/ChatView.jsx src/App.jsx
git commit -m "feat(versions-ui): old-version banner in the reader, chat's old-version label, Activity lines for version events"
```

---

### Task 15: Docs, and the spec brought in line with what was built

**Files:**
- Modify: `docs/LIBRARY.md`, `docs/USER_GUIDE.md`, `CHANGELOG.md`, `docs/TESTING.md`
- Modify: `docs/superpowers/specs/2026-10-12-document-versions-design.md`

- [ ] **Step 1: Correct the spec where the build had to differ**

Make each of these edits, adding a one-line "(changed in the build: …)" note where the build differs:

- **§5.1.** Split the upload row in two:
  - `POST …/documents/{id}/versions`: multipart `file` + optional `file_name`, `note`;
  - `POST …/documents/{id}/versions/from-library`: JSON `{doc_id, note}`.

  Reason: FastAPI validates one body type per route.
- **§5.2.** `versions_in` / `newer_in` items also carry `current_doc_id` and `current_name`, which "Open current" needs. The Library chips also carry `versions_count`.
- **§6 Library.** Add: "Chip × removes the project's document with its history; with more than one version it first asks *Remove … and its N versions from …?*; a chip's name opens the project page."
- **§9.** The role matrix lives in `server/tests/test_project_permission_matrix.py`, not `test_library_access_matrix.py`.

- [ ] **Step 2: `docs/USER_GUIDE.md`, "Projects and people"**

Replace the **Documents.** paragraph with:

```markdown
**Documents.** **Documents → File a document** adds one of your uploads. If the
project already has a document with that name (letter case and spaces ignored),
or the exact same file as an older version, you're asked: **Add as new version**
(or **Restore vN as current**), **Keep both**, or **Cancel**.

**Versions.** Each document keeps its history. **v3 · 3 versions** opens it:
every version can be opened; Contributors can **Restore** an old one (it comes
back as a new version on top, nothing is lost) and use **⋯ → Upload new
version** (a file, or one from your library, with an optional note);
Maintainers can **Delete** an old version for good, and use **⋯ → Mark as
newer version of…** to merge two documents that are really one (this can't be
undone, but every file stays in the history). Chat and the Library see only a
document's current version. A citation in an old chat opens the version it
quoted, with a banner saying it's an old version and a link to the current one.
If you keep your own copy of a document a project has since moved past, your
Library row says so and offers **Open current**.

A Maintainer removes a document with × (with all its versions; a file nobody
else has in their library is deleted with it, so you're asked first).
```

Replace the last line, "What you can't do yet…", with:

```markdown
What you can't do yet: chat with a whole project at once (planned), compare two
versions or review a version before it becomes current (planned), or rename a
document without uploading a new version.
```

- [ ] **Step 3: `docs/LIBRARY.md`**

Make these additions:

1. **A new section after "Projects govern their documents": "Versions (V1)".** It contains:
   - the content / document / version model table from spec §2;
   - "readable vs listed";
   - the same-bytes table (§4.3);
   - the name-match order (§4.2);
   - one line: "a document's name is stored on each version, as filed, and no longer follows the filer's own copy".
2. **The Projects API table:**
   - `PUT …/docs/{doc_id}` now returns `200`/`201 {document_id, version}`, takes `as_version_of` / `keep_both`, and can return `409 name_exists` / `older_version`;
   - delete the `DELETE …/docs/{doc_id}` row;
   - add a row for each route in `server/routers/project_documents.py`, with its role.
3. **The Documents API table:** `GET /v1/docs` lists current versions only and adds `newer_in`; chips gain `document_id`, `version` and `versions_count`; `GET /v1/docs/{id}` adds `versions_in`.
4. **Schema:** a "Migration 018 (document versions)" subsection, summarising §3.1 and the backfill, with the upgrade note "take a `pg_dump` first: the primary key changes and there is no down step".

- [ ] **Step 4: `CHANGELOG.md`, under `[Unreleased]`**

```markdown
### Added

- **Document versions in projects.** A revised file becomes a new version of a
  project's document instead of a second document: upload it from the
  document's ⋯ menu, or file a same-named file and choose *Add as new
  version*. Each document keeps its history: open any version, restore one
  (it comes back on top; nothing is lost), delete an old one, or merge two
  documents that are really one. The Library and chat see only the current
  version; an old chat's citation opens the version it quoted, labelled.

### Changed

- **API (breaking):** `PUT /v1/projects/{id}/docs/{doc_id}` returns
  `200`/`201 {document_id, version}` instead of `204`, and answers
  `409 name_exists` / `409 older_version` until the caller passes
  `as_version_of` or `keep_both=true`. `DELETE /v1/projects/{id}/docs/{doc_id}`
  is removed: use `DELETE /v1/projects/{id}/documents/{document_id}`, which
  removes the document with its history. New routes under
  `/v1/projects/{id}/documents`. `GET /v1/docs` lists current versions only;
  `doc_count` counts documents, not versions.
- A project's name for a document is stored when it is filed and no longer
  changes when the filer renames or removes their own copy.

### Upgrade notes

- **Take a `pg_dump` before upgrading.** Migration 018 changes the primary key
  of `project_documents` and has no down step. Existing project documents
  become version 1 of themselves, with the names they show today. Existing
  duplicates are not merged automatically: use *Mark as newer version of…*.
```

- [ ] **Step 5: `docs/TESTING.md`**

Add a "Document versions (V1)" entry to the feature → test ownership list, mapping each new test file to what it pins:
- `test_migration_018`;
- `test_document_versions_schema`;
- `test_doc_versions_read` / `_write` / `_concurrency`;
- `test_project_documents_routes`, `test_version_upload`, `test_project_filing`;
- the matrix rows;
- `ProjectDocsTab`, `NameMatchDialog`, `useProjectFiling`, `LibraryPage`, `OldVersionBanner`, `versions`, `ActivityTab`.

Also update the "How to run" counts with the numbers from Task 16's gate.

- [ ] **Step 6: Ask, then commit**

```bash
git add docs/LIBRARY.md docs/USER_GUIDE.md CHANGELOG.md docs/TESTING.md \
  docs/superpowers/specs/2026-10-12-document-versions-design.md
git commit -m "docs(versions): user guide, library reference, changelog with the API breaks and pg_dump note; spec matches the build"
```

---

### Task 16: Gate, running-app walk, handoff

**Files:**
- Create: `docs/superpowers/handoff/2026-10-12-document-versions/HANDOFF.md`

- [ ] **Step 1: Full gate**

```bash
.venv/bin/python -m pytest server/tests -q
npx vitest run
npm run lint
```

Expected: 0 failures and lint clean. Record the three results verbatim, with counts.

- [ ] **Step 2: Set up the walk (never the compose Keycloak)**

Follow the recipe in the user's memory `compose-keycloak-is-real` and in A0's handoff:
- a throwaway Keycloak 26.0 container on **:18081** (`start-dev --import-realm`), using a scratch copy of `deploy/keycloak/realm-export.json` with `http://localhost:5173/v1/auth/callback` added;
- the backend via `run.py` with explicit env and a scratch database: `DATABASE_URL=…/natural_reader_walk`. `run.py` doesn't read `.env`;
- Vite on 5173.

Create three users in the throwaway realm: an Owner, a Contributor and a Reader. Never `pkill -f run.py` from a shell whose command line contains that string.

- [ ] **Step 3: Walk J1–J12 from spec §9 in the browser**

Each journey uses only UI controls. For each, record the control used, what was seen, and pass/fail:

| # | Journey |
|---|---|
| J1 | A Contributor uploads v2 from ⋯ → one row "v2 · 2 versions", indexing → indexed |
| J2 | Chat on the current version answers from v2. Open v1 from history → banner. Chat answers from v1 with "v1 (old version)" |
| J3 | Restore v1 → v3, with an Activity line |
| J4 | File `report.pdf` from the library → dialog → *Add as new version*. A different same-named file → *Keep both* |
| J5 | A Maintainer merges two duplicates. The confirmation text and the "merged" labels are right |
| J6 | A Maintainer deletes an old version. A Contributor sees no Delete |
| J7 | A Reader sees history and can Open, with no ⋯ and no Restore |
| J8 | The Owner's personal copy has fallen behind → Library "newer version" line → *Open current* |
| J9 | README in two projects: v2 in X leaves Y unchanged, and the Library shows two rows |
| J10 | An old chat's citation opens v1, with the banner |
| J11 | The Documents tab, the history, the upload strip and the dialog at 390 px |
| J12 | A copy of a pre-018 database (restore a `pg_dump` of the dev DB into a scratch DB) upgrades: documents appear as v1 with unchanged names |

A failure is fixed in this task with TDD (a failing test first), then that journey is walked again.

- [ ] **Step 4: Tear down and write the handoff**

Remove the throwaway Keycloak container, the scratch databases and any walk files.

Write `HANDOFF.md` in the style of A0's:
- set-up;
- gate results;
- journeys with pass/fail;
- what was found and fixed;
- deferred minors;
- **what the user still can't do**: project chat (C2.1), compare and review (V2), renaming without a new version, un-merge, snapshots, quotas, the same-name marker, DEPLOYMENT backup guidance.

- [ ] **Step 5: Ask, then commit; never push or merge without asking**

```bash
git add docs/superpowers/handoff/2026-10-12-document-versions/HANDOFF.md
git commit -m "docs(handoff): V1 document versions — gate, running-app walk J1–J12, deferred"
```

Merging `feat/v1-document-versions` into `development` and pushing are separate decisions for the user.
