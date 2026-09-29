"""v2.3 Task E: documents indexed before sub-page chunks and prefixes (or
under another embedding profile) are rebuilt in the background on first use.
The old chunks stay searchable until the new set swaps in (Review focus 6);
a document embedded by another model isn't searched meanwhile."""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from server.services import doc_pipeline, embeddings, extract
from server.services.doc_search import ReadableDoc, readable_doc
from server.tests import seed
from server.tests.docs_harness import build_docs_app
from server.tests.test_doc_pipeline import _member

LONG = " ".join(f"Sentence number {i} says something about the method." for i in range(120)).encode()
DOC = hashlib.sha256(LONG).hexdigest()


@pytest.fixture(autouse=True)
def _fresh_rebuild_state(monkeypatch):
    """The pipeline's per-process rebuild bookkeeping, empty for each test."""
    monkeypatch.setattr(doc_pipeline, "_failed", {})
    monkeypatch.setattr(doc_pipeline, "_rebuilding", set())


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def embedded(db_conn, monkeypatch, store):
    """Records what was embedded, and checks the old chunks are still there
    while the new ones are being embedded."""
    seen = {"texts": [], "old_chunks_during": []}

    async def fake_embed(texts):
        cur = await db_conn.execute("SELECT count(*) FROM doc_chunks WHERE doc_id = %s AND text = 'old'",
                                    (DOC,))
        seen["old_chunks_during"].append((await cur.fetchone())[0])
        seen["texts"] += texts
        return [[1.0] + [0.0] * (embeddings.EMBEDDING_DIM - 1) for _ in texts]

    build_docs_app(db_conn, monkeypatch, storage_dir=store)
    # The real embed_documents (it adds the prefix) over a fake embed_batch.
    monkeypatch.setattr(doc_pipeline, "embed_documents", embeddings.embed_documents)
    monkeypatch.setattr(embeddings, "embed_batch", fake_embed)
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    return seen


async def _old_index(db_conn, owner, store=None, *, model="nomic-embed-text", profile=None,
                     file_type="text", extracted_by="server"):
    path = None
    if store is not None:
        path = store / f"{DOC}.txt"
        path.write_bytes(LONG)
    await seed.seed_doc(db_conn, DOC, owner, file_name="Long.txt", file_type=file_type, state="indexed",
                        bytes_path=path, extracted_by=extracted_by)
    await db_conn.execute("UPDATE documents SET embedding_model = %s, embedding_profile = %s, page_count = 1 "
                          "WHERE doc_id = %s", (model, profile, DOC))
    vec = "[" + ",".join(["1"] + ["0"] * (embeddings.EMBEDDING_DIM - 1)) + "]"
    await db_conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, 0, 1, 'page', 'old', 'h', %s::vector, %s)", (DOC, vec, model))


async def _state(db_conn):
    cur = await db_conn.execute("SELECT state, embedding_profile, embedding_model FROM documents WHERE doc_id = %s",
                                (DOC,))
    return await cur.fetchone()


async def _chunks(db_conn):
    cur = await db_conn.execute("SELECT page, chunk_type, text, embedding_model FROM doc_chunks "
                                "WHERE doc_id = %s ORDER BY ord", (DOC,))
    return await cur.fetchall()


async def test_a_stale_document_is_rebuilt_with_split_prefixed_chunks(db_conn, embedded, store):
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store)
    await doc_pipeline.run_rebuild(DOC)
    chunks = await _chunks(db_conn)
    pages = [c[0] for c in chunks]
    assert len(chunks) > len(set(pages)) and pages == sorted(pages)      # pages split; pages kept in order
    assert chunks[1][1] == "page" + extract.CONTINUATION_SUFFIX
    assert all(len(c[2]) <= extract.CHUNK_MAX_CHARS for c in chunks)
    assert all(t.startswith("search_document: ") for t in embedded["texts"])
    # The old set answered searches until the swap.
    assert embedded["old_chunks_during"] and all(n == 1 for n in embedded["old_chunks_during"])
    assert await _state(db_conn) == ("indexed", embeddings.current_profile(), "nomic-embed-text")


async def test_ensure_current_schedules_one_rebuild_and_keeps_a_same_model_document_searchable(
        db_conn, embedded, store, monkeypatch):
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store)
    started = []

    async def fake_rebuild(doc_id):
        started.append(doc_id)
        await asyncio.sleep(0)

    monkeypatch.setattr(doc_pipeline, "run_rebuild", fake_rebuild)
    doc = await readable_doc(db_conn, DOC, owner.user_id)
    first = doc_pipeline.ensure_current(doc)
    second = doc_pipeline.ensure_current(doc)
    await asyncio.sleep(0.01)
    assert started == [DOC]                                                  # one rebuild, not two
    assert first.state == second.state == "indexed"                          # old chunks still answer


async def test_a_document_embedded_by_another_model_is_not_searched_until_rebuilt(db_conn, embedded, store,
                                                                                   monkeypatch):
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store, model="mxbai-embed-large", profile="mxbai-embed-large|''|''|x")

    async def fake_rebuild(doc_id):
        return None

    monkeypatch.setattr(doc_pipeline, "run_rebuild", fake_rebuild)
    doc = doc_pipeline.ensure_current(await readable_doc(db_conn, DOC, owner.user_id))
    assert doc.state == "reindexing"


def test_a_current_or_unindexed_document_is_left_alone(monkeypatch):
    monkeypatch.setattr(doc_pipeline, "_schedule_rebuild", lambda doc_id: pytest.fail("scheduled"))
    current = ReadableDoc(DOC, "x", "indexed", 1, embeddings.current_profile(), "nomic-embed-text")
    assert doc_pipeline.ensure_current(current) is current
    extracting = ReadableDoc(DOC, "x", "extracting", None, None, None)
    assert doc_pipeline.ensure_current(extracting) is extracting
    assert doc_pipeline.ensure_current(None) is None


async def test_a_legacy_document_without_stored_bytes_is_rebuilt_from_its_own_chunks(db_conn, embedded):
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, None, extracted_by="client")
    await db_conn.execute("UPDATE doc_chunks SET text = %s WHERE doc_id = %s", (LONG.decode(), DOC))
    await doc_pipeline.run_rebuild(DOC)
    chunks = await _chunks(db_conn)
    assert len(chunks) > 1
    assert (await _state(db_conn))[1] == embeddings.current_profile()


async def test_a_converted_document_is_rebuilt_from_its_markdown_pages(db_conn, embedded, store):
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store)
    await db_conn.execute("UPDATE documents SET conversion_state = 'converted' WHERE doc_id = %s", (DOC,))
    await db_conn.execute("INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s, 1, '# One'), (%s, 2, '# Two')",
                          (DOC, DOC))
    await doc_pipeline.run_rebuild(DOC)
    assert [(c[0], c[1], c[2]) for c in await _chunks(db_conn)] == [(1, "page-md", "# One"), (2, "page-md", "# Two")]


async def test_a_failed_rebuild_keeps_the_old_index(db_conn, monkeypatch, store):
    async def failing_embed(texts):
        return [None for _ in texts]

    build_docs_app(db_conn, monkeypatch, storage_dir=store, embed=failing_embed)
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store)
    await doc_pipeline.run_rebuild(DOC)
    assert [c[2] for c in await _chunks(db_conn)] == ["old"]
    assert (await _state(db_conn))[1] is None


async def test_a_new_document_is_indexed_with_split_chunks_and_the_current_profile(db_conn, embedded, store):
    owner = await _member(db_conn, "owner")
    (store / f"{DOC}.txt").write_bytes(LONG)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_name="Long.txt", file_type="text", state="stored",
                        bytes_path=store / f"{DOC}.txt")
    await doc_pipeline.run_pipeline(DOC)
    assert len(await _chunks(db_conn)) > 1
    assert await _state(db_conn) == ("indexed", embeddings.current_profile(), "nomic-embed-text")


async def test_a_reindexing_document_gets_no_document_tools_and_the_rules_say_why(monkeypatch):
    from server.chat.config import ChatConfig
    from server.chat.context import TurnInput, build_context
    from server.chat.tools import ToolContext, available_tools
    from datetime import datetime, timezone
    doc = ReadableDoc(DOC, "Thesis.pdf", "reindexing", 3, "old|''|''|x", "old-model")
    ctx = ToolContext("u", doc)
    assert [t.name for t in available_tools(ctx) if getattr(t, "reads_documents", False)] == []
    built = await build_context(TurnInput(
        user_id="u", text="q", attachments=(), doc=doc, timezone="UTC", pins=[], history=[], window=None,
        now=datetime(2026, 9, 29, tzinfo=timezone.utc), tools=tuple(available_tools(ctx)), tool_ctx=ctx),
        ChatConfig())
    system = built.messages[0].content
    assert "being re-indexed" in system and "search_documents" not in system
    assert not built.prefetch_hit


async def test_a_turn_on_a_mismatched_document_reads_it_as_reindexing(db_conn, embedded, store, monkeypatch):
    from server.chat import orchestrator
    from server.chat.orchestrator import TurnRequest
    from server.llm.types import CallSettings
    from server.tests.chat_harness import shim_pool
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store, model="other-model")
    shim_pool(monkeypatch, db_conn, orchestrator)
    monkeypatch.setattr(doc_pipeline, "_schedule_rebuild", lambda doc_id: None)
    req = TurnRequest(user_id=owner.user_id, session_id="s", model_id="m", text="q", attachments=(),
                      settings=CallSettings(), doc_id=DOC, timezone="UTC")
    assert (await orchestrator._open_doc(req)).state == "reindexing"


async def test_the_search_route_says_reindexing_for_a_mismatched_document(db_conn, monkeypatch, store):
    _app, as_user = build_docs_app(db_conn, monkeypatch, storage_dir=store)
    monkeypatch.setattr(doc_pipeline, "_schedule_rebuild", lambda doc_id: None)
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store, model="other-model")
    async with as_user(owner) as c:
        r = await c.post(f"/v1/docs/{DOC}/search", json={"query": "q", "k": 3})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "reindexing"


# ---- Task E review fix round 1 ----

async def test_identical_parts_are_kept_as_separate_chunks_on_their_own_pages(db_conn, embedded, store,
                                                                             monkeypatch):
    """Review I1: UNIQUE(doc_id, text_hash) hashed only the text, so identical
    parts (a letterhead opening every page, repetitive text) collapsed into
    one row on the last page, and page reads lost text."""
    from server.chat.tools import ToolContext, read_document_pages as rp_tool
    from server.chat.tools import available_tools, run_tool
    from server.llm.types import ToolCall
    from server.tests.chat_harness import shim_pool
    owner = await _member(db_conn, "owner")
    letterhead = "ACME Research Ltd. Confidential report. " * 20                    # ~800 chars, same on each page
    pages = [extract.Chunk(0, 1, "page", letterhead + "Page one body. " * 60),
             extract.Chunk(1, 2, "page", letterhead + "Page two body. " * 60),
             extract.Chunk(2, 3, "page", " ".join(["word"] * 900))]
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_name="Long.txt", file_type="text", state="indexed")
    await doc_pipeline.replace_chunks(db_conn, DOC, extract.split_chunks(pages))
    first_parts = [c for c in await _chunks(db_conn) if c[1] == "page"]
    assert [c[0] for c in first_parts] == [1, 2, 3]                                   # each page keeps its opening
    shim_pool(monkeypatch, db_conn, rp_tool)
    ctx = ToolContext(owner.user_id, await readable_doc(db_conn, DOC, owner.user_id))
    for chunk in pages:
        run = await run_tool(ToolCall("c", "read_document_pages", {"first_page": chunk.page}), ctx,
                             available_tools(ctx))
        assert run.result["pages"][0]["text"] == chunk.text


async def test_a_failed_rebuild_is_not_retried_at_once(db_conn, monkeypatch, store):
    """Review I2: without a backoff, every turn on the document started another
    full re-embed that failed the same way."""
    async def failing_embed(texts):
        return [None for _ in texts]

    build_docs_app(db_conn, monkeypatch, storage_dir=store, embed=failing_embed)
    owner = await _member(db_conn, "owner")
    await _old_index(db_conn, owner.user_id, store)
    await doc_pipeline.run_rebuild(DOC)
    started = []

    async def recording_rebuild(doc_id):
        started.append(doc_id)

    monkeypatch.setattr(doc_pipeline, "run_rebuild", recording_rebuild)
    doc_pipeline._schedule_rebuild(DOC)
    await asyncio.sleep(0.01)
    assert started == []
    later = doc_pipeline._clock() + doc_pipeline.REBUILD_RETRY_S + 1
    monkeypatch.setattr(doc_pipeline, "_clock", lambda: later)
    doc_pipeline._schedule_rebuild(DOC)
    await asyncio.sleep(0.01)
    assert started == [DOC]


def test_chunk_size_leaves_room_for_the_prefix_within_the_embedding_input():
    """Review minor: a part longer than EMBEDDING_MAX_CHARS would be truncated
    before embedding, bringing back the invisible tail."""
    assert extract.chunk_settings({"CHUNK_MAX_CHARS": "5000"}) == (1200, 200)
    assert extract.chunk_settings({"CHUNK_MAX_CHARS": "5000", "EMBEDDING_MAX_CHARS": "8000"})[0] == 5000
