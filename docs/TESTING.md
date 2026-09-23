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
  - [Inference gateway (models + chat)](#inference-gateway-models--chat)
  - [Model-router config](#model-router-config)
  - [Budgets: service + enforcement](#budgets-service--enforcement)
  - [Server-side routing through model_router](#server-side-routing-through-model_router)
  - [Security hardening (SEC fixes)](#security-hardening-sec-fixes)
  - [Frontend: auth UI](#frontend-auth-ui)
  - [Frontend: chat transport & source switch](#frontend-chat-transport--source-switch)
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

### Inference gateway (models + chat)

`test_inference_router.py`:

| Case | Asserts |
|---|---|
| models_requires_auth / chat_requires_auth | 401 without principal — the unauthenticated browser→Ollama path is closed |
| models_lists_from_upstream | proxy of Ollama `/api/tags` |
| models_filtered_by_allowlist | `INFERENCE_MODELS` filters the list |
| models_upstream_down_is_502 | Ollama down → 502, not hang |
| chat_streams_ndjson_byte_faithful | chunk-for-chunk passthrough (uses `stream_response()` helper) |
| chat_forwards_body_upstream | validated body forwarded intact |
| chat_rejects_unknown_top_level_field / _message_field / _option_field | `extra="forbid"` envelope — unknown fields 422 |
| chat_accepts_images_and_tool_calls_round | per-message `images` + `tool_calls` pass through |
| chat_allowlist_rejects_other_models / accepts_listed_model | allowlist 422 / pass |
| chat_forwards_upstream_error_status_and_body | SPA's think/tools fallback chain still branches on upstream status |
| chat_upstream_down_is_502 | upstream outage → 502 |

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

| File | Case | Asserts |
|---|---|---|
| `test_inference_budget.py` | *(suite)* | record/spent/state/over/effective/admin_usage: UTC-day computation (Python, not SQL), per-user override vs deployment default, aggregation |
| `test_inference_router.py` (budget half) | chat_429_when_over_budget | 429 pre-check with `{remaining_tokens, reset_at}` detail |
| | chat_allowed_when_under_budget | under-budget passes |
| | models_includes_budget | budget rides `/v1/inference/models` |
| | models_budget_absent_when_unlimited | unlimited → no budget key |
| | stream_accounts_usage_from_final_chunk | accounting uses Ollama's real final-chunk counts |
| | aborted_stream_does_not_account | client-aborted streams never count |
| | budget_pre_check_failure_fails_open | DB down → chat still serves (fail-open doctrine) |
| | usage_tap_handles_split_lines_and_stops_at_done | `_UsageTap` parser: split NDJSON lines, `done:true` terminator |
| | usage_tap_ignores_unparsable_lines | junk lines can't corrupt counts |

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

### Frontend: chat transport & source switch

| File | Case count | Asserts |
|---|---|---|
| `chatTransport.test.js` | 10 | `chatFetch` targets `/v1/inference/chat` in server mode vs `/api/chat` local; `MODELS_PATH`/`CHAT_PATH`; `budgetDetail`/`formatResetAt` parsing (incl. missing keys, `Z` timestamps) |
| `InferenceSourceSelect.test.jsx` | 3 | Server/Local toggle renders; persisted change; host/port only in local mode |
| `ChatSidebar.inference.test.jsx` | 10 | INFERENCE SOURCE block wiring, model dropdown from gateway list, budget meter visibility |
| `useChatEngine` (via `ChatView.meter` + transport tests) | 9+4 | context meter; `callChat` 429 interception happens **before** the think/tools retry chain; request-body shape per source |

### Frontend: budget UX

| File | Case | Asserts |
|---|---|---|
| `ChatView.meter.test.jsx` | 9 | context meter accounting; send disabled when `inferenceBudget.remaining_tokens === 0` |
| `chatTransport.test.js` (budget half) | — | `budgetDetail` + `formatResetAt` |
| `ChatSidebar.inference.test.jsx` (meter) | — | "N tokens left today" rendering |

## Integration seams covered by tests

- **Auth × every router**: ownership/status tests exist per surface (docs,
  chat, admin, inference, tools/TTS via `test_protected_routes`).
- **Gateway × budgets**: pre-check + post-stream accounting + `/models`
  surface, in one file, against a real Postgres user row.
- **Frontend × backend contract shapes**: `chatTransport.test.js` pins the
  request/response shapes the gateway's Pydantic envelope must keep matching
  (unknown-field 422s are pinned from the backend side in
  `test_inference_router.py`).

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
3. **Still open (needs a human in a browser):** Server/Local dropdown toggle,
   budget meter rendering, 429 toast + disabled send at zero, local-mode
   parity, image-attach through the gateway.

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
