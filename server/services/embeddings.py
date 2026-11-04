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

import httpx

from ..llm.providers.base import ProviderConfig, auth_headers
from . import model_router
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


def _where(e: Embedder) -> str:
    return f"{e.model} on {e.provider.name}"


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
    except httpx.HTTPStatusError as err:   # reachable, but refusing: a model never pulled is a 404
        raise EmbeddingError(f"{_where(e)} answered HTTP {err.response.status_code}") from None
    except httpx.HTTPError as err:
        raise EmbeddingError(f"Can't reach {e.provider.name} for {e.model} ({type(err).__name__})") from None
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
    e = e or env_embedder()

    async def safe(batch: list[str]) -> list[list[float] | None]:
        try:
            return await _one_request(e, batch)
        except EmbeddingError as err:
            logger.warning("Failed to embed %d text(s): %s", len(batch), err)
            return [None] * len(batch)

    out = await asyncio.gather(*(safe(b) for b in _batches(texts)))
    return [vec for batch in out for vec in batch]


def env_embedder(env=None) -> Embedder:
    """The embedder the environment describes: the Ollama at OLLAMA_URL and EMBEDDING_MODEL."""
    cfg = model_router.load_inference_config(env if env is not None else os.environ)
    return Embedder(ProviderConfig(name="ollama", kind="ollama", url=cfg.ollama_url), cfg.embed_model, None,
                    cfg.embed_document_prefix, cfg.embed_query_prefix)


# ---------- what is embedded, and how (v2.3 Task E) ----------

async def embed_query(text: str, e: Embedder | None = None) -> list[float]:
    """A question or search, with the model's query prefix. Raises EmbeddingError."""
    e = e or env_embedder()
    return (await embed_texts(e, [e.query_prefix + text]))[0]


async def embed_documents(texts: list[str], e: Embedder | None = None) -> list[list[float] | None]:
    """Document chunks, with the model's document prefix (see embed_batch)."""
    e = e or env_embedder()
    return await embed_batch([e.document_prefix + t for t in texts], e)


def current_profile() -> str:
    """How chunks embedded now are made: model | prefixes | chunker. A
    document indexed under another profile is rebuilt (doc_pipeline); vectors
    are only compared within one model."""
    from .extract import CHUNKER_VERSION
    cfg = model_router.get_config()
    return "|".join((cfg.embed_model, repr(cfg.embed_document_prefix), repr(cfg.embed_query_prefix),
                     CHUNKER_VERSION))


def profile_model(profile: str | None) -> str | None:
    return profile.split("|", 1)[0] if profile else None
