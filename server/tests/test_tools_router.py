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
