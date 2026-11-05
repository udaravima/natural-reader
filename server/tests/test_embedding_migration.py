"""Build, then switch (phase 2b Task 5, spec §4.2.4): a second column is filled with the target model while
search keeps using the current one; one transaction swaps them. Review Focus 1-4, and ledger rulings P2-P5."""
import pytest

from server.llm.providers.base import ProviderConfig
from server.services import (console_settings, doc_pipeline, embedding_migration as mig, embedding_state,
                             rebuild_queue)
from server.services.doc_search import ReadableDoc, search_chunks
from server.services.embeddings import EMBEDDING_DIM, Embedder, ProbeResult, profile_of
from server.tests import seed
from server.tests.chat_harness import shim_pool

ENV = {"OLLAMA_URL": "http://ollama.test", "EMBEDDING_MODEL": "nomic-embed-text"}
SEED_ADMIN = "00000000-0000-0000-0000-000000000001"
DOC, DOC2 = "d" * 64, "e" * 64
GEMMA = Embedder(ProviderConfig(name="ollama", kind="ollama", url="http://ollama.test"), "embeddinggemma-2:740m",
                 None, "title: none | text: ", "task: search result | query: ")
MXBAI = Embedder(GEMMA.provider, "mxbai-embed-large", 768, "", "")


def _axis(i):
    """A unit vector along axis i: which model made a vector is readable from where its 1 is."""
    return [1.0 if j == i else 0.0 for j in range(EMBEDDING_DIM)]


AXIS = {"nomic-embed-text": 0, "embeddinggemma-2:740m": 1, "mxbai-embed-large": 2}


def _literal(vec):
    return "[" + ",".join(str(v) for v in vec) + "]"


@pytest.fixture
async def lib(db_conn, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    await db_conn.execute("DELETE FROM app_settings WHERE key IN ('embedding_state', 'embeddings')")
    await embedding_state.init(db_conn, ENV)
    current = await embedding_state.current(db_conn, ENV)
    for doc in (DOC, DOC2):
        await seed.seed_doc(db_conn, doc, None, file_type="text", state="indexed")
        await db_conn.execute("UPDATE documents SET embedding_model = %s, embedding_profile = %s WHERE doc_id = %s",
                              (current.model, profile_of(current), doc))
        for i in range(3):
            await db_conn.execute(
                "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
                "VALUES (%s, %s, 1, 'page', %s, %s, %s::vector, %s)",
                (doc, i, f"{doc[0]} passage {i}", f"{doc[0]}{i}", _literal(_axis(0)), current.model))
    shim_pool(monkeypatch, db_conn, mig, doc_pipeline)
    calls = []

    async def fake_embed_texts(e, texts):
        calls.append((e.model, list(texts)))
        return [_axis(AXIS[e.model]) for _ in texts]
    monkeypatch.setattr(mig, "embed_texts", fake_embed_texts)
    return calls


async def _fill(worker="w"):
    for _ in range(50):
        step = await mig.fill_step(worker)
        if step != "filled":
            return step
    raise AssertionError("never ready")


async def _where_ones(conn, column="embedding"):
    cur = await conn.execute(f"SELECT text, array_position({column}::real[], 1) - 1 FROM doc_chunks ORDER BY text")
    return dict(await cur.fetchall())


async def _has_next_column(conn):
    cur = await conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'doc_chunks' "
                             "AND column_name = 'embedding_next'")
    return await cur.fetchone() is not None


async def test_a_migration_fills_every_vectored_chunk_with_the_target(db_conn, lib):
    assert await mig.start(db_conn, GEMMA, by=SEED_ADMIN) == "started"
    assert (await embedding_state.read_state(db_conn))["total"] == 6
    assert await _fill() == "ready"
    assert set((await _where_ones(db_conn, "embedding_next")).values()) == {1}
    assert all(model == GEMMA.model and all(t.startswith("title: none | text: ") for t in texts)
               for model, texts in lib)                                       # the target, with its document prefix
    assert await mig.switch(db_conn)
    assert set((await _where_ones(db_conn)).values()) == {1}                  # embedding now holds the new vectors
    assert not await _has_next_column(db_conn)
    state = await embedding_state.read_state(db_conn)
    assert state["current"]["model"] == GEMMA.model and state["next"] is None and state["filled"] == 6
    cur = await db_conn.execute("SELECT DISTINCT embedding_model FROM doc_chunks")
    assert await cur.fetchall() == [(GEMMA.model,)]


async def test_search_meanwhile_uses_the_current_vectors(db_conn, lib):
    before = await search_chunks(db_conn, DOC, _axis(0), 3)
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    await _fill()
    assert (await embedding_state.current(db_conn)).model == "nomic-embed-text"   # queries still embed with it
    assert await search_chunks(db_conn, DOC, _axis(0), 3) == before


async def test_the_switch_waits_for_a_chunk_added_during_it(db_conn, lib):
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    assert await _fill() == "ready"
    await db_conn.execute(   # indexing went on meanwhile, with the current model
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, 9, 2, 'page', 'late passage', 'late', %s::vector, 'nomic-embed-text')", (DOC, _literal(_axis(0))))
    assert not await mig.switch(db_conn)
    assert await _fill() == "ready"
    assert await mig.switch(db_conn)
    assert (await _where_ones(db_conn))["late passage"] == 1


async def test_a_lapsed_claim_is_taken_over_and_switches_once(db_conn, lib, monkeypatch):
    monkeypatch.setattr(mig, "FILL_BATCH", 2)
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    assert await mig.fill_step("a") == "filled"
    assert await mig.fill_step("b") == "claimed-elsewhere"
    state = await embedding_state.read_state(db_conn)
    await embedding_state.write_state(db_conn, {**state, "claimed_at": "2000-01-01T00:00:00+00:00"})   # "a" died
    assert await _fill("b") == "ready"
    assert await mig.switch(db_conn)
    assert not await mig.switch(db_conn)
    assert (await embedding_state.read_state(db_conn))["current"]["model"] == GEMMA.model


async def test_a_new_target_mid_way_starts_over(db_conn, lib, monkeypatch):
    monkeypatch.setattr(mig, "FILL_BATCH", 2)
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    assert await mig.fill_step("w") == "filled"
    real = mig.embed_texts

    async def chosen_again_while_embedding(e, texts):   # a gemma batch whose write lands after the new start
        out = await real(e, texts)
        await mig.start(db_conn, MXBAI, by=SEED_ADMIN)
        return out
    monkeypatch.setattr(mig, "embed_texts", chosen_again_while_embedding)
    await mig.fill_step("w")
    monkeypatch.setattr(mig, "embed_texts", real)
    assert set((await _where_ones(db_conn, "embedding_next")).values()) == {None}   # no gemma vector kept
    state = await embedding_state.read_state(db_conn)
    assert state["next"]["model"] == MXBAI.model and state["filled"] == 0
    assert await _fill() == "ready"
    assert set((await _where_ones(db_conn, "embedding_next")).values()) == {2}       # one model in the column


async def test_cancel_drops_the_column_and_changes_nothing(db_conn, lib):
    await console_settings.save(db_conn, embedding_state.SECTION, {"model": GEMMA.model}, updated_by=SEED_ADMIN,
                                env=ENV)
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    await mig.fill_step("w")
    await mig.cancel(db_conn, by=SEED_ADMIN)
    assert not await _has_next_column(db_conn)
    state = await embedding_state.read_state(db_conn)
    assert state["next"] is None and state["current"]["model"] == "nomic-embed-text"
    assert set((await _where_ones(db_conn)).values()) == {0}
    assert not await embedding_state.needs_migration(db_conn, ENV)    # the target went back: nothing restarts it


async def test_after_the_switch_documents_need_no_rebuild(db_conn, lib):
    await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
    await _fill()
    await mig.switch(db_conn)
    now = await embedding_state.current(db_conn)
    cur = await db_conn.execute("SELECT embedding_model, embedding_profile FROM documents WHERE doc_id = %s", (DOC,))
    model, profile = await cur.fetchone()
    assert (model, profile) == (GEMMA.model, profile_of(now))
    doc = ReadableDoc(DOC, "x", "indexed", 1, profile, model)
    assert await doc_pipeline.ensure_current(db_conn, doc) is doc
    assert (await rebuild_queue.queue_counts(db_conn))["queued"] == 0


async def test_the_same_model_on_another_provider_switches_at_once(db_conn, lib):
    elsewhere = Embedder(ProviderConfig(name="other", kind="ollama", url="http://other.test"), "nomic-embed-text:latest",
                         None, "search_document: ", "search_query: ")
    assert await mig.start(db_conn, elsewhere, by=SEED_ADMIN) == "switched"
    assert not await _has_next_column(db_conn)
    state = await embedding_state.read_state(db_conn)
    assert state["current"]["provider"] == "other" and state["current"]["model"] == "nomic-embed-text"
    assert lib == []                                                      # nothing re-embedded


async def test_an_index_write_after_the_switch_is_made_again_with_the_new_model(db_conn, lib, monkeypatch):
    """Ledger ruling P2: a batch embedded before the switch and written after it would put old-model vectors in
    the new column. The writer checks the state row in its write transaction and embeds again."""
    await seed.seed_doc(db_conn, "f" * 64, None, file_type="text", state="indexing")
    await db_conn.execute("INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash) "
                          "VALUES (%s, 0, 1, 'page', 'fresh passage', 'fresh')", ("f" * 64,))
    switched = []

    async def embed_documents(texts, e):
        if not switched:   # the admin's switch completes while this batch is being embedded
            switched.append(True)
            await mig.start(db_conn, GEMMA, by=SEED_ADMIN)
            await _fill()
            assert await mig.switch(db_conn)
        return [_axis(AXIS[e.model]) for _ in texts]
    monkeypatch.setattr(doc_pipeline, "embed_documents", embed_documents)
    await doc_pipeline.run_embed("f" * 64)
    assert (await _where_ones(db_conn))["fresh passage"] == 1
    cur = await db_conn.execute("SELECT state, embedding_model FROM documents WHERE doc_id = %s", ("f" * 64,))
    assert await cur.fetchone() == ("indexed", GEMMA.model)


async def test_an_env_model_change_starts_the_migration_and_a_refusal_isnt_retried(db_conn, lib, monkeypatch):
    """Rulings R3 and P5: one tick of the loop notices a target that differs from current."""
    probes = []

    async def probe(provider, model, **_kw):
        probes.append(model)
        if model == "mxbai-embed-large":
            return ProbeResult(False, 1024, None, "mxbai-embed-large gives 1024 numbers")
        return ProbeResult(True, 768, None, "fine")
    monkeypatch.setattr(mig, "probe", probe)
    assert await mig.tick("w") == "idle"                                      # nothing to do
    monkeypatch.setenv("EMBEDDING_MODEL", "mxbai-embed-large")
    assert await mig.tick("w") == "refused"
    assert "1024" in (await embedding_state.read_state(db_conn))["error"]
    assert await mig.tick("w") == "idle" and probes == ["mxbai-embed-large"]  # not probed on every tick
    monkeypatch.setenv("EMBEDDING_MODEL", GEMMA.model)
    assert await mig.tick("w") == "started"
    assert (await embedding_state.read_state(db_conn))["error"] is None


async def test_a_model_that_wasnt_reachable_is_tried_again_later_but_a_wrong_size_isnt(db_conn, lib, monkeypatch):
    """Final review: a refusal was recorded for good, so an .env model changed before it was pulled ("isn't on
    ollama") never started its re-embed, even after a restart. Only a wrong size is final."""
    answers = {"embeddinggemma-2:740m": ProbeResult(False, None, None, "embeddinggemma-2:740m isn't on ollama (HTTP 404)")}

    async def probe(provider, model, **_kw):
        return answers.get(model) or ProbeResult(True, 768, None, "fine")
    monkeypatch.setattr(mig, "probe", probe)
    monkeypatch.setenv("EMBEDDING_MODEL", GEMMA.model)
    assert await mig.tick("w") == "refused"
    assert await mig.tick("w") == "idle"                          # not probed every 5 s
    answers.clear()                                               # the owner pulled it
    state = await embedding_state.read_state(db_conn)
    await embedding_state.write_state(db_conn, {**state, "error_retry_at": "2000-01-01T00:00:00+00:00"})
    assert await mig.tick("w") == "started"                       # tried again once its wait is over

    await mig.cancel(db_conn)   # it pins the console's choice to current, which outranks .env: clear it
    await console_settings.save(db_conn, embedding_state.SECTION, {"provider": None, "model": None},
                                updated_by=SEED_ADMIN, env=ENV)
    monkeypatch.setenv("EMBEDDING_MODEL", "mxbai-embed-large")
    answers["mxbai-embed-large"] = ProbeResult(False, 1024, None, "mxbai-embed-large gives 1024 numbers")
    assert await mig.tick("w") == "refused"
    state = await embedding_state.read_state(db_conn)
    assert state.get("error_retry_at") is None                    # a wrong size doesn't change with time
    assert await mig.tick("w") == "idle"


async def test_a_rebuild_takes_the_state_row_before_its_document_row(db_conn, monkeypatch, tmp_path):
    """Final review: run_rebuild locked the document row, then the state row; the switch takes the state row,
    then (in its UPDATE documents) the document rows. Opposite orders deadlock. The state row comes first."""
    from contextlib import asynccontextmanager
    from server.tests.docs_harness import build_docs_app
    order = []

    class Recording:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def execute(self, sql, *args, **kwargs):
            if "FROM documents WHERE doc_id = %s FOR UPDATE" in sql:
                order.append("document")
            return await self._conn.execute(sql, *args, **kwargs)

    class Shim:
        @asynccontextmanager
        async def connection(self, timeout=None):
            yield Recording(db_conn)

    build_docs_app(db_conn, monkeypatch, storage_dir=tmp_path)
    monkeypatch.setattr(doc_pipeline, "get_pool", lambda: Shim())
    real = embedding_state.locked_current

    async def recording_locked_current(conn, *a, **kw):
        order.append("state")
        return await real(conn, *a, **kw)
    monkeypatch.setattr(embedding_state, "locked_current", recording_locked_current)
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    await db_conn.execute("DELETE FROM app_settings WHERE key IN ('embedding_state', 'embeddings')")
    await embedding_state.init(db_conn, ENV)
    await seed.seed_doc(db_conn, DOC, None, file_type="text", state="indexed", extracted_by="client")
    await db_conn.execute("UPDATE documents SET embedding_profile = 'older', embedding_model = 'nomic-embed-text' "
                          "WHERE doc_id = %s", (DOC,))
    await db_conn.execute("INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash) "
                          "VALUES (%s, 0, 1, 'page', 'Some passage of text.', 'h0')", (DOC,))
    assert await doc_pipeline.run_rebuild(DOC) is None
    assert order[:2] == ["state", "document"]
