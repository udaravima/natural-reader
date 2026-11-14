"""Signing a request in must not keep a pooled connection for the whole route.

It used to: `get_current_user` took its connection as a dependency, which FastAPI closes only when the route
function returns. A route that then opened a second connection and waited (document search waits on the
embedding model, up to two minutes when it is cold) held two at once, so ten such requests on a worker took
the whole pool of ten and every one of them waited for a connection none could give back.
"""
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from server.auth import deps, users

ROW = {"id": users.SEED_ADMIN_ID, "email": "admin@example.com", "status": "active",
       "capabilities": ["admin", "reader", "chat"], "token_scopes": None}


def _app(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("HOST", "127.0.0.1")
    held = {"now": 0, "opened": 0}

    async def counting_conn():
        held["now"] += 1
        held["opened"] += 1
        try:
            yield object()
        finally:
            held["now"] -= 1

    async def get_user(conn, user_id):
        assert held["now"] == 1      # the lookup itself does run on a connection
        return ROW

    monkeypatch.setattr(users, "get_user", get_user)
    app = FastAPI()
    app.dependency_overrides[deps.get_conn] = counting_conn
    return app, held


def test_the_route_runs_with_no_connection_held_for_signing_in(monkeypatch):
    app, held = _app(monkeypatch)

    @app.get("/who")
    async def who(principal: deps.Principal = Depends(deps.get_current_user)):
        return {"email": principal.email, "held": held["now"]}

    body = TestClient(app, base_url="http://127.0.0.1").get("/who").json()
    assert body == {"email": "admin@example.com", "held": 0}


def test_a_route_with_its_own_connection_holds_one_not_two(monkeypatch):
    app, held = _app(monkeypatch)

    @app.get("/work")
    async def work(principal: deps.Principal = Depends(deps.require_capability("reader")),
                   conn=Depends(deps.get_conn, scope="function")):
        return {"held": held["now"]}

    assert TestClient(app, base_url="http://127.0.0.1").get("/work").json() == {"held": 1}
    assert held["now"] == 0
