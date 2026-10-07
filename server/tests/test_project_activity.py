"""A0 §9.4: GET /v1/projects/{id}/events — members and admins, newest first, paged."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import projects as projects_router
from server.services import project_events
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, first=None):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io",
                                            first_name=first))["id"]


def _client(db_conn, uid, *, admin=False):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=uid, email="x@x.io", role="admin" if admin else "member",
        capabilities=frozenset({"reader"}))
    app.include_router(projects_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_events_for_members_and_admins_newest_first_and_paged(db_conn):
    owner, reader = await _user(db_conn, "o", "Olu"), await _user(db_conn, "r")
    stranger, admin = await _user(db_conn, "s"), await _user(db_conn, "a")
    pid = await seed.make_project(db_conn, owner, members=[(reader, "reader")])
    for n in range(3):
        await project_events.record(db_conn, pid, owner, "project.described", details={"n": n})
    async with _client(db_conn, reader) as c:
        first = (await c.get(f"/v1/projects/{pid}/events", params={"limit": 2})).json()
        assert [e["details"]["n"] for e in first["events"]] == [2, 1]
        assert first["events"][0]["actor"] == {"id": owner, "name": "Olu"}
        rest = (await c.get(f"/v1/projects/{pid}/events",
                            params={"before": first["next_before"]})).json()
        assert [e["details"]["n"] for e in rest["events"]] == [0] and rest["next_before"] is None
        assert (await c.get(f"/v1/projects/{pid}/events", params={"limit": 0})).status_code == 422
        assert (await c.get(f"/v1/projects/{pid}/events", params={"limit": 101})).status_code == 422
    async with _client(db_conn, stranger) as c:
        assert (await c.get(f"/v1/projects/{pid}/events")).status_code == 404
    async with _client(db_conn, admin, admin=True) as c:
        assert (await c.get(f"/v1/projects/{pid}/events")).status_code == 200
