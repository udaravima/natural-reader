# Model-Router Gateway — server-side, authenticated inference (sub-project E)

**Date:** 2026-09-17
**Status:** Draft — decisions below reflect the 2026-09-17 planning session; not yet approved or built.
**Area:** New router `server/routers/inference.py` · chat loop (`src/hooks/useChatEngine.js`) · inference settings (`src/hooks/inference.js`) · schema (`server/sql/007_*.sql`)

> Origin: sub-project **E** of the multiuser-hardening effort ([multiuser auth design §9](2026-09-13-multiuser-auth-authz-design.md)).
> Scope decision from the 2026-09-17 session: **full router, not a thin proxy** — allowlists, per-user
> token budgets, and task-type routing are in v1. Sequencing: **E lands before the document-library
> RAG feature** so its multi-round chat loop grows on the gateway instead of on direct browser→Ollama calls.

## 1. Problem

The browser talks to Ollama directly: `GET /api/tags` for the model list and
`POST /api/chat` for every generation ([useChatEngine.js:361](../../../src/hooks/useChatEngine.js), `:626`, `:648`, `:662`, `:752`).
That path is **unauthenticated and unauthenticated by design** — Ollama has no notion of users.
In any multi-user deployment this means anyone who can reach the box can run arbitrary inference,
list models, or pull new ones (README "Security & hardening" calls this out; the reference nginx
config's `/api/` block proxies it straight through). The fix is to move those calls server-side
behind the app's existing auth and, while we're there, make the server the single place that knows
which model serves which task.

## 2. Goals / non-goals

**Goals**
- All browser inference goes through authenticated backend endpoints (session cookie or PAT —
  the Chrome extension's Bearer flow works unchanged).
- Admin-configurable **model allowlist** — what the model dropdown offers.
- **Per-user token budgets** with real accounting (from Ollama's final-chunk stats), enforced
  with 429 + remaining-budget detail.
- **Task-type routing table** in one place (chat / summarize / embed), replacing the scattered
  env reads in `embeddings.py` and `web_search.py`.
- Streaming and tool-call passthrough are **byte-faithful**: the SPA's tool loop is not rewritten.
- Local/offline mode survives: a user may still point the SPA at their own Ollama.

**Non-goals (v1)**
- Multi-provider routing (OpenAI/Anthropic backends) — the seam is designed for it, not built.
- Server-side conversation orchestration — the tool loop stays in the SPA.
- Per-model API keys, billing, orgs.

## 3. Current call surface (verified 2026-09-17)

| Caller | Endpoint | Where | Notes |
|---|---|---|---|
| SPA | `GET /api/tags` | [useChatEngine.js:361](../../../src/hooks/useChatEngine.js) | Model dropdown + `reachable` flag, 5s timeout |
| SPA | `POST /api/chat` | useChatEngine.js `:626` (first call, with `tools`), `:648` (no-tools fallback), `:662` (retry), `:752` (post-tools follow-up) | Streaming NDJSON; final chunk carries `total_duration`/`prompt_eval_count`/`eval_count` |
| Server | `POST /api/embeddings` | [embeddings.py:65](../../../server/services/embeddings.py) | `OLLAMA_URL` env |
| Server | `POST /api/generate` | [web_search.py:176](../../../server/services/web_search.py) | Page summaries, small model |

Request bodies are assembled by `buildRequestFields` ([inference.js:32](../../../src/hooks/inference.js)):
`model`, `messages`, `tools`, `stream: true`, `think`, `keep_alive`, `options.num_ctx`,
`options.num_predict`. The gateway's allowlist must cover exactly these fields.

## 4. Design

### 4.1 Endpoints — `server/routers/inference.py`

- **`GET /v1/inference/models`** — proxies `/api/tags`, filtered by the allowlist. Returns
  `{ models: [{ name, ... }], budget: { remaining_tokens, reset_at } }` so the UI can render the
  meter and disable send when exhausted.
- **`POST /v1/inference/chat`** — validated passthrough to `/api/chat` via an `httpx.AsyncClient`
  stream → `StreamingResponse` (`application/x-ndjson`). On the final chunk, record usage and
  check budget *before* the request is accepted (pre-check), then account actuals after.

Both use `Depends(get_current_user)`; `AUTH_ENABLED=false` dev bypass (loopback-only) behaves as
everywhere else — the gateway is usable in local dev with zero ceremony.

### 4.2 The router part

- **Allowlist** — `INFERENCE_MODELS` env (comma-separated). Unset ⇒ all models (dev convenience);
  set ⇒ both `/v1/inference/models` and `/v1/inference/chat` reject non-listed models with 422.
  This is the admin's "what may this deployment run" knob.
- **Task routing** — one config module (`server/services/model_router.py`) owning the mapping:
  `chat` (user-chosen, must be in allowlist), `summarize` (`SUMMARIZE_MODEL`), `embed`
  (`EMBEDDING_MODEL`). `embeddings.py` and `web_search.py` stop reading env directly; the
  document-library RAG feature's per-doc description generation (its Phase 2) routes through
  here too — one server-side inference path, one audit point.

### 4.3 Budgets & telemetry

- `007_inference_budgets.sql`:
  - `inference_usage (user_id, day DATE, prompt_tokens, eval_tokens, requests)` — PK
    `(user_id, day)`, upsert-incremented from the final chunk's real counts.
  - `ALTER TABLE users ADD COLUMN inference_daily_token_budget BIGINT` — `NULL` = deployment
    default (`INFERENCE_DAILY_TOKEN_BUDGET`, `0`/unset = unlimited).
- Pre-request: reject with **429** `{ detail: { remaining_tokens, reset_at } }` if the user is
  over budget. Frontend surfaces it as a toast, not a silent failure.
- Admin sees per-user usage via `GET /v1/admin/inference/usage` (follows the existing
  [admin.py](../../../server/routers/admin.py) pattern).

### 4.4 Request validation — never a blind proxy

The gateway accepts **only** the known-safe fields (§3 list). Anything else in the body is
dropped or rejected. This is what makes it a *chat* gateway rather than a general Ollama proxy:
`/api/pull`, `/api/delete`, `/api/run` must be unreachable through app auth, forever. A future
multi-provider router changes the target of the passthrough, not this contract.

### 4.5 Frontend changes

- New persisted setting: **Inference source: Server (default) | Local Ollama**. Server mode hides
  the host/port inputs in ChatSidebar and swaps every `ollamaUrl('/api/...')` call to
  `apiFetch`-style `/v1/inference/...` with credentials. Local mode is byte-identical to today.
- `useChatEngine`'s four `/api/chat` sites collapse into one `callChat()` helper with a
  `tools` argument — the source switch lives inside it, and the tool loop logic is untouched.
- Model dropdown + `reachable` state consume `/v1/inference/models`.
- The context meter (`estimateTokens`, chars/4) stays client-side and approximate; budgets use
  server-side real counts. The two will disagree slightly — that's fine, they answer different
  questions ("am I close to the window" vs "what did I spend").

### 4.6 Config (`.env.example` additions)

| Var | Meaning | Default |
|---|---|---|
| `INFERENCE_MODELS` | Allowlist (comma-separated) | unset = all |
| `INFERENCE_DAILY_TOKEN_BUDGET` | Default per-user daily tokens | unset = unlimited |
| `INFERENCE_TIMEOUT_S` | Upstream read timeout for streaming | `300` |

(`OLLAMA_URL` already exists.)

### 4.7 Deployment

With the gateway live, the nginx `/api/` block is **deleted** — the open-Ollama trap from the
prod-deploy discussion closes here. SearXNG stays loopback as-is.

## 5. File / unit inventory

| Change | File |
|---|---|
| New gateway router | `server/routers/inference.py` |
| Task-routing config module | `server/services/model_router.py` |
| Budget schema | `server/sql/007_inference_budgets.sql` |
| Mount router, allowlist validation | `server/app.py` |
| Route summarize/embed through router | `server/services/web_search.py`, `server/services/embeddings.py` |
| Admin usage endpoint | `server/routers/admin.py` |
| Source switch + `callChat()` helper | `src/hooks/useChatEngine.js` |
| Inference-source setting | `src/hooks/inference.js`, `src/components/ChatSidebar.jsx` |
| Env documentation | `.env.example`, README hardening section |

## 6. Testing strategy

- **Backend (pytest):** validation drops unknown fields; allowlist 422s; budget pre-check 429s and
  post-accounting increments; streaming passthrough preserves NDJSON lines including `tool_calls`
  and the stats final chunk (mock upstream with `httpx.MockTransport`); PAT and session auth
  paths; dev bypass.
- **Frontend (vitest):** source switch toggles URL + credentials; over-budget renders a toast;
  tool-loop behavior unchanged in server mode (existing `inference.requestBody.test.js` patterns).

## 7. Phasing

- **E1 — Pipe.** Endpoints + auth + streaming + frontend source switch (thin proxy). Deployable alone.
- **E2 — Router.** Allowlist + `model_router.py` task table; services rerouted.
- **E3 — Budgets.** Migration, 429 enforcement, admin usage view.
- **E4 — (future) Multi-provider.** Out of scope; the validated-passthrough seam is where it plugs in.

## 8. Risks / open questions

- **Proxy buffering** kills token streaming — nginx `proxy_buffering off` is already in the
  reference config; verify no ASGI middleware buffers.
- **Retry semantics vs budget:** `:648`/`:662` retries re-spend tokens; count only completed
  generations (final chunk seen), pre-check is advisory. Accepted cost.
- **Extension UX:** the Chrome extension must send PATs — it already speaks Bearer (design §),
  but needs its inference base URL pointed at `/v1/inference`. Verify during E1.
- **Open:** should local mode be disabled when `AUTH_ENABLED=true` + non-loopback deploy? (Current
  answer: no — local mode is the user's own machine, their business.)
