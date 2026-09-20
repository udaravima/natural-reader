import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import inference as inf

PRINCIPAL = deps.Principal(user_id="u1", email="a@x.io", role="member",
                            capabilities=frozenset({"chat"}))

NDJSON = (
    b'{"model":"m","message":{"role":"assistant","content":"Hel"}}\n'
    b'{"model":"m","message":{"role":"assistant","tool_calls":[{"function":'
    b'{"name":"search_document","arguments":{"query":"x"}}}]}}\n'
    b'{"model":"m","message":{"role":"assistant","content":""},"done":true,'
    b'"done_reason":"stop","total_duration":1,"prompt_eval_count":10,"eval_count":5}\n'
)

def stream_response(content: bytes = NDJSON, status_code: int = 200):
    """A genuinely streamable mock response. `Response(content=bytes)` is
    eagerly read at construction (is_stream_consumed=True), which would make
    the gateway's aiter_raw() raise StreamConsumed — an async-iterator body
    keeps the stream unconsumed, like a real Ollama NDJSON reply."""
    async def gen():
        yield content
    return httpx.Response(
        status_code,
        content=gen(),
        headers={"content-type": "application/x-ndjson"},
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
    requests; every upstream request is recorded for assertions."""
    state = {
        "handler": lambda request: httpx.Response(200, json={"models": []}),
        "requests": [],
    }

    def handler(request):
        state["requests"].append(request)
        return state["handler"](request)

    state["transport"] = httpx.MockTransport(handler)
    return state


@pytest.fixture
async def app(db_conn, upstream):
    application = FastAPI()
    application.include_router(inf.router)
    # The gateway's budget lookups need a conn — hand it the transactional
    # test connection everywhere (individual tests may re-override).
    async def _conn():
        yield db_conn

    application.dependency_overrides[deps.get_conn] = _conn
    await inf.start_client(transport=upstream["transport"])
    yield application
    await inf.stop_client()


@pytest.fixture
async def client(app):
    app.dependency_overrides[deps.get_current_user] = lambda: PRINCIPAL
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        yield c


async def _no_auth(app, db_conn):
    # Keep the real get_current_user (with the overridden conn) so that
    # no-credential → 401 rather than a get_pool() RuntimeError.
    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn


async def test_models_requires_auth(db_conn, app):
    await _no_auth(app, db_conn)
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
    await _no_auth(app, db_conn)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/inference/chat", json=VALID_BODY)
        assert r.status_code == 401


async def test_chat_streams_ndjson_byte_faithful(client, upstream):
    upstream["handler"] = lambda r: stream_response()
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.content == NDJSON


async def test_chat_forwards_body_upstream(client, upstream):
    upstream["handler"] = lambda r: stream_response()
    await client.post("/v1/inference/chat", json=VALID_BODY)
    body = json.loads(upstream["requests"][0].content)
    assert body["model"] == "m"
    assert body["stream"] is True
    assert body["think"] == "low"
    assert body["keep_alive"] == "5m"
    assert body["options"] == {"num_ctx": 4096}
    assert "images" not in body["messages"][0]


async def test_chat_rejects_unknown_top_level_field(client):
    r = await client.post("/v1/inference/chat", json={**VALID_BODY, "format": "json"})
    assert r.status_code == 422


async def test_chat_rejects_unknown_message_field(client):
    bad = {**VALID_BODY, "messages": [{"role": "user", "content": "hi", "tool": 1}]}
    r = await client.post("/v1/inference/chat", json=bad)
    assert r.status_code == 422


async def test_chat_rejects_unknown_option_field(client):
    bad = {**VALID_BODY, "options": {"num_ctx": 4096, "temperature": 0.5}}
    r = await client.post("/v1/inference/chat", json=bad)
    assert r.status_code == 422


async def test_chat_accepts_images_and_tool_calls_round(client, upstream):
    upstream["handler"] = lambda r: stream_response()
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
    sent = json.loads(upstream["requests"][0].content)
    assert sent["messages"][0]["images"] == ["aGVsbG8="]
    assert sent["messages"][1]["tool_calls"][0]["function"]["name"] == "search_document"


async def test_chat_allowlist_rejects_other_models(client, monkeypatch):
    monkeypatch.setenv("INFERENCE_MODELS", "gemma3")
    r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 422
    assert "not allowed" in r.text


async def test_chat_allowlist_accepts_listed_model(client, upstream, monkeypatch):
    monkeypatch.setenv("INFERENCE_MODELS", "m")
    upstream["handler"] = lambda r: stream_response()
    assert (await client.post("/v1/inference/chat", json=VALID_BODY)).status_code == 200


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


async def _authed_db_client(app, db_conn):
    """A client whose principal is a REAL users row (the inference_usage FK
    requires it) and whose get_conn is the transactional test connection."""
    from server.auth.users import resolve_or_provision_user

    u = await resolve_or_provision_user(db_conn, iss="i", sub="inf", email="inf@x.io")
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=u["id"], email=u["email"], role="member",
        capabilities=frozenset({"chat"}),
    )

    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn
    return u["id"], httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_chat_429_when_over_budget(db_conn, app, monkeypatch):
    from server.services import inference_budget as ib

    monkeypatch.setenv("INFERENCE_DAILY_TOKEN_BUDGET", "100")
    uid, client = await _authed_db_client(app, db_conn)
    await ib.record_usage(db_conn, uid, 100, 0)  # exhaust it

    async with client:
        r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["remaining_tokens"] == 0
    assert detail["reset_at"].endswith("Z")


async def test_chat_allowed_when_under_budget(db_conn, app, upstream, monkeypatch):
    from server.services import inference_budget as ib

    monkeypatch.setenv("INFERENCE_DAILY_TOKEN_BUDGET", "100000")
    uid, client = await _authed_db_client(app, db_conn)
    await ib.record_usage(db_conn, uid, 10, 5)

    upstream["handler"] = lambda r: stream_response()
    async with client:
        r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 200


async def test_models_includes_budget(db_conn, app, monkeypatch):
    monkeypatch.setenv("INFERENCE_DAILY_TOKEN_BUDGET", "1000")
    uid, client = await _authed_db_client(app, db_conn)

    async with client:
        r = await client.get("/v1/inference/models")
    assert r.status_code == 200
    assert r.json()["budget"]["remaining_tokens"] == 1000


async def test_models_budget_absent_when_unlimited(db_conn, app, monkeypatch):
    monkeypatch.delenv("INFERENCE_DAILY_TOKEN_BUDGET", raising=False)
    uid, client = await _authed_db_client(app, db_conn)

    async with client:
        body = (await client.get("/v1/inference/models")).json()
    assert body["budget"]["remaining_tokens"] is None


async def test_stream_accounts_usage_from_final_chunk(db_conn, app, upstream, monkeypatch):
    from server.services import inference_budget as ib

    monkeypatch.setattr(inf, "get_pool", lambda: _PoolShim(db_conn))
    uid, client = await _authed_db_client(app, db_conn)
    upstream["handler"] = lambda r: stream_response()

    async with client:
        r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 200
    assert await ib.spent_today(db_conn, uid) == (10, 5)


async def test_aborted_stream_does_not_account(db_conn, app, upstream, monkeypatch):
    from server.services import inference_budget as ib

    monkeypatch.setattr(inf, "get_pool", lambda: _PoolShim(db_conn))
    uid, client = await _authed_db_client(app, db_conn)
    # No done:true chunk — a stream the client (or Ollama) cut short.
    upstream["handler"] = lambda r: stream_response(
        b'{"model":"m","message":{"role":"assistant","content":"Hel"}}\n')

    async with client:
        await client.post("/v1/inference/chat", json=VALID_BODY)
    assert await ib.spent_today(db_conn, uid) == (0, 0)


async def test_budget_pre_check_failure_fails_open(db_conn, app, upstream, monkeypatch):
    async def boom(conn, user_id, budget):
        raise RuntimeError("db down")

    monkeypatch.setattr(inf.inference_budget, "over_budget", boom)
    uid, client = await _authed_db_client(app, db_conn)
    upstream["handler"] = lambda r: stream_response()

    async with client:
        r = await client.post("/v1/inference/chat", json=VALID_BODY)
    assert r.status_code == 200


def test_usage_tap_handles_split_lines_and_stops_at_done():
    tap = inf._UsageTap()
    tap.feed(b'{"model":"m","message":{"content":"He"}')
    tap.feed(b'"}\n{"done":true,"prompt_eval_count":7,"eval_count":3}\n')
    # Everything after the final chunk is ignored — no double capture.
    tap.feed(b'{"done":true,"prompt_eval_count":999,"eval_count":999}\n')
    assert tap.usage == {"prompt_eval_count": 7, "eval_count": 3}


def test_usage_tap_ignores_unparsable_lines():
    tap = inf._UsageTap()
    tap.feed(b'not json\n{"done":true,"prompt_eval_count":1,"eval_count":2}\n')
    assert tap.usage == {"prompt_eval_count": 1, "eval_count": 2}
