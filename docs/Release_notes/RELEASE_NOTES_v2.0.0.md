# v2.0.0 — Multi-user, authenticated Natural Reader

The single-user reader becomes a **hosted, multi-tenant application**. Every API
route now requires a signed-in principal, chat is brokered through a server-side
inference gateway with per-user budgets, documents gain a projects/grants sharing
model with a browsable Library, and the SPA is reorganized around a dedicated
Settings page and a profile menu. This is a **major release** — the API-auth
change is breaking for any pre-1.9.0 open deployment (see [Upgrade notes](#upgrade-notes)).

## Added

### 🔐 Multi-user accounts & sign-in (OIDC)

The backend is now multi-user. Sign in through any discovery-based OIDC provider
(Keycloak in the reference deployment), **PKCE S256**. First login becomes the
admin; everyone else lands `pending` until an admin activates them. Sessions are
**DB-backed** (a random cookie token, only its sha256 stored) and there are
**personal access tokens** (`nrp_…`, sha256-at-rest, shown once) for the browser
extension and scripts. Documents and chat sessions are **owned per user** — other
people's rows are `404`s, never `403`s, so existence can't be enumerated. Admins
activate/disable/promote; disabling hard-revokes every session immediately.

### 🎫 Capabilities (reader / chat / admin)

Feature access is no longer one `role` column. `reader`, `chat`, and `admin` are
**Keycloak realm roles** mirrored into `users.capabilities` and enforced
**deny-by-default** on the feature routes. The SPA reads them from
`/v1/auth/me`, gates the Reader/Chat/Admin views, and re-probes on a
`403 missing_capability`; an active user with no capabilities gets a distinct
"access not yet granted" screen instead of a broken app. Admin enroll creates the
Keycloak user (invite or one-time temp password, graceful fallback when SMTP is
absent), edits capabilities, and disables/deletes — all propagated to Keycloak,
with a **last-active-admin guard** on every path that could remove the final
admin, failing hard (never fail-open) if the Keycloak write errors.

### 🚦 Inference gateway + per-user budgets

Chat no longer talks to Ollama from the browser. It goes through authenticated
backend endpoints (`GET /v1/inference/models`, `POST /v1/inference/chat`) — a
strictly validated envelope (unknown fields → 422; `/api/pull` & co. unreachable)
with **byte-faithful NDJSON streaming** so the tool loop, thinking traces, and
image attachments are unchanged. `INFERENCE_MODELS` caps what's served.
`INFERENCE_DAILY_TOKEN_BUDGET` sets a per-user daily allowance (real token counts,
UTC-midnight reset); over-budget → 429 with a "N tokens left today" meter and a
disabled send button. Budget checks **fail open** if Postgres is down — chat never
dies with the DB. An **Inference source: Server | Local Ollama** switch keeps the
old direct-to-Ollama mode for local use.

### 📚 Document Library (projects & grants)

Documents move from strictly private to **shareable**. A document is readable by
its owner, by members of a project it's assigned to, or by an explicit per-document
grantee — writes stay owner-only, and a non-reader still gets a `404`. New
endpoints back it: `GET /v1/docs` (own / member / granted, with `q`/project/tag
filters), `PATCH /v1/docs/{id}` (tags, project assignment, admin reassignment),
owner-only project CRUD, project members, and per-doc read grants — with IDOR
guards on cross-tenant project assignment. The SPA gains a **Library** view (in the
top switcher): list, search, project/tag filters, share indicators, and optional
project + tags on upload/register. Design: [../LIBRARY.md](../LIBRARY.md).

### ⚙️ Settings page + profile menu (app shell)

Voice & reading, chat & inference (with a per-model inference selector),
connection, appearance, and account (personal access tokens) settings —
previously scattered across the reader and chat sidebars — now live on **one
dedicated Settings view**, reached from a header **gear button** and a new
**profile menu** (identity, Settings, dark-mode toggle, log out). The reader
sidebar keeps navigation only; the chat sidebar keeps the model picker, sessions,
and the budget meter. The chat composer draft also moved up to `App`, so it now
**survives switching tabs** (and a reload).

### 🌐 Production deployment (nginx + TLS + Keycloak)

New guide [../DEPLOYMENT.md](../DEPLOYMENT.md): SPA + backend on one origin with
Keycloak on its own, the five config values that must agree, and the traps (KC
proxy headers, live-realm ≠ export, `COOKIE_SECURE`, `client_max_body_size`).
`./startup.sh up-with-dev-auth` runs a full local OIDC rig (Keycloak seeded from a
checked-in realm, realm state persisted in the shared Postgres). The nginx `/api/`
block is gone — with the gateway live, Ollama is backend-only.

## Changed

- **Breaking: every API route now requires authentication.** Pre-1.9.0 the
  backend was open; now `/v1/*` resolves a principal from the session cookie or a
  Bearer PAT and denies by default. Open deployments must set `AUTH_ENABLED=false`
  (loopback only) or provision tokens. The SPA must be **same-origin** with the
  backend (blank `apiHost`); a stale `localhost` value is migrated away on boot.
- **nginx: delete the `/api/` block.** Ollama becomes backend-only;
  `proxy_buffering off` now matters on `/v1/` (that's where NDJSON streams).
- Server-side summarize (web_search) and embedding calls route through
  `model_router` instead of reading env vars in each service.

## Fixed

- **Founder lockout on a fresh Keycloak realm** — the checked-in realm now assigns
  `admin-user` the `reader`/`chat`/`admin` realm roles directly; trap + recovery
  runbook documented (DEPLOYMENT Trap 4).
- **OIDC callback 500 on an IdP error redirect** — the callback catches
  `OAuthError` and redirects to the SPA instead of 500-ing.
- **Logout "Invalid redirect uri"** — the space-delimited post-logout URI list was
  parsed as one bogus value; delimiter fixed and the login-vs-logout drift
  documented (DEPLOYMENT Trap 2).
- **Library view bounced back to reader** — `useViewModeGuard` now recognizes
  capability-free views (`CAP_FREE_VIEWS`).
- **Reader toolbar overflowed off-screen on mobile** — secondary actions collapse
  into a "⋯ More" dropdown below the `md` breakpoint.
- **Enroll / delete buttons gave no click feedback** — both now disable and show a
  spinner while their request is in flight (also blocks double-submits).
- **Chat draft lost when switching tabs** — the composer draft moved up to `App`
  and is persisted.

## Upgrade notes

- **New migrations `005`–`009`** apply automatically on boot (numeric order, one
  transaction each): users + ownership, sessions + PATs, inference budgets,
  `users.capabilities`, and the Library (projects, members, doc_grants,
  `documents.project_id`/`tags`). No manual step.
- **Auth is on by default.** For a single-user/loopback box, set
  `AUTH_ENABLED=false` (refused on a non-loopback bind). For a real deployment,
  configure the OIDC vars and a strong `SESSION_SECRET` — see
  [../DEPLOYMENT.md](../DEPLOYMENT.md) and [../IDENTITY_AND_ROLES.md](../IDENTITY_AND_ROLES.md).
- **The SPA must be same-origin with the backend** (blank `apiHost`) — the only
  cookie-compatible setup.
- **New Python deps** for auth (`authlib`, `python-jose`/Keycloak admin client,
  test deps). Run `./startup.sh init` or `.venv/bin/pip install -r requirements.txt`.
- **`package.json` bumped** `1.9.0` → `2.0.0`.

## Not yet included (planned for 2.1)

Background audio across tab switches, the projects/permissions admin UI, Library
RAG retrieval beyond Phase 0, and OpenRouter/vLLM inference backends.

## Full changelog

[CHANGELOG.md#200---2026-09-23](../../CHANGELOG.md#200---2026-09-23) · diff:
[v1.9.0…v2.0.0](https://github.com/udaravima/natural-reader/compare/v1.9.0...v2.0.0)
