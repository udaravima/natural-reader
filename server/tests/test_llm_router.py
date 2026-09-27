import logging

import httpx
import pytest

from server.llm import router as llm_router
from server.llm.router import Router, UnknownModel, load_provider_configs
from server.llm.types import CallSettings, Message
from server.tests.llm_fakes import FakeUpstream, ndjson, sse

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


async def test_complete_concatenates_text():
    r, up = _router()
    assert await r.complete("local:qwen2.5:7b", [Message("user", "hi")]) == "Hi there"
    assert "think" not in up.bodies("/api/chat")[0]   # /api/show says no thinking: field omitted


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
