# Testing — what covers what

This is the **test map**. The detailed areas below (auth, budgets, chat turns,
document versions, the admin console) say which file owns each feature, what each
case asserts, what is deliberately *not* automated, and the manual checks that
have been run live. The other test files are listed by area under
[Everything else](#everything-else-by-area), without their cases. Use it to answer "if I change X, what must stay green?" and to
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
  - [Admin console v2, phase 1](#admin-console-v2-phase-1)
  - [Admin console v2, phase 2a](#admin-console-v2-phase-2a)
  - [Admin console v2, phase 3 (usage by model)](#admin-console-v2-phase-3-usage-by-model)
  - [Message box inputs (text files and voice)](#message-box-inputs-text-files-and-voice)
  - [Frontend: auth UI](#frontend-auth-ui)
  - [Frontend: chat streaming](#frontend-chat-streaming)
  - [Frontend: budget UX](#frontend-budget-ux)
  - [Everything else, by area](#everything-else-by-area)
- [Integration seams covered by tests](#integration-seams-covered-by-tests)
- [Manual / live verification ledger](#manual--live-verification-ledger)
- [Known gaps](#known-gaps)

---

## How to run

> **Warning: a bare `pytest` connects to port 5433.** With `TEST_DATABASE_URL`
> and `TEST_ADMIN_DATABASE_URL` unset, the suite uses the defaults
> `postgresql://natural_reader:natural_reader@127.0.0.1:5433/...`, which is the
> port the app's own compose Postgres uses. On a machine that also runs the app,
> that is the live server: the suite creates `natural_reader_test` on it, applies
> migrations there, and its migration tests create and drop scratch databases
> beside your real one. Point both
> variables at a scratch Postgres first, in the same shell as the run:
>
> ```bash
> # a scratch container on another port (any Postgres with pgvector works)
> podman run -d --name nr-test-pg -p 127.0.0.1:5434:5432 \
>   -e POSTGRES_USER=natural_reader -e POSTGRES_PASSWORD=<scratch-password> \
>   -e POSTGRES_DB=postgres pgvector/pgvector:pg16
>
> export TEST_ADMIN_DATABASE_URL=postgresql://natural_reader:<scratch-password>@127.0.0.1:5434/postgres
> export TEST_DATABASE_URL=postgresql://natural_reader:<scratch-password>@127.0.0.1:5434/natural_reader_test
> ```
>
> `TEST_ADMIN_DATABASE_URL` is the maintenance database the suite connects to in
> order to create `natural_reader_test`; `TEST_DATABASE_URL` is the test database
> itself. Never put a real password in a file you commit.

```bash
# Backend — REQUIRES Postgres up (conftest migrates natural_reader_test on a
# real connection; without the DB every test errors, not skips). Run it with
# the two TEST_* variables above exported, or it uses 5433.
.venv/bin/python -m pytest server/tests/ -q          # 2812 passed, 16 skipped, 2 deselected @ 2026-11-03 (Talk in the chat)

# Frontend (vitest, jsdom — no services needed)
npx vitest run                                       # 1663 passed (138 files) @ 2026-11-03 (Talk in the chat)

# Lint
npx eslint .                                         # 0 errors; 24 warnings, see eslint.config.js (the same as npm run lint)

# Opt-in, slow: build and run the real backend image and the compose Keycloak
# entrypoint (deselected by default via pytest.ini addopts)
.venv/bin/python -m pytest -m container -q
```

**Test speech on a stream, in a real browser.** A VAD has memory, so a test that hands it one clip at a time and a fresh model measures something else: offline a spoken "stop" passed 92% of the time, and live 24%. To drive the real path, replace `navigator.mediaDevices.getUserMedia` in a Playwright init script with a `MediaStreamDestination`, play clips into it with a `BufferSource`, and read the numbers through **What it heard → Copy numbers**. Everything but the microphone is then real. Test audio stays out of the repo: ESC-50 is non-commercial. Still by hand: a real room, real speakers, a phone.

**Talk in the chat, walked on 2026-11-03** (scratch database, a scratch backend on loopback with sign-in on against a throwaway Keycloak on :18081, real Chromium, the fake microphone above, `base.en` and `gemma4:31b-cloud`): the admin's **Talk** swaps the typing box for the panel, the halo follows the voice, two questions are answered in the chat and **End** gives the box back, focused, with no live microphone track; a tap on the circle mid-reply is listening again in 57 ms, and the reply is kept as the 30 of its 202 characters that played; Mute, Escape. A member without `talk` sees no button and a hand-made spoken turn gets 403; the **People** tick writes the role to the app and to Keycloak, and a reload shows the button; a role given in Keycloak shows it at the next login. 375 and 360 px phones fit, talking included; the chat beside a page; five questions, first sound median 2.09 s. Found and fixed: someone left with only `talk` saw **Loading…** forever. Found, not fixed: a tick from a **People** list loaded before the person's roles synced sends the stale set (USER_GUIDE says to reload first).

`startup.sh` is tested without containers: `server/tests/startup_harness.py`
copies the script into a temp project and replaces podman, compose, curl, npm,
loginctl and systemctl with stubs that record their arguments
(`test_startup_script.py`). `test_container_files.py` renders the compose
files with the real podman-compose.

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
- Reset a poisoned test DB, on the scratch server only (the same URL as
  `TEST_ADMIN_DATABASE_URL`):
  `psql "$TEST_ADMIN_DATABASE_URL" -c 'DROP DATABASE IF EXISTS natural_reader_test;'`

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
| `test_migration_007.py` | inference_usage_shape | the table and its counters (its PK was `(user_id, day)` until migration 023, which the next section's `test_migration_023.py` covers) |
| | users_budget_column_exists | `inference_daily_token_budget` nullable |
| | usage_round_trip | upsert + read-back |
| `test_migration_023.py` (5, new) | 023_keeps_old_rows_as_unknown_model_and_kind | old rows survive as model `''`, kind `''`, 0 estimated, with version 23 recorded |
| | 023_primary_key_is_user_day_model_kind | the PK columns, in that order |
| | 023_kind_accepts_only_chat_reader_or_empty | `kind='other'` is a `CheckViolation`; `chat`, `reader` and `''` are accepted |
| | 023_drops_the_defaults_so_a_forgetful_writer_fails_loudly | an INSERT without `model`, or without `kind`, is a `NotNullViolation` |
| | 023_indexes_day_and_is_safe_to_run_twice | the `day` index exists and running the file again is safe |

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
| | *(phase 3, 12 new `usage` cases)* | see [phase 3](#admin-console-v2-phase-3-usage-by-model) |
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
| `test_chat_turns.py` | the `POST /v1/chat/sessions/{id}/turns` endpoint: request validation (413 over `CHAT_MAX_REQUEST_MB`, 422 malformed/empty/model-not-allowed/attachment-unsupported, 429 budget, 409 `turn_in_progress`, 503 `no_providers`/`db_unavailable`, 404 not-yours), SSE framing (`data: {...}` lines, always ending `data: [DONE]`, a terminal `error` frame on an unhandled exception), `: keep-alive` comments during a silent phase, and raw-ASGI disconnects (mid-stream, during a silent phase, and before the stream starts) all ending the turn `aborted` with the claim released |
| `test_chat_orchestrator.py` | `run_turn` against a fake provider: plain text, one tool round then a tools-off final step, a failing/unknown tool, budget exhausted between steps, client disconnect mid-step (saves `aborted`, releases the claim), a model without tools, invalid tool-call JSON becomes `{}`, a tool call written as reply text (bare or fenced) runs as a real call while non-call JSON, unoffered tools and prose stream unchanged — prose with no hold at all |
| `test_chat_context.py` | Stage 0: document prefetch hit/miss/unreadable doc, the `Current time: …` line (`test_time_line_falls_back_to_utc_on_bad_timezone` for a garbage timezone), history trimming (old attachments replaced by a marker before a whole turn is dropped), and the strict-template shape (`test_the_prompt_fits_strict_chat_templates`: one leading system message, alternating roles, the volatile block leading the last user message) |
| `test_llm_router.py` | provider-config parsing (`INFERENCE_PROVIDERS` + `INFERENCE_<NAME>_*`), `<provider>:model` id resolution, the no-config single-`ollama` fallback, a bare pre-C1 model name routing to the first `ollama`-kind provider, a stalled provider timing out of the model list (`LIST_TIMEOUT_S`) without hiding the others |
| `test_chat_sessions_api.py` | session CRUD, `POST /v1/chat/sessions/import` (legacy-chat copy, create-only) |

### Model-router config

`test_model_router.py` (pure env-parsing unit tests):

| Case | Asserts |
|---|---|
| defaults | unset → all-models allowlist (dev), `nomic-embed-text` embed, 300s timeout |
| allowlist_parses_comma_separated_with_whitespace | CSV parsing |
| empty_allowlist_means_all | unset/blank = allow everything |
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
| `test_inference_budget.py` | *(suite)* | record/spent/state/over/effective/admin_usage: day computed in Python, not SQL, in the deployment's timezone; per-user override vs deployment default, aggregation. Since admin console v2: `reset_is_the_next_local_midnight`, `reset_on_the_night_the_clocks_change` (New York, 2026-11-01, reset at 05:00Z), `usage_counts_toward_the_deployment_day` (20:00Z in Colombo is the next day), `admin_usage_window_starts_on_the_deployment_day` |
| `test_inference_budget.py` (21) | *(phase 3, new cases)* | `record_usage_splits_by_model_and_kind`; `record_usage_same_key_accumulates` (two upserts on one key: `requests` 2, tokens summed, which catches an `ON CONFLICT` target left on the old key); `record_usage_estimated_tokens` counts only writes flagged estimated; `record_usage_rejects_unknown_kind` (`''` and `CHAT` too, no row written); **`test_spent_today_sums_all_rows`** (a pre-migration `''`/`''` row plus new rows, and `budget_state`'s remaining: migrating mid-day changes nobody's budget); `admin_usage_default_shape_sums_models` (one dict per person per day, the old keys) |
| `test_chat_turns.py` | budget_already_spent_is_429_with_reset_time | 429 pre-check with `{remaining_tokens, reset_at}` detail, before a turn is even claimed |
| `test_inference_router.py` | models_includes_budget / models_budget_absent_when_unlimited | budget state rides `GET /v1/inference/models` |
| `test_chat_orchestrator.py` | budget_runs_out_between_steps | checked before **every** step, not just once up front |
| | missing_usage_is_estimated_and_recorded | a provider with no usage in its response is charged the chars/4 estimate |
| | disconnect_mid_step_still_records_usage | **C1 behavior change**: a client disconnect still charges for the partial reply |
| | provider_error_mid_text_still_records_usage | a mid-turn provider error still charges for what was generated before it failed |
| | *(phase 3, 6 new)* | `chat_step_records_model_and_kind` (`req.model_id`, `chat`); `cut_short_step_records_model_and_kind` (the `_finalize` path); `estimated_usage_marks_estimated_tokens`; `reader_batch_records_reader_kind` (chat and reader rows side by side, the reader on its own model); **`reader_bare_name_lands_canonical`** (`m2` and `ollama:m2` give one row `ollama:m2`; `vllm:small` and an unplaceable `gone` keep their names); `a_bare_reader_name_is_charged_under_its_canonical_id` (the `_charge_reader` write) |

### Server-side routing through model_router

| File | Case | Asserts |
|---|---|---|
| `test_embeddings_routing.py` | *(suite)* | embeddings read URL+model from `model_router` (no direct env reads) |
| `test_web_search.py` | web_search_makes_no_page_fetch_and_no_model_call (+SSRF suite, +fetch-cap suite, +passage suite) | a search returns `{title, url, site, snippet}` and fetches no page and calls no model (the summary machinery is gone); SSRF guard suite (private-IP block, redirect re-check, scheme allowlist); fetch-cap suite via `httpx.MockTransport` (endless body stopped at `MAX_RESPONSE_BYTES`, a trickling body cut off at `WEB_SEARCH_FETCH_TOTAL_S`, non-text `Content-Type` skipped without reading the body, normal HTML still extracts); passage suite (`page_passages` finds text past 6,000 characters, keeps page order, cuts at lines) |

### Security hardening (SEC fixes)

`test_security_hardening.py` — path traversal on `doc_id` (hex-regex gate),
PDF storage path escape, CORS origin parsing (wildcard vs explicit list,
credentials off/on), TTS/batch request size caps.

### Document versions (V1)

A project's documents gain a version history (spec:
[superpowers/specs/2026-10-12-document-versions-design.md](superpowers/specs/2026-10-12-document-versions-design.md);
rules in [LIBRARY.md](LIBRARY.md) "Versions (V1)"). The backend tests share
`server/tests/versions_harness.py`, which builds an app with the project and
document routers over real Postgres.

| File | Pins |
|---|---|
| `test_migration_018.py` | a pre-018 database upgrades: every placement (normal, unverified legacy, filer renamed their entry, filer removed it) becomes a current v1 whose `file_name` equals what the project displayed before; `verified` kept; `last_version = 1` |
| `test_document_versions_schema.py` | one current version per document; content current in at most one document per project (the duplicate guard); a version can't belong to another project's document; notes capped at 500 characters; removing a document removes its versions but not content someone else holds; the test seed names a placement like the migration does |
| `test_doc_versions_read.py` | the current-versions listing (one row per document, sorted by name); history newest first, unverified versions hidden; another project's document is 404; `versions_in` names only visible projects and flags current; the highest number is reported when content repeats; the display-name order (my entry, a version's name, the canonical name); an old version is readable but not listed; SQL placeholders match their params |
| `test_doc_versions_write.py` | add / restore / delete / merge / remove rules: a new current version is recorded; the same-bytes table (`already_current`, older bytes become a restore, `current_elsewhere`); restore appends a copy and refuses bytes a later version already holds; deleting an old version collects orphaned content and keeps shared content; merge order, numbering and refusals (`merge_self` only for a visible document); numbers are never reused; a legacy unverified current version is verified in place; note cleaning |
| `test_doc_versions_concurrency.py` | two simultaneous uploads become consecutive versions; the same bytes into one document at once is one version and one 409; crossed merges: one wins, the other 404s, no deadlock; a version write overlapping a project deletion: both complete, no deadlock; the same bytes into two documents at once is a 409, not a 500 |
| `test_project_documents_routes.py` | the `/v1/projects/{id}/documents` routes end to end: list, history, add, restore, delete, merge, remove; refusal shapes (every 409 names the document); `from-library` needs your own verified upload; note length is the service's rule (422), not pydantic's; a `DeadlockDetected` is `503 busy`, not a 500 |
| `test_version_upload.py` | the multipart upload: new bytes become the indexed current version with no library entry added; known bytes aren't reprocessed; same-bytes rules; a row upload ignores another document's name; the note and the role are checked before any bytes are stored; a lost race with garbage collection retries once; the document is locked before the content row |
| `test_project_filing.py` | `PUT …/docs/{doc_id}`: a new file is a new document and refiling is idempotent; a matching name asks, and `as_version_of` / `keep_both` both work; the same name in another project doesn't ask; older bytes offer a restore and win over a name match; content current elsewhere can't become another document's version; a legacy unverified current version is proved, not duplicated; a lost filing race is a 409 |
| `test_project_permission_matrix.py` | every project-document action (see documents, see history, add a version, restore, delete an old version, merge, remove) against reader / contributor / maintainer / owner / non-member / admin-non-member, and a full walk through filing, versions, merge and removal writes no names, file names or notes at INFO or above (IDs, roles and counts only) |
| `test_chat_tools.py`, `test_chat_prompt.py` | an old version's document listing carries `version_note` (and a current one doesn't); the system rules gain the older-version sentence only when a note is present |
| `ProjectDocsTab.test.jsx` | one row per document with its version count; history newest first with Open / Restore / Delete by role; upload from a file or from my library, with a note; "matched an older version" message; merge and remove confirmations (naming the versions); an open history refreshes when another row's merge changes it; Reader sees no controls; a refusal reloads |
| `NameMatchDialog.test.jsx`, `useProjectFiling.test.jsx` | the dialog's buttons for a name match and for an older version; Escape cancels; the hook files straight through, opens the dialog on a conflict, retries with the choice, and reports other refusals |
| `LibraryPage.test.jsx` | chips show the version a project is on and the name opens the project; × asks first when there are several versions; the "has a newer version" line with Open current |
| `OldVersionBanner.test.jsx`, `useVersionStatus.test.jsx`, `versions.test.js` | the banner's text and Open current, nothing when current, in normal flow (never over the toolbar); the hook refetches on a new version; the rule "old somewhere and current nowhere" (the README case shows no banner) |
| `ActivityTab.test.jsx` | the four version events render as sentences |

### Admin console v2, phase 1

The sections, the one row pattern, the Overview, the deployment timezone and the Docling limits (spec:
[superpowers/specs/2026-10-25-admin-console-v2-design.md](superpowers/specs/2026-10-25-admin-console-v2-design.md)).
Backend counts below are collected cases (parametrized ones expanded).

| File | Pins |
|---|---|
| `test_console_settings.py` (10) | the row pattern: console value beats env beats default; `narrow` keeps a console value inside the env's bound; an invalid env value is warned once and ignored; an unusable stored value is ignored; **a save keeps keys it never loaded** (a second admin's, or a newer version's); a reset removes only that key and the last reset deletes the row; a refused value saves nothing; one audit line per changed setting; the view's shape |
| `test_deployment_settings.py` (12) | zone names are checked against the zone database (a path like `../etc/passwd` is refused); UTC with nothing set; `TZ`; the console wins over `TZ`; a POSIX `TZ` string is ignored with one warning |
| `test_deployment_info.py` (5) | the read-only rows: **a secret never appears** (set / not set only); a URL loses user info, query and fragment; each row names its variable or "default"; values come from the env it is given; a malformed URL shows a placeholder, not a 500 |
| `test_console_overview.py` (26) | each check's states; **a check that hangs answers "Didn't answer within 5 seconds." while the others still answer**; a check that raises names the kind of failure; fixed order; the embedding probe bypasses the indexing semaphore, sends `embed_one`'s request, and reads an HTTP 404 as "answered HTTP 404", not "Can't reach"; a local Ollama model that isn't loaded is "Unknown", never its maximum; an Ollama cloud model gets its maximum; the project-turn estimate; the embedding model isn't listed as a chat model; **a malformed `INFERENCE_*` value costs one line, not the page**; the route is admin-only |
| `test_admin_settings.py` (7) | `GET`/`PUT /v1/admin/settings/{section}`: shape, set then reset the timezone, an invalid zone is a 422 `invalid_setting` naming the setting and writing nothing, an unknown section is 404, an unknown body field is 422, a member gets 403, a malformed URL in the env doesn't break the GET |
| `test_docling_limits.py` (51) | parsing of each setting and its env text; narrowing (presets intersected, switches ANDed, the lower page limit, `role` wins over `readers`); `check` for every refusal and its `option`; **OCR off refuses Accurate**; `user_view`; the info rows |
| `test_convert_limits.py` (18) | the rules at `POST /v1/docs/{id}/convert`: **an unknown page count is counted from the stored file** (a long file is refused, a short one isn't), a recorded count is used without opening the file, no page limit means the file isn't opened, an uncountable file is left to Docling; `role` mode refuses a reader without the role and lets role holders and admins through; **a `documents`-scoped token carries the role**; **an env bound refuses what the console allowed**; a bad range is a 422; `GET /v1/settings/docling` and its auth. Not covered: the "Only people with the Docling role" refusal was never walked with a second signed-in user |
| `test_capabilities.py`, `test_auth_token_scopes.py`, `test_admin_router.py` | `docling` is read from the realm roles; the `documents` scope lets `docling` through and `read_aloud` doesn't; `PATCH` accepts the `docling` capability |
| `test_llm_ollama.py` | `loaded_context_lengths` reads `/api/ps` `context_length` and is empty on any error |
| `test_chat_context.py`, `test_chat_orchestrator.py` | the "Today is" line falls back to the deployment zone; a turn gets the deployment zone as its default; UTC when it can't be read |
| `AdminConsole.test.jsx`, `ConsoleShell.test.jsx` | the section list and the phone picker; arrow keys, Home, End and wrap; **the unsaved-changes prompt** (Keep editing, Discard changes, Escape, focus); sections open from Overview's links; the moved sections behave as before |
| `useSectionSettings.test.js`, `SettingRow.test.jsx` | drafts, one Save, reset shows the fallback before anything is sent, **edits made during an in-flight save survive**, one save at a time; the source line wording and **Use the server setting** |
| `OverviewSection.test.jsx`, `DeploymentSection.test.jsx`, `DocumentsSection.test.jsx` | the states and **Check again**; the timezone row and the read-only list; Documents: who may convert, presets (at least one), OCR (**Accurate off with it**), the page limit's validation, narrowing by the env shown with its reason |
| `DoclingConvertDialog.limits.test.jsx`, `useConvertLimits.test.js` | the dialog greys out what is off with "Turned off by your admin"; Convert waits for a page range that fits the limit; a person who may not convert sees why |
| `zoneFormat.test.js` | a day formatted in the deployment's zone; the zone list |
| `phoneTargets.test.jsx` | **44 px targets below `md`** in the console's sections (class assertions: jsdom has no layout; the measured heights are in the walk below) |

**Walked in the running app on 2026-10-26** (scratch database, `AUTH_ENABLED=false`, headless Chromium, desktop and 390 px):
Overview and every check state; `/api/ps`'s `context_length` is a number on the installed Ollama; Check again; the timezone, the unsaved prompt and People's dates in Colombo; Documents with OCR off and a 2-page limit; the Convert dialog's greyed options and its page-range gate, then a real 2-page conversion; the server's 403s by `curl` (6 pages, Accurate with OCR off, forced OCR); the phone picker; keyboard; a canary `SESSION_SECRET` found nowhere in the page or the API payload. The 44 px check failed on the moved sections the first time and was fixed in `1100e71`.
**Not walked:** a non-admin refused by the `role` rule (needs a second signed-in user: tests only); the Context windows row of a *loaded* local chat model (the `/api/ps` shape was confirmed live, the row is tested with fakes); dark mode.

### Admin console v2, phase 2a

The Models section, the per-turn model resolution, and People's search (plan:
[superpowers/plans/2026-10-26-admin-console-v2-phase2a.md](superpowers/plans/2026-10-26-admin-console-v2-phase2a.md)).
Counts are collected cases (parametrized ones expanded) in the whole file, new and old.

| File | Pins |
|---|---|
| `test_model_settings.py` (74, new) | the six settings' parsing and the console's refusals (a size outside 2,048-2,097,152, a window outside 1,024-10,000,000, a locality that isn't local/cloud); maps stored sorted, lists unique in order; console beats env and a reset returns to it; the reader chain names the variable that decided it; **`num_ctx_for`'s order: per-model size, deployment size, the person's value, 16,384, always clamped**; a non-sized model ignores the deployment sizes and passes the person's value; **`sizes_context`**: any self-hosted Ollama is sized whatever its host, Ollama cloud names / ollama.com / `openai`-kind are not, and a provider marked Cloud still is (P11); **`is_local`**: localhost, private, loopback and 100.64.0.0/10, single-label and `.local/.lan/.internal/.home.arpa` hosts, one trailing dot ignored, the admin's override; `:latest` doesn't matter; declared windows (console, then `INFERENCE_<NAME>_CONTEXT_WINDOW`, a bad value ignored with one warning); `offered` narrows and a selection whose models are all gone offers nothing |
| `test_chat_orchestrator.py` (96) | **a local Ollama turn sends 16,384 with no setting; a remote self-hosted one is sized too; the admin's size beats the person's, and unset keeps theirs; a cloud model keeps the personal value;** history is trimmed to a declared window for an `openai`-kind model; an unreadable `models` row falls back to the environment; **the reader is sized to its own model, gets none on a cloud chat model even when the person set a size, is held to the ceiling, gets none when its model ignores `num_ctx`;** an unresolvable reader never fails the turn; the console's reader beats the environment's; the reader keeps its other knobs |
| `test_chat_reader.py` (78) | **`fit_input_chars` keeps every batch inside the window (a property over many sizes), is `READER_INPUT_CHARS` with no window and never exceeds it;** the configured size is sent on every batch, never a per-batch one; a reader of `chat` uses the turn's model; the default reader is the chat model; a batch holds at most 16 passages |
| `test_chat_turns.py` (27) | a model the console left out is refused (422 `model_not_allowed`), one it selected is allowed, no selection allows what the environment allows, an unreadable `models` row doesn't stop a turn |
| `test_inference_router.py` (15) | `GET /v1/inference/models` follows the console's selection; a self-hosted Ollama model reports `context_size {fixed, auto}`, a cloud or `openai`-kind one has none; a self-hosted Ollama on a public host gets the entry; an unreadable row falls back to the environment. Note: the file defines the `client` fixture twice and the later one wins, so every test in it runs as the seed admin, not `u1` (a deferred review finding) |
| `test_admin_router.py` (37) | `GET /v1/admin/models` (shape; lists models the console's own selection left out; needs admin); **`GET /v1/admin/users` search**: name, email, username and full name; `%` and `_` are literal; status and capability filters; paging with `X-Total-Count`; **no parameters returns the whole table as before**; bad parameters are 422; admin only |
| `test_console_overview.py` (41) | the default reader is **OK**, never a warning; a reader of `chat` from the environment or the console is OK; **the quota hint asks about cost (`is_local`), counts only models offered, and never costs the reader line when a provider hangs or `INFERENCE_*` is malformed;** a named reader is checked and says where it was set; a model that isn't listed says how to fix it; **the context window of a sized model is the size requests send, whatever its host; a loaded model reports what is loaded;** other providers report their number, a declared one, or Unknown; the console's selection applies; an unreadable row uses the environment |
| `test_app_wiring.py`, `test_admin_settings.py`, `test_deployment_info.py` | CORS exposes `X-Total-Count`; the `models` section round-trips through `PUT`/`GET /v1/admin/settings/models`; Deployment's read-only list has no "Reading model" row |
| `ModelsSection.test.jsx` (24, new) | the six rows from a settings payload and a catalog; Save off for an invalid size (with the reason, and clearing once fixed) and for an invalid deployment size; a per-model size adds or removes only its key; a declared window below 1,024 is refused; **a model whose provider is set to Cloud keeps its per-model size box** (Cloud is cost-only); an Ollama cloud model or an ollama.com provider is never sized; **Auto** names what the host rule detected; none ticked says every allowed model is offered, and a stored id the catalog no longer lists survives a toggle; a catalog that fails leaves the settings rows; the current reader stays choosable; `role="alert"` on a refusal; help and source lines tied to each control; the toast "applies from the next message" |
| `AdminConsole.test.jsx` | a Models tab between Projects and Documents; Overview's **Open models** opens it; the unsaved-changes prompt covers Models; **People search**: first page of 50 and the total, one request 300 ms after typing stops with `q`, status and capability apply at once and combine, **Show more** appends and disappears when all are loaded, "No one matches.", **a late answer to an older search never overwrites the newest**, a reload after an edit keeps the search and the pages loaded |
| `SettingsDialog.test.jsx`, `inferenceLimits.test.js` | a fixed size disables **Context window**, says "Set by your admin: N tokens." and keeps the person's stored value; a thousands separator; **Auto** names the size it resolves to; a model with no `context_size` is as before; the snake_case payload is read into camelCase; the Auto label is the only option changed |
| `phoneTargets.test.jsx` | 44 px classes on Models' controls and on People's search, filters and **Show more** (class assertions: jsdom has no layout) |

**Walked in the running app:** not yet. Task 11 of the plan (the in-app walk) is
pending; everything above is jsdom or backend tests with fakes. See HANDOVER's
2026-10-26 phase 2a entry for what is unverified.

### Admin console v2, phase 3 (usage by model)

Usage per model and kind: migration 023, the writers, the grouped route and the
rebuilt Usage section (plan:
[superpowers/plans/2026-10-26-admin-console-v2-phase3.md](superpowers/plans/2026-10-26-admin-console-v2-phase3.md)).
Counts are collected cases in the whole file, new and old. Run the backend with
the two `TEST_*` variables set (see [How to run](#how-to-run)).

| File | Pins |
|---|---|
| `test_migration_023.py` (5, new) | an old row survives as `''`/`''`/0 and version 23 is recorded; the PK is `(user_id, day, model, kind)`; the kind CHECK; no defaults left on `model` and `kind`; the `day` index and a safe re-run |
| `test_inference_budget.py` (21) | six new cases: the split by model and kind; **two upserts on one key both count**; estimated tokens; an unknown kind is refused before any write; **`spent_today` sums old and new rows, so an upgrade mid-day leaves everyone's remaining budget alone**; `admin_usage` is still the one-row-per-person-per-day shape |
| `test_chat_orchestrator.py` (103) | six new cases: a chat step, a cut-short step and a reader batch each record their model and kind; the chars/4 fallback marks `estimated_tokens`; **a reader named bare or canonical lands in one row, an unplaceable one keeps its name**; the `usage` helper in the file now sums rows (a turn with a reader writes two) |
| `test_admin_router.py` (49) | twelve new `usage` cases: `group=model` rows carry `tag`; **the tag follows the provider's mark (changing it re-tags)**; **a model whose provider was removed, and a bare name, are `unknown`, not a 500**; `user_id` and `model` filters, and `model=` empty matching the Unknown rows; `group=day` and its window; the chat/reader split as integers with kind `''` only in `tokens`; **a bad `group` or `user_id` is a 422**; **no `group` is exactly the old key set**; a member gets 403 on all four groups |
| `UsageSection.test.jsx` (19, new) | By model is the default and renders **Where**; a model-less row reads Unknown and Total is not chat + reader; tables sit in `overflow-x-auto`; **By person** and **By day** request their group, with `aria-pressed` following the buttons, and a day is shown as given; the per-person disclosure fetches once, sets `aria-expanded` and doesn't refetch on reopen; a range change clears expansions; the **Model** select lists every model, sends no `model` for **All models** and encodes a pick; **an opened person's rows follow the Model select without another request (R5)**; the Unknown note shows in any view only when the range has a model-less row; **Estimated** as a whole percent, `<1%` and a dash; the empty state; the load-failure toast; **three race tests, each shown red with its guard removed:** a slow reply for an old range, a slow By person reply after the Model select changed, and an expansion answered after the range changed |
| `phoneTargets.test.jsx` (12), `AdminConsole.test.jsx` | 44 px classes on the three view buttons, the **Model** select and the disclosure (class assertions: jsdom has no layout); the three older Usage tests now click **By day** and expect `?days=30&group=model` |

**Walked in the running app (plan Task 6, before the last fix):** the Usage tab
at 1280 px and 375 px against a scratch database and real turns on a local and
a cloud Ollama model, with one seeded pre-upgrade row. **Not walked:** the final
change (a person's rows following the **Model** select); more than one person;
the **Estimated** percentage (only the unit tests render it, Ollama always
sends counts); a provider's mark changing a tag, or a removed provider, in the
browser (both are backend-tested); a production build (the first open sent
`group=model` twice in the dev server, likely StrictMode); dark mode. See
HANDOVER's phase 3 entry.

### Message box inputs (text files and voice)

Attaching a text file with its secrets masked in the browser, and speaking a
message into the box (spec:
[superpowers/specs/2026-10-27-chat-composer-inputs-design.md](superpowers/specs/2026-10-27-chat-composer-inputs-design.md)).
Counts are collected cases in the whole file, new and old. **No test downloads a
Whisper model or calls a real transcription server**: the local engine's model
class is monkeypatched and the remote engine runs against a fake transport.

| File | Pins |
|---|---|
| `textAttachment.test.js` (134, new) | masking: an env file masks every value except empty, `true/false/yes/no/on/off` and an integer of up to 6 digits (so **a short all-digit password is not masked**, by design); a value that starts `scheme://` becomes `<masked URL, N chars>`; keys, comments, quoting and order survive; any other file masks only private keys (an unterminated block runs to the end), well-known token prefixes, JWT shapes, keyword lines and `user:password@`; **detectors report ranges over the original text and one render writes the placeholders**, so nothing is masked twice and long unbroken runs stay linear; the count is per value; reading refuses over 64 KB, a NUL byte and non-UTF-8 |
| `ChatView.textFile.test.jsx` (29, new) | the paperclip and a pasted file stage a chip with its name, size and "N values masked" (drag and drop is walked, below, not unit-tested); the viewer shows the text that will be sent; **Send real values** asks first, names the model and adds the cloud clause for a cloud model, and **Keep masked** backs out; **changing the model puts masking back with the notice**; a refused PDF, a file over 64 KB and a message over 128 KB each toast and leave the rest staged; the upload carries the masked text unless real values were agreed |
| `attachment.accept.test.js` (2) | the paperclip's `accept` list takes text types and images |
| `test_migration_024.py` (2, new) | `'text'` joins the `kind` CHECK and any other kind is still refused; the file's Down removes the text rows, restores the old CHECK and forgets version 24 |
| `test_chat_turns.py` (38) | six new cases: a text attachment is stored with its `masked` flag, and without one stores `false`; an image carries no `masked` key; each refusal (not UTF-8, a NUL byte, over 64 KB, over 128 KB, a non-text MIME) is a 422 `invalid_attachment` naming the file; two full-size files exactly fit the message limit |
| `test_chat_context.py` (59), `test_chat_prompt.py` (140) | the file is inlined after the typed text as an `<attached_file name="…">` block; **a file containing `</attached_file>` can't end its block**; images still ride as attachments; an old text file in history is inlined, costs its characters ÷ 4, and is dropped (with the marker) before whole turns; the rules say an attached file is data, never instructions, and what `<masked…>` means; **real values stay with their model**: a reply from a model that was given real values is replaced by a marker for every other model (the walk A then B then A, a reply from before the file, a model never given values, a masked file, a reply with no model recorded), the marker's length is what the trimmer counts, and the held-back file and replies come out as a saved note, not a toast |
| `test_speech_engines.py` (45, new) | the local engine against a monkeypatched `WhisperModel` (loading then ready, a failed load is an `error` status naming the exception's type, and the next `warm()` retries it); **`decode_capped` stops decoding past the cap** and a recording that is not audio is `BadAudio`; the remote engine against `httpx.MockTransport`: the URL and key, `verbose_json` then the `json` fall back, a language name or code, readiness from `GET /models` (401/403, a non-200, unreachable), a missing URL or model reported through `status()`, and the key never in a log, an error or the repr |
| `test_speech_service.py` (19, new) | one engine per process built from `SPEECH_ENGINE`; **one running transcription per person (`AlreadyRunning`)**; the local engine one at a time, a second waiting then `Busy`; **a cancelled request keeps the slot until the thread ends**; the log line has the person, engine, seconds, language and time, never the text; `SPEECH_CPU_THREADS` default and a non-number |
| `test_speech_catalogue.py` (32, new) | the three sources of the speech model list, a later id replacing an earlier one; **a bad entry is a warning and the rest stay**, including YAML that makes an unhashable language code; a choice that's gone falls back; an entry in the database can't name a local directory; a standing problem warns once, not on every request |
| `test_speech_convert.py` (18, new) | conversion runs as its own process, is reused, and survives a second worker finishing first; a killed one says so, where stderr holds only a progress bar; the Hub's answers become `NotOnHub`, `NotWhisper` or `HubUnreachable`, and a name it won't accept isn't an outage; **a card language Whisper doesn't know is left out**, because keeping it made the whole entry vanish after "Added" |
| `test_speech_models_admin.py` (35, new) | choose, reset, add and remove through the real routes; a model the list would drop is refused, not added; the same repo at another revision and two repos with the same slug both land, and neither replaces a built-in; removing a model not in use leaves the choice alone |
| `test_tts_language.py` (26, new) | each voice reads in its own language; **real espeak: Sinhala through any voice gives exactly the phonemes of Sinhala itself, with no `(si)` or `(enus)` left to be spoken**; English words in it stay English; plain English is untouched and real brackets stay; the app's list and the extension's each hold exactly the voices in `voices-v1.0.bin` |
| `SearchableList.test.jsx` (29, new), `SpeechModelPicker.test.jsx` (18, new) | a short list stays a native select; a long one is one control, opening with every option and the box focused, on the option in use; typing narrows, and typing on the closed control starts the search; a click, or the arrows and Enter, choose and hand focus back; a disabled option is seen but skipped; **Escape closes the popup, not the dialog around it**; a press outside chooses nothing; **it draws outside its panel, and inside a dialog**; every added model has its own **Remove**, one that can't run here included; the control keeps its name past 12 entries |
| `test_speech_router.py` (51, new) | **a failed read of the options keeps the running model, and a switch during an upload isn't swapped back**; the status route (always 200, the languages sorted by name, nothing built while off, a hung or raising engine is a state, an engine in error is not retried by a page load); the POST (404 when off, 403 without the `chat` capability or a PAT without the scope, 413 over 10 MB by declared length and by a chunked body with none, 400 for an empty or non-multipart body, 422 for an unknown language, each engine error to its status and words); **a 3 MB upload never rolls over to a temp file**; **the transcript is never logged**; **a client that drops mid-upload is a quiet 400, not a traceback** |
| `test_chat_options.py` (56), `test_admin_router.py` | `voice_input` beside `web_search` in one row: each save leaves the other key alone and a reset removes only its own; the environment value, an invalid one (warned, then off) and the admin's explicit `false` beating `CHAT_VOICE_INPUT=on`; the PUT starts the engine loading only when voice ends up on, and a failed `warm()` doesn't fail the save |
| `test_console_overview.py` (48), `test_deployment_info.py` (9), `test_user_defaults.py` (21) | Overview's **Voice input** line in each state (off, ready with engine, model and place, getting ready, a reason, a status that raises); the Deployment rows with their defaults and never the key; the **Speech language** default accepts `auto` or a Whisper code |
| `useVoiceInput.test.js` (56, new) | one thing at a time; the microphone's tracks end on **every** way out (Stop, Cancel, Escape, the 2-minute stop, a recorder error, unmount, a stream arriving after Cancel); an answer after Cancel changes nothing; `language` is sent only when it isn't Auto; **a 404 when voice was switched off toasts "Voice input was turned off."** and one while it is still on toasts "Voice input isn't available right now."; the three disabled reasons; "No speech was heard"; **each refusal message the server sends, in its real `{detail: {error, message}}` shape, reaches the toast** |
| `test_speech_window.py` (7, new), `test_tts_threads.py` (4, new) | Whisper's scores come from the most speech-like segment, none without segments; the window's defaults are the measured ones, a bad value keeps its default, the stay score never passes the start score; Kokoro's threads |
| `test_chat_store.py`, `test_chat_turns.py`, `test_chat_web_consent.py` | **the next turn cuts a reply to what was heard, one saved `complete` included**, only an assistant reply of this chat; a spoken turn asks for a short plain reply and is never offered the web search card; **a spoken turn without the `talk` role is a 403 before the model is called**, an admin's isn't |
| `test_capabilities.py`, `test_auth_token_scopes.py` | `talk` is read from the realm roles, **the realm file seeds every capability the app knows**, and a token's `chat` scope lets `talk` through |
| `lib/speech/*.test.js` (68, new) | the whole conversation with no browser: pause, resume at the same place, a real interruption, how much was heard, an echo only while a reply plays, words arriving while a reply stops; **a tap on the circle stops the reply, keeps listening, and only the next message carries what was heard**; sentences and the cut at a comma; **`listen.test.js`: the VAD's memory is cleared in a silence**, and each frame's loudness on a decibel scale |
| `useSpeechWindow.test.js` (17, new), `TalkPanel.test.jsx` (19, new), `ChatView.talk.test.jsx` (9, new) | the microphone is released on End, on leaving, and when it opens too late; Mute; a clip goes as WAV in the window's language; **the screen is kept awake while talking, asked again when the page comes back, and a refusal changes nothing**; the panel's states, Escape, a tap that cuts in only while it speaks or thinks; Talk's three waiting reasons; **in the chat: Talk swaps the typing box for the panel and End brings it back, focused; another chat or losing the role ends it, and a new chat getting its id doesn't** |
| `AuthGate.test.jsx`, `useViewModeGuard.test.js`, `AdminConsole.test.jsx` | **`talk` or `docling` alone shows the no-access screen**, not an endless Loading; a gone view moves on even for someone holding a role of the same name; the **talk** tick and enrolling with it |
| `ChatView.voice.test.jsx` (30, new) | the mic shows only when voice is on (nothing when it is off or the status can't be read); Stop and Cancel with the timer; **the text lands at the caret with a space only where needed and nothing is sent**; Escape cancels; **focus goes to Stop, then Cancel while transcribing, then back to the box, and never takes it from another field**; the word "Transcribing…" is `sr-only` below `sm` and stays in the accessibility tree |
| `ChatOptionsSection.test.jsx` (40), `SettingsDialog.test.jsx` (27), `DefaultsSection.test.jsx` (8) | **Voice input** On/Off saves at once, with its source line and **Use the server setting**; **Speech language** lists Auto then the languages by name and shows only when voice is on; the admin default's language names |

**Walked in the running app on 2026-11-02** (scratch database with migrations
001-024, a scratch backend on loopback with `AUTH_ENABLED=false`, real Chromium
1217 headless, a fake microphone replaying a recorded sentence; 35 steps, 33
passed and 2 failed, then four fixes): an `.env` attached, masked, viewed, sent to
a cloud model that named the right line without asking for the secrets, and found
in no table, dump or log; **Send real values** for a cloud and a local model, and
masking coming back on a model change; drop and paste; voice off (no button) then
on from **Chat options**, with the first download and load at **46.6 s** and a
restart at about 3 s; a sentence in English, Spanish (Auto detected `es`) and Hindi
(right script, several word errors); Cancel, Cancel while transcribing and Escape;
the microphone released on every way out; two tabs with voice switched off under
the recording; the recording nowhere on disk, in the database or in any log; the
speech language in **Settings** and the admin default; a Docling conversion
keeping a picture. It found, and these were fixed (the controller re-checks them
in a browser): a closed connection mid-upload logged a traceback; a recording
lost with no message when voice was switched off under it; the message box
squeezed to 30 px at 375 px while "Transcribing…"; keyboard focus dropped to the
page after Stop. Still open from the walk: the page scrolls sideways at 375 px
because of the header's right-hand buttons (not the composer; a known item from
before this branch).
**Also checked after the fixes:** the real container image (memory and speed figures in DEPLOYMENT.md, "Time, as measured"), and a logging proxy in front of a local model server, which showed what each model was actually sent. It found that a local model's reply carrying real values was still sent to a cloud model after the file itself was held back; replies are now held back too. Walked again through the proxy: the cloud model's request no longer carries the value, the note shows in the chat, and back on the local model the file and its replies are sent.
**Not walked:** real Safari or a real phone (only Chromium's 375 x 812); a person
without chat access (the walk had one admin, so the 403 is tests only); a real
blocked-microphone prompt and the disabled states (the fake-media flags
auto-grant and loopback counts as secure); the recording stopping itself at 2
minutes (the longest was 101 s); a real remote speech engine; Docling's
**Describe** and **Accurate** modes; Sinhala and Tamil quality; real drag and drop
from the operating system (only synthetic events carrying real files); a real
microphone in a noisy room.
**Walking it yourself:** set `SEARXNG_URL` to something unreachable
(`http://127.0.0.1:1`) first. Its default is the usual local SearXNG port, and
**Overview**'s Web search check will otherwise probe a live SearXNG if one is
running there.

**Speech models and long lists, walked on 2026-11-02** (scratch database and backend, real Chromium, real speech through a fake microphone): **Speech model** in the console, with run15 and the Tamil model each downloaded and converted by the app (105-126 s) and spoken Sinhala and Tamil landing in the message box in 1.7-1.9 s; the unconverted engine at 8.8 s; a model added from the real Hugging Face Hub, pinned, chosen and removed; a typo, a missing repo and a repo that isn't Whisper each refused with the reason; 340 models in **Admin → Models** at 1366 and 375 px, with Save on screen throughout, a search narrowing to 1, a tick saved; the timezone search. Conversion and both engines also ran inside the real Docling image, with no network.

After the final review, again in a real browser: the Dhivehi repo that used to vanish after "Added" stays in the list; **Remove** on a model not in use sends no `PUT` and leaves the choice alone; a model marked as unable to run (the mark was faked in the browser, the DELETE was real) is still removable; `owner/na--me` says there's no such model, not that Hugging Face is down; a repo already built in is refused as listed; the select keeps its name past 12 entries; in **Settings → Chat → Speech language**, Escape clears the search and a second Escape closes the dialog. Converting elsewhere, exactly as DEPLOYMENT describes: the app's converter inside the Docling image with no network, `podman cp` into a fresh `cache` volume, and the real engine with `torch` and `transformers` blocked: ready, 8.4 s of speech in 1.9 s, neither package imported. `compose.speech.yml` was rendered by podman-compose with the file mounted read-only.

**Not walked:** the real plain image (its refusal is unit-tested, and the route above blocked the packages inside the Docling image); `compose.speech.yml` on a running stack; more than one worker swapping (unit tests only); Sri Lankan Tamil; a person's Settings line in a browser (unit tests only); a fine-tune for a language Whisper doesn't list, past adding it.

**Voices and popup dropdowns, walked on 2026-11-02** (scratch database and backend, real Chromium, real Kokoro): a Sinhala text file opened in the reader, **Omega (Hindi Male)** found by typing in **Settings → Voice**, its preview reading Hindi, and **Read** sending all four sentences through `hm_omega` with audio back, the one mixed with English included. Before the change, 340 models in the chat panel showed rows about 15 px tall; after, rows are 28 px on desktop and 48 px at 375 px, and the popup fits the screen. Also: keyboard choice with focus handed back, Escape inside Settings, dark mode, the timezone search, the Library opening on Projects, headphones on the audiobook button, and the three icon files served. English audio was compared sample for sample, old path against new, on three texts: identical. **Measure rows, not only where Save sits:** 424 rows in **Chat models offered** were 0 px tall at desktop width and passed an earlier walk, because that walk checked Save and the phone width's 44 px minimum hid it; they measure 16 px now, in a box that scrolls. **Not walked:** a real phone keyboard over the popup; how the Chinese and Japanese voices sound; the extension's voice list in a browser.

**Cloud evals before v2.5.0** (`gemma4:31b-cloud` with reader and summary models set, on a scratch database, at `727a336`; the review fixes after it touch no chat code): document QA 9/9 and inline citations 4/4. Project QA 12/13, 10/13 and 10/13, against 10/13, 9/13 and 9/13 for the v2.4.0 code on the same frozen eval, so no regression. The same questions fail on both, and `overview/features` failed every run on both.

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
| `ChatView.status.test.jsx` (10) | "Still generating…" for a reload mid-reply, "Stopped" for an aborted one; the held-back and larger-than-context notes drawn under a reply, and again after a reload |
| `InferenceRow.test.jsx` | per-model settings row, replacing the removed source-toggle UI |

### Frontend: budget UX

| File | Case | Asserts |
|---|---|---|
| `ChatView.meter.test.jsx` | 9 | context meter accounting; send disabled when `inferenceBudget.remaining_tokens === 0` |
| `chatTransport.test.js` (budget half) | — | `budgetDetail` + `formatResetAt` |
| `ChatSidebar.jsx` tests (meter) | — | "N tokens left today" rendering — C1 removed the dedicated `ChatSidebar.inference.test.jsx`/source-toggle file along with the toggle itself |

### Everything else, by area

| Area | Backend (`server/tests/`) | Frontend (`src/`) |
|---|---|---|
| Documents | `test_docling_pictures.py` (Docling's real document type and export, no model), `test_doc_figures.py`, `test_docs_*`, `test_doc_*`, `test_extract.py`, `test_chunk_split.py`, `test_content_ops.py`, `test_entry_verification.py`, `test_hybrid_search.py` | `IndexButton`, `ConvertButton`, `PdfViewer.banner`, `PdfToolbarMenu`, `MarkdownReader.figures`, `DoclingConvertDialog.limits`, `docMeta`, `docHash`, `docStatusPoller`, `segmentation.fixtures` |
| Library and access | `test_library_*`, `test_people.py`, `test_project_*`, `test_admin_projects.py`, `test_capability_enforcement.py` | `LibraryPage*`, `ProjectsTab`, `ProjectPage`, `ProjectDocRow`, `MembersTab`, `ShareDialog`, `PeoplePicker`, `projectsApi`, `visibleTo` |
| Chat | `test_chat_*` (context, store, sources, web, consent, project scope, read pages, multiround), `test_llm_openai.py`, `test_project_map.py`, `test_assistant_profile.py` | `ChatView.*` (incl. `textFile`, `voice`), `textAttachment`, `attachment.accept`, `ChatSidebar.*`, `ChatSidePanel`, `ScopeChip*`, `WebOfferCard*`, `*.journey`, `citations`, `pins`, `sessionStore`, `useChatEngine`, `useDocUse` |
| Reader and voice | `test_tts_stops_on_disconnect.py`, `test_synthesize_gate.py`, `test_speech_*` (voice input, above) | `useVoiceInput`, `usePdfEngine.*`, `useTtsEngine.*`, `useWorkspaceRestore`, `WorkspaceContext`, `WorkspaceLink`, `resolvePath`, `ReadSelectionButton`, `WelcomeScreen`, `useKeyboardShortcuts`, `useMenuDismiss` |
| Accounts | `test_kc_admin.py`, `test_profile.py`, `test_auth_touch.py`, `test_admin_lifecycle.py` | `ProfileMenu`, `ProfilePanel`, `useViewModeGuard`, `useAuth.*`, `usePersistedState`, `useDeploymentDefaults`, `db.*` |
| Platform and evals | `test_conftest_smoke.py`, `test_conn_commits_before_response.py`, `test_db_pool_options.py`, `test_logging_config.py`, `test_platform_compat.py`, `test_container_*`, `test_startup_script.py`, `test_eval_*`, `test_migration_*` | `smoke`, `Header.settings`, `ViewSwitcher`, `Sidebar.sections`, `MenuOpeners.shortcuts`, `userAgent`, `apiErrors` |

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
- **Usage figures have a UI now** (admin console v2 phase 3); per-user budgets
  are set under People. What no test or walk covers: the **Estimated** column
  with real provider-estimated rows, and more than one person in **By person**.
- **User deletion doesn't exist** (API or UI) — also admin-console scope;
  today's remedy for orphans is documented in IDENTITY_AND_ROLES.md.
- Frontend has **no E2E** (Playwright etc.) — all UI tests are jsdom-level;
  the manual ledger above is the substitute for now.
