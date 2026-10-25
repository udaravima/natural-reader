# Admin console v2: run the deployment from the console, not from `.env`

**Status:** proposal, revision 2 (2026-10-25). Not approved yet. Written after v2.4.0 from the owner's notes: the admin panel needs more flexibility and should be as friendly as the Settings window; the timezone should be set there; "we're trapped on the embedding model and llama3.2:3b". Revision 2 folds in a review of revision 1. Its four must-fix findings were checked against this repository and against Ollama's source (`server/routes.go` `EmbedHandler`, `server/sched.go` `needsReload`).

## 1. Why

Chat is not locked to one engine. It goes through a gateway with two provider kinds (`server/llm/router.py:32`): `ollama`, which is Ollama's own chat API, and `openai`, any server speaking the OpenAI chat format. vLLM, llama.cpp, Unsloth Studio, OpenRouter and OpenAI can be chat providers today through `INFERENCE_PROVIDERS`. The locks are elsewhere. Everything that decides answer quality, cost or load is set in environment variables, which need shell access and a restart:

| What the admin wants to change | Where it lives today | What goes wrong |
|---|---|---|
| **The embedding engine** | `services/embeddings.py`: Ollama's old `/api/embeddings` at `OLLAMA_URL`, one text per call | A deployment without Ollama can't index a single document, so it has no document search. With Ollama, indexing makes one HTTP call per chunk. |
| **The embedding model** | `EMBEDDING_MODEL` (env), column `vector(768)` (`sql/001_init.sql`) | Only 768-dimension models fit, and switching needs a restart. After a switch, every document reads as "reindexing" and isn't searched until its own rebuild finishes (`services/doc_pipeline.py:371-375`), which starts only when someone next uses it. |
| **The reader model** (`extract_from_documents`, `read_web_pages`, web summaries) | `READER_MODEL`, `SUMMARIZE_MODEL` (env); fallback `llama3.2:3b` (`services/model_router.py:63`, `chat/reader.py:42`) | A deployment that sets neither runs its reader on a 3B model: slow on a CPU, and failing on every call where that model isn't pulled. A bare model name resolves only when some Ollama provider exists, so without one every reader call fails. The console shows the value read-only. |
| **The context size of local Ollama models** | Not set by the app unless a user sets one in Settings | Ollama gives a local model **4k tokens** on a machine with under 24 GiB of VRAM (docs.ollama.com/context-length), so 4k always on this CPU-only machine. A project-chat turn passes that: about 2k tokens of rules and tool specs, the map, up to 24,000 characters (~6k tokens) of tool results, and 2,048 tokens kept for the reply. With no `num_ctx` set, the app trims history against the model's *maximum* context (`chat/orchestrator.py:684-689`, e.g. 131k), so it never trims and Ollama cuts the prompt silently. Cloud models get their maximum context (verified: a 19k-token prompt to `gemma4:31b-cloud` with no `num_ctx` kept its first line). |
| **Model reloads** | The reader sends a context size computed per batch (`chat/reader.py:320-322`) | Ollama reloads a loaded model whenever a request's options, `num_ctx` included, differ from the ones it was loaded with. The only exception is when both let Ollama choose the size (`sched.go` `needsReload`). With `READER_MODEL=chat` on a local model, as `.env.example` recommends for CPU machines, Ollama reloads the model whenever chat and reader calls alternate. |
| **The context window of `openai`-kind models** | Only from `/models` (`llm/providers/openai_compat.py:109`) | OpenAI's own API reports none, and llama.cpp or Unsloth may not either. History is then never trimmed to fit. |
| **Docling's options** | Each user picks them per conversion; `DOCLING_ENABLED` turns everything on or off | An admin can't stop "describe pictures" (a vision model per image) or OCR on a 600-page scan from tying up the machine. |
| **The timezone** | Each browser, for the chat's "Today is …" line only | The daily token budget resets at UTC midnight (05:30 in Colombo), and usage is grouped by UTC day. |
| **Model usage** | `inference_usage`: per user per day, no model column | There is no way to see what each model used, cloud or local, or how much of it was the reader. |

The console is also hard to use: one long scroll of seven sections in very small text (`AdminConsole.jsx`: "one scroll, no tabs"). The Settings window (v2.4) has a section list, keyboard navigation and a phone layout.

## 2. Goals, non-goals, anti-goals

### Goals

1. Every setting that decides quality, cost or load can be changed in the console and applies without a restart, on every worker.
2. Every setting shows where its value comes from ("Set here" / "From the server setting `VAR`" / "The default") and can be reset to the server setting. Chat options already works this way; this design makes it the rule.
3. The console has the Settings window's shape: sections in a list on the left (a picker at the top on a phone), one section at a time, keyboard navigable, 44 px targets on a phone.
4. An **Overview** section says what is wrong before anyone asks: a missing model, a context window too small for project chat, documents waiting to be rebuilt, Docling or web search unavailable.
5. A deployment with no Ollama at all, only vLLM, llama.cpp, Unsloth, OpenRouter or OpenAI, can index and search documents.
6. Changing the embedding model never makes a document unsearchable.
7. Usage by model, cloud vs local, chat vs reader.

### Non-goals (this design)

- Editing secrets in the console: API keys, the OIDC client secret, `SESSION_SECRET`. They stay in the environment, never shown.
- Adding providers or editing their URLs in the console. A URL an admin can set is a URL the server will call (an SSRF surface), and a wrong one locks the console out of its own models. **The effect:** adding a vLLM or Unsloth server still needs env access and a restart, and the console chooses among the providers already configured. A later path, as LibreChat does it: URLs set in the UI, checked against an SSRF block list plus an explicit `host:port` allow-list.
- Embedding vectors wider than 768. That needs a column type change (`halfvec`, which pgvector indexes up to 4,000 dimensions) and a full re-embed; a later design.
- Money cost. OpenRouter reports `usage.cost` per call, Ollama Cloud doesn't. Tokens first.
- Per-group or per-project policies.
- LiteLLM as a dependency. The two provider kinds already cover every target; a deployer can run LiteLLM in front and point an `openai`-kind provider at it (one line in the docs).

### Anti-goals: it fails even if it works, if…

- a setting changed in the console silently stops applying because an env var wins. The console's value always wins, and the env var is shown as the fallback.
- changing the embedding model makes documents vanish from search, even with a warning. The new vectors are built before the switch (§4.2.4).
- the console widens what the deployer allowed: a model allow-list that caps an OpenRouter bill, or `DOCLING_ENABLED=false`. The console can only narrow what the environment allows.
- the console becomes a second `.env` editor. Only settings an admin can change safely, with a clear effect, belong here.

## 3. Shape

A full-height view, as today, opened from the header (the console holds wide tables, so not a modal). Left: the section list. Right: the section. On a phone: a section picker at the top, the section below.

| Section | Contains | New? |
|---|---|---|
| **Overview** | Health checks and warnings (§4.1), each linking to the section that fixes it | New |
| **People** | Users: list, enroll, role and capabilities, daily budget, disable, delete | Moved |
| **Projects** | All projects | Moved |
| **Models** | Providers (read-only, with their local/cloud flag), chat models offered, context windows, the reader, the embedding provider and model (§4.2) | New, absorbs "Deployment config" |
| **Documents** | Docling limits (§4.3); upload size read-only (it is the proxy's) | New |
| **Chat** | Web search mode, assistant profile | Moved (Chat options + Assistant) |
| **Defaults for users** | As today | Moved |
| **Usage** | By day, user, model, cloud/local, chat/reader (§4.4) | Rebuilt |
| **Deployment** | Timezone (§4.5); env-only values, read-only, with where each came from | New |

### One row pattern for every setting

Every setting is one row:

- its label, and one line of help saying what it changes;
- the control;
- the source: "Set here", "From the server setting `READER_MODEL`", or "The default";
- **Use the server setting**, when the value was set here.

A section has one **Save** for its changes, and leaving a section with unsaved changes asks first. Every save writes an audit line: who, key, before, after, never a secret.

Storage is the existing `app_settings` table, one row per section key (`models`, `documents`, `deployment`, and the existing `chat_options` and `assistant_profile`). Each is read with one primary-key read where it's used, so a change applies from the next request on every worker. Precedence everywhere: console row, then env var, then built-in default. For allow-lists, the console's list is intersected with the env's, never unioned.

## 4. The sections that are new

### 4.1 Overview

The checks run when the section opens, each with a short timeout. Each is one line with a state and a link:

- **Providers:** reachable, and how many models each lists. This exists today in Deployment config.
- **Reader:** what it resolves to, and whether that model is reachable. Warn when it is still the fallback. Hint when the chat model is a cloud model and no local reader is chosen: a small local reader keeps reader calls off the cloud quota. This machine's owner reports `gemma4:e4b` faster than `llama3.2:3b`; not measured yet, §8.
- **Context windows:** every chat model offered, with the window it gets and where that number came from:
  - Ollama's `/api/ps` for a loaded model;
  - the deployment's context size;
  - the provider's `/models`;
  - a declared window;
  - or "unknown".

  The check compares each window with a typical project-chat turn here (rules, the largest project's map, the tool-result budget), in tokens. It warns when a window is smaller, and also when it is unknown, because history can't then be trimmed to fit.
- **Embeddings:** the provider and model, reachable, giving 768 trained dimensions. Shows the progress of a rebuild in progress, read from the database (§4.2.4).
- **Docling:** installed and enabled, or not.
- **Web search:** SearXNG reachable, or not.

### 4.2 Models

#### 4.2.1 Providers, chat models and context windows

- **Providers:** read-only, as configured by env. Each has a **local/cloud** flag, used by Usage and by the reader hint:
  - by default, a provider whose host is loopback or a private address is local (LLMRouter's rule);
  - an Ollama model whose name ends in `-cloud` is cloud on any host;
  - the console can override the flag per provider.
- **Chat models offered:** the providers' models as checkboxes, narrowing the env allow-list (`INFERENCE_MODELS`, or `INFERENCE_<NAME>_MODELS` per provider when `INFERENCE_PROVIDERS` is set). An empty console selection means "the env list", never "everything".
- **Declared context window per model:** used when the provider reports none. Set in the console, or with `INFERENCE_<NAME>_CONTEXT_WINDOW` as the env default for a provider's models. It is what history is trimmed to.
- **Context size for local Ollama models:** one size per model, the same on every request to it, chat and reader alike. Ollama then never reloads a model because two callers asked for different sizes.
  - **Default:** 16,384 tokens, capped by the existing ceiling (`INFERENCE_NUM_CTX_MAX`, default 32,768). It can be changed per model.
  - **The reader:** sends the model's size, never a per-batch value. A batch that would need more is split into smaller batches instead.
  - **History trimming:** uses the size actually sent, which fixes the never-trims case in §1.
  - **A user's own Settings value:** applies only while the admin leaves the deployment's context size unset. That keeps today's behaviour for deployments that don't use the setting. The cost: once it is set, a user's personal context size stops applying to local Ollama models.

#### 4.2.2 The reader

"The chat model of the conversation", or one named model from the providers' lists. When nothing is set in the console, today's env chain applies: `READER_MODEL`, then `SUMMARIZE_MODEL`, then `WEB_SEARCH_SUMMARY_MODEL`. When none of those is set either, the reader is **the chat model**, replacing the `llama3.2:3b` fallback. That is what the evals measured, and it works on a deployment without Ollama. The cost: reader calls bill the chat model, which for a cloud model counts against its quota. Overview's hint (§4.1) points to a local reader for that case.

#### 4.2.3 The embedding provider

`EMBEDDING_PROVIDER` names one of the `INFERENCE_PROVIDERS` and reuses its URL and key. The console can choose among the configured providers. The default is the Ollama provider at `OLLAMA_URL`, so nothing changes for existing deployments.

- `ollama` kind: `POST {url}/api/embed`. `openai` kind: `POST {base}/embeddings`.
- Both take an `input` array, so texts go in batches (about 32 per call) instead of one call per chunk.
- Both take `dimensions`, sent only when the probe found it needed (§4.2.4).
- The query and document prefixes (`EMBEDDING_*_PREFIX`) follow the model's known defaults, as today, and stay env-overridable.

#### 4.2.4 Changing the embedding model: probe, then build, then switch

**The probe.** It never trusts the length of a returned vector. Ollama cuts any model's vector to the requested `dimensions` and renormalizes it, refusing only when the model declares its trained sizes. So a 1024-dimension model (`bge-m3`, `mxbai-embed-large`) asked for 768 returns 768 numbers that search noticeably worse, with no error. vLLM refuses such a request with a 400. The probe runs in two steps:

1. Embed one sentence **without** `dimensions`, to learn the model's native size.
2. Accept the model when:
   - its native size is 768; or
   - it declares 768 as a trained size; or
   - it belongs to a known shortenable family: `qwen3-embedding`, `embeddinggemma`, `nomic-embed-text` v1.5, `text-embedding-3-*`.

   Anything else is refused, and the refusal shows the native size and why.

**Build, then switch,** for a change of model only, where the chunk rows stay the same:

1. Saving the new model doesn't switch anything. It starts a migration: a second column, `doc_chunks.embedding_next vector(768)`, with its own HNSW index, is filled by a background job using the new model.
2. Meanwhile, queries keep using the current model and column, so every document stays searchable. Both models must stay reachable until the switch.
3. When every chunk has a vector, one transaction makes the new model current, moves the vectors into `embedding`, and drops `embedding_next` and its index. Overview shows progress, and the admin can cancel before the switch, which drops the column.
4. The cost: one extra column and index for the duration of the migration.

A **change of chunker** (`CHUNKER_VERSION`, part of the rebuild key) produces new chunk rows, which a second column on the old rows can't hold. It keeps today's per-document swap, under the queue below.

**The rebuild queue lives in the database.** Today's `_rebuilding` set and `_failed` dict are per-process memory (`doc_pipeline.py:354-358`). With `WORKERS > 1`, Overview can't see another worker's progress, two workers can rebuild the same document, and a restart loses the queue. Instead, each document has a rebuild state row (target profile, state, attempts, error, claimed at). Workers claim rows with `FOR UPDATE SKIP LOCKED`, one document at a time per worker. A restart resumes, and Overview reads the progress from the same rows. The existing first-use rebuild (`ensure_current`) enqueues there too.

### 4.3 Documents (Docling limits)

What a conversion may use. `POST /v1/docs/{id}/convert` enforces it, and a refusal says which option the admin turned off. The convert dialog greys disallowed options out with "Turned off by your admin".

- **Who may convert:** everyone with the reader capability (the default, as today), or admins only.
- **Presets allowed:** fast / standard / accurate.
- **OCR allowed:** yes/no.
- **Picture description allowed:** yes/no. It runs a vision model per picture.
- **Largest conversion:** at most N pages per conversion (0 = no limit). A longer PDF can still be converted by page range.

`DOCLING_ENABLED` stays the env switch for whether Docling exists at all; the console can only narrow it.

### 4.4 Usage

`inference_usage` gains the model and the kind of call (a migration). Its primary key becomes `(user_id, day, model, kind)`, with kind `chat` or `reader`. Rows written before the migration get model `''`, shown as "unknown", because a key column can't be NULL.

- **The daily budget check** sums all of a user's rows for the day.
- **The section** shows tokens and requests by day, by user and by model. Each model is tagged cloud or local by its provider's flag (§4.2.1), with the share that was estimated (from a provider that sent no counts).
- **Date range:** 1-90 days, as today.

### 4.5 Deployment

- **Timezone:** an IANA name, validated against the server's zone database. The default is UTC, with env fallback `TZ`.
  - **What it decides:** when a day's token budget resets; the days in Usage; the times shown in the console; the chat's "Today is …" line when the browser sends no timezone. A user's own browser timezone still names their day in chat.
  - **Past rows don't move:** rows already written stay in the UTC days they were counted in.
  - **The switch day:** changing the zone moves the current day's boundary once, so a user can get a fresh budget a few hours early or late. The help line says so.
- **Read-only:** each env-only value the console doesn't edit, with where it came from: provider URLs, storage folder, `DOCLING_ENABLED`, the SearXNG host, workers. Secrets show only "set" or "not set".

## 5. Constraints

- **Open source, many deployers:** no value specific to one deployment, in code or docs (docs use example.com). Every new setting has a safe default and an env fallback.
- **No new Ollama-only paths:** anything that talks to a model works for both provider kinds, or says plainly which kind it needs.
- **Per request, never cached per process:** settings are read on every request, and job state lives in the database (`WORKERS > 1`).
- **Admin routes:** all `/v1/admin/*` routes require the admin capability, server-side; the UI only hides.
- **Accessibility:** the Settings window's rules carry over: tablist semantics, arrow keys, focus return, Escape, 44 px targets on a phone.
- **Measured before release:** changing a model, the context size or the reader changes answers, so Models changes are checked with the cloud eval first, as the tool wording was.

## 6. Phases (each one releasable)

1. **The shell and the cheap settings:**
   - the section list and the row pattern;
   - the existing sections moved in unchanged;
   - Overview, with the checks that need no new settings;
   - Deployment (timezone and the read-only list);
   - Documents (Docling limits).
2. **Models.** Two releasable parts:
   - **a.** The reader falls back to the chat model, and the reader is chosen in the console. One context size per local Ollama model, with the reader sized to it and trimming fixed. Declared context windows, providers' local/cloud flag, and chat models offered (narrowing only). These fix today's bugs in §1 (silent truncation, reloads, no trimming without a window), so this part comes first.
   - **b.** The embedding provider, the two-step probe, build-then-switch, and the database rebuild queue. These share one machinery.
3. **Usage by model:** the migration, writers for chat and reader calls, and the new Usage section.

## 7. Decisions (recommended; the owner confirms at review)

1. **When no reader model is set anywhere:** the chat model (§4.2.2). The owner's direction is to make the reader choosable; this is only the fallback.
2. **Context size for local Ollama models:** 16,384 by default, one size per model (§4.2.1). The rule matters more than the number: without it, a fixed default still reloads the model on every reader call.
3. **Who may convert with Docling by default:** everyone with the reader capability, as today. Changing the default would surprise existing deployments, and an admin can still narrow it.

## 8. Measure later (after phase 2a, on this machine)

None of these change what gets built. They tune recommendations:

- `gemma4:e4b` as the reader against the cloud reader: facts found and seconds per batch. The owner reports e4b faster than `llama3.2:3b`; no reader figure exists for it yet.
- One reload of a local model: what the reload churn costs a project-chat turn before phase 2a.
- Whether 16,384 holds a project-chat turn here, or the default should differ.
- How long build-then-switch takes on a CPU for a library this size.

## 9. Left to the builder

- the section components' internal structure;
- caching Overview's checks for the few seconds the section is open;
- the exact copy of each help line: plain, one line, saying what changes;
- the batch size for embeddings (start around 32);
- the rebuild state table's exact columns;
- the list of known shortenable embedding families, kept in one place in code with a comment saying where each entry was checked.

## 10. Not verified

- **Other engines:** nothing here was run against a live vLLM, llama.cpp or Unsloth server. Their API details come from the review's reading of their docs and source, which I didn't re-check.
- **Unsloth's model list:** whether its `/v1/models` lists models that aren't loaded (a `loaded` field) is unverified. If it does, the picker should drop the unloaded ones.
- **Unsloth's tools:** its own python and bash tools run only when a request asks for them, which this app never does. Keep it bound to localhost anyway.
- **Anthropic's and Google's** OpenAI-compatible endpoints weren't examined; both are reachable through OpenRouter.

## Sources

Checked for this design (2026-10-25):

- Ollama context defaults by VRAM; cloud models at their maximum: <https://docs.ollama.com/context-length>
- Ollama `/api/embed` parameters, `dimensions` and `truncate`: <https://docs.ollama.com/api/embed>
- Ollama truncates any vector to `dimensions` unless the model declares its trained sizes (`EmbedHandler`): <https://github.com/ollama/ollama/blob/main/server/routes.go>
- Ollama reloads a model when options differ, unless both requests let Ollama choose the context size (`needsReload`): <https://github.com/ollama/ollama/blob/main/server/sched.go>
- `embeddinggemma-2` (768 native; MRL 128, 256, 512): <https://ollama.com/library/embeddinggemma-2>
- `qwen3-embedding` (0.6b, 4b, 8b; 8b outputs 32-4,096): <https://ollama.com/library/qwen3-embedding>
- pgvector `halfvec` HNSW up to 4,000 dimensions: <https://www.dbi-services.com/blog/pgvector-a-guide-for-dba-part-2-indexes-update-march-2026/>

From the review, not re-checked here:

- vLLM online serving and embedding models: <https://docs.vllm.ai/en/latest/serving/online_serving/>, <https://docs.vllm.ai/en/latest/models/pooling_models/embed/>
- llama.cpp server: <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>
- Unsloth API: <https://unsloth.ai/docs/basics/api>
- OpenRouter embeddings: <https://openrouter.ai/docs/api/reference/embeddings.md>
