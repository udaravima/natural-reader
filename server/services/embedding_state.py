"""What the library's vectors were made with, what the admin chose, and the re-embed between them (admin console
spec §4.2.3–4.2.4).

Two facts, kept apart:
- the **target** is a setting: the console's `embeddings` section (provider, model), else EMBEDDING_PROVIDER and
  EMBEDDING_MODEL, else the Ollama at OLLAMA_URL with nomic-embed-text. Only the embeddings route writes it,
  after a probe; it isn't in the Models section the generic settings route saves.
- the **current** embedder is a database fact, the `embedding_state` row in app_settings: what every stored
  vector was made with. Queries are embedded with it, so they compare with the vectors they meet.

The row: {"current": {...}, "next": {...} | null, "filled": int, "total": int, "claimed_by": str | null,
"claimed_at": iso | null, "error": str | null}. `next` is a migration's target while one runs
(embedding_migration.py). An embedder is stored by provider *name*: its URL and key stay in the environment.

Whether two embedders give comparable vectors is `vector_identity`: the model and the two prefixes (ruling R1).
The provider is only where the call goes, so the same model elsewhere needs no re-embed. `dimensions` isn't part
of it either: for a given model the probe always decides it the same way.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from psycopg.types.json import Jsonb

from ..llm.providers.base import ProviderConfig
from ..llm.router import load_provider_configs
from . import console_settings, embedding_models, model_router
from .console_settings import Section, Setting
from .embeddings import Embedder

logger = logging.getLogger(__name__)

STATE_KEY = "embedding_state"
DEFAULT_MODEL = "nomic-embed-text"
_NAME_MAX = 200
_NAME_ERROR = f"Names must be 1 to {_NAME_MAX} characters, with no spaces."


def _name(value: Any, _fallback: Any = None) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if 1 <= len(name) <= _NAME_MAX and not any(ch.isspace() for ch in name):
        return name
    raise ValueError(_NAME_ERROR)


def _provider_name(value: Any, _fallback: Any = None) -> str:
    return _name(value).lower()


SECTION = Section("embeddings", (
    Setting("provider", env="EMBEDDING_PROVIDER", default=None, parse=_provider_name, parse_env=_provider_name),
    Setting("model", env="EMBEDDING_MODEL", default=DEFAULT_MODEL, parse=_name, parse_env=_name),
))


# ---------- providers ----------

def providers(env: Mapping[str, str] = os.environ) -> list[ProviderConfig]:
    """The configured providers, plus the Ollama at OLLAMA_URL when none of them is it: that one is where
    embeddings went before this phase, and stays the default (spec §4.2.3)."""
    everyone = load_provider_configs(env)
    # A Gemini provider is for chat only: the embedding client speaks Ollama's /api/embed and OpenAI's
    # /embeddings, and Gemini has neither at its address.
    configured = [p for p in everyone if p.kind != "gemini"]
    url = model_router.load_inference_config(env).ollama_url
    if any(p.kind == "ollama" and p.url == url for p in configured):
        return configured
    taken = {p.name for p in everyone}
    name = "ollama" if "ollama" not in taken else "ollama-url"
    return [*configured, ProviderConfig(name=name, kind="ollama", url=url)]


def default_provider(env: Mapping[str, str] = os.environ) -> ProviderConfig:
    url = model_router.load_inference_config(env).ollama_url
    return next(p for p in providers(env) if p.kind == "ollama" and p.url == url)


def provider_named(name: str | None, env: Mapping[str, str] = os.environ) -> ProviderConfig | None:
    return next((p for p in providers(env) if p.name == name), None) if name else None


# ---------- the target ----------

def prefixes_for(model: str, env: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """(document, query). EMBEDDING_*_PREFIX apply to the environment's own EMBEDDING_MODEL only (ruling R6); any
    other model takes its family's (embedding_models.KNOWN), or none."""
    env_model = (env.get("EMBEDDING_MODEL") or "").strip() or DEFAULT_MODEL
    if _bare(model) == _bare(env_model):
        return model_router.prefixes(env, model)
    fam = embedding_models.family(model)
    return (fam.prefix_document, fam.prefix_query) if fam else ("", "")


def target(values: Mapping[str, console_settings.Value], env: Mapping[str, str] = os.environ) -> Embedder:
    """The embedder the settings describe. `dimensions` is unknown until probed (embedding_migration.start)."""
    model = values["model"].value
    provider = provider_named(values["provider"].value, env) or default_provider(env)
    return Embedder(provider, model, None, *prefixes_for(model, env))


def env_target(env: Mapping[str, str] = os.environ) -> Embedder:
    """The target with no console setting: what the environment alone says."""
    return target(console_settings._resolve(SECTION, {}, env), env)


async def load_target(conn, env: Mapping[str, str] = os.environ) -> Embedder:
    return target(await console_settings.load(conn, SECTION, env), env)


# ---------- the current embedder, and the state row ----------

def _bare(model: str) -> str:
    return model.removesuffix(":latest")


def vector_identity(e: Embedder) -> str:
    """Two embedders with one identity give comparable vectors (ruling R1)."""
    return "|".join((_bare(e.model), repr(e.document_prefix), repr(e.query_prefix)))


def to_json(e: Embedder) -> dict[str, Any]:
    return {"provider": e.provider.name, "model": e.model, "dimensions": e.dimensions,
            "document_prefix": e.document_prefix, "query_prefix": e.query_prefix}


def from_json(d: Mapping[str, Any], env: Mapping[str, str] = os.environ) -> Embedder:
    """A stored embedder. A provider that is no longer configured falls back to the default one: the model is what
    the vectors depend on, the provider only where the call goes."""
    provider = provider_named(d.get("provider"), env) or default_provider(env)
    return Embedder(provider, d["model"], d.get("dimensions"), d.get("document_prefix", ""),
                    d.get("query_prefix", ""))


async def read_state(conn, *, lock: str | None = None) -> dict[str, Any] | None:
    """The state row, or None before init. `lock` is "share" or "update": a writer of vectors reads it FOR SHARE
    and a migration step FOR UPDATE, so a write can't land on the wrong side of a switch (ledger ruling P2)."""
    suffix = {None: "", "share": " FOR SHARE", "update": " FOR UPDATE"}[lock]
    cur = await conn.execute(f"SELECT value FROM app_settings WHERE key = %s{suffix}", (STATE_KEY,))
    row = await cur.fetchone()
    return row[0] if row and isinstance(row[0], dict) else None


async def write_state(conn, state: Mapping[str, Any], *, by: str | None = None) -> None:
    """Replace the state row. `by` is the admin's user id; None for the app's own steps."""
    await conn.execute(
        "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
        "updated_at = now()", (STATE_KEY, Jsonb(dict(state)), by))


def fresh_state(current: Embedder) -> dict[str, Any]:
    return {"current": to_json(current), "next": None, "filled": 0, "total": 0,
            "claimed_by": None, "claimed_at": None, "error": None}


async def init(conn, env: Mapping[str, str] = os.environ) -> None:
    """At startup: with no state row yet, the vectors already stored were made with the target (the only
    embedder there was before this phase), so it becomes current. An existing row is left alone."""
    await conn.execute(
        "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, NULL, now()) "
        "ON CONFLICT (key) DO NOTHING", (STATE_KEY, Jsonb(fresh_state(await load_target(conn, env)))))


async def current(conn, env: Mapping[str, str] = os.environ) -> Embedder:
    """What the stored vectors were made with. Before init (a test, a worker that hasn't started), the target."""
    state = await read_state(conn)
    if state and isinstance(state.get("current"), dict):
        return from_json(state["current"], env)
    return await load_target(conn, env)


async def locked_current(conn, env: Mapping[str, str] = os.environ) -> Embedder:
    """The current embedder, with the state row held FOR SHARE until the caller's transaction ends: a writer of
    vectors calls it in its write transaction, so a switch can't happen between its check and its write."""
    state = await read_state(conn, lock="share")
    if state and isinstance(state.get("current"), dict):
        return from_json(state["current"], env)
    return await load_target(conn, env)


async def needs_migration(conn, env: Mapping[str, str] = os.environ) -> bool:
    return vector_identity(await load_target(conn, env)) != vector_identity(await current(conn, env))
