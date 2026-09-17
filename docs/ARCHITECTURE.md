# Architecture — Developer Guide

This document explains how Neural Reader is put together: every component, what it controls, and — most importantly — why the pieces are shaped the way they are. Read it top-down; each section builds on the previous one.

- [The mental model](#the-mental-model)
- [The one rule: same-origin `/v1`](#the-one-rule-same-origin-v1)
- [Frontend](#frontend)
  - [App.jsx — the hub](#appjsx--the-hub)
  - [Hooks](#hooks)
  - [Persistence layer (lib/)](#persistence-layer-lib)
  - [Utils](#utils)
  - [Components by group](#components-by-group)
- [Backend](#backend)
  - [App wiring](#app-wiring)
  - [Auth subsystem](#auth-subsystem)
  - [Routers](#routers)
  - [Services](#services)
  - [TTS pipeline](#tts-pipeline)
  - [Database & migrations](#database--migrations)
- [End-to-end flows](#end-to-end-flows)
- [Local development](#local-development)
- [Invariants & traps](#invariants--traps)

---

## The mental model

Neural Reader is a PDF/e-book reader with TTS read-aloud and an RAG-capable chat side. At runtime there are up to **five moving parts**:

| Process / container | Port (dev) | Role |
|---|---|---|
| Vite dev server (SPA) | 5173 | React frontend + **reverse proxy** for `/v1` and `/api` |
| FastAPI backend (`run.py`) | 8000 | TTS synthesis, doc RAG, chat-session storage, auth, tools |
| Postgres + pgvector | 127.0.0.1:5433 | users, sessions, documents/chunks+embeddings, chat sessions |
| Ollama | 11434 | chat model + `nomic-embed-text` embeddings + web-search summarizer |
| SearXNG / Keycloak | 127.0.0.1:18043 / 18080 | web-search backend / local OIDC provider (dev rig) |

The frontend is a **single-page app with no router** (`src/App.jsx` is ~1300 lines, one component, a `viewMode` state toggling reader vs chat). The backend is **API-only** — it serves no static files. In dev, Vite proxies; in production, nginx does (see `deploy/nginx`). Either way the browser only ever talks to one origin.

Everything else in this document is detail on those two codebases.

## The one rule: same-origin `/v1`

If you remember one design constraint, remember this one. Every `/v1` fetch the SPA makes goes out with `credentials: 'include'` (`src/utils/apiFetch.js`) so the OIDC session cookie rides along. The browser refuses `credentials: 'include'` responses when the server answers with the wildcard `Access-Control-Allow-Origin: *` — and the backend's default CORS *is* the wildcard (on purpose, for the browser extension, which calls `/v1/synthesize` from arbitrary page origins using a Bearer token instead of cookies).

The consequences chain out from there:

- A blank `apiHost` in the SPA (`buildApiUrl` in `src/utils/url.js`) produces a **relative URL** — same-origin. This is the only supported configuration for cookie auth.
- `vite.config.js` proxies `/v1` → `http://localhost:8000` and `/api` → `http://localhost:11434` so dev on :5173 is same-origin with everything.
- `localhost:5173 → localhost:8000` is **cross-origin** (different port = different origin), so any persisted `apiHost: 'localhost'` was silently broken by the auth work. `App.jsx` migrates the stale localStorage value to blank on mount (`migratePersisted('apiHost', 'localhost', '')`).
- Pinning `FRONTEND_ORIGIN` on the backend switches CORS to an exact-origin list and enables `allow_credentials` — for deployments where the SPA is hosted on a different origin than the API.

If the SPA ever shows the "backend isn't responding" gate while `curl localhost:8000/v1/health` works fine, check the browser console for a CORS complaint before blaming the backend — the request may be succeeding at the network layer and being discarded by the browser.

---

## Frontend

### App.jsx — the hub

`src/App.jsx` owns all global state and wires the engines. The order of things matters:

1. **Migration first.** `migratePersisted(...)` runs at the very top of the component body, before the hooks read localStorage, because the old default `apiHost='localhost'` predates cookie auth and is always CORS-dead now.
2. **Persisted settings** (`usePersistedState`, localStorage key prefix `neural-pdf-`): `darkMode`, `volume`, `scale`, `playbackSpeed`, `selectedVoice`, `isLocalhost` (see [traps](#invariants--traps) — despite the name it means "use Kokoro TTS, not Web Speech"), `apiHost`/`apiPort`, `requestTimeout` (seconds), `unlimitedBatchTimeout`, `viewMode` (`'reader'|'chat'`), Ollama `ollamaHost`/`ollamaPort`/`selectedModel`, `chatTtsMode`, `chatAutoTts`, per-model inference overrides (`inferenceByModel`), `distractionFree`, layout knobs.
3. **Engines**: `useAuth`, `useTheme`, `useMobileDetect`, `usePdfEngine`, `useTtsEngine`, `useChatEngine`, `useKeyboardShortcuts`.
4. **Per-document state maps keyed by sha256** (`docIndexByDocId`, `docConvertByDocId`, `docViewByDocId`) — switching documents never collides.
5. **The auth gate** renders *after all hooks* (rules-of-hooks safe) and short-circuits every render: `auth.state !== 'active'` → `<AuthGate>` alone. Note the subtlety: the gate blocks what's **displayed**, but App's hooks and effects still run — that's why you can see CORS errors in the console while staring at a login screen.

Big effects in App: the health probe (`GET /v1/health` once on mount → decides Kokoro vs Web-Speech fallback), the document hash + status fetch (sha256 of file bytes → `GET /v1/docs/{id}` to restore pipeline state), the **index pipeline** and **docling pipeline** handlers (both: register → upload → kick off → poll every 2s), and workspace restore from IndexedDB.

### Hooks

| Hook | Controls | Backend endpoints |
|---|---|---|
| `useAuth` | Auth state machine: `loading→active/anonymous/pending/disabled/error`. Any 401 anywhere (via apiFetch's global handler) resets to `anonymous`. | `/v1/auth/me`, `login` (full-page redirect), `logout` |
| `usePdfEngine` | Document load/render/extract. Pure client + IndexedDB; no backend. Sentence segmentation, mdast-based markdown pipeline, 5-book LRU library, 7-day reading progress. | — |
| `useTtsEngine` | Reader + chat + preview audio channels. Sentence queue with 2-ahead prefetch, in-flight dedup, cache eviction, 3-failure circuit breaker. Downloads: page / message / whole-book (3-worker parallel pool, client-side WAV stitch via `wavConcat`). | `POST /v1/synthesize`, `POST /v1/batch_synthesize` |
| `useChatEngine` | Ollama chat driver. NDJSON stream consumption, think-mode fallback chain, tool execution, chat TTS queue, session persistence, pins, event log. | Ollama `GET /api/tags`, `POST /api/chat`; sessions via sessionStore; tools via `/v1/...` |
| `usePersistedState` | `useState` + JSON in localStorage; `migratePersisted` helper; reading-progress save/load with expiry. | — |
| Others | `useKeyboardShortcuts` (Space/Esc/arrows/F for distraction-free), `useMobileDetect`, `useTheme` (Tailwind class maps). | — |

Two design details worth knowing before touching chat code:

- **Fallback order matters**: if the model 4xx's on a *think level* (`think:'low'`), the engine retries once with `think:true` **keeping tools**, and only drops tools if that also 4xx's. Getting this backwards would misread a think complaint as a tool complaint.
- **Chat TTS prefetch is bounded to +2**: Kokoro serializes synthesis server-side under a global lock; queuing everything at once would just pile up. `speakMessage` (manual per-bubble read) fires all sentences immediately — the server serializes anyway.

Pure companions: `chatHistory.js` (Ollama message assembly — attachments, including **audio**, go into `images:[base64]` because Ollama's message struct has no audio field), `inference.js` (per-model overrides; **unset means omitted from the request** — sending `null` would clobber Modelfile tuning), `pins.js` (max 6 pins, 12k char budget, dedup).

### Persistence layer (lib/)

- **`sessionStore.js`** — chat sessions live in **two places**: legacy IndexedDB (pre-multi-user) and Postgres. Reads merge both, newest-first, tagged `source:'pg'|'local'`. Writes always target Postgres; a legacy-sourced session **forks** to a new pg id on first save so the original stays untouched. Backend offline → toast once per streak, chat keeps working (it talks to Ollama directly).
- **`workspace.js` / `WorkspaceContext.jsx`** — folder-as-workspace support with two interchangeable backends (File System Access handle vs `webkitdirectory` snapshot) and a reducer-driven back/forward history for navigating markdown between files.
- **`uploadPdf.js`** — pulls PDF bytes back out of IndexedDB and `POST`s them for the docling pipeline.
- **`chatTools/`** — the frontend half of Ollama tool calling. A registry of `{name, definition, when(ctx), execute}`: `search_document` (only advertised when the loaded doc is `indexed` on the backend), `web_search`, `current_time_date`. When no tool's `when()` passes, `tools` is omitted from the request entirely.

### Utils

- **`apiFetch.js` / `url.js`** — see [the one rule](#the-one-rule-same-origin-v1). `buildApiUrl(host, port, path)`: blank host → relative path; bare host gets `http://`; port appended only if not already embedded.
- **`wavConcat.js`** — stitches WAVs by reusing the first header and concatenating data payloads; **assumes identical PCM format** (callers guarantee same voice+speed). Audiobook export depends on this.
- **`markdownToSpeech.js`** — strips fences/links/markers; headings get a trailing `:` so TTS pauses.
- **`docHash.js`** — WebCrypto sha256 of file bytes, memoized per name+size. This hash *is* the document id everywhere (backend, IndexedDB, state maps).
- **`attachment.js`** — 10 MB image / 25 MB audio caps; `stripAttachmentData` before persisting sessions (base64 payloads never reach Postgres).
- **`resolvePath.js`** — href classification for workspace links: external / anchor / unsafe (`javascript:` etc. rendered inert) / relative path with `..` normalization.

### Components by group

- **Auth**: `auth/AuthGate.jsx` — full-page screens for the six auth states (sign-in, pending, disabled, error, loading).
- **Account / Admin** (sections inside the reader Sidebar): `AccountPanel` (profile + personal-access-token management), `AdminPanel` (user list, activate/disable, role toggle; self-lockout prevented).
- **Chat**: `ChatSidebar` (settings, sessions, event log), `ChatView` (message list with thinking disclosure, markdown rendering, context meter, streaming stop, tool-call and stats disclosures, per-message read-aloud/download). `AttachmentPreview`; `VoiceRecorder` exists but is intentionally unwired pending Whisper transcription.
- **Reader**: `PdfViewer` (hub: toolbar + four-way content switch: docling MD → local MD → text pseudo-pages → pdf.js canvas), `MarkdownReader` (docling output, page markers, scroll-sync via IntersectionObserver), `MarkdownPageRenderer` (local MD with block-level TTS highlight), `TextPageRenderer`, `WelcomeScreen` (drop zone + recent library), `IndexButton` / `ConvertButton` / `DoclingConvertDialog` (index and convert pipeline UIs), `WorkspaceLink` (markdown links that navigate the workspace).
- **Chrome**: `Header` (playback cluster, KOKORO/SYSTEM toggle, audiobook export with progress), `HeaderOverflowMenu` (mobile), `Sidebar` (voice/speed/settings, sentence list, chapters), `MobileBottomNav`, `DistractionFreeBar`.
- **Overlays**: `DragOverlay`, `ToastNotification`, `ContextMenu` (right-click a sentence → "continue from here"), `KeyboardShortcutsModal`, `ReadSelectionButton` (+ "Ask AI" pins the selection for chat).

---

## Backend

### App wiring

`server/app.py: create_app()` wires in this order — order is load-bearing:

1. **Logging** (rotating file + console, `logging_config.py`).
2. **CORS** — `FRONTEND_ORIGIN` unset → `["*"]`, credentials **off**; pinned → exact origins, credentials **on** (`appconfig.py`).
3. **SessionMiddleware** (starlette signed cookie) — holds *transient OIDC flow state only* (state/nonce/PKCE). This is deliberately separate from the app session, which is a DB row. `SESSION_SECRET` missing → ephemeral per-process secret, which is why `WORKERS>1` without a shared secret silently breaks OIDC (each worker signs with a different key).
4. **Routers**: TTS, chat sessions, docs, tools, auth, admin.
5. **Startup**: `startup_guard()` (fail fast on auth-bypass or weak secret with a non-loopback bind), `init_db()` (retrying; **failure does not kill the app** — TTS keeps serving, DB-backed routes return 503), embeddings/web-search httpx clients, PDF storage dir.

The design theme is **"TTS never dies"**: Postgres down → 503s on chat/doc routes only; SearXNG down → search results degrade to snippets; Keycloak down → login 503s. The only boot-time refusals are the two security guards.

### Auth subsystem

`server/auth/` — all of it is worth reading, but the shape is:

- **`config.py`** — `AUTH_ENABLED` (default **true**), OIDC vars, `SESSION_TTL_HOURS=168`, `COOKIE_SECURE=true`. Loopback detection resolves the *bind address* through `ipaddress` (never a client Host header — spoofable). `startup_guard()` refuses to boot if auth is off or the secret is short on a non-loopback bind.
- **`oidc.py`** — Authlib client, discovery-based, **PKCE S256**.
- **`sessions.py`** — DB-backed app sessions. Cookie holds a random token; only its **sha256** is stored, so a DB leak yields no usable cookies.
- **`users.py`** — JIT provisioning on login. Three branches: known identity → refresh email; matching **verified** email → link; brand-new → **first-user-admin**: an atomic conditional `UPDATE ... WHERE oidc_sub IS NULL` claims the seed admin row (the row lock serializes concurrent first logins); everyone else lands as `pending` member. An unverified email can never claim the pre-provisioned admin row.
- **`tokens.py`** — personal access tokens (`nrp_...`, sha256-stored, shown once). These are how the browser extension and scripts authenticate.
- **`deps.py`** — `get_current_user`: Bearer PAT first, then the `nr_session` cookie, else 401. Non-active status → 403 with the status in the detail (the SPA distinguishes `pending` from `disabled` on this). **Dev bypass**: `AUTH_ENABLED=false` + loopback bind → every request is the seed admin.
- **`authz.py`** — ownership checks raise **404** (not 403) on other people's rows so callers can't enumerate.

**The login round-trip** (`server/routers/auth.py`): `GET /v1/auth/login?next=...` stashes the post-login path in the signed cookie (validated same-site path only — no open redirects) and 302s to the IdP → `GET /v1/auth/callback` exchanges the code (Authlib verifies state + PKCE), provisions the user, creates the session row, and 303s back to `next` with the `nr_session` httponly cookie. The SPA has **no callback route** — it just re-probes `/v1/auth/me` after the reload.

For the full identity map — how Keycloak's `keycloak` schema and the app's `users` table relate (and don't), why `sub` UUID persistence matters, and where roles live — see [IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md).

### Routers

- **`docs.py`** — the RAG pipeline. State machine: `registered → chunks_uploaded → indexing → indexed|failed`. The `doc_id` is the client's sha256 (regex-gated `^[0-9a-f]{64}$`) because it's interpolated into filesystem paths. Chunks upsert idempotently; indexing embeds in batches of 16 under a per-doc lock; search is pgvector cosine distance. Docling conversion (PDF → per-page markdown) lives here too.
- **`chat_sessions.py`** — mirrors the frontend's session shape (messages, events, pins) as plain relational + JSONB. **No pgvector here.** Whole-record PUT upsert in one transaction; ownership 404s; messages ordered by timestamp with a role tiebreaker.
- **`tools.py`** — `POST /v1/tools/web_search`, synchronous by design (Ollama tool calls can't poll).
- **`admin.py`** — list/patch users; disabling hard-revokes their sessions.
- **`endpoints.py`** — TTS + `/v1/health` (see [TTS pipeline](#tts-pipeline)).

### Services

- **`embeddings.py`** — Ollama `nomic-embed-text`, **dimension 768 hard-coded** in the SQL column too; swapping embedding models requires dropping the column and re-indexing. Semaphore(4); failed texts return `None` rather than aborting the batch.
- **`web_search.py`** — SearXNG query → parallel fetch of results (**SSRF guard**: DNS must resolve to public addresses only, and redirects are followed manually with the check re-applied at every hop, max 4) → trafilatura extraction → small-model summary. Degrades to the snippet on any failure.
- **`docling_convert.py`** — PDF→Markdown, disabled by default (`DOCLING_ENABLED=false`), lazy imports so the server boots without docling installed. Presets fast/standard/accurate; heavy sync work in `asyncio.to_thread`.

### TTS pipeline

`server/model.py` loads Kokoro (~325 MB) **at import time** — missing model files are a hard `exit(1)`. Execution provider priority: CUDA → OpenVINO GPU → NPU → CPU, with fallback at session creation.

The critical invariant: **Kokoro is not thread-safe**, so all inference runs in an executor under a **global `asyncio.Lock`** (`server/endpoints.py`). Concurrent requests queue rather than corrupt the voice files. This is why the frontend's bounded prefetch matters, why `batch_synthesize` holds the lock for one whole batch instead of per-sentence, and why the request-size caps (`TTS_MAX_*` in `schemas.py`) exist — one oversized request under a global lock stalls TTS for everyone.

### Database & migrations

Single `psycopg AsyncConnectionPool`, pgvector codec registered per connection. Migrations are plain `.sql` files in `server/sql/`, applied in numeric order, one transaction each, versioned in `schema_migrations` — no external tooling. The interesting ones: `001` (documents, chunks with `vector(768)` + HNSW cosine index, chat tables), `005` (users + ownership columns + the seed admin row), `006` (sessions + PATs, hashes only). Restart crash-recovery resets stale `indexing`/`converting` rows on boot.

---

## End-to-end flows

**Index a document (reader RAG).** Open a PDF → `usePdfEngine` saves bytes to IndexedDB → App computes the sha256 → `POST /v1/docs` (register) → local chunk extraction → `POST /v1/docs/{id}/chunks` in batches of 50 → `POST /v1/docs/{id}/index` (202, background job) → poll `GET /v1/docs/{id}` every 2s until `indexed`. Only then does `search_document` advertise itself to the chat model.

**Convert with docling.** Register → `POST /v1/docs/{id}/pdf` (raw bytes retained under `data/pdfs/`) → `POST /v1/docs/{id}/convert` with options → backend chains convert → seed chunks from `doc_pages` → embed → poll until `converted` + `indexed` → the reader switches to the markdown view (`MarkdownReader`), fetched via `GET /v1/docs/{id}/markdown` with `<!-- page N -->` markers.

**One chat turn.** `sendMessage` assembles history (pins as a preamble immediately before the user turn) → `POST /api/chat` with tools (if any are active) → stream NDJSON: content, thinking, tool_calls, metrics (nanosecond durations) → if tools were called: execute all in parallel, re-POST history + tool results **without** tools, stream the final answer → meanwhile `chatTtsMode='streaming'` flushes complete sentences to the TTS queue as they form → session auto-creates on first save, title from the prompt.

**A page of read-aloud.** `textItems` (sentences) → synthesis for current + next 2, in-flight deduped → `onended` recursion commits position and revokes the played blob URL → end of page bumps `currentPage` (which clears the cache); end of document stops → 3 consecutive failures abort with a toast.

## Local development

`./startup.sh` (see `CONTRIBUTING.md` for the full quick-start):

- `init` — venv, deps, Kokoro model download, frontend build.
- `up` — Postgres + SearXNG + backend with **`AUTH_ENABLED=false`** (loopback bypass: you are the seed admin, no cookie needed).
- `up-with-dev-auth` — adds Keycloak, creates `.env` on first run (rig values + generated `SESSION_SECRET`, `COOKIE_SECURE=false`), waits for the realm import, runs with auth on. Login at :5173 with `admin-user` / `password`.
- `down` — backend + containers down. `up` is foreground; Ctrl-C traps and tears everything down.

The SPA is `npm run dev` on :5173. The Vite proxy makes it same-origin with backend and Ollama; Keycloak at :18080 is deliberately a different origin (the backend, not the browser, talks to it). Backend env vars are read from the environment or `.env` (sourced by `startup.sh` — there is no `python-dotenv`). See `deploy/README.md` for production auth wiring.

## Invariants & traps

- **Blank `apiHost` = same-origin is the only cookie-auth-compatible setup.** Never default it to `localhost`; the migration in `App.jsx` exists precisely because that was once the default and every login-then-fetch silently died to CORS.
- **`isLocalhost` is misnamed** — it really means "use Kokoro TTS" (`true`) vs "browser Web Speech" (`false`). Blank host + `backendAvailable` is what matters at runtime.
- **Kokoro is serialized by a global lock** — any new server-side synthesis path must respect it, and any new client-side path should bound its prefetch (the +2 rule).
- **`WORKERS>1`** means N copies of the 325 MB model *and* — without a shared `SESSION_SECRET` — N different OIDC flow-cookie signing keys.
- **`embedding vector(768)` is hard-coded** — swapping embedding models is a migration, not a config change.
- **Unset inference settings must be omitted, not `null`** (`inference.js`) — `null` clobbers Modelfile tuning.
- **The auth gate blocks rendering, not effects** — App's fetches fire behind the login screen. Don't be surprised by network noise while unauthenticated.
- **Audio attachments ride in Ollama's `images` field** — the message struct has no audio slot; audio-capable models interpret those bytes as audio.
- **Vite watch ignores `.venv`/`data`/`logs`** (`vite.config.js`) — without this, chokidar exhausts `fs.inotify.max_user_watches` on this repo and the dev server dies with ENOSPC.
- **Ownership misses return 404, not 403** (`authz.py`) — leaking existence is an enumeration bug.
- **Stale docstrings**: `webSearch.js`/`currentTimeDate.js` claim to be unregistered stubs, but both are in the registry — trust the registry line. `VoiceRecorder` is intentionally dead until Whisper lands.
