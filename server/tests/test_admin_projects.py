"""A0 §9.5: the admin's view of every project, per-user project limits, and
enrolling people with their names."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import get_user, resolve_or_provision_user
from server.routers import admin as admin_router
from server.tests import seed

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, first=None):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io",
                                            first_name=first))["id"]


def _client(db_conn, uid):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=uid, email="admin@x.io", role="admin", capabilities=frozenset({"admin", "reader"}))
    app.dependency_overrides[deps.get_kc_admin] = lambda: None
    app.include_router(admin_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_admin_lists_every_project_with_owners_and_the_ownerless_filter(db_conn):
    admin, olu = await _user(db_conn, "ad"), await _user(db_conn, "ol", "Olu")
    owned = await seed.make_project(db_conn, olu, "Owned")
    orphan = await seed.make_project(db_conn, None, "Orphan")
    await seed.seed_doc(db_conn, "a" * 64, olu, project_ids=[owned])
    async with _client(db_conn, admin) as c:
        rows = {r["name"]: r for r in (await c.get("/v1/admin/projects")).json()}
        only = (await c.get("/v1/admin/projects", params={"ownerless": "true"})).json()
    assert rows["Owned"]["owners"] == [{"id": olu, "name": "Olu"}]
    assert (rows["Owned"]["member_count"], rows["Owned"]["doc_count"], rows["Owned"]["ownerless"]) == (1, 1, False)
    assert rows["Orphan"]["owners"] == [] and rows["Orphan"]["ownerless"] is True
    assert [r["id"] for r in only] == [orphan]


async def test_admin_projects_is_admin_only(db_conn):
    me = await _user(db_conn, "nm")
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=me, email="nm@x.io", role="member", capabilities=frozenset({"reader"}))
    app.include_router(admin_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/v1/admin/projects")).status_code == 403


async def test_admin_sets_and_clears_a_project_limit(db_conn):
    admin, u = await _user(db_conn, "ad"), await _user(db_conn, "pl")
    async with _client(db_conn, admin) as c:
        assert (await c.patch(f"/v1/admin/users/{u}", json={"project_limit": 5})).json()["project_limit"] == 5
        assert (await c.patch(f"/v1/admin/users/{u}", json={"project_limit": -1})).status_code == 422
        assert (await c.patch(f"/v1/admin/users/{u}", json={"project_limit": None})).json()["project_limit"] is None
        listed = {r["id"]: r for r in (await c.get("/v1/admin/users")).json()}
    assert "project_limit" in listed[u] and "first_name" in listed[u]


async def test_enroll_takes_username_and_names_without_copying_the_email(db_conn):
    admin = await _user(db_conn, "ad")
    async with _client(db_conn, admin) as c:
        r = await c.post("/v1/admin/users", json={"email": "new@example.com", "username": "newbie",
                                                  "first_name": "New", "last_name": "Person"})
        bare = await c.post("/v1/admin/users", json={"email": "bare@example.com"})
    assert r.status_code == 201
    u = await get_user(db_conn, r.json()["user"]["id"])
    assert (u["username"], u["first_name"], u["last_name"]) == ("newbie", "New", "Person")
    assert (await get_user(db_conn, bare.json()["user"]["id"]))["username"] is None
