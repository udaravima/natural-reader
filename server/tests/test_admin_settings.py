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
