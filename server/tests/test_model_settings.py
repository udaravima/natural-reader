"""Admin console v2 phase 2a, Task 1: the models section and its resolution rules (services/model_settings.py)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from server.auth.users import SEED_ADMIN_ID
from server.llm.providers.base import ProviderConfig
from server.llm.router import ModelInfo
from server.llm.types import Capabilities
from server.services import console_settings, model_settings
from server.services.inference_limits import InferenceLimits


@pytest.fixture(autouse=True)
def _fresh_warnings():
    console_settings._warned.clear()


def _info(model_id: str) -> ModelInfo:
    provider, _, name = model_id.partition(":")
    return ModelInfo(model_id, provider, "ollama", name, Capabilities())


def test_defaults_without_console_or_env():
    ms = model_settings.from_env({})
    assert ms.reader == "chat" and ms.reader_source == "default"
    assert ms.context_size is None and ms.context_sizes == {} and ms.chat_models == ()
    assert ms.reader_env is None and ms.context_size_source == "default"


def test_reader_env_chain_names_the_deciding_variable():
    ms = model_settings.from_env({"SUMMARIZE_MODEL": "ollama:gemma4:e4b"})
    assert (ms.reader, ms.reader_source, ms.reader_env) == ("ollama:gemma4:e4b", "env", "SUMMARIZE_MODEL")


def test_reader_chain_takes_the_first_variable_that_is_set():
    env = {"WEB_SEARCH_SUMMARY_MODEL": "ollama:c", "SUMMARIZE_MODEL": "ollama:b"}
    assert model_settings.from_env(env).reader == "ollama:b"
    assert model_settings.from_env({**env, "READER_MODEL": "Chat"}).reader == "chat"
    assert model_settings.from_env({**env, "READER_MODEL": "Chat"}).reader_env == "READER_MODEL"


def test_an_unusable_reader_variable_falls_back_to_the_default_and_names_no_variable():
    ms = model_settings.from_env({"READER_MODEL": "two words"})
    assert (ms.reader, ms.reader_source, ms.reader_env) == ("chat", "default", None)


def test_the_local_size_comes_from_its_variable():
    ms = model_settings.from_env({"INFERENCE_LOCAL_NUM_CTX": "24576"})
    assert (ms.context_size, ms.context_size_source) == (24576, "env")
    assert model_settings.from_env({"INFERENCE_LOCAL_NUM_CTX": "tiny"}).context_size is None
    assert model_settings.from_env({"INFERENCE_LOCAL_NUM_CTX": "100"}).context_size is None


def test_num_ctx_order_model_then_deployment_then_user_then_default():
    lim = InferenceLimits(num_ctx_max=32768, keep_alive_max_s=None)
    ms = replace(model_settings.from_env({}), context_size=8192, context_sizes={"ollama:qwen3:8b": 12288})
    assert model_settings.num_ctx_for(ms, "ollama:qwen3:8b", local=True, user_value=4096, limits=lim) == 12288
    assert model_settings.num_ctx_for(ms, "ollama:gemma4:e4b", local=True, user_value=4096, limits=lim) == 8192
    unset = model_settings.from_env({})
    assert model_settings.num_ctx_for(unset, "ollama:gemma4:e4b", local=True, user_value=4096, limits=lim) == 4096
    assert model_settings.num_ctx_for(unset, "ollama:gemma4:e4b", local=True, user_value=None, limits=lim) == 16384


def test_num_ctx_is_clamped_and_non_local_passes_the_user_value():
    lim = InferenceLimits(num_ctx_max=8192, keep_alive_max_s=None)
    unset = model_settings.from_env({})
    assert model_settings.num_ctx_for(unset, "m", local=True, user_value=None, limits=lim) == 8192
    assert model_settings.num_ctx_for(unset, "m", local=False, user_value=4096, limits=lim) == 4096
    assert model_settings.num_ctx_for(unset, "m", local=False, user_value=None, limits=lim) is None
    assert model_settings.num_ctx_for(unset, "m", local=False, user_value=99999, limits=lim) == 8192


def test_a_non_local_model_ignores_the_deployment_sizes():
    lim = InferenceLimits(num_ctx_max=None, keep_alive_max_s=None)
    ms = replace(model_settings.from_env({}), context_size=8192, context_sizes={"ollama:x": 12288})
    assert model_settings.num_ctx_for(ms, "ollama:x", local=False, user_value=None, limits=lim) is None


def test_deployment_num_ctx_says_where_the_number_came_from():
    lim = InferenceLimits(num_ctx_max=10000, keep_alive_max_s=None)
    base = model_settings.from_env({})
    assert model_settings.deployment_num_ctx(base, "m", lim) == (None, "unset")
    env = model_settings.from_env({"INFERENCE_LOCAL_NUM_CTX": "8192"})
    assert model_settings.deployment_num_ctx(env, "m", lim) == (8192, "env")
    admin = replace(env, context_size=16384, context_size_source="admin")
    assert model_settings.deployment_num_ctx(admin, "m", lim) == (10000, "admin")   # clamped
    own = replace(admin, context_sizes={"m": 4096})
    assert model_settings.deployment_num_ctx(own, "m:latest", lim) == (4096, "model")


def test_latest_suffix_does_not_matter():
    ms = replace(model_settings.from_env({}), context_sizes={"ollama:gemma4:e4b": 12288})
    assert model_settings.lookup(ms.context_sizes, "ollama:gemma4:e4b:latest") == 12288
    ms2 = replace(ms, context_sizes={"ollama:gemma4:e4b:latest": 9000})
    assert model_settings.lookup(ms2.context_sizes, "ollama:gemma4:e4b") == 9000
    assert model_settings.lookup(ms2.context_sizes, "ollama:other") is None
    assert model_settings.same_model("a:b:latest", "a:b") and not model_settings.same_model("a:b", "a:c")


@pytest.mark.parametrize("url,name,override,local", [
    ("http://localhost:11434", "gemma4:e4b", {}, True),
    ("http://127.0.0.1:11434", "gemma4:31b-cloud", {}, False),     # cloud on any host
    ("http://192.168.1.20:11434", "qwen3:8b", {}, True),
    ("http://ollama:11434", "qwen3:8b", {}, True),                  # single-label service name
    ("https://openrouter.ai/api/v1", "x/y", {}, False),
    ("http://gpu.example.com:11434", "qwen3:8b", {"gpu": "local"}, True),
    ("http://localhost:11434", "qwen3:8b", {"ollama": "cloud"}, False),
    ("http://localhost:11434", "m:cloud", {"ollama": "local"}, False),   # the model name beats the override
    ("http://[::1]:11434", "qwen3:8b", {}, True),
    ("http://8.8.8.8:11434", "qwen3:8b", {}, False),
    ("http://host.docker.internal:11434", "qwen3:8b", {}, True),
    ("http://host.containers.internal:11434", "qwen3:8b", {}, True),
    ("http://x.local:11434", "qwen3:8b", {}, True),
    ("http://x.lan:11434", "qwen3:8b", {}, True),
    ("http://x.internal:11434", "qwen3:8b", {}, True),
    ("http://x.home.arpa:11434", "qwen3:8b", {}, True),
    ("http://100.64.0.1:11434", "qwen3:8b", {}, True),
    ("http://100.127.255.254:11434", "qwen3:8b", {}, True),
    ("http://localhost.:11434", "qwen3:8b", {}, True),
    ("http://100.128.0.1:11434", "qwen3:8b", {}, False),
    ("http://gpu.example.com:11434", "qwen3:8b", {}, False),
    ("http://[::1", "qwen3:8b", {}, False),                          # malformed: never an exception
    ("", "qwen3:8b", {}, False),
    ("http://.:11434", "qwen3:8b", {}, False),                       # a bare dot is no host
])
def test_is_local(url, name, override, local):
    cfg = ProviderConfig(name="gpu" if "gpu" in url else "ollama", kind="ollama", url=url, models=None)
    assert model_settings.is_local(cfg, name, override) is local


@pytest.mark.parametrize("kind,url,name,sizes", [
    ("ollama", "http://localhost:11434", "gemma4:e4b", True),
    ("ollama", "http://gpu.example.com:11434", "qwen3:8b", True),      # a remote self-hosted Ollama: still 4k
    ("ollama", "http://127.0.0.1:11434", "gemma4:31b-cloud", False),
    ("ollama", "http://localhost:11434", "gpt-oss:120b:cloud", False),
    ("ollama", "https://ollama.com", "gpt-oss:120b", False),
    ("ollama", "https://api.ollama.com/", "gpt-oss:120b", False),
    ("ollama", "https://notollama.com", "qwen3:8b", True),             # a suffix match is not a subdomain
    ("openai", "http://localhost:8000", "qwen3", False),
])
def test_sizes_context(kind, url, name, sizes):
    cfg = ProviderConfig(name="ollama", kind=kind, url=url)
    assert model_settings.sizes_context(cfg, name) is sizes


def test_a_cloud_mark_doesnt_stop_sizing_a_self_hosted_ollama():
    """Ruling P11: the mark is cost-only. A self-hosted Ollama marked "cloud" still loads at 4,096 tokens unless told
    otherwise, so it is still sized; it just stops counting as local."""
    cfg = ProviderConfig(name="ollama", kind="ollama", url="http://localhost:11434")
    assert model_settings.sizes_context(cfg, "gemma4:e4b") is True
    assert model_settings.is_local(cfg, "gemma4:e4b", {"ollama": "cloud"}) is False


def test_is_local_answers_for_the_provider_alone_with_an_empty_model_name():
    here = ProviderConfig(name="ollama", kind="ollama", url="http://localhost:11434")
    there = ProviderConfig(name="or", kind="openai", url="https://openrouter.ai/api/v1")
    assert model_settings.is_local(here, "", {}) is True
    assert model_settings.is_local(there, "", {}) is False
    assert model_settings.is_local(there, "", {"or": "local"}) is True


def test_a_cloud_suffix_means_nothing_on_an_openai_kind_provider():
    cfg = ProviderConfig(name="vllm", kind="openai", url="http://localhost:8000")
    assert model_settings.is_local(cfg, "weird-cloud", {}) is True


def test_declared_window_console_then_provider_env():
    ms = replace(model_settings.from_env({}), context_windows={"vllm:qwen3": 40960})
    env = {"INFERENCE_VLLM_CONTEXT_WINDOW": "32768"}
    assert model_settings.declared_window(ms, "vllm:qwen3", "vllm", env) == (40960, "admin")
    assert model_settings.declared_window(ms, "vllm:other", "vllm", env) == (32768, "env")
    assert model_settings.declared_window(ms, "ollama:x", "ollama", env) == (None, "none")


def test_declared_window_provider_name_maps_to_its_variable_and_ignores_a_bad_value():
    ms = model_settings.from_env({})
    assert model_settings.declared_window(ms, "my-gpu:x", "my-gpu", {"INFERENCE_MY_GPU_CONTEXT_WINDOW": "8192"}) \
        == (8192, "env")
    assert model_settings.declared_window(ms, "v:x", "v", {"INFERENCE_V_CONTEXT_WINDOW": "huge"}) == (None, "none")
    assert model_settings.declared_window(ms, "v:x", "v", {"INFERENCE_V_CONTEXT_WINDOW": "5"}) == (None, "none")


def test_offered_narrows_and_a_stale_selection_offers_nothing():
    models = [_info("ollama:a"), _info("ollama:b")]
    assert model_settings.offered(models, model_settings.from_env({})) == models
    pick = replace(model_settings.from_env({}), chat_models=("ollama:b:latest",))
    assert [m.id for m in model_settings.offered(models, pick)] == ["ollama:b"]
    gone = replace(model_settings.from_env({}), chat_models=("ollama:zzz",))
    assert model_settings.offered(models, gone) == []


def test_info_rows_show_the_ceilings_and_where_they_come_from():
    rows = {r["id"]: r for r in model_settings.info_rows({})}
    assert rows["num_ctx_max"] == {"id": "num_ctx_max", "label": "Largest context size", "value": "32,768 tokens",
                                   "env": "INFERENCE_NUM_CTX_MAX", "source": "default"}
    assert rows["keep_alive_max"]["value"] == "30 minutes" and rows["keep_alive_max"]["env"] == "INFERENCE_KEEP_ALIVE_MAX"
    rows = {r["id"]: r for r in model_settings.info_rows({"INFERENCE_NUM_CTX_MAX": "0", "INFERENCE_KEEP_ALIVE_MAX": "-1"})}
    assert rows["num_ctx_max"]["value"] == "No limit" and rows["num_ctx_max"]["source"] == "env"
    assert rows["keep_alive_max"]["value"] == "No limit"
    assert model_settings.info_rows({"INFERENCE_KEEP_ALIVE_MAX": "90s"})[1]["value"] == "90 seconds"


@pytest.mark.parametrize("name,value", [
    ("context_size", 1024), ("context_size", "big"), ("context_size", True), ("context_size", 2_097_153),
    ("context_sizes", {"m": 0}), ("context_sizes", ["m"]), ("context_sizes", {"": 4096}),
    ("context_sizes", {"m" * 201: 4096}), ("context_sizes", {f"m{i}": 4096 for i in range(501)}),
    ("context_windows", {"m": 10}), ("context_windows", {"m": 10_000_001}),
    ("provider_locality", {"ollama": "nearby"}), ("provider_locality", "local"),
    ("chat_models", "ollama:a"), ("chat_models", [""]), ("chat_models", [3]), ("reader", ""),
    ("reader", "two words"), ("reader", 5), ("reader", "x" * 201),
])
async def test_save_refuses_bad_values(db_conn, name, value):
    with pytest.raises(console_settings.SettingError):
        await console_settings.save(db_conn, model_settings.SECTION, {name: value}, updated_by="admin")


async def test_save_keeps_maps_sorted_and_lists_unique_and_ordered(db_conn):
    await console_settings.save(db_conn, model_settings.SECTION, {
        "context_sizes": {"ollama:b": 4096, "ollama:a": 8192},
        "chat_models": ["ollama:z", "ollama:a", "ollama:z:latest", "ollama:a"],
        "reader": "Chat",
    }, updated_by=SEED_ADMIN_ID)
    ms = await model_settings.load_models_settings(db_conn, {})
    assert list(ms.context_sizes) == ["ollama:a", "ollama:b"]
    assert ms.chat_models == ("ollama:z", "ollama:a")
    assert (ms.reader, ms.reader_source) == ("chat", "admin")


async def test_the_console_beats_the_environment_and_a_reset_returns_to_it(db_conn):
    env = {"SUMMARIZE_MODEL": "ollama:small", "INFERENCE_LOCAL_NUM_CTX": "8192"}
    await console_settings.save(db_conn, model_settings.SECTION, {"reader": "ollama:big", "context_size": 12288},
                                updated_by=SEED_ADMIN_ID, env=model_settings.models_env(env))
    ms = await model_settings.load_models_settings(db_conn, env)
    assert (ms.reader, ms.reader_source, ms.context_size, ms.context_size_source) == ("ollama:big", "admin", 12288, "admin")
    assert ms.reader_env == "SUMMARIZE_MODEL"   # the variable behind the server setting the console overrides
    await console_settings.save(db_conn, model_settings.SECTION, {"reader": None, "context_size": None},
                                updated_by=SEED_ADMIN_ID, env=model_settings.models_env(env))
    ms = await model_settings.load_models_settings(db_conn, env)
    assert (ms.reader, ms.reader_source, ms.context_size, ms.context_size_source) == ("ollama:small", "env", 8192, "env")


async def test_view_names_the_readers_deciding_variable(db_conn):
    env = {"WEB_SEARCH_SUMMARY_MODEL": "ollama:s"}
    values = await console_settings.load(db_conn, model_settings.SECTION, model_settings.models_env(env))
    assert model_settings.view(values, env)["reader"]["env"] == "WEB_SEARCH_SUMMARY_MODEL"
    assert model_settings.view(values, env)["reader"]["value"] == "ollama:s"
    assert model_settings.view(values, {})["reader"]["env"] == "READER_MODEL"
