"""Model-router config — the single place that knows which model serves which
task and what this deployment is allowed to run (gateway spec §4.2).

Everything is pure env parsing so tests can pass a dict. get_config() re-reads
os.environ on every call: parsing is trivial, and it keeps monkeypatch.setenv
effective in tests and .env edits effective without re-importing the module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class InferenceConfig:
    ollama_url: str
    timeout_s: float
    allowed_models: tuple[str, ...] | None   # None = allow everything (dev)
    summarize_model: str
    embed_model: str
    daily_token_budget: int | None           # None = unlimited


def load_inference_config(env: Mapping[str, str]) -> InferenceConfig:
    def _csv(key: str) -> tuple[str, ...] | None:
        raw = env.get(key)
        if raw is None or not raw.strip():
            return None
        return tuple(m.strip() for m in raw.split(",") if m.strip())

    budget_raw = (env.get("INFERENCE_DAILY_TOKEN_BUDGET") or "").strip()
    budget = int(budget_raw) if budget_raw else None

    return InferenceConfig(
        ollama_url=env.get("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        timeout_s=float(env.get("INFERENCE_TIMEOUT_S", "300")),
        allowed_models=_csv("INFERENCE_MODELS"),
        # SUMMARIZE_MODEL is the canonical name; WEB_SEARCH_SUMMARY_MODEL is the
        # pre-gateway var kept working so existing deployments don't break.
        summarize_model=(
            env.get("SUMMARIZE_MODEL")
            or env.get("WEB_SEARCH_SUMMARY_MODEL")
            or "llama3.2:3b"
        ),
        embed_model=env.get("EMBEDDING_MODEL", "nomic-embed-text"),
        # Spec §4.3: 0/unset = unlimited, so a falsy budget collapses to None.
        daily_token_budget=budget if budget else None,
    )


def get_config() -> InferenceConfig:
    return load_inference_config(os.environ)


def is_model_allowed(cfg: InferenceConfig, model: str) -> bool:
    return cfg.allowed_models is None or model in cfg.allowed_models
