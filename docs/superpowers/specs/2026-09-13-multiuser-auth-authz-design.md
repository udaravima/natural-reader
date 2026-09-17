# Multi-user Auth & Authz — Design Spec (sub-projects B + C)

_Date: 2026-09-13 · Branch target: `feat/multiuser-auth` (off `feat/security-hardening`) · Status: draft for review_

Part of the multi-user/hosted overhaul. Sub-project **A** (vuln hardening) is
done (`21e6e39`). This spec covers **B** (authentication) and **C**
(authorization), which are tightly coupled and ship as one cycle. Sub-projects
**E** (model-router gateway) and **D** (containerization) are separate specs and
out of scope here, though this design leaves the seams they need.

---

## 1. Goals / non-goals

**Goals**
- Turn a single-tenant app into a multi-user one: every chat session and
  document is owned by a user; users see only their own.
- Authenticate via **OIDC** (Keycloak as the reference IdP, but provider-agnostic
  through discovery). The app is a Relying Party; it stores no passwords.
- Preserve the existing single-user's data by assigning it to a bootstrap admin.
- Keep the day-to-day UX frictionless: log in once, stay logged in; the
  extension and scripts authenticate without a browser.
- Deny-by-default on every data route; a user must never learn that another
  user's resource exists.

**Non-goals (this cycle)**
- Provider/model routing, per-user model entitlements, API-key storage → **E**.
- Container/proxy/secret-injection topology → **D** (this spec only names the
  secrets it needs).
- Rate-limiting / per-user quotas on TTS/LLM compute → tracked, deferred.
- Refactoring the extension's fetch path → deferred (see §11); auth is additive
  and does not break the extension in this cycle.

---

## 2. Architecture overview

```
                 ┌─────────── Reverse proxy (D) : TLS, same-origin routing ──────────┐
  Browser SPA ──►│  /            → static SPA                                          │
                 │  /v1/*, /api/* → FastAPI backend                                    │
  Extension  ──► │  (cross-origin; uses Bearer PAT, not cookies)                       │
                 └───────────────────────────────────────────────────────────────────┘
                                        │
                                   FastAPI backend
        ┌───────────────────────────────┼─────────────────────────────────┐
        │  Auth router /v1/auth/*        │  get_current_user dependency     │
        │  - OIDC Auth Code + PKCE       │  - Bearer PAT  OR  session cookie │
        │  - JIT provision local user    │  - loads Principal(user_id, role) │
        │  - issue server-side session   │  - status must be 'active'        │
        └───────────────────────────────┴─────────────────────────────────┘
                                        │
                              Postgres: users, sessions,
                              personal_access_tokens,
                              documents.user_id, chat_sessions.user_id
                                        │
                              OIDC IdP (Keycloak) — identity only
```

**Two credential types, by design:**
- **Session cookie** for the SPA (same-origin behind the proxy). App-issued,
  server-side, revocable; `httpOnly; Secure; SameSite=Lax`.
- **Personal Access Token (PAT)** for the extension and programmatic clients,
  sent as `Authorization: Bearer …`. Cross-origin clients can't ride cookies.

The app issues its **own** session after the OIDC exchange rather than handing a
raw Keycloak JWT to the browser: revocation is instant (admin disables a user →
their sessions die), and no access token sits in JS where XSS could take it.

---

## 3. Data model

### Migration 005 — identity + ownership (self-contained, no env)

```sql
-- users: local mirror of OIDC identities + app-level role/status.
CREATE TABLE IF NOT EXISTS users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    oidc_iss     TEXT,                    -- issuer; NULL for the seed row
    oidc_sub     TEXT,                    -- subject; NULL until first login links it
    email        TEXT UNIQUE NOT NULL,
    display_name TEXT,
    role         TEXT NOT NULL DEFAULT 'member'
                     CHECK (role IN ('admin','member')),
    status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('active','pending','disabled')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (oidc_iss, oidc_sub)           -- sub is unique per issuer
);

-- Fixed-UUID bootstrap admin. Owns all pre-existing data. email is a sentinel
-- that startup code may rewrite to BOOTSTRAP_ADMIN_EMAIL so the real admin's
-- first OIDC login links to this row (and inherits the data).
INSERT INTO users (id, email, role, status)
VALUES ('00000000-0000-0000-0000-000000000001', 'admin@localhost', 'admin', 'active')
ON CONFLICT (id) DO NOTHING;

-- Ownership columns (nullable during backfill, then NOT NULL).
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

Children (`doc_chunks`, `doc_pages` → `documents`; `chat_messages`,
`chat_events` → `chat_sessions`) get **no** `user_id` — they inherit ownership
through their parent (`doc_id` / `session_id`) and are always reached via a
parent that's already authorized. Enforced by query, not schema. Pins are a
`pins JSONB` column on `chat_sessions` (migration 004), so they're covered by
session ownership with no extra work.

### Migration 006 — credentials

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,        -- sha256(cookie token); never store the raw token
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);

CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,    -- sha256(shown-once token)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ              -- NULL = no expiry
);
CREATE INDEX IF NOT EXISTS pat_user_idx ON personal_access_tokens(user_id);

INSERT INTO schema_migrations(version) VALUES (6) ON CONFLICT DO NOTHING;
```

Session ids and PATs are high-entropy random bearer secrets, so **sha256 at
rest** (no salt/argon2 needed — those defend low-entropy passwords). The raw
value is shown once and lives only in the client.

> `gen_random_uuid()` is built into Postgres 13+; the compose image is pg16. ✓

### Startup bootstrap (env-aware, runs after migrations)

In `db.py` after `_run_migrations()`, add `bootstrap_admin()`:
- If `BOOTSTRAP_ADMIN_EMAIL` is set, `UPDATE users SET email = :email WHERE
  id = <seed uuid> AND oidc_sub IS NULL`. Idempotent; only touches the unlinked
  seed row.
- Log the current bootstrap email so the operator knows which OIDC identity
  inherits existing data.

---

## 4. Authentication flow (OIDC Authorization Code + PKCE)

Library: **Authlib** (`authlib`) `starlette_client.OAuth`, which handles
discovery, PKCE, `state`/`nonce`, JWKS signature validation, and token exchange.
Transient flow state (state/nonce/verifier) rides Starlette `SessionMiddleware`
(a signed cookie keyed by `SESSION_SECRET`), separate from our app session.

Endpoints (`/v1/auth`, new router):

| Route | Purpose |
|---|---|
| `GET /v1/auth/login?next=/` | Build authorize URL (PKCE), redirect to Keycloak. `next` is validated to a same-site path to prevent open redirect. |
| `GET /v1/auth/callback` | Exchange `code`+verifier for tokens; validate ID token (iss/aud/exp/nonce/signature); resolve/JIT-provision user; issue session cookie; redirect to `next`. |
| `POST /v1/auth/logout` | Delete the server-side session row, clear the cookie. Optionally hit the IdP end-session endpoint. |
| `GET /v1/auth/me` | Return `{id, email, display_name, role, status}` for the current principal, or 401. The SPA calls this on load to decide logged-in vs login screen. |

**JIT provisioning + first-user-admin** (in the callback), given validated
`iss`, `sub`, `email`:
1. Look up by `(oidc_iss, oidc_sub)` → found: use it (refresh email/display_name).
2. Else look up by `email`:
   - Found + `oidc_sub IS NULL` (the seed/pre-provisioned row) → **link**: set
     `oidc_iss`, `oidc_sub`. This is how the bootstrap admin claims their identity
     and inherits existing data.
   - Found + already linked to a different sub → 409 (email collision across
     identities; rare, single-IdP).
3. Else (brand-new identity):
   - If the **only** user is the still-unlinked seed admin, link this login to it
     → **first real user becomes admin**. (Race note below.)
   - Otherwise create `role=member, status=pending`. They authenticate but every
     data route returns 403 until an admin activates them — the app-side invite
     gate, independent of Keycloak's own registration policy.

Then create a session and redirect. A `pending`/`disabled` user still gets a
session but `get_current_user` rejects them at 403 with a clear message, so the
SPA can show "awaiting approval".

> **First-user race:** on a fresh install reachable by multiple Keycloak
> identities, whoever logs in first becomes admin. Setting
> `BOOTSTRAP_ADMIN_EMAIL` pre-designates the admin by email and removes the race.
> Documented as the recommended setup step.

---

## 5. Sessions & cookies

- Cookie name `nr_session`; value = 32-byte URL-safe random token. DB stores
  `sha256(token)` as `sessions.id`.
- Attributes: `HttpOnly`, `Secure` (togglable via `COOKIE_SECURE=false` for local
  http dev), `SameSite=Lax`, `Path=/`, `Max-Age = SESSION_TTL_HOURS` (default
  168h / 7 days). Optional `Domain=COOKIE_DOMAIN`.
- Sliding refresh: on each authenticated request, bump `last_seen_at`; extend
  `expires_at` lazily (e.g. if <½ TTL remains). Cheap single UPDATE.
- **CSRF:** `SameSite=Lax` already blocks the cookie from cross-site
  non-GET requests, which is every state-changing route (POST/PUT/PATCH/DELETE).
  As belt-and-suspenders, mutating routes require `Content-Type: application/json`
  (or an `X-Requested-With` header the SPA sends) — a plain cross-site form POST
  can't set those. No separate CSRF token needed. Login uses the OIDC `state`
  param.

---

## 6. Personal Access Tokens

- `POST /v1/auth/tokens` (authed) → create `{name, expires_at?}`, returns the raw
  token **once** (`nrp_<43 url-safe chars>`); only its hash is stored.
- `GET /v1/auth/tokens` → list own tokens (id, name, created, last_used, expires;
  never the value).
- `DELETE /v1/auth/tokens/{id}` → revoke.
- Recognized by `get_current_user` via `Authorization: Bearer nrp_…`: hash,
  look up, check not expired, bump `last_used_at`, resolve to its user.
- The extension gains an optional token field beside its existing `baseUrl`
  setting; it sends the Bearer header on `/v1/health` and `/v1/batch_synthesize`.

---

## 7. Authorization (sub-project C)

### Principal resolution — one dependency, used everywhere

`get_current_user(request) -> Principal` (FastAPI dependency):
1. `Authorization: Bearer …` present → resolve PAT → user.
2. Else `nr_session` cookie → resolve session → user.
3. No credential → **401**. User `status != 'active'` → **403**.
4. Returns `Principal(user_id, role, email)`.

`require_admin` wraps it and 403s non-admins.

### Ownership enforcement

- **List routes** (`GET /v1/chat/sessions`) → add `WHERE user_id = :me`.
- **Item routes** (everything with `{doc_id}` / `{session_id}`) → the row's
  `user_id` must equal the principal. Mismatch or missing → **404** (not 403),
  so a user can't probe whether another's id exists. Implement as a small helper:
  `assert_owns_doc(conn, doc_id, me)` / `assert_owns_session(...)` that fetches
  `user_id` and raises 404 unless it matches. Called at the top of each handler,
  before any work.
- **Create routes** (`POST /v1/docs`, `PUT /v1/chat/sessions/{id}`) → stamp
  `user_id = me` on insert. On upsert of an existing id owned by someone else →
  404.
- Children are reached only through an authorized parent, so `doc_chunks` /
  `chat_messages` / etc. queries already scope by `doc_id`/`session_id` that was
  just authorized.

### Route-by-route impact

| Route | Change |
|---|---|
| `POST /v1/docs` | stamp `user_id`; upsert only own row |
| `GET/DELETE /v1/docs/{doc_id}`, `/chunks`, `/index`, `/search`, `/pdf`, `/convert`, `/markdown` (10 routes) | `assert_owns_doc` first |
| `GET /v1/chat/sessions` | filter by `user_id` |
| `GET/PUT/PATCH/DELETE /v1/chat/sessions/{id}` | `assert_owns_session`; PUT stamps owner on create |
| `POST /v1/tools/web_search` | require active user (no ownership) |
| `POST /v1/synthesize`, `/v1/batch_synthesize` | require active user or PAT |
| `GET /v1/health` | stays open (liveness; no data) |
| `/api/*` (Ollama passthrough) | see §9 — proxied & authed once E lands; interim: require auth at the proxy |

Admin (`role=admin`) manages users but does **not** read others' data by
default: new admin routes `GET /v1/admin/users`, `PATCH /v1/admin/users/{id}`
(activate/disable/promote). No cross-user data access.

---

## 8. Frontend (SPA) changes

- On load, call `GET /v1/auth/me`. `401` → show a login screen with a "Sign in"
  button → `GET /v1/auth/login?next=<current path>`. `403 pending` → "awaiting
  approval" screen. `200` → app as today, with the user shown in the sidebar +
  a logout action.
- All existing `fetch`es already go through `buildApiUrl`; add
  `credentials: 'include'` so the session cookie rides cross-origin in dev
  (same-origin in prod needs it too for safety). Centralize in `sessionStore`
  and the chat/doc fetch helpers.
- On any `401` mid-session (expired), surface a re-login prompt rather than a
  silent failure.
- A small **Settings → Access tokens** panel: create/list/revoke PATs (for
  wiring the extension).
- No secrets in JS: the SPA never sees the OIDC client secret or tokens.

---

## 9. `/api/*` Ollama passthrough — interim note

Today the browser talks to Ollama directly (`/api/chat`, `/api/tags`). That path
is unauthenticated and moves server-side in **E** (the gateway). Until E lands,
B does **not** put app auth in front of `/api/*` (the SPA calls Ollama directly).
The proxy (D) can require auth for `/api/*` in the meantime. This is called out
so the gap is deliberate, not forgotten.

---

## 10. Config / env (new)

| Var | Meaning | Default |
|---|---|---|
| `OIDC_ISSUER` | Discovery base (e.g. `https://kc.example/realms/nr`) | — (required to enable auth) |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | RP credentials | — |
| `OIDC_REDIRECT_URL` | Callback URL registered in Keycloak | — |
| `SESSION_SECRET` | Signs the transient OIDC-flow cookie | — (required) |
| `SESSION_TTL_HOURS` | App session lifetime | `168` |
| `COOKIE_SECURE` | `Secure` flag on cookies | `true` |
| `COOKIE_DOMAIN` | Cookie domain | unset (host-only) |
| `BOOTSTRAP_ADMIN_EMAIL` | Pre-designate the admin identity | unset |
| `AUTH_ENABLED` | Master switch (see below) | `true` |

**`AUTH_ENABLED=false`** — developer convenience: skip OIDC and treat every
request as the bootstrap admin. **Refuses to start if `HOST != 127.0.0.1`** and
logs a loud warning, so it can never be the posture on an exposed box. Requires
adding `authlib` to `requirements.txt`.

When `AUTH_ENABLED=true` but OIDC vars are missing, the app starts but
`/v1/auth/login` 503s with a clear config error — TTS still serves (matches the
existing "DB down ≠ dead" philosophy).

---

## 11. Extension impact (deferred, non-breaking)

The extension keeps working unchanged in this cycle **because** TTS routes
accept a PAT and the CORS default stays permissive (sub-project A, Option A).
Once the operator pins `FRONTEND_ORIGIN`, they must also give the extension a
PAT and (properly) route its fetch through the background service worker — a
follow-up tracked with the extension work, not blocking B/C. See
`read-aloud-extension` memory.

---

## 12. Testing strategy (TDD)

Pytest, router-only apps + a test Postgres (the existing pattern), with auth
dependencies overridden via `app.dependency_overrides` where a full OIDC flow
isn't the unit under test.

- **Migration 005/006:** apply on a scratch DB; assert columns exist, existing
  rows backfilled to the seed admin, `NOT NULL` holds, seed row present.
- **JIT provisioning:** unit-test the resolve function for all 4 branches
  (sub-match, email-link, first-user-admin, new-pending).
- **Session lifecycle:** create → resolve → expire → revoke.
- **PAT:** create returns raw once; hash stored; resolve; expiry; revoke.
- **Principal resolution:** PAT path, cookie path, none→401, pending→403,
  disabled→403.
- **Ownership:** owner 200; non-owner **404** (not 403) on every item route;
  list returns only own rows; create stamps owner.
- **Admin routes:** member→403; admin can activate/disable/promote.
- **Regression:** the sub-project A security tests stay green.

---

## 13. Rollout / backward-compat

- Ships on `feat/multiuser-auth` (branched from `feat/security-hardening`).
- First run applies 005/006, seeds the bootstrap admin, backfills ownership.
- Operator sets OIDC + `SESSION_SECRET` + `BOOTSTRAP_ADMIN_EMAIL`, logs in as the
  admin, inherits all prior data.
- Local dev without Keycloak: `AUTH_ENABLED=false` (localhost-only).

---

## 14. Resolved decisions

Approved 2026-09-14; the body above reflects these.

1. **Session store → DB-backed** (revocable). Instant kill when an admin disables
   a user; worth the one table over a stateless signed cookie.
2. **Logout → local-only** for now (clear our session + cookie). RP-initiated
   single-logout to Keycloak is a later toggle, not this cycle.
3. **`AUTH_ENABLED=false` dev bypass → included**, hard-gated to `HOST=127.0.0.1`
   and loudly logged; the app refuses to start with the bypass on an exposed bind.
4. **Email uniqueness → single-IdP assumption accepted.** Revisit only when a
   second provider is added: key identity fully on `(iss, sub)` and drop the
   `email` UNIQUE constraint.
```
