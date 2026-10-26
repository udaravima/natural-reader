import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import SEED_ADMIN_ID
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
                                          "audio": False, "contextWindow": 4096},
                         "limits": {"numCtxMax": 32768, "keepAliveMaxS": 1800},
                         "context_size": {"fixed": None, "auto": 16384}}


async def test_ollama_models_carry_the_deployment_limits(client, upstream, monkeypatch):
    """v2.4 Task F: the Settings page offers only what the server will honour."""
    monkeypatch.setenv("INFERENCE_NUM_CTX_MAX", "16384")
    monkeypatch.setenv("INFERENCE_KEEP_ALIVE_MAX", "-1")
    upstream["handler"] = _tags_and_show(["gemma3"])
    models = (await client.get("/v1/inference/models")).json()["models"]
    assert models[0]["limits"] == {"numCtxMax": 16384, "keepAliveMaxS": None}


async def test_models_omits_the_configured_embedding_model(client, upstream, monkeypatch):
    """GET /v1/inference/models is the chat model picker's source; the
    embedding model isn't a chat model and picking it fails (walk fix 4).
    Ollama's /api/tags reports it with a ':latest' suffix even though
    EMBEDDING_MODEL is set without one, so the comparison must ignore it."""
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    upstream["handler"] = _tags_and_show(["gemma3", "nomic-embed-text:latest"])
    r = await client.get("/v1/inference/models")
    assert [m["id"] for m in r.json()["models"]] == ["ollama:gemma3"]


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


# ---- Admin console v2 phase 2a: the console narrows the models and sizes the local ones ----

@pytest.fixture(autouse=True)
def _no_deployment_context_size(monkeypatch):
    monkeypatch.delenv("INFERENCE_LOCAL_NUM_CTX", raising=False)


@pytest.fixture
async def client(app):
    # A real users row as the principal: the budget lookup then never fails, and so never aborts the test's
    # transaction between two requests.
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=SEED_ADMIN_ID, email="a@x.io", role="member", capabilities=frozenset({"chat"}))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _save_models(db_conn, **changes):
    from server.services import console_settings, model_settings
    await console_settings.save(db_conn, model_settings.SECTION, changes, updated_by=SEED_ADMIN_ID)


async def _restart_router(upstream, monkeypatch, **env):
    from server.llm import router as llm_router
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    await llm_router.stop_router()
    await llm_router.start_router(transport=upstream["transport"])


async def test_models_list_follows_the_console_selection(client, db_conn, upstream):
    upstream["handler"] = _tags_and_show(["gemma3", "qwen2.5"])
    ids = lambda r: [m["id"] for m in r.json()["models"]]
    assert ids(await client.get("/v1/inference/models")) == ["ollama:gemma3", "ollama:qwen2.5"]   # empty: the env list
    await _save_models(db_conn, chat_models=["ollama:qwen2.5:latest"])   # :latest on either side doesn't matter
    assert ids(await client.get("/v1/inference/models")) == ["ollama:qwen2.5"]
    await _save_models(db_conn, chat_models=["ollama:gone"])   # a stale selection offers nothing, never everything
    r = await client.get("/v1/inference/models")
    assert r.status_code == 200 and r.json()["models"] == []


async def test_local_ollama_model_reports_its_context_size(client, db_conn, upstream, monkeypatch):
    upstream["handler"] = _tags_and_show(["gemma3"])
    entry = lambda r: r.json()["models"][0]["context_size"]
    assert entry(await client.get("/v1/inference/models")) == {"fixed": None, "auto": 16384}
    await _save_models(db_conn, context_size=8192)
    assert entry(await client.get("/v1/inference/models")) == {"fixed": 8192, "auto": 16384}
    await _save_models(db_conn, context_size=None, context_sizes={"ollama:gemma3": 12288})   # the model's own size wins
    assert entry(await client.get("/v1/inference/models"))["fixed"] == 12288
    monkeypatch.setenv("INFERENCE_NUM_CTX_MAX", "4096")   # the ceiling clamps both
    assert entry(await client.get("/v1/inference/models")) == {"fixed": 4096, "auto": 4096}


async def test_cloud_model_has_no_context_size_entry(client, upstream):
    upstream["handler"] = _tags_and_show(["gemma4:31b-cloud"])
    models = (await client.get("/v1/inference/models")).json()["models"]
    assert [m["id"] for m in models] == ["ollama:gemma4:31b-cloud"]
    assert "context_size" not in models[0]


async def test_openai_kind_model_has_no_context_size_entry(client, upstream, monkeypatch):
    await _restart_router(upstream, monkeypatch, INFERENCE_PROVIDERS="gw", INFERENCE_GW_KIND="openai",
                          INFERENCE_GW_URL="http://localhost:9000/v1")
    upstream["handler"] = lambda request: (
        httpx.Response(200, json={"data": [{"id": "gpt-x"}]}) if request.url.path.endswith("/models")
        else httpx.Response(404))
    models = (await client.get("/v1/inference/models")).json()["models"]
    assert [m["id"] for m in models] == ["gw:gpt-x"]
    assert "context_size" not in models[0]


async def test_self_hosted_ollama_on_a_public_host_gets_the_entry(client, upstream, monkeypatch):
    """The entry follows sizes_context, not the URL guess: a remote GPU box is still an Ollama that loads at 4,096."""
    await _restart_router(upstream, monkeypatch, INFERENCE_PROVIDERS="gpu", INFERENCE_GPU_KIND="ollama",
                          INFERENCE_GPU_URL="http://gpu.example.com:11434")
    upstream["handler"] = _tags_and_show(["gemma3"])
    models = (await client.get("/v1/inference/models")).json()["models"]
    assert models[0]["id"] == "gpu:gemma3"
    assert models[0]["context_size"] == {"fixed": None, "auto": 16384}


async def test_unreadable_models_row_falls_back_to_the_environment(client, upstream, monkeypatch):
    """Fail-open: a model list must survive a failed read of the console's row."""
    from server.services import model_settings

    async def boom(conn, env=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(model_settings, "load_models_settings", boom)
    upstream["handler"] = _tags_and_show(["gemma3"])
    models = (await client.get("/v1/inference/models")).json()["models"]
    assert [m["id"] for m in models] == ["ollama:gemma3"]
    assert models[0]["context_size"] == {"fixed": None, "auto": 16384}
