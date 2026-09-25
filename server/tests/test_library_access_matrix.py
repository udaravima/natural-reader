"""Access matrix: every role x (doc in 0 / 1 / 2 projects), checked through
BOTH read paths — assert_can_read_doc (single-doc routes) and GET /v1/docs
(the list) — so the two can never disagree."""
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router
from server.tests import seed

pytestmark = pytest.mark.asyncio

DOC = "e" * 64
ROLES = ["doc_owner", "grantee", "p1_owner", "p1_member", "p2_owner", "p2_member", "stranger"]


@pytest.fixture
def docs_app(db_conn, monkeypatch):
    class _PoolShim:
        @asynccontextmanager
        async def connection(self):
            yield db_conn

    monkeypatch.setattr(docs_router, "get_pool", lambda: _PoolShim())
    monkeypatch.setattr(docs_router, "is_ready", lambda: True)
    app = FastAPI()
    app.include_router(docs_router.router)
    return app


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


def _expected(role, n_projects):
    if role in ("doc_owner", "grantee"):
        return True
    if role in ("p1_owner", "p1_member"):
        return n_projects >= 1
    if role in ("p2_owner", "p2_member"):
        return n_projects >= 2
    return False


@pytest.mark.parametrize("n_projects", [0, 1, 2])
@pytest.mark.parametrize("role", ROLES)
async def test_access_matrix(db_conn, docs_app, role, n_projects):
    users = {r: await _member(db_conn, f"mx-{r}") for r in ROLES}
    uid = {r: users[r].user_id for r in ROLES}

    projects = []
    for owner_role, member_role, name in (("p1_owner", "p1_member", "P1"),
                                          ("p2_owner", "p2_member", "P2")):
        cur = await db_conn.execute(
            "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id",
            (uid[owner_role], name))
        pid = str((await cur.fetchone())[0])
        await db_conn.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
            (pid, uid[member_role]))
        projects.append(pid)

    await seed.seed_doc(db_conn, DOC, uid["doc_owner"], file_name="f.pdf",
                        project_ids=projects[:n_projects])
    await seed.share_doc(db_conn, DOC, uid["doc_owner"], uid["grantee"])

    readable = _expected(role, n_projects)
    if readable:
        await authz.assert_can_read_doc(db_conn, DOC, uid[role])
    else:
        with pytest.raises(HTTPException) as e:
            await authz.assert_can_read_doc(db_conn, DOC, uid[role])
        assert e.value.status_code == 404

    docs_app.dependency_overrides[deps.get_current_user] = lambda: users[role]
    async with httpx.AsyncClient(transport=ASGITransport(app=docs_app),
                                 base_url="http://t") as client:
        r = await client.get("/v1/docs")
        assert r.status_code == 200
        assert (DOC in {row["doc_id"] for row in r.json()}) is readable
