"""Model-router config — the single place that knows which model serves which
task and what this deployment is allowed to run (gateway spec §4.2). Since C1
it owns only what isn't chat: embeddings, the budget and the timeout (the
reader's model is server/services/model_settings.py's); provider lists and allow-lists live in server/llm/router.py
(spec §4.6).

Everything is pure env parsing so tests can pass a dict. get_config() re-reads
os.environ on every call: parsing is trivial, and it keeps monkeypatch.setenv
effective in tests and .env edits effective without re-importing the module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from . import embedding_models


@dataclass(frozen=True)
class InferenceConfig:
    ollama_url: str
    timeout_s: float
    allowed_models: tuple[str, ...] | None   # None = allow everything (dev)
    embed_model: str
    daily_token_budget: int | None           # None = unlimited
    # Text put before what is embedded (v2.3 Task E). Retrieval-trained models
    # expect them: nomic-embed-text was trained with these two.
    embed_document_prefix: str = ""
    embed_query_prefix: str = ""


def prefixes(env: Mapping[str, str], model: str) -> tuple[str, str]:
    """(document, query) prefixes for `model`: EMBEDDING_*_PREFIX when set, else its family's."""
    return _prefixes(env, model)


def _prefixes(env: Mapping[str, str], model: str) -> tuple[str, str]:
    # The prefixes the model's family was trained with (embedding_models.KNOWN); unlisted: none.
    fam = embedding_models.family(model)
    known = (fam.prefix_document, fam.prefix_query) if fam else ("", "")
    # Set (even to "") wins: an empty value turns a known model's prefix off.
    return (env.get("EMBEDDING_DOCUMENT_PREFIX", known[0]), env.get("EMBEDDING_QUERY_PREFIX", known[1]))


def load_inference_config(env: Mapping[str, str]) -> InferenceConfig:
    def _csv(key: str) -> tuple[str, ...] | None:
        raw = env.get(key)
        if raw is None or not raw.strip():
            return None
        return tuple(m.strip() for m in raw.split(",") if m.strip())

    embed_model = env.get("EMBEDDING_MODEL", "nomic-embed-text")
    doc_prefix, query_prefix = _prefixes(env, embed_model)
    budget_raw = (env.get("INFERENCE_DAILY_TOKEN_BUDGET") or "").strip()
    budget = int(budget_raw) if budget_raw else None

    return InferenceConfig(
        ollama_url=env.get("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        timeout_s=float(env.get("INFERENCE_TIMEOUT_S", "300")),
        allowed_models=_csv("INFERENCE_MODELS"),
        embed_model=embed_model,
        # Spec §4.3: 0/unset = unlimited, so a falsy budget collapses to None.
        daily_token_budget=budget if budget else None,
        embed_document_prefix=doc_prefix,
        embed_query_prefix=query_prefix,
    )


def get_config() -> InferenceConfig:
    return load_inference_config(os.environ)


def _strip_latest(name: str) -> str:
    return name[:-len(":latest")] if name.endswith(":latest") else name


def offered_chat_models(models: list, embed_model: str) -> list:
    """The models a person may chat with: everything the providers list except embedding models, which can't
    answer a chat (Ollama answers HTTP 501). One is: a model Ollama says only embeds (`capabilities.embedding`),
    one of a known embedding family on any provider (embedding_models.KNOWN), or the environment's
    EMBEDDING_MODEL on Ollama (`:latest` on either side doesn't matter), for an Ollama too old to say."""
    embed = _strip_latest(embed_model)

    def embeds(m) -> bool:
        return (getattr(m.capabilities, "embedding", None) is True
                or embedding_models.family(m.name) is not None
                or (m.kind == "ollama" and _strip_latest(m.name) == embed))
    return [m for m in models if not embeds(m)]
