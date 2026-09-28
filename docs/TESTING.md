# Testing — what covers what (post-v1.9.0: auth + model-router gateway)

This is the **test map for everything that shipped after tag `v1.9.0`**: the
multi-user auth+authz backend, the SPA auth UI, and the inference gateway with
budgets. For each feature area: which file owns it, what each case actually
asserts, what is deliberately *not* automated, and the manual checks that have
been run live. Use it to answer "if I change X, what must stay green?" and to
spot a feature that shipped without coverage.

- [How to run](#how-to-run)
- [Feature → test ownership](#feature--test-ownership)
  - [Auth config & startup guards](#auth-config--startup-guards)
  - [Migrations 005 / 006 / 007](#migrations-005--006--007)
  - [Users & JIT provisioning](#users--jit-provisioning)
  - [Sessions & personal access tokens](#sessions--personal-access-tokens)
  - [Auth router (/v1/auth/*)](#auth-router-v1auth)
  - [Principal resolution (deps) & admin gate](#principal-resolution-deps--admin-gate)
  - [Ownership guards (authz)](#ownership-guards-authz)
  - [Admin router](#admin-router)
  - [Model listing (GET /v1/inference/models)](#model-listing-get-v1inferencemodels)
  - [Chat turns and the orchestrator (C1)](#chat-turns-and-the-orchestrator-c1)
  - [Model-router config](#model-router-config)
  - [Budgets: service + enforcement](#budgets-service--enforcement)
  - [Server-side routing through model_router](#server-side-routing-through-model_router)
  - [Security hardening (SEC fixes)](#security-hardening-sec-fixes)
  - [Frontend: auth UI](#frontend-auth-ui)
  - [Frontend: chat streaming](#frontend-chat-streaming)
  - [Frontend: budget UX](#frontend-budget-ux)
- [Integration seams covered by tests](#integration-seams-covered-by-tests)
- [Manual / live verification ledger](#manual--live-verification-ledger)
- [Known gaps](#known-gaps)

---

## How to run

```bash
# Backend — REQUIRES Postgres up (conftest migrates natural_reader_test on a
# real connection; without the DB every test errors, not skips).
podman-compose up -d postgres          # or: env -u XDG_DATA_HOME .venv/bin/podman-compose ...
.venv/bin/python -m pytest server/tests/ -q          # 143 passed @ 2026-09-17

# Frontend (vitest, jsdom — no services needed)
npx vitest run                                       # 163 passed @ 2026-09-17

# Lint
npm run lint
```

Backend test conventions that matter when writing new cases:

- `httpx.AsyncClient` + `ASGITransport` — **never** `TestClient` (streaming
  responses hang under it).
- The `app` fixture overrides `deps.get_conn` with `db_conn`, so every route
  runs inside the caller's rolled-back transaction — no cross-test leakage.
- Upstream Ollama is mocked with `httpx.MockTransport` via
  `start_client(transport=...)`. For **streaming** mocks, use the
  `stream_response()` helper — an eager `httpx.Response(200, content=bytes)`
  arrives pre-consumed (`StreamConsumed`).
- Budget tests need a real `users` row (FK target) — provision with
  `resolve_or_provision_user`.
- Reset a poisoned test DB:
  `psql "postgresql://natural_reader:natural_reader@localhost:5433/postgres" -c 'DROP DATABASE IF EXISTS natural_reader_test;'`

## Feature → test ownership

### Auth config & startup guards

| File | Case | Asserts |
|---|---|---|
| `test_auth_config.py` | defaults_enabled | `AUTH_ENABLED` defaults to **true** (secure default) |
| | disabled_flag_parsed | explicit `AUTH_ENABLED=false` parses |
| | dev_bypass_only_on_localhost | bypass refuses non-loopback binds (loopback resolved via `ipaddress`, not Host header) |
| | dev_bypass_false_when_auth_enabled | bypass requires the flag *and* loopback |
| | oidc_values_parsed | issuer/client/redirect/secret parsing |
| `test_app_wiring.py` | dev_bypass_on_public_bind_raises | startup guard: auth off + public bind → boot refusal |
| | dev_bypass_on_localhost_ok | …allowed on loopback |
| | auth_enabled_public_bind_with_strong_secret_ok | happy path |
| | missing_session_secret_on_public_bind_raises | guard: no secret on public bind |
| | short_session_secret_on_public_bind_raises | guard: weak secret rejected |
| | missing_session_secret_on_loopback_ok | loopback exempt (dev) |

### Migrations 005 / 006 / 007

| File | Case | Asserts |
|---|---|---|
| `test_migration_005.py` | seed_admin_exists | fixed UUID row, `admin`/`active` |
| | ownership_columns_not_null | `documents`/`chat_sessions.user_id` NOT NULL after backfill |
| | new_doc_carries_owner | inserted docs stamp owner |
| `test_migration_006.py` | sessions_table_shape | columns/timestamps present |
| | pat_table_shape | + `token_hash` unique |
| `test_migration_007.py` | inference_usage_shape | PK (user_id, day), counters |
| | users_budget_column_exists | `inference_daily_token_budget` nullable |
| | usage_round_trip | upsert + read-back |

### Users & JIT provisioning

`test_auth_users.py` — this file *is* the spec of the three-branch resolver
(see IDENTITY_AND_ROLES.md § branches):

| Case | Asserts |
|---|---|
| first_login_links_seed_admin | branch 3: unlinked seed row claimed → admin |
| verified_email_links_preseeded_row | branch 2: verified email links pre-provisioned row |
| unverified_email_cannot_claim_preseeded_row | branch 2 guard: unverified email → ValueError (409 upstream) |
| second_identity_is_pending_member | branch 3: subsequent identities land `pending` member |
| returning_user_matched_by_sub | branch 1: known `(iss, sub)` → same row (refresh, no duplicate) |
| disabling_user_revokes_sessions | hard-revoke: sessions deleted on disable |
| set_status | status transition |

### Sessions & personal access tokens

| File | Case | Asserts |
|---|---|---|
| `test_auth_sessions.py` | create_then_resolve | cookie token resolves to user |
| | raw_token_not_stored | only sha256 in DB |
| | expired_session_resolves_none | expiry honored |
| | revoke | revoked cookie is dead |
| | unknown_token_resolves_none | junk → no principal |
| `test_auth_tokens.py` | create_returns_raw_once_and_resolves | PAT round trip |
| | raw_not_stored | sha256 only |
| | list_hides_value_and_revoke | listing never returns secrets; revoke works |
| | cannot_revoke_others_token | ownership on revoke |
| | non_pat_string_resolves_none | junk Bearer → no principal |
| `test_auth_token_endpoints.py` | create_list_revoke | HTTP surface: POST/GET/DELETE `/v1/auth/tokens` |

### Auth router (/v1/auth/*)

| File | Case | Asserts |
|---|---|---|
| `test_auth_router.py` | me_returns_principal | `/v1/auth/me` → `{id,email,role}` |
| | me_401_without_principal | deny-by-default |

**Not automated** (needs a real IdP): the `/login → IdP → /callback` redirect
flow itself, state/PKCE verification, cookie settings. Covered by the live
scripted dance (see ledger below) and the rig in `deploy/README.md`.

### Principal resolution (deps) & admin gate

| File | Case | Asserts |
|---|---|---|
| `test_auth_deps.py` | no_credential_is_401 | deny-by-default |
| | pat_authenticates_admin | Bearer path |
| | *(cookie path, status-403 path)* | exercised via `test_auth_router` + `test_protected_routes` |
| | require_admin_blocks_member (et al.) | admin gate: member → 403 on admin-only route |

### Ownership guards (authz)

| File | Case | Asserts |
|---|---|---|
| `test_auth_authz.py` | owner_ok / missing_doc_is_404 / non_owner_is_404_not_403 | **404 doctrine**: missing and not-owned are indistinguishable (anti-enumeration) |
| | session_ownership | same for chat sessions |
| `test_docs_authz.py` | get_others_doc_is_404 / owner_can_get / register_stamps_owner / unauthenticated_is_401 | docs surface end-to-end ownership |
| `test_chat_sessions_authz.py` | list_returns_only_own / get_others_404 / upsert_stamps_owner / unauthenticated_401 | chat sessions surface |
| `test_protected_routes.py` | *(matrix)* | TTS/tools routes require a principal |

### Admin router

| File | Case | Asserts |
|---|---|---|
| `test_admin_router.py` | admin_lists_and_activates | list + activate flow |
| | member_forbidden | 403 for members |
| | inference_usage_admin_only | usage endpoint gated |
| | admin_inference_usage_returns_rows | per-day rows, per-user |
| | admin_inference_usage_clamps_days | `?days=` clamped 1..90 |
| | patch_sets_and_clears_budget | set / explicit-null-clears distinction |
| | patch_budget_round_trip | set → read-back |
| | patch_rejects_negative_budget | validation → 422 |

### Model listing (`GET /v1/inference/models`)

**C1:** the old `POST /v1/inference/chat` passthrough is gone — chat is
`POST /v1/chat/sessions/{id}/turns` (see below). `test_inference_router.py`
now covers model listing only:

| Case | Asserts |
|---|---|
| models_requires_auth | 401 without a principal |
| models_lists_ids_with_provider_and_capabilities | `{id, provider, kind, name, capabilities}` per model, `id` = `<provider>:<model>` |
| models_filtered_by_allowlist | `INFERENCE_MODELS` still filters the no-config default `ollama` provider |
| models_every_provider_down_is_502 | every configured provider unreachable → 502, not a hang |
| models_includes_budget / models_budget_absent_when_unlimited | budget state rides this endpoint |
| raw_chat_passthrough_is_gone | `POST /v1/inference/chat` no longer exists (404/405) |

### Chat turns and the orchestrator (C1)

The chat loop moved server-side (`server/chat/`, `server/llm/`). Coverage
spans several files:

| File | Covers |
|---|---|
| `test_chat_turns.py` | the `POST /v1/chat/sessions/{id}/turns` endpoint: request validation (413 over `CHAT_MAX_REQUEST_MB`, 422 malformed/empty/model-not-allowed/attachment-unsupported, 429 budget, 409 `turn_in_progress`, 503 `no_providers`/`db_unavailable`, 404 not-yours), SSE framing (`data: {...}` lines, always ending `data: [DONE]`, a terminal `error` frame on an unhandled exception) |
| `test_chat_orchestrator.py` | `run_turn` against a fake provider: plain text, one tool round then a tools-off final step, a failing/unknown tool, budget exhausted between steps, client disconnect mid-step (saves `aborted`, releases the claim), a model without tools, invalid tool-call JSON becomes `{}` |
| `test_chat_context.py` | Stage 0: document prefetch hit/miss/unreadable doc, the `Current time: …` line (`test_time_line_falls_back_to_utc_on_bad_timezone` for a garbage timezone), history trimming (old attachments replaced by a marker before a whole turn is dropped) |
| `test_llm_router.py` | provider-config parsing (`INFERENCE_PROVIDERS` + `INFERENCE_<NAME>_*`), `<provider>:model` id resolution, the no-config single-`ollama` fallback, a bare pre-C1 model name routing to the first `ollama`-kind provider |
| `test_chat_sessions_api.py` | session CRUD, `POST /v1/chat/sessions/import` (legacy-chat copy, create-only) |

### Model-router config

`test_model_router.py` (pure env-parsing unit tests):

| Case | Asserts |
|---|---|
| defaults | unset → all-models allowlist (dev), `llama3.2:3b` summarize, `nomic-embed-text` embed, 300s timeout |
| allowlist_parses_comma_separated_with_whitespace | CSV parsing |
| empty_allowlist_means_all | unset/blank = allow everything |
| summarize_model_precedence | `SUMMARIZE_MODEL` > `WEB_SEARCH_SUMMARY_MODEL` > default |
| budget_zero_and_unset_mean_unlimited | falsy budget → None |
| ollama_url_trailing_slash_stripped | URL normalization |

### Budgets: service + enforcement

**C1 changed what "aborted streams never count" means.** Pre-C1, a
client-aborted stream never accounted (no final chunk, no usage). Now a
turn that's stopped, disconnects, or errors mid-way **still charges** —
`_finalize` records whatever was generated using the provider's own count
if it reported one, else a chars/4 estimate (`estimated: true`). There is
no `_UsageTap` any more; usage accounting lives in the orchestrator.

| File | Case | Asserts |
|---|---|---|
| `test_inference_budget.py` | *(suite)* | record/spent/state/over/effective/admin_usage: UTC-day computation (Python, not SQL), per-user override vs deployment default, aggregation |
| `test_chat_turns.py` | budget_already_spent_is_429_with_reset_time | 429 pre-check with `{remaining_tokens, reset_at}` detail, before a turn is even claimed |
| `test_inference_router.py` | models_includes_budget / models_budget_absent_when_unlimited | budget state rides `GET /v1/inference/models` |
| `test_chat_orchestrator.py` | budget_runs_out_between_steps | checked before **every** step, not just once up front |
| | missing_usage_is_estimated_and_recorded | a provider with no usage in its response is charged the chars/4 estimate |
| | disconnect_mid_step_still_records_usage | **C1 behavior change**: a client disconnect still charges for the partial reply |
| | provider_error_mid_text_still_records_usage | a mid-turn provider error still charges for what was generated before it failed |

### Server-side routing through model_router

| File | Case | Asserts |
|---|---|---|
| `test_embeddings_routing.py` | *(suite)* | embeddings read URL+model from `model_router` (no direct env reads) |
| `test_web_search.py` | summarize_one_calls_ollama_generate (+SSRF suite) | summarizer uses `model_router`'s summarize model; SSRF guard suite (private-IP block, redirect re-check, scheme allowlist) |

### Security hardening (SEC fixes)

`test_security_hardening.py` — path traversal on `doc_id` (hex-regex gate),
PDF storage path escape, CORS origin parsing (wildcard vs explicit list,
credentials off/on), TTS/batch request size caps.

### Frontend: auth UI

| File | Case count | Asserts |
|---|---|---|
| `useAuth.test.jsx` | 5 | state machine: probe → active/anonymous; login redirect; logout; 401 global handler |
| `AuthGate.test.jsx` | 5 | one screen per auth state (sign-in/pending/disabled/error/loading) |
| `apiFetch.test.js` | 4 | `credentials:'include'` always; relative-URL building; 401 → global handler |
| `AccountPanel.test.jsx` | 3 | PAT create/copy-once/revoke UI flow |
| `AdminPanel.test.jsx` | 3 | list render, activate/disable via PATCH, role toggle |

### Frontend: chat streaming

**C1:** there's no "source switch" any more — `InferenceSourceSelect.jsx`,
its test file, `ChatSidebar.inference.test.jsx`, and the old NDJSON
`chatTransport.test.js` (server-vs-local `chatFetch`) are all gone. Chat's
frontend half is now a thin SSE reader plus a pure event reducer:

| File | Covers |
|---|---|
| `chatStream.test.js` | `postTurn` + reading the turn's SSE response: partial lines, `[DONE]`, abort |
| `chatEvents.test.js` | the pure `applyEvent` reducer: text/reasoning deltas, tool panel entries, `data-context`/`data-notice`, `finish`/`error`/`aborted` outcomes — fed from the same fixture files the backend's orchestrator tests write (`chatFixtures.testutil.js` / `server/tests/fixtures/chat_events/*.jsonl`), so a format change on either side fails a test |
| `chatTransport.test.js` | now just `GET /v1/inference/models` (`MODELS_PATH`) and `budgetDetail`/`formatResetAt` parsing |
| `ChatView.status.test.jsx` | "Still generating…" for a reload mid-reply, "Stopped" for an aborted one |
| `InferenceRow.test.jsx` | per-model settings row, replacing the removed source-toggle UI |

### Frontend: budget UX

| File | Case | Asserts |
|---|---|---|
| `ChatView.meter.test.jsx` | 9 | context meter accounting; send disabled when `inferenceBudget.remaining_tokens === 0` |
| `chatTransport.test.js` (budget half) | — | `budgetDetail` + `formatResetAt` |
| `ChatSidebar.jsx` tests (meter) | — | "N tokens left today" rendering — C1 removed the dedicated `ChatSidebar.inference.test.jsx`/source-toggle file along with the toggle itself |

## Integration seams covered by tests

- **Auth × every router**: ownership/status tests exist per surface (docs,
  chat, admin, inference, tools/TTS via `test_protected_routes`).
- **Budgets × turns**: pre-check (`test_chat_turns.py`) + per-step accounting,
  including stopped/failed replies (`test_chat_orchestrator.py`) + `/models`
  surface, against a real Postgres user row.
- **Frontend × backend event contract (C1)**: both sides read the same
  `server/tests/fixtures/chat_events/*.jsonl` files — the backend writes them
  from real orchestrator runs, the frontend's `chatEvents.test.js` replays
  them through `applyEvent`. A wire-format change that only one side notices
  fails a test.

## Manual / live verification ledger

Actually executed (not just designed), with results:

1. **2026-09-17 — Gateway scripted pass** (HANDOVER "later" entry): allowlist
   filter, 422, byte-faithful streaming, `INFERENCE_DAILY_TOKEN_BUDGET=1` →
   `remaining_tokens: 0` + structured 429, tool_calls passthrough
   (gemma4 → `current_time_date`), `GET /v1/admin/inference/usage` rows.
2. **2026-09-17 — Keycloak↔app propagation matrix** (HANDOVER "later still"):
   create-in-KC invisible until first login; delete-in-KC leaves the app row
   active **with a still-working session**; same-email re-login → 409; full
   OIDC dance scripted (PKCE + form POST + callback) against the live rig.
3. **Still open (needs a human in a browser):** budget meter rendering, 429
   toast + disabled send at zero, image-attach through a real turn, "Still
   generating…"/"Stopped" against an actual reload, streaming through a
   buffering proxy (see DEPLOYMENT.md). The C1 plan's own running-app walk
   (spec §11) is the authoritative checklist for this.

## Known gaps

- **OIDC flow tests are mocked at the claims level** — no automated test
  drives `authorize_access_token`. Accepted: needs a real IdP; covered by the
  scripted dance + rig walkthrough.
- **`/v1/auth/callback` error branches** (409 paths) are covered via the
  resolver's unit tests, but the router's 403/409 translation isn't
  table-driven anywhere — fine while the resolver owns the semantics.
- **No concurrency tests** for first-user-admin beyond the row-lock design
  comment (hard to test meaningfully without a second connection; the
  conditional UPDATE is the guarantee).
- **Admin UI for usage/budgets doesn't exist yet** — API covered, UI is the
  admin-console spec's job.
- **User deletion doesn't exist** (API or UI) — also admin-console scope;
  today's remedy for orphans is documented in IDENTITY_AND_ROLES.md.
- Frontend has **no E2E** (Playwright etc.) — all UI tests are jsdom-level;
  the manual ledger above is the substitute for now.
