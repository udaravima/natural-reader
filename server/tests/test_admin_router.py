import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(admin_router.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_admin_lists_and_activates(db_conn):
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    pending = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=admin["id"], email=admin["email"], role="admin")
    async with _client(_app(db_conn, p)) as client:
        assert len((await client.get("/v1/admin/users")).json()) == 2
        r = await client.patch(
            f"/v1/admin/users/{pending['id']}", json={"status": "active"}
        )
        assert r.status_code == 200 and r.json()["status"] == "active"


async def test_member_forbidden(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as client:
        assert (await client.get("/v1/admin/users")).status_code == 403


async def _admin(db_conn):
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    return admin, deps.Principal(user_id=admin["id"], email=admin["email"], role="admin")


async def test_inference_usage_admin_only(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as client:
        r = await client.get("/v1/admin/inference/usage")
        assert r.status_code == 403


async def test_admin_inference_usage_returns_rows(db_conn):
    from server.services import inference_budget as ib

    admin, p = await _admin(db_conn)
    await ib.record_usage(db_conn, admin["id"], 10, 5)
    async with _client(_app(db_conn, p)) as client:
        rows = (await client.get("/v1/admin/inference/usage?days=1")).json()
    assert len(rows) == 1
    assert rows[0]["tokens"] == 15
    assert rows[0]["email"] == admin["email"]


async def test_admin_inference_usage_clamps_days(db_conn):
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        r = await client.get("/v1/admin/inference/usage?days=99999")
    assert r.status_code == 200  # clamped to 90, not an error


async def test_patch_sets_and_clears_budget(db_conn):
    admin, p = await _admin(db_conn)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="c@x.io")

    async with _client(_app(db_conn, p)) as client:
        r = await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": 1000}
        )
        assert r.status_code == 200
        # explicit null clears back to the deployment default
        r = await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": None}
        )
        assert r.status_code == 200
        # an absent field leaves the column untouched
        r = await client.patch(f"/v1/admin/users/{other['id']}", json={})
        assert r.status_code == 200

    from server.auth.users import get_user

    assert (await get_user(db_conn, other["id"]))["inference_daily_token_budget"] is None


async def test_patch_budget_round_trip(db_conn):
    admin, p = await _admin(db_conn)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="c@x.io")

    async with _client(_app(db_conn, p)) as client:
        await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": 5000}
        )
    from server.auth.users import get_user

    assert (await get_user(db_conn, other["id"]))["inference_daily_token_budget"] == 5000


async def test_patch_rejects_negative_budget(db_conn):
    admin, p = await _admin(db_conn)
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="c@x.io")
    async with _client(_app(db_conn, p)) as client:
        r = await client.patch(
            f"/v1/admin/users/{other['id']}", json={"inference_daily_token_budget": -5}
        )
    assert r.status_code == 422
