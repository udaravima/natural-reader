"""/v1/projects/{pid}/documents (V1 spec §5.1): shapes, refusals and the
admin-only-through-a-role rule. The rules themselves are tested in
test_doc_versions_write.py; this file tests the HTTP layer."""
import uuid

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import project_documents as pd_router
from server.routers import projects as projects_router
from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2, C3 = "1" * 64, "2" * 64, "3" * 64


def _client(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    app.include_router(pd_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
async def proj(db_conn):
    alice = await member(db_conn, "rt-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    for doc, name in ((C1, "report.pdf"), (C2, "report-2.pdf"), (C3, "other.pdf")):
        await seed.seed_doc(db_conn, doc, alice.user_id, file_name=name, state="indexed")
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    return alice, pid, x


async def test_list_history_add_restore_delete_merge_remove(db_conn, proj):
    alice, pid, x = proj
    base = f"/v1/projects/{pid}/documents"
    async with _client(db_conn, alice) as c:
        [row] = (await c.get(base)).json()
        assert (row["id"], row["name"], row["version"], row["versions_count"]) == (x, "report.pdf", 1, 1)
        r = await c.post(f"{base}/{x}/versions/from-library", json={"doc_id": C2, "note": "v2"})
        assert r.status_code == 201
        assert r.json() == {"version": 2, "doc_id": C2, "restored_from": None, "state": "indexed"}
        assert [v["version"] for v in (await c.get(f"{base}/{x}/versions")).json()] == [2, 1]
        r = await c.post(f"{base}/{x}/versions/1/restore")
        assert r.status_code == 201 and r.json()["restored_from"] == 1
        assert (await c.delete(f"{base}/{x}/versions/2")).status_code == 204
        y = await seed.place_doc(db_conn, pid, C3, alice.user_id)
        r = await c.post(f"{base}/{x}/merge", json={"from": y})
        assert r.status_code == 200 and r.json()["name"] == "other.pdf"
        assert (await c.delete(f"{base}/{x}")).status_code == 204
        assert (await c.get(base)).json() == []


async def test_refusal_shapes(db_conn, proj):
    alice, pid, x = proj
    base = f"/v1/projects/{pid}/documents"
    async with _client(db_conn, alice) as c:
        r = await c.post(f"{base}/{x}/versions/from-library", json={"doc_id": C1})
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "already_current"
        assert r.json()["detail"]["document"] == {"id": x, "name": "report.pdf", "version": 1}
        assert (await c.delete(f"{base}/{x}/versions/1")).json()["detail"]["error"] == "is_current"
        assert (await c.get(f"{base}/{uuid.uuid4()}/versions")).status_code == 404
        assert (await c.get(f"{base}/not-a-uuid/versions")).status_code == 422
        r = await c.post(f"{base}/{x}/versions/from-library", json={"doc_id": "NOT-HEX"})
        assert r.status_code == 422
        r = await c.post(f"{base}/{x}/merge", json={"from": x})
        assert r.json()["detail"]["error"] == "merge_self"


async def test_from_library_needs_my_verified_upload(db_conn, proj):
    alice, pid, x = proj
    bob = await member(db_conn, "rt-bob", caps=("reader",))
    await seed.add_member(db_conn, pid, bob.user_id, "contributor")
    async with _client(db_conn, bob) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/versions/from-library",
                         json={"doc_id": C2})
    assert r.status_code == 404  # bob holds no upload entry for C2


async def test_note_length_is_the_services_rule_not_pydantics(db_conn, proj):
    """P14: a 501-character note is refused by clean_note (422 note_too_long),
    the same code the multipart route gives — not by a pydantic max_length."""
    alice, pid, x = proj
    async with _client(db_conn, alice) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/versions/from-library",
                         json={"doc_id": C2, "note": "n" * 501})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "note_too_long"
