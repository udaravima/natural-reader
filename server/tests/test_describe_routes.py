"""Helper model phase 1, Task 5: the routes behind tags and descriptions (spec 2026-11-15 §5.4, §5.5, §5.8).
A project shows its own words when a Contributor set them, else what the helper wrote for the current version; the
generated values are the content's, the edits are the project's alone."""
import hashlib

import pytest

from server.services import bulk_upload, doc_describe, helper, inference_budget
from server.tests import seed
from server.tests.chat_harness import member, shim_pool
from server.tests.versions_harness import build_versions_app

pytestmark = pytest.mark.asyncio

ON = helper.HelperConfig(model="ollama:gemma4:e4b", timeout_s=5.0, input_chars=6000, num_ctx=None, local=True)
OFF = helper.HelperConfig(model="", timeout_s=5.0, input_chars=6000, num_ctx=None, local=False)
A, B, C = "a" * 64, "b" * 64, "c" * 64
FRESH = b"# Fresh\n\nSome new words here."


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def helper_cfg(monkeypatch):
    state = {"cfg": ON}

    async def load(conn, env=None):
        return state["cfg"]
    monkeypatch.setattr(helper, "load_config", load)
    return state


@pytest.fixture
async def world(db_conn, monkeypatch, store, helper_cfg):
    """Alice owns P, which holds report.pdf (A: indexed, described); Rita reads P."""
    monkeypatch.setattr(bulk_upload, "free_mb", lambda: 10_000_000)
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store)
    shim_pool(monkeypatch, db_conn, doc_describe, helper)
    alice = await member(db_conn, "dr-alice", caps=("reader",))
    rita = await member(db_conn, "dr-rita", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P", members=((rita.user_id, "reader"),))
    await seed.seed_doc(db_conn, A, alice.user_id, file_name="report.pdf", state="indexed", tags=("finance",))
    await _gen(db_conn, A, ["finance", "risk"], "Yearly results.")
    x = await seed.place_doc(db_conn, pid, A, alice.user_id)
    return as_user, alice, rita, pid, x


async def _gen(conn, doc_id, tags, description):
    await conn.execute("UPDATE documents SET gen_tags = %s, gen_description = %s, gen_model = 'ollama:m', "
                       "gen_at = now() WHERE doc_id = %s", (tags, description, doc_id))


async def _rows(as_user, who, pid):
    async with as_user(who) as c:
        r = await c.get(f"/v1/projects/{pid}/documents")
    assert r.status_code == 200, r.text
    return {d["id"]: d for d in r.json()}


async def _patch(as_user, who, pid, x, body):
    async with as_user(who) as c:
        return await c.patch(f"/v1/projects/{pid}/documents/{x}", json=body)


async def _jobs(conn):
    cur = await conn.execute("SELECT doc_id, requested_by::text, project_id::text FROM doc_describe_jobs ORDER BY doc_id")
    return await cur.fetchall()


async def test_reader_sees_tags_cannot_patch(world):
    as_user, alice, rita, pid, x = world
    row = (await _rows(as_user, rita, pid))[x]
    assert (row["tags"], row["description"], row["generated"], row["edited"], row["describing"]) == (
        ["finance", "risk"], "Yearly results.", True, False, False)
    r = await _patch(as_user, rita, pid, x, {"tags": ["mine"]})
    assert r.status_code == 403
    async with as_user(rita) as c:
        assert (await c.post(f"/v1/projects/{pid}/documents/{x}/describe")).status_code == 403
        assert (await c.post(f"/v1/projects/{pid}/documents/describe-missing")).status_code == 403


async def test_contributor_patch_and_null_returns_to_generated(world):
    as_user, alice, rita, pid, x = world
    r = await _patch(as_user, alice, pid, x, {"tags": ["Audit", " audit ", "q3"], "description": "  Our words.\n"})
    assert r.status_code == 200, r.text
    assert (r.json()["tags"], r.json()["description"], r.json()["edited"]) == (["audit", "q3"], "Our words.", True)
    r = await _patch(as_user, alice, pid, x, {"description": None})              # tags stay the project's
    assert (r.json()["tags"], r.json()["description"]) == (["audit", "q3"], "Yearly results.")
    r = await _patch(as_user, alice, pid, x, {"tags": None})
    assert (r.json()["tags"], r.json()["edited"]) == (["finance", "risk"], False)
    r = await _patch(as_user, alice, pid, x, {"tags": []})                       # none at all is a choice
    assert (r.json()["tags"], r.json()["edited"]) == ([], True)


async def test_project_edit_stays_in_its_project(db_conn, world):
    as_user, alice, rita, pid, x = world
    qid = await seed.make_project(db_conn, alice.user_id, "Q")
    y = await seed.place_doc(db_conn, qid, A, alice.user_id)
    await _patch(as_user, alice, pid, x, {"tags": ["only in p"], "description": "P's words."})
    row = (await _rows(as_user, alice, qid))[y]
    assert (row["tags"], row["description"]) == (["finance", "risk"], "Yearly results.")


async def test_patch_cleans_tags_like_the_model(world):
    as_user, alice, rita, pid, x = world
    r = await _patch(as_user, alice, pid, x, {"tags": ["#Hardware", "a" * 33, "bad\x07", "", "Hardware"]})
    assert r.status_code == 200 and r.json()["tags"] == ["hardware"]
    r = await _patch(as_user, alice, pid, x, {"tags": [f"t{i}" for i in range(11)]})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "too_many_tags"
    r = await _patch(as_user, alice, pid, x, {"description": "w" * 301})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "description_too_long"
    r = await _patch(as_user, alice, pid, x, {"colour": "red"})
    assert r.status_code == 422


async def test_describe_409_when_off(world, helper_cfg):
    as_user, alice, rita, pid, x = world
    helper_cfg["cfg"] = OFF
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/describe")
        assert r.status_code == 409 and r.json()["detail"]["error"] == "helper_off"
        r = await c.post(f"/v1/projects/{pid}/documents/describe-missing")
        assert r.status_code == 409 and r.json()["detail"]["error"] == "helper_off"


async def test_describe_429_over_budget(db_conn, world, monkeypatch):
    as_user, alice, rita, pid, x = world
    monkeypatch.setattr(helper, "_deployment_budget", lambda: 10)
    await inference_budget.record_usage(db_conn, alice.user_id, 50, 0, model="ollama:x", kind="chat", estimated=False)
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/describe")
    assert r.status_code == 429 and r.json()["detail"]["error"] == "budget_exhausted"
    assert await _jobs(db_conn) == []


async def test_describe_queues_and_clears_project_values(db_conn, world):
    as_user, alice, rita, pid, x = world
    await _patch(as_user, alice, pid, x, {"tags": ["ours"], "description": "Ours."})
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/describe")
    assert r.status_code == 202, r.text
    assert (r.json()["describing"], r.json()["edited"], r.json()["tags"]) == (True, False, ["finance", "risk"])
    assert await _jobs(db_conn) == [(A, alice.user_id, pid)]


async def test_describe_refuses_a_document_not_indexed(db_conn, world):
    as_user, alice, rita, pid, x = world
    await db_conn.execute("UPDATE documents SET state = 'stored' WHERE doc_id = %s", (A,))
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/{x}/describe")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "not_indexed"


async def test_describe_missing_queues_only_untagged_indexed(db_conn, world):
    as_user, alice, rita, pid, x = world
    await seed.seed_doc(db_conn, B, alice.user_id, file_name="b.pdf", state="indexed", project_ids=(pid,))
    await seed.seed_doc(db_conn, C, alice.user_id, file_name="c.pdf", state="stored", project_ids=(pid,))
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/documents/describe-missing")
    assert r.status_code == 202 and r.json() == {"queued": 1}
    assert await _jobs(db_conn) == [(B, alice.user_id, pid)]
    rows = await _rows(as_user, alice, pid)
    assert [d["describing"] for d in rows.values() if d["doc_id"] == B] == [True]
    assert sum(1 for d in rows.values() if d["state"] == "indexed" and not d["generated"]) == 1


async def test_upload_describe_false_queues_nothing(db_conn, world):
    as_user, alice, rita, pid, x = world
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/uploads", files={"file": ("fresh.md", FRESH, "text/markdown")},
                         data={"describe": "false"})
    assert r.status_code == 201, r.text
    cur = await db_conn.execute("SELECT state FROM documents WHERE doc_id = %s", (r.json()["doc_id"],))
    assert await cur.fetchone() == ("indexed",)                     # indexed after the reply, as a runner would
    assert await _jobs(db_conn) == []


async def test_upload_queues_the_helper_with_its_project_by_default(db_conn, world):
    as_user, alice, rita, pid, x = world
    async with as_user(alice) as c:
        r = await c.post(f"/v1/projects/{pid}/uploads", files={"file": ("fresh.md", FRESH, "text/markdown")})
    assert r.status_code == 201, r.text
    assert await _jobs(db_conn) == [(hashlib.sha256(FRESH).hexdigest(), alice.user_id, pid)]


async def test_new_version_shows_its_own_generated_values(db_conn, world):
    as_user, alice, rita, pid, x = world
    await seed.seed_doc(db_conn, B, alice.user_id, file_name="report.pdf", state="indexed")
    await _gen(db_conn, B, ["forecast"], "Next year's plan.")
    await seed.add_version(db_conn, x, B, alice.user_id)
    row = (await _rows(as_user, alice, pid))[x]
    assert (row["tags"], row["description"]) == (["forecast"], "Next year's plan.")


async def test_hand_typed_values_survive_a_new_version(db_conn, world):
    as_user, alice, rita, pid, x = world
    await _patch(as_user, alice, pid, x, {"tags": ["kept"], "description": "Kept."})
    await seed.seed_doc(db_conn, B, alice.user_id, file_name="report.pdf", state="indexed")
    await _gen(db_conn, B, ["forecast"], "Next year's plan.")
    await seed.add_version(db_conn, x, B, alice.user_id)
    row = (await _rows(as_user, alice, pid))[x]
    assert (row["tags"], row["description"]) == (["kept"], "Kept.")


async def _library(as_user, who, query=""):
    async with as_user(who) as c:
        r = await c.get(f"/v1/docs{query}")
    assert r.status_code == 200, r.text
    return {d["doc_id"]: d for d in r.json()}


async def test_library_row_suggested_tags_exclude_own(world):
    as_user, alice, rita, pid, x = world
    row = (await _library(as_user, alice))[A]
    assert (row["tags"], row["suggested_tags"], row["description"], row["generated"], row["describing"]) == (
        ["finance"], ["risk"], "Yearly results.", True, False)


async def test_library_search_and_tag_filter_match_generated_values(world):
    as_user, alice, rita, pid, x = world
    assert A in await _library(as_user, alice, "?q=risk")
    assert A in await _library(as_user, alice, "?q=yearly")
    assert A in await _library(as_user, alice, "?tag=risk")
    assert A not in await _library(as_user, alice, "?q=nothing-like-it")


async def test_library_describe_for_a_holder_never_described(db_conn, world):
    as_user, alice, rita, pid, x = world
    await seed.seed_doc(db_conn, B, alice.user_id, file_name="b.pdf", state="indexed")
    async with as_user(alice) as c:
        r = await c.post(f"/v1/docs/{B}/describe")
        assert r.status_code == 202 and r.json() == {"doc_id": B, "describing": True}
        r = await c.post(f"/v1/docs/{A}/describe")                       # already has tags: the project's Generate
        assert r.status_code == 409 and r.json()["detail"]["error"] == "already_described"
    async with as_user(rita) as c:                                       # holds no entry for B
        assert (await c.post(f"/v1/docs/{B}/describe")).status_code == 404
    assert await _jobs(db_conn) == [(B, alice.user_id, None)]


async def test_settings_helper_says_whether_it_is_on_and_nothing_more(db_conn, helper_cfg):
    """Every signed-in screen asks this to decide whether to offer Generate and the upload tick (helper spec §5.4)."""
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from server.auth import deps
    from server.routers import settings as settings_router
    plain = await member(db_conn, "dr-plain", caps=("reader",))
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: plain
    app.include_router(settings_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/v1/settings/helper")).json() == {"on": True}
        helper_cfg["cfg"] = OFF
        assert (await c.get("/v1/settings/helper")).json() == {"on": False}
