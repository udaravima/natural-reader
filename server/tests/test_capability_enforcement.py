"""Positive/negative gate test for Task 9: a capability-less principal must be
turned away by `require_capability` BEFORE any ownership or business logic
runs. This pins the deny-by-default contract independent of any single
route's own behavior (docs ownership, chat session ownership, etc.)."""
import httpx
import pytest
from httpx import ASGITransport
from fastapi import FastAPI

from server.auth import deps
from server.routers import docs as docs_router

pytestmark = pytest.mark.asyncio


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(docs_router.router)
    return app


async def test_docs_get_requires_reader_capability(db_conn):
    # A capability-less principal must 403 on a representative docs GET route
    # (GET /v1/docs/{doc_id}) BEFORE ownership is even checked — the doc_id
    # below doesn't exist, so reaching ownership logic would 404, not 403.
    p = deps.Principal(user_id="u", email="e@x.io", role="member", capabilities=frozenset())
    doc_id = "0" * 64
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(db_conn, p)), base_url="http://t"
    ) as c:
        r = await c.get(f"/v1/docs/{doc_id}")
        assert r.status_code == 403
        assert r.json()["detail"] == {"error": "missing_capability", "capability": "reader"}


async def test_docs_get_allows_reader_capability_holder(db_conn, monkeypatch):
    # Positive half of the gate: holding the capability clears the gate and
    # reaches ownership logic, which then 404s a nonexistent doc (not 403).
    from contextlib import asynccontextmanager

    class _PoolShim:
        @asynccontextmanager
        async def connection(self):
            yield db_conn

    monkeypatch.setattr(docs_router, "get_pool", lambda: _PoolShim())
    monkeypatch.setattr(docs_router, "is_ready", lambda: True)

    p = deps.Principal(user_id="u", email="e@x.io", role="member",
                        capabilities=frozenset({"reader"}))
    doc_id = "1" * 64
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(db_conn, p)), base_url="http://t"
    ) as c:
        r = await c.get(f"/v1/docs/{doc_id}")
        assert r.status_code == 404
