"""C2.1 Task 3: a chat scoped to a project (spec §5.1, §5.10, §5.12).

The scope is the project's current documents (list_project_documents), stored
on the session and re-checked on every turn. This task is plumbing: search
over the scope keeps today's cross-document cosine sort as the baseline."""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import authz, deps
from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.config import ChatConfig, load_chat_config
from server.chat.orchestrator import TurnRequest, run_turn
from server.chat.project_scope import load_project_scope
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import read_document_pages as rp_tool
from server.chat.tools import search_documents as sd_tool
from server.llm.types import CallSettings, Finish, ToolCall, ToolCallReady, Usage
from server.routers import chat_sessions as sessions_router
from server.services import doc_pipeline
from server.services.embeddings import EMBEDDING_DIM, current_profile
from server.tests import seed
from server.tests.chat_harness import FakeRouter, member, reply, shim_pool
from server.tests.test_chat_turns import _post, app  # noqa: F401 — the route client fixture

A1, A2, B, C, D = ("a" * 64, "b" * 64, "c" * 64, "e" * 64, "f" * 64)


def _vec(*head):
    return "[" + ",".join(str(x) for x in list(head) + [0] * (EMBEDDING_DIM - len(head))) + "]"


async def _doc(conn, doc_id, holder, name, *, state="indexed", pages=3):
    """Content under the current embedding profile, so ensure_current leaves
    it alone (no background rebuild)."""
    await seed.seed_doc(conn, doc_id, holder.user_id, file_name=name, state=state)
    await conn.execute("UPDATE documents SET page_count = %s, embedding_profile = %s WHERE doc_id = %s",
                       (pages, current_profile(), doc_id))


async def _chunk(conn, doc_id, ord_, page, text, vec=None):
    await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, %s, %s, 'page', %s, %s, %s::vector, 'm')",
        (doc_id, ord_, page, text, f"h{doc_id[:4]}{ord_}", vec or _vec(1)))


async def _two_doc_project(conn, name="Handbook"):
    """Alice's project with Alpha.md (v1 then v2: v2 current) and Beta.md."""
    alice = await member(conn, "alice")
    pid = await seed.make_project(conn, alice.user_id, name)
    await _doc(conn, A1, alice, "Alpha.md")
    await _doc(conn, A2, alice, "Alpha v2.md")
    await _doc(conn, B, alice, "Beta.md")
    alpha = await seed.place_doc(conn, pid, A1, alice.user_id, file_name="Alpha.md")
    await seed.add_version(conn, alpha, A2, alice.user_id, file_name="Alpha.md")
    await seed.place_doc(conn, pid, B, alice.user_id, file_name="Beta.md")
    return alice, pid


# ---- the scope ----

async def test_scope_lists_only_current_versions_in_project_order(db_conn):
    alice, pid = await _two_doc_project(db_conn)
    scope = await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=None)
    assert (scope.project_id, scope.name) == (pid, "Handbook")
    assert [(d.ref, d.doc.doc_id, d.name) for d in scope.docs] == [(1, A2, "Alpha.md"), (2, B, "Beta.md")]
    assert [d.doc.page_count for d in scope.docs] == [3, 3]


async def test_duplicate_names_get_a_number(db_conn):
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, alice.user_id)
    for doc_id in (A1, B):
        await _doc(db_conn, doc_id, alice, "Notes.md")
        await seed.place_doc(db_conn, pid, doc_id, alice.user_id)
    scope = await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=None)
    assert [d.name for d in scope.docs] == ["Notes.md", "Notes.md (2)"]
    assert {d.doc.doc_id for d in scope.docs} == {A1, B}
    # A document really named "Notes.md (2)" keeps its name; the repeat skips it.
    await _doc(db_conn, C, alice, "Notes.md (2)")
    await seed.place_doc(db_conn, pid, C, alice.user_id)
    scope = await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=None)
    assert [d.name for d in scope.docs] == ["Notes.md", "Notes.md (3)", "Notes.md (2)"]
    assert scope.docs[2].doc.doc_id == C


async def test_unindexed_documents_are_listed_not_searchable(db_conn, monkeypatch):
    monkeypatch.setattr(doc_pipeline, "_schedule_rebuild", lambda doc_id: None)
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, alice.user_id)
    await _doc(db_conn, A1, alice, "A failed.pdf", state="failed")
    await _doc(db_conn, B, alice, "B indexing.pdf", state="indexing")
    await _doc(db_conn, C, alice, "C indexed.pdf")
    await _doc(db_conn, D, alice, "D old model.pdf")
    # Indexed under another embedding model: ensure_current reads it as reindexing.
    await db_conn.execute("UPDATE documents SET embedding_profile = 'old|x', embedding_model = 'old-model' "
                          "WHERE doc_id = %s", (D,))
    for doc_id in (A1, B, C, D):
        await seed.place_doc(db_conn, pid, doc_id, alice.user_id)
    scope = await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=None)
    assert [(d.ref, d.doc.state) for d in scope.docs] == [
        (1, "failed"), (2, "indexing"), (3, "indexed"), (4, "reindexing")]
    assert [d.doc.doc_id for d in scope.searchable] == [C]


async def test_open_document_is_marked_only_when_in_the_project(db_conn):
    alice, pid = await _two_doc_project(db_conn)
    await _doc(db_conn, C, alice, "Elsewhere.md")

    async def marked(open_doc_id):
        scope = await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=open_doc_id)
        return [d.doc.doc_id for d in scope.docs if d.open_in_reader]

    assert await marked(B) == [B]
    assert await marked(A1) == []     # an old version: not a current document of the project
    assert await marked(C) == []      # not in the project
    assert await marked(None) == []


async def test_loading_the_scope_needs_membership(db_conn):
    _, pid = await _two_doc_project(db_conn)
    bob = await member(db_conn, "bob")
    with pytest.raises(LookupError):
        await load_project_scope(db_conn, bob.user_id, pid, open_doc_id=None)


# ---- access, every turn (the route) ----

def _body(**context):
    return {"message": {"content": "What is in this project?"}, "model": "ollama:m",
            "context": {"timezone": "UTC", **context}}


async def _count(conn, sql, params=()):
    return (await (await conn.execute(sql, params)).fetchone())[0]


async def test_turn_for_a_non_member_is_404_and_clears_the_session_scope(db_conn, app):
    owner = await member(db_conn, "owner")
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, owner.user_id, members=[(alice.user_id, "reader")])
    await db_conn.execute("INSERT INTO chat_sessions (id, user_id, scope_project_id) VALUES ('s-1', %s, %s)",
                          (alice.user_id, pid))
    await db_conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
                          (pid, alice.user_id))   # alice left the project
    r = await _post(app, alice, body=_body(project_id=pid))
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found", "message": authz.PROJECT_NOT_FOUND}
    assert await store.session_scope("s-1", alice.user_id) is None
    assert await _count(db_conn, "SELECT count(*) FROM chat_messages") == 0   # refused before begin_turn
    assert app[1].calls == []


async def test_admin_who_is_not_a_member_gets_404(db_conn, app):
    owner = await member(db_conn, "owner")
    admin = dataclasses.replace(await member(db_conn, "root"), role="admin")
    pid = await seed.make_project(db_conn, owner.user_id)
    r = await _post(app, admin, body=_body(project_id=pid))
    assert r.status_code == 404 and r.json()["detail"]["error"] == "not_found"
    assert await _count(db_conn, "SELECT count(*) FROM chat_sessions") == 0


async def test_a_project_id_that_is_not_a_uuid_is_422(db_conn, app):
    r = await _post(app, await member(db_conn, "alice"), body=_body(project_id="not-a-uuid"))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "invalid_request"


async def test_absent_project_id_keeps_the_stored_scope_and_null_clears_it(db_conn, app):
    alice, pid = await _two_doc_project(db_conn)
    router = app[1]

    assert (await _post(app, alice, body=_body(project_id=pid))).status_code == 200
    assert await store.session_scope("s-1", alice.user_id) == pid
    assert "search_documents" in router.calls[-1]["tools"]          # the project's documents

    assert (await _post(app, alice, body=_body())).status_code == 200
    assert await store.session_scope("s-1", alice.user_id) == pid    # kept
    assert "search_documents" in router.calls[-1]["tools"]          # still a project turn

    assert (await _post(app, alice, body=_body(project_id=None))).status_code == 200
    assert await store.session_scope("s-1", alice.user_id) is None   # cleared
    assert "search_documents" not in router.calls[-1]["tools"]      # general chat

    # The stored scope is re-checked: a member who left gets 404 without sending one.
    await store.set_session_scope("s-1", alice.user_id, pid)
    owner = await member(db_conn, "owner2")
    await seed.add_member(db_conn, pid, owner.user_id, "owner")
    await db_conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
                          (pid, alice.user_id))
    calls = len(router.calls)
    r = await _post(app, alice, body=_body())
    assert r.status_code == 404 and r.json()["detail"]["error"] == "not_found"
    assert await store.session_scope("s-1", alice.user_id) is None
    assert len(router.calls) == calls


async def test_set_session_scope_touches_only_the_callers_session(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store)
    alice, pid = await _two_doc_project(db_conn)
    bob = await member(db_conn, "bob")
    await db_conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-1', %s)", (alice.user_id,))
    await store.set_session_scope("s-1", bob.user_id, pid)
    assert await store.session_scope("s-1", alice.user_id) is None
    assert await store.session_scope("s-1", bob.user_id) is None


# ---- session meta ----

def _sessions_client(app_, principal):
    application, current = app_
    current["p"] = principal
    return httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://t")


@pytest.fixture
def sessions_app(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, sessions_router)
    application = FastAPI()
    application.include_router(sessions_router.router)
    current: dict = {}
    application.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    return application, current


async def test_session_meta_carries_scope_project_name_for_members_only(db_conn, sessions_app):
    alice, pid = await _two_doc_project(db_conn)
    await db_conn.execute("INSERT INTO chat_sessions (id, user_id, scope_project_id) VALUES ('s-1', %s, %s)",
                          (alice.user_id, pid))
    await db_conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-2', %s)", (alice.user_id,))

    async def meta():
        async with _sessions_client(sessions_app, alice) as c:
            listed = {s["id"]: s["scopeProject"] for s in (await c.get("/v1/chat/sessions")).json()}
            got = (await c.get("/v1/chat/sessions/s-1")).json()["scopeProject"]
            plain = (await c.get("/v1/chat/sessions/s-2")).json()["scopeProject"]
        return listed, got, plain

    listed, got, plain = await meta()
    assert listed == {"s-1": {"id": pid, "name": "Handbook"}, "s-2": None}
    assert got == {"id": pid, "name": "Handbook"} and plain is None

    owner = await member(db_conn, "owner2")
    await seed.add_member(db_conn, pid, owner.user_id, "owner")
    await db_conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
                          (pid, alice.user_id))
    listed, got, _ = await meta()
    assert listed["s-1"] == {"id": pid, "name": None} and got == {"id": pid, "name": None}


# ---- the tools in project scope ----

async def _project_ctx(conn, monkeypatch):
    """Alice's two-document project with pages to read, as a turn's ToolContext."""
    shim_pool(monkeypatch, conn, rp_tool, sd_tool)
    alice, pid = await _two_doc_project(conn)
    await _chunk(conn, A2, 0, 1, "Alpha page one.")
    await _chunk(conn, A2, 1, 2, "Alpha page two.")
    await _chunk(conn, B, 0, 1, "Beta page one.", _vec(1, 1))
    await _chunk(conn, B, 1, 2, "Beta page two.", _vec(0, 1))
    scope = await load_project_scope(conn, alice.user_id, pid, open_doc_id=None)
    return ToolContext(alice.user_id, None, project=scope, question="q")


async def _call(ctx, name, args):
    return await run_tool(ToolCall("c1", name, args), ctx, available_tools(ctx))


async def test_read_document_pages_needs_a_document_in_project_scope(db_conn, monkeypatch):
    ctx = await _project_ctx(db_conn, monkeypatch)
    missing = await _call(ctx, "read_document_pages", {"first_page": 1})
    assert not missing.ok
    assert "1 Alpha.md" in missing.result["error"] and "2 Beta.md" in missing.result["error"]

    run = await _call(ctx, "read_document_pages", {"document": 2, "first_page": 1, "last_page": 2})
    assert run.ok
    assert run.result["pages"] == [{"ref": 2, "page": 1, "text": "Beta page one."},
                                   {"ref": 2, "page": 2, "text": "Beta page two."}]
    assert ctx.evidence == [(B, 1, "Beta page one."), (B, 2, "Beta page two.")]
    # No single docId: a project reply's "(page N)" mustn't open one document (R3).
    assert "docId" not in run.summary["result_summary"]
    assert run.summary["result_summary"]["document"] == {"docId": B, "docName": "Beta.md"}

    by_name = await _call(ctx, "read_document_pages", {"document": "alpha.MD", "first_page": 2})
    assert by_name.result["pages"] == [{"ref": 1, "page": 2, "text": "Alpha page two."}]
    assert (await _call(ctx, "read_document_pages", {"document": "2", "first_page": 2})).result["pages"][0]["ref"] == 2
    unknown = await _call(ctx, "read_document_pages", {"document": "Gamma.md", "first_page": 1})
    assert unknown.result["error"].startswith("Unknown document: Gamma.md.") and "2 Beta.md" in unknown.result["error"]


async def test_a_document_that_isnt_searchable_is_named_as_such(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, rp_tool)
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, alice.user_id)
    await _doc(db_conn, A1, alice, "Ready.md")
    await _doc(db_conn, B, alice, "Scan.pdf", state="failed")
    for doc_id in (A1, B):
        await seed.place_doc(db_conn, pid, doc_id, alice.user_id)
    ctx = ToolContext(alice.user_id, None,
                      project=await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=None))
    run = await _call(ctx, "read_document_pages", {"document": "Scan.pdf", "first_page": 1})
    assert run.result == {"error": 'Document 2 ("Scan.pdf") isn\'t searchable yet.'}


async def test_search_documents_covers_the_project_sorted_by_cosine(db_conn, monkeypatch):
    ctx = await _project_ctx(db_conn, monkeypatch)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    run = await _call(ctx, "search_documents", {"query": "page", "k": 3})
    assert run.ok
    assert run.result["documents"] == [{"ref": 1, "name": "Alpha.md"}, {"ref": 2, "name": "Beta.md"}]
    # One ranking across documents (Task 8): every passage has the word "page", so here it is also the cosine
    # order: Alpha's two at 1.0, then Beta's 0.71.
    assert [(p["ref"], p["page"]) for p in run.result["passages"]] == [(1, 1), (1, 2), (2, 1)]
    assert ctx.evidence[-1] == (B, 1, "Beta page one.")
    summary = run.summary["result_summary"]
    assert "docId" not in summary
    assert [p["docId"] for p in summary["passages"]] == [A2, A2, B]


async def test_a_project_search_that_finds_nothing_says_the_project_has_none(db_conn, monkeypatch):
    """Task 14: "in the document" would be false of a project; document scope keeps its own wording
    (test_chat_tools: test_passages_below_the_floor_are_dropped_and_none_left_says_so)."""
    from dataclasses import replace

    from server.chat.config import ChatConfig
    ctx = await _project_ctx(db_conn, monkeypatch)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    strict = replace(ctx, cfg=ChatConfig(search_min_score=1.01))       # nothing reaches the floor
    run = await _call(strict, "search_documents", {"query": "zzzqx"})
    assert run.ok and run.result["passages"] == []
    assert run.result["message"] == "No passages about this in the project's documents."


async def test_a_document_scope_turn_keeps_its_single_document_tools(db_conn, monkeypatch):
    """Document scope is unchanged: no `document` needed, ref 1, the open document."""
    shim_pool(monkeypatch, db_conn, rp_tool)
    alice, _ = await _two_doc_project(db_conn)
    await _chunk(db_conn, B, 0, 1, "Beta page one.")
    from server.services.doc_search import readable_doc
    ctx = ToolContext(alice.user_id, await readable_doc(db_conn, B, alice.user_id))
    run = await _call(ctx, "read_document_pages", {"first_page": 1})
    assert run.result["pages"] == [{"ref": 1, "page": 1, "text": "Beta page one."}]
    assert run.summary["result_summary"]["docId"] == B
    assert ctx.evidence == [(B, 1, "Beta page one.")]


async def test_read_document_pages_ignores_document_in_document_scope(db_conn, monkeypatch):
    """A small model fills `document` in with a near-name; with one open document that must not cost a round."""
    shim_pool(monkeypatch, db_conn, rp_tool)
    alice, _ = await _two_doc_project(db_conn)
    await _chunk(db_conn, B, 0, 1, "Beta page one.")
    from server.services.doc_search import readable_doc
    ctx = ToolContext(alice.user_id, await readable_doc(db_conn, B, alice.user_id))
    for value in ("the beta document", "Alpha.md", 7, "2"):
        run = await _call(ctx, "read_document_pages", {"document": value, "first_page": 1})
        assert run.ok, value
        assert run.result["pages"] == [{"ref": 1, "page": 1, "text": "Beta page one."}]


# ---- the turn ----

def test_project_tool_rounds_config():
    assert ChatConfig().project_max_tool_rounds == 5
    assert load_chat_config({"CHAT_PROJECT_MAX_TOOL_ROUNDS": "8"}).project_max_tool_rounds == 8
    assert load_chat_config({"CHAT_PROJECT_MAX_TOOL_ROUNDS": "21"}).project_max_tool_rounds == 5
    assert load_chat_config({"CHAT_PROJECT_MAX_TOOL_ROUNDS": "0"}).project_max_tool_rounds == 0


async def _project_turn(conn, monkeypatch, router, text="q", cfg=None):
    shim_pool(monkeypatch, conn, store, orchestrator, ctx_mod, sd_tool)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    monkeypatch.setattr(ctx_mod, "embed_query", fake_embed)
    alice, pid = await _two_doc_project(conn)
    await _chunk(conn, A2, 0, 1, "The Alpha deployment uses ZEPHYR-9 nodes.")
    await _chunk(conn, B, 0, 4, "Beta covers the ZEPHYR-9 rollout schedule.")
    claim = await store.begin_turn(session_id="s-1", user_id=alice.user_id, model_id="ollama:m", text=text,
                                   attachments=[], new_session_pins=None, scope_project_id=pid)
    req = TurnRequest(user_id=alice.user_id, session_id="s-1", model_id="ollama:m", text=text, attachments=(),
                      settings=CallSettings(), doc_id=B, timezone="UTC", project_id=pid)
    events = [e async for e in run_turn(req, claim, router=router, cfg=cfg or ChatConfig(), deployment_budget=None)]
    return events, claim


def _search_step(i):
    return [ToolCallReady(ToolCall(f"c{i}", "search_documents", {"query": f"zephyr {i}"})),
            Usage(5, 5), Finish("tool_calls")]


async def test_project_scope_uses_project_tool_rounds(db_conn, monkeypatch):
    router = FakeRouter(steps=[_search_step(i) for i in range(6)])
    events, _ = await _project_turn(db_conn, monkeypatch, router)
    # ChatConfig().max_tool_rounds is 3: a project turn has CHAT_PROJECT_MAX_TOOL_ROUNDS (5).
    assert [bool(c["tools"]) for c in router.calls] == [True] * 5 + [False]
    assert sum(e["type"] == "tool-input-available" for e in events) == 5
    assert json.loads(router.calls[5]["messages"][-1].content)["note"] == orchestrator.LAST_ROUND_NOTE
    assert events[-1]["type"] == "finish" and events[-1]["finishReason"] == "max-steps"


async def test_a_project_turn_emits_no_source_chips_yet(db_conn, monkeypatch):
    """R3: source chips open ONE document; per-document sources come later."""
    router = FakeRouter(steps=[_search_step(0), reply("Beta covers the ZEPHYR-9 rollout schedule (page 4).")])
    events, claim = await _project_turn(db_conn, monkeypatch, router)
    tool_out = next(e for e in events if e["type"] == "tool-output-available")
    assert tool_out["output"]["chunk_count"] == 2   # it searched both documents
    assert "data-sources" not in [e["type"] for e in events]
    cur = await db_conn.execute("SELECT status, doc_context FROM chat_messages WHERE id = %s", (claim.turn_id,))
    status, doc_context = await cur.fetchone()
    assert status == "complete"
    assert not any(n.get("kind") == "sources" for n in (doc_context or {}).get("notes", []))


async def test_a_project_turn_prefetches_across_the_whole_project(db_conn, monkeypatch):
    """Task 8 end to end: the orchestrator hands the project's documents to the prefetch, the note names each
    document that gave passages, and the model reads them as a search_documents result before its turn."""
    router = FakeRouter(steps=[reply("Both cover ZEPHYR-9 (Alpha.md, page 1; Beta.md, page 4).")])
    events, claim = await _project_turn(db_conn, monkeypatch, router, text="zephyr nodes",
                                        cfg=ChatConfig(prefetch="on"))
    [note] = next(e for e in events if e["type"] == "data-context")["items"]
    assert note["kind"] == "prefetch" and "docId" not in note and note["count"] == 2
    assert note["documents"] == [{"docId": A2, "docName": "Alpha.md", "count": 1, "pages": [1]},
                                 {"docId": B, "docName": "Beta.md", "count": 1, "pages": [4]}]
    sent = router.calls[0]["messages"]
    assert [m.role for m in sent] == ["system", "user", "assistant", "tool"]
    result = json.loads(sent[-1].content)
    assert sorted((p["ref"], p["page"]) for p in result["passages"]) == [(1, 1), (2, 4)]
    assert [d["name"] for d in result["documents"]] == ["Alpha.md", "Beta.md"]
    cur = await db_conn.execute("SELECT doc_context FROM chat_messages WHERE id = %s", (claim.turn_id,))
    assert (await cur.fetchone())[0] == {"notes": [note]}
