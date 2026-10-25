"""Deployment defaults for user preferences (Admin → Defaults).

A browser keeps a user's own choice; a setting they never chose follows these
defaults. "Apply to everyone" bumps the setting's epoch, and a browser that
sees a higher epoch drops the user's own choice for it (LibreChat's "hard"
default), so a changed default can reach people who once chose otherwise."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router
from server.routers import settings as settings_router
from server.services import user_defaults


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(admin_router.router)
    app.include_router(settings_router.router)
    return app


async def _req(app, method, path, **kw):
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, **kw)


@pytest.fixture
async def admin(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="a", email="a@x.io")
    return deps.Principal(user_id=u["id"], email=u["email"], role="admin",
                          capabilities=frozenset({"admin", "chat", "reader"}))


@pytest.fixture
def member():
    return deps.Principal(user_id="00000000-0000-0000-0000-0000000000aa", email="m@x.io",
                          role="member", capabilities=frozenset({"reader"}))


BUILT_IN = {"chatAutoTts": False, "chatTtsMode": "after-complete", "think": "off",
            "selectedVoice": "af_heart", "playbackSpeed": 1.0}


async def test_with_nothing_saved_everyone_gets_the_built_in_defaults(db_conn, member):
    r = await _req(_app(db_conn, member), "GET", "/v1/settings/defaults")
    assert r.status_code == 200
    assert r.json() == {"values": BUILT_IN, "epochs": {}}


async def test_an_admin_default_replaces_the_built_in_for_everyone(db_conn, admin, member):
    r = await _req(_app(db_conn, admin), "PUT", "/v1/admin/defaults",
                   json={"values": {"chatAutoTts": True, "playbackSpeed": 1.25}})
    assert r.status_code == 200
    got = (await _req(_app(db_conn, member), "GET", "/v1/settings/defaults")).json()
    assert got["values"] == {**BUILT_IN, "chatAutoTts": True, "playbackSpeed": 1.25}


async def test_saving_a_value_equal_to_the_built_in_drops_the_override(db_conn, admin):
    await _req(_app(db_conn, admin), "PUT", "/v1/admin/defaults", json={"values": {"think": "on"}})
    r = await _req(_app(db_conn, admin), "PUT", "/v1/admin/defaults", json={"values": {"think": "off"}})
    assert r.json()["overridden"] == []


@pytest.mark.parametrize("values", [
    {"chatAutoTts": "yes"}, {"chatTtsMode": "loud"}, {"think": "max"}, {"playbackSpeed": 9},
    {"selectedVoice": "<script>"}, {"unknown": 1},
])
async def test_bad_values_are_refused(db_conn, admin, values):
    r = await _req(_app(db_conn, admin), "PUT", "/v1/admin/defaults", json={"values": values})
    assert r.status_code == 422


async def test_apply_to_everyone_bumps_that_settings_epoch(db_conn, admin, member):
    app = _app(db_conn, admin)
    await _req(app, "POST", "/v1/admin/defaults/chatAutoTts/apply-to-everyone")
    await _req(app, "POST", "/v1/admin/defaults/chatAutoTts/apply-to-everyone")
    got = (await _req(_app(db_conn, member), "GET", "/v1/settings/defaults")).json()
    assert got["epochs"] == {"chatAutoTts": 2}


async def test_apply_to_everyone_refuses_an_unknown_setting(db_conn, admin):
    r = await _req(_app(db_conn, admin), "POST", "/v1/admin/defaults/nope/apply-to-everyone")
    assert r.status_code == 404


async def test_only_admins_change_defaults(db_conn, member):
    app = _app(db_conn, member)
    assert (await _req(app, "PUT", "/v1/admin/defaults", json={"values": {}})).status_code == 403
    assert (await _req(app, "POST", "/v1/admin/defaults/think/apply-to-everyone")).status_code == 403


async def test_admin_view_lists_built_ins_and_choices(db_conn, admin):
    view = (await _req(_app(db_conn, admin), "GET", "/v1/admin/defaults")).json()
    assert view["builtIn"] == BUILT_IN
    assert set(view["choices"]["chatTtsMode"]) == {"streaming", "after-complete"}


def test_the_built_ins_are_the_services_own():
    assert user_defaults.built_in() == BUILT_IN
