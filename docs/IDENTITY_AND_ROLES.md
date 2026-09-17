# Identity & Roles — how Keycloak users map to natural_reader users

A deep-dive companion to [ARCHITECTURE.md](ARCHITECTURE.md) § "Auth subsystem". Read that
first for the login round-trip shape; this document is the full map of the identity
layer: which system owns which fact, how the two user stores are joined, and what
that implies for role-based UI.

- [The two stores, one join key](#the-two-stores-one-join-key)
- [The login round-trip, link by link](#the-login-round-trip-link-by-link)
- [`resolve_or_provision_user` — the three branches](#resolve_or_provision_user--the-three-branches)
- [Why the mapping used to break (and why Keycloak state now lives in Postgres)](#why-the-mapping-used-to-break-and-why-keycloak-state-now-lives-in-postgres)
- [Where roles actually live (not in Keycloak)](#where-roles-actually-live-not-in-keycloak)
- [Credentials: two kinds, both DB-backed](#credentials-two-kinds-both-db-backed)
- [Special identities](#special-identities)
- [Roles → UI: today, and the case for a dedicated admin surface](#roles--ui-today-and-the-case-for-a-dedicated-admin-surface)

---

## The two stores, one join key

The confusion usually starts here, so name it plainly: there are **two completely
separate user tables in the same Postgres database**, owned by two different
systems, with **no foreign key between them**.

| | Keycloak's store | The app's store |
|---|---|---|
| Location | schema **`keycloak`** (tables like `user_entity`, `credential`) | schema `public`, table **`users`** (`server/sql/005_users_ownership.sql`) |
| Owns | **Credentials**: passwords, MFA, required actions. Who can log in. | **Accounts**: role, status, display name, ownership of documents/chat sessions, per-user inference budget |
| Rows keyed by | its own internal UUID (`user_entity.id`) — this is the OIDC **`sub`** | app UUID (`users.id`) |
| Created by | Keycloak admin UI / realm import | **JIT on first login** (`server/auth/users.py`) |

**The join key is the OIDC `(iss, sub)` pair**, copied into `users.oidc_iss` /
`users.oidc_sub` at login time. There is deliberately no FK and no shared ID
column: the app must survive swapping the IdP (the OIDC client is
discovery-based, `server/auth/oidc.py`), and identity is *mirrored*, never
*referenced*. Keycloak generates the `sub` UUID; the app never generates
identity, it only records what the IdP asserted.

Consequences worth holding onto:

- **Deleting a user in Keycloak does not delete the app row.** The app row
  (with all owned documents) goes orphan-but-intact; the identity can never log
  in again until a Keycloak user with the same `sub` exists again (practically:
  never). Disabling the account in the *app* (`status='disabled'`) is the
  supported kill switch.
- **`users.email` is a cache, not a key.** It is refreshed from the IdP claims on
  every login of a known identity. The uniqueness constraint on it exists for
  the pre-provisioning flow (below), not because email identifies the user —
  `(oidc_iss, oidc_sub)` does.
- **Keycloak's `user_entity.id` ≠ `users.id`.** Never compare them; the only
  bridge is `sub → users.oidc_sub`.

## The login round-trip, link by link

1. SPA redirects the browser to `GET /v1/auth/login?next=...`
   (`server/routers/auth.py`). The backend stashes `next` (validated: same-site
   path only — no open redirects) in the *signed flow cookie* and 302s to
   Keycloak's authorize endpoint with **PKCE S256**.
2. Keycloak authenticates the human (this is the *only* step that touches the
   `keycloak` schema) and redirects back to
   `GET /v1/auth/callback?code=...&state=...`.
3. The backend exchanges the code (Authlib verifies state + PKCE) and reads the
   ID-token/userinfo **claims**. The four that matter: **`sub`**, **`iss`**,
   **`email`**, **`email_verified`** (plus optional `name` → display_name).
4. `resolve_or_provision_user(conn, iss=…, sub=…, email=…, email_verified=…)`
   maps the identity to a `users` row (three branches, next section).
5. A **session row** is created and its raw token set as the `nr_session`
   httponly cookie; 303 back to `next`. The SPA has no callback route — it just
   re-probes `GET /v1/auth/me` after the reload.

Note what is **not** in the claims: any role or permission. See
[Where roles actually live](#where-roles-actually-live-not-in-keycloak).

## `resolve_or_provision_user` — the three branches

`server/auth/users.py:65`. Order matters; the first match wins.

| Branch | Condition | Action | Security rationale |
|---|---|---|---|
| 1. Known identity | `(oidc_iss, oidc_sub)` row exists | Refresh cached email/display_name, return it | The happy path; `(iss, sub)` is the only fully-trusted key |
| 2. Email claim | No identity match, but a row carries this email **and** `oidc_sub IS NULL` (pre-provisioned) | If `email_verified` → link the identity to that row; else **409** | An *unverified* email must never claim a pre-provisioned account — anyone who can mint a token with the admin's email string would otherwise win. Also 409 if the email is already linked to a *different* identity (`email already linked to another identity`) |
| 3. Brand-new | Neither | **First-user-admin race**: atomic `UPDATE ... WHERE id = seed AND oidc_sub IS NULL` — exactly one concurrent first login wins (row lock serializes) and inherits the seed admin row; everyone else INSERTs as `role='member', status='pending'` | Bootstrap without a provisioning UI: the first person to log in to a fresh install is the admin, and the conditional UPDATE makes it race-safe |

Why branch 2 only links `oidc_sub IS NULL` rows: pre-provisioned accounts
(seed admin, or rows an admin pre-created for onboarding) wait *unlinked* for
their owner's first login. Once linked, email is never a claiming key again —
only `(iss, sub)`.

## Why the mapping used to break (and why Keycloak state now lives in Postgres)

Commit `286fa41` fixed a real desync class; the mechanism is worth understanding
because it's the failure mode of *any* mirrored-identity design:

- Before: Keycloak ran `start-dev` with its **embedded H2** store. The store
  died with the container. Every `docker compose up` recreation minted **new
  `sub` UUIDs** for the same humans.
- App-side, branch 1 then misses (new `sub`), and branch 2 rejects — the email
  is already linked to the *old* `sub` — so every returning user got
  **409 "email already linked to another identity"**. The `users` table
  accreted orphaned rows.
- Now: the realm lives in the shared Postgres, schema **`keycloak`**, created
  by `deploy/postgres/init/00-create-keycloak-schema.sql` on **first volume
  initialization only** (`docker-entrypoint-initdb.d` semantics). `--import-realm`
  seeds the realm only if absent. `sub` UUIDs — and therefore the app's
  `users.oidc_sub` links — now survive container recreation.

**Trap:** on an *already-initialized* Postgres volume, the init script never
runs. If Keycloak starts with `KC_DB_SCHEMA: keycloak` and the schema is
missing, it fails (or offers to create it, depending on version). Create it by
hand once (the comment at the top of the SQL file has the exact `psql` command).

## Where roles actually live (not in Keycloak)

**Keycloak does not know about `admin`/`member` at all.** The realm export
(`deploy/keycloak/realm-export.json`) defines no roles, mappers, or claims for
them. Roles are an **application fact**, stored on `users.role`, checked
server-side per request:

- `get_current_user` (`server/auth/deps.py`) resolves the credential (PAT or
  session) to a **fresh `users` row on every request**, and builds the
  `Principal(user_id, email, role)` from it. **Role and status changes take
  effect immediately** — no token to invalidate, no re-login needed. This is
  deliberate: had roles lived in the ID token, demoting an admin would require
  token expiry to take effect.
- `require_admin` (same file) is the single gate for every `/v1/admin/*` route.
- Role is mutated only via `PATCH /v1/admin/users/{id}` (admins only; the UI
  is AdminPanel's "Make admin/member" — self-demotion of the last admin is
  only prevented in the UI by hiding the button on your own row; the API allows
  it, so don't).

`status` (`active|pending|disabled`) is the other half of the account lifecycle:
new JIT users land `pending` and see a "waiting for approval" screen (the 403
carries `detail.status` so the SPA can branch); an admin activates; disabling
**deletes all the user's session rows** (hard revoke) and the per-request
status re-check is the backstop for PATs, which have no session to delete.

If you ever *want* IdP-driven roles (e.g. enterprise SSO groups), the mapping
point is `resolve_or_provision_user` / `get_current_user` — read the claim
there and write/interpret `users.role` accordingly. Do not start trusting
client-supplied role hints anywhere else.

## Credentials: two kinds, both DB-backed

| | Session (SPA) | Personal access token (extension, scripts) |
|---|---|---|
| Transport | `nr_session` httponly cookie | `Authorization: Bearer nrp_...` |
| Stored | `sessions` table — **sha256(cookie)** only | `personal_access_tokens` — **sha256(token)** only |
| Scope | one per browser login | user-managed, named, optional expiry, revocable (`/v1/auth/tokens`) |
| Works on `/v1/inference/*` | yes | yes — this is how the browser extension would chat, if it ever grows chat |

A DB leak yields no usable credentials (hashes only). Resolution order in
`get_current_user`: Bearer first, then cookie — a PAT and a session can coexist.

## Special identities

- **Seed admin** — fixed UUID `00000000-0000-0000-0000-000000000001`, seeded by
  migration 005 with sentinel email `admin@localhost`, unlinked. It owns every
  pre-multi-user document/chat row (migration backfills `user_id` to it), and
  the first OIDC login *inherits* it (branch 3). `BOOTSTRAP_ADMIN_EMAIL`
  (`server/db.py: bootstrap_admin()`) re-points its email before that first
  login so a known address claims it via branch 2 instead of racing.
- **Dev bypass** — `AUTH_ENABLED=false` **and** loopback bind → every request
  is the seed admin, no credential at all (`dev_bypass_allowed`). Never
  possible on a non-loopback bind; `startup_guard()` refuses to boot that
  combination.

## Roles → UI: today, and the case for a dedicated admin surface

What the frontend knows: `GET /v1/auth/me` → `{id, email, role}`, held in
`useAuth`'s `user` state. It gates exactly one thing today — the Admin
section in the reader Sidebar (`Sidebar.jsx`: `user?.role === 'admin' && …`),
which renders `AdminPanel` (user list, activate/disable, role toggle).
`AccountPanel` (profile + PATs) is role-agnostic. Views are
`viewMode: 'reader' | 'chat'`; members and admins see identical views.

So: **"reader / chat / admin" as role-partitioned pages is not how the app is
shaped today, and per-member pages need nothing** — members have no
capability difference. The real question is whether the *admin* surface has
outgrown a collapsible sidebar block. It is about to:

- AdminPanel doesn't yet show the inference **usage view**
  (`GET /v1/admin/inference/usage?days=7`, per-day per-user token rows) or the
  per-user **budget knob** (`PATCH /v1/admin/users/{id}` with
  `inference_daily_token_budget`) — API-only since Task 11/12, flagged in
  HANDOVER as a known gap.
- Admin interactions are rare, batch-y, and list-heavy — a bad fit for a
  reader sidebar's vertical space (`max-h-[70vh]` scroll region).

**Proposal (not yet built — treat as the design sketch for a future plan):**
add a third `viewMode: 'admin'`, gated on `user?.role === 'admin'`, holding:

1. **Users** — move AdminPanel's list there; add the budget field
   (text/number, empty = deployment default, 0 = unlimited — mirror the
   PATCH's `exclude_unset` semantics so *absent* never clobbers a value).
2. **Usage** — the 7-day token table from the usage endpoint. Remember the
   endpoint returns `day` as an ISO **date** (UTC-derived), not a timestamp.
3. **Server health** — later: allowlist, task-model config visibility
   (read-only render of `model_router` config via an admin endpoint).

Entry point: a shield icon in the existing view switcher (next to
reader/chat), rendered only for admins. Members never see it; nothing about
their pages changes. The SPA has no router — `viewMode` state is the routing,
and `admin` is just one more value (persisted like the others, but coerced
back to `reader` when `role !== 'admin'` so a demoted admin doesn't boot into
a forbidden view).

One caveat to carry into that work: **`useAuth` caches `user` until the next
`/v1/auth/me` probe** (mount or explicit refresh), so a role change made by
another admin won't appear in your open tab until reload. Fine for now;
re-probe after any self-affecting PATCH in the admin view.

---

*Verified against source as of commit `286fa41` (branch
`feat/model-router-gateway`): `server/auth/users.py`, `server/auth/deps.py`,
`server/routers/auth.py`, `server/db.py`, `server/sql/005–007`,
`deploy/keycloak/realm-export.json`, `deploy/postgres/init/`,
`docker-compose.yml`, `src/hooks/useAuth.js`, `src/components/Sidebar.jsx`,
`src/components/admin/AdminPanel.jsx`.*