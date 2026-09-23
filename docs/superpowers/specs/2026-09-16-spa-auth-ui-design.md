# SPA Auth UI + Local OIDC Test Rig — Design Spec

_Date: 2026-09-16 · Branch target: `feat/spa-auth-ui` (off `feat/security-hardening`) · Status: draft for review_

Part of the multi-user/hosted overhaul. Sub-projects **A** (vuln hardening,
`21e6e39`) and **B+C** (multi-user auth+authz backend, merged onto
`feat/security-hardening` at `c9b4949`) are done. That work left the app
**backend-secured but front-end-blind**: the SPA has no login, sends no
credentials, and only runs with `AUTH_ENABLED=false` on a loopback bind. This
spec wires the React SPA into the auth backend and stands up a reproducible
local OIDC environment (Keycloak) so the real flow can be exercised end to end.

Sub-projects **E** (model-router gateway) and **D** (full containerization /
secrets / TLS) remain separate. This spec pulls a *sliver* of D forward — a
reverse-proxy site config and a Keycloak container for local use — because the
OIDC flow cannot be tested without them.

---

## 1. Goals / non-goals

**Goals**
- A logged-out visitor sees a **login screen**; signing in runs the real OIDC
  Authorization-Code flow and returns them to the app, authenticated.
- A JIT-provisioned but not-yet-approved user sees an **"awaiting approval"**
  screen (not a dead login loop); a disabled user sees a distinct **"disabled"**
  screen. Both can log out.
- Every `/v1` request the SPA makes carries the session cookie; a 401 anywhere
  cleanly returns the user to the login screen.
- A user can mint, list, and revoke **Personal Access Tokens** from the UI, so
  the shipped read-aloud extension (and scripts) keep working under auth.
- An **admin** can list users and activate / disable / re-role them from the UI,
  so the first-user-admin + invite flow works without hand-editing Postgres.
- A developer can bring up Keycloak + the proxy locally with a couple of
  commands and a checked-in realm, and walk the whole flow.

**Non-goals (this cycle)**
- Wiring the **extension itself** to send a PAT — separate repo; this spec only
  produces the token in the UI. (Tracked in [[read-aloud-extension]].)
- Moving chat/embeddings LLM calls server-side, provider/model routing → **E**.
- Production Keycloak (external DB, TLS, HA), container images for the app/SPA,
  secret injection → **D**. The Keycloak container here is `start-dev`,
  local-only, explicitly not production.
- Silent token / session renewal. Session TTL (168 h) + re-login on expiry is
  enough; no refresh-token rotation.
- Any change to the `/api/*` path (browser → Ollama direct). Those calls do not
  hit our backend and need no credentials this cycle.

---

## 2. The load-bearing constraint: same-origin

OIDC session cookies only work when the browser sees the **SPA and the backend
as one origin**. The session cookie is set on the backend's origin during the
`/v1/auth/callback` redirect; if the SPA is served from a different origin, the
browser won't send that cookie back on `fetch`, and third-party-cookie blocking
makes it worse. Everything below follows from making SPA + backend same-origin:

- **Production / integration:** the already-running **system nginx** serves the
  built SPA and reverse-proxies `/v1` + `/api` to the backend — one origin.
- **Dev loop:** a **Vite dev proxy** does the same for `npm run dev`, so :5173
  is same-origin with the backend during UI iteration.
- **Keycloak is deliberately a *different* origin** (its own host/port). An IdP
  is expected to be separate; only the SPA↔backend pair must be same-origin. The
  full-page redirect out to Keycloak and back is standard for an OIDC RP.

PATs (Bearer header, no cookie) are the escape hatch for the one client that
*can't* be same-origin — the browser extension.

```
                    same origin (nginx :8080  OR  vite :5173)
  ┌───────────────────────────────────────────────────────────┐
  │  Browser SPA                                                │
  │    /                → static SPA (dist/) [nginx]            │
  │    /v1/*  ──proxy──►  backend :8000  (session cookie)       │
  │    /api/* ──proxy──►  Ollama  (unchanged, no cookie)        │
  └───────────────────────────────────────────────────────────┘
              │  full-page redirect (login) ▲ callback
              ▼                              │
      Keycloak container  http://localhost:18080/realms/natural-reader
                          (different origin — that's fine)

  Extension / scripts ──Authorization: Bearer nrp_…──► backend :8000
```

---

## 3. Backend refinement (small, TDD)

The only backend change. Today `get_current_user` raises
`HTTPException(403, detail="Account pending")` for a non-active user
([deps.py:53-54](../../../server/auth/deps.py#L53-L54)). The SPA must render *different*
screens for `pending` vs `disabled`, and branching on a substring of a prose
`detail` is fragile.

**Change:** raise the 403 with a structured detail:

```python
raise HTTPException(
    status_code=403,
    detail={"status": row["status"], "message": f"Account {row['status']}"},
)
```

FastAPI renders `{"detail": {"status": "pending", "message": "Account pending"}}`.
The SPA reads `err.detail.status`. Hot-path-safe (only the not-active branch),
backward-tolerant (existing consumers that ignore `detail` are unaffected).

`/v1/auth/me` for an **active** user is unchanged — a 200 already implies active,
and it already returns `role` (needed to decide whether to show the admin UI).

_Unit / test:_ extend `test_deps.py` — a request from a `pending` user asserts
`403` and `resp.json()["detail"]["status"] == "pending"`.

---

## 4. Frontend auth core

Three new, independently testable units.

### 4.1 `src/utils/apiFetch.js` — the credential seam
The retrofit hinges here. Today every `/v1` call is a bare `fetch()` with its own
options object; nothing sends credentials. `apiFetch(path, opts)`:
- builds the URL via the existing `buildApiUrl(apiHost, apiPort, path)`,
- always sets `credentials: 'include'`,
- passes through method/body/headers/signal,
- on **401**, invokes an injected `onUnauthorized()` (wired to flip global auth
  state → `anonymous`) and still returns/throws so the caller's error path runs.

Only `/v1` callers migrate to `apiFetch`. The `/api` (Ollama) calls stay bare.
`buildApiUrl` and the `apiHost/apiPort` settings are untouched — `apiFetch` is a
thin wrapper over them, so the "empty host = relative = same-origin behind proxy"
behavior is preserved.

### 4.2 `src/hooks/useAuth.js` — the state machine
On mount, `GET /v1/auth/me` and derive:

| Result | State | Meaning |
|--------|-------|---------|
| 200 `{id,email,role}` | `active` | render the app |
| 401 | `anonymous` | show LoginScreen |
| 403 `detail.status==="pending"` | `pending` | show PendingScreen |
| 403 `detail.status==="disabled"` | `disabled` | show DisabledScreen |
| network error | `error` | show a retry screen (backend down) |

Exposes `user {email, role}`, `state`, `login()` and `logout()`:
- `login()` → `window.location.assign(buildApiUrl(apiHost, apiPort, '/v1/auth/login?next=' + encodeURIComponent(location.pathname)))`. Full-page redirect — Authorization-Code flow must leave to the IdP.
- `logout()` → `apiFetch('/v1/auth/logout', {method:'POST'})`, then set state `anonymous`.

The hook also owns the `onUnauthorized` callback handed to `apiFetch`, so a 401
from *any* call collapses the app back to the login gate.

### 4.3 Gate in `App.jsx`
No router (matches today's single-component app). Wrap the render: `loading` →
spinner; `anonymous`/`pending`/`disabled`/`error` → the matching full-page
screen; `active` → the existing application tree, unchanged. A dev-mode note:
when `AUTH_ENABLED=false`, `/v1/auth/me` returns the seed admin (the backend's
loopback bypass), so the gate resolves to `active` and the app renders exactly as
it does today — zero friction for pure-frontend work.

---

## 5. UI surfaces

### 5.1 Gate screens (`src/components/auth/`)
- **LoginScreen** — product name, one "Sign in" button → `login()`.
- **PendingScreen** — "Your account is awaiting approval." + Refresh + Log out.
- **DisabledScreen** — "Your access has been disabled." + Log out.
- **AuthErrorScreen** — "Can't reach the server." + Retry. (Reuses the existing
  backend-health notion.)

### 5.2 Account section (`src/components/account/AccountPanel.jsx`)
There is **no settings modal** — Settings is a collapsible section inside the
left **Sidebar** ([Sidebar.jsx:60-72](../../../src/components/Sidebar.jsx#L60-L72),
with the `apiHost/apiPort` inputs; Ollama host/port live in `ChatSidebar.jsx`).
The Account panel is a **new collapsible sidebar section following that same
pattern** (a titled toggle that expands a body). Shows email + role, and a
**PAT panel**:
- list tokens (name, created, last-used, expiry) — `GET /v1/auth/tokens`,
- create (name + optional expiry) — `POST /v1/auth/tokens`; the raw token is
  shown **exactly once** in a copy-to-clipboard box with a "you won't see this
  again" note (the backend only returns it on create),
- revoke — `DELETE /v1/auth/tokens/{id}`.

### 5.3 Admin section (`src/components/admin/AdminPanel.jsx`)
Another collapsible sidebar section (same pattern), rendered **only when
`user.role === 'admin'`**:
- user table (email, role, status) — `GET /v1/admin/users`,
- activate / disable and member↔admin toggle — `PATCH /v1/admin/users/{id}`,
- guard rails: the current admin can't disable or demote **themselves** (avoid
  locking the last admin out); confirm before disabling anyone.

---

## 6. Data flow

**Login (happy path).** SPA gate = `anonymous` → user clicks Sign in →
full-page nav to `/v1/auth/login` (same-origin, proxied) → backend 302 →
Keycloak → user authenticates → Keycloak 302 → `/v1/auth/callback?code=…`
(same-origin, proxied) → backend exchanges code, JIT-provisions/looks up the
user, creates a DB session, sets the `nr_session` cookie, 303 → SPA → SPA
re-mounts, `GET /v1/auth/me` → 200 → gate = `active`.

**First user vs. invited user.** Provisioning has three branches
([users.py:68-113](../../../server/auth/users.py#L68-L113)): (1) known identity →
returned; (2) a pre-provisioned row whose email matches *and the OIDC email is
verified* → claimed; (3) brand-new identity → an **atomic claim of the still-
unlinked seed admin row** (`UPDATE … WHERE id=SEED AND oidc_sub IS NULL`).

The consequence, which the E2E path depends on: **the first person to ever log
in becomes the admin regardless of their email** — branch 3 claims (and
overwrites the email of) the seed row. `BOOTSTRAP_ADMIN_EMAIL` does *not* gate
this; it only pre-seeds the row's email so a specific **verified** email claims
it via branch 2, removing the "whoever logs in first wins" race. A user only
lands in `pending` when they are **not** the first — branch 3's conditional
UPDATE returns rowcount 0 and falls through to `INSERT … role='member',
status='pending'`. That pending user's callback still sets a session cookie, so
`/v1/auth/me` → 403 `pending` → PendingScreen; an admin flips them to `active`
in the Admin section and they hit Refresh → `active`.

**Session expiry / revocation.** Any `/v1` call returning 401 → `apiFetch`
fires `onUnauthorized` → gate = `anonymous`. No half-broken UI.

**PAT for the extension.** User opens Account → creates a token → copies the raw
`nrp_…` value once → pastes it into the extension's settings (separate repo).

---

## 7. Credential & CSRF posture

- Cookie stays `httpOnly` + `Secure` + `SameSite=Lax` (backend already sets
  this). `apiFetch` sends `credentials:'include'`.
- Same-origin (proxy / vite) → cookie flows. A cross-origin `apiHost` still
  works but requires `FRONTEND_ORIGIN` pinned so CORS returns the specific origin
  + `Allow-Credentials: true` (already implemented in `appconfig.py`).
- **CSRF:** all mutations are `POST`/`DELETE` via `fetch`. `SameSite=Lax` does
  not attach the cookie to cross-site `fetch` writes, so CSRF is mitigated
  without tokens. Logout is `POST` (not a GET a foreign page can trigger). PATs
  are Bearer-only — no ambient cookie, no CSRF surface.
- `login()`'s `next` param is already open-redirect-guarded server-side
  (`_safe_next` — same-site absolute paths only).

---

## 8. Local OIDC test rig

### 8.1 Keycloak container (non-default port)
Add a `keycloak` service to `docker-compose.yml`:
- image `quay.io/keycloak/keycloak:<pinned>`, command `start-dev --import-realm`,
- host port **`18080:8080`** (Keycloak's default 8080 is avoided, per the ask),
- bootstrap admin via `KC_BOOTSTRAP_ADMIN_USERNAME/PASSWORD` (console only),
- mounts `deploy/keycloak/realm-export.json` → `/opt/keycloak/data/import/`.
Uses Keycloak's embedded dev store — **local only, not production** (that's D).

### 8.2 Reproducible realm — `deploy/keycloak/realm-export.json`
Realm `natural-reader`; confidential client `natural-reader` (Authorization-Code
+ PKCE S256); valid redirect URIs `http://localhost:5173/v1/auth/callback` (Vite
dev) and one for the nginx origin (`http://<your-nginx-host>/v1/auth/callback` —
whatever `server_name`/port your existing nginx serves the app on; the spec uses
`localhost:8080` only as a stand-in); web origins for those hosts; one seed test
user with a **verified** email (so it can claim the bootstrap admin row).

### 8.3 System nginx site — `deploy/nginx/natural-reader.conf`
A **reference `server` block you install into your existing nginx** (this spec
does not touch system files). It serves the built SPA from `dist/` and proxies
`/v1/` + `/api/` to `127.0.0.1:8000`, forwarding `Host`, `X-Forwarded-*`, and
(for the future SSE/stream paths) disabling proxy buffering on `/v1`. Optionally
a second `server` block gives Keycloak a clean hostname → `127.0.0.1:18080`;
otherwise the browser hits Keycloak directly on :18080. `deploy/README.md`
documents install + the end-to-end walk-through.

### 8.4 Vite dev proxy — `vite.config.js`
```js
server: { proxy: {
  '/v1':  'http://localhost:8000',
  '/api': 'http://localhost:11434', // Ollama, matching today's default
} }
```
So `npm run dev` at :5173 is same-origin with the backend and cookies work in
the fast loop. (The `/api` proxy target mirrors the current browser→Ollama
default; unchanged behavior, just same-origin.)

### 8.5 Env (`.env.example` additions, all already-scaffolded keys)
Local values documented: `OIDC_ISSUER=http://localhost:18080/realms/natural-reader`,
`OIDC_CLIENT_ID=natural-reader`, `OIDC_CLIENT_SECRET=<from realm>`,
`OIDC_REDIRECT_URL=http://localhost:5173/v1/auth/callback` (dev) /
`…:8080/…` (nginx), `SESSION_SECRET=<generated>`, `COOKIE_SECURE=false`
(local http), `BOOTSTRAP_ADMIN_EMAIL=<test user email>`. Note the issuer must be
identical for browser and backend (both `localhost:18080`) or token validation
fails — a classic Keycloak footgun. Leave **`COOKIE_DOMAIN` unset** for
`localhost`; only set it (to the registrable domain) once the app is served from
a real hostname behind nginx — a `COOKIE_DOMAIN` that doesn't match the origin
silently drops the session cookie and the user never appears logged in.

---

## 9. File / unit inventory

**New**
- `src/utils/apiFetch.js` — credential seam (+ `apiFetch.test.js`)
- `src/hooks/useAuth.js` — auth state machine (+ `useAuth.test.js`)
- `src/components/auth/{LoginScreen,PendingScreen,DisabledScreen,AuthErrorScreen}.jsx`
- `src/components/account/AccountPanel.jsx` (+ test)
- `src/components/admin/AdminPanel.jsx` (+ test)
- `deploy/keycloak/realm-export.json`
- `deploy/nginx/natural-reader.conf`
- `deploy/README.md`

**Changed**
- `server/auth/deps.py` — structured 403 (+ `test_deps.py`)
- `src/App.jsx` — mount the gate; migrate its inline `/v1` fetches (around lines
  646, 776, 916, 959, plus the `/v1/health` probe) to `apiFetch`
- `src/components/Sidebar.jsx` — add the Account (and, for admins, Admin)
  collapsible sections alongside the existing Settings section
- `src/hooks/useTtsEngine.js`, `src/lib/uploadPdf.js`, `src/lib/sessionStore.js`,
  `src/lib/chatTools/*` — migrate `/v1` fetches to `apiFetch`
- `vite.config.js` — dev proxy
- `docker-compose.yml` — `keycloak` service
- `.env.example` — documented local OIDC values

---

## 10. Testing strategy

- **Backend (TDD):** structured-403 status in `test_deps.py`.
- **Frontend (vitest):** `apiFetch` (sends credentials; 401 → `onUnauthorized`);
  `useAuth` state machine (200/401/403-pending/403-disabled/error → correct
  state); `AccountPanel` (create shows raw token once; revoke calls DELETE);
  `AdminPanel` (activate calls PATCH with right body; self-disable guarded);
  gate rendering by state. Mock `fetch`; assert on behavior, not mocks.
- **E2E (manual, documented):** compose up postgres + keycloak; backend with the
  local env; `npm run dev` (or nginx + `npm run build`). The pending path needs
  **two Keycloak users** (per §6, the first-ever login always becomes admin):
  (1) sign in as the admin user → `active`/admin; (2) sign in (other browser /
  profile) as a second user → `pending` screen; (3) admin activates them in the
  Admin section; (4) second user hits Refresh → `active`; (5) create a PAT →
  copy once; (6) log out. Steps live in `deploy/README.md`.

---

## 11. Risks / open questions / follow-ups

- **Retrofit completeness.** Missing a `/v1` call site means one request without
  credentials → a spurious 401 → surprise logout. Mitigation: grep-audit every
  `/v1` string during implementation; the `apiFetch` 401 handler makes a missed
  one *loud* (visible logout) rather than silent.
- **Keycloak issuer URL.** Browser-facing and backend-facing issuer must match
  (§8.5). Documented; only bites if the backend is later containerized (→ D).
- **`COOKIE_SECURE=false` for local http.** Required without TLS locally; the
  spec calls it out so it never leaks into a real deploy (D provides TLS).
- **Admin self-lockout.** UI guard rails (§5.3) prevent the last admin disabling/
  demoting themselves; the backend could also enforce this — noted as a possible
  hardening, not required this cycle.
- **Follow-up:** the extension still needs a PAT input wired in its own repo
  before it works under `AUTH_ENABLED=true`.
```
