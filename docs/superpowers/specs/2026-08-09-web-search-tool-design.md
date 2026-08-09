# Web search tool (`web_search`) — design

**Date:** 2026-08-09
**Status:** Approved (spec review passed) — ready for implementation planning
**Area:** Chat tools (frontend `chatTools/webSearch.js`) + backend (new `services/web_search.py`, new `routers/tools.py`) + `docker-compose.yml` (add SearXNG)

## Problem

The frontend tool [`chatTools/webSearch.js`](../../../src/lib/chatTools/webSearch.js)
is already filled in and registered in the
[REGISTRY](../../../src/lib/chatTools/index.js), so the chat model is *already*
told a `web_search` tool exists. But it POSTs to `/v1/tools/web_search`, and that
endpoint does not exist — there is no `/v1/tools/` router at all. Every time a
model decides to use the tool today, it gets an HTTP error back. The frontend half
is done; the whole feature is the backend.

Separately, the backend has never made a *generative* Ollama call. It only ever
hits `/api/embeddings` ([`services/embeddings.py`](../../../server/services/embeddings.py)).
This feature introduces the first backend `/api/generate` path — the
"summarize with a small model" step is genuinely new infrastructure, not a copy of
something already present.

## Goal

Give the chat model a working `web_search` tool that, on the backend:

1. Searches the public web via a **self-hosted SearXNG** instance (JSON API).
2. For each of the top results, **fetches the full page, extracts readable text,
   and summarizes it with a small "simple" model** (llama), fanned out in parallel
   under a bounded semaphore — mirroring the indexing embed fan-out.
3. Returns **per-result summaries** (`{title, url, summary}`). The capable chat
   model the user selected does the final synthesis and citation — the small model
   only condenses individual pages.

## Non-goals (YAGNI)

- **No backend final-synthesis pass.** The backend returns per-result summaries;
  it does not merge them into one answer. The main chat model owns the reasoning
  and source-to-claim mapping.
- **No result caching / dedupe store** across queries. (Future: a short-TTL cache
  keyed on query.)
- **No async job + poll.** Unlike indexing, this is a synchronous request/response
  — see "Sync, not a job" below.
- **No hosted search API / API keys.** SearXNG only.
- **No robots.txt fetching or crawl politeness beyond timeouts/size caps.** Single
  page per result, no link-following.

## Sync, not a job — why the indexing analogy is half true

Indexing is fire-and-forget: [`POST /v1/docs/{id}/index`](../../../server/routers/docs.py)
returns `202` immediately and the UI polls `GET /v1/docs/{id}` for progress. That
works because a human clicked a button and can wait.

A **tool call cannot do that** — the chat model is blocked mid-turn waiting for the
tool's return value, and there is no polling loop inside `execute()`. So
`/v1/tools/web_search` must be **synchronous**: do all the work, return when done,
within a bounded total time.

What *is* copied from indexing is the **service internals**: a module-level httpx
client with `start_client`/`stop_client` wired into the app lifecycle, a
`Semaphore` bounding parallel Ollama calls, and env-driven config — exactly like
[`services/embeddings.py`](../../../server/services/embeddings.py). Same engine,
different transmission.

## Architecture — three new pieces, no churn to existing code

```
webSearch.js (exists) ──POST /v1/tools/web_search──▶ routers/tools.py (NEW)
                                                          │
                                                          ▼
                                                services/web_search.py (NEW)
                                       ┌──────────────────┼───────────────────┐
                                       ▼                  ▼                   ▼
                              SearXNG /search      fetch each URL       Ollama /api/generate
                               ?format=json      + trafilatura extract   (llama, per result)
                                       │                                      │
                                       └────────── Semaphore(N) fan-out ──────┘
                                                          │
                              return [{title,url,summary,source}] ──▶ chat model synthesizes
```

- **`docker-compose.yml`** — add a `searxng` service beside `postgres`, same
  conventions (`container_name`, `restart: unless-stopped`, healthcheck, a pinned
  image). Ship a `searxng/settings.yml` that **enables the JSON format** (see
  gotcha below) and a `secret_key`.
- **`server/services/web_search.py`** (new) — twin of `embeddings.py`:
  `start_client()` / `stop_client()` wired into [`app.py`](../../../server/app.py)'s
  startup/shutdown; a module-level `httpx.AsyncClient`; a `Semaphore`; env config.
  Functions:
  - `searxng_search(query, count) -> list[dict]` — GET SearXNG `/search`, return
    top `count` `{title, url, snippet}`.
  - `fetch_and_extract(url) -> str | None` — SSRF-guarded GET → trafilatura →
    plain text, truncated to `WEB_SEARCH_MAX_PAGE_CHARS`. `None` on failure.
  - `summarize_one(query, text) -> str` — Ollama `/api/generate` (`stream:false`)
    with a query-focused summarization prompt.
  - `web_search(query, count) -> dict` — orchestrator: search, then fan out
    fetch+summarize under the semaphore, assemble results.
- **`server/routers/tools.py`** (new) — `APIRouter(prefix="/v1/tools")` with one
  `POST /web_search`, registered in `app.py` alongside the other routers.

## Data flow (one request)

1. Tool POSTs `{query, count}` to `/v1/tools/web_search`.
2. `searxng_search(query, count)` → SearXNG `/search?q=…&format=json`, take top
   `count` results (title, url, snippet).
3. Fan out under `Semaphore(WEB_SEARCH_MAX_CONCURRENCY)`: per result,
   `fetch_and_extract(url)` then `summarize_one(query, text)`.
4. **Graceful per-result degradation:** a page that fails (timeout, paywall,
   SSRF-blocked, empty extract) **falls back to its SearXNG snippet** as the
   summary rather than being dropped — the model still gets something for every
   source. Mirrors `embed_batch` returning `None` and skipping instead of aborting
   the whole batch. `source` is tagged `"page"` or `"snippet"` so the caller knows.
5. Return `{query, results: [{title, url, summary, source}]}`. The chat model does
   final synthesis + citations.

## Config (new `.env.example` block, same commented style as Ollama/embeddings)

| Var | Default | Controls |
|---|---|---|
| `SEARXNG_URL` | `http://localhost:8080` | SearXNG base URL |
| `WEB_SEARCH_SUMMARY_MODEL` | `llama3.2:3b` | the "simple model" for per-page summaries |
| `WEB_SEARCH_RESULT_COUNT` | `5` (cap 10) | fan-out width — dominates latency |
| `WEB_SEARCH_MAX_CONCURRENCY` | `4` | parallel fetch+summarize jobs — matches embeddings' `Semaphore(4)` |
| `WEB_SEARCH_FETCH_TIMEOUT_S` | `8` | per-page fetch timeout |
| `WEB_SEARCH_SUMMARY_TIMEOUT_S` | `30` | per-summary Ollama timeout |
| `WEB_SEARCH_MAX_PAGE_CHARS` | `6000` | truncate extracted page text before summarizing (llama context guard) |

`OLLAMA_URL` is reused (same env the embeddings service reads) — the summarizer
talks to the same Ollama the embedder does.

## Error handling & safety

- **SSRF guard — non-negotiable.** The URLs fetched are chosen by a search engine,
  i.e. attacker-influenceable. Before each fetch, resolve the host and **reject
  loopback / private / link-local ranges** (`127.0.0.0/8`, `10.0.0.0/8`,
  `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` — that last covers the cloud
  metadata endpoint `169.254.169.254`), plus IPv6 loopback/ULA/link-local. Allow
  only `http`/`https` schemes, cap redirects, and cap the response body size.
  Without this, "summarize this result" becomes "read my internal network." A
  blocked URL degrades to its snippet (step 4), it is not a hard error.
- **SearXNG JSON gotcha:** SearXNG returns **HTTP 403 for `format=json` unless it
  is explicitly enabled** in `settings.yml` (`search: formats: [html, json]`). The
  compose service must ship that config or every search fails. Flag loudly in the
  README/setup notes.
- **Graceful degradation:** SearXNG unreachable → the endpoint returns
  `{error: "..."}` (HTTP 502), which the tool surfaces to the model per the
  [`index.js`](../../../src/lib/chatTools/index.js) contract so it can recover.
  Individual page failure → snippet fallback, never a hard fail.
- **Small frontend fix:** [`webSearch.js`](../../../src/lib/chatTools/webSearch.js)
  currently `await fetch(...)`es *before* validating `query` / `count` — it fires a
  request even for an empty query. Move validation ahead of the fetch so an empty
  query returns `{error}` without a round-trip.

## Testing

**Reality check:** the backend has **no Python test harness today** — no pytest,
no `conftest.py`, no tests for the embeddings service, and pytest is not in
`requirements.txt`. There is nothing to "mirror." The frontend, by contrast, has
a mature Vitest suite. So the testing choice is a real decision, flagged for the
review gate:

- **Recommended:** establish a **minimal pytest harness** for the security- and
  logic-critical *pure* functions — above all the **SSRF guard**, which needs no
  network and must not ship untested. This adds `pytest` + `pytest-asyncio` +
  `respx` (httpx mock) as dev deps and a `server/tests/` dir with `conftest.py`.
  - `test_web_search.py`:
    - `searxng_search` with mocked httpx — parses results, respects `count`.
    - **SSRF guard** — private/loopback/link-local hosts rejected
      (`127.0.0.1`, `169.254.169.254`, `10.x`, IPv6 `::1`), public allowed.
    - Snippet-fallback — a failing `fetch_and_extract` yields a result whose
      `summary` is the snippet and `source == "snippet"`.
    - Truncation to `WEB_SEARCH_MAX_PAGE_CHARS`; semaphore bound respected.
  - `test_tools_router.py`: `/v1/tools/web_search` happy path, SearXNG-down →
    502, empty-query → 400 — network mocked.
- **Minimum, if we decline a backend harness:** keep the SSRF guard a pure
  function and cover it however the user prefers; rely on the frontend test +
  manual verification for the rest. (Not recommended — leaves the SSRF guard and
  network paths unverified.)
- **Frontend (Vitest), regardless:** `webSearch.js` returns `{error}` for empty
  query / bad `count` **without** issuing a fetch (validation-before-fetch
  regression guard).

## Files touched (estimate)

- `docker-compose.yml` — add `searxng` service (+ `searxng/settings.yml`, new).
- `server/services/web_search.py` — new service (search, fetch+extract, summarize,
  orchestrate) + lifecycle client.
- `server/routers/tools.py` — new `/v1/tools` router with `POST /web_search`.
- `server/app.py` — register the tools router; start/stop the web-search client.
- `server/tests/` — new pytest dir (`conftest.py`, `test_web_search.py`,
  `test_tools_router.py`) **if** the backend harness is adopted (see Testing).
- `requirements.txt` — add `trafilatura` (httpx + fastapi already present); add
  `pytest` + `pytest-asyncio` + `respx` if the backend harness is adopted.
- `.env.example` — new web-search config block.
- `src/lib/chatTools/webSearch.js` — validate before fetch (+ a small Vitest).
- `README.md` — SearXNG setup + the `format: json` gotcha.
