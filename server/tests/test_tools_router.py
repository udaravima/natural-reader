import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport
from psycopg.types.json import Jsonb

from server.auth import deps
from server.routers import tools as tools_router
from server.services import web_search as web_search_service
from server.tests.chat_harness import shim_pool


def _app():
    app = FastAPI()
    # web_search now requires an authenticated principal; stub one so these
    # tests exercise the tool logic, not the auth layer (covered separately).
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id="u1", email="a@x.io", role="member", capabilities=frozenset({"chat"})
    )
    app.include_router(tools_router.router)
    return app


def test_web_search_happy_path(monkeypatch):
    """The endpoint returns what the service finds: results, with no page fetch and no summary."""
    async def fake_searxng(query, count):
        return [{"title": "T", "url": "https://www.X.test/a", "snippet": "s"}]

    async def no_fetch(*a, **k):
        raise AssertionError("the endpoint must not fetch pages")

    monkeypatch.setattr(web_search_service, "searxng_search", fake_searxng)
    monkeypatch.setattr(web_search_service, "_safe_get", no_fetch)
    client = TestClient(_app())
    resp = client.post("/v1/tools/web_search", json={"query": "hi", "count": 3})
    assert resp.status_code == 200
    assert resp.json() == {"query": "hi", "results": [
        {"title": "T", "url": "https://www.X.test/a", "site": "x.test", "snippet": "s"}]}


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


# ---- the admin's Off covers this route too (final review M3) ----

OFF = "Web search is off on this server."


@pytest.fixture
def searched(monkeypatch):
    """The queries the route sent to the search service."""
    sent = []

    async def fake_web_search(query, count):
        sent.append(query)
        return {"query": query, "results": []}

    monkeypatch.setattr(tools_router, "web_search", fake_web_search)
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    return sent


async def _post_search():
    async with httpx.AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as client:
        return await client.post("/v1/tools/web_search", json={"query": "hi"})


async def _admin_mode(conn, monkeypatch, mode):
    """The admin console's choice, as save_chat_options stores it; the route reads it from this connection."""
    shim_pool(monkeypatch, conn, tools_router)
    await conn.execute("INSERT INTO app_settings (key, value) VALUES ('chat_options', %s)", (Jsonb({"web_search": mode}),))


async def test_off_in_the_admin_console_refuses_the_search_with_403(db_conn, monkeypatch, searched):
    await _admin_mode(db_conn, monkeypatch, "off")
    r = await _post_search()
    assert (r.status_code, r.json()) == (403, {"detail": OFF})
    assert searched == []


async def test_off_from_the_environment_refuses_it_too(db_conn, monkeypatch, searched):
    shim_pool(monkeypatch, db_conn, tools_router)      # no admin row: CHAT_WEB_SEARCH decides
    monkeypatch.setenv("CHAT_WEB_SEARCH", "off")
    r = await _post_search()
    assert (r.status_code, r.json()) == (403, {"detail": OFF})
    assert searched == []


async def test_the_admins_choice_wins_over_the_environment(db_conn, monkeypatch, searched):
    monkeypatch.setenv("CHAT_WEB_SEARCH", "off")
    await _admin_mode(db_conn, monkeypatch, "ask")
    r = await _post_search()
    assert r.status_code == 200 and searched == ["hi"]


@pytest.mark.parametrize("mode", ["ask", "auto"])
async def test_ask_and_auto_still_search_the_users_own_query(db_conn, monkeypatch, searched, mode):
    """The route is the caller's own explicit search, so "ask" doesn't apply to it: only Off stops it."""
    await _admin_mode(db_conn, monkeypatch, mode)
    r = await _post_search()
    assert r.status_code == 200 and searched == ["hi"]


async def test_off_from_the_environment_applies_when_the_settings_cant_be_read(monkeypatch, searched):
    """As in a chat turn: no database to read the admin's row from, so the environment decides, then "ask"."""
    monkeypatch.setenv("CHAT_WEB_SEARCH", "off")
    assert (await _post_search()).status_code == 403
    monkeypatch.delenv("CHAT_WEB_SEARCH")
    assert (await _post_search()).status_code == 200 and searched == ["hi"]
