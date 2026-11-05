"""What Admin → Models → Embeddings shows and does (admin console spec §4.2.3–4.2.4): the model the library's
vectors were made with, the model chosen, where that choice comes from, and a re-embed in progress. The routes
are thin (routers/admin.py); the rules are here and in embedding_state / embedding_migration."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Mapping

from . import console_settings, embedding_migration, embedding_models, embedding_state, embeddings
from .embeddings import Embedder, ProbeResult


class Refused(ValueError):
    """A choice the console can't save. `code` is the refusal's error id; str() is the admin's message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def migration_view(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """none | building | switching (every passage filled; the switch is next) | failed (paused on an error, or
    a target the loop refused)."""
    state = state or {}
    nxt = state.get("next")
    if nxt:
        filled, total = state.get("filled", 0), state.get("total", 0)
        kind = "failed" if state.get("error") else ("switching" if filled >= total else "building")
        return {"state": kind, "model": nxt.get("model"), "filled": filled, "total": total, "error": state.get("error")}
    if state.get("error"):
        return {"state": "failed", "model": None, "filled": 0, "total": 0, "error": state["error"]}
    return {"state": "none", "model": None, "filled": 0, "total": 0, "error": None}


async def view(conn, env: Mapping[str, str] = os.environ) -> dict[str, Any]:
    values = await console_settings.load(conn, embedding_state.SECTION, env)
    target = embedding_state.target(values, env)
    state = await embedding_state.read_state(conn)
    current = embedding_state.from_json(state["current"], env) if state else target
    return {
        "current": {"provider": current.provider.name, "model": current.model, "dimensions": current.dimensions},
        "target": {"provider": target.provider.name, "model": target.model},
        "source": {"provider": values["provider"].source, "model": values["model"].source},
        "env": {"provider": "EMBEDDING_PROVIDER", "model": "EMBEDDING_MODEL"},
        "migration": migration_view(state),
        "providers": [{"name": p.name, "kind": p.kind} for p in embedding_state.providers(env)],
    }


def provider(name: str | None, env: Mapping[str, str] = os.environ):
    """A configured provider by name (None: the default one). Only a configured provider can be chosen: the
    browser never sends a URL."""
    found = embedding_state.provider_named(name, env) if name else embedding_state.default_provider(env)
    if found is None:
        raise Refused("unknown_provider", "That provider isn't configured on this server.")
    return found


def _model(name: str) -> str:
    try:
        return embedding_state.SECTION.get("model").parse(name, None)
    except ValueError as err:
        raise Refused("invalid_setting", str(err)) from None


async def check(provider_name: str | None, model: str) -> ProbeResult:
    return await embeddings.probe(provider(provider_name), _model(model))


async def choose(conn, provider_name: str | None, model: str | None, *, by: str,
                 env: Mapping[str, str] = os.environ) -> None:
    """Save the admin's choice and act on it: a model giving other vectors starts a re-embed; the same vectors
    (the same model elsewhere, or back to what is current) switch at once. The model is probed here, whatever
    the browser's own check said, and a refused one saves nothing. Both None: back to the environment's."""
    if model is None:
        candidate, changes = embedding_state.env_target(env), {"provider": None, "model": None}
    else:
        chosen = provider(provider_name, env)
        name = _model(model)
        candidate = Embedder(chosen, name, None, *embedding_state.prefixes_for(name, env))
        changes = {"provider": chosen.name, "model": name}
    current = await embedding_state.current(conn, env)
    same = (embedding_state.vector_identity(candidate) == embedding_state.vector_identity(current)
            and candidate.provider.name == current.provider.name)
    if same:   # nothing new to check: it is what the library already uses
        dimensions = current.dimensions
    else:
        # The re-embed starts within seconds: keep the model loaded for it rather than load it twice.
        verdict = await embeddings.probe(candidate.provider, candidate.model, unload=False)
        if not verdict.ok:
            raise Refused("refused_model", verdict.reason)
        dimensions = verdict.dimensions
    await console_settings.save(conn, embedding_state.SECTION, changes, updated_by=by, env=env)
    await embedding_migration.start(conn, replace(candidate, dimensions=dimensions), by=by, env=env)


async def models(provider_name: str, env: Mapping[str, str] = os.environ) -> list[str]:
    """The provider's model names, embedding models first (a known family, or "embed" in the name)."""
    names = await embeddings.list_model_names(provider(provider_name, env))

    def likely(name: str) -> bool:
        return embedding_models.family(name) is not None or "embed" in name.lower()
    return sorted(names, key=lambda n: (not likely(n), names.index(n)))
