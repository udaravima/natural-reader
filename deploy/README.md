# Deploy / local OIDC test rig

How to run the multi-user auth flow end to end against a real Keycloak, on your
own machine. This is a **local-dev** rig — the Keycloak container runs in
`start-dev` mode (no TLS, bootstrap admin creds). Production hardening
(external DB user, TLS, a real reverse proxy, dedicated Keycloak credentials)
is sub-project **D**.

> **Shortcut:** `./startup.sh up-with-dev-auth` automates steps 1–3 below — it
> starts the Postgres/Keycloak/SearXNG containers, creates `.env` on first run
> (the local realm values + a generated `SESSION_SECRET`), waits for the realm
> import, and runs the backend with those vars. The manual walkthrough
> explains what each step does and how to customize it. `./startup.sh up`
> (without the auth rig) runs the backend with `AUTH_ENABLED=false` instead.

Files here:

- `keycloak/realm-export.json` — a reproducible `natural-reader` realm: the
  browser-facing confidential client (PKCE, plus a realm-roles protocol
  mapper so `realm_access.roles` reaches the ID token), the realm roles
  `reader`/`chat`/`admin` (granted to **nobody** by default), one verified
  test user (`admin-user`), and a second confidential client
  **`natural-reader-admin`** (service accounts, no login flows) whose
  service-account user holds the `realm-management` roles the app's admin
  API needs — see [docs/IDENTITY_AND_ROLES.md](../docs/IDENTITY_AND_ROLES.md).
  Imported on first container start **only if the realm doesn't already
  exist**.
- `postgres/init/00-create-keycloak-schema.sql` — creates the `keycloak`
  schema Keycloak stores its realm in. Runs **only on a fresh Postgres data
  volume** (`docker-entrypoint-initdb.d` semantics). On an already-initialized
  volume it never runs — create the schema once by hand:
  `psql ... -c 'CREATE SCHEMA IF NOT EXISTS keycloak'`.
  Realm state persists across container recreation, so the users' OIDC `sub`
  UUIDs — and the app's `users.oidc_sub` links — stay stable. (With the old
  embedded H2 store, every recreation minted new `sub`s and returning users
  got 409 "email already linked to another identity".)
- `nginx/natural-reader.conf` — a **reference** nginx site (SPA + same-origin
  `/v1` proxy; NDJSON needs `proxy_buffering off`). You install it into your
  own nginx; nothing here edits system files.

## The one thing that matters: same origin

OIDC session cookies only work when the browser sees the **SPA and the backend
as one origin**. Two ways to get that:

- **Fast dev loop:** `npm run dev` (:5173) — `vite.config.js` proxies `/v1` and
  `/api` to the backend, so :5173 is same-origin. Use this while building UI.
- **Prod-like:** build the SPA and let nginx serve it and proxy the backend
  (one origin, e.g. :8080).

Keycloak is deliberately a **different** origin (:18080) — that's normal for an
IdP; only the SPA↔backend pair must match.

## 1. Bring up Postgres + Keycloak

```bash
# (snap-podman note: the `env -u XDG_DATA_HOME` prefix is required on this box)
env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres keycloak
```

Wait for Keycloak to import the realm (~20–40s the first time), then confirm the
discovery document is served — the issuer must read back exactly
`http://localhost:18080/...`:

```bash
curl -s http://localhost:18080/realms/natural-reader/.well-known/openid-configuration \
  | grep -o '"issuer":"[^"]*"'
# → "issuer":"http://localhost:18080/realms/natural-reader"
```

Keycloak admin console (to add a second user later): <http://localhost:18080/admin>
— fresh volumes bootstrap `admin` / `admin` (from `KC_BOOTSTRAP_ADMIN_*` in
`docker-compose.yml`); **change the password on first visit** — the bootstrap
value is not re-applied on later container starts, and your changed password
persists in the `keycloak` schema like everything else.

## 2. Start the backend with OIDC enabled

Set these (see `.env.example` for the full list), then run the backend:

```bash
export OIDC_ISSUER=http://localhost:18080/realms/natural-reader
export OIDC_CLIENT_ID=natural-reader
export OIDC_CLIENT_SECRET=natural-reader-dev-secret
export OIDC_REDIRECT_URL=http://localhost:5173/v1/auth/callback   # matches vite dev
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export COOKIE_SECURE=false                 # local http, no TLS
export BOOTSTRAP_ADMIN_EMAIL=admin@example.com
export HOST=127.0.0.1
# Optional — inference gateway tuning (see .env.example § Inference gateway):
# export INFERENCE_MODELS="llama3.2:3b,gemma3:4b"   # unset = allow all (dev)
# export INFERENCE_DAILY_TOKEN_BUDGET=100000        # unset/0 = unlimited
# Optional — Keycloak admin service account (matches realm-export.json's
# natural-reader-admin client). Without it, admin-console capability grants
# still take effect immediately for the running session, but don't survive
# the user's next Keycloak login — see docs/IDENTITY_AND_ROLES.md.
# export KC_ADMIN_CLIENT_ID=natural-reader-admin
# export KC_ADMIN_CLIENT_SECRET=natural-reader-admin-dev-secret
.venv/bin/python run.py
```

With auth on, chat goes through the authenticated gateway
(`/v1/inference/*`) using the session cookie — see
[docs/USER_GUIDE.md](../docs/USER_GUIDE.md) for the user-facing behavior and
`../README.md` § Inference Gateway for the operator view.

## 3. Start the SPA

```bash
npm run dev      # http://localhost:5173
```

## 4. Walk the two-user flow

The **first** person to log in becomes the admin **regardless of email** (the
backend claims the seed-admin row on first login, and is force-granted the
`admin`/`reader`/`chat` capabilities). So to see the *pending* screen you need
a **second** user.

**Capabilities are opt-in, not opt-out:** the realm defines `reader`/`chat`/
`admin` but grants none of them to anyone by default (`realm-export.json`).
Every new user — including this second one, once activated — starts with
**zero capabilities** and sees "your account is active but has no access
yet" until an admin explicitly grants some.

1. Open <http://localhost:5173>. You land on the **login screen** → **Sign in** →
   Keycloak. Log in as `admin-user` / `password` (email `admin@example.com`,
   already verified). You come back **authenticated as admin**; the app renders.
2. In the Keycloak admin console, add a second user (Users → Add user; set an
   email, mark **Email verified** on, and set a password under Credentials).
3. In a separate browser/profile, open the app and sign in as that second user.
   You get the **"awaiting approval"** screen (JIT-provisioned, `pending`,
   zero capabilities).
4. Back as the admin: click the **shield icon** (admin view) in the view
   switcher. The pending user is listed → click **Activate**, then check the
   **reader** and **chat** boxes in that row's capability strip (each
   checkbox PATCHes immediately). Skip this and the user stays "active but no
   access" after step 5.
5. As the second user, click **Refresh** on the pending screen → the app
   renders (no fresh Keycloak login needed — the running session picks up
   the new capabilities on its next request either way).
6. Open Sidebar → **Account** → create a **Personal Access Token**; copy it once
   (this is what the browser extension / scripts use as a `Bearer` token — it
   needs the owning user to hold `reader` to authenticate TTS calls).
7. **Log out** from the Account section → back to the login screen.

> The older per-user "Make admin/member" toggle still visible in the reader
> Sidebar's collapsible Admin block (`AdminPanel.jsx`) only writes the legacy
> `role` column — it does **not** grant a capability, so it no longer confers
> real admin access. Use the shield-icon admin view's capability checkboxes
> for anything that has to actually take effect. See
> [docs/IDENTITY_AND_ROLES.md](../docs/IDENTITY_AND_ROLES.md) § "Roles → UI".
>
> **Offboarding note (live-verified):** deleting a user in the Keycloak console
> does **not** remove them from the app — their account stays active with any
> tokens still working, and their email stays blocked for future Keycloak users
> (409 "email already linked to another identity"). To remove someone:
> **Disable them in the app's Admin section first** (that revokes sessions and
> tokens), then delete in Keycloak. Details: [docs/IDENTITY_AND_ROLES.md](../docs/IDENTITY_AND_ROLES.md).

## Prod-like variant (nginx)

Instead of `npm run dev`: `npm run build`, point `nginx/natural-reader.conf`'s
`root` at `dist/`, install it, and set `OIDC_REDIRECT_URL` to your nginx origin
(and add that origin to the realm's redirect URIs). Everything else is the same.

## Tear down

```bash
env -u XDG_DATA_HOME .venv/bin/podman-compose down     # add -v to wipe volumes
```
