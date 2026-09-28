# Library many-to-many projects (C0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one document belong to many projects, make "can read this document" a single SQL definition, and let project owners read and prune every document in their projects.

**Architecture:** A new `project_documents` join table (migration `010`) replaces `documents.project_id`. `server/auth/authz.py` owns the one read predicate plus parameter helpers that every caller uses, so the placeholder count can't drift. New link/unlink routes live on the projects router. The Library shows one chip per project instead of a single select.

**Tech Stack:** FastAPI + psycopg 3 (async) + Postgres 16/pgvector, pytest (`asyncio_mode = auto`, per-test rollback), React + vitest + Testing Library.

**Spec:** [docs/superpowers/specs/2026-09-24-library-many-to-many-design.md](../specs/2026-09-24-library-many-to-many-design.md)

## Global Constraints

- Work on branch `development`. Never `master`.
- **Never run `git commit` or `git push` without asking the user first, every time.** Approval for one commit does not carry to the next. Every "Commit" step below means: show the diff summary, ask, and commit only on a yes.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend tests: `.venv/bin/python -m pytest <path> -v`. They need Postgres on `localhost:5433`: `podman compose up -d postgres` (or `docker compose up -d postgres`). From a snap-packaged terminal, prefix with `env -u XDG_DATA_HOME`.
- Frontend tests: `npx vitest run <path>`. Lint: `npm run lint`.
- The test DB `natural_reader_test` is **persistent**. Each pytest session applies any new `server/sql/NNN_*.sql` to it once. After Task 2 lands, `documents.project_id` is gone from that DB for good.
- `doc_id` is a 64-char lowercase hex string (`^[0-9a-f]{64}$`). `project_id` is a UUID.
- Missing and not-permitted both answer `404`. Never `403` for ownership or visibility.
- Membership and grants are read-only. Only a doc's owner may link it. The doc owner or the project owner may unlink it.

## Review Focus

1. **All three list filters at once** (`GET /v1/docs?q=…&project_id=…&tag=…`). The `projects` sub-select sits in the SELECT clause, so its parameters must be bound **before** the WHERE parameters. A wrong order either errors out or silently checks visibility against the wrong value. Expected: returns exactly the matching doc. *Test added in Task 2.*
2. **Doc owner removed from a project their doc is still linked to.** Expected: `projects[]` still shows that project to the doc's owner (spec §7.2 owner exception), so the UI can show and remove the link, and the API unlink returns `204`. *Tests added in Task 2 (list) and Task 3 (unlink).*
3. **Project deleted while docs are linked.** Expected: the docs survive and no longer list the project. *Test added in Task 3.*
4. **Admin reassigns a doc's owner after it was linked.** Expected: the new owner can unlink links the old owner made (`204`). *Test added in Task 3.*
5. **Two visible projects with the same name** (you own "Infra" and are a member of someone else's "Infra"). Expected: both chips appear, distinct by id, in a stable order (name, then id). *Test added in Task 2.*

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `server/auth/authz.py` | The one read predicate + `*_params()` helpers; visible-projects predicate | 1, 2 |
| `server/tests/dbutil.py` (new) | Test-DB URLs and a reusable migration applier (`max_version`) | 2 |
| `server/tests/conftest.py` | Uses `dbutil`; behaviour unchanged | 2 |
| `server/sql/010_project_documents.sql` (new) | Join table, backfill, drop `documents.project_id` | 2 |
| `server/routers/docs.py` | List / status responses carry `projects[]`; PATCH drops `project_id` | 1, 2 |
| `server/routers/projects.py` | `PUT`/`DELETE /v1/projects/{id}/docs/{doc_id}` | 3 |
| `server/tests/test_migration_010.py` (new) | Scratch-database backfill test | 2 |
| `server/tests/test_library_access_matrix.py` (new) | Role × project-count matrix through both read paths | 2 |
| `server/tests/test_library_authz.py`, `test_library_schema.py`, `test_docs_list.py`, `test_docs_patch.py` | Moved to the new shape | 1, 2 |
| `server/tests/test_projects_router.py` | Link/unlink rules | 3 |
| `src/lib/docMeta.js` (+ test) | Register flow links via `PUT` | 4 |
| `src/components/library/LibraryPage.jsx` (+ test) | Project chips, add select, remove × | 5 |
| `docs/LIBRARY.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md` | Docs | 6 |

**Why Task 2 is large:** dropping `documents.project_id` breaks every reader of that column the moment the migration file exists, because the next pytest session applies it. The migration, the predicate change, the `docs.py` readers and the affected tests can't be split without leaving the suite red between tasks.

---

### Task 1: One read predicate with parameter helpers (no behaviour change)

**Files:**
- Modify: `server/auth/authz.py:25-59`
- Modify: `server/routers/docs.py:32` (import), `server/routers/docs.py:224-227` (`list_documents` params)
- Test: `server/tests/test_library_authz.py`

**Interfaces:**
- Produces:
  - `readable_docs_where(alias: str = "d") -> str`
  - `readable_docs_params(user_id: str) -> list[str]`
  - `CAN_READ_ONE_DOC_SQL: str` (takes `[doc_id, *readable_docs_params(uid)]`)
  - `CAN_READ_DOCS_SQL: str` (kept alias)
  - `assert_can_read_doc(conn, doc_id, user_id)` — same signature and behaviour

- [ ] **Step 1: Write the failing tests** — append to `server/tests/test_library_authz.py`:

```python
async def test_readable_predicate_placeholders_match_params():
    # The predicate and its params are defined side by side; a caller that
    # hand-counts user ids is exactly how a uid gets bound to the wrong column.
    assert authz.readable_docs_where("d").count("%s") == len(authz.readable_docs_params("u"))


async def test_can_read_one_doc_query_placeholders_match_params():
    # The single-doc query has a LEADING doc_id before the user ids.
    assert authz.CAN_READ_ONE_DOC_SQL.count("%s") == 1 + len(authz.readable_docs_params("u"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_library_authz.py -v`
Expected: the two new tests FAIL with `AttributeError: module 'server.auth.authz' has no attribute 'readable_docs_params'`; the five existing tests PASS.

- [ ] **Step 3: Implement.** Replace `assert_can_read_doc`, `readable_docs_where` and `CAN_READ_DOCS_SQL` in `server/auth/authz.py` (everything from `async def assert_can_read_doc` to the end of the file) with:

```python
def readable_docs_where(alias: str = "d") -> str:
    """The ONE definition of "can read this document". `<alias>` is a
    `documents` row. Bind it with `readable_docs_params(user_id)` — never a
    hand-counted list. Subquery aliases are underscore-prefixed so they can't
    shadow a table alias in the caller's query. Access is resolved in SQL,
    never post-filtered in Python."""
    return (
        f"({alias}.user_id = %s "
        f"OR EXISTS (SELECT 1 FROM project_members _rpm "
        f"WHERE _rpm.project_id = {alias}.project_id AND _rpm.user_id = %s) "
        f"OR EXISTS (SELECT 1 FROM doc_grants _rg "
        f"WHERE _rg.doc_id = {alias}.doc_id AND _rg.grantee_user_id = %s))"
    )


def readable_docs_params(user_id: str) -> list[str]:
    """Exactly the parameters `readable_docs_where` needs, in order."""
    return [user_id, user_id, user_id]


CAN_READ_DOCS_SQL = readable_docs_where()
CAN_READ_ONE_DOC_SQL = (
    f"SELECT 1 FROM documents d WHERE d.doc_id = %s AND {readable_docs_where('d')}"
)


async def assert_can_read_doc(conn, doc_id: str, user_id: str) -> None:
    """404 unless `user_id` can read `doc_id`. Missing and not-readable are
    indistinguishable (no existence leak)."""
    cur = await conn.execute(CAN_READ_ONE_DOC_SQL, [doc_id, *readable_docs_params(user_id)])
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Document not found")
```

In `server/routers/docs.py` change the import on line 32 to:

```python
from ..auth.authz import (
    assert_can_read_doc,
    assert_owns_doc,
    readable_docs_params,
    readable_docs_where,
)
```

and in `list_documents` replace

```python
    params: list[Any] = [uid, uid, uid]
```

with

```python
    params: list[Any] = readable_docs_params(uid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_library_authz.py server/tests/test_docs_list.py server/tests/test_docs_authz.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (ask first)

```bash
git add server/auth/authz.py server/routers/docs.py server/tests/test_library_authz.py
git commit -m "refactor(authz): one read predicate with parameter helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Migration 010 and every reader moved to `project_documents`

**Files:**
- Create: `server/tests/dbutil.py`, `server/sql/010_project_documents.sql`, `server/tests/test_migration_010.py`, `server/tests/test_library_access_matrix.py`
- Modify: `server/tests/conftest.py`, `server/auth/authz.py`, `server/routers/docs.py`
- Modify tests: `server/tests/test_library_authz.py`, `server/tests/test_library_schema.py`, `server/tests/test_docs_list.py`, `server/tests/test_docs_patch.py`

**Interfaces:**
- Consumes: Task 1's `readable_docs_where`, `readable_docs_params`, `CAN_READ_ONE_DOC_SQL`.
- Produces:
  - `server.tests.dbutil`: `TEST_DB`, `ADMIN_URL`, `TEST_URL`, `SQL_DIR`, `url_for(db_name: str) -> str`, `async apply_migrations(url: str, max_version: int | None = None) -> None`
  - `authz.visible_projects_where(alias: str = "p") -> str`, `authz.visible_projects_params(user_id: str) -> list[str]`
  - `readable_docs_params` now returns **four** ids
  - `docs._doc_projects_sql(alias: str) -> str` and `docs._doc_projects_params(user_id: str) -> list[str]`
  - `docs._fetch_doc_status(conn, doc_id: str, user_id: str)`
  - `GET /v1/docs` rows: `{doc_id, file_name, state, tags, projects: [{id, name}], owner_user_id, is_owner}`
  - Doc status responses carry `projects` instead of `project_id`
  - `PATCH /v1/docs/{id}` rejects `project_id` with 422

- [ ] **Step 1: Extract the test-DB plumbing.** Create `server/tests/dbutil.py`:

```python
"""Test-database plumbing shared by conftest.py and migration tests that need
a *scratch* database. Lives outside conftest so tests can import it."""
from __future__ import annotations

import os
from pathlib import Path

from psycopg import AsyncConnection

TEST_DB = "natural_reader_test"
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql://natural_reader:natural_reader@localhost:5433/postgres",
)
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql://natural_reader:natural_reader@localhost:5433/{TEST_DB}",
)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def url_for(db_name: str) -> str:
    """Same server and credentials as ADMIN_URL, different database."""
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + db_name


async def apply_migrations(url: str, max_version: int | None = None) -> None:
    """Apply pending `NNN_*.sql` files in order, one transaction each — the same
    rules as server/db.py's runner. `max_version` stops early, so a migration
    test can build the schema as it was BEFORE the migration under test."""
    conn = await AsyncConnection.connect(url)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur = await conn.execute("SELECT version FROM schema_migrations")
        applied = {r[0] for r in await cur.fetchall()}
        await conn.commit()
        for path in sorted(SQL_DIR.glob("*.sql")):
            head = path.name.split("_", 1)[0]
            version = int(head) if head.isdigit() else None
            if version is None or version in applied:
                continue
            if max_version is not None and version > max_version:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
    finally:
        await conn.close()
```

In `server/tests/conftest.py`, delete the `TEST_DB`, `_ADMIN_URL`, `TEST_URL`, `_SQL_DIR` definitions and the `_apply_migrations` function. Delete the now-unused `import os` and `from pathlib import Path` lines too, but keep the other imports. Add:

```python
from server.tests.dbutil import ADMIN_URL as _ADMIN_URL, TEST_DB, TEST_URL, apply_migrations
```

and change `_migrated` to:

```python
@pytest.fixture(scope="session", autouse=True)
def _migrated():
    """Ensure the test DB exists and all migrations are applied, once."""
    asyncio.run(_ensure_test_db())
    asyncio.run(apply_migrations(TEST_URL))
```

- [ ] **Step 2: Check the refactor imports and changes nothing**

Run: `.venv/bin/python -m pytest server/tests/test_conftest_smoke.py server/tests/test_library_authz.py -v`
Expected: PASS. If `from server.tests.dbutil import …` raises `ModuleNotFoundError`, `server/tests` is not resolving as a namespace subpackage. Stop and report it rather than adding `__init__.py`, which would change how pytest names every test module.

- [ ] **Step 3: Write the failing migration test.** Create `server/tests/test_migration_010.py`:

```python
"""Migration 010 backfill. The shared test DB already has 010 applied (the
session fixture runs every migration once), so pre-010 rows can't exist there.
This test builds a throwaway database at version 009, seeds it, runs the real
010 file from disk, and drops the database."""
import secrets

import pytest
from psycopg import AsyncConnection

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def test_010_backfills_links_and_drops_column():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = url_for(name)
            await apply_migrations(url, max_version=9)
            conn = await AsyncConnection.connect(url, autocommit=True)
            try:
                alice = (await _one(conn,
                    "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                    "VALUES ('i','a','a@x.io','member','active') RETURNING id"))[0]
                bob = (await _one(conn,
                    "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                    "VALUES ('i','b','b@x.io','member','active') RETURNING id"))[0]
                p1 = (await _one(conn,
                    "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P1') RETURNING id",
                    (alice,)))[0]
                p2 = (await _one(conn,
                    "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P2') RETURNING id",
                    (bob,)))[0]
                seeds = [("a" * 64, alice, p1), ("b" * 64, bob, p2),
                         ("c" * 64, alice, p1), ("d" * 64, alice, None)]
                for doc_id, owner, pid in seeds:
                    await conn.execute(
                        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, "
                        "user_id, project_id) VALUES (%s,'f.pdf','pdf',1,%s,%s)",
                        (doc_id, owner, pid))

                async with conn.transaction():  # one transaction, like server/db.py
                    await conn.execute((SQL_DIR / "010_project_documents.sql").read_text())

                cur = await conn.execute("SELECT project_id, doc_id FROM project_documents")
                links = {(str(p), d) for p, d in await cur.fetchall()}
                assert links == {(str(p1), "a" * 64), (str(p2), "b" * 64), (str(p1), "c" * 64)}

                assert await _one(conn,
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'documents' AND column_name = 'project_id'") is None
                assert await _one(conn,
                    "SELECT 1 FROM pg_indexes WHERE indexname = 'documents_project_idx'") is None
                assert await _one(conn,
                    "SELECT 1 FROM schema_migrations WHERE version = 10") is not None
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
```

- [ ] **Step 4: Run it to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_migration_010.py -v`
Expected: FAIL with `FileNotFoundError: …/010_project_documents.sql`.

- [ ] **Step 5: Write the migration.** Create `server/sql/010_project_documents.sql`:

```sql
-- Many-to-many projects <-> documents. Replaces documents.project_id (009).
-- No added_by: only a doc's owner can link it, so it would always equal
-- documents.user_id. Add it if project owners ever get to add others' docs.
CREATE TABLE IF NOT EXISTS project_documents (
    project_id UUID NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, doc_id)
);
-- PK serves project -> docs; this serves doc -> projects (the read predicate).
CREATE INDEX IF NOT EXISTS project_documents_doc_idx ON project_documents(doc_id);

-- Carry every existing single-project assignment across. ON CONFLICT can't
-- fire (one project per doc before this migration); kept as a statement of intent.
INSERT INTO project_documents (project_id, doc_id)
SELECT project_id, doc_id FROM documents WHERE project_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Same migration, so there is never a moment with two sources of truth.
DROP INDEX IF EXISTS documents_project_idx;
ALTER TABLE documents DROP COLUMN IF EXISTS project_id;

INSERT INTO schema_migrations(version) VALUES (10) ON CONFLICT DO NOTHING;
```

- [ ] **Step 6: Run the migration test to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_migration_010.py -v`
Expected: PASS. (This session also applies 010 to `natural_reader_test`. From here until Step 12, the docs-router tests fail — expected.)

- [ ] **Step 7: Write the failing read-model tests.** In `server/tests/test_library_authz.py` replace the `_doc` helper with:

```python
async def _doc(conn, doc_id, owner, project_ids=()):
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f.pdf','pdf',1,%s)", (doc_id, owner))
    for pid in project_ids:
        await conn.execute(
            "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, doc_id))


async def _project(conn, owner, name="P"):
    cur = await conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id", (owner, name))
    return str((await cur.fetchone())[0])
```

In `test_project_member_can_read` change `await _doc(db_conn, "d1", o, pid)` to `await _doc(db_conn, "d1", o, [pid])`. In `test_member_of_other_project_cannot_read` change `await _doc(db_conn, "d1", o, proj_b)` to `await _doc(db_conn, "d1", o, [proj_b])`, and its comment's last sentence to `Pins the project_documents join to the doc's own links.` Then append:

```python
async def test_project_owner_reads_member_filed_doc(db_conn):
    # Regression: the owner of a project has no project_members row, and used
    # to be a stranger to docs other members filed into their own project.
    alice = await _user(db_conn, "alice", "alice@x.io")
    bob = await _user(db_conn, "bob", "bob@x.io")
    pid = await _project(db_conn, alice)
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, bob))
    await _doc(db_conn, "d1", bob, [pid])
    await authz.assert_can_read_doc(db_conn, "d1", alice)  # no raise


async def test_member_of_either_linked_project_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    m1 = await _user(db_conn, "m1", "m1@x.io")
    m2 = await _user(db_conn, "m2", "m2@x.io")
    p1 = await _project(db_conn, o, "A")
    p2 = await _project(db_conn, o, "B")
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s),(%s,%s)",
        (p1, m1, p2, m2))
    await _doc(db_conn, "d1", o, [p1, p2])
    await authz.assert_can_read_doc(db_conn, "d1", m1)
    await authz.assert_can_read_doc(db_conn, "d1", m2)


async def test_visible_projects_placeholders_match_params():
    assert authz.visible_projects_where("p").count("%s") == len(authz.visible_projects_params("u"))
```

In `server/tests/test_library_schema.py` replace `test_documents_gain_project_and_tags` with:

```python
async def test_documents_have_tags_and_no_project_column(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o2','o2@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, tags) "
        "VALUES ('d1','f.pdf','pdf',10,%s,'{alpha,beta}')", (owner,))
    cur = await db_conn.execute("SELECT tags FROM documents WHERE doc_id='d1'")
    assert set((await cur.fetchone())[0]) == {"alpha", "beta"}
    cur = await db_conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='documents' AND column_name='project_id'")
    assert await cur.fetchone() is None


async def test_project_documents_links_and_cascades(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o3','o3@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (owner,))
    pid = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES ('d2','f.pdf','pdf',1,%s)", (owner,))
    await db_conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,'d2')", (pid,))
    await db_conn.execute("DELETE FROM projects WHERE id = %s", (pid,))
    cur = await db_conn.execute("SELECT count(*) FROM project_documents WHERE doc_id='d2'")
    assert (await cur.fetchone())[0] == 0
    cur = await db_conn.execute("SELECT count(*) FROM documents WHERE doc_id='d2'")
    assert (await cur.fetchone())[0] == 1  # the doc survives its project
```

- [ ] **Step 8: Rewrite the predicate.** In `server/auth/authz.py` replace `readable_docs_where` and `readable_docs_params` with the project_documents version, and add the visible-projects pair:

```python
def readable_docs_where(alias: str = "d") -> str:
    """The ONE definition of "can read this document": owner, grantee, owner of
    a linked project, or member of a linked project. `<alias>` is a `documents`
    row. Bind it with `readable_docs_params(user_id)` — never a hand-counted
    list. Subquery aliases are underscore-prefixed so they can't shadow a table
    alias in the caller's query. Access is resolved in SQL, never in Python."""
    return (
        f"({alias}.user_id = %s "
        f"OR EXISTS (SELECT 1 FROM doc_grants _rg "
        f"WHERE _rg.doc_id = {alias}.doc_id AND _rg.grantee_user_id = %s) "
        f"OR EXISTS (SELECT 1 FROM project_documents _rpd "
        f"JOIN projects _rp ON _rp.id = _rpd.project_id "
        f"WHERE _rpd.doc_id = {alias}.doc_id AND (_rp.owner_user_id = %s "
        f"OR EXISTS (SELECT 1 FROM project_members _rpm "
        f"WHERE _rpm.project_id = _rp.id AND _rpm.user_id = %s))))"
    )


def readable_docs_params(user_id: str) -> list[str]:
    """Exactly the parameters `readable_docs_where` needs, in order."""
    return [user_id, user_id, user_id, user_id]


def visible_projects_where(alias: str = "p") -> str:
    """A `projects` row the user owns or is a member of. Bind with
    `visible_projects_params(user_id)`."""
    return (
        f"({alias}.owner_user_id = %s OR EXISTS (SELECT 1 FROM project_members _vpm "
        f"WHERE _vpm.project_id = {alias}.id AND _vpm.user_id = %s))"
    )


def visible_projects_params(user_id: str) -> list[str]:
    return [user_id, user_id]
```

Leave `CAN_READ_DOCS_SQL`, `CAN_READ_ONE_DOC_SQL` and `assert_can_read_doc` as Task 1 wrote them. They pick up the new predicate automatically.

- [ ] **Step 9: Run the read-model tests**

Run: `.venv/bin/python -m pytest server/tests/test_library_authz.py server/tests/test_library_schema.py server/tests/test_migration_010.py -v`
Expected: all PASS.

- [ ] **Step 10: Write the failing list/status/PATCH tests.**

In `server/tests/test_docs_list.py` replace `_doc` with:

```python
async def _doc(db_conn, doc_id, owner_id, *, tags=None, project_ids=(), file_name="f.pdf"):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, tags) "
        "VALUES (%s,%s,'pdf',1,%s,%s)",
        (doc_id, file_name, owner_id, tags or []),
    )
    for pid in project_ids:
        await db_conn.execute(
            "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, doc_id))


async def _project(db_conn, owner_id, name):
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id",
        (owner_id, name))
    return str((await cur.fetchone())[0])
```

In `test_list_project_member_sees_project_doc_stranger_still_excluded`, change `await _doc(db_conn, DOC_P, owner.user_id, project_id=project_id)` to `await _doc(db_conn, DOC_P, owner.user_id, project_ids=[project_id])`, and replace the two lines

```python
        assert row["project_id"] == project_id
        assert row["project_name"] == "P"
```

with

```python
        assert row["projects"] == [{"id": project_id, "name": "P"}]
```

Then append:

```python
async def test_list_projects_hide_names_the_caller_cannot_see(db_conn, docs_app):
    owner = await _member(db_conn, "owner4")
    caller = await _member(db_conn, "caller4")
    visible = await _project(db_conn, owner.user_id, "Visible")
    hidden = await _project(db_conn, owner.user_id, "Hidden")
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
        (visible, caller.user_id))
    await _doc(db_conn, DOC_P, owner.user_id, project_ids=[visible, hidden])

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        row = next(r for r in (await client.get("/v1/docs")).json() if r["doc_id"] == DOC_P)
        assert row["projects"] == [{"id": visible, "name": "Visible"}]


async def test_doc_owner_sees_links_to_projects_they_left(db_conn, docs_app):
    # Review Focus 2 / spec §7.2 owner exception: removed from the project,
    # the owner must still see (and so be able to withdraw) the link.
    owner = await _member(db_conn, "owner8")
    proj_owner = await _member(db_conn, "projowner8")
    pid = await _project(db_conn, proj_owner.user_id, "TheirProject")
    await _doc(db_conn, DOC_C, owner.user_id, project_ids=[pid])  # owner is NOT a member

    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        row = (await client.get("/v1/docs")).json()[0]
        assert row["projects"] == [{"id": pid, "name": "TheirProject"}]
        status = (await client.get(f"/v1/docs/{DOC_C}")).json()
        assert status["projects"] == [{"id": pid, "name": "TheirProject"}]


async def test_doc_projects_placeholders_match_params():
    assert docs_router._doc_projects_sql("d").count("%s") == len(
        docs_router._doc_projects_params("u"))


async def test_list_filter_by_invisible_project_is_empty(db_conn, docs_app):
    owner = await _member(db_conn, "owner5")
    caller = await _member(db_conn, "caller5")
    hidden = await _project(db_conn, owner.user_id, "Hidden")
    await _doc(db_conn, DOC_G, owner.user_id, project_ids=[hidden])
    await _grant(db_conn, DOC_G, caller.user_id)  # readable, but the project is not

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs", params={"project_id": hidden})
        assert r.status_code == 200 and r.json() == []


async def test_list_all_three_filters_bind_in_order(db_conn, docs_app):
    # Review Focus 1: the projects sub-select's params come BEFORE the WHERE
    # params. All three filters together exercise every placeholder.
    caller = await _member(db_conn, "caller6")
    pid = await _project(db_conn, caller.user_id, "Mine")
    await _doc(db_conn, DOC_C, caller.user_id, tags=["alpha"], file_name="report.pdf",
               project_ids=[pid])
    await _doc(db_conn, "2" * 64, caller.user_id, tags=["alpha"], file_name="report2.pdf")

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs",
                             params={"q": "report", "project_id": pid, "tag": "alpha"})
        assert r.status_code == 200
        assert [row["doc_id"] for row in r.json()] == [DOC_C]


async def test_list_same_named_projects_are_both_listed_in_stable_order(db_conn, docs_app):
    # Review Focus 5: own "Infra" + member of someone else's "Infra".
    caller = await _member(db_conn, "caller7")
    other = await _member(db_conn, "other7")
    mine = await _project(db_conn, caller.user_id, "Infra")
    theirs = await _project(db_conn, other.user_id, "Infra")
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
        (theirs, caller.user_id))
    await _doc(db_conn, DOC_C, caller.user_id, project_ids=[mine, theirs])

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        row = (await client.get("/v1/docs")).json()[0]
        assert row["projects"] == [{"id": i, "name": "Infra"} for i in sorted([mine, theirs])]
```

In `server/tests/test_docs_patch.py`:
- **Delete** `test_owner_cannot_assign_doc_to_foreign_project`, `test_owner_member_can_assign_doc_to_a_project_they_belong_to`, `test_owner_can_assign_doc_to_own_project` and `test_owner_can_clear_project`. Their rules move to the link endpoint (Task 3).
- **Replace** `test_owner_sets_tags_and_project_reflected_in_response` with:

```python
async def test_owner_sets_tags_reflected_in_response(db_conn, docs_app):
    owner = await _user(db_conn, "owner-patch")
    await _insert_doc(db_conn, HEX, owner.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        r = await client.patch(f"/v1/docs/{HEX}", json={"tags": ["b", "a", "a"]})
        assert r.status_code == 200
        body = r.json()
        assert body["tags"] == ["a", "b"]  # sorted + deduped
        assert body["projects"] == []
        assert "project_id" not in body
```

- **Replace** `test_malformed_project_id_is_422_not_500` with:

```python
@pytest.mark.parametrize("project_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000009"])
async def test_patch_rejects_project_id(db_conn, docs_app, project_id):
    # Project links moved to PUT/DELETE /v1/projects/{id}/docs/{doc_id}.
    # A stale client sending project_id gets 422 — and so does any tag
    # change in the same request (the whole body is rejected).
    owner = await _user(db_conn, "owner-patch-badproj")
    doc_id = "5" + "a" * 63
    await _insert_doc(db_conn, doc_id, owner.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        r = await client.patch(f"/v1/docs/{doc_id}",
                               json={"project_id": project_id, "tags": ["t"]})
        assert r.status_code == 422

    cur = await db_conn.execute("SELECT tags FROM documents WHERE doc_id = %s", (doc_id,))
    assert (await cur.fetchone())[0] == []
```

Create `server/tests/test_library_access_matrix.py`:

```python
"""Access matrix: every role x (doc in 0 / 1 / 2 projects), checked through
BOTH read paths — assert_can_read_doc (single-doc routes) and GET /v1/docs
(the list) — so the two can never disagree."""
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router

pytestmark = pytest.mark.asyncio

DOC = "e" * 64
ROLES = ["doc_owner", "grantee", "p1_owner", "p1_member", "p2_owner", "p2_member", "stranger"]


@pytest.fixture
def docs_app(db_conn, monkeypatch):
    class _PoolShim:
        @asynccontextmanager
        async def connection(self):
            yield db_conn

    monkeypatch.setattr(docs_router, "get_pool", lambda: _PoolShim())
    monkeypatch.setattr(docs_router, "is_ready", lambda: True)
    app = FastAPI()
    app.include_router(docs_router.router)
    return app


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


def _expected(role, n_projects):
    if role in ("doc_owner", "grantee"):
        return True
    if role in ("p1_owner", "p1_member"):
        return n_projects >= 1
    if role in ("p2_owner", "p2_member"):
        return n_projects >= 2
    return False


@pytest.mark.parametrize("n_projects", [0, 1, 2])
@pytest.mark.parametrize("role", ROLES)
async def test_access_matrix(db_conn, docs_app, role, n_projects):
    users = {r: await _member(db_conn, f"mx-{r}") for r in ROLES}
    uid = {r: users[r].user_id for r in ROLES}

    projects = []
    for owner_role, member_role, name in (("p1_owner", "p1_member", "P1"),
                                          ("p2_owner", "p2_member", "P2")):
        cur = await db_conn.execute(
            "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id",
            (uid[owner_role], name))
        pid = str((await cur.fetchone())[0])
        await db_conn.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
            (pid, uid[member_role]))
        projects.append(pid)

    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f.pdf','pdf',1,%s)", (DOC, uid["doc_owner"]))
    for pid in projects[:n_projects]:
        await db_conn.execute(
            "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, DOC))
    await db_conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s)", (DOC, uid["grantee"]))

    readable = _expected(role, n_projects)
    if readable:
        await authz.assert_can_read_doc(db_conn, DOC, uid[role])
    else:
        with pytest.raises(HTTPException) as e:
            await authz.assert_can_read_doc(db_conn, DOC, uid[role])
        assert e.value.status_code == 404

    docs_app.dependency_overrides[deps.get_current_user] = lambda: users[role]
    async with httpx.AsyncClient(transport=ASGITransport(app=docs_app),
                                 base_url="http://t") as client:
        r = await client.get("/v1/docs")
        assert r.status_code == 200
        assert (DOC in {row["doc_id"] for row in r.json()}) is readable
```

- [ ] **Step 11: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_docs_list.py server/tests/test_docs_patch.py server/tests/test_library_access_matrix.py -v`
Expected: FAIL. `docs.py` still selects `d.project_id` (`UndefinedColumn: column d.project_id does not exist`), and PATCH still accepts `project_id`.

- [ ] **Step 12: Move `docs.py` to the new model.**

Extend the authz import:

```python
from ..auth.authz import (
    assert_can_read_doc,
    assert_owns_doc,
    readable_docs_params,
    readable_docs_where,
    visible_projects_params,
    visible_projects_where,
)
```

Remove `project_id` from `DocPatchIn`:

```python
class DocPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] | None = None
    owner_user_id: str | None = None
```

Add this helper just above `_fetch_doc_status`:

```python
def _doc_projects_sql(alias: str) -> str:
    """JSON array `[{id, name}]` of the projects `<alias>` is linked to. The
    doc's OWNER sees every link, even to projects they've since left, so they
    can always see and withdraw where their document is shared. Everyone else
    sees only projects they own or belong to: a doc shared by grant may also sit
    in projects the caller has no access to, and listing those would leak their
    names. Sorted by name, then id, so same-named projects keep a stable order.
    Bind with `_doc_projects_params(user_id)`. Used in a SELECT list, so its
    params come BEFORE any WHERE params."""
    return (
        "COALESCE((SELECT json_agg(json_build_object('id', _vp.id, 'name', _vp.name) "
        "ORDER BY _vp.name, _vp.id) "
        "FROM project_documents _vpd JOIN projects _vp ON _vp.id = _vpd.project_id "
        f"WHERE _vpd.doc_id = {alias}.doc_id "
        f"AND ({alias}.user_id = %s OR {visible_projects_where('_vp')})), '[]'::json)"
    )


def _doc_projects_params(user_id: str) -> list[str]:
    """Exactly the parameters `_doc_projects_sql` needs, in order."""
    return [user_id, *visible_projects_params(user_id)]
```

Replace `_fetch_doc_status` with:

```python
async def _fetch_doc_status(conn, doc_id: str, user_id: str) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT d.doc_id, d.file_name, d.file_type, d.size_bytes, d.page_count,
                   d.state, d.embedding_model, d.embedding_dim, d.error_message,
                   d.created_at, d.updated_at,
                   d.conversion_state, d.conversion_options, d.conversion_error,
                   d.converted_at, d.pdf_path, d.tags,
                   {_doc_projects_sql('d')} AS projects,
                   COALESCE(c.cnt, 0) AS chunk_count,
                   COALESCE(c.embedded, 0) AS embedded_count,
                   COALESCE(p.page_cnt, 0) AS converted_page_count
            FROM documents d
            LEFT JOIN (
                SELECT doc_id,
                       COUNT(*) AS cnt,
                       COUNT(embedding) AS embedded
                FROM doc_chunks
                GROUP BY doc_id
            ) c ON c.doc_id = d.doc_id
            LEFT JOIN (
                SELECT doc_id, COUNT(*) AS page_cnt FROM doc_pages GROUP BY doc_id
            ) p ON p.doc_id = d.doc_id
            WHERE d.doc_id = %s
            """,
            [*_doc_projects_params(user_id), doc_id],
        )
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
    rec = dict(zip(cols, row))
    rec["created_at"] = _epoch_ms(rec["created_at"])
    rec["updated_at"] = _epoch_ms(rec["updated_at"])
    if rec.get("converted_at") is not None:
        rec["converted_at"] = _epoch_ms(rec["converted_at"])
    # Don't leak the absolute filesystem path to the client.
    rec["has_pdf"] = bool(rec.pop("pdf_path", None))
    return rec
```

Update its four callers:
- `register_document`: `status = await _fetch_doc_status(conn, payload.doc_id, principal.user_id)`
- `get_document`: rename the parameter `_reader` to `reader` and call `status = await _fetch_doc_status(conn, doc_id, reader.user_id)`
- `patch_document`: `status = await _fetch_doc_status(conn, doc_id, principal.user_id)`
- `upload_chunks`: rename `_owner` to `owner` and call `status = await _fetch_doc_status(conn, doc_id, owner.user_id)`

Replace the body of `list_documents` from `_ensure_ready()` to the `return` with:

```python
    _ensure_ready()
    uid = principal.user_id
    where = [readable_docs_where("d")]
    where_params: list[Any] = readable_docs_params(uid)
    if q:
        where.append("(d.file_name ILIKE %s OR %s = ANY(d.tags))")
        where_params += [f"%{q}%", q]
    if project_id:
        # Only a project the caller can see: filtering a granted doc by a
        # guessed project id must not reveal whether its owner linked it there.
        where.append(
            "EXISTS (SELECT 1 FROM project_documents _fpd "
            "JOIN projects _fp ON _fp.id = _fpd.project_id "
            f"WHERE _fpd.doc_id = d.doc_id AND _fpd.project_id = %s "
            f"AND {visible_projects_where('_fp')})"
        )
        where_params += [project_id, *visible_projects_params(uid)]
    if tag:
        where.append("%s = ANY(d.tags)")
        where_params.append(tag)
    sql = (
        "SELECT d.doc_id, d.file_name, d.state, d.tags, d.user_id, "
        f"{_doc_projects_sql('d')} AS projects "
        "FROM documents d "
        f"WHERE {' AND '.join(where)} ORDER BY d.updated_at DESC"
    )
    # SELECT-list params first, then WHERE params: psycopg binds by position.
    params = [*_doc_projects_params(uid), *where_params]
    pool = get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {"doc_id": r[0], "file_name": r[1], "state": r[2], "tags": r[3],
         "projects": r[5], "owner_user_id": str(r[4]), "is_owner": str(r[4]) == uid}
        for r in rows
    ]
```

In `patch_document`:
- Delete the whole `if data.get("project_id") is not None and principal.role != "admin":` block together with its comment.
- Delete the `if "project_id" in data:` branch.
- In the docstring, replace everything after the first paragraph with: `Project links are managed by PUT/DELETE /v1/projects/{id}/docs/{doc_id}; project_id here is a 422 (extra="forbid").`
- Change the first docstring line to `Update tags (owner-only) or reassign ownership (admin-only).`

- [ ] **Step 13: Run the full backend suite**

Run: `.venv/bin/python -m pytest server/tests -v`
Expected: all PASS. Then run `grep -rn "project_id" server/routers/docs.py`. The only remaining hits should be the `project_id` query parameter of `list_documents` and the filter code that uses it.

- [ ] **Step 14: Commit** (ask first)

```bash
git add server/sql/010_project_documents.sql server/auth/authz.py server/routers/docs.py \
  server/tests/dbutil.py server/tests/conftest.py server/tests/test_migration_010.py \
  server/tests/test_library_access_matrix.py server/tests/test_library_authz.py \
  server/tests/test_library_schema.py server/tests/test_docs_list.py server/tests/test_docs_patch.py
git commit -m "feat(library): many-to-many project_documents + project-owner read fix

Migration 010 replaces documents.project_id with a join table (backfilled,
column dropped in the same transaction). One read predicate now grants read
to owners of a linked project. List/status responses carry projects[]
limited to projects the caller can see. PATCH /v1/docs no longer accepts
project_id (422).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Link / unlink endpoints

**Files:**
- Modify: `server/routers/projects.py`
- Test: `server/tests/test_projects_router.py`

**Interfaces:**
- Consumes: `authz.visible_projects_where`, `authz.visible_projects_params`, `authz.assert_owns_doc(conn, doc_id, user_id)`, `docs.DocId`.
- Produces:
  - `PUT /v1/projects/{project_id}/docs/{doc_id}` → 204 | 404 | 422
  - `DELETE /v1/projects/{project_id}/docs/{doc_id}` → 204 | 404 | 422
  - Both require the `reader` capability.

- [ ] **Step 1: Write the failing tests.** Add `import pytest` to the imports at the top of `server/tests/test_projects_router.py`, then append:

```python
DOC_A = "a" * 64


def _reader(u, role="member"):
    return deps.Principal(user_id=u["id"], email=u["email"], role=role,
                          capabilities=frozenset({"reader"}))


async def _mk_project(db_conn, owner_id, name="P"):
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id",
        (owner_id, name))
    return str((await cur.fetchone())[0])


async def _mk_doc(db_conn, doc_id, owner_id):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f.pdf','pdf',1,%s)", (doc_id, owner_id))


async def _linked(db_conn, pid, doc_id):
    cur = await db_conn.execute(
        "SELECT 1 FROM project_documents WHERE project_id=%s AND doc_id=%s", (pid, doc_id))
    return await cur.fetchone() is not None


def _client_for(db_conn, principal):
    return httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, principal)),
                             base_url="http://t")


async def test_link_own_doc_into_own_project(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l1", email="l1@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204  # idempotent
    assert await _linked(db_conn, pid, DOC_A)


async def test_link_own_doc_into_project_where_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="l2o", email="l2o@x.io")
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l2", email="l2@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, u["id"]))
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204


async def test_link_into_invisible_project_is_404(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="l3o", email="l3o@x.io")
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l3", email="l3@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")
        assert r.status_code == 404 and r.json()["detail"] == "Project not found"
    assert not await _linked(db_conn, pid, DOC_A)


async def test_link_foreign_doc_is_404(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l4", email="l4@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="l4x", email="l4x@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, other["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")
        assert r.status_code == 404 and r.json()["detail"] == "Document not found"


async def test_project_owner_cannot_link_a_granted_doc(db_conn):
    # Deliberate asymmetry (spec §6): project owners remove, but only doc owners add.
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l5", email="l5@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="l5x", email="l5x@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, other["id"])
    await db_conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s)", (DOC_A, u["id"]))
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 404
    assert not await _linked(db_conn, pid, DOC_A)


async def test_admin_links_own_doc_into_any_project(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="l6o", email="l6o@x.io")
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="l6", email="l6@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await _mk_doc(db_conn, DOC_A, admin["id"])
    async with _client_for(db_conn, _reader(admin, role="admin")) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204


@pytest.mark.parametrize("path", [
    f"/v1/projects/not-a-uuid/docs/{DOC_A}",
    "/v1/projects/00000000-0000-0000-0000-000000000001/docs/NOT-HEX",
])
async def test_link_malformed_ids_are_422(db_conn, path):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l7", email="l7@x.io")
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(path)).status_code == 422
        assert (await c.delete(path)).status_code == 422


# Unlink resolution table (spec §6):
#   link exists  -> doc owner 204, project owner 204, other 404
#   link missing -> doc owner 204, project owner 404, other 404
@pytest.mark.parametrize("linked,caller,expected", [
    (True, "doc_owner", 204), (True, "project_owner", 204), (True, "other", 404),
    (False, "doc_owner", 204), (False, "project_owner", 404), (False, "other", 404),
])
async def test_unlink_resolution_table(db_conn, linked, caller, expected):
    doc_owner = await resolve_or_provision_user(db_conn, iss="i", sub="u1", email="u1@x.io")
    proj_owner = await resolve_or_provision_user(db_conn, iss="i", sub="u2", email="u2@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="u3", email="u3@x.io")
    pid = await _mk_project(db_conn, proj_owner["id"])
    # `other` is a plain member: members must not unlink someone else's doc.
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s),(%s,%s)",
        (pid, doc_owner["id"], pid, other["id"]))
    await _mk_doc(db_conn, DOC_A, doc_owner["id"])
    if linked:
        await db_conn.execute(
            "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, DOC_A))
    who = {"doc_owner": doc_owner, "project_owner": proj_owner, "other": other}[caller]
    async with _client_for(db_conn, _reader(who)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == expected
    assert await _linked(db_conn, pid, DOC_A) is (linked and expected == 404)


async def test_doc_owner_who_left_the_project_can_still_unlink(db_conn):
    # Review Focus 2: projects[] hides this project from them now, but the
    # API must still let the doc's owner withdraw it.
    doc_owner = await resolve_or_provision_user(db_conn, iss="i", sub="rf2", email="rf2@x.io")
    proj_owner = await resolve_or_provision_user(db_conn, iss="i", sub="rf2p", email="rf2p@x.io")
    pid = await _mk_project(db_conn, proj_owner["id"])
    await _mk_doc(db_conn, DOC_A, doc_owner["id"])
    await db_conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, DOC_A))
    async with _client_for(db_conn, _reader(doc_owner)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
    assert not await _linked(db_conn, pid, DOC_A)


async def test_deleting_project_keeps_doc_and_drops_link(db_conn):
    # Review Focus 3.
    u = await resolve_or_provision_user(db_conn, iss="i", sub="rf3", email="rf3@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
        assert (await c.delete(f"/v1/projects/{pid}")).status_code == 204
    assert not await _linked(db_conn, pid, DOC_A)
    cur = await db_conn.execute("SELECT 1 FROM documents WHERE doc_id=%s", (DOC_A,))
    assert await cur.fetchone() is not None


async def test_new_owner_after_reassignment_can_unlink(db_conn):
    # Review Focus 4: links survive an admin ownership reassignment.
    old = await resolve_or_provision_user(db_conn, iss="i", sub="rf4a", email="rf4a@x.io")
    new = await resolve_or_provision_user(db_conn, iss="i", sub="rf4b", email="rf4b@x.io")
    pid = await _mk_project(db_conn, old["id"])
    await _mk_doc(db_conn, DOC_A, old["id"])
    await db_conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, DOC_A))
    await db_conn.execute("UPDATE documents SET user_id=%s WHERE doc_id=%s", (new["id"], DOC_A))
    async with _client_for(db_conn, _reader(new)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
    assert not await _linked(db_conn, pid, DOC_A)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py -v`
Expected: the new tests FAIL on status mismatches (`405 Method Not Allowed` for the missing routes). The existing tests PASS.

- [ ] **Step 3: Implement.** In `server/routers/projects.py` add imports:

```python
from ..auth.authz import assert_owns_doc, visible_projects_params, visible_projects_where
from .docs import DocId
```

and append:

```python
@router.put("/{project_id}/docs/{doc_id}", status_code=204)
async def link_doc(project_id: uuid.UUID, doc_id: DocId,
                   principal: deps.Principal = Depends(deps.require_capability("reader")),
                   conn=Depends(deps.get_conn)):
    """File a doc into a project. Caller must OWN the doc and be able to SEE
    the project (owner or member); admins skip the visibility check, as the
    old PATCH project_id path did. The project is checked first, so an
    invisible project 404s before doc ids can be used to probe it. Only doc
    owners add — a project owner can't pull in a doc they merely read (spec §6)."""
    if principal.role == "admin":
        cur = await conn.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,))
    else:
        cur = await conn.execute(
            f"SELECT 1 FROM projects p WHERE p.id = %s AND {visible_projects_where('p')}",
            [project_id, *visible_projects_params(principal.user_id)])
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await assert_owns_doc(conn, doc_id, principal.user_id)
    await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING", (project_id, doc_id))
    return Response(status_code=204)


@router.delete("/{project_id}/docs/{doc_id}", status_code=204)
async def unlink_doc(project_id: uuid.UUID, doc_id: DocId,
                     principal: deps.Principal = Depends(deps.require_capability("reader")),
                     conn=Depends(deps.get_conn)):
    """Remove a doc from a project. The doc owner always gets 204 (idempotent,
    and it reveals nothing about the project). The project owner gets 204 only
    when the link exists — they can already see their own project's docs.
    Everyone else, including plain members, gets 404."""
    cur = await conn.execute("SELECT user_id FROM documents WHERE doc_id = %s", (doc_id,))
    row = await cur.fetchone()
    if row is not None and str(row[0]) == principal.user_id:
        await conn.execute(
            "DELETE FROM project_documents WHERE project_id = %s AND doc_id = %s",
            (project_id, doc_id))
        return Response(status_code=204)
    cur = await conn.execute(
        "DELETE FROM project_documents pd USING projects p "
        "WHERE pd.project_id = %s AND pd.doc_id = %s "
        "AND p.id = pd.project_id AND p.owner_user_id = %s",
        (project_id, doc_id, principal.user_id))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(status_code=204)
```

Also update the module docstring to: `"""/v1/projects — grouping + primary sharing unit. Owner-only project writes; listing returns owned + member-of. Doc links: doc owners add, doc owners or project owners remove. Not-permitted is 404 (no existence leak)."""`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py -v`
Expected: all PASS. Then run the full suite, `.venv/bin/python -m pytest server/tests`. Expected: all PASS.

- [ ] **Step 5: Commit** (ask first)

```bash
git add server/routers/projects.py server/tests/test_projects_router.py
git commit -m "feat(projects): link/unlink documents to projects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Register flow links via `PUT`

**Files:**
- Modify: `src/lib/docMeta.js:31-72`
- Test: `src/lib/docMeta.test.js`

**Interfaces:**
- Consumes: `PUT /v1/projects/{project_id}/docs/{doc_id}` (Task 3), `PATCH /v1/docs/{doc_id}` with `{tags}` only (Task 2).
- Produces: `registerDocument({apiHost, apiPort, docId, fileName, fileType, sizeBytes, pageCount, projectId, tags})`, same signature. It returns the register response and throws only when register fails.

- [ ] **Step 1: Write the failing tests.** In `src/lib/docMeta.test.js`, replace the three tests `issues a PATCH carrying project_id and tags after a chosen project + tags`, `issues a PATCH with only project_id when tags are empty` and `throws when the register call fails, without attempting a PATCH` with:

```js
  it('PATCHes tags and PUTs the project link after register', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/docs/d1' && opts?.method === 'PATCH') return json(200, { doc_id: 'd1' });
      if (path === '/v1/projects/p1/docs/d1' && opts?.method === 'PUT') return json(204, {});
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, projectId: 'p1', tags: ['a', 'b'] });

    const [postCall, patchCall, putCall] = apiFetch.mock.calls;
    expect(apiFetch).toHaveBeenCalledTimes(3);
    expect(postCall[2]).toBe('/v1/docs');
    expect(patchCall[2]).toBe('/v1/docs/d1');
    expect(JSON.parse(patchCall[3].body)).toEqual({ tags: ['a', 'b'] });
    expect(putCall[2]).toBe('/v1/projects/p1/docs/d1');
    expect(putCall[3].method).toBe('PUT');
  });

  it('only PUTs the link when tags are empty (no PATCH)', async () => {
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/projects/p1/docs/d1' && opts?.method === 'PUT') return json(204, {});
      return json(404, {});
    });

    await registerDocument({ ...baseArgs, projectId: 'p1', tags: [] });

    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(apiFetch.mock.calls[1][2]).toBe('/v1/projects/p1/docs/d1');
    expect(apiFetch.mock.calls.some(([, , , o]) => o?.method === 'PATCH')).toBe(false);
  });

  it('logs but does not throw when the link fails, and still sends tags', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    apiFetch.mockImplementation(async (host, port, path, opts) => {
      if (path === '/v1/docs' && opts?.method === 'POST') return json(200, { doc_id: 'd1' });
      if (path === '/v1/docs/d1' && opts?.method === 'PATCH') return json(200, { doc_id: 'd1' });
      if (opts?.method === 'PUT') return json(404, {});
      return json(404, {});
    });

    await expect(registerDocument({ ...baseArgs, projectId: 'p1', tags: ['a'] })).resolves.toBeDefined();
    expect(apiFetch.mock.calls.some(([, , p, o]) => p === '/v1/docs/d1' && o?.method === 'PATCH')).toBe(true);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('throws when the register call fails, without attempting follow-ups', async () => {
    apiFetch.mockResolvedValue(json(500, {}));
    await expect(registerDocument({ ...baseArgs, projectId: 'p1', tags: ['a'] })).rejects.toThrow('HTTP 500');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
```

Keep `issues NO PATCH when neither a project nor tags were chosen` and the `parseTagsInput` tests unchanged.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/lib/docMeta.test.js`
Expected: the new link tests FAIL. The PATCH body still carries `project_id`, and no `PUT` is sent.

- [ ] **Step 3: Implement.** In `src/lib/docMeta.js`, replace the block from `const hasProject = !!projectId;` through the closing brace of `if (hasProject || hasTags) { … }` with:

```js
    const hasProject = !!projectId;
    const hasTags = Array.isArray(tags) && tags.length > 0;
    // Two independent follow-ups, each fail-soft: a failed tag PATCH must not
    // skip the project link, and neither may block the indexing/conversion
    // that runs right after register.
    if (hasTags) {
        try {
            const patchRes = await apiFetch(apiHost, apiPort, `/v1/docs/${encodeURIComponent(docId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tags }),
            });
            if (!patchRes.ok) throw new Error(`HTTP ${patchRes.status}`);
        } catch (e) {
            console.error('Doc tags PATCH failed:', e);
        }
    }
    if (hasProject) {
        try {
            const linkRes = await apiFetch(
                apiHost, apiPort,
                `/v1/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(docId)}`,
                { method: 'PUT' },
            );
            if (!linkRes.ok) throw new Error(`HTTP ${linkRes.status}`);
        } catch (e) {
            console.error('Doc project link failed:', e);
        }
    }
```

In the doc comment above `registerDocument`, change `Skips the PATCH entirely (no extra request) when neither \`projectId\` nor \`tags\` is set` to `Sends no follow-up request at all when neither \`projectId\` nor \`tags\` is set`, and `A failed metadata PATCH is logged but does not throw` to `A failed tag PATCH or project link is logged but does not throw`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/lib/docMeta.test.js`
Expected: all PASS.

- [ ] **Step 5: Commit** (ask first)

```bash
git add src/lib/docMeta.js src/lib/docMeta.test.js
git commit -m "feat(library): register flow links the chosen project via PUT

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Library project chips

**Files:**
- Modify: `src/components/library/LibraryPage.jsx`
- Test: `src/components/library/LibraryPage.test.jsx`

**Interfaces:**
- Consumes: `GET /v1/docs` rows with `projects: [{id, name}]` (Task 2); `GET /v1/projects` rows with `is_owner`; `PUT`/`DELETE /v1/projects/{id}/docs/{doc_id}` (Task 3).
- Produces: nothing new for other tasks.

- [ ] **Step 1: Write the failing tests.** In `src/components/library/LibraryPage.test.jsx` replace the `docs` and `projects` fixtures with:

```js
const docs = [
  {
    doc_id: 'd1', file_name: 'Owned.pdf', state: 'indexed', tags: ['x'],
    projects: [], owner_user_id: 'me', is_owner: true,
  },
  {
    doc_id: 'd2', file_name: 'Teammate.pdf', state: 'indexed', tags: [],
    projects: [{ id: 'p1', name: 'Project A' }, { id: 'p2', name: 'Project B' }],
    owner_user_id: 'other', is_owner: false,
  },
];

const projects = [
  { id: 'p1', owner_user_id: 'me', name: 'Project A', description: null, is_owner: true },
  { id: 'p2', owner_user_id: 'other', name: 'Project B', description: null, is_owner: false },
];
```

In `mount`, return `json(204, {})` for link calls by adding this line before the final `return json(404, {})`:

```js
    if (path.startsWith('/v1/projects/')) return json(204, {});
```

(`mount` checks `/v1/projects` with `===` before this, so the project list still resolves.) In the test `has no tag-edit or reassign affordance on a shared row`, replace `/reassign project/i` with `/add teammate\.pdf to project/i`. Then append:

```js
  it('renders one chip per linked project', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getByText('Project A')).toBeInTheDocument();
    expect(within(row).getByText('Project B')).toBeInTheDocument();
  });

  it('PUTs a link when the owner picks a project from the add select', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const row = screen.getByTestId('doc-row-d1');
    fireEvent.change(within(row).getByLabelText(/add owned\.pdf to project/i), { target: { value: 'p2' } });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p2/docs/d1', { method: 'PUT' }));
  });

  it('shows × on a shared doc only for the chip of a project the caller owns', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const row = screen.getByTestId('doc-row-d2');
    expect(within(row).getByLabelText(/remove teammate\.pdf from project a/i)).toBeInTheDocument();
    expect(within(row).queryByLabelText(/remove teammate\.pdf from project b/i)).toBeNull();
  });

  it('DELETEs the link and reloads the list when × is clicked', async () => {
    mount();
    await screen.findByText('Teammate.pdf');
    const before = docsCalls().length;
    fireEvent.click(within(screen.getByTestId('doc-row-d2')).getByLabelText(/remove teammate\.pdf from project a/i));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('', '', '/v1/projects/p1/docs/d2', { method: 'DELETE' }));
    await waitFor(() => expect(docsCalls().length).toBeGreaterThan(before));
  });

  it('offers only unlinked projects in the add select', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    const select = within(screen.getByTestId('doc-row-d1')).getByLabelText(/add owned\.pdf to project/i);
    const values = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    expect(values).toEqual(['', 'p1', 'p2']);
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/library/LibraryPage.test.jsx`
Expected: the new tests FAIL because no chips, add select or × labels exist yet.

- [ ] **Step 3: Implement.** In `LibraryPage.jsx` add this component above the `LibraryPage` doc comment (after `TagEditor`):

```jsx
// One chip per linked project. The owner of the doc can add it to any
// project they can see and remove it from any; a project owner can remove
// someone else's doc from THEIR project (never add it — spec §6).
function ProjectChips({ doc, projects, theme, onLink, onUnlink }) {
  const linked = doc.projects || [];
  const linkedIds = new Set(linked.map((p) => p.id));
  const ownedProjectIds = new Set((projects || []).filter((p) => p.is_owner).map((p) => p.id));
  const addable = (projects || []).filter((p) => !linkedIds.has(p.id));

  return (
    <div className="flex flex-wrap items-center gap-1">
      <FolderOpen size={10} className={theme.textSecondary} />
      {linked.length === 0 && <span className={theme.textSecondary}>No project</span>}
      {linked.map((p) => (
        <span
          key={p.id}
          className={`flex items-center gap-1 px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}
        >
          {p.name}
          {(doc.is_owner || ownedProjectIds.has(p.id)) && (
            <button
              onClick={() => onUnlink(doc, p)}
              aria-label={`Remove ${doc.file_name} from ${p.name}`}
              className="hover:text-red-500"
            >
              <X size={10} />
            </button>
          )}
        </span>
      ))}
      {doc.is_owner && addable.length > 0 && (
        <select
          value=""
          onChange={(e) => { if (e.target.value) onLink(doc, e.target.value); }}
          aria-label={`Add ${doc.file_name} to project`}
          className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg}`}
        >
          <option value="">+ project</option>
          {addable.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      )}
    </div>
  );
}
```

Inside `LibraryPage`, after `patchDoc`, add:

```jsx
  const changeLink = async (doc, projectId, method) => {
    try {
      const res = await apiFetch(apiHost, apiPort, `/v1/projects/${projectId}/docs/${doc.doc_id}`, { method });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadDocs(search, projectFilter);
    } catch (e) {
      showToast(`Update failed: ${e.message}`, 5000);
    }
  };
  const linkDoc = (doc, projectId) => changeLink(doc, projectId, 'PUT');
  const unlinkDoc = (doc, project) => changeLink(doc, project.id, 'DELETE');
```

Replace the project block in the row, the `<div className="flex flex-wrap items-center gap-3 text-[10px]">` containing the `doc.project_name` span and the `Reassign project` select, with:

```jsx
                  <div className="text-[10px]">
                    <ProjectChips
                      doc={doc}
                      projects={projects}
                      theme={theme}
                      onLink={linkDoc}
                      onUnlink={unlinkDoc}
                    />
                  </div>
```

In the `LibraryPage` doc comment, replace `Owner rows get inline tag + project-reassign affordances` with `Owner rows get inline tag editing and project chips (add/remove); project owners can also remove others' docs from their projects`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/components/library/`
Before running, in `src/components/library/LibraryPage.delete-busy.test.jsx` line 17 replace `project_id: null, project_name: null,` with `projects: [],` so its fixture matches the new list shape.

Expected: all PASS, including `LibraryPage.delete-busy.test.jsx`.

- [ ] **Step 5: Run the whole frontend suite and lint**

Run: `npm run test:run && npm run lint`
Expected: all PASS, no lint errors.

- [ ] **Step 6: Commit** (ask first)

```bash
git add src/components/library/
git commit -m "feat(library): project chips with add/remove per document

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, query-cost check, final verification

**Files:**
- Modify: `docs/LIBRARY.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md`

- [ ] **Step 1: `docs/LIBRARY.md`.**
  - In the access-path table (line ~22), change the "Project member" row's Table cell to `project_members` + `project_documents` and its Meaning to `You were added to a project the doc is linked to. Read-only.`, and add a row after it:
    `| Project owner | projects.owner_user_id + project_documents | You own a project the doc is linked to. Read-only (plus remove-from-project). |`
  - In "Schema (migration 009)", add a paragraph: `Migration 010 replaced documents.project_id with project_documents(project_id, doc_id, added_at), PK (project_id, doc_id), index on doc_id. A document can be in any number of projects.`
  - In the documents API table, change the `GET /v1/docs` Notes to return `{doc_id, file_name, state, tags, projects: [{id, name}], owner_user_id, is_owner}`, and add that `projects` lists projects the caller owns or belongs to (a doc's owner sees all of its links, including projects they have left), and that the `project_id` filter only accepts projects the caller owns or belongs to. Change the `PATCH` row to `owner (tags) · admin (owner)`, with Notes `Owner sets tags; admin-only sets owner_user_id. project_id is rejected (422) — use the project link routes.`
  - In the projects API table, add:
    `| PUT /v1/projects/{id}/docs/{doc_id} | doc owner who can see the project (admins: any project) | Link (204, idempotent). Invisible project → 404 "Project not found"; not your doc → 404 "Document not found". |`
    `| DELETE /v1/projects/{id}/docs/{doc_id} | doc owner, or project owner if linked | Unlink (204). Doc owner always 204; project owner 404 when not linked; anyone else 404. |`
    Also change the `DELETE /v1/projects/{id}` Notes from "un-files its docs (`ON DELETE SET NULL`)" to "removes its doc links; the documents survive".
  - Add a short section before "What's next":

```markdown
## Decision: curation is asymmetric

A project owner can **remove** anyone's document from their project but can only
**add** documents they own. Adding a doc you merely read (say, one granted to you)
would be coherent too — read access already spreads through membership — but C0
keeps the least-privilege rule that matched the old `PATCH project_id`. This is
deliberate, not a bug. Relaxing it is a subsystem A question, and would bring
back an `added_by` column (it carries no information while only doc owners link).
```

- [ ] **Step 2: `docs/ARCHITECTURE.md`.** In the migrations paragraph (line ~173), append after the `009` entry: `, \`010\` (\`project_documents\` join table — many-to-many projects↔documents, backfilled from and replacing \`documents.project_id\`)`. In the `projects.py` bullet (line ~150), add `and project↔document links (doc owners add; doc or project owners remove)`. In the `docs.py` bullet, add a sentence: `Read access for every read path is the single predicate in \`server/auth/authz.py\` (\`readable_docs_where\` + \`readable_docs_params\`): owner, grantee, or owner/member of any linked project.`

- [ ] **Step 3: `CHANGELOG.md`.** Under `## [Unreleased]` add:

```markdown
### Added

- **Documents can belong to several projects.** New `project_documents` join table
  (migration `010`, backfilled from `documents.project_id`, which is dropped).
  Link/unlink with `PUT`/`DELETE /v1/projects/{id}/docs/{doc_id}`; the Library shows
  one chip per project with add/remove.

### Fixed

- **Project owners can read documents members file into their project.** The read
  check only looked at `project_members`, and owners never get a membership row.
  "Can read" is now one SQL definition shared by every read path.

### Changed

- **Breaking:** `PATCH /v1/docs/{id}` no longer accepts `project_id` (422), which
  also fails any tag change sent in the same request. `GET /v1/docs` and document
  status responses return `projects: [{id, name}]` (projects you can see; a doc's
  owner sees all of its links) instead of `project_id`/`project_name`.
```

- [ ] **Step 4: Check the cost of the read predicate.** Save this as `explain_library.py` in the session scratchpad directory and run it with `.venv/bin/python <path>`. It seeds 200 users, 200 projects, 20k documents and 20k links inside a transaction, prints `EXPLAIN ANALYZE` for the real list query, and rolls back:

```python
import asyncio

from psycopg import AsyncConnection

from server.auth.authz import readable_docs_params, readable_docs_where
from server.routers.docs import _doc_projects_params, _doc_projects_sql
from server.tests.dbutil import TEST_URL

SEED = """
INSERT INTO users (oidc_iss, oidc_sub, email, role, status)
  SELECT 'perf', 'u'||g, 'u'||g||'@perf.io', 'member', 'active' FROM generate_series(1,200) g;
CREATE TEMP TABLE pu AS SELECT id, row_number() OVER (ORDER BY id) - 1 AS n
  FROM users WHERE oidc_iss = 'perf';
INSERT INTO projects (owner_user_id, name) SELECT id, 'proj-'||n FROM pu;
CREATE TEMP TABLE pp AS SELECT id, row_number() OVER (ORDER BY id) - 1 AS n
  FROM projects WHERE name LIKE 'proj-%';
INSERT INTO project_members (project_id, user_id)
  SELECT pp.id, pu.id FROM pp JOIN pu ON (pu.n + pp.n) % 20 = 0 AND pu.n <> pp.n;
INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id)
  SELECT encode(sha256(('perf'||g)::bytea),'hex'), 'doc'||g||'.pdf', 'pdf', 1, pu.id
  FROM generate_series(1,20000) g JOIN pu ON pu.n = g % 200;
INSERT INTO project_documents (project_id, doc_id)
  SELECT pp.id, encode(sha256(('perf'||g)::bytea),'hex')
  FROM generate_series(1,20000) g JOIN pp ON pp.n = (g * 7) % 200;
ANALYZE users; ANALYZE projects; ANALYZE project_members;
ANALYZE documents; ANALYZE project_documents; ANALYZE doc_grants;
"""


async def main():
    conn = await AsyncConnection.connect(TEST_URL)
    try:
        await conn.execute(SEED)
        cur = await conn.execute("SELECT id FROM pu WHERE n = 7")
        uid = str((await cur.fetchone())[0])
        sql = (
            "EXPLAIN (ANALYZE, BUFFERS) SELECT d.doc_id, d.file_name, d.state, d.tags, "
            f"d.user_id, {_doc_projects_sql('d')} AS projects FROM documents d "
            f"WHERE {readable_docs_where('d')} ORDER BY d.updated_at DESC"
        )
        cur = await conn.execute(sql, [*_doc_projects_params(uid), *readable_docs_params(uid)])
        for (line,) in await cur.fetchall():
            print(line)
    finally:
        await conn.rollback()
        await conn.close()


asyncio.run(main())
```

Expected: the final `Execution Time` is **under 100 ms**, and the plan shows index use on `project_documents_doc_idx` / `project_members_pkey` (or `project_members_user_idx`) rather than a per-row sequential scan of `project_documents`. Record the time and the top plan node in the handoff. If it is over 100 ms, stop and report the plan output. Don't tune without the user.

- [ ] **Step 5: Final verification**

Run: `.venv/bin/python -m pytest server/tests && npm run test:run && npm run lint`
Expected: every suite PASSES. Report the pass counts.

- [ ] **Step 6: Commit** (ask first)

```bash
git add docs/LIBRARY.md docs/ARCHITECTURE.md CHANGELOG.md
git commit -m "docs(library): many-to-many projects, link routes, curation decision

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
