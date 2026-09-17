# Session handover

> Working notes (now tracked in git). Latest session on top.

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
