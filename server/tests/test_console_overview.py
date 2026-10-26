"""The Overview's health checks (admin console v2 spec §4.1): one line each, never a hang."""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.llm.providers.base import ProviderConfig
from server.llm.router import ModelInfo, UnknownModel
from server.llm.types import Capabilities
from server.routers import admin as admin_router
from server.auth.users import SEED_ADMIN_ID
from server.services import (chat_options, console_overview, console_settings, docling_convert, embeddings, model_settings,
                             web_search)
from server.services.inference_limits import InferenceLimits
from server.chat.config import ChatConfig
from server.services.console_overview import project_turn_tokens, run_checks, window_for
from server.tests import seed


class _Provider:
    def __init__(self, name, names=("m", "llama3.2:3b"), kind="ollama", fails=False, url="http://gpu.example.com:11434"):
        self.config = ProviderConfig(name=name, kind=kind, url=url)
        self._names, self._fails = list(names), fails

    async def list_models(self):
        if self._fails:
            raise httpx.ConnectError("down")
        return list(self._names)

    async def loaded_context_lengths(self):
        return dict(getattr(self, "loaded", {}))


class _Router:
    def __init__(self, *providers):
        self.providers = {p.config.name: p for p in providers}

    def resolve(self, model_id):
        prefix, sep, rest = model_id.partition(":")
        if sep and prefix in self.providers:
            return self.providers[prefix], rest
        for p in self.providers.values():
            if p.config.kind == "ollama":
                return p, model_id
        raise UnknownModel(model_id)

    async def list_models(self):
        models, failed = [], []
        for p in self.providers.values():
            if p._fails:
                failed.append(p.config.name)
                continue
            models += [ModelInfo(f"{p.config.name}:{n}", p.config.name, p.config.kind, n, Capabilities())
                       for n in p._names]
        return models, failed


def _router():
    return _Router(_Provider("local"))


def _embedding(width=768):
    return lambda request: httpx.Response(200, json={"embedding": [0.1] * width})


def _searxng(status=200, embed=None):
    """One client for both network checks: /healthz answers SearXNG's, /api/embeddings the embedding probe's
    (a healthy 768-wide model unless `embed` says otherwise)."""
    def handler(request):
        if request.url.path == "/api/embeddings":
            return (embed or _embedding())(request)
        assert request.url.path == "/healthz"
        return httpx.Response(status)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _searxng_ok():
    return _searxng(200)


def _with_embed(embed):
    return _searxng(200, embed)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    async def not_this(_text):   # the probe makes its own request: embed_one waits on the indexing semaphore
        raise AssertionError("the embedding check must not go through embed_one")
    monkeypatch.setattr(embeddings, "embed_one", not_this)
    monkeypatch.setattr(web_search, "SEARXNG_URL", "http://searx.example.com:8080")


async def _by_id(db_conn, llm, env=None, http=None):
    return {c.id: c for c in await run_checks(db_conn, llm, {} if env is None else env, http=http or _searxng_ok())}


async def test_a_check_that_hangs_reports_itself_and_the_others_still_answer(db_conn, monkeypatch):   # Review Focus 1
    monkeypatch.setattr(console_overview, "CHECK_TIMEOUT_S", 0.2)

    async def never(_request):
        await asyncio.sleep(10)
    started = time.monotonic()
    checks = {c.id: c for c in await run_checks(db_conn, _router(), {}, http=_with_embed(never))}
    assert time.monotonic() - started < 2
    assert checks["embeddings"].state == "error"
    assert checks["embeddings"].summary.startswith("Didn't answer within")
    assert checks["providers"].state == "ok" and checks["web_search"].state == "ok"
    # the connection survived: a hung check never held it
    await db_conn.execute("SELECT 1")


async def test_a_check_that_raises_says_which_kind_of_failure(db_conn, monkeypatch):
    def boom(_request):
        raise KeyError("x")
    c = (await _by_id(db_conn, _router(), http=_with_embed(boom)))["embeddings"]
    assert (c.state, c.summary) == ("error", "The check failed: KeyError.")


async def test_checks_come_in_a_fixed_order(db_conn):
    checks = await run_checks(db_conn, _router(), {}, http=_searxng_ok())
    assert [c.id for c in checks] == ["providers", "reader", "context", "embeddings", "docling", "web_search"]


async def test_providers_states(db_conn):
    two = _Router(_Provider("local", names=("a", "b")), _Provider("cloud", names=("c",), kind="openai"))
    c = (await _by_id(db_conn, two))["providers"]
    assert (c.state, c.title, c.section) == ("ok", "Model providers", "models")
    assert c.summary == "2 reachable: local (2 models), cloud (1 model)"

    some = _Router(_Provider("local"), _Provider("cloud", kind="openai", fails=True))
    c = (await _by_id(db_conn, some))["providers"]
    assert (c.state, c.summary) == ("warn", "cloud can't be reached.")

    none = _Router(_Provider("local", fails=True))
    c = (await _by_id(db_conn, none))["providers"]
    assert (c.state, c.summary) == ("error", "No provider can be reached.")

    c = (await _by_id(db_conn, _Router()))["providers"]
    assert (c.state, c.summary) == ("error", "No model provider is configured (INFERENCE_PROVIDERS or OLLAMA_URL).")


async def _save_models(db_conn, **changes):
    await console_settings.save(db_conn, model_settings.SECTION, changes, updated_by=SEED_ADMIN_ID)


def _local_router(*names):
    return _Router(_Provider("local", names=names or ("m", "llama3.2:3b"), url="http://localhost:11434"))


_HINT = (" Reader calls then count against a cloud model's quota; a small local reader, chosen in Models, "
         "keeps them off it.")


async def test_the_default_reader_is_ok_never_warn(db_conn):   # R5
    c = (await _by_id(db_conn, _local_router()))["reader"]
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation (the default).")
    assert c.section == "models"

    # models that aren't local add the quota hint, and the line is still ok
    c = (await _by_id(db_conn, _router()))["reader"]
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation (the default)." + _HINT)


async def test_a_reader_of_chat_set_in_the_environment_or_here_is_ok(db_conn):
    c = (await _by_id(db_conn, _local_router(), {"READER_MODEL": "chat"}))["reader"]
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation.")
    c = (await _by_id(db_conn, _router(), {"SUMMARIZE_MODEL": "chat"}))["reader"]
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation." + _HINT)

    await _save_models(db_conn, reader="chat")
    c = (await _by_id(db_conn, _router()))["reader"]
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation." + _HINT)


async def test_the_reader_hint_asks_whether_a_model_costs_money_not_whether_it_needs_a_size(db_conn):
    # an Ollama on a dotted public host still needs a context size, but the admin says it's theirs: no cloud quota
    await _save_models(db_conn, provider_locality={"local": "local"})
    c = (await _by_id(db_conn, _router()))["reader"]
    assert c.summary == "The chat model of each conversation (the default)."

    # and the other way round: a localhost provider the admin marks cloud brings the hint
    await _save_models(db_conn, provider_locality={"local": "cloud"})
    c = (await _by_id(db_conn, _local_router()))["reader"]
    assert c.summary == "The chat model of each conversation (the default)." + _HINT


async def test_the_reader_hint_counts_only_the_models_offered(db_conn):
    two = _Router(_Provider("local", names=("a",), url="http://localhost:11434"),
                  _Provider("cloud", names=("b",), kind="openai", url="https://api.example.com/v1"))
    c = (await _by_id(db_conn, two))["reader"]
    assert c.summary.endswith(_HINT)
    await _save_models(db_conn, chat_models=["local:a"])
    c = (await _by_id(db_conn, two))["reader"]
    assert c.summary == "The chat model of each conversation (the default)."


class _HangingList(_Router):
    """A router whose model listing never answers; each provider still answers for itself."""
    async def list_models(self):
        await asyncio.sleep(10)


async def test_chat_reader_stays_ok_when_a_provider_hangs(db_conn, monkeypatch):
    monkeypatch.setattr(console_overview, "CHECK_TIMEOUT_S", 1.0)
    started = time.monotonic()
    c = (await _by_id(db_conn, _HangingList(_Provider("local"))))["reader"]
    assert time.monotonic() - started < 2
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation (the default).")   # no hint, no error


async def test_chat_reader_stays_ok_when_config_is_malformed(db_conn):
    c = (await _by_id(db_conn, _router(), {"INFERENCE_DAILY_TOKEN_BUDGET": "lots"}))["reader"]
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation (the default).")


async def test_a_named_reader_stays_ok_when_the_model_list_hangs(db_conn, monkeypatch):
    monkeypatch.setattr(console_overview, "CHECK_TIMEOUT_S", 1.0)
    # only the reader's own provider decides a named reader's state
    c = (await _by_id(db_conn, _HangingList(_Provider("local")), {"READER_MODEL": "m"}))["reader"]
    assert (c.state, c.summary) == ("ok", "m, from READER_MODEL.")


async def test_a_named_reader_is_ok_with_where_it_was_set(db_conn):
    c = (await _by_id(db_conn, _router(), {"SUMMARIZE_MODEL": "m"}))["reader"]
    assert (c.state, c.summary) == ("ok", "m, from SUMMARIZE_MODEL.")

    await _save_models(db_conn, reader="m")
    c = (await _by_id(db_conn, _router(), {"SUMMARIZE_MODEL": "llama3.2:3b"}))["reader"]
    assert (c.state, c.summary) == ("ok", "m, set in Models.")


async def test_a_reader_model_that_isnt_listed_says_how_to_fix_it(db_conn):
    for env in ({"READER_MODEL": "gone"}, {}):
        if not env:
            await _save_models(db_conn, reader="gone")
        c = (await _by_id(db_conn, _router(), env))["reader"]
        assert (c.state, c.summary) == ("error", "gone isn't on local: every reader call fails until it's pulled "
                                                 "or another reader is chosen in Models.")


async def test_a_reader_that_cannot_be_checked(db_conn):
    down = _Router(_Provider("local", fails=True))
    c = (await _by_id(db_conn, down, {"READER_MODEL": "m"}))["reader"]
    assert (c.state, c.summary) == ("error", "Can't reach local to check m.")

    c = (await _by_id(db_conn, _Router(), {"READER_MODEL": "m"}))["reader"]
    assert (c.state, c.summary) == ("error", "m can't be used: no Ollama provider is configured, so a bare model name has nowhere to go.")


async def test_embeddings_states(db_conn):
    c = (await _by_id(db_conn, _router()))["embeddings"]
    assert c.state == "ok"
    assert c.summary == "nomic-embed-text at localhost:11434: 768 dimensions."

    c = (await _by_id(db_conn, _router(), http=_with_embed(_embedding(1024))))["embeddings"]
    assert (c.state, c.summary) == ("error", "nomic-embed-text returned 1024 dimensions; the database holds 768.")

    def refuses(_request):
        raise httpx.ConnectError("no")
    c = (await _by_id(db_conn, _router(), http=_with_embed(refuses)))["embeddings"]
    assert c.state == "error"
    assert c.summary == "Can't reach the embedding model nomic-embed-text at localhost:11434: documents can't be indexed or searched."

    def slow(_request):
        raise httpx.ReadTimeout("slow")
    c = (await _by_id(db_conn, _router(), http=_with_embed(slow)))["embeddings"]
    assert c.summary.startswith("Can't reach the embedding model nomic-embed-text")


async def test_a_model_that_was_never_pulled_reads_as_an_http_error_not_as_unreachable(db_conn):
    c = (await _by_id(db_conn, _router(), http=_with_embed(lambda _r: httpx.Response(404, json={"error": "model not found"}))))["embeddings"]
    assert (c.state, c.summary) == ("error", "nomic-embed-text at localhost:11434 answered HTTP 404: documents can't be indexed or searched.")


async def test_an_answer_with_no_embedding_is_reported_as_such(db_conn):
    for answer in (httpx.Response(200, json={}), httpx.Response(200, json={"embedding": []}),
                   httpx.Response(200, text="not json"), httpx.Response(200, json=[1, 2])):
        c = (await _by_id(db_conn, _router(), http=_with_embed(lambda _r, a=answer: a)))["embeddings"]
        assert c.state == "error"
        assert c.summary == "nomic-embed-text at localhost:11434 answered, but returned no embedding: documents can't be indexed or searched."


async def test_the_probe_sends_embed_ones_request_and_asks_for_no_dimensions(db_conn):
    seen = []

    def spy(request):
        seen.append(request)
        return httpx.Response(200, json={"embedding": [0.1] * 768})
    await _by_id(db_conn, _router(), env={"OLLAMA_URL": "http://embed.example.com:11434", "EMBEDDING_MODEL": "other-model"},
                 http=_with_embed(spy))
    assert len(seen) == 1
    assert str(seen[0].url) == "http://embed.example.com:11434/api/embeddings"
    assert json.loads(seen[0].content) == {"model": "other-model", "prompt": console_overview.EMBEDDING_PROBE}


async def test_a_busy_indexing_queue_does_not_make_a_healthy_model_look_down(db_conn):   # T5
    held = [await embeddings._semaphore.acquire() for _ in range(embeddings.MAX_CONCURRENCY)]
    try:
        c = (await _by_id(db_conn, _router()))["embeddings"]
    finally:
        for _ in held:
            embeddings._semaphore.release()
    assert c.state == "ok"


async def test_embeddings_warns_about_documents_under_an_older_setup(db_conn):
    for i in range(2):
        await seed.seed_doc(db_conn, f"old{i}", None, state="indexed")
        await db_conn.execute("UPDATE documents SET embedding_profile = 'older' WHERE doc_id = %s", (f"old{i}",))
    await seed.seed_doc(db_conn, "cur", None, state="indexed")
    await db_conn.execute("UPDATE documents SET embedding_profile = %s WHERE doc_id = 'cur'", (embeddings.current_profile(),))
    c = (await _by_id(db_conn, _router()))["embeddings"]
    assert c.state == "warn"
    assert "2 documents were indexed under an older embedding setup; each rebuilds the next time someone uses it." in c.summary


async def test_docling_states(db_conn, monkeypatch):
    monkeypatch.setattr(docling_convert, "is_enabled", lambda: False)
    c = (await _by_id(db_conn, _router()))["docling"]
    assert (c.state, c.summary, c.section) == ("off", "Off on this server (DOCLING_ENABLED).", "documents")

    monkeypatch.setattr(docling_convert, "is_enabled", lambda: True)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    c = (await _by_id(db_conn, _router()))["docling"]
    assert (c.state, c.summary) == ("error", "DOCLING_ENABLED is on, but Docling isn't installed: every conversion fails.")

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    c = (await _by_id(db_conn, _router()))["docling"]
    assert (c.state, c.summary) == ("ok", "On.")


async def test_web_search_states(db_conn, monkeypatch):
    c = (await _by_id(db_conn, _router()))["web_search"]
    assert (c.state, c.summary, c.section) == ("ok", "SearXNG at searx.example.com:8080 answers.", "chat")

    c = (await _by_id(db_conn, _router(), http=_searxng(500)))["web_search"]
    assert (c.state, c.summary) == ("error", "SearXNG at searx.example.com:8080 doesn't answer: web search fails.")

    def refuse(request):
        raise httpx.ConnectError("no")
    down = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    c = (await _by_id(db_conn, _router(), http=down))["web_search"]
    assert c.state == "error"

    await chat_options.save_chat_options(db_conn, "off", updated_by=None)
    c = (await _by_id(db_conn, _router()))["web_search"]
    assert (c.state, c.summary) == ("off", "Turned off in Chat options.")

    monkeypatch.setattr(web_search, "SEARXNG_URL", "")
    c = (await _by_id(db_conn, _router()))["web_search"]
    assert (c.state, c.summary) == ("off", "Not set up on this server (SEARXNG_URL).")


async def test_a_malformed_searxng_url_is_an_error_line_not_a_500(db_conn, monkeypatch):
    monkeypatch.setattr(web_search, "SEARXNG_URL", "http://[::1")
    c = (await _by_id(db_conn, _router()))["web_search"]
    assert c.state == "error"


async def test_to_json_shape():
    c = console_overview.Check("x", "ok", "T", "S", None)
    assert c.to_json() == {"id": "x", "state": "ok", "title": "T", "summary": "S", "section": None, "items": []}


async def test_overview_route_is_admin_only(db_conn, monkeypatch):
    monkeypatch.setattr(admin_router, "get_router", lambda: _router())

    def app_for(principal):
        app = FastAPI()

        async def _conn_override():
            yield db_conn
        app.dependency_overrides[deps.get_conn] = _conn_override
        app.dependency_overrides[deps.get_current_user] = lambda: principal
        app.include_router(admin_router.router)
        return app

    def client(app):
        return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    member = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    async with client(app_for(deps.Principal(user_id=member["id"], email=member["email"], role="member"))) as c:
        assert (await c.get("/v1/admin/overview")).status_code == 403
    async with client(app_for(deps.Principal(user_id=admin["id"], email=admin["email"], role="admin"))) as c:
        r = await c.get("/v1/admin/overview")
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["checks"]] == ["providers", "reader", "context", "embeddings", "docling", "web_search"]


_LIMITS = InferenceLimits(num_ctx_max=32768, keep_alive_max_s=1800)
_MS = model_settings.from_env({})
_GPU = ProviderConfig(name="ollama", kind="ollama", url="http://gpu.example.com:11434")   # a dotted public host


def _ollama(name="gemma4:e4b", window=131072):
    return ModelInfo(f"ollama:{name}", "ollama", "ollama", name, Capabilities(context_window=window))


def _openai(window=None, provider="oa"):
    return ModelInfo(f"{provider}:gpt", provider, "openai", "gpt", Capabilities(context_window=window))


def _win(model, ms=_MS, loaded=None, cfg=_GPU, env=None, limits=_LIMITS):
    return window_for(model, loaded or {}, ms, cfg, env or {}, limits)


def test_a_loaded_ollama_model_reports_what_is_loaded():
    assert _win(_ollama(), loaded={"gemma4:e4b": 4096}) == (4096, "Loaded now (Ollama).")
    assert _win(_ollama("x:latest"), loaded={"x": 8192}) == (8192, "Loaded now (Ollama).")


def test_a_local_ollama_model_not_loaded_gets_the_size_set_for_it():
    ms = dataclasses.replace(_MS, context_sizes={"ollama:gemma4:e4b": 24000})
    assert _win(_ollama(), ms) == (24000, "The context size set for this model in Models.")
    # a size above the ceiling is clamped, as the request will clamp it
    ms = dataclasses.replace(_MS, context_sizes={"ollama:gemma4:e4b": 100000})
    assert _win(_ollama(), ms) == (32768, "The context size set for this model in Models.")


def test_a_local_ollama_model_not_loaded_gets_the_deployments_size():
    ms = dataclasses.replace(_MS, context_size=20000, context_size_source="admin")
    assert _win(_ollama(), ms) == (20000, "The deployment's context size, set in Models.")
    ms = model_settings.from_env({"INFERENCE_LOCAL_NUM_CTX": "20000"})
    assert _win(_ollama(), ms) == (20000, "The deployment's context size, from INFERENCE_LOCAL_NUM_CTX.")


def test_with_no_size_set_a_local_ollama_model_gets_the_default_clamped():
    assert _win(_ollama()) == (16384, "16,384 tokens unless a person sets their own in Settings (no deployment size is set).")
    low = InferenceLimits(num_ctx_max=8192, keep_alive_max_s=1800)   # P10: the copy names the number that applies
    assert _win(_ollama(), limits=low) == (8192, "8,192 tokens unless a person sets their own in Settings (no deployment size is set).")


def test_sizing_follows_the_ollama_not_its_host():   # P4: a remote GPU box on a public name still loads at 4,096
    assert _win(_ollama(), cfg=_GPU)[0] == 16384
    marked_cloud = dataclasses.replace(_MS, provider_locality={"ollama": "cloud"})
    assert _win(_ollama(), marked_cloud) == (131072, "Reported by the provider.")
    hosted = ProviderConfig(name="ollama", kind="ollama", url="https://ollama.com")
    assert _win(_ollama(), cfg=hosted) == (131072, "Reported by the provider.")


def test_an_ollama_cloud_model_gets_its_maximum():
    m = _ollama("gemma4:31b-cloud", 262144)
    assert _win(m) == (262144, "The model's maximum (Ollama cloud).")


def test_other_providers_report_their_own_number_or_are_declared_or_unknown():
    oa = ProviderConfig(name="oa", kind="openai", url="https://api.example.com/v1")
    assert _win(_openai(131072), cfg=oa) == (131072, "Reported by the provider.")
    ms = dataclasses.replace(_MS, context_windows={"oa:gpt": 64000})
    assert _win(_openai(), ms, cfg=oa) == (64000, "Declared in Models.")
    assert _win(_openai(), cfg=oa, env={"INFERENCE_OA_CONTEXT_WINDOW": "50000"}) == (
        50000, "From INFERENCE_OA_CONTEXT_WINDOW.")
    assert _win(_openai(), cfg=oa) == (None, "Unknown: the provider doesn't report one. Declare it in Models.")


def test_a_project_turn_is_larger_than_its_fixed_budgets():
    cfg = ChatConfig()
    n = project_turn_tokens(cfg, "ask")
    assert n > (cfg.project_map_chars + cfg.tool_result_budget_chars) // 4 + cfg.reply_reserve_tokens
    assert project_turn_tokens(cfg, "ask", profile="x" * 4000) >= n + 900   # the profile leads the rules (its first line is a little shorter than the default)


class _Caps(_Router):
    """A router whose models carry given context windows."""
    def __init__(self, models, *providers):
        super().__init__(*providers)
        self._models = models

    async def list_models(self):
        return self._models, []


async def test_context_check_warns_on_a_small_or_unknown_window(db_conn):
    p = _Provider("local")
    p.loaded = {"small": 4096}
    models = [ModelInfo("local:small", "local", "ollama", "small", Capabilities(context_window=131072)),
              ModelInfo("local:idle", "local", "ollama", "idle", Capabilities(context_window=131072))]
    c = (await _by_id(db_conn, _Caps(models, p)))["context"]
    assert c.state == "warn" and c.summary.startswith("1 of 2 chat models have room")
    assert [i["enough"] for i in c.items] == [False, True]
    assert c.items[0]["window"] == 4096 and c.items[1]["window"] == 16384
    assert c.items[1]["source"].startswith("16,384 tokens unless a person sets their own")
    assert (c.title, c.section) == ("Context windows", "models")


async def test_context_check_ok_when_every_window_fits(db_conn):
    models = [ModelInfo("local:big-cloud", "local", "ollama", "big-cloud", Capabilities(context_window=262144)),
              ModelInfo("oa:gpt", "oa", "openai", "gpt", Capabilities(context_window=131072))]
    llm = _Caps(models, _Provider("local"), _Provider("oa", kind="openai"))
    c = (await _by_id(db_conn, llm))["context"]
    assert c.state == "ok" and all(i["enough"] for i in c.items)


async def test_context_applies_the_console_selection(db_conn):
    models = [ModelInfo("local:a", "local", "ollama", "a", Capabilities(context_window=131072)),
              ModelInfo("local:b", "local", "ollama", "b", Capabilities(context_window=131072))]
    llm = _Caps(models, _Provider("local"))
    await _save_models(db_conn, chat_models=["local:b"], context_sizes={"local:b": 24000})
    c = (await _by_id(db_conn, llm))["context"]
    assert [(i["model"], i["window"]) for i in c.items] == [("local:b", 24000)]
    assert c.items[0]["source"] == "The context size set for this model in Models."
    assert c.summary.startswith("1 of 1 chat models")

    await _save_models(db_conn, chat_models=["local:gone"])   # a selection that leaves nothing
    c = (await _by_id(db_conn, llm))["context"]
    assert (c.state, c.summary) == ("warn", "None of the chat models chosen in Models is listed by a provider: nobody can chat.")
    assert c.items == ()

    await _save_models(db_conn, chat_models=None)   # no selection: the environment's list
    assert len((await _by_id(db_conn, llm))["context"].items) == 2


async def test_the_overview_reads_the_environment_when_the_models_row_cannot_be_read(db_conn, monkeypatch):
    async def broken(conn, env=None):
        raise RuntimeError("no row")
    monkeypatch.setattr(model_settings, "load_models_settings", broken)
    checks = await _by_id(db_conn, _local_router(), {"SUMMARIZE_MODEL": "m"})
    assert checks["reader"].summary == "m, from SUMMARIZE_MODEL."
    assert checks["context"].state in ("ok", "warn")


async def test_context_check_warns_when_no_chat_models(db_conn):
    c = (await _by_id(db_conn, _Caps([], _Provider("local"))))["context"]
    assert (c.state, c.summary) == ("warn", "No chat models are listed.")


def test_the_embedding_model_isnt_listed_as_a_chat_model():
    from server.services.model_router import offered_chat_models
    emb = ModelInfo("o:nomic-embed-text:latest", "o", "ollama", "nomic-embed-text:latest", Capabilities())
    chat = ModelInfo("o:m", "o", "ollama", "m", Capabilities())
    other = ModelInfo("oa:nomic-embed-text", "oa", "openai", "nomic-embed-text", Capabilities())
    assert offered_chat_models([emb, chat, other], "nomic-embed-text") == [chat, other]
    assert offered_chat_models([emb, chat], "nomic-embed-text:latest") == [chat]


async def test_a_malformed_env_value_costs_one_line_not_the_page(db_conn, monkeypatch):
    checks = {c.id: c for c in await run_checks(db_conn, _router(), {"INFERENCE_DAILY_TOKEN_BUDGET": "lots"},
                                                http=_searxng_ok())}
    assert list(checks) == ["providers", "reader", "context", "embeddings", "docling", "web_search"]
    assert (checks["context"].state, checks["context"].summary) == ("error", "The check failed: ValueError.")
    assert checks["providers"].state == "ok" and checks["web_search"].state == "ok"


async def test_the_overview_route_answers_with_a_malformed_env_value(db_conn, monkeypatch):
    monkeypatch.setattr(admin_router, "get_router", lambda: _router())
    monkeypatch.setenv("INFERENCE_DAILY_TOKEN_BUDGET", "lots")
    monkeypatch.setattr(console_overview, "_web_search", lambda http, mode: _off())
    app = FastAPI()

    async def _conn_override():
        yield db_conn
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s9", email="z@x.io")
    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id=admin["id"], email=admin["email"], role="admin")
    app.include_router(admin_router.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/admin/overview")
    assert r.status_code == 200
    by = {x["id"]: x for x in r.json()["checks"]}
    assert by["context"]["summary"] == "The check failed: ValueError."


async def _off():
    return console_overview._check("web_search", "off", "Off.")
