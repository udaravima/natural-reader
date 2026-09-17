# Session handover

> Working notes (now tracked in git). Latest session on top.

---

## 2026-09-17 (later still) — Keycloak↔app propagation live-verified; user guide written

**Branch:** `feat/model-router-gateway` · docs + live verification only · **Tests:** unchanged (143/163, lint clean).

### The question
Does creating/deleting a user in Keycloak propagate to the app? **Live-verified end-to-end** against the running rig (Postgres + Keycloak + backend auth-on + Vite), using the Keycloak Admin API + a full scripted OIDC login dance (curl through `/v1/auth/login` → KC form → `/v1/auth/callback` → `nr_session`). Results:
- **Create in KC → nothing happens app-side until first login.** Verified: created `prop-test`, users table unchanged; after the login dance, the row appeared as `pending`/`member` with `oidc_sub` = the KC UUID (JIT provisioning).
- **Delete in KC → NOTHING propagates.** Verified: row stayed `active`, and the user's **existing session still worked** (`/v1/auth/me` 200 after KC deletion — the app never re-checks the IdP).
- **The email trap is real:** a second KC user with the same email got **409 "email already linked to another identity"** at login. And the dev DB already contained a **real orphan of this class**: `aakash.n@…` is `active` in the app but has no Keycloak identity at all (its sub has no `user_entity` row) — this is what happens when KC deletion happens without app-side disable, or a leftover from the H2-era store.
- **Correct offboarding order: disable in the app first (hard-revokes sessions), then delete in Keycloak.** Documented in IDENTITY_AND_ROLES.md + USER_GUIDE.md.

### Also this session
- `docs/USER_GUIDE.md` — end-user guide (sign-in states, PATs & why they exist — "tokens are on the Account panel, not Admin", admin buttons, Server/Local source, budget meter/429/UTC-midnight, FAQ). Linked from README.
- Test artifacts cleaned up: prop-test + prop-test2 removed from both stores (KC natural-reader realm back to just `udara.v`; app back to 2 rows).
- Notes for replaying the dance: Keycloak's login form `action` URL needs `&amp;` → `&` unescaping; header matching must be case-insensitive (`Location:` from KC vs `location:` from uvicorn); form session codes are one-time — the whole login dance must run in one shot; KC Admin API access tokens expire in ~60s; Admin-API-created users hit `VERIFY_PROFILE` unless `firstName`/`lastName` are set. Master-realm admin password is not the bootstrap `admin/admin` (user-changed).

### Next (unchanged from below, plus)
- Admin-side **user deletion** (to free blocked emails / clean orphans) is now a motivated follow-up alongside the `viewMode: 'admin'` panel.

---

## 2026-09-17 (later) — identity deep-dive doc + gateway live verification on `feat/model-router-gateway`

**Branch:** `feat/model-router-gateway` · **Tests:** backend 143, frontend 163, lint clean · **Working tree:** docs only.

### What happened
- User commit `286fa41` ("Keycloak Presistent settings") moved the realm out of Keycloak's embedded H2 into the shared Postgres (schema `keycloak`, seeded by `deploy/postgres/init/` on first volume init). This fixed the recurring 409 "email already linked to another identity" desync: with H2, every container recreation minted new `sub` UUIDs, so returning users stopped matching `users.oidc_sub`.
- Wrote **`docs/IDENTITY_AND_ROLES.md`** — the deep-dive answering "how do Keycloak users map to natural_reader users": two stores (Keycloak schema = credentials, `public.users` = accounts) joined only by the mirrored `(oidc_iss, oidc_sub)` pair; the three `resolve_or_provision_user` branches and their guards; roles live in the app (NOT Keycloak claims) and take effect immediately (Principal rebuilt from the users row per request); credentials (sessions/PATs, sha256-only); special identities (seed admin, dev bypass); and a **proposal** for a third `viewMode: 'admin'` (gated on `role === 'admin'`) to house users + the still-UI-less inference usage/budget endpoints. Cross-linked from ARCHITECTURE.md § auth.
- **Task 13's manual pass (scripted subset) — done and green**, against a live backend (dev bypass) + real Ollama: models endpoint (allowlist filters to `gemma4:latest`; budget key with UTC-midnight `reset_at`), allowlist 422, real NDJSON streaming (thinking chunks byte-faithful), `INFERENCE_DAILY_TOKEN_BUDGET=1` → models shows `remaining_tokens: 0` + chat **429 with structured detail** (accounting had already recorded the earlier stream — recording is unconditional, budget only gates), tool_calls passthrough (gemma4 emitted a proper `current_time_date` call), `GET /v1/admin/inference/usage` (today's 27 tokens for the seed admin — the row's email is the user's real one, i.e. already linked via `BOOTSTRAP_ADMIN_EMAIL`/first-login).
- Not yet human-clicked: the browser-side bits (Server/Local dropdown, budget meter, 429 toast, disabled send in local-mode parity). Unit-tested; final confidence click-through still open.

### Next
- **AdminPanel follow-up** (now designed in IDENTITY_AND_ROLES.md § last): `viewMode: 'admin'` surface with the usage view + per-user budget knob (`PATCH /v1/admin/users/{id}`; `day` from the usage endpoint is an ISO date, not a timestamp; absent field ≠ null in the PATCH semantics).
- Document-library RAG (unblocked by the gateway), then sub-project D.
- `origin/feat/model-router-gateway` is behind local — push only when asked.

---

## 2026-09-17 — Model-router gateway (sub-project E) built on `feat/model-router-gateway`

**Branch:** `feat/model-router-gateway` (branched off `feat/security-hardening` line) · **Tests:** backend 143, frontend 163, lint clean · **Working tree:** clean at last commit.

### What happened
- Implemented the full [gateway spec](docs/superpowers/specs/2026-09-17-model-router-gateway-design.md) (all phases E1–E3) via the 13-task TDD plan ([docs/superpowers/plans/2026-09-17-model-router-gateway.md](docs/superpowers/plans/2026-09-17-model-router-gateway.md)).
- **The pipe (E1):** new `server/routers/inference.py` — `GET /v1/inference/models` + `POST /v1/inference/chat` behind `get_current_user`; strictly validated envelope (`extra="forbid"` down to per-message fields, incl. `images` + `tool_calls`); byte-faithful NDJSON passthrough (uses `client.send(stream=True)` + closes in the generator's `finally` — the StreamingResponse trap); upstream errors forwarded status+body so the SPA's think/tools fallback chain still branches correctly. Frontend: `src/lib/chatTransport.js` seam, `useChatEngine`'s four `/api/chat` sites collapsed into `callChat()`, model list via the gateway, persisted **Inference source: Server | Local Ollama** setting in ChatSidebar.
- **The router (E2):** `server/services/model_router.py` owns everything — `INFERENCE_MODELS` allowlist (422 otherwise), task models (`SUMMARIZE_MODEL` w/ `WEB_SEARCH_SUMMARY_MODEL` fallback, `EMBEDDING_MODEL`), `INFERENCE_TIMEOUT_S`, `INFERENCE_DAILY_TOKEN_BUDGET`. `embeddings.py` / `web_search.py` / `docs.py` (embedding metadata) all read from it now — no service reads Ollama env directly anymore.
- **The budgets (E3):** migration `007_inference_budgets.sql` (`inference_usage` PK (user_id, day) — **day computed in Python, UTC**, no SQL default; `users.inference_daily_token_budget` NULL=default/0=unlimited), `inference_budget.py` service, 429 pre-check + `_UsageTap` stream accounting (only counts completed generations; fresh pooled conn in the generator `finally`; fail-open on DB errors). Admin: `GET /v1/admin/inference/usage` + budget field on the user PATCH (`exclude_unset` semantics: absent=untouched, null=clear). Frontend: budget rides `/v1/inference/models`, meter in ChatSidebar, 429 intercepted in `callChat` BEFORE the retry chains, send disabled at zero.
- **Docs/config:** `.env.example` gateway section; README (new Inference Gateway API table, threat-model `/api/*` rows removed, nginx `/api/` block deleted everywhere + `proxy_buffering off` moved to `/v1/`); CHANGELOG `[Unreleased]`.

### Mid-session incident (resolved)
- The branch was accidentally switched to `master` mid-implementation. **No commits were lost** — all were on `feat/model-router-gateway`; only uncommitted Task-6 scratch files were wiped by the force-checkout and were recreated. Note: `searxng/` is owned by a container uid; checkout errors there are fixed with `git checkout -f` (the files are identical).

### Not verified here / next
- **The live browser walk-through (plan Task 13 step 5) was NOT done** — needs Ollama + a real model: server-mode dropdown/streaming, tool loop through the gateway, image attach, local-mode parity, allowlist, and a budget-exhaustion 429. All of these are covered by unit tests with a mocked upstream; the manual pass is a final confidence check.
- **AdminPanel UI** doesn't surface the usage view / budget knob yet (API only). Small follow-up.
- **Document-library RAG is now unblocked** — its Phase 2 description generation routes through `model_router`'s `summarize` task, and Phase 3's multi-round loop checks budget via the 429 detail.
- Then sub-project **D** (containerization/prod Keycloak/TLS).
- The **admin usage endpoint returns `day` as a date object** — FastAPI serializes it; if the SPA later renders it, remember it's ISO date, not timestamp.

### How to run / test
- Postgres must be up: `env -u XDG_DATA_HOME podman-compose up -d postgres` (container stops between sessions).
- Backend: `.venv/bin/pytest server/tests/` (143). Frontend: `npm run test:run` (163), `npm run lint`.
- Manual: `./startup.sh up` (loopback dev bypass = seed admin) + `npm run dev`; chat defaults to Server mode → `/v1/inference/*`.

---

## 2026-09-17 — SPA auth UI + local OIDC rig built; merged onto `feat/security-hardening`

**Branch:** `feat/spa-auth-ui` (off, and merged back into, `feat/security-hardening`) · **Tests:** backend 92, frontend 150, lint clean · **Working tree:** clean.

### What happened
- Built the **SPA auth UI** — the frontend half of multi-user, so the app is usable through the browser (before this it only ran with `AUTH_ENABLED=false`). Followed the 9-task TDD plan ([docs/superpowers/plans/2026-09-17-spa-auth-ui.md](docs/superpowers/plans/2026-09-17-spa-auth-ui.md)) implementing the [spec](docs/superpowers/specs/2026-09-16-spa-auth-ui-design.md).
- **Pieces:** a `src/utils/apiFetch.js` credential seam (every `/v1` call sends the cookie; a 401 anywhere fires a global handler → back to the login gate); `src/hooks/useAuth.js` state machine (`loading/anonymous/pending/disabled/active/error` from the `/v1/auth/me` probe); `src/components/auth/AuthGate.jsx` + screens; App gated behind it; migrated **every** `/v1` fetch across App/useTtsEngine/lib/MarkdownReader to `apiFetch` (grep-audited); `AccountPanel` (PAT create/list/revoke) and admin-only `AdminPanel` (activate/disable/role) as new collapsible **Sidebar** sections.
- **Backend:** one change — the not-active `403` now carries structured `detail.status` (`pending`/`disabled`) so the SPA branches the gate screens.
- **Local OIDC rig:** `docker-compose.yml` gains a **Keycloak** service (host port **18080**, `start-dev`, imports `deploy/keycloak/realm-export.json`); a **Vite dev proxy** makes `:5173` same-origin with the backend; `deploy/nginx/natural-reader.conf` is a reference site for the prod-like path; `deploy/README.md` has the two-user walkthrough. Verified Keycloak imports the realm and serves correct discovery (`issuer: http://localhost:18080/realms/natural-reader`).

### Project state
- **A + B+C + SPA UI now all on `feat/security-hardening`.** Still **not pushed and not merged to master** (per the standing "keep everything here until the UI and all is done" instruction).
- **The live browser login flow was NOT walked here** — infra (realm import, discovery) and all unit tests are verified, but the human two-user click-through in `deploy/README.md` still wants doing before this goes near master.

### Not done / next
- Wire the **browser extension** to send a PAT (separate repo — the Account panel now produces the token). Until then the shipped extension breaks under `AUTH_ENABLED=true`.
- Sub-project **E** (server-side model-router gateway) and **D** (full containerization / prod Keycloak / TLS / secrets) — still pending. See [memory/project_multiuser_hardening.md].

### How to run
- `deploy/README.md` is the source of truth. Short version: `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres keycloak`; backend with the OIDC env from `.env.example`; `npm run dev`. The pending screen needs a **second** Keycloak user (first login always becomes admin).

---

## 2026-09-16 — Multi-user auth+authz backend done; merged A+B+C onto `feat/security-hardening`

**Branch:** `feat/security-hardening` (integration branch — both sub-projects live here now) · **Tip:** `c9b4949` · **Working tree:** clean · **Tests:** 91 backend passing (needs Postgres up).

### What happened
- **Sub-project B+C (multi-user auth + authz) — backend complete.** 16-task TDD plan ([docs/superpowers/plans/2026-09-14-multiuser-auth-authz.md](docs/superpowers/plans/2026-09-14-multiuser-auth-authz.md)) all green. New `server/auth/` package (config, users+JIT provisioning, DB-backed sessions, personal access tokens, principal-resolution deps, row-ownership `authz`, OIDC RP client), routers `auth`+`admin`, migrations `005` (users + per-user ownership) / `006` (sessions + PATs), ownership guards wired into docs/chat-sessions/tools/TTS, app wiring + startup guard + `bootstrap_admin`.
- **Two rounds of background security-review findings fixed.** Round 1 (4 findings) in `9cacf8f`: TOCTOU seed-claim → atomic UPDATE, `email_verified` gate on email-match provisioning, dev-bypass keyed to the real bind host via `ipaddress`, session hard-revoke on user disable. Round 2 (1 HIGH) in `c9b4949`: the `SessionMiddleware` secret no longer falls back to a hardcoded `"dev-insecure-change-me"` — `startup_guard` refuses to boot a non-loopback bind without a strong (>=32 char) `SESSION_SECRET`; loopback dev degrades to an ephemeral per-process secret.
- **Integrated:** fast-forward-merged `feat/multiuser-auth` (22 commits, `454f2f4..c9b4949`) into `feat/security-hardening`. Sub-project A (vuln hardening, commit `21e6e39`) was already the base of that branch. Both branches now point at `c9b4949`.
- **Cleaned up a stray git state:** an in-progress cherry-pick of `defcf03` ("upgrade vulnerabilities on npm packages: humanfs", from `origin/development`) was stuck on a `package-lock.json` conflict and was aborted. **Loose end:** that npm-audit fix still needs to land on this line — bring it in properly via a `development` → `master` merge, not a cherry-pick onto a backend branch.

### Project state
- **A + B+C are done on `feat/security-hardening`, not pushed and not merged to `master`.** Awaiting the push/PR decision.
- **SPA is NOT auth-wired yet.** The frontend has no login screen / token panel and doesn't send credentials, so it only works with `AUTH_ENABLED=false` (which `startup_guard` permits *only* on a loopback bind). A follow-up plan is needed before the SPA is usable multi-user.
- Sub-projects **E (server-side model-router gateway)** and **D (containerize + proxy + secrets)** are still pending — see [memory/project_multiuser_hardening.md] and the locked design decisions there.

### How to run / test
- Start Postgres (snap-podman env gotcha — the `env -u` is required):
  `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres` (container **stops between sessions** — restart it; `env -u XDG_DATA_HOME podman ps` to check).
- Backend suite: `.venv/bin/python -m pytest server/tests/` (test DB `natural_reader_test`, per-test rollback; router tests use `httpx.AsyncClient`+`ASGITransport`, never `TestClient`).
- New env vars in [.env.example](.env.example): `AUTH_ENABLED`, `OIDC_*`, `SESSION_SECRET` (mandatory for exposed binds), `COOKIE_*`, `BOOTSTRAP_ADMIN_EMAIL`.

### Next step / where to resume
- **Decide integration for `feat/security-hardening`:** push + open a PR to `master`, or keep local. (Push/merge to `master` still needs explicit per-action approval.)
- Then: brainstorm → spec → plan the **SPA login-UI** follow-up (login screen, `credentials:'include'`, PAT management panel) so the frontend actually uses the new auth.
- Then sub-project **E** (model router), then **D** (containerize).

---

## 2026-09-14 — Understood project; captured document-library RAG suggestion

**Branch:** `development` · **Version:** v1.9.0 (`ed83a3f`) · **Working tree:** was clean at start; this session adds two untracked/ignored docs (see below).

### What happened
- Read the project end-to-end to understand the RAG/chat architecture (README, [CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md), the `superpowers/specs` + `plans`, and the actual code paths).
- Captured a new feature idea — **ask chat across a library of many indexed docs (multi-project, with a document-routing step and multi-round retrieval)** — as a rich, analyzed suggestion:
  **[docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md](docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md)**
- No code changed. No spec written, no plan, nothing approved to build.

### Project state (as understood this session)
- **What it is:** local-first reader — Kokoro ONNX TTS + Ollama chat side panel, fused into "Chat with PDF" (v1.6.0+). FastAPI + Postgres/pgvector backend, React/Vite frontend. Fully local (Ollama does chat *and* embeddings).
- **Retrieval today is single-doc:** index the open doc → `nomic-embed-text` 768-dim chunks in `doc_chunks` → model gets a `search_document` tool → **one** autonomous round-trip.
- **Tool loop is capped at 1 round** on purpose ([useChatEngine.js:694-763](src/hooks/useChatEngine.js#L694-L763)); follow-up call drops `tools`.
- Recent shipped work (git log): file logging + license/contributing + v1.9.0 release; SearXNG-backed `web_search` tool (first backend `/api/generate` path, in `services/web_search.py`); hardcoded doclin support.

### Key file map (for the suggested feature)
| Concern | File |
|---|---|
| Tool registry (add tools here) | [src/lib/chatTools/index.js](src/lib/chatTools/index.js) |
| Existing single-doc search tool + `when` gate | [src/lib/chatTools/searchDocument.js](src/lib/chatTools/searchDocument.js) |
| Chat engine + tool loop + round cap | [src/hooks/useChatEngine.js](src/hooks/useChatEngine.js) |
| Docs + search endpoints (`WHERE doc_id`) | [server/routers/docs.py](server/routers/docs.py) |
| Schema (documents, doc_chunks, HNSW) | [server/sql/001_init.sql](server/sql/001_init.sql) |
| Backend generation pattern to reuse | [server/services/web_search.py](server/services/web_search.py) |

### Next step / where to resume
- Decision made this session: capture as suggestion (done), backlog lives under `docs/superpowers/specs/`.
- When building: start **Phase 1 (corpus search, no routing)** from the suggestion doc, via brainstorming → spec → plan. Phases 2 (descriptions + routing tool) and 3 (budgeted multi-round loop) follow.
- Open decisions still to make (all listed in the suggestion): project = column vs. table; routing = semantic vs. list-and-pick; round budget; descriptions LLM-generated vs. editable.
