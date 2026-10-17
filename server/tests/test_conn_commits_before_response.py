"""A route's database writes must be committed before its response goes out.

deps.get_conn commits when its `async with pool.connection()` block exits. A
yield dependency with FastAPI's default "request" scope exits only after the
response has been sent and its background tasks have run, so a client that
reads again at once could miss its own write (measured: 3 of 30 immediate
re-reads missed a token just created), and an upload's connection stayed
checked out for the whole background index. With scope="function" the block
exits as the route function returns, before the response.

httpx's ASGITransport runs the app to completion before handing back the
response, which hides the gap, so these tests drive the ASGI app directly and
look at the database at the moment the response's last byte is sent."""
import importlib
import json
import pkgutil
import secrets

import pytest
from fastapi import FastAPI
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from server import db
from server import endpoints, routers
from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import auth as auth_router
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for

def _routes(routes):
    # FastAPI 0.142 keeps an included router as a lazy _IncludedRouter entry.
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes)
        else:
            yield route


def _get_conn_dependants(dependant, path=()):
    for sub in dependant.dependencies:
        if sub.call is deps.get_conn:
            yield path + (sub.call.__name__,), sub
        yield from _get_conn_dependants(sub, path + (getattr(sub.call, "__name__", "?"),))


def _all_routers():
    # Every router module, found rather than listed, so a new one is covered.
    # (Not create_app(): importing server.app configures logging for the whole
    # test session, which test_logging_config then sees.)
    yield endpoints.router
    for mod in pkgutil.iter_modules(routers.__path__):
        router = getattr(importlib.import_module(f"server.routers.{mod.name}"), "router", None)
        if router is not None:
            yield router


def test_every_route_takes_its_connection_with_function_scope():
    wrong, checked = [], 0
    for route in (r for router in _all_routers() for r in _routes(router.routes)):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for chain, sub in _get_conn_dependants(dependant):
            checked += 1
            if sub.scope != "function":
                wrong.append(f"{sorted(route.methods)} {route.path} via {' → '.join(chain)}")
    assert checked > 30   # the walk reaches the routes (it once saw none)
    assert not wrong, "get_conn without scope='function' (commits after the response):\n" + "\n".join(wrong)


@pytest.fixture
async def scratch(monkeypatch):
    name = f"natural_reader_commit_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    pool = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        url = url_for(name)
        await apply_migrations(url)
        pool = AsyncConnectionPool(url, min_size=1, max_size=4, open=False)
        await pool.open(wait=True, timeout=10)
        monkeypatch.setattr(db, "_pool", pool)
        async with pool.connection() as conn:
            user = await resolve_or_provision_user(conn, iss="i", sub="s", email="s@x.io")
        yield url, user
    finally:
        try:
            if pool is not None:
                await pool.close()
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _committed_names(url):
    conn = await AsyncConnection.connect(url, autocommit=True)
    try:
        cur = await conn.execute("SELECT name FROM personal_access_tokens")
        return {r[0] for r in await cur.fetchall()}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_write_is_committed_before_the_response_is_sent(scratch):
    url, user = scratch
    app = FastAPI()
    app.include_router(auth_router.router)
    principal = deps.Principal(user_id=user["id"], email=user["email"], role="admin",
                               capabilities=frozenset(user["capabilities"]))
    app.dependency_overrides[deps.get_current_user] = lambda: principal   # real get_conn

    body = json.dumps({"name": "seen-at-once", "scopes": ["read_aloud"]}).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/v1/auth/tokens", "raw_path": b"/v1/auth/tokens",
             "query_string": b"", "root_path": "", "server": ("t", 80), "client": ("c", 1),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]}
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    seen = {}

    async def send(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]
        if message["type"] == "http.response.body" and not message.get("more_body"):
            # The client has the whole answer now: a re-read starts here.
            seen["names"] = await _committed_names(url)

    await app(scope, receive, send)
    assert seen["status"] == 200
    assert "seen-at-once" in seen["names"]


@pytest.mark.asyncio
async def test_a_background_task_runs_after_the_request_connection_is_returned(scratch):
    """v2.2.0 limitation: each running index held one of the pool's
    connections until it finished. The request's connection must be back in
    the pool before a background task starts."""
    import asyncio

    from fastapi import BackgroundTasks, Depends

    pool = db.get_pool()
    app = FastAPI()
    result = {}

    async def background_job():
        # The fixture's pool has max_size=4: all four must be free now.
        held = []
        try:
            for _ in range(4):
                held.append(await asyncio.wait_for(pool.getconn(), timeout=2))
            result["all_free"] = True
        except asyncio.TimeoutError:
            result["all_free"] = False
        finally:
            for c in held:
                await pool.putconn(c)

    @app.post("/work")
    async def work(background: BackgroundTasks, conn=Depends(deps.get_conn, scope="function")):
        await conn.execute("SELECT 1")
        background.add_task(background_job)
        return {"ok": True}

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/work", "raw_path": b"/work", "query_string": b"",
             "root_path": "", "server": ("t", 80), "client": ("c", 1), "headers": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    await app(scope, receive, send)
    assert result["all_free"] is True
