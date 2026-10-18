import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import deps
from server.routers import tools as tools_router
from server.services import web_search as web_search_service


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
