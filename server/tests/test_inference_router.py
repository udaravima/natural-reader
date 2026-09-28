import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import inference as inf

PRINCIPAL = deps.Principal(user_id="u1", email="a@x.io", role="member",
                            capabilities=frozenset({"chat"}))


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
async def app(db_conn, upstream, monkeypatch):
    from server.llm import router as llm_router
    application = FastAPI()
    application.include_router(inf.router)

    async def _conn():
        yield db_conn

    application.dependency_overrides[deps.get_conn] = _conn
    monkeypatch.delenv("INFERENCE_PROVIDERS", raising=False)
    await llm_router.start_router(transport=upstream["transport"])
    yield application
    await llm_router.stop_router()


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


def _tags_and_show(names):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in names] + [{"other": 1}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"],
                                             "model_info": {"x.context_length": 4096}})
        return httpx.Response(404)
    return handler


async def test_models_lists_ids_with_provider_and_capabilities(client, upstream):
    upstream["handler"] = _tags_and_show(["gemma3", "qwen2.5"])
    r = await client.get("/v1/inference/models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert [m["id"] for m in models] == ["ollama:gemma3", "ollama:qwen2.5"]
    assert models[0] == {"id": "ollama:gemma3", "provider": "ollama", "kind": "ollama", "name": "gemma3",
                         "capabilities": {"tools": True, "thinking": False, "vision": False,
                                          "audio": False, "contextWindow": 4096}}


async def test_models_filtered_by_allowlist(client, upstream, monkeypatch):
    from server.llm import router as llm_router
    monkeypatch.setenv("INFERENCE_MODELS", "gemma3")
    await llm_router.stop_router()
    await llm_router.start_router(transport=upstream["transport"])
    upstream["handler"] = _tags_and_show(["gemma3", "qwen2.5"])
    r = await client.get("/v1/inference/models")
    assert [m["id"] for m in r.json()["models"]] == ["ollama:gemma3"]


async def test_models_every_provider_down_is_502(client, upstream):
    def boom(request):
        raise httpx.ConnectError("nope")
    upstream["handler"] = boom
    r = await client.get("/v1/inference/models")
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "providers_unreachable"


# ---- Budgets (E3) ----

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


async def test_raw_chat_passthrough_is_gone(client):
    r = await client.post("/v1/inference/chat", json={"model": "m", "messages": []})
    assert r.status_code in (404, 405)
