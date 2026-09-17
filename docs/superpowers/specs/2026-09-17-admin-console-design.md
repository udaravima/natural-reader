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
- Keycloak management (creating IdP users stays in the IdP's console; the app
  remains discovery-based and IdP-agnostic).
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
  Demote, and Delete — extending today's lockout guard).
- **Budget field** per user: empty = deployment default (rendered as a
  placeholder), `0` = unlimited, number = tokens/day. PATCH semantics: the
  field is **omitted** from the request when untouched (never serialized as
  `null` by accident — `null` explicitly clears). UI hint text must say
  "empty = default, 0 = unlimited".
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

## 5. Security & invariants

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

## 6. Test plan (TDD outline — mirror of the gateway plan's style)

Backend (Postgres-backed, `db_conn` harness):

1. DELETE user: happy path (204, row gone, sessions/PATs/usage cascaded).
2. DELETE self → 409.
3. DELETE last active admin → 409.
4. DELETE seed admin UUID → 409.
5. DELETE non-existent → 404.
6. DELETE removes the user's `data/pdfs/{doc_id}` files (tmp storage dir).
7. GET config: returns parsed allowlist/task models/budget; member → 403.
8. Existing admin router tests keep passing (list/patch/usage).

Frontend (vitest + Testing Library, `apiFetch` mocked):

9. View switcher renders shield only for admin; member never sees it.
10. Persisted `admin` viewMode for a member coerces to `reader` on boot.
11. Users section: renders rows, PATCHes budget with **absent** field when
    untouched; `0` vs empty distinction preserved.
12. Delete flow: confirm requires typed email; calls DELETE; toast on success;
    self-row shows no Delete.
13. Usage section: renders date rows (ISO date string shown as-is), days
    selector sends `?days=`.
14. Config section: renders read-only values; empty allowlist renders
    "all models allowed".

Manual pass checklist (browser):

15. Admin: enter console, edit a budget, verify the ChatSidebar meter reflects
    it on the affected user's next `/v1/inference/models` refresh.
16. Delete a throwaway user end-to-end (verify 409-email is freed: a new
    Keycloak user with the same email can now log in).
17. Demote self attempt → blocked; demote other admin → reflected on their
    next page load.

## 7. Rollout

- Branch `feat/admin-console` off the integration line after the gateway
  branch merges; the gateway's admin endpoints (usage/PATCH budget) are its
  foundation — building before that merge would fork.
- Migration: **none required** (no schema change — delete uses existing
  cascades; if the PDF-sweep or soft-delete decision changes, revisit).
- Docs to touch in the same PR: USER_GUIDE admin section (Delete button,
  the offboarding order becomes fully self-serve), IDENTITY_AND_ROLES
  (orphan-remedy paragraph → point at the console), TESTING.md (this spec's §6
  becomes a checked-off matrix), CHANGELOG.

## 8. Open questions

- **Soft delete / retention?** Some orgs want disable-only policies (legal
  hold). If that surfaces as a need, add `status='deleted'` + hide-from-list
  rather than row deletion. Default now: hard delete, clearly worded.
- Should the usage table gain a **per-user totals row / CSV export**? Cheap to
  add server-side (the endpoint already returns rows); defer until someone
  asks.
- **Session page for "active sessions" admin visibility** (list/revoke per
  user) — tempting, but low priority; the disable hard-revoke already covers
  the security need.
