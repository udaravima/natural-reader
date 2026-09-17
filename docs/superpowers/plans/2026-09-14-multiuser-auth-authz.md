# Multi-user Auth & Authz (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the FastAPI backend multi-user — authenticate via OIDC (Keycloak), give every document and chat session an owner, and deny-by-default on every data route.

**Architecture:** The app is an OIDC Relying Party (Authlib). After the OIDC exchange it issues its *own* DB-backed session (httpOnly cookie) for the SPA and hashed Personal Access Tokens (Bearer) for the extension/scripts. A single `get_current_user` dependency resolves either credential to a `Principal`; ownership is enforced per-row with 404-on-mismatch. Identity mirrors into a local `users` table with JIT provisioning and first-user-admin.

**Tech Stack:** FastAPI 0.135, Starlette 0.52 (SessionMiddleware), Authlib (OIDC), psycopg 3.3 async + AsyncConnectionPool, Postgres 16, pytest + asyncio (auto mode).

**Spec:** `docs/superpowers/specs/2026-09-13-multiuser-auth-authz-design.md` — read it alongside this plan.

## Global Constraints

- **Scope is the backend only.** The SPA login UI / `credentials:'include'` / token panel are a separate follow-up plan. `AUTH_ENABLED=false` keeps the SPA usable during backend dev.
- **Migrations** are `server/sql/NNN_*.sql`, applied once each in a transaction by `db._run_migrations()`, and MUST end with `INSERT INTO schema_migrations(version) VALUES (N) ON CONFLICT DO NOTHING;`. No env reads inside SQL.
- **Bearer secrets** (session tokens, PATs) are stored **sha256-hashed**, never raw. Generate with `secrets.token_urlsafe(32)`.
- **Owner mismatch or missing row → HTTP 404**, never 403 (don't leak existence). Missing/invalid credential → 401. Authenticated-but-not-active → 403.
- **Data-access helpers take an explicit `conn`** (injected), never call `get_pool()` internally — this is what makes them unit-testable inside a rolled-back transaction.
- **Seed admin UUID** is the literal `00000000-0000-0000-0000-000000000001` everywhere.
- Tests run against a real Postgres: `docker-compose up -d postgres` first. Test DB is `natural_reader_test` (separate from dev data).
- Run tests with `.venv/bin/python -m pytest`. Keep the sub-project A security tests green.

---

### Task 0: Dependencies + DB-backed test harness

**Files:**
- Modify: `requirements.txt`
- Create: `server/tests/conftest.py`
- Test: `server/tests/test_conftest_smoke.py`

**Interfaces:**
- Produces: pytest fixtures `db_pool` (session-scoped `AsyncConnectionPool` against the test DB, migrations applied), `db_conn` (function-scoped async connection inside a transaction that is **rolled back** at teardown), and `migrated_url` (the test DATABASE_URL). Auth data-layer tests consume `db_conn`.

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:
```
authlib>=1.3
itsdangerous>=2.1
```
Then install:
```bash
.venv/bin/pip install "authlib>=1.3" "itsdangerous>=2.1"
```

- [ ] **Step 2: Write the conftest**

Create `server/tests/conftest.py`:
```python
"""Shared pytest fixtures. DB fixtures need a running Postgres:
`docker-compose up -d postgres`. They use a separate `natural_reader_test`
database and roll back every test, so dev data is never touched."""
import os
from pathlib import Path

import pytest
import pytest_asyncio
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

_ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql://natural_reader:natural_reader@localhost:5433/postgres",
)
TEST_DB = "natural_reader_test"
_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql://natural_reader:natural_reader@localhost:5433/{TEST_DB}",
)
_SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


async def _ensure_test_db() -> None:
    # CREATE DATABASE cannot run inside a transaction — use autocommit.
    conn = await AsyncConnection.connect(_ADMIN_URL, autocommit=True)
    try:
        cur = await conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
        )
        if await cur.fetchone() is None:
            await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()


async def _apply_migrations(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        for path in sorted(_SQL_DIR.glob("*.sql")):
            async with conn.transaction():
                await conn.execute(path.read_text())


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    await _ensure_test_db()
    pool = AsyncConnectionPool(_TEST_URL, min_size=1, max_size=4, open=False)
    await pool.open(wait=True, timeout=10)
    await _apply_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def db_conn(db_pool):
    # One connection per test, wrapped in a transaction we always roll back.
    async with db_pool.connection() as conn:
        tx = conn.transaction(force_rollback=True)
        await tx.__aenter__()
        try:
            yield conn
        finally:
            await tx.__aexit__(None, None, None)


@pytest.fixture
def migrated_url():
    return _TEST_URL
```

- [ ] **Step 3: Write a smoke test**

Create `server/tests/test_conftest_smoke.py`:
```python
async def test_db_conn_round_trips(db_conn):
    cur = await db_conn.execute("SELECT 1")
    assert (await cur.fetchone())[0] == 1
```

- [ ] **Step 4: Run it**

```bash
docker-compose up -d postgres
.venv/bin/python -m pytest server/tests/test_conftest_smoke.py -v
```
Expected: PASS (fails clearly if Postgres isn't up — that's the prerequisite).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt server/tests/conftest.py server/tests/test_conftest_smoke.py
git commit -m "test(auth): add DB-backed pytest harness + authlib deps"
```

---

### Task 1: Migration 005 — users table + ownership + backfill

**Files:**
- Create: `server/sql/005_users_ownership.sql`
- Test: `server/tests/test_migration_005.py`

**Interfaces:**
- Produces: table `users(id UUID, oidc_iss, oidc_sub, email UNIQUE, display_name, role, status, timestamps)`; seed admin row id `00000000-0000-0000-0000-000000000001`; NOT NULL `documents.user_id` / `chat_sessions.user_id` FKs.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_migration_005.py`:
```python
SEED = "00000000-0000-0000-0000-000000000001"


async def test_seed_admin_exists(db_conn):
    cur = await db_conn.execute(
        "SELECT role, status FROM users WHERE id = %s", (SEED,)
    )
    row = await cur.fetchone()
    assert row == ("admin", "active")


async def test_ownership_columns_not_null(db_conn):
    for table in ("documents", "chat_sessions"):
        cur = await db_conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'user_id'",
            (table,),
        )
        assert (await cur.fetchone())[0] == "NO"


async def test_existing_rows_backfilled_to_seed(db_conn):
    # A doc inserted without user_id would violate NOT NULL, proving the column
    # is enforced; the backfill covers pre-migration rows. Insert via seed owner.
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES ('a', 'f', 'text', 1, %s)",
        (SEED,),
    )
    cur = await db_conn.execute("SELECT user_id FROM documents WHERE doc_id='a'")
    assert str((await cur.fetchone())[0]) == SEED
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_migration_005.py -v
```
Expected: FAIL — `users` table / `user_id` column don't exist yet. (The session-scoped `db_pool` applies whatever SQL files exist; 005 is missing.)

- [ ] **Step 3: Write the migration**

Create `server/sql/005_users_ownership.sql` — copy the DDL from spec §3 (Migration 005) verbatim:
```sql
CREATE TABLE IF NOT EXISTS users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    oidc_iss     TEXT,
    oidc_sub     TEXT,
    email        TEXT UNIQUE NOT NULL,
    display_name TEXT,
    role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('active','pending','disabled')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (oidc_iss, oidc_sub)
);

INSERT INTO users (id, email, role, status)
VALUES ('00000000-0000-0000-0000-000000000001', 'admin@localhost', 'admin', 'active')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE documents     ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;

UPDATE documents     SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;
UPDATE chat_sessions SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;

ALTER TABLE documents     ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE chat_sessions ALTER COLUMN user_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS documents_user_idx     ON documents(user_id);
CREATE INDEX IF NOT EXISTS chat_sessions_user_idx ON chat_sessions(user_id);

INSERT INTO schema_migrations(version) VALUES (5) ON CONFLICT DO NOTHING;
```

- [ ] **Step 4: Run to verify it passes**

The `db_pool` fixture is session-scoped and already applied migrations before 005 existed. Drop the test DB so the fixture re-applies from scratch:
```bash
psql "postgresql://natural_reader:natural_reader@localhost:5433/postgres" -c 'DROP DATABASE IF EXISTS natural_reader_test;'
.venv/bin/python -m pytest server/tests/test_migration_005.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/sql/005_users_ownership.sql server/tests/test_migration_005.py
git commit -m "feat(auth): migration 005 — users table + per-user ownership"
```

---

### Task 2: Migration 006 — sessions + personal_access_tokens

**Files:**
- Create: `server/sql/006_auth_credentials.sql`
- Test: `server/tests/test_migration_006.py`

**Interfaces:**
- Produces: tables `sessions(id TEXT pk = sha256, user_id, created_at, last_seen_at, expires_at, user_agent)` and `personal_access_tokens(id UUID, user_id, name, token_hash UNIQUE, created_at, last_used_at, expires_at)`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_migration_006.py`:
```python
async def _cols(db_conn, table):
    cur = await db_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,),
    )
    return {r[0] for r in await cur.fetchall()}


async def test_sessions_table_shape(db_conn):
    cols = await _cols(db_conn, "sessions")
    assert {"id", "user_id", "expires_at", "last_seen_at"} <= cols


async def test_pat_table_shape(db_conn):
    cols = await _cols(db_conn, "personal_access_tokens")
    assert {"id", "user_id", "name", "token_hash", "expires_at"} <= cols
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_migration_006.py -v
```
Expected: FAIL — tables don't exist.

- [ ] **Step 3: Write the migration**

Create `server/sql/006_auth_credentials.sql` — copy from spec §3 (Migration 006) verbatim (sessions + personal_access_tokens + indexes + `INSERT INTO schema_migrations(version) VALUES (6)`).

- [ ] **Step 4: Run to verify it passes**

```bash
psql "postgresql://natural_reader:natural_reader@localhost:5433/postgres" -c 'DROP DATABASE IF EXISTS natural_reader_test;'
.venv/bin/python -m pytest server/tests/test_migration_006.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/sql/006_auth_credentials.sql server/tests/test_migration_006.py
git commit -m "feat(auth): migration 006 — sessions + personal access tokens"
```

---

### Task 3: Auth config (model-free)

**Files:**
- Create: `server/auth/__init__.py` (empty)
- Create: `server/auth/config.py`
- Test: `server/tests/test_auth_config.py`

**Interfaces:**
- Produces: `AuthConfig` dataclass with `enabled: bool`, `oidc_issuer/client_id/client_secret/redirect_url: str|None`, `session_secret: str|None`, `session_ttl_hours: int`, `cookie_secure: bool`, `cookie_domain: str|None`, `bootstrap_admin_email: str|None`; `load_auth_config(env: Mapping) -> AuthConfig`; `dev_bypass_allowed(cfg, host: str) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_auth_config.py`:
```python
from server.auth.config import load_auth_config, dev_bypass_allowed


def test_defaults_enabled():
    cfg = load_auth_config({})
    assert cfg.enabled is True
    assert cfg.session_ttl_hours == 168
    assert cfg.cookie_secure is True


def test_disabled_flag_parsed():
    cfg = load_auth_config({"AUTH_ENABLED": "false"})
    assert cfg.enabled is False


def test_dev_bypass_only_on_localhost():
    cfg = load_auth_config({"AUTH_ENABLED": "false"})
    assert dev_bypass_allowed(cfg, "127.0.0.1") is True
    assert dev_bypass_allowed(cfg, "0.0.0.0") is False


def test_dev_bypass_false_when_auth_enabled():
    cfg = load_auth_config({})
    assert dev_bypass_allowed(cfg, "127.0.0.1") is False
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_config.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/auth/__init__.py` (empty). Create `server/auth/config.py`:
```python
"""Auth configuration parsing. Model-free (no import of server.model)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    oidc_issuer: str | None
    oidc_client_id: str | None
    oidc_client_secret: str | None
    oidc_redirect_url: str | None
    session_secret: str | None
    session_ttl_hours: int
    cookie_secure: bool
    cookie_domain: str | None
    bootstrap_admin_email: str | None


def load_auth_config(env: Mapping[str, str]) -> AuthConfig:
    def flag(key: str, default: bool) -> bool:
        v = env.get(key)
        return default if v is None else v.strip().lower() in _TRUE

    return AuthConfig(
        enabled=flag("AUTH_ENABLED", True),
        oidc_issuer=env.get("OIDC_ISSUER"),
        oidc_client_id=env.get("OIDC_CLIENT_ID"),
        oidc_client_secret=env.get("OIDC_CLIENT_SECRET"),
        oidc_redirect_url=env.get("OIDC_REDIRECT_URL"),
        session_secret=env.get("SESSION_SECRET"),
        session_ttl_hours=int(env.get("SESSION_TTL_HOURS", "168")),
        cookie_secure=flag("COOKIE_SECURE", True),
        cookie_domain=env.get("COOKIE_DOMAIN"),
        bootstrap_admin_email=env.get("BOOTSTRAP_ADMIN_EMAIL"),
    )


def dev_bypass_allowed(cfg: AuthConfig, host: str) -> bool:
    """The AUTH_ENABLED=false bypass is valid ONLY on a localhost bind."""
    return (not cfg.enabled) and host in ("127.0.0.1", "localhost", "::1")
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_config.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/__init__.py server/auth/config.py server/tests/test_auth_config.py
git commit -m "feat(auth): auth config parsing + localhost-gated dev bypass"
```

---

### Task 4: User data-access + JIT provisioning

**Files:**
- Create: `server/auth/users.py`
- Test: `server/tests/test_auth_users.py`

**Interfaces:**
- Consumes: `db_conn` fixture.
- Produces:
  - `async resolve_or_provision_user(conn, *, iss, sub, email, display_name=None) -> dict` — returns the user row (dict) implementing spec §4 branches.
  - `async get_user(conn, user_id) -> dict | None`
  - `async set_status(conn, user_id, status) -> None`; `async set_role(conn, user_id, role) -> None`
  - `async list_users(conn) -> list[dict]`
  - Row dict keys: `id (str), email, display_name, role, status, oidc_iss, oidc_sub`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_auth_users.py`:
```python
import pytest
from server.auth.users import resolve_or_provision_user, get_user, set_status

SEED = "00000000-0000-0000-0000-000000000001"


async def test_first_login_links_seed_admin(db_conn):
    # Fresh DB: only the unlinked seed admin exists -> first login becomes admin.
    u = await resolve_or_provision_user(
        db_conn, iss="https://kc/realms/nr", sub="abc", email="me@x.io"
    )
    assert u["id"] == SEED
    assert u["role"] == "admin" and u["status"] == "active"
    assert u["oidc_sub"] == "abc"


async def test_email_links_preseeded_row(db_conn):
    # Operator pre-set the seed admin email to the real admin's email.
    await db_conn.execute("UPDATE users SET email='real@x.io' WHERE id=%s", (SEED,))
    u = await resolve_or_provision_user(
        db_conn, iss="i", sub="s1", email="real@x.io"
    )
    assert u["id"] == SEED and u["oidc_sub"] == "s1"


async def test_second_identity_is_pending_member(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    assert u2["role"] == "member" and u2["status"] == "pending"


async def test_returning_user_matched_by_sub(db_conn):
    a = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    b = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    assert a["id"] == b["id"]


async def test_set_status(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await set_status(db_conn, u["id"], "active")
    assert (await get_user(db_conn, u["id"]))["status"] == "active"
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_users.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/auth/users.py`:
```python
"""User records + JIT provisioning (spec §4). All functions take an explicit
connection so they compose inside a request transaction and are unit-testable."""
from __future__ import annotations

from typing import Any

SEED_ADMIN_ID = "00000000-0000-0000-0000-000000000001"

_COLS = "id, email, display_name, role, status, oidc_iss, oidc_sub"


def _row(record) -> dict[str, Any] | None:
    if record is None:
        return None
    keys = ["id", "email", "display_name", "role", "status", "oidc_iss", "oidc_sub"]
    d = dict(zip(keys, record))
    d["id"] = str(d["id"])
    return d


async def get_user(conn, user_id: str) -> dict[str, Any] | None:
    cur = await conn.execute(f"SELECT {_COLS} FROM users WHERE id = %s", (user_id,))
    return _row(await cur.fetchone())


async def list_users(conn) -> list[dict[str, Any]]:
    cur = await conn.execute(f"SELECT {_COLS} FROM users ORDER BY created_at")
    return [_row(r) for r in await cur.fetchall()]


async def set_status(conn, user_id: str, status: str) -> None:
    await conn.execute(
        "UPDATE users SET status=%s, updated_at=now() WHERE id=%s", (status, user_id)
    )


async def set_role(conn, user_id: str, role: str) -> None:
    await conn.execute(
        "UPDATE users SET role=%s, updated_at=now() WHERE id=%s", (role, user_id)
    )


async def _fetch_by(conn, where: str, params) -> dict[str, Any] | None:
    cur = await conn.execute(f"SELECT {_COLS} FROM users WHERE {where}", params)
    return _row(await cur.fetchone())


async def _only_unlinked_seed(conn) -> bool:
    cur = await conn.execute("SELECT count(*), count(oidc_sub) FROM users")
    total, linked = await cur.fetchone()
    return total == 1 and linked == 0


async def resolve_or_provision_user(
    conn, *, iss: str, sub: str, email: str, display_name: str | None = None
) -> dict[str, Any]:
    # 1) Known identity.
    found = await _fetch_by(conn, "oidc_iss=%s AND oidc_sub=%s", (iss, sub))
    if found:
        await conn.execute(
            "UPDATE users SET email=%s, display_name=COALESCE(%s, display_name), "
            "updated_at=now() WHERE id=%s",
            (email, display_name, found["id"]),
        )
        return await get_user(conn, found["id"])

    # 2) Pre-provisioned/seed row by email, not yet linked.
    by_email = await _fetch_by(conn, "email=%s", (email,))
    if by_email and by_email["oidc_sub"] is None:
        await conn.execute(
            "UPDATE users SET oidc_iss=%s, oidc_sub=%s, "
            "display_name=COALESCE(%s, display_name), updated_at=now() WHERE id=%s",
            (iss, sub, display_name, by_email["id"]),
        )
        return await get_user(conn, by_email["id"])
    if by_email:
        # Email already bound to a different identity.
        raise ValueError("email already linked to another identity")

    # 3) Brand-new identity. First real login (only the unlinked seed exists) -> admin.
    if await _only_unlinked_seed(conn):
        await conn.execute(
            "UPDATE users SET oidc_iss=%s, oidc_sub=%s, email=%s, "
            "display_name=COALESCE(%s, display_name), updated_at=now() WHERE id=%s",
            (iss, sub, email, display_name, SEED_ADMIN_ID),
        )
        return await get_user(conn, SEED_ADMIN_ID)

    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, display_name, role, status) "
        "VALUES (%s, %s, %s, %s, 'member', 'pending') RETURNING id",
        (iss, sub, email, display_name),
    )
    new_id = (await cur.fetchone())[0]
    return await get_user(conn, str(new_id))
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_users.py -v
```
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add server/auth/users.py server/tests/test_auth_users.py
git commit -m "feat(auth): user records + JIT provisioning with first-user-admin"
```

---

### Task 5: Session layer

**Files:**
- Create: `server/auth/sessions.py`
- Test: `server/tests/test_auth_sessions.py`

**Interfaces:**
- Produces:
  - `async create_session(conn, user_id, *, ttl_hours=168, user_agent=None) -> str` — returns the **raw** cookie token (stores sha256).
  - `async resolve_session(conn, raw_token) -> dict | None` — returns the user row (via `users.get_user`) if the session is live (not expired); bumps `last_seen_at`; else None.
  - `async revoke_session(conn, raw_token) -> None`
- Consumes: `server.auth.users.get_user`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_auth_sessions.py`:
```python
from server.auth.sessions import create_session, resolve_session, revoke_session
from server.auth.users import resolve_or_provision_user


async def _user(db_conn):
    return await resolve_or_provision_user(db_conn, iss="i", sub="s", email="a@x.io")


async def test_create_then_resolve(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"])
    assert tok and len(tok) > 20
    resolved = await resolve_session(db_conn, tok)
    assert resolved["id"] == u["id"]


async def test_raw_token_not_stored(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"])
    cur = await db_conn.execute("SELECT id FROM sessions")
    stored = (await cur.fetchone())[0]
    assert stored != tok  # stored value is a hash


async def test_expired_session_resolves_none(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"], ttl_hours=-1)
    assert await resolve_session(db_conn, tok) is None


async def test_revoke(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"])
    await revoke_session(db_conn, tok)
    assert await resolve_session(db_conn, tok) is None


async def test_unknown_token_resolves_none(db_conn):
    assert await resolve_session(db_conn, "nope") is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_sessions.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/auth/sessions.py`:
```python
"""DB-backed sessions. The cookie holds a random token; only its sha256 is
stored, so a DB leak doesn't yield usable session cookies."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from . import users


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_session(
    conn, user_id: str, *, ttl_hours: int = 168, user_agent: str | None = None
) -> str:
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    await conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at, user_agent) "
        "VALUES (%s, %s, %s, %s)",
        (_hash(raw), user_id, expires, user_agent),
    )
    return raw


async def resolve_session(conn, raw_token: str) -> dict | None:
    if not raw_token:
        return None
    cur = await conn.execute(
        "SELECT user_id, expires_at FROM sessions WHERE id = %s", (_hash(raw_token),)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    user_id, expires_at = row
    if expires_at <= datetime.now(timezone.utc):
        return None
    await conn.execute(
        "UPDATE sessions SET last_seen_at = now() WHERE id = %s", (_hash(raw_token),)
    )
    return await users.get_user(conn, str(user_id))


async def revoke_session(conn, raw_token: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE id = %s", (_hash(raw_token),))
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_sessions.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/sessions.py server/tests/test_auth_sessions.py
git commit -m "feat(auth): DB-backed session create/resolve/revoke (hashed at rest)"
```

---

### Task 6: Personal Access Token layer

**Files:**
- Create: `server/auth/tokens.py`
- Test: `server/tests/test_auth_tokens.py`

**Interfaces:**
- Produces:
  - `async create_token(conn, user_id, name, *, expires_at=None) -> tuple[str, str]` — returns `(token_id, raw_token)`; raw is `nrp_<urlsafe>`, shown once; stores sha256.
  - `async resolve_token(conn, raw_token) -> dict | None` — user row if valid & unexpired; bumps `last_used_at`.
  - `async list_tokens(conn, user_id) -> list[dict]` (id, name, created_at, last_used_at, expires_at — never the value)
  - `async revoke_token(conn, user_id, token_id) -> bool` — only own token.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_auth_tokens.py`:
```python
from server.auth.tokens import create_token, resolve_token, list_tokens, revoke_token
from server.auth.users import resolve_or_provision_user


async def _user(db_conn, sub="s"):
    return await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")


async def test_create_returns_raw_once_and_resolves(db_conn):
    u = await _user(db_conn)
    tid, raw = await create_token(db_conn, u["id"], "cli")
    assert raw.startswith("nrp_")
    assert (await resolve_token(db_conn, raw))["id"] == u["id"]


async def test_raw_not_stored(db_conn):
    u = await _user(db_conn)
    _, raw = await create_token(db_conn, u["id"], "cli")
    cur = await db_conn.execute("SELECT token_hash FROM personal_access_tokens")
    assert (await cur.fetchone())[0] != raw


async def test_list_hides_value_and_revoke(db_conn):
    u = await _user(db_conn)
    tid, raw = await create_token(db_conn, u["id"], "cli")
    listed = await list_tokens(db_conn, u["id"])
    assert listed[0]["name"] == "cli" and "token_hash" not in listed[0]
    assert await revoke_token(db_conn, u["id"], tid) is True
    assert await resolve_token(db_conn, raw) is None


async def test_cannot_revoke_others_token(db_conn):
    a = await _user(db_conn, "a")
    b = await _user(db_conn, "b")
    tid, _ = await create_token(db_conn, a["id"], "cli")
    assert await revoke_token(db_conn, b["id"], tid) is False
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_tokens.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/auth/tokens.py`:
```python
"""Personal Access Tokens: Bearer credentials for the extension + scripts.
Raw token shown once; sha256 stored."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from . import users


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_token(conn, user_id: str, name: str, *, expires_at=None):
    raw = "nrp_" + secrets.token_urlsafe(32)
    cur = await conn.execute(
        "INSERT INTO personal_access_tokens (user_id, name, token_hash, expires_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, name, _hash(raw), expires_at),
    )
    tid = str((await cur.fetchone())[0])
    return tid, raw


async def resolve_token(conn, raw_token: str) -> dict | None:
    if not raw_token or not raw_token.startswith("nrp_"):
        return None
    cur = await conn.execute(
        "SELECT id, user_id, expires_at FROM personal_access_tokens WHERE token_hash=%s",
        (_hash(raw_token),),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    tid, user_id, expires_at = row
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return None
    await conn.execute(
        "UPDATE personal_access_tokens SET last_used_at=now() WHERE id=%s", (tid,)
    )
    return await users.get_user(conn, str(user_id))


async def list_tokens(conn, user_id: str) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, name, created_at, last_used_at, expires_at "
        "FROM personal_access_tokens WHERE user_id=%s ORDER BY created_at",
        (user_id,),
    )
    keys = ["id", "name", "created_at", "last_used_at", "expires_at"]
    out = []
    for r in await cur.fetchall():
        d = dict(zip(keys, r))
        d["id"] = str(d["id"])
        out.append(d)
    return out


async def revoke_token(conn, user_id: str, token_id: str) -> bool:
    cur = await conn.execute(
        "DELETE FROM personal_access_tokens WHERE id=%s AND user_id=%s RETURNING id",
        (token_id, user_id),
    )
    return await cur.fetchone() is not None
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_tokens.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/tokens.py server/tests/test_auth_tokens.py
git commit -m "feat(auth): personal access tokens (create/resolve/list/revoke)"
```

---

### Task 7: Principal resolution dependency + connection dependency

**Files:**
- Create: `server/auth/deps.py`
- Test: `server/tests/test_auth_deps.py`

**Interfaces:**
- Consumes: `sessions.resolve_session`, `tokens.resolve_token`, `users.get_user`, `db.get_pool`, `config.load_auth_config`.
- Produces:
  - `@dataclass Principal(user_id: str, email: str, role: str)`
  - `async get_conn()` — FastAPI dependency yielding a pooled connection (overridable in tests).
  - `async get_current_user(request, conn=Depends(get_conn)) -> Principal` — Bearer PAT first, else `nr_session` cookie; 401 if none; 403 if status != active; honors localhost dev bypass (returns seed admin).
  - `async require_admin(principal=Depends(get_current_user)) -> Principal` — 403 for non-admin.
  - `COOKIE_NAME = "nr_session"`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_auth_deps.py`:
```python
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from server.auth import deps
from server.auth.tokens import create_token
from server.auth.users import resolve_or_provision_user, set_status


def _app(db_conn):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override

    @app.get("/whoami")
    async def whoami(p: deps.Principal = Depends(deps.get_current_user)):
        return {"id": p.user_id, "role": p.role}

    @app.get("/admin-only")
    async def admin_only(p: deps.Principal = Depends(deps.require_admin)):
        return {"ok": True}

    return app


async def test_no_credential_is_401(db_conn):
    client = TestClient(_app(db_conn))
    assert client.get("/whoami").status_code == 401


async def test_pat_authenticates(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s", email="a@x.io")
    _, raw = await create_token(db_conn, u["id"], "cli")
    client = TestClient(_app(db_conn))
    r = client.get("/whoami", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200 and r.json()["role"] == "admin"


async def test_pending_user_is_403(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    _, raw = await create_token(db_conn, u2["id"], "cli")
    client = TestClient(_app(db_conn))
    r = client.get("/whoami", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403


async def test_require_admin_blocks_member(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await set_status(db_conn, u2["id"], "active")
    _, raw = await create_token(db_conn, u2["id"], "cli")
    client = TestClient(_app(db_conn))
    r = client.get("/admin-only", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_deps.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/auth/deps.py`:
```python
"""Auth dependencies: resolve a Principal from a Bearer PAT or session cookie."""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from ..db import get_pool
from . import sessions, tokens, users
from .config import dev_bypass_allowed, load_auth_config

COOKIE_NAME = "nr_session"


@dataclass
class Principal:
    user_id: str
    email: str
    role: str


async def get_conn():
    pool = get_pool()
    async with pool.connection() as conn:
        yield conn


def _principal(row: dict) -> Principal:
    return Principal(user_id=row["id"], email=row["email"], role=row["role"])


async def get_current_user(request: Request, conn=Depends(get_conn)) -> Principal:
    cfg = load_auth_config(os.environ)
    host = os.environ.get("HOST", "127.0.0.1")
    if dev_bypass_allowed(cfg, host):
        row = await users.get_user(conn, users.SEED_ADMIN_ID)
        return _principal(row)

    row = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        row = await tokens.resolve_token(conn, auth[7:].strip())
    if row is None:
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            row = await sessions.resolve_session(conn, cookie)
    if row is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if row["status"] != "active":
        raise HTTPException(status_code=403, detail=f"Account {row['status']}")
    return _principal(row)


async def require_admin(principal: Principal = Depends(get_current_user)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return principal
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_deps.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/deps.py server/tests/test_auth_deps.py
git commit -m "feat(auth): principal resolution dependency (PAT + cookie, deny-by-default)"
```

---

### Task 8: Ownership helpers

**Files:**
- Create: `server/auth/authz.py`
- Test: `server/tests/test_auth_authz.py`

**Interfaces:**
- Produces:
  - `async assert_owns_doc(conn, doc_id, user_id) -> None` — 404 if the doc doesn't exist OR isn't owned by user_id.
  - `async assert_owns_session(conn, session_id, user_id) -> None` — same for chat_sessions.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_auth_authz.py`:
```python
import pytest
from fastapi import HTTPException
from server.auth.authz import assert_owns_doc
from server.auth.users import resolve_or_provision_user

SEED = "00000000-0000-0000-0000-000000000001"


async def _doc(db_conn, doc_id, owner):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f','text',1,%s)",
        (doc_id, owner),
    )


async def test_owner_ok(db_conn):
    await _doc(db_conn, "d1", SEED)
    await assert_owns_doc(db_conn, "d1", SEED)  # no raise


async def test_missing_doc_is_404(db_conn):
    with pytest.raises(HTTPException) as e:
        await assert_owns_doc(db_conn, "nope", SEED)
    assert e.value.status_code == 404


async def test_non_owner_is_404_not_403(db_conn):
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await _doc(db_conn, "d2", SEED)
    with pytest.raises(HTTPException) as e:
        await assert_owns_doc(db_conn, "d2", other["id"])
    assert e.value.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_authz.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/auth/authz.py`:
```python
"""Row-ownership guards. Missing OR not-owned both raise 404 so a caller can't
distinguish 'exists but not yours' from 'does not exist'."""
from __future__ import annotations

from fastapi import HTTPException


async def _owner(conn, table: str, key_col: str, key: str) -> str | None:
    cur = await conn.execute(
        f"SELECT user_id FROM {table} WHERE {key_col} = %s", (key,)
    )
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def assert_owns_doc(conn, doc_id: str, user_id: str) -> None:
    if await _owner(conn, "documents", "doc_id", doc_id) != user_id:
        raise HTTPException(status_code=404, detail="Document not found")


async def assert_owns_session(conn, session_id: str, user_id: str) -> None:
    if await _owner(conn, "chat_sessions", "id", session_id) != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_authz.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/authz.py server/tests/test_auth_authz.py
git commit -m "feat(authz): row-ownership guards (404 on mismatch)"
```

---

### Task 9: OIDC client + auth router (login / callback / logout / me)

**Files:**
- Create: `server/auth/oidc.py`
- Create: `server/routers/auth.py`
- Test: `server/tests/test_auth_router.py`

**Interfaces:**
- Consumes: `deps.get_conn`, `deps.get_current_user`, `users.resolve_or_provision_user`, `sessions.create_session`, `config`.
- Produces: router with `GET /v1/auth/login`, `GET /v1/auth/callback`, `POST /v1/auth/logout`, `GET /v1/auth/me`; `build_oauth(cfg)` returning an Authlib `OAuth` with a `keycloak` client registered via `server_metadata_url`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_auth_router.py`. `/me` and `/logout` are unit-testable without a real IdP by overriding `get_current_user`; the callback is tested by monkeypatching the OAuth client's token exchange.
```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import deps
from server.routers import auth as auth_router


def _app(db_conn, principal=None):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    if principal is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(auth_router.router)
    return app


async def test_me_returns_principal(db_conn):
    p = deps.Principal(user_id="u1", email="a@x.io", role="admin")
    client = TestClient(_app(db_conn, principal=p))
    r = client.get("/v1/auth/me")
    assert r.status_code == 200 and r.json()["email"] == "a@x.io"


async def test_me_401_without_principal(db_conn):
    client = TestClient(_app(db_conn))
    assert client.get("/v1/auth/me").status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_router.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the OIDC client**

Create `server/auth/oidc.py`:
```python
"""Authlib OIDC Relying Party. Keycloak (or any OIDC provider) is configured via
its discovery document, so no per-provider code is needed."""
from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from .config import AuthConfig


def build_oauth(cfg: AuthConfig) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=cfg.oidc_client_id,
        client_secret=cfg.oidc_client_secret,
        server_metadata_url=f"{cfg.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    return oauth
```

- [ ] **Step 4: Implement the router**

Create `server/routers/auth.py`:
```python
"""/v1/auth/* — OIDC login/callback, logout, and the current-user probe."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from ..auth import deps, sessions, users
from ..auth.config import load_auth_config
from ..auth.oidc import build_oauth

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_oauth = None


def _client():
    global _oauth
    cfg = load_auth_config(os.environ)
    if not (cfg.oidc_issuer and cfg.oidc_client_id):
        raise HTTPException(status_code=503, detail="OIDC is not configured")
    if _oauth is None:
        _oauth = build_oauth(cfg)
    return _oauth.oidc


def _safe_next(raw: str | None) -> str:
    # Only same-site absolute paths — never an absolute URL (open-redirect guard).
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _set_cookie(resp: Response, token: str) -> None:
    cfg = load_auth_config(os.environ)
    resp.set_cookie(
        deps.COOKIE_NAME, token, max_age=cfg.session_ttl_hours * 3600,
        httponly=True, secure=cfg.cookie_secure, samesite="lax",
        domain=cfg.cookie_domain, path="/",
    )


@router.get("/login")
async def login(request: Request, next: str = "/"):
    request.session["post_login"] = _safe_next(next)
    cfg = load_auth_config(os.environ)
    return await _client().authorize_redirect(request, cfg.oidc_redirect_url)


@router.get("/callback")
async def callback(request: Request, conn=Depends(deps.get_conn)):
    token = await _client().authorize_access_token(request)
    claims = token.get("userinfo") or {}
    sub, iss = claims.get("sub"), claims.get("iss")
    email = claims.get("email")
    if not (sub and email):
        raise HTTPException(status_code=400, detail="OIDC token missing sub/email")
    try:
        user = await users.resolve_or_provision_user(
            conn, iss=iss, sub=sub, email=email, display_name=claims.get("name")
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    raw = await sessions.create_session(
        conn, user["id"], user_agent=request.headers.get("user-agent")
    )
    dest = request.session.pop("post_login", "/")
    resp = RedirectResponse(url=dest, status_code=303)
    _set_cookie(resp, raw)
    return resp


@router.post("/logout")
async def logout(request: Request, conn=Depends(deps.get_conn)):
    cookie = request.cookies.get(deps.COOKIE_NAME)
    if cookie:
        await sessions.revoke_session(conn, cookie)
    resp = Response(status_code=204)
    resp.delete_cookie(deps.COOKIE_NAME, path="/")
    return resp


@router.get("/me")
async def me(principal: deps.Principal = Depends(deps.get_current_user)):
    return {"id": principal.user_id, "email": principal.email, "role": principal.role}
```

- [ ] **Step 5: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_router.py -v
```
Expected: PASS. (Full login/callback is exercised in the integration/E2E pass with a real Keycloak; the mock-free unit tests cover `/me`.)

- [ ] **Step 6: Commit**

```bash
git add server/auth/oidc.py server/routers/auth.py server/tests/test_auth_router.py
git commit -m "feat(auth): OIDC RP + /v1/auth login/callback/logout/me"
```

---

### Task 10: Token-management endpoints

**Files:**
- Modify: `server/routers/auth.py`
- Test: `server/tests/test_auth_token_endpoints.py`

**Interfaces:**
- Consumes: `deps.get_current_user`, `deps.get_conn`, `tokens.*`.
- Produces: `POST /v1/auth/tokens` (body `{name, expires_at?}` → `{id, token}` once), `GET /v1/auth/tokens` (list, no values), `DELETE /v1/auth/tokens/{id}` (204/404).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_auth_token_endpoints.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import deps
from server.routers import auth as auth_router


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(auth_router.router)
    return app


async def test_create_list_revoke(db_conn):
    from server.auth.users import resolve_or_provision_user
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s", email="a@x.io")
    p = deps.Principal(user_id=u["id"], email=u["email"], role="admin")
    client = TestClient(_app(db_conn, p))

    created = client.post("/v1/auth/tokens", json={"name": "cli"})
    assert created.status_code == 200 and created.json()["token"].startswith("nrp_")
    tid = created.json()["id"]

    listed = client.get("/v1/auth/tokens").json()
    assert listed[0]["name"] == "cli" and "token" not in listed[0]

    assert client.delete(f"/v1/auth/tokens/{tid}").status_code == 204
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_auth_token_endpoints.py -v
```
Expected: FAIL — endpoints missing.

- [ ] **Step 3: Implement — append to `server/routers/auth.py`**

```python
from pydantic import BaseModel, Field
from ..auth import tokens as pat


class TokenCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_at: str | None = None  # ISO-8601; None = no expiry


@router.post("/tokens")
async def create_pat(
    body: TokenCreateIn,
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    tid, raw = await pat.create_token(
        conn, principal.user_id, body.name, expires_at=body.expires_at
    )
    return {"id": tid, "token": raw}


@router.get("/tokens")
async def list_pats(
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    return await pat.list_tokens(conn, principal.user_id)


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_pat(
    token_id: str,
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    if not await pat.revoke_token(conn, principal.user_id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return Response(status_code=204)
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_auth_token_endpoints.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routers/auth.py server/tests/test_auth_token_endpoints.py
git commit -m "feat(auth): personal access token management endpoints"
```

---

### Task 11: Admin router (user management)

**Files:**
- Create: `server/routers/admin.py`
- Test: `server/tests/test_admin_router.py`

**Interfaces:**
- Consumes: `deps.require_admin`, `deps.get_conn`, `users.list_users/set_status/set_role`.
- Produces: `GET /v1/admin/users`, `PATCH /v1/admin/users/{id}` (body `{status?, role?}`).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_admin_router.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(admin_router.router)
    return app


async def test_admin_lists_and_activates(db_conn):
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    pending = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=admin["id"], email=admin["email"], role="admin")
    client = TestClient(_app(db_conn, p))

    assert len(client.get("/v1/admin/users").json()) == 2
    r = client.patch(f"/v1/admin/users/{pending['id']}", json={"status": "active"})
    assert r.status_code == 200 and r.json()["status"] == "active"


async def test_member_forbidden(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    client = TestClient(_app(db_conn, p))
    assert client.get("/v1/admin/users").status_code == 403
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_admin_router.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/routers/admin.py`:
```python
"""/v1/admin/* — user administration. Admin manages accounts, not their data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import deps, users

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class UserPatchIn(BaseModel):
    status: str | None = None
    role: str | None = None


@router.get("/users")
async def list_all(
    _: deps.Principal = Depends(deps.require_admin), conn=Depends(deps.get_conn)
):
    return await users.list_users(conn)


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchIn,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
):
    if body.status is not None:
        if body.status not in ("active", "pending", "disabled"):
            raise HTTPException(status_code=422, detail="bad status")
        await users.set_status(conn, user_id, body.status)
    if body.role is not None:
        if body.role not in ("admin", "member"):
            raise HTTPException(status_code=422, detail="bad role")
        await users.set_role(conn, user_id, body.role)
    updated = await users.get_user(conn, user_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_admin_router.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routers/admin.py server/tests/test_admin_router.py
git commit -m "feat(auth): admin user-management endpoints"
```

---

### Task 12: Enforce ownership in the docs router

**Files:**
- Modify: `server/routers/docs.py`
- Test: `server/tests/test_docs_authz.py`

**Interfaces:**
- Consumes: `deps.get_current_user`, `authz.assert_owns_doc`.
- Note: docs handlers open their own connection via `get_pool()`. Add `principal: Principal = Depends(get_current_user)` to each route; call `await assert_owns_doc(conn, doc_id, principal.user_id)` using the connection the handler already opens (right after `_ensure_ready()`); on register (`POST ""`) set `user_id = principal.user_id` in the INSERT and only upsert a row the caller owns.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_docs_authz.py`. Build an app with the docs router + a real pool pointed at the test DB, override `get_current_user`. Because docs handlers use the global pool (not `get_conn`), this test uses a dedicated pool fixture that shares the test DB and is cleaned per-test.
```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import deps
from server.routers import docs as docs_router

HEX = "a" * 64
HEX2 = "b" * 64


@pytest.fixture
def docs_app(db_conn, monkeypatch):
    # Route the docs router's pool access at our transactional test connection.
    class _PoolShim:
        def connection(self):
            conn = db_conn
            class _Ctx:
                async def __aenter__(self_): return conn
                async def __aexit__(self_, *a): return False
            return _Ctx()

    monkeypatch.setattr(docs_router, "get_pool", lambda: _PoolShim())
    monkeypatch.setattr(docs_router, "is_ready", lambda: True)

    app = FastAPI()
    app.include_router(docs_router.router)
    return app


async def _make_principal(db_conn, sub):
    from server.auth.users import resolve_or_provision_user, set_status
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member")


async def test_get_others_doc_is_404(db_conn, docs_app):
    owner = await _make_principal(db_conn, "owner")
    other = await _make_principal(db_conn, "other")
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f','text',1,%s)", (HEX, owner.user_id))

    docs_app.dependency_overrides[deps.get_current_user] = lambda: other
    client = TestClient(docs_app)
    assert client.get(f"/v1/docs/{HEX}").status_code == 404


async def test_owner_can_get(db_conn, docs_app):
    owner = await _make_principal(db_conn, "owner")
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f','text',1,%s)", (HEX2, owner.user_id))
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    client = TestClient(docs_app)
    assert client.get(f"/v1/docs/{HEX2}").status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_docs_authz.py -v
```
Expected: FAIL — routes don't require a principal or check ownership (200 for non-owner).

- [ ] **Step 3: Implement**

In `server/routers/docs.py`:
- Add imports: `from ..auth.deps import get_current_user, Principal` and `from ..auth.authz import assert_owns_doc`, plus `from fastapi import Depends`.
- On `POST ""` (register): add `principal: Principal = Depends(get_current_user)`; change the INSERT to include `user_id` = `principal.user_id`; on the `ON CONFLICT` upsert add `WHERE documents.user_id = <principal>` guard, and if the row exists but isn't owned, 404 (fetch owner first via `assert_owns_doc` when the row already exists — or attempt insert and on conflict verify ownership).
- On every `/{doc_id}...` route: add `principal: Principal = Depends(get_current_user)`; immediately after `_ensure_ready()` and after opening `conn`, call `await assert_owns_doc(conn, doc_id, principal.user_id)`. (For routes that open the connection later, open it first, assert, then proceed.)

Apply to: `get_document`, `upload_chunks`, `delete_document`, `start_index_job`, `search_document`, `upload_pdf_bytes`, `delete_pdf_bytes`, `start_convert_job`, `get_document_markdown`, `delete_document_markdown`.

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_docs_authz.py server/tests/test_security_hardening.py -v
```
Expected: PASS (authz enforced; A's tests still green).

- [ ] **Step 5: Commit**

```bash
git add server/routers/docs.py server/tests/test_docs_authz.py
git commit -m "feat(authz): enforce per-user ownership on all doc routes"
```

---

### Task 13: Enforce ownership in the chat-sessions router

**Files:**
- Modify: `server/routers/chat_sessions.py`
- Test: `server/tests/test_chat_sessions_authz.py`

**Interfaces:**
- Consumes: `deps.get_current_user`, `authz.assert_owns_session`.
- Behavior: `GET ""` (list) → add `WHERE user_id = principal`; `GET/PUT/PATCH/DELETE /{session_id}` → `assert_owns_session` first; `PUT` create stamps `user_id`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_chat_sessions_authz.py` mirroring Task 12's shim pattern (monkeypatch `get_pool`/`is_ready` on `chat_sessions`), asserting: list returns only own sessions; `GET /{id}` on another user's session → 404; `PUT` create stamps the caller as owner. (Insert a `chat_sessions` row with an explicit `user_id`; the table PK `id` is TEXT.)

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_chat_sessions_authz.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement**

In `server/routers/chat_sessions.py`, add the same imports as Task 12. `list_sessions` gains `principal` and its SELECT gets `WHERE user_id = %s`. Each item route gains `principal` and calls `await assert_owns_session(conn, session_id, principal.user_id)` after opening the connection. `upsert_session`'s INSERT includes `user_id = principal.user_id`; on conflict, verify ownership (404 if the id belongs to someone else).

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_chat_sessions_authz.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routers/chat_sessions.py server/tests/test_chat_sessions_authz.py
git commit -m "feat(authz): scope chat sessions to their owner"
```

---

### Task 14: Require auth on tools + TTS routes

**Files:**
- Modify: `server/routers/tools.py`
- Modify: `server/endpoints.py`
- Test: `server/tests/test_protected_routes.py`

**Interfaces:**
- `POST /v1/tools/web_search` → `Depends(get_current_user)` (any active user).
- `POST /v1/synthesize`, `POST /v1/batch_synthesize` → `Depends(get_current_user)` (cookie or PAT).
- `GET /v1/health` → stays open.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_protected_routes.py`: build an app with the tools router; no credential → expect 401 (currently 200/valid). Override `get_current_user` → expect the route proceeds (monkeypatch the underlying `web_search` to a stub as in `test_tools_router.py`). Keep it DB-free by overriding `get_current_user` directly.
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.auth import deps
from server.routers import tools as tools_router


def test_web_search_requires_auth():
    app = FastAPI(); app.include_router(tools_router.router)
    client = TestClient(app)
    assert client.post("/v1/tools/web_search", json={"query": "hi"}).status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_protected_routes.py -v
```
Expected: FAIL — currently 200/422, not 401.

- [ ] **Step 3: Implement**

Add `principal: Principal = Depends(get_current_user)` (import from `..auth.deps`) to `web_search` in `tools.py` and to `synthesize`/`batch_synthesize` in `endpoints.py`. Leave `/v1/health` untouched.

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_protected_routes.py server/tests/test_tools_router.py -v
```
Expected: the new 401 test passes. NOTE: existing `test_tools_router.py` tests will now 401 — update them to override `get_current_user` (add the override in their `_app()` helper). Do that in this task and re-run until green.

- [ ] **Step 5: Commit**

```bash
git add server/routers/tools.py server/endpoints.py server/tests/test_protected_routes.py server/tests/test_tools_router.py
git commit -m "feat(auth): require an authenticated principal on tools + TTS routes"
```

---

### Task 15: Wire it into the app (middleware, routers, startup gate, bootstrap)

**Files:**
- Modify: `server/app.py`
- Modify: `server/db.py` (add `bootstrap_admin()` call)
- Test: `server/tests/test_app_wiring.py`

**Interfaces:**
- `app.py`: add `SessionMiddleware(secret_key=SESSION_SECRET)`; include `auth` + `admin` routers; on startup, if `AUTH_ENABLED` and OIDC configured, register the OIDC client; if the dev bypass is on but `HOST != 127.0.0.1`, **raise at startup** (refuse to boot).
- `db.py`: after `_run_migrations()`, call `bootstrap_admin()` — if `BOOTSTRAP_ADMIN_EMAIL` set, `UPDATE users SET email=:email WHERE id='<seed>' AND oidc_sub IS NULL`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_app_wiring.py`. Avoid loading the Kokoro model by testing the pure wiring helpers rather than `create_app()`:
```python
import pytest
from server.app import _startup_guard  # new pure helper


def test_dev_bypass_on_public_bind_raises():
    with pytest.raises(RuntimeError):
        _startup_guard(auth_enabled=False, host="0.0.0.0")


def test_dev_bypass_on_localhost_ok():
    _startup_guard(auth_enabled=False, host="127.0.0.1")  # no raise


def test_auth_enabled_any_host_ok():
    _startup_guard(auth_enabled=True, host="0.0.0.0")  # no raise
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest server/tests/test_app_wiring.py -v
```
Expected: FAIL — `_startup_guard` missing.

- [ ] **Step 3: Implement**

In `server/app.py`:
```python
from starlette.middleware.sessions import SessionMiddleware
from .auth.config import load_auth_config, dev_bypass_allowed
from .routers.auth import router as auth_router
from .routers.admin import router as admin_router


def _startup_guard(auth_enabled: bool, host: str) -> None:
    # The AUTH_ENABLED=false bypass must never run on a non-localhost bind.
    if not auth_enabled and host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(
            "AUTH_ENABLED=false is only allowed when HOST is localhost; "
            f"refusing to start with HOST={host}"
        )
```
Then in `create_app()`: after CORS, add `app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET", "dev-insecure-change-me"), same_site="lax", https_only=<cookie_secure>)`; `app.include_router(auth_router)`; `app.include_router(admin_router)`. In the startup event, call `_startup_guard(cfg.enabled, os.environ.get("HOST","127.0.0.1"))` before `init_db()`.

In `server/db.py`, add and call `bootstrap_admin()` after `_run_migrations()`:
```python
async def bootstrap_admin() -> None:
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    if not email:
        return
    pool = get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE users SET email=%s, updated_at=now() "
            "WHERE id='00000000-0000-0000-0000-000000000001' AND oidc_sub IS NULL",
            (email,),
        )
```
Guard the call so it no-ops if the `users` table doesn't exist yet (same `information_schema` guard pattern used for `conversion_state`).

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest server/tests/test_app_wiring.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/app.py server/db.py server/tests/test_app_wiring.py
git commit -m "feat(auth): wire session middleware, auth/admin routers, startup guard, bootstrap admin"
```

---

### Task 16: Documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Test: none (docs)

- [ ] **Step 1: Update `.env.example`**

Add an `# ─── Auth (OIDC) ───` section documenting every var from spec §10 (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URL`, `SESSION_SECRET`, `SESSION_TTL_HOURS`, `COOKIE_SECURE`, `COOKIE_DOMAIN`, `BOOTSTRAP_ADMIN_EMAIL`, `AUTH_ENABLED`), each with a one-line comment and the default.

- [ ] **Step 2: Update `README.md`**

In the Security & Hardening section, replace the "no auth gateway" paragraph with a short "Authentication (OIDC)" subsection: the app is an OIDC RP, point at Keycloak setup, `BOOTSTRAP_ADMIN_EMAIL` designates the admin, PATs for the extension, `AUTH_ENABLED=false` for localhost dev.

- [ ] **Step 3: Full suite + commit**

```bash
docker-compose up -d postgres
.venv/bin/python -m pytest server/tests/ -v
```
Expected: all green.
```bash
git add .env.example README.md
git commit -m "docs(auth): document OIDC/session/PAT config and admin bootstrap"
```

---

## Self-Review

**Spec coverage:** users table + migration 005 (T1); sessions/PATs 006 (T2); OIDC RP + login/callback/logout/me (T9); JIT + first-user-admin (T4); session cookie (T5); PATs (T6, T10); principal resolution + deny-by-default (T7); ownership 404 (T8, T12, T13); tools/TTS auth (T14); admin management (T11); SessionMiddleware + startup gate + bootstrap (T15); config incl. AUTH_ENABLED bypass (T3); docs/env (T16). `/api/*` passthrough is intentionally deferred to E (spec §9) — no task, by design. Frontend SPA (spec §8) is the separate follow-up plan, by design.

**Placeholder scan:** Tasks 12 and 13 describe the router edits in prose rather than a single copy-paste block because the change is "add the same two lines to N handlers"; the exact imports, the exact call (`await assert_owns_doc(conn, doc_id, principal.user_id)`), and the full handler list are given, so there is no ambiguity. All new modules have complete code.

**Type consistency:** `Principal(user_id, email, role)`, `resolve_or_provision_user(conn, *, iss, sub, email, display_name)`, `create_session(conn, user_id, *, ttl_hours, user_agent) -> raw`, `resolve_session -> user dict | None`, `create_token -> (id, raw)`, `assert_owns_doc/assert_owns_session(conn, id, user_id)`, `get_conn`/`get_current_user`/`require_admin`, `COOKIE_NAME="nr_session"`, seed UUID `…0001` — used consistently across tasks.

**Known execution risks (validate when running, not placeholders):**
- The `_PoolShim` in Tasks 12/13 fakes the docs/chat routers' `get_pool()` so their existing `async with pool.connection()` usage hits the transactional test connection. If a handler opens two `connection()` blocks, the shim yields the same conn twice — fine for reads, and the rollback fixture cleans up.
- Authlib's `authorize_access_token` returns `userinfo` when `openid` scope is requested; if a provider omits it, fetch via `parse_id_token`. Verified against Authlib ≥1.3.
- `SessionMiddleware` requires `itsdangerous` (added in T0) and `SESSION_SECRET`.
