# Keycloak Identity & Capability Permissions (P1+P2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app a Keycloak admin client — enroll creates/deletes Keycloak users and Keycloak realm roles (`reader`/`chat`/`admin`) drive deny-by-default feature access, persisted per-user and enforced on backend + SPA.

**Architecture:** A new injected `KeycloakAdmin` service (service-account client-credentials) does user CRUD + role mapping. Realm roles are mirrored into `users.capabilities` (migration 008), rebuilt into `Principal.capabilities` every request, and enforced by a `require_capability(name)` dependency. The SPA reads capabilities from `/v1/auth/me` and renders only permitted views. When the admin client is unconfigured, provisioning degrades to today's app-only behavior.

**Tech Stack:** FastAPI/Starlette, psycopg3 (async), httpx, Authlib (existing OIDC RP), pytest + pytest-asyncio (`httpx.AsyncClient` + `ASGITransport`), React 19 + Vite + Vitest/@testing-library.

**Spec:** [docs/superpowers/specs/2026-09-20-keycloak-identity-permissions-design.md](../specs/2026-09-20-keycloak-identity-permissions-design.md)

## Global Constraints

- **Migration number is `008`** (`server/sql/008_user_capabilities.sql`). The RAG track uses `009` — do not reuse.
- **Capability set** is exactly `{"reader", "chat", "admin"}` (`KNOWN_CAPABILITIES`). Bootstrap admin caps = `{"admin","reader","chat"}`.
- **New unknown logins → `status='pending'`, `capabilities='{}'`** (nothing until granted).
- **Router tests never use `TestClient`** — use `httpx.AsyncClient(transport=ASGITransport(app=app))`, override `deps.get_conn` with the rolled-back `db_conn`, `deps.get_current_user` with a fixed `Principal`, and `deps.get_kc_admin` with a fake.
- **No live Keycloak in CI** — `KeycloakAdmin` HTTP is exercised via `httpx.MockTransport`; routers use a duck-typed fake.
- **Commit after each task.** Per the repo owner's rule, the executor pauses for per-commit approval unless told otherwise; keep each task's commit self-contained.
- Backend baseline: **35** relevant auth/admin tests green (whole suite must stay green). Frontend baseline: **189** tests green.

---

### Task 1: Config — Keycloak admin credentials + availability gate

**Files:**
- Modify: `server/auth/config.py`
- Test: `server/tests/test_auth_config.py`

**Interfaces:**
- Produces: `AuthConfig.kc_admin_client_id: str | None`, `AuthConfig.kc_admin_client_secret: str | None`; `kc_admin_available(cfg: AuthConfig) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# append to server/tests/test_auth_config.py
from server.auth.config import load_auth_config, kc_admin_available


def test_kc_admin_fields_and_availability():
    cfg = load_auth_config({
        "KC_ADMIN_CLIENT_ID": "natural-reader-admin",
        "KC_ADMIN_CLIENT_SECRET": "s3cr3t",
    })
    assert cfg.kc_admin_client_id == "natural-reader-admin"
    assert cfg.kc_admin_client_secret == "s3cr3t"
    assert kc_admin_available(cfg) is True


def test_kc_admin_unavailable_when_unset():
    assert kc_admin_available(load_auth_config({})) is False
    assert kc_admin_available(load_auth_config({"KC_ADMIN_CLIENT_ID": "x"})) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_auth_config.py -v`
Expected: FAIL (`AuthConfig` has no `kc_admin_client_id` / `kc_admin_available` undefined).

- [ ] **Step 3: Implement**

In `server/auth/config.py`, add two fields to the `AuthConfig` dataclass (after `bootstrap_admin_email`):

```python
    kc_admin_client_id: str | None
    kc_admin_client_secret: str | None
```

In `load_auth_config`, add to the returned `AuthConfig(...)`:

```python
        kc_admin_client_id=env.get("KC_ADMIN_CLIENT_ID"),
        kc_admin_client_secret=env.get("KC_ADMIN_CLIENT_SECRET"),
```

At module scope add:

```python
def kc_admin_available(cfg: "AuthConfig") -> bool:
    """True only when both service-account credentials are configured — the
    switch between full Keycloak provisioning and app-only degradation."""
    return bool(cfg.kc_admin_client_id and cfg.kc_admin_client_secret)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_auth_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/config.py server/tests/test_auth_config.py
git commit -m "feat(auth): KC_ADMIN_* config fields + kc_admin_available gate"
```

---

### Task 2: Migration 008 — `users.capabilities` + backfill + data-layer read

**Files:**
- Create: `server/sql/008_user_capabilities.sql`
- Modify: `server/auth/users.py` (add `"capabilities"` to `_KEYS`)
- Test: `server/tests/test_user_capabilities_migration.py`

**Interfaces:**
- Produces: every `users.get_user` / `list_users` / resolver result dict now carries `capabilities: list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_user_capabilities_migration.py
"""008 adds users.capabilities and backfills it from role/status without
locking anyone out. The migration ran session-wide; here we re-run the exact
backfill UPDATEs against seeded rows in a rolled-back tx to prove the logic."""
import pytest

pytestmark = pytest.mark.asyncio

BACKFILL = [
    "UPDATE users SET capabilities='{admin,reader,chat}' WHERE role='admin'",
    "UPDATE users SET capabilities='{reader,chat}' "
    "WHERE role='member' AND status='active'",
    "UPDATE users SET capabilities='{}' WHERE status IN ('pending','disabled')",
]


async def _mk(conn, email, role, status):
    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status, capabilities) "
        "VALUES ('i', %s, %s, %s, %s, '{}') RETURNING id",
        (email, email, role, status),
    )
    return str((await cur.fetchone())[0])


async def test_column_exists_and_defaults_empty(db_conn):
    uid = await _mk(db_conn, "z@x.io", "member", "pending")
    from server.auth.users import get_user
    assert (await get_user(db_conn, uid))["capabilities"] == []


async def test_backfill_preserves_access(db_conn):
    a = await _mk(db_conn, "a@x.io", "admin", "active")
    m = await _mk(db_conn, "m@x.io", "member", "active")
    p = await _mk(db_conn, "p@x.io", "member", "pending")
    d = await _mk(db_conn, "d@x.io", "member", "disabled")
    for sql in BACKFILL:
        await db_conn.execute(sql)
    from server.auth.users import get_user
    assert set((await get_user(db_conn, a))["capabilities"]) == {"admin", "reader", "chat"}
    assert set((await get_user(db_conn, m))["capabilities"]) == {"reader", "chat"}
    assert (await get_user(db_conn, p))["capabilities"] == []
    assert (await get_user(db_conn, d))["capabilities"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_user_capabilities_migration.py -v`
Expected: FAIL — `column "capabilities" does not exist` (migration not written / `_KEYS` lacks it).

- [ ] **Step 3: Implement the migration**

```sql
-- server/sql/008_user_capabilities.sql
-- Capability-based access control. Realm roles (reader/chat/admin) are mirrored
-- here and rebuilt into the request Principal every call, so admin changes take
-- effect immediately. Backfill preserves existing access on upgrade.
ALTER TABLE users ADD COLUMN IF NOT EXISTS capabilities TEXT[] NOT NULL DEFAULT '{}';

UPDATE users SET capabilities='{admin,reader,chat}' WHERE role='admin';
UPDATE users SET capabilities='{reader,chat}'
  WHERE role='member' AND status='active';
UPDATE users SET capabilities='{}' WHERE status IN ('pending','disabled');
```

- [ ] **Step 4: Add `capabilities` to the data layer**

In `server/auth/users.py`, add `"capabilities"` to `_KEYS` (place it right before `"created_at"`):

```python
_KEYS = [
    "id", "email", "display_name", "role", "status", "oidc_iss", "oidc_sub",
    "inference_daily_token_budget", "capabilities", "created_at",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_user_capabilities_migration.py server/tests/test_auth_users.py -v`
Expected: PASS (the migration auto-applies to the test DB; `get_user` now returns `capabilities`).

- [ ] **Step 6: Commit**

```bash
git add server/sql/008_user_capabilities.sql server/auth/users.py server/tests/test_user_capabilities_migration.py
git commit -m "feat(auth): migration 008 users.capabilities + backfill; data layer reads it"
```

---

### Task 3: Principal capabilities + `require_capability` + role derivation

**Files:**
- Modify: `server/auth/deps.py`
- Create: `server/auth/capabilities.py`
- Test: `server/tests/test_auth_deps.py` (extend), `server/tests/test_capabilities.py`

**Interfaces:**
- Consumes: `users.get_user` result carrying `capabilities` (Task 2).
- Produces: `Principal.capabilities: frozenset[str]`; `deps.require_capability(name: str) -> Callable` (a dependency returning `Principal`, 403 `{"error":"missing_capability","capability":name}` when absent); `capabilities.KNOWN_CAPABILITIES`, `capabilities.BOOTSTRAP_ADMIN_CAPABILITIES`, `capabilities.caps_from_claims(claims: dict) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_capabilities.py
from server.auth.capabilities import caps_from_claims, KNOWN_CAPABILITIES


def test_caps_from_claims_intersects_known():
    claims = {"realm_access": {"roles": ["chat", "reader", "offline_access", "admin"]}}
    assert set(caps_from_claims(claims)) == {"chat", "reader", "admin"}


def test_caps_from_claims_empty_when_absent():
    assert caps_from_claims({}) == []
    assert caps_from_claims({"realm_access": {}}) == []


def test_known_capabilities_frozen():
    assert KNOWN_CAPABILITIES == frozenset({"reader", "chat", "admin"})
```

```python
# append to server/tests/test_auth_deps.py
import httpx
from httpx import ASGITransport
from fastapi import Depends, FastAPI
from server.auth import deps


def _cap_app(principal):
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: principal

    @app.get("/needs-chat")
    async def needs_chat(p: deps.Principal = Depends(deps.require_capability("chat"))):
        return {"ok": p.email}

    return app


async def test_require_capability_allows_holder():
    p = deps.Principal(user_id="u", email="e@x.io", role="member",
                       capabilities=frozenset({"chat"}))
    async with httpx.AsyncClient(transport=ASGITransport(app=_cap_app(p)),
                                 base_url="http://t") as c:
        assert (await c.get("/needs-chat")).status_code == 200


async def test_require_capability_403s_without():
    p = deps.Principal(user_id="u", email="e@x.io", role="member",
                       capabilities=frozenset({"reader"}))
    async with httpx.AsyncClient(transport=ASGITransport(app=_cap_app(p)),
                                 base_url="http://t") as c:
        r = await c.get("/needs-chat")
        assert r.status_code == 403
        assert r.json()["detail"] == {"error": "missing_capability", "capability": "chat"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_capabilities.py server/tests/test_auth_deps.py -v`
Expected: FAIL (`capabilities` module missing; `Principal` has no `capabilities`; `require_capability` undefined).

- [ ] **Step 3: Implement `capabilities.py`**

```python
# server/auth/capabilities.py
"""The app's capability vocabulary and how it is read from OIDC claims.

Realm roles are the source of truth; this module maps the token's
`realm_access.roles` to the app's known capabilities (ignoring Keycloak's
built-in roles like `offline_access`)."""
from __future__ import annotations

KNOWN_CAPABILITIES = frozenset({"reader", "chat", "admin"})
BOOTSTRAP_ADMIN_CAPABILITIES = frozenset({"admin", "reader", "chat"})


def caps_from_claims(claims: dict) -> list[str]:
    roles = ((claims.get("realm_access") or {}).get("roles")) or []
    return sorted(KNOWN_CAPABILITIES.intersection(roles))
```

- [ ] **Step 4: Implement Principal + require_capability in `deps.py`**

Add `capabilities` to the dataclass:

```python
@dataclass
class Principal:
    user_id: str
    email: str
    role: str
    capabilities: frozenset[str] = frozenset()
```

Replace `_principal`:

```python
def _principal(row: dict) -> Principal:
    caps = frozenset(row.get("capabilities") or [])
    role = "admin" if "admin" in caps else "member"
    return Principal(user_id=row["id"], email=row["email"], role=role, capabilities=caps)
```

Add the factory (after `require_admin`):

```python
def require_capability(name: str):
    """Deny-by-default gate for a feature capability (reader/chat/admin)."""
    async def _dep(principal: Principal = Depends(get_current_user)) -> Principal:
        if name not in principal.capabilities:
            raise HTTPException(
                status_code=403,
                detail={"error": "missing_capability", "capability": name},
            )
        return principal
    return _dep
```

> Note: `require_admin` keeps checking `principal.role == "admin"`; `role` is now derived from the `admin` capability in `_principal`, so the two agree. Router tests that construct `Principal(role="admin")` directly still pass `require_admin`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_capabilities.py server/tests/test_auth_deps.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/auth/capabilities.py server/auth/deps.py server/tests/test_capabilities.py server/tests/test_auth_deps.py
git commit -m "feat(auth): Principal.capabilities + require_capability gate + role derivation"
```

---

### Task 4: KeycloakAdmin — client-credentials token core + `get_kc_admin`

**Files:**
- Create: `server/auth/kc_admin.py`
- Modify: `server/auth/deps.py` (add `get_kc_admin` dependency)
- Test: `server/tests/test_kc_admin.py`

**Interfaces:**
- Consumes: `AuthConfig` + `kc_admin_available` (Task 1).
- Produces: `KCAdminError(RuntimeError)`; `KeycloakAdmin(issuer, client_id, client_secret, *, http: httpx.AsyncClient | None = None)` with `async _token() -> str` (cached, refreshed on expiry); `build_kc_admin(cfg) -> KeycloakAdmin | None`; `deps.get_kc_admin() -> KeycloakAdmin | None`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_kc_admin.py
import time
import httpx
import pytest
from server.auth.kc_admin import KeycloakAdmin

pytestmark = pytest.mark.asyncio
ISSUER = "http://kc.test/realms/nr"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_token_is_fetched_and_cached():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/realms/nr/protocol/openid-connect/token"
        assert b"grant_type=client_credentials" in req.content
        calls["n"] += 1
        return httpx.Response(200, json={"access_token": "T", "expires_in": 300})

    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_client(handler))
    assert await kc._token() == "T"
    assert await kc._token() == "T"        # second call served from cache
    assert calls["n"] == 1


async def test_token_error_raises_kcadminerror():
    from server.auth.kc_admin import KCAdminError

    def handler(req):
        return httpx.Response(401, json={"error": "invalid_client"})

    kc = KeycloakAdmin(ISSUER, "svc", "bad", http=_client(handler))
    with pytest.raises(KCAdminError):
        await kc._token()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_kc_admin.py -v`
Expected: FAIL — module `server.auth.kc_admin` does not exist.

- [ ] **Step 3: Implement the token core**

```python
# server/auth/kc_admin.py
"""Keycloak Admin REST client (service-account, client-credentials grant).

Derives all URLs from OIDC_ISSUER (`{base}/realms/{realm}`): the token endpoint
is `{issuer}/protocol/openid-connect/token`, the admin base is
`{base}/admin/realms/{realm}`. Caches the admin access token. All unexpected
responses raise KCAdminError so callers map them to a clear HTTP error."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx


class KCAdminError(RuntimeError):
    pass


class KeycloakAdmin:
    def __init__(self, issuer: str, client_id: str, client_secret: str,
                 *, http: httpx.AsyncClient | None = None):
        issuer = issuer.rstrip("/")
        self._issuer = issuer
        parts = urlsplit(issuer)
        base = f"{parts.scheme}://{parts.netloc}"
        realm = issuer.rsplit("/realms/", 1)[-1]
        self._token_url = f"{issuer}/protocol/openid-connect/token"
        self._admin = f"{base}/admin/realms/{realm}"
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._tok: str | None = None
        self._tok_exp: float = 0.0

    async def _token(self) -> str:
        if self._tok and time.monotonic() < self._tok_exp:
            return self._tok
        try:
            r = await self._http.post(self._token_url, data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            })
        except httpx.HTTPError as e:
            raise KCAdminError(f"token request failed: {e}") from e
        if r.status_code != 200:
            raise KCAdminError(f"token endpoint returned {r.status_code}")
        body = r.json()
        self._tok = body["access_token"]
        self._tok_exp = time.monotonic() + max(30, int(body.get("expires_in", 60)) - 30)
        return self._tok

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        tok = await self._token()
        headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {tok}"}
        try:
            r = await self._http.request(method, f"{self._admin}{path}",
                                         headers=headers, **kw)
        except httpx.HTTPError as e:
            raise KCAdminError(f"{method} {path} failed: {e}") from e
        if r.status_code == 401:  # token might have expired early — retry once
            self._tok = None
            tok = await self._token()
            headers["Authorization"] = f"Bearer {tok}"
            r = await self._http.request(method, f"{self._admin}{path}",
                                         headers=headers, **kw)
        return r


def build_kc_admin(cfg) -> "KeycloakAdmin | None":
    from .config import kc_admin_available
    if not (cfg.oidc_issuer and kc_admin_available(cfg)):
        return None
    return KeycloakAdmin(cfg.oidc_issuer, cfg.kc_admin_client_id,
                         cfg.kc_admin_client_secret)
```

- [ ] **Step 4: Add the `get_kc_admin` dependency in `deps.py`**

```python
# in server/auth/deps.py
_kc_admin = None


async def get_kc_admin():
    """Yield a shared KeycloakAdmin, or None when the service account is
    unconfigured (callers then use the app-only degraded path)."""
    global _kc_admin
    cfg = load_auth_config(os.environ)
    from .kc_admin import build_kc_admin
    if _kc_admin is None:
        _kc_admin = build_kc_admin(cfg)
    return _kc_admin
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest server/tests/test_kc_admin.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/auth/kc_admin.py server/auth/deps.py server/tests/test_kc_admin.py
git commit -m "feat(auth): KeycloakAdmin token core + get_kc_admin dependency"
```

---

### Task 5: KeycloakAdmin — user CRUD, realm roles, SMTP detection

**Files:**
- Modify: `server/auth/kc_admin.py`
- Test: `server/tests/test_kc_admin.py` (extend)

**Interfaces:**
- Produces (all `async`): `find_user_by_email(email) -> str | None`; `create_user(*, email, display_name=None, email_verified=False) -> str`; `set_temp_password(sub, password, *, temporary=True) -> None`; `send_actions_email(sub, actions) -> None`; `set_enabled(sub, enabled) -> None`; `delete_user(sub) -> None`; `assign_realm_roles(sub, names) -> None`; `remove_realm_roles(sub, names) -> None`; `get_user_realm_roles(sub) -> list[str]`; `realm_smtp_configured() -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# append to server/tests/test_kc_admin.py
def _authed(handler):
    def wrapped(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 300})
        return handler(req)
    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped))


async def test_create_user_returns_sub_from_location():
    def handler(req):
        assert req.method == "POST" and req.url.path.endswith("/users")
        return httpx.Response(201, headers={
            "Location": "http://kc.test/admin/realms/nr/users/abc-123"})
    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_authed(handler))
    assert await kc.create_user(email="b@x.io", email_verified=True) == "abc-123"


async def test_find_user_by_email_exact():
    def handler(req):
        assert req.url.params.get("email") == "b@x.io"
        assert req.url.params.get("exact") == "true"
        return httpx.Response(200, json=[{"id": "u9", "email": "b@x.io"}])
    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_authed(handler))
    assert await kc.find_user_by_email("b@x.io") == "u9"


async def test_find_user_by_email_none():
    kc = KeycloakAdmin(ISSUER, "svc", "sec",
                       http=_authed(lambda req: httpx.Response(200, json=[])))
    assert await kc.find_user_by_email("nobody@x.io") is None


async def test_assign_realm_roles_looks_up_then_posts():
    seen = {}
    def handler(req):
        if req.url.path.endswith("/roles/chat"):
            return httpx.Response(200, json={"id": "r-chat", "name": "chat"})
        if req.url.path.endswith("/roles/reader"):
            return httpx.Response(200, json={"id": "r-read", "name": "reader"})
        if req.method == "POST" and "role-mappings/realm" in req.url.path:
            seen["body"] = req.content
            return httpx.Response(204)
        raise AssertionError(req.url.path)
    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_authed(handler))
    await kc.assign_realm_roles("u1", ["chat", "reader"])
    assert b"r-chat" in seen["body"] and b"r-read" in seen["body"]


async def test_realm_smtp_configured():
    def on(req): return httpx.Response(200, json={"smtpServer": {"host": "smtp.x"}})
    def off(req): return httpx.Response(200, json={"smtpServer": {}})
    assert await KeycloakAdmin(ISSUER, "s", "s", http=_authed(on)).realm_smtp_configured() is True
    assert await KeycloakAdmin(ISSUER, "s", "s", http=_authed(off)).realm_smtp_configured() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_kc_admin.py -v`
Expected: FAIL — methods not implemented.

- [ ] **Step 3: Implement the methods**

Add to `KeycloakAdmin` (using `self._req`):

```python
    async def find_user_by_email(self, email: str) -> str | None:
        r = await self._req("GET", "/users", params={"email": email, "exact": "true"})
        if r.status_code != 200:
            raise KCAdminError(f"find_user_by_email {r.status_code}")
        rows = r.json()
        return rows[0]["id"] if rows else None

    async def create_user(self, *, email: str, display_name: str | None = None,
                          email_verified: bool = False) -> str:
        payload = {"email": email, "username": email, "enabled": True,
                   "emailVerified": email_verified}
        if display_name:
            payload["firstName"] = display_name
        r = await self._req("POST", "/users", json=payload)
        if r.status_code == 409:
            raise KCAdminError("user already exists in Keycloak")
        if r.status_code != 201:
            raise KCAdminError(f"create_user {r.status_code}")
        return r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    async def set_temp_password(self, sub: str, password: str,
                                *, temporary: bool = True) -> None:
        r = await self._req("PUT", f"/users/{sub}/reset-password",
                            json={"type": "password", "value": password,
                                  "temporary": temporary})
        if r.status_code not in (200, 204):
            raise KCAdminError(f"set_temp_password {r.status_code}")

    async def send_actions_email(self, sub: str, actions: list[str]) -> None:
        r = await self._req("PUT", f"/users/{sub}/execute-actions-email", json=actions)
        if r.status_code not in (200, 204):
            raise KCAdminError(f"send_actions_email {r.status_code}")

    async def set_enabled(self, sub: str, enabled: bool) -> None:
        r = await self._req("PUT", f"/users/{sub}", json={"enabled": enabled})
        if r.status_code not in (200, 204):
            raise KCAdminError(f"set_enabled {r.status_code}")

    async def delete_user(self, sub: str) -> None:
        r = await self._req("DELETE", f"/users/{sub}")
        if r.status_code not in (200, 204, 404):
            raise KCAdminError(f"delete_user {r.status_code}")

    async def _role(self, name: str) -> dict:
        r = await self._req("GET", f"/roles/{name}")
        if r.status_code != 200:
            raise KCAdminError(f"role {name} lookup {r.status_code}")
        return {"id": r.json()["id"], "name": name}

    async def assign_realm_roles(self, sub: str, names: list[str]) -> None:
        if not names:
            return
        body = [await self._role(n) for n in names]
        r = await self._req("POST", f"/users/{sub}/role-mappings/realm", json=body)
        if r.status_code not in (200, 204):
            raise KCAdminError(f"assign_realm_roles {r.status_code}")

    async def remove_realm_roles(self, sub: str, names: list[str]) -> None:
        if not names:
            return
        body = [await self._role(n) for n in names]
        r = await self._req("DELETE", f"/users/{sub}/role-mappings/realm", json=body)
        if r.status_code not in (200, 204):
            raise KCAdminError(f"remove_realm_roles {r.status_code}")

    async def get_user_realm_roles(self, sub: str) -> list[str]:
        r = await self._req("GET", f"/users/{sub}/role-mappings/realm/composite")
        if r.status_code != 200:
            raise KCAdminError(f"get_user_realm_roles {r.status_code}")
        return [x["name"] for x in r.json()]

    async def realm_smtp_configured(self) -> bool:
        r = await self._req("GET", "")
        if r.status_code != 200:
            raise KCAdminError(f"realm read {r.status_code}")
        return bool((r.json().get("smtpServer") or {}).get("host"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_kc_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/auth/kc_admin.py server/tests/test_kc_admin.py
git commit -m "feat(auth): KeycloakAdmin user CRUD, realm-role mapping, SMTP detection"
```

---

### Task 6: Login capability sync + bootstrap role assignment + `/me`

**Files:**
- Modify: `server/auth/users.py` (`resolve_or_provision_user` + `set_capabilities`), `server/routers/auth.py` (callback + `/me`)
- Test: `server/tests/test_auth_users.py` (extend), `server/tests/test_auth_router.py` (extend)

**Interfaces:**
- Consumes: `caps_from_claims` (Task 3), `KeycloakAdmin.assign_realm_roles` (Task 5), `deps.get_kc_admin` (Task 4).
- Produces: `users.set_capabilities(conn, user_id, capabilities) -> None`; `resolve_or_provision_user(..., capabilities: list[str] | None = None)`; `/v1/auth/me` returns `{id, email, role, capabilities}`.

- [ ] **Step 1: Write the failing tests**

```python
# append to server/tests/test_auth_users.py
from server.auth.users import resolve_or_provision_user, get_user, SEED_ADMIN_ID


async def test_first_login_claims_seed_admin_with_bootstrap_caps(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    assert u["id"] == SEED_ADMIN_ID
    assert set(u["capabilities"]) == {"admin", "reader", "chat"}


async def test_brand_new_user_is_pending_with_no_caps(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")  # seed
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io",
                                        capabilities=[])
    assert m["status"] == "pending" and m["capabilities"] == []


async def test_known_identity_caps_synced_from_token(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")  # seed
    await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io",
                                    capabilities=[])
    synced = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io",
                                             capabilities=["reader"])
    assert set(synced["capabilities"]) == {"reader"} and synced["role"] == "member"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_auth_users.py -v`
Expected: FAIL — `resolve_or_provision_user` has no `capabilities` param / seed not given bootstrap caps.

- [ ] **Step 3: Implement `set_capabilities` + resolver changes**

Add to `server/auth/users.py`:

```python
async def set_capabilities(conn, user_id: str, capabilities) -> None:
    """Overwrite a user's capabilities; keep the legacy role column in sync
    (admin iff the admin capability is present)."""
    caps = sorted(set(capabilities))
    role = "admin" if "admin" in caps else "member"
    await conn.execute(
        "UPDATE users SET capabilities=%s, role=%s, updated_at=now() WHERE id=%s",
        (caps, role, user_id),
    )
```

Change `resolve_or_provision_user` signature to add `capabilities: list[str] | None = None`, and:

- Branch 1 (known identity), after the email/display_name UPDATE:
  ```python
  if capabilities is not None:
      await set_capabilities(conn, found["id"], capabilities)
  ```
- Branch 2 (link), after the link UPDATE:
  ```python
  if capabilities is not None:
      await set_capabilities(conn, by_email["id"], capabilities)
  ```
- Branch 3 seed-admin claim (`cur.rowcount == 1`), before `return`:
  ```python
  from .capabilities import BOOTSTRAP_ADMIN_CAPABILITIES
  await set_capabilities(conn, SEED_ADMIN_ID, BOOTSTRAP_ADMIN_CAPABILITIES)
  ```
  (Brand-new INSERT stays `role='member', status='pending'` with the column default `'{}'` — no caps for unknown self-registration.)

- [ ] **Step 4: Wire the callback + `/me` in `server/routers/auth.py`**

Add imports: `from ..auth.capabilities import caps_from_claims`. Change `callback` to depend on the admin client and pass caps:

```python
@router.get("/callback")
async def callback(request: Request, conn=Depends(deps.get_conn),
                   kc=Depends(deps.get_kc_admin)):
    token = await _client().authorize_access_token(request)
    claims = token.get("userinfo") or {}
    sub, iss, email = claims.get("sub"), claims.get("iss"), claims.get("email")
    if not (sub and email):
        raise HTTPException(status_code=400, detail="OIDC token missing sub/email")
    caps = caps_from_claims(claims)
    try:
        user = await users.resolve_or_provision_user(
            conn, iss=iss, sub=sub, email=email,
            display_name=claims.get("name"),
            email_verified=bool(claims.get("email_verified")),
            capabilities=caps,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # Bootstrap: the founder who claimed the seed admin must also hold the
    # realm roles in Keycloak, or the next login's sync would demote them.
    if kc is not None and user["id"] == users.SEED_ADMIN_ID:
        try:
            await kc.assign_realm_roles(sub, ["admin", "reader", "chat"])
        except Exception:  # noqa: BLE001 — best-effort; app row is authoritative
            pass
    raw = await sessions.create_session(
        conn, user["id"], user_agent=request.headers.get("user-agent")
    )
    ...  # unchanged: id_token stash, redirect, cookie
```

Update `/me`:

```python
@router.get("/me")
async def me(principal: deps.Principal = Depends(deps.get_current_user)):
    return {"id": principal.user_id, "email": principal.email,
            "role": principal.role, "capabilities": sorted(principal.capabilities)}
```

- [ ] **Step 5: Add a `/me` capabilities test**

```python
# append to server/tests/test_auth_router.py — follow the file's existing app/override pattern
async def test_me_returns_capabilities(db_conn):
    import httpx
    from httpx import ASGITransport
    from fastapi import FastAPI
    from server.auth import deps
    from server.routers import auth as auth_router

    app = FastAPI()
    app.dependency_overrides[deps.get_conn] = lambda: (yield db_conn)  # if needed
    p = deps.Principal(user_id="u", email="e@x.io", role="member",
                       capabilities=frozenset({"reader"}))
    app.dependency_overrides[deps.get_current_user] = lambda: p
    app.include_router(auth_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://t") as c:
        body = (await c.get("/v1/auth/me")).json()
    assert body["capabilities"] == ["reader"] and body["role"] == "member"
```

> If the `lambda: (yield ...)` generator override is awkward, mirror `test_admin_router.py`'s `async def _conn_override(): yield db_conn` helper instead.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_auth_users.py server/tests/test_auth_router.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add server/auth/users.py server/routers/auth.py server/tests/test_auth_users.py server/tests/test_auth_router.py
git commit -m "feat(auth): sync capabilities from token at login; bootstrap KC roles; /me returns caps"
```

---

### Task 7: Enroll — Keycloak-integrated with graceful fallback

**Files:**
- Modify: `server/routers/admin.py` (`enroll_user` endpoint, `UserEnrollIn`), `server/auth/users.py` (caps on `enroll_user`; add `enroll_linked_user`)
- Test: `server/tests/test_admin_router.py` (extend)

**Interfaces:**
- Consumes: `deps.get_kc_admin`, `KeycloakAdmin` methods, `KNOWN_CAPABILITIES`.
- Produces: `POST /v1/admin/users` accepts `capabilities: list[str]`; returns `{user, onboarding, temp_password?}`; `users.enroll_linked_user(conn, *, iss, sub, email, display_name, capabilities, status, budget) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# append to server/tests/test_admin_router.py
class _FakeKC:
    def __init__(self, smtp=False, existing=None):
        self.smtp, self.existing = smtp, (existing or set())
        self.created, self.roles, self.temp, self.emailed, self.deleted = [], {}, {}, [], []
    async def find_user_by_email(self, email): return "kc-x" if email in self.existing else None
    async def create_user(self, *, email, display_name=None, email_verified=False):
        self.created.append(email); return f"sub-{email}"
    async def assign_realm_roles(self, sub, names): self.roles[sub] = list(names)
    async def set_temp_password(self, sub, pw, *, temporary=True): self.temp[sub] = pw
    async def send_actions_email(self, sub, actions): self.emailed.append(sub)
    async def realm_smtp_configured(self): return self.smtp
    async def delete_user(self, sub): self.deleted.append(sub)


def _app_kc(db_conn, principal, kc):
    from fastapi import FastAPI
    app = FastAPI()
    async def _conn():  # noqa
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.dependency_overrides[deps.get_kc_admin] = lambda: kc
    app.include_router(admin_router.router)
    return app


async def test_enroll_creates_linked_kc_user_temp_password(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKC(smtp=False)
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={
            "email": "new@x.io", "capabilities": ["reader", "chat"], "status": "active"})
    assert r.status_code == 201
    body = r.json()
    assert body["onboarding"] == "temp_password" and body["temp_password"]
    assert body["user"]["oidc_sub"] == "sub-new@x.io"
    assert set(body["user"]["capabilities"]) == {"reader", "chat"}
    assert kc.roles["sub-new@x.io"] == ["reader", "chat"]


async def test_enroll_sends_invite_when_smtp(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKC(smtp=True)
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={"email": "e@x.io", "capabilities": []})
    assert r.json()["onboarding"] == "email" and "temp_password" not in r.json()
    assert kc.emailed == ["sub-e@x.io"]


async def test_enroll_conflicts_when_email_in_kc(db_conn):
    _, p = await _admin(db_conn)
    kc = _FakeKC(existing={"dup@x.io"})
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.post("/v1/admin/users", json={"email": "dup@x.io", "capabilities": []})
    assert r.status_code == 409


async def test_enroll_falls_back_when_kc_absent(db_conn):
    _, p = await _admin(db_conn)
    async with _client(_app_kc(db_conn, p, None)) as c:
        r = await c.post("/v1/admin/users", json={"email": "f@x.io", "capabilities": ["reader"]})
    body = r.json()
    assert r.status_code == 201 and body["onboarding"] == "manual"
    assert body["user"]["oidc_sub"] is None
    assert set(body["user"]["capabilities"]) == {"reader"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_admin_router.py -k enroll -v`
Expected: FAIL — enroll doesn't accept `capabilities`, doesn't use kc, doesn't return `onboarding`.

- [ ] **Step 3: Add the data-layer helpers in `users.py`**

Add `capabilities` to `enroll_user` (the unlinked/fallback insert) and a linked variant:

```python
async def enroll_user(conn, *, email, display_name=None, role="member",
                      status="pending", inference_daily_token_budget=None,
                      capabilities=None) -> dict[str, Any]:
    try:
        cur = await conn.execute(
            "INSERT INTO users (email, display_name, role, status, "
            "inference_daily_token_budget, capabilities) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (email, display_name, role, status, inference_daily_token_budget,
             sorted(set(capabilities or []))),
        )
    except pg_errors.UniqueViolation:
        raise ValueError("email already exists")
    return await get_user(conn, str((await cur.fetchone())[0]))


async def enroll_linked_user(conn, *, iss, sub, email, display_name=None,
                             capabilities=None, status="active",
                             inference_daily_token_budget=None) -> dict[str, Any]:
    """A row already bound to its Keycloak identity (created via the admin API)."""
    caps = sorted(set(capabilities or []))
    role = "admin" if "admin" in caps else "member"
    try:
        cur = await conn.execute(
            "INSERT INTO users (oidc_iss, oidc_sub, email, display_name, role, "
            "status, inference_daily_token_budget, capabilities) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (iss, sub, email, display_name, role, status,
             inference_daily_token_budget, caps),
        )
    except pg_errors.UniqueViolation:
        raise ValueError("email already exists")
    return await get_user(conn, str((await cur.fetchone())[0]))
```

- [ ] **Step 4: Rewrite `enroll_user` in `server/routers/admin.py`**

Add `import secrets` and `from ..auth.capabilities import KNOWN_CAPABILITIES`, extend `UserEnrollIn` with `capabilities: list[str] = []`, and:

```python
@router.post("/users", status_code=201)
async def enroll_user(
    body: UserEnrollIn,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
    kc=Depends(deps.get_kc_admin),
):
    if body.status not in ("active", "pending", "disabled"):
        raise HTTPException(status_code=422, detail="bad status")
    caps = sorted(set(body.capabilities))
    if not set(caps).issubset(KNOWN_CAPABILITIES):
        raise HTTPException(status_code=422, detail="unknown capability")
    if body.inference_daily_token_budget is not None and body.inference_daily_token_budget < 0:
        raise HTTPException(status_code=422, detail="budget must be >= 0")

    if kc is None:
        try:
            user = await users.enroll_user(
                conn, email=body.email, display_name=body.display_name,
                status=body.status, capabilities=caps,
                inference_daily_token_budget=body.inference_daily_token_budget)
        except ValueError:
            raise HTTPException(status_code=409, detail="email already exists")
        return {"user": user, "onboarding": "manual"}

    from ..auth.config import load_auth_config
    from ..auth.kc_admin import KCAdminError
    if await kc.find_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="email already exists in Keycloak")
    try:
        sub = await kc.create_user(email=body.email, display_name=body.display_name,
                                   email_verified=True)
    except KCAdminError as e:
        raise HTTPException(status_code=502, detail=f"Keycloak create failed: {e}")
    try:
        await kc.assign_realm_roles(sub, caps)
        onboarding, temp = "email", None
        if await kc.realm_smtp_configured():
            await kc.send_actions_email(sub, ["VERIFY_EMAIL", "UPDATE_PASSWORD"])
        else:
            temp = secrets.token_urlsafe(12)
            await kc.set_temp_password(sub, temp, temporary=True)
            onboarding = "temp_password"
        iss = load_auth_config(os.environ).oidc_issuer
        user = await users.enroll_linked_user(
            conn, iss=iss, sub=sub, email=body.email,
            display_name=body.display_name, capabilities=caps, status=body.status,
            inference_daily_token_budget=body.inference_daily_token_budget)
    except Exception as e:  # compensate: no orphaned Keycloak user
        try:
            await kc.delete_user(sub)
        except Exception:
            pass
        if isinstance(e, ValueError):
            raise HTTPException(status_code=409, detail="email already exists")
        raise HTTPException(status_code=502, detail=f"enroll failed: {e}")
    out = {"user": user, "onboarding": onboarding}
    if temp:
        out["temp_password"] = temp
    return out
```

Add `import os` to `admin.py` if not present.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_admin_router.py -k enroll -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routers/admin.py server/auth/users.py server/tests/test_admin_router.py
git commit -m "feat(admin): enroll creates the Keycloak user (invite/temp-password) with graceful fallback"
```

---

### Task 8: Disable/enable + delete propagation + capability PATCH

**Files:**
- Modify: `server/routers/admin.py` (`patch_user`, `delete_user`)
- Test: `server/tests/test_admin_router.py`, `server/tests/test_admin_lifecycle.py` (extend)

**Interfaces:**
- Consumes: `deps.get_kc_admin`, `users.set_capabilities`, `KeycloakAdmin.{set_enabled,delete_user,assign_realm_roles,remove_realm_roles}`.
- Produces: `PATCH /v1/admin/users/{id}` honors `capabilities` (reconciles KC + DB) and propagates `status` to KC `enabled`; `DELETE` removes the KC user too.

- [ ] **Step 1: Write the failing tests**

```python
# append to server/tests/test_admin_router.py
async def test_patch_capabilities_reconciles_kc_and_db(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user, get_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-1", email="c@x.io",
                                 capabilities=["reader"], status="active")

    class _KC(_FakeKC):
        def __init__(self): super().__init__(); self.assigned=[]; self.removed=[]
        async def assign_realm_roles(self, sub, names): self.assigned.append((sub, list(names)))
        async def remove_realm_roles(self, sub, names): self.removed.append((sub, list(names)))
    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.patch(f"/v1/admin/users/{u['id']}",
                          json={"capabilities": ["reader", "chat"]})
    assert r.status_code == 200
    assert set((await get_user(db_conn, u["id"]))["capabilities"]) == {"reader", "chat"}
    assert kc.assigned == [("sub-1", ["chat"])] and kc.removed == []


async def test_disable_propagates_enabled_false(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-2", email="d@x.io",
                                 capabilities=["reader"], status="active")
    class _KC(_FakeKC):
        def __init__(self): super().__init__(); self.enabled=[]
        async def set_enabled(self, sub, enabled): self.enabled.append((sub, enabled))
    kc = _KC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        await c.patch(f"/v1/admin/users/{u['id']}", json={"status": "disabled"})
    assert kc.enabled == [("sub-2", False)]


async def test_delete_removes_kc_user(db_conn):
    _, p = await _admin(db_conn)
    from server.auth.users import enroll_linked_user
    u = await enroll_linked_user(db_conn, iss="i", sub="sub-3", email="g@x.io",
                                 capabilities=["reader"], status="active")
    kc = _FakeKC()
    async with _client(_app_kc(db_conn, p, kc)) as c:
        r = await c.delete(f"/v1/admin/users/{u['id']}")
    assert r.status_code == 204 and kc.deleted == ["sub-3"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_admin_router.py -k "capabilities or disable or delete_removes" -v`
Expected: FAIL — PATCH ignores capabilities; status/delete don't touch KC.

- [ ] **Step 3: Implement in `admin.py`**

Extend `UserPatchIn` with `capabilities: list[str] | None = None`. Add `kc=Depends(deps.get_kc_admin)` to `patch_user` and `delete_user`.

In `patch_user`, after the existing `status`/`role`/`budget` handling, and change status handling to propagate:

```python
    target = await users.get_user(conn, user_id)  # need oidc_sub for KC calls
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    sub = target["oidc_sub"]

    if "status" in data:
        if data["status"] not in ("active", "pending", "disabled"):
            raise HTTPException(status_code=422, detail="bad status")
        await users.set_status(conn, user_id, data["status"])
        if kc is not None and sub:
            try:
                await kc.set_enabled(sub, data["status"] == "active")
            except Exception:
                logger.warning("KC set_enabled failed for %s", sub)
    if "capabilities" in data:
        caps = sorted(set(data["capabilities"]))
        if not set(caps).issubset(KNOWN_CAPABILITIES):
            raise HTTPException(status_code=422, detail="unknown capability")
        current = set(target["capabilities"])
        if kc is not None and sub:
            try:
                await kc.assign_realm_roles(sub, sorted(set(caps) - current))
                await kc.remove_realm_roles(sub, sorted(current - set(caps)))
            except Exception:
                logger.warning("KC role reconcile failed for %s", sub)
        await users.set_capabilities(conn, user_id, caps)
    # (existing role/budget branches unchanged)
```

> Keep the existing `role`/`inference_daily_token_budget` branches. Note `set_capabilities` already updates the legacy `role` column, so an explicit `role` PATCH remains supported for back-compat but capabilities are the real gate.

In `delete_user`, after `await users.delete_user(conn, user_id)` (the app-row delete) and before the PDF unlink loop returns, add KC deletion using the `target` already fetched:

```python
    if kc is not None and target["oidc_sub"]:
        try:
            await kc.delete_user(target["oidc_sub"])
        except Exception:
            logger.warning("KC delete failed for %s (app row already removed)",
                           target["oidc_sub"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_admin_router.py server/tests/test_admin_lifecycle.py -v`
Expected: PASS (all existing lifecycle rails still hold — they run with `kc=None` via the default override or none).

> If `test_admin_lifecycle.py`'s existing app builder doesn't override `get_kc_admin`, add `app.dependency_overrides[deps.get_kc_admin] = lambda: None` there so those tests exercise the degraded path deterministically.

- [ ] **Step 5: Commit**

```bash
git add server/routers/admin.py server/tests/test_admin_router.py server/tests/test_admin_lifecycle.py
git commit -m "feat(admin): propagate disable/delete to Keycloak; capability PATCH reconciles roles"
```

---

### Task 9: Route enforcement — `require_capability` on feature routes

**Files:**
- Modify: `server/routers/docs.py`, `server/routers/inference.py`, `server/routers/chat_sessions.py`, `server/routers/tools.py`, `server/endpoints.py`
- Test: update `server/tests/test_docs_authz.py`, `server/tests/test_chat_sessions_authz.py` (+ any tools/tts tests) to give principals capabilities; add a positive/negative gate test

**Interfaces:**
- Consumes: `deps.require_capability` (Task 3).

- [ ] **Step 1: Write the failing test (new gate behavior)**

```python
# server/tests/test_capability_enforcement.py
import httpx, pytest
from httpx import ASGITransport
from fastapi import FastAPI
from server.auth import deps
from server.routers import docs as docs_router

pytestmark = pytest.mark.asyncio


def _app(db_conn, principal):
    app = FastAPI()
    async def _conn(): yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(docs_router.router)
    return app


async def test_docs_list_requires_reader_capability(db_conn):
    # Pick any GET route on the docs router that returns 200 for an owner; here
    # we assert the capability gate fires BEFORE ownership logic.
    p = deps.Principal(user_id="u", email="e@x.io", role="member", capabilities=frozenset())
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        # a representative docs GET (adjust path to a real one, e.g. a status route)
        r = await c.get("/v1/docs/does-not-exist/status")
        assert r.status_code == 403
        assert r.json()["detail"]["capability"] == "reader"
```

> Adjust the path to a real docs GET route. The assertion is that a capability-less principal gets `403 missing_capability` rather than reaching ownership (which would 404).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_capability_enforcement.py -v`
Expected: FAIL — route still returns 404/200 (no capability gate).

- [ ] **Step 3: Swap the dependency on each feature route**

Mechanical change: in every route handler in the files below, replace the principal dependency
`Depends(deps.get_current_user)` with the capability gate (it also returns the `Principal`, so the
handler body is unchanged):

- `server/routers/docs.py` → `Depends(deps.require_capability("reader"))`
- `server/endpoints.py` (`/v1/synthesize`, `/v1/batch_synthesize`; leave `/v1/health` open) → `require_capability("reader")`
- `server/routers/inference.py` → `require_capability("chat")`
- `server/routers/chat_sessions.py` → `require_capability("chat")`
- `server/routers/tools.py` → `require_capability("chat")`

- [ ] **Step 4: Update existing authz tests to grant capabilities**

In `server/tests/test_docs_authz.py`, every `deps.Principal(...)` construction gains
`capabilities=frozenset({"reader"})`. In `server/tests/test_chat_sessions_authz.py` and any
tools/tts test, add `capabilities=frozenset({"chat"})` (or `{"reader"}` for tts). This keeps the
ownership tests testing ownership (they now hold the capability, so the gate passes and the 404/200
ownership assertions still exercise the same code).

- [ ] **Step 5: Run the full backend suite**

Run: `.venv/bin/python -m pytest server/tests/ -q`
Expected: PASS (all previously-green tests + new gate test).

- [ ] **Step 6: Commit**

```bash
git add server/routers/docs.py server/routers/inference.py server/routers/chat_sessions.py server/routers/tools.py server/endpoints.py server/tests/
git commit -m "feat(authz): enforce reader/chat capabilities on feature routes (deny-by-default)"
```

---

### Task 10: `useAuth` exposes capabilities + status (SPA)

**Files:**
- Modify: `src/hooks/useAuth.js`
- Test: `src/hooks/useAuth.test.jsx` (extend)

**Interfaces:**
- Produces: `useAuth(...).user` includes `capabilities: string[]`; a helper `hasCapability(cap)` (or expose `user.capabilities` and let callers check).

- [ ] **Step 1: Write the failing test**

```jsx
// append to src/hooks/useAuth.test.jsx — follow the file's existing renderHook + fetch-mock setup
it('exposes capabilities from /v1/auth/me', async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ id: 'u', email: 'e@x.io', role: 'member',
      capabilities: ['reader'] }), { status: 200 }));
  const { result } = renderHook(() => useAuth('', ''));
  await waitFor(() => expect(result.current.state).toBe('active'));
  expect(result.current.user.capabilities).toEqual(['reader']);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- useAuth`
Expected: FAIL — `capabilities` undefined on `user`.

- [ ] **Step 3: Implement**

In `src/hooks/useAuth.js`, where the `/v1/auth/me` JSON is stored into `user`, ensure the whole
body (including `capabilities`) is kept, and default it: when setting user state, use
`{ ...body, capabilities: body.capabilities ?? [] }`.

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- useAuth`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hooks/useAuth.js src/hooks/useAuth.test.jsx
git commit -m "feat(spa): useAuth exposes capabilities from /v1/auth/me"
```

---

### Task 11: AuthGate — "access not yet granted" screen

**Files:**
- Modify: `src/components/auth/AuthGate.jsx`
- Test: `src/components/auth/AuthGate.test.jsx` (create if absent; else extend)

**Interfaces:**
- Consumes: `user.capabilities`.
- Produces: `AuthGate` renders a **NoAccessScreen** when `state === 'active'` and `user.capabilities.length === 0`, instead of `children`.

- [ ] **Step 1: Write the failing test**

```jsx
// src/components/auth/AuthGate.test.jsx
import { render, screen } from '@testing-library/react';
import { AuthGate } from './AuthGate';

it('shows access-not-granted when active but no capabilities', () => {
  render(
    <AuthGate state="active" user={{ capabilities: [] }}
              onLogin={() => {}} onLogout={() => {}} onRetry={() => {}}>
      <div>APP</div>
    </AuthGate>);
  expect(screen.queryByText('APP')).toBeNull();
  expect(screen.getByText(/access/i)).toBeInTheDocument();
});

it('renders children when active with a capability', () => {
  render(
    <AuthGate state="active" user={{ capabilities: ['reader'] }}
              onLogin={() => {}} onLogout={() => {}} onRetry={() => {}}>
      <div>APP</div>
    </AuthGate>);
  expect(screen.getByText('APP')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- AuthGate`
Expected: FAIL — children render even with empty capabilities; no access screen.

- [ ] **Step 3: Implement**

`AuthGate` now takes a `user` prop. When `state === 'active'`:
```jsx
if (state === 'active') {
  if (!user?.capabilities?.length) {
    return <NoAccessScreen onLogout={onLogout} onRetry={onRetry} />;
  }
  return children;
}
```
Add a `NoAccessScreen` (mirror the existing PendingScreen markup): a short "Your account is active but has no access yet — ask an administrator to grant Reader or Chat," with Retry + Log out buttons. Update the `App.jsx` call site to pass `user={auth.user}`.

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- AuthGate`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/auth/AuthGate.jsx src/components/auth/AuthGate.test.jsx src/App.jsx
git commit -m "feat(spa): AuthGate shows 'access not yet granted' for active users with no capabilities"
```

---

### Task 12: View gating — reader/chat/admin respect capabilities

**Files:**
- Modify: `src/App.jsx` (view switcher + `viewMode` coercion), `src/utils/apiFetch.js` (403 handler seam)
- Test: `src/App.test.jsx` or a focused view-switch test (follow existing App test patterns)

**Interfaces:**
- Consumes: `auth.user.capabilities`.

- [ ] **Step 1: Write the failing test**

```jsx
// focused test — a reader-only user must not see the Chat switch
// (adapt selectors to the real view-switcher markup in App.jsx / Sidebar)
it('hides the chat view for a reader-only user', async () => {
  // render App with useAuth mocked to an active reader-only user;
  // assert the chat toggle/button is absent and the admin entry is absent
});
```

> The executor should read `App.jsx`'s current view-switcher and write concrete selectors. The behavior contract: `reader` cap → reader view available; `chat` cap → chat view available; `admin` cap → admin entry available; a persisted `viewMode` not permitted by caps is coerced to the first permitted view; none permitted → AuthGate's NoAccessScreen (Task 11) already covers it.

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- App`
Expected: FAIL.

- [ ] **Step 3: Implement**

- Compute `const caps = auth.user?.capabilities ?? []`.
- Render the reader view only if `caps.includes('reader')`; the chat view only if `caps.includes('chat')`; the admin entry only if `caps.includes('admin')`.
- On mount / when caps change, coerce `viewMode`: if the current `viewMode` isn't permitted, set it to the first permitted one (`reader` → `chat` → `admin` order).
- In `src/utils/apiFetch.js`, extend the existing unauthorized seam with an optional forbidden handler: on a `403` whose JSON `detail.error === 'missing_capability'`, invoke a module-level `_onForbidden` (registered by `useAuth` to trigger a `/me` re-probe). Keep it best-effort (`res.clone().json().catch(...)`).

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- App apiFetch`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/App.jsx src/utils/apiFetch.js src/App.test.jsx
git commit -m "feat(spa): gate reader/chat/admin views on capabilities; re-probe on 403 missing_capability"
```

---

### Task 13: Admin console — capability editor + enroll capabilities + temp password

**Files:**
- Modify: `src/components/admin/AdminConsole.jsx`
- Test: `src/components/admin/AdminConsole.test.jsx` (extend)

**Interfaces:**
- Consumes: `PATCH /v1/admin/users/{id}` with `{capabilities}`; `POST /v1/admin/users` returning `{user, onboarding, temp_password?}`.

- [ ] **Step 1: Write the failing tests**

```jsx
// append to src/components/admin/AdminConsole.test.jsx
it('PATCHes capabilities when a capability checkbox is toggled', async () => {
  // render AdminConsole with a mocked apiFetch returning one user with
  // capabilities: ['reader']; click the "chat" checkbox on that row;
  // assert apiFetch called PATCH /v1/admin/users/<id> with body.capabilities
  // containing reader and chat.
});

it('shows the temp password once after enroll', async () => {
  // mock POST /v1/admin/users → { user, onboarding: 'temp_password', temp_password: 'abc' };
  // submit the enroll form; assert 'abc' is shown and an onboarding hint is visible.
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- AdminConsole`
Expected: FAIL — no capability checkboxes; temp password not surfaced.

- [ ] **Step 3: Implement**

- Replace the per-row admin/member toggle (`Make admin`/`Make member`) with three capability
  checkboxes (`reader`, `chat`, `admin`) reflecting `u.capabilities`; toggling one issues
  `patchUser(u.id, { capabilities: nextCaps })`. (Keep self-row guards: don't let an admin remove
  their own `admin` — hide/disable that checkbox on `isSelf`.)
- Enroll form: add the same three checkboxes; include `capabilities` in the POST body; on success,
  if `onboarding === 'temp_password'`, show `temp_password` in the existing `enrollNote` (kind
  `ok`) with copy text "share this once"; if `email`, note "invite emailed"; if `manual`, note
  "create this user in Keycloak — links on first verified-email login."
- The list-row role/status label can show effective capabilities instead of `role`.

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- AdminConsole`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/admin/AdminConsole.jsx src/components/admin/AdminConsole.test.jsx
git commit -m "feat(admin-ui): capability editor + enroll capabilities + one-time temp password"
```

---

### Task 14: Realm export, env, and docs

**Files:**
- Modify: `deploy/keycloak/realm-export.json`, `.env.example`, `docs/IDENTITY_AND_ROLES.md`, `docs/README.md` / deploy guide
- Test: none (config/docs) — validated by the manual verification in Task 15

- [ ] **Step 1: Realm export additions**

- Add realm roles `reader`, `chat`, `admin`.
- Add a confidential client **`natural-reader-admin`**: `serviceAccountsEnabled: true`,
  `standardFlowEnabled: false`, `implicitFlowEnabled: false`, `publicClient: false`, with a known
  `secret`; grant its service-account user the `realm-management` client roles `manage-users`,
  `view-realm`, `query-users`.
- On the browser-facing `natural-reader` client, add a **realm-roles protocol mapper** with
  `id.token.claim: "true"` (and `access.token.claim: "true"`) so `realm_access.roles` reaches the
  ID token the app parses.
- No default roles (new users get nothing).

- [ ] **Step 2: `.env.example`**

Add under the auth section:
```
# Keycloak Admin REST service account (enables full user provisioning + role
# management from the app). Unset = app-only degraded mode (enroll pre-provisions
# an app row that links on first login).
# KC_ADMIN_CLIENT_ID=natural-reader-admin
# KC_ADMIN_CLIENT_SECRET=change-me
```

- [ ] **Step 3: Docs**

Rewrite the "Where roles actually live" section of `docs/IDENTITY_AND_ROLES.md`: roles now **do**
live in Keycloak (realm roles `reader`/`chat`/`admin`), mirrored to `users.capabilities`,
enforced by `require_capability`; enroll creates the KC user; the extension PAT user needs the
`reader` capability. Note the documented group-vs-direct limitation from the spec.

- [ ] **Step 4: Commit**

```bash
git add deploy/keycloak/realm-export.json .env.example docs/IDENTITY_AND_ROLES.md docs/README.md
git commit -m "chore(deploy): realm roles + admin service-account client + realm-roles mapper; docs"
```

---

### Task 15: Manual end-to-end verification (no code)

**Not a TDD task** — a scripted manual pass on the live rig. Record results in `HANDOVER.md`.

- [ ] **Step 1:** Bring up Postgres + Keycloak: `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres keycloak`. Confirm the realm imports the new roles + `natural-reader-admin` client (`http://localhost:18080/admin`).
- [ ] **Step 2:** Set `KC_ADMIN_CLIENT_ID/_SECRET` + OIDC env; start backend + `npm run dev`.
- [ ] **Step 3:** First login (bootstrap): confirm the founder becomes admin and Keycloak shows the `admin`/`reader`/`chat` realm roles assigned to their user.
- [ ] **Step 4:** As admin, enroll a user with only `reader`; confirm (SMTP off) a temp password is shown once and a Keycloak user exists; log in as them in a private window → reader view only, no chat, "access" screen never (they have reader).
- [ ] **Step 5:** Enroll a user with **no** capabilities; log in → "access not yet granted" screen. Grant `chat` from the console → confirm the Keycloak realm role appears and the next request/refresh unlocks chat.
- [ ] **Step 6:** Disable then hard-delete a user; confirm the Keycloak user is disabled/removed accordingly. Confirm all rails (self / seed-admin / last-active-admin) still 409.
- [ ] **Step 7:** Update `HANDOVER.md` with the outcome and any deviations.

---

## Self-Review notes (author)

- **Spec coverage:** admin client (T4/T5) · capability model + storage (T2/T3) · enforcement (T9) · login sync + bootstrap (T6) · enroll/onboarding (T7) · disable/delete/capability edit (T8) · `/me` (T6) · SPA gating + screens + admin UI (T10–T13) · realm/env/docs (T14) · degraded fallback (T4 `get_kc_admin→None`, T7/T8 branches) · migration + backfill (T2). All spec sections map to a task.
- **Known follow-through for the executor:** several frontend tasks (T12/T13) require reading the current `App.jsx` / `AdminConsole.jsx` markup to write concrete selectors — the behavior contract is specified; the exact DOM assertions are finalized at implementation. The two `_FakeKC` helpers (T7/T8) are the single source for router-level Keycloak fakes; reuse, don't fork.
