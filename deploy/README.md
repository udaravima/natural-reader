# Deploy / local OIDC test rig

How to run the multi-user auth flow end to end against a real Keycloak, on your
own machine. This is a **local-dev** rig — the Keycloak container runs in
`start-dev` mode (embedded store, no TLS). Production hardening (external DB,
TLS, a real reverse proxy) is sub-project **D**.

> **Shortcut:** `./startup.sh up-with-dev-auth` automates steps 1–3 below — it
> starts the Postgres/Keycloak/SearXNG containers, creates `.env` on first run
> (the local realm values + a generated `SESSION_SECRET`), waits for the realm
> import, and runs the backend with those vars. The manual walkthrough
> explains what each step does and how to customize it. `./startup.sh up`
> (without the auth rig) runs the backend with `AUTH_ENABLED=false` instead.

Files here:

- `keycloak/realm-export.json` — a reproducible `natural-reader` realm (a
  confidential client with PKCE + one verified test user), imported on first
  container start.
- `nginx/natural-reader.conf` — a **reference** nginx site (SPA + same-origin
  `/v1`,`/api` proxy). You install it into your own nginx; nothing here edits
  system files.

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
— log in with `admin` / `admin`.

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
.venv/bin/python run.py
```

## 3. Start the SPA

```bash
npm run dev      # http://localhost:5173
```

## 4. Walk the two-user flow

The **first** person to log in becomes the admin **regardless of email** (the
backend claims the seed-admin row on first login). So to see the *pending*
screen you need a **second** user.

1. Open <http://localhost:5173>. You land on the **login screen** → **Sign in** →
   Keycloak. Log in as `admin-user` / `password` (email `admin@example.com`,
   already verified). You come back **authenticated as admin**; the app renders.
2. In the Keycloak admin console, add a second user (Users → Add user; set an
   email, mark **Email verified** on, and set a password under Credentials).
3. In a separate browser/profile, open the app and sign in as that second user.
   You get the **"awaiting approval"** screen (JIT-provisioned, `pending`).
4. Back as the admin: open the Sidebar → **Admin** section. The pending user is
   listed → click **Activate**.
5. As the second user, click **Refresh** on the pending screen → the app renders.
6. Open Sidebar → **Account** → create a **Personal Access Token**; copy it once
   (this is what the browser extension / scripts use as a `Bearer` token).
7. **Log out** from the Account section → back to the login screen.

## Prod-like variant (nginx)

Instead of `npm run dev`: `npm run build`, point `nginx/natural-reader.conf`'s
`root` at `dist/`, install it, and set `OIDC_REDIRECT_URL` to your nginx origin
(and add that origin to the realm's redirect URIs). Everything else is the same.

## Tear down

```bash
env -u XDG_DATA_HOME .venv/bin/podman-compose down     # add -v to wipe volumes
```
