# Web Search Tool (`web_search`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the already-registered `web_search` chat tool actually work by building its backend: SearXNG search → per-result full-page fetch + text extraction + small-model summary, fanned out in parallel, returning per-result summaries the chat model synthesizes.

**Architecture:** A new `server/services/web_search.py` (twin of `embeddings.py`: module-level httpx client with `start_client`/`stop_client` in the app lifecycle, a `Semaphore` bounding parallel Ollama calls, env-driven config) does the work. A new `server/routers/tools.py` exposes `POST /v1/tools/web_search` **synchronously** (a tool call can't poll like indexing does). SearXNG runs as a second docker-compose service.

**Tech Stack:** Python/FastAPI, httpx (async), trafilatura (HTML→text), Ollama `/api/generate` (llama summary), SearXNG (self-hosted JSON search), pytest+respx (new backend test harness), React/Vitest (frontend tool).

## Global Constraints

- **Synchronous endpoint.** No 202/job/poll — the tool call blocks on the result. Do all work in one request.
- **Per-result summaries only.** The backend returns `{title, url, summary, source}` per result; it does **not** merge them. The chat model synthesizes.
- **SearXNG only**, JSON API. SearXNG returns **HTTP 403 for `format=json` unless enabled** in `searxng/settings.yml` (`search.formats` includes `json`).
- **SSRF guard is mandatory.** Only `http`/`https`, and only hosts resolving entirely to **public** IPs. Re-check every redirect hop. Blocked URL → degrade to snippet, never a hard error.
- **`count` cap is 10** everywhere (frontend tool definition, frontend validation, backend Pydantic `le=10`, service clamp).
- **Backend virtualenv is `.venv`** (per `startup.sh` `VENV_DIR=".venv"`). Install all new Python deps there.
- **New Python deps:** `trafilatura`, `pytest`, `pytest-asyncio`, `respx`. Reuse `OLLAMA_URL` (same env the embeddings service reads).
- **Mirror `server/services/embeddings.py`** structure for the service (client lifecycle, semaphore, env config, graceful per-item failure like `embed_batch`).
- **Commits require the user's explicit per-action approval.** Each task ends with a commit step, but STOP and ask before running `git commit` — do not commit automatically.

---

### Task 1: Backend pytest harness + SSRF guard

Establishes the backend's first Python test harness (there is none today) and the security-critical, network-free SSRF guard.

**Files:**
- Create: `pytest.ini`
- Create: `server/services/web_search.py`
- Create: `server/tests/test_web_search.py`
- Modify: `requirements.txt` (add pytest deps)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Module-level config constants in `web_search.py`: `OLLAMA_URL`, `SEARXNG_URL`, `SUMMARY_MODEL`, `RESULT_COUNT` (int, default 5), `RESULT_COUNT_CAP` (int, 10), `MAX_CONCURRENCY`, `FETCH_TIMEOUT_S`, `SUMMARY_TIMEOUT_S`, `MAX_PAGE_CHARS`, `MAX_RESPONSE_BYTES`.
  - `is_url_fetchable(url: str) -> bool` — SSRF guard.

- [ ] **Step 1: Add pytest deps to `requirements.txt`**

Append under the existing deps:

```
# Backend test harness (dev). respx mocks httpx; pytest-asyncio runs async tests.
pytest
pytest-asyncio
respx
```

- [ ] **Step 2: Install the deps into the backend venv**

Run: `.venv/bin/pip install pytest pytest-asyncio respx`
Expected: installs cleanly.

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
pythonpath = .
asyncio_mode = auto
testpaths = server/tests
```

- [ ] **Step 4: Write the failing SSRF-guard test**

Create `server/tests/test_web_search.py`:

```python
import pytest
from server.services import web_search as ws


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://10.0.0.5/x",
    "http://172.16.0.1/x",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data",  # cloud metadata endpoint
    "http://[::1]/x",
    "ftp://example.com/x",                        # bad scheme
    "notaurl",
])
def test_ssrf_guard_blocks_private_loopback_and_bad_scheme(url):
    assert ws.is_url_fetchable(url) is False


@pytest.mark.parametrize("url", [
    "http://93.184.216.34/",
    "https://93.184.216.34/some/path",
])
def test_ssrf_guard_allows_public_ip(url):
    assert ws.is_url_fetchable(url) is True
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `.venv/bin/pytest server/tests/test_web_search.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (no `web_search` module / no `is_url_fetchable`).

- [ ] **Step 6: Create `server/services/web_search.py` with config + guard**

```python
"""
Web search tool backend — SearXNG search + per-page fetch/extract + small-model
summary.

Mirrors services/embeddings.py: a module-level httpx.AsyncClient with
start_client()/stop_client() wired into the app lifecycle, a Semaphore bounding
the parallel Ollama calls, and env-driven config. Unlike indexing this runs
synchronously (a tool call can't poll), so web_search() does all the work in one
request and returns per-result summaries — the chat model does final synthesis.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080").rstrip("/")
SUMMARY_MODEL = os.environ.get("WEB_SEARCH_SUMMARY_MODEL", "llama3.2:3b")
RESULT_COUNT = int(os.environ.get("WEB_SEARCH_RESULT_COUNT", "5"))
RESULT_COUNT_CAP = 10
MAX_CONCURRENCY = int(os.environ.get("WEB_SEARCH_MAX_CONCURRENCY", "4"))
FETCH_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_FETCH_TIMEOUT_S", "8"))
SUMMARY_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_SUMMARY_TIMEOUT_S", "30"))
MAX_PAGE_CHARS = int(os.environ.get("WEB_SEARCH_MAX_PAGE_CHARS", "6000"))
MAX_RESPONSE_BYTES = int(os.environ.get("WEB_SEARCH_MAX_RESPONSE_BYTES", "2000000"))

_UA = "Mozilla/5.0 (compatible; NaturalReaderBot/1.0)"


def _is_ip_public(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def is_url_fetchable(url: str) -> bool:
    """SSRF guard: True only for http/https whose host resolves entirely to
    public IPs. Rejects loopback/private/link-local/reserved/multicast and any
    non-http(s) scheme. DNS is resolved so a public hostname pointing at a
    private IP is still blocked."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            if not _is_ip_public(ip_str):
                return False
        except ValueError:
            return False
    return True
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `.venv/bin/pytest server/tests/test_web_search.py -v`
Expected: PASS (both parametrized tests).

- [ ] **Step 8: Commit** (ask the user first)

```bash
git add requirements.txt pytest.ini server/services/web_search.py server/tests/test_web_search.py
git commit -m "feat(web-search): backend pytest harness + SSRF guard"
```

---

### Task 2: SearXNG search client + lifecycle wiring

**Files:**
- Modify: `server/services/web_search.py` (add client lifecycle + `searxng_search`)
- Modify: `server/app.py:13,42,52` (import + start/stop in lifecycle)
- Modify: `server/tests/test_web_search.py` (add search test)

**Interfaces:**
- Consumes: config constants from Task 1.
- Produces:
  - `async start_client() -> None`, `async stop_client() -> None`, `_get_client() -> httpx.AsyncClient`.
  - `async searxng_search(query: str, count: int) -> list[dict]` — each dict `{"title": str, "url": str, "snippet": str}`.

- [ ] **Step 1: Write the failing search test**

Add to `server/tests/test_web_search.py`:

```python
import httpx
import respx


@respx.mock
async def test_searxng_search_parses_and_caps(monkeypatch):
    monkeypatch.setattr(ws, "SEARXNG_URL", "http://searx.test")
    route = respx.get("http://searx.test/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"title": "A", "url": "http://a.test", "content": "sa"},
            {"title": "B", "url": "http://b.test", "content": "sb"},
            {"title": "C", "url": "http://c.test", "content": "sc"},
        ]})
    )
    await ws.start_client()
    try:
        out = await ws.searxng_search("q", 2)
    finally:
        await ws.stop_client()
    assert route.called
    assert [r["url"] for r in out] == ["http://a.test", "http://b.test"]
    assert out[0] == {"title": "A", "url": "http://a.test", "snippet": "sa"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest server/tests/test_web_search.py::test_searxng_search_parses_and_caps -v`
Expected: FAIL — no `start_client`/`searxng_search`.

- [ ] **Step 3: Add the client lifecycle + `searxng_search`**

Append to `server/services/web_search.py`:

```python
_client: httpx.AsyncClient | None = None
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


async def start_client() -> None:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(headers={"User-Agent": _UA})


async def stop_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("Web-search HTTP client not started — call start_client() first")
    return _client


async def searxng_search(query: str, count: int) -> list[dict]:
    """Query SearXNG's JSON API and return up to `count` {title,url,snippet}."""
    client = _get_client()
    resp = await client.get(
        f"{SEARXNG_URL}/search",
        params={"q": query, "format": "json"},
        timeout=FETCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    out: list[dict] = []
    for r in (data.get("results") or [])[:count]:
        url = r.get("url") or ""
        if not url:
            continue
        out.append({
            "title": r.get("title") or url,
            "url": url,
            "snippet": r.get("content") or "",
        })
    return out
```

- [ ] **Step 4: Run it to verify it passes**

Run: `.venv/bin/pytest server/tests/test_web_search.py::test_searxng_search_parses_and_caps -v`
Expected: PASS.

- [ ] **Step 5: Wire the client into the app lifecycle**

In `server/app.py`, add the import next to the embeddings import (line ~13):

```python
from .services.web_search import start_client as start_web_search, stop_client as stop_web_search
```

In the `_startup` handler, after `await start_embeddings()`:

```python
        await start_web_search()
```

In the `_shutdown` handler, after `await stop_embeddings()`:

```python
        await stop_web_search()
```

- [ ] **Step 6: Verify the app still imports**

Run: `.venv/bin/python -c "from server.app import app; print('ok')"`
Expected: prints `ok` (no import error).

- [ ] **Step 7: Commit** (ask the user first)

```bash
git add server/services/web_search.py server/app.py server/tests/test_web_search.py
git commit -m "feat(web-search): SearXNG JSON search + client lifecycle"
```

---

### Task 3: Page fetch + extract (SSRF-guarded, redirect-safe)

**Files:**
- Modify: `server/services/web_search.py` (add `_safe_get`, `fetch_and_extract`)
- Modify: `requirements.txt` (add `trafilatura`)
- Modify: `server/tests/test_web_search.py` (add fetch/extract tests)

**Interfaces:**
- Consumes: `_get_client`, `is_url_fetchable`, `FETCH_TIMEOUT_S`, `MAX_RESPONSE_BYTES`, `MAX_PAGE_CHARS`.
- Produces: `async fetch_and_extract(url: str) -> str | None` — extracted, truncated page text, or `None` on block/failure/empty.

- [ ] **Step 1: Add `trafilatura` to `requirements.txt`**

Under the document-conversion section:

```
# HTML → clean text extraction for the web_search tool (strips nav/ads/boilerplate).
trafilatura
```

- [ ] **Step 2: Install it into the backend venv**

Run: `.venv/bin/pip install trafilatura`
Expected: installs cleanly.

- [ ] **Step 3: Write the failing fetch/extract tests**

Add to `server/tests/test_web_search.py`:

```python
@respx.mock
async def test_fetch_and_extract_returns_clean_text():
    html = (
        "<html><head><title>T</title></head><body><nav>home about</nav>"
        "<article><h1>Findings</h1>"
        "<p>The core measured value in this study is 42 across all trials. "
        "The experiment was repeated ten times with consistent results. "
        "Researchers concluded the effect is stable and reproducible.</p>"
        "</article></body></html>"
    )
    respx.get("http://pub.test/a").mock(return_value=httpx.Response(200, html=html))
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://pub.test/a")
    finally:
        await ws.stop_client()
    assert text is not None
    assert "42" in text
    assert "home about" not in text  # nav/boilerplate stripped


@respx.mock
async def test_fetch_and_extract_truncates_to_max_page_chars(monkeypatch):
    monkeypatch.setattr(ws, "MAX_PAGE_CHARS", 50)
    para = "This sentence provides ample readable content for extraction. " * 40
    html = f"<html><body><article><h1>Doc</h1><p>{para}</p></article></body></html>"
    respx.get("http://pub.test/big").mock(return_value=httpx.Response(200, html=html))
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://pub.test/big")
    finally:
        await ws.stop_client()
    assert text is not None
    assert len(text) == 50


@respx.mock
async def test_fetch_and_extract_blocks_redirect_to_private():
    respx.get("http://pub.test/redir").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://pub.test/redir")
    finally:
        await ws.stop_client()
    assert text is None


async def test_fetch_and_extract_blocks_private_url_without_request():
    # No respx route registered — if it tried to fetch, respx would raise.
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://127.0.0.1/secret")
    finally:
        await ws.stop_client()
    assert text is None
```

- [ ] **Step 4: Run them to verify they fail**

Run: `.venv/bin/pytest server/tests/test_web_search.py -k fetch_and_extract -v`
Expected: FAIL — no `fetch_and_extract`.

- [ ] **Step 5: Implement redirect-safe fetch + extract**

Append to `server/services/web_search.py`:

```python
async def _safe_get(url: str) -> httpx.Response | None:
    """GET with manual redirect handling — every hop is re-checked against the
    SSRF guard so a public URL can't 30x us onto a private address."""
    client = _get_client()
    current = url
    for _ in range(4):  # cap redirect hops
        if not is_url_fetchable(current):
            logger.warning("Blocked non-public URL: %s", current)
            return None
        try:
            resp = await client.get(
                current, timeout=FETCH_TIMEOUT_S, follow_redirects=False,
            )
        except httpx.HTTPError as e:
            logger.warning("Fetch failed for %s: %s", current, e)
            return None
        if resp.is_redirect:
            loc = resp.headers.get("location")
            if not loc:
                return None
            current = str(httpx.URL(current).join(loc))
            continue
        return resp
    return None


async def fetch_and_extract(url: str) -> str | None:
    """Fetch a page and return clean, truncated text — or None on block/failure."""
    resp = await _safe_get(url)
    if resp is None:
        return None
    try:
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Non-2xx for %s: %s", url, e)
        return None
    raw = resp.content[:MAX_RESPONSE_BYTES]
    html = raw.decode(resp.encoding or "utf-8", errors="ignore")
    import trafilatura
    # favor_recall keeps short pages from being dropped by trafilatura's
    # precision heuristic — we'd rather summarize a thin page than lose it.
    text = (trafilatura.extract(html, favor_recall=True) or "").strip()
    if not text:
        return None
    return text[:MAX_PAGE_CHARS]
```

- [ ] **Step 6: Run them to verify they pass**

Run: `.venv/bin/pytest server/tests/test_web_search.py -k fetch_and_extract -v`
Expected: PASS (all three).

- [ ] **Step 7: Commit** (ask the user first)

```bash
git add requirements.txt server/services/web_search.py server/tests/test_web_search.py
git commit -m "feat(web-search): redirect-safe page fetch + trafilatura extract"
```

---

### Task 4: Summarize one page via Ollama `/api/generate`

**Files:**
- Modify: `server/services/web_search.py` (add `summarize_one`)
- Modify: `server/tests/test_web_search.py` (add summary test)

**Interfaces:**
- Consumes: `_get_client`, `OLLAMA_URL`, `SUMMARY_MODEL`, `SUMMARY_TIMEOUT_S`.
- Produces: `async summarize_one(query: str, text: str) -> str` — the model's summary. Raises `httpx.HTTPError` on transport failure (the orchestrator catches it).

- [ ] **Step 1: Write the failing summary test**

Add to `server/tests/test_web_search.py`:

```python
@respx.mock
async def test_summarize_one_calls_ollama_generate(monkeypatch):
    monkeypatch.setattr(ws, "OLLAMA_URL", "http://ollama.test")
    route = respx.post("http://ollama.test/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "  A tidy summary.  "})
    )
    await ws.start_client()
    try:
        out = await ws.summarize_one("what is x", "long page text about x")
    finally:
        await ws.stop_client()
    assert route.called
    body = route.calls.last.request.content.decode()
    assert '"stream": false' in body or '"stream":false' in body
    assert out.strip() == "A tidy summary."
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest server/tests/test_web_search.py::test_summarize_one_calls_ollama_generate -v`
Expected: FAIL — no `summarize_one`.

- [ ] **Step 3: Implement `summarize_one`**

Append to `server/services/web_search.py`:

```python
_SUMMARY_PROMPT = (
    "You are summarizing a web page for someone researching: \"{query}\".\n"
    "Write 2-4 sentences capturing ONLY the information on the page relevant to "
    "that query. If the page has nothing relevant, say so in one sentence. Do "
    "not invent information that is not present.\n\nPAGE CONTENT:\n{text}"
)


async def summarize_one(query: str, text: str) -> str:
    """Summarize one page's text with the small model via Ollama /api/generate."""
    client = _get_client()
    resp = await client.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": SUMMARY_MODEL,
            "prompt": _SUMMARY_PROMPT.format(query=query, text=text),
            "stream": False,
        },
        timeout=SUMMARY_TIMEOUT_S,
    )
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `.venv/bin/pytest server/tests/test_web_search.py::test_summarize_one_calls_ollama_generate -v`
Expected: PASS.

- [ ] **Step 5: Commit** (ask the user first)

```bash
git add server/services/web_search.py server/tests/test_web_search.py
git commit -m "feat(web-search): per-page summary via Ollama /api/generate"
```

---

### Task 5: Orchestrator with semaphore fan-out + snippet fallback

**Files:**
- Modify: `server/services/web_search.py` (add `web_search`)
- Modify: `server/tests/test_web_search.py` (add orchestrator test)

**Interfaces:**
- Consumes: `searxng_search`, `fetch_and_extract`, `summarize_one`, `_semaphore`, `RESULT_COUNT_CAP`.
- Produces: `async web_search(query: str, count: int) -> dict` →
  `{"query": str, "results": [{"title": str, "url": str, "summary": str, "source": "page"|"snippet"}]}`.

- [ ] **Step 1: Write the failing orchestrator test**

Add to `server/tests/test_web_search.py`:

```python
async def test_web_search_fans_out_with_snippet_fallback(monkeypatch):
    async def fake_search(query, count):
        return [
            {"title": "T1", "url": "http://one.test", "snippet": "snip1"},
            {"title": "T2", "url": "http://two.test", "snippet": "snip2"},
        ]

    async def fake_fetch(url):
        return "page text" if url == "http://one.test" else None

    async def fake_summarize(query, text):
        return "SUMMARY"

    monkeypatch.setattr(ws, "searxng_search", fake_search)
    monkeypatch.setattr(ws, "fetch_and_extract", fake_fetch)
    monkeypatch.setattr(ws, "summarize_one", fake_summarize)

    out = await ws.web_search("q", 5)
    assert out["query"] == "q"
    assert len(out["results"]) == 2
    first, second = out["results"]
    assert first == {"title": "T1", "url": "http://one.test",
                     "summary": "SUMMARY", "source": "page"}
    assert second == {"title": "T2", "url": "http://two.test",
                      "summary": "snip2", "source": "snippet"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest server/tests/test_web_search.py::test_web_search_fans_out_with_snippet_fallback -v`
Expected: FAIL — no `web_search`.

- [ ] **Step 3: Implement `web_search`**

Append to `server/services/web_search.py`:

```python
async def web_search(query: str, count: int) -> dict:
    """Search, then fetch+summarize each result in parallel (semaphore-bounded).
    Per-result failures degrade to the SearXNG snippet rather than dropping —
    the caller always gets one entry per result."""
    count = max(1, min(RESULT_COUNT_CAP, count))
    hits = await searxng_search(query, count)

    async def _one(hit: dict) -> dict:
        async with _semaphore:
            summary = ""
            source = "snippet"
            text = await fetch_and_extract(hit["url"])
            if text:
                try:
                    summary = await summarize_one(query, text)
                    if summary:
                        source = "page"
                except Exception as e:  # noqa: BLE001 — degrade, don't abort the batch
                    logger.warning("Summary failed for %s: %s", hit["url"], e)
            if not summary:
                summary = hit.get("snippet") or "(no summary available)"
                source = "snippet"
            return {
                "title": hit["title"],
                "url": hit["url"],
                "summary": summary.strip(),
                "source": source,
            }

    results = await asyncio.gather(*(_one(h) for h in hits))
    return {"query": query, "results": list(results)}
```

- [ ] **Step 4: Run the whole service suite to verify all pass**

Run: `.venv/bin/pytest server/tests/test_web_search.py -v`
Expected: PASS (all tests from Tasks 1-5).

- [ ] **Step 5: Commit** (ask the user first)

```bash
git add server/services/web_search.py server/tests/test_web_search.py
git commit -m "feat(web-search): orchestrator with semaphore fan-out + snippet fallback"
```

---

### Task 6: `/v1/tools/web_search` router + app registration

**Files:**
- Create: `server/routers/tools.py`
- Modify: `server/app.py` (import + `include_router`)
- Create: `server/tests/test_tools_router.py`

**Interfaces:**
- Consumes: `web_search`, `RESULT_COUNT` from `web_search.py`.
- Produces: `router` (`APIRouter(prefix="/v1/tools")`) with `POST /web_search` accepting `{query, count}` and returning `web_search`'s dict; `502` on failure; `422` on empty query (Pydantic).

- [ ] **Step 1: Write the failing router test**

Create `server/tests/test_tools_router.py`:

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import tools as tools_router


def _app():
    app = FastAPI()
    app.include_router(tools_router.router)
    return app


def test_web_search_happy_path(monkeypatch):
    async def fake_web_search(query, count):
        return {"query": query, "results": [
            {"title": "T", "url": "http://x.test", "summary": "s", "source": "page"},
        ]}
    # Endpoint imports web_search into the router module's namespace.
    monkeypatch.setattr(tools_router, "web_search", fake_web_search)
    client = TestClient(_app())
    resp = client.post("/v1/tools/web_search", json={"query": "hi", "count": 3})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["source"] == "page"


def test_web_search_empty_query_is_422():
    client = TestClient(_app())
    resp = client.post("/v1/tools/web_search", json={"query": "", "count": 3})
    assert resp.status_code == 422


def test_web_search_backend_error_is_502(monkeypatch):
    async def boom(query, count):
        raise RuntimeError("searxng down")
    monkeypatch.setattr(tools_router, "web_search", boom)
    client = TestClient(_app())
    resp = client.post("/v1/tools/web_search", json={"query": "hi"})
    assert resp.status_code == 502
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest server/tests/test_tools_router.py -v`
Expected: FAIL — no `server.routers.tools` module.

- [ ] **Step 3: Create the router**

Create `server/routers/tools.py`:

```python
"""
Frontend-orchestrated chat tools that need a backend. Currently: web_search.

Synchronous by design — a chat tool call blocks on the result, so there is no
202/poll pattern here (unlike doc indexing). See services/web_search.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.web_search import RESULT_COUNT, web_search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/tools", tags=["tools"])


class WebSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    count: int = Field(default=RESULT_COUNT, ge=1, le=10)


@router.post("/web_search")
async def web_search_endpoint(req: WebSearchIn) -> dict:
    try:
        return await web_search(req.query, req.count)
    except Exception as e:  # noqa: BLE001 — surface as 502 so the tool can recover
        logger.exception("web_search failed")
        raise HTTPException(status_code=502, detail=f"Web search failed: {e}") from e
```

- [ ] **Step 4: Run it to verify it passes**

Run: `.venv/bin/pytest server/tests/test_tools_router.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Register the router in the app**

In `server/app.py`, add near the other router imports (line ~11-12):

```python
from .routers.tools import router as tools_router
```

After `app.include_router(docs_router)` (line ~32):

```python
    app.include_router(tools_router)
```

- [ ] **Step 6: Verify the app imports with the router mounted**

Run: `.venv/bin/python -c "from server.app import app; print([r.path for r in app.routes if 'web_search' in r.path])"`
Expected: prints `['/v1/tools/web_search']`.

- [ ] **Step 7: Commit** (ask the user first)

```bash
git add server/routers/tools.py server/app.py server/tests/test_tools_router.py
git commit -m "feat(web-search): /v1/tools/web_search router + app registration"
```

---

### Task 7: Frontend — validate before fetch + align count cap to 10

**Files:**
- Modify: `src/lib/chatTools/webSearch.js`
- Create: `src/lib/chatTools/webSearch.test.js`

**Interfaces:**
- Consumes: the backend `/v1/tools/web_search` contract (`{query, count}` → `{query, results:[...]}`).
- Produces: unchanged tool export shape (`{name, definition, when, execute}`); `execute` now validates before any network call and caps `count` at 10.

- [ ] **Step 1: Write the failing frontend test**

Create `src/lib/chatTools/webSearch.test.js`:

```js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import webSearch from './webSearch';

const ctx = { apiHost: 'localhost', apiPort: 8000 };

beforeEach(() => { global.fetch = vi.fn(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('webSearch.execute validation', () => {
  it('errors and does NOT fetch when query is empty/whitespace', async () => {
    const r = await webSearch.execute({ query: '   ' }, ctx);
    expect(r.error).toMatch(/query is required/i);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('errors and does NOT fetch when count is out of range (>10)', async () => {
    const r = await webSearch.execute({ query: 'hi', count: 11 }, ctx);
    expect(r.error).toMatch(/count must be/i);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('fetches once and returns agent_response on success', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ query: 'hi', results: [{}, {}] }),
    });
    const r = await webSearch.execute({ query: 'hi', count: 3 }, ctx);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(r.agent_response.results).toHaveLength(2);
    expect(r.summary_text).toMatch(/2 result/);
  });
});

describe('webSearch.definition', () => {
  it('caps count at 10 in the JSON schema', () => {
    expect(webSearch.definition.function.parameters.properties.count.maximum).toBe(10);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm run test:run -- src/lib/chatTools/webSearch.test.js`
Expected: FAIL — current `execute` fetches before validating (first two tests fail), and `count.maximum` is 15.

- [ ] **Step 3: Rewrite `execute` (validate first) and cap `count` at 10**

In `src/lib/chatTools/webSearch.js`, change the `count` schema line from `maximum: 15` to `maximum: 10`, and replace the `execute` body:

```js
    when: (_ctx) => true, // tighten: e.g. gate on a user preference
    execute: async (args, ctx) => {
        const query = (args?.query || '').trim();
        const count = args?.count ?? 5;
        if (!query) return { error: 'query is required and must be non-empty.' };
        if (count < 1 || count > 10) return { error: 'count must be between 1 and 10.' };
        const res = await fetch(
            buildApiUrl(ctx.apiHost, ctx.apiPort, '/v1/tools/web_search'),
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, count }) },
        );
        if (!res.ok) return { error: `Web search HTTP ${res.status}` };
        const data = await res.json();
        return {
            summary_text: `Web search for "${query}" returned ${data?.results?.length || 0} result(s).`,
            agent_response: data,
        };
    },
```

Also update the `count` description text if it mentions 15 (it says "Number of results." — no change needed there).

- [ ] **Step 4: Run it to verify it passes**

Run: `npm run test:run -- src/lib/chatTools/webSearch.test.js`
Expected: PASS (all four).

- [ ] **Step 5: Run the full frontend suite to check for regressions**

Run: `npm run test:run`
Expected: PASS (existing suite green + the new file).

- [ ] **Step 6: Commit** (ask the user first)

```bash
git add src/lib/chatTools/webSearch.js src/lib/chatTools/webSearch.test.js
git commit -m "fix(web-search): validate before fetch; cap count at 10"
```

---

### Task 8: SearXNG service, config, docs + end-to-end smoke test

Infrastructure and documentation the feature needs to actually run. No automated test; ends with a manual smoke test.

**Files:**
- Modify: `docker-compose.yml` (add `searxng` service)
- Create: `searxng/settings.yml`
- Modify: `.env.example` (web-search config block)
- Modify: `README.md` (setup + the JSON-format gotcha)

**Interfaces:**
- Consumes: `SEARXNG_URL` (service), `WEB_SEARCH_*` env (documented).
- Produces: a running SearXNG with JSON enabled + documented configuration.

- [ ] **Step 1: Add the SearXNG service to `docker-compose.yml`**

Add under `services:` (sibling of `postgres`):

```yaml
  searxng:
    image: searxng/searxng:latest   # pin to a dated tag for reproducibility
    container_name: natural-reader-searxng
    ports:
      - "8080:8080"
    volumes:
      - ./searxng:/etc/searxng:rw
    environment:
      SEARXNG_BASE_URL: http://localhost:8080/
    restart: unless-stopped
```

- [ ] **Step 2: Create `searxng/settings.yml` with JSON enabled**

```yaml
# Minimal SearXNG config for the web_search tool. JSON output is OFF by default
# in SearXNG — without it, /search?format=json returns HTTP 403.
use_default_settings: true
server:
  # Generate a real value: `openssl rand -hex 32`
  secret_key: "REPLACE_WITH_openssl_rand_hex_32"
  limiter: false
search:
  formats:
    - html
    - json
```

- [ ] **Step 3: Set a real secret_key**

Run: `openssl rand -hex 32`
Then paste the output into `searxng/settings.yml` `server.secret_key` (replacing the placeholder).

- [ ] **Step 4: Append the web-search config block to `.env.example`**

```
# ─── Web Search (SearXNG-backed web_search tool) ─────────────────────────────
# Base URL of the self-hosted SearXNG instance (see docker-compose.yml). JSON
# output must be enabled in searxng/settings.yml or every search 403s.
# SEARXNG_URL=http://localhost:8080
#
# Small model used to summarize each fetched page (pull it: `ollama pull llama3.2:3b`).
# The capable chat model still does the final synthesis; this only condenses pages.
# WEB_SEARCH_SUMMARY_MODEL=llama3.2:3b
#
# Results fetched+summarized per query (1-10). Each is a full page fetch plus a
# model call, so this dominates latency.
# WEB_SEARCH_RESULT_COUNT=5
#
# Max parallel fetch+summarize jobs (matches the embedding pipeline default).
# WEB_SEARCH_MAX_CONCURRENCY=4
#
# Per-page fetch timeout (seconds).
# WEB_SEARCH_FETCH_TIMEOUT_S=8
#
# Per-summary Ollama timeout (seconds).
# WEB_SEARCH_SUMMARY_TIMEOUT_S=30
#
# Extracted page text truncated to this many chars before summarizing.
# WEB_SEARCH_MAX_PAGE_CHARS=6000
```

- [ ] **Step 5: Add a README section**

Add this subsection near the chat/Ollama docs in `README.md`:

```markdown
### Web search (`web_search` tool)

The chat model can search the live web via a self-hosted SearXNG instance. For
each result it fetches the page, extracts the readable text, and summarizes it
with a small model (`llama3.2:3b` by default); the summaries go back to the chat
model, which writes the final answer and cites sources.

**Setup**

1. Start SearXNG: `docker compose up -d searxng`
2. Pull the summary model: `ollama pull llama3.2:3b`
3. Verify JSON search works:
   `curl -s "http://localhost:8080/search?q=test&format=json" | head -c 80`
   It must return JSON. **A 403 means SearXNG's JSON format is disabled** —
   check that `searxng/settings.yml` lists `json` under `search.formats`.

**Configuration** — all optional; see the `WEB_SEARCH_*` and `SEARXNG_URL`
entries in `.env.example`. The most impactful knob is `WEB_SEARCH_RESULT_COUNT`
(default 5): each result is a full page fetch plus a model call, so it dominates
latency.

**Security note:** the backend only fetches URLs whose host resolves to a public
IP — private/loopback/link-local addresses (including cloud metadata endpoints)
are refused, and every redirect hop is re-checked.
```

- [ ] **Step 6: Start SearXNG and verify JSON works**

Run: `docker compose up -d searxng`
Then: `curl -s "http://localhost:8080/search?q=test&format=json" | head -c 200`
Expected: JSON (starts with `{`), **not** a 403/HTML page. If 403 → JSON format isn't enabled; recheck `searxng/settings.yml`.

- [ ] **Step 7: End-to-end smoke test**

Ensure `llama3.2:3b` is pulled (`ollama list`), start the backend (`./startup.sh up`) and the frontend, open a chat, and ask something current (e.g. "what's the latest stable Python release?"). Confirm the model invokes `web_search`, the tool returns per-result summaries, and the answer cites source URLs. Check backend logs for SSRF blocks / fetch fallbacks.

- [ ] **Step 8: Commit** (ask the user first)

```bash
git add docker-compose.yml searxng/settings.yml .env.example README.md
git commit -m "feat(web-search): SearXNG compose service, config, and docs"
```

---

## Notes for the implementer

- **Run the whole backend suite** after Tasks 1-6: `.venv/bin/pytest server/tests -v`. It should be fully hermetic (respx/monkeypatch — no real SearXNG, Ollama, or network).
- **`asyncio_mode = auto`** (in `pytest.ini`) is what lets the `async def test_*` functions run without per-test decorators.
- **Semaphore bound** is exercised implicitly by the orchestrator test (the fan-out returns all results through `async with _semaphore`); it is not asserted as a hard concurrency count, since that test is timing-fragile for little value.
- **The SSRF guard is the security spine.** If you change `fetch_and_extract`, keep the per-redirect re-check — auto-following redirects (`follow_redirects=True`) silently reopens the SSRF hole.
- **Latency:** with `RESULT_COUNT=5` and `MAX_CONCURRENCY=4`, expect a few seconds to ~15s per query (5 page fetches + 5 small-model calls). The frontend tool already `await`s the single request; no UI change needed.
