"""The values only the server's environment can change, for the console's Deployment section (admin console v2
spec §4.5, "Read-only"): what each is now and which variable sets it, so an admin can see the whole deployment
in one place without a shell. Nothing here is editable and nothing is a secret: a key or secret shows as "set" or
"not set", and a URL never carries its user info, query or fragment.

Every value is read from the `env` passed in, with the same variable names and defaults as the module that owns
it, so the list answers for the environment it was given (and a test can give it a small one).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from ..chat import reader
from ..llm.router import load_provider_configs

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_SEARXNG_URL = "http://localhost:18043"


def _host(netloc: str) -> str:
    """`host[:port]` of a netloc, without user info."""
    return netloc.rsplit("@", 1)[-1]


def safe_url(url: str) -> str:
    """The URL's scheme, host, port and path. User info (a password may sit in it), query and fragment (a key
    may) are dropped, so the result is safe to show an admin."""
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return ""
    scheme = f"{parts.scheme}://" if parts.scheme else ""
    return f"{scheme}{_host(parts.netloc)}{parts.path}"


def _set(env: Mapping[str, str], key: str) -> bool:
    return bool((env.get(key) or "").strip())


def _row(id: str, label: str, value: str, var: str | None, is_set: bool) -> dict:
    return {"id": id, "label": label, "value": value, "env": var, "source": "env" if is_set else "default"}


def _secret(env: Mapping[str, str], id: str, label: str, var: str) -> dict:
    is_set = _set(env, var)
    return _row(id, label, "set" if is_set else "not set", var, is_set)


def readonly_rows(env: Mapping[str, str] = os.environ) -> list[dict]:
    rows: list[dict] = []
    listed = _set(env, "INFERENCE_PROVIDERS")
    for cfg in load_provider_configs(env):
        key = cfg.name.upper().replace("-", "_")
        var = f"INFERENCE_{key}_URL" if listed else "OLLAMA_URL"
        rows.append(_row(f"provider:{cfg.name}", f"Model provider “{cfg.name}”",
                         f"{cfg.kind} · {safe_url(cfg.url)}", var, _set(env, var)))
        if listed:
            rows.append(_secret(env, f"provider_key:{cfg.name}", f"Key for “{cfg.name}”", f"INFERENCE_{key}_API_KEY"))

    budget = (env.get("INFERENCE_DAILY_TOKEN_BUDGET") or "").strip()
    reader_var = reader.reader_model_source(env)
    reader_model = reader.load_reader_config(env).model
    storage = env.get("DOC_STORAGE_DIR") or env.get("PDF_STORAGE_DIR") or "./data/pdfs"
    searxng = (env.get("SEARXNG_URL", _DEFAULT_SEARXNG_URL) or "").strip()
    rows += [
        _row("allowed_models", "Allowed models", (env.get("INFERENCE_MODELS") or "").strip() or "All models allowed",
             "INFERENCE_MODELS", _set(env, "INFERENCE_MODELS")),
        _row("chat_timeout", "Chat timeout", f"{(env.get('INFERENCE_TIMEOUT_S') or '').strip() or '300'} s",
             "INFERENCE_TIMEOUT_S", _set(env, "INFERENCE_TIMEOUT_S")),
        _row("daily_budget", "Daily token budget", budget if budget not in ("", "0") else "Unlimited",
             "INFERENCE_DAILY_TOKEN_BUDGET", _set(env, "INFERENCE_DAILY_TOKEN_BUDGET")),
        _row("reader_model", "Reading model",
             "The chat model of each conversation" if reader_model == "chat" else reader_model,
             reader_var, reader_var is not None),
        _row("embedding_model", "Embedding model", (env.get("EMBEDDING_MODEL") or "").strip() or "nomic-embed-text",
             "EMBEDDING_MODEL", _set(env, "EMBEDDING_MODEL")),
        _row("embedding_url", "Embedding server", safe_url((env.get("OLLAMA_URL") or _DEFAULT_OLLAMA_URL)),
             "OLLAMA_URL", _set(env, "OLLAMA_URL")),
        _row("storage", "Document storage", str(Path(storage).resolve()),
             "DOC_STORAGE_DIR" if _set(env, "DOC_STORAGE_DIR") or not _set(env, "PDF_STORAGE_DIR") else "PDF_STORAGE_DIR",
             _set(env, "DOC_STORAGE_DIR") or _set(env, "PDF_STORAGE_DIR")),
        _row("docling", "Docling",
             "On" if (env.get("DOCLING_ENABLED") or "false").lower() in ("1", "true", "yes") else "Off",
             "DOCLING_ENABLED", _set(env, "DOCLING_ENABLED")),
        _row("searxng", "Web search server", _host(urlsplit(searxng).netloc) if searxng else "Not set up",
             "SEARXNG_URL", bool(searxng) and "SEARXNG_URL" in env),
        _row("workers", "Server workers", (env.get("WORKERS") or "").strip() or "1", "WORKERS", _set(env, "WORKERS")),
        _secret(env, "session_secret", "Session secret", "SESSION_SECRET"),
        _secret(env, "oidc_client_secret", "Sign-in client secret", "OIDC_CLIENT_SECRET"),
        _secret(env, "kc_admin_client_secret", "Keycloak admin client secret", "KC_ADMIN_CLIENT_SECRET"),
    ]
    return rows
