# Admin console v2, phase 2a (Models: reader, context sizes, windows) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A **Models** section in the admin console that fixes today's three local-model bugs (silent truncation at Ollama's 4k default, reader-vs-chat reloads, no trimming without a window) and makes the reader, context sizes, declared windows, provider locality and the offered chat models settable without a restart. People also gets a search, because the member list keeps growing (owner request, 2026-10-26).

**Architecture:**
- **One new `app_settings` section, `models`.** It is resolved by phase 1's `console_settings` (console value, then env, then default). The pure resolution rules live in one module, `server/services/model_settings.py`.
- **One read per turn.** The orchestrator reads the section once at the start of a turn (fail-open to the environment). It then settles the chat call's `num_ctx`, the trimming window, and the reader's model and `num_ctx`, and hands the reader's part to the tools through `ToolContext.reader_cfg`.
- **Narrowing in three places.** The model list, the turn route and the Overview apply the console's chat-model narrowing.

**Tech Stack:** FastAPI, psycopg3 async, pytest (scratch Postgres), React 19, vitest, Tailwind 4.

**Spec:** `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`: §1 (the bugs), §2 anti-goals, §3 (row pattern, `models` section key), §4.1 (Reader and Context windows checks), §4.2.1, §4.2.2, §5, §6 item 2a, §7 decisions 1 and 2. Phase 1's plan, `docs/superpowers/plans/2026-10-25-admin-console-v2-phase1.md`, built the shell, the row pattern and the Overview this plan extends.

## Global Constraints

- **Open source, many deployers.** No value specific to one deployment, in code or docs; docs use example.com. Every new setting has a safe default and an env fallback.
- **No new Ollama-only paths.** Anything that talks to a model works for both provider kinds (`ollama`, `openai`), or says plainly which kind it needs. `num_ctx` is an Ollama option; the `openai` kind ignores it (`openai_compat.py:172`).
- **Per request, never cached per process.** The `models` row is read once per turn and once per request; no module-level cache (`WORKERS > 1`).
- **Admin routes require the admin capability server-side** (`deps.require_admin`); the UI only hides.
- **Accessibility as in phase 1:** labelled controls, `aria-describedby` to the help and source lines, `role="alert"` on refusals, 44 px targets below `md` (`min-h-11 md:min-h-0`).
- **The console can only narrow.** The chat-model selection is intersected with the env allow-lists, never unioned; an empty selection means "the env list", never "everything" (spec §2 anti-goals, §4.2.1).
- **Constants, verbatim:**
  - local Ollama default context size `16384` tokens (`DEFAULT_LOCAL_NUM_CTX`);
  - ceiling `INFERENCE_NUM_CTX_MAX`, default `32768` (existing, `inference_limits.py`);
  - section key `models`;
  - setting names `reader`, `context_size`, `context_sizes`, `context_windows`, `provider_locality`, `chat_models`.
- **Env names:**
  - `INFERENCE_LOCAL_NUM_CTX` (new, ruling R1);
  - `INFERENCE_<NAME>_CONTEXT_WINDOW` per provider (spec §4.2.1; `<NAME>` as in `INFERENCE_<NAME>_URL`, so `INFERENCE_OLLAMA_CONTEXT_WINDOW` when `INFERENCE_PROVIDERS` is unset);
  - the reader's existing chain `READER_MODEL`, then `SUMMARIZE_MODEL`, then `WEB_SEARCH_SUMMARY_MODEL`.
- **Model ids** are canonical `provider:name` (`llm/router.py`). A trailing `:latest` on either side doesn't matter when a console map or list is matched against a model.
- **Tests run against the scratch Postgres only.** Set `TEST_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` to `127.0.0.1:5434` (values in the workspace's `constraints.md`) before every pytest run. Unset, the suite connects to **5433, the live server**.
- **Never:**
  - use port 8000 for a test backend (nginx proxies the live site to it; walks use 8100);
  - use the compose Keycloak;
  - use the real DB `natural_reader` on 5433;
  - print `.env` values;
  - run `pkill -f run.py`;
  - run the spec §8 measurements unasked;
  - evaluate with local `llama3.2:3b`.
- **Stage explicit paths. Commits only as the owner has allowed for this plan. No push, no merge.**
- **Copy:** plain, one line per help text, saying what changes. Exact strings this plan fixes are quoted in the tasks.

## Rulings made while writing this plan (each costs one small change if wrong)

- **R1.** The spec names the "deployment's context size" but no variable for it; it is `INFERENCE_LOCAL_NUM_CTX`. Cost: renaming one env var.
- **R2.** The size a local Ollama model gets resolves in this order:
  1. the console's size for that model;
  2. the deployment size (console, then `INFERENCE_LOCAL_NUM_CTX`);
  3. the person's own Settings value;
  4. `16384`.

  The result is clamped to `INFERENCE_NUM_CTX_MAX`. Steps 1–2 empty is what spec §4.2.1 calls "unset", so a person's own value keeps working there and nobody falls to Ollama's 4k. Cost: if the owner wants 16,384 to beat personal values, step 3 moves below step 4.
- **R3.** A non-local model (cloud Ollama, any `openai`-kind provider) keeps today's behaviour: the person's own `num_ctx` passes through clamped, and the reader sends **no** `num_ctx`. Cloud Ollama gives its maximum context (spec §1, verified), and `openai` ignores the option. Cost: none known.
- **R4.** Locality, first match wins:
  1. an Ollama model name ending in `-cloud` or `:cloud` is cloud on any host (`console_overview.is_ollama_cloud`);
  2. the console's override for the provider;
  3. a provider host that is an IP literal in a loopback or private range, `localhost`, or a single-label name (a container service such as `ollama`) is local;
  4. anything else is cloud.

  Cost: a LAN host with a dotted name reads as cloud until the admin overrides it.
- **R5.** With the chat-model fallback, the Overview's Reader line is **ok**, not a warning; the cloud hint (spec §4.1) rides on it. A permanent warning on every default install would teach admins to ignore the page. Cost: one state word.
- **R6.** A non-empty console chat-model selection whose models are all gone offers **nothing** and the Overview says so. Falling back to the env list would widen what the admin narrowed (spec §2 anti-goal). Cost: none known.

## Review Focus

1. **A person's own context size meets an admin's.** With the admin's size set, it wins for local Ollama models. With it unset, the person's value applies. Either is clamped to `INFERENCE_NUM_CTX_MAX`. (Task 2 tests the three cases.)
2. **Reader and chat on different kinds.** When the reader is the chat model and the chat is cloud or `openai`, no `num_ctx` is sent for reader calls. When the reader is a different local model, it gets that model's size, never the person's chat value. (Task 3.)
3. **A console entry names a model that is gone** (in `context_sizes`, `chat_models` or `reader`). Nothing crashes. A stale `reader` is an Overview error naming Models. A stale `chat_models` selection offers nothing, per R6. (Tasks 1, 4, 6.)
4. **The `models` row can't be read** (Postgres down mid-turn). The turn uses the env and the defaults and never fails. (Task 2.)
5. **`:latest` on one side only.** A console size saved for `ollama:gemma4:e4b` applies to a turn on `ollama:gemma4:e4b:latest`, and the reverse. (Task 1.)

---

### Task 1: The `models` section and its resolution rules

**Files:**
- Create: `server/services/model_settings.py`
- Modify: `server/routers/admin.py` (`_SECTIONS` gains `"models"`)
- Test: `server/tests/test_model_settings.py`, `server/tests/test_admin_settings.py`

**Interfaces:**
- Consumes:
  - `console_settings.Section`, `Setting`, `load`, `view`;
  - `inference_limits.InferenceLimits`, `clamp`;
  - `llm.providers.base.ProviderConfig` (`name`, `kind`, `url`);
  - `llm.router.ModelInfo` (`id`, `provider`, `kind`, `name`, `capabilities.context_window`);
  - `console_overview.is_ollama_cloud`. Move it here and re-export it from `console_overview` so existing imports keep working.
- Produces:
  - `SECTION: Section` (key `"models"`);
  - `DEFAULT_LOCAL_NUM_CTX = 16384`;
  - `@dataclass(frozen=True) class ModelsSettings` with fields:
    - `reader: str` (`"chat"` or a model id);
    - `reader_source: str` (`"admin"|"env"|"default"`);
    - `reader_env: str | None` (the variable that decided it, `chat.reader.reader_model_source`);
    - `context_size: int | None`;
    - `context_size_source: str`;
    - `context_sizes: dict[str, int]`;
    - `context_windows: dict[str, int]`;
    - `provider_locality: dict[str, str]`;
    - `chat_models: tuple[str, ...]`.
  - `async def load_models_settings(conn, env=os.environ) -> ModelsSettings`;
  - `def from_env(env=os.environ) -> ModelsSettings` (the fail-open value: what applies with no console row);
  - `def same_model(a: str, b: str) -> bool` and `def lookup(mapping: Mapping[str, V], model_id: str) -> V | None` (`:latest`-insensitive);
  - `def is_local(provider: ProviderConfig, model_name: str, locality: Mapping[str, str]) -> bool` (R4);
  - `def num_ctx_for(ms, model_id, *, local: bool, user_value: int | None, limits) -> int | None` (R2/R3);
  - `def deployment_num_ctx(ms, model_id, limits) -> tuple[int | None, str]`: steps 1–2 of R2, clamped, with where it came from (`"model"|"admin"|"env"`); `(None, "unset")` when neither;
  - `def declared_window(ms, model_id, provider_name: str, env) -> tuple[int | None, str]`: console map, then `INFERENCE_<NAME>_CONTEXT_WINDOW`; source `"admin"|"env"|"none"`;
  - `def offered(models: list, ms) -> list`: R6;
  - `def info_rows(env=os.environ) -> list[dict]`: read-only rows in `deployment_info`'s shape. Ids `num_ctx_max` (`INFERENCE_NUM_CTX_MAX`) and `keep_alive_max` (`INFERENCE_KEEP_ALIVE_MAX`).

- [ ] **Step 1: Write the failing tests** in `test_model_settings.py`:

```python
def test_defaults_without_console_or_env():
    ms = model_settings.from_env({})
    assert ms.reader == "chat" and ms.reader_source == "default"
    assert ms.context_size is None and ms.context_sizes == {} and ms.chat_models == ()

def test_reader_env_chain_names_the_deciding_variable():
    ms = model_settings.from_env({"SUMMARIZE_MODEL": "ollama:gemma4:e4b"})
    assert (ms.reader, ms.reader_source, ms.reader_env) == ("ollama:gemma4:e4b", "env", "SUMMARIZE_MODEL")

def test_num_ctx_order_model_then_deployment_then_user_then_default():
    lim = InferenceLimits(num_ctx_max=32768, keep_alive_max_s=None)
    ms = replace(model_settings.from_env({}), context_size=8192, context_sizes={"ollama:qwen3:8b": 12288})
    assert model_settings.num_ctx_for(ms, "ollama:qwen3:8b", local=True, user_value=4096, limits=lim) == 12288
    assert model_settings.num_ctx_for(ms, "ollama:gemma4:e4b", local=True, user_value=4096, limits=lim) == 8192
    unset = model_settings.from_env({})
    assert model_settings.num_ctx_for(unset, "ollama:gemma4:e4b", local=True, user_value=4096, limits=lim) == 4096
    assert model_settings.num_ctx_for(unset, "ollama:gemma4:e4b", local=True, user_value=None, limits=lim) == 16384

def test_num_ctx_is_clamped_and_non_local_passes_the_user_value():
    lim = InferenceLimits(num_ctx_max=8192, keep_alive_max_s=None)
    unset = model_settings.from_env({})
    assert model_settings.num_ctx_for(unset, "m", local=True, user_value=None, limits=lim) == 8192
    assert model_settings.num_ctx_for(unset, "m", local=False, user_value=4096, limits=lim) == 4096
    assert model_settings.num_ctx_for(unset, "m", local=False, user_value=None, limits=lim) is None

def test_latest_suffix_does_not_matter():
    ms = replace(model_settings.from_env({}), context_sizes={"ollama:gemma4:e4b": 12288})
    assert model_settings.lookup(ms.context_sizes, "ollama:gemma4:e4b:latest") == 12288
    ms2 = replace(ms, context_sizes={"ollama:gemma4:e4b:latest": 9000})
    assert model_settings.lookup(ms2.context_sizes, "ollama:gemma4:e4b") == 9000

@pytest.mark.parametrize("url,name,override,local", [
    ("http://localhost:11434", "gemma4:e4b", {}, True),
    ("http://127.0.0.1:11434", "gemma4:31b-cloud", {}, False),     # cloud on any host
    ("http://192.168.1.20:11434", "qwen3:8b", {}, True),
    ("http://ollama:11434", "qwen3:8b", {}, True),                  # single-label service name
    ("https://openrouter.ai/api/v1", "x/y", {}, False),
    ("http://gpu.example.com:11434", "qwen3:8b", {"gpu": "local"}, True),
    ("http://localhost:11434", "qwen3:8b", {"ollama": "cloud"}, False),
])
def test_is_local(url, name, override, local):
    cfg = ProviderConfig(name="gpu" if "gpu" in url else "ollama", kind="ollama", url=url, models=None)
    assert model_settings.is_local(cfg, name, override) is local

def test_declared_window_console_then_provider_env():
    ms = replace(model_settings.from_env({}), context_windows={"vllm:qwen3": 40960})
    env = {"INFERENCE_VLLM_CONTEXT_WINDOW": "32768"}
    assert model_settings.declared_window(ms, "vllm:qwen3", "vllm", env) == (40960, "admin")
    assert model_settings.declared_window(ms, "vllm:other", "vllm", env) == (32768, "env")
    assert model_settings.declared_window(ms, "ollama:x", "ollama", env) == (None, "none")

def test_offered_narrows_and_a_stale_selection_offers_nothing():
    models = [_info("ollama:a"), _info("ollama:b")]
    assert model_settings.offered(models, model_settings.from_env({})) == models
    pick = replace(model_settings.from_env({}), chat_models=("ollama:b:latest",))
    assert [m.id for m in model_settings.offered(models, pick)] == ["ollama:b"]
    gone = replace(model_settings.from_env({}), chat_models=("ollama:zzz",))
    assert model_settings.offered(models, gone) == []

@pytest.mark.parametrize("name,value", [
    ("context_size", 1024), ("context_size", "big"), ("context_sizes", {"m": 0}),
    ("context_windows", {"m": 10}), ("provider_locality", {"ollama": "nearby"}),
    ("chat_models", "ollama:a"), ("reader", ""),
])
async def test_save_refuses_bad_values(db_conn, name, value):
    with pytest.raises(console_settings.SettingError):
        await console_settings.save(db_conn, model_settings.SECTION, {name: value}, updated_by="admin")
```

In `test_admin_settings.py`, add `test_models_section_round_trips`: `GET /v1/admin/settings/models` returns all six settings and the two info rows; a `PUT` of `{"context_size": 12288}` returns `source: "admin"`. Copy the shape of the existing deployment and documents tests in that file.

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest server/tests/test_model_settings.py server/tests/test_admin_settings.py -q` (with the scratch `TEST_*` env set)
Expected: FAIL, because `server.services.model_settings` doesn't exist yet.

- [ ] **Step 3: Implement `model_settings.py`** with the interfaces above. The parse rules and refusal copy:
  - `reader`: `"chat"`, or a string of 1 to 200 characters with no whitespace. Refusal: "Choose the chat model, or a model from the list."
  - `context_size`, and each value in `context_sizes`: an int from 2,048 to 2,097,152. Refusal: "Must be a whole number of tokens from 2,048 to 2,097,152." The env var `INFERENCE_LOCAL_NUM_CTX` parses the same way.
  - `context_windows` values: an int from 1,024 to 10,000,000. Refusal: "Must be a whole number of tokens from 1,024 to 10,000,000."
  - Map keys and `chat_models` entries: 1 to 200 characters, at most 500 entries; `chat_models` is de-duplicated and kept in order.
  - `provider_locality` values: `"local"` or `"cloud"`. Refusal: "Must be local or cloud."
  - Store the maps sorted by key, so the console's JSON comparison sees a stable order.

  For the reader's three-variable chain, `_fallback` reads one env name: pass `{**env, "READER_MODEL": <first set of the chain>}` into `console_settings`, and record `reader_env` from `chat.reader.reader_model_source(env)`. `view()` output for `reader` must report `env` as that variable, or `READER_MODEL` when none is set. Add `"models": (model_settings.SECTION, model_settings.info_rows)` to `_SECTIONS`.

- [ ] **Step 4: Run the tests** (same command). Expected: PASS. Then run the whole backend suite. Expected: no new failures.

- [ ] **Step 5: Commit**: `feat(admin): the models settings section and its resolution rules`

---

### Task 2: One context size per local model in a chat turn, and trimming to it

**Files:**
- Modify: `server/chat/orchestrator.py` (`run_turn` start, `_window`, new `_models_settings`)
- Test: `server/tests/test_chat_orchestrator.py`

**Interfaces:**
- Consumes: Task 1's `load_models_settings`, `from_env`, `is_local`, `num_ctx_for`, `declared_window`; `inference_limits.get_limits`; `router.resolve(model_id) -> (Provider, name)`.
- Produces:
  - `async def _models_settings() -> ModelsSettings`, which is fail-open like `_chat_options`: any exception logs a WARNING and returns `model_settings.from_env()`.
  - The turn's effective `CallSettings`: `dataclasses.replace(req.settings, num_ctx=num_ctx_for(...))`, used for every `stream_chat` call of the turn and for `_window`.
  - `_window(router, req, caps, ms)`: `num_ctx` when the provider kind is `ollama` and a size is sent; else `caps.context_window`; else `declared_window(...)[0]`; else None.
  - Task 3 receives `ms` and the resolved settings through the `ToolContext` it builds.

- [ ] **Step 1: Write the failing tests**, using the file's existing fake router and turn helpers:
  - `test_local_ollama_turn_sends_16384_without_any_setting`: no console row, a local Ollama model, the person sends no `num_ctx`. Every `stream_chat` call's `settings.num_ctx == 16384`, and the context is built with `window == 16384`.
  - `test_admin_size_beats_personal_value_and_unset_keeps_it`: row `{"context_size": 8192}` with a person's 4096 sends 8192. With no row, the same person's 4096 sends 4096.
  - `test_cloud_model_keeps_the_personal_value`: for `ollama:gemma4:31b-cloud` with the person's 4096, it sends 4096; with none, it sends None.
  - `test_openai_model_trims_to_a_declared_window`: an `openai`-kind model whose capabilities report no window, row `{"context_windows": {"vllm:m": 9000}}`. The context is built with `window == 9000`.
  - `test_unreadable_models_row_falls_back_to_env`: make `get_pool().connection()` raise inside `_models_settings`. The turn completes, using `INFERENCE_LOCAL_NUM_CTX` from a monkeypatched env (e.g. 6144).

- [ ] **Step 2: Run them and see them fail**: `.venv/bin/python -m pytest server/tests/test_chat_orchestrator.py -q -k "16384 or admin_size or cloud_model_keeps or declared_window or unreadable_models"`. Expected: FAIL.

- [ ] **Step 3: Implement it.** Read `_models_settings()` once near the existing `_chat_options()` read. Resolve the provider with `router.resolve` (an `UnknownModel` is impossible here, because the route already resolved it). Compute `local = is_local(provider.config, name, ms.provider_locality)` only when `provider.config.kind == "ollama"`, and `False` otherwise. Replace the turn's settings once; don't mutate `req`.

- [ ] **Step 4: Run** the file, then the whole backend suite. Expected: PASS, with no new failures. Existing tests that asserted "no `num_ctx` when the user sends none" for a local Ollama model now expect 16384: update them, and say so in the report.

- [ ] **Step 5: Commit**: `fix(chat): one context size per local Ollama model, and trim history to it`

---

### Task 3: The reader falls back to the chat model, is chosen in the console, and is sized to its model

**Files:**
- Modify:
  - `server/chat/reader.py` (`DEFAULT_MODEL` removed, `ReaderConfig.num_ctx`, `fit_input_chars`, `read_batch` sends `cfg.num_ctx`);
  - `server/chat/tools/__init__.py` (`ToolContext.reader_cfg`);
  - `server/chat/tools/extract_from_documents.py`, `server/chat/tools/read_web_pages.py`;
  - `server/chat/orchestrator.py` (builds `reader_cfg`);
  - `server/services/model_router.py` (`summarize_model` default becomes `"chat"`);
  - `server/routers/admin.py` (`/inference/config` shows the resolved reader).
- Test: `server/tests/test_chat_reader.py`, `server/tests/test_chat_orchestrator.py`, the two tools' test files.

**Interfaces:**
- Consumes: Task 1 (`ms.reader`, `num_ctx_for`, `is_local`); Task 2's resolved chat settings (`num_ctx` already decided for the chat model).
- Produces:
  - `ReaderConfig.num_ctx: int | None = None`, the size every reader call of the turn sends (None: send none);
  - `def fit_input_chars(cfg: ReaderConfig, question: str, need: str) -> int`: `cfg.input_chars` when `cfg.num_ctx` is None, else the largest batch text that keeps one reader call's estimate within `cfg.num_ctx`, floored at 500;
  - `ToolContext.reader_cfg: ReaderConfig | None = None` (tools use `ctx.reader_cfg or get_reader_config()`).
  - The orchestrator builds `reader_cfg` once per turn:
    - `model`: `ms.reader` resolved with `resolve_model(cfg, req.model_id)`, so `"chat"` becomes the turn's model;
    - `num_ctx`: the turn's chat `num_ctx` when the reader model is the chat model; else `num_ctx_for(ms, reader_id, local=..., user_value=None, limits)` for a local Ollama reader; else None.

- [ ] **Step 1: Write the failing tests**:
  - `test_reader_defaults_to_the_chat_model`: `load_reader_config({}).model == "chat"`, and `model_router.load_inference_config({}).summarize_model == "chat"`.
  - `test_read_batch_sends_the_configured_size_never_a_per_batch_one`: with `cfg.num_ctx=16384`, two batches of very different sizes both send `num_ctx == 16384`; with `cfg.num_ctx=None`, the settings carry `num_ctx is None`.
  - `test_fit_input_chars_keeps_every_batch_within_the_window`: for `num_ctx` in (2048, 4096, 16384), with a 300-character question and need, every batch from `batches(passages, fit_input_chars(...))` passes `ceil(chars / CHARS_PER_TOKEN) + NUM_CTX_MARGIN + ceil(output_chars / CHARS_PER_OUTPUT_TOKEN) <= num_ctx`. Here `chars` is the length of `reader_messages(...)` for that batch, with passages of mixed lengths and many short ones (the label overhead).
  - `test_turn_reader_is_sized_to_its_own_model` (orchestrator):
    - reader = chat model (local) → the reader's `num_ctx` equals the chat's;
    - reader = a different local model, with the person's chat value 4096 and no admin size → 16384;
    - reader = chat, with the chat a cloud model → None.
  - `test_console_reader_wins_over_env`: row `{"reader": "ollama:gemma4:e4b"}` plus env `READER_MODEL=chat` → the tools' `ctx.reader_cfg.model == "ollama:gemma4:e4b"`.

- [ ] **Step 2: Run them and see them fail**: `.venv/bin/python -m pytest server/tests/test_chat_reader.py server/tests/test_chat_orchestrator.py -q -k "reader"`. Expected: FAIL.

- [ ] **Step 3: Implement it.** The two tools call `fit_input_chars(cfg, ctx.question, need)` where they pass `reader_cfg.input_chars` today (`extract_from_documents.py:303,309`, `read_web_pages.py:160`). Delete the per-batch `num_ctx` computation in `read_batch` (`reader.py:329-332`). Keep `num_predict` as it is. Update the module docstrings that name `llama3.2:3b` or a per-batch window.

- [ ] **Step 4: Run** the reader, orchestrator and tool test files, then the whole backend suite. Expected: PASS. Tests pinned to the old per-batch `num_ctx` or the `llama3.2:3b` default change; list them in the report.

- [ ] **Step 5: Commit**: `fix(reader): fall back to the chat model, choose it in the console, size it to its model`

---

### Task 4: Offer only the chat models the console allows, and tell the Settings window the context size

**Files:**
- Modify: `server/routers/inference.py`, `server/routers/chat_turns.py`
- Test: `server/tests/test_inference_router.py`, `server/tests/test_chat_turns.py`

**Interfaces:**
- Consumes: Task 1 (`load_models_settings`, `offered`, `is_local`, `deployment_num_ctx`, `DEFAULT_LOCAL_NUM_CTX`), and `inference_limits.clamp`.
- Produces:
  - `GET /v1/inference/models` lists `offered(models, ms)`. Each **local** Ollama model also carries `"context_size": {"fixed": int | None, "auto": int}`, where:
    - `fixed` is `deployment_num_ctx(...)[0]`, set when the admin or the env decided it;
    - `auto` is `DEFAULT_LOCAL_NUM_CTX` clamped to the ceiling.
  - Non-local models carry no `context_size`.
  - The turn route refuses a model outside a non-empty console selection with the existing `refusal(422, "model_not_allowed", "That model isn't available on this server.")`, after `is_allowed` and before `begin_turn`.

- [ ] **Step 1: Write the failing tests**:
  - `test_models_list_follows_the_console_selection` (empty means the env list; a selection narrows; a stale selection gives `[]`);
  - `test_local_ollama_model_reports_its_context_size` (no row: `{"fixed": None, "auto": 16384}`; row `{"context_size": 8192}`: `fixed == 8192`; a ceiling of 4096: `auto == 4096`);
  - `test_cloud_model_has_no_context_size_entry`;
  - `test_turn_refuses_a_model_the_console_left_out`: 422 `model_not_allowed`, and no turn row is written.

- [ ] **Step 2: Run them and see them fail**: `.venv/bin/python -m pytest server/tests/test_inference_router.py server/tests/test_chat_turns.py -q -k "console or context_size or left_out"`. Expected: FAIL.

- [ ] **Step 3: Implement it.** Both routes already hold a connection or can take one through `deps.get_conn`. Use the route's own connection, the way the budget lookup does in `inference.py`. Reading the row is fail-open: on an exception, use `from_env()`.

- [ ] **Step 4: Run** both files, then the whole backend suite. Expected: PASS.

- [ ] **Step 5: Commit**: `feat(models): the console narrows the chat models; the model list reports each local model's context size`

---

### Task 5: The admin's model catalog

**Files:**
- Modify: `server/routers/admin.py` (new `GET /v1/admin/models`)
- Test: `server/tests/test_admin_router.py`

**Interfaces:**
- Consumes: `get_router()` (`providers`, `list_models()`), `model_router.offered_chat_models` (removes the embedding model), Task 1 (`is_local`, `ms.provider_locality`), `deployment_info.url_host`.
- Produces: `GET /v1/admin/models` (admin only) returning:
  - `providers`: `[{name, kind, host, detected: "local"|"cloud", reachable: bool}]`, where `detected` applies R4 without the console's override, so the console can show "Auto (local)";
  - `models`: `[{id, provider, kind, name, reported_window: int|None, cloud_name: bool}]`, filtered by the env allow-lists and **not** by the console selection (the admin chooses from it);
  - `limits`: `{"num_ctx_max": int|None}`.

  An unreachable provider is listed with `reachable: false` and contributes no models. The whole call stays under `llm.list_models`'s own 5-second bound.

- [ ] **Step 1: Write the failing tests**:
  - `test_admin_models_catalog_shape`: two providers, one of them unreachable;
  - `test_admin_models_lists_models_the_console_left_out`;
  - `test_admin_models_needs_admin`: 403 for a non-admin principal.

- [ ] **Step 2: Run and see them fail**: `.venv/bin/python -m pytest server/tests/test_admin_router.py -q -k admin_models`. Expected: FAIL.

- [ ] **Step 3: Implement** the route next to `/overview`.

- [ ] **Step 4: Run** the file, then the whole backend suite. Expected: PASS.

- [ ] **Step 5: Commit**: `feat(admin): GET /v1/admin/models, the catalog the Models section chooses from`

---

### Task 6: Overview's Reader and Context-window lines read the new settings

**Files:**
- Modify: `server/services/console_overview.py` (`_TITLES`, `_reader`, `window_for`, `_context`, `run_checks`)
- Test: `server/tests/test_console_overview.py`

**Interfaces:**
- Consumes: Task 1 (`load_models_settings` in `run_checks`'s database phase, `offered`, `is_local`, `deployment_num_ctx`, `declared_window`), and the existing `project_turn_tokens`.
- Produces:
  - `_TITLES`: `providers`, `reader` and `context` now point to section `"models"` (providers are listed there, spec §3).
  - `window_for(model, loaded, ms, provider_cfg, env, limits) -> tuple[int | None, str]`. The source order follows spec §4.1. The source copy, verbatim:
    - loaded: `"Loaded now (Ollama)."`;
    - a local Ollama model not loaded, with a size set: `"The context size set for this model in Models."`, `"The deployment's context size, set in Models."` or `"The deployment's context size, from INFERENCE_LOCAL_NUM_CTX."`;
    - unset: `"16,384 tokens unless a person sets their own in Settings (no deployment size is set)."`. The window is 16,384 clamped;
    - cloud Ollama: `"The model's maximum (Ollama cloud)."`;
    - reported: `"Reported by the provider."`;
    - declared: `"Declared in Models."` or `"From INFERENCE_<NAME>_CONTEXT_WINDOW."` (the real name);
    - none: `"Unknown: the provider doesn't report one. Declare it in Models."`.
  - `_reader(llm, ms, models)`:
    - `"chat"` from the default: **ok**, `"The chat model of each conversation (the default)."`. When any offered model is not local, append `" Reader calls then count against a cloud model's quota; a small local reader, chosen in Models, keeps them off it."` (R5);
    - `"chat"` set here or by env: ok, `"The chat model of each conversation."` plus the same hint;
    - a named model that isn't listed: error, `"<name> isn't on <provider>: every reader call fails until it's pulled or another reader is chosen in Models."`;
    - unreachable: today's copy;
    - listed: ok, `"<name>, set in Models."` or `"<name>, from <VAR>."`.
  - `_context`: a non-empty selection that leaves nothing gives **warn** `"None of the chat models chosen in Models is listed by a provider: nobody can chat."`

- [ ] **Step 1: Write the failing tests**: one per copy line above, plus `test_context_applies_the_console_selection`. Use the file's existing fakes.

- [ ] **Step 2: Run and see them fail**: `.venv/bin/python -m pytest server/tests/test_console_overview.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement it.** The `models` row is read in `run_checks`'s database phase, in turn with the other reads, before any bounded check (phase 1 ruling P2). The checks receive the `ModelsSettings` value, never `conn`.

- [ ] **Step 4: Run** the file, then the whole backend suite. Expected: PASS.

- [ ] **Step 5: Commit**: `feat(admin): Overview's reader and context lines follow the Models settings`

---

### Task 7: The Models section

**Files:**
- Create: `src/components/admin/ModelsSection.jsx`, `src/components/admin/ModelsSection.test.jsx`
- Modify:
  - `src/components/admin/AdminConsole.jsx`: `SECTIONS` gains `{ id: 'models', label: 'Models' }` after Projects (spec §3 order); render it; report dirty like Documents;
  - `src/components/admin/OverviewSection.jsx`: its section links already go by `check.section`; check that `models` resolves;
  - `src/components/admin/phoneTargets.test.jsx`: add the section to its sweep.

**Interfaces:**
- Consumes: `useSectionSettings(apiHost, apiPort, 'models')`, `SettingRow`, `SectionSaveBar`, Task 5's `GET /v1/admin/models`, Task 1's setting names.
- Produces: the section, in this order:
  1. **Reader**: a select whose first option is `"The chat model of each conversation"` (value `chat`), followed by every catalog model id. Help: "The model that reads documents and web pages for the chat model."
  2. **Context size for local Ollama models**: a number input; empty means unset. Help: "One size for every request to a local Ollama model, so it isn't reloaded. Empty: each person's own setting, else 16,384." Show the ceiling: "At most N (`INFERENCE_NUM_CTX_MAX`)."
  3. **Per-model sizes**: one number input per catalog model that is local Ollama under R4 with the current locality drafts; empty uses the deployment size. One `SettingRow` for the whole map (`group`).
  4. **Declared context windows**: one number input per model whose `reported_window` is null; empty means unknown. Help: "What history is trimmed to when the provider doesn't say."
  5. **Providers**: per provider, a select of `Auto (local)`/`Auto (cloud)` (from `detected`), `Local` and `Cloud`; Auto removes the key. Each row shows the kind and host. An unreachable one shows "Can't be reached."
  6. **Chat models offered**: a checkbox per catalog model. None ticked means "Every model the server allows." Help: "People can chat only with the ticked models. None ticked: every model the server allows."

  Map and list values are drafted whole through `s.set(name, newMap)`. A per-model number that is invalid while typed keeps Save off, using the `pagesText` pattern from `DocumentsSection`, keyed by model. Save toast: "Model settings saved. They apply from the next message."

- [ ] **Step 1: Write the failing tests** in `ModelsSection.test.jsx`, mocking `apiFetch` like `DocumentsSection.test.jsx`:
  - it renders the six rows from a settings payload and a catalog;
  - choosing a reader and saving PUTs `{changes: {reader: 'ollama:gemma4:e4b'}}`;
  - typing a per-model size PUTs `context_sizes` with only that key added;
  - clearing it removes the key;
  - an invalid size (`100`) disables Save and shows "Must be a whole number of tokens from 2,048 to 2,097,152.";
  - setting a provider to Cloud removes its models from the per-model sizes list before saving;
  - ticking no chat models shows "Every model the server allows.";
  - a catalog that fails to load shows "Couldn't load the models." while the settings rows still render;
  - each control has an accessible name, and the Save bar announces refusals with `role="alert"`.

  Also, in `AdminConsole.test.jsx`, a Models tab exists between Projects and Documents.

- [ ] **Step 2: Run and see them fail**: `npx vitest run src/components/admin/ModelsSection.test.jsx src/components/admin/AdminConsole.test.jsx`. Expected: FAIL.

- [ ] **Step 3: Implement** the section after `DocumentsSection.jsx`'s structure and classes.

- [ ] **Step 4: Run** `npx vitest run` and `npx eslint src`. Expected: everything passes and lint is clean.

- [ ] **Step 5: Commit**: `feat(admin): the Models section`

---

### Task 8: The Settings window shows the context size the server will use

**Files:**
- Modify: `src/lib/inferenceLimits.js` (parse `context_size`), `src/components/settings/SettingsDialog.jsx` (the Context window row)
- Test: the existing `SettingsDialog` test file for the inference rows (find it with `grep -l "Context window" src/components/settings/*.test.jsx`), and `src/lib/inferenceLimits.test.js` if one exists, else create it.

**Interfaces:**
- Consumes: Task 4's `context_size: {fixed, auto}` on local Ollama models.
- Produces:
  - With `fixed` set, the Context window row is disabled and reads `"Set by your admin: 16,384 tokens."` (the number formatted with `toLocaleString('en-US')`). The person's stored value is kept, not cleared, because it applies again if the admin unsets the size.
  - Without `fixed`, the `Auto` option's label is `"Auto (16,384 on this server)"`, using `auto`.
  - A model without `context_size` (cloud, or `openai`) is unchanged.

- [ ] **Step 1: Write the failing tests** for the three cases.
- [ ] **Step 2: Run and see them fail**: `npx vitest run src/components/settings src/lib`.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run** `npx vitest run` and `npx eslint src`. Expected: everything passes.
- [ ] **Step 5: Commit**: `feat(settings): show the context size the server will use for a local model`

---

### Task 9: Search and filter People (owner request, 2026-10-26)

People loads every user in one list (`users.list_users`, `ORDER BY created_at`), which stops being usable as members grow. The search runs on the server, so a large list is never sent whole.

**Files:**
- Modify: `server/auth/users.py` (new `search_users`), `server/routers/admin.py` (`GET /v1/admin/users` takes query parameters), `src/components/admin/PeopleSection.jsx`
- Test: `server/tests/test_admin_router.py`, `src/components/admin/AdminConsole.test.jsx`

**Interfaces:**
- Produces:
  - `async def search_users(conn, *, q: str | None, status: str | None, capability: str | None, limit: int | None, offset: int) -> tuple[list[dict], int]`, returning the rows and the total number of matches.
  - `q` matches case-insensitively, as a substring, on `email`, `username`, `display_name`, `first_name`, `last_name`, and on `first_name || ' ' || last_name` (so "Ada Lovelace" finds her). `%`, `_` and `\` in `q` are escaped: they match literally.
  - `status` is one of `active|pending|disabled`; `capability` is one of `KNOWN_CAPABILITIES`. Order: `created_at`, then `id`.
  - `GET /v1/admin/users?q=&status=&capability=&limit=&offset=`. **With no parameters, the response is unchanged:** every user, as a JSON list, because `AdminPanel.jsx` and existing tests read it that way. With any parameter it is still a list, plus an `X-Total-Count` header carrying the total.
  - Limits: `limit` 1–200, default 50 once any parameter is given; `offset` ≥ 0. A bad value is a 422. `q` has a maximum of 200 characters.
- People UI:
  - a search box (label "Search people", placeholder "Name, email or username") that searches 300 ms after typing stops;
  - a status select (All, Active, Pending, Disabled) and a capability select (Any, reader, chat, admin, docling);
  - the count line "Showing N of T";
  - a **Show more** button that loads the next 50 with `offset`, shown while N < T;
  - an empty result reads "No one matches.";
  - every request after an edit (PATCH, DELETE, enroll) reloads with the same search;
  - only the newest search's answer is shown; an older one arriving late is dropped.

- [ ] **Step 1: Write the failing tests**:
  - `test_admin_users_search_matches_name_email_username_and_full_name`;
  - `test_admin_users_search_escapes_like_wildcards` (`q="%"` matches only an email containing `%`);
  - `test_admin_users_filters_status_and_capability`;
  - `test_admin_users_pages_with_total_header`;
  - `test_admin_users_without_params_is_unchanged` (a list, no header);
  - `test_admin_users_rejects_bad_params` (422);
  - frontend: typing in the search box sends one request with `q` after the debounce (fake timers); the status filter sends `status=pending`; Show more appends the next page; "No one matches." appears for an empty result; a late answer to an older search doesn't overwrite a newer one.
- [ ] **Step 2: Run and see them fail**: `.venv/bin/python -m pytest server/tests/test_admin_router.py -q -k admin_users` and `npx vitest run src/components/admin/AdminConsole.test.jsx`.
- [ ] **Step 3: Implement it.** The SQL uses parameters only (no string-built `WHERE` from input); `ILIKE ... ESCAPE '\'` (one backslash: Postgres takes a single escape character).
- [ ] **Step 4: Run** both whole suites and `npx eslint src`. Expected: everything passes.
- [ ] **Step 5: Commit**: `feat(admin): search and filter People on the server`

---

### Task 10: Docs

**Files:**
- Modify:
  - `.env.example`: `INFERENCE_LOCAL_NUM_CTX`, `INFERENCE_<NAME>_CONTEXT_WINDOW`; `READER_MODEL`'s comment, which now says the default is the chat model;
  - `CHANGELOG.md` `## [Unreleased]`: an Added entry for the Models section, Fixed entries for the three bugs, and the Upgrade notes below;
  - `docs/USER_GUIDE.md` § Admin console: a Models subsection, People's search, and the Settings window note;
  - `docs/ARCHITECTURE.md`: `model_settings`, `GET /v1/admin/models`, and the per-turn resolution;
  - `docs/TESTING.md`: the phase 2a tests, the new gate counts, and the `TEST_DATABASE_URL` warning (unset means 5433);
  - `docs/CHAT_WITH_PDF.md`: the context-size and reload paragraphs;
  - `HANDOVER.md`: a new top entry.

- **Upgrade notes, verbatim in substance:**
  - **The reader moves to the chat model** on a deployment that sets none of `READER_MODEL`, `SUMMARIZE_MODEL` or `WEB_SEARCH_SUMMARY_MODEL`. It used to be `llama3.2:3b`. With a cloud chat model, reader calls count against its quota; choose a local reader under Models to keep them off it.
  - **Local Ollama models now get 16,384 tokens** instead of Ollama's own 4,096 (on under 24 GiB of VRAM), unless a person or the admin sets a size. A larger context reserves more memory when the model loads. Size it down under Models, or with `INFERENCE_LOCAL_NUM_CTX`, on a machine short of RAM.
  - **A person's own context size stops applying to local Ollama models** once the admin sets one.

- [ ] **Step 1:** Write the docs. Every statement about what a person can do names the control (Admin → Models → …).
- [ ] **Step 2:** `grep -rn "llama3.2:3b" docs .env.example README.md` → no line still says it's the reader's default.
- [ ] **Step 3: Commit**: `docs: admin console v2 phase 2a`

---

### Task 11: Walk it in the running app

Same setup as phase 1's walk (Task 13a there):
- the scratch DB on 5434;
- the backend on **8100** with `AUTH_ENABLED=false`;
- a scratch Vite config pointing at 8100;
- headless Chromium;
- the local Ollama at its usual URL.

No commits beyond a walk report in the workspace. Fixes found here go back through the task loop.

- [ ] **Step 1: The reload bug, end to end.** Turn on reader-via-chat (Models → Reader → "The chat model of each conversation") with a local chat model. Run a document chat turn that calls `extract_from_documents`.
  - **Before it:** record `/api/ps`.
  - **After it:** `/api/ps` lists the model once, with `context_length` 16,384.
  - **The journal:** Ollama's log shows no reload between the chat step and the reader call. If it isn't readable, compare `/api/ps` `expires_at` before and after.
- [ ] **Step 2: Trimming.** The same turn's context was built with window 16,384. Check it with the orchestrator's existing debug log line, or a test hook, without logging message text.
- [ ] **Step 3: Each Models row.**
  - Set and save each one, reload the page, then use "Use the server setting".
  - Narrow the chat models to one: the chat picker shows only that one, and a request for another is refused (curl on 8100).
  - Set a per-model size: Overview's Context windows line shows "The context size set for this model in Models."
- [ ] **Step 4: Settings window.** With the deployment size set, the Context window row reads "Set by your admin: …" and is disabled. Unset, it reads "Auto (16,384 on this server)".
- [ ] **Step 5: People search.** Enroll about 60 scratch users (a loop of `POST /v1/admin/users` on 8100). Then search by first and last name, filter Pending, and use Show more until the count line reads "Showing 60 of 60".
- [ ] **Step 6: Phone width** (375 px). The Models section's and People search's controls are at least 44 px tall. Measure in the browser, not only by class.
- [ ] **Step 7:** Write `task-11-walk-report.md` in the workspace: each step, the control used, what was seen, and what wasn't walked and why.

**Not part of this plan:**
- The spec §8 measurements (e4b as the reader, reload cost, whether 16,384 holds). The owner decides when.
- The cloud eval that spec §5 requires before a **release**. It needs the owner's go-ahead, and runs on `ollama:gemma4:31b-cloud` or OpenRouter, never local `llama3.2:3b`.
