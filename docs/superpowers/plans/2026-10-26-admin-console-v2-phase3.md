# Admin console v2, phase 3 (Usage by model) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin can see, in the console, how many tokens each person used on each model, chat versus reader, cloud versus local, over 1-90 days. Today `inference_usage` has one row per person per day with no model, so this is impossible (spec §1 "Model usage"; owner request 2026-10-26: "model wise statistics per user").

**Architecture:**
- **One migration (023).** It adds `model`, `kind` and `estimated_tokens` to `inference_usage`, and moves the primary key to `(user_id, day, model, kind)`.
- **Every write names its model and kind.** The writers are `orchestrator._record_usage` (chat steps, a step cut short, reader batches). Each one names the model (canonical `provider:name`) and the kind (`chat` | `reader`).
- **The budget check still sums a person's whole day.**
- **One admin route groups the rows.** `GET /v1/admin/inference/usage` gains `group`, `user_id` and `model`. It tags each model local, cloud or unknown at read time, from its provider's flag (`model_settings.is_local`).
- **The Usage section is rebuilt.** It has three views: By model, By person (each person expands to their per-model rows) and By day.

**Tech Stack:** FastAPI, psycopg3 async, pytest (scratch Postgres), React 19, vitest, Tailwind 4.

**Spec:** `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`: §1 (Model usage row), §4.2.1 (the provider's local/cloud flag), §4.4 Usage, §4.5 (days in the deployment's timezone), §6 item 3. It builds on phase 2a (`docs/superpowers/plans/2026-10-26-admin-console-v2-phase2a.md`), which built `model_settings.is_local` and the `provider_locality` setting.

**Branch:** `feat/admin-console-v2-phase3`, created from the head of `feat/admin-console-v2-phase2a` after its final fix is reviewed. If phase 2a is merged first, it is created from `development`.

## Global Constraints

- **Open source, many deployers.** Nothing deployment-specific in code or docs; docs use example.com.
- **Admin routes require the admin capability server-side** (`deps.require_admin`); the UI only hides.
- **Per request, never cached per process** (`WORKERS > 1`). The models settings for the tag are read per request (`model_settings.load_models_settings(conn, os.environ)`).
- **Usage accounting stays fail-open.** A failed usage write logs and never breaks a turn (`_record_usage`'s existing try/except).
- **Days come from `inference_budget.today(conn)`,** the deployment's timezone. Never `CURRENT_DATE`.
- **Accessibility as in phases 1-2a:**
  - labelled controls;
  - `aria-pressed` on view toggles and `aria-expanded` on disclosure buttons;
  - 44 px targets below `md` (`min-h-11 md:min-h-0`);
  - tables inside an `overflow-x-auto` wrapper, so the page never scrolls sideways at 375 px.
- **Constants, verbatim:**
  - migration file `server/sql/023_usage_by_model.sql`;
  - kinds `chat`, `reader`, and `''` for rows written before the migration;
  - `group` values `user_day` (default, today's shape), `model`, `user`, `day`;
  - tags `local`, `cloud`, `unknown`;
  - days clamp `1..90` (existing).
- **Test DB:** before any pytest, export `TEST_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` for the scratch Postgres on 5434. Bare pytest hits the live database on 5433.

## Decisions

- **U1. Old rows get model `''` and kind `''`,** both shown as "Unknown". The spec names only `chat` and `reader`. But pre-migration rows mix both kinds, so labelling them `chat` would misreport them. The CHECK allows `('', 'chat', 'reader')`. After the backfill, the column defaults are dropped, so a writer that forgets a column fails loudly instead of writing `''`.
- **U2. The model is stored canonical (`provider:name`).**
  - A chat step stores `req.model_id`, which is already canonical.
  - A reader batch stores `BatchCall.model`. `_turn_reader` sets the config's model to the resolved key (`f"{provider.config.name}:{name}"`) when the router can place it, so `gemma4:e4b` and `ollama:gemma4:e4b` land in one row.
  - A reader the router can't place keeps its name.
- **U3. The tag is computed at read time, not stored.**
  - `''` → `unknown`.
  - A provider prefix not in `llm.providers` (the provider was removed) → `unknown`.
  - Otherwise `local` if `is_local(provider.config, name, ms.provider_locality)`, else `cloud`.
  - Changing a provider's mark therefore re-tags history. That is the spec's "tagged by its provider's flag".
- **U4. The estimated share is `estimated_tokens / tokens`.** `estimated_tokens` adds prompt plus eval tokens of every write whose `Usage.estimated` is true. The UI shows a whole percent, `—` when 0, and `<1%` for a share above 0 that rounds to 0.
- **U5. `group` absent means `user_day`.** That returns today's list shape, summed over model and kind, so scripts reading the old route keep working.

## Review Focus

1. **Migrating mid-day must not change anyone's remaining budget.** Pre-migration rows survive with model and kind `''`, and `spent_today` sums them with new rows. Test in Task 1.
2. **The same reader model under two spellings must land in one row.** A bare name and the canonical key are the same model. Test in Task 2.
3. **Two upserts on one key must both count** (`requests` 2, tokens summed). This catches an `ON CONFLICT` target left on the old key. Test in Task 1.
4. **A model whose provider was removed** must show tag `unknown`, not a 500. Test in Task 3.
5. **A malformed `user_id` or an unknown `group`** must return 422, not 500. Test in Task 3.

---

### Task 1: Migration 023 and the usage data layer

**Files:**
- Create: `server/sql/023_usage_by_model.sql`
- Modify: `server/services/inference_budget.py` (`record_usage`, `spent_today`, `admin_usage`)
- Test: `server/tests/test_migration_023.py` (pattern: `test_migration_021.py`), `server/tests/test_inference_budget.py`

**Interfaces:**
- Produces:
  - `record_usage(conn, user_id: str, prompt_tokens: int, eval_tokens: int, *, model: str, kind: str, estimated: bool) -> None`. The keyword arguments are required. `kind` is `"chat"` or `"reader"`; anything else raises `ValueError`.
  - `spent_today(conn, user_id) -> tuple[int, int]`, which sums every row of the day.
  - `admin_usage(conn, days: int = 7) -> list[dict]`, unchanged shape (U5): one dict per user per day, summed over model and kind.

- [ ] **Step 1: Write the failing migration test.** Create a scratch DB and apply migrations up to 022. Insert two `inference_usage` rows for one user (today and yesterday), then apply 023. Assert:
  - both rows exist with `model = ''`, `kind = ''`, `estimated_tokens = 0`;
  - the primary key columns are `(user_id, day, model, kind)` (from `pg_index`/`pg_attribute`);
  - inserting `kind='other'` raises `CheckViolation`;
  - an INSERT naming no `model` raises `NotNullViolation`, because the defaults were dropped.
- [ ] **Step 2: Write the failing budget tests** in `test_inference_budget.py`:
  - `test_record_usage_splits_by_model_and_kind`: chat on `ollama:a` and reader on `ollama:b` give 2 rows.
  - `test_record_usage_same_key_accumulates`: two calls on one key give `requests == 2` and summed tokens (Review Focus 3).
  - `test_record_usage_estimated_tokens`: `estimated=True` adds prompt+eval to `estimated_tokens`, `False` adds 0.
  - `test_record_usage_rejects_unknown_kind`.
  - `test_spent_today_sums_all_rows`: a pre-migration-style row (`''`/`''`) plus two new rows (Review Focus 1).
  - `test_admin_usage_default_shape_sums_models`: one dict per user per day, with the existing keys.
- [ ] **Step 3: Run them.** `pytest server/tests/test_migration_023.py server/tests/test_inference_budget.py -v` should FAIL.
- [ ] **Step 4: Write the migration.**
  1. Add the three columns with defaults `''`, `''`, `0`.
  2. Add the CHECK `kind IN ('', 'chat', 'reader')`.
  3. Drop `inference_usage_pkey` and add `PRIMARY KEY (user_id, day, model, kind)`.
  4. Drop the defaults on `model` and `kind`.
  5. Add `CREATE INDEX inference_usage_day_idx ON inference_usage (day)`, since the admin queries filter by day.
- [ ] **Step 5: Implement the three functions.**
  - The upsert's `ON CONFLICT (user_id, day, model, kind)` also adds `estimated_tokens`.
  - `spent_today` uses `COALESCE(SUM(...), 0)`.
  - `admin_usage` groups by user and day.
  - Update the module docstring: rows are per user, day, model and kind.
- [ ] **Step 6: Run the tests.** The Step 3 command should PASS. Then `pytest server/tests -q -k "budget or usage or migration"` should be green. Existing callers break on the new keyword arguments until Task 2. Fix the tests that call `record_usage` directly by passing `model="ollama:test", kind="chat", estimated=False`.
- [ ] **Step 7: Commit.** `git add server/sql/023_usage_by_model.sql server/services/inference_budget.py server/tests/test_migration_023.py server/tests/test_inference_budget.py <any test files adjusted>` then `git commit -m "feat(usage): record usage per model and kind (migration 023)"`

### Task 2: Every usage write names its model and kind

**Files:**
- Modify: `server/chat/orchestrator.py` (`_record_usage`, its two chat call sites at the end of a step and in `_finalize`, `_charge_reader`, `_turn_reader`)
- Test: `server/tests/test_chat_orchestrator.py`

**Interfaces:**
- Consumes: `record_usage(..., *, model, kind, estimated)` from Task 1.
- Produces: `_record_usage(user_id, usage, messages, output, *, model: str, kind: str) -> Usage`.
  - A chat step passes `model=req.model_id, kind="chat"`.
  - `_charge_reader` passes `model=call.model, kind="reader"`.
  - `estimated` comes from the `Usage` it records (including the chars/4 fallback).

- [ ] **Step 1: Write the failing tests** in `test_chat_orchestrator.py`, following the file's existing usage tests and patching `inference_budget.record_usage` or reading the scratch table:
  - `test_chat_step_records_model_and_kind`: a one-step turn on `ollama:m` gives a row with `model='ollama:m'`, `kind='chat'`.
  - `test_cut_short_step_records_model_and_kind`: the `_finalize` path for an aborted turn records `kind='chat'` with the turn's model.
  - `test_reader_batch_records_reader_kind`: an `extract_from_documents` call with the reader set to a different model gives a `kind='reader'` row under that model's canonical id.
  - `test_reader_bare_name_lands_canonical`: models setting `reader='m2'`, with the router placing it on provider `ollama`, records `model='ollama:m2'` (Review Focus 2).
  - `test_estimated_usage_marks_estimated_tokens`: a provider sending no usage records `estimated_tokens > 0`.
- [ ] **Step 2: Run them.** `pytest server/tests/test_chat_orchestrator.py -v -k "records or reader_bare or estimated_usage"` should FAIL.
- [ ] **Step 3: Implement.** Thread `model` and `kind` through `_record_usage`. In `_turn_reader`, return `replace(cfg, model=key, ...)` on each path where `router.resolve` succeeded (U2). Update the `_charge_reader` docstring and drop its "the usage table has no model column" sentence. The log line stays.
- [ ] **Step 4: Run the tests.** The Step 2 command should PASS. Then the full backend suite (with the 5434 exports) should be green.
- [ ] **Step 5: Commit.** `git add server/chat/orchestrator.py server/tests/test_chat_orchestrator.py` then `git commit -m "feat(usage): chat and reader writes name their model and kind"`

### Task 3: Admin usage route groups by model, person and day

**Files:**
- Modify: `server/services/inference_budget.py` (new `admin_usage_grouped`), `server/routers/admin.py` (`GET /inference/usage`)
- Test: `server/tests/test_admin_router.py`

**Interfaces:**
- Consumes: the Task 1 table; `model_settings.load_models_settings`, `model_settings.is_local`; the router from `get_router()` (`.providers`).
- Produces: `GET /v1/admin/inference/usage?days=7&group=user_day|model|user|day&user_id=<uuid>&model=<id>`.
  - Every grouped row carries `prompt_tokens`, `eval_tokens`, `tokens`, `requests`, `chat_tokens`, `reader_tokens` and `estimated_tokens`.
  - `group=model` adds `model` and `tag`, sorted by `tokens` descending, then `model`.
  - `group=user` adds `user_id` and `email`, sorted by `tokens` descending, then `email`.
  - `group=day` adds `day` (YYYY-MM-DD), sorted by day descending.
  - `user_id` and `model` filter before grouping, and combine with any group. `model=` with an empty value matches the Unknown rows.
  - `group=user_day` (or `group` absent) returns `admin_usage`'s shape (U5).
  - `admin_usage_grouped(conn, *, days: int, group: str, user_id: str | None, model: str | None) -> list[dict]` does the SQL. The route adds `tag`.

- [ ] **Step 1: Write the failing tests:**
  - `test_usage_group_model_tags`: rows on a local provider, a cloud-marked provider and `''` give tags `local`, `cloud`, `unknown`. The provider mark comes from the `models` section's `provider_locality`.
  - `test_usage_group_model_removed_provider_unknown`: a row under `gone:m` gives `tag == 'unknown'` and 200 (Review Focus 4).
  - `test_usage_group_user_with_model_filter`: two users and two models, `group=user&model=ollama:a`, give only that model's tokens.
  - `test_usage_user_filter_group_model`: one person's per-model rows.
  - `test_usage_group_day`.
  - `test_usage_chat_reader_split`: chat_tokens and reader_tokens add up to tokens; `''`-kind tokens count in `tokens` only.
  - `test_usage_bad_group_422`, `test_usage_bad_user_id_422` (Review Focus 5).
  - `test_usage_default_shape_unchanged`.
  - `test_usage_requires_admin` (a member gets 403).
- [ ] **Step 2: Run them.** `pytest server/tests/test_admin_router.py -v -k usage` should FAIL.
- [ ] **Step 3: Implement.**
  - The route takes `group: Literal["user_day", "model", "user", "day"] = "user_day"` and `user_id: uuid.UUID | None`, so FastAPI returns the 422s.
  - Tag with `model.partition(":")`: a prefix in `router.providers` gives `is_local(...)`, anything else gives `unknown`.
  - `chat_tokens` is `SUM(...) FILTER (WHERE kind='chat')`, and the same for reader.
  - All filters are parameterised.
- [ ] **Step 4: Run the tests.** The Step 2 command should PASS, and the full backend suite should be green.
- [ ] **Step 5: Commit.** `git add server/services/inference_budget.py server/routers/admin.py server/tests/test_admin_router.py` then `git commit -m "feat(admin): usage grouped by model, person and day, tagged local or cloud"`

### Task 4: The Usage section, rebuilt

**Files:**
- Modify: `src/components/admin/UsageSection.jsx`
- Create: `src/components/admin/UsageSection.test.jsx`
- Modify: `src/components/admin/phoneTargets.test.jsx` (include Usage)

**Interfaces:**
- Consumes: the Task 3 route.

The section, top to bottom:
1. **Header row.** "Inference usage" and the existing range select (`DAY_OPTIONS` unchanged).
2. **View toggle.** Three buttons with `aria-pressed`: **By model** (default), **By person**, **By day**.
3. **Model filter.** On By person and By day only: a select labelled "Model", with "All models" plus every model from the By model data. Unknown shows as "Unknown".
4. **Table,** inside `overflow-x-auto`:
   - By model: Model · Where (Local / Cloud / Unknown) · Chat · Reader · Requests · Total · Estimated.
   - By person: Person (email) · Chat · Reader · Requests · Total · Estimated · a disclosure button per row, labelled "Models for <email>", with `aria-expanded`. Opening it fetches `group=model&user_id=<id>` once and renders that person's per-model rows indented beneath, with the By model columns.
   - By day: Day · Chat · Reader · Requests · Total · Estimated.
   - Numbers use `toLocaleString()`. Estimated follows U4. The day renders as given, never `new Date(day)`.
5. **Notes,** shown when there are rows:
   - the existing timezone line;
   - "Usage recorded before this version has no model and shows as Unknown." (only when an Unknown row is present);
   - "Estimated: counted from text length because the provider sent no token counts."

Empty: "No usage recorded yet." A load failure keeps the existing toast copy.

- [ ] **Step 1: Write the failing tests** (mock `apiFetch`, as the other admin section tests do):
  - The default view requests `group=model` and renders tags.
  - The By person toggle requests `group=user`, and `aria-pressed` follows the toggle.
  - Expanding a person requests `group=model&user_id=…` once, renders their rows, and sets `aria-expanded`.
  - Picking a model in the filter adds `&model=` (URL-encoded).
  - The Unknown note appears only with a `''` model.
  - Estimated renders `12%`, `<1%` and `—`.
  - Changing the range refetches the current view.
- [ ] **Step 2: Run them.** `npx vitest run src/components/admin/UsageSection.test.jsx` should FAIL.
- [ ] **Step 3: Implement.** Match `ModelsSection.jsx` and `PeopleSection.jsx` for classes and for the sequence guard against out-of-order responses.
- [ ] **Step 4: Run the tests.** `npx vitest run src/components/admin` should PASS, and `npm test` plus `npx eslint .` should be clean.
- [ ] **Step 5: Commit.** `git add src/components/admin/UsageSection.jsx src/components/admin/UsageSection.test.jsx src/components/admin/phoneTargets.test.jsx` then `git commit -m "feat(admin): Usage by model, person and day"`

### Task 5: Docs

**Files:** `docs/USER_GUIDE.md` (admin console → Usage), `docs/ARCHITECTURE.md` (`inference_budget`, migration list), `docs/TESTING.md` (new tests, refreshed counts), `CHANGELOG.md` (Unreleased), `HANDOVER.md` (top entry).

- [ ] **Step 1:** Describe what an admin can do in the UI, naming each control:
  - Usage → By model / By person (expand a person) / By day;
  - the Model filter;
  - the range select.
  
  State the limits plainly:
  - usage before the upgrade shows as Unknown and can't be split;
  - the tag follows the provider's current Local/Cloud mark;
  - the Estimated meaning;
  - the route's `group`, `user_id` and `model` parameters (in ARCHITECTURE), including that no `group` keeps the old shape.
- [ ] **Step 2:** `grep -n "no model column\|per user per day" docs README.md CHANGELOG.md server --include=*.md -r` returns nothing stale.
- [ ] **Step 3: Commit** the listed files: `docs: usage by model`.

### Task 6: Walk it in the app

Run on a scratch stack, never the live one:
- backend on 8100, started with `env -i`;
- a scratch DB on 5434;
- Vite on a spare port;
- auth off.

Seed usage by holding two short chat turns on two models, plus one document question that triggers the reader. Then, as the admin:
1. Open Usage. By model shows both models with the right tags, and a Reader figure for the reader model.
2. Open By person, and expand the person. Their per-model rows match.
3. Pick a model in the Model filter. By person and By day narrow to it.
4. Switch the range.
5. At 375 px, the page does not scroll sideways because of the Usage tables, and every control is at least 44 px.

Then drop the DB, stop the processes and unload any model loaded for the walk. Write `task-6-walk-report.md`, naming the control used for each step.
