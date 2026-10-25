"""The Overview's health checks (admin console v2 spec §4.1): one line per part of the deployment that can be
quietly broken (a provider that's down, a reader model that was never pulled, an embedding model with the wrong
width), each answering "does this work right now?" from the same environment the server runs on.

Two rules keep the page usable when something is wrong:

* Every check runs under CHECK_TIMEOUT_S, all at once. A provider or SearXNG that accepts the connection and then
  goes silent costs the page that one line ("Didn't answer within 5 seconds."), not the whole answer.
* The database is read first, one query after another, before any check starts. A check that is cancelled by the
  timeout while a query is in flight leaves the connection unusable for the rest of the request, so no check gets
  `conn`: it gets the values it needs.
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping

import httpx

from ..chat import context as chat_context
from ..chat import reader
from ..chat.config import ChatConfig, get_chat_config
from ..chat.project_scope import ProjectScope, ScopeDoc
from ..chat.prompt import system_rules
from ..chat.tools import ToolContext, available_tools, tool_spec
from ..llm.router import ModelInfo, UnknownModel
from . import assistant_profile, chat_options, docling_convert, embeddings, model_router, web_search
from .deployment_info import NOT_A_URL, url_host
from .doc_search import ReadableDoc

CHECK_TIMEOUT_S = 5.0
SEARXNG_TIMEOUT_S = 3.0
EMBEDDING_PROBE = "Natural Reader embedding check."

_TITLES = {   # id -> (title, section), in the order the Overview shows them
    "providers": ("Model providers", "deployment"),
    "reader": ("Reader", "deployment"),
    "context": ("Context windows", None),
    "embeddings": ("Embeddings", "deployment"),
    "docling": ("Docling", "documents"),
    "web_search": ("Web search", "chat"),
}


@dataclass(frozen=True)
class Check:
    id: str
    state: str      # "ok" | "warn" | "error" | "off"
    title: str
    summary: str
    section: str | None
    items: tuple[dict, ...] = ()

    def to_json(self) -> dict:
        return {"id": self.id, "state": self.state, "title": self.title, "summary": self.summary,
                "section": self.section, "items": list(self.items)}


def _check(id: str, state: str, summary: str) -> Check:
    title, section = _TITLES[id]
    return Check(id, state, title, summary, section)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


async def _providers(llm) -> Check:
    if not llm.providers:
        return _check("providers", "error", "No model provider is configured (INFERENCE_PROVIDERS or OLLAMA_URL).")
    models, failed = await llm.list_models()
    counts: dict[str, int] = {}
    for m in models:
        counts[m.provider] = counts.get(m.provider, 0) + 1
    reachable = [name for name in llm.providers if name not in failed]
    if not reachable:
        return _check("providers", "error", "No provider can be reached.")
    if failed:
        return _check("providers", "warn", f"{', '.join(failed)} can't be reached.")
    listing = ", ".join(f"{name} ({_plural(counts.get(name, 0), 'model')})" for name in reachable)
    return _check("providers", "ok", f"{len(reachable)} reachable: {listing}")


async def _reader(llm, env: Mapping[str, str]) -> Check:
    model = reader.load_reader_config(env).model
    if model == "chat":
        return _check("reader", "ok", "The chat model of each conversation (READER_MODEL=chat).")
    try:
        provider, name = llm.resolve(model)
    except UnknownModel:
        return _check("reader", "error", f"{model} can't be used: no Ollama provider is configured, "
                                         "so a bare model name has nowhere to go.")
    where = provider.config.name
    # The provider's own listing, not Router.is_listed: that one reports an unreachable provider as listed.
    try:
        names = await provider.list_models()
    except Exception:
        return _check("reader", "error", f"Can't reach {where} to check {name}.")
    if name not in names:
        return _check("reader", "error", f"{name} isn't on {where}: every reader call fails until it's pulled "
                                         "or READER_MODEL changes.")
    var = reader.reader_model_source(env)
    if var is None:
        return _check("reader", "warn", f"The built-in fallback {name}. Set READER_MODEL on the server, "
                                        "or READER_MODEL=chat.")
    return _check("reader", "ok", f"{name}, from {var}.")


_DIM_MISMATCH = re.compile(r"model returned (\d+)")


async def _embeddings(env: Mapping[str, str], stale: int) -> Check:
    cfg = model_router.load_inference_config(env)
    model, host = cfg.embed_model, url_host(cfg.ollama_url)
    dim = embeddings.EMBEDDING_DIM
    try:
        # No `dimensions` asked for: the length that comes back is the model's own.
        got = len(await embeddings.embed_one(EMBEDDING_PROBE))
    except httpx.HTTPError:
        return _check("embeddings", "error", f"Can't reach the embedding model {model} at {host}: "
                                             "documents can't be indexed or searched.")
    except ValueError as e:   # embed_one's own refusal of a wrong width carries the count
        found = _DIM_MISMATCH.search(str(e))
        if not found:
            raise
        got = int(found.group(1))
    if got != dim:
        return _check("embeddings", "error", f"{model} returned {got} dimensions; the database holds {dim}.")
    summary = f"{model} at {host}: {dim} dimensions."
    if stale:
        was = "was" if stale == 1 else "were"
        each = "it rebuilds" if stale == 1 else "each rebuilds"
        return _check("embeddings", "warn", f"{summary} {_plural(stale, 'document')} {was} indexed under an older "
                                            f"embedding setup; {each} the next time someone uses it.")
    return _check("embeddings", "ok", summary)


async def _docling() -> Check:
    if not docling_convert.is_enabled():
        return _check("docling", "off", "Off on this server (DOCLING_ENABLED).")
    if importlib.util.find_spec("docling") is None:
        return _check("docling", "error", "DOCLING_ENABLED is on, but Docling isn't installed: every conversion fails.")
    return _check("docling", "ok", "On.")


async def _web_search(http: httpx.AsyncClient, mode: str) -> Check:
    url = web_search.SEARXNG_URL
    if not url:
        return _check("web_search", "off", "Not set up on this server (SEARXNG_URL).")
    if mode == "off":
        return _check("web_search", "off", "Turned off in Chat options.")
    host = url_host(url)
    if host == NOT_A_URL:
        return _check("web_search", "error", "SEARXNG_URL isn't a valid URL: web search fails.")
    broken = _check("web_search", "error", f"SearXNG at {host} doesn't answer: web search fails.")
    try:
        resp = await http.get(f"{url}/healthz", timeout=SEARXNG_TIMEOUT_S)
    except (httpx.HTTPError, httpx.InvalidURL):
        return broken
    if resp.status_code != 200:
        return broken
    return _check("web_search", "ok", f"SearXNG at {host} answers.")


def is_ollama_cloud(name: str) -> bool:
    return name.endswith(("-cloud", ":cloud"))


def window_for(model: ModelInfo, loaded: dict[str, int]) -> tuple[int | None, str]:
    """The context window `model` gets, and where the number comes from. A local Ollama model's /api/show
    maximum is never reported as its window: Ollama loads it at 4,096 tokens on a machine with under 24 GiB
    of VRAM, and the app trims history against whatever number it believes."""
    maximum = model.capabilities.context_window
    if model.kind == "ollama":
        if is_ollama_cloud(model.name):
            if maximum:
                return maximum, "The model's maximum (Ollama cloud)."
        else:
            mine = model.name.removesuffix(":latest")
            for name, size in loaded.items():
                if name.removesuffix(":latest") == mine:
                    return size, "Loaded now (Ollama)."
            return None, ("Unknown: Ollama picks the size when it loads the model "
                          "(4,096 tokens on a machine with under 24 GiB of VRAM).")
    if maximum:
        return maximum, "Reported by the provider."
    return None, "Unknown: the provider doesn't report one."


def project_turn_tokens(cfg: ChatConfig, web_mode: str, profile: str = "") -> int:
    """About how many tokens a project-chat turn needs before any history: the rules, the tools' specs, a full
    project map and a full tool-result budget (4 characters a token), plus the reply's reserve."""
    ready = ReadableDoc("sample", "Sample document", "indexed", 10)
    scope = ProjectScope("sample", "Sample project", (ScopeDoc(1, ready, "Sample document", False),))
    ctx = ToolContext("sample", None, cfg=cfg, project=scope, web_mode=web_mode)
    tools = available_tools(ctx)
    rules = system_rules(None, tools, has_pins=True, prefetch=True, profile=profile, has_map=True, project=scope,
                         today=chat_context.today_line(datetime.now(timezone.utc), "UTC"))
    spec_chars = sum(len(json.dumps(dataclasses.asdict(tool_spec(t, ctx)))) for t in tools)
    chars = len(rules) + spec_chars + cfg.project_map_chars + cfg.tool_result_budget_chars
    return math.ceil(chars / 4) + cfg.reply_reserve_tokens


async def _context(llm, env: Mapping[str, str], web_mode: str, profile: str) -> Check:
    # Config parsing lives here, not up front: one malformed value (INFERENCE_DAILY_TOKEN_BUDGET=lots) must cost
    # this check's line, not the page an admin uses to find it.
    cfg = get_chat_config()
    embed_model = model_router.load_inference_config(env).embed_model
    models, _failed = await llm.list_models()
    models = model_router.offered_chat_models(models, embed_model)
    if not models:
        return _check("context", "warn", "No chat models are listed.")
    ollama = [p for p in llm.providers.values() if p.config.kind == "ollama"]
    lengths = await asyncio.gather(*(p.loaded_context_lengths() for p in ollama))
    loaded = {p.config.name: got for p, got in zip(ollama, lengths)}
    needed = project_turn_tokens(cfg, web_mode, profile)
    items = []
    for m in models:
        window, source = window_for(m, loaded.get(m.provider, {}))
        items.append({"model": m.id, "window": window, "source": source,
                      "enough": window is not None and window >= needed})
    fits = sum(1 for i in items if i["enough"])
    summary = (f"{fits} of {len(items)} chat models have room for a project-chat turn here "
               f"(about {needed:,} tokens).")
    title, section = _TITLES["context"]
    return Check("context", "ok" if fits == len(items) else "warn", title, summary, section, tuple(items))


async def _bounded(id: str, run: Callable[[], Awaitable[Check]]) -> Check:
    try:
        return await asyncio.wait_for(run(), timeout=CHECK_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _check(id, "error", f"Didn't answer within {CHECK_TIMEOUT_S:g} seconds.")
    except Exception as e:
        return _check(id, "error", f"The check failed: {type(e).__name__}.")


async def run_checks(conn, llm, env: Mapping[str, str] = os.environ,
                     http: httpx.AsyncClient | None = None) -> list[Check]:
    # Every database read first, in turn, outside any timeout (see the module docstring).
    web_mode = (await chat_options.load_chat_options(conn, env)).web_search
    try:
        current = embeddings.current_profile()
    except ValueError:   # a malformed INFERENCE_* value: the embeddings and context checks report it themselves
        stale = 0
    else:
        cur = await conn.execute("SELECT count(*) FROM documents WHERE state = 'indexed' "
                                 "AND embedding_profile IS DISTINCT FROM %s", (current,))
        stale = (await cur.fetchone())[0]
    profile = (await assistant_profile.load_profile(conn)).text

    owned = http is None
    client = httpx.AsyncClient() if owned else http
    try:
        return list(await asyncio.gather(
            _bounded("providers", lambda: _providers(llm)),
            _bounded("reader", lambda: _reader(llm, env)),
            _bounded("context", lambda: _context(llm, env, web_mode, profile)),
            _bounded("embeddings", lambda: _embeddings(env, stale)),
            _bounded("docling", _docling),
            _bounded("web_search", lambda: _web_search(client, web_mode)),
        ))
    finally:
        if owned:
            await client.aclose()
