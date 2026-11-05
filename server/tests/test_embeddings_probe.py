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


def _nothing_loaded(*loaded):
    """Ollama's /api/ps: the models in memory now (none unless named)."""
    return respx.get("http://ollama.test/api/ps").mock(
        return_value=httpx.Response(200, json={"models": [{"name": n, "model": n} for n in loaded]}))


def _asked(route):
    """The probe's own requests, not the unload that follows them (an empty input)."""
    return [json.loads(c.request.content) for c in route.calls if json.loads(c.request.content)["input"]]


def _sized(native):
    """An Ollama that gives `native` numbers, or as many as `dimensions` asks for (it truncates anything)."""
    def answer(request):
        body = json.loads(request.content)
        n = body.get("dimensions") or native
        return httpx.Response(200, json={"embeddings": [[0.1] * n for _ in body["input"]]})
    return answer


@respx.mock
async def test_a_native_768_model_is_admitted_without_dimensions():
    _nothing_loaded()
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(768))
    got = await probe(LOCAL, "nomic-embed-text")
    assert (got.ok, got.native, got.dimensions) == (True, 768, None)
    assert len(_asked(route)) == 1 and "dimensions" not in _asked(route)[0]


@respx.mock
async def test_a_known_shortenable_family_is_admitted_with_768():
    _nothing_loaded()
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(1024))
    got = await probe(LOCAL, "qwen3-embedding:0.6b")
    assert (got.ok, got.native, got.dimensions) == (True, 1024, 768)
    assert [body.get("dimensions") for body in _asked(route)] == [None, 768]
    assert "1024" in got.reason and "768" in got.reason


@respx.mock
async def test_a_1024_model_outside_the_families_is_refused_with_its_size():
    _nothing_loaded()
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(1024))
    got = await probe(LOCAL, "mxbai-embed-large")
    assert (got.ok, got.native, got.dimensions) == (False, 1024, None)
    assert "1024" in got.reason and "768" in got.reason and "shorten" in got.reason
    assert len(_asked(route)) == 1   # never asked for 768: Ollama would cut it and say nothing


@respx.mock
async def test_an_unreachable_or_refusing_provider_says_so():
    _nothing_loaded()
    respx.post("http://ollama.test/api/embed").mock(side_effect=httpx.ConnectError("refused"))
    got = await probe(LOCAL, "nomic-embed-text")
    assert not got.ok and got.native is None and "reach" in got.reason and "local" in got.reason
    respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(401, json={"error": "bad key sk-test-secret"}))
    got = await probe(CLOUD, "text-embedding-3-small")
    assert not got.ok and "401" in got.reason and "sk-test-secret" not in got.reason


@respx.mock
async def test_a_shortened_answer_of_the_wrong_length_is_refused():
    _nothing_loaded()

    def ignores_dimensions(request):   # a server that doesn't honour `dimensions`
        body = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.1] * 1024 for _ in body["input"]]})
    respx.post("http://ollama.test/api/embed").mock(side_effect=ignores_dimensions)
    got = await probe(LOCAL, "qwen3-embedding:0.6b")
    assert (got.ok, got.native, got.dimensions) == (False, 1024, None)
    assert "768" in got.reason and "1024" in got.reason


@respx.mock
async def test_a_model_smaller_than_768_is_refused():
    _nothing_loaded()
    respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(384))
    got = await probe(LOCAL, "all-minilm")
    assert (got.ok, got.native) == (False, 384) and "384" in got.reason


@respx.mock
async def test_a_missing_model_or_a_chat_model_is_named_plainly():
    _nothing_loaded()
    respx.post("http://ollama.test/api/embed").mock(return_value=httpx.Response(404, json={"error": "not found"}))
    assert "isn't on local" in (await probe(LOCAL, "no-such-model")).reason
    respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(501, json={"error": "this model does not support embeddings"}))
    assert "doesn't make embeddings" in (await probe(LOCAL, "gemma4:e4b")).reason


@respx.mock
async def test_a_model_the_probe_had_to_load_is_unloaded_after():
    """The owner, 2026-11-05: a check that loads a model and leaves it in memory for Ollama's five minutes is churn
    nobody asked for. The probe asks /api/ps first; a model that wasn't in memory is unloaded when it's done."""
    _nothing_loaded()
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(1024))
    got = await probe(LOCAL, "qwen3-embedding:0.6b")
    assert got.ok
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert len(bodies) == 3 and all("keep_alive" not in b for b in bodies[:2])     # loaded once for both asks
    assert bodies[2] == {"model": "qwen3-embedding:0.6b", "input": [], "keep_alive": 0}


@respx.mock
async def test_a_model_already_in_memory_is_left_there():
    _nothing_loaded("nomic-embed-text:latest")
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(768))
    assert (await probe(LOCAL, "nomic-embed-text")).ok
    assert route.call_count == 1


@respx.mock
async def test_a_probe_for_a_model_about_to_be_used_leaves_it_loaded():
    """Use this model probes again on the server and the re-embed starts seconds later: unloading in between
    would only load it twice."""
    ps = _nothing_loaded()
    route = respx.post("http://ollama.test/api/embed").mock(side_effect=_sized(768))
    assert (await probe(LOCAL, "nomic-embed-text", unload=False)).ok
    assert route.call_count == 1 and ps.call_count == 0


@respx.mock
async def test_a_model_that_never_answered_isnt_unloaded():
    _nothing_loaded()
    route = respx.post("http://ollama.test/api/embed").mock(return_value=httpx.Response(404, json={"error": "nope"}))
    assert not (await probe(LOCAL, "no-such-model")).ok
    assert route.call_count == 1


@respx.mock
async def test_an_openai_compatible_provider_has_nothing_to_unload():
    route = respx.post("https://api.example.com/v1/embeddings").mock(return_value=httpx.Response(
        200, json={"data": [{"index": 0, "embedding": [0.1] * 768}]}))
    assert (await probe(CLOUD, "text-embedding-3-small")).ok
    assert route.call_count == 1
