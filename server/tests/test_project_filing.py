"""PUT /v1/projects/{pid}/docs/{doc_id} — filing from a library (V1 spec
§4.2): the checks in order 0–4, and the two choices."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
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
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
async def ctx(db_conn):
    alice = await member(db_conn, "fl-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    return alice, pid


async def _events(db_conn, pid):
    cur = await db_conn.execute("SELECT kind FROM project_events WHERE project_id = %s "
                                "AND kind LIKE 'document.%%' ORDER BY id", (pid,))
    return [r[0] for r in await cur.fetchall()]


async def test_a_new_file_is_a_new_document_and_refiling_is_idempotent(db_conn, ctx):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="Q3.pdf")
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}")
        assert r.status_code == 201 and r.json()["version"] == 1
        again = await c.put(f"/v1/projects/{pid}/docs/{C1}")
        assert again.status_code == 200 and again.json() == r.json()
    assert await _events(db_conn, pid) == ["document.added"]


@pytest.mark.parametrize("filed_as", ["report.pdf", "  Report.PDF ", "REPORT.pdf"])
async def test_a_matching_name_asks_and_both_choices_work(db_conn, ctx, filed_as):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="report.pdf")
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name=filed_as)
    await seed.seed_doc(db_conn, C3, alice.user_id, file_name=filed_as)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C2}")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "name_exists"
        assert r.json()["detail"]["document"] == {"id": x, "name": "report.pdf", "version": 1}
        r = await c.put(f"/v1/projects/{pid}/docs/{C2}", params={"as_version_of": x})
        assert r.status_code == 201 and r.json() == {"document_id": x, "version": 2,
                                                     "restored_from": None}
        r = await c.put(f"/v1/projects/{pid}/docs/{C3}", params={"keep_both": "true"})
        assert r.status_code == 201 and r.json()["document_id"] != x


async def test_the_same_name_in_another_project_does_not_ask(db_conn, ctx):
    alice, pid = ctx
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="README.md")
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name="README.md")
    await seed.place_doc(db_conn, other, C1, alice.user_id)
    async with _client(db_conn, alice) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{C2}")).status_code == 201


async def test_an_older_versions_bytes_offer_a_restore_and_win_over_a_name_match(db_conn, ctx):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id, file_name="taken.pdf")  # same name as E's
    await seed.seed_doc(db_conn, C2, alice.user_id, file_name="d2.pdf")
    await seed.seed_doc(db_conn, C3, alice.user_id, file_name="taken.pdf")
    d = await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="doc.pdf")
    await seed.add_version(db_conn, d, C2, alice.user_id)  # C1 is now v1, old
    await seed.place_doc(db_conn, pid, C3, alice.user_id)  # E: current, named taken.pdf
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}")
        assert r.status_code == 409 and r.json()["detail"]["error"] == "older_version"
        assert (r.json()["detail"]["document"]["id"], r.json()["detail"]["version"]) == (d, 1)
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}", params={"as_version_of": d})
        assert r.status_code == 201 and r.json()["restored_from"] == 1


async def test_content_current_elsewhere_cannot_become_another_documents_version(db_conn, ctx):
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id)
    await seed.seed_doc(db_conn, C2, alice.user_id)
    await seed.place_doc(db_conn, pid, C1, alice.user_id)
    y = await seed.place_doc(db_conn, pid, C2, alice.user_id)
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}", params={"as_version_of": y})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "current_elsewhere"


async def test_a_legacy_unverified_current_version_is_proved_not_duplicated(db_conn, ctx):
    alice, pid = ctx
    bob = await member(db_conn, "fl-bob")
    await seed.seed_doc(db_conn, C1, alice.user_id)
    x = await seed.place_doc(db_conn, pid, C1, bob.user_id, verified=False)
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}")
    assert r.status_code == 200 and r.json()["document_id"] == x
    assert await _events(db_conn, pid) == ["document.added"]


async def test_a_lost_filing_race_is_a_409_not_a_500(db_conn, ctx, monkeypatch):
    """A double-click: the other request files the content between our
    "is it current?" check and our insert. The unique index fires inside a
    savepoint and becomes current_elsewhere, never a 500."""
    from server.services import doc_versions
    alice, pid = ctx
    await seed.seed_doc(db_conn, C1, alice.user_id)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)  # the "other request" won

    real, calls = doc_versions._current_holder, []

    async def _blind(*a, **k):
        calls.append(1)
        return None if len(calls) == 1 else await real(*a, **k)  # the first check ran too early
    monkeypatch.setattr(doc_versions, "_current_holder", _blind)
    async with _client(db_conn, alice) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{C1}", params={"keep_both": "true"})
        assert r.status_code == 409 and r.json()["detail"]["error"] == "current_elsewhere"
        assert r.json()["detail"]["document"]["id"] == x
        # the transaction is still usable afterwards (the savepoint rolled back)
        assert (await c.put(f"/v1/projects/{pid}/docs/{C1}")).status_code == 200
