# Document Library & RAG — Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-doc reader into a shareable **document library** — projects, per-doc grants, tags, a list endpoint, and a Library page — while keeping the deny-by-default 404 posture.

**Architecture:** A new `009_library.sql` adds `projects`, `project_members`, `doc_grants`, and `documents.project_id`/`tags`. Read access widens from owner-only to `can_read = owner ∨ project-member ∨ grantee`, expressed as one SQL predicate (`assert_can_read_doc` + a shared filter fragment); writes stay owner-only. A new `GET /v1/docs` list endpoint and a `projects` router expose the library; a Library page consumes them.

**Tech Stack:** FastAPI/Starlette, psycopg3 (async), pgvector (existing), React 19 + Vite + Vitest.

**Spec:** [docs/superpowers/specs/2026-09-17-document-library-rag-design.md](../specs/2026-09-17-document-library-rag-design.md) (Phase 0 rows in §10)

## Global Constraints

- **Migration number is `009`** (`server/sql/009_library.sql`). `008` is reserved by the Keycloak-identity track — do **not** reuse it.
- **Phase 0 only.** `documents.description` / `description_embedding` and the `docScope` cross-doc search are **Phase 1/2** — do not add them here. Multi-select "Ask about these" is Phase 1.
- **404-indistinguishability holds everywhere:** missing doc and readable-but-not-owned and not-readable all surface as `404` (never leak existence). Reads use `can_read`; writes/deletes stay owner-only.
- **Capability coordination:** this branch still gates on `get_current_user` (pre-auth-rework). At integration with the Keycloak-identity branch, the read routes additionally gain `require_capability("reader")` and the Library page gates on the `reader` capability — noted in each affected task, not implemented here.
- **Router-test pattern:** the docs router's guards open their own pool connection via `get_pool()` (not the `get_conn` dependency). Follow `server/tests/test_docs_authz.py`'s existing setup for library tests; projects-router tests may use the `_app(db_conn, principal)` + `dependency_overrides[get_conn]` pattern from `test_admin_router.py`.
- **Commit after each task**, self-contained, with per-commit approval.

---

### Task 1: Migration 009 — projects, members, grants, doc columns

**Files:**
- Create: `server/sql/009_library.sql`
- Test: `server/tests/test_library_schema.py`

**Interfaces:**
- Produces: tables `projects(id, owner_user_id, name, description, created_at)`, `project_members(project_id, user_id)`, `doc_grants(doc_id, grantee_user_id)`; `documents.project_id UUID NULL`, `documents.tags TEXT[]`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_library_schema.py
import pytest
pytestmark = pytest.mark.asyncio


async def test_project_and_membership_roundtrip(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o1','o@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'Proj') RETURNING id",
        (owner,))
    pid = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, owner))
    cur = await db_conn.execute(
        "SELECT count(*) FROM project_members WHERE project_id=%s", (pid,))
    assert (await cur.fetchone())[0] == 1


async def test_documents_gain_project_and_tags(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o2','o2@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, tags) "
        "VALUES ('d1','f.pdf','pdf',10,%s,'{alpha,beta}')", (owner,))
    cur = await db_conn.execute("SELECT tags, project_id FROM documents WHERE doc_id='d1'")
    tags, project_id = await cur.fetchone()
    assert set(tags) == {"alpha", "beta"} and project_id is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_library_schema.py -v`
Expected: FAIL — `relation "projects" does not exist` / `column "tags" does not exist`.

- [ ] **Step 3: Write the migration**

```sql
-- server/sql/009_library.sql
-- Document library: projects (grouping + primary sharing unit), per-doc grants
-- (exception path), and tags. Read access = owner OR project member OR grantee.
CREATE TABLE IF NOT EXISTS projects (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS doc_grants (
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    grantee_user_id UUID NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
    PRIMARY KEY (doc_id, grantee_user_id)
);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS project_id UUID
    REFERENCES projects(id) ON DELETE SET NULL;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS documents_project_idx   ON documents(project_id);
CREATE INDEX IF NOT EXISTS documents_tags_gin      ON documents USING GIN (tags);
CREATE INDEX IF NOT EXISTS doc_grants_grantee_idx  ON doc_grants(grantee_user_id);
CREATE INDEX IF NOT EXISTS project_members_user_idx ON project_members(user_id);
```

> `gen_random_uuid()` is built in on the compose Postgres (matches the `users` table's id default). If a `test_library_schema` insert errors on it, the migration needs `CREATE EXTENSION IF NOT EXISTS pgcrypto;` at the top — check `005_users_ownership.sql` for how `users.id` defaults and mirror it.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_library_schema.py -v`
Expected: PASS (migration auto-applies to the test DB).

- [ ] **Step 5: Commit**

```bash
git add server/sql/009_library.sql server/tests/test_library_schema.py
git commit -m "feat(library): migration 009 — projects, members, doc_grants, documents.project_id/tags"
```

---

### Task 2: Access model — `assert_can_read_doc` + `can_read` list fragment

**Files:**
- Modify: `server/auth/authz.py`
- Test: `server/tests/test_library_authz.py`

**Interfaces:**
- Produces: `authz.assert_can_read_doc(conn, doc_id, user_id) -> None` (404 unless owner/member/grantee); `authz.CAN_READ_DOCS_SQL` — a reusable `WHERE`-fragment string + a helper `readable_docs_where(alias="d") -> str` returning the predicate using `%s` for the user id (used 3× in one query, so callers pass the uid 3 times, or use a named style).

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_library_authz.py
import pytest
from fastapi import HTTPException
from server.auth import authz
pytestmark = pytest.mark.asyncio


async def _user(conn, sub, email):
    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i',%s,%s,'member','active') RETURNING id", (sub, email))
    return str((await cur.fetchone())[0])


async def _doc(conn, doc_id, owner, project_id=None):
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, project_id) "
        "VALUES (%s,'f.pdf','pdf',1,%s,%s)", (doc_id, owner, project_id))


async def test_owner_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    await _doc(db_conn, "d1", o)
    await authz.assert_can_read_doc(db_conn, "d1", o)  # no raise


async def test_stranger_cannot_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    s = await _user(db_conn, "s", "s@x.io")
    await _doc(db_conn, "d1", o)
    with pytest.raises(HTTPException) as e:
        await authz.assert_can_read_doc(db_conn, "d1", s)
    assert e.value.status_code == 404


async def test_project_member_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    m = await _user(db_conn, "m", "m@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (o,))
    pid = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, m))
    await _doc(db_conn, "d1", o, pid)
    await authz.assert_can_read_doc(db_conn, "d1", m)  # no raise


async def test_grantee_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    g = await _user(db_conn, "g", "g@x.io")
    await _doc(db_conn, "d1", o)
    await db_conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES ('d1',%s)", (g,))
    await authz.assert_can_read_doc(db_conn, "d1", g)  # no raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_library_authz.py -v`
Expected: FAIL — `assert_can_read_doc` undefined.

- [ ] **Step 3: Implement in `authz.py`**

```python
async def assert_can_read_doc(conn, doc_id: str, user_id: str) -> None:
    """Read access = owner OR project member OR explicit grantee. Missing and
    not-readable are both 404 (no existence leak)."""
    cur = await conn.execute(
        """
        SELECT 1 FROM documents d
        WHERE d.doc_id = %s AND (
            d.user_id = %s
            OR EXISTS (SELECT 1 FROM project_members pm
                       WHERE pm.project_id = d.project_id AND pm.user_id = %s)
            OR EXISTS (SELECT 1 FROM doc_grants g
                       WHERE g.doc_id = d.doc_id AND g.grantee_user_id = %s)
        )
        """,
        (doc_id, user_id, user_id, user_id),
    )
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Document not found")


def readable_docs_where(alias: str = "d") -> str:
    """Reusable predicate for list/search: `<alias>` is a `documents` row and
    the caller passes the user id THREE times positionally after any earlier
    params. Keeps access resolution in SQL, never post-filtered in Python."""
    return (
        f"({alias}.user_id = %s "
        f"OR EXISTS (SELECT 1 FROM project_members pm "
        f"WHERE pm.project_id = {alias}.project_id AND pm.user_id = %s) "
        f"OR EXISTS (SELECT 1 FROM doc_grants g "
        f"WHERE g.doc_id = {alias}.doc_id AND g.grantee_user_id = %s))"
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_library_authz.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/authz.py server/tests/test_library_authz.py
git commit -m "feat(library): can_read access model (owner/member/grantee), 404-preserving"
```

---

### Task 3: Read routes widen to `can_read`; writes stay owner-only

**Files:**
- Modify: `server/routers/docs.py` (add `_require_doc_reader`; switch GET/read routes to it)
- Test: `server/tests/test_docs_authz.py` (extend)

**Interfaces:**
- Consumes: `authz.assert_can_read_doc`.
- Produces: `_require_doc_reader(doc_id, principal) -> Principal` (mirrors `_require_doc_owner` but uses `assert_can_read_doc`).

- [ ] **Step 1: Write the failing test**

```python
# append to server/tests/test_docs_authz.py — follow the file's existing pattern
async def test_grantee_can_get_document_but_not_delete(db_conn_or_pool):
    # owner registers d1; grant read to user g; g GETs /v1/docs/d1 → 200;
    # g DELETE /v1/docs/d1 → 404 (writes owner-only). Adapt to the file's
    # existing harness (this router uses get_pool() in its guards).
    ...
```

> Read the file's current setup (it exercises the real pool). The behavior contract: a **grantee** or **project member** can hit the read routes (`GET /{doc_id}`, `POST /{doc_id}/search`, `GET /{doc_id}/markdown`, `POST /{doc_id}/chunks` if that is a read in context — see note) but **not** `DELETE`/write routes, which stay `_require_doc_owner`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_docs_authz.py -v`
Expected: FAIL (grantee currently 404s on read — owner-only).

- [ ] **Step 3: Implement `_require_doc_reader` and swap read routes**

```python
# server/routers/docs.py — beside _require_doc_owner
from ..auth.authz import assert_owns_doc, assert_can_read_doc  # extend the import

async def _require_doc_reader(
    doc_id: DocId,
    principal: Principal = Depends(get_current_user),
) -> Principal:
    """Read gate: owner OR project member OR grantee (404 otherwise)."""
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await assert_can_read_doc(conn, doc_id, principal.user_id)
    return principal
```

Switch the **read** routes' dependency from `Depends(_require_doc_owner)` to
`Depends(_require_doc_reader)`:
- `GET /{doc_id}` (get_document)
- `POST /{doc_id}/search` (search_document)
- `GET /{doc_id}/markdown` (get_document_markdown)

Leave **write/delete/index/convert** routes on `_require_doc_owner`:
`DELETE /{doc_id}`, `POST /{doc_id}/chunks`, `POST /{doc_id}/index`,
`POST /{doc_id}/pdf`, `DELETE /{doc_id}/pdf`, `POST /{doc_id}/convert`,
`DELETE /{doc_id}/markdown`, and `POST ""` (register).

> **Integration note:** at merge with the Keycloak-identity branch, these read routes ALSO get `require_capability("reader")`. Not in this task.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_docs_authz.py -v`
Expected: PASS (grantee/member read; writes still owner-only).

- [ ] **Step 5: Commit**

```bash
git add server/routers/docs.py server/tests/test_docs_authz.py
git commit -m "feat(library): read routes honor can_read (owner/member/grantee); writes stay owner-only"
```

---

### Task 4: `GET /v1/docs` — the library list endpoint

**Files:**
- Modify: `server/routers/docs.py`
- Test: `server/tests/test_docs_list.py`

**Interfaces:**
- Consumes: `authz.readable_docs_where`.
- Produces: `GET /v1/docs?q=&project_id=&tag=` → `[{doc_id, file_name, state, tags, project_id, project_name, owner_user_id, is_owner}]`, only docs the caller can read.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_docs_list.py
import httpx, pytest
from httpx import ASGITransport
from fastapi import FastAPI
from server.auth import deps
from server.routers import docs as docs_router
pytestmark = pytest.mark.asyncio


async def test_list_returns_own_and_granted_not_stranger(db_conn):
    # Seed via SQL: owner o with d1 (tags {x}); stranger s with d2; grant d2? no.
    # Grant a THIRD doc d3 (owned by o) to caller c. Caller c lists → sees d3 only.
    # (Adapt to how this router accesses the DB — it uses get_pool(); for the
    # list route add a get_conn-based handler OR test via the pool like
    # test_docs_authz.py. Assert stranger's docs never appear.)
    ...
```

> The list endpoint should take a `conn` the same way the router's other DB work does. Prefer adding it with `conn=Depends(deps.get_conn)` so it is unit-testable with the `dependency_overrides` pattern; if the router convention is `get_pool()`, follow that and test against the pool as `test_docs_authz.py` does. Decide by reading the file; keep it consistent with neighbors.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_docs_list.py -v`
Expected: FAIL — `GET /v1/docs` returns 404 (route doesn't exist).

- [ ] **Step 3: Implement**

```python
# server/routers/docs.py
from ..auth.authz import readable_docs_where

@router.get("")
async def list_documents(
    q: str | None = None,
    project_id: str | None = None,
    tag: str | None = None,
    principal: Principal = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _ensure_ready()
    uid = principal.user_id
    where = [readable_docs_where("d")]
    params: list[Any] = [uid, uid, uid]
    if q:
        where.append("(d.file_name ILIKE %s OR %s = ANY(d.tags))")
        params += [f"%{q}%", q]
    if project_id:
        where.append("d.project_id = %s")
        params.append(project_id)
    if tag:
        where.append("%s = ANY(d.tags)")
        params.append(tag)
    sql = (
        "SELECT d.doc_id, d.file_name, d.state, d.tags, d.project_id, "
        "p.name AS project_name, d.user_id "
        "FROM documents d LEFT JOIN projects p ON p.id = d.project_id "
        f"WHERE {' AND '.join(where)} ORDER BY d.updated_at DESC"
    )
    pool = get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [
        {"doc_id": r[0], "file_name": r[1], "state": r[2], "tags": r[3],
         "project_id": str(r[4]) if r[4] else None, "project_name": r[5],
         "owner_user_id": str(r[6]), "is_owner": str(r[6]) == uid}
        for r in rows
    ]
```

> Note: `@router.get("")` must be registered **before** `@router.get("/{doc_id}")` route-matching only matters within the same path; FastAPI matches `""` vs `"/{doc_id}"` unambiguously, so ordering is safe here.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_docs_list.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routers/docs.py server/tests/test_docs_list.py
git commit -m "feat(library): GET /v1/docs list endpoint (own/member/granted, q/project/tag filters)"
```

---

### Task 5: `PATCH /v1/docs/{doc_id}` — tags, project, reassign owner

**Files:**
- Modify: `server/routers/docs.py`
- Test: `server/tests/test_docs_patch.py`

**Interfaces:**
- Produces: `PATCH /v1/docs/{doc_id}` body `{tags?: [str], project_id?: str|null, owner_user_id?: str}`. Owner may set tags/project. `owner_user_id` reassignment is **admin-only** (the seed-admin-backfill escape hatch); non-admins get 403 if they try to reassign, 404 if not owner.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_docs_patch.py — owner sets tags+project; non-owner 404;
# non-admin reassign 403; admin reassign 200. Adapt harness to the router.
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_docs_patch.py -v`
Expected: FAIL — no PATCH route.

- [ ] **Step 3: Implement**

```python
class DocPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] | None = None
    project_id: str | None = None
    owner_user_id: str | None = None

@router.patch("/{doc_id}")
async def patch_document(
    doc_id: DocId, body: DocPatchIn,
    principal: Principal = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_ready()
    data = body.model_dump(exclude_unset=True)
    pool = get_pool()
    async with pool.connection() as conn:
        # reassignment is admin-only; everything else is owner-only
        if "owner_user_id" in data:
            if principal.role != "admin":
                raise HTTPException(status_code=403, detail="Admin only for reassignment")
        else:
            await assert_owns_doc(conn, doc_id, principal.user_id)
        sets, params = [], []
        if "tags" in data:
            sets.append("tags = %s"); params.append(sorted(set(data["tags"])))
        if "project_id" in data:
            sets.append("project_id = %s"); params.append(data["project_id"])
        if "owner_user_id" in data:
            sets.append("user_id = %s"); params.append(data["owner_user_id"])
        if sets:
            sets.append("updated_at = now()")
            params.append(doc_id)
            r = await conn.execute(
                f"UPDATE documents SET {', '.join(sets)} WHERE doc_id = %s", params)
            if r.rowcount == 0:
                raise HTTPException(status_code=404, detail="Document not found")
        status = await _fetch_doc_status(conn, doc_id)
    if not status:
        raise HTTPException(status_code=404, detail="Document not found")
    return status
```

> `_fetch_doc_status` currently may not select `tags`/`project_id`; extend its SELECT (line ~128) to include them so the response reflects the change. Add `tags` and `project_id` to the returned dict.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_docs_patch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routers/docs.py server/tests/test_docs_patch.py
git commit -m "feat(library): PATCH /v1/docs/{id} — tags, project, admin reassignment"
```

---

### Task 6: Projects router — CRUD (owner-only)

**Files:**
- Create: `server/routers/projects.py`
- Modify: `server/app.py` (include the router)
- Test: `server/tests/test_projects_router.py`

**Interfaces:**
- Produces: `POST /v1/projects` `{name, description?}` → row; `GET /v1/projects` (owned + member-of); `PATCH /v1/projects/{id}` (owner); `DELETE /v1/projects/{id}` (owner). Prefix `/v1/projects`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_projects_router.py — mirror test_admin_router.py's _app/_client
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import projects as projects_router


def _app(db_conn, principal):
    app = FastAPI()
    async def _conn(): yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    return app


async def test_create_and_list_project(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    p = deps.Principal(user_id=u["id"], email=u["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.post("/v1/projects", json={"name": "Alpha"})
        assert r.status_code == 201 and r.json()["name"] == "Alpha"
        rows = (await c.get("/v1/projects")).json()
        assert len(rows) == 1 and rows[0]["is_owner"] is True


async def test_non_owner_cannot_patch(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.patch(f"/v1/projects/{pid}", json={"name": "Hijack"})
        assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `server/routers/projects.py`**

```python
"""/v1/projects — grouping + primary sharing unit. Owner-only writes; listing
returns owned + member-of. Not-owned writes 404 (no existence leak)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import deps

router = APIRouter(prefix="/v1/projects", tags=["projects"])


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


def _row(r) -> dict:
    return {"id": str(r[0]), "owner_user_id": str(r[1]), "name": r[2],
            "description": r[3]}


async def _assert_owner(conn, project_id: str, user_id: str) -> None:
    cur = await conn.execute(
        "SELECT owner_user_id FROM projects WHERE id = %s", (project_id,))
    row = await cur.fetchone()
    if row is None or str(row[0]) != user_id:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("", status_code=201)
async def create_project(body: ProjectIn,
                         principal: deps.Principal = Depends(deps.get_current_user),
                         conn=Depends(deps.get_conn)):
    cur = await conn.execute(
        "INSERT INTO projects (owner_user_id, name, description) "
        "VALUES (%s,%s,%s) RETURNING id, owner_user_id, name, description",
        (principal.user_id, body.name, body.description))
    out = _row(await cur.fetchone())
    out["is_owner"] = True
    return out


@router.get("")
async def list_projects(principal: deps.Principal = Depends(deps.get_current_user),
                        conn=Depends(deps.get_conn)):
    cur = await conn.execute(
        "SELECT id, owner_user_id, name, description FROM projects "
        "WHERE owner_user_id = %s OR id IN "
        "(SELECT project_id FROM project_members WHERE user_id = %s) "
        "ORDER BY created_at DESC",
        (principal.user_id, principal.user_id))
    rows = await cur.fetchall()
    return [{**_row(r), "is_owner": str(r[1]) == principal.user_id} for r in rows]


@router.patch("/{project_id}")
async def patch_project(project_id: str, body: ProjectPatch,
                        principal: deps.Principal = Depends(deps.get_current_user),
                        conn=Depends(deps.get_conn)):
    await _assert_owner(conn, project_id, principal.user_id)
    data = body.model_dump(exclude_unset=True)
    if data:
        sets = ", ".join(f"{k} = %s" for k in data)
        await conn.execute(f"UPDATE projects SET {sets} WHERE id = %s",
                           [*data.values(), project_id])
    cur = await conn.execute(
        "SELECT id, owner_user_id, name, description FROM projects WHERE id = %s",
        (project_id,))
    return {**_row(await cur.fetchone()), "is_owner": True}


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str,
                         principal: deps.Principal = Depends(deps.get_current_user),
                         conn=Depends(deps.get_conn)):
    await _assert_owner(conn, project_id, principal.user_id)
    await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    return Response(status_code=204)
```

- [ ] **Step 4: Mount the router**

In `server/app.py`, add `from .routers.projects import router as projects_router` and `app.include_router(projects_router)` beside the other `include_router` calls.

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routers/projects.py server/app.py server/tests/test_projects_router.py
git commit -m "feat(library): projects router — owner-only CRUD, member-of listing"
```

---

### Task 7: Project members + doc grants (owner-only)

**Files:**
- Modify: `server/routers/projects.py` (members), `server/routers/docs.py` (grants)
- Test: `server/tests/test_projects_router.py`, `server/tests/test_doc_grants.py`

**Interfaces:**
- Produces: `PUT/DELETE /v1/projects/{id}/members/{user_id}` (owner-only); `PUT/DELETE /v1/docs/{doc_id}/grants/{user_id}` (doc-owner-only). All idempotent; 204 on success.

- [ ] **Step 1: Write the failing tests**

```python
# append to server/tests/test_projects_router.py
async def test_owner_adds_and_removes_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    member = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="m@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=owner["id"], email=owner["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        assert (await c.put(f"/v1/projects/{pid}/members/{member['id']}")).status_code == 204
        assert (await c.delete(f"/v1/projects/{pid}/members/{member['id']}")).status_code == 204
```

```python
# server/tests/test_doc_grants.py — owner grants read to another user; a
# non-owner PUT grant → 404. Use the docs router harness.
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py server/tests/test_doc_grants.py -v`
Expected: FAIL — endpoints missing.

- [ ] **Step 3: Implement members (projects.py)**

```python
@router.put("/{project_id}/members/{user_id}", status_code=204)
async def add_member(project_id: str, user_id: str,
                     principal: deps.Principal = Depends(deps.get_current_user),
                     conn=Depends(deps.get_conn)):
    await _assert_owner(conn, project_id, principal.user_id)
    await conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING", (project_id, user_id))
    return Response(status_code=204)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_member(project_id: str, user_id: str,
                        principal: deps.Principal = Depends(deps.get_current_user),
                        conn=Depends(deps.get_conn)):
    await _assert_owner(conn, project_id, principal.user_id)
    await conn.execute(
        "DELETE FROM project_members WHERE project_id=%s AND user_id=%s",
        (project_id, user_id))
    return Response(status_code=204)
```

- [ ] **Step 4: Implement grants (docs.py)**

```python
@router.put("/{doc_id}/grants/{user_id}", status_code=204)
async def add_grant(doc_id: DocId, user_id: str,
                    principal: Principal = Depends(_require_doc_owner)):
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING", (doc_id, user_id))
    return Response(status_code=204)


@router.delete("/{doc_id}/grants/{user_id}", status_code=204)
async def remove_grant(doc_id: DocId, user_id: str,
                       principal: Principal = Depends(_require_doc_owner)):
    _ensure_ready()
    pool = get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM doc_grants WHERE doc_id=%s AND grantee_user_id=%s",
            (doc_id, user_id))
    return Response(status_code=204)
```

(Add `from fastapi import Response` to docs.py if not already imported.)

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_projects_router.py server/tests/test_doc_grants.py -v`
Expected: PASS. Then run the whole backend suite: `.venv/bin/python -m pytest server/tests/ -q`.

- [ ] **Step 6: Commit**

```bash
git add server/routers/projects.py server/routers/docs.py server/tests/test_projects_router.py server/tests/test_doc_grants.py
git commit -m "feat(library): project members + per-doc read grants (owner-only, idempotent)"
```

---

### Task 8: Library page (SPA) — list, search, filter, tags/project

**Files:**
- Create: `src/components/library/LibraryPage.jsx`, `src/components/library/LibraryPage.test.jsx`
- Modify: `src/App.jsx` (add a `library` view + switcher entry), `src/utils/apiFetch.js` (none — reuse)
- Test: `LibraryPage.test.jsx`

**Interfaces:**
- Consumes: `GET /v1/docs`, `GET /v1/projects`, `PATCH /v1/docs/{id}`, grant/member endpoints.

- [ ] **Step 1: Write the failing test (behavior contract)**

```jsx
// src/components/library/LibraryPage.test.jsx
// Render LibraryPage with apiFetch mocked to return two docs (one owned with
// tags ['x'], one shared is_owner:false); assert both render; type in the
// search box → assert a GET /v1/docs?q=... is issued; assert the shared doc
// shows a "shared" indicator and no delete affordance.
```

> Read the existing reader/chat view components for the visual language (tiny text, list rows) and the App view-switcher to add a `library` entry. The behavior contract: a table/list of `{file_name, state, tags, project_name, is_owner}`; search-as-you-type calls `GET /v1/docs?q=`; a project filter (from `GET /v1/projects`); owner-only rows expose tag-edit + reassign; shared rows are read-only. **No "Ask about these" here — that is Phase 1.**

- [ ] **Step 2: Run to verify it fails** — `npm run test -- LibraryPage` → FAIL (component missing).

- [ ] **Step 3: Implement** `LibraryPage.jsx` per the contract, using `apiFetch(apiHost, apiPort, ...)`. Add a `library` value to `App.jsx`'s `viewMode` and a switcher entry (icon), persisted like the others.

- [ ] **Step 4: Run to verify it passes** — `npm run test -- LibraryPage`.

- [ ] **Step 5: Commit**

```bash
git add src/components/library/ src/App.jsx
git commit -m "feat(library-ui): Library page — list, search, project/tag filter, share indicators"
```

---

### Task 9: Upload gains project + tags

**Files:**
- Modify: `src/App.jsx` (upload pipeline), `src/lib/uploadPdf.js` (carry fields if needed)
- Test: extend the relevant upload test

**Interfaces:**
- Consumes: `PATCH /v1/docs/{id}` (set project/tags after register), or extend register — Phase 0 uses PATCH after upload to avoid touching the register contract.

- [ ] **Step 1: Write the failing test** — after an upload with a chosen project + tags, a `PATCH /v1/docs/{id}` with those `project_id`/`tags` is issued.

- [ ] **Step 2: Run to verify it fails** — `npm run test -- upload`.

- [ ] **Step 3: Implement** — the upload flow gains an optional project select (from `GET /v1/projects`) + a tags input; after the doc registers, issue `PATCH /v1/docs/{id}` with `{project_id, tags}`. Keep it optional (skip PATCH when neither set).

- [ ] **Step 4: Run to verify it passes** — `npm run test -- upload`.

- [ ] **Step 5: Commit**

```bash
git add src/App.jsx src/lib/uploadPdf.js
git commit -m "feat(library-ui): optional project + tags on upload"
```

---

### Task 10: Docs + backfill note

**Files:**
- Modify: `docs/CHAT_WITH_PDF.md` (or a new `docs/LIBRARY.md`), `HANDOVER.md`
- Test: none

- [ ] **Step 1:** Document the library: access model (`can_read`), projects/grants/tags, the list endpoint, the admin reassignment path for seed-admin-backfilled docs. Note migration **009** and the capability-coordination (read routes gain `require_capability("reader")` at integration).
- [ ] **Step 2:** Run the full backend + frontend suites once more (`.venv/bin/python -m pytest server/tests/ -q` && `npm run test`). Record the green counts in `HANDOVER.md`.
- [ ] **Step 3: Commit**

```bash
git add docs/ HANDOVER.md
git commit -m "docs(library): Phase 0 — access model, projects/grants/tags, migration 009"
```

---

## Self-Review notes (author)

- **Spec coverage (Phase 0 rows of §10):** schema (T1) · access model (T2) · read widening (T3) · list API (T4) · tags/project/reassign (T5) · projects CRUD (T6) · members + grants (T7) · Library page (T8) · upload project/tags (T9) · docs/backfill (T10). Phase 1 (`docScope` cross-doc search), Phase 2 (descriptions + `find_documents`), Phase 3 (multi-round loop) are explicitly **out of scope** and get their own plans.
- **Coordination with the Keycloak-identity track:** migration is **009** (not 008); read routes/Library gain `require_capability("reader")` only at integration — flagged in T3/T8, not implemented here to avoid a cross-branch dependency.
- **Honest caveats for the executor:** the docs router accesses the DB via `get_pool()` inside its guards, so library read-route tests should follow `test_docs_authz.py`'s pool-based harness rather than the `get_conn` override; the list endpoint (T4) can adopt either convention — pick the one matching neighbors. Frontend tasks (T8/T9) carry behavior contracts + test skeletons; exact selectors are written after reading `App.jsx`/the reader components.
