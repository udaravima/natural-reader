"""
Embedding generation via Ollama's `/api/embeddings` endpoint.

We deliberately stay on the single-prompt-per-call API (rather than the newer
`/api/embed` batch endpoint) so this works against older Ollama versions, and
bound concurrency with a Semaphore(4) — nomic-embed-text is fast, four in flight
is enough to keep the pipeline saturated without making Kokoro's process
unresponsive when it shares the host.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))
EMBEDDING_TIMEOUT_S = float(os.environ.get("EMBEDDING_TIMEOUT_S", "30"))
# Ollama's default context for nomic-embed-text is often 2048 tokens.
# At ~4 chars/token the safe ceiling is ~2 000 chars.  Override via env
# if you've set a larger context (e.g. `num_ctx` in a Modelfile).
MAX_INPUT_CHARS = int(os.environ.get("EMBEDDING_MAX_CHARS", "2000"))
MAX_CONCURRENCY = int(os.environ.get("EMBEDDING_MAX_CONCURRENCY", "4"))

_client: httpx.AsyncClient | None = None
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


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


async def embed_one(text: str) -> list[float]:
    """Embed a single string via Ollama. Raises httpx.HTTPError on failure."""
    client = _get_client()
    # Truncate to stay within the model's context window.
    if len(text) > MAX_INPUT_CHARS:
        logger.warning(
            "Truncating text from %d to %d chars for embedding",
            len(text), MAX_INPUT_CHARS,
        )
        text = text[:MAX_INPUT_CHARS]
    async with _semaphore:
        resp = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
        )
        resp.raise_for_status()
        data = resp.json()
    vec = data.get("embedding")
    if not isinstance(vec, list) or not vec:
        raise ValueError(f"Ollama returned no embedding for text len={len(text)}")
    if len(vec) != EMBEDDING_DIM:
        raise ValueError(
            f"Embedding dim mismatch: model returned {len(vec)}, schema expects {EMBEDDING_DIM}. "
            f"Switching embedding models requires re-creating the doc_chunks.embedding column."
        )
    return vec


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed many strings in parallel (bounded by the semaphore).

    Returns ``None`` for any text that fails to embed (e.g. too long even
    after truncation, transient Ollama error).  Callers should skip
    ``None`` entries rather than aborting the whole batch.
    """
    if not texts:
        return []

    async def _safe_embed(t: str) -> list[float] | None:
        try:
            return await embed_one(t)
        except Exception as e:
            logger.warning("Failed to embed text (len=%d): %s", len(t), e)
            return None

    return await asyncio.gather(*(_safe_embed(t) for t in texts))
