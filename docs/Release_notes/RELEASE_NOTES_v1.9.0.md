# v1.9.0 — Live web search, per-model inference controls, and real logging

This release makes the chat model genuinely web-aware, gives you fine-grained
control over how each model is called, and finally writes logs to a file. It also
adds the project's first `LICENSE` and a contributor guide.

## Added

### 🔎 Web search (`web_search` tool)

The chat model can now search the **live web** through a self-hosted **SearXNG**
instance. The frontend tool was scaffolded in 1.8.3 but had no backend; this
release builds the whole pipeline:

1. Query SearXNG's JSON API for the top results.
2. For each result, **fetch the page, extract the readable text** (trafilatura,
   nav/boilerplate stripped), and **summarize it with a small model**
   (`llama3.2:3b` by default) — fanned out in parallel, bounded by a semaphore
   exactly like the embedding pipeline.
3. Return **per-result summaries** with source URLs; the capable chat model you
   picked does the final synthesis and citation.

It's engineered for the messy realities of the open web:

- **Synchronous, not a job.** A tool call can't poll, so the endpoint does all the
  work in one request (unlike document indexing, which is fire-and-forget).
- **SSRF-guarded.** Only `http`/`https` to hosts that resolve to **public** IPs are
  fetched, and **every redirect hop is re-checked** — a malicious result can't
  point the fetcher at `localhost`, your LAN, or a cloud metadata endpoint
  (`169.254.169.254`).
- **Graceful degradation.** A page that times out, is paywalled, or is blocked
  falls back to its search snippet instead of disappearing, so you always get one
  entry per result.

SearXNG ships as a `docker-compose` service bound to **`127.0.0.1:18043`**
(localhost-only — it's unauthenticated) and configured **API-only**. `./startup.sh up`
bootstraps its config on first run. Configure via `SEARXNG_URL` and the
`WEB_SEARCH_*` env vars (see `.env.example`).

### 🎛️ Per-model Ollama inference settings

A new **Inference** block in the chat sidebar exposes four request parameters —
**context window** (`num_ctx`), **keep-alive**, **thinking level**, and **max reply
tokens** (`num_predict`) — stored **per model name**, because a 9.7B and a 3B want
different context sizes on the same machine. Thinking is now five states
(`Off`/`On`/`Low`/`Medium`/`High`); a model that rejects a graduated level is
retried once with plain thinking. Every setting defaults to **unset**, so nothing
changes until you opt in.

### 📊 Context meter + truncation warning

A `~3.3k / 16k ctx` estimate sits above the composer (amber past 75% of the
window), and a reply cut off by the context limit now raises a toast naming the
real token counts instead of failing silently.

### 📋 File logging

The backend now logs to **both the console and a size-rotating `logs/server.log`**
(10 MB × 5 backups), and — importantly — wires up the app's own loggers so `INFO`
messages actually surface. Before this, no logging was configured at all: `INFO`
was swallowed and nothing was ever written to disk. Tune with `LOG_LEVEL`,
`LOG_DIR`, `LOG_FILE_MAX_MB`, `LOG_FILE_BACKUPS`; `logs/` is gitignored.

### 📄 License & contributing

The project now has an **MIT `LICENSE`** (it had none) and a **`CONTRIBUTING.md`**
covering setup, the test/lint commands, branch and commit conventions, and the
design-first workflow for larger features.

## Fixed

- **Silently truncated replies.** The chat engine never sent an `options` object,
  so Ollama applied its own default context window — a real case hit prompt 3317 +
  generation 779 = exactly 4096. The window is now configurable and exhaustion is
  surfaced, on both the initial response and the (larger) tool follow-up.

## Upgrade notes

- **New Python dependency: `trafilatura`** (plus dev deps `pytest`,
  `pytest-asyncio`, `respx`). Run `./startup.sh init` or
  `.venv/bin/pip install -r requirements.txt`.
- **Web search is optional.** To enable it: `docker-compose up -d searxng` (or
  `./startup.sh up`, which also bootstraps `searxng/settings.yml`) and
  `ollama pull llama3.2:3b`. Verify JSON works:
  `curl -s "http://localhost:18043/search?q=test&format=json"` — a **403** means
  SearXNG's JSON format is disabled. Without SearXNG configured, the rest of the app
  is unaffected.
- **No schema changes.** No new migrations; existing chat sessions and documents
  are untouched.
- **`package.json` realigned** from `1.8.0` to `1.9.0` (it had lagged behind the
  `v1.8.x` tags).

## Full changelog

[CHANGELOG.md#190---2026-08-10](../CHANGELOG.md#190---2026-08-10) · diff:
[v1.8.3…v1.9.0](https://github.com/udaravima/natural-reader/compare/v1.8.3...v1.9.0)
