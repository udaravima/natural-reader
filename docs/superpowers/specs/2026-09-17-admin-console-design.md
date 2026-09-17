# Admin Console — design spec

**Status:** Draft (design agreed in principle 2026-09-17; implementation NOT on
`feat/model-router-gateway` — start a fresh branch, e.g. `feat/admin-console`,
from the integration line once the gateway branch lands.)

**Related:** [IDENTITY_AND_ROLES.md](../../IDENTITY_AND_ROLES.md) (identity
model, the admin-surface proposal this spec expands),
[USER_GUIDE.md](../../USER_GUIDE.md) (what end-users are told today),
[TESTING.md](../../TESTING.md) (existing coverage the new work must extend).

## 1. Problem

Admin capability has outgrown the reader sidebar, and two verified operational
gaps have no UI at all:

1. **The Admin sidebar section is a `max-h-[70vh]` scroll box** inside the
   *reader* sidebar — fine for three buttons, wrong for user lists + usage
   tables + budget forms. It also conflates "I'm reading" with "I'm
   administering".
2. **Inference usage + per-user budgets are API-only** (shipped in the gateway
   work: `GET /v1/admin/inference/usage?days=7`,
   `PATCH /v1/admin/users/{id}` `inference_daily_token_budget`) — admins
   cannot actually use them without curl.
3. **Users cannot be deleted, and live verification (see HANDOVER
   2026-09-17) showed why that bites:** deleting a user in Keycloak does
   *nothing* app-side — the account stays active with working tokens, and the
   email becomes permanently 409-blocked for any future Keycloak user with the
   same address. The dev DB already contains such an orphan
   (an `active` row whose `oidc_sub` matches no Keycloak identity). Today the
   only fix is database surgery.

## 2. Goals / non-goals

**Goals**

- A dedicated **admin console view** in the SPA — not a sidebar pane — reachable
  by every admin, invisible to members.
- **User management**: list, search/filter, activate, disable, role toggle
  (parity with today's AdminPanel) **plus delete** with safety rails.
- **User enrollment**: admins create a **pre-provisioned** account (email,
  display name, role, budget, status) that waits `oidc_sub NULL` until its
  owner's first login claims it via verified email (resolver branch 2 — this
  path already exists and is tested; the console just makes it creatable).
  This is the app-native answer to "things we can't do in the Keycloak
  console": onboarding decisions (role, budget, activation) become app-side
  and *precede* the login instead of racing it.
- **Inference governance**: per-user budget edit (respecting the PATCH's
  `exclude_unset` semantics) and the usage dashboard (N days × per-user rows).
- **Read-only deployment info**: the effective model-router config (allowlist,
  task models, timeout, default budget) so admins can see *why* a model
  422s without shell access.
- React-first navigation: the SPA has **no router**; `viewMode` state *is* the
  routing, and this adds one value to it.

**Non-goals**

- Per-role member pages (members have no capability differences; nothing to
  build).
- **Creating IdP users from the app** (calling Keycloak's Admin API). The app
  stays discovery-based and IdP-agnostic; enrollment here is *account*
  pre-provisioning, not credential creation. If one-click "also make the
  Keycloak user" is wanted later, it goes behind a Keycloak-specific feature
  flag (see §9).
- **Keycloak→app sync** (webhooks, admin-event listeners, scheduled
  reconciliation). Rejected deliberately: it couples the app to one IdP's
  proprietary API, needs long-lived admin credentials in the backend, and
  replaces a *policy* problem with a *drift* problem. The propagation rules
  are solved by tooling + order-of-operations (§ User lifecycle), not by
  mirroring.
- Audit log, invitations, email flows, granular permissions beyond
  admin/member.

## 3. UX design

### Entry + routing

- `viewMode` gains `'admin'` alongside `'reader' | 'chat'`.
- The view switcher gains a **shield icon**, rendered only when
  `user?.role === 'admin'` (same `useAuth` state that gates AdminPanel today).
- **Coercion rule:** on boot, a persisted `viewMode: 'admin'` for a non-admin
  falls back to `'reader'` — a demoted admin must never land in a forbidden
  view, and a member must never even be able to type a URL/shortcut into it.
- **Staleness caveat (carried from identity doc):** `useAuth` caches the user
  until the next `/v1/auth/me` probe; a role change by another admin appears on
  reload. Fine — but the console re-probes after any *self-affecting* action
  (e.g. demoting yourself is blocked anyway — see rails).

### Layout

- Full-height page (like chat), header row with the console title + a "back to
  reader" affordance. Three stacked sections in one scroll (no tabs to keep it
  router-free and mobile-simple):

  1. **Users**
  2. **Inference usage**
  3. **Deployment config**

- Reuse `AdminPanel`/`AccountPanel` visual language (tiny text, list rows,
  underline buttons) — this is a movement of surface, not a redesign.

### Users section

- Row per user: email · `role/status` · created date; inline actions
  **Activate / Disable / Make admin|member / Delete** (self-row hides Disable,
  Demote, and Delete — extending today's lockout guard). Unlinked
  pre-provisioned rows show an **"awaiting first login"** badge (distinguish
  them from linked-but-pending).
- **Budget field** per user: empty = deployment default (rendered as a
  placeholder), `0` = unlimited, number = tokens/day. PATCH semantics: the
  field is **omitted** from the request when untouched (never serialized as
  `null` by accident — `null` explicitly clears). UI hint text must say
  "empty = default, 0 = unlimited".
- **Enroll form** (new, top of the section): email (required), display name,
  role (default member), status (default pending), budget (default unset) →
  `POST /v1/admin/users`. Result: a pre-provisioned row with `oidc_sub NULL`.
  The console tells the admin the remaining step: *"have the user log in with
  this exact (verified) email"* — the login claims the row and inherits the
  chosen role/status/budget (resolver branch 2 preserves them; only
  `oidc_iss/oidc_sub/display_name` are filled in).
- **Delete flow** (new): a confirm step requiring the email to be typed
  (destructive-action guard), then `DELETE /v1/admin/users/{id}`. After delete,
  the list refreshes. A toast explains the consequence ("their documents were
  deleted / detached" — see §4).

### Inference usage section

- Days selector (default 7, server clamps 1..90 — already enforced).
- Table: user × day rows, `prompt_tokens`, `eval_tokens`, `requests`, total.
  Remember: `day` is an **ISO date (UTC-derived)**, not a timestamp — sort as a
  string or parse as a date, never `new Date(day).getTime()` assumptions about
  local midnight.
- Empty state: "No usage recorded yet."

### Deployment config section

- Read-only render of a new `GET /v1/admin/inference/config`-style endpoint
  (see §4): allowlist (or "all models allowed — dev default"), chat timeout,
  summarize + embedding models, deployment-default daily budget (unset/0 =
  unlimited).

## 4. API additions

| Endpoint | Method | Notes |
|---|---|---|
| `/v1/admin/users` | POST | **Enrollment.** Body `{email, display_name?, role?, status?, inference_daily_token_budget?}`; 422 on unknown fields (house envelope style); 409 if email taken; creates a row with `oidc_sub NULL`. `role/status` validated against the existing CHECK constraints. Admin-gated. |
| `/v1/admin/users/{id}` | DELETE | 204. Rails: cannot delete self; cannot delete the **last active admin**; cannot delete the **seed admin row** (`00000000-…-0001`) — it owns all pre-multi-user documents and is the dev-bypass identity; deleting it bricks the dev bypass and orphans legacy docs. 409 with a machine-readable reason otherwise. |
| `/v1/admin/inference/config` | GET | Read-only view of `model_router.get_config()` (no secrets — none exist in it). Admin-gated. |
| `/v1/admin/inference/usage` | GET | Exists (gateway work). No change. |
| `/v1/admin/users/{id}` | PATCH | Exists. No change — the console just has to respect `exclude_unset` semantics. |

**Deletion data model — the one real decision.** `users` rows are referenced by
`sessions`, `personal_access_tokens`, `inference_usage` (all `ON DELETE
CASCADE` — deleting a user cleanly kills credentials and accounting) and by
`documents`/`chat_sessions` (`user_id … ON DELETE CASCADE`). **Deleting a user
deletes their documents and chat history**, including chunk rows and the stored
PDFs (chunks cascade via documents; PDF files on disk need an explicit sweep —
the storage layout is `data/pdfs/{doc_id}` keyed by the sha, so either sweep
paths from the deleted docs before row deletion, or accept orphans with a note).
The spec takes the simple, honest line:

- **Delete = full wipe** (documents and sessions cascade). The confirm dialog
  says this in plain words: *"This permanently deletes this user's documents
  and chat history."*
- Before the row delete, the endpoint collects the user's `doc_id`s and removes
  their PDF files best-effort (log failures; rows are already gone by then, so
  a failed unlink is only a disk leak, not a correctness issue).

## 5. User lifecycle & propagation policy

The operational answer to "how do we manage users?" — one table, three flows,
built on the live-verified propagation rules (IDENTITY_AND_ROLES.md):

**Division of ownership.** The IdP (Keycloak) owns **credentials** — who can
log in, passwords, MFA. The app owns **accounts** — role, status, budgets,
document ownership. Nothing syncs between them; the only bridge is the
`(iss, sub)` claim pair recorded at first login. That is a feature (IdP-agnostic,
no drift), provided the app-side lifecycle tools exist — which is this spec.

| Flow | Steps | Why this order |
|---|---|---|
| **Onboard (enroll)** | Admin creates a pre-provisioned row (email + role + budget + status) → user logs in with that exact **verified** email → resolver branch 2 claims the row, inheriting role/status/budget | Account decisions precede the first login; no "pending, activate later" race. (Alternative, unchanged: user logs in cold → JIT `pending` row → admin activates.) |
| **Change permissions** | Admin toggles role or budget in the console (`PATCH`) | Takes effect on the user's **next request** — the Principal is rebuilt from the DB row per request; no token/session invalidation needed. Their open tab re-probes on next page load. |
| **Offboard** | 1. **Disable in the app** (hard-revokes sessions; blocks PATs) → 2. delete the IdP user in Keycloak if desired → 3. **Delete the app row** to free the email and wipe data | Live-verified: skipping step 1 leaves active sessions working; skipping step 3 permanently 409-blocks the email for any future IdP user with that address. |

**Permissions model (unchanged by this spec).** Coarse by design: `admin`
(user management + inference governance) vs `member` (own documents, chat,
TTS), enforced by `require_admin` on `/v1/admin/*` and by row-ownership 404s
everywhere else. The only resource-scoped permission today is the inference
budget (per-user daily tokens, 429-enforced). If a third capability tier is
ever needed (e.g. "reader only, no chat"), it's a new `role` CHECK value +
gates at the routers — a migration, deliberately out of scope here.

## 6. Security & invariants

- Every new endpoint behind `require_admin` (existing dependency, no new
  authz machinery).
- Last-admin and seed-row rails are enforced **server-side** (the UI hiding is
  cosmetic; the API must be safe against direct calls).
- Delete is idempotent-ish: missing id → 404 (indistinguishable from
  never-existed — no enumeration signal, matching the ownership-404 doctrine).
- Usage/config endpoints must not expose other users' emails to non-admins —
  they're admin-gated; fine by construction.
- The console itself must render nothing privileged when
  `user.role !== 'admin'` — the view simply isn't mounted (and `viewMode`
  coercion on boot, §3).

## 7. Test plan (TDD outline — mirror of the gateway plan's style)

Backend (Postgres-backed, `db_conn` harness):

1. DELETE user: happy path (204, row gone, sessions/PATs/usage cascaded).
2. DELETE self → 409.
3. DELETE last active admin → 409.
4. DELETE seed admin UUID → 409.
5. DELETE non-existent → 404.
6. DELETE removes the user's `data/pdfs/{doc_id}` files (tmp storage dir).
7. GET config: returns parsed allowlist/task models/budget; member → 403.
8. Existing admin router tests keep passing (list/patch/usage).
9. POST enroll: creates unlinked row with given role/status/budget; duplicate
   email → 409; member → 403; bad role/status → 422.
10. Enroll → login claim: `resolve_or_provision_user` with a **verified**
    matching email claims the row **and preserves** its role/status/budget;
    unverified email still rejected (branch-2 guard, already tested in
    `test_auth_users.py` — extend, don't duplicate).

Frontend (vitest + Testing Library, `apiFetch` mocked):

11. View switcher renders shield only for admin; member never sees it.
12. Persisted `admin` viewMode for a member coerces to `reader` on boot.
13. Users section: renders rows, PATCHes budget with **absent** field when
    untouched; `0` vs empty distinction preserved.
14. Delete flow: confirm requires typed email; calls DELETE; toast on success;
    self-row shows no Delete.
15. Enroll form: happy path POSTs and shows "awaiting first login" badge;
    duplicate-email 409 renders the error.
16. Usage section: renders date rows (ISO date string shown as-is), days
    selector sends `?days=`.
17. Config section: renders read-only values; empty allowlist renders
    "all models allowed".

Manual pass checklist (browser):

18. Admin: enter console, edit a budget, verify the ChatSidebar meter reflects
    it on the affected user's next `/v1/inference/models` refresh.
19. Enroll a user → have them log in with the matching email → they inherit
    the chosen role/budget (no pending screen).
20. Delete a throwaway user end-to-end (verify 409-email is freed: a new
    Keycloak user with the same email can now log in).
21. Demote self attempt → blocked; demote other admin → reflected on their
    next page load.

## 8. Rollout

- Branch `feat/admin-console` off the integration line after the gateway
  branch merges; the gateway's admin endpoints (usage/PATCH budget) are its
  foundation — building before that merge would fork.
- Migration: **none required** (no schema change — delete uses existing
  cascades; enroll uses the existing nullable `oidc_sub`; if the PDF-sweep or
  soft-delete decision changes, revisit).
- Docs to touch in the same PR: USER_GUIDE admin section (Enroll + Delete,
  the offboarding order becomes fully self-serve), IDENTITY_AND_ROLES
  (orphan-remedy paragraph → point at the console), TESTING.md (this spec's §7
  becomes a checked-off matrix), CHANGELOG.

## 9. Open questions

- **Soft delete / retention?** Some orgs want disable-only policies (legal
  hold). If that surfaces as a need, add `status='deleted'` + hide-from-list
  rather than row deletion. Default now: hard delete, clearly worded.
- **One-click Keycloak user creation from the enroll form** (calling the
  Keycloak Admin API so the admin never opens the KC console): possible
  follow-up behind a Keycloak-specific feature flag (`KEYCLOAK_ADMIN_*` env,
  service-account token). Deliberately out of scope — it breaks IdP-agnosticism
  and stores IdP admin credentials in the app.
- Should the usage table gain a **per-user totals row / CSV export**? Cheap to
  add server-side (the endpoint already returns rows); defer until someone
  asks.
- **Session page for "active sessions" admin visibility** (list/revoke per
  user) — tempting, but low priority; the disable hard-revoke already covers
  the security need.
- **A third role tier** ("reader only, no chat") — see §5; only if a real
  need appears.
