import asyncio
import logging
import time

import httpx
import pytest

from server.llm import router as llm_router
from server.llm.router import Received, Router, UnknownModel, load_provider_configs
from server.llm.types import Capabilities, CallSettings, Message, ProviderUnavailable, Usage
from server.tests.llm_fakes import FakeUpstream, ndjson

DONE = {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 1, "prompt_eval_count": 1}


def test_no_config_is_one_ollama_provider_from_today_s_env():
    cfgs = load_provider_configs({"OLLAMA_URL": "http://gpu.example.com:11434/", "INFERENCE_MODELS": "a, b"})
    assert [(c.name, c.kind, c.url, c.models) for c in cfgs] == [
        ("ollama", "ollama", "http://gpu.example.com:11434", ("a", "b"))]


def test_two_providers_in_order_with_keys_and_allow_lists():
    env = {"INFERENCE_PROVIDERS": "local, open-router",
           "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.example.com:11434",
           "INFERENCE_OPEN_ROUTER_KIND": "openai",
           "INFERENCE_OPEN_ROUTER_URL": "https://openrouter.example.com/api/v1/",
           "INFERENCE_OPEN_ROUTER_API_KEY": "sk-1", "INFERENCE_OPEN_ROUTER_MODELS": "x/y"}
    cfgs = load_provider_configs(env)
    assert [(c.name, c.kind, c.url, c.api_key, c.models) for c in cfgs] == [
        ("local", "ollama", "http://ollama.example.com:11434", "", None),
        ("open-router", "openai", "https://openrouter.example.com/api/v1", "sk-1", ("x/y",))]


def test_invalid_providers_are_skipped_with_a_warning(caplog):
    env = {"INFERENCE_PROVIDERS": "good,badkind,nourl,Bad Name,good",
           "INFERENCE_GOOD_KIND": "ollama", "INFERENCE_GOOD_URL": "http://a.example.com",
           "INFERENCE_BADKIND_KIND": "anthropic", "INFERENCE_BADKIND_URL": "http://b.example.com",
           "INFERENCE_NOURL_KIND": "openai"}
    with caplog.at_level(logging.WARNING):
        cfgs = load_provider_configs(env)
    assert [c.name for c in cfgs] == ["good"]
    assert "badkind" in caplog.text and "nourl" in caplog.text


def test_a_bad_provider_is_warned_about_once_not_on_every_read(caplog):
    """Final review minor, fixed for the owner (2026-11-05): every query reads the embedder, which parses
    INFERENCE_PROVIDERS, so one misconfigured provider logged its warning once per query."""
    env = {"INFERENCE_PROVIDERS": "good,badkind", "INFERENCE_GOOD_KIND": "ollama",
           "INFERENCE_GOOD_URL": "http://a.example.com", "INFERENCE_BADKIND_KIND": "anthropic",
           "INFERENCE_BADKIND_URL": "http://b.example.com"}
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert [c.name for c in load_provider_configs(env)] == ["good"]
    assert caplog.text.count("badkind") == 1


def _router():
    configs = load_provider_configs({
        "INFERENCE_PROVIDERS": "local,cloud",
        "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
        "INFERENCE_CLOUD_KIND": "openai", "INFERENCE_CLOUD_URL": "http://cloud.test/v1",
        "INFERENCE_CLOUD_MODELS": "vendor/a"})
    up = (FakeUpstream()
          .on("GET", "/api/tags", lambda: httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]}))
          .on("POST", "/api/show", lambda: httpx.Response(200, json={"capabilities": ["tools"]}))
          .on("POST", "/api/chat", lambda: ndjson({"message": {"content": "Hi "}},
                                                  {"message": {"content": "there"}}, DONE))
          .on("GET", "/v1/models", lambda: httpx.Response(200, json={"data": [{"id": "vendor/a"}, {"id": "vendor/b"}]})))
    return Router(configs, up.client()), up


def test_resolve_splits_at_the_first_colon():
    r, _ = _router()
    p, name = r.resolve("local:qwen2.5:7b")
    assert (p.config.name, name) == ("local", "qwen2.5:7b")
    p, name = r.resolve("cloud:vendor/a")
    assert (p.config.name, name) == ("cloud", "vendor/a")


def test_bare_pre_c1_names_go_to_the_first_ollama_provider():
    r, _ = _router()
    p, name = r.resolve("qwen2.5:7b")
    assert (p.config.name, name) == ("local", "qwen2.5:7b")
    assert r.canonical_id("qwen2.5:7b") == "local:qwen2.5:7b"


def test_no_ollama_provider_means_bare_names_are_unknown():
    cfgs = load_provider_configs({"INFERENCE_PROVIDERS": "cloud", "INFERENCE_CLOUD_KIND": "openai",
                                  "INFERENCE_CLOUD_URL": "http://cloud.test/v1"})
    with pytest.raises(UnknownModel):
        Router(cfgs, httpx.AsyncClient()).resolve("qwen2.5")


def test_allow_lists():
    r, _ = _router()
    assert r.is_allowed("local:anything")          # no allow-list: everything (dev, today's rule)
    assert r.is_allowed("cloud:vendor/a")
    assert not r.is_allowed("cloud:vendor/b")


async def test_is_listed_true_false_and_cached():
    r, up = _router()
    assert await r.is_listed("local:qwen2.5:7b") is True
    assert await r.is_listed("local:missing") is False
    before = len([req for req in up.requests if req.url.path == "/api/tags"])
    await r.is_listed("local:qwen2.5:7b")
    after = len([req for req in up.requests if req.url.path == "/api/tags"])
    assert after == before   # cached: no extra call to the provider per lookup


async def test_is_listed_fails_open_when_the_provider_is_unreachable():
    configs = load_provider_configs({"OLLAMA_URL": "http://ollama.test"})
    up = FakeUpstream().on("GET", "/api/tags", httpx.ConnectError("down"))
    r = Router(configs, up.client())
    assert await r.is_listed("ollama:anything") is True


async def test_list_models_filters_and_tags_ids_and_capabilities():
    r, _ = _router()
    models, failed = await r.list_models()
    assert failed == []
    assert [m.to_json()["id"] for m in models] == ["local:qwen2.5:7b", "cloud:vendor/a"]
    assert models[0].to_json()["kind"] == "ollama"
    assert models[0].capabilities.tools is True


async def test_one_failing_provider_is_omitted_and_reported():
    configs = load_provider_configs({
        "INFERENCE_PROVIDERS": "local,cloud",
        "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
        "INFERENCE_CLOUD_KIND": "openai", "INFERENCE_CLOUD_URL": "http://cloud.test/v1"})
    up = (FakeUpstream()
          .on("GET", "/api/tags", lambda: httpx.Response(200, json={"models": [{"name": "m"}]}))
          .on("POST", "/api/show", lambda: httpx.Response(200, json={}))
          .on("GET", "/v1/models", httpx.ConnectError("down")))
    models, failed = await Router(configs, up.client()).list_models()
    assert [m.id for m in models] == ["local:m"] and failed == ["cloud"]


async def test_a_provider_whose_capabilities_are_not_json_still_lists_with_unknown_capabilities():
    configs = load_provider_configs({
        "INFERENCE_PROVIDERS": "broken,working",
        "INFERENCE_BROKEN_KIND": "ollama", "INFERENCE_BROKEN_URL": "http://broken.test",
        "INFERENCE_WORKING_KIND": "ollama", "INFERENCE_WORKING_URL": "http://working.test"})
    def handle(request: httpx.Request) -> httpx.Response:
        # Answer by host: the two providers list concurrently, so arrival order is not fixed.
        broken = request.url.host == "broken.test"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m1" if broken else "m2"}]})
        if broken:
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, json={"capabilities": ["tools"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    models, failed = await Router(configs, client).list_models()
    # Final review M3: an HTML/garbage 200 from /api/show is "unknown", not a
    # failed provider (and never a ValueError escaping the adapter).
    assert sorted(m.id for m in models) == ["broken:m1", "working:m2"] and failed == []
    broken = next(m for m in models if m.id == "broken:m1")
    assert broken.capabilities == Capabilities()


async def test_a_stalled_provider_times_out_and_the_others_still_list(monkeypatch, caplog):
    """Final review I2: a provider that accepts the connection and then goes
    quiet must not hold the whole listing (the SPA gives up at 8 s and
    disables Send for every provider). It is reported failed after
    LIST_TIMEOUT_S; the working provider still lists."""
    monkeypatch.setattr(llm_router, "LIST_TIMEOUT_S", 0.2)
    configs = load_provider_configs({
        "INFERENCE_PROVIDERS": "stalled,working",
        "INFERENCE_STALLED_KIND": "openai", "INFERENCE_STALLED_URL": "http://stalled.test/v1",
        "INFERENCE_WORKING_KIND": "ollama", "INFERENCE_WORKING_URL": "http://working.test"})

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "stalled.test":
            await asyncio.sleep(30)   # connected, then silent (well under httpx's 300 s read timeout)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m"}]})
        return httpx.Response(200, json={"capabilities": ["tools"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="server.llm.router"):
        models, failed = await Router(configs, client).list_models()
    assert time.monotonic() - started < 5
    assert [m.id for m in models] == ["working:m"] and failed == ["stalled"]
    assert "stalled" in caplog.text


async def test_complete_concatenates_text():
    r, up = _router()
    assert await r.complete("local:qwen2.5:7b", [Message("user", "hi")]) == "Hi there"
    assert "think" not in up.bodies("/api/chat")[0]   # /api/show says no thinking: field omitted


async def test_complete_fills_what_it_received_and_returns_the_same_text():
    """Final review I1: the reader bills each call, so a caller can pass a Received that `complete` fills as the
    stream arrives (the text, and the provider's own usage). Without one, a caller gets what it always got."""
    r, _ = _router()
    got = Received()
    assert await r.complete("local:qwen2.5:7b", [Message("user", "hi")], received=got) == "Hi there"
    assert got.text == "Hi there" and got.usage == Usage(1, 1, detail={})


async def test_complete_that_breaks_off_still_shows_what_it_received():
    """A stream that breaks off raises as before, and the text that came first is in the Received: the part of a
    call that failed or timed out that was produced, which is what it is charged for."""
    configs = load_provider_configs({"INFERENCE_PROVIDERS": "local", "INFERENCE_LOCAL_KIND": "ollama",
                                     "INFERENCE_LOCAL_URL": "http://ollama.test"})
    up = (FakeUpstream()
          .on("POST", "/api/show", lambda: httpx.Response(200, json={"capabilities": []}))
          .on("POST", "/api/chat", lambda: ndjson({"message": {"content": "Half a"}})))   # no "done": cut off
    got = Received()
    with pytest.raises(ProviderUnavailable):
        await Router(configs, up.client()).complete("local:m", [Message("user", "hi")], received=got)
    assert got.text == "Half a" and got.usage is None


async def test_lifecycle_uses_env_and_transport(monkeypatch):
    monkeypatch.delenv("INFERENCE_PROVIDERS", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    up = FakeUpstream().on("GET", "/api/tags", lambda: httpx.Response(200, json={"models": []}))
    await llm_router.start_router(transport=httpx.MockTransport(up._handle))
    try:
        assert list(llm_router.get_router().providers) == ["ollama"]
    finally:
        await llm_router.stop_router()
    with pytest.raises(RuntimeError):
        llm_router.get_router()


def test_a_provider_prefix_with_no_model_is_refused_not_a_bare_name():
    """Deferred C1 minor: "local:" used to fall back to the Ollama provider
    with the model name "local:"; an empty model after a provider's prefix
    is no model at all."""
    r, _ = _router()
    for model_id in ("local:", "cloud:"):
        with pytest.raises(UnknownModel):
            r.resolve(model_id)
        assert not r.is_allowed(model_id)
    # A bare name that merely contains a colon is still a pre-C1 name.
    assert r.resolve("qwen2.5:7b")[1] == "qwen2.5:7b"


async def test_load_and_in_memory_reach_only_a_self_hosted_ollama():
    # Phone lines (R17.1, R17.8 I2): only a model in the memory of a server the deployer hosts is loaded or asked
    # about. A hosted API has nothing to load and a request there may cost money: Gemini, an OpenAI-compatible API,
    # an Ollama cloud model on the local server (`-cloud` or `:cloud`), and ollama.com itself get no request.
    up = (FakeUpstream()
          .on("POST", "/api/chat", lambda: httpx.Response(200, json={"done": True, "done_reason": "load"}))
          .on("GET", "/api/ps", lambda: httpx.Response(200, json={"models": [
              {"name": "m:latest", "context_length": 16384}]})))
    router = Router(load_provider_configs({
        "INFERENCE_PROVIDERS": "local,gem,or,com",
        "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
        "INFERENCE_GEM_KIND": "gemini", "INFERENCE_GEM_URL": "https://gemini.test", "INFERENCE_GEM_API_KEY": "k",
        "INFERENCE_OR_KIND": "openai", "INFERENCE_OR_URL": "https://openai.test/v1", "INFERENCE_OR_API_KEY": "k",
        "INFERENCE_COM_KIND": "ollama", "INFERENCE_COM_URL": "https://ollama.com", "INFERENCE_COM_API_KEY": "k"}),
        up.client())
    settings = CallSettings(think="off", keep_alive=900)
    assert router.holds_models("local:m") and await router.load("local:m", settings) is True
    assert await router.in_memory("local:m") == {"local:m:latest": 16384}
    for hosted in ("gem:gemini-x-flash", "or:x/y", "local:gpt-oss:120b-cloud", "local:kimi-k2:cloud",
                   "com:gpt-oss:120b"):
        assert router.holds_models(hosted) is False, hosted
        assert await router.load(hosted, settings) is False, hosted
        assert await router.in_memory(hosted) is None, hosted
    assert [(r.url.host, r.method, r.url.path) for r in up.requests] == [
        ("ollama.test", "POST", "/api/chat"), ("ollama.test", "GET", "/api/ps")]
    await router.aclose()


async def test_read_prompt_reaches_only_a_self_hosted_ollama():
    # R19.2: a phone line reads its prompt ahead of the first turn only where a server the deployer hosts can cache
    # it. A hosted API gets no request: it caches nothing for us, and a request there may cost money.
    up = (FakeUpstream()
          .on("POST", "/api/show", lambda: httpx.Response(200, json={"capabilities": ["completion", "tools"]}))
          .on("POST", "/api/chat", lambda: httpx.Response(200, json={"done": True, "done_reason": "length"})))
    router = Router(load_provider_configs({
        "INFERENCE_PROVIDERS": "local,gem,or,com",
        "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
        "INFERENCE_GEM_KIND": "gemini", "INFERENCE_GEM_URL": "https://gemini.test", "INFERENCE_GEM_API_KEY": "k",
        "INFERENCE_OR_KIND": "openai", "INFERENCE_OR_URL": "https://openai.test/v1", "INFERENCE_OR_API_KEY": "k",
        "INFERENCE_COM_KIND": "ollama", "INFERENCE_COM_URL": "https://ollama.com", "INFERENCE_COM_API_KEY": "k"}),
        up.client())
    settings = CallSettings(think="off", num_ctx=16384, keep_alive=900)
    prefix = [Message("system", "rules"), Message("user", "(note)")]
    assert await router.read_prompt("local:m", prefix, [], settings) is True
    for hosted in ("gem:gemini-x-flash", "or:x/y", "local:gpt-oss:120b-cloud", "local:kimi-k2:cloud",
                   "com:gpt-oss:120b"):
        assert await router.read_prompt(hosted, prefix, [], settings) is False, hosted
    assert [(r.url.host, r.method, r.url.path) for r in up.requests] == [
        ("ollama.test", "POST", "/api/show"), ("ollama.test", "POST", "/api/chat")]
    await router.aclose()


async def test_has_model_reads_the_list_and_raises_when_the_provider_cant_be_asked():
    # The phone's refresher skips a model the provider no longer lists, as the gate does, but keeps its own outage
    # accounting, so this check raises (and logs nothing) where is_listed fails open with a warning.
    up = FakeUpstream().on("GET", "/api/tags", lambda: httpx.Response(200, json={"models": [{"name": "m"}]}))
    router = Router(load_provider_configs({"OLLAMA_URL": "http://ollama.test"}), up.client())
    assert await router.has_model("ollama:m") is True and await router.has_model("ollama:gone") is False
    down = Router(load_provider_configs({"OLLAMA_URL": "http://ollama.test"}),
                  FakeUpstream().on("GET", "/api/tags", httpx.ConnectError("down")).client())
    with pytest.raises(ProviderUnavailable):
        await down.has_model("ollama:m")
    await router.aclose()
    await down.aclose()
