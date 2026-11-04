"""Embedding model families this app knows: their prefixes, and whether their vectors may be shortened.

One place, as the admin console spec asks (§4.2.4, §9). `shortenable` means the family was trained to give
good vectors when cut short (Matryoshka): the probe admits such a model at a native size above 768 and asks
it for 768. Any other model must give 768 numbers by itself, because Ollama cuts every vector to the
`dimensions` asked for and renormalizes it with no error, so a 1024-number model asked for 768 searches
noticeably worse and nothing says so.

Each entry says where it was checked. Adding a family is one line here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Family:
    prefix_document: str    # put before every passage that is stored
    prefix_query: str       # put before every question that is searched
    shortenable: bool       # trained to be cut short (Matryoshka); see the module note
    source: str             # where this entry was checked


KNOWN: dict[str, Family] = {
    # Model card read 2026-11-05: search_document / search_query; v1.5 is Matryoshka (768 native).
    # Ollama's nomic-embed-text is v1.5.
    "nomic-embed-text": Family("search_document: ", "search_query: ", True,
                               "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5"),
    # Model card read 2026-11-05 (EmbeddingGemma 2; the first EmbeddingGemma uses the same prompts):
    # 768 native, MRL 512/256/128. Prefixes apply to text only.
    "embeddinggemma": Family("title: none | text: ", "task: search result | query: ", True,
                             "https://huggingface.co/google/embeddinggemma-2"),
    # Model card read 2026-11-05: 1024 native for 0.6B (larger sizes more), MRL 32..native; queries take a
    # one-line instruction, documents none.
    "qwen3-embedding": Family(
        "", "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:", True,
        "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B"),
    # OpenAI's embeddings guide: text-embedding-3-small (1536) and -large (3072) take `dimensions`.
    # Not re-read in this session.
    "text-embedding-3": Family("", "", True, "https://platform.openai.com/docs/guides/embeddings"),
}


def family(model: str) -> Family | None:
    """The family a model name belongs to, or None. A path (`openai/`, `hf.co/owner/`), a `:tag` and letter
    case are ignored: `hf.co/someone/EmbeddingGemma-2-GGUF:Q8_0` is embeddinggemma."""
    name = model.rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return next((f for key, f in KNOWN.items() if name.startswith(key)), None)
