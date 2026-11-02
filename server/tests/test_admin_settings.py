import httpx

from server.auth import deps
from server.tests.test_chat_options import _app, _client, _principal


async def _admin(db_conn):
    return await _principal(db_conn, "a1", "a@x.io", "admin")


async def test_get_returns_settings_and_info(db_conn, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        body = (await c.get("/v1/admin/settings/deployment")).json()
    assert body["settings"]["timezone"]["value"] == "UTC"
    assert body["settings"]["timezone"]["source"] == "default" and body["settings"]["timezone"]["env"] == "TZ"
    assert isinstance(body["info"], list) and body["info"]


async def test_put_sets_then_resets_the_timezone(db_conn, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        r = await c.put("/v1/admin/settings/deployment", json={"changes": {"timezone": "Asia/Colombo"}})
        assert r.json()["settings"]["timezone"]["value"] == "Asia/Colombo"
        assert r.json()["settings"]["timezone"]["source"] == "admin"
        r = await c.put("/v1/admin/settings/deployment", json={"changes": {"timezone": None}})
        assert r.json()["settings"]["timezone"]["source"] == "default"


async def test_an_invalid_zone_is_a_422_naming_the_setting_and_writes_nothing(db_conn):
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        r = await c.put("/v1/admin/settings/deployment", json={"changes": {"timezone": "Mars/Base"}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "invalid_setting" and detail["setting"] == "timezone" and "Mars/Base" in detail["message"]
    cur = await db_conn.execute("SELECT 1 FROM app_settings WHERE key = 'deployment'")
    assert await cur.fetchone() is None


async def test_unknown_section_is_404(db_conn):
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        r = await c.get("/v1/admin/settings/nope")
        r2 = await c.put("/v1/admin/settings/nope", json={"changes": {}})
    assert r.status_code == 404 and r.json()["detail"]["error"] == "unknown_section"
    assert r2.status_code == 404


async def test_unknown_body_field_is_422(db_conn):
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        r = await c.put("/v1/admin/settings/deployment", json={"changes": {}, "extra": 1})
    assert r.status_code == 422


async def test_member_gets_403_on_get_and_put(db_conn):
    member = await _principal(db_conn, "m1", "m@x.io", "member")
    async with _client(_app(db_conn, member)) as c:
        assert (await c.get("/v1/admin/settings/deployment")).status_code == 403
        assert (await c.put("/v1/admin/settings/deployment", json={"changes": {}})).status_code == 403


async def test_get_survives_a_malformed_url_in_the_environment(db_conn, monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://[bad")
    monkeypatch.setenv("INFERENCE_PROVIDERS", "x")
    monkeypatch.setenv("INFERENCE_X_KIND", "openai")
    monkeypatch.setenv("INFERENCE_X_URL", "http://[::1")
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        r = await c.get("/v1/admin/settings/deployment")
    assert r.status_code == 200
    assert {row["value"] for row in r.json()["info"] if row["id"] in ("searxng", "provider:x")} == {"Not a valid URL"}


async def test_models_section_round_trips(db_conn, monkeypatch):
    for var in ("READER_MODEL", "SUMMARIZE_MODEL", "WEB_SEARCH_SUMMARY_MODEL", "INFERENCE_LOCAL_NUM_CTX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SUMMARIZE_MODEL", "ollama:small")
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        body = (await c.get("/v1/admin/settings/models")).json()
        assert set(body["settings"]) == {"reader", "context_size", "context_sizes", "context_windows",
                                         "provider_locality", "chat_models"}
        assert {row["id"] for row in body["info"]} == {"num_ctx_max", "keep_alive_max"}
        assert body["settings"]["reader"]["value"] == "ollama:small"
        assert body["settings"]["reader"]["env"] == "SUMMARIZE_MODEL"   # the chain's winner, not READER_MODEL
        assert body["settings"]["context_size"]["source"] == "default"
        r = await c.put("/v1/admin/settings/models", json={"changes": {"context_size": 12288}})
        assert r.json()["settings"]["context_size"]["value"] == 12288
        assert r.json()["settings"]["context_size"]["source"] == "admin"
        r = await c.put("/v1/admin/settings/models", json={"changes": {"context_size": None}})
        assert r.json()["settings"]["context_size"]["source"] == "default"
        r = await c.put("/v1/admin/settings/models", json={"changes": {"context_size": 100}})
    assert r.status_code == 422 and r.json()["detail"]["setting"] == "context_size"
    assert "2,048" in r.json()["detail"]["message"]


def _allow_list_router(monkeypatch, env):
    """A real Router over `env`'s providers (no request is made: is_allowed reads only the configs)."""
    from server.llm.router import Router, load_provider_configs
    from server.routers import admin as admin_router
    router = Router(load_provider_configs(env), httpx.AsyncClient())
    monkeypatch.setattr(admin_router, "get_router", lambda: router)
    return router


async def test_a_reader_outside_the_deployments_model_list_is_refused(db_conn, monkeypatch):   # ruling P12
    _allow_list_router(monkeypatch, {
        "INFERENCE_PROVIDERS": "ollama,openrouter",
        "INFERENCE_OLLAMA_KIND": "ollama", "INFERENCE_OLLAMA_URL": "http://localhost:11434",
        "INFERENCE_OLLAMA_MODELS": "gemma4:e4b",
        "INFERENCE_OPENROUTER_KIND": "openai", "INFERENCE_OPENROUTER_URL": "https://openrouter.example.com/api/v1",
        "INFERENCE_OPENROUTER_MODELS": "some/cheap-model"})
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        for refused in ("openrouter:some/expensive-model", "ollama:qwen3:8b", "qwen3:8b", "nowhere:x"):
            r = await c.put("/v1/admin/settings/models", json={"changes": {"reader": refused}})
            assert r.status_code == 422, refused
            assert r.json()["detail"] == {"error": "invalid_setting", "setting": "reader",
                                          "message": "That model isn't in this deployment's model list."}
        cur = await db_conn.execute("SELECT 1 FROM app_settings WHERE key = 'models'")
        assert await cur.fetchone() is None                               # one refused value saves nothing

        for allowed in ("openrouter:some/cheap-model", "ollama:gemma4:e4b", "gemma4:e4b", "chat", "Chat"):
            r = await c.put("/v1/admin/settings/models", json={"changes": {"reader": allowed}})
            assert r.status_code == 200, allowed
        assert r.json()["settings"]["reader"]["value"] == "chat"
        r = await c.put("/v1/admin/settings/models", json={"changes": {"reader": None}})    # a reset is never refused
        assert r.status_code == 200


async def test_with_no_allow_list_any_model_of_a_provider_may_be_the_reader(db_conn, monkeypatch):
    _allow_list_router(monkeypatch, {"OLLAMA_URL": "http://localhost:11434"})
    async with _client(_app(db_conn, await _admin(db_conn))) as c:
        r = await c.put("/v1/admin/settings/models", json={"changes": {"reader": "ollama:anything"}})
    assert r.status_code == 200 and r.json()["settings"]["reader"]["value"] == "ollama:anything"
