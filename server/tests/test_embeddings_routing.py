import httpx
import respx
from server.services import embeddings


@respx.mock
async def test_embed_one_uses_model_router(monkeypatch):
    # model_router re-reads env on every call — setenv steers both the
    # upstream URL and the model name.
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("EMBEDDING_MODEL", "my-embed")
    route = respx.post("http://ollama.test/api/embeddings").mock(
        return_value=httpx.Response(200, json={"embedding": [0.0] * 768})
    )
    await embeddings.start_client()
    try:
        await embeddings.embed_one("some text")
    finally:
        await embeddings.stop_client()
    body = route.calls.last.request.content.decode()
    assert '"model": "my-embed"' in body or '"model":"my-embed"' in body


@respx.mock
async def test_embed_one_env_change_takes_effect_without_reload(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    route = respx.post("http://ollama.test/api/embeddings").mock(
        return_value=httpx.Response(200, json={"embedding": [0.0] * 768})
    )
    await embeddings.start_client()
    try:
        monkeypatch.setenv("EMBEDDING_MODEL", "first-model")
        await embeddings.embed_one("text one")
        monkeypatch.setenv("EMBEDDING_MODEL", "second-model")
        await embeddings.embed_one("text two")
    finally:
        await embeddings.stop_client()
    bodies = [call.request.content.decode() for call in route.calls]
    assert "first-model" in bodies[0]
    assert "second-model" in bodies[1]


# ---- v2.3 Task E: the embedding model's own prefixes, and the profile ----

import json  # noqa: E402

import pytest  # noqa: E402

from server.services import model_router  # noqa: E402


@pytest.mark.parametrize("env,doc,query", [
    ({"EMBEDDING_MODEL": "nomic-embed-text"}, "search_document: ", "search_query: "),
    ({"EMBEDDING_MODEL": "nomic-embed-text:v1.5"}, "search_document: ", "search_query: "),
    ({"EMBEDDING_MODEL": "mxbai-embed-large"}, "", ""),
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
    route = respx.post("http://ollama.test/api/embeddings").mock(
        return_value=httpx.Response(200, json={"embedding": [0.0] * 768}))
    await embeddings.start_client()
    try:
        await embeddings.embed_query("what is it?")
        await embeddings.embed_documents(["Page text."])
    finally:
        await embeddings.stop_client()
    prompts = [json.loads(c.request.content)["prompt"] for c in route.calls]
    assert prompts == ["search_query: what is it?", "search_document: Page text."]


def test_the_profile_names_the_model_the_prefixes_and_the_chunker(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    base = embeddings.current_profile()
    assert base.startswith("nomic-embed-text|") and "split1:" in base
    monkeypatch.setenv("EMBEDDING_QUERY_PREFIX", "")
    assert embeddings.current_profile() != base
    monkeypatch.setenv("EMBEDDING_MODEL", "other")
    assert embeddings.profile_model(embeddings.current_profile()) == "other"
    assert embeddings.profile_model(None) is None
