"""
Embeddings through any configured provider (admin console spec §4.2.3).

An `Embedder` says which provider serves the vectors, which model, and how texts are dressed: the
`dimensions` asked for (only when the probe found it needed) and the model's two prefixes. Texts go in
batches of up to EMBED_BATCH_SIZE per request: `ollama` kind to `POST {url}/api/embed`, `openai` kind to
`POST {url}/embeddings` (its URL already ends in `/v1`). Both take an `input` array and return the
vectors in order (the openai kind by `index`).

Every answer is checked for its count and length. A wrong length is an error rather than a vector
stored in a 768-number column it doesn't belong to.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg_pool import PoolTimeout

from ..db import get_pool
from ..llm.providers.base import ProviderConfig, auth_headers
from .extract import embedding_max_chars

logger = logging.getLogger(__name__)

EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))
# One request carries up to EMBED_BATCH_SIZE texts, so it gets longer than one text used to (30 s).
EMBEDDING_TIMEOUT_S = float(os.environ.get("EMBEDDING_TIMEOUT_S", "120"))
# Ollama's default context for nomic-embed-text is often 2048 tokens.
# At ~4 chars/token the safe ceiling is ~2 000 chars.  Override via env
# if you've set a larger context (e.g. `num_ctx` in a Modelfile).
MAX_INPUT_CHARS = embedding_max_chars(os.environ)   # one parse, shared with the chunker
MAX_CONCURRENCY = int(os.environ.get("EMBEDDING_MAX_CONCURRENCY", "4"))   # requests in flight, per process
EMBED_BATCH_SIZE = 32

_client: httpx.AsyncClient | None = None
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


class EmbeddingError(Exception):
    """The provider refused, couldn't be reached, or answered with the wrong count or length. The message
    names the provider and model, never a key."""


@dataclass(frozen=True)
class Embedder:
    provider: ProviderConfig
    model: str
    dimensions: int | None      # sent as `dimensions` only when set (a shortened Matryoshka model)
    document_prefix: str
    query_prefix: str


async def start_client() -> None:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT_S)


async def stop_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("Embedding HTTP client not started — call start_client() first")
    return _client


def host_of(url: str) -> str:
    """host[:port] of a provider URL, never its credentials or path."""
    try:
        parts = urlsplit(url)
        return f"{parts.hostname}:{parts.port}" if parts.port else (parts.hostname or "")
    except ValueError:
        return ""


def _at(e: Embedder) -> str:
    return f"{e.provider.name} at {host_of(e.provider.url)}"


def _where(e: Embedder) -> str:
    return f"{e.model} on {_at(e)}"


async def request_vectors(e: Embedder, texts: list[str], *, client: httpx.AsyncClient | None = None,
                          extra: dict[str, Any] | None = None) -> list[list[float]]:
    """One request for `texts`, checked for count only (the probe reads the native length from it).
    `extra` adds fields to the body (the Overview's `keep_alive`)."""
    body: dict[str, Any] = {"model": e.model, "input": texts}
    if e.dimensions:
        body["dimensions"] = e.dimensions
    body.update(extra or {})
    if e.provider.kind == "ollama":
        url, headers = f"{e.provider.url}/api/embed", {}
    else:
        url, headers = f"{e.provider.url}/embeddings", auth_headers(e.provider)
    try:
        resp = await (client or _get_client()).post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as err:   # reachable, but refusing
        code = err.response.status_code
        if code == 404:     # Ollama: never pulled; an OpenAI-compatible server: no such model
            raise EmbeddingError(f"{e.model} isn't on {_at(e)} (HTTP 404)") from None
        if code == 501:     # Ollama: a chat model asked for embeddings
            raise EmbeddingError(f"{_where(e)} doesn't make embeddings (HTTP 501)") from None
        raise EmbeddingError(f"{_where(e)} answered HTTP {code}") from None
    except httpx.HTTPError as err:
        raise EmbeddingError(f"Can't reach {_at(e)} for {e.model} ({type(err).__name__})") from None
    except ValueError:
        raise EmbeddingError(f"{_where(e)} answered with something that isn't JSON") from None
    try:
        if e.provider.kind == "ollama":
            vectors = data["embeddings"]
        else:
            vectors = [item["embedding"] for item in sorted(data["data"], key=lambda item: item["index"])]
    except (KeyError, TypeError):
        raise EmbeddingError(f"{_where(e)} answered without embeddings") from None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        got = len(vectors) if isinstance(vectors, list) else 0
        raise EmbeddingError(f"{_where(e)} returned {got} vectors for {len(texts)} texts")
    return vectors


def _cap(text: str) -> str:
    if len(text) <= MAX_INPUT_CHARS:
        return text
    logger.warning("Truncating text from %d to %d chars for embedding", len(text), MAX_INPUT_CHARS)
    return text[:MAX_INPUT_CHARS]


async def _one_request(e: Embedder, batch: list[str]) -> list[list[float]]:
    async with _semaphore:
        vectors = await request_vectors(e, [_cap(t) for t in batch])
    for vec in vectors:
        if not isinstance(vec, list) or len(vec) != EMBEDDING_DIM:
            got = len(vec) if isinstance(vec, list) else 0
            raise EmbeddingError(f"{_where(e)} returned {got} numbers; the database holds {EMBEDDING_DIM}")
    return vectors


def _batches(texts: list[str]) -> list[list[str]]:
    return [texts[i:i + EMBED_BATCH_SIZE] for i in range(0, len(texts), EMBED_BATCH_SIZE)]


async def embed_texts(e: Embedder, texts: list[str]) -> list[list[float]]:
    """Every text's vector, in order; raises EmbeddingError if any request fails. No prefix is added."""
    out = await asyncio.gather(*(_one_request(e, b) for b in _batches(texts)))
    return [vec for batch in out for vec in batch]


async def embed_batch(texts: list[str], e: Embedder | None = None) -> list[list[float] | None]:
    """Like embed_texts, but a failed request marks its own texts None instead of failing the rest, so
    indexing keeps what it got and skips the gaps."""
    e = e or await current_embedder()

    async def safe(batch: list[str]) -> list[list[float] | None]:
        try:
            return await _one_request(e, batch)
        except EmbeddingError as err:
            logger.warning("Failed to embed %d text(s): %s", len(batch), err)
            return [None] * len(batch)

    out = await asyncio.gather(*(safe(b) for b in _batches(texts)))
    return [vec for batch in out for vec in batch]


LIST_TIMEOUT_S = 5.0


async def list_model_names(provider: ProviderConfig, *, timeout: float = LIST_TIMEOUT_S) -> list[str]:
    """Every model the provider lists (Ollama's /api/tags, an OpenAI-compatible /models), for the console's
    picker; the chat allow-lists don't apply to an embedding model. [] when it doesn't answer: the picker then
    takes a typed name."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            if provider.kind == "ollama":
                resp = await http.get(f"{provider.url}/api/tags")
                resp.raise_for_status()
                return [m["name"] for m in resp.json().get("models", []) if isinstance(m, dict) and m.get("name")]
            resp = await http.get(f"{provider.url}/models", headers=auth_headers(provider))
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", []) if isinstance(m, dict) and m.get("id")]
    except (httpx.HTTPError, ValueError, AttributeError) as err:
        logger.info("Couldn't list %s's models: %s", provider.name, type(err).__name__)
        return []


# ---------- the probe: may this model fill a 768-number column? (spec §4.2.4) ----------

PROBE_SENTENCE = "The quick brown fox jumps over the lazy dog."
PROBE_TIMEOUT_S = 60.0   # the first call may load the model


@dataclass
class ProbeResult:
    ok: bool
    native: int | None       # numbers the model gives by itself; None when it didn't answer
    dimensions: int | None   # the `dimensions` to send from now on; None = send none
    reason: str              # one plain sentence, shown in the console


async def probe(provider: ProviderConfig, model: str, *, client: httpx.AsyncClient | None = None) -> ProbeResult:
    """Two steps. First without `dimensions`, to learn the native size, since a returned length can't be
    trusted once `dimensions` is sent: Ollama cuts any vector to it and renormalizes, with no error. Then,
    only for a family trained to be shortened, once more asking for 768."""
    from .embedding_models import KNOWN, family
    own = client is None
    http = client or httpx.AsyncClient(timeout=PROBE_TIMEOUT_S)
    try:
        try:
            first = (await request_vectors(Embedder(provider, model, None, "", ""), [PROBE_SENTENCE],
                                           client=http))[0]
        except EmbeddingError as err:
            return ProbeResult(False, None, None, f"{err}.")
        native = len(first) if isinstance(first, list) else 0
        if native == EMBEDDING_DIM:
            return ProbeResult(True, native, None, f"{model} gives {native} numbers, the size the library holds.")
        fam = family(model)
        if native < EMBEDDING_DIM or fam is None or not fam.shortenable:
            why = ("it can't be lengthened" if native < EMBEDDING_DIM else
                   "it isn't in a family trained to be shortened (" + ", ".join(KNOWN) + "), and cutting "
                   "its vectors would make search worse with no error")
            return ProbeResult(False, native, None,
                               f"{model} gives {native} numbers and the library holds {EMBEDDING_DIM}: {why}.")
        try:
            short = (await request_vectors(Embedder(provider, model, EMBEDDING_DIM, "", ""), [PROBE_SENTENCE],
                                           client=http))[0]
        except EmbeddingError as err:
            return ProbeResult(False, native, None, f"Asked for {EMBEDDING_DIM} numbers, {err}.")
        got = len(short) if isinstance(short, list) else 0
        if got != EMBEDDING_DIM:
            return ProbeResult(False, native, None, f"{model} gives {native} numbers, and asked for {EMBEDDING_DIM} "
                                                    f"it still gave {got}: this server doesn't shorten it.")
        return ProbeResult(True, native, EMBEDDING_DIM,
                           f"{model} gives {native} numbers and is trained to be shortened: it will be asked "
                           f"for {EMBEDDING_DIM}.")
    finally:
        if own:
            await http.aclose()


def env_embedder(env=None) -> Embedder:
    """The embedder the environment alone describes (EMBEDDING_PROVIDER, EMBEDDING_MODEL; else the Ollama at
    OLLAMA_URL with nomic-embed-text): what applies when the database can't be read."""
    from .embedding_state import env_target
    return env_target(env if env is not None else os.environ)


async def current_embedder() -> Embedder:
    """What the stored vectors were made with (embedding_state.current), read on a short pooled connection.
    Without a database (a worker still starting, a unit test), the environment's embedder."""
    from .embedding_state import current
    try:
        async with get_pool().connection() as conn:
            return await current(conn)
    except (RuntimeError, psycopg.Error, PoolTimeout) as err:
        logger.debug("Embedding with the environment's model: the database can't be read (%s)", type(err).__name__)
        return env_embedder()


# ---------- what is embedded, and how (v2.3 Task E) ----------

async def embed_query(text: str, e: Embedder | None = None) -> list[float]:
    """A question or search, with the current model's query prefix. Raises EmbeddingError."""
    e = e or await current_embedder()
    return (await embed_texts(e, [e.query_prefix + text]))[0]


async def embed_documents(texts: list[str], e: Embedder | None = None) -> list[list[float] | None]:
    """Document chunks, with the model's document prefix (see embed_batch)."""
    e = e or await current_embedder()
    return await embed_batch([e.document_prefix + t for t in texts], e)


def profile_of(e: Embedder) -> str:
    """How chunks embedded with `e` are made: model | prefixes | chunker. A
    document indexed under another profile is rebuilt (doc_pipeline); vectors
    are only compared within one model."""
    from .extract import CHUNKER_VERSION
    return "|".join((e.model, repr(e.document_prefix), repr(e.query_prefix), CHUNKER_VERSION))


def current_profile() -> str:
    """The environment's profile (what a fresh install records as current). The app reads the database's
    (embedding_state.current); tests use this as the default library's profile."""
    return profile_of(env_embedder())


def profile_model(profile: str | None) -> str | None:
    return profile.split("|", 1)[0] if profile else None
