# Admin Console v2, Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The admin console gets the Settings window's shape (a section list, one section at a time), an Overview that says what is wrong, a deployment timezone that decides budget days, and Docling limits that `POST /v1/docs/{id}/convert` enforces and the convert dialog shows.

**Architecture:**
- One storage module, `server/services/console_settings.py`, implements the spec's row pattern for every new setting: an `app_settings` row per section, read with one primary-key read per request, precedence console → env → default, narrowing for limits, a reset per setting, an audit line per change. Two sections use it in phase 1: `deployment` (timezone) and `documents` (Docling limits).
- Two generic admin routes (`GET`/`PUT /v1/admin/settings/{section}`) serve every section; `GET /v1/admin/overview` runs the health checks concurrently, each under its own timeout.
- The frontend wraps the existing sections in a `ConsoleShell` (tablist on desktop, a picker on a phone, an unsaved-changes guard) and builds the new sections on one hook (`useSectionSettings`) and one row component (`SettingRow`).

**Tech Stack:** Python 3.13, FastAPI, psycopg 3, `zoneinfo` (+ the `tzdata` package), pypdfium2, httpx; React 19, Vite, vitest + Testing Library, Tailwind 4.

**Spec:** `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md` (revision 2, §7 approved 2026-10-25). Read it first; § numbers below refer to it. This plan covers §6 phase 1 only.

## Global Constraints

- **Precedence (§3):** "console row, then env var, then built-in default. For allow-lists, the console's list is intersected with the env's, never unioned." "The console can only narrow what the environment allows."
- **Per request, never cached per process (§5):** settings are read on every request (one primary-key read of `app_settings`). The only process-level cache allowed is the list of zone names the server's zone database knows, which can't change while the process runs.
- **Admin routes (§5):** every `/v1/admin/*` route depends on `deps.require_admin`; the UI only hides.
- **Connections:** every route that takes a connection uses `Depends(deps.get_conn, scope="function")` (`server/tests/test_conn_commits_before_response.py` fails otherwise).
- **Refusals** use `server/http_errors.refusal(status, error, message, **extra)`, so `src/lib/apiErrors.js` passes `message` through.
- **Audit (§3):** every save writes one `audit()` line per changed setting: who, key, before, after. Never a secret.
- **Open source:** no deployment-specific value in code or docs; docs use `example.com`. Every new setting has a safe default and an env fallback (§5).
- **No migration in phase 1.** `app_settings` exists (`016_app_settings.sql`); new sections are new rows.
- **Copy (exact):** source lines `Set here.` / `From the server setting VAR.` (VAR in code font) / `The default.`; the reset button `Use the server setting`; the dialog's note `Turned off by your admin`; section labels `Overview`, `People`, `Projects`, `Documents`, `Chat`, `Defaults for users`, `Usage`, `Deployment`.
- **Accessibility (§5):** tablist semantics, arrow keys, focus return, Escape, 44 px targets below the `md` breakpoint (768 px, the Settings window's).
- **Databases:** tests run on the scratch container `nr-c21-testpg` (`podman start nr-c21-testpg`), never on `127.0.0.1:5433` (`natural_reader`, the owner's real data). `$PYTEST` below is shorthand for this full command; shell variables don't persist between tool calls, so spell it out every time:
  `TEST_ADMIN_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/postgres TEST_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_test .venv/bin/python -m pytest -q`
- **Gates:** backend `$PYTEST server/tests`; frontend `npx vitest run`; lint `npx eslint src`.
- **Commits:** a **Commit** step runs only with the owner's explicit permission for that commit. Stage explicit paths.
- **No eval needed for phase 1:** nothing here changes what a model is sent, except the "Today is …" line when the browser sends no usable timezone, and the SPA always sends one (`useChatEngine.js:22`).

## Review Focus

1. **An Overview check that hangs** (a provider or SearXNG that accepts the connection and then goes silent). The section must still answer within about 5 seconds, with that one line saying it didn't answer and every other line filled in. Pinned in Task 5.
2. **Two admins saving different settings of one section** (or a newer version's key in the row). Saving one setting must leave every other stored key exactly as it was, including keys this code doesn't know. Pinned in Task 1.
3. **The day boundary in a zone with daylight saving, and a zone ahead of UTC.** The budget resets at the next *local* midnight, also on the night the clocks change, and a request at 20:00 UTC in Colombo counts toward the next day. Pinned in Task 2.
4. **A PDF whose page count isn't known yet** (just uploaded, its indexing still running, `page_count` NULL) under a page limit. The server counts the stored file's pages; it neither lets a 600-page PDF through nor refuses a 3-page one. Pinned in Task 4.
5. **An env value narrower than the console's choice** (`DOCLING_PRESETS=fast,standard` while the console chose `accurate`). Accurate is refused at convert, and shown off, with the env as the reason, in the console and the dialog. Pinned in Task 4 (server), Task 11 (console) and Task 12 (dialog).

## Rulings made while planning (deviations from the spec's wording, recorded for review)

1. **No Models section in phase 1.** It arrives with phase 2. Overview's provider, reader and embedding lines link to **Deployment**, whose read-only list shows those env values. The old "Deployment config" block's content moves into that list.
2. **No "use a local reader" hint yet (§4.1).** Its remedy, choosing the reader in the console, ships in 2a. A hint pointing at a control that doesn't exist would be a dead end, so 2a adds the hint with the local/cloud flag.
3. **Context windows in phase 1 have four sources:** Ollama's `/api/ps` (a loaded model); an Ollama cloud model's maximum (`-cloud`, verified in §1); the provider's `/models` (openai kind); else unknown. 2a adds the deployment context size and declared windows. A local Ollama model that isn't loaded is **unknown**, never its `/api/show` maximum: that maximum is the number the app wrongly trims to today (§1).
4. **"A typical project-chat turn"** is: a sample project chat's rules and tool specs (rendered, chars/4), plus `CHAT_PROJECT_MAP_CHARS`/4, `CHAT_TOOL_RESULT_BUDGET_CHARS`/4 and `CHAT_REPLY_RESERVE_TOKENS`. The map's cap stands in for "the largest project's map": it's an upper bound, it needs no query, and it reads no one's project.
5. **Rebuild progress on Overview is a count in phase 1:** documents whose embedding profile isn't current, each rebuilt on first use (today's behaviour). The database queue and its progress are 2b.
6. **Docling limits apply to admins too.** "Admins only" decides only who may start a conversion. An admin who needs a turned-off option turns it back on in Documents.
7. **Env fallbacks for the Docling limits (§5),** each the outer bound the console narrows: `DOCLING_CONVERT_BY` (`readers`|`admins`, default `readers`), `DOCLING_PRESETS` (default `fast,standard,accurate`), `DOCLING_ALLOW_OCR` (default `true`), `DOCLING_ALLOW_PICTURE_DESCRIPTION` (default `true`), `DOCLING_MAX_PAGES` (default `0` = no limit).
8. **Timezone:** env fallback `TZ` (already in `.env.example`, for logging). `tzdata` joins `requirements.txt`, because Windows bare-metal installs have no system zone database and `zoneinfo` then knows no zone at all.
9. **The moved sections keep their own save behaviour** (People, Projects, Chat options, Assistant, Defaults, Usage: §6 "moved in unchanged"). One Save per section applies to the new ones, Documents and Deployment.
10. **The unsaved-changes prompt is inline** (no `window.confirm`) and covers switching section and **Back to reader**. Reloading or closing the tab isn't guarded.
11. **"The times shown in the console" (§4.5)** is one value today: a person's created date under People. It's shown in the deployment zone. Usage days are stored in the zone at write time, so the Usage header loses "(UTC)" and gains a one-line note.
12. **`GET /v1/admin/inference/config` stays.** The console stops calling it in Task 10; phase 2 decides whether Models reuses or retires it.

## File structure

**Backend, new**

| File | Responsibility |
|---|---|
| `server/services/console_settings.py` | The row pattern's storage: `Setting`, `Section`, `Value`, `load`, `save`, `view` |
| `server/services/deployment_settings.py` | The `deployment` section (timezone) and `deployment_zone(conn)` |
| `server/services/deployment_info.py` | The read-only list of env-only values, secrets as set/not set |
| `server/services/docling_limits.py` | The `documents` section, `ConvertLimits`, the convert check, the user view |
| `server/services/console_overview.py` | Overview's checks and `run_checks` |

**Backend, modified:** `server/services/inference_budget.py` (days in the zone), `server/chat/context.py` + `server/chat/orchestrator.py` (the "Today is …" fallback), `server/chat/reader.py` (`reader_model_source`), `server/services/extract.py` (`pdf_page_count`), `server/services/model_router.py` (`offered_chat_models`), `server/routers/inference.py` (uses it), `server/llm/providers/ollama.py` (`loaded_context_lengths`), `server/routers/admin.py` (three routes), `server/routers/settings.py` (`GET /v1/settings/docling`), `server/routers/docs.py` (enforcement, `page_range` validation), `requirements.txt`.

**Frontend, new:** `src/components/admin/ConsoleShell.jsx`, `PeopleSection.jsx`, `UsageSection.jsx`, `SettingRow.jsx`, `OverviewSection.jsx`, `DeploymentSection.jsx`, `DocumentsSection.jsx`; `src/hooks/useSectionSettings.js`, `src/hooks/useConvertLimits.js`; `src/lib/zoneFormat.js`. Each with a test file beside it.

**Frontend, modified:** `src/components/admin/AdminConsole.jsx` (hosts the shell), its two test files, `src/components/DoclingConvertDialog.jsx`, `src/App.jsx`.

---

### Task 1: The settings store (`console_settings`)

**Files:**
- Create: `server/services/console_settings.py`
- Test: `server/tests/test_console_settings.py`

**Interfaces:**
- Produces (used by Tasks 2, 3, 4):
  - `class SettingError(ValueError)` with attribute `name: str`: a refused change; `str(e)` is a message written for an admin.
  - `@dataclass(frozen=True) class Setting`: `name: str`; `env: str | None`; `default: Any`; `parse: Callable[[Any, Any], Any]` (console value, fallback value → value to store, or `ValueError(message)`); `parse_env: Callable[[str], Any]` (env text → value, or `ValueError`); `narrow: Callable[[Any, Any], Any] | None = None` (console value, fallback value → what applies; None: the console value applies as is).
  - `@dataclass(frozen=True) class Section`: `key: str` (the `app_settings.key`); `settings: tuple[Setting, ...]`; `def get(self, name: str) -> Setting` (KeyError when unknown).
  - `@dataclass(frozen=True) class Value`: `value: Any`, `source: str` (`"admin"|"env"|"default"`), `fallback: Any`, `fallback_source: str` (`"env"|"default"`). The fallback is what applies without the console's value, and the bound a narrowing setting is narrowed to.
  - `async def load(conn, section: Section, env: Mapping[str, str] = os.environ) -> dict[str, Value]`
  - `async def save(conn, section: Section, changes: Mapping[str, Any], *, updated_by: str, env: Mapping[str, str] = os.environ) -> dict[str, Value]`: `None` for a name removes it (use the server setting).
  - `def view(section: Section, values: dict[str, Value]) -> dict[str, dict]`: JSON for the console, `{name: {"value", "source", "env", "fallback", "fallback_source"}}`, tuples as lists.

- [ ] **Step 1: Write the failing tests**

Use a toy section defined in the test (`limit`: int, env `TOY_LIMIT`, default 10, narrowed to the lower of the two with 0 = none; `zone`: str, env `TOY_ZONE`, default `"UTC"`, no narrowing). Follow `test_chat_options.py`'s fixtures (`db_conn`, `resolve_or_provision_user` for `updated_by`).

```python
async def test_admin_value_beats_env_beats_default(db_conn):
    assert (await load(db_conn, TOY, {}))["zone"] == Value("UTC", "default", "UTC", "default")
    assert (await load(db_conn, TOY, {"TOY_ZONE": "Asia/Colombo"}))["zone"].source == "env"
    await save(db_conn, TOY, {"zone": "Europe/Berlin"}, updated_by=by)
    got = (await load(db_conn, TOY, {"TOY_ZONE": "Asia/Colombo"}))["zone"]
    assert got == Value("Europe/Berlin", "admin", "Asia/Colombo", "env")

async def test_narrow_keeps_the_console_value_inside_the_env_bound(db_conn):
    await save(db_conn, TOY, {"limit": 100}, updated_by=by)
    assert (await load(db_conn, TOY, {"TOY_LIMIT": "50"}))["limit"].value == 50
    assert (await load(db_conn, TOY, {}))["limit"].value == 10   # the default is the bound too

async def test_save_keeps_stored_settings_it_never_loaded(db_conn):   # Review Focus 2
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('toy', '{\"future\": 1, \"zone\": \"UTC\"}')")
    await save(db_conn, TOY, {"limit": 5}, updated_by=by)
    cur = await db_conn.execute("SELECT value FROM app_settings WHERE key = 'toy'")
    assert (await cur.fetchone())[0] == {"future": 1, "zone": "UTC", "limit": 5}
```

Also, each with these assertions:
- `test_invalid_env_value_is_warned_once_and_ignored`: `TOY_LIMIT=abc` loaded twice → value 10, source `default`, exactly one WARNING naming `TOY_LIMIT`.
- `test_an_unusable_stored_value_is_ignored`: a row `{"limit": "lots"}` → env/default applies, one WARNING naming the section and the setting.
- `test_reset_removes_only_that_setting_and_the_last_reset_deletes_the_row`: save `limit` and `zone`, reset `limit` → row is `{"zone": …}`; reset `zone` → no row.
- `test_save_refuses_an_unknown_name_or_an_invalid_value_and_writes_nothing`: `{"limit": 5, "nope": 1}` → `SettingError` with `.name == "nope"`, no row; `{"limit": -1, "zone": "X"}` → `SettingError` with `.name == "limit"`, no row.
- `test_save_writes_one_audit_line_per_changed_setting`: saving `{"limit": 5}` logs `settings.toy.limit by=<id> old=10 new=5`; a reset logs `new=None`; an unchanged value logs nothing.
- `test_view_shape`: `view(TOY, values)["zone"] == {"value": "UTC", "source": "default", "env": "TOY_ZONE", "fallback": "UTC", "fallback_source": "default"}`.

- [ ] **Step 2: Run them to verify they fail**

Run: `$PYTEST server/tests/test_console_settings.py`
Expected: FAIL, `ModuleNotFoundError: server.services.console_settings`.

- [ ] **Step 3: Implement the module**

- Fallback: the env value when `env` is set, non-empty and `parse_env` accepts it, else the default. An invalid env value is warned about once per `(var, value)` (a module-level set, like `chat_options._warned`).
- A stored value applies only after `parse(stored, fallback)` accepts it, then `narrow` when set.
- `save` validates every change before writing anything. It writes with jsonb operators, so it never rewrites keys it didn't change: `INSERT … ON CONFLICT (key) DO UPDATE SET value = app_settings.value || EXCLUDED.value` for sets, `UPDATE … SET value = value - %s` for each reset, then `DELETE … WHERE key = %s AND value = '{}'::jsonb`. Set `updated_by` and `updated_at` as `chat_options.save_chat_options` does.
- Audit event names are `settings.<section>.<name>` through `server.audit.audit`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$PYTEST server/tests/test_console_settings.py`
Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add server/services/console_settings.py server/tests/test_console_settings.py
git commit -m "feat(admin): one settings store for the console's row pattern"
```

---

### Task 2: The deployment timezone decides budget days, usage days and the "Today" fallback

**Files:**
- Create: `server/services/deployment_settings.py`
- Modify: `server/services/inference_budget.py`, `server/chat/context.py:74-90` and `:229-245` (`today_line`, `TurnInput`), `server/chat/orchestrator.py:296-303` and beside `_chat_options` (`:752`), `requirements.txt`
- Test: `server/tests/test_deployment_settings.py` (new), `server/tests/test_inference_budget.py`, `server/tests/test_chat_context.py`, `server/tests/test_chat_orchestrator.py`

**Interfaces:**
- Consumes: Task 1's `Section`, `Setting`, `load`.
- Produces:
  - `deployment_settings.SECTION`: `Section("deployment", (Setting("timezone", env="TZ", default="UTC", …),))`.
  - `def zone_name(raw: Any) -> str`: strips spaces and one leading `:` (glibc's `TZ=:Area/City`), returns the name when the zone database knows it or it is `"UTC"`, else `ValueError("“{raw}” isn't a timezone this server knows. Use a name such as Europe/Berlin.")`. Known names: `zoneinfo.available_timezones() | {"UTC"}`, cached once per process.
  - `async def deployment_zone_name(conn, env=os.environ) -> str` and `async def deployment_zone(conn, env=os.environ) -> ZoneInfo`.
  - `inference_budget._now() -> datetime` (aware UTC; the seam tests patch), `async def today(conn) -> date`, `def next_reset_at(now: datetime, zone: ZoneInfo) -> datetime` (the next local midnight, as UTC). The existing public functions keep their signatures.
  - `context.today_line(now, tz_name, default_tz: str = "UTC") -> str`: the browser zone when usable, else `default_tz` when usable, else UTC. `TurnInput.default_timezone: str = "UTC"`.

- [ ] **Step 1: Write the failing tests**

```python
# test_inference_budget.py — Review Focus 3
async def test_usage_counts_toward_the_deployment_day(db_conn, monkeypatch):
    await _set_zone(db_conn, "Asia/Colombo")           # a deployment row, through console_settings.save
    monkeypatch.setattr(ib, "_now", lambda: datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc))
    await ib.record_usage(db_conn, U, 10, 5)
    cur = await db_conn.execute("SELECT day FROM inference_usage WHERE user_id = %s", (U,))
    assert (await cur.fetchone())[0] == date(2026, 10, 26)     # 01:30 on the 26th in Colombo
    assert await ib.spent_today(db_conn, U) == (10, 5)

def test_reset_is_the_next_local_midnight():
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    assert ib.next_reset_at(now, ZoneInfo("Asia/Colombo")) == datetime(2026, 10, 26, 18, 30, tzinfo=timezone.utc)

def test_reset_on_the_night_the_clocks_change():
    now = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)    # New York falls back at 06:00 UTC that day
    assert ib.next_reset_at(now, ZoneInfo("America/New_York")) == datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)
```

Also:
- `test_inference_budget.py::test_admin_usage_window_starts_on_the_deployment_day`: zone Colombo, `_now` 2026-10-25T20:00Z, rows on 2026-10-26 and 2026-10-25; `admin_usage(days=1)` returns only the 26th.
- The existing `test_reset_at_is_next_utc_midnight` passes unchanged (no row: UTC).
- `test_deployment_settings.py`: `zone_name` accepts `"Asia/Colombo"`, `" Europe/Berlin "`, `":Asia/Colombo"`, `"UTC"`; refuses `"Mars/Base"`, `"../etc/passwd"`, `""`, `5` with the message above. `deployment_zone_name` gives `"UTC"` with nothing set, `TZ`'s value with `{"TZ": "Asia/Colombo"}`, the console's over `TZ`, and `"UTC"` plus one warning for `{"TZ": "EST5EDT,M3.2.0"}`.
- `test_chat_context.py::test_today_line_falls_back_to_the_deployment_zone`: with `NOW` (19:24 UTC, 26 September), `today_line(NOW, None, "Asia/Colombo")` and `today_line(NOW, "Mars/Olympus", "Asia/Colombo")` both equal `"Today is Sunday, 27 September 2026 (Asia/Colombo)."`; `today_line(NOW, None, "Nope/Nope")` is the UTC line. The existing parametrized UTC test stays as is.
- `test_chat_orchestrator.py::test_a_turn_gets_the_deployment_zone_as_its_default`: with the `build_context` spy (pattern at `:542`) and `orchestrator._deployment_timezone` patched to return `"Asia/Colombo"`, the spy's `TurnInput.default_timezone == "Asia/Colombo"`. With `get_pool` raising inside `_deployment_timezone`, it is `"UTC"` (fail-open, like `_chat_options`).

- [ ] **Step 2: Run them to verify they fail**

Run: `$PYTEST server/tests/test_deployment_settings.py server/tests/test_inference_budget.py server/tests/test_chat_context.py server/tests/test_chat_orchestrator.py`
Expected: FAIL (module missing; `next_reset_at` and `default_tz` unknown).

- [ ] **Step 3: Implement**

- `inference_budget`: `today(conn)` is `_now().astimezone(await deployment_zone(conn)).date()`; `spent_today`, `record_usage`, `admin_usage` use it; `budget_state` uses `next_reset_at(_now(), zone)`, still formatted with a trailing `Z`. Rewrite the module docstring: days are the deployment's, computed in Python, and rows already written keep their days.
- `orchestrator._deployment_timezone() -> str`: one pool connection, `deployment_zone_name`, any exception logged and `"UTC"`. Pass `default_timezone=` to `TurnInput`; `build_context` calls `today_line(turn.now, turn.timezone, turn.default_timezone)`.
- `requirements.txt`: add `tzdata` under a comment saying why (Windows has no system zone database).

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: PASS. Then `$PYTEST server/tests` (the whole suite: the budget is used by chat and the gateway).

- [ ] **Step 5: Commit** (with permission)

```bash
git add server/services/deployment_settings.py server/services/inference_budget.py server/chat/context.py server/chat/orchestrator.py requirements.txt server/tests/test_deployment_settings.py server/tests/test_inference_budget.py server/tests/test_chat_context.py server/tests/test_chat_orchestrator.py
git commit -m "feat(admin): a deployment timezone for budget days, usage days and the Today line"
```

---

### Task 3: Admin settings routes and the read-only deployment list

**Files:**
- Create: `server/services/deployment_info.py`
- Modify: `server/routers/admin.py`, `server/chat/reader.py:132-145`
- Test: `server/tests/test_deployment_info.py`, `server/tests/test_admin_settings.py` (both new)

**Interfaces:**
- Consumes: Task 1 (`load`, `save`, `view`, `SettingError`), Task 2 (`deployment_settings.SECTION`).
- Produces:
  - `reader.reader_model_source(env) -> str | None`: the env var that decided the reader model (`READER_MODEL`, `SUMMARIZE_MODEL` or `WEB_SEARCH_SUMMARY_MODEL`, the order of `load_reader_config`), None for the built-in default.
  - `deployment_info.safe_url(url: str) -> str`: scheme, host, port and path; no user info, query or fragment.
  - `deployment_info.readonly_rows(env=os.environ) -> list[dict]`: rows `{"id", "label", "value", "env", "source"}` (`source` is `"env"` or `"default"`). In order: each provider (`provider:<name>`, value `"<kind> · <safe_url>"`, env `INFERENCE_<KEY>_URL`, or `OLLAMA_URL` without `INFERENCE_PROVIDERS`) and its key (`provider_key:<name>`, `"set"`/`"not set"`, only for `INFERENCE_PROVIDERS` entries); `allowed_models` (`INFERENCE_MODELS`, default "All models allowed"); `chat_timeout` (`INFERENCE_TIMEOUT_S`, "300 s"); `daily_budget` (`INFERENCE_DAILY_TOKEN_BUDGET`, "Unlimited"); `reader_model` ("The chat model of each conversation" for `chat`); `embedding_model` (`EMBEDDING_MODEL`); `embedding_url` (`OLLAMA_URL`); `storage` (`DOC_STORAGE_DIR`, `doc_storage.storage_dir()`); `docling` (`DOCLING_ENABLED`, "On"/"Off"); `searxng` (`SEARXNG_URL`, host only, "Not set up" when empty); `workers` (`WORKERS`, "1"); then `SESSION_SECRET`, `OIDC_CLIENT_SECRET`, `KC_ADMIN_CLIENT_SECRET` as `"set"`/`"not set"`.
  - Routes, both admin-only:
    - `GET /v1/admin/settings/{section}` → `{"settings": view(...), "info": rows}`.
    - `PUT /v1/admin/settings/{section}` with body `SectionChangesIn` (`extra="forbid"`, `changes: dict[str, Any]`) → the same shape.
    - Section registry in `admin.py`: `_SECTIONS: dict[str, tuple[Section, Callable[[], list[dict]]]] = {"deployment": (deployment_settings.SECTION, deployment_info.readonly_rows)}` (Task 4 adds `documents`).
  - Refusals: unknown section → `refusal(404, "unknown_section", "No such settings section.")`; a `SettingError` → `refusal(422, "invalid_setting", str(e), setting=e.name)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_secret_values_never_appear(monkeypatch):
    env = {"INFERENCE_PROVIDERS": "cloud", "INFERENCE_CLOUD_KIND": "openai",
           "INFERENCE_CLOUD_URL": "https://user:pw@llm.example.com:8443/v1?key=q",
           "INFERENCE_CLOUD_API_KEY": "sk-top", "SESSION_SECRET": "s" * 40}
    rows = readonly_rows(env)
    dumped = json.dumps(rows)
    assert "sk-top" not in dumped and "s" * 40 not in dumped and "pw" not in dumped and "key=q" not in dumped
    by_id = {r["id"]: r for r in rows}
    assert by_id["provider:cloud"]["value"] == "openai · https://llm.example.com:8443/v1"
    assert by_id["provider_key:cloud"]["value"] == "set"
    assert by_id["session_secret"]["value"] == "set" and by_id["kc_admin_client_secret"]["value"] == "not set"
```

Also:
- `test_deployment_info.py::test_sources_name_the_variable_or_the_default`: `{}` gives `reader_model` the value `llama3.2:3b` with source `default`; `{"SUMMARIZE_MODEL": "m"}` gives `m`, env `SUMMARIZE_MODEL`, source `env`; `READER_MODEL=chat` gives "The chat model of each conversation".
- `test_admin_settings.py` (fixtures as in `test_chat_options.py`):
  - `test_get_returns_settings_and_info`: `settings.timezone == {"value": "UTC", "source": "default", "env": "TZ", …}`; `info` is a list.
  - `test_put_sets_then_resets_the_timezone`: PUT `{"changes": {"timezone": "Asia/Colombo"}}` gives source `admin`; PUT `{"changes": {"timezone": null}}` gives `default`.
  - `test_an_invalid_zone_is_a_422_naming_the_setting_and_writes_nothing`: `detail.error == "invalid_setting"`, `detail.setting == "timezone"`, the message names the zone, no `deployment` row.
  - `test_unknown_section_is_404` and `test_unknown_body_field_is_422`.
  - `test_member_gets_403_on_get_and_put`.

- [ ] **Step 2: Run them to verify they fail**

Run: `$PYTEST server/tests/test_deployment_info.py server/tests/test_admin_settings.py`
Expected: FAIL (module missing; 404 on the routes).

- [ ] **Step 3: Implement** the two routes (`conn=Depends(deps.get_conn, scope="function")`, `principal: deps.Principal = Depends(deps.require_admin)`, `updated_by=str(principal.user_id)`), `reader_model_source`, and `deployment_info`.

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command, then `$PYTEST server/tests/test_conn_commits_before_response.py`. Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add server/services/deployment_info.py server/routers/admin.py server/chat/reader.py server/tests/test_deployment_info.py server/tests/test_admin_settings.py
git commit -m "feat(admin): settings routes per section, and the deployment's read-only values"
```

---

### Task 4: Docling limits, enforced at convert

**Files:**
- Create: `server/services/docling_limits.py`
- Modify: `server/routers/docs.py:81-88` (`ConvertOptionsIn`) and `:909-957` (`start_convert_job`), `server/services/extract.py`, `server/routers/settings.py`, `server/routers/admin.py` (`_SECTIONS`)
- Test: `server/tests/test_docling_limits.py` (new), `server/tests/test_convert_limits.py` (new, the `test_content_ops.py` harness)

**Interfaces:**
- Consumes: Task 1, Task 3's `_SECTIONS`.
- Produces:
  - `PRESETS = ("fast", "standard", "accurate")`; `SECTION = Section("documents", …)` with settings `convert_by` (`"readers"|"admins"`, env `DOCLING_CONVERT_BY`, narrowed to `admins` when either side says so), `presets` (a list, env `DOCLING_PRESETS` as CSV, narrowed by intersection in `PRESETS` order), `ocr` and `picture_description` (bools; env `DOCLING_ALLOW_OCR` / `DOCLING_ALLOW_PICTURE_DESCRIPTION` reading `true/false/1/0/yes/no`; narrowed by AND), `max_pages` (int 0–100000, 0 = no limit, env `DOCLING_MAX_PAGES`; narrowed to the lower nonzero limit).
  - Parse refusals (exact): presets empty → `"Keep at least one preset."`; presets that the fallback allows none of → `"None of these presets is allowed by the server setting DOCLING_PRESETS."`; a non-bool for a bool, or a bool or non-integer for `max_pages`, → `"Must be true or false."` / `"Must be a whole number from 0 to 100000."`.
  - `@dataclass(frozen=True) class ConvertLimits`: `convert_by: str`, `presets: tuple[str, ...]`, `ocr: bool`, `picture_description: bool`, `max_pages: int`.
  - `async def load_limits(conn, env=os.environ) -> ConvertLimits`
  - `@dataclass(frozen=True) class Refused`: `option: str`, `message: str`.
  - `def check(limits: ConvertLimits, options: dict, *, is_admin: bool, pages: int | None) -> Refused | None`: rules in this order, messages exact:
    - `convert_by`: "Only admins can convert documents on this server."
    - `preset`: "Your admin has turned off the {Fast|Standard|Accurate} preset."
    - `ocr`: "Your admin has turned off OCR for conversions."
    - `picture_description` (`images == "describe"`): "Your admin has turned off picture descriptions for conversions."
    - `max_pages` (`pages is not None and pages > max_pages > 0`): "This server converts at most {n} pages at a time. Choose a page range of {n} pages or fewer."
  - `def pages_requested(page_range: list[int] | None, total: int | None) -> int | None`: with a range, `min(end, total or end) - start + 1`; without one, `total`.
  - `def user_view(limits, *, is_admin: bool, enabled: bool) -> dict`: `{"enabled", "may_convert", "presets", "ocr", "picture_description", "max_pages"}`.
  - `def info_rows(env=os.environ) -> list[dict]`: rows shaped like Task 3's, ids `docling` ("On"/"Off", `DOCLING_ENABLED`), `pdf_upload_max` ("{n} MB", `PDF_UPLOAD_MAX_MB`, default 50), `text_upload_max` (`TEXT_UPLOAD_MAX_MB`, default 10).
  - `extract.pdf_page_count(path: Path) -> int` (pypdfium2, document closed after).
  - `GET /v1/settings/docling` (any signed-in user, `deps.get_current_user`) → `user_view`.
  - The convert refusal: `refusal(403, "convert_not_allowed", r.message, option=r.option)`.

- [ ] **Step 1: Write the failing tests**

`_narrowed(db_conn, console, env)` saves `console` with `console_settings.save(…, env=env)` and returns `{name: v.value}` from `load(db_conn, SECTION, env)`. `test_convert_limits.py` copies `store`, `harness`, `_member`, `_admin`, `_write` and `_doc` from `test_content_ops.py`; `_save_documents(db_conn, changes)` is `console_settings.save` with a provisioned user as `updated_by`.

```python
# test_docling_limits.py
async def test_the_console_narrows_and_never_widens_the_environment(db_conn):   # Review Focus 5
    vals = await _narrowed(db_conn, {"presets": ["accurate", "standard"]}, {"DOCLING_PRESETS": "fast,standard"})
    assert vals["presets"] == ["standard"]

@pytest.mark.parametrize("console, env, expected", [(0, 0, 0), (0, 50, 50), (100, 50, 50), (30, 50, 30), (30, 0, 30)])
def test_max_pages_takes_the_lower_limit(console, env, expected):
    assert SECTION.get("max_pages").narrow(console, env) == expected

def test_check_order_and_messages():
    limits = ConvertLimits("admins", ("fast",), False, False, 10)
    assert check(limits, {"preset": "accurate", "ocr": True}, is_admin=False, pages=50).option == "convert_by"
    r = check(limits, {"preset": "accurate", "ocr": True}, is_admin=True, pages=50)
    assert (r.option, r.message) == ("preset", "Your admin has turned off the Accurate preset.")
```

```python
# test_convert_limits.py — Review Focus 4
async def test_an_unknown_page_count_is_counted_from_the_file(db_conn, harness, store):
    _, as_user = harness
    owner = await _member(db_conn, "pl-owner")
    path = _write(store, HEX, "pdf", make_pdf(["a", "b", "c"]))
    await seed.seed_doc(db_conn, HEX, owner.user_id, file_type="pdf", bytes_path=path)   # page_count NULL
    await _save_documents(db_conn, {"max_pages": 2})
    async with as_user(owner) as client:
        r = await client.post(f"/v1/docs/{HEX}/convert", json={})
        assert r.status_code == 403 and r.json()["detail"]["option"] == "max_pages"
        assert (await _doc(db_conn, HEX))["conversion_state"] != "converting"
        assert (await client.post(f"/v1/docs/{HEX}/convert", json={"page_range": [1, 2]})).status_code == 202
```

Also, in `test_convert_limits.py`:
- `test_admins_only_refuses_a_member_and_lets_an_admin_convert`: member 403 `option == "convert_by"`; admin 202.
- `test_ocr_and_describe_refusals_name_the_option`: `{"ocr": true}` → `ocr`; `{"images": "describe"}` → `picture_description`.
- `test_an_env_bound_refuses_what_the_console_allowed`: env `DOCLING_PRESETS=fast,standard` (monkeypatch), console `["standard"]`, a request for `"accurate"` → 403 `preset`.
- `test_a_bad_page_range_is_a_422`: `[3, 1]`, `[0, 2]` and `[1]` → 422.
- `test_settings_docling_view`: a member with `convert_by` admins gets `may_convert: false`; an admin gets `true`; `enabled` follows `docling_convert.is_enabled`.
- `test_documents_section_is_served_by_the_admin_routes`: `GET /v1/admin/settings/documents` lists the five settings and `info` with id `docling`.

- [ ] **Step 2: Run them to verify they fail**

Run: `$PYTEST server/tests/test_docling_limits.py server/tests/test_convert_limits.py`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

- `ConvertOptionsIn.page_range`: a field validator; None, or exactly two ints with `1 <= start <= end`.
- `start_convert_job`: after the `is_enabled` check, add `page_count` to the existing SELECT and load the limits on the same connection. Only when `max_pages > 0`, find the page count: from the range, else `page_count`, else `await asyncio.to_thread(extract.pdf_page_count, doc_storage.resolve_stored(bytes_path))`. A file that can't be counted gives `None`, and Docling's own failure reports it. Then run `check`, and refuse before the `UPDATE` that sets `converting`. Rename `_principal` to `principal` (it's used now).
- Register `"documents": (docling_limits.SECTION, docling_limits.info_rows)` in `_SECTIONS`.

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command, then `$PYTEST server/tests/test_content_ops.py server/tests/test_docling_progress.py`. Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add server/services/docling_limits.py server/services/extract.py server/routers/docs.py server/routers/settings.py server/routers/admin.py server/tests/test_docling_limits.py server/tests/test_convert_limits.py
git commit -m "feat(docs): admin Docling limits, enforced when a conversion starts"
```

---

### Task 5: Overview checks: providers, reader, embeddings, Docling, web search

**Files:**
- Create: `server/services/console_overview.py`
- Modify: `server/routers/admin.py`
- Test: `server/tests/test_console_overview.py`

**Interfaces:**
- Consumes: Task 3's `reader_model_source`; `chat.reader.load_reader_config`; `llm.router.Router` (`providers`, `resolve`, `list_models`); `services.embeddings.embed_one`, `EMBEDDING_DIM`; `doc_pipeline.current_profile`; `docling_convert.is_enabled`; `web_search.SEARXNG_URL`; `chat_options.load_chat_options`.
- Produces:
  - `CHECK_TIMEOUT_S = 5.0`
  - `@dataclass(frozen=True) class Check`: `id: str`, `state: str` (`"ok"|"warn"|"error"|"off"`), `title: str`, `summary: str`, `section: str | None`, `items: tuple[dict, ...] = ()`; `def to_json(self) -> dict`.
  - `async def run_checks(conn, llm, env=os.environ, http: httpx.AsyncClient | None = None) -> list[Check]`: all checks at once; each under `CHECK_TIMEOUT_S`; a check that times out or raises becomes `Check(id, "error", title, f"Didn't answer within {CHECK_TIMEOUT_S:g} seconds." | "The check failed: <ExceptionType>.", section)`. Fixed order: providers, reader, context (Task 6), embeddings, docling, web_search.
  - `GET /v1/admin/overview` (admin) → `{"checks": [c.to_json() …]}`, calling `run_checks(conn, get_router())`.
- Titles, links and summaries (exact; `{…}` filled in):

  | id | title | section | states and summaries |
  |---|---|---|---|
  | providers | Model providers | deployment | ok "{n} reachable: {name} ({k} models), …"; warn "{names} can't be reached." (some); error "No provider can be reached." / "No model provider is configured (INFERENCE_PROVIDERS or OLLAMA_URL)." |
  | reader | Reader | deployment | ok "The chat model of each conversation (READER_MODEL=chat)." / "{model}, from {VAR}."; warn "The built-in fallback {model}. Set READER_MODEL on the server, or READER_MODEL=chat."; error "{model} isn't on {provider}: every reader call fails until it's pulled or READER_MODEL changes." / "Can't reach {provider} to check {model}." / "{model} can't be used: no Ollama provider is configured, so a bare model name has nowhere to go." |
  | embeddings | Embeddings | deployment | ok "{model} at {host}: {dim} dimensions."; warn adds " {n} documents were indexed under an older embedding setup; each rebuilds the next time someone uses it."; error "{model} returned {n} dimensions; the database holds {dim}." / "Can't reach the embedding model {model} at {host}: documents can't be indexed or searched." |
  | docling | Docling | documents | off "Off on this server (DOCLING_ENABLED)."; error "DOCLING_ENABLED is on, but Docling isn't installed: every conversion fails."; ok "On." |
  | web_search | Web search | chat | off "Not set up on this server (SEARXNG_URL)." / "Turned off in Chat options."; ok "SearXNG at {host} answers."; error "SearXNG at {host} doesn't answer: web search fails." |

- [ ] **Step 1: Write the failing tests**

Fake the router as `test_admin_lifecycle.py:239-254` does: `_router()` returns one Ollama provider listing `m`, whose own `list_models()` returns `["m", "llama3.2:3b"]`. Fake the embedding with `monkeypatch.setattr(embeddings, "embed_one", …)`. `_searxng_ok()` is an `httpx.AsyncClient` on an `httpx.MockTransport` answering 200 to `/healthz`, passed as `http`.

```python
async def test_a_check_that_hangs_reports_itself_and_the_others_still_answer(db_conn, monkeypatch):   # Review Focus 1
    monkeypatch.setattr(console_overview, "CHECK_TIMEOUT_S", 0.2)
    async def never(_text): await asyncio.sleep(10)
    monkeypatch.setattr(embeddings, "embed_one", never)
    started = time.monotonic()
    checks = {c.id: c for c in await run_checks(db_conn, _router(), {}, http=_searxng_ok())}
    assert time.monotonic() - started < 2
    assert checks["embeddings"].state == "error"
    assert checks["embeddings"].summary.startswith("Didn't answer within")
    assert checks["providers"].state == "ok" and checks["web_search"].state == "ok"
```

Also:
- `test_providers_states`: all listed → ok, with the counts in the summary; one failed → warn naming it; all failed → error; no providers → error with the configuration text.
- `test_reader_states`: `READER_MODEL=chat` → ok; `SUMMARIZE_MODEL=m` listed → ok "m, from SUMMARIZE_MODEL."; nothing set and `llama3.2:3b` listed → warn; not listed → error; provider unreachable → error "Can't reach …".
- `test_embeddings_states`: 768 numbers → ok; 1024 → error; raising `httpx.ConnectError` → error "Can't reach …"; two indexed documents with an old `embedding_profile` → warn with "2 documents".
- `test_docling_states`: disabled → off; enabled, `importlib.util.find_spec("docling")` patched to None → error; enabled and found → ok.
- `test_web_search_states`: `SEARXNG_URL` empty → off; chat options `off` → off "Turned off in Chat options."; `/healthz` 200 → ok; 500 or a connect error → error.
- `test_overview_route_is_admin_only`: member 403; admin 200 with `checks` in the order above.

- [ ] **Step 2: Run them to verify they fail**

Run: `$PYTEST server/tests/test_console_overview.py`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

- The reader check resolves the model with `llm.resolve` (`UnknownModel` → the "nowhere to go" error), then calls that provider's own `list_models()`. It doesn't use `is_listed`, which reports an unreachable provider as listed.
- The embedding check sends one fixed sentence ("Natural Reader embedding check."), without `dimensions`, so the length returned is the model's own.
- The rebuild count: `SELECT count(*) FROM documents WHERE state = 'indexed' AND embedding_profile IS DISTINCT FROM %s` with `current_profile()`.
- SearXNG: `GET {SEARXNG_URL}/healthz`, 3 s timeout. When `http` is None, use a short-lived `httpx.AsyncClient`.
- Hosts are shown with `urlsplit(url).hostname`, never the full URL.

- [ ] **Step 4: Run them to verify they pass**

Run: `$PYTEST server/tests/test_console_overview.py server/tests/test_conn_commits_before_response.py`
Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add server/services/console_overview.py server/routers/admin.py server/tests/test_console_overview.py
git commit -m "feat(admin): Overview checks for providers, reader, embeddings, Docling and web search"
```

---

### Task 6: Overview check: context windows

**Files:**
- Modify: `server/services/console_overview.py`, `server/llm/providers/ollama.py`, `server/services/model_router.py`, `server/routers/inference.py:19-38`
- Test: `server/tests/test_console_overview.py`, `server/tests/test_llm_ollama.py`

**Interfaces:**
- Consumes: Task 5's `Check` and `run_checks`; `chat.prompt.system_rules`; `chat.tools.available_tools`, `tool_spec`, `ToolContext`; `chat.project_scope.ProjectScope`, `ScopeDoc`; `chat.config.get_chat_config`; `services.assistant_profile.load_profile`.
- Produces:
  - `OllamaProvider.loaded_context_lengths(self) -> dict[str, int]`: `GET {url}/api/ps` → `{name: context_length}` for entries whose `context_length` is an int. Any error → `{}` with a debug log. The field is `context_length` in Ollama's `ProcessModelResponse` (`api/types.go`); confirm it on a loaded model during Task 13's walk.
  - `model_router.offered_chat_models(models: list[ModelInfo], embed_model: str) -> list[ModelInfo]`: the filter now inline in `inference.py` (drops the Ollama embedding model, `:latest`-insensitive). `inference.py` calls it; its tests stay green.
  - `console_overview.is_ollama_cloud(name: str) -> bool`: the name ends in `-cloud` or `:cloud`.
  - `console_overview.window_for(model: ModelInfo, loaded: dict[str, int]) -> tuple[int | None, str]`: Ruling 3, with sources (exact):
    - "The model's maximum (Ollama cloud)."
    - "Loaded now (Ollama)."
    - "Unknown: Ollama picks the size when it loads the model (4,096 tokens on a machine with under 24 GiB of VRAM)."
    - "Reported by the provider."
    - "Unknown: the provider doesn't report one."
  - `console_overview.project_turn_tokens(cfg: ChatConfig, web_mode: str, profile: str = "") -> int`: Ruling 4. A sample `ProjectScope` with one indexed `ScopeDoc`, the tools `available_tools` offers it, `ceil((len(rules) + spec_chars + cfg.project_map_chars + cfg.tool_result_budget_chars) / 4) + cfg.reply_reserve_tokens`. `spec_chars` is the `json.dumps` length of each `tool_spec(tool, ctx)` as a dict.
  - Check `context`, title "Context windows", section None. Items `{"model", "window", "source", "enough"}`. Summary "{k} of {m} chat models have room for a project-chat turn here (about {needed:,} tokens)."; ok when all have room, warn otherwise (unknown counts as no room); warn "No chat models are listed." when there are none.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_local_ollama_model_not_loaded_is_unknown_never_its_maximum():
    m = ModelInfo("ollama:gemma4:e4b", "ollama", "ollama", "gemma4:e4b", Capabilities(context_window=131072))
    assert window_for(m, {}) == (None, "Unknown: Ollama picks the size when it loads the model (4,096 tokens on a machine with under 24 GiB of VRAM).")
    assert window_for(m, {"gemma4:e4b": 4096}) == (4096, "Loaded now (Ollama).")

def test_an_ollama_cloud_model_gets_its_maximum():
    m = ModelInfo("ollama:gemma4:31b-cloud", "ollama", "ollama", "gemma4:31b-cloud", Capabilities(context_window=262144))
    assert window_for(m, {}) == (262144, "The model's maximum (Ollama cloud).")

def test_a_project_turn_is_larger_than_its_fixed_budgets():
    cfg = ChatConfig()
    n = project_turn_tokens(cfg, "ask")
    assert n > (cfg.project_map_chars + cfg.tool_result_budget_chars) // 4 + cfg.reply_reserve_tokens
    assert project_turn_tokens(cfg, "ask", profile="x" * 4000) >= n + 1000   # the profile leads the rules
```

Also:
- `test_context_check_warns_on_a_small_or_unknown_window`: a 4,096 loaded model and an unknown one → warn, "0 of 2"; items carry `enough: False`.
- `test_context_check_ok_when_every_window_fits`: 262,144 cloud and an openai model reporting 131,072 → ok.
- `test_the_embedding_model_isnt_listed_as_a_chat_model` (`offered_chat_models`, `:latest` on either side).
- Ollama provider: `/api/ps` with `{"models": [{"name": "m:latest", "context_length": 8192}, {"name": "x"}]}` → `{"m:latest": 8192}`; HTTP 500 → `{}`.

- [ ] **Step 2: Run them to verify they fail**

Run: `$PYTEST server/tests/test_console_overview.py server/tests/test_llm_ollama.py`
Expected: FAIL (`window_for` and `loaded_context_lengths` missing).

- [ ] **Step 3: Implement** the functions above. Add the `context` check to `run_checks` in third place. It lists models once, and calls `loaded_context_lengths` once per Ollama provider.

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command, plus `$PYTEST server/tests -k inference`. Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add server/services/console_overview.py server/llm/providers/ollama.py server/services/model_router.py server/routers/inference.py server/tests/test_console_overview.py server/tests/test_llm_ollama.py
git commit -m "feat(admin): Overview shows each chat model's context window and whether a project turn fits"
```

---

### Task 7: The console shell; existing sections moved in unchanged

**Files:**
- Create: `src/components/admin/ConsoleShell.jsx`, `src/components/admin/ConsoleShell.test.jsx`, `src/components/admin/PeopleSection.jsx`, `src/components/admin/UsageSection.jsx`
- Modify: `src/components/admin/AdminConsole.jsx`, `src/components/admin/AdminConsole.test.jsx`, `src/components/admin/AdminConsole.enroll-busy.test.jsx`

**Interfaces:**
- Produces:
  - `ConsoleShell({ theme, sections, active, onSelect, onBack, dirty, children })`. `sections` is `[{ id, label }]`. It renders the heading "Admin console", **Back to reader**, a vertical `role="tablist"` (`aria-label="Admin sections"`, tabs `id="admin-tab-<id>"`, `aria-controls="admin-panel"`, roving `tabIndex`) shown from `md` up, a `<select aria-label="Admin section">` shown below `md` (`min-h-11`), and `role="tabpanel"` `id="admin-panel"` labelled by the active tab.
  - Keys on the tablist: ArrowDown/ArrowRight next, ArrowUp/ArrowLeft previous (wrapping), Home first, End last. Selection follows focus, as in `SettingsDialog`.
  - Unsaved guard: while `dirty`, a tab, the picker or **Back to reader** doesn't navigate. It shows an inline `role="alertdialog"` "You have unsaved changes in {label}." with **Discard changes** (navigates, focus to the new tab) and **Keep editing** (focus back to the active tab). Escape is Keep editing.
  - `AdminConsole` keeps its props (App.jsx unchanged) and holds `active` (default `people` until Task 9) and `dirty` (reset to false on every navigation). It passes `onDirtyChange={setDirty}` to sections that edit. The sections:

    | id | label | renders |
    |---|---|---|
    | people | People | `PeopleSection` |
    | projects | Projects | `AdminProjectsSection` (`openProjectId` → `ProjectPage`, as today) |
    | chat | Chat | `AssistantProfileSection` then `ChatOptionsSection` (the preview refresh kept) |
    | defaults | Defaults for users | `DefaultsSection` |
    | usage | Usage | `UsageSection` |
    | deployment | Deployment | today's "Deployment config" block, moved as is (replaced in Task 10) |

  - `PeopleSection({ theme, apiHost, apiPort, currentUserId, showToast })` and `UsageSection({ theme, apiHost, apiPort, showToast })`: the current JSX and handlers, moved. The `projectsVersion` reload key goes: Projects now remounts, and so reloads, every time it opens.

- [ ] **Step 1: Write the failing tests**

`ConsoleShell.test.jsx`, rendering the shell alone with three sections:
- `it('has a tab per section and a phone picker with the same sections')`: `getAllByRole('tab')` labels equal the picker's option labels; the panel's `aria-labelledby` is the active tab's id.
- `it('moves with the arrow keys, Home and End')`: ArrowDown from the first calls `onSelect('b')`; End calls `onSelect('c')`; ArrowDown from the last wraps to `'a'`.
- `it('asks before leaving a section with unsaved changes')`: with `dirty`, a click on tab b doesn't call `onSelect`, and the alert says "You have unsaved changes in A."; **Keep editing** closes it and focuses tab a; clicking again then **Discard changes** calls `onSelect('b')`.
- `it('asks before going back to the reader, and Escape keeps editing')`.

`AdminConsole.test.jsx`: add `openSection(name)` (`fireEvent.click(screen.getByRole('tab', { name }))`). Each existing `describe` opens its section first: users → People; usage → Usage; config → Deployment; assistant and chat options → Chat. Rewrite `states what deleting a user does to their projects, and reloads the Projects section afterwards`: after the delete, opening Projects fetches `/v1/admin/projects` again. Add `it('shows one section at a time')`: on People, no "Inference usage" region.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/admin`
Expected: FAIL (no `ConsoleShell`; no tab named People).

- [ ] **Step 3: Implement** the shell and the moves. Copy class names from `SettingsDialog.jsx:97-121` for the tab look; the console stays a full-height view, not a modal.

- [ ] **Step 4: Run them to verify they pass**

Run: `npx vitest run src/components/admin && npx eslint src`
Expected: PASS, lint clean.

- [ ] **Step 5: Commit** (with permission)

```bash
git add src/components/admin/ConsoleShell.jsx src/components/admin/ConsoleShell.test.jsx src/components/admin/PeopleSection.jsx src/components/admin/UsageSection.jsx src/components/admin/AdminConsole.jsx src/components/admin/AdminConsole.test.jsx src/components/admin/AdminConsole.enroll-busy.test.jsx
git commit -m "feat(admin): the console in sections, with a section list and an unsaved-changes guard"
```

---

### Task 8: The row pattern: `useSectionSettings` and `SettingRow`

**Files:**
- Create: `src/hooks/useSectionSettings.js`, `src/hooks/useSectionSettings.test.js`, `src/components/admin/SettingRow.jsx`, `src/components/admin/SettingRow.test.jsx`

**Interfaces:**
- Consumes: Task 3's routes and view shape.
- Produces:
  - `useSectionSettings(apiHost, apiPort, section)` → `{ status: 'loading'|'ready'|'failed', info, value(name), source(name), fallback(name), envName(name), set(name, v), useServer(name), canUseServer(name), dirty, saving, error, save(): Promise<boolean>, discard() }`.
    - `value`: the drafted value; after `useServer`, the fallback; else the server's value.
    - `source`: `'admin'` when drafted; the fallback's source after `useServer`; else the server's.
    - `canUseServer(name)`: the shown source is `'admin'`.
    - `save`: PUT `{ changes }` with only the drafted names (null for `useServer`). On 2xx it adopts the response, clears the draft and returns true. Otherwise it sets `error` to `detail.message` (or the string detail, or `HTTP n`), keeps the draft and returns false.
  - `SettingRow({ theme, id, label, help, source, envName, canUseServer, onUseServer, children })`: label, help (`id="<id>-help"`), control, source line (`id="<id>-source"`), and **Use the server setting** when `canUseServer`. Source copy is in Global Constraints: env → "From the server setting `{envName}`."
  - `SectionSaveBar({ theme, dirty, saving, error, onSave, onDiscard })`, exported from `SettingRow.jsx`: **Save** (disabled unless dirty and not saving; "Saving…" while out), **Discard changes** (only when dirty), and `error` as `role="alert"`. Buttons are `min-h-11 md:min-h-0`.

- [ ] **Step 1: Write the failing tests**

```js
it('drafts, resets to the fallback, and saves only what changed', async () => {
  mockView({ timezone: { value: 'Europe/Berlin', source: 'admin', env: 'TZ', fallback: 'UTC', fallback_source: 'default' } });
  const { result } = renderHook(() => useSectionSettings('', '', 'deployment'));
  await waitFor(() => expect(result.current.status).toBe('ready'));
  act(() => result.current.useServer('timezone'));
  expect([result.current.value('timezone'), result.current.source('timezone'), result.current.dirty]).toEqual(['UTC', 'default', true]);
  await act(() => result.current.save());
  expect(lastPut()).toEqual({ changes: { timezone: null } });
  expect(result.current.dirty).toBe(false);
});
```

Also:
- `it('keeps the draft and shows the server message when a save is refused')`: a 422 `{detail: {error: 'invalid_setting', message: 'Nope.'}}` → `save()` resolves false, `error === 'Nope.'`, still dirty.
- `it('reports failed when the section cannot be loaded')`.
- `SettingRow.test.jsx`: the three source lines exactly, `TZ` in a `<code>`; the button only with `canUseServer`, calling `onUseServer`. `SectionSaveBar`: Save disabled when clean; the alert shows the error.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/hooks/useSectionSettings.test.js src/components/admin/SettingRow.test.jsx`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement.** Load with the async-IIFE pattern `AdminConsole.jsx:121-130` uses (`react-hooks/set-state-in-effect`).

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command, then `npx eslint src`. Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add src/hooks/useSectionSettings.js src/hooks/useSectionSettings.test.js src/components/admin/SettingRow.jsx src/components/admin/SettingRow.test.jsx
git commit -m "feat(admin): one row pattern for console settings: source, reset, one Save"
```

---

### Task 9: The Overview section

**Files:**
- Create: `src/components/admin/OverviewSection.jsx`, `src/components/admin/OverviewSection.test.jsx`
- Modify: `src/components/admin/AdminConsole.jsx` (first section, the default), `src/components/admin/AdminConsole.test.jsx`

**Interfaces:**
- Consumes: Task 5/6's `GET /v1/admin/overview`; Task 7's section ids.
- Produces: `OverviewSection({ theme, apiHost, apiPort, sectionLabel, onOpenSection })`. `sectionLabel(id)` gives a section's label. Each check is one row: a state word (**OK**, **Warning**, **Problem**, **Off**: words, never colour alone), the title and the summary, plus **Open {label}** when `section` is set. The context check's items are a table: Model, Window (`toLocaleString('en-US')` tokens, or "Unknown"), Where it came from, Room (Yes/No). The section shows "Checking…" while loading, **Check again** to re-run, and "Couldn't run the checks." on failure.

- [ ] **Step 1: Write the failing tests**

- `it('shows each check with a state word, and links to the section that fixes it')`: a `docling` `error` row reads "Problem", and **Open Documents** calls `onOpenSection('documents')`.
- `it('lists each model in the context check')`: an item `{model: 'ollama:m', window: null, source: 'Unknown: …', enough: false}` renders "Unknown" and "No".
- `it('checks again on request')`: a second GET to `/v1/admin/overview`.
- `AdminConsole.test.jsx`: `it('opens on Overview, and an Open link switches section')`: the active tab is Overview; clicking **Open Documents** on a mocked check selects the Documents tab. Until Task 11, Documents is a placeholder, so assert on the tab's `aria-selected`.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/admin/OverviewSection.test.jsx src/components/admin/AdminConsole.test.jsx`
Expected: FAIL.

- [ ] **Step 3: Implement.** Add `overview` first and `documents` after Projects to the section list (Documents renders nothing until Task 11). Make `overview` the default.

- [ ] **Step 4: Run them to verify they pass**

Run: `npx vitest run src/components/admin && npx eslint src`. Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add src/components/admin/OverviewSection.jsx src/components/admin/OverviewSection.test.jsx src/components/admin/AdminConsole.jsx src/components/admin/AdminConsole.test.jsx
git commit -m "feat(admin): Overview section, the console's first page"
```

---

### Task 10: The Deployment section: timezone and the read-only list

**Files:**
- Create: `src/components/admin/DeploymentSection.jsx`, `src/components/admin/DeploymentSection.test.jsx`, `src/lib/zoneFormat.js`, `src/lib/zoneFormat.test.js`
- Modify: `src/components/admin/AdminConsole.jsx` (the old config block goes; the zone loads), `src/components/admin/PeopleSection.jsx` (dates), `src/components/admin/UsageSection.jsx` (header and note), `src/components/admin/AdminConsole.test.jsx` (config tests replaced)

**Interfaces:**
- Consumes: Task 8 (`useSectionSettings`, `SettingRow`, `SectionSaveBar`); Task 3's `info` rows.
- Produces:
  - `zoneFormat.zoneOptions(current?: string) -> string[]`: `'UTC'` first, then `Intl.supportedValuesOf('timeZone')` without `'UTC'`, plus `current` when the list lacks it. Just `['UTC']` (plus `current`) when `supportedValuesOf` is missing.
  - `zoneFormat.formatDay(value: string, zone: string) -> string`: `YYYY-MM-DD` in `zone` (`Intl.DateTimeFormat('en-CA', { timeZone, year, month, day })`); a zone `Intl` refuses → UTC; an unparseable value → the first 10 characters (today's behaviour).
  - `DeploymentSection({ theme, apiHost, apiPort, showToast, onDirtyChange, onSaved })`:
    - One row **Timezone** (`<select>` of `zoneOptions`), help line (exact): "Decides when each day's token budget resets and which day usage counts toward. Changing it moves today's reset once, so someone may get a fresh budget a few hours early or late."
    - A Save bar; `onDirtyChange(dirty)` on every change; `onSaved(timezone)` after a save; a toast "Timezone saved. It applies from the next request."
    - Below, "Server settings (read-only)": each `info` row as label, value, and its source line ("From the server setting `X`." / "The default.").
  - `AdminConsole` loads the zone once on mount (`GET /v1/admin/settings/deployment`, `settings.timezone.value`, `'UTC'` on failure), passes `zone` to `PeopleSection`, and updates it from `onSaved`.
  - `PeopleSection` shows `formatDay(u.created_at, zone)` where it sliced the string.
  - `UsageSection` header "Day" (was "Day (UTC)"), and under the table: "Each day is counted in the deployment's timezone. Changing it doesn't move days already counted."

- [ ] **Step 1: Write the failing tests**

```js
it('formats a day in the deployment zone', () => {
  expect(formatDay('2026-10-25T20:00:00Z', 'Asia/Colombo')).toBe('2026-10-26');
  expect(formatDay('2026-10-25T20:00:00Z', 'UTC')).toBe('2026-10-25');
  expect(formatDay('2026-10-25T20:00:00Z', 'Nope/Nope')).toBe('2026-10-25');
});
```

Also:
- `DeploymentSection.test.jsx`:
  - `it('saves a chosen zone and reports unsaved changes until then')`: choose `Asia/Colombo` → `onDirtyChange(true)`; Save → PUT `{changes: {timezone: 'Asia/Colombo'}}`; after it, the source line reads "Set here." and `onSaved('Asia/Colombo')`.
  - `it('goes back to the server setting')`: **Use the server setting**, then Save → `{changes: {timezone: null}}`.
  - `it('shows a refused save inline')`.
  - `it('lists the read-only values with their source, and secrets only as set or not set')`: rows `provider:cloud` "openai · https://llm.example.com/v1" with "From the server setting INFERENCE_CLOUD_URL."; `session_secret` "set".
- `AdminConsole.test.jsx`:
  - `it('shows a person's created date in the deployment zone')`: zone `Asia/Colombo`, `created_at: '2026-09-17T20:00:00Z'` → "2026-09-18".
  - The three old `config` tests become one: Deployment lists the providers from `info`, never a key.
  - `it('asks before leaving Deployment with an unsaved zone')`: change the zone, click People → the alert.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/lib/zoneFormat.test.js src/components/admin`
Expected: FAIL.

- [ ] **Step 3: Implement.** Remove the "Deployment config" block and its `/v1/admin/inference/config` fetch from `AdminConsole` (Ruling 12).

- [ ] **Step 4: Run them to verify they pass**

Run: `npx vitest run src/lib src/components/admin && npx eslint src`. Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add src/lib/zoneFormat.js src/lib/zoneFormat.test.js src/components/admin/DeploymentSection.jsx src/components/admin/DeploymentSection.test.jsx src/components/admin/AdminConsole.jsx src/components/admin/AdminConsole.test.jsx src/components/admin/PeopleSection.jsx src/components/admin/UsageSection.jsx
git commit -m "feat(admin): Deployment section: the timezone, and the server's values read-only"
```

---

### Task 11: The Documents section: Docling limits

**Files:**
- Create: `src/components/admin/DocumentsSection.jsx`, `src/components/admin/DocumentsSection.test.jsx`
- Modify: `src/components/admin/AdminConsole.jsx` (render it; pass `onDirtyChange`)

**Interfaces:**
- Consumes: Task 8; Task 4's `documents` view and `info` ids (`docling`, `pdf_upload_max`, `text_upload_max`).
- Produces: `DocumentsSection({ theme, apiHost, apiPort, showToast, onDirtyChange })`. Rows, with help lines (exact):

  | Row | Control | Help |
  |---|---|---|
  | Who may convert | radios **Everyone with the reader capability** / **Admins only** | "Who can turn a PDF into Markdown with Docling." |
  | Presets allowed | checkboxes Fast / Standard / Accurate | "Accurate runs a vision model on every page: the slowest." |
  | OCR | checkbox **Allow OCR** | "Reads scanned pages. Slow on long scans." |
  | Picture descriptions | checkbox **Allow picture descriptions** | "Runs a vision model on every picture." |
  | Largest conversion | number, min 0 | "Pages one conversion may take; 0 = no limit. A longer PDF can still be converted a page range at a time." |

  - Narrowing shown: an option the fallback doesn't allow is disabled, with "Turned off by the server setting `{env}`." (presets outside `fallback`; a bool whose fallback is false; `convert_by` locked to Admins only when the fallback is `admins`). With a nonzero fallback for `max_pages`: "The server setting `DOCLING_MAX_PAGES` allows at most {n}."
  - Client checks: no preset ticked → Save disabled and "Keep at least one preset."; `max_pages` not a whole number ≥ 0 → Save disabled and "Must be a whole number from 0 to 100000."
  - Banner when info `docling` is "Off": "Docling is off on this server (`DOCLING_ENABLED`). These limits apply once it's on."
  - Read-only, under the rows: "PDF uploads up to {pdf} and other files up to {text} (`PDF_UPLOAD_MAX_MB`, `TEXT_UPLOAD_MAX_MB`). A proxy in front of the server can set a lower limit."

- [ ] **Step 1: Write the failing tests**

- `it('saves the limits the admin changed')`: tick off OCR, set Largest conversion 200, Save → `{changes: {ocr: false, max_pages: 200}}`.
- `it('disables what the server setting turns off, and says so')` (Review Focus 5): `presets.fallback = ['fast', 'standard']`, `fallback_source: 'env'` → the Accurate checkbox is disabled, with "Turned off by the server setting DOCLING_PRESETS."
- `it('keeps at least one preset')`.
- `it('reports unsaved changes')`: `onDirtyChange(true)` after a change and `false` after Save.
- `it('says when Docling is off on this server')`.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/admin/DocumentsSection.test.jsx`
Expected: FAIL.

- [ ] **Step 3: Implement** on `useSectionSettings(apiHost, apiPort, 'documents')`.

- [ ] **Step 4: Run them to verify they pass**

Run: `npx vitest run src/components/admin && npx eslint src`. Expected: PASS.

- [ ] **Step 5: Commit** (with permission)

```bash
git add src/components/admin/DocumentsSection.jsx src/components/admin/DocumentsSection.test.jsx src/components/admin/AdminConsole.jsx
git commit -m "feat(admin): Documents section: what a Docling conversion may use"
```

---

### Task 12: The convert dialog follows the limits

**Files:**
- Create: `src/hooks/useConvertLimits.js`, `src/components/DoclingConvertDialog.limits.test.jsx`
- Modify: `src/components/DoclingConvertDialog.jsx`, `src/App.jsx:191` and `:1394-1402`

**Interfaces:**
- Consumes: Task 4's `GET /v1/settings/docling`.
- Produces:
  - `useConvertLimits(apiHost, apiPort, open: boolean) -> limits | null`: fetches each time `open` turns true. `null` until it answers, or on any failure: the dialog then behaves as today, and the server still enforces.
  - `DoclingConvertDialog` gains `limits` (default `null`):
    - a preset not in `limits.presets`, OCR when `!limits.ocr`, and Describe when `!limits.picture_description` are disabled, each with "Turned off by your admin";
    - the opening form moves off a disabled choice: the first allowed preset, OCR off, images `drop`;
    - `!limits.may_convert` → the notice "Only admins can convert documents on this server." and Convert disabled;
    - `limits.presets` empty → "Your admin hasn't allowed any conversion preset." and Convert disabled;
    - when `limits.max_pages > 0` and the pages to convert (the range, else `pageCount`) exceed it → "This server converts at most {n} pages at a time. Choose a page range of {n} pages or fewer." and Convert disabled.
  - `App.jsx`: `const convertLimits = useConvertLimits(apiHost, apiPort, convertDialogOpen);` passed as `limits`.

- [ ] **Step 1: Write the failing tests**

- `it('greys out what the admin turned off')` (Review Focus 5): limits `{presets: ['fast', 'standard'], ocr: false, picture_description: false, may_convert: true, max_pages: 0}` → the Accurate button, the OCR checkbox and Describe are disabled; "Turned off by your admin" appears three times.
- `it('opens on an allowed preset when the saved one is turned off')`: `initialOptions.preset = 'accurate'` → submitting sends `preset: 'fast'`.
- `it('asks for a page range over the page limit')`: `pageCount` 300, `max_pages` 100 → Convert disabled with the message; tick Limit page range, 1 to 100 → Convert enabled, and submit sends `page_range: [1, 100]`.
- `it('tells a reader only admins may convert')`.
- `it('behaves as before without limits')`: `limits={null}` → everything enabled (the existing behaviour).

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/components/DoclingConvertDialog.limits.test.jsx`
Expected: FAIL.

- [ ] **Step 3: Implement.** `deriveInitial(initialOptions, pageCount, limits)` applies the move-off rule. Keep the dialog purely controlled, as its comment says.

- [ ] **Step 4: Run them to verify they pass**

Run: `npx vitest run && npx eslint src`. Expected: PASS (the whole frontend: App.jsx changed).

- [ ] **Step 5: Commit** (with permission)

```bash
git add src/hooks/useConvertLimits.js src/components/DoclingConvertDialog.jsx src/components/DoclingConvertDialog.limits.test.jsx src/App.jsx
git commit -m "feat(docs): the convert dialog shows what the admin turned off"
```

---

### Task 13: Documentation, and the walk in the running app

**Files:**
- Modify: `.env.example` (Ruling 7's five variables beside `DOCLING_ENABLED`; next to `TZ`, that it is also the deployment timezone's default), `CHANGELOG.md` (`## [Unreleased]`, written for users), `docs/USER_GUIDE.md` (§ Admin console: the sections, the row pattern, Overview, timezone, Docling limits), `docs/ARCHITECTURE.md` (the five new services, the three new routes), `docs/TESTING.md` (the new test files)

- [ ] **Step 1: Write the docs.** Use `example.com` in examples, and name each env var with its default.

- [ ] **Step 2: Run every gate**

Run: `$PYTEST server/tests`, then `npx vitest run`, then `npx eslint src`.
Expected: all pass. Record the counts for the handover.

- [ ] **Step 3: Walk the journey in the running app** (HANDOVER.md § Environment: scratch database on 5434, `run.py` under `env -i` with `AUTH_ENABLED=false` on `HOST=127.0.0.1`, `DOCLING_ENABLED=true`, `npx vite --port 5173`, headless Chromium from `~/.npm/_npx`). Each step names the control that performs it:

  1. Profile menu → **Admin console**: it opens on **Overview**. Every check has a state; the context check lists `gemma4:31b-cloud`.
  2. Load a local model once (one chat message to it), then **Check again**: its window reads "Loaded now (Ollama)." That confirms `/api/ps`'s `context_length` (Task 6).
  3. **Deployment** → Timezone `Asia/Colombo`, then **People**: the unsaved prompt appears; **Keep editing**, then **Save**. Reload the page: "Set here.". **People** dates are in Colombo.
  4. **Documents**: untick **Allow OCR**, Largest conversion 2, **Save**.
  5. Open a 6-page PDF → **Convert**: OCR is greyed with "Turned off by your admin", and Convert is disabled until a 2-page range is chosen. Convert pages 1–2: it runs.
  6. Phone width (390 px): the section picker replaces the list, and every target is at least 44 px.
  7. Keyboard: Tab to the section list, then ArrowDown, Home, End.

  **Not walkable here:** a non-admin refused by "Admins only". It needs a second signed-in user, which `AUTH_ENABLED=false` can't give. Walk it with the throwaway Keycloak on `:18081` (never the compose Keycloak) if the owner wants; otherwise report it as covered by `test_convert_limits.py` only.

- [ ] **Step 4: Update `HANDOVER.md`** with a new top entry: what shipped, the gate counts, what was walked and what wasn't.

- [ ] **Step 5: Commit** (with permission)

```bash
git add .env.example CHANGELOG.md docs/USER_GUIDE.md docs/ARCHITECTURE.md docs/TESTING.md HANDOVER.md
git commit -m "docs: admin console v2 phase 1"
```
