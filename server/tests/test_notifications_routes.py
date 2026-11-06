"""The inbox routes (spec 2026-11-06, Delivery): the signed-in person's own notices only."""
import ast
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import notifications as notifications_router
from server.services import notifications



async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


def _client(db_conn, uid):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=uid, email="x@x.io", role="member", capabilities=frozenset({"reader"}))
    app.include_router(notifications_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _ids(conn, uid):
    cur = await conn.execute("SELECT id FROM notifications WHERE user_id = %s ORDER BY id", (uid,))
    return [r[0] for r in await cur.fetchall()]


async def test_unread_list_read_and_read_all(db_conn):
    ann = await _user(db_conn, "ann")
    await notifications.send(db_conn, [ann], "doc.indexed", title="Q3.pdf finished indexing.",
                             target=notifications.doc_target("d1", "Q3.pdf"))
    await notifications.send(db_conn, [ann], "doc.index_failed", title="Couldn't index a.pdf.", key="index:d2")
    async with _client(db_conn, ann) as c:
        assert (await c.get("/v1/notifications/unread")).json() == {"count": 2, "action": 1}
        page = (await c.get("/v1/notifications")).json()
        assert page["total"] == 2
        first = page["rows"][0]
        assert first["kind"] == "doc.index_failed" and first["needs_action"] is True and first["unread"] is True
        assert page["rows"][1]["target"] == {"doc": "d1", "name": "Q3.pdf"}
        assert isinstance(first["at"], str)
        r = await c.post("/v1/notifications/read", json={"ids": first["ids"]})
        assert r.status_code == 200 and r.json() == {"changed": 1}
        assert (await c.get("/v1/notifications/unread")).json() == {"count": 1, "action": 0}
        assert (await c.post("/v1/notifications/read-all")).json() == {"changed": 1}
        assert (await c.get("/v1/notifications/unread")).json() == {"count": 0, "action": 0}


async def test_paging_limits(db_conn):
    ann = await _user(db_conn, "ann")
    async with _client(db_conn, ann) as c:
        assert (await c.get("/v1/notifications", params={"limit": 0})).status_code == 422
        assert (await c.get("/v1/notifications", params={"limit": 101})).status_code == 422
        assert (await c.get("/v1/notifications", params={"offset": -1})).status_code == 422
        assert (await c.get("/v1/notifications", params={"limit": 100})).status_code == 200


async def test_read_takes_at_most_1000_ids(db_conn):
    ann = await _user(db_conn, "ann")
    async with _client(db_conn, ann) as c:
        assert (await c.post("/v1/notifications/read", json={"ids": list(range(1001))})).status_code == 422
        assert (await c.post("/v1/notifications/read", json={"ids": []})).json() == {"changed": 0}


async def test_read_ignores_other_peoples_ids(db_conn):
    ann, ben = await _user(db_conn, "ann"), await _user(db_conn, "ben")
    await notifications.send(db_conn, [ben], "doc.indexed", title="Ben's")
    bens = await _ids(db_conn, ben)
    async with _client(db_conn, ann) as c:
        r = await c.post("/v1/notifications/read", json={"ids": bens})
        assert r.status_code == 200 and r.json() == {"changed": 0}
    assert await notifications.unread_counts(db_conn, ben) == {"count": 1, "action": 0}


async def test_rows_are_only_the_persons_own(db_conn):
    ann, ben = await _user(db_conn, "ann"), await _user(db_conn, "ben")
    await notifications.send(db_conn, [ben], "doc.indexed", title="Ben's")
    async with _client(db_conn, ann) as c:
        assert (await c.get("/v1/notifications")).json() == {"rows": [], "total": 0}
        assert (await c.get("/v1/notifications/unread")).json() == {"count": 0, "action": 0}


def test_the_router_is_included_in_the_app():
    src = (Path(__file__).parents[1] / "app.py").read_text()
    included = [n.args[0].id for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "include_router"
                and n.args and isinstance(n.args[0], ast.Name)]
    assert "notifications_router" in included


def test_the_four_routes_are_registered():
    paths = {(m, r.path) for r in notifications_router.router.routes for m in r.methods}
    assert {("GET", "/v1/notifications/unread"), ("GET", "/v1/notifications"),
            ("POST", "/v1/notifications/read"), ("POST", "/v1/notifications/read-all")} <= paths
