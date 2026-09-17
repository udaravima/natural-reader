# Model-Router Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every browser inference call behind the authenticated backend — a validated, streaming chat gateway (`/v1/inference/models`, `/v1/inference/chat`) with an admin model allowlist, a task-routing table for server-side inference (embed / summarize), and per-user daily token budgets enforced with 429s and visible to the user.

**Architecture:** The SPA today talks to Ollama directly (`GET /api/tags`, `POST /api/chat` in [useChatEngine.js](../../../src/hooks/useChatEngine.js)) — unauthenticated by design, since Ollama has no notion of users. The gateway proxies exactly those two upstream calls behind `Depends(get_current_user)` (session cookie or PAT), validates the request envelope down to the per-message fields the SPA emits, and streams NDJSON through byte-faithfully so the SPA's tool loop and think-fallback chain are untouched. All server-side model selection (`embeddings.py`, `web_search.py`, and the future document-library RAG) routes through one config module (`model_router.py`). A new `inference_usage` table accounts real token counts from Ollama's final-chunk stats against per-user daily budgets.

**Tech Stack:** FastAPI + httpx (AsyncClient, `MockTransport` for hermetic tests), Pydantic v2 (`extra="forbid"` validation), psycopg async pool, Postgres 16 (migration 007), React 19 + Vitest.

**Spec:** [docs/superpowers/specs/2026-09-17-model-router-gateway-design.md](../specs/2026-09-17-model-router-gateway-design.md) — read it alongside this plan.

**Commits:** pre-approved by the repo owner (2026-09-17): one Conventional Commit per green task, no per-commit approval needed.

## Global Constraints

- **Never a general Ollama proxy.** The only upstream calls the gateway ever makes are `GET {OLLAMA_URL}/api/tags` and `POST {OLLAMA_URL}/api/chat`. `/api/pull`, `/api/delete`, `/api/run` etc. must remain unreachable through app auth, forever. A future multi-provider router changes the target of the passthrough, not this contract.
- **Byte-faithful streaming.** The passthrough must not rewrite, re-chunk, or delay NDJSON bytes. Validation happens on the *request envelope* only. (E3's usage tap *scans* lines but never mutates forwarded bytes.)
- **The request envelope, verified** (spec §3 + [chatHistory.js:12-23](../../../src/hooks/chatHistory.js), [inference.js:32](../../../src/hooks/inference.js)): top-level `model, messages, stream, tools, think, keep_alive, options{num_ctx, num_predict}`; per-message `role, content, images, tool_calls`. **The tool follow-up round carries `tool_calls` on the assistant message and `images` (base64, image *and* audio) on user messages** — validation must accept both or the tool loop and attachments break. Unknown top-level or per-message fields → **422**.
- **Streaming-passthrough trap:** `StreamingResponse` consumes the body *after* the handler returns, so `async with client.stream(...)` would close the upstream early. Use `client.send(build_request(...), stream=True)` and close the response in the generator's `finally`.
- **429 must short-circuit before the SPA fallback chain.** `useChatEngine`'s think-level and tools retries trigger on any 4xx — a 429 fed into them would re-spend tokens. Handle 429 explicitly first.
- **Budgets fail open.** If Postgres is unreachable during a budget pre-check or post-accounting, the request proceeds (logged). Chat degrades like session persistence already does — it never dies with the DB. Auth itself still needs the DB (that's the existing `get_current_user` behavior).
- **Budget days are UTC, computed in Python.** A SQL `DEFAULT CURRENT_DATE` would use the Postgres server timezone and silently shift the reset boundary. `reset_at` is next UTC midnight, ISO-8601 `Z`. `0` and unset both mean *unlimited* (spec §4.3) at both the env and per-user-column level.
- **Migrations** are `server/sql/NNN_*.sql`, one transaction, and MUST end with `INSERT INTO schema_migrations(version) VALUES (7) ON CONFLICT DO NOTHING;`. After adding 007, drop the test DB once so the session fixture re-applies from scratch: `psql "postgresql://natural_reader:natural_reader@localhost:5433/postgres" -c 'DROP DATABASE IF EXISTS natural_reader_test;'`.
- **Backend tests:** hermetic. Upstream Ollama is mocked with `httpx.MockTransport` (injected via `start_client(transport=...)`); config is env-driven and re-read per call, so tests use `monkeypatch.setenv`. Router tests use `httpx.AsyncClient` + `ASGITransport` (never `TestClient`). DB-backed tests need Postgres: `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres`. The 401-without-credential path needs `get_conn` overridden with the `db_conn` fixture (otherwise `get_pool()` raises → 500, not 401 — see `test_protected_routes.py`).
- **The suite never imports `server.app`** (it loads the 325 MB Kokoro model at import time). App wiring (Task 3) is verified by grep + the manual pass in Task 13, never by importing it in a test.
- **Local mode stays byte-identical.** `inferenceSource === 'local'` must produce the same URLs, same body, and *no* credentials header as today's browser→Ollama calls.
- **Frontend:** TDD with vitest; `npm run test:run` and `npm run lint` green before each commit. Keep every existing suite green (sub-projects A/B+C, SPA auth UI).

---

### Task 1 (E2 foundation): `model_router.py` — the one config module

**Files:**
- Create: `server/services/model_router.py`
- Test: `server/tests/test_model_router.py`

**Interfaces:**
- Produces:
  - `InferenceConfig` frozen dataclass: `ollama_url: str`, `timeout_s: float`, `allowed_models: tuple[str, ...] | None` (None = all), `summarize_model: str`, `embed_model: str`, `daily_token_budget: int | None` (None = unlimited).
  - `load_inference_config(env: Mapping[str, str]) -> InferenceConfig` — pure.
  - `get_config() -> InferenceConfig` — `load_inference_config(os.environ)`, re-read every call (parsing is trivial; keeps `monkeypatch.setenv` effective).
  - `is_model_allowed(cfg, model: str) -> bool`.
- Env vars: `OLLAMA_URL` (existing), `INFERENCE_MODELS` (new, comma-separated, unset = all), `INFERENCE_TIMEOUT_S` (new, default 300), `INFERENCE_DAILY_TOKEN_BUDGET` (new, unset/0 = unlimited), `SUMMARIZE_MODEL` (new canonical name) with **fallback to the pre-gateway `WEB_SEARCH_SUMMARY_MODEL`** then `llama3.2:3b`, `EMBEDDING_MODEL` (existing, default `nomic-embed-text`).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_model_router.py`:

```python
from server.services.model_router import load_inference_config, is_model_allowed


def test_defaults():
    cfg = load_inference_config({})
    assert cfg.ollama_url == "http://localhost:11434"
    assert cfg.timeout_s == 300
    assert cfg.allowed_models is None          # unset = allow everything
    assert cfg.summarize_model == "llama3.2:3b"
    assert cfg.embed_model == "nomic-embed-text"
    assert cfg.daily_token_budget is None      # unset = unlimited


def test_allowlist_parses_comma_separated_with_whitespace():
    cfg = load_inference_config({"INFERENCE_MODELS": "gemma3, qwen2.5 ,, deepseek-r1:1.5b"})
    assert cfg.allowed_models == ("gemma3", "qwen2.5", "deepseek-r1:1.5b")
    assert is_model_allowed(cfg, "gemma3")
    assert not is_model_allowed(cfg, "llama3.2:3b")


def test_empty_allowlist_means_all():
    cfg = load_inference_config({"INFERENCE_MODELS": "  "})
    assert cfg.allowed_models is None
    assert is_model_allowed(cfg, "anything")


def test_summarize_model_precedence():
    assert load_inference_config({}) .summarize_model == "llama3.2:3b"
    assert load_inference_config(
        {"WEB_SEARCH_SUMMARY_MODEL": "old"}).summarize_model == "old"
    assert load_inference_config({
        "WEB_SEARCH_SUMMARY_MODEL": "old",
        "SUMMARIZE_MODEL": "new",
    }).summarize_model == "new"


def test_budget_zero_and_unset_mean_unlimited():
    assert load_inference_config({}).daily_token_budget is None
    assert load_inference_config(
        {"INFERENCE_DAILY_TOKEN_BUDGET": "0"}).daily_token_budget is None
    assert load_inference_config(
        {"INFERENCE_DAILY_TOKEN_BUDGET": "500000"}).daily_token_budget == 500000
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest server/tests/test_model_router.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/services/model_router.py`:

```python
"""Model-router config — the single place that knows which model serves which
task and what this deployment is allowed to run (gateway spec §4.2).

Everything is pure env parsing so tests can pass a dict. get_config() re-reads
os.environ on every call: parsing is trivial and it keeps monkeypatch.setenv
effective in tests and .env edits effective without a restart of the module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class InferenceConfig:
    ollama_url: str
    timeout_s: float
    allowed_models: tuple[str, ...] | None   # None = allow everything (dev)
    summarize_model: str
    embed_model: str
    daily_token_budget: int | None           # None = unlimited


def load_inference_config(env: Mapping[str, str]) -> InferenceConfig:
    def _csv(key: str) -> tuple[str, ...] | None:
        raw = env.get(key)
        if raw is None or not raw.strip():
            return None
        return tuple(m.strip() for m in raw.split(",") if m.strip())

    budget_raw = (env.get("INFERENCE_DAILY_TOKEN_BUDGET") or "").strip()
    # Spec §4.3: 0/unset = unlimited, so a falsy budget collapses to None.
    budget = int(budget_raw) if budget_raw else None

    return InferenceConfig(
        ollama_url=env.get("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        timeout_s=float(env.get("INFERENCE_TIMEOUT_S", "300")),
        allowed_models=_csv("INFERENCE_MODELS"),
        # SUMMARIZE_MODEL is the canonical name; WEB_SEARCH_SUMMARY_MODEL is the
        # pre-gateway var kept working so existing deployments don't break.
        summarize_model=(
            env.get("SUMMARIZE_MODEL")
            or env.get("WEB_SEARCH_SUMMARY_MODEL")
            or "llama3.2:3b"
        ),
        embed_model=env.get("EMBEDDING_MODEL", "nomic-embed-text"),
        daily_token_budget=budget if budget else None,
    )


def get_config() -> InferenceConfig:
    return load_inference_config(os.environ)


def is_model_allowed(cfg: InferenceConfig, model: str) -> bool:
    return cfg.allowed_models is None or model in cfg.allowed_models
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest server/tests/test_model_router.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add server/services/model_router.py server/tests/test_model_router.py
git commit -m "feat(inference): model-router config module (allowlist, task models, budget)"
```

---

### Task 2 (E1): `server/routers/inference.py` — validated, streaming gateway

**Files:**
- Create: `server/routers/inference.py`
- Test: `server/tests/test_inference_router.py`

**Interfaces:**
- Consumes: `deps.get_current_user`, `model_router.get_config/is_model_allowed`.
- Produces:
  - `GET /v1/inference/models` → `{models: [name, ...]}` — proxies `/api/tags`, filtered by the allowlist. 502 when Ollama is unreachable.
  - `POST /v1/inference/chat` → validated passthrough to `/api/chat`, `StreamingResponse` (`application/x-ndjson`). Upstream error status **and JSON body** forwarded unchanged (the SPA's fallback chain branches on the status). 422 for non-allowlisted models or unknown fields. 502 when Ollama is unreachable.
  - `start_client(transport=None)` / `stop_client()` / `_get_client()` — module-level httpx client lifecycle (mirrors `embeddings.py`); `transport` exists so tests inject `httpx.MockTransport`.
- Pydantic models: `ChatOptions` (`extra="forbid"`), `ToolCallFunction`/`ToolCall` (`extra="allow"` — tool schemas are model-facing passthrough JSON), `ChatMessage` (`extra="forbid"`, `role: Literal["system","user","assistant","tool"]`, `content: str = ""`, optional `images: list[str]`, `tool_calls: list[ToolCall]`), `ChatRequest` (`extra="forbid"`, fields exactly per Global Constraints). Serialize upstream with `model_dump(exclude_none=True)` — the SPA's "unset means omitted" rule must survive (note `exclude_none` keeps `False` and `""`, only drops `None`).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_inference_router.py`:

```python
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import inference as inf

PRINCIPAL = deps.Principal(user_id="u1", email="a@x.io", role="member")

NDJSON = (
    b'{"model":"m","message":{"role":"assistant","content":"Hel"}}\n'
    b'{"model":"m","message":{"role":"assistant","tool_calls":[{"function":'
    b'{"name":"search_document","arguments":{"query":"x"}}}]}}\n'
    b'{"model":"m","message":{"role":"assistant","content":""},"done":true,'
    b'"done_reason":"stop","total_duration":1,"prompt_eval_count":10,"eval_count":5}\n'
)

VALID_BODY = {
    "model": "m",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
    "think": "low",
    "keep_alive": "5m",
    "options": {"num_ctx": 4096},
}


@pytest.fixture
def upstream():
    """A programmable upstream: tests replace state['handler'] before making
    requests; the handler sees every request for assertions."""
    state = {"handler": lambda request: httpx.Response(200, json={"models": []}),
             "requests": []}

    def handler(request):
        state["requests"].append(request)
        return state["handler"](request)

    state["transport"] = httpx.MockTransport(handler)
    return state


@pytest.fixture
async def app(db_conn, upstream):
    application = FastAPI()
    application.include_router(inf.router)
    await inf.start_client(transport=upstream["transport"])
    yield application
    await inf.stop_client()


@pytest.fixture
async def client(app):
    app.dependency_overrides[deps.get_current_user] = lambda: PRINCIPAL
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


def _client_no_auth(app):
    # Keep the real get_current_user (overridden conn) so no-credential → 401.
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return c


async def test_models_requires_auth(db_conn, app):
    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/v1/inference/models")).status_code == 401


async def test_models_lists_from_upstream(client, upstream):
    upstream["handler"] = lambda r: httpx.Response(200, json={"models": [
        {"name": "gemma3"}, {"name": "qwen2.5"}, {"other": 1},
    ]})
    r = await client.get("/v1/inference/models")
    assert r.status_code == 200
    assert r.json() == {"models": ["gemma3", "qwen2.5"]}


async def test_models_filtered_by_allowlist(client, upstream, monkeypatch):
    monkeypatch.setenv("INFERENCE_MODELS", "gemma3")
    upstream["handler"] = lambda r: httpx.Response(200, json={"models": [
        {"name": "gemma3"}, {"name": "qwen2.5"},
    ]})
    r = await client.get("/v1/inference/models")
    assert r.json() == {"models": ["gemma3"]}


async def test_models_upstream_down_is_502(client, upstream):
    def boom(request):
        raise httpx.ConnectError("nope")
    upstream["handler"] = boom
    assert (await client.get("/v1/inference/models")).status_code == 502


async def test_chat_requires_auth(db_conn, app):
    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/inference/chat", json=VALID_BODY)
        assert r.status_code == 401


async def test_chat_streams_ndjson_byte_faithful(client, upstream):
    upstream["handler"] = lambda r: httpx.Response(
        200, content=NDJSON, headers={"content-type": "application/x-ndjson"})
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.content == NDJSON


async def test_chat_forwards_body_upstream(client, upstream):
    upstream["handler"] = lambda r: httpx.Response(
        200, content=NDJSON, headers={"content-type": "application/x-ndjson"})
    await client.post("/v1/inference/chat", json=VALID_BODY)
    sent = httpx.Request("POST", "x", json=upstream["requests"][0].read() or None)
    import json as _json
    body = _json.loads(upstream["requests"][0].content)
    assert body["model"] == "m"
    assert body["stream"] is True
    assert body["think"] == "low"
    assert body["options"] == {"num_ctx": 4096}
    assert "images" not in body["messages"][0]


async def test_chat_rejects_unknown_top_level_field(client):
    r = await client.post("/v1/inference/chat", json={**VALID_BODY, "format": "json"})
    assert r.status_code == 422


async def test_chat_rejects_unknown_message_field(client):
    bad = {**VALID_BODY, "messages": [{"role": "user", "content": "hi", "tool": 1}]}
    r = await client.post("/v1/inference/chat", json=bad)
    assert r.status_code == 422


async def test_chat_accepts_images_and_tool_calls_round(client, upstream):
    upstream["handler"] = lambda r: httpx.Response(
        200, content=NDJSON, headers={"content-type": "application/x-ndjson"})
    body = {
        "model": "m", "stream": True,
        "messages": [
            {"role": "user", "content": "look", "images": ["aGVsbG8="]},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search_document", "arguments": {"query": "x"}}}]},
            {"role": "tool", "content": "{}"},
        ],
    }
    r = await client.post("/v1/inference/chat", json=body)
    assert r.status_code == 200
    import json as _json
    sent = _json.loads(upstream["requests"][0].content)
    assert sent["messages"][0]["images"] == ["aGVsbG8="]
    assert sent["messages"][1]["tool_calls"][0]["function"]["name"] == "search_document"


async def test_chat_allowlist_rejects_other_models(client, monkeypatch):
    monkeypatch.setenv("INFERENCE_MODELS", "gemma3")
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 422
    assert "not allowed" in r.text


async def test_chat_forwards_upstream_error_status_and_body(client, upstream):
    # The SPA's think-level fallback branches on the 4xx status — it must
    # arrive unchanged, with Ollama's error JSON intact.
    upstream["handler"] = lambda r: httpx.Response(400, json={"error": "bad think level"})
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 400
    assert r.json() == {"error": "bad think level"}


async def test_chat_upstream_down_is_502(client, upstream):
    def boom(request):
        raise httpx.ConnectError("nope")
    upstream["handler"] = boom
    assert (await client.post("/v1/inference/chat", json=VALID_BODY)).status_code == 502
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest server/tests/test_inference_router.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/routers/inference.py`:

```python
"""/v1/inference/* — the authenticated chat gateway to Ollama (sub-project E).

A *chat* gateway, never a general Ollama proxy: the only upstream calls are
GET /api/tags (model list) and POST /api/chat (generation). The request
envelope is validated down to the per-message fields the SPA actually sends;
anything else is a 422. /api/pull, /api/delete, /api/run & co. stay
unreachable through app auth, forever.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from ..auth import deps
from ..services import model_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/inference", tags=["inference"])

_client: httpx.AsyncClient | None = None


async def start_client(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Called from app startup. `transport` exists so tests inject a MockTransport."""
    global _client
    if _client is None:
        cfg = model_router.get_config()
        # Short connect timeout so a dead Ollama fails fast; the read timeout
        # spans whole streamed replies (slow models can think for minutes).
        _client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=cfg.timeout_s, write=30.0, pool=5.0),
        )


async def stop_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("Inference client not started — call start_client() first")
    return _client


class ChatOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num_ctx: int | None = None
    num_predict: int | None = None


class ToolCallFunction(BaseModel):
    # Tool schemas are model-facing passthrough JSON — permissive on purpose.
    model_config = ConfigDict(extra="allow")
    name: str
    arguments: str | dict | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")
    function: ToolCallFunction


class ChatMessage(BaseModel):
    # `images` carries base64 (image AND audio attachments — Ollama's message
    # struct has a single binary field); the tool follow-up round carries
    # `tool_calls` on the assistant message. Both must survive validation or
    # the SPA's attachment and tool loops break.
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    images: list[str] | None = None
    tool_calls: list[ToolCall] | None = None


class ChatRequest(BaseModel):
    # Exactly the fields buildRequestFields (src/hooks/inference.js) emits,
    # plus the tool round's additions. extra="forbid" is what makes this a
    # chat gateway: unknown fields are a 422, not a silent passthrough.
    model_config = ConfigDict(extra="forbid")
    model: str
    messages: list[ChatMessage]
    stream: bool = True
    tools: list[dict] | None = None
    think: bool | str | None = None
    keep_alive: str | int | None = None
    options: ChatOptions | None = None


@router.get("/models")
async def list_models(principal: deps.Principal = Depends(deps.get_current_user)):
    cfg = model_router.get_config()
    try:
        resp = await _get_client().get(f"{cfg.ollama_url}/api/tags")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Ollama unreachable")
    if resp.status_code != 200:
        return JSONResponse(status_code=resp.status_code, content={"detail": "Ollama error"})
    names = [
        m["name"] for m in resp.json().get("models", [])
        if isinstance(m, dict) and m.get("name")
    ]
    if cfg.allowed_models is not None:
        names = [n for n in names if n in cfg.allowed_models]
    return {"models": names}


@router.post("/chat")
async def chat(
    body: ChatRequest,
    principal: deps.Principal = Depends(deps.get_current_user),
):
    cfg = model_router.get_config()
    if not model_router.is_model_allowed(cfg, body.model):
        raise HTTPException(
            status_code=422,
            detail=f"Model '{body.model}' is not allowed on this deployment",
        )
    client = _get_client()
    req = client.build_request(
        "POST",
        f"{cfg.ollama_url}/api/chat",
        json=body.model_dump(exclude_none=True),
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Ollama unreachable")

    if upstream.status_code != 200:
        # Forward status AND body: the SPA's think-level / tools fallback
        # chain branches on res.status, and its toasts read the error JSON.
        raw = (await upstream.aread()).decode("utf-8", errors="replace")
        await upstream.aclose()
        try:
            content = json.loads(raw)
        except ValueError:
            content = {"detail": raw[:2000]}
        return JSONResponse(status_code=upstream.status_code, content=content)

    async def _passthrough():
        # STREAMING TRAP: StreamingResponse consumes this generator AFTER the
        # handler returns — an `async with client.stream(...)` here would close
        # the upstream before the first byte is forwarded. Close it only when
        # the last byte has gone out (or the client hung up).
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(_passthrough(), media_type="application/x-ndjson")
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest server/tests/test_inference_router.py -q`
Expected: PASS (12 tests). Then the full slice: `.venv/bin/python -m pytest server/tests/ -q` — expected all green (no existing module is touched yet).

- [ ] **Step 5: Commit**

```bash
git add server/routers/inference.py server/tests/test_inference_router.py
git commit -m "feat(inference): authenticated streaming chat gateway with validated envelope"
```

---

### Task 3 (E1): Wire the router into the app

**Files:**
- Modify: `server/app.py`

**Interfaces:**
- Consumes: `inference.start_client/stop_client`, `inference.router`.
- Produces: `/v1/inference/*` mounted in the real app; upstream client started on startup, closed on shutdown (mirrors the embeddings/web-search client lifecycle at [app.py:89-101](../../../server/app.py)).

- [ ] **Step 1: Edit `server/app.py`**

Add to the imports (beside the other service imports):

```python
from .routers.inference import router as inference_router
from .routers.inference import start_client as start_inference, stop_client as stop_inference
```

Add `app.include_router(inference_router)` after `app.include_router(admin_router)`.

In `_startup`, after `await start_web_search()` add `await start_inference()`. In `_shutdown`, before `await close_db()` add `await stop_inference()`.

- [ ] **Step 2: Verify without importing the app**

The suite never imports `server.app` (Kokoro loads at import). Verify the wiring textually:

Run: `grep -n "inference" server/app.py`
Expected: the import, the `include_router`, and both lifecycle lines present.

- [ ] **Step 3: Full suite**

Run: `.venv/bin/python -m pytest server/tests/ -q`
Expected: PASS (the router tests mount the router directly; app wiring is exercised live in Task 13).

- [ ] **Step 4: Commit**

```bash
git add server/app.py
git commit -m "feat(inference): mount gateway router + upstream client lifecycle"
```

---

### Task 4 (E1): `chatTransport.js` — the frontend source switch seam

**Files:**
- Create: `src/lib/chatTransport.js`
- Test: `src/lib/chatTransport.test.js`

**Interfaces:**
- Consumes: `apiFetch(host, port, path, opts)` ([apiFetch.js](../../../src/utils/apiFetch.js)), `buildApiUrl(host, port, path)` ([url.js](../../../src/utils/url.js)).
- Produces:
  - `MODELS_PATH = { server: '/v1/inference/models', local: '/api/tags' }`
  - `CHAT_PATH = { server: '/v1/inference/chat', local: '/api/chat' }`
  - `chatFetch(source, hosts, path, opts = {}) → Promise<Response>` — server mode rides `apiFetch` (session cookie, global 401 handler); local mode is byte-identical to today's browser→Ollama call (no credentials).

- [ ] **Step 1: Write the failing tests**

Create `src/lib/chatTransport.test.js`:

```javascript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { chatFetch, MODELS_PATH, CHAT_PATH } from './chatTransport';

const HOSTS = {
  apiHost: '', apiPort: '',
  ollamaHost: 'localhost', ollamaPort: '11434',
};

describe('chatTransport', () => {
  beforeEach(() => { global.fetch = vi.fn(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('server mode hits the gateway with credentials (cookie)', async () => {
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await chatFetch('server', HOSTS, CHAT_PATH.server, { method: 'POST', body: 'x' });
    expect(global.fetch).toHaveBeenCalledWith(
      '/v1/inference/chat',
      expect.objectContaining({ credentials: 'include', method: 'POST', body: 'x' }),
    );
  });

  it('local mode hits Ollama directly with NO credentials', async () => {
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await chatFetch('local', HOSTS, CHAT_PATH.local, { method: 'POST', body: 'x' });
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toBe('http://localhost:11434/api/chat');
    expect(opts).not.toHaveProperty('credentials');
  });

  it('local mode with blank host is same-origin relative', async () => {
    global.fetch.mockResolvedValue(new Response('{}', { status: 200 }));
    await chatFetch('local', { ...HOSTS, ollamaHost: '', ollamaPort: '' }, MODELS_PATH.local);
    expect(global.fetch.mock.calls[0][0]).toBe('/api/tags');
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/lib/chatTransport.test.js`
Expected: FAIL — cannot resolve `./chatTransport`.

- [ ] **Step 3: Implement**

Create `src/lib/chatTransport.js`:

```javascript
import { apiFetch } from '../utils/apiFetch';
import { buildApiUrl } from '../utils/url';

// The two inference sources share one transport. Server mode rides the
// authenticated same-origin /v1 gateway (session cookie via apiFetch; PAT
// Bearer for the extension); local mode is byte-identical to the historical
// browser→Ollama calls — no credentials, /api/* paths, host/port honored.
export const MODELS_PATH = { server: '/v1/inference/models', local: '/api/tags' };
export const CHAT_PATH = { server: '/v1/inference/chat', local: '/api/chat' };

export function chatFetch(source, hosts, path, opts = {}) {
  if (source === 'server') {
    return apiFetch(hosts.apiHost, hosts.apiPort, path, opts);
  }
  return fetch(buildApiUrl(hosts.ollamaHost, hosts.ollamaPort, path), opts);
}
```

- [ ] **Step 4: Run — verify pass**

Run: `npm run test:run -- src/lib/chatTransport.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/chatTransport.js src/lib/chatTransport.test.js
git commit -m "feat(inference): chatTransport source-switch seam"
```

---

### Task 5 (E1): `useChatEngine` — `callChat()` + model refresh through the transport

**Files:**
- Modify: `src/hooks/useChatEngine.js`

**Interfaces:**
- Consumes: `chatFetch`, `MODELS_PATH`, `CHAT_PATH` (Task 4); new hook param `inferenceSource` (`'server' | 'local'`, default `'server'`).
- Produces: identical hook surface. The four `/api/chat` fetch sites collapse into one `callChat(body, signal)`; `refreshModels` switches URL + credentials by source. `/api/tags` and `/v1/inference/models` both return `{models: [...]}` so the parsing is unchanged.

No new unit test file — the hook has none today; behavior is guarded by Task 4's transport tests, the full suite, and the Task 13 manual pass (streaming, think fallback, tool loop through the gateway).

- [ ] **Step 1: Accept the new param**

Add `inferenceSource = 'server',` to the `useChatEngine({...})` parameter list (after `ollamaPort,`).

- [ ] **Step 2: Replace the four chat fetch sites with `callChat`**

Add the import `import { chatFetch, CHAT_PATH, MODELS_PATH } from '../lib/chatTransport';` and define, near `ollamaUrl` (which this task retires — delete it and the now-unused `buildApiUrl` import if nothing else uses it):

```javascript
const chatHosts = { apiHost, apiPort, ollamaHost, ollamaPort };
const callChat = useCallback((body, signal) => chatFetch(
    inferenceSource, chatHosts, CHAT_PATH[inferenceSource],
    {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
    },
), [inferenceSource, apiHost, apiPort, ollamaHost, ollamaPort]);
```

Then replace each site (keep the surrounding fallback-chain logic *exactly* as-is — only the fetch expression changes):

- `:626` (first call): `let res = await callChat(firstBody, controller.signal);`
- `:648` (think-level retry): `res = await callChat(tools.length > 0 ? buildBody({ tools }, { think: 'on' }) : buildBody({}, { think: 'on' }), controller.signal);`
- `:662` (tools-drop retry): `res = await callChat(buildBody({}, usedLevel ? { think: 'on' } : {}), controller.signal);`
- `:752` (post-tools follow-up): `const res2 = await callChat({ model: selectedModel, messages: followupHistory, stream: true, ...buildRequestFields(inferenceForThisMsg) }, controller.signal);`

Add `callChat` to the `sendMessage` dependency array (replacing nothing — append it).

- [ ] **Step 3: Route `refreshModels` through the transport**

In `refreshModels` (useChatEngine.js:357), replace `fetch(ollamaUrl('/api/tags'), { signal: controller.signal })` with:

```javascript
const res = await chatFetch(inferenceSource, chatHosts, MODELS_PATH[inferenceSource], { signal: controller.signal });
```

Both sources return `{models: [...]}` — leave the parsing untouched. Extend the `useCallback` deps: `[inferenceSource, apiHost, apiPort, ollamaHost, ollamaPort]` (the debounce effect at `:377` re-runs on those changes, which is correct: switching source re-probes).

- [ ] **Step 4: Full suite + lint**

Run: `npm run test:run && npm run lint`
Expected: PASS (no behavior change for local mode; no test asserted the bare fetch internals of the hook).

- [ ] **Step 5: Commit**

```bash
git add src/hooks/useChatEngine.js
git commit -m "feat(inference): route chat + model list through the source-switch transport"
```

---

### Task 6 (E1): UI — inference-source setting (ChatSidebar + App wiring)

**Files:**
- Create: `src/components/chat/InferenceSourceSelect.jsx`
- Test: `src/components/chat/InferenceSourceSelect.test.jsx`
- Modify: `src/components/ChatSidebar.jsx`, `src/App.jsx`

**Interfaces:**
- Produces: persisted setting `neural-pdf-inferenceSource` (`'server'` default) via `usePersistedState` in App.jsx; segmented **Server | Local Ollama** control above the OLLAMA SERVER block in ChatSidebar; host/port inputs (and the same-origin hint) render **only in local mode**. The reachable/refresh row stays visible in both modes (server-mode reachability = backend + Ollama).

- [ ] **Step 1: Write the failing test**

Create `src/components/chat/InferenceSourceSelect.test.jsx`:

```javascript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { InferenceSourceSelect } from './InferenceSourceSelect';

const theme = { textSecondary: '', hover: '', border: '', bgTertiary: '' };

describe('InferenceSourceSelect', () => {
  it('marks the active source', () => {
    render(<InferenceSourceSelect source="server" onChange={vi.fn()} theme={theme} />);
    expect(screen.getByRole('button', { name: 'Server' }).className).toContain('bg-blue-500');
    expect(screen.getByRole('button', { name: 'Local Ollama' }).className).not.toContain('bg-blue-500');
  });

  it('fires onChange with the picked source', () => {
    const onChange = vi.fn();
    render(<InferenceSourceSelect source="server" onChange={onChange} theme={theme} />);
    fireEvent.click(screen.getByRole('button', { name: 'Local Ollama' }));
    expect(onChange).toHaveBeenCalledWith('local');
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/components/chat/InferenceSourceSelect.test.jsx`
Expected: FAIL — cannot resolve.

- [ ] **Step 3: Implement the component**

Create `src/components/chat/InferenceSourceSelect.jsx`:

```jsx
// Segmented control: run inference through the authenticated backend gateway
// (default) or directly against a local Ollama (pre-gateway behavior).
export function InferenceSourceSelect({ source, onChange, theme }) {
    const opt = (value, label) => (
        <button
            key={value}
            type="button"
            onClick={() => onChange(value)}
            className={`flex-1 text-[10px] font-bold px-2 py-1.5 rounded-lg transition-colors ${
                source === value
                    ? 'bg-blue-500 text-white'
                    : `${theme.textSecondary} ${theme.hover}`
            }`}
        >
            {label}
        </button>
    );
    return (
        <div className={`flex gap-1 p-1 rounded-lg border ${theme.border} ${theme.bgTertiary}`}>
            {opt('server', 'Server')}
            {opt('local', 'Local Ollama')}
        </div>
    );
}
```

- [ ] **Step 4: Wire into ChatSidebar + App**

In `src/App.jsx`, with the other persisted chat settings (~line 75):

```javascript
const [inferenceSource, setInferenceSource] = usePersistedState('inferenceSource', 'server');
```

Pass `inferenceSource` into `useChatEngine({...})` (after `ollamaPort,`), and pass `inferenceSource={inferenceSource} setInferenceSource={setInferenceSource}` to `<ChatSidebar ...>`.

In `src/components/ChatSidebar.jsx`: accept the two new props; import `InferenceSourceSelect`; directly above the `{/* Ollama host/port */}` block add:

```jsx
{/* Inference source */}
<div className="space-y-2">
    <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1`}>INFERENCE SOURCE</span>
    <InferenceSourceSelect source={inferenceSource} onChange={setInferenceSource} theme={theme} />
    {inferenceSource === 'server' && (
        <p className={`text-[9px] ${theme.textMuted} px-1`}>
            Runs through the Natural Reader backend — authenticated, with the
            deployment's model allowlist and daily token budget applied.
        </p>
    )}
</div>
```

Wrap the host/port `<div className="space-y-2">` block (lines 67-96, through the same-origin hint) in `{inferenceSource === 'local' && ( ... )}` so server mode hides both inputs and the hint. The model picker, reachable badge, and Refresh button stay visible in both modes.

- [ ] **Step 5: Run + commit**

Run: `npm run test:run && npm run lint`
Expected: PASS.

```bash
git add src/components/chat/ src/components/ChatSidebar.jsx src/App.jsx
git commit -m "feat(inference): inference-source setting (server gateway vs local Ollama)"
```

---

### Task 7 (E2): Route `embeddings.py` + `web_search.py` through `model_router`

**Files:**
- Modify: `server/services/embeddings.py`, `server/services/web_search.py`
- Test: extend `server/tests/test_web_search.py`; new `server/tests/test_embeddings_routing.py`

**Interfaces:**
- `embed_one` and `summarize_one` stop reading `OLLAMA_URL`/`EMBEDDING_MODEL`/`SUMMARY_MODEL` module constants and instead use `model_router.get_config()` (`cfg.ollama_url`, `cfg.embed_model`, `cfg.summarize_model`). Delete the now-dead constants (`EMBEDDING_MODEL`, `SUMMARY_MODEL`, and the `OLLAMA_URL` constants in both files — check no other module imports them first: `grep -rn "embeddings.OLLAMA_URL\|web_search.OLLAMA_URL\|SUMMARY_MODEL" server/`).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_embeddings_routing.py`:

```python
import httpx
import respx
from server.services import embeddings


@respx.mock
async def test_embed_one_uses_model_router(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("EMBEDDING_MODEL", "my-embed")
    route = respx.post("http://ollama.test/api/embeddings").mock(
        return_value=httpx.Response(200, json={"embedding": [0.0] * 768})
    )
    await embeddings.start_client()
    try:
        await embeddings.embed_one("text")
    finally:
        await embeddings.stop_client()
    body = route.calls.last.request.content.decode()
    assert '"model": "my-embed"' in body or '"model":"my-embed"' in body
```

In `server/tests/test_web_search.py`, update `test_summarize_one_calls_ollama_generate`: replace `monkeypatch.setattr(ws, "OLLAMA_URL", "http://ollama.test")` with `monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")` (config is env-driven now), and add one assertion that the model comes from the router:

```python
    monkeypatch.setenv("SUMMARIZE_MODEL", "my-summarizer")
    # ... existing mock setup ...
    body = route.calls.last.request.content.decode()
    assert '"model": "my-summarizer"' in body or '"model":"my-summarizer"' in body
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest server/tests/test_embeddings_routing.py server/tests/test_web_search.py -q`
Expected: FAIL — the services still read module constants (env changes have no effect).

- [ ] **Step 3: Implement**

In `server/services/embeddings.py`: add `from . import model_router`; delete the `OLLAMA_URL` and `EMBEDDING_MODEL` constants; in `embed_one` (line 64) use:

```python
    cfg = model_router.get_config()
    # ... inside the semaphore block:
    resp = await client.post(
        f"{cfg.ollama_url}/api/embeddings",
        json={"model": cfg.embed_model, "prompt": text},
    )
```

In `server/services/web_search.py`: add `from . import model_router`; delete `OLLAMA_URL` and `SUMMARY_MODEL`; in `summarize_one` (line 172):

```python
    cfg = model_router.get_config()
    resp = await client.post(
        f"{cfg.ollama_url}/api/generate",
        json={
            "model": cfg.summarize_model,
            "prompt": _SUMMARY_PROMPT.format(query=query, text=text),
            "stream": False,
        },
        timeout=SUMMARY_TIMEOUT_S,
    )
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest server/tests/ -q`
Expected: PASS (including `test_web_search.py` after the setenv update).

- [ ] **Step 5: Commit**

```bash
git add server/services/embeddings.py server/services/web_search.py server/tests/test_embeddings_routing.py server/tests/test_web_search.py
git commit -m "refactor(inference): server-side model selection routes through model_router"
```

---

### Task 8 (E3): Migration 007 — usage table + per-user budget column

**Files:**
- Create: `server/sql/007_inference_budgets.sql`
- Test: `server/tests/test_migration_007.py`

**Interfaces:**
- Produces: `inference_usage (user_id UUID → users, day DATE, prompt_tokens BIGINT, eval_tokens BIGINT, requests BIGINT, PK (user_id, day))` and `users.inference_daily_token_budget BIGINT` (NULL = deployment default; 0 = unlimited). `day` has **no DEFAULT** — callers pass the UTC date computed in Python (Global Constraints).

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_migration_007.py`:

```python
SEED = "00000000-0000-0000-0000-000000000001"


async def _cols(db_conn, table):
    cur = await db_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,),
    )
    return {r[0] for r in await cur.fetchall()}


async def test_inference_usage_shape(db_conn):
    cols = await _cols(db_conn, "inference_usage")
    assert {"user_id", "day", "prompt_tokens", "eval_tokens", "requests"} <= cols


async def test_users_budget_column_exists(db_conn):
    assert "inference_daily_token_budget" in await _cols(db_conn, "users")


async def test_usage_round_trip(db_conn):
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, CURRENT_DATE, 10, 5, 1)",
        (SEED,),
    )
    cur = await db_conn.execute(
        "SELECT prompt_tokens, eval_tokens, requests FROM inference_usage "
        "WHERE user_id=%s AND day=CURRENT_DATE",
        (SEED,),
    )
    assert await cur.fetchone() == (10, 5, 1)
```

- [ ] **Step 2: Drop the test DB, run — verify fail**

```bash
psql "postgresql://natural_reader:natural_reader@localhost:5433/postgres" -c 'DROP DATABASE IF EXISTS natural_reader_test;'
.venv/bin/python -m pytest server/tests/test_migration_007.py -q
```
Expected: FAIL — table missing.

- [ ] **Step 3: Write the migration**

Create `server/sql/007_inference_budgets.sql`:

```sql
-- Inference gateway (sub-project E): per-user daily token accounting.
-- `day` carries no DEFAULT on purpose: callers compute the UTC date in
-- Python so the budget boundary never depends on the DB server timezone.
CREATE TABLE IF NOT EXISTS inference_usage (
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day           DATE NOT NULL,
    prompt_tokens BIGINT NOT NULL DEFAULT 0,
    eval_tokens   BIGINT NOT NULL DEFAULT 0,
    requests      BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

-- NULL = deployment default (INFERENCE_DAILY_TOKEN_BUDGET); 0 = unlimited.
ALTER TABLE users ADD COLUMN IF NOT EXISTS inference_daily_token_budget BIGINT;

INSERT INTO schema_migrations(version) VALUES (7) ON CONFLICT DO NOTHING;
```

- [ ] **Step 4: Run — verify pass**

```bash
.venv/bin/python -m pytest server/tests/test_migration_007.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/sql/007_inference_budgets.sql server/tests/test_migration_007.py
git commit -m "feat(inference): migration 007 — inference_usage + per-user budget column"
```

---

### Task 9 (E3): `inference_budget.py` — accounting + budget math

**Files:**
- Create: `server/services/inference_budget.py`
- Test: `server/tests/test_inference_budget.py`

**Interfaces:**
- All functions take an explicit `conn` (testable inside rolled-back transactions):
  - `effective_budget(conn, user_id, deployment_budget) -> int | None` — per-user column (NULL → deployment default; 0 → None/unlimited).
  - `spent_today(conn, user_id) -> tuple[int, int]` — (prompt, eval) for the UTC today row.
  - `budget_state(conn, user_id, deployment_budget) -> dict` — `{"remaining_tokens": int | None, "reset_at": iso}` (None = unlimited; `reset_at` = next UTC midnight, `...Z`).
  - `over_budget(conn, user_id, deployment_budget) -> bool`.
  - `record_usage(conn, user_id, prompt_tokens, eval_tokens)` — upsert-increment on `(user_id, utc_today)`.
  - `admin_usage(conn, days=7) -> list[dict]` — joined with users for email, newest day first, each row gains `tokens` = prompt+eval.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_inference_budget.py`:

```python
from datetime import datetime, timedelta, timezone

from server.auth.users import SEED_ADMIN_ID
from server.services import inference_budget as ib

U = SEED_ADMIN_ID


async def test_record_usage_increments(db_conn):
    await ib.record_usage(db_conn, U, 10, 5)
    await ib.record_usage(db_conn, U, 3, 2)
    assert await ib.spent_today(db_conn, U) == (13, 7)
    cur = await db_conn.execute(
        "SELECT requests FROM inference_usage WHERE user_id=%s", (U,))
    assert (await cur.fetchone())[0] == 2


async def test_budget_state_unlimited_when_no_budget(db_conn):
    state = await ib.budget_state(db_conn, U, None)
    assert state["remaining_tokens"] is None
    assert state["reset_at"].endswith("Z")


async def test_budget_state_remaining_math(db_conn):
    await ib.record_usage(db_conn, U, 60, 40)
    state = await ib.budget_state(db_conn, U, 200)
    assert state["remaining_tokens"] == 100


async def test_over_budget_boundary(db_conn):
    await ib.record_usage(db_conn, U, 100, 0)
    assert await ib.over_budget(db_conn, U, 200) is False   # spent == budget → 0 left… 
    # …actually spent(100) < budget(200): fine. Exhaust it:
    await ib.record_usage(db_conn, U, 100, 0)
    assert await ib.over_budget(db_conn, U, 200) is True


async def test_per_user_column_overrides_default(db_conn):
    await db_conn.execute(
        "UPDATE users SET inference_daily_token_budget=1000 WHERE id=%s", (U,))
    await ib.record_usage(db_conn, U, 300, 300)
    # deployment says 500, per-user says 1000 → 400 left
    state = await ib.budget_state(db_conn, U, 500)
    assert state["remaining_tokens"] == 400


async def test_zero_column_means_unlimited(db_conn):
    await db_conn.execute(
        "UPDATE users SET inference_daily_token_budget=0 WHERE id=%s", (U,))
    await ib.record_usage(db_conn, U, 10**9, 10**9)
    assert await ib.over_budget(db_conn, U, 100) is False


async def test_reset_at_is_next_utc_midnight(db_conn):
    state = await ib.budget_state(db_conn, U, 100)
    reset = datetime.fromisoformat(state["reset_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert now < reset <= now + timedelta(days=1)
    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)


async def test_admin_usage_joins_email(db_conn):
    await ib.record_usage(db_conn, U, 10, 5)
    rows = await ib.admin_usage(db_conn, days=1)
    assert len(rows) == 1
    assert rows[0]["tokens"] == 15
    assert rows[0]["email"]
```

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest server/tests/test_inference_budget.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `server/services/inference_budget.py`:

```python
"""Per-user daily inference budgets (gateway spec §4.3).

Day boundaries are UTC and computed in Python — a SQL `DEFAULT CURRENT_DATE`
would use the Postgres server's timezone and silently shift the reset time.
All functions take an explicit conn; callers own fail-open behavior (the
gateway logs and proceeds when the DB is down — chat never dies with Postgres).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _utc_today():
    return datetime.now(timezone.utc).date()


def _next_reset_at() -> datetime:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _normalize_budget(value: int | None) -> int | None:
    # 0 and None both mean unlimited (spec §4.3).
    return value if value else None


async def effective_budget(conn, user_id: str, deployment_budget: int | None) -> int | None:
    cur = await conn.execute(
        "SELECT inference_daily_token_budget FROM users WHERE id = %s", (user_id,)
    )
    row = await cur.fetchone()
    per_user = row[0] if row else None
    if per_user is not None:
        return _normalize_budget(per_user)
    return _normalize_budget(deployment_budget)


async def spent_today(conn, user_id: str) -> tuple[int, int]:
    cur = await conn.execute(
        "SELECT prompt_tokens, eval_tokens FROM inference_usage "
        "WHERE user_id = %s AND day = %s",
        (user_id, _utc_today()),
    )
    row = await cur.fetchone()
    return (row[0] or 0, row[1] or 0) if row else (0, 0)


async def budget_state(conn, user_id: str, deployment_budget: int | None) -> dict:
    """{remaining_tokens, reset_at}; remaining_tokens is None when unlimited."""
    reset_at = _next_reset_at().isoformat().replace("+00:00", "Z")
    budget = await effective_budget(conn, user_id, deployment_budget)
    if budget is None:
        return {"remaining_tokens": None, "reset_at": reset_at}
    prompt, evals = await spent_today(conn, user_id)
    return {
        "remaining_tokens": max(0, budget - (prompt + evals)),
        "reset_at": reset_at,
    }


async def over_budget(conn, user_id: str, deployment_budget: int | None) -> bool:
    state = await budget_state(conn, user_id, deployment_budget)
    remaining = state["remaining_tokens"]
    return remaining is not None and remaining <= 0


async def record_usage(conn, user_id: str, prompt_tokens: int, eval_tokens: int) -> None:
    await conn.execute(
        "INSERT INTO inference_usage (user_id, day, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, %s, %s, %s, 1) "
        "ON CONFLICT (user_id, day) DO UPDATE SET "
        "prompt_tokens = inference_usage.prompt_tokens + EXCLUDED.prompt_tokens, "
        "eval_tokens = inference_usage.eval_tokens + EXCLUDED.eval_tokens, "
        "requests = inference_usage.requests + 1",
        (user_id, _utc_today(), prompt_tokens, eval_tokens),
    )


async def admin_usage(conn, days: int = 7) -> list[dict]:
    since = _utc_today() - timedelta(days=days - 1)
    cur = await conn.execute(
        "SELECT u.email, s.user_id, s.day, s.prompt_tokens, s.eval_tokens, s.requests "
        "FROM inference_usage s JOIN users u ON u.id = s.user_id "
        "WHERE s.day >= %s ORDER BY s.day DESC, u.email",
        (since,),
    )
    keys = ["email", "user_id", "day", "prompt_tokens", "eval_tokens", "requests"]
    out = []
    for r in await cur.fetchall():
        d = dict(zip(keys, r))
        d["user_id"] = str(d["user_id"])
        d["tokens"] = d["prompt_tokens"] + d["eval_tokens"]
        out.append(d)
    return out
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest server/tests/test_inference_budget.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add server/services/inference_budget.py server/tests/test_inference_budget.py
git commit -m "feat(inference): budget accounting service (UTC days, fail-open callers)"
```

---

### Task 10 (E3): Enforce budgets in the gateway (429 pre-check + stream tap)

**Files:**
- Modify: `server/routers/inference.py`
- Test: extend `server/tests/test_inference_router.py` (budget block)

**Interfaces:**
- `GET /v1/inference/models` response becomes `{models, budget: {remaining_tokens, reset_at}}` (budget omitted on DB failure — fail-open).
- `POST /v1/inference/chat`:
  - Pre-check (before the upstream call): `over_budget` → **429** with `detail = {remaining_tokens, reset_at}`. Wrapped: any DB error during the check logs and proceeds.
  - Stream tap: `_UsageTap` scans the passthrough for the final chunk (`done: true`) and captures `prompt_eval_count` / `eval_count`. Accounting runs in the generator's `finally` **only when a final chunk was seen** (aborted streams never account — spec §8 retry semantics), on a **fresh pooled connection** (the handler's `get_conn` context has closed by then) — `get_pool()` from `..db`, shimmed in tests exactly like `test_docs_authz.py`.
- Consumes: `inference_budget.*`, `model_router.get_config().daily_token_budget`, `db.get_pool`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_inference_router.py`:

```python
# ---- Budgets (E3) ----

class _PoolShim:
    """Routes the router's post-stream accounting at the transactional test
    connection (same pattern as test_docs_authz.py)."""
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        conn = self._conn
        class _Ctx:
            async def __aenter__(self):
                return conn
            async def __aexit__(self, *a):
                return False
        return _Ctx()


async def _exhaust_budget(db_conn, user_id="u1", budget=100):
    from server.services import inference_budget as ib
    await ib.record_usage(db_conn, user_id, 100, 0)
    return budget


async def test_chat_429_when_over_budget(db_conn, app, client, monkeypatch):
    monkeypatch.setenv("INFERENCE_DAILY_TOKEN_BUDGET", "100")
    await _exhaust_budget(db_conn)
    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["remaining_tokens"] == 0
    assert detail["reset_at"].endswith("Z")


async def test_models_includes_budget(db_conn, app, client, monkeypatch):
    monkeypatch.setenv("INFERENCE_DAILY_TOKEN_BUDGET", "1000")
    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    upstream = app  # handler already programmed by fixture default
    r = await client.get("/v1/inference/models")
    assert r.status_code == 200
    assert r.json()["budget"]["remaining_tokens"] == 1000


async def test_stream_accounts_usage_from_final_chunk(db_conn, app, client, upstream, monkeypatch):
    from server.services import inference_budget as ib
    monkeypatch.setattr(inf, "get_pool", lambda: _PoolShim(db_conn))
    upstream["handler"] = lambda r: httpx.Response(
        200, content=NDJSON, headers={"content-type": "application/x-ndjson"})
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 200
    assert await ib.spent_today(db_conn, "u1") == (10, 5)


async def test_aborted_stream_does_not_account(db_conn, app, client, upstream, monkeypatch):
    from server.services import inference_budget as ib
    monkeypatch.setattr(inf, "get_pool", lambda: _PoolShim(db_conn))
    # No done:true chunk — a stream the client (or Ollama) cut short.
    upstream["handler"] = lambda r: httpx.Response(
        200, content=b'{"model":"m","message":{"role":"assistant","content":"Hel"}}\n',
        headers={"content-type": "application/x-ndjson"})
    await client.post("/v1/inference/chat", json=VALID_BODY)
    assert await ib.spent_today(db_conn, "u1") == (0, 0)


async def test_budget_check_failure_fails_open(db_conn, app, client, monkeypatch):
    async def boom(conn, user_id, budget):
        raise RuntimeError("db down")
    monkeypatch.setattr(inf.inference_budget, "over_budget", boom)
    upstream["handler"] = lambda r: httpx.Response(
        200, content=NDJSON, headers={"content-type": "application/x-ndjson"})
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 200
```

Note: these tests need the `upstream` fixture where referenced; keep the existing `app`/`client` fixtures (they already start the client with the MockTransport).

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest server/tests/test_inference_router.py -q`
Expected: FAIL — no budget logic yet (the 429 test gets 200; models has no `budget` key; accounting never happens).

- [ ] **Step 3: Implement**

In `server/routers/inference.py`:

Add imports:

```python
from ..db import get_pool
from ..services import inference_budget
```

Add the tap class (module level, above the endpoints):

```python
class _UsageTap:
    """Scans the NDJSON stream for the final chunk's token counts WITHOUT
    altering a single forwarded byte. Only a completed generation (done:true
    seen) counts — aborted streams never account (spec §8)."""

    def __init__(self) -> None:
        self._buf = b""
        self.usage: dict | None = None

    def feed(self, chunk: bytes) -> None:
        if self.usage is not None:
            return
        self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("done"):
                self.usage = {
                    "prompt_eval_count": payload.get("prompt_eval_count"),
                    "eval_count": payload.get("eval_count"),
                }
```

`list_models` gains a conn and the budget block:

```python
@router.get("/models")
async def list_models(
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    cfg = model_router.get_config()
    # ... existing upstream fetch + filtering, then:
    out = {"models": names}
    try:
        out["budget"] = await inference_budget.budget_state(
            conn, principal.user_id, cfg.daily_token_budget
        )
    except Exception:
        logger.warning("Budget lookup failed (fail-open)", exc_info=True)
    return out
```

`chat` gains the pre-check and the tap:

```python
@router.post("/chat")
async def chat(
    body: ChatRequest,
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    cfg = model_router.get_config()
    if not model_router.is_model_allowed(cfg, body.model):
        raise HTTPException(status_code=422, detail=f"Model '{body.model}' is not allowed on this deployment")
    # Pre-check (advisory): a 429 before any upstream call saves the tokens a
    # doomed request would spend. Accounting of actuals happens after the
    # stream completes.
    try:
        if await inference_budget.over_budget(conn, principal.user_id, cfg.daily_token_budget):
            state = await inference_budget.budget_state(
                conn, principal.user_id, cfg.daily_token_budget
            )
            raise HTTPException(status_code=429, detail=state)
    except HTTPException:
        raise
    except Exception:
        logger.warning("Budget pre-check failed (fail-open)", exc_info=True)

    # ... existing upstream send + non-200 forwarding, then:

    tap = _UsageTap()

    async def _passthrough():
        try:
            async for chunk in upstream.aiter_raw():
                tap.feed(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            if tap.usage is not None:
                # The handler's get_conn context is long closed by now — open
                # a fresh pooled connection. DB failure only loses accounting,
                # never the chat.
                try:
                    pool = get_pool()
                    async with pool.connection() as conn2:
                        await inference_budget.record_usage(
                            conn2,
                            principal.user_id,
                            tap.usage["prompt_eval_count"] or 0,
                            tap.usage["eval_count"] or 0,
                        )
                except Exception:
                    logger.warning("Usage accounting failed (fail-open)", exc_info=True)

    return StreamingResponse(_passthrough(), media_type="application/x-ndjson")
```

Also add a pure unit test for the tap itself (same file):

```python
def test_usage_tap_handles_split_lines_and_stops_at_done():
    tap = inf._UsageTap()
    tap.feed(b'{"model":"m","message":{"content":"He"}')
    tap.feed(b'"}\n{"done":true,"prompt_eval_count":7,"eval_count":3}\n')
    tap.feed(b'{"done":true,"prompt_eval_count":999,"eval_count":999}\n')  # ignored after done
    assert tap.usage == {"prompt_eval_count": 7, "eval_count": 3}
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest server/tests/test_inference_router.py server/tests/test_inference_budget.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routers/inference.py server/tests/test_inference_router.py
git commit -m "feat(inference): per-user daily token budgets — 429 pre-check + stream accounting"
```

---

### Task 11 (E3): Admin — usage view + per-user budget knob

**Files:**
- Modify: `server/routers/admin.py`, `server/auth/users.py`
- Test: `server/tests/test_admin_router.py` (extend)

**Interfaces:**
- `GET /v1/admin/inference/usage?days=7` (admin-only, `days` clamped 1..90) → `inference_budget.admin_usage(conn, days)`.
- `PATCH /v1/admin/users/{id}` body grows `inference_daily_token_budget: int | None`. Use `model_dump(exclude_unset=True)` so **absent = untouched, explicit `null` = clear back to deployment default**; a present value must be ≥ 0 (0 = unlimited).
- `users.set_inference_budget(conn, user_id, budget)`; `users._COLS` gains `inference_daily_token_budget` so `list_users` surfaces it.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_admin_router.py` (follow its existing `_app` + ASGITransport pattern):

```python
async def test_inference_usage_admin_only(db_conn):
    # member → 403 (no rows needed)
    ...


async def test_admin_inference_usage_returns_rows(db_conn):
    from server.services import inference_budget as ib
    await ib.record_usage(db_conn, SEED_ADMIN_ID, 10, 5)
    p = deps.Principal(user_id=SEED_ADMIN_ID, email="a@x.io", role="admin")
    # app with conn override + get_current_user override → GET /v1/admin/inference/usage
    assert rows[0]["tokens"] == 15


async def test_patch_sets_and_clears_budget(db_conn):
    # PATCH {"inference_daily_token_budget": 1000} → users row updated
    # PATCH {"inference_daily_token_budget": null} → column back to NULL
    # PATCH {} → column untouched
    ...
```

(Flesh out with the file's existing `_app(db_conn, principal)` helper; assert on `users.get_user(...)["inference_daily_token_budget"]`.)

- [ ] **Step 2: Run — verify fail**

Run: `.venv/bin/python -m pytest server/tests/test_admin_router.py -q`
Expected: FAIL — endpoint/field missing.

- [ ] **Step 3: Implement**

In `server/auth/users.py`: add `inference_daily_token_budget` to `_COLS` and to the row-dict keys (both places), plus:

```python
async def set_inference_budget(conn, user_id: str, budget: int | None) -> None:
    await conn.execute(
        "UPDATE users SET inference_daily_token_budget=%s, updated_at=now() WHERE id=%s",
        (budget, user_id),
    )
```

In `server/routers/admin.py`: import `inference_budget`; extend `UserPatchIn` with `inference_daily_token_budget: int | None = None`; switch `patch_user` to `data = body.model_dump(exclude_unset=True)` and handle the new key (validate `>= 0`, call `users.set_inference_budget`; keep the existing status/role handling, now also driven from `data`); add:

```python
@router.get("/inference/usage")
async def inference_usage(
    days: int = 7,
    _: deps.Principal = Depends(deps.require_admin),
    conn=Depends(deps.get_conn),
):
    days = max(1, min(90, days))
    return await inference_budget.admin_usage(conn, days=days)
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/bin/python -m pytest server/tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routers/admin.py server/auth/users.py server/tests/test_admin_router.py
git commit -m "feat(admin): inference usage view + per-user daily budget knob"
```

---

### Task 12 (E3): Frontend budget UX — meter, 429 toast, disable send

**Files:**
- Modify: `src/lib/chatTransport.js` (+ test), `src/hooks/useChatEngine.js`, `src/App.jsx`, `src/components/ChatSidebar.jsx`, `src/components/ChatView.jsx`

**Interfaces:**
- `chatTransport.js` grows:
  - `budgetDetail(res) → Promise<{remaining_tokens, reset_at} | null>` — non-429 (or unparsable) → null. Reads the body only on 429.
  - `formatResetAt(iso) → string` — local `HH:MM` for toasts.
- `useChatEngine`:
  - new state `inferenceBudget` (null until known; set from `refreshModels` in server mode: `data.budget ?? null`; cleared to null in local mode); exposed on the hook's return.
  - `callChat` intercepts 429 **before** any caller-level fallback logic: sets the budget, toasts once per exhaustion, `logEvent('budget', ...)`, and throws so `sendMessage`'s existing error path renders the aborted turn cleanly. The think-level/tools retry chain must never see a 429.
- UI: ChatSidebar shows `N tokens left today` under the model picker when `inferenceBudget?.remaining_tokens != null`; ChatView's `canSend` (ChatView.jsx:393) gains `&& inferenceBudget?.remaining_tokens !== 0`.

- [ ] **Step 1: Write the failing tests**

Append to `src/lib/chatTransport.test.js`:

```javascript
import { budgetDetail, formatResetAt } from './chatTransport';

describe('budgetDetail', () => {
  it('returns the detail on a 429', async () => {
    const res = new Response(JSON.stringify({
      detail: { remaining_tokens: 0, reset_at: '2026-09-18T00:00:00Z' },
    }), { status: 429 });
    expect(await budgetDetail(res)).toEqual({ remaining_tokens: 0, reset_at: '2026-09-18T00:00:00Z' });
  });

  it('returns null on non-429', async () => {
    const res = new Response('{}', { status: 200 });
    expect(await budgetDetail(res)).toBeNull();
  });

  it('returns null on an unparsable 429 body', async () => {
    const res = new Response('nope', { status: 429 });
    expect(await budgetDetail(res)).toBeNull();
  });
});

describe('formatResetAt', () => {
  it('renders a local time string', () => {
    const out = formatResetAt('2026-09-18T00:00:00Z');
    expect(out).toMatch(/\d{1,2}:\d{2}/);
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `npm run test:run -- src/lib/chatTransport.test.js`
Expected: FAIL — exports missing.

- [ ] **Step 3: Implement**

In `src/lib/chatTransport.js`:

```javascript
// 429 from the gateway means the daily token budget is gone. Callers must
// handle it BEFORE any 4xx retry chain — retries re-spend tokens.
export async function budgetDetail(res) {
  if (res.status !== 429) return null;
  try {
    const body = await res.json();
    const detail = body?.detail;
    return detail && typeof detail === 'object' ? detail : null;
  } catch {
    return null;
  }
}

export function formatResetAt(iso) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}
```

In `src/hooks/useChatEngine.js`:

- import `budgetDetail, formatResetAt` alongside the transport import;
- add `const [inferenceBudget, setInferenceBudget] = useState(null);`
- wrap the transport in the budget guard:

```javascript
const callChat = useCallback(async (body, signal) => {
    const res = await chatFetch(inferenceSource, chatHosts, CHAT_PATH[inferenceSource], {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
    });
    const detail = await budgetDetail(res);
    if (detail) {
        setInferenceBudget(detail);
        showToast?.(`Daily inference budget exhausted — resets at ${formatResetAt(detail.reset_at)}`, 6000);
        logEvent('budget', 'daily token budget exhausted');
        throw new Error('Daily inference budget exhausted');
    }
    return res;
}, [inferenceSource, apiHost, apiPort, ollamaHost, ollamaPort, showToast, logEvent]);
```

- in `refreshModels` after the ok-branch parses `data`: `setInferenceBudget(inferenceSource === 'server' ? (data?.budget ?? null) : null);`
- return `inferenceBudget` from the hook.

In `src/App.jsx`: destructure `inferenceBudget: chatInferenceBudget` from the engine; pass `inferenceBudget={chatInferenceBudget}` to both `<ChatSidebar>` and `<ChatView>`.

In `src/components/ChatSidebar.jsx` (accept `inferenceBudget`), under the model picker:

```jsx
{inferenceSource === 'server' && inferenceBudget?.remaining_tokens != null && (
    <p className={`text-[9px] ${inferenceBudget.remaining_tokens === 0 ? 'text-red-400' : theme.textMuted} px-1`}>
        {inferenceBudget.remaining_tokens.toLocaleString()} tokens left today
    </p>
)}
```

In `src/components/ChatView.jsx`: accept `inferenceBudget`; extend the `canSend` computation (line ~393) with `&& inferenceBudget?.remaining_tokens !== 0`.

- [ ] **Step 4: Run + commit**

Run: `npm run test:run && npm run lint`
Expected: PASS.

```bash
git add src/lib/chatTransport.js src/lib/chatTransport.test.js src/hooks/useChatEngine.js src/App.jsx src/components/ChatSidebar.jsx src/components/ChatView.jsx
git commit -m "feat(inference): budget meter, 429 toast, send disabled when exhausted"
```

---

### Task 13: Docs, deployment, and the manual end-to-end pass

**Files:**
- Modify: `.env.example`, `README.md`, `deploy/nginx/natural-reader.conf`, `docs/chat.oraian.net.sample`, `CHANGELOG.md`, `HANDOVER.md`

- [ ] **Step 1: `.env.example`**

Add an `# ─── Inference gateway (model router) ───` section after the Ollama block:

```bash
# Comma-separated model allowlist served through /v1/inference/*. Unset = all
# installed models (local-dev convenience). Set it on any shared deployment —
# it's also what the SPA's model dropdown offers.
# INFERENCE_MODELS=gemma3,qwen2.5

# Default per-user daily token budget (prompt + eval). Unset or 0 = unlimited.
# Per-user override: Admin panel → user budget (NULL = this default, 0 = unlimited).
# Budgets roll at UTC midnight.
# INFERENCE_DAILY_TOKEN_BUDGET=500000

# Upstream read timeout (seconds) for streamed generations.
# INFERENCE_TIMEOUT_S=300

# Model for server-side summarize tasks (web_search page summaries, and the
# document-library description generation to come). Replaces the older
# WEB_SEARCH_SUMMARY_MODEL name, which still works as a fallback.
# SUMMARIZE_MODEL=llama3.2:3b
```

- [ ] **Step 2: README.md**

- Security & Hardening: replace the "`/api/*` (Ollama) is not yet behind app auth" paragraph (~line 507) with the new reality: inference goes through `/v1/inference/*` (session cookie or PAT), allowlist + daily budgets configurable; the nginx `/api/` block should be **deleted** from both the example config and `docs/chat.oraian.net.sample` (Ollama becomes backend-only; loopback bind + no proxy exposure). Update the threat-model table's `/api/chat` and `/api/tags` rows to note they're now gated.
- API Endpoints: add a "#### Inference Gateway (same FastAPI server)" table — `GET /v1/inference/models` (allowlisted model list + budget), `POST /v1/inference/chat` (validated NDJSON streaming passthrough; 429 when the daily budget is exhausted). Mention the SPA's **Inference source: Server | Local Ollama** setting in the chat feature bullets.
- The minimal nginx example: remove the `/api/` location block, add a sentence that local-Ollama mode requires the user's own proxying and server mode needs nothing.

- [ ] **Step 3: Reference configs**

- `deploy/nginx/natural-reader.conf`: delete the `location /api/` block; add a comment that all inference now flows through `/v1/` (server mode) and local mode is the browser's own business.
- `docs/chat.oraian.net.sample`: same removal for its `/api/` block (keep the commented hardening recipes that still apply).

- [ ] **Step 4: CHANGELOG + HANDOVER**

- `CHANGELOG.md` under `[Unreleased]`: gateway, allowlist, budgets, source switch, admin usage view, migration 007, nginx `/api/` removal note.
- `HANDOVER.md`: new session entry (what shipped, how to run, what's next: document-library RAG now unblocked).

- [ ] **Step 5: Manual end-to-end pass**

Prerequisites: Postgres + backend + Ollama + `npm run dev` (e.g. `./startup.sh up` in one shell — the `AUTH_ENABLED=false` loopback bypass exercises the gateway with zero ceremony — and `npm run dev` in another; pull `gemma3` and `nomic-embed-text`).

1. **Server mode (default):** model dropdown populates through `/v1/inference` (devtools → network: no direct `/api/tags`); send a message; tokens stream live (no buffering).
2. **Tool loop:** index a doc, ask something answerable from it → `search_document` round-trip works through the gateway (tool-call disclosure + final answer).
3. **Attachments + thinking:** paste an image to a vision model; set Thinking to a level; verify the think→boolean fallback toast still behaves (gateway forwards the 400 status).
4. **Local mode:** switch to Local Ollama, set host/port; devtools shows direct `/api/chat` to `:11434`, no credentials; everything works as before.
5. **Allowlist:** `INFERENCE_MODELS=gemma3` → restart backend → dropdown only offers gemma3; a request for another model 422s.
6. **Budgets:** `INFERENCE_DAILY_TOKEN_BUDGET=100` → one short reply; next request 429s → toast names the reset time; send disabled. `GET /v1/admin/inference/usage` (or the Admin panel once surfaced) shows the spent row.
7. **Streams clean:** `npm run test:run`, `.venv/bin/pytest server/tests/`, `npm run lint` — all green.

- [ ] **Step 6: Commit**

```bash
git add .env.example README.md deploy/nginx/natural-reader.conf docs/chat.oraian.net.sample CHANGELOG.md HANDOVER.md
git commit -m "docs(inference): gateway config, hardening + proxy guidance, changelog"
```

---

## Notes for the executor

- **Postgres must be up** for anything touching `db_conn` (Tasks 2, 8-11): `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres` (the container stops between sessions — restart it). Frontend tests need nothing.
- **After Task 8, drop the test DB once** so the session fixture applies 007 (the fixture skips already-applied versions).
- **Ollama is only needed for Task 13's manual pass** — every automated test mocks the upstream.
- **Commits are pre-approved** (owner decision, 2026-09-17): one Conventional Commit per green task; never commit a red suite.
- The extension needs nothing: it speaks Bearer PATs already and only calls TTS endpoints — the spec §8 "point its inference base URL at /v1/inference" concern only applies if/when the extension grows chat features.
- Vite's `/api` dev proxy **stays** — local mode depends on it.

## Self-Review

**Spec coverage:** authenticated pipe + byte-faithful streaming (T2-T3, spec §4.1/§4.4); allowlist (T1+T2, §4.2); task-routing table + services reroute (T1, T7, §4.2); budgets — schema, 429 + detail, admin usage, per-user override (T8-T11, §4.3); frontend source switch + callChat collapse (T4-T6, §4.5); env config (T1, T13, §4.6); nginx `/api/` removal (T13, §4.7); testing strategy (§6) mapped into every task; phasing E1=T2-T6, E2=T1+T7, E3=T8-T12 — each deployable alone (allowlist/budgets default off).

**Deliberate additions beyond the letter of the spec** (flagged here for review): admin PATCH grows the per-user budget field (otherwise migration 007's column is unreachable without psql); `SUMMARIZE_MODEL` falls back to the existing `WEB_SEARCH_SUMMARY_MODEL`; budget pre-check and accounting fail open on DB errors (consistent with the "TTS never dies" philosophy); budget days are UTC-computed in Python.

**Known execution risks:** the `_PoolShim` for post-stream accounting assumes the generator's `pool.connection()` context is the only DB touch after the handler returns (true as designed); httpx `MockTransport` returns whole bodies, so true chunk-boundary streaming fidelity is covered by the tap's split-line unit test plus the Task 13 manual pass; `TestClient` is banned in this repo's newer tests — use `httpx.AsyncClient` + `ASGITransport` everywhere new.
