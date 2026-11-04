"""The probe (phase 2b Task 2, spec §4.2.4): a model is admitted when it gives 768 numbers by itself, or
when it belongs to a family trained to be shortened and gives 768 when asked. Never trust a returned
length alone: Ollama cuts any vector to the `dimensions` asked for, with no error."""
import json

import httpx
import respx

from server.llm.providers.base import ProviderConfig
from server.services.embeddings import probe

LOCAL = ProviderConfig(name="local", kind="ollama", url="http://ollama.test")
CLOUD = ProviderConfig(name="cloud", kind="openai", url="https://api.example.com/v1", api_key="sk-test-secret")


def _sized(native):
    """An Ollama that gives `native` numbers, or as many as `dimensions` asks for (it truncates anything)."""
    def answer(request):
        body = json.loads(request.content)
        n = body.get("dimensions") or native
        return httpx.Response(200, json={"embeddings": [[0.1] * n for _ in body["input"]]})
    return answer


@respx.mock
async def test_a_native_768_model_is_admitted_without_dimensions():
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(768))
    got = await probe(LOCAL, "nomic-embed-text")
    assert (got.ok, got.native, got.dimensions) == (True, 768, None)
    assert route.call_count == 1 and "dimensions" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_a_known_shortenable_family_is_admitted_with_768():
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(1024))
    got = await probe(LOCAL, "qwen3-embedding:0.6b")
    assert (got.ok, got.native, got.dimensions) == (True, 1024, 768)
    assert [json.loads(c.request.content).get("dimensions") for c in route.calls] == [None, 768]
    assert "1024" in got.reason and "768" in got.reason


@respx.mock
async def test_a_1024_model_outside_the_families_is_refused_with_its_size():
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(1024))
    got = await probe(LOCAL, "mxbai-embed-large")
    assert (got.ok, got.native, got.dimensions) == (False, 1024, None)
    assert "1024" in got.reason and "768" in got.reason and "shorten" in got.reason
    assert route.call_count == 1   # never asked for 768: Ollama would cut it and say nothing


@respx.mock
async def test_an_unreachable_or_refusing_provider_says_so():
    respx.post("http://ollama.test/api/embed").mock(side_effect=httpx.ConnectError("refused"))
    got = await probe(LOCAL, "nomic-embed-text")
    assert not got.ok and got.native is None and "reach" in got.reason and "local" in got.reason
    respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(401, json={"error": "bad key sk-test-secret"}))
    got = await probe(CLOUD, "text-embedding-3-small")
    assert not got.ok and "401" in got.reason and "sk-test-secret" not in got.reason


@respx.mock
async def test_a_shortened_answer_of_the_wrong_length_is_refused():
    def ignores_dimensions(request):   # a server that doesn't honour `dimensions`
        body = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.1] * 1024 for _ in body["input"]]})
    respx.post("http://ollama.test/api/embed").mock(side_effect=ignores_dimensions)
    got = await probe(LOCAL, "qwen3-embedding:0.6b")
    assert (got.ok, got.native, got.dimensions) == (False, 1024, None)
    assert "768" in got.reason and "1024" in got.reason


@respx.mock
async def test_a_model_smaller_than_768_is_refused():
    respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(384))
    got = await probe(LOCAL, "all-minilm")
    assert (got.ok, got.native) == (False, 384) and "384" in got.reason


@respx.mock
async def test_a_missing_model_or_a_chat_model_is_named_plainly():
    respx.post("http://ollama.test/api/embed").mock(return_value=httpx.Response(404, json={"error": "not found"}))
    assert "isn't on local" in (await probe(LOCAL, "no-such-model")).reason
    respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(501, json={"error": "this model does not support embeddings"}))
    assert "doesn't make embeddings" in (await probe(LOCAL, "gemma4:e4b")).reason
