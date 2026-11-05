import json

import httpx
import respx
from server.services import embeddings


def _answer(request):
    n = len(json.loads(request.content)["input"])
    return httpx.Response(200, json={"embeddings": [[0.0] * 768 for _ in range(n)]})


@respx.mock
async def test_embedding_follows_the_env_model_and_url(monkeypatch):
    # model_router re-reads env on every call — setenv steers both the
    # upstream URL and the model name.
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("EMBEDDING_MODEL", "my-embed")
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_answer)
    await embeddings.start_client()
    try:
        await embeddings.embed_query("some text")
    finally:
        await embeddings.stop_client()
    assert json.loads(route.calls.last.request.content)["model"] == "my-embed"


@respx.mock
async def test_an_env_change_takes_effect_without_reload(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_answer)
    await embeddings.start_client()
    try:
        monkeypatch.setenv("EMBEDDING_MODEL", "first-model")
        await embeddings.embed_query("text one")
        monkeypatch.setenv("EMBEDDING_MODEL", "second-model")
        await embeddings.embed_query("text two")
    finally:
        await embeddings.stop_client()
    models = [json.loads(call.request.content)["model"] for call in route.calls]
    assert models == ["first-model", "second-model"]


# ---- v2.3 Task E: the embedding model's own prefixes, and the profile ----

import pytest  # noqa: E402

from server.services import model_router  # noqa: E402


@pytest.mark.parametrize("env,doc,query", [
    ({"EMBEDDING_MODEL": "nomic-embed-text"}, "search_document: ", "search_query: "),
    ({"EMBEDDING_MODEL": "nomic-embed-text:v1.5"}, "search_document: ", "search_query: "),
    ({"EMBEDDING_MODEL": "mxbai-embed-large"}, "", ""),
    ({"EMBEDDING_MODEL": "embeddinggemma-2:740m"}, "title: none | text: ", "task: search result | query: "),
    ({"EMBEDDING_MODEL": "e5", "EMBEDDING_DOCUMENT_PREFIX": "passage: ", "EMBEDDING_QUERY_PREFIX": "query: "},
     "passage: ", "query: "),
    # Set but empty turns nomic's prefixes off.
    ({"EMBEDDING_MODEL": "nomic-embed-text", "EMBEDDING_DOCUMENT_PREFIX": "", "EMBEDDING_QUERY_PREFIX": ""}, "", ""),
])
def test_prefixes_default_per_model_and_can_be_set(env, doc, query):
    cfg = model_router.load_inference_config(env)
    assert (cfg.embed_document_prefix, cfg.embed_query_prefix) == (doc, query)


@respx.mock
async def test_queries_and_documents_are_embedded_with_their_prefixes(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_answer)
    await embeddings.start_client()
    try:
        await embeddings.embed_query("what is it?")
        await embeddings.embed_documents(["Page text."])
    finally:
        await embeddings.stop_client()
    prompts = [json.loads(c.request.content)["input"] for c in route.calls]
    assert prompts == [["search_query: what is it?"], ["search_document: Page text."]]


def test_the_profile_names_the_model_the_prefixes_and_the_chunker(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    base = embeddings.current_profile()
    assert base.startswith("nomic-embed-text|") and "split1:" in base
    monkeypatch.setenv("EMBEDDING_QUERY_PREFIX", "")
    assert embeddings.current_profile() != base
    monkeypatch.setenv("EMBEDDING_MODEL", "other")
    assert embeddings.profile_model(embeddings.current_profile()) == "other"
    assert embeddings.profile_model(None) is None


async def test_a_database_hiccup_keeps_the_model_last_read_not_the_environments(monkeypatch):
    """Final review minor, fixed for the owner (2026-11-05): with the database briefly unreadable, a query was
    embedded with the environment's model. After an admin has switched models that compares with nothing, and
    search quietly finds the wrong passages. The model last read from the database is the better guess."""
    from contextlib import asynccontextmanager

    import psycopg
    from server.llm.providers.base import ProviderConfig
    from server.services import embedding_state
    gemma = embeddings.Embedder(ProviderConfig(name="ollama", kind="ollama", url="http://ollama.test"),
                                "embeddinggemma-2:740m", None, "", "")

    class Pool:
        down = False

        @asynccontextmanager
        async def connection(self, timeout=None):
            if self.down:
                raise psycopg.OperationalError("the database is restarting")
            yield object()
    pool = Pool()
    monkeypatch.setattr(embeddings, "get_pool", lambda: pool)

    async def current(conn, env=None):
        return gemma
    monkeypatch.setattr(embedding_state, "current", current)
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    assert await embeddings.current_embedder() == gemma
    pool.down = True
    assert await embeddings.current_embedder() == gemma
