"""The Overview's health checks (admin console v2 spec §4.1): one line each, never a hang."""
from __future__ import annotations

import asyncio
import importlib.util
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
from server.services import chat_options, console_overview, docling_convert, embeddings, web_search
from server.services.console_overview import run_checks
from server.tests import seed


class _Provider:
    def __init__(self, name, names=("m", "llama3.2:3b"), kind="ollama", fails=False, url="http://gpu.example.com:11434"):
        self.config = ProviderConfig(name=name, kind=kind, url=url)
        self._names, self._fails = list(names), fails

    async def list_models(self):
        if self._fails:
            raise httpx.ConnectError("down")
        return list(self._names)


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


def _searxng(status=200):
    def handler(request):
        assert request.url.path == "/healthz"
        return httpx.Response(status)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _searxng_ok():
    return _searxng(200)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    async def fine(_text):
        return [0.1] * 768
    monkeypatch.setattr(embeddings, "embed_one", fine)
    monkeypatch.setattr(web_search, "SEARXNG_URL", "http://searx.example.com:8080")


async def _by_id(db_conn, llm, env=None, http=None):
    return {c.id: c for c in await run_checks(db_conn, llm, {} if env is None else env, http=http or _searxng_ok())}


async def test_a_check_that_hangs_reports_itself_and_the_others_still_answer(db_conn, monkeypatch):   # Review Focus 1
    monkeypatch.setattr(console_overview, "CHECK_TIMEOUT_S", 0.2)

    async def never(_text):
        await asyncio.sleep(10)
    monkeypatch.setattr(embeddings, "embed_one", never)
    started = time.monotonic()
    checks = {c.id: c for c in await run_checks(db_conn, _router(), {}, http=_searxng_ok())}
    assert time.monotonic() - started < 2
    assert checks["embeddings"].state == "error"
    assert checks["embeddings"].summary.startswith("Didn't answer within")
    assert checks["providers"].state == "ok" and checks["web_search"].state == "ok"
    # the connection survived: a hung check never held it
    await db_conn.execute("SELECT 1")


async def test_a_check_that_raises_says_which_kind_of_failure(db_conn, monkeypatch):
    async def boom(_text):
        raise KeyError("x")
    monkeypatch.setattr(embeddings, "embed_one", boom)
    c = (await _by_id(db_conn, _router()))["embeddings"]
    assert (c.state, c.summary) == ("error", "The check failed: KeyError.")


async def test_checks_come_in_a_fixed_order(db_conn):
    checks = await run_checks(db_conn, _router(), {}, http=_searxng_ok())
    assert [c.id for c in checks] == ["providers", "reader", "embeddings", "docling", "web_search"]


async def test_providers_states(db_conn):
    two = _Router(_Provider("local", names=("a", "b")), _Provider("cloud", names=("c",), kind="openai"))
    c = (await _by_id(db_conn, two))["providers"]
    assert (c.state, c.title, c.section) == ("ok", "Model providers", "deployment")
    assert c.summary == "2 reachable: local (2 models), cloud (1 model)"

    some = _Router(_Provider("local"), _Provider("cloud", kind="openai", fails=True))
    c = (await _by_id(db_conn, some))["providers"]
    assert (c.state, c.summary) == ("warn", "cloud can't be reached.")

    none = _Router(_Provider("local", fails=True))
    c = (await _by_id(db_conn, none))["providers"]
    assert (c.state, c.summary) == ("error", "No provider can be reached.")

    c = (await _by_id(db_conn, _Router()))["providers"]
    assert (c.state, c.summary) == ("error", "No model provider is configured (INFERENCE_PROVIDERS or OLLAMA_URL).")


async def test_reader_states(db_conn):
    c = (await _by_id(db_conn, _router(), {"READER_MODEL": "chat"}))["reader"]
    assert (c.state, c.summary) == ("ok", "The chat model of each conversation (READER_MODEL=chat).")

    c = (await _by_id(db_conn, _router(), {"SUMMARIZE_MODEL": "m"}))["reader"]
    assert (c.state, c.summary) == ("ok", "m, from SUMMARIZE_MODEL.")

    c = (await _by_id(db_conn, _router()))["reader"]
    assert c.state == "warn"
    assert c.summary == ("The built-in fallback llama3.2:3b. Set READER_MODEL on the server, or READER_MODEL=chat.")

    c = (await _by_id(db_conn, _router(), {"READER_MODEL": "gone"}))["reader"]
    assert (c.state, c.summary) == ("error", "gone isn't on local: every reader call fails until it's pulled or READER_MODEL changes.")

    down = _Router(_Provider("local", fails=True))
    c = (await _by_id(db_conn, down, {"READER_MODEL": "m"}))["reader"]
    assert (c.state, c.summary) == ("error", "Can't reach local to check m.")

    c = (await _by_id(db_conn, _Router(), {"READER_MODEL": "m"}))["reader"]
    assert (c.state, c.summary) == ("error", "m can't be used: no Ollama provider is configured, so a bare model name has nowhere to go.")


async def test_embeddings_states(db_conn, monkeypatch):
    c = (await _by_id(db_conn, _router()))["embeddings"]
    assert c.state == "ok"
    assert c.summary == "nomic-embed-text at localhost:11434: 768 dimensions."

    async def wide(_text):
        return [0.1] * 1024
    monkeypatch.setattr(embeddings, "embed_one", wide)
    c = (await _by_id(db_conn, _router()))["embeddings"]
    assert (c.state, c.summary) == ("error", "nomic-embed-text returned 1024 dimensions; the database holds 768.")

    async def refuses(_text):
        raise httpx.ConnectError("no")
    monkeypatch.setattr(embeddings, "embed_one", refuses)
    c = (await _by_id(db_conn, _router()))["embeddings"]
    assert c.state == "error"
    assert c.summary == "Can't reach the embedding model nomic-embed-text at localhost:11434: documents can't be indexed or searched."


async def test_the_real_dimension_mismatch_error_still_names_the_count(db_conn, monkeypatch):
    async def mismatch(_text):
        raise ValueError("Embedding dim mismatch: model returned 1024, schema expects 768. Switching ...")
    monkeypatch.setattr(embeddings, "embed_one", mismatch)
    c = (await _by_id(db_conn, _router()))["embeddings"]
    assert c.summary == "nomic-embed-text returned 1024 dimensions; the database holds 768."


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
    assert [x["id"] for x in r.json()["checks"]] == ["providers", "reader", "embeddings", "docling", "web_search"]
