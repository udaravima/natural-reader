"""The embedding client over providers (phase 2b Task 1): one batched request per <=32 texts, to Ollama's
/api/embed or an OpenAI-compatible /embeddings, checked for count and length."""
import json

import httpx
import pytest
import respx

from server.llm.providers.base import ProviderConfig
from server.services import embedding_models, embeddings
from server.services.embeddings import Embedder, EmbeddingError, embed_texts

DIM = embeddings.EMBEDDING_DIM
LOCAL = ProviderConfig(name="local", kind="ollama", url="http://ollama.test")
CLOUD = ProviderConfig(name="cloud", kind="openai", url="https://api.example.com/v1", api_key="sk-test-secret")


def _vec(i=0, n=DIM):
    return [float(i)] + [0.0] * (n - 1)


@pytest.fixture
async def client():
    await embeddings.start_client()
    yield
    await embeddings.stop_client()


def _ollama_answer(request):
    texts = json.loads(request.content)["input"]
    return httpx.Response(200, json={"model": "m", "embeddings": [_vec(i) for i, _ in enumerate(texts)]})


@respx.mock
async def test_ollama_kind_posts_a_batch_to_api_embed(client):
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_ollama_answer)
    out = await embed_texts(Embedder(LOCAL, "nomic-embed-text", None, "d: ", "q: "), ["one", "two"])
    assert [v[0] for v in out] == [0.0, 1.0]
    body = json.loads(route.calls.last.request.content)
    assert body == {"model": "nomic-embed-text", "input": ["one", "two"]}   # no dimensions, no prefix added here


@respx.mock
async def test_openai_kind_posts_to_embeddings_with_the_key(client):
    def answer(request):
        n = len(json.loads(request.content)["input"])
        data = [{"object": "embedding", "index": i, "embedding": _vec(i)} for i in range(n)]
        return httpx.Response(200, json={"object": "list", "data": list(reversed(data))})
    route = respx.post("https://api.example.com/v1/embeddings").mock(side_effect=answer)
    out = await embed_texts(Embedder(CLOUD, "text-embedding-3-small", 768, "", ""), ["a", "b", "c"])
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]   # put back in `index` order
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer sk-test-secret"
    assert json.loads(req.content) == {"model": "text-embedding-3-small", "input": ["a", "b", "c"], "dimensions": 768}


@respx.mock
async def test_forty_texts_go_in_two_calls(client):
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_ollama_answer)
    out = await embed_texts(Embedder(LOCAL, "nomic-embed-text", None, "", ""), [f"t{i}" for i in range(40)])
    assert len(out) == 40
    sizes = sorted(len(json.loads(c.request.content)["input"]) for c in route.calls)
    assert sizes == [8, 32]


@respx.mock
async def test_a_wrong_length_or_count_is_an_error_naming_the_model(client):
    e = Embedder(LOCAL, "odd-embed", None, "", "")
    respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [_vec(0, 767)]}))
    with pytest.raises(EmbeddingError, match=r"odd-embed.*767"):
        await embed_texts(e, ["one"])
    respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [_vec(0)]}))
    with pytest.raises(EmbeddingError, match=r"odd-embed.*1.*2"):
        await embed_texts(e, ["one", "two"])


@respx.mock
async def test_dimensions_is_sent_only_when_set(client):
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_ollama_answer)
    await embed_texts(Embedder(LOCAL, "qwen3-embedding:0.6b", 768, "", ""), ["x"])
    assert json.loads(route.calls.last.request.content)["dimensions"] == 768
    await embed_texts(Embedder(LOCAL, "nomic-embed-text", None, "", ""), ["x"])
    assert "dimensions" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_a_refusal_is_an_error_without_the_key(client):
    respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key sk-test-secret"}}))
    with pytest.raises(EmbeddingError) as err:
        await embed_texts(Embedder(CLOUD, "text-embedding-3-small", 768, "", ""), ["x"])
    assert "401" in str(err.value) and "cloud" in str(err.value)
    assert "sk-test-secret" not in str(err.value)


@respx.mock
async def test_a_failed_request_marks_only_its_texts_none(client):
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if json.loads(request.content)["input"][0] == "search_document: t32":   # the second batch
            return httpx.Response(500)
        return _ollama_answer(request)
    respx.post("http://ollama.test/api/embed").mock(side_effect=flaky)
    e = Embedder(LOCAL, "nomic-embed-text", None, "search_document: ", "search_query: ")
    out = await embeddings.embed_documents([f"t{i}" for i in range(40)], e)
    assert [v is None for v in out] == [False] * 32 + [True] * 8
    assert calls["n"] == 2


@respx.mock
async def test_queries_and_documents_carry_their_prefixes(client):
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_ollama_answer)
    e = Embedder(LOCAL, "nomic-embed-text", None, "search_document: ", "search_query: ")
    await embeddings.embed_query("what is it?", e)
    await embeddings.embed_documents(["Page text."], e)
    assert [json.loads(c.request.content)["input"] for c in route.calls] == [
        ["search_query: what is it?"], ["search_document: Page text."]]


def test_known_families_and_their_prefixes():
    nomic = embedding_models.family("nomic-embed-text:latest")
    assert (nomic.prefix_document, nomic.prefix_query) == ("search_document: ", "search_query: ")
    gemma = embedding_models.family("embeddinggemma-2:740m")
    assert (gemma.prefix_document, gemma.prefix_query) == ("title: none | text: ", "task: search result | query: ")
    qwen = embedding_models.family("Qwen/Qwen3-Embedding-0.6B")
    assert qwen.shortenable and qwen.prefix_document == ""
    assert qwen.prefix_query.startswith("Instruct: Given a web search query")
    openai = embedding_models.family("openai/text-embedding-3-small")
    assert openai.shortenable and (openai.prefix_document, openai.prefix_query) == ("", "")
    assert embedding_models.family("hf.co/someone/EmbeddingGemma-2-GGUF:Q8_0") is gemma
    assert embedding_models.family("mxbai-embed-large") is None
    assert all(f.source.startswith("https://") for f in embedding_models.KNOWN.values())
