# A0: Projects, Roles & People Directory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teams manage projects entirely from the UI: GitLab-style roles (Reader < Contributor < Maintainer < Owner), a people directory to find colleagues, a Members screen, per-document sharing, an activity history per project, and admin recovery of ownerless projects.

**Architecture:** Migration 017 makes `project_members` (with a `role`) the only source of project access and adds `project_events` (one row per change, written in the request's transaction). A new authz seam (`require_project_role`, `can_for`) decides every project action once on the server, and the API hands the UI a `can` object, so the screens never re-derive rules. The UI adds a Projects tab in the Library with a project page (Documents, Members, Activity), a shared people picker, a Share dialog, and an admin Projects section.

**Tech Stack:** FastAPI + psycopg 3 (async) on Postgres 16; pytest (asyncio_mode=auto, real Postgres on port 5433); React + Vite + vitest + Testing Library; Tailwind classes via the `theme` prop.

**Spec:** `docs/superpowers/specs/2026-09-25-projects-roles-directory-design.md` (approved 2026-10-06). Section references below (§N) are to that spec.

## Global Constraints

- Branch `feat/c2-multi-document`. Python 3.12–3.13 (`.venv/bin/python`).
- **Commits:** the user's global rule is "never commit without explicit, per-action permission". Every "Commit" step means: stage the listed files, show the message, and **ask before running `git commit`** unless the user has approved commit-as-you-go for this plan. Never push.
- Nothing deployment-specific is hardcoded. Every new setting is an env var with a safe default and a commented `.env.example` line (§12). Docs use example.com.
- **No personal data at INFO or above:** log user/project IDs, roles, counts — never emails, names, file names or lookup text (§7).
- Refusals use `server.http_errors.refusal(status, error, message, **extra)` → `{"detail": {"error", "message", ...}}`.
- Not a member → 404 (`not_found`, message "This project doesn't exist or you don't have access."). Member with too low a role → 403 `{"error": "insufficient_role", "required": "<role>"}`. The role check runs **before** any other validation of path ids (§6).
- Every membership write and project delete takes `SELECT … FROM projects WHERE id=%s FOR UPDATE` first and reads the caller's role after it (§3.3).
- Each request runs in one database transaction (pooled connections are not autocommit; the pool commits on a clean exit). Write the `project_events` row in the handler, never in a background task.
- Every new migration file ends with `INSERT INTO schema_migrations(version) VALUES (N) ON CONFLICT DO NOTHING;` (the runner does not record versions itself).
- Frontend calls go through `apiFetch` (`src/utils/apiFetch`); refusals become notices through `describeRefusal` (`src/lib/apiErrors.js`); never show raw `HTTP nnn`.
- Test commands: backend `.venv/bin/python -m pytest <paths> -q`; frontend `npx vitest run <paths>`; lint `npx eslint src`. Postgres must be running (`env -u XDG_DATA_HOME podman start natural-reader-postgres`).
- Whole-suite gate before a task's completion: `.venv/bin/python -m pytest server/tests -q` (baseline: 0 failures at 6edca75) and, for frontend tasks, `npx vitest run` + `npx eslint src`.

## Review Focus

1. **Stale permissions on an open project page.** Someone else demotes or removes you while your project page is open; your next click must show the server's notice (403/404) and then reload the project (or return to the list on 404), never leave buttons that keep failing. Pinned in Task 12 (`ProjectPage reloads after a refusal`).
2. **LIKE wildcards in the people lookup.** `q="%%"` or `q="a_"` must match literally, not list everyone (a directory leak in `domain`/`open` mode, and in `exact` mode via the type-ahead path). Pinned in Task 3 (`test_lookup_escapes_like_wildcards`).
3. **Whitespace-only names.** `PATCH {"name": "   "}` or `POST {"name": " "}` must be a 422, not a project with a blank title the UI can't click. Pinned in Task 5 (`test_blank_project_name_is_422`).
4. **An admin who isn't a member opens a project page.** Documents tab must explain "Only members can see this project's documents" instead of an empty list or an error; File/Remove controls must be absent. Pinned in Task 12 (`admin non-member sees the members-only note`).
5. **Double-click on membership controls.** Add, role change, remove and Leave disable their control while a request is in flight, so a second click can't fire a second write (which would surface a false `last_owner` or 404). Pinned in Task 13 (`disables the role menu while the change is in flight`).

## File map

| File | Responsibility |
|---|---|
| `server/sql/017_project_roles.sql` (new) | Roles, names, limits, `created_by`, `project_events`, backfill |
| `server/sql/014_*.sql`, `015_*.sql`, `016_*.sql` | One line each: record their version |
| `server/auth/authz.py` | Membership-only predicates; `ROLES`, `role_at_least`, `project_role`, `require_project_role`, `ProjectAccess`, `can_for` |
| `server/services/people.py` (new) | Directory config, `person_label`, `person_cols`, `person_from`, `lookup` |
| `server/routers/people.py` (new) | `GET /v1/users/lookup` |
| `server/services/project_events.py` (new) | `KINDS`, `record`, `page`, `retention_days`, `purge`, retention task |
| `server/services/project_policy.py` (new) | `PROJECT_CREATION`, `PROJECT_LIMIT_PER_USER`, `check_can_create` |
| `server/services/project_members.py` (new) | `list_members`, `put_member`, `remove_member` (lock, last-owner, events) |
| `server/routers/projects.py` | Project object, CRUD, members, documents, events routes |
| `server/routers/docs.py` | `GET /{doc_id}/shares`; disabled-user share refusal |
| `server/routers/admin.py`, `server/auth/users.py`, `server/auth/kc_admin.py`, `server/routers/auth.py` | Names at login and enroll, `project_limit`, admin project list |
| `server/services/doc_content.py` | `docs_referenced_by_user` loses the owned-projects branch |
| `server/app.py` | Register `people` router; start/stop event retention |
| `server/tests/seed.py` | `make_project`, `add_member` |
| `src/lib/apiErrors.js` | A0 notices |
| `src/lib/projectsApi.js` (new) | One function per project/people/share call |
| `src/components/people/PeoplePicker.jsx` (new) | Debounced lookup + pick |
| `src/components/library/ProjectsTab.jsx` (new) | Project cards |
| `src/components/projects/ProjectPage.jsx` (new) | Header, tabs, edit/leave/delete |
| `src/components/projects/ProjectDocsTab.jsx` (new) | Project documents, file, remove |
| `src/components/projects/MembersTab.jsx` (new) | Members, roles, add people |
| `src/components/projects/ActivityTab.jsx` (new) | Event feed, `describeEvent`, `timeAgo` |
| `src/components/library/ShareDialog.jsx` (new) | Share one document with people |
| `src/components/admin/AdminProjectsSection.jsx` (new) | All projects, ownerless filter, recovery |
| `src/components/library/LibraryPage.jsx` | Tabs, `can`-driven chips, Share button, project page |
| `src/components/admin/AdminConsole.jsx` | Projects section, project limit field, enroll names |
| `src/App.jsx` | `currentUserId` to Library; upload picker lists `can.file_docs` projects |
| `.env.example`, `CHANGELOG.md`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md` | §12, §13 |

---

### Task 1: Migration 017 and the membership-only schema switch

The migration renames `projects.owner_user_id`, so every query and test that reads it changes in this one task. The public API keeps its current shape until Task 5 (`is_owner` is now derived from the Owner role).

**Files:**
- Create: `server/sql/017_project_roles.sql`
- Modify: `server/sql/014_embedding_profile.sql`, `server/sql/015_chunk_text_search.sql`, `server/sql/016_app_settings.sql` (append one line each)
- Create: `server/tests/test_migration_017.py`
- Modify: `server/tests/seed.py` (add `make_project`, `add_member`)
- Modify: `server/auth/authz.py` (`readable_docs_where`, `readable_docs_params`, `visible_projects_where`, `visible_projects_params`, `can_manage_project_docs`)
- Modify: `server/routers/projects.py` (`_row`, `_assert_owner`, `create_project`, `list_projects`, `patch_project`, `add_member`)
- Modify: `server/services/doc_content.py:133-143` (`docs_referenced_by_user`)
- Modify: `server/auth/users.py:193-198` and `server/routers/admin.py:222-226` (docstrings only)
- Modify tests that create projects or members with raw SQL: `test_admin_lifecycle.py`, `test_content_ops.py`, `test_doc_content.py`, `test_doc_pipeline.py`, `test_docs_file.py`, `test_docs_list.py`, `test_docs_patch.py`, `test_entry_verification.py`, `test_library_access_matrix.py`, `test_library_authz.py`, `test_library_schema.py`, `test_projects_router.py`

**Interfaces:**
- Produces: `seed.make_project(conn, owner_id, name="P", *, members=()) -> str` (project id); `seed.add_member(conn, project_id, user_id, role="contributor") -> None`; `visible_projects_params(user_id) -> [user_id]` (one param now); `readable_docs_params(user_id) -> [user_id, user_id]` (two params now).

- [ ] **Step 1: Write the failing migration tests**

Create `server/tests/test_migration_017.py`:

```python
"""Migration 017 (A0 §3): roles on memberships, people's names, project
limits and the activity feed. Membership becomes the only access source; a
project outlives its creator."""
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import NotNullViolation

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


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


async def test_017_backfills_roles_and_projects_outlive_their_creator():
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url, max_version=16)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            alice, bob, carol = [await _user(conn, s) for s in ("alice", "bob", "carol")]
            p1 = (await _one(conn, "INSERT INTO projects (owner_user_id, name) "
                                   "VALUES (%s,'P1') RETURNING id", (alice,)))[0]
            p2 = (await _one(conn, "INSERT INTO projects (owner_user_id, name) "
                                   "VALUES (%s,'P2') RETURNING id", (carol,)))[0]
            # alice owns P1 AND has a member row; bob is a plain member;
            # carol owns P2 with no member row (the pre-A0 two-sources case).
            await conn.execute(
                "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s),(%s,%s)",
                (p1, alice, p1, bob))

            async with conn.transaction():  # one transaction, like server/db.py
                await conn.execute((SQL_DIR / "017_project_roles.sql").read_text())

            cur = await conn.execute("SELECT project_id, user_id, role, added_by FROM project_members")
            rows = {(str(r[0]), str(r[1])): (r[2], r[3]) for r in await cur.fetchall()}
            assert rows[(str(p1), str(alice))][0] == "owner"
            assert rows[(str(p1), str(bob))][0] == "contributor"
            assert rows[(str(p2), str(carol))] == ("owner", carol)
            cur = await conn.execute(
                "SELECT project_id, kind, details->>'name', (details->>'backfilled')::bool "
                "FROM project_events")
            assert sorted((str(r[0]), r[1], r[2], r[3]) for r in await cur.fetchall()) == sorted(
                [(str(p1), "project.created", "P1", True), (str(p2), "project.created", "P2", True)])
            with pytest.raises(NotNullViolation):  # the backfill default is gone
                await conn.execute(
                    "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (p2, bob))
            # Deleting the creator keeps the project, ownerless, with its history.
            await conn.execute("DELETE FROM users WHERE id = %s", (carol,))
            assert await _one(conn, "SELECT created_by FROM projects WHERE id = %s", (p2,)) == (None,)
            assert (await _one(conn, "SELECT count(*) FROM project_members "
                                     "WHERE project_id = %s", (p2,)))[0] == 0
            assert (await _one(conn, "SELECT count(*) FROM project_events "
                                     "WHERE project_id = %s", (p2,)))[0] == 1
            await conn.execute(
                "UPDATE users SET username='al', first_name='Al', last_name='Ice', "
                "project_limit=3 WHERE id=%s", (alice,))
            assert (await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 17")) == (1,)
        finally:
            await conn.close()
    finally:
        await _drop(admin, name)


async def test_every_migration_records_its_version():
    # The runner (server/db.py) never records versions itself: a file that
    # forgets its INSERT re-runs on every startup. 017 is not idempotent
    # (a column rename), so a re-run would stop the server from starting.
    admin, name = await _scratch_db()
    try:
        url = url_for(name)
        await apply_migrations(url)
        conn = await AsyncConnection.connect(url, autocommit=True)
        try:
            cur = await conn.execute("SELECT version FROM schema_migrations")
            recorded = {r[0] for r in await cur.fetchall()}
        finally:
            await conn.close()
        files = {int(p.name.split("_", 1)[0]) for p in SQL_DIR.glob("*.sql")
                 if p.name.split("_", 1)[0].isdigit()}
        assert recorded == files
    finally:
        await _drop(admin, name)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_migration_017.py -q`
Expected: FAIL — `FileNotFoundError: …017_project_roles.sql` in the first test; the second fails with `recorded` missing 14, 15, 16.

- [ ] **Step 3: Write migration 017 and record 014–016**

Create `server/sql/017_project_roles.sql`:

```sql
-- A0 (spec docs/superpowers/specs/2026-09-25-projects-roles-directory-design.md §3):
-- GitLab-style roles on memberships, people's names, per-user project limits,
-- and the project activity feed. Membership becomes the ONLY source of
-- project access; a project's creator is history, not access.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS username      TEXT,
    ADD COLUMN IF NOT EXISTS first_name    TEXT,
    ADD COLUMN IF NOT EXISTS last_name     TEXT,
    ADD COLUMN IF NOT EXISTS project_limit INTEGER CHECK (project_limit >= 0);

ALTER TABLE project_members
    ADD COLUMN IF NOT EXISTS role      TEXT NOT NULL DEFAULT 'contributor'
        CHECK (role IN ('reader', 'contributor', 'maintainer', 'owner')),
    ADD COLUMN IF NOT EXISTS added_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS added_via TEXT NOT NULL DEFAULT 'member'
        CHECK (added_via IN ('member', 'admin'));

-- Every project's owner becomes an Owner member; an existing member row is raised.
INSERT INTO project_members (project_id, user_id, role, added_by, added_at)
SELECT id, owner_user_id, 'owner', owner_user_id, created_at FROM projects
ON CONFLICT (project_id, user_id) DO UPDATE SET role = 'owner';

-- The default existed for the backfill; from now on every row names its role.
ALTER TABLE project_members ALTER COLUMN role DROP DEFAULT;

ALTER TABLE projects RENAME COLUMN owner_user_id TO created_by;
ALTER TABLE projects ALTER COLUMN created_by DROP NOT NULL;
ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_owner_user_id_fkey;
ALTER TABLE projects ADD CONSTRAINT projects_created_by_fkey
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
-- Serves the per-user project-limit count.
CREATE INDEX IF NOT EXISTS projects_created_by_idx ON projects(created_by);

-- One row per change, written in the same transaction as the change (§3.2).
-- `kind` is validated in the app (server/services/project_events.py KINDS),
-- so later features add kinds without a migration. `doc_id` has no FK so
-- history survives the document.
CREATE TABLE IF NOT EXISTS project_events (
    id              BIGSERIAL PRIMARY KEY,
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL,
    subject_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    doc_id          TEXT,
    details         JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS project_events_feed_idx ON project_events(project_id, id DESC);
-- Serves the retention purge (PROJECT_EVENTS_RETENTION_DAYS).
CREATE INDEX IF NOT EXISTS project_events_at_idx ON project_events(at);

-- So an existing project's Activity tab isn't empty after the upgrade.
INSERT INTO project_events (project_id, at, actor_user_id, kind, details)
SELECT id, created_at, created_by, 'project.created',
       jsonb_build_object('name', name, 'backfilled', true)
FROM projects;

INSERT INTO schema_migrations(version) VALUES (17) ON CONFLICT DO NOTHING;
```

Append to the end of each of `server/sql/014_embedding_profile.sql`, `015_chunk_text_search.sql`, `016_app_settings.sql` (N = 14, 15, 16):

```sql

INSERT INTO schema_migrations(version) VALUES (N) ON CONFLICT DO NOTHING;
```

- [ ] **Step 4: Run the migration tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_migration_017.py -q`
Expected: 2 passed.

- [ ] **Step 5: Add the seed helpers**

Append to `server/tests/seed.py`:

```python
async def make_project(conn, owner_id, name="P", *, members=()):
    """A project created by `owner_id`, who is its Owner, plus `members` as
    (user_id, role) pairs. `owner_id=None` makes an ownerless project.
    Returns the project id as a string."""
    cur = await conn.execute(
        "INSERT INTO projects (created_by, name) VALUES (%s,%s) RETURNING id",
        (owner_id, name))
    pid = str((await cur.fetchone())[0])
    if owner_id is not None:
        await add_member(conn, pid, owner_id, "owner")
    for user_id, role in members:
        await add_member(conn, pid, user_id, role)
    return pid


async def add_member(conn, project_id, user_id, role="contributor"):
    """Membership with a role (A0); re-adding a member changes their role."""
    await conn.execute(
        "INSERT INTO project_members (project_id, user_id, role) VALUES (%s,%s,%s) "
        "ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role",
        (project_id, user_id, role))
```

- [ ] **Step 6: Switch the tests to the helpers**

In each test file listed under **Files** (except `test_migration_010/011/012.py`, which build pre-017 schemas on purpose, and `test_migration_017.py`), apply these rewrites:

1. Every `cur = await <conn>.execute("INSERT INTO projects (owner_user_id, name) VALUES (%s,<name>) RETURNING id", (<owner>,))` followed by `<var> = …(await cur.fetchone())[0]…` becomes `<var> = await seed.make_project(<conn>, <owner>, <name>)` (`<name>` is the literal (`'P'` → `"P"`) or the variable the old statement bound). Helper functions such as `_mk_project` in `test_projects_router.py`, `test_library_authz.py:30`, `test_docs_list.py:56` and `test_doc_content.py:14` keep their names and return `await seed.make_project(conn, owner, name)`.
2. Every `INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)` becomes `await seed.add_member(<conn>, <project>, <user>)`; a two-row `VALUES (%s,%s),(%s,%s)` becomes two calls.
3. Add `from server.tests import seed` where a file lacks it.

`test_library_schema.py:19` inserts the owner as a member and then counts members: with `make_project` the owner already has a row, so drop that insert and change the expected count to 1 (it was 1 before: the owner's explicit row). `test_library_authz.py:101-110` ("the owner of a project has no project_members row"): keep the test, change its comment to "A0: owners are members; the read predicate has one branch".

Then verify nothing raw is left:

Run: `grep -rn "owner_user_id\|INSERT INTO project_members\|INSERT INTO projects" server/tests | grep -v "test_migration_01[0-7]\|seed.py"`
Expected: no output.

- [ ] **Step 7: Rewrite the lifecycle test for "projects outlive their creator"**

In `server/tests/test_admin_lifecycle.py`, replace `test_delete_user_gcs_sole_held_content` with:

```python
async def test_delete_user_gcs_sole_held_content_and_leaves_their_project(db_conn, tmp_path):
    # A1 §3 + A0 §3.1: deleting a user drops their entries; content goes only
    # if nobody else holds it. Their project survives (ownerless), so its
    # placements still hold their content.
    admin, p = await _admin(db_conn)
    victim = await _make_user(db_conn, "b@x.io", status="active")
    victims_project = await seed.make_project(db_conn, victim, "Theirs")

    files = {}
    for name in ("sole", "co_held", "admins"):
        files[name] = tmp_path / f"{name}.pdf"
        files[name].write_bytes(name.encode())
    await seed.seed_doc(db_conn, "d1", victim, bytes_path=files["sole"])
    await seed.seed_doc(db_conn, "d2", victim, bytes_path=files["co_held"])
    await seed.share_doc(db_conn, "d2", victim, admin["id"])  # someone else holds it too
    await seed.seed_doc(db_conn, "d3", None, project_ids=[victims_project])  # placement only
    await seed.seed_doc(db_conn, "d4", admin["id"], bytes_path=files["admins"])

    async with _client(_app(db_conn, p)) as client:
        r = await client.delete(f"/v1/admin/users/{victim}")
    assert r.status_code == 204

    cur = await db_conn.execute(
        "SELECT doc_id FROM documents WHERE doc_id IN ('d1','d2','d3','d4')")
    assert {r[0] for r in await cur.fetchall()} == {"d2", "d3", "d4"}
    assert not files["sole"].exists()
    assert files["co_held"].exists()
    assert files["admins"].exists()
    cur = await db_conn.execute(
        "SELECT created_by, (SELECT count(*) FROM project_members m WHERE m.project_id = p.id) "
        "FROM projects p WHERE id = %s", (victims_project,))
    assert await cur.fetchone() == (None, 0)  # ownerless, awaiting admin recovery
```

- [ ] **Step 8: Run the affected tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests -q -x -k "not migration_01" 2>&1 | tail -5`
Expected: FAIL — `psycopg.errors.UndefinedColumn: column … owner_user_id does not exist` (from `server/auth/authz.py` or `server/routers/projects.py`).

- [ ] **Step 9: Make access membership-only**

In `server/auth/authz.py`, replace `readable_docs_where`, `readable_docs_params`, `can_manage_project_docs`, `visible_projects_where` and `visible_projects_params` with:

```python
def readable_docs_where(alias: str = "d") -> str:
    """The ONE definition of "can read this document" (A1 spec §3, A0 §3.1):
    the user has a library entry for it, or it is placed in a project the
    user is a member of (any role — owners are members) — and that entry or
    placement is PROVED (migration 012): `verified`, i.e. it traces back to
    someone who sent this server the bytes. `<alias>` is a `documents` row.
    Bind with `readable_docs_params(user_id)`. Subquery aliases are
    underscore-prefixed so they can't shadow the caller's. Resolved in SQL,
    never in Python."""
    return (
        f"(EXISTS (SELECT 1 FROM library_entries _re "
        f"WHERE _re.doc_id = {alias}.doc_id AND _re.user_id = %s "
        f"AND {effective_holding_sql('_re')}) "
        f"OR EXISTS (SELECT 1 FROM project_documents _rpd "
        f"JOIN project_members _rpm ON _rpm.project_id = _rpd.project_id "
        f"WHERE _rpd.doc_id = {alias}.doc_id AND {effective_holding_sql('_rpd')} "
        f"AND _rpm.user_id = %s))"
    )


def readable_docs_params(user_id: str) -> list[str]:
    """Exactly the parameters `readable_docs_where` needs, in order."""
    return [user_id, user_id]


async def can_manage_project_docs(conn, user_id: str, project_id) -> bool:
    """The one seam for "may remove a document from this project" (A1 §5).
    Task 1: the Owner role. Task 2 widens it to role >= maintainer."""
    cur = await conn.execute(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s "
        "AND role = 'owner'", (project_id, user_id))
    return await cur.fetchone() is not None


def visible_projects_where(alias: str = "p") -> str:
    """A `projects` row the user is a member of (any role). Bind with
    `visible_projects_params(user_id)`."""
    return (
        f"EXISTS (SELECT 1 FROM project_members _vpm "
        f"WHERE _vpm.project_id = {alias}.id AND _vpm.user_id = %s)"
    )


def visible_projects_params(user_id: str) -> list[str]:
    return [user_id]
```

In `server/services/doc_content.py`, replace `docs_referenced_by_user` with:

```python
async def docs_referenced_by_user(conn, user_id) -> list[str]:
    """Docs whose last reference may vanish when this user is deleted: their
    library entries. Projects outlive their members (A0 §3.1), so placements
    are never orphaned by a user deletion. Sorted: GC locks each content row,
    and every multi-doc GC path taking them in the same order can't deadlock."""
    cur = await conn.execute(
        "SELECT doc_id FROM library_entries WHERE user_id = %s ORDER BY 1", (user_id,))
    return [r[0] for r in await cur.fetchall()]
```

In `server/auth/users.py` `delete_user`, change the docstring's "library entries, owned projects, chat_sessions" to "library entries, project memberships (projects themselves survive, A0), chat_sessions". In `server/routers/admin.py` `delete_user`, change "library entries, owned projects and chat history all cascade" to "library entries, project memberships and chat history all cascade (projects survive, ownerless if this was their last Owner — A0)", and change the comment above `docs_referenced_by_user` to "# Collect the user's entry docs BEFORE the rows cascade away; GC runs after."

- [ ] **Step 10: Keep the projects router working on the new schema**

In `server/routers/projects.py` (Task 5 replaces these functions; this step only keeps today's API alive):

```python
def _row(r) -> dict:
    # Transitional (A0 Task 1): `owner_user_id` now reports `created_by`.
    return {"id": str(r[0]), "owner_user_id": str(r[1]) if r[1] else None,
            "name": r[2], "description": r[3]}


async def _assert_owner(conn, project_id: str, user_id: str) -> None:
    cur = await conn.execute(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s "
        "AND role = 'owner'", (project_id, user_id))
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Project not found")
```

`create_project`: insert `created_by` and the Owner row:

```python
    cur = await conn.execute(
        "INSERT INTO projects (created_by, name, description) "
        "VALUES (%s,%s,%s) RETURNING id, created_by, name, description",
        (principal.user_id, body.name, body.description))
    out = _row(await cur.fetchone())
    await conn.execute(
        "INSERT INTO project_members (project_id, user_id, role, added_by) "
        "VALUES (%s,%s,'owner',%s)", (out["id"], principal.user_id, principal.user_id))
    out["is_owner"] = True
    return out
```

`list_projects`:

```python
    cur = await conn.execute(
        f"SELECT p.id, p.created_by, p.name, p.description, "
        f"EXISTS (SELECT 1 FROM project_members _o WHERE _o.project_id = p.id "
        f"AND _o.user_id = %s AND _o.role = 'owner') FROM projects p "
        f"WHERE {visible_projects_where('p')} ORDER BY p.created_at DESC",
        [principal.user_id, *visible_projects_params(principal.user_id)])
    rows = await cur.fetchall()
    return [{**_row(r), "is_owner": r[4]} for r in rows]
```

`patch_project`: change its final SELECT to `"SELECT id, created_by, name, description FROM projects WHERE id = %s"`. `add_member`: change the INSERT to `"INSERT INTO project_members (project_id, user_id, role, added_by) VALUES (%s,%s,'contributor',%s) ON CONFLICT DO NOTHING", (project_id, user_id, principal.user_id)`.

Check every other `owner_user_id` is gone:

Run: `grep -rn "owner_user_id" server --include='*.py' | grep -v "tests/test_migration_01\|routers/projects.py:.*\"owner_user_id\""`
Expected: no output.

- [ ] **Step 11: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: all passed, 0 failed (885 = 883 + the 2 new migration tests).

- [ ] **Step 12: Commit** (ask first — Global Constraints)

```bash
git add server/sql/014_embedding_profile.sql server/sql/015_chunk_text_search.sql server/sql/016_app_settings.sql server/sql/017_project_roles.sql server/tests/ server/auth/authz.py server/auth/users.py server/routers/projects.py server/routers/admin.py server/services/doc_content.py
git commit -m "feat(projects): migration 017 — roles on memberships, people's names, project limits, project_events; membership is the only project access and projects outlive their creator (A0 Task 1)"
```

---

### Task 2: The project-role seam

**Files:**
- Modify: `server/auth/authz.py`
- Create: `server/tests/test_project_roles.py`

**Interfaces:**
- Consumes: `seed.make_project`, `seed.add_member` (Task 1).
- Produces:
  - `ROLES: tuple[str, ...] = ("reader", "contributor", "maintainer", "owner")`
  - `role_at_least(role: str | None, minimum: str) -> bool`
  - `async project_role(conn, user_id, project_id, *, lock=False) -> str | None`
  - `ProjectAccess(role: str | None, is_admin: bool)` (frozen dataclass)
  - `async require_project_role(conn, principal, project_id, minimum: str | None, *, lock=False, admin_ok=True) -> ProjectAccess` — 404 `not_found` for a missing project or a non-member (unless an admin with `admin_ok`); 403 `insufficient_role` with `required=minimum` when below `minimum` (admins with `admin_ok` pass).
  - `PROJECT_NOT_FOUND = "This project doesn't exist or you don't have access."`
  - `insufficient_role(required: str) -> HTTPException`
  - `can_for(role: str | None, is_admin: bool) -> dict[str, bool]` with keys `edit, manage_members, manage_owners, file_docs, remove_docs, delete, leave`.
  - `can_manage_project_docs` → role ≥ maintainer.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_project_roles.py`:

```python
"""A0 §5: the project-role seam. One place decides membership, role order,
the 404/403 split and the `can` object the UI reads."""
import pytest
from fastapi import HTTPException

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


def _p(uid, *, admin=False):
    return deps.Principal(user_id=uid, email="x@x.io", role="admin" if admin else "member",
                          capabilities=frozenset({"reader"}))


@pytest.mark.parametrize("role,minimum,expected", [
    (None, "reader", False), ("reader", "reader", True), ("reader", "contributor", False),
    ("contributor", "contributor", True), ("contributor", "maintainer", False),
    ("maintainer", "maintainer", True), ("maintainer", "owner", False), ("owner", "maintainer", True),
])
def test_role_at_least(role, minimum, expected):
    assert authz.role_at_least(role, minimum) is expected


@pytest.mark.parametrize("role,is_admin,expected", [
    (None, False, dict(edit=False, manage_members=False, manage_owners=False, file_docs=False,
                       remove_docs=False, delete=False, leave=False)),
    ("reader", False, dict(edit=False, manage_members=False, manage_owners=False, file_docs=False,
                           remove_docs=False, delete=False, leave=True)),
    ("contributor", False, dict(edit=False, manage_members=False, manage_owners=False, file_docs=True,
                                remove_docs=False, delete=False, leave=True)),
    ("maintainer", False, dict(edit=True, manage_members=True, manage_owners=False, file_docs=True,
                               remove_docs=True, delete=False, leave=True)),
    ("owner", False, dict(edit=True, manage_members=True, manage_owners=True, file_docs=True,
                          remove_docs=True, delete=True, leave=True)),
    # Admins manage any project but file/remove documents only through a member role (§5).
    (None, True, dict(edit=True, manage_members=True, manage_owners=True, file_docs=False,
                      remove_docs=False, delete=True, leave=False)),
    ("reader", True, dict(edit=True, manage_members=True, manage_owners=True, file_docs=False,
                          remove_docs=False, delete=True, leave=True)),
])
def test_can_for(role, is_admin, expected):
    assert authz.can_for(role, is_admin) == expected


async def test_project_role_reads_membership(db_conn):
    owner, other = await _user(db_conn, "o"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner)
    assert await authz.project_role(db_conn, owner, pid) == "owner"
    assert await authz.project_role(db_conn, other, pid) is None
    assert await authz.project_role(db_conn, owner, pid, lock=True) == "owner"


async def test_require_project_role_404_for_non_members_and_missing_projects(db_conn):
    owner, other = await _user(db_conn, "o"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner)
    for project_id in (pid, "00000000-0000-0000-0000-00000000dead"):
        with pytest.raises(HTTPException) as e:
            await authz.require_project_role(db_conn, _p(other), project_id, None)
        assert e.value.status_code == 404
        assert e.value.detail == {"error": "not_found", "message": authz.PROJECT_NOT_FOUND}


async def test_require_project_role_403_names_the_required_role(db_conn):
    owner, reader = await _user(db_conn, "o"), await _user(db_conn, "r")
    pid = await seed.make_project(db_conn, owner, members=[(reader, "reader")])
    with pytest.raises(HTTPException) as e:
        await authz.require_project_role(db_conn, _p(reader), pid, "maintainer")
    assert e.value.status_code == 403
    assert e.value.detail["error"] == "insufficient_role"
    assert e.value.detail["required"] == "maintainer"
    assert e.value.detail["message"] == "Only Maintainers or Owners can do that."
    access = await authz.require_project_role(db_conn, _p(reader), pid, "reader")
    assert access == authz.ProjectAccess(role="reader", is_admin=False)


async def test_admins_pass_only_when_admin_ok(db_conn):
    owner, admin = await _user(db_conn, "o"), await _user(db_conn, "a")
    pid = await seed.make_project(db_conn, owner)
    access = await authz.require_project_role(db_conn, _p(admin, admin=True), pid, "owner")
    assert access == authz.ProjectAccess(role=None, is_admin=True)
    with pytest.raises(HTTPException) as e:  # documents: admins act only as members (§5)
        await authz.require_project_role(db_conn, _p(admin, admin=True), pid, "contributor",
                                         admin_ok=False)
    assert e.value.status_code == 404


async def test_can_manage_project_docs_is_maintainer_or_above(db_conn):
    owner, m, c = await _user(db_conn, "o"), await _user(db_conn, "m"), await _user(db_conn, "c")
    pid = await seed.make_project(db_conn, owner, members=[(m, "maintainer"), (c, "contributor")])
    assert await authz.can_manage_project_docs(db_conn, owner, pid)
    assert await authz.can_manage_project_docs(db_conn, m, pid)
    assert not await authz.can_manage_project_docs(db_conn, c, pid)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_project_roles.py -q`
Expected: FAIL — `AttributeError: module 'server.auth.authz' has no attribute 'role_at_least'`.

- [ ] **Step 3: Implement the seam**

In `server/auth/authz.py`, add `from dataclasses import dataclass` to the imports, replace `can_manage_project_docs` with the version below, and add the rest after `visible_projects_params`:

```python
ROLES = ("reader", "contributor", "maintainer", "owner")
_RANK = {r: i for i, r in enumerate(ROLES)}
PROJECT_NOT_FOUND = "This project doesn't exist or you don't have access."
_ROLE_MESSAGES = {
    "reader": "Only members can do that.",
    "contributor": "Only Contributors, Maintainers or Owners can do that.",
    "maintainer": "Only Maintainers or Owners can do that.",
    "owner": "Only Owners can do that.",
}


def role_at_least(role: str | None, minimum: str) -> bool:
    return role is not None and _RANK[role] >= _RANK[minimum]


def insufficient_role(required: str) -> HTTPException:
    return refusal(403, "insufficient_role", _ROLE_MESSAGES[required], required=required)


async def project_role(conn, user_id: str, project_id, *, lock: bool = False) -> str | None:
    """The user's role in the project; None if not a member (or no project).
    `lock=True` first takes the project's row lock (A0 §3.3): membership
    writes on one project then run one at a time, and the role read here is
    the one committed by whoever held the lock before."""
    if lock:
        await conn.execute("SELECT 1 FROM projects WHERE id = %s FOR UPDATE", (project_id,))
    cur = await conn.execute(
        "SELECT role FROM project_members WHERE project_id = %s AND user_id = %s",
        (project_id, user_id))
    row = await cur.fetchone()
    return row[0] if row else None


@dataclass(frozen=True)
class ProjectAccess:
    role: str | None      # the caller's member role (None: an admin who isn't a member)
    is_admin: bool


async def require_project_role(conn, principal, project_id, minimum: str | None, *,
                               lock: bool = False, admin_ok: bool = True) -> ProjectAccess:
    """Every project route's first check (A0 §5, §6). A missing project and a
    non-member are the same 404, so a project's existence never leaks. A
    member below `minimum` (None = any member) gets 403 insufficient_role.
    An admin passes when `admin_ok` (the matrix's admin column); document
    routes pass `admin_ok=False`, because admins file and remove documents
    only through a member role."""
    cur = await conn.execute(
        "SELECT 1 FROM projects WHERE id = %s" + (" FOR UPDATE" if lock else ""), (project_id,))
    if await cur.fetchone() is None:
        raise refusal(404, "not_found", PROJECT_NOT_FOUND)
    role = await project_role(conn, principal.user_id, project_id)
    is_admin = principal.role == "admin"
    if is_admin and admin_ok:
        return ProjectAccess(role=role, is_admin=True)
    if role is None:
        raise refusal(404, "not_found", PROJECT_NOT_FOUND)
    if minimum is not None and not role_at_least(role, minimum):
        raise insufficient_role(minimum)
    return ProjectAccess(role=role, is_admin=is_admin)


def can_for(role: str | None, is_admin: bool) -> dict[str, bool]:
    """What the caller may do in a project, from their role (A0 §5). The API
    returns it with every project so the UI never re-derives the rules."""
    def at(minimum: str) -> bool:
        return role_at_least(role, minimum)
    return {
        "edit": at("maintainer") or is_admin,
        "manage_members": at("maintainer") or is_admin,
        "manage_owners": at("owner") or is_admin,
        "file_docs": at("contributor"),
        "remove_docs": at("maintainer"),
        "delete": at("owner") or is_admin,
        "leave": role is not None,
    }


async def can_manage_project_docs(conn, user_id: str, project_id) -> bool:
    """The one seam for "may remove a document from this project" (A1 §5,
    A0 §5): a member whose role is Maintainer or above."""
    return role_at_least(await project_role(conn, user_id, project_id), "maintainer")
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_project_roles.py -q`
Expected: all passed.

- [ ] **Step 5: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed. (`test_unlink_resolution_table` still passes: the project owner is a Maintainer-or-above; the other callers are contributors.)

- [ ] **Step 6: Commit** (ask first)

```bash
git add server/auth/authz.py server/tests/test_project_roles.py
git commit -m "feat(projects): one project-role seam — require_project_role (404 non-member, 403 insufficient_role), the row lock, and can_for (A0 Task 2)"
```

---

### Task 3: People — names at login, labels, and the directory lookup

**Files:**
- Modify: `server/auth/users.py` (`_KEYS`, `resolve_or_provision_user`)
- Modify: `server/routers/auth.py:103-111` (pass the name claims)
- Create: `server/services/people.py`
- Create: `server/routers/people.py`
- Modify: `server/app.py` (register the router)
- Create: `server/tests/test_people.py`

**Interfaces:**
- Produces (in `server/services/people.py`):
  - `DirectoryConfig(mode: str, domains: frozenset[str], show_email: bool)`; `load_directory_config(env=os.environ) -> DirectoryConfig`
  - `mask_email(email: str | None) -> str`; `shown_username(username, show_email) -> str | None`
  - `person_label(*, first_name, last_name, display_name, username, email, show_email) -> str`
  - `person_cols(alias: str) -> str` — six columns `id, first_name, last_name, display_name, username, email`
  - `person_from(cols: Sequence, show_email: bool) -> dict | None` — `{"id", "name"}` or None when `cols[0]` is None
  - `async lookup(conn, cfg, caller_id, caller_email, q) -> list[dict]` — `[{id, name, username, email?, status}]`
- `users.resolve_or_provision_user(..., username=None, first_name=None, last_name=None)`; user dicts gain `username, first_name, last_name, project_limit`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_people.py`:

```python
"""A0 §4: people's names from the login token, one labelling rule, and the
directory lookup (exact / domain / open)."""
import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import people as people_router
from server.services import people

pytestmark = pytest.mark.asyncio


async def _person(conn, sub, email, *, first=None, last=None, username=None, status="active"):
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=email, username=username,
                                        first_name=first, last_name=last)
    await set_status(conn, u["id"], status)
    return u["id"]


def _cfg(mode="open", domains=(), show_email=False):
    return people.DirectoryConfig(mode=mode, domains=frozenset(domains), show_email=show_email)


async def test_login_stores_names_and_a_missing_claim_keeps_them(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="n1", email="n1@x.io",
                                        username="asha", first_name="Asha", last_name="Perera")
    assert (u["username"], u["first_name"], u["last_name"]) == ("asha", "Asha", "Perera")
    again = await resolve_or_provision_user(db_conn, iss="i", sub="n1", email="n1@x.io")
    assert (again["username"], again["first_name"], again["last_name"]) == ("asha", "Asha", "Perera")
    assert again["project_limit"] is None


@pytest.mark.parametrize("fields,show_email,expected", [
    (dict(first_name="Asha", last_name="Perera", display_name="A", username="asha"), False, "Asha Perera"),
    (dict(first_name=None, last_name="Perera", display_name=None, username=None), False, "Perera"),
    (dict(first_name=None, last_name=None, display_name="Asha P", username="asha"), False, "Asha P"),
    (dict(first_name=None, last_name=None, display_name=None, username="asha"), False, "@asha"),
    # An email-shaped username is hidden while emails are hidden.
    (dict(first_name=None, last_name=None, display_name=None, username="asha@x.io"), False, "a•••@x.io"),
    (dict(first_name=None, last_name=None, display_name=None, username="asha@x.io"), True, "@asha@x.io"),
    (dict(first_name=None, last_name=None, display_name=None, username=None), True, "asha@x.io"),
])
def test_person_label(fields, show_email, expected):
    assert people.person_label(email="asha@x.io", show_email=show_email, **fields) == expected


def test_load_directory_config_defaults_and_bad_values(caplog):
    assert people.load_directory_config({}) == _cfg(mode="exact")
    cfg = people.load_directory_config({"USER_DIRECTORY_MODE": "Domain",
                                        "USER_DIRECTORY_DOMAINS": " Example.com, ,corp.example.com",
                                        "USER_DIRECTORY_SHOW_EMAIL": "true"})
    assert cfg == _cfg(mode="domain", domains={"example.com", "corp.example.com"}, show_email=True)
    with caplog.at_level(logging.WARNING):
        bad = people.load_directory_config({"USER_DIRECTORY_MODE": "everyone",
                                            "USER_DIRECTORY_SHOW_EMAIL": "yes"})
    assert bad == _cfg(mode="exact")
    assert "USER_DIRECTORY_MODE" in caplog.text and "USER_DIRECTORY_SHOW_EMAIL" in caplog.text


async def test_exact_email_finds_active_and_pending_never_disabled_or_self(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "Ann@Partner.org", first="Ann")
    await _person(db_conn, "p", "pen@example.com", status="pending")
    await _person(db_conn, "d", "dis@example.com", status="disabled")
    cfg = _cfg(mode="exact")
    found = await people.lookup(db_conn, cfg, me, "me@example.com", "ann@partner.org")
    assert [(r["name"], r["status"]) for r in found] == [("Ann", "active")]
    assert "email" not in found[0]
    assert len(await people.lookup(db_conn, cfg, me, "me@example.com", "pen@example.com")) == 1
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "dis@example.com") == []
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "me@example.com") == []


async def test_exact_mode_has_no_type_ahead(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann")
    assert await people.lookup(db_conn, _cfg(mode="exact"), me, "me@example.com", "an") == []


async def test_type_ahead_needs_two_characters_and_lists_only_active(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann", last="Lee", username="annl")
    await _person(db_conn, "p", "anna@example.com", first="Anna", status="pending")
    cfg = _cfg(mode="open")
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "a") == []
    found = await people.lookup(db_conn, cfg, me, "me@example.com", "an")
    assert [(r["name"], r["username"]) for r in found] == [("Ann Lee", "annl")]
    assert [r["name"] for r in await people.lookup(db_conn, cfg, me, "me@example.com", "le")] == ["Ann Lee"]


async def test_domain_mode_only_for_listed_domains(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann")
    await _person(db_conn, "b", "anne@other.org", first="Anne")
    gmail = await _person(db_conn, "g", "g@gmail.com")
    cfg = _cfg(mode="domain", domains={"example.com"})
    assert [r["name"] for r in await people.lookup(db_conn, cfg, me, "me@example.com", "an")] == ["Ann"]
    # An unlisted caller domain falls back to exact: no type-ahead at all.
    assert await people.lookup(db_conn, cfg, gmail, "g@gmail.com", "an") == []


async def test_lookup_escapes_like_wildcards(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "ann@example.com", first="Ann")
    cfg = _cfg(mode="open")
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "%%") == []
    assert await people.lookup(db_conn, cfg, me, "me@example.com", "a_") == []


async def test_email_shaped_usernames_never_match_while_emails_are_hidden(db_conn):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "zed@example.com", username="zed@example.com")
    assert await people.lookup(db_conn, _cfg(mode="open"), me, "me@example.com", "ze") == []
    shown = await people.lookup(db_conn, _cfg(mode="open", show_email=True), me, "me@example.com", "ze")
    assert [r["email"] for r in shown] == ["zed@example.com"]


async def test_lookup_route_needs_reader_and_logs_no_query(db_conn, caplog, monkeypatch):
    me = await _person(db_conn, "me", "me@example.com")
    await _person(db_conn, "a", "secret.person@example.com", first="Secret")
    monkeypatch.setenv("USER_DIRECTORY_MODE", "open")
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.include_router(people_router.router)
    for caps, status in ((frozenset(), 403), (frozenset({"reader"}), 200)):
        app.dependency_overrides[deps.get_current_user] = lambda caps=caps: deps.Principal(
            user_id=me, email="me@example.com", role="member", capabilities=caps)
        with caplog.at_level(logging.DEBUG):
            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.get("/v1/users/lookup", params={"q": "secret"})
        assert r.status_code == status
    assert [p["name"] for p in r.json()] == ["Secret"]
    # httpx logs the request URL itself; the rule is about this app's loggers.
    assert not any("secret" in rec.getMessage().lower()
                   for rec in caplog.records if rec.name.startswith("server"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_people.py -q`
Expected: FAIL — `ImportError: cannot import name 'people' from 'server.routers'`.

- [ ] **Step 3: Store names at login**

In `server/auth/users.py`, extend `_KEYS`:

```python
_KEYS = [
    "id", "email", "display_name", "role", "status", "oidc_iss", "oidc_sub",
    "inference_daily_token_budget", "capabilities", "created_at",
    "username", "first_name", "last_name", "project_limit",
]
```

Give `resolve_or_provision_user` three keyword parameters after `display_name` — `username: str | None = None, first_name: str | None = None, last_name: str | None = None` — and a docstring line: "`username`, `first_name`, `last_name` come from the token's `preferred_username`, `given_name`, `family_name`; a claim the token lacks leaves the stored value unchanged (A0 §3.1)." Then refresh them in every branch. Add this helper above the function:

```python
_NAMES_SET = ("username=COALESCE(%s, username), first_name=COALESCE(%s, first_name), "
              "last_name=COALESCE(%s, last_name)")
```

Branch 1's UPDATE becomes:

```python
        await conn.execute(
            "UPDATE users SET email=%s, display_name=COALESCE(%s, display_name), "
            f"{_NAMES_SET}, updated_at=now() WHERE id=%s",
            (email, display_name, username, first_name, last_name, found["id"]),
        )
```

Branch 2's UPDATE becomes:

```python
        await conn.execute(
            "UPDATE users SET oidc_iss=%s, oidc_sub=%s, "
            f"display_name=COALESCE(%s, display_name), {_NAMES_SET}, updated_at=now() WHERE id=%s",
            (iss, sub, display_name, username, first_name, last_name, by_email["id"]),
        )
```

Branch 3 (seed admin claim) UPDATE becomes:

```python
    cur = await conn.execute(
        "UPDATE users SET oidc_iss=%s, oidc_sub=%s, email=%s, "
        f"display_name=COALESCE(%s, display_name), {_NAMES_SET}, updated_at=now() "
        "WHERE id=%s AND oidc_sub IS NULL",
        (iss, sub, email, display_name, username, first_name, last_name, SEED_ADMIN_ID),
    )
```

The brand-new INSERT becomes:

```python
    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, display_name, username, first_name, "
        "last_name, role, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'member', 'pending') "
        "RETURNING id",
        (iss, sub, email, display_name, username, first_name, last_name),
    )
```

In `server/routers/auth.py`, pass the claims in the `resolve_or_provision_user` call:

```python
            display_name=claims.get("name"),
            username=claims.get("preferred_username"),
            first_name=claims.get("given_name"),
            last_name=claims.get("family_name"),
```

- [ ] **Step 4: Write the people service**

Create `server/services/people.py`:

```python
"""People directory (A0 §4): how a person is labelled everywhere, and who a
caller may find when adding someone to a project or sharing a document.

USER_DIRECTORY_MODE: `exact` (full email only, the default), `domain`
(type-ahead within the caller's email domain, only when it is listed in
USER_DIRECTORY_DOMAINS), `open` (type-ahead over everyone). An exact email
works in every mode and finds active and pending (awaiting approval)
accounts; type-ahead lists only active ones; disabled accounts never appear.
Lookup text is never logged at INFO or above (it is personal data)."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MODES = ("exact", "domain", "open")
TYPE_AHEAD_MIN = 2
MAX_RESULTS = 10


@dataclass(frozen=True)
class DirectoryConfig:
    mode: str
    domains: frozenset[str]
    show_email: bool


def load_directory_config(env: Mapping[str, str] = os.environ) -> DirectoryConfig:
    """Read at request time. An invalid value logs a WARNING and falls back
    to the safe default (exact; emails hidden)."""
    mode = env.get("USER_DIRECTORY_MODE", "").strip().lower() or "exact"
    if mode not in MODES:
        logger.warning("USER_DIRECTORY_MODE=%r is not exact, domain or open; using exact", mode)
        mode = "exact"
    domains = frozenset(d.strip().lower() for d in env.get("USER_DIRECTORY_DOMAINS", "").split(",")
                        if d.strip())
    raw = env.get("USER_DIRECTORY_SHOW_EMAIL", "").strip().lower() or "false"
    if raw not in ("true", "false"):
        logger.warning("USER_DIRECTORY_SHOW_EMAIL=%r is not true or false; using false", raw)
        raw = "false"
    return DirectoryConfig(mode=mode, domains=domains, show_email=raw == "true")


def mask_email(email: str | None) -> str:
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}•••@{domain}" if domain else "•••"


def shown_username(username: str | None, show_email: bool) -> str | None:
    """A username, unless it is email-shaped while emails are hidden."""
    if not username or ("@" in username and not show_email):
        return None
    return username


def person_label(*, first_name, last_name, display_name, username, email, show_email: bool) -> str:
    """The one labelling rule (A0 §4): "First Last", else the display name,
    else @username, else the email (shown) or a masked email (hidden)."""
    full = " ".join(p for p in (first_name, last_name) if p)
    if full:
        return full
    if display_name:
        return display_name
    uname = shown_username(username, show_email)
    if uname:
        return f"@{uname}"
    return email if (show_email and email) else mask_email(email)


def person_cols(alias: str) -> str:
    """The six columns `person_from` reads, in order."""
    return (f"{alias}.id, {alias}.first_name, {alias}.last_name, {alias}.display_name, "
            f"{alias}.username, {alias}.email")


def person_from(cols: Sequence, show_email: bool) -> dict | None:
    """`{"id", "name"}` from `person_cols` values; None for a deleted person."""
    if cols[0] is None:
        return None
    return {"id": str(cols[0]),
            "name": person_label(first_name=cols[1], last_name=cols[2], display_name=cols[3],
                                 username=cols[4], email=cols[5], show_email=show_email)}


def _like_prefix(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _result(r, show_email: bool) -> dict:
    out = {**person_from(r[:6], show_email), "username": shown_username(r[4], show_email),
           "status": r[6]}
    if show_email:
        out["email"] = r[5]
    return out


async def lookup(conn, cfg: DirectoryConfig, caller_id: str, caller_email: str,
                 q: str) -> list[dict]:
    q = q.strip()
    if len(q) < TYPE_AHEAD_MIN:
        return []
    cols = f"{person_cols('u')}, u.status"
    if "@" in q:
        cur = await conn.execute(
            f"SELECT {cols} FROM users u WHERE lower(u.email) = lower(%s) "
            "AND u.status IN ('active', 'pending') AND u.id <> %s", (q, caller_id))
        exact = [_result(r, cfg.show_email) for r in await cur.fetchall()]
        if exact:
            logger.debug("directory lookup: exact match, %d result(s)", len(exact))
            return exact
    mode = cfg.mode
    domain = (caller_email or "").rpartition("@")[2].lower()
    if mode == "domain" and domain not in cfg.domains:
        mode = "exact"
    if mode == "exact":
        logger.debug("directory lookup: mode=exact, no type-ahead")
        return []
    pattern = _like_prefix(q.lower())
    matches = ["lower(u.first_name) LIKE %s", "lower(u.last_name) LIKE %s",
               "lower(u.display_name) LIKE %s",
               "(lower(u.username) LIKE %s AND (%s OR position('@' in u.username) = 0))"]
    params: list = [pattern, pattern, pattern, pattern, cfg.show_email]
    if cfg.show_email:
        matches.append("lower(u.email) LIKE %s")
        params.append(pattern)
    where = f"u.status = 'active' AND u.id <> %s AND ({' OR '.join(matches)})"
    params = [caller_id, *params]
    if mode == "domain":
        where += " AND lower(split_part(u.email, '@', 2)) = %s"
        params.append(domain)
    cur = await conn.execute(
        f"SELECT {cols} FROM users u WHERE {where} "
        "ORDER BY lower(coalesce(u.first_name, u.display_name, u.username, u.email)), u.id "
        f"LIMIT {MAX_RESULTS}", params)
    found = [_result(r, cfg.show_email) for r in await cur.fetchall()]
    logger.debug("directory lookup: mode=%s, %d result(s)", mode, len(found))
    return found
```

Note on `LIKE … %s` with an escaped pattern: Postgres's default LIKE escape character is the backslash, so `\%` and `\_` match literally.

Create `server/routers/people.py`:

```python
"""/v1/users/lookup — find a person to add to a project or share with (A0 §4, §9.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import deps
from ..services import people

router = APIRouter(prefix="/v1/users", tags=["people"])


@router.get("/lookup")
async def lookup_people(q: str = Query("", max_length=200),
                        principal: deps.Principal = Depends(deps.require_capability("reader")),
                        conn=Depends(deps.get_conn)):
    return await people.lookup(conn, people.load_directory_config(), principal.user_id,
                               principal.email, q)
```

In `server/app.py`, import it next to the projects router (`from .routers.people import router as people_router`, matching how `projects_router` is imported) and add `app.include_router(people_router)` after `app.include_router(projects_router)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_people.py -q`
Expected: all passed.

- [ ] **Step 6: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed.

- [ ] **Step 7: Commit** (ask first)

```bash
git add server/auth/users.py server/routers/auth.py server/services/people.py server/routers/people.py server/app.py server/tests/test_people.py
git commit -m "feat(people): names from the login token, one person-label rule, and GET /v1/users/lookup (exact/domain/open; disabled never shown; wildcards escaped) (A0 Task 3)"
```

---

### Task 4: The project activity feed service and its retention

**Files:**
- Create: `server/services/project_events.py`
- Modify: `server/app.py` (start/stop retention)
- Create: `server/tests/test_project_events.py`

**Interfaces:**
- Consumes: `people.person_cols`, `people.person_from`, `people.load_directory_config` (Task 3); `seed.make_project` (Task 1).
- Produces:
  - `KINDS: frozenset[str]`
  - `async record(conn, project_id, actor_user_id, kind, *, subject_user_id=None, doc_id=None, details=None) -> None` — raises `ValueError` for an unknown kind; writes the row and (except `document.*`, which callers audit as `placement.*`) an audit line with IDs and roles only.
  - `async page(conn, project_id, *, before: int | None = None, limit: int = 50, show_email: bool = False) -> dict` → `{"events": [{id, at, kind, actor, subject, doc, details}], "next_before": int | None}`
  - `retention_days(env=os.environ) -> int`; `async purge(conn, days: int) -> int`
  - `start_retention() -> None`; `async stop_retention() -> None`

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_project_events.py`:

```python
"""A0 §3.2: the project activity feed — one row per change, people shown by
their current names, document names kept as they were, paged newest first,
and an optional retention window in days."""
import logging

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import project_events
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, first=None):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io",
                                            first_name=first))["id"]


async def test_record_rejects_unknown_kinds(db_conn):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    with pytest.raises(ValueError):
        await project_events.record(db_conn, pid, owner, "member.teleported")


async def test_record_writes_the_row_and_an_audit_line_without_names(db_conn, caplog):
    owner, ben = await _user(db_conn, "o", "Olu"), await _user(db_conn, "b", "Ben")
    pid = await seed.make_project(db_conn, owner, "Secret Plans")
    with caplog.at_level(logging.INFO, logger="server.audit"):
        await project_events.record(db_conn, pid, owner, "member.added", subject_user_id=ben,
                                    details={"role": "maintainer", "via": "member"})
        await project_events.record(db_conn, pid, owner, "project.renamed",
                                    details={"from": "Secret Plans", "to": "Open Plans"})
    assert f"member.added project={pid} by={owner} user={ben} role=maintainer via=member" in caplog.text
    assert "Secret" not in caplog.text and "Ben" not in caplog.text
    cur = await db_conn.execute(
        "SELECT kind, subject_user_id, details FROM project_events WHERE project_id=%s "
        "AND kind <> 'project.created' ORDER BY id", (pid,))
    rows = await cur.fetchall()
    assert [(r[0], str(r[1]) if r[1] else None) for r in rows] == [
        ("member.added", ben), ("project.renamed", None)]
    assert rows[0][2] == {"role": "maintainer", "via": "member"}


async def test_document_events_are_not_audited_twice(db_conn, caplog):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    with caplog.at_level(logging.INFO, logger="server.audit"):
        await project_events.record(db_conn, pid, owner, "document.added", doc_id="d" * 64,
                                    details={"name": "Q3.pdf"})
    assert "document.added" not in caplog.text  # the route audits placement.added


async def test_page_newest_first_with_current_names_and_paging(db_conn):
    owner, ben = await _user(db_conn, "o", "Olu"), await _user(db_conn, "b", "Ben")
    pid = await seed.make_project(db_conn, owner)
    for i in range(3):
        await project_events.record(db_conn, pid, owner, "member.added", subject_user_id=ben,
                                    details={"role": "reader", "via": "member", "n": i})
    first = await project_events.page(db_conn, pid, limit=2)
    assert [e["details"]["n"] for e in first["events"]] == [2, 1]
    assert first["events"][0]["actor"] == {"id": owner, "name": "Olu"}
    assert first["events"][0]["subject"] == {"id": ben, "name": "Ben"}
    assert first["events"][0]["doc"] is None
    # seed.make_project writes no project.created event, so page two holds only n=0.
    second = await project_events.page(db_conn, pid, before=first["next_before"], limit=2)
    assert [e["details"]["n"] for e in second["events"]] == [0]
    assert second["next_before"] is None


async def test_page_shows_a_deleted_actor_as_null_and_keeps_document_names(db_conn):
    owner, gone = await _user(db_conn, "o"), await _user(db_conn, "g")
    pid = await seed.make_project(db_conn, owner, members=[(gone, "contributor")])
    await project_events.record(db_conn, pid, gone, "document.removed", doc_id="e" * 64,
                                details={"name": "Old.pdf"})
    await db_conn.execute("DELETE FROM users WHERE id = %s", (gone,))
    ev = (await project_events.page(db_conn, pid))["events"][0]
    assert ev["actor"] is None
    assert ev["doc"] == {"id": "e" * 64, "name": "Old.pdf"}


async def test_page_clamps_limit(db_conn):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    for _ in range(3):
        await project_events.record(db_conn, pid, owner, "project.described")
    assert len((await project_events.page(db_conn, pid, limit=0))["events"]) == 1
    assert len((await project_events.page(db_conn, pid, limit=500))["events"]) == 3


def test_retention_days_parsing(caplog):
    assert project_events.retention_days({}) == 0
    assert project_events.retention_days({"PROJECT_EVENTS_RETENTION_DAYS": "365"}) == 365
    with caplog.at_level(logging.WARNING):
        assert project_events.retention_days({"PROJECT_EVENTS_RETENTION_DAYS": "-1"}) == 0
        assert project_events.retention_days({"PROJECT_EVENTS_RETENTION_DAYS": "a year"}) == 0
    assert caplog.text.count("PROJECT_EVENTS_RETENTION_DAYS") == 2


async def test_purge_removes_only_older_events_and_zero_keeps_all(db_conn):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    await db_conn.execute(
        "INSERT INTO project_events (project_id, at, kind) VALUES (%s, now() - interval '40 days', "
        "'project.described')", (pid,))
    assert await project_events.purge(db_conn, 0) == 0
    assert await project_events.purge(db_conn, 30) == 1
    cur = await db_conn.execute("SELECT kind FROM project_events WHERE project_id=%s", (pid,))
    assert [r[0] for r in await cur.fetchall()] == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_project_events.py -q`
Expected: FAIL — `ImportError: cannot import name 'project_events' from 'server.services'`.

- [ ] **Step 3: Write the service**

Create `server/services/project_events.py`:

```python
"""Project activity feed (A0 §3.2). One `project_events` row per change,
written by the request that makes the change (same transaction), so a
rolled-back change leaves no event. People are stored by id and shown by
their CURRENT names; document names are copied because the document may be
gone. The audit log (server.audit) gets IDs and roles only — never names.

Retention: PROJECT_EVENTS_RETENTION_DAYS (days; 0 = keep forever, the
default). When positive, older events are deleted at startup and then every
24 hours; the DELETE is idempotent, so several workers purging is harmless."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping

from psycopg.types.json import Jsonb

from ..audit import audit
from ..db import get_pool
from . import people

logger = logging.getLogger(__name__)

KINDS = frozenset({
    "project.created", "project.renamed", "project.described",
    "member.added", "member.role_changed", "member.removed", "member.left",
    "document.added", "document.removed",
})
_AUDITED_DETAILS = ("role", "from", "to", "via")  # roles only; never names
MAX_PAGE = 100
PURGE_EVERY_S = 24 * 3600


async def record(conn, project_id, actor_user_id, kind: str, *, subject_user_id=None,
                 doc_id: str | None = None, details: dict | None = None) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown project event kind: {kind}")
    details = details or {}
    await conn.execute(
        "INSERT INTO project_events (project_id, actor_user_id, kind, subject_user_id, doc_id, "
        "details) VALUES (%s,%s,%s,%s,%s,%s)",
        (project_id, actor_user_id, kind, subject_user_id, doc_id, Jsonb(details)))
    if kind.startswith("document."):
        return  # the placement routes audit these as placement.added/removed
    fields: dict[str, object] = {"project": project_id, "by": actor_user_id}
    if subject_user_id:
        fields["user"] = subject_user_id
    if kind != "project.renamed":  # a rename's from/to are names
        fields.update({k: details[k] for k in _AUDITED_DETAILS if k in details})
    audit(kind, **fields)


async def page(conn, project_id, *, before: int | None = None, limit: int = 50,
               show_email: bool = False) -> dict:
    limit = max(1, min(limit, MAX_PAGE))
    cur = await conn.execute(
        "SELECT e.id, e.at, e.kind, e.doc_id, e.details, "
        f"{people.person_cols('a')}, {people.person_cols('s')} "
        "FROM project_events e LEFT JOIN users a ON a.id = e.actor_user_id "
        "LEFT JOIN users s ON s.id = e.subject_user_id "
        "WHERE e.project_id = %s AND (%s::bigint IS NULL OR e.id < %s) "
        "ORDER BY e.id DESC LIMIT %s",
        (project_id, before, before, limit + 1))
    rows = await cur.fetchall()
    events = []
    for r in rows[:limit]:
        details = r[4] or {}
        events.append({
            "id": r[0], "at": r[1], "kind": r[2],
            "actor": people.person_from(r[5:11], show_email),
            "subject": people.person_from(r[11:17], show_email),
            "doc": {"id": r[3], "name": details.get("name")} if r[3] else None,
            "details": details,
        })
    next_before = events[-1]["id"] if len(rows) > limit else None
    return {"events": events, "next_before": next_before}


def retention_days(env: Mapping[str, str] = os.environ) -> int:
    raw = env.get("PROJECT_EVENTS_RETENTION_DAYS", "").strip() or "0"
    try:
        days = int(raw)
    except ValueError:
        days = -1
    if days < 0:
        logger.warning("PROJECT_EVENTS_RETENTION_DAYS=%r is not a whole number of days >= 0; "
                       "keeping events forever", raw)
        return 0
    return days


async def purge(conn, days: int) -> int:
    if days <= 0:
        return 0
    cur = await conn.execute(
        "DELETE FROM project_events WHERE at < now() - make_interval(days => %s)", (days,))
    return cur.rowcount


_task: asyncio.Task | None = None


async def _purge_loop(days: int) -> None:
    while True:
        try:
            async with get_pool().connection() as conn:
                n = await purge(conn, days)
            if n:
                logger.info("project events: purged %d older than %d days", n, days)
        except Exception:
            logger.warning("project event purge failed", exc_info=True)
        await asyncio.sleep(PURGE_EVERY_S)


def start_retention() -> None:
    """Called from the app's startup hook once the database is up."""
    global _task
    days = retention_days()
    if days > 0 and _task is None:
        _task = asyncio.create_task(_purge_loop(days))


async def stop_retention() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
```

In `server/app.py`: import `from .services import project_events`; in `_startup`, inside the `else:` branch after the `recover_stale` try/except, add `project_events.start_retention()`; in `_shutdown`, add `await project_events.stop_retention()` as its first line.

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_project_events.py -q`
Expected: all passed.

- [ ] **Step 5: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed.

- [ ] **Step 6: Commit** (ask first)

```bash
git add server/services/project_events.py server/app.py server/tests/test_project_events.py
git commit -m "feat(projects): the activity feed service — record (validated kinds, audit without names), newest-first paging with current names, PROJECT_EVENTS_RETENTION_DAYS purge (A0 Task 4)"
```

---

### Task 5: Projects API — the project object, creation policy and limits

**Files:**
- Create: `server/services/project_policy.py`
- Modify: `server/services/people.py` (add `assert_addable`)
- Modify: `server/routers/projects.py` (module docstring, models, `create_project`, `list_projects`, new `get_project`, `patch_project`, `delete_project`)
- Create: `server/tests/test_projects_api.py`
- Modify: `server/tests/test_projects_router.py` (delete four superseded tests)

**Interfaces:**
- Consumes: `require_project_role`, `can_for`, `visible_projects_where/params` (Tasks 1–2); `people.person_cols/person_from/load_directory_config` (Task 3); `project_events.record` (Task 4).
- Produces:
  - `people.assert_addable(conn, user_id: str) -> None` — 404 `{"error": "not_found", "message": "User not found"}` unless the id is a UUID of an existing, non-disabled user.
  - `project_policy.ProjectPolicy(creation: str, limit_per_user: int)`, `load_project_policy(env=os.environ)`, `async check_can_create(conn, principal, policy) -> None`.
  - The project object (§9.1): `{id, name, description, created_at, created_by: {id, name} | None, my_role, member_count, doc_count, can}`, returned by `POST /v1/projects` (201), `GET /v1/projects` (list), `GET /v1/projects/{id}`, `PATCH /v1/projects/{id}`.
  - In `projects.py`: `_reader = deps.require_capability("reader")`, `_show_email() -> bool`, `async _load_project(conn, principal, project_id) -> dict` (Tasks 6–7 reuse them).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_projects_api.py`:

```python
"""A0 §9.1: projects — the project object with `my_role` and `can`, the
creation policy and per-user limit, and events for changes."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import projects as projects_router
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, *, first=None, status="active"):
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io", first_name=first)
    await set_status(conn, u["id"], status)
    return u["id"]


def _p(uid, *, admin=False):
    return deps.Principal(user_id=uid, email=f"{uid}@x.io", role="admin" if admin else "member",
                          capabilities=frozenset({"reader"}))


def _client(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _events(db_conn, pid):
    cur = await db_conn.execute(
        "SELECT kind, details FROM project_events WHERE project_id=%s ORDER BY id", (pid,))
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def test_create_returns_the_project_object_and_records_creation(db_conn):
    me = await _user(db_conn, "c1", first="Cara")
    async with _client(db_conn, _p(me)) as c:
        r = await c.post("/v1/projects", json={"name": "  Q3 Audit ", "description": "Evidence"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Q3 Audit"
    assert body["my_role"] == "owner" and body["member_count"] == 1 and body["doc_count"] == 0
    assert body["created_by"] == {"id": me, "name": "Cara"}
    assert body["can"] == authz.can_for("owner", False)
    assert "is_owner" not in body and "owner_user_id" not in body
    assert await _events(db_conn, body["id"]) == [("project.created", {"name": "Q3 Audit"})]


@pytest.mark.parametrize("method,payload", [
    ("post", {"name": "   "}), ("patch", {"name": "  "}), ("patch", {"name": None}),
])
async def test_blank_project_name_is_422(db_conn, method, payload):
    me = await _user(db_conn, "b1")
    pid = await seed.make_project(db_conn, me)
    async with _client(db_conn, _p(me)) as c:
        url = "/v1/projects" if method == "post" else f"/v1/projects/{pid}"
        assert (await getattr(c, method)(url, json=payload)).status_code == 422


async def test_list_shows_only_my_projects_with_my_role_and_counts(db_conn):
    me, other = await _user(db_conn, "l1"), await _user(db_conn, "l2")
    mine = await seed.make_project(db_conn, other, "Theirs, I read", members=[(me, "reader")])
    await seed.make_project(db_conn, other, "Not mine")
    await seed.seed_doc(db_conn, "a" * 64, other, project_ids=[mine])
    async with _client(db_conn, _p(me)) as c:
        rows = (await c.get("/v1/projects")).json()
    assert [(r["name"], r["my_role"], r["member_count"], r["doc_count"]) for r in rows] == [
        ("Theirs, I read", "reader", 2, 1)]
    assert rows[0]["can"] == authz.can_for("reader", False)


async def test_get_project_member_200_non_member_404_bad_id_422(db_conn):
    owner, other = await _user(db_conn, "g1"), await _user(db_conn, "g2")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.get(f"/v1/projects/{pid}")).json()["my_role"] == "owner"
        assert (await c.get("/v1/projects/not-a-uuid")).status_code == 422
    async with _client(db_conn, _p(other)) as c:
        r = await c.get(f"/v1/projects/{pid}")
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found", "message": authz.PROJECT_NOT_FOUND}


async def test_admin_who_is_not_a_member_sees_the_project_with_admin_powers(db_conn):
    owner, admin = await _user(db_conn, "a1"), await _user(db_conn, "a2")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(admin, admin=True)) as c:
        body = (await c.get(f"/v1/projects/{pid}")).json()
    assert body["my_role"] is None
    assert body["can"] == authz.can_for(None, True)


async def test_patch_needs_maintainer_and_records_only_real_changes(db_conn):
    owner, m, c_ = await _user(db_conn, "p1"), await _user(db_conn, "p2"), await _user(db_conn, "p3")
    pid = await seed.make_project(db_conn, owner, "Old", members=[(m, "maintainer"), (c_, "contributor")])
    async with _client(db_conn, _p(c_)) as c:
        r = await c.patch(f"/v1/projects/{pid}", json={"name": "Hijack"})
    assert r.status_code == 403 and r.json()["detail"]["required"] == "maintainer"
    async with _client(db_conn, _p(m)) as c:
        r = await c.patch(f"/v1/projects/{pid}", json={"name": "New", "description": "Why"})
        assert r.status_code == 200 and r.json()["name"] == "New"
        await c.patch(f"/v1/projects/{pid}", json={"name": "New"})  # no change, no event
    assert await _events(db_conn, pid) == [
        ("project.renamed", {"from": "Old", "to": "New"}), ("project.described", {})]


async def test_delete_needs_owner_or_admin(db_conn):
    owner, m, admin = await _user(db_conn, "d1"), await _user(db_conn, "d2"), await _user(db_conn, "d3")
    p1 = await seed.make_project(db_conn, owner, members=[(m, "maintainer")])
    p2 = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(m)) as c:
        assert (await c.delete(f"/v1/projects/{p1}")).status_code == 403
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.delete(f"/v1/projects/{p1}")).status_code == 204
    async with _client(db_conn, _p(admin, admin=True)) as c:
        assert (await c.delete(f"/v1/projects/{p2}")).status_code == 204


async def test_creation_restricted_to_admins(db_conn, monkeypatch):
    monkeypatch.setenv("PROJECT_CREATION", "admins")
    me, admin = await _user(db_conn, "r1"), await _user(db_conn, "r2")
    async with _client(db_conn, _p(me)) as c:
        r = await c.post("/v1/projects", json={"name": "X"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "project_creation_restricted"
    async with _client(db_conn, _p(admin, admin=True)) as c:
        assert (await c.post("/v1/projects", json={"name": "X"})).status_code == 201


async def test_project_limit_default_override_unlimited_and_admins(db_conn, monkeypatch, caplog):
    monkeypatch.setenv("PROJECT_LIMIT_PER_USER", "1")
    me, admin = await _user(db_conn, "m1"), await _user(db_conn, "m2")
    async with _client(db_conn, _p(me)) as c:
        assert (await c.post("/v1/projects", json={"name": "One"})).status_code == 201
        r = await c.post("/v1/projects", json={"name": "Two"})
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == "project_limit" and r.json()["detail"]["limit"] == 1
        await db_conn.execute("UPDATE users SET project_limit = 2 WHERE id = %s", (me,))
        assert (await c.post("/v1/projects", json={"name": "Two"})).status_code == 201
        await db_conn.execute("UPDATE users SET project_limit = 0 WHERE id = %s", (me,))
        assert (await c.post("/v1/projects", json={"name": "Three"})).status_code == 201
    assert "project limit reached" in caplog.text
    async with _client(db_conn, _p(admin, admin=True)) as c:
        for n in range(3):
            assert (await c.post("/v1/projects", json={"name": f"A{n}"})).status_code == 201


async def test_admin_creates_a_project_for_someone_else(db_conn):
    admin, owner = await _user(db_conn, "o1"), await _user(db_conn, "o2", status="pending")
    gone = await _user(db_conn, "o3", status="disabled")
    async with _client(db_conn, _p(admin, admin=True)) as c:
        r = await c.post("/v1/projects", json={"name": "Theirs", "owner_user_id": owner})
        assert r.status_code == 201
        body = r.json()
        assert body["my_role"] is None and body["created_by"]["id"] == admin
        assert (await c.post("/v1/projects", json={"name": "X", "owner_user_id": gone})).status_code == 404
    cur = await db_conn.execute(
        "SELECT user_id, role, added_via FROM project_members WHERE project_id=%s", (body["id"],))
    assert [(str(r[0]), r[1], r[2]) for r in await cur.fetchall()] == [(owner, "owner", "admin")]
    async with _client(db_conn, _p(owner)) as c:
        r = await c.post("/v1/projects", json={"name": "X", "owner_user_id": admin})
    assert r.status_code == 403 and r.json()["detail"]["required"] == "admin"


async def test_project_routes_need_the_reader_capability(db_conn):
    me = await _user(db_conn, "n1")
    capless = deps.Principal(user_id=me, email="n1@x.io", role="member", capabilities=frozenset())
    async with _client(db_conn, capless) as c:
        assert (await c.get("/v1/projects")).status_code == 403
        assert (await c.post("/v1/projects", json={"name": "X"})).status_code == 403
```

In `server/tests/test_projects_router.py`, delete `test_create_and_list_project`, `test_non_owner_cannot_patch`, `test_non_owner_cannot_delete_project` and `test_list_excludes_other_users_projects` (each is covered above with A0 semantics).

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_projects_api.py -q`
Expected: FAIL — `test_create_returns_the_project_object…` fails on `body["my_role"]` (KeyError) and the policy module import is missing.

- [ ] **Step 3: Add `assert_addable` and the policy module**

Append to `server/services/people.py` (and add `import uuid` and `from ..http_errors import refusal` to its imports):

```python
async def assert_addable(conn, user_id: str) -> None:
    """404 "User not found" unless `user_id` is an existing account that isn't
    disabled (A0 §4: disabled people can't be added or shared with)."""
    try:
        uuid.UUID(str(user_id))
    except ValueError:
        raise refusal(404, "not_found", "User not found")
    cur = await conn.execute("SELECT status FROM users WHERE id = %s", (str(user_id),))
    row = await cur.fetchone()
    if row is None or row[0] == "disabled":
        raise refusal(404, "not_found", "User not found")
```

Create `server/services/project_policy.py`:

```python
"""Who may create projects, and how many (A0 §5, §12).

PROJECT_CREATION: `readers` (anyone with the reader capability, the default)
or `admins`. PROJECT_LIMIT_PER_USER: projects a person may create (counted
by `projects.created_by`); default 20, 0 = unlimited. An admin can override
it per user (`users.project_limit`, NULL = the default; 0 = unlimited).
Admins are never limited. Invalid values log a WARNING and use the default."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..http_errors import refusal

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class ProjectPolicy:
    creation: str
    limit_per_user: int


def load_project_policy(env: Mapping[str, str] = os.environ) -> ProjectPolicy:
    creation = env.get("PROJECT_CREATION", "").strip().lower() or "readers"
    if creation not in ("readers", "admins"):
        logger.warning("PROJECT_CREATION=%r is not readers or admins; using readers", creation)
        creation = "readers"
    raw = env.get("PROJECT_LIMIT_PER_USER", "").strip() or str(DEFAULT_LIMIT)
    try:
        limit = int(raw)
    except ValueError:
        limit = -1
    if limit < 0:
        logger.warning("PROJECT_LIMIT_PER_USER=%r is not a whole number >= 0; using %d",
                       raw, DEFAULT_LIMIT)
        limit = DEFAULT_LIMIT
    return ProjectPolicy(creation=creation, limit_per_user=limit)


async def check_can_create(conn, principal, policy: ProjectPolicy) -> None:
    if principal.role == "admin":
        return
    if policy.creation == "admins":
        raise refusal(403, "project_creation_restricted", "Only admins can create projects here.")
    # Locking the creator's row serializes one person's concurrent creates,
    # so two at once can't both slip under the limit.
    cur = await conn.execute(
        "SELECT project_limit FROM users WHERE id = %s FOR UPDATE", (principal.user_id,))
    row = await cur.fetchone()
    limit = row[0] if row is not None and row[0] is not None else policy.limit_per_user
    if limit == 0:
        return
    cur = await conn.execute("SELECT count(*) FROM projects WHERE created_by = %s",
                             (principal.user_id,))
    if (await cur.fetchone())[0] >= limit:
        logger.warning("project limit reached: user %s, limit %d", principal.user_id, limit)
        raise refusal(429, "project_limit",
                      f"You've reached your project limit ({limit}). Ask an admin to raise it.",
                      limit=limit)
```

- [ ] **Step 4: Rewrite the project routes**

In `server/routers/projects.py`, replace the module docstring, the imports, `ProjectIn`, `ProjectPatch`, `_row`, `create_project`, `list_projects`, `patch_project` and `delete_project` with the code below. Keep `_assert_owner`, `add_member`, `remove_member`, `link_doc` and `unlink_doc` as they are (Tasks 6 and 7 replace them).

```python
"""/v1/projects — projects, their members, documents and activity (A0).
Membership (`project_members.role`, Reader < Contributor < Maintainer <
Owner) is the only source of access. Every route's first check is
`require_project_role`: a non-member gets 404 before any other id is
examined, and a member below the route's role gets 403 insufficient_role.
Project responses carry `can` (authz.can_for), so the UI never re-derives
the rules. Documents (A1 §5): a Contributor who holds an upload entry files
them in; only a Maintainer or Owner removes them."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..audit import audit
from ..auth import deps
from ..auth.authz import (
    assert_holds_upload,
    can_for,
    can_manage_project_docs,
    require_project_role,
    visible_projects_params,
    visible_projects_where,
)
from ..http_errors import refusal
from ..services import doc_content, people, project_events, project_policy
from .docs import DocId

router = APIRouter(prefix="/v1/projects", tags=["projects"])
_reader = deps.require_capability("reader")


def _clean_name(v: str | None) -> str:
    if v is None or not v.strip():
        raise ValueError("name must not be blank")
    return v.strip()


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    owner_user_id: uuid.UUID | None = None  # admins only: create for someone else

    clean_name = field_validator("name")(_clean_name)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None

    clean_name = field_validator("name")(_clean_name)


def _show_email() -> bool:
    return people.load_directory_config().show_email


def _project_sql(where: str) -> str:
    """Bind [caller user_id, *where params]."""
    return (
        "SELECT p.id, p.name, p.description, p.created_at, "
        f"{people.person_cols('cb')}, "
        "(SELECT role FROM project_members WHERE project_id = p.id AND user_id = %s), "
        "(SELECT count(*) FROM project_members WHERE project_id = p.id), "
        "(SELECT count(*) FROM project_documents WHERE project_id = p.id AND verified) "
        f"FROM projects p LEFT JOIN users cb ON cb.id = p.created_by WHERE {where}"
    )


def _project(r, *, is_admin: bool, show_email: bool) -> dict:
    my_role = r[10]
    return {"id": str(r[0]), "name": r[1], "description": r[2], "created_at": r[3],
            "created_by": people.person_from(r[4:10], show_email),
            "my_role": my_role, "member_count": r[11], "doc_count": r[12],
            "can": can_for(my_role, is_admin)}


async def _load_project(conn, principal, project_id) -> dict:
    cur = await conn.execute(_project_sql("p.id = %s"), (principal.user_id, project_id))
    return _project(await cur.fetchone(), is_admin=principal.role == "admin",
                    show_email=_show_email())


@router.post("", status_code=201)
async def create_project(body: ProjectIn, principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    if body.owner_user_id is not None and principal.role != "admin":
        raise refusal(403, "insufficient_role",
                      "Only admins can create a project for someone else.", required="admin")
    await project_policy.check_can_create(conn, principal, project_policy.load_project_policy())
    owner_id = str(body.owner_user_id) if body.owner_user_id else principal.user_id
    if owner_id != principal.user_id:
        await people.assert_addable(conn, owner_id)
    cur = await conn.execute(
        "INSERT INTO projects (created_by, name, description) VALUES (%s,%s,%s) RETURNING id",
        (principal.user_id, body.name, body.description))
    pid = str((await cur.fetchone())[0])
    via = "member" if owner_id == principal.user_id else "admin"
    await conn.execute(
        "INSERT INTO project_members (project_id, user_id, role, added_by, added_via) "
        "VALUES (%s,%s,'owner',%s,%s)", (pid, owner_id, principal.user_id, via))
    await project_events.record(
        conn, pid, principal.user_id, "project.created",
        subject_user_id=None if via == "member" else owner_id, details={"name": body.name})
    return await _load_project(conn, principal, pid)


@router.get("")
async def list_projects(principal: deps.Principal = Depends(_reader),
                        conn=Depends(deps.get_conn)):
    """Projects the caller is a member of, newest first. Admins too: every
    project is listed under /v1/admin/projects."""
    cur = await conn.execute(
        _project_sql(visible_projects_where("p")) + " ORDER BY p.created_at DESC",
        [principal.user_id, *visible_projects_params(principal.user_id)])
    show_email = _show_email()
    return [_project(r, is_admin=principal.role == "admin", show_email=show_email)
            for r in await cur.fetchall()]


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                      conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None)
    return await _load_project(conn, principal, project_id)


@router.patch("/{project_id}")
async def patch_project(project_id: uuid.UUID, body: ProjectPatch,
                        principal: deps.Principal = Depends(_reader),
                        conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "maintainer")
    data = body.model_dump(exclude_unset=True)
    cur = await conn.execute("SELECT name, description FROM projects WHERE id = %s", (project_id,))
    old_name, old_description = await cur.fetchone()
    if "name" in data and data["name"] != old_name:
        await conn.execute("UPDATE projects SET name = %s WHERE id = %s", (data["name"], project_id))
        await project_events.record(conn, project_id, principal.user_id, "project.renamed",
                                    details={"from": old_name, "to": data["name"]})
    if "description" in data and data["description"] != old_description:
        await conn.execute("UPDATE projects SET description = %s WHERE id = %s",
                           (data["description"], project_id))
        await project_events.record(conn, project_id, principal.user_id, "project.described")
    return await _load_project(conn, principal, project_id)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                         conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, "owner", lock=True)
    # Sorted, like admin delete_user's GC: row locks in one order can't deadlock.
    cur = await conn.execute(
        "SELECT doc_id FROM project_documents WHERE project_id = %s ORDER BY doc_id",
        (project_id,))
    doc_ids = [r[0] for r in await cur.fetchall()]
    await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    audit("project.deleted", project=project_id, by=principal.user_id)
    for doc_id in doc_ids:
        await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="project_deleted")
    return Response(status_code=204)
```

The transitional `_assert_owner` still used by `add_member`/`remove_member` stays until Task 6.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_projects_api.py server/tests/test_projects_router.py -q`
Expected: all passed.

- [ ] **Step 6: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed.

- [ ] **Step 7: Commit** (ask first)

```bash
git add server/services/project_policy.py server/services/people.py server/routers/projects.py server/tests/test_projects_api.py server/tests/test_projects_router.py
git commit -m "feat(projects): the project object (my_role, counts, can), GET one project, Maintainer edits, Owner deletes, PROJECT_CREATION and PROJECT_LIMIT_PER_USER, admin creates for someone else (A0 Task 5)"
```

Between this task and Task 12 the Library's project chips read `is_owner`, which the API no longer sends: in the running app no "×" appears on project chips until Task 12. Tests are unaffected (they mock the API).

---

### Task 6: Members — list, add or re-role, remove or leave, and the last-owner race

**Files:**
- Create: `server/services/project_members.py`
- Modify: `server/routers/projects.py` (replace `_assert_owner`, `add_member`, `remove_member` with three member routes)
- Create: `server/tests/test_project_members.py`
- Modify: `server/tests/test_projects_router.py` (delete the five superseded member tests)

**Interfaces:**
- Consumes: `require_project_role`, `project_role`, `role_at_least`, `insufficient_role` (Task 2); `people.*`, `people.assert_addable` (Tasks 3, 5); `project_events.record` (Task 4); `_reader`, `_show_email` (Task 5).
- Produces:
  - `async list_members(conn, project_id, *, show_email: bool, user_id: str | None = None) -> list[dict]` — `[{user_id, name, username, email?, status, role, added_at, added_by, added_via}]`, Owners first then by name.
  - `async put_member(conn, principal, project_id, user_id: str, role: str, *, show_email: bool) -> dict` (one member dict).
  - `async remove_member(conn, principal, project_id, user_id: str) -> None`.
  - Routes: `GET /v1/projects/{id}/members`, `PUT /v1/projects/{id}/members/{user_id}` with `{"role": …}` → 200, `DELETE /v1/projects/{id}/members/{user_id}` → 204.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_project_members.py`:

```python
"""A0 §5, §9.2, §3.3: members — roles, the Maintainer/Owner boundary, the
last-owner rule (including two Owners racing), leaving, admin self-add."""
import asyncio
import logging
import secrets

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport
from psycopg import AsyncConnection

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import projects as projects_router
from server.services import project_members
from server.tests import seed
from server.tests.dbutil import TEST_URL

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, *, first=None, status="active"):
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io", first_name=first)
    await set_status(conn, u["id"], status)
    return u["id"]


def _p(uid, *, admin=False):
    return deps.Principal(user_id=uid, email=f"{uid}@x.io", role="admin" if admin else "member",
                          capabilities=frozenset({"reader"}))


def _client(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _role(db_conn, pid, uid):
    return await authz.project_role(db_conn, uid, pid)


async def _kinds(db_conn, pid):
    cur = await db_conn.execute(
        "SELECT kind, details FROM project_events WHERE project_id=%s ORDER BY id", (pid,))
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def test_owner_adds_a_member_with_a_role(db_conn):
    owner, ben = await _user(db_conn, "o", first="Olu"), await _user(db_conn, "b", first="Ben")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "contributor"})
    assert r.status_code == 200
    m = r.json()
    assert (m["user_id"], m["name"], m["role"], m["added_via"], m["status"]) == (
        ben, "Ben", "contributor", "member", "active")
    assert m["added_by"] == {"id": owner, "name": "Olu"}
    assert "email" not in m
    assert await _kinds(db_conn, pid) == [("member.added", {"role": "contributor", "via": "member"})]


@pytest.mark.parametrize("payload", [None, {}, {"role": "admin"}, {"role": "owner", "x": 1}])
async def test_put_needs_a_known_role(db_conn, payload):
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        kwargs = {} if payload is None else {"json": payload}
        assert (await c.put(f"/v1/projects/{pid}/members/{ben}", **kwargs)).status_code == 422


async def test_same_role_is_a_no_op_and_a_change_is_recorded(db_conn):
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner, members=[(ben, "reader")])
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "reader"})).status_code == 200
        assert (await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "maintainer"})).status_code == 200
    assert await _kinds(db_conn, pid) == [("member.role_changed", {"from": "reader", "to": "maintainer"})]


async def test_maintainers_manage_up_to_maintainer_but_never_owners(db_conn):
    owner, m, x = await _user(db_conn, "o"), await _user(db_conn, "m"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner, members=[(m, "maintainer")])
    async with _client(db_conn, _p(m)) as c:
        assert (await c.put(f"/v1/projects/{pid}/members/{x}", json={"role": "maintainer"})).status_code == 200
        for call in (c.put(f"/v1/projects/{pid}/members/{x}", json={"role": "owner"}),
                     c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "reader"}),
                     c.delete(f"/v1/projects/{pid}/members/{owner}"),
                     c.put(f"/v1/projects/{pid}/members/{m}", json={"role": "owner"})):
            r = await call
            assert r.status_code == 403 and r.json()["detail"]["required"] == "owner"
        assert (await c.delete(f"/v1/projects/{pid}/members/{x}")).status_code == 204
    assert await _role(db_conn, pid, owner) == "owner"


async def test_readers_and_contributors_cannot_manage_members(db_conn):
    owner, r_, x = await _user(db_conn, "o"), await _user(db_conn, "r"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner, members=[(r_, "contributor")])
    async with _client(db_conn, _p(r_)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{x}", json={"role": "reader"})
    assert r.status_code == 403 and r.json()["detail"]["required"] == "maintainer"


async def test_non_member_gets_404_before_the_user_id_is_examined(db_conn):
    owner, stranger = await _user(db_conn, "o"), await _user(db_conn, "s")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(stranger)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/not-a-uuid", json={"role": "reader"})
    assert r.status_code == 404
    assert r.json()["detail"]["message"] == authz.PROJECT_NOT_FOUND


@pytest.mark.parametrize("target", ["missing", "malformed", "disabled"])
async def test_unknown_malformed_or_disabled_people_are_not_found(db_conn, target):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    uid = {"missing": "00000000-0000-0000-0000-00000000beef", "malformed": "nope",
           "disabled": await _user(db_conn, "d", status="disabled")}[target]
    async with _client(db_conn, _p(owner)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{uid}", json={"role": "reader"})
        assert r.status_code == 404
        assert r.json()["detail"]["message"] == "User not found"
        assert (await c.get(f"/v1/projects/{pid}")).status_code == 200  # transaction still usable


async def test_pending_people_can_be_added(db_conn):
    owner, pending = await _user(db_conn, "o"), await _user(db_conn, "p", status="pending")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{pending}", json={"role": "reader"})
    assert r.status_code == 200 and r.json()["status"] == "pending"


async def test_the_last_owner_cannot_demote_remove_or_leave(db_conn, caplog):
    owner, second = await _user(db_conn, "o"), await _user(db_conn, "s")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        for call in (c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "maintainer"}),
                     c.delete(f"/v1/projects/{pid}/members/{owner}")):
            r = await call
            assert r.status_code == 409 and r.json()["detail"]["error"] == "last_owner"
        assert (await c.put(f"/v1/projects/{pid}/members/{second}", json={"role": "owner"})).status_code == 200
        assert (await c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "maintainer"})).status_code == 200
    assert "last-owner guard" in caplog.text


async def test_any_member_can_leave(db_conn):
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner, members=[(ben, "reader")])
    async with _client(db_conn, _p(ben)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/members/{ben}")).status_code == 204
        assert (await c.get(f"/v1/projects/{pid}")).status_code == 404
    assert await _kinds(db_conn, pid) == [("member.left", {"role": "reader"})]


async def test_admin_adds_themselves_to_an_ownerless_project(db_conn, caplog):
    admin = await _user(db_conn, "a")
    pid = await seed.make_project(db_conn, None)
    with caplog.at_level(logging.WARNING):
        async with _client(db_conn, _p(admin, admin=True)) as c:
            r = await c.put(f"/v1/projects/{pid}/members/{admin}", json={"role": "owner"})
    assert r.status_code == 200 and r.json()["added_via"] == "admin"
    assert f"admin {admin} added themselves to project {pid} as owner" in caplog.text


async def test_members_list_owners_first_and_email_only_when_shown(db_conn, monkeypatch):
    owner, ann, zed = (await _user(db_conn, "o", first="Olu"), await _user(db_conn, "a", first="Ann"),
                       await _user(db_conn, "z", first="Zed"))
    pid = await seed.make_project(db_conn, owner, members=[(zed, "maintainer"), (ann, "reader")])
    async with _client(db_conn, _p(ann)) as c:
        rows = (await c.get(f"/v1/projects/{pid}/members")).json()
        assert [(m["name"], m["role"]) for m in rows] == [
            ("Olu", "owner"), ("Zed", "maintainer"), ("Ann", "reader")]
        assert all("email" not in m for m in rows)
        monkeypatch.setenv("USER_DIRECTORY_SHOW_EMAIL", "true")
        rows = (await c.get(f"/v1/projects/{pid}/members")).json()
    assert rows[0]["email"] == "o@x.io"


async def test_a_removed_member_immediately_loses_read_access(db_conn):
    """§11 read access: membership is the only path; removing it closes it."""
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner, members=[(ben, "reader")])
    await seed.seed_doc(db_conn, "c" * 64, owner, project_ids=[pid])
    await authz.assert_can_read_doc(db_conn, "c" * 64, ben)  # a Reader reads
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/members/{ben}")).status_code == 204
    with pytest.raises(HTTPException) as e:
        await authz.assert_can_read_doc(db_conn, "c" * 64, ben)
    assert e.value.status_code == 404


async def test_two_owners_demoting_each_other_at_once_leave_exactly_one_owner():
    """§3.3. Without the project row lock, B's request would read the state
    before A's commit, see "another Owner remains", and both demotions would
    commit: zero Owners. With it, B waits for A and is then refused."""
    tag = secrets.token_hex(4)
    setup = await AsyncConnection.connect(TEST_URL, autocommit=True)
    a_conn = await AsyncConnection.connect(TEST_URL)
    b_conn = await AsyncConnection.connect(TEST_URL)
    pid, ids = None, []
    try:
        for who in ("a", "b"):
            u = await resolve_or_provision_user(setup, iss="race", sub=f"{who}-{tag}",
                                                email=f"{who}-{tag}@race.example.com")
            ids.append(u["id"])
        a, b = ids
        pid = await seed.make_project(setup, a, f"Race {tag}", members=[(b, "owner")])

        await project_members.put_member(a_conn, _p(a), pid, b, "maintainer", show_email=False)
        b_task = asyncio.create_task(
            project_members.put_member(b_conn, _p(b), pid, a, "maintainer", show_email=False))
        await asyncio.sleep(0.5)
        assert not b_task.done()  # waiting on the project row lock A holds
        await a_conn.commit()
        with pytest.raises(HTTPException) as e:
            await b_task
        assert e.value.status_code == 403  # B is a Maintainer now; Owner rows are Owner-only
        await b_conn.rollback()
        cur = await setup.execute(
            "SELECT user_id FROM project_members WHERE project_id = %s AND role = 'owner'", (pid,))
        assert [str(r[0]) for r in await cur.fetchall()] == [a]
    finally:
        await a_conn.close()
        await b_conn.close()
        if pid:
            await setup.execute("DELETE FROM projects WHERE id = %s", (pid,))
        if ids:
            await setup.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", (ids,))
        await setup.close()
```

In `server/tests/test_projects_router.py`, delete `test_owner_adds_and_removes_member`, `test_non_owner_cannot_add_member`, `test_non_owner_cannot_remove_member`, `test_add_member_nonexistent_user_404` and `test_add_member_malformed_user_404`, and the `_app` helper and `import uuid` if nothing else in the file uses them.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_project_members.py -q`
Expected: FAIL — `ImportError: cannot import name 'project_members' from 'server.services'`.

- [ ] **Step 3: Write the members service**

Create `server/services/project_members.py`:

```python
"""Project membership changes (A0 §2, §3.3, §5).

Every write first takes the project's row lock (via require_project_role
lock=True) and reads the caller's role after it, so two writes on one
project run one at a time: two Owners demoting each other at once can't
leave a project with no Owner. A Maintainer manages Readers, Contributors
and Maintainers; Owner rows (adding, changing, removing an Owner, or making
someone one) need an Owner — or an admin, recorded as added_via='admin'."""
from __future__ import annotations

import logging
import uuid

from ..auth.authz import insufficient_role, project_role, require_project_role, role_at_least
from ..http_errors import refusal
from . import people, project_events

logger = logging.getLogger(__name__)

_ROLE_ORDER_SQL = "array_position(ARRAY['owner','maintainer','contributor','reader'], m.role)"


def _member(r, show_email: bool) -> dict:
    out = {
        "user_id": str(r[0]),
        "name": people.person_label(first_name=r[1], last_name=r[2], display_name=r[3],
                                    username=r[4], email=r[5], show_email=show_email),
        "username": people.shown_username(r[4], show_email),
        "status": r[6], "role": r[7], "added_at": r[8], "added_via": r[9],
        "added_by": people.person_from(r[10:16], show_email),
    }
    if show_email:
        out["email"] = r[5]
    return out


async def list_members(conn, project_id, *, show_email: bool,
                       user_id: str | None = None) -> list[dict]:
    cur = await conn.execute(
        f"SELECT {people.person_cols('u')}, u.status, m.role, m.added_at, m.added_via, "
        f"{people.person_cols('ab')} "
        "FROM project_members m JOIN users u ON u.id = m.user_id "
        "LEFT JOIN users ab ON ab.id = m.added_by "
        "WHERE m.project_id = %s AND (%s::uuid IS NULL OR m.user_id = %s::uuid) "
        f"ORDER BY {_ROLE_ORDER_SQL}, "
        "lower(coalesce(u.first_name, u.display_name, u.username, u.email)), u.id",
        (project_id, user_id, user_id))
    return [_member(r, show_email) for r in await cur.fetchall()]


def _authority(access, needed: str) -> str:
    """'member' when the caller's own role suffices; 'admin' when only the
    admin capability does; otherwise 403 naming the role needed."""
    if role_at_least(access.role, needed):
        return "member"
    if access.is_admin:
        return "admin"
    raise insufficient_role(needed)


async def _other_owners(conn, project_id, user_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM project_members WHERE project_id = %s AND role = 'owner' "
        "AND user_id <> %s", (project_id, user_id))
    return (await cur.fetchone())[0]


def _last_owner(project_id, user_id: str):
    logger.warning("last-owner guard: project %s, member %s", project_id, user_id)
    return refusal(409, "last_owner", "A project must keep at least one Owner.")


def _same_user(user_id: str, other: str) -> bool:
    try:
        return str(uuid.UUID(user_id)) == other
    except ValueError:
        return False


async def put_member(conn, principal, project_id, user_id: str, role: str, *,
                     show_email: bool) -> dict:
    access = await require_project_role(conn, principal, project_id, "maintainer", lock=True)
    await people.assert_addable(conn, user_id)
    user_id = str(uuid.UUID(user_id))
    current = await project_role(conn, user_id, project_id)
    via = _authority(access, "owner" if "owner" in (role, current) else "maintainer")
    if current != role:
        if current == "owner" and await _other_owners(conn, project_id, user_id) == 0:
            raise _last_owner(project_id, user_id)
        if current is None:
            await conn.execute(
                "INSERT INTO project_members (project_id, user_id, role, added_by, added_via) "
                "VALUES (%s,%s,%s,%s,%s)", (project_id, user_id, role, principal.user_id, via))
            await project_events.record(conn, project_id, principal.user_id, "member.added",
                                        subject_user_id=user_id,
                                        details={"role": role, "via": via})
            if via == "admin" and user_id == principal.user_id:
                logger.warning("admin %s added themselves to project %s as %s",
                               principal.user_id, project_id, role)
        else:
            await conn.execute(
                "UPDATE project_members SET role = %s WHERE project_id = %s AND user_id = %s",
                (role, project_id, user_id))
            await project_events.record(conn, project_id, principal.user_id,
                                        "member.role_changed", subject_user_id=user_id,
                                        details={"from": current, "to": role})
    return (await list_members(conn, project_id, show_email=show_email, user_id=user_id))[0]


async def remove_member(conn, principal, project_id, user_id: str) -> None:
    leaving = _same_user(user_id, principal.user_id)
    if leaving:
        access = await require_project_role(conn, principal, project_id, None, lock=True)
        if access.role is None:  # an admin who isn't a member
            raise refusal(404, "not_found", "You aren't a member of this project.")
        target, current = principal.user_id, access.role
    else:
        access = await require_project_role(conn, principal, project_id, "maintainer", lock=True)
        try:
            target = str(uuid.UUID(user_id))
        except ValueError:
            raise refusal(404, "not_found", "Member not found")
        current = await project_role(conn, target, project_id)
        if current is None:
            raise refusal(404, "not_found", "Member not found")
        _authority(access, "owner" if current == "owner" else "maintainer")
    if current == "owner" and await _other_owners(conn, project_id, target) == 0:
        raise _last_owner(project_id, target)
    await conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
                       (project_id, target))
    await project_events.record(conn, project_id, principal.user_id,
                                "member.left" if leaving else "member.removed",
                                subject_user_id=None if leaving else target,
                                details={"role": current})
```

- [ ] **Step 4: Replace the member routes**

In `server/routers/projects.py`, delete `_assert_owner`, `add_member` and `remove_member`, drop the now-unused `HTTPException` and `pg_errors` imports, add `from typing import Literal` and `project_members` to the services import, and add:

```python
class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["reader", "contributor", "maintainer", "owner"]


@router.get("/{project_id}/members")
async def get_members(project_id: uuid.UUID, principal: deps.Principal = Depends(_reader),
                      conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None)
    return await project_members.list_members(conn, project_id, show_email=_show_email())


@router.put("/{project_id}/members/{user_id}")
async def put_member(project_id: uuid.UUID, user_id: str, body: MemberIn,
                     principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """Add a person or change their role (A0 §9.2). The body is required: a
    body-less PUT (accepted before A0) is a 422."""
    return await project_members.put_member(conn, principal, project_id, user_id, body.role,
                                            show_email=_show_email())


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def delete_member(project_id: uuid.UUID, user_id: str,
                        principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """Remove a member, or leave when `user_id` is the caller's own."""
    await project_members.remove_member(conn, principal, project_id, user_id)
    return Response(status_code=204)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_project_members.py server/tests/test_projects_router.py -q`
Expected: all passed. To prove the race test guards the lock, temporarily change `lock=True` to `lock=False` in `put_member`, rerun `-k two_owners` (expected: FAIL at `assert not b_task.done()`), then restore it.

- [ ] **Step 6: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed.

- [ ] **Step 7: Commit** (ask first)

```bash
git add server/services/project_members.py server/routers/projects.py server/tests/test_project_members.py server/tests/test_projects_router.py
git commit -m "feat(projects): members — list, add or re-role with a required role, remove or leave; Owner rows are Owner-only; the last Owner stays, even when two Owners race (row lock) (A0 Task 6)"
```

---

### Task 7: Documents in a project, and the activity endpoint

**Files:**
- Modify: `server/routers/projects.py` (`link_doc`, `unlink_doc`; new `get_events`)
- Modify: `server/tests/test_projects_router.py`
- Create: `server/tests/test_project_activity.py`

**Interfaces:**
- Consumes: `require_project_role(..., admin_ok=False)` (Task 2); `project_events.record/page` (Task 4); `_reader`, `_show_email` (Task 5).
- Produces: `PUT /v1/projects/{id}/docs/{doc_id}` (Contributor+, member only) and `DELETE …` (Maintainer+, member only) writing `document.added`/`document.removed` with the document's name; `GET /v1/projects/{id}/events?before=&limit=` → `{events, next_before}`.

- [ ] **Step 1: Update the existing document tests to A0 rules**

In `server/tests/test_projects_router.py`:

1. `test_link_into_invisible_project_is_404`: change the detail assertion to `r.json()["detail"] == {"error": "not_found", "message": authz.PROJECT_NOT_FOUND}` and add `from server.auth import authz`.
2. Replace `test_admin_links_own_doc_into_any_project` with:

```python
async def test_admins_file_documents_only_as_members(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="l6o", email="l6o@x.io")
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="l6", email="l6@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await _mk_doc(db_conn, DOC_A, admin["id"])
    async with _client_for(db_conn, _reader(admin, role="admin")) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 404
        await seed.add_member(db_conn, pid, admin["id"], "contributor")
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
```

3. Replace the unlink resolution table (its comment and parametrize list) with the A0 table — role check first, so a member below Maintainer gets 403 whether or not the link exists:

```python
# Unlink resolution table (A0 §5 — projects govern their documents; the role
# check runs before the link is looked up):
#   link exists  -> uploader (contributor) 403, project owner 204, other (contributor) 403
#   link missing -> uploader 403, project owner 404, other 403
@pytest.mark.parametrize("linked,caller,expected", [
    (True, "doc_owner", 403), (True, "project_owner", 204), (True, "other", 403),
    (False, "doc_owner", 403), (False, "project_owner", 404), (False, "other", 403),
])
```

and in the test body change `if expected == 404:` to:

```python
        if expected == 404:
            assert r.json()["detail"] == {"error": "not_found", "message": "Not found"}
        if expected == 403:
            assert r.json()["detail"]["required"] == "maintainer"
```

and the final link assertion to `assert await _linked(db_conn, pid, DOC_A) is (linked and expected != 204)`.

4. Add:

```python
async def test_readers_cannot_file_documents(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="rd1o", email="rd1o@x.io")
    u = await resolve_or_provision_user(db_conn, iss="i", sub="rd1", email="rd1@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await seed.add_member(db_conn, pid, u["id"], "reader")
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")
    assert r.status_code == 403 and r.json()["detail"]["required"] == "contributor"
    assert not await _linked(db_conn, pid, DOC_A)


async def test_filing_and_removing_record_events_with_the_document_name(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="ev1", email="ev1@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await seed.seed_doc(db_conn, DOC_A, owner["id"], file_name="Q3 report.pdf")
    async with _client_for(db_conn, _reader(owner)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204  # no 2nd event
        assert (await c.delete(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
    cur = await db_conn.execute(
        "SELECT kind, doc_id, details->>'name' FROM project_events WHERE project_id=%s "
        "ORDER BY id", (pid,))
    assert [tuple(r) for r in await cur.fetchall()] == [
        ("document.added", DOC_A, "Q3 report.pdf"), ("document.removed", DOC_A, "Q3 report.pdf")]
```

`test_uploader_who_left_the_project_cannot_unlink` stays as it is (a non-member still gets 404).

- [ ] **Step 2: Write the activity endpoint tests**

Create `server/tests/test_project_activity.py`:

```python
"""A0 §9.4: GET /v1/projects/{id}/events — members and admins, newest first, paged."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import projects as projects_router
from server.services import project_events
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, first=None):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io",
                                            first_name=first))["id"]


def _client(db_conn, uid, *, admin=False):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=uid, email="x@x.io", role="admin" if admin else "member",
        capabilities=frozenset({"reader"}))
    app.include_router(projects_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_events_for_members_and_admins_newest_first_and_paged(db_conn):
    owner, reader = await _user(db_conn, "o", "Olu"), await _user(db_conn, "r")
    stranger, admin = await _user(db_conn, "s"), await _user(db_conn, "a")
    pid = await seed.make_project(db_conn, owner, members=[(reader, "reader")])
    for n in range(3):
        await project_events.record(db_conn, pid, owner, "project.described", details={"n": n})
    async with _client(db_conn, reader) as c:
        first = (await c.get(f"/v1/projects/{pid}/events", params={"limit": 2})).json()
        assert [e["details"]["n"] for e in first["events"]] == [2, 1]
        assert first["events"][0]["actor"] == {"id": owner, "name": "Olu"}
        rest = (await c.get(f"/v1/projects/{pid}/events",
                            params={"before": first["next_before"]})).json()
        assert [e["details"]["n"] for e in rest["events"]] == [0] and rest["next_before"] is None
        assert (await c.get(f"/v1/projects/{pid}/events", params={"limit": 0})).status_code == 422
        assert (await c.get(f"/v1/projects/{pid}/events", params={"limit": 101})).status_code == 422
    async with _client(db_conn, stranger) as c:
        assert (await c.get(f"/v1/projects/{pid}/events")).status_code == 404
    async with _client(db_conn, admin, admin=True) as c:
        assert (await c.get(f"/v1/projects/{pid}/events")).status_code == 200
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py server/tests/test_project_activity.py -q`
Expected: FAIL — the activity route 404s (no route), the unlink table's 403 rows get 404, and the events test finds no events.

- [ ] **Step 4: Rewrite the document routes and add the events route**

In `server/routers/projects.py`, replace `link_doc` and `unlink_doc`, drop the unused `can_manage_project_docs`, `visible_*` names only if nothing else uses them (the list route still uses `visible_projects_*`), add `Query` to the FastAPI import, and add the helpers and the events route:

```python
async def _filed_name(conn, doc_id: str, user_id: str) -> str | None:
    """The name the filer sees for the document (their entry's, else the content's)."""
    cur = await conn.execute(
        "SELECT COALESCE(e.file_name, d.file_name) FROM library_entries e "
        "JOIN documents d ON d.doc_id = e.doc_id WHERE e.user_id = %s AND e.doc_id = %s",
        (user_id, doc_id))
    row = await cur.fetchone()
    return row[0] if row else None


async def _placement_name(conn, project_id, doc_id: str) -> str | None:
    """The name the project shows for a placed document: its filer's entry
    name, else the content's canonical name."""
    cur = await conn.execute(
        "SELECT COALESCE(e.file_name, d.file_name) FROM project_documents pd "
        "JOIN documents d ON d.doc_id = pd.doc_id "
        "LEFT JOIN library_entries e ON e.user_id = pd.added_by AND e.doc_id = pd.doc_id "
        "WHERE pd.project_id = %s AND pd.doc_id = %s", (project_id, doc_id))
    row = await cur.fetchone()
    return row[0] if row else None


@router.put("/{project_id}/docs/{doc_id}", status_code=204)
async def link_doc(project_id: uuid.UUID, doc_id: DocId,
                   principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """File a doc into a project: a Contributor or above (admins only through
    a member role, A0 §5) who holds a VERIFIED upload entry for it (A1 §5).
    The project is checked first, so an invisible project 404s before doc
    ids can be used to probe it. Idempotent: an existing placement keeps its
    original `added_by` and writes no event — unless it is an UNVERIFIED
    legacy placement, which this verified filing replaces."""
    await require_project_role(conn, principal, project_id, "contributor", admin_ok=False)
    await assert_holds_upload(conn, doc_id, principal.user_id)
    cur = await conn.execute(
        "INSERT INTO project_documents (project_id, doc_id, added_by, verified) "
        "VALUES (%s,%s,%s,true) ON CONFLICT (project_id, doc_id) DO UPDATE "
        "SET added_by = EXCLUDED.added_by, verified = true "
        "WHERE NOT project_documents.verified", (project_id, doc_id, principal.user_id))
    if cur.rowcount:
        audit("placement.added", project=project_id, doc=doc_id, by=principal.user_id)
        await project_events.record(
            conn, project_id, principal.user_id, "document.added", doc_id=doc_id,
            details={"name": await _filed_name(conn, doc_id, principal.user_id)})
    return Response(status_code=204)


@router.delete("/{project_id}/docs/{doc_id}", status_code=204)
async def unlink_doc(project_id: uuid.UUID, doc_id: DocId,
                     principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    """Remove a doc from a project: a Maintainer or above (A0 §5; admins only
    through a member role). The uploader has no special power over a
    placement. A missing link is 404 — after the role check."""
    await require_project_role(conn, principal, project_id, "maintainer", admin_ok=False)
    name = await _placement_name(conn, project_id, doc_id)
    cur = await conn.execute(
        "DELETE FROM project_documents WHERE project_id = %s AND doc_id = %s",
        (project_id, doc_id))
    if cur.rowcount == 0:
        raise refusal(404, "not_found", "Not found")
    audit("placement.removed", project=project_id, doc=doc_id, by=principal.user_id)
    await project_events.record(conn, project_id, principal.user_id, "document.removed",
                                doc_id=doc_id, details={"name": name})
    await doc_content.gc_content_if_orphaned(conn, doc_id, trigger="placement_removed")
    return Response(status_code=204)


@router.get("/{project_id}/events")
async def get_events(project_id: uuid.UUID, before: int | None = Query(None, ge=1),
                     limit: int = Query(50, ge=1, le=100),
                     principal: deps.Principal = Depends(_reader), conn=Depends(deps.get_conn)):
    await require_project_role(conn, principal, project_id, None)
    return await project_events.page(conn, project_id, before=before, limit=limit,
                                     show_email=_show_email())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py server/tests/test_project_activity.py -q`
Expected: all passed.

- [ ] **Step 6: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed.

- [ ] **Step 7: Commit** (ask first)

```bash
git add server/routers/projects.py server/tests/test_projects_router.py server/tests/test_project_activity.py
git commit -m "feat(projects): Contributors file and Maintainers remove documents (admins only as members), both recorded with the document's name; GET /v1/projects/{id}/events (A0 Task 7)"
```

---

### Task 8: The permission matrix as one table, and the privacy check

**Files:**
- Create: `server/tests/test_project_permission_matrix.py`

**Interfaces:**
- Consumes: every project route (Tasks 5–7).

- [ ] **Step 1: Write the matrix test**

Create `server/tests/test_project_permission_matrix.py`:

```python
"""A0 §5 as one table. Each row is an action; each column a caller. Read it
side by side with the spec's matrix — they must say the same thing.
`admin` is an admin who isn't a member; `admin+c` is an admin who is a
Contributor (admins file/remove documents only through a member role)."""
import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import people as people_router
from server.routers import projects as projects_router
from server.tests import seed

pytestmark = pytest.mark.asyncio

CALLERS = ("none", "reader", "contributor", "maintainer", "owner", "admin", "admin+c")
DOC_MINE = "1" * 64     # the caller holds an upload entry for it
DOC_PLACED = "2" * 64   # already in the project, filed by the base owner

#                 action               none reader contrib maint owner admin admin+c
MATRIX = {
    "see project":     (404, 200, 200, 200, 200, 200, 200),
    "see members":     (404, 200, 200, 200, 200, 200, 200),
    "see activity":    (404, 200, 200, 200, 200, 200, 200),
    "file own doc":    (404, 403, 204, 204, 204, 404, 204),
    "remove any doc":  (404, 403, 403, 204, 204, 404, 403),
    "edit":            (404, 403, 403, 200, 200, 200, 200),
    "add contributor": (404, 403, 403, 200, 200, 200, 200),
    "add owner":       (404, 403, 403, 403, 200, 200, 200),
    "remove an owner": (404, 403, 403, 403, 204, 204, 204),
    "delete project":  (404, 403, 403, 403, 204, 204, 204),
}


def _call(c, action, pid, target, second_owner):
    base = f"/v1/projects/{pid}"
    return {
        "see project": lambda: c.get(base),
        "see members": lambda: c.get(f"{base}/members"),
        "see activity": lambda: c.get(f"{base}/events"),
        "file own doc": lambda: c.put(f"{base}/docs/{DOC_MINE}"),
        "remove any doc": lambda: c.delete(f"{base}/docs/{DOC_PLACED}"),
        "edit": lambda: c.patch(base, json={"name": "Renamed"}),
        "add contributor": lambda: c.put(f"{base}/members/{target}", json={"role": "contributor"}),
        "add owner": lambda: c.put(f"{base}/members/{target}", json={"role": "owner"}),
        "remove an owner": lambda: c.delete(f"{base}/members/{second_owner}"),
        "delete project": lambda: c.delete(base),
    }[action]()


async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


@pytest.mark.parametrize("action", list(MATRIX))
@pytest.mark.parametrize("caller", CALLERS)
async def test_permission_matrix(db_conn, action, caller):
    base_owner, second_owner = await _user(db_conn, "bo"), await _user(db_conn, "so")
    target, me = await _user(db_conn, "tg"), await _user(db_conn, "me")
    member_role = {"reader": "reader", "contributor": "contributor", "maintainer": "maintainer",
                   "owner": "owner", "admin+c": "contributor"}.get(caller)
    members = [(second_owner, "owner")] + ([(me, member_role)] if member_role else [])
    pid = await seed.make_project(db_conn, base_owner, members=members)
    await seed.seed_doc(db_conn, DOC_MINE, me, file_name="mine.pdf")
    await seed.seed_doc(db_conn, DOC_PLACED, base_owner, file_name="placed.pdf", project_ids=[pid])

    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=me, email="me@x.io", role="admin" if caller.startswith("admin") else "member",
        capabilities=frozenset({"reader"}))
    app.include_router(projects_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await _call(c, action, pid, target, second_owner)
    assert r.status_code == MATRIX[action][CALLERS.index(caller)], r.text


async def test_no_personal_data_at_info_or_above(db_conn, caplog, monkeypatch):
    """§7: IDs, roles and counts only — never emails, names, file names or lookup text."""
    monkeypatch.setenv("USER_DIRECTORY_MODE", "open")
    owner = (await resolve_or_provision_user(db_conn, iss="i", sub="pv1", email="olu.secret@x.io",
                                             first_name="Olusegun"))["id"]
    ben = (await resolve_or_provision_user(db_conn, iss="i", sub="pv2", email="ben.secret@x.io",
                                           first_name="Benedikt"))["id"]
    await db_conn.execute("UPDATE users SET status='active' WHERE id = ANY(%s::uuid[])", ([owner, ben],))
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=owner, email="olu.secret@x.io", role="member", capabilities=frozenset({"reader"}))
    app.include_router(projects_router.router)
    app.include_router(people_router.router)
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            pid = (await c.post("/v1/projects", json={"name": "Codename Falcon"})).json()["id"]
            await c.get("/v1/users/lookup", params={"q": "Benedi"})
            await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "maintainer"})
            await c.patch(f"/v1/projects/{pid}", json={"name": "Codename Heron"})
            await c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "reader"})  # 409
            await c.delete(f"/v1/projects/{pid}/members/{ben}")
    text = "\n".join(r.getMessage() for r in caplog.records
                     if r.name.startswith("server") and r.levelno >= logging.INFO)
    for secret in ("secret", "Olusegun", "Benedikt", "Benedi", "Falcon", "Heron"):
        assert secret not in text
    assert f"member.added project={pid}" in text
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest server/tests/test_project_permission_matrix.py -q`
Expected: 70 + 1 passed. Any failure is a real disagreement between the code and §5: fix the code (never the table) unless the spec says otherwise.

- [ ] **Step 3: Commit** (ask first)

```bash
git add server/tests/test_project_permission_matrix.py
git commit -m "test(projects): the A0 permission matrix as one table (7 callers × 10 actions) and a no-personal-data-in-logs check (A0 Task 8)"
```

---

### Task 9: Sharing — list my shares, and never share with a disabled account

**Files:**
- Modify: `server/routers/docs.py` (new `list_shares`; `add_share` refuses disabled users)
- Modify: `server/tests/test_doc_shares.py`

**Interfaces:**
- Consumes: `people.person_cols/person_from/shown_username/load_directory_config/assert_addable` (Tasks 3, 5).
- Produces: `GET /v1/docs/{doc_id}/shares` → `[{user_id, name, username, shared_at}]` (only shares the caller made; caller must hold a verified upload entry, else 404).

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_doc_shares.py`:

```python
async def test_list_shares_shows_only_the_people_i_shared_with(db_conn, docs_app):
    me, ann, bob = await _member(db_conn, "ls1"), await _member(db_conn, "ls2"), await _member(db_conn, "ls3")
    await db_conn.execute("UPDATE users SET first_name='Ann' WHERE id=%s", (ann.user_id,))
    await _insert_doc(db_conn, HEX, me.user_id)
    await seed.share_doc(db_conn, HEX, me.user_id, ann.user_id)
    await seed.seed_doc(db_conn, "b" * 64, bob.user_id, file_name="g", file_type="text")
    await seed.share_doc(db_conn, "b" * 64, bob.user_id, ann.user_id)  # someone else's share
    r = await _as(docs_app, me, "GET", f"/v1/docs/{HEX}/shares")
    assert r.status_code == 200
    rows = r.json()
    assert [(s["user_id"], s["name"]) for s in rows] == [(ann.user_id, "Ann")]
    assert rows[0]["shared_at"]


async def test_list_shares_needs_an_upload_entry(db_conn, docs_app):
    me, ann = await _member(db_conn, "ls4"), await _member(db_conn, "ls5")
    await _insert_doc(db_conn, HEX, me.user_id)
    await seed.share_doc(db_conn, HEX, me.user_id, ann.user_id)
    assert (await _as(docs_app, ann, "GET", f"/v1/docs/{HEX}/shares")).status_code == 404


async def test_sharing_with_a_disabled_account_is_not_found(db_conn, docs_app):
    me, gone = await _member(db_conn, "ls6"), await _member(db_conn, "ls7")
    await set_status(db_conn, gone.user_id, "disabled")
    await _insert_doc(db_conn, HEX, me.user_id)
    r = await _as(docs_app, me, "PUT", f"/v1/docs/{HEX}/shares/{gone.user_id}")
    assert r.status_code == 404
    assert await _entry(db_conn, gone.user_id) is None
```

(`_as(docs_app, principal, method, url)` is the file's existing request helper; read its body first and use it as it is.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_doc_shares.py -q`
Expected: FAIL — `GET …/shares` returns 405 (no such route); the disabled share returns 204.

- [ ] **Step 3: Implement**

In `server/routers/docs.py`, add `from ..services import people` to the imports (merge with the existing services import), add this route before `add_share`:

```python
@router.get("/{doc_id}/shares")
async def list_shares(doc_id: DocId, principal: Principal = Depends(_require_upload_holder)):
    """The people I shared this document with (A0 §9.3), oldest share first.
    Only a holder of a verified upload entry can share, so only they can
    list; everyone else gets 404. Other sharers' shares are never listed."""
    _ensure_ready()
    show_email = people.load_directory_config().show_email
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {people.person_cols('u')}, e.added_at FROM library_entries e "
            "JOIN users u ON u.id = e.user_id "
            "WHERE e.doc_id = %s AND e.shared_by = %s AND e.added_via = 'shared' "
            "ORDER BY e.added_at, u.id", (doc_id, principal.user_id))
        rows = await cur.fetchall()
    return [{"user_id": p["id"], "name": p["name"],
             "username": people.shown_username(r[4], show_email), "shared_at": r[6]}
            for r in rows if (p := people.person_from(r[:6], show_email))]
```

and in `add_share`, replace the `uuid.UUID(user_id)` try/except with a status check inside the connection block, before the `SELECT COALESCE(e.file_name …)`:

```python
    async with get_pool().connection() as conn:
        await people.assert_addable(conn, user_id)  # 404 for malformed, missing or disabled
        try:
            async with conn.transaction():  # savepoint: a caught FK error leaves conn usable
```

(re-indent the existing body under the new `try:`; the `except pg_errors.ForeignKeyViolation` clause stays attached to it and still maps to 404).

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_doc_shares.py -q`
Expected: all passed.

- [ ] **Step 5: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed.

- [ ] **Step 6: Commit** (ask first)

```bash
git add server/routers/docs.py server/tests/test_doc_shares.py
git commit -m "feat(sharing): GET /v1/docs/{id}/shares lists who I shared a document with; a disabled account can't be shared with (A0 Task 9)"
```

---

### Task 10: Admin — every project, project limits, and names at enrollment

**Files:**
- Modify: `server/routers/admin.py` (`UserPatchIn`, `UserEnrollIn`, `patch_user`, `enroll_user`, new `list_projects`)
- Modify: `server/auth/users.py` (`set_project_limit`; `enroll_user` and `enroll_linked_user` take names)
- Modify: `server/auth/kc_admin.py:76-90` (`create_user` takes names)
- Create: `server/tests/test_admin_projects.py`
- Modify: `server/tests/test_kc_admin.py` (the `create_user` payload test)

**Interfaces:**
- Consumes: `people.person_label` (Task 3).
- Produces: `GET /v1/admin/projects?ownerless=true|false` → `[{id, name, created_at, member_count, doc_count, owners: [{id, name}], ownerless}]`; `PATCH /v1/admin/users/{id}` accepts `project_limit` (int ≥ 0 or null); `POST /v1/admin/users` accepts `username`, `first_name`, `last_name`; `kc.create_user(*, email, display_name=None, email_verified=False, username=None, first_name=None, last_name=None)`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_admin_projects.py`:

```python
"""A0 §9.5: the admin's view of every project, per-user project limits, and
enrolling people with their names."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import get_user, resolve_or_provision_user
from server.routers import admin as admin_router
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, first=None):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io",
                                            first_name=first))["id"]


def _client(db_conn, uid):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=uid, email="admin@x.io", role="admin", capabilities=frozenset({"admin", "reader"}))
    app.dependency_overrides[deps.get_kc_admin] = lambda: None
    app.include_router(admin_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_admin_lists_every_project_with_owners_and_the_ownerless_filter(db_conn):
    admin, olu = await _user(db_conn, "ad"), await _user(db_conn, "ol", "Olu")
    owned = await seed.make_project(db_conn, olu, "Owned")
    orphan = await seed.make_project(db_conn, None, "Orphan")
    await seed.seed_doc(db_conn, "a" * 64, olu, project_ids=[owned])
    async with _client(db_conn, admin) as c:
        rows = {r["name"]: r for r in (await c.get("/v1/admin/projects")).json()}
        only = (await c.get("/v1/admin/projects", params={"ownerless": "true"})).json()
    assert rows["Owned"]["owners"] == [{"id": olu, "name": "Olu"}]
    assert (rows["Owned"]["member_count"], rows["Owned"]["doc_count"], rows["Owned"]["ownerless"]) == (1, 1, False)
    assert rows["Orphan"]["owners"] == [] and rows["Orphan"]["ownerless"] is True
    assert [r["id"] for r in only] == [orphan]


async def test_admin_projects_is_admin_only(db_conn):
    me = await _user(db_conn, "nm")
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=me, email="nm@x.io", role="member", capabilities=frozenset({"reader"}))
    app.include_router(admin_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/v1/admin/projects")).status_code == 403


async def test_admin_sets_and_clears_a_project_limit(db_conn):
    admin, u = await _user(db_conn, "ad"), await _user(db_conn, "pl")
    async with _client(db_conn, admin) as c:
        assert (await c.patch(f"/v1/admin/users/{u}", json={"project_limit": 5})).json()["project_limit"] == 5
        assert (await c.patch(f"/v1/admin/users/{u}", json={"project_limit": -1})).status_code == 422
        assert (await c.patch(f"/v1/admin/users/{u}", json={"project_limit": None})).json()["project_limit"] is None
        listed = {r["id"]: r for r in (await c.get("/v1/admin/users")).json()}
    assert "project_limit" in listed[u] and "first_name" in listed[u]


async def test_enroll_takes_username_and_names_without_copying_the_email(db_conn):
    admin = await _user(db_conn, "ad")
    async with _client(db_conn, admin) as c:
        r = await c.post("/v1/admin/users", json={"email": "new@example.com", "username": "newbie",
                                                  "first_name": "New", "last_name": "Person"})
        bare = await c.post("/v1/admin/users", json={"email": "bare@example.com"})
    assert r.status_code == 201
    u = await get_user(db_conn, r.json()["user"]["id"])
    assert (u["username"], u["first_name"], u["last_name"]) == ("newbie", "New", "Person")
    assert (await get_user(db_conn, bare.json()["user"]["id"]))["username"] is None
```

Append to `server/tests/test_kc_admin.py`:

```python
async def test_create_user_sends_username_and_names():
    import json
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(201, headers={"Location": "http://kc.test/admin/realms/nr/users/u1"})
    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_authed(handler))
    await kc.create_user(email="n@x.io", username="newbie", first_name="New", last_name="Person")
    await kc.create_user(email="d@x.io", display_name="Dee")
    assert (seen[0]["username"], seen[0]["firstName"], seen[0]["lastName"]) == ("newbie", "New", "Person")
    assert seen[1]["username"] == "d@x.io" and seen[1]["firstName"] == "Dee"
    assert "lastName" not in seen[1]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_admin_projects.py server/tests/test_kc_admin.py -q`
Expected: FAIL — `/v1/admin/projects` 404; `project_limit` 422 (extra field) or ignored; enroll 422 (`extra="forbid"`).

- [ ] **Step 3: Implement users, Keycloak and admin changes**

In `server/auth/users.py` add:

```python
async def set_project_limit(conn, user_id: str, limit: int | None) -> None:
    """Per-user override of PROJECT_LIMIT_PER_USER. NULL = deployment default; 0 = unlimited."""
    await conn.execute(
        "UPDATE users SET project_limit=%s, updated_at=now() WHERE id=%s", (limit, user_id))
```

and give both `enroll_user` and `enroll_linked_user` keyword parameters `username=None, first_name=None, last_name=None`, adding the three columns to each INSERT's column list and values (same order). In `server/auth/kc_admin.py`, `create_user` becomes:

```python
    async def create_user(self, *, email: str, display_name: str | None = None,
                          email_verified: bool = False, username: str | None = None,
                          first_name: str | None = None, last_name: str | None = None) -> str:
        # Keycloak requires a username; without one it gets the email (A0 §4),
        # but the app row keeps username NULL until the first login fills it.
        payload = {"email": email, "username": username or email, "enabled": True,
                   "emailVerified": email_verified}
        if first_name or display_name:
            payload["firstName"] = first_name or display_name
        if last_name:
            payload["lastName"] = last_name
```

(the rest of the method is unchanged).

In `server/routers/admin.py`:

```python
class UserPatchIn(BaseModel):
    status: str | None = None
    inference_daily_token_budget: int | None = None
    capabilities: list[str] | None = None
    project_limit: int | None = None


class UserEnrollIn(BaseModel):
    # House envelope style: unknown fields are a 422, not a silent ignore.
    model_config = ConfigDict(extra="forbid")
    email: str
    display_name: str | None = None
    username: str | None = Field(default=None, max_length=100)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    status: str = "pending"
    inference_daily_token_budget: int | None = None
    capabilities: list[str] = []
```

In `patch_user`, after the budget block:

```python
    if "project_limit" in data:
        limit = data["project_limit"]
        if limit is not None and limit < 0:
            raise HTTPException(status_code=422, detail="project_limit must be >= 0")
        await users.set_project_limit(conn, user_id, limit)
```

In `enroll_user`, pass `username=body.username, first_name=body.first_name, last_name=body.last_name` to `users.enroll_user(...)`, to `kc.create_user(...)` and to `users.enroll_linked_user(...)`.

Add the projects list (with `from ..services import people` merged into the services import):

```python
@router.get("/projects")
async def list_projects(ownerless: bool = False,
                        _: deps.Principal = Depends(deps.require_admin),
                        conn=Depends(deps.get_conn)):
    """Every project (A0 §9.5): owners, member and document counts, and
    whether it is ownerless (its last Owner was deleted) — the recovery list.
    Admins see owners' emails as labels' last resort, like the Users list."""
    cur = await conn.execute(
        "SELECT p.id, p.name, p.created_at, "
        "(SELECT count(*) FROM project_members m WHERE m.project_id = p.id), "
        "(SELECT count(*) FROM project_documents d WHERE d.project_id = p.id AND d.verified), "
        "COALESCE((SELECT json_agg(json_build_object('id', u.id, 'first_name', u.first_name, "
        "'last_name', u.last_name, 'display_name', u.display_name, 'username', u.username, "
        "'email', u.email) ORDER BY m.added_at, u.id) "
        "FROM project_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.project_id = p.id AND m.role = 'owner'), '[]'::json) "
        "FROM projects p "
        "WHERE NOT %s OR NOT EXISTS (SELECT 1 FROM project_members m "
        "WHERE m.project_id = p.id AND m.role = 'owner') "
        "ORDER BY p.created_at DESC, p.id", (ownerless,))
    out = []
    for r in await cur.fetchall():
        owners = [{"id": o["id"], "name": people.person_label(
            first_name=o["first_name"], last_name=o["last_name"], display_name=o["display_name"],
            username=o["username"], email=o["email"], show_email=True)} for o in r[5]]
        out.append({"id": str(r[0]), "name": r[1], "created_at": r[2], "member_count": r[3],
                    "doc_count": r[4], "owners": owners, "ownerless": not owners})
    return out
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_admin_projects.py server/tests/test_kc_admin.py server/tests/test_admin_router.py -q`
Expected: all passed.

- [ ] **Step 5: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3`
Expected: 0 failed.

- [ ] **Step 6: Commit** (ask first)

```bash
git add server/routers/admin.py server/auth/users.py server/auth/kc_admin.py server/tests/test_admin_projects.py server/tests/test_kc_admin.py
git commit -m "feat(admin): GET /v1/admin/projects (owners, counts, ownerless filter), per-user project_limit, enroll with username and first/last name (A0 Task 10)"
```

---

### Task 11: Frontend foundations — notices, the API client, roles, and the people picker

**Files:**
- Modify: `src/lib/apiErrors.js`, `src/lib/apiErrors.test.js`
- Create: `src/lib/projectsApi.js`, `src/lib/projectRoles.js`, `src/hooks/useLatest.js`
- Create: `src/components/people/PeoplePicker.jsx`, `src/components/people/PeoplePicker.test.jsx`

**Interfaces:**
- Consumes: every A0 route (Tasks 3, 5–7, 9, 10).
- Produces:
  - `noticeFor(status, detail, { notFound, tooLarge, serverNotFound })` — new codes `insufficient_role`, `project_creation_restricted`, `last_owner`, `project_limit`; with `serverNotFound: true`, a 404's own `message` wins over `notFound`. Exports `PROJECT_NOT_FOUND`.
  - `projectsApi(host, port)` → `{ list, get, create, update, remove, members, putMember, removeMember, events, fileDoc, unfileDoc, projectDocs, myDocs, lookup, shares, share, unshare, adminProjects }`; each resolves to parsed JSON (null for 204) or throws `Error(notice)`.
  - `ROLES = ['reader', 'contributor', 'maintainer', 'owner']`, `roleLabel(role) -> string`.
  - `useLatest(value) -> { current }` (a ref kept current after each render).
  - `<PeoplePicker theme lookup onPick excludeIds label disabled />`.

- [ ] **Step 1: Write the failing notice tests**

Append inside `describe('noticeFor', …)` in `src/lib/apiErrors.test.js`:

```js
    it('maps the A0 project refusals', () => {
        expect(noticeFor(403, { error: 'insufficient_role', required: 'maintainer' })).toBe('Only Maintainers or Owners can do that.');
        expect(noticeFor(403, { error: 'insufficient_role', required: 'owner' })).toBe('Only Owners can do that.');
        expect(noticeFor(403, { error: 'insufficient_role', required: 'contributor' })).toBe('Only Contributors, Maintainers or Owners can do that.');
        expect(noticeFor(403, { error: 'project_creation_restricted' })).toBe('Only admins can create projects here.');
        expect(noticeFor(409, { error: 'last_owner' })).toBe('A project must keep at least one Owner.');
        expect(noticeFor(429, { error: 'project_limit', limit: 20 })).toBe("You've reached your project limit (20). Ask an admin to raise it.");
    });
    it('lets a 404 carry its own message when asked, else the context notice', () => {
        expect(noticeFor(404, { error: 'not_found', message: 'User not found' }, { serverNotFound: true, notFound: PROJECT_NOT_FOUND })).toBe('User not found');
        expect(noticeFor(404, 'Not Found', { serverNotFound: true, notFound: PROJECT_NOT_FOUND })).toBe(PROJECT_NOT_FOUND);
        expect(noticeFor(404, { error: 'not_found', message: 'Document not found' })).toBe("This document doesn't exist or you don't have access.");
    });
```

and change the import line to `import { noticeFor, describeRefusal, PROJECT_NOT_FOUND } from './apiErrors';`.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/lib/apiErrors.test.js`
Expected: FAIL — `PROJECT_NOT_FOUND` is undefined and the A0 codes fall through to `d.message`/`HTTP nnn`.

- [ ] **Step 3: Implement the notices**

In `src/lib/apiErrors.js`, add above `noticeFor`:

```js
export const PROJECT_NOT_FOUND = "This project doesn't exist or you don't have access.";
// A0 §6: a member whose role is too low. `required` is the lowest role that may.
const ROLE_NOTICE = {
    contributor: 'Only Contributors, Maintainers or Owners can do that.',
    maintainer: 'Only Maintainers or Owners can do that.',
    owner: 'Only Owners can do that.',
};
```

Change the signature to `export function noticeFor(status, detail, { notFound, tooLarge, serverNotFound } = {}) {`, and replace the `if (status === 404) …` line with:

```js
    if (status === 403 && d?.error === 'insufficient_role') {
        return ROLE_NOTICE[d.required] || d.message || "Your role in this project doesn't allow that.";
    }
    if (status === 403 && d?.error === 'project_creation_restricted') return 'Only admins can create projects here.';
    if (status === 409 && d?.error === 'last_owner') return 'A project must keep at least one Owner.';
    if (status === 429 && d?.error === 'project_limit') {
        return `You've reached your project limit (${d.limit ?? '?'}). Ask an admin to raise it.`;
    }
    // Project routes name what wasn't found ("User not found", the project
    // notice); `serverNotFound` lets their message through.
    if (status === 404) {
        return (serverNotFound && d?.message) || notFound || "This document doesn't exist or you don't have access.";
    }
```

Update the file's header comment line "(A1 spec §8; A0 adds its codes here)" to "(A1 spec §8, A0 spec §6)".

- [ ] **Step 4: Run them to verify they pass**

Run: `npx vitest run src/lib/apiErrors.test.js`
Expected: all passed.

- [ ] **Step 5: Add the API client, roles and `useLatest`**

Create `src/lib/projectRoles.js`:

```js
// A0 §2: GitLab-style roles, lowest first.
export const ROLES = ['reader', 'contributor', 'maintainer', 'owner'];
const LABEL = { reader: 'Reader', contributor: 'Contributor', maintainer: 'Maintainer', owner: 'Owner' };
export const roleLabel = (role) => LABEL[role] || role || '';
```

Create `src/hooks/useLatest.js`:

```js
import { useEffect, useRef } from 'react';

// A ref that always holds the latest `value`, updated after each render. Lets
// effects and timers call a parent's callback without listing it as a
// dependency (an inline arrow from the parent would re-run them every render).
export function useLatest(value) {
  const ref = useRef(value);
  useEffect(() => { ref.current = value; });
  return ref;
}
```

Create `src/lib/projectsApi.js`:

```js
/**
 * One function per A0 call: projects, members, activity, the people lookup,
 * per-document shares and the admin project list. Each resolves to parsed
 * JSON (null for a 204) or throws an Error whose message is the notice to
 * show (apiErrors.js) — never a raw "HTTP nnn".
 */
import { apiFetch } from '../utils/apiFetch';
import { describeRefusal, PROJECT_NOT_FOUND } from './apiErrors';

const enc = encodeURIComponent;

export function projectsApi(host, port) {
  const call = async (path, { method = 'GET', body } = {}) => {
    const opts = { method };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const res = await apiFetch(host, port, path, opts);
    if (!res.ok) throw new Error(await describeRefusal(res, { serverNotFound: true, notFound: PROJECT_NOT_FOUND }));
    return res.status === 204 ? null : res.json();
  };
  const project = (id) => `/v1/projects/${enc(id)}`;
  return {
    list: () => call('/v1/projects'),
    get: (id) => call(project(id)),
    create: (body) => call('/v1/projects', { method: 'POST', body }),
    update: (id, body) => call(project(id), { method: 'PATCH', body }),
    remove: (id) => call(project(id), { method: 'DELETE' }),
    members: (id) => call(`${project(id)}/members`),
    putMember: (id, userId, role) => call(`${project(id)}/members/${enc(userId)}`, { method: 'PUT', body: { role } }),
    removeMember: (id, userId) => call(`${project(id)}/members/${enc(userId)}`, { method: 'DELETE' }),
    events: (id, before) => call(`${project(id)}/events?limit=50${before ? `&before=${enc(before)}` : ''}`),
    fileDoc: (id, docId) => call(`${project(id)}/docs/${enc(docId)}`, { method: 'PUT' }),
    unfileDoc: (id, docId) => call(`${project(id)}/docs/${enc(docId)}`, { method: 'DELETE' }),
    projectDocs: (id) => call(`/v1/docs?project_id=${enc(id)}`),
    myDocs: () => call('/v1/docs'),
    lookup: (q) => call(`/v1/users/lookup?q=${enc(q)}`),
    shares: (docId) => call(`/v1/docs/${enc(docId)}/shares`),
    share: (docId, userId) => call(`/v1/docs/${enc(docId)}/shares/${enc(userId)}`, { method: 'PUT' }),
    unshare: (docId, userId) => call(`/v1/docs/${enc(docId)}/shares/${enc(userId)}`, { method: 'DELETE' }),
    adminProjects: (ownerless) => call(`/v1/admin/projects${ownerless ? '?ownerless=true' : ''}`),
  };
}
```

- [ ] **Step 6: Write the failing people-picker tests**

Create `src/components/people/PeoplePicker.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PeoplePicker from './PeoplePicker';

const theme = { bg: '', border: '', text: '', textSecondary: '', textMuted: '', bgTertiary: '' };
const ann = { id: 'u1', name: 'Ann Lee', username: 'annl', status: 'active' };
const pen = { id: 'u2', name: 'Pen Pal', username: null, status: 'pending' };

function mount({ lookup = vi.fn(async () => [ann, pen]), excludeIds = [] } = {}) {
  const onPick = vi.fn();
  render(<PeoplePicker theme={theme} lookup={lookup} onPick={onPick} excludeIds={excludeIds} label="Find a person" />);
  return { lookup, onPick, box: screen.getByLabelText('Find a person') };
}

describe('PeoplePicker', () => {
  beforeEach(() => vi.clearAllMocks());

  it('waits for two characters and debounces typing into one lookup', async () => {
    const { lookup, box } = mount();
    fireEvent.change(box, { target: { value: 'a' } });
    fireEvent.change(box, { target: { value: 'an' } });
    fireEvent.change(box, { target: { value: 'ann' } });
    await screen.findByText('Ann Lee');
    expect(lookup).toHaveBeenCalledTimes(1);
    expect(lookup).toHaveBeenCalledWith('ann');
  });

  it('shows @username and "awaiting approval", and picking clears the box', async () => {
    const { onPick, box } = mount();
    fireEvent.change(box, { target: { value: 'an' } });
    expect(await screen.findByText('@annl')).toBeTruthy();
    expect(screen.getByText('awaiting approval')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Ann Lee/ }));
    expect(onPick).toHaveBeenCalledWith(ann);
    expect(box.value).toBe('');
    expect(screen.queryByText('Ann Lee')).toBeNull();
  });

  it('hides people in excludeIds', async () => {
    const { box } = mount({ excludeIds: ['u1'] });
    fireEvent.change(box, { target: { value: 'an' } });
    expect(await screen.findByText('Pen Pal')).toBeTruthy();
    expect(screen.queryByText('Ann Lee')).toBeNull();
  });

  it('says what to try when nobody is found', async () => {
    const { box } = mount({ lookup: vi.fn(async () => []) });
    fireEvent.change(box, { target: { value: 'zz' } });
    expect(await screen.findByText('Nobody found. Try their full email address.')).toBeTruthy();
    fireEvent.change(box, { target: { value: 'zz@example.com' } });
    expect(await screen.findByText('No account uses that email address.')).toBeTruthy();
  });

  it('shows a refused lookup as a notice', async () => {
    const { box } = mount({ lookup: vi.fn(async () => { throw new Error('Something went wrong on the server. Try again.'); }) });
    fireEvent.change(box, { target: { value: 'an' } });
    expect((await screen.findByRole('alert')).textContent).toBe('Something went wrong on the server. Try again.');
  });

  it('ignores a slow answer to an older query', async () => {
    let releaseOld;
    const lookup = vi.fn((q) => (q === 'an'
      ? new Promise((resolve) => { releaseOld = () => resolve([pen]); })
      : Promise.resolve([ann])));
    const { box } = mount({ lookup });
    fireEvent.change(box, { target: { value: 'an' } });
    await waitFor(() => expect(lookup).toHaveBeenCalledWith('an'));
    fireEvent.change(box, { target: { value: 'ann' } });
    await screen.findByText('Ann Lee');
    releaseOld();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText('Pen Pal')).toBeNull();
  });
});
```

- [ ] **Step 7: Run them to verify they fail**

Run: `npx vitest run src/components/people/PeoplePicker.test.jsx`
Expected: FAIL — `Failed to resolve import "./PeoplePicker"`.

- [ ] **Step 8: Implement the picker**

Create `src/components/people/PeoplePicker.jsx`:

```jsx
import { useEffect, useRef, useState } from 'react';
import { Search, Loader2 } from 'lucide-react';
import { useLatest } from '../../hooks/useLatest';

const LOOKUP_DEBOUNCE_MS = 250;
const MIN_CHARS = 2;

/**
 * Find a person to add or share with (A0 §4, §10). Type a name or a full
 * email address and pick from the results. The server decides who can be
 * found (USER_DIRECTORY_MODE); "Try their full email address" is advice that
 * works in every mode. `excludeIds` hides people already chosen. `lookup`
 * resolves to [{id, name, username, email?, status}] or throws Error(notice).
 */
export default function PeoplePicker({ theme, lookup, onPick, excludeIds = [], label = 'Find a person', disabled = false }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState(null); // null: nothing searched for the current text
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const lookupRef = useLatest(lookup);
  // Monotonic id: an older, slower answer must not overwrite a newer one.
  const reqId = useRef(0);

  useEffect(() => {
    const text = q.trim();
    const id = ++reqId.current;
    if (text.length < MIN_CHARS) return undefined;
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        const found = await lookupRef.current(text);
        if (id === reqId.current) { setResults(found); setError(null); }
      } catch (e) {
        if (id === reqId.current) { setResults([]); setError(e.message); }
      } finally {
        if (id === reqId.current) setBusy(false);
      }
    }, LOOKUP_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q, lookupRef]);

  const change = (value) => {
    setQ(value);
    if (value.trim().length < MIN_CHARS) { setResults(null); setError(null); setBusy(false); }
  };
  const pick = (person) => {
    onPick(person);
    change('');
  };
  const shown = (results || []).filter((p) => !excludeIds.includes(p.id));

  return (
    <div className="flex flex-col gap-1">
      <div className={`flex items-center gap-2 px-2 py-1 rounded border ${theme.border} ${theme.bg}`}>
        <Search size={12} className={theme.textSecondary} />
        <input
          value={q}
          onChange={(e) => change(e.target.value)}
          placeholder="Name or full email address"
          aria-label={label}
          disabled={disabled}
          className={`flex-1 bg-transparent text-xs outline-none ${theme.text}`}
        />
        {busy && <Loader2 size={12} className="animate-spin" />}
      </div>
      {error && <p role="alert" className="text-[10px] text-red-500">{error}</p>}
      {results !== null && !busy && !error && shown.length === 0 && (
        <p className={`text-[10px] ${theme.textMuted}`}>
          {q.includes('@') ? 'No account uses that email address.' : 'Nobody found. Try their full email address.'}
        </p>
      )}
      {shown.length > 0 && (
        <ul aria-label="People found" className={`flex flex-col rounded border ${theme.border}`}>
          {shown.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => pick(p)}
                disabled={disabled}
                className={`w-full flex items-center gap-2 px-2 py-1 text-left text-xs hover:text-blue-500 ${theme.text}`}
              >
                <span className="truncate">{p.name}</span>
                {p.username && <span className={`text-[10px] ${theme.textMuted}`}>@{p.username}</span>}
                {p.email && <span className={`text-[10px] ${theme.textMuted}`}>{p.email}</span>}
                {p.status === 'pending' && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">awaiting approval</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 9: Run them to verify they pass, then lint**

Run: `npx vitest run src/components/people/PeoplePicker.test.jsx src/lib/apiErrors.test.js && npx eslint src/components/people src/lib src/hooks`
Expected: all passed; eslint prints nothing.

- [ ] **Step 10: Commit** (ask first)

```bash
git add src/lib/apiErrors.js src/lib/apiErrors.test.js src/lib/projectsApi.js src/lib/projectRoles.js src/hooks/useLatest.js src/components/people/
git commit -m "feat(ui): A0 notices, one projects API client, roles, and a debounced people picker (A0 Task 11)"
```

---

### Task 12: The Projects tab and project page (header and Documents tab)

**Files:**
- Create: `src/components/library/ProjectsTab.jsx`, `src/components/library/ProjectsTab.test.jsx`
- Create: `src/components/projects/ProjectPage.jsx`, `src/components/projects/ProjectPage.test.jsx`
- Create: `src/components/projects/ProjectDocsTab.jsx`, `src/components/projects/ProjectDocsTab.test.jsx`
- Create (placeholder modules replaced in Task 13): `src/components/projects/MembersTab.jsx`, `src/components/projects/ActivityTab.jsx`
- Modify: `src/components/library/LibraryPage.jsx`, `src/components/library/LibraryPage.test.jsx`
- Modify: `src/App.jsx` (LibraryPage gets `currentUserId`; the upload picker lists `can.file_docs` projects)

**Interfaces:**
- Consumes: `projectsApi`, `roleLabel`, `useLatest` (Task 11); the project object (Task 5).
- Produces:
  - `<ProjectsTab theme projects onOpenProject />`
  - `<ProjectPage theme apiHost apiPort projectId currentUserId showToast onBack onChanged onOpenDoc />` — renders `MembersTab` and `ActivityTab` with props `{ theme, api, project, currentUserId, showToast, onRefused, onChanged }` and `{ theme, api, project }`.
  - `<ProjectDocsTab theme api project showToast onOpenDoc onRefused onChanged />`
  - `LibraryPage` gains prop `currentUserId`.

- [ ] **Step 1: Write the failing tests**

Create `src/components/library/ProjectsTab.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ProjectsTab from './ProjectsTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };

describe('ProjectsTab', () => {
  it('shows a card per project with my role and counts, and opens one', () => {
    const onOpenProject = vi.fn();
    render(<ProjectsTab theme={theme} onOpenProject={onOpenProject} projects={[
      { id: 'p1', name: 'Q3 Audit', description: 'Evidence', my_role: 'contributor', member_count: 4, doc_count: 12 },
    ]} />);
    expect(screen.getByText('Contributor')).toBeTruthy();
    expect(screen.getByText('4 members · 12 documents')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open project Q3 Audit' }));
    expect(onOpenProject).toHaveBeenCalledWith('p1');
  });

  it('says how to start when there are none', () => {
    render(<ProjectsTab theme={theme} onOpenProject={vi.fn()} projects={[]} />);
    expect(screen.getByText('No projects yet. Create one with New project.')).toBeTruthy();
  });
});
```

Create `src/components/projects/ProjectDocsTab.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ProjectDocsTab from './ProjectDocsTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: false,
  remove_docs: false, delete: false, leave: true, ...over });
const project = (over = {}) => ({ id: 'p1', name: 'Q3', my_role: 'contributor', can: can({ file_docs: true }), ...over });
const placed = { doc_id: 'd1', file_name: 'Placed.pdf', state: 'indexed', projects: [{ id: 'p1', name: 'Q3' }], added_via: null };
const mineFree = { doc_id: 'd2', file_name: 'Mine.pdf', state: 'indexed', projects: [], added_via: 'upload' };
const sharedToMe = { doc_id: 'd3', file_name: 'Shared.pdf', state: 'indexed', projects: [], added_via: 'shared' };

function api(over = {}) {
  return {
    projectDocs: vi.fn(async () => [placed]),
    myDocs: vi.fn(async () => [placed, mineFree, sharedToMe]),
    fileDoc: vi.fn(async () => null),
    unfileDoc: vi.fn(async () => null),
    ...over,
  };
}

function mount(p, a = api(), extra = {}) {
  const props = { theme, api: a, project: p, showToast: vi.fn(), onRefused: vi.fn(), onChanged: vi.fn(), ...extra };
  render(<ProjectDocsTab {...props} />);
  return props;
}

describe('ProjectDocsTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists the project documents and offers only my own unfiled uploads to file', async () => {
    const a = api();
    const props = mount(project(), a);
    expect(await screen.findByText('Placed.pdf')).toBeTruthy();
    const select = screen.getByLabelText('File a document into this project');
    expect([...select.options].map((o) => o.textContent)).toEqual(['File a document…', 'Mine.pdf']);
    fireEvent.change(select, { target: { value: 'd2' } });
    await waitFor(() => expect(a.fileDoc).toHaveBeenCalledWith('p1', 'd2'));
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
  });

  it('has no file or remove controls for a Reader', async () => {
    mount(project({ my_role: 'reader', can: can() }));
    await screen.findByText('Placed.pdf');
    expect(screen.queryByLabelText('File a document into this project')).toBeNull();
    expect(screen.queryByLabelText('Remove Placed.pdf from Q3')).toBeNull();
  });

  it('asks before a Maintainer removes a document, then removes it', async () => {
    const a = api();
    mount(project({ my_role: 'maintainer', can: can({ file_docs: true, remove_docs: true }) }), a);
    fireEvent.click(await screen.findByLabelText('Remove Placed.pdf from Q3'));
    expect(screen.getByText(/If nobody else has it in their library, it is deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(a.unfileDoc).toHaveBeenCalledWith('p1', 'd1'));
  });

  it('shows the notice and asks the page to reload when the server refuses', async () => {
    const a = api({ unfileDoc: vi.fn(async () => { throw new Error('Only Maintainers or Owners can do that.'); }) });
    const props = mount(project({ my_role: 'maintainer', can: can({ remove_docs: true }) }), a);
    fireEvent.click(await screen.findByLabelText('Remove Placed.pdf from Q3'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Only Maintainers or Owners can do that.', 5000));
    expect(props.onRefused).toHaveBeenCalled();
  });

  it('admin non-member sees the members-only note instead of documents', async () => {
    const a = api();
    mount(project({ my_role: null, can: can({ edit: true, manage_members: true, manage_owners: true, delete: true, leave: false }) }), a);
    expect(screen.getByText("Only members can see this project's documents.")).toBeTruthy();
    expect(a.projectDocs).not.toHaveBeenCalled();
  });
});
```

Create `src/components/projects/ProjectPage.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ProjectPage from './ProjectPage';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: true,
  remove_docs: false, delete: false, leave: true, ...over });
const proj = (over = {}) => ({ id: 'p1', name: 'Q3 Audit', description: 'Evidence', my_role: 'contributor',
  member_count: 2, doc_count: 0, created_by: null, can: can(), ...over });

function mount(routes) {
  apiFetch.mockImplementation(async (h, p, path, opts = {}) => {
    const key = `${opts.method || 'GET'} ${path}`;
    const hit = routes[key];
    if (hit) return typeof hit === 'function' ? hit(opts) : hit;
    if (path.startsWith('/v1/docs')) return json(200, []);
    return json(404, {});
  });
  const props = { theme, apiHost: '', apiPort: '', projectId: 'p1', currentUserId: 'me',
    showToast: vi.fn(), onBack: vi.fn(), onChanged: vi.fn(), onOpenDoc: vi.fn() };
  render(<ProjectPage {...props} />);
  return props;
}

describe('ProjectPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the name, my role, and only the controls my role allows', async () => {
    mount({ 'GET /v1/projects/p1': json(200, proj()) });
    expect(await screen.findByRole('heading', { name: /Q3 Audit/ })).toBeTruthy();
    expect(screen.getByText('Contributor')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit project' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete project' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Leave project' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Documents', selected: true })).toBeTruthy();
  });

  it('leaving asks first, removes me, and goes back', async () => {
    const props = mount({ 'GET /v1/projects/p1': json(200, proj()), 'DELETE /v1/projects/p1/members/me': json(204, null) });
    fireEvent.click(await screen.findByRole('button', { name: 'Leave project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm leave' }));
    await waitFor(() => expect(props.onBack).toHaveBeenCalled());
    expect(props.onChanged).toHaveBeenCalled();
  });

  it('a Maintainer edits the name and description', async () => {
    const saved = proj({ name: 'Q4 Audit', my_role: 'maintainer', can: can({ edit: true }) });
    mount({ 'GET /v1/projects/p1': json(200, proj({ my_role: 'maintainer', can: can({ edit: true }) })),
      'PATCH /v1/projects/p1': json(200, saved) });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Q4 Audit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('heading', { name: /Q4 Audit/ })).toBeTruthy();
    const patch = apiFetch.mock.calls.find(([, , , o]) => o?.method === 'PATCH');
    expect(JSON.parse(patch[3].body)).toEqual({ name: 'Q4 Audit', description: 'Evidence' });
  });

  it('reloads after a refusal so stale controls disappear', async () => {
    let n = 0;
    mount({
      'GET /v1/projects/p1': () => json(200, n++ === 0
        ? proj({ my_role: 'maintainer', can: can({ edit: true }) })
        : proj({ my_role: 'reader', can: can({ file_docs: false }) })),
      'PATCH /v1/projects/p1': json(403, { detail: { error: 'insufficient_role', required: 'maintainer' } }),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByText('Reader')).toBeTruthy());
    expect(screen.queryByRole('button', { name: 'Edit project' })).toBeNull();
  });

  it('a project I can no longer see sends me back with the notice', async () => {
    const props = mount({ 'GET /v1/projects/p1': json(404, { detail: { error: 'not_found',
      message: "This project doesn't exist or you don't have access." } }) });
    await waitFor(() => expect(props.onBack).toHaveBeenCalled());
    expect(props.showToast).toHaveBeenCalledWith("This project doesn't exist or you don't have access.", 5000);
  });

  it('an Owner deletes the project after confirming', async () => {
    const props = mount({ 'GET /v1/projects/p1': json(200, proj({ my_role: 'owner', can: can({ delete: true }) })),
      'DELETE /v1/projects/p1': json(204, null) });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete project' }));
    expect(screen.getByText(/a document only this project held is deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() => expect(props.onBack).toHaveBeenCalled());
  });
});
```

In `src/components/library/LibraryPage.test.jsx`:
1. Replace the `projects` fixture with the A0 shape:

```jsx
const canFor = (role) => ({
  edit: role === 'owner' || role === 'maintainer', manage_members: role === 'owner' || role === 'maintainer',
  manage_owners: role === 'owner', file_docs: role !== 'reader', remove_docs: role === 'owner' || role === 'maintainer',
  delete: role === 'owner', leave: true,
});
const projects = [
  { id: 'p1', name: 'Project A', description: null, my_role: 'owner', member_count: 1, doc_count: 2, can: canFor('owner') },
  { id: 'p2', name: 'Project B', description: null, my_role: 'contributor', member_count: 3, doc_count: 1, can: canFor('contributor') },
];
```

2. In the create-project mock (line ~294), replace `{ id: 'p9', owner_user_id: 'me', ...body, is_owner: true }` with `{ id: 'p9', ...body, my_role: 'owner', member_count: 1, doc_count: 0, can: canFor('owner') }`.
3. Rename `'shows × on a chip only for the chip of a project the caller owns'` to `'shows × on a chip only where the caller may remove documents'` and `'shows × only for owned-project chips even on the uploader\'s own row'` to `'shows × only where the caller may remove documents, even on the uploader\'s own row'` (their assertions hold: p1 can remove, p2 can't).
4. Add:

```jsx
  it('offers only projects where the caller may file documents in the + project select', async () => {
    apiFetch.mockImplementation(async (h, p, path) => {
      if (path.startsWith('/v1/docs')) return json(200, docs);
      if (path === '/v1/projects') return json(200, [...projects,
        { id: 'p3', name: 'Read only', my_role: 'reader', member_count: 2, doc_count: 0, can: canFor('reader') }]);
      return json(404, {});
    });
    render(<LibraryPage theme={theme} apiHost="" apiPort="" showToast={vi.fn()} />);
    const select = await screen.findByLabelText('Add Owned.pdf to project');
    expect([...select.options].map((o) => o.textContent)).toEqual(['+ project', 'Project A', 'Project B']);
  });

  it('switches to the Projects tab and opens a project page', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    apiFetch.mockImplementation(async (h, p, path) => {
      if (path === '/v1/projects') return json(200, projects);
      if (path === '/v1/projects/p2') return json(200, projects[1]);
      if (path.startsWith('/v1/docs')) return json(200, []);
      return json(404, {});
    });
    fireEvent.click(screen.getByRole('tab', { name: 'Projects' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open project Project B' }));
    expect(await screen.findByRole('heading', { name: /Project B/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Projects/ }));
    expect(await screen.findByRole('button', { name: 'Open project Project A' })).toBeTruthy();
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/library src/components/projects`
Expected: FAIL — the new modules don't exist; LibraryPage has no Projects tab; the "+ project" select still offers the Reader-only project.

- [ ] **Step 3: Implement ProjectsTab**

Create `src/components/library/ProjectsTab.jsx`:

```jsx
import { roleLabel } from '../../lib/projectRoles';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// The Library's Projects tab (A0 §10): one card per project I belong to.
export default function ProjectsTab({ theme, projects, onOpenProject }) {
  if (projects === null) return <p className={`text-xs ${theme.textMuted}`}>Loading…</p>;
  if (projects.length === 0) {
    return <p className={`text-xs ${theme.textMuted}`}>No projects yet. Create one with New project.</p>;
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {projects.map((p) => (
        <button
          key={p.id}
          onClick={() => onOpenProject(p.id)}
          aria-label={`Open project ${p.name}`}
          className={`flex flex-col gap-1 p-3 text-left rounded-lg border ${theme.border} ${theme.bgSecondary} hover:border-blue-500`}
        >
          <span className="flex items-center justify-between gap-2">
            <span className="text-xs font-bold truncate">{p.name}</span>
            {p.my_role && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">{roleLabel(p.my_role)}</span>
            )}
          </span>
          {p.description && <span className={`text-[10px] truncate ${theme.textSecondary}`}>{p.description}</span>}
          <span className={`text-[10px] ${theme.textMuted}`}>
            {plural(p.member_count, 'member')} · {plural(p.doc_count, 'document')}
          </span>
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Implement ProjectDocsTab**

Create `src/components/projects/ProjectDocsTab.jsx`:

```jsx
import { useCallback, useEffect, useState } from 'react';
import { BookOpen, Loader2, X } from 'lucide-react';

/**
 * A project's documents (A0 §10). Contributors and above file their own
 * uploads in; Maintainers and Owners remove any — after a confirmation,
 * because content nobody else holds is deleted with its last placement.
 * An admin who isn't a member sees counts only (§5), so this says so.
 */
export default function ProjectDocsTab({ theme, api, project, showToast, onOpenDoc, onRefused, onChanged }) {
  const [docs, setDocs] = useState(null);
  const [mine, setMine] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const member = project.my_role !== null;
  const { can } = project;

  const load = useCallback(async () => {
    if (!member) return;
    try {
      const [inProject, all] = await Promise.all([
        api.projectDocs(project.id),
        can.file_docs ? api.myDocs() : Promise.resolve([]),
      ]);
      setDocs(inProject);
      setMine(all.filter((d) => d.added_via === 'upload' && !(d.projects || []).some((p) => p.id === project.id)));
    } catch (e) {
      setDocs([]);
      showToast(e.message, 5000);
    }
  }, [api, project.id, can.file_docs, member, showToast]);

  useEffect(() => { load(); }, [load]);

  const run = async (docId, fn, ok) => {
    setBusyId(docId);
    try {
      await fn();
      if (ok) showToast(ok, 3000);
      onChanged?.();
    } catch (e) {
      showToast(e.message, 5000);
      onRefused?.();
    } finally {
      setBusyId(null);
      setConfirmId(null);
      await load();
    }
  };

  if (!member) {
    return <p className={`text-xs ${theme.textMuted}`}>Only members can see this project's documents.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {can.file_docs && mine.length > 0 && (
        <select
          value=""
          onChange={(e) => {
            const d = mine.find((x) => x.doc_id === e.target.value);
            if (d) run(d.doc_id, () => api.fileDoc(project.id, d.doc_id), `Filed ${d.file_name} into ${project.name}.`);
          }}
          disabled={busyId !== null}
          aria-label="File a document into this project"
          className={`self-start px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg}`}
        >
          <option value="">File a document…</option>
          {mine.map((d) => <option key={d.doc_id} value={d.doc_id}>{d.file_name}</option>)}
        </select>
      )}
      {docs === null ? (
        <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : docs.length === 0 ? (
        <p className={`text-xs ${theme.textMuted}`}>No documents in this project yet.</p>
      ) : docs.map((d) => (
        <div key={d.doc_id} className={`flex flex-col gap-1 p-2 rounded-lg border ${theme.border} ${theme.bgSecondary}`}>
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 min-w-0">
              <span className="text-xs font-bold truncate">{d.file_name}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${theme.bgTertiary} ${theme.textSecondary}`}>{d.state}</span>
            </span>
            <span className="flex items-center gap-2 shrink-0">
              {onOpenDoc && (
                <button onClick={() => onOpenDoc(d)} aria-label={`Open ${d.file_name}`}
                  className="flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-blue-500 text-white hover:bg-blue-600">
                  <BookOpen size={10} /> Open
                </button>
              )}
              {can.remove_docs && (
                <button onClick={() => setConfirmId(d.doc_id)} disabled={busyId !== null}
                  aria-label={`Remove ${d.file_name} from ${project.name}`}
                  className={`hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
                  {busyId === d.doc_id ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                </button>
              )}
            </span>
          </div>
          {confirmId === d.doc_id && (
            <div className="flex flex-wrap items-center gap-2 text-[10px]">
              <span className={theme.textSecondary}>
                Remove from {project.name}? If nobody else has it in their library, it is deleted.
              </span>
              <button onClick={() => run(d.doc_id, () => api.unfileDoc(project.id, d.doc_id), `Removed ${d.file_name}.`)}
                className="underline text-red-500">Confirm remove</button>
              <button onClick={() => setConfirmId(null)} className="underline">Cancel</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Implement ProjectPage, with placeholder Members and Activity tabs**

Create `src/components/projects/MembersTab.jsx` and `src/components/projects/ActivityTab.jsx`, each with exactly this content (Task 13 replaces both):

```jsx
// Replaced in A0 Task 13.
export default function Placeholder() {
  return null;
}
```

Create `src/components/projects/ProjectPage.jsx`:

```jsx
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Pencil, Loader2 } from 'lucide-react';
import { projectsApi } from '../../lib/projectsApi';
import { roleLabel } from '../../lib/projectRoles';
import { useLatest } from '../../hooks/useLatest';
import ProjectDocsTab from './ProjectDocsTab';
import MembersTab from './MembersTab';
import ActivityTab from './ActivityTab';

const TABS = [['documents', 'Documents'], ['members', 'Members'], ['activity', 'Activity']];

/**
 * One project (A0 §10): header (name, description, my role, Edit / Leave /
 * Delete as `can` allows) and Documents | Members | Activity tabs. Any
 * refusal reloads the project, so controls the server no longer allows
 * disappear; a project I can no longer see sends me back with the notice.
 */
export default function ProjectPage({ theme, apiHost, apiPort, projectId, currentUserId, showToast, onBack, onChanged, onOpenDoc }) {
  const api = useMemo(() => projectsApi(apiHost, apiPort), [apiHost, apiPort]);
  const [project, setProject] = useState(null);
  const [tab, setTab] = useState('documents');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ name: '', description: '' });
  const [confirm, setConfirm] = useState(null); // 'leave' | 'delete'
  const [busy, setBusy] = useState(false);
  const cb = useLatest({ showToast, onBack, onChanged });

  const reload = useCallback(async () => {
    try {
      setProject(await api.get(projectId));
    } catch (e) {
      cb.current.showToast(e.message, 5000);
      cb.current.onBack();
    }
  }, [api, projectId, cb]);

  useEffect(() => { reload(); }, [reload]);

  const act = async (fn) => {
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      showToast(e.message, 5000);
      await reload();
      return undefined;
    } finally {
      setBusy(false);
    }
  };

  if (!project) return <p className={`text-xs ${theme.textMuted}`}>Loading…</p>;
  const { can } = project;

  const startEdit = () => { setDraft({ name: project.name, description: project.description || '' }); setEditing(true); };
  const save = (e) => {
    e.preventDefault();
    act(async () => {
      const saved = await api.update(project.id, { name: draft.name.trim(), description: draft.description.trim() || null });
      setProject(saved);
      setEditing(false);
      onChanged?.();
    });
  };
  const leave = () => act(async () => {
    await api.removeMember(project.id, currentUserId);
    showToast(`You left ${project.name}.`, 3000);
    onChanged?.();
    onBack();
  });
  const remove = () => act(async () => {
    await api.remove(project.id);
    showToast(`Deleted ${project.name}.`, 3000);
    onChanged?.();
    onBack();
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1 min-w-0 flex-1">
          <button onClick={onBack} className="self-start text-xs underline text-blue-500 flex items-center gap-1">
            <ArrowLeft size={12} /> Projects
          </button>
          {editing ? (
            <form onSubmit={save} className="flex flex-col gap-2">
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} maxLength={200}
                aria-label="Project name" disabled={busy}
                className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`} />
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                aria-label="Project description" placeholder="Description (optional)" disabled={busy}
                className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} ${theme.text}`} />
              <span className="flex gap-2">
                <button type="submit" disabled={busy || !draft.name.trim()}
                  className="px-2 py-1 text-xs rounded bg-blue-500 text-white disabled:opacity-50">
                  {busy ? <Loader2 size={12} className="animate-spin" /> : 'Save'}
                </button>
                <button type="button" onClick={() => setEditing(false)} className="text-xs underline">Cancel</button>
              </span>
            </form>
          ) : (
            <>
              <h2 className="text-base font-bold flex items-center gap-2">
                <span className="truncate">{project.name}</span>
                {project.my_role && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">{roleLabel(project.my_role)}</span>
                )}
              </h2>
              {project.description && <p className={`text-xs ${theme.textSecondary}`}>{project.description}</p>}
            </>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0 text-xs">
          {can.edit && !editing && (
            <button onClick={startEdit} aria-label="Edit project" className="flex items-center gap-1 underline">
              <Pencil size={12} /> Edit
            </button>
          )}
          {can.leave && <button onClick={() => setConfirm('leave')} aria-label="Leave project" className="underline">Leave</button>}
          {can.delete && (
            <button onClick={() => setConfirm('delete')} aria-label="Delete project" className="underline text-red-500">Delete project</button>
          )}
        </div>
      </div>

      {confirm && (
        <div className={`flex flex-wrap items-center gap-2 p-2 text-[11px] rounded border ${theme.border}`}>
          <span className={theme.textSecondary}>
            {confirm === 'leave'
              ? `Leave ${project.name}? You lose access to its documents unless they're in your library.`
              : `Delete ${project.name}? Members lose access. Documents stay in the libraries of people who hold them; a document only this project held is deleted.`}
          </span>
          <button onClick={confirm === 'leave' ? leave : remove} disabled={busy} className="underline text-red-500">
            {confirm === 'leave' ? 'Confirm leave' : 'Confirm delete'}
          </button>
          <button onClick={() => setConfirm(null)} className="underline">Cancel</button>
        </div>
      )}

      <div role="tablist" aria-label="Project sections" className={`flex gap-4 border-b ${theme.border}`}>
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
            className={`pb-1 text-xs ${tab === key ? 'border-b-2 border-blue-500 text-blue-500' : theme.textSecondary}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'documents' && (
        <ProjectDocsTab theme={theme} api={api} project={project} showToast={showToast} onOpenDoc={onOpenDoc}
          onRefused={reload} onChanged={reload} />
      )}
      {tab === 'members' && (
        <MembersTab theme={theme} api={api} project={project} currentUserId={currentUserId} showToast={showToast}
          onRefused={reload} onChanged={reload} />
      )}
      {tab === 'activity' && <ActivityTab theme={theme} api={api} project={project} />}
    </div>
  );
}
```

- [ ] **Step 6: Wire the Library and the reader's picker**

In `src/components/library/LibraryPage.jsx`:

1. Imports: add `import ProjectsTab from './ProjectsTab';` and `import ProjectPage from '../projects/ProjectPage';`.
2. `ProjectChips`: replace the `ownedProjectIds`/`addable` lines with:

```jsx
  // Projects decide (A0 §5): × where the caller may remove documents, and
  // "+ project" offers only projects the caller may file into.
  const removableIds = new Set((projects || []).filter((p) => p.can?.remove_docs).map((p) => p.id));
  const addable = (projects || []).filter((p) => p.can?.file_docs && !linkedIds.has(p.id));
```

and change `{ownedProjectIds.has(p.id) && (` to `{removableIds.has(p.id) && (`. Update the comment above `ProjectChips` to: "One chip per linked project. Projects govern their own documents (A0 §5): a Contributor or above who uploaded a document files it in; only a Maintainer or Owner removes it — never the uploader as such."
3. Signature: `export default function LibraryPage({ theme, apiHost, apiPort, showToast, onProjectsChanged, onOpen, currentUserId })`; in the doc comment replace "Adding members has no screen until A0." with "The Projects tab lists my projects; a project page manages its documents, members and activity (A0)."
4. State: add `const [view, setView] = useState('documents');` and `const [openProjectId, setOpenProjectId] = useState(null);`.
5. Add a handler after `createProject`:

```jsx
  const projectsChanged = async () => {
    await loadProjects();
    onProjectsChanged?.();
  };
```

6. In the JSX, keep the header (title + "New project" button) and the `NewProjectForm`. Directly below them add the tab strip, and wrap everything from the search row to the end of the document rows in `view === 'documents'`:

```jsx
        <div role="tablist" aria-label="Library sections" className={`flex gap-4 border-b ${theme.border}`}>
          {[['documents', 'Documents'], ['projects', 'Projects']].map(([key, label]) => (
            <button key={key} role="tab" aria-selected={view === key && !openProjectId}
              onClick={() => { setView(key); setOpenProjectId(null); }}
              className={`pb-1 text-xs ${view === key ? 'border-b-2 border-blue-500 text-blue-500' : theme.textSecondary}`}>
              {label}
            </button>
          ))}
        </div>

        {openProjectId ? (
          <ProjectPage
            theme={theme} apiHost={apiHost} apiPort={apiPort} projectId={openProjectId}
            currentUserId={currentUserId} showToast={showToast}
            onBack={() => setOpenProjectId(null)}
            onChanged={projectsChanged}
            onOpenDoc={onOpen ? openDoc : undefined}
          />
        ) : view === 'projects' ? (
          <ProjectsTab theme={theme} projects={projects} onOpenProject={setOpenProjectId} />
        ) : (
          <>
            {/* Search + project filter */}
            …the existing search row and document rows, unchanged…
          </>
        )}
```

7. In `createProject`, after `await loadProjects();` keep `onProjectsChanged?.();` (unchanged).

In `src/App.jsx`:
- the `<LibraryPage …>` element gains `currentUserId={auth.user?.id}`;
- in the effect that fetches `/v1/projects` for the reader's picker, change `if (!cancelled) setProjects(data);` to `if (!cancelled) setProjects(data.filter((p) => p.can?.file_docs));` and add to its comment: "Only projects the user may file into (A0 §10): a Reader is never offered a project that would refuse them."

- [ ] **Step 7: Run the tests to verify they pass, then lint**

Run: `npx vitest run src/components/library src/components/projects && npx eslint src/components src/App.jsx`
Expected: all passed; eslint prints nothing.

- [ ] **Step 8: Run the whole frontend suite**

Run: `npx vitest run 2>&1 | tail -5`
Expected: all passed.

- [ ] **Step 9: Commit** (ask first)

```bash
git add src/components/library/ src/components/projects/ src/App.jsx
git commit -m "feat(ui): Library Projects tab and project page — header with Edit/Leave/Delete by role, Documents tab (file, remove with confirmation, members-only note); chips and the reader's picker follow can (A0 Task 12)"
```

---

### Task 13: Members tab and Activity tab

**Files:**
- Modify (replace the placeholders): `src/components/projects/MembersTab.jsx`, `src/components/projects/ActivityTab.jsx`
- Create: `src/components/projects/MembersTab.test.jsx`, `src/components/projects/ActivityTab.test.jsx`

**Interfaces:**
- Consumes: `PeoplePicker` (Task 11), `ROLES`, `roleLabel` (Task 11); member objects (Task 6); event pages (Task 7).
- Produces: `<MembersTab theme api project currentUserId showToast onRefused onChanged />`; `<ActivityTab theme api project />`; `describeEvent(ev) -> string`; `timeAgo(at, now?) -> string`.

- [ ] **Step 1: Write the failing tests**

Create `src/components/projects/MembersTab.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import MembersTab from './MembersTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const can = (over = {}) => ({ edit: false, manage_members: false, manage_owners: false, file_docs: true,
  remove_docs: false, delete: false, leave: true, ...over });
const asOwner = { id: 'p1', name: 'Q3', my_role: 'owner', can: can({ edit: true, manage_members: true, manage_owners: true, remove_docs: true, delete: true }) };
const asMaintainer = { id: 'p1', name: 'Q3', my_role: 'maintainer', can: can({ edit: true, manage_members: true, remove_docs: true }) };
const asReader = { id: 'p1', name: 'Q3', my_role: 'reader', can: can({ file_docs: false }) };
const member = (over) => ({ username: null, status: 'active', added_via: 'member', added_by: null, ...over });
const members = [
  member({ user_id: 'o1', name: 'Asha Perera', username: 'asha', role: 'owner' }),
  member({ user_id: 'm1', name: 'Ben Silva', role: 'maintainer', status: 'pending' }),
  member({ user_id: 'me', name: 'Me Myself', role: 'contributor' }),
  member({ user_id: 'a1', name: 'Root Admin', role: 'owner', added_via: 'admin' }),
];

function api(over = {}) {
  return {
    members: vi.fn(async () => members),
    putMember: vi.fn(async () => ({})),
    removeMember: vi.fn(async () => null),
    lookup: vi.fn(async () => [{ id: 'n1', name: 'New Person', username: 'newp', status: 'active' }]),
    ...over,
  };
}

function mount(project, a = api()) {
  const props = { theme, api: a, project, currentUserId: 'me', showToast: vi.fn(), onRefused: vi.fn(), onChanged: vi.fn() };
  render(<MembersTab {...props} />);
  return props;
}

const row = (name) => screen.getByText(name).closest('li');

describe('MembersTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('an Owner can change any role, including to and from Owner', async () => {
    mount(asOwner);
    await screen.findByText('Asha Perera');
    const menu = screen.getByLabelText('Role for Asha Perera');
    expect([...menu.options].map((o) => o.value)).toEqual(['reader', 'contributor', 'maintainer', 'owner']);
    expect(screen.getByLabelText('Remove Asha Perera from Q3')).toBeTruthy();
  });

  it('a Maintainer manages up to Maintainer and sees Owners as plain text', async () => {
    mount(asMaintainer);
    await screen.findByText('Asha Perera');
    expect(screen.queryByLabelText('Role for Asha Perera')).toBeNull();
    expect(within(row('Asha Perera')).getByText('Owner')).toBeTruthy();
    expect([...screen.getByLabelText('Role for Ben Silva').options].map((o) => o.value))
      .toEqual(['reader', 'contributor', 'maintainer']);
  });

  it('a Reader sees roles as plain text and no Add people', async () => {
    mount(asReader);
    await screen.findByText('Asha Perera');
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: '+ Add people' })).toBeNull();
  });

  it('marks me, pending people and admin-added members, with no controls on my own row', async () => {
    mount(asOwner);
    await screen.findByText('Asha Perera');
    expect(within(row('Me Myself')).getByText('(you)')).toBeTruthy();
    expect(screen.queryByLabelText('Role for Me Myself')).toBeNull();
    expect(within(row('Ben Silva')).getByText('awaiting approval')).toBeTruthy();
    expect(within(row('Root Admin')).getByText('added by admin')).toBeTruthy();
  });

  it('changes a role and disables the role menu while the change is in flight', async () => {
    let finish;
    const a = api({ putMember: vi.fn(() => new Promise((r) => { finish = r; })) });
    const props = mount(asOwner, a);
    const menu = await screen.findByLabelText('Role for Ben Silva');
    fireEvent.change(menu, { target: { value: 'reader' } });
    expect(menu.disabled).toBe(true);
    expect(a.putMember).toHaveBeenCalledWith('p1', 'm1', 'reader');
    finish({});
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Ben Silva is now Reader.', 3000));
    expect(props.onChanged).toHaveBeenCalled();
  });

  it('adds a person found with the picker, with the chosen role', async () => {
    const a = api();
    mount(asMaintainer, a);
    await screen.findByText('Asha Perera');
    fireEvent.click(screen.getByRole('button', { name: '+ Add people' }));
    fireEvent.change(screen.getByLabelText('Find a person to add'), { target: { value: 'ne' } });
    fireEvent.click(await screen.findByRole('button', { name: /New Person/ }));
    const role = screen.getByLabelText('Role for the new member');
    expect(role.value).toBe('contributor');
    expect([...role.options].map((o) => o.value)).toEqual(['reader', 'contributor', 'maintainer']);
    fireEvent.change(role, { target: { value: 'reader' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(a.putMember).toHaveBeenCalledWith('p1', 'n1', 'reader'));
  });

  it('shows the last-owner notice and reloads the project on a refusal', async () => {
    const a = api({ removeMember: vi.fn(async () => { throw new Error('A project must keep at least one Owner.'); }) });
    const props = mount(asOwner, a);
    fireEvent.click(await screen.findByLabelText('Remove Asha Perera from Q3'));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('A project must keep at least one Owner.', 5000));
    expect(props.onRefused).toHaveBeenCalled();
  });
});
```

Create `src/components/projects/ActivityTab.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ActivityTab, { describeEvent, timeAgo } from './ActivityTab';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const asha = { id: 'a', name: 'Asha' };
const ben = { id: 'b', name: 'Ben' };
const ev = (kind, details = {}, over = {}) => ({ id: 1, at: '2026-10-06T00:00:00Z', kind, actor: asha, subject: null, doc: null, details, ...over });

describe('describeEvent', () => {
  it.each([
    [ev('project.created', { name: 'Q3' }), 'Asha created the project'],
    [ev('project.created', { name: 'Q3' }, { subject: ben }), 'Asha created the project for Ben'],
    [ev('project.renamed', { from: 'Q3', to: 'Q4' }), 'Asha renamed the project from "Q3" to "Q4"'],
    [ev('project.described'), 'Asha changed the description'],
    [ev('member.added', { role: 'maintainer', via: 'member' }, { subject: ben }), 'Asha added Ben as Maintainer'],
    [ev('member.role_changed', { from: 'reader', to: 'owner' }, { subject: ben }), 'Asha changed Ben from Reader to Owner'],
    [ev('member.removed', { role: 'reader' }, { subject: null }), 'Asha removed a former member'],
    [ev('member.left', { role: 'reader' }, { actor: null }), 'A former member left the project'],
    [ev('document.added', { name: 'Q3.pdf' }, { doc: { id: 'd', name: 'Q3.pdf' } }), 'Asha added "Q3.pdf"'],
    [ev('document.removed', { name: 'Old.pdf' }, { doc: { id: 'd', name: 'Old.pdf' } }), 'Asha removed "Old.pdf"'],
  ])('%#', (event, text) => {
    expect(describeEvent(event)).toBe(text);
  });
});

describe('timeAgo', () => {
  const now = Date.parse('2026-10-06T12:00:00Z');
  it.each([
    ['2026-10-06T11:59:30Z', 'just now'], ['2026-10-06T11:15:00Z', '45 min ago'],
    ['2026-10-06T09:00:00Z', '3 h ago'], ['2026-10-04T12:00:00Z', '2 d ago'],
  ])('%s', (at, text) => {
    expect(timeAgo(at, now)).toBe(text);
  });
});

describe('ActivityTab', () => {
  it('lists events newest first and loads more with next_before', async () => {
    const api = {
      events: vi.fn(async (id, before) => (before
        ? { events: [ev('project.created', { name: 'Q3' }, { id: 1 })], next_before: null }
        : { events: [ev('project.described', {}, { id: 2 })], next_before: 2 })),
    };
    render(<ActivityTab theme={theme} api={api} project={{ id: 'p1' }} />);
    expect(await screen.findByText(/Asha changed the description/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText(/Asha created the project/)).toBeTruthy();
    expect(api.events).toHaveBeenLastCalledWith('p1', 2);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull());
  });

  it('says when there is no activity', async () => {
    render(<ActivityTab theme={theme} api={{ events: vi.fn(async () => ({ events: [], next_before: null })) }} project={{ id: 'p1' }} />);
    expect(await screen.findByText('No activity yet.')).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/projects/MembersTab.test.jsx src/components/projects/ActivityTab.test.jsx`
Expected: FAIL — the placeholders render nothing; `describeEvent` is not exported.

- [ ] **Step 3: Implement MembersTab**

Replace `src/components/projects/MembersTab.jsx` with:

```jsx
import { useCallback, useEffect, useState } from 'react';
import { Loader2, UserPlus, X } from 'lucide-react';
import PeoplePicker from '../people/PeoplePicker';
import { ROLES, roleLabel } from '../../lib/projectRoles';

/**
 * A project's members (A0 §10). Controls follow `project.can`: Maintainers
 * manage Readers, Contributors and Maintainers; Owner rows and the Owner
 * role need `can.manage_owners`. My own row has no controls (Leave is in
 * the header). Every control is disabled while its request is in flight,
 * and a refusal shows the server's notice and reloads the project.
 */
export default function MembersTab({ theme, api, project, currentUserId, showToast, onRefused, onChanged }) {
  const [members, setMembers] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState(null);
  const [newRole, setNewRole] = useState('contributor');
  const { can } = project;
  const roleOptions = can.manage_owners ? ROLES : ROLES.filter((r) => r !== 'owner');

  const load = useCallback(async () => {
    try {
      setMembers(await api.members(project.id));
    } catch (e) {
      setMembers([]);
      showToast(e.message, 5000);
    }
  }, [api, project.id, showToast]);

  useEffect(() => { load(); }, [load]);

  const run = async (key, fn, ok) => {
    setBusyId(key);
    try {
      await fn();
      showToast(ok, 3000);
      onChanged?.();
      return true;
    } catch (e) {
      showToast(e.message, 5000);
      onRefused?.();
      return false;
    } finally {
      setBusyId(null);
      await load();
    }
  };

  const editable = (m) => m.user_id !== currentUserId && (m.role === 'owner' ? can.manage_owners : can.manage_members);
  const changeRole = (m, role) => run(m.user_id, () => api.putMember(project.id, m.user_id, role), `${m.name} is now ${roleLabel(role)}.`);
  const remove = (m) => run(m.user_id, () => api.removeMember(project.id, m.user_id), `Removed ${m.name} from ${project.name}.`);
  const add = async () => {
    const ok = await run('add', () => api.putMember(project.id, picked.id, newRole), `Added ${picked.name} as ${roleLabel(newRole)}.`);
    if (ok) { setPicked(null); setAdding(false); setNewRole('contributor'); }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className={`text-xs ${theme.textSecondary}`}>Members ({members ? members.length : '…'})</span>
        {can.manage_members && !adding && (
          <button onClick={() => setAdding(true)} className="flex items-center gap-1 text-xs underline text-blue-500">
            <UserPlus size={12} /> + Add people
          </button>
        )}
      </div>

      {adding && (
        <div className={`flex flex-col gap-2 p-2 rounded border ${theme.border}`}>
          {picked ? (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-bold">{picked.name}</span>
              <select value={newRole} onChange={(e) => setNewRole(e.target.value)} aria-label="Role for the new member"
                className={`px-1.5 py-0.5 text-xs rounded border ${theme.border} ${theme.bg}`}>
                {roleOptions.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
              </select>
              <button onClick={add} disabled={busyId === 'add'} className="px-2 py-0.5 rounded bg-blue-500 text-white disabled:opacity-50">
                {busyId === 'add' ? <Loader2 size={12} className="animate-spin" /> : 'Add'}
              </button>
              <button onClick={() => setPicked(null)} className="underline">Choose someone else</button>
            </div>
          ) : (
            <PeoplePicker theme={theme} lookup={api.lookup} onPick={setPicked} label="Find a person to add"
              excludeIds={(members || []).map((m) => m.user_id)} />
          )}
          <button onClick={() => { setAdding(false); setPicked(null); }} className="self-start text-[10px] underline">Close</button>
        </div>
      )}

      {members === null ? (
        <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {members.map((m) => (
            <li key={m.user_id} className={`flex items-center justify-between gap-2 p-2 rounded border ${theme.border} ${theme.bgSecondary}`}>
              <span className="flex flex-wrap items-center gap-2 min-w-0 text-xs">
                <span className="font-bold truncate">{m.name}</span>
                {m.user_id === currentUserId && <span className={theme.textMuted}>(you)</span>}
                {m.username && <span className={`text-[10px] ${theme.textMuted}`}>@{m.username}</span>}
                {m.status === 'pending' && <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">awaiting approval</span>}
                {m.status === 'disabled' && <span className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-500">disabled</span>}
                {m.added_via === 'admin' && <span className={`text-[9px] ${theme.textMuted}`}>added by admin</span>}
              </span>
              <span className="flex items-center gap-2 shrink-0">
                {editable(m) ? (
                  <select value={m.role} onChange={(e) => changeRole(m, e.target.value)} disabled={busyId !== null}
                    aria-label={`Role for ${m.name}`}
                    className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} disabled:opacity-50`}>
                    {roleOptions.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                  </select>
                ) : (
                  <span className={`text-[10px] ${theme.textSecondary}`}>{roleLabel(m.role)}</span>
                )}
                {editable(m) && (
                  <button onClick={() => remove(m)} disabled={busyId !== null} aria-label={`Remove ${m.name} from ${project.name}`}
                    className={`hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
                    {busyId === m.user_id ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

`api.lookup` is a stable function from the memoized client (`useMemo` in `ProjectPage`); `PeoplePicker` also reads it through `useLatest`, so either way typing never loops.

- [ ] **Step 4: Implement ActivityTab**

Replace `src/components/projects/ActivityTab.jsx` with:

```jsx
import { useCallback, useEffect, useState } from 'react';
import { roleLabel } from '../../lib/projectRoles';

export function timeAgo(at, now = Date.now()) {
  const s = Math.max(0, Math.round((now - new Date(at).getTime()) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(at).toLocaleDateString();
}

// One line per event (A0 §10). People appear by their current names; a
// deleted person reads "a former member"; a removed document keeps its name.
export function describeEvent(ev) {
  const actor = ev.actor?.name || 'A former member';
  const subject = ev.subject?.name || 'a former member';
  const d = ev.details || {};
  const doc = ev.doc?.name || d.name || 'a document';
  switch (ev.kind) {
    case 'project.created': return ev.subject ? `${actor} created the project for ${subject}` : `${actor} created the project`;
    case 'project.renamed': return `${actor} renamed the project from "${d.from}" to "${d.to}"`;
    case 'project.described': return `${actor} changed the description`;
    case 'member.added': return `${actor} added ${subject} as ${roleLabel(d.role)}`;
    case 'member.role_changed': return `${actor} changed ${subject} from ${roleLabel(d.from)} to ${roleLabel(d.to)}`;
    case 'member.removed': return `${actor} removed ${subject}`;
    case 'member.left': return `${actor} left the project`;
    case 'document.added': return `${actor} added "${doc}"`;
    case 'document.removed': return `${actor} removed "${doc}"`;
    default: return `${actor}: ${ev.kind}`;
  }
}

export default function ActivityTab({ theme, api, project }) {
  const [events, setEvents] = useState(null);
  const [next, setNext] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);

  // Every setState here runs after the await, so calling load() from the
  // effect never sets state synchronously inside it.
  const load = useCallback(async (before) => {
    try {
      const page = await api.events(project.id, before);
      setEvents((prev) => (before ? [...(prev || []), ...page.events] : page.events));
      setNext(page.next_before);
    } catch {
      setEvents((prev) => prev || []);
    } finally {
      setLoadingMore(false);
    }
  }, [api, project.id]);

  useEffect(() => { load(null); }, [load]);

  if (events === null) return <p className={`text-xs ${theme.textMuted}`}>Loading…</p>;
  if (events.length === 0) return <p className={`text-xs ${theme.textMuted}`}>No activity yet.</p>;
  return (
    <div className="flex flex-col gap-2">
      <ul className="flex flex-col gap-1">
        {events.map((ev) => (
          <li key={ev.id} className="text-xs">
            {describeEvent(ev)} <span className={`text-[10px] ${theme.textMuted}`}>· {timeAgo(ev.at)}</span>
          </li>
        ))}
      </ul>
      {next && (
        <button onClick={() => { setLoadingMore(true); load(next); }} disabled={loadingMore}
          className="self-start text-xs underline disabled:opacity-50">
          Load more
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass, then lint**

Run: `npx vitest run src/components/projects && npx eslint src/components/projects`
Expected: all passed; eslint prints nothing.

- [ ] **Step 6: Commit** (ask first)

```bash
git add src/components/projects/
git commit -m "feat(ui): Members tab (roles by can, Owner rows Owner-only, add people with a role, busy-disabled controls) and Activity tab (plain-language lines, Load more) (A0 Task 13)"
```

---

### Task 14: Share a document with a person

**Files:**
- Create: `src/components/library/ShareDialog.jsx`, `src/components/library/ShareDialog.test.jsx`
- Modify: `src/components/library/LibraryPage.jsx`, `src/components/library/LibraryPage.test.jsx`

**Interfaces:**
- Consumes: `PeoplePicker`, `projectsApi` (`shares`, `share`, `unshare`, `lookup`) (Task 11); `GET /v1/docs/{id}/shares` (Task 9).
- Produces: `<ShareDialog theme api doc showToast onClose />`; a **Share** button on library rows the caller uploaded.

- [ ] **Step 1: Write the failing tests**

Create `src/components/library/ShareDialog.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ShareDialog from './ShareDialog';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const doc = { doc_id: 'd1', file_name: 'Owned.pdf' };

function api(over = {}) {
  return {
    shares: vi.fn(async () => [{ user_id: 'u1', name: 'Ann Lee', username: 'annl', shared_at: '2026-10-06T00:00:00Z' }]),
    share: vi.fn(async () => null),
    unshare: vi.fn(async () => null),
    lookup: vi.fn(async () => [{ id: 'u1', name: 'Ann Lee', status: 'active' }, { id: 'u2', name: 'Bob Ray', status: 'active' }]),
    ...over,
  };
}

describe('ShareDialog', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists who I shared with and hides them from the picker', async () => {
    const a = api();
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByText('Ann Lee')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Share Owned.pdf with'), { target: { value: 'ra' } });
    expect(await screen.findByRole('button', { name: /Bob Ray/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Ann Lee/ })).toBeNull();
  });

  it('shares with a picked person and reloads the list', async () => {
    const a = api();
    const showToast = vi.fn();
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={showToast} onClose={vi.fn()} />);
    await screen.findByText('Ann Lee');
    fireEvent.change(screen.getByLabelText('Share Owned.pdf with'), { target: { value: 'ra' } });
    fireEvent.click(await screen.findByRole('button', { name: /Bob Ray/ }));
    await waitFor(() => expect(a.share).toHaveBeenCalledWith('d1', 'u2'));
    expect(showToast).toHaveBeenCalledWith('Shared Owned.pdf with Bob Ray.', 3000);
    expect(a.shares).toHaveBeenCalledTimes(2);
  });

  it('stops sharing with someone', async () => {
    const a = api();
    render(<ShareDialog theme={theme} api={a} doc={doc} showToast={vi.fn()} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText('Stop sharing with Ann Lee'));
    await waitFor(() => expect(a.unshare).toHaveBeenCalledWith('d1', 'u1'));
  });
});
```

In `src/components/library/LibraryPage.test.jsx`, add:

```jsx
  it('offers Share only on rows I uploaded, and opens the share dialog', async () => {
    mount();
    await screen.findByText('Owned.pdf');
    expect(screen.queryByRole('button', { name: 'Share Teammate.pdf' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Share ViaProject.pdf' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Share Owned.pdf' }));
    expect(await screen.findByRole('dialog', { name: 'Share Owned.pdf' })).toBeTruthy();
  });
```

and extend `mount()`'s mock so `/v1/docs/d1/shares` returns `json(200, [])` (put that check before the generic `path.startsWith('/v1/docs')` line).

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/library`
Expected: FAIL — `ShareDialog` doesn't exist; no Share button.

- [ ] **Step 3: Implement**

Create `src/components/library/ShareDialog.jsx`:

```jsx
import { useCallback, useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import PeoplePicker from '../people/PeoplePicker';

/**
 * Share one document I uploaded with one person at a time (A0 §10, journey
 * 6). Shares are read-only and can't be passed on (A1 §5). Lists the people
 * I already shared it with, each removable.
 */
export default function ShareDialog({ theme, api, doc, showToast, onClose }) {
  const [shares, setShares] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      setShares(await api.shares(doc.doc_id));
    } catch (e) {
      setShares([]);
      showToast(e.message, 5000);
    }
  }, [api, doc.doc_id, showToast]);

  useEffect(() => { load(); }, [load]);

  const run = async (key, fn, ok) => {
    setBusyId(key);
    try {
      await fn();
      showToast(ok, 3000);
    } catch (e) {
      showToast(e.message, 5000);
    } finally {
      setBusyId(null);
      await load();
    }
  };

  return (
    <div role="dialog" aria-label={`Share ${doc.file_name}`} className={`flex flex-col gap-2 p-2 rounded border ${theme.border} ${theme.bg}`}>
      <div className="flex items-center justify-between text-xs">
        <span className="font-bold">Share “{doc.file_name}”</span>
        <button onClick={onClose} aria-label="Close sharing" className={theme.textSecondary}><X size={12} /></button>
      </div>
      <p className={`text-[10px] ${theme.textMuted}`}>People you share with can read this document. They can't share it on.</p>
      <PeoplePicker theme={theme} lookup={api.lookup} label={`Share ${doc.file_name} with`} disabled={busyId !== null}
        excludeIds={(shares || []).map((s) => s.user_id)}
        onPick={(p) => run(p.id, () => api.share(doc.doc_id, p.id), `Shared ${doc.file_name} with ${p.name}.`)} />
      {shares === null ? (
        <p className={`text-[10px] ${theme.textMuted}`}>Loading…</p>
      ) : shares.length === 0 ? (
        <p className={`text-[10px] ${theme.textMuted}`}>Not shared with anyone yet.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {shares.map((s) => (
            <li key={s.user_id} className="flex items-center justify-between gap-2 text-xs">
              <span>
                {s.name}
                {s.username && <span className={`ml-1 text-[10px] ${theme.textMuted}`}>@{s.username}</span>}
              </span>
              <button onClick={() => run(s.user_id, () => api.unshare(doc.doc_id, s.user_id), `Stopped sharing with ${s.name}.`)}
                disabled={busyId !== null} aria-label={`Stop sharing with ${s.name}`}
                className={`hover:text-red-500 disabled:opacity-50 ${theme.textSecondary}`}>
                {busyId === s.user_id ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

In `src/components/library/LibraryPage.jsx`:
- imports: `useMemo` from react, `import ShareDialog from './ShareDialog';`, `import { projectsApi } from '../../lib/projectsApi';`;
- state: `const [shareDocId, setShareDocId] = useState(null);` and `const api = useMemo(() => projectsApi(apiHost, apiPort), [apiHost, apiPort]);`;
- in the row's right-hand button group, before the Open button:

```jsx
                      {doc.added_via === 'upload' && (
                        <button
                          onClick={() => setShareDocId(shareDocId === doc.doc_id ? null : doc.doc_id)}
                          aria-label={`Share ${doc.file_name}`}
                          className={`flex items-center gap-1 text-[10px] hover:text-blue-500 ${theme.textSecondary}`}
                        >
                          <Share2 size={10} /> Share
                        </button>
                      )}
```

- after the `ProjectChips` block inside the row:

```jsx
                  {shareDocId === doc.doc_id && (
                    <ShareDialog theme={theme} api={api} doc={doc} showToast={showToast} onClose={() => setShareDocId(null)} />
                  )}
```

- [ ] **Step 4: Run them to verify they pass, then lint**

Run: `npx vitest run src/components/library && npx eslint src/components/library`
Expected: all passed; eslint prints nothing.

- [ ] **Step 5: Commit** (ask first)

```bash
git add src/components/library/
git commit -m "feat(ui): Share a document I uploaded with a person, see and stop existing shares (A0 Task 14)"
```

---

### Task 15: Admin console — every project, recovery, project limits, enroll names

**Files:**
- Create: `src/components/admin/AdminProjectsSection.jsx`, `src/components/admin/AdminProjectsSection.test.jsx`
- Modify: `src/components/admin/AdminConsole.jsx`, `src/components/admin/AdminConsole.test.jsx`

**Interfaces:**
- Consumes: `projectsApi.adminProjects`, `putMember` (Task 11); `ProjectPage` (Task 12); `GET /v1/admin/projects`, `project_limit`, enroll names (Task 10).
- Produces: `<AdminProjectsSection theme apiHost apiPort currentUserId showToast onOpenProject />`.

- [ ] **Step 1: Write the failing tests**

Create `src/components/admin/AdminProjectsSection.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AdminProjectsSection } from './AdminProjectsSection';

vi.mock('../../utils/apiFetch', () => ({ apiFetch: vi.fn() }));
import { apiFetch } from '../../utils/apiFetch';

const theme = { bg: '', bgSecondary: '', border: '', text: '', textSecondary: '', textMuted: '' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const owned = { id: 'p1', name: 'Owned', created_at: '2026-10-01', member_count: 2, doc_count: 3, owners: [{ id: 'o', name: 'Olu' }], ownerless: false };
const orphan = { id: 'p2', name: 'Orphan', created_at: '2026-10-02', member_count: 0, doc_count: 1, owners: [], ownerless: true };

function mount() {
  apiFetch.mockImplementation(async (h, p, path, opts = {}) => {
    if (path === '/v1/admin/projects') return json(200, [orphan, owned]);
    if (path === '/v1/admin/projects?ownerless=true') return json(200, [orphan]);
    if (opts.method === 'PUT' && path === '/v1/projects/p2/members/admin1') return json(200, {});
    return json(404, {});
  });
  const props = { theme, apiHost: '', apiPort: '', currentUserId: 'admin1', showToast: vi.fn(), onOpenProject: vi.fn() };
  render(<AdminProjectsSection {...props} />);
  return props;
}

describe('AdminProjectsSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists every project with owners and counts, and opens one', async () => {
    const props = mount();
    expect(await screen.findByText('Olu')).toBeTruthy();
    expect(screen.getByText('No owner')).toBeTruthy();
    expect(screen.getByText('2 members · 3 documents')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open Owned' }));
    expect(props.onOpenProject).toHaveBeenCalledWith('p1');
  });

  it('filters to ownerless projects', async () => {
    mount();
    await screen.findByText('Olu');
    fireEvent.click(screen.getByLabelText('Ownerless only'));
    await waitFor(() => expect(screen.queryByText('Olu')).toBeNull());
    expect(screen.getByText('Orphan')).toBeTruthy();
  });

  it('recovers an ownerless project by adding me as Owner', async () => {
    const props = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Add me as Owner of Orphan' }));
    await waitFor(() => expect(props.showToast).toHaveBeenCalledWith("You're now an Owner of Orphan.", 4000));
    const put = apiFetch.mock.calls.find(([, , , o]) => o?.method === 'PUT');
    expect(JSON.parse(put[3].body)).toEqual({ role: 'owner' });
    expect(screen.queryByRole('button', { name: 'Add me as Owner of Owned' })).toBeNull();
  });
});
```

In `src/components/admin/AdminConsole.test.jsx`:
1. In `mount()`'s mock, add `if (path.startsWith('/v1/admin/projects')) return json(200, []);` before the final `return json(404, {});` (do the same in `AdminConsole.enroll-busy.test.jsx`'s mock), so the new Projects section loads quietly.
2. In the `user()` fixture add `project_limit: null, username: null, first_name: null, last_name: null,`.
3. Add inside `describe('AdminConsole — users', …)`:

```jsx
  it('sets a per-user project limit, empty meaning the default', async () => {
    mount({ users: [user()] });
    fireEvent.change(await screen.findByLabelText('Project limit for a@x.io'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set project limit for a@x.io' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(1));
    expect(lastBody('PATCH')).toEqual({ project_limit: 5 });
    fireEvent.change(screen.getByLabelText('Project limit for a@x.io'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set project limit for a@x.io' }));
    await waitFor(() => expect(calls('PATCH').length).toBe(2));
    expect(lastBody('PATCH')).toEqual({ project_limit: null });
  });

  it('enrolls with username, first and last name', async () => {
    mount({ users: [user()] });
    fireEvent.change(await screen.findByLabelText('Enroll email'), { target: { value: 'n@example.com' } });
    fireEvent.change(screen.getByLabelText('Enroll username'), { target: { value: 'newbie' } });
    fireEvent.change(screen.getByLabelText('Enroll first name'), { target: { value: 'New' } });
    fireEvent.change(screen.getByLabelText('Enroll last name'), { target: { value: 'Person' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enroll' }));
    await waitFor(() => expect(calls('POST').length).toBe(1));
    expect(lastBody('POST')).toMatchObject({ email: 'n@example.com', username: 'newbie', first_name: 'New', last_name: 'Person' });
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/admin`
Expected: FAIL — the section module is missing; no project-limit or name fields.

- [ ] **Step 3: Implement the section**

Create `src/components/admin/AdminProjectsSection.jsx`:

```jsx
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { projectsApi } from '../../lib/projectsApi';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/**
 * Every project on the server (A0 §10): owners, counts, an "Ownerless only"
 * filter, Open (the project page with admin powers) and, for a project whose
 * last Owner was deleted, "Add me as Owner" — the recovery path. The server
 * logs an admin adding themselves at WARNING.
 */
export function AdminProjectsSection({ theme, apiHost, apiPort, currentUserId, showToast, onOpenProject }) {
  const api = useMemo(() => projectsApi(apiHost, apiPort), [apiHost, apiPort]);
  const [ownerlessOnly, setOwnerlessOnly] = useState(false);
  const [rows, setRows] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.adminProjects(ownerlessOnly));
    } catch (e) {
      setRows([]);
      showToast(`Could not load projects: ${e.message}`, 5000);
    }
  }, [api, ownerlessOnly, showToast]);

  useEffect(() => { load(); }, [load]);

  const recover = async (p) => {
    setBusyId(p.id);
    try {
      await api.putMember(p.id, currentUserId, 'owner');
      showToast(`You're now an Owner of ${p.name}.`, 4000);
    } catch (e) {
      showToast(e.message, 5000);
    } finally {
      setBusyId(null);
      await load();
    }
  };

  return (
    <section className="flex flex-col gap-3" aria-label="Projects">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-bold uppercase tracking-wider text-blue-500">Projects</h2>
        <label className="flex items-center gap-1 text-[10px]">
          <input type="checkbox" checked={ownerlessOnly} onChange={(e) => setOwnerlessOnly(e.target.checked)} aria-label="Ownerless only" />
          Ownerless only
        </label>
      </div>
      {rows === null ? (
        <p className={`text-xs ${theme.textMuted}`}>Loading…</p>
      ) : rows.length === 0 ? (
        <p className={`text-xs ${theme.textMuted}`}>{ownerlessOnly ? 'No ownerless projects.' : 'No projects yet.'}</p>
      ) : rows.map((p) => (
        <div key={p.id} className={`flex items-center justify-between gap-2 p-3 rounded-lg border ${theme.border} ${theme.bgSecondary} text-xs`}>
          <span className="flex flex-col gap-0.5 min-w-0">
            <span className="font-bold truncate">{p.name}</span>
            <span className={`text-[10px] ${theme.textMuted}`}>
              {p.ownerless
                ? <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">No owner</span>
                : p.owners.map((o) => o.name).join(', ')}
            </span>
            <span className={`text-[10px] ${theme.textMuted}`}>{plural(p.member_count, 'member')} · {plural(p.doc_count, 'document')}</span>
          </span>
          <span className="flex items-center gap-2 shrink-0">
            <button onClick={() => onOpenProject(p.id)} aria-label={`Open ${p.name}`} className="underline">Open</button>
            {p.ownerless && (
              <button onClick={() => recover(p)} disabled={busyId !== null} aria-label={`Add me as Owner of ${p.name}`}
                className="underline text-green-600 disabled:opacity-50">
                {busyId === p.id ? <Loader2 size={12} className="animate-spin" /> : 'Add me as Owner'}
              </button>
            )}
          </span>
        </div>
      ))}
    </section>
  );
}
```

- [ ] **Step 4: Wire the console**

In `src/components/admin/AdminConsole.jsx`:

1. Imports: `import { AdminProjectsSection } from './AdminProjectsSection';` and `import ProjectPage from '../projects/ProjectPage';`.
2. Add after `BudgetField`:

```jsx
// Per-user project limit (A0 §5). Same semantics as the budget: empty =
// deployment default (PROJECT_LIMIT_PER_USER), 0 = unlimited.
function ProjectLimitField({ u, theme, onSet }) {
  const [val, setVal] = useState(u.project_limit == null ? '' : String(u.project_limit));
  return (
    <span className="flex items-center gap-1 shrink-0">
      <input
        type="number" min="0" value={val}
        onChange={(e) => setVal(e.target.value)}
        placeholder="default"
        aria-label={`Project limit for ${u.email}`}
        title="projects this person may create; empty = default, 0 = unlimited"
        className={`px-1.5 py-0.5 text-[10px] rounded border ${theme.border} ${theme.bg} w-16`}
      />
      <button onClick={() => onSet(u, val)} aria-label={`Set project limit for ${u.email}`} className="text-[10px] underline">Set</button>
    </span>
  );
}
```

3. State: `const [openProjectId, setOpenProjectId] = useState(null);` and enroll fields `enrollUsername`, `enrollFirst`, `enrollLast` (each `useState('')`).
4. `submitEnroll`: after the display-name line add

```jsx
    if (enrollUsername.trim()) body.username = enrollUsername.trim();
    if (enrollFirst.trim()) body.first_name = enrollFirst.trim();
    if (enrollLast.trim()) body.last_name = enrollLast.trim();
```

and reset them with the other fields after a successful enroll (`setEnrollUsername(''); setEnrollFirst(''); setEnrollLast('');`).
5. Add after `setBudget`:

```jsx
  const setProjectLimit = async (u, raw) => {
    const trimmed = raw.trim();
    const value = trimmed === '' ? null : Number(trimmed);
    if (trimmed !== '' && (!Number.isInteger(value) || value < 0)) {
      showToast('Project limit must be a whole number ≥ 0 (empty = default, 0 = unlimited).', 4000);
      return;
    }
    await patchUser(u.id, { project_limit: value });
  };
```

6. In the enroll form, after the display-name input, add three inputs in the same style:

```jsx
            <input type="text" value={enrollUsername} onChange={(e) => setEnrollUsername(e.target.value)}
              placeholder="username" aria-label="Enroll username"
              className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[100px]`} />
            <input type="text" value={enrollFirst} onChange={(e) => setEnrollFirst(e.target.value)}
              placeholder="first name" aria-label="Enroll first name"
              className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[100px]`} />
            <input type="text" value={enrollLast} onChange={(e) => setEnrollLast(e.target.value)}
              placeholder="last name" aria-label="Enroll last name"
              className={`px-2 py-1 text-xs rounded border ${theme.border} ${theme.bg} min-w-[100px]`} />
```

7. In each user row, next to `BudgetField`:

```jsx
                    <ProjectLimitField
                      key={`${u.id}-pl-${u.project_limit ?? 'null'}`}
                      u={u} theme={theme} onSet={setProjectLimit}
                    />
```

8. Add the section before `{/* … Inference usage … */}`:

```jsx
        <AdminProjectsSection theme={theme} apiHost={apiHost} apiPort={apiPort} currentUserId={currentUserId}
          showToast={showToast} onOpenProject={setOpenProjectId} />
```

9. Just before the component's main `return (`, add the project-page view:

```jsx
  if (openProjectId) {
    return (
      <div className={`h-full w-full overflow-y-auto ${theme.bg} ${theme.text}`}>
        <div className="max-w-4xl mx-auto px-4 md:px-8 py-6">
          <ProjectPage theme={theme} apiHost={apiHost} apiPort={apiPort} projectId={openProjectId}
            currentUserId={currentUserId} showToast={showToast} onBack={() => setOpenProjectId(null)} />
        </div>
      </div>
    );
  }
```

10. Update the component's header comment ("Inference usage, Deployment config, Assistant (v2.4)") to list "Users, Projects (A0), Inference usage, Deployment config, Assistant".
11. The delete-user toast: change "their documents and chat history were removed" to "their library and chat history were removed; projects they belonged to stay (ownerless ones appear under Projects)".

- [ ] **Step 5: Run the tests to verify they pass, then lint**

Run: `npx vitest run src/components/admin && npx eslint src/components/admin`
Expected: all passed; eslint prints nothing.

- [ ] **Step 6: Run the whole frontend suite**

Run: `npx vitest run 2>&1 | tail -5`
Expected: all passed.

- [ ] **Step 7: Commit** (ask first)

```bash
git add src/components/admin/
git commit -m "feat(admin-ui): Projects section (owners, counts, Ownerless only, Open, Add me as Owner), per-user project limit, enroll with username and names (A0 Task 15)"
```

---

### Task 16: Configuration and documentation

**Files:**
- Modify: `.env.example`, `CHANGELOG.md`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`

- [ ] **Step 1: `.env.example`**

Insert before the `# ─── Logging ───` section:

```bash
# ─── Projects & people directory (A0) ─────────────────────────────────────────
# Who can be found when adding someone to a project or sharing a document.
#   exact  — only by a full email address (default)
#   domain — type-ahead among people with the caller's email domain, only when
#            that domain is listed in USER_DIRECTORY_DOMAINS (others: exact)
#   open   — type-ahead over everyone (GitLab's default)
# An exact email works in every mode. Disabled accounts never appear.
# USER_DIRECTORY_MODE=exact
# Comma-separated organisation email domains for `domain` mode.
# USER_DIRECTORY_DOMAINS=example.com,corp.example.com
# Show email addresses in lookups and member lists (true|false).
# USER_DIRECTORY_SHOW_EMAIL=false
# Who may create projects: readers (anyone with the reader capability) | admins.
# PROJECT_CREATION=readers
# Projects one person may create (count). 0 = unlimited. Admins can override per
# user in the admin console; admins themselves are never limited.
# PROJECT_LIMIT_PER_USER=20
# Keep project activity history this many days (0 = forever). Read at startup;
# older events are deleted then and every 24 hours.
# PROJECT_EVENTS_RETENTION_DAYS=0
```

- [ ] **Step 2: `CHANGELOG.md`**

Under `## [Unreleased]`, add to `### Added` (create the heading if absent, keeping the existing entries):

```markdown
- **Projects for teams, with roles (A0).** Every project has members with a
  role: Reader (read the documents), Contributor (also file their own
  documents), Maintainer (also remove documents, rename, manage members up to
  Maintainer) and Owner (everything, including Owners and deleting the
  project). A project keeps at least one Owner. Library → Projects lists your
  projects; a project page has Documents, Members and Activity tabs.
- **Find people to add or share with.** Type a name or a full email address.
  `USER_DIRECTORY_MODE` (exact | domain | open), `USER_DIRECTORY_DOMAINS` and
  `USER_DIRECTORY_SHOW_EMAIL` decide who can be found; the default finds people
  only by their full email.
- **Share a document from the Library.** A Share button on documents you
  uploaded; see and stop existing shares.
- **Project activity history.** Who added or removed whom, role changes,
  renames, documents filed and removed. `PROJECT_EVENTS_RETENTION_DAYS`
  (default 0 = keep forever).
- **Admin: every project and recovery.** Admin → Projects lists all projects
  with their owners; "Ownerless only" and "Add me as Owner" recover a project
  whose last Owner was deleted. Per-user project limits
  (`PROJECT_LIMIT_PER_USER`, default 20) and `PROJECT_CREATION`
  (readers | admins). Enroll takes username, first and last name.
```

Add a `### Changed` subsection (or append to it):

```markdown
- **Breaking (API):** `PUT /v1/projects/{id}/members/{user_id}` now needs a
  body `{"role": "reader|contributor|maintainer|owner"}`; a body-less call is
  a 422. Project objects drop `is_owner` and `owner_user_id` for `my_role`,
  `member_count`, `doc_count`, `created_by` and `can`.
- Deleting a user no longer deletes the projects they created; only their
  memberships go. A project left without an Owner appears in Admin → Projects.
- Admins file and remove project documents only through a member role.
- People's username, first and last name are refreshed from the login token.
```

Add a `### Fixed` subsection (or append to it):

```markdown
- Migrations 014–016 never recorded their version and re-ran (harmlessly) on
  every startup; every migration now records itself, and a test checks it.
```

- [ ] **Step 3: `docs/USER_GUIDE.md`**

Insert a new section after `## Library: documents on the server` (before `## Chat: models and streaming`):

```markdown
## Projects and people

A project is a shared shelf of documents for a team. Open **Library →
Projects** to see the projects you belong to; **New project** creates one,
and you become its Owner.

**Roles.** Each member has one role:

| Role | Can |
|---|---|
| Reader | see the project, its members, activity and documents |
| Contributor | also file documents they uploaded into the project |
| Maintainer | also remove any document, rename the project, and add, remove or change members up to Maintainer |
| Owner | everything, including Owners and deleting the project |

A project always keeps at least one Owner, so the last Owner can't leave or
step down until someone else is made Owner.

**Adding people.** On a project page, open **Members → + Add people**, type a
name or their full email address, pick the person, choose a role and press
**Add**. Whether typing a name finds people depends on your organisation's
setting; a full email address always works. "Awaiting approval" means the
account exists but an admin hasn't activated it yet.

**Documents.** **Documents → File a document** adds one of your uploads. A
Maintainer removes a document with × (a document nobody else has in their
library is deleted with it, so you're asked first).

**Activity** lists who changed what, newest first.

**Sharing one document with one person.** In the Library, press **Share** on a
document you uploaded, find the person, and pick them. They can read it but
can't share it on. The same panel lists who you've shared with; × stops
sharing.

**Leaving.** **Leave** in the project header. Documents you had in your own
library stay there.

What you can't do yet: chat with a whole project at once (planned), or upload
a revised file as a new version of a document (planned).
```

In the `## Admin console (admins only)` section, add:

```markdown
**Projects.** Every project, with its owners and counts. **Ownerless only**
shows projects whose last Owner was deleted; **Add me as Owner** takes one
over (recorded as "added by admin" and logged). **Open** shows the project
page; as an admin you can manage members, rename and delete any project, but
you see its documents only if you're a member. Each user row has a
**Project limit** (empty = the deployment default, 0 = unlimited).
```

- [ ] **Step 4: `docs/ARCHITECTURE.md`**

In the `## Backend` section, add:

```markdown
### Projects, roles and activity (A0)

- `project_members.role` (reader < contributor < maintainer < owner) is the
  only source of project access; `projects.created_by` is history. Every
  project route starts with `authz.require_project_role` (404 non-member,
  403 `insufficient_role`); responses carry `can` from `authz.can_for`, and
  the UI renders from it.
- Membership writes take `SELECT … FROM projects … FOR UPDATE` first and read
  the caller's role after it, so concurrent changes on one project serialize
  and the last-Owner rule can't be raced (`services/project_members.py`).
- `project_events` gets one row per change in the same transaction
  (`services/project_events.py`); `audit.log` stays the operators' trail.
- The people directory (`services/people.py`) owns the person-label rule and
  the `USER_DIRECTORY_*` modes.
```

and under `## Invariants & traps` add:

```markdown
- Every `server/sql/NNN_*.sql` must end with
  `INSERT INTO schema_migrations(version) VALUES (NNN) ON CONFLICT DO NOTHING;`
  — the runner doesn't record versions, and a non-idempotent migration that
  re-runs stops startup. `test_every_migration_records_its_version` checks it.
```

- [ ] **Step 5: Check no deployment-specific value slipped in**

Run: `git diff -- .env.example CHANGELOG.md docs/ | grep -inE "<real-domain>|https?://[a-z0-9.-]+\.(net|org|io)" | grep -v example`
Expected: no output.

- [ ] **Step 6: Commit** (ask first)

```bash
git add .env.example CHANGELOG.md docs/USER_GUIDE.md docs/ARCHITECTURE.md
git commit -m "docs: A0 — projects and roles in the user guide, admin recovery, six env settings, architecture notes, changelog with the API breaks (A0 Task 16)"
```

---

### Task 17: The gate and the walk through the running app

This task proves journeys 1–9 work for a person, not just in tests. It writes no product code. A step that fails here becomes a fix (with its own failing test first), not a note.

**Files:**
- Create: `docs/superpowers/handoff/2026-10-06-a0-projects-roles/HANDOFF.md`

- [ ] **Step 1: Run the full gate**

```bash
.venv/bin/python -m pytest server/tests -q 2>&1 | tail -3
npx vitest run 2>&1 | tail -5
npx eslint src
```

Expected: backend 0 failed; vitest all passed; eslint prints nothing.

- [ ] **Step 2: Start the stack with Keycloak**

```bash
env -u XDG_DATA_HOME podman-compose up -d postgres keycloak
./startup.sh
```

Confirm `.env` has the dev Keycloak `OIDC_*` lines (`.env.example` lines 28–31) without printing secrets: `grep -c '^OIDC_ISSUER=http://localhost:18080' .env` → `1`. Open the app in the browser (Chrome tools: `tabs_context_mcp` first, then a new tab) and sign in as the admin.

- [ ] **Step 3: Walk the journeys, recording each result**

Use two browser sessions: the admin's, and a second (incognito/new profile) for the second user. Record each row's outcome (pass / the exact failure) in the handoff file.

| # | Journey | Do | Expect |
|---|---|---|---|
| 0 | Set-up | Admin → Users: enroll `second@example.com` with username `second`, first `Sam`, last `Second`, status active, capability reader | Temp password shown; row appears with a Project limit field |
| 1 | Create | Library → New project "Walk Test" | Projects tab shows the card with "Owner" |
| 2 | Add a colleague | Project → Members → + Add people → type `second@example.com` → pick → role Contributor → Add | "Added Sam Second as Contributor"; Members lists Sam |
| 3 | Change role / remove | Change Sam to Maintainer; then back to Contributor | Toasts; Activity shows both changes |
| 4 | File a document | Upload a PDF in the reader, then Project → Documents → File a document | It appears in the project; Activity: "… added "<name>"" |
| 5 | Maintainer removes | Make Sam Maintainer; as Sam (second session), × on the document → Confirm remove | Removed; Activity records it |
| 6 | Share one document | Library → Share on the uploaded PDF → pick Sam | Sam's Library shows it "shared by …"; × in the dialog stops it |
| 7 | Leave | As Sam, project header → Leave → Confirm | Sam's Projects tab no longer lists it |
| 8 | Admin recovery | Make Sam the only Owner (add as Owner, then the admin leaves), then Admin → Users → delete Sam; Admin → Projects → Ownerless only → Add me as Owner | The project reappears in the admin's Library with "Owner"; Members shows "added by admin" |
| 9 | Activity | Project → Activity | Every step above, newest first, Sam shown as "A former member" after deletion |

Also check: a Reader sees no Edit/Delete/× controls; the reader's upload picker lists only projects where you're Contributor or above.

- [ ] **Step 4: Write the handoff**

Create `docs/superpowers/handoff/2026-10-06-a0-projects-roles/HANDOFF.md` with: the commit range, the gate results (counts), the walk table with outcomes, anything API-only or env-only (expected: none for journeys 1–9; the six env settings are deployer-only by design), and what A0 deliberately leaves to V1/C2.1 (project chat, document versions).

If the browser tools are unavailable, write that the walk is **owed**, list which rows remain, and do not call A0 done.

- [ ] **Step 5: Commit** (ask first)

```bash
git add docs/superpowers/handoff/2026-10-06-a0-projects-roles/HANDOFF.md
git commit -m "docs(handoff): A0 gate and running-app walk results"
```
