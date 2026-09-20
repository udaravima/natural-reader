import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import tools as tools_router


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_web_search_requires_auth(db_conn):
    app = FastAPI()

    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn
    app.include_router(tools_router.router)
    async with _client(app) as client:
        r = await client.post("/v1/tools/web_search", json={"query": "hi"})
    assert r.status_code == 401


async def test_web_search_allows_authenticated(db_conn, monkeypatch):
    async def fake_web_search(query, count):
        return {"query": query, "results": []}

    monkeypatch.setattr(tools_router, "web_search", fake_web_search)
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id="u1", email="a@x.io", role="member", capabilities=frozenset({"chat"})
    )
    app.include_router(tools_router.router)
    async with _client(app) as client:
        r = await client.post("/v1/tools/web_search", json={"query": "hi"})
    assert r.status_code == 200
