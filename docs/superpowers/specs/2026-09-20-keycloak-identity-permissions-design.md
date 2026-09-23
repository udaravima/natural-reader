# Keycloak Identity & Capability Permissions — app as a Keycloak admin client

**Date:** 2026-09-20
**Status:** Draft — reflects the 2026-09-20 brainstorming session; awaiting review.
**Area:** `server/auth/` (new `kc_admin.py`, changes to `deps.py`, `users.py`, `config.py`,
`oidc.py`) · `server/routers/{admin,auth}.py` · route enforcement across
`docs`/`inference`/`chat_sessions`/`tools`/`endpoints` · `server/sql/008_*.sql` ·
`deploy/keycloak/realm-export.json` · SPA (`useAuth`, `AuthGate`, view switcher, `AdminConsole`).

**Supersedes** the app-side-only identity model documented in
[IDENTITY_AND_ROLES.md](../../IDENTITY_AND_ROLES.md) (that doc is rewritten by this work).

> **Scope:** This is sub-projects **P1 (identity core)** and **P2 (SPA feature-gating)** of the
> larger auth/UX program, merged into one spec by decision. Later sub-projects — **P3** (app shell +
> dedicated Settings page + top-bar indicators) and **P4** (user self-service: password via
> Keycloak account console, profile picture) — get their own specs. The **document-library RAG**
> model proceeds on its own branch in parallel.

---

## 1. Why — the problem this fixes

The multi-user auth that shipped deliberately **mirrors** Keycloak identity into Postgres and never
*references* it: roles live only in `users.role`, and the admin console's "enroll" merely inserts an
app row with `oidc_sub = NULL` that waits for its owner to already exist in Keycloak and log in.
Two consequences made user management feel broken:

1. **The provisioning loop has no closing half.** An admin enrolls `bob@example.com`, but nothing
   creates Bob in Keycloak, so Bob cannot log in and claim the row. If Bob *is* created in Keycloak
   by hand without the **Email Verified** flag (Keycloak's default for manual users), his first
   login is rejected with `email not verified; cannot claim pre-provisioned account`.
2. **Roles and permissions are not Keycloak-driven at all.** There is no way to gate *features*
   (reader vs. chat) per user or group, and no extensible role→permission model.

The fix: the app becomes a **Keycloak admin client** for provisioning, and **capabilities** become
the unit of authorization, owned by Keycloak realm roles and enforced deny-by-default in the app.

## 2. Goals / non-goals

**Goals**
- Enroll **creates** the Keycloak user (config-driven onboarding), so the app row is linked
  immediately — no "awaiting first login," no email-verified trap.
- Disable/enable and hard-delete propagate to Keycloak.
- **Capability-based access control**: `reader`, `chat`, `admin` (extensible), gating features per
  user; groups (defined in Keycloak) bundle capabilities and flow in through the token.
- Backend enforcement (deny-by-default) + SPA feature-gating that shows only permitted views.
- New unknown logins land `pending` with **zero capabilities** until an admin grants access.
- Graceful degradation: with the admin client unconfigured, the app falls back to today's app-only
  behavior instead of erroring (keeps the IdP-agnostic promise for non-Keycloak deployments).

**Non-goals (this spec)**
- In-app group *management* — Keycloak owns groups; our console edits a user's **direct** role
  assignments only (P-later if needed).
- In-app password reset / MFA — Keycloak's account console (linked in P4).
- Bulk user import.
- Per-client roles or resource-level fine-grained authorization (realm roles are enough here).
- The app shell, dedicated Settings page, top-bar indicators, and profile picture — those are
  **P3/P4**, separate specs.

## 3. Architecture — P1 (backend)

### 3.1 The Keycloak admin client — `server/auth/kc_admin.py`

A small module authenticated as a **service account** via the OAuth2 client-credentials grant.
Base URL, realm, and token/admin endpoints are derived from `OIDC_ISSUER`
(`{base}/realms/{realm}`) plus the discovery document — no new URLs to configure. The client
caches its access token and re-fetches on expiry or a `401`.

Surface (all async, all raising a typed `KCAdminError` on unexpected failure):

| Function | Keycloak call | Used by |
|---|---|---|
| `find_user_by_email(email) -> sub \| None` | `GET /admin/realms/{r}/users?email=&exact=true` | enroll conflict check |
| `create_user(email, display_name, email_verified) -> sub` | `POST /admin/realms/{r}/users` | enroll |
| `set_temp_password(sub, password, temporary=True)` | `PUT .../users/{id}/reset-password` | enroll (no-SMTP path) |
| `send_actions_email(sub, actions)` | `PUT .../users/{id}/execute-actions-email` | enroll (SMTP path) |
| `set_enabled(sub, enabled)` | `PUT .../users/{id}` | disable/enable |
| `delete_user(sub)` | `DELETE .../users/{id}` | hard delete |
| `assign_realm_roles(sub, names)` / `remove_realm_roles(sub, names)` | `POST/DELETE .../users/{id}/role-mappings/realm` | capability edits, bootstrap |
| `get_user_realm_roles(sub) -> [name]` | `GET .../users/{id}/role-mappings/realm/composite` | reconciliation (optional) |
| `realm_smtp_configured() -> bool` | `GET /admin/realms/{r}` → `smtpServer` non-empty | onboarding-mode switch |

**Availability + injection.** `kc_admin_available(cfg)` is true only when both
`KC_ADMIN_CLIENT_ID` and `KC_ADMIN_CLIENT_SECRET` are set. A FastAPI dependency
`get_kc_admin()` yields the client, or **`None`** when unconfigured. Every caller branches on
`None` → degraded path. Tests override `get_kc_admin` with a fake — **no live Keycloak in CI**.

### 3.2 Capability model — and where it is stored (the load-bearing choice)

**Keycloak realm roles are the source of truth:** `reader`, `chat`, `admin`. A **realm-roles
protocol mapper** places `realm_access.roles` into the ID token (and userinfo). Composite roles or
**groups** defined in Keycloak bundle these into "roles with different permissions"; the token's
`realm_access.roles` already resolves composites and group membership, so bundling needs **zero app
code**.

**But capabilities are also persisted** in `users.capabilities` (migration 008) and
`Principal.capabilities` is rebuilt from the DB row **on every request** — exactly as `role` is
today. Rationale: if capabilities lived only in the token, revoking someone's `chat` access would
not take effect until their token expired. Persisting them makes an admin's change bite on the
**next request**, no re-login. To keep the two stores consistent:

- **Admin edits write both**: assign/remove the Keycloak realm role **and** update `users.capabilities`
  in the same request.
- **Login re-syncs from the token** as the backstop (`realm_access.roles` ∩ known caps → DB).

`admin` is just another capability. `require_admin` becomes `require_capability("admin")`. The
`role` field returned by `/me` is **derived** (`"admin"` if the cap is present, else `"member"`) so
existing frontend expectations keep working; `users.role` is kept in sync for compatibility but is
no longer the gate.

### 3.3 Backend enforcement

A dependency factory `require_capability(name)` → 403
`detail={"error":"missing_capability","capability":name}` when absent. Applied:

| Routes | Required capability |
|---|---|
| `/v1/docs/*`, `/v1/synthesize`, `/v1/batch_synthesize` (read-aloud is part of reading) | `reader` |
| `/v1/inference/*`, `/v1/chat/sessions/*`, `/v1/tools/*` | `chat` |
| `/v1/admin/*` | `admin` |
| `/v1/health` | none (stays public for liveness probes) |

> **Extension note:** the browser-extension read-aloud uses a PAT; that PAT's user needs the
> `reader` capability once enforcement lands. Called out in deploy docs.

### 3.4 Login sync + first-user bootstrap

In `resolve_or_provision_user` / the callback, read `realm_access.roles`, intersect with the known
capability set, and write `users.capabilities`:

- **Branch 1 (known identity):** refresh email/display_name **and** capabilities from the token.
- **Branch 2 (link pre-provisioned):** link identity; capabilities come from the token (which
  reflects the roles the admin assigned at enroll).
- **Branch 3 (brand-new unknown):** `status='pending'`, `capabilities='{}'` — the "nothing until
  granted" default. Their token carries no capability roles (default access is none).
- **First-user-admin bootstrap:** when branch 3 atomically claims the seed admin, the app **also**
  calls `assign_realm_roles(sub, ["admin","reader","chat"])` so Keycloak agrees with the app — else
  the next login's sync would demote the founder. If the admin client is unavailable, capabilities
  are set app-side only.

All Keycloak calls in the login path are best-effort and no-op safely when the admin client is
absent.

### 3.5 Enroll — closing the loop (`POST /v1/admin/users`)

Body gains `capabilities: list[str]` (validated subset of known caps) alongside `status` and
`inference_daily_token_budget`.

**With the admin client:**
1. Validate; reject if the email already exists in the app **or** in Keycloak (`find_user_by_email`)
   → `409`.
2. `create_user(email, display_name, email_verified=…)` → `sub`.
3. `assign_realm_roles(sub, capabilities)`.
4. **Onboarding branch:** if `realm_smtp_configured()` → `send_actions_email([VERIFY_EMAIL,
   UPDATE_PASSWORD])`; else `set_temp_password(generate())` and return the temp password **once**
   in the response.
5. Insert the app row **already linked** (`oidc_iss`, `oidc_sub=sub`, email, capabilities, status,
   budget).
6. **Compensation:** any failure after step 2 triggers `delete_user(sub)` so no orphaned Keycloak
   user is left behind.

**Without the admin client:** fall back to today's unlinked pre-provisioning (`oidc_sub NULL`) with
the chosen capabilities recorded, plus a response note that the user must be created in Keycloak
manually and will link on first verified-email login.

Response shape: `{ user: <row>, onboarding: "email"|"temp_password"|"manual", temp_password?: str }`.

### 3.6 Disable / enable / delete / capability edit

- `PATCH /v1/admin/users/{id}` `status=disabled` → `set_enabled(sub, false)` (if linked+available) +
  app `disabled` (hard-revoke sessions, as today); `status=active` → `set_enabled(sub, true)`.
- `PATCH` `capabilities=[...]` → reconcile: assign/remove Keycloak realm roles to match the target
  set, then update `users.capabilities`. `exclude_unset` semantics unchanged for other fields.
- `DELETE /v1/admin/users/{id}` (hard) → delete the app row (existing cascade + PDF sweep) **and**
  `delete_user(sub)` in Keycloak (if linked+available). All rails kept (self / seed-admin /
  last-active-admin → `409`).

### 3.7 `/v1/auth/me` contract

Returns `{ id, email, role, capabilities, status }`. `role` is derived (see 3.2); `capabilities` is
the list the SPA gates on; `status` lets the SPA distinguish pending/disabled/active.

## 4. Architecture — P2 (SPA feature-gating)

- **`useAuth`** exposes `user.capabilities` (and `status`) from the `/me` probe.
- **View gating:** the reader / chat / admin views and the view switcher render **only** permitted
  views (`reader`→reader view, `chat`→chat view, `admin`→admin entry). A persisted `viewMode` is
  coerced to a permitted one; if none is permitted, the access screen shows.
- **Screens (in `AuthGate`):** existing `login` / `disabled` / `error`, plus `pending` (status) and a
  new **"access not yet granted"** screen for an *active* user with **empty capabilities**.
- **Admin console (`AdminConsole.jsx`):** the admin/member toggle becomes a **capability editor**
  (reader / chat / admin checkboxes per user, writes through the PATCH → Keycloak). The enroll form
  gains capability checkboxes, shows the returned temp password **once**, and surfaces the onboarding
  mode. Disable/delete already exist; delete/disable copy notes Keycloak propagation.
- **Defense in depth:** a `403 missing_capability` from any backend call triggers a `/me` re-probe
  and routes to the correct screen, so the hidden UI is a convenience, not the security boundary.

## 5. Data model, realm, config

**Migration `008_user_capabilities.sql`:** add `users.capabilities text[] NOT NULL DEFAULT '{}'`.
**Backfill preserves current access** (nobody is locked out on upgrade):

| Existing row | Backfilled capabilities |
|---|---|
| `role='admin'` | `{admin, reader, chat}` |
| `role='member'`, `status='active'` | `{reader, chat}` |
| `status IN ('pending','disabled')` | `{}` |

**Realm export (`deploy/keycloak/realm-export.json`):** add realm roles `reader`/`chat`/`admin`;
add a **dedicated confidential client `natural-reader-admin`** with service accounts enabled and
standard/implicit flows disabled (it never logs a human in), granted the `realm-management` client
roles `manage-users`, `view-realm`, `query-users`. Keeping it separate from the browser-facing
`natural-reader` login client means the admin credential is never exposed to the OIDC redirect flow.
Add the **realm-roles mapper** into the `natural-reader` login client's ID token; **no default
roles** (new users get nothing).

**Config / env (`.env.example`):** `KC_ADMIN_CLIENT_ID`, `KC_ADMIN_CLIENT_SECRET`.
`AuthConfig` gains these two fields.

## 6. Error handling & two-store consistency

- **Create then insert:** compensate by deleting the Keycloak user if the app-row insert (or any
  intermediate step) fails — the app row and the Keycloak user are created atomically-enough that a
  crash leaves no orphan of the *new* user. (A pre-existing orphan is still surfaced by the
  `find_user_by_email` conflict check.)
- **Propagation is best-effort on destructive paths:** if `set_enabled`/`delete_user` fails after
  the app row is already disabled/deleted, log a warning with the `sub`; the app state is the
  security-relevant one (sessions are already revoked). Never 500 the admin on a Keycloak hiccup
  *after* the app change committed — report a partial-success note instead.
- **Token/permission errors from Keycloak** (misconfigured service account) surface as a clear
  `502`/`503` with a message pointing at `realm-management` roles, not a stack trace.

## 7. Testing strategy

- **`kc_admin`** against a mocked HTTP transport: token fetch + cache + refresh-on-401,
  create/delete/enable, role assign/remove, `find_user_by_email`, `realm_smtp_configured`, error
  mapping.
- **Admin router** with a **fake `kc_admin`** injected via dependency override: enroll (available →
  linked row + roles assigned + temp password when no SMTP + invite when SMTP), enroll (unavailable →
  unlinked fallback + note), capability PATCH (reconcile assign/remove + DB), disable
  (`set_enabled`), hard delete (KC delete), all rails intact.
- **Login sync + bootstrap:** resolver reads `realm_access.roles` → caps; brand-new → pending/empty;
  first-user → admin caps + `assign_realm_roles` (fake) called.
- **`deps`:** `require_capability` 403 shape; `get_current_user` builds caps from the DB row.
- **Migration 008 backfill** correctness for each existing row class.
- **Frontend:** `useAuth` exposes capabilities; view-gating (reader-only user sees no chat;
  zero-cap active user sees the access screen); admin-console capability editor; enroll shows temp
  password. All existing tests stay green.

Target: all **35 backend + 189 frontend** tests remain green, plus the new suites above.

## 8. Documented limitations

- Our console edits **direct** realm-role assignments. Capabilities granted via a Keycloak **group**
  are managed by group membership in Keycloak's own UI — they still flow in through the token and
  render as effective capabilities, but "remove chat" in our console will not remove a group-granted
  `chat` (it returns on next login).
- With `KC_ADMIN_CLIENT_ID/_SECRET` unset, provisioning degrades to app-only pre-provisioning; the
  full loop requires the service account.

## 9. Backward compatibility / rollout

- Existing sessions keep working; capabilities are backfilled so current users retain their access.
- `role` remains in the `/me` payload (derived), so no frontend breaks before P2 lands.
- The realm-export additions are additive; an already-initialized realm needs the roles + mapper +
  service-account client added (documented in the deploy guide).

## 10. File map (what changes)

| Concern | File(s) |
|---|---|
| KC admin client (new) | `server/auth/kc_admin.py` |
| Config fields | `server/auth/config.py` |
| Capabilities in Principal + enforcement | `server/auth/deps.py` |
| Login capability sync + bootstrap role assign | `server/auth/users.py`, `server/routers/auth.py` |
| Enroll/disable/delete/capability edit | `server/routers/admin.py` |
| Route enforcement | `server/routers/{docs,inference,chat_sessions,tools}.py`, `server/endpoints.py` |
| Schema | `server/sql/008_user_capabilities.sql` |
| Realm | `deploy/keycloak/realm-export.json` |
| SPA gating | `src/hooks/useAuth.js`, `src/components/auth/AuthGate.jsx`, view switcher, `src/components/admin/AdminConsole.jsx` |
| Docs | `docs/IDENTITY_AND_ROLES.md`, `.env.example`, deploy guide |
