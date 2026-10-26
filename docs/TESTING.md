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
  - [Admin console v2, phase 1](#admin-console-v2-phase-1)
  - [Admin console v2, phase 2a](#admin-console-v2-phase-2a)
  - [Admin console v2, phase 3 (usage by model)](#admin-console-v2-phase-3-usage-by-model)
  - [Frontend: auth UI](#frontend-auth-ui)
  - [Frontend: chat streaming](#frontend-chat-streaming)
  - [Frontend: budget UX](#frontend-budget-ux)
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
.venv/bin/python -m pytest server/tests/ -q          # 2447 passed, 16 skipped, 2 deselected @ 2026-10-26 (phase 3)

# Frontend (vitest, jsdom — no services needed)
npx vitest run                                       # 1190 passed (119 files) @ 2026-10-26 (phase 3)

# Lint
npx eslint src                                       # clean (npm run lint is the same)

# Opt-in, slow: build and run the real backend image and the compose Keycloak
# entrypoint (deselected by default via pytest.ini addopts)
.venv/bin/python -m pytest -m container -q
```

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
- **Usage figures have a UI now** (admin console v2 phase 3); per-user budgets
  are set under People. What no test or walk covers: the **Estimated** column
  with real provider-estimated rows, and more than one person in **By person**.
- **User deletion doesn't exist** (API or UI) — also admin-console scope;
  today's remedy for orphans is documented in IDENTITY_AND_ROLES.md.
- Frontend has **no E2E** (Playwright etc.) — all UI tests are jsdom-level;
  the manual ledger above is the substitute for now.
