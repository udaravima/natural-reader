"""A0 §5 as one table. Each row is an action; each column a caller. Read it
side by side with the spec's matrix — they must say the same thing.
`admin` is an admin who isn't a member; `admin+c` is an admin who is a
Contributor (admins file/remove documents only through a member role)."""
import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import people as people_router
from server.routers import projects as projects_router
from server.tests import seed

pytestmark = pytest.mark.asyncio

CALLERS = ("none", "reader", "contributor", "maintainer", "owner", "admin", "admin+c")
DOC_MINE = "1" * 64     # the caller holds an upload entry for it
DOC_PLACED = "2" * 64   # already in the project, filed by the base owner

#                 action               none reader contrib maint owner admin admin+c
MATRIX = {
    "see project":     (404, 200, 200, 200, 200, 200, 200),
    "see members":     (404, 200, 200, 200, 200, 200, 200),
    "see activity":    (404, 200, 200, 200, 200, 200, 200),
    "file own doc":    (404, 403, 204, 204, 204, 404, 204),
    "remove any doc":  (404, 403, 403, 204, 204, 404, 403),
    "edit":            (404, 403, 403, 200, 200, 200, 200),
    "add contributor": (404, 403, 403, 200, 200, 200, 200),
    "add owner":       (404, 403, 403, 403, 200, 200, 200),
    "remove an owner": (404, 403, 403, 403, 204, 204, 204),
    "delete project":  (404, 403, 403, 403, 204, 204, 204),
}


def _call(c, action, pid, target, second_owner):
    base = f"/v1/projects/{pid}"
    return {
        "see project": lambda: c.get(base),
        "see members": lambda: c.get(f"{base}/members"),
        "see activity": lambda: c.get(f"{base}/events"),
        "file own doc": lambda: c.put(f"{base}/docs/{DOC_MINE}"),
        "remove any doc": lambda: c.delete(f"{base}/docs/{DOC_PLACED}"),
        "edit": lambda: c.patch(base, json={"name": "Renamed"}),
        "add contributor": lambda: c.put(f"{base}/members/{target}", json={"role": "contributor"}),
        "add owner": lambda: c.put(f"{base}/members/{target}", json={"role": "owner"}),
        "remove an owner": lambda: c.delete(f"{base}/members/{second_owner}"),
        "delete project": lambda: c.delete(base),
    }[action]()


async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


@pytest.mark.parametrize("action", list(MATRIX))
@pytest.mark.parametrize("caller", CALLERS)
async def test_permission_matrix(db_conn, action, caller):
    base_owner, second_owner = await _user(db_conn, "bo"), await _user(db_conn, "so")
    target, me = await _user(db_conn, "tg"), await _user(db_conn, "me")
    member_role = {"reader": "reader", "contributor": "contributor", "maintainer": "maintainer",
                   "owner": "owner", "admin+c": "contributor"}.get(caller)
    members = [(second_owner, "owner")] + ([(me, member_role)] if member_role else [])
    pid = await seed.make_project(db_conn, base_owner, members=members)
    await seed.seed_doc(db_conn, DOC_MINE, me, file_name="mine.pdf")
    await seed.seed_doc(db_conn, DOC_PLACED, base_owner, file_name="placed.pdf", project_ids=[pid])

    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=me, email="me@x.io", role="admin" if caller.startswith("admin") else "member",
        capabilities=frozenset({"reader"}))
    app.include_router(projects_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await _call(c, action, pid, target, second_owner)
    assert r.status_code == MATRIX[action][CALLERS.index(caller)], r.text


@pytest.fixture
def production_logging(tmp_path, monkeypatch, caplog):
    """Apply the real logging config (so httpx/httpcore get their production levels), then
    re-attach caplog's handler: dictConfig clears root's handlers and the configured
    loggers don't propagate, so caplog would otherwise see nothing."""
    from server.logging_config import configure_logging
    names = ["", "server", "server.audit", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"]
    saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).level, logging.getLogger(n).propagate)
             for n in names}
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    configure_logging()
    for n in names:
        logging.getLogger(n).addHandler(caplog.handler)
    yield
    for n, (handlers, level, prop) in saved.items():
        lg = logging.getLogger(n)
        for h in lg.handlers[:]:
            if h not in handlers and h is not caplog.handler:
                h.close()
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = prop


async def test_no_personal_data_at_info_or_above(db_conn, caplog, monkeypatch, production_logging):
    """§7: IDs, roles and counts only — never emails, names, file names or lookup text."""
    monkeypatch.setenv("USER_DIRECTORY_MODE", "open")
    owner = (await resolve_or_provision_user(db_conn, iss="i", sub="pv1", email="olu.secret@x.io",
                                             first_name="Olusegun"))["id"]
    ben = (await resolve_or_provision_user(db_conn, iss="i", sub="pv2", email="ben.secret@x.io",
                                           first_name="Benedikt"))["id"]
    await db_conn.execute("UPDATE users SET status='active' WHERE id = ANY(%s::uuid[])", ([owner, ben],))
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=owner, email="olu.secret@x.io", role="member", capabilities=frozenset({"reader"}))
    app.include_router(projects_router.router)
    app.include_router(people_router.router)
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            pid = (await c.post("/v1/projects", json={"name": "Codename Falcon"})).json()["id"]
            await c.get("/v1/users/lookup", params={"q": "Benedi"})
            await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "maintainer"})
            await c.patch(f"/v1/projects/{pid}", json={"name": "Codename Heron"})
            await c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "reader"})  # 409
            await c.delete(f"/v1/projects/{pid}/members/{ben}")
    text = "\n".join(r.getMessage() for r in caplog.records
                     if r.levelno >= logging.INFO)
    for secret in ("secret", "Olusegun", "Benedikt", "Benedi", "Falcon", "Heron"):
        assert secret not in text
    assert f"member.added project={pid}" in text
