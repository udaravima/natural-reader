"""What the vectors were made with (current, a database fact) against what the admin chose (the target, a
setting), phase 2b Task 3. Rulings R1 (identity = model + prefixes; the provider is only where calls go) and
R6 (env prefixes apply only to the env's own model)."""
import json
from contextlib import asynccontextmanager

import httpx
import respx

from server.services import console_settings, embedding_state, embeddings

SEED_ADMIN = "00000000-0000-0000-0000-000000000001"
ENV = {"OLLAMA_URL": "http://ollama.test", "EMBEDDING_MODEL": "nomic-embed-text"}
TWO = {**ENV, "INFERENCE_PROVIDERS": "local,other",
       "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
       "INFERENCE_OTHER_KIND": "ollama", "INFERENCE_OTHER_URL": "http://other.test"}


async def _choose(conn, env, **changes):
    await console_settings.save(conn, embedding_state.SECTION, changes, updated_by=SEED_ADMIN, env=env)


async def test_a_fresh_install_starts_with_the_target_as_current(db_conn):
    await db_conn.execute("DELETE FROM app_settings WHERE key IN ('embedding_state', 'embeddings')")
    await embedding_state.init(db_conn, ENV)
    cur = await embedding_state.current(db_conn, ENV)
    assert (cur.model, cur.provider.url, cur.dimensions) == ("nomic-embed-text", "http://ollama.test", None)
    assert (cur.document_prefix, cur.query_prefix) == ("search_document: ", "search_query: ")
    state = await embedding_state.read_state(db_conn)
    assert state["next"] is None and state["filled"] == 0 and state["error"] is None
    # A later start with another env model keeps what the vectors were made with.
    await embedding_state.init(db_conn, {**ENV, "EMBEDDING_MODEL": "embeddinggemma-2:740m"})
    assert (await embedding_state.current(db_conn, ENV)).model == "nomic-embed-text"


async def test_the_same_model_on_another_provider_needs_no_migration(db_conn):
    await db_conn.execute("DELETE FROM app_settings WHERE key IN ('embedding_state', 'embeddings')")
    await embedding_state.init(db_conn, TWO)
    assert (await embedding_state.current(db_conn, TWO)).provider.name == "local"
    await _choose(db_conn, TWO, provider="other", model="nomic-embed-text:latest")
    target = await embedding_state.load_target(db_conn, TWO)
    assert target.provider.url == "http://other.test"
    assert embedding_state.vector_identity(target) == embedding_state.vector_identity(
        await embedding_state.current(db_conn, TWO))
    assert not await embedding_state.needs_migration(db_conn, TWO)


async def test_another_model_or_prefix_needs_one(db_conn):
    await db_conn.execute("DELETE FROM app_settings WHERE key IN ('embedding_state', 'embeddings')")
    await embedding_state.init(db_conn, ENV)
    await _choose(db_conn, ENV, model="embeddinggemma-2:740m")
    assert await embedding_state.needs_migration(db_conn, ENV)
    await _choose(db_conn, ENV, model=None)   # back to the env's model
    assert not await embedding_state.needs_migration(db_conn, ENV)
    assert await embedding_state.needs_migration(db_conn, {**ENV, "EMBEDDING_QUERY_PREFIX": "q: "})


async def test_env_prefixes_apply_only_to_the_env_model(db_conn):
    env = {**ENV, "EMBEDDING_DOCUMENT_PREFIX": "d: ", "EMBEDDING_QUERY_PREFIX": "q: "}
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'embeddings'")
    own = await embedding_state.load_target(db_conn, env)
    assert (own.model, own.document_prefix, own.query_prefix) == ("nomic-embed-text", "d: ", "q: ")
    await _choose(db_conn, env, model="embeddinggemma-2:740m")
    other = await embedding_state.load_target(db_conn, env)
    assert (other.document_prefix, other.query_prefix) == ("title: none | text: ", "task: search result | query: ")


async def test_an_unknown_provider_name_falls_back_to_the_ollama_at_ollama_url(db_conn):
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'embeddings'")
    target = await embedding_state.load_target(db_conn, {**ENV, "EMBEDDING_PROVIDER": "gone"})
    assert (target.provider.kind, target.provider.url) == ("ollama", "http://ollama.test")


@respx.mock
async def test_queries_use_the_current_embedder_until_the_switch(db_conn, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    await db_conn.execute("DELETE FROM app_settings WHERE key IN ('embedding_state', 'embeddings')")
    await embedding_state.init(db_conn, ENV)
    await _choose(db_conn, ENV, model="embeddinggemma-2:740m")   # chosen, not yet built

    class Shim:
        @asynccontextmanager
        async def connection(self):
            yield db_conn
    monkeypatch.setattr(embeddings, "get_pool", lambda: Shim())
    route = respx.post("http://ollama.test/api/embed").mock(
        side_effect=lambda r: httpx.Response(200, json={"embeddings": [[0.0] * 768]}))
    await embeddings.start_client()
    try:
        await embeddings.embed_query("what is it?")
    finally:
        await embeddings.stop_client()
    body = json.loads(route.calls.last.request.content)
    assert body == {"model": "nomic-embed-text", "input": ["search_query: what is it?"]}


def test_the_profile_of_an_embedder_matches_the_old_format(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    e = embeddings.env_embedder()
    assert embeddings.profile_of(e) == embeddings.current_profile()   # existing documents keep their profile
