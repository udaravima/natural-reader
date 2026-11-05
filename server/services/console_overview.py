"""The Overview's health checks (admin console v2 spec §4.1): one line per part of the deployment that can be
quietly broken (a provider that's down, a reader model that was never pulled, an embedding model with the wrong
width), each answering "does this work right now?" from the same environment the server runs on.

Two rules keep the page usable when something is wrong:

* Every check runs under CHECK_TIMEOUT_S, all at once. A provider or SearXNG that accepts the connection and then
  goes silent costs the page that one line ("Didn't answer within 5 seconds."), not the whole answer.
* The database is read first, one query after another, before any check starts. A check that is cancelled by the
  timeout while a query is in flight leaves the connection unusable for the rest of the request, so no check gets
  `conn`: it gets the values it needs. The Models settings (the reader, context sizes, declared windows, the chat
  selection) are one of those reads; if it fails the checks use the environment's answer.
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping

import httpx

from ..chat import context as chat_context
from ..chat.config import ChatConfig, get_chat_config
from ..chat.project_scope import ProjectScope, ScopeDoc
from ..chat.prompt import system_rules
from ..chat.tools import ToolContext, available_tools, tool_spec
from ..llm.providers.base import ProviderConfig
from ..llm.router import ModelInfo, UnknownModel
from .. import chat_voice, google_cloud
from . import (assistant_profile, chat_options, docling_convert, embedding_console, embedding_state, embeddings, model_router,
               model_settings, rebuild_queue, web_search)
from ..speech import catalogue as speech_catalogue, service as speech_service
from ..speech.languages import WHISPER_LANGUAGES
from .deployment_info import NOT_A_URL, url_host
from .doc_search import ReadableDoc
from .inference_limits import InferenceLimits, clamp, load_inference_limits
from .model_settings import is_ollama_cloud   # moved there (locality needs it); re-exported here

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 5.0
SEARXNG_TIMEOUT_S = 3.0
EMBEDDING_PROBE = "Natural Reader embedding check."

_TITLES = {   # id -> (title, section), in the order the Overview shows them
    "providers": ("Model providers", "models"),
    "reader": ("Reader", "models"),
    "context": ("Context windows", "models"),
    "embeddings": ("Embeddings", "models"),     # Models → Embeddings chooses the model
    "docling": ("Docling", "documents"),
    "web_search": ("Web search", "chat"),
    "voice_input": ("Voice input", "chat"),
    "chat_voice": ("Chat voice", "chat"),
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


async def _offered_models(llm, env: Mapping[str, str], ms: model_settings.ModelsSettings) -> list[ModelInfo]:
    """The chat models a person can pick: what the providers list, minus the embedding model, narrowed to the
    console's selection (which can only narrow the environment's list)."""
    embed_model = model_router.load_inference_config(env).embed_model
    models, _failed = await llm.list_models()
    return model_settings.offered(model_router.offered_chat_models(models, embed_model), ms)


_QUOTA_HINT = (" Reader calls then count against a cloud model's quota; a small local reader, chosen in Models, "
               "keeps them off it.")


async def _quota_hint(llm, ms: model_settings.ModelsSettings, env: Mapping[str, str]) -> str:
    """The cloud-quota hint for a reader that is the chat model: a cost question (is_local), not whether a model
    needs a context size (sizes_context). Best effort under its own bound: a provider that hangs, or a malformed
    INFERENCE_* value, costs the hint and never the reader line, which is fine without it."""
    try:
        offered = await asyncio.wait_for(_offered_models(llm, env, ms), timeout=CHECK_TIMEOUT_S / 2)
        if any(not model_settings.is_local(llm.providers[m.provider].config, m.name, ms.provider_locality)
               for m in offered):
            return _QUOTA_HINT
    except (Exception, asyncio.TimeoutError):
        logger.warning("The Overview's reader line couldn't tell whether a cloud model is offered", exc_info=True)
    return ""


async def _reader(llm, ms: model_settings.ModelsSettings, env: Mapping[str, str]) -> Check:
    model = ms.reader
    if model == "chat":
        text = ("The chat model of each conversation (the default)." if ms.reader_source == "default"
                else "The chat model of each conversation.")
        return _check("reader", "ok", text + await _quota_hint(llm, ms, env))
    try:
        provider, name = llm.resolve(model)
    except UnknownModel:
        return _check("reader", "error", f"{model} can't be used: no Ollama provider is configured, "
                                         "so a bare model name has nowhere to go.")
    where = provider.config.name
    # From any source: an env change can narrow the list under a reader set in Models (ruling P12)
    if not llm.is_allowed(model):
        return _check("reader", "error", f"{name} isn't in this deployment's model list. "
                                         "Choose another reader in Models.")
    # The provider's own listing, not Router.is_listed: that one reports an unreachable provider as listed.
    try:
        names = await provider.list_models()
    except Exception:
        return _check("reader", "error", f"Can't reach {where} to check {name}.")
    if name not in names:
        return _check("reader", "error", f"{name} isn't on {where}: every reader call fails until it's pulled "
                                         "or another reader is chosen in Models.")
    if ms.reader_source == "admin":
        return _check("reader", "ok", f"{name}, set in Models.")
    return _check("reader", "ok", f"{name}, from {ms.reader_env or 'READER_MODEL'}.")


_STAKE = "documents can't be indexed or searched."


async def _ollama_embedder(http: httpx.AsyncClient, e: embeddings.Embedder, state: Mapping | None) -> Check | str:
    """The model in use on Ollama, from two lists and nothing loaded: /api/tags (is it pulled?) and /api/ps (is
    it in memory?). Its size comes from the state row: checked once, when it became the model in use or by the
    background loop (embedding_migration.tick), never on a visit to this page (the owner, 2026-11-05). Returns the
    line's opening sentence, or the error Check."""
    host = url_host(e.provider.url)
    try:
        resp = await http.get(f"{e.provider.url}/api/tags")
    except (httpx.HTTPError, httpx.InvalidURL) as err:
        return _check("embeddings", "error", f"Can't reach {e.provider.name} at {host} for {e.model} "
                                             f"({type(err).__name__}): {_STAKE}")
    if resp.status_code != 200:
        return _check("embeddings", "error", f"{e.provider.name} at {host} answered HTTP {resp.status_code}: {_STAKE}")
    try:
        names = [m.get("name") or "" for m in resp.json().get("models", [])]
    except (ValueError, AttributeError, TypeError):
        return _check("embeddings", "error", f"{e.provider.name} at {host} answered with something that isn't "
                                             f"a model list: {_STAKE}")
    if not any(n.removesuffix(":latest") == e.model.removesuffix(":latest") for n in names):
        return _check("embeddings", "error", f"{e.model} isn't on {e.provider.name} at {host} (ollama pull "
                                             f"{e.model}): {_STAKE}")
    state = state or {}
    if state.get("current_error"):
        return _check("embeddings", "error", f"{state['current_error']}: {_STAKE}")
    if state.get("checked") != embedding_state.vector_identity(e):
        return f"{e.model} at {host}: pulled; its size hasn't been checked yet (the server checks it once, in the " \
               "background)."
    if await embeddings.is_loaded(e.provider, e.model, http):
        return f"{e.model} at {host}: {embeddings.EMBEDDING_DIM} dimensions, in memory now."
    return (f"{e.model} at {host}: {embeddings.EMBEDDING_DIM} dimensions. Not in memory now: it loads when a document "
            "is indexed or searched.")


async def _remote_embedder(http: httpx.AsyncClient, e: embeddings.Embedder) -> Check | str:
    """An OpenAI-compatible embedder: one sentence embedded. Nothing is loaded on this machine for it, and a
    provider's /models can't be trusted to list embedding models (OpenRouter's lists chat models).

    The request is its own instead of going through embed_texts: that one waits on the shared indexing
    semaphore, so right after an upload a healthy model would read as "didn't answer"."""
    dim = embeddings.EMBEDDING_DIM
    try:
        vectors = await embeddings.request_vectors(e, [EMBEDDING_PROBE], client=http)
    except embeddings.EmbeddingError as err:
        return _check("embeddings", "error", f"{err}: {_STAKE}")
    vec = vectors[0]
    got = len(vec) if isinstance(vec, list) else 0
    host = url_host(e.provider.url)
    if not got:
        return _check("embeddings", "error", f"{e.model} at {host} answered, but returned no embedding: {_STAKE}")
    if got != dim:
        return _check("embeddings", "error", f"{e.model} returned {got} dimensions; the database holds {dim}.")
    return f"{e.model} at {host}: {dim} dimensions."


async def _embeddings(http: httpx.AsyncClient, e: embeddings.Embedder, state: Mapping | None, stale: int,
                      queue: Mapping[str, int]) -> Check:
    """The model in use (what the stored vectors were made with) is there and gives 768 numbers; and how far a
    re-embed and the rebuild queue are, read from the database."""
    head = await (_ollama_embedder(http, e, state) if e.provider.kind == "ollama" else _remote_embedder(http, e))
    if isinstance(head, Check):
        return head
    notes, warn = [], False
    migration = embedding_console.migration_view(state)
    if migration["state"] in ("building", "switching"):
        notes.append(f"Re-embedding for {migration['model']}: {migration['filled']:,} of {migration['total']:,} "
                     "passages.")
    elif migration["state"] == "failed":
        warn = True
        notes.append(f"Re-embedding for {migration['model']} is paused: {migration['error']}" if migration["model"]
                     else f"The chosen embedding model isn't used: {migration['error']}")
    if stale:
        warn = True
        was = "was" if stale == 1 else "were"
        each = "it rebuilds" if stale == 1 else "each rebuilds"
        notes.append(f"{_plural(stale, 'document')} {was} indexed under an older embedding setup; {each} the next "
                     "time someone uses it.")
    waiting = queue.get("queued", 0) + queue.get("running", 0)
    if waiting:
        notes.append(f"{_plural(waiting, 'document')} waiting to be rebuilt.")
    if queue.get("failed"):
        warn = True
        notes.append(f"{_plural(queue['failed'], 'rebuild')} failed and will be retried.")
    summary = " ".join([head, *notes])
    return _check("embeddings", "warn" if warn else "ok", summary)


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


def window_for(model: ModelInfo, loaded: dict[str, int], ms: model_settings.ModelsSettings,
               provider_cfg: ProviderConfig, env: Mapping[str, str],
               limits: InferenceLimits) -> tuple[int | None, str]:
    """The context window `model` gets, and where the number comes from. A local Ollama model's /api/show
    maximum is never reported as its window: Ollama loads it at 4,096 tokens on a machine with under 24 GiB
    of VRAM, and the app trims history against whatever number it believes. So a model the app sizes
    (`sizes_context`: any Ollama you host, whatever its host) gets what a request to it sends, and a model
    that is loaded gets what it was loaded with. First match wins: loaded; the size the app sends; Ollama
    cloud's maximum; what the provider reports; what the admin declared; unknown."""
    maximum = model.capabilities.context_window
    if model.kind == "ollama" and not is_ollama_cloud(model.name):
        mine = model.name.removesuffix(":latest")
        for name, size in loaded.items():
            if name.removesuffix(":latest") == mine:
                return size, "Loaded now (Ollama)."
    if model_settings.sizes_context(provider_cfg, model.name):
        size, where = model_settings.deployment_num_ctx(ms, model.id, limits)
        if size is not None:
            return size, {"model": "The context size set for this model in Models.",
                          "admin": "The deployment's context size, set in Models.",
                          "env": "The deployment's context size, from INFERENCE_LOCAL_NUM_CTX."}[where]
        window = clamp(model_settings.DEFAULT_LOCAL_NUM_CTX, None, limits)[0]
        return window, f"{window:,} tokens unless a person sets their own in Settings (no deployment size is set)."
    if model.kind == "ollama" and is_ollama_cloud(model.name) and maximum:
        return maximum, "The model's maximum (Ollama cloud)."
    if maximum:
        return maximum, "Reported by the provider."
    declared, where = model_settings.declared_window(ms, model.id, model.provider, env)
    if declared is not None:
        if where == "admin":
            return declared, "Declared in Models."
        return declared, f"From INFERENCE_{model.provider.upper().replace('-', '_')}_CONTEXT_WINDOW."
    return None, "Unknown: the provider doesn't report one. Declare it in Models."


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


async def _context(llm, ms: model_settings.ModelsSettings, env: Mapping[str, str], web_mode: str,
                   profile: str) -> Check:
    # Config parsing lives here, not up front: one malformed value (INFERENCE_DAILY_TOKEN_BUDGET=lots) must cost
    # this check's line, not the page an admin uses to find it.
    cfg = get_chat_config()
    limits = load_inference_limits(env)
    embed_model = model_router.load_inference_config(env).embed_model
    models, _failed = await llm.list_models()
    models = model_router.offered_chat_models(models, embed_model)
    if not models:
        return _check("context", "warn", "No chat models are listed.")
    models = model_settings.offered(models, ms)
    if not models:
        return _check("context", "warn", "None of the chat models chosen in Models is listed by a provider: "
                                         "nobody can chat.")
    ollama = [p for p in llm.providers.values() if p.config.kind == "ollama"]
    lengths = await asyncio.gather(*(p.loaded_context_lengths() for p in ollama))
    loaded = {p.config.name: got for p, got in zip(ollama, lengths)}
    needed = project_turn_tokens(cfg, web_mode, profile)
    items = []
    for m in models:
        window, source = window_for(m, loaded.get(m.provider, {}), ms, llm.providers[m.provider].config, env, limits)
        items.append({"model": m.id, "window": window, "source": source,
                      "enough": window is not None and window >= needed})
    fits = sum(1 for i in items if i["enough"])
    summary = (f"{fits} of {len(items)} chat models have room for a project-chat turn here "
               f"(about {needed:,} tokens).")
    title, section = _TITLES["context"]
    return Check("context", "ok" if fits == len(items) else "warn", title, summary, section, tuple(items))


async def _voice_input(options, env: Mapping[str, str] = os.environ) -> Check:
    """Off when the admin hasn't switched it on, and then nothing is built or probed. On: the engine's own
    status (an exception becomes the type-named error line from _bounded)."""
    if not options.voice_input:
        return _check("voice_input", "off", "Off. Admin → Chat options turns it on.")
    engine = speech_service.get_transcriber(wanted=speech_catalogue.wanted(options, env),
                                            engine=speech_service.chosen_engine(options, env),
                                            recognizers=speech_service.recognizer_names(options, env))
    status = await engine.status()
    if status.state == "ready":
        summary = f"{engine.name} · {engine.model} · runs on {engine.where}"
        hears = getattr(engine, "auto_hears", None)
        if hears:
            summary += (f". Auto hears only {WHISPER_LANGUAGES.get(hears, hears)} with this recognizer: people "
                        "should pick their language in Settings, or set Speech language in Defaults. For Auto "
                        "across languages, use a chirp_3 recognizer with the language auto.")
        return _check("voice_input", "ok", summary)
    if status.state == "loading":
        return _check("voice_input", "warn", f"Getting ready: downloading or loading {engine.model}.")
    return _check("voice_input", "error", status.detail or "The speech engine isn't working.")


async def _chat_voice(options, env: Mapping[str, str] = os.environ) -> Check:
    """Who reads the chat and Talk. Google's credentials are checked (a token costs nothing); nothing is spoken,
    since every sentence is billed."""
    voice = chat_voice.current(options, env)
    if voice.engine == "kokoro":
        return _check("chat_voice", "ok", "Kokoro on this server reads the chat and Talk, in each person's own voice.")
    try:
        await google_cloud.access_token()
    except google_cloud.GoogleAuthError as e:
        return _check("chat_voice", "error", str(e))
    model = (env.get("GOOGLE_TTS_MODEL") or "").strip()
    return _check("chat_voice", "ok", f"Google Gemini-TTS ({model}), voice {voice.voice}: the chat's replies and "
                                      "Talk are sent to Google Cloud to be spoken.")


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
    options = await chat_options.load_chat_options(conn, env)
    web_mode = options.web_search
    try:
        embedder = await embedding_state.current(conn, env)
    except ValueError:   # a malformed INFERENCE_* value: the embeddings and context checks report it themselves
        embedder, stale = None, 0
    else:
        cur = await conn.execute("SELECT count(*) FROM documents WHERE state = 'indexed' "
                                 "AND embedding_profile IS DISTINCT FROM %s", (embeddings.profile_of(embedder),))
        stale = (await cur.fetchone())[0]
    state = await embedding_state.read_state(conn)
    queue = await rebuild_queue.queue_counts(conn)
    profile = (await assistant_profile.load_profile(conn)).text
    try:
        ms = await model_settings.load_models_settings(conn, env)
    except Exception:   # fail open: the environment's answer is better than no Overview
        logger.warning("The models settings couldn't be read; the Overview uses the environment's", exc_info=True)
        ms = model_settings.from_env(env)

    owned = http is None
    client = httpx.AsyncClient() if owned else http
    try:
        return list(await asyncio.gather(
            _bounded("providers", lambda: _providers(llm)),
            _bounded("reader", lambda: _reader(llm, ms, env)),
            _bounded("context", lambda: _context(llm, ms, env, web_mode, profile)),
            _bounded("embeddings", lambda: _embeddings(client, embedder or embedding_state.env_target(env), state,
                                                       stale, queue)),
            _bounded("docling", _docling),
            _bounded("web_search", lambda: _web_search(client, web_mode)),
            _bounded("voice_input", lambda: _voice_input(options, env)),
            _bounded("chat_voice", lambda: _chat_voice(options, env)),
        ))
    finally:
        if owned:
            await client.aclose()
