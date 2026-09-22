# Identity & Roles — how Keycloak users map to natural_reader users

A deep-dive companion to [ARCHITECTURE.md](ARCHITECTURE.md) § "Auth subsystem". Read that
first for the login round-trip shape; this document is the full map of the identity
layer: which system owns which fact, how the two user stores are joined, and what
that implies for capability-gated UI.

- [The two stores, one join key](#the-two-stores-one-join-key)
- [The login round-trip, link by link](#the-login-round-trip-link-by-link)
- [`resolve_or_provision_user` — the three branches](#resolve_or_provision_user--the-three-branches)
- [Why the mapping used to break (and why Keycloak state now lives in Postgres)](#why-the-mapping-used-to-break-and-why-keycloak-state-now-lives-in-postgres)
- [Where roles actually live (Keycloak realm roles → `users.capabilities`)](#where-roles-actually-live-keycloak-realm-roles--userscapabilities)
- [Credentials: two kinds, both DB-backed](#credentials-two-kinds-both-db-backed)
- [Special identities](#special-identities)
- [Roles → UI: capability-gated views](#roles--ui-capability-gated-views)

---

## The two stores, one join key

The confusion usually starts here, so name it plainly: there are **two completely
separate user tables in the same Postgres database**, owned by two different
systems, with **no foreign key between them**.

| | Keycloak's store | The app's store |
|---|---|---|
| Location | schema **`keycloak`** (tables like `user_entity`, `credential`) | schema `public`, table **`users`** (`server/sql/005_users_ownership.sql`) |
| Owns | **Credentials**: passwords, MFA, required actions. Who can log in. | **Accounts**: capabilities (mirrored from Keycloak), role, status, display name, ownership of documents/chat sessions, per-user inference budget |
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
  never). Two consequences were **live-verified** on the dev rig (2026-09-17,
  see HANDOVER): the deleted user's **existing app sessions and PATs remain
  valid** — Keycloak deletion revokes nothing app-side, because the app never
  re-checks with the IdP — and the orphaned row's **email is permanently
  blocked**: a *new* Keycloak user with the same email gets **409 "email
  already linked to another identity"** at login (branch 2 of the resolver).
  Correct order of operations: **disable the user in the app first**
  (`status='disabled'` — hard-revokes sessions), *then* delete in Keycloak if
  desired. Creating a user in Keycloak, by contrast, is safe but invisible to
  the app until that user's **first login** (JIT provisioning, `pending`
  member).
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
   ID-token/userinfo **claims**. The five that matter: **`sub`**, **`iss`**,
   **`email`**, **`email_verified`** (plus optional `name` → display_name), and
   **`realm_access.roles`** — the realm-roles protocol mapper on the
   `natural-reader` client puts this on the **ID token** (Keycloak's built-in
   mapper only targets the access token; see
   [Where roles actually live](#where-roles-actually-live-keycloak-realm-roles--userscapabilities)).
   `caps_from_claims` (`server/auth/capabilities.py`) intersects that role list
   with the app's known capability vocabulary (`reader`/`chat`/`admin`),
   dropping Keycloak built-ins like `offline_access`.
4. `resolve_or_provision_user(conn, iss=…, sub=…, email=…, email_verified=…,
   capabilities=…)` maps the identity to a `users` row (three branches, next
   section) **and** syncs the resolved capability set onto it on every login
   for an already-known identity.
5. A **session row** is created and its raw token set as the `nr_session`
   httponly cookie; 303 back to `next`. The SPA has no callback route — it just
   re-probes `GET /v1/auth/me` after the reload.

The claims carry **capabilities**, not the legacy `admin`/`member` role label —
see the next section for how one is derived from the other.

## `resolve_or_provision_user` — the three branches

`server/auth/users.py:65`. Order matters; the first match wins.

| Branch | Condition | Action | Security rationale |
|---|---|---|---|
| 1. Known identity | `(oidc_iss, oidc_sub)` row exists | Refresh cached email/display_name, and overwrite `users.capabilities` with the token's `realm_access.roles` (intersected with the known vocabulary) | The happy path; `(iss, sub)` is the only fully-trusted key. This is also why capabilities are **self-healing on next login**: fix a role in Keycloak and the app row catches up without an admin-console edit |
| 2. Email claim | No identity match, but a row carries this email **and** `oidc_sub IS NULL` (pre-provisioned) | If `email_verified` → link the identity to that row and apply the token's capabilities the same way; else **409** | An *unverified* email must never claim a pre-provisioned account — anyone who can mint a token with the admin's email string would otherwise win. Also 409 if the email is already linked to a *different* identity (`email already linked to another identity`) |
| 3. Brand-new | Neither | **First-user-admin race**: atomic `UPDATE ... WHERE id = seed AND oidc_sub IS NULL` — exactly one concurrent first login wins (row lock serializes), inherits the seed admin row, and is force-granted `{admin, reader, chat}` (`BOOTSTRAP_ADMIN_CAPABILITIES`) regardless of what roles that Keycloak user happened to hold — the callback also best-effort-assigns those three realm roles to the winner in Keycloak so the *next* login's sync (row above) doesn't demote them; everyone else INSERTs as `role='member', status='pending'`, **zero capabilities** | Bootstrap without a provisioning UI: the first person to log in to a fresh install is the admin, and the conditional UPDATE makes it race-safe |

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

## Where roles actually live (Keycloak realm roles → `users.capabilities`)

This used to say "Keycloak does not know about roles at all" — that changed.
**Roles now live in Keycloak**, as three plain **realm roles**:
`reader`, `chat`, `admin` (`deploy/keycloak/realm-export.json` §`roles.realm`).
No group defaults them onto anyone — a brand-new Keycloak user, and a
brand-new app row, both start with **zero capabilities**. Someone with the
`admin` capability has to grant them, either through this app's admin console
or directly in Keycloak.

Keycloak is the **source of truth**; the app keeps a **mirror**:

- `users.capabilities text[]` (migration `008_user_capabilities.sql`) caches
  the realm roles that mattered at last login/sync. `users.role` (`admin` |
  `member`) still exists too — it's now a **derived** column, kept in lockstep
  by `set_capabilities()` (`server/auth/users.py`): `admin` iff the `admin`
  capability is present. Nothing computes `role` independently any more.
- The realm-to-app hop happens two ways, and both matter:
  1. **Login sync** (previous section, branches 1–2): every login reads
     `realm_access.roles` off the ID token (`caps_from_claims`,
     `server/auth/capabilities.py`) and overwrites `users.capabilities` to
     match. This is what makes Keycloak authoritative — an app-side edit that
     isn't also reflected in Keycloak gets reverted on the user's next login.
  2. **Admin-console writes** (`PATCH /v1/admin/users/{id}` with a
     `capabilities` array, `server/routers/admin.py`): when the
     `natural-reader-admin` service account is configured (below), a
     capability edit here calls the Keycloak Admin REST API to
     add/remove the matching realm-role assignment on that user **first**,
     and only writes `users.capabilities` after that succeeds — a failed
     Keycloak call is a `502`, capabilities unchanged, rather than a
     console/reality split that the next login would silently fix by
     reverting the console's change. Revoke is sent before grant for the same
     reason: a swallowed revoke must never fail open.
- `get_current_user` (`server/auth/deps.py`) still resolves the credential
  (PAT or session) to a **fresh `users` row on every request** and builds the
  `Principal(user_id, email, role, capabilities)` from the DB mirror — **not**
  from the token. **Capability and status changes still take effect
  immediately**, no re-login needed, for the same reason as before: nothing
  server-side trusts the token itself past login, only the DB row it fed.
- `require_capability(name)` (same file) is the deny-by-default gate: reader
  endpoints (`POST /v1/synthesize`, `/v1/synthesize/batch`) need `reader`;
  chat/inference/web-search endpoints need `chat`; `require_admin` is
  effectively `require_capability("admin")` for `/v1/admin/*`. A 403 carries
  `{"error": "missing_capability", "capability": "<name>"}` so the SPA can
  render a specific message instead of a generic "forbidden".
- **The browser-extension / script PAT path needs `reader`.** A personal
  access token authenticates as its owning user and is subject to the exact
  same capability check as the session cookie — if that user's account has no
  `reader` capability, the extension's TTS calls 403 just like the web app's
  would. There is no separate "extension scope"; grant the human `reader` and
  every credential they hold inherits it.

### The service account that makes admin-console edits real

`PATCH`/enroll capability and lifecycle changes can only reach Keycloak when
the app itself holds Keycloak admin credentials — the dedicated confidential
client **`natural-reader-admin`** (`serviceAccountsEnabled: true`,
`standardFlowEnabled: false`, `directAccessGrantsEnabled: false`; it never
logs a human in, only mints client-credentials tokens for
`server/auth/kc_admin.py`). Its service-account user is granted the
`realm-management` client roles `manage-users`, `view-realm`, `query-users` —
enough to create/enable/disable/delete users and manage realm-role
assignments, nothing more. Wire it with `KC_ADMIN_CLIENT_ID` /
`KC_ADMIN_CLIENT_SECRET` (`.env.example`); the realm-export dev value is
`natural-reader-admin` / `natural-reader-admin-dev-secret`.

**Unset = app-only degraded mode**, and this is the trap to know about: enroll
still pre-provisions an unlinked app row (branch 2 claims it on first login,
same as before this feature existed), but an admin-console capability edit on
an **already-linked** user only writes `users.capabilities` — it cannot touch
Keycloak. Because login sync (above) treats Keycloak as authoritative, that
edit **survives until the user's next login, then reverts** to whatever their
realm roles actually say. Configuring the service account is what makes the
admin console's capability editor a durable, two-way control instead of a
DB-only sticky note.

### Documented limitation: direct assignment vs. groups

Everything above — the admin console's capability editor, the sync-on-login —
operates on a user's **direct** realm-role assignments via the Keycloak Admin
REST API. Keycloak also supports granting roles through **group**
membership, and that is deliberately **out of reach of this app's console**:
a capability granted by putting a user in a Keycloak group still flows
through fine (it's part of `realm_access.roles` like any other effective
role, so login sync picks it up), but clicking "remove chat" in our admin
console will not remove a group-granted `chat` — Keycloak's direct
role-mappings DELETE call has nothing to remove, so login sync just re-adds
it from the group on the next login. Manage group-based grants in Keycloak's
own admin UI (Groups → role mappings); use this app's console for
individually-assigned capabilities.

`status` (`active|pending|disabled`) is the other half of the account lifecycle:
new JIT users land `pending` and see a "waiting for approval" screen (the 403
carries `detail.status` so the SPA can branch); an admin activates; disabling
**deletes all the user's session rows** (hard revoke) and the per-request
status re-check is the backstop for PATs, which have no session to delete.

The `resolve_or_provision_user` / `get_current_user` pair above **is** that
IdP-driven mapping point now — `resolve_or_provision_user` reads the realm-role
claim and writes `users.capabilities`; `get_current_user` re-derives
`Principal.role` from the DB mirror on every request. Do not start trusting a
client-supplied role or capability hint from anywhere *other* than that one
path (e.g. never read a header the SPA sets, never trust an unmirrored claim
mid-request) — the DB row, refreshed at login, stays the single point of
truth for the running session.

## Credentials: two kinds, both DB-backed

| | Session (SPA) | Personal access token (extension, scripts) |
|---|---|---|
| Transport | `nr_session` httponly cookie | `Authorization: Bearer nrp_...` |
| Stored | `sessions` table — **sha256(cookie)** only | `personal_access_tokens` — **sha256(token)** only |
| Scope | one per browser login | user-managed, named, optional expiry, revocable (`/v1/auth/tokens`) |
| Works on `/v1/inference/*` (needs `chat` capability) | yes | yes — this is how the browser extension would chat, if it ever grows chat |
| Works on `/v1/synthesize*` (needs `reader` capability) | yes | yes — **this is how the browser extension reads pages aloud today**; its PAT's owner must hold `reader` or every TTS call 403s |

A DB leak yields no usable credentials (hashes only). Resolution order in
`get_current_user`: Bearer first, then cookie — a PAT and a session can
coexist. Either way the capability check (`require_capability`, previous
section) runs the same regardless of which credential resolved it — a PAT
carries no scope narrower than its owning user's current capabilities.

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

## Roles → UI: capability-gated views

What the frontend knows: `GET /v1/auth/me` → `{id, email, role, capabilities}`,
held in `useAuth`'s `user` state (`capabilities` defaults to `[]` if the
backend omits it). The SPA has no router — `viewMode` state *is* the routing —
and it is now a **three-way** value: `'reader' | 'chat' | 'admin'`, each
gated on its matching capability (`App.jsx`: `canReader`/`canChat`/`canAdmin`
derived from `auth.user.capabilities`).

- **Zero capabilities, `status: active`:** `AuthGate` (`src/components/auth/
  AuthGate.jsx`) renders `NoAccessScreen` instead of the app shell — "your
  account is active but has no access yet" — distinct from the `pending` /
  `disabled` screens, which are about `status`, not capabilities. An account
  can be fully active and still see nothing if an admin never granted a
  capability (this is the normal state for a brand-new user now that nothing
  is granted by default).
- **`useViewModeGuard`** (`src/hooks/useViewModeGuard.js`) coerces a
  persisted `viewMode` the user no longer (or never did) hold the capability
  for to the first permitted view, in `reader → chat → admin` order, once the
  auth probe resolves — so a capability revoked mid-session, or a demoted
  admin, doesn't stay boot-looping into a view that now 403s.
- **Admin view** (`src/components/admin/AdminConsole.jsx`) is a real third
  view, not a sidebar block — entry point is an icon in the view switcher,
  rendered only when `canAdmin` (`App.jsx`: `inAdmin` suppresses the reader
  `Sidebar` entirely while this view is active). It holds the user list
  (activate/disable, the capability editor — a per-user checkbox strip for
  reader/chat/admin — enroll with capabilities, one-time temp password
  display when SMTP isn't configured). The self-row hides Disable/Delete and
  disables your own `admin` checkbox — UI-only convenience (the server's real
  guard is `other_active_admins(target) == 0`, which fires for *any* row,
  self or not, so this only pre-empts the common case).
- `require_capability`'s 403 shape (`{"error": "missing_capability",
  "capability": "<name>"}`, previous section) is what `apiFetch`'s
  forbidden-handler watches for to **re-probe** `/v1/auth/me` — if an admin
  changes your capabilities while your tab is open and your next request
  needs the one that was removed, that 403 triggers a refresh instead of a
  stuck error.

One caveat still worth carrying forward: **`useAuth` otherwise only refreshes
`user` on mount** — a capability change made by another admin won't appear in
your open tab until either such a `missing_capability` 403 fires, or you
reload.

**Known duplication, not yet cleaned up:** the *reader* view's `Sidebar`
still carries its own collapsible "Admin" block (`Sidebar.jsx`, gated on
`user?.role === 'admin'`) that renders the older `AdminPanel.jsx` — a smaller
user list with activate/disable and a role toggle, no capability editor, no
usage/budget view. It's only reachable while in the reader view (the admin
*view* hides `Sidebar` outright), so the two don't fight over the same
screen, but a `role === 'admin'` user now has two different places to manage
users with two different feature sets. Treat `AdminConsole` as the
current/complete surface; `Sidebar`'s block is a leftover from before this
feature and a candidate for removal in a follow-up, not a second supported
path.

---

*Verified against source as of commit `1602721` (branch
`feat/keycloak-identity-permissions`): `server/auth/users.py`,
`server/auth/deps.py`, `server/auth/capabilities.py`, `server/auth/kc_admin.py`,
`server/routers/auth.py`, `server/routers/admin.py`, `server/db.py`,
`server/sql/005–008`, `deploy/keycloak/realm-export.json`,
`deploy/postgres/init/`, `docker-compose.yml`, `.env.example`,
`src/App.jsx`, `src/hooks/useAuth.js`, `src/hooks/useViewModeGuard.js`,
`src/utils/apiFetch.js`, `src/components/auth/AuthGate.jsx`,
`src/components/admin/AdminConsole.jsx`, `src/components/Sidebar.jsx`,
`src/components/admin/AdminPanel.jsx`. (Earlier pass: `286fa41` on
`feat/model-router-gateway`.)*
