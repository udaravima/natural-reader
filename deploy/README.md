# Deploy / local OIDC test rig

How to run the multi-user auth flow end to end against a real Keycloak, on your
own machine. This is a **local-dev** rig — the Keycloak container runs in
`start-dev` mode (no TLS, bootstrap admin creds). Production hardening
(external DB user, TLS, a real reverse proxy, dedicated Keycloak credentials)
is covered in [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

**Deploying for real?** See [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) —
production behind nginx + TLS on real hostnames, with the two nginx sample vhosts
and the five config values that must agree, plus the traps (the biggest: editing
`keycloak/realm-export.json` does **not** change an already-imported live realm).

> **Shortcut:** `./startup.sh up-with-dev-auth` automates steps 1–3 below — it
> starts the Postgres/Keycloak/SearXNG containers, creates `.env` on first run
> (the local realm values + a generated `SESSION_SECRET`), waits for the realm
> import, and runs the backend container (your working tree) with those vars. The manual walkthrough
> explains what each step does and how to customize it. `./startup.sh up`
> (without the auth rig) runs the backend with `AUTH_ENABLED=false` instead.
> On Windows, run `startup.cmd up-with-dev-auth` (or `startup.cmd up`) instead:
> same steps, same `.env`.

Files here:

- `keycloak/realm-export.json` — a reproducible `natural-reader` realm: the
  browser-facing confidential client (PKCE, plus a realm-roles protocol
  mapper so `realm_access.roles` reaches the ID token), the realm roles
  `reader`/`chat`/`admin`/`docling` (granted to **nobody** by default, apart from the test user below), one verified
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
The reference nginx vhosts live under `../docs/` (they're copied into your own
nginx by hand, so they sit with the docs rather than the tool-consumed artifacts
above): [`chat.example.com.sample`](../docs/sample_configs/chat.example.com.sample) (SPA + same-origin
`/v1` proxy) and [`auth.example.com.sample`](../docs/sample_configs/auth.example.com.sample)
(Keycloak). See [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

## The one thing that matters: same origin

OIDC session cookies only work when the browser sees the **SPA and the backend
as one origin**. Two ways to get that:

- **Fast dev loop:** `npm run dev` (:5173) — `vite.config.js` proxies `/v1` to the
  backend, so :5173 is same-origin. Use this while building UI.
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
— user `admin`, with the password in `.env` as `KC_BOOTSTRAP_ADMIN_PASSWORD`
(`./startup.sh` generates it). It is read once, when Keycloak first starts on an
empty database, and not re-applied on later container starts.

The realm file holds no secrets. The two client secrets and `admin-user`'s
password are `${PLACEHOLDERS}` that Keycloak fills from `.env` when it first
creates the realm (`OIDC_CLIENT_SECRET`, `KC_ADMIN_CLIENT_SECRET`,
`KC_SEED_USER_PASSWORD`). Anything unset gets a random value nobody knows
(`deploy/keycloak-entrypoint.sh`), so that login or client simply doesn't work
until you set it and re-create the realm.

## 2. Start the backend with OIDC enabled

Set these (see `.env.example` for the full list), then run the backend:

```bash
export OIDC_ISSUER=http://localhost:18080/realms/natural-reader
export OIDC_CLIENT_ID=natural-reader
export OIDC_CLIENT_SECRET=<OIDC_CLIENT_SECRET from .env>
export OIDC_REDIRECT_URL=http://localhost:5173/v1/auth/callback   # matches vite dev
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export COOKIE_SECURE=false                 # local http, no TLS
export BOOTSTRAP_ADMIN_EMAIL=admin@example.com
export HOST=127.0.0.1
# Optional — which models people get and what they may spend (see .env.example):
# export INFERENCE_MODELS="llama3.2:3b,gemma3:4b"   # unset = allow all (dev)
# export INFERENCE_DAILY_TOKEN_BUDGET=100000        # unset/0 = unlimited
# Optional — Keycloak admin service account (matches realm-export.json's
# natural-reader-admin client). Without it: admin-console capability grants take
# effect for the running session but don't survive the user's next Keycloak
# login, AND the founder-bootstrap self-heal (which re-assigns the roles in
# Keycloak so the seed admin isn't demoted) is skipped — see
# docs/IDENTITY_AND_ROLES.md and docs/DEPLOYMENT.md Trap 4.
# export KC_ADMIN_CLIENT_ID=natural-reader-admin
# export KC_ADMIN_CLIENT_SECRET=<KC_ADMIN_CLIENT_SECRET from .env>
.venv/bin/python run.py
```

With auth on, chat runs on the backend (`/v1/chat/sessions/{id}/turns`) under the
session cookie — see [docs/USER_GUIDE.md](../docs/USER_GUIDE.md) for the
user-facing behavior and `../README.md` § Model providers for the operator view.

## 3. Start the SPA

```bash
npm run dev      # http://localhost:5173
```

## 4. Walk the two-user flow

The **first** person to log in claims the seed-admin row and becomes the admin,
so to see the *pending* screen you need a **second** user. **How** they get their
capabilities depends on a subtlety worth pinning down, because it's a real
lockout trap:

- If `BOOTSTRAP_ADMIN_EMAIL` is **set** (as above, to `admin@example.com`) and
  matches the founder's Keycloak email, they claim the seed row via the
  *email-claim* path, which applies **no capability floor** — they get exactly
  the realm roles their token carries.
- If it's **unset**, the founder comes in via the *first-login race*, which
  additionally **force-grants** `{admin,reader,chat}` regardless of their roles.

So in this walkthrough `admin-user` renders as admin **because
`realm-export.json` assigns `admin-user` the `reader`/`chat`/`admin` realm roles
directly** (login sync mirrors them), *not* because of a force-grant. Strip those
roles off `admin-user` while `BOOTSTRAP_ADMIN_EMAIL` is set and the founder lands
on "active but no access" — see the founder-lockout trap in
[docs/IDENTITY_AND_ROLES.md](../docs/IDENTITY_AND_ROLES.md) and
[docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) Trap 4.

**Capabilities are opt-in, not opt-out:** the realm defines `reader`/`chat`/
`admin` but grants none of them to anyone by default (`realm-export.json`).
Every new user — including this second one, once activated — starts with
**zero capabilities** and sees "your account is active but has no access
yet" until an admin explicitly grants some.

1. Open <http://localhost:5173>. You land on the **login screen** → **Sign in** →
   Keycloak. Log in as `admin-user` with `KC_SEED_USER_PASSWORD` from `.env` (email `admin@example.com`,
   already verified). You come back **authenticated as admin**; the app renders.
2. In the Keycloak admin console, add a second user (Users → Add user; set an
   email, mark **Email verified** on, and set a password under Credentials).
3. In a separate browser/profile, open the app and sign in as that second user.
   You get the **"awaiting approval"** screen (JIT-provisioned, `pending`,
   zero capabilities).
4. Back as the admin: click the **shield icon** (admin view) in the view
   switcher, then **People**. The pending user is listed → click **Activate**, then check the
   **reader** and **chat** boxes in that row's capability strip (each
   checkbox PATCHes immediately). Skip this and the user stays "active but no
   access" after step 5.
5. As the second user, click **Refresh** on the pending screen → the app
   renders (no fresh Keycloak login needed — the running session picks up
   the new capabilities on its next request either way).
6. Open **Settings → Access tokens** (the header gear) → create a token; copy it
   once (this is what the browser extension / scripts use as a `Bearer` token —
   it needs the owning user to hold `reader` to authenticate TTS calls).
7. **Log out** from the profile menu (top right) → back to the login screen.

> **Offboarding note (live-verified):** deleting a user in the Keycloak console
> does **not** remove them from the app — their account stays active with any
> tokens still working, and their email stays blocked for future Keycloak users
> (409 "email already linked to another identity"). To remove someone:
> **Disable them in the app's Admin section first** (that revokes sessions and
> tokens), then delete in Keycloak. Details: [docs/IDENTITY_AND_ROLES.md](../docs/IDENTITY_AND_ROLES.md).

## Prod-like variant (nginx)

Instead of `npm run dev`: `npm run build`, point
[`../docs/sample_configs/chat.example.com.sample`](../docs/sample_configs/chat.example.com.sample)'s `root` at
`dist/`, install it, and set `OIDC_REDIRECT_URL` to your nginx origin. Then add
that origin to the realm's redirect URIs — and note this must be done on the
**live** realm (admin console or `kcadm`); editing `realm-export.json` only
affects a *fresh* database, not the one already in Postgres. Full production
walkthrough with TLS + Keycloak on its own hostname:
[../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

## Tear down

```bash
env -u XDG_DATA_HOME .venv/bin/podman-compose down     # add -v to wipe volumes
```
