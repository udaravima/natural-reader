"""The admin's settings for the models themselves (admin console v2 phase 2a, spec §4.2): which model answers the
reader's small jobs, how big a context a local Ollama model is loaded with, what a model's window is declared to
be, which providers count as local, and which chat models are offered. The six settings resolve like every console
setting (services/console_settings.py): the console's value, then the environment, then the default.

Why this exists: Ollama loads a local model at 4,096 tokens on a machine with under 24 GiB of VRAM unless the
request names a `num_ctx`, so a chat that never says one silently loses most of its context. `num_ctx_for` is the
one place that decides what a request sends. For a *local* Ollama model it resolves, first match wins:
1. the console's size for that model (`context_sizes`);
2. the deployment's size (`context_size`: the console's, else INFERENCE_LOCAL_NUM_CTX);
3. the person's own Settings value;
4. DEFAULT_LOCAL_NUM_CTX;
and clamps the result to INFERENCE_NUM_CTX_MAX. A model that is not local (Ollama cloud, any `openai`-kind
provider) keeps the person's value, clamped, and gets none when they sent none: cloud Ollama already gives its
whole context and `openai` ignores the option.

Locality (`is_local`) is a guess from the provider's URL that the admin can override per provider: localhost, a
private, loopback or 100.64.0.0/10 (Tailscale) address, a single-label name (`ollama`), or a .local, .lan,
.internal or .home.arpa name counts as local; any other host is cloud. Model ids are
`provider:name`; a trailing `:latest` on either side of a comparison doesn't matter (`same_model`, `lookup`).

Nothing here is cached: `load_models_settings` is one primary-key read per request, and every function takes the
settings it works from.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, TypeVar
from urllib.parse import urlsplit

from ..chat.reader import reader_model_source
from ..llm.providers.base import ProviderConfig
from . import console_settings
from .console_settings import Section, Setting
from .inference_limits import InferenceLimits, clamp, load_inference_limits

logger = logging.getLogger(__name__)

V = TypeVar("V")

DEFAULT_LOCAL_NUM_CTX = 16384        # tokens
NUM_CTX_RANGE = (2048, 2_097_152)    # tokens: what an admin may set a context size to
WINDOW_RANGE = (1024, 10_000_000)    # tokens: what an admin may declare a model's window to be
_NAME_MAX = 200                      # characters in a model id or provider name
_ENTRIES_MAX = 500

_READER_ERROR = "Choose the chat model, or a model from the list."
_SIZE_ERROR = "Must be a whole number of tokens from 2,048 to 2,097,152."
_WINDOW_ERROR = "Must be a whole number of tokens from 1,024 to 10,000,000."
_LOCALITY_ERROR = "Must be local or cloud."
_NAME_ERROR = f"Names must be 1 to {_NAME_MAX} characters."
_MAP_ERROR = "Must be a list of models, each with a number."
_LIST_ERROR = "Must be a list of model names."
_TOO_MANY_ERROR = f"At most {_ENTRIES_MAX} entries."


# ---------- parsing: what the console may store, what the environment may say ----------

def _int_in(value: Any, low: int, high: int, message: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
        return value
    raise ValueError(message)


def _size(value: Any, _fallback: Any = None) -> int:
    return _int_in(value, *NUM_CTX_RANGE, _SIZE_ERROR)


def _window(value: Any, _fallback: Any = None) -> int:
    return _int_in(value, *WINDOW_RANGE, _WINDOW_ERROR)


def _parse_int(text: str, parse, message: str) -> int:
    try:
        return parse(int(text.strip()))
    except ValueError:
        raise ValueError(message) from None


def _size_env(text: str) -> int:
    return _parse_int(text, _size, _SIZE_ERROR)


def _locality(value: Any, _fallback: Any = None) -> str:
    if value in ("local", "cloud"):
        return value
    raise ValueError(_LOCALITY_ERROR)


def _reader(value: Any, _fallback: Any = None) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if name.lower() == "chat":
        return "chat"
    if 1 <= len(name) <= _NAME_MAX and not any(ch.isspace() for ch in name):
        return name
    raise ValueError(_READER_ERROR)


def _key(value: Any) -> str:
    key = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(key) <= _NAME_MAX:
        raise ValueError(_NAME_ERROR)
    return key


def _number_map(parse):
    """A parser for {name: number}, with each number held to `parse`. Stored sorted by name, so the console's
    JSON comparison sees one order."""
    def parse_map(value: Any, _fallback: Any = None) -> dict[str, int]:
        if not isinstance(value, dict):
            raise ValueError(_MAP_ERROR)
        if len(value) > _ENTRIES_MAX:
            raise ValueError(_TOO_MANY_ERROR)
        out = {_key(name): parse(number) for name, number in value.items()}
        return dict(sorted(out.items()))
    return parse_map


def _locality_map(value: Any, _fallback: Any = None) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(_MAP_ERROR)
    if len(value) > _ENTRIES_MAX:
        raise ValueError(_TOO_MANY_ERROR)
    out = {_key(name): _locality(choice) for name, choice in value.items()}
    return dict(sorted(out.items()))


def _model_list(value: Any, _fallback: Any = None) -> list[str]:
    """Model ids in the order given, each once (`x` and `x:latest` are one model). A list, not a tuple: the
    audit line compares it with the JSON the row stores."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(_LIST_ERROR)
    if len(value) > _ENTRIES_MAX:
        raise ValueError(_TOO_MANY_ERROR)
    out: list[str] = []
    for entry in value:
        name = _key(entry)
        if not any(same_model(name, seen) for seen in out):
            out.append(name)
    return out


SECTION = Section("models", (
    # `env` is the first variable of the reader's chain; models_env() puts the chain's winner under this name
    Setting("reader", env="READER_MODEL", default="chat", parse=_reader, parse_env=_reader),
    Setting("context_size", env="INFERENCE_LOCAL_NUM_CTX", default=None, parse=_size, parse_env=_size_env),
    Setting("context_sizes", env=None, default={}, parse=_number_map(_size), parse_env=_number_map(_size)),
    Setting("context_windows", env=None, default={}, parse=_number_map(_window), parse_env=_number_map(_window)),
    Setting("provider_locality", env=None, default={}, parse=_locality_map, parse_env=_locality_map),
    Setting("chat_models", env=None, default=[], parse=_model_list, parse_env=_model_list),
))


# ---------- the settings a request meets ----------

@dataclass(frozen=True)
class ModelsSettings:
    reader: str                       # "chat" (the conversation's own model) or a model id
    reader_source: str                # "admin" | "env" | "default"
    reader_env: str | None            # the variable the reader's server setting comes from; None when no usable one is set
    context_size: int | None          # tokens: the deployment's size for local Ollama models; None = unset
    context_size_source: str          # "admin" | "env" | "default"
    context_sizes: dict[str, int]     # model id -> tokens, one model's own size
    context_windows: dict[str, int]   # model id -> tokens, a window declared for a model that doesn't report one
    provider_locality: dict[str, str]  # provider name -> "local" | "cloud": the admin's override of the URL guess
    chat_models: tuple[str, ...]      # model ids offered in the chat; empty = everything the environment allows


def models_env(env: Mapping[str, str]) -> dict[str, str]:
    """`env` with the reader's three-variable chain (READER_MODEL, SUMMARIZE_MODEL, WEB_SEARCH_SUMMARY_MODEL)
    put under READER_MODEL, the one name the reader setting reads: the first one set wins, as in chat/reader.py."""
    source = reader_model_source(env)
    return {**env, "READER_MODEL": env[source].strip()} if source else dict(env)


def reader_env_name(env: Mapping[str, str]) -> str:
    """The variable the console names for the reader setting: the one the chain would read, else READER_MODEL."""
    return reader_model_source(env) or "READER_MODEL"


def _build(values: dict[str, console_settings.Value], env: Mapping[str, str]) -> ModelsSettings:
    reader = values["reader"]
    size = values["context_size"]
    return ModelsSettings(
        reader=reader.value, reader_source=reader.source,
        reader_env=reader_model_source(env) if reader.fallback_source == "env" else None,
        context_size=size.value, context_size_source=size.source,
        context_sizes=dict(values["context_sizes"].value), context_windows=dict(values["context_windows"].value),
        provider_locality=dict(values["provider_locality"].value),
        chat_models=tuple(values["chat_models"].value))


async def load_models_settings(conn, env: Mapping[str, str] = os.environ) -> ModelsSettings:
    return _build(await console_settings.load(conn, SECTION, models_env(env)), env)


def from_env(env: Mapping[str, str] = os.environ) -> ModelsSettings:
    """What applies with no console row: the fail-open value for a request that can't read the database."""
    return _build(console_settings._resolve(SECTION, {}, models_env(env)), env)


def view(values: dict[str, console_settings.Value], env: Mapping[str, str] = os.environ) -> dict[str, dict]:
    """console_settings.view for this section, naming the reader's real variable instead of READER_MODEL."""
    out = console_settings.view(SECTION, values)
    out["reader"]["env"] = reader_env_name(env)
    return out


# ---------- model ids ----------

def _bare(model_id: str) -> str:
    return model_id.removesuffix(":latest")


def same_model(a: str, b: str) -> bool:
    return _bare(a) == _bare(b)


def lookup(mapping: Mapping[str, V], model_id: str) -> V | None:
    """The entry for `model_id`, whether the map or the id carries a ':latest' or not."""
    if model_id in mapping:
        return mapping[model_id]
    for name, value in mapping.items():
        if same_model(name, model_id):
            return value
    return None


# ---------- locality ----------

def is_ollama_cloud(name: str) -> bool:
    return name.endswith(("-cloud", ":cloud"))


# Private-network names and the shared address space (CGNAT, which Tailscale uses): ipaddress calls the latter public
_PRIVATE_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _host_is_local(url: str) -> bool:
    try:
        host = urlsplit(url if "://" in url else f"//{url}").hostname
    except ValueError:    # a malformed URL (http://[::1): not a host we can call local
        return False
    if not host:
        return False
    host = host.removesuffix(".")    # `localhost.` is `localhost`
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # a single-label name is a container service (`ollama`); a dotted one is local only under a private-network suffix
        return "." not in host or host.endswith(_PRIVATE_SUFFIXES)
    return ip.is_loopback or ip.is_private or ip in _CGNAT


def is_local(provider: ProviderConfig, model_name: str, locality: Mapping[str, str]) -> bool:
    """Whether the model runs on hardware the deployer controls, first match wins:
    1. an Ollama model whose name ends in -cloud or :cloud is cloud on any host;
    2. the admin's override for the provider;
    3. the provider's host: localhost, a loopback, private or 100.64.0.0/10 IP, a single-label name, or a name ending
       in .local, .lan, .internal or .home.arpa is local (private-network names a deployer reaches without a
       cloud bill; the admin can still override per provider);
    4. anything else is cloud.
    An empty `model_name` skips step 1, which answers for the provider alone."""
    if provider.kind == "ollama" and is_ollama_cloud(model_name):
        return False
    override = locality.get(provider.name)
    if override in ("local", "cloud"):
        return override == "local"
    return _host_is_local(provider.url)


# ---------- the context size a request sends ----------

def deployment_num_ctx(ms: ModelsSettings, model_id: str, limits: InferenceLimits) -> tuple[int | None, str]:
    """Steps 1 and 2 above, clamped, with where the number came from ("model", "admin" or "env").
    (None, "unset") when the admin and the environment said nothing."""
    own = lookup(ms.context_sizes, model_id)
    if own is not None:
        return clamp(own, None, limits)[0], "model"
    if ms.context_size is not None:
        return clamp(ms.context_size, None, limits)[0], ms.context_size_source
    return None, "unset"


def num_ctx_for(ms: ModelsSettings, model_id: str, *, local: bool, user_value: int | None,
                limits: InferenceLimits) -> int | None:
    """The `num_ctx` to send for `model_id`, or None to send none (see the module docstring for the order)."""
    if not local:
        return clamp(user_value, None, limits)[0]
    size, _ = deployment_num_ctx(ms, model_id, limits)
    if size is not None:
        return size
    return clamp(user_value if user_value is not None else DEFAULT_LOCAL_NUM_CTX, None, limits)[0]


# ---------- windows and the model list ----------

def declared_window(ms: ModelsSettings, model_id: str, provider_name: str,
                    env: Mapping[str, str]) -> tuple[int | None, str]:
    """The window an admin has declared for a model the provider doesn't report one for, and where it came from
    ("admin": the console's map; "env": INFERENCE_<NAME>_CONTEXT_WINDOW for the whole provider; "none")."""
    own = lookup(ms.context_windows, model_id)
    if own is not None:
        return own, "admin"
    var = f"INFERENCE_{provider_name.upper().replace('-', '_')}_CONTEXT_WINDOW"
    raw = (env.get(var) or "").strip()
    if raw:
        try:
            return _parse_int(raw, _window, _WINDOW_ERROR), "env"
        except ValueError:
            if (var, raw) not in console_settings._warned:
                console_settings._warned.add((var, raw))
                logger.warning("%s=%r isn't a usable value: ignored", var, raw)
    return None, "none"


def offered(models: list, ms: ModelsSettings) -> list:
    """`models` (already what the environment allows) narrowed to the console's selection. No selection means
    all of them. A selection whose models are all gone offers nothing: falling back to everything would widen
    what the admin narrowed."""
    if not ms.chat_models:
        return models
    return [m for m in models if any(same_model(m.id, chosen) for chosen in ms.chat_models)]


# ---------- the server's own values, read-only beside the settings ----------

def _row(id: str, label: str, value: str, var: str, env: Mapping[str, str]) -> dict:
    return {"id": id, "label": label, "value": value, "env": var,
            "source": "env" if (env.get(var) or "").strip() else "default"}


def _duration(seconds: int) -> str:
    for unit, size in (("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            n = seconds // size
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return f"{seconds} seconds"


def info_rows(env: Mapping[str, str] = os.environ) -> list[dict]:
    limits = load_inference_limits(env)
    return [
        _row("num_ctx_max", "Largest context size",
             "No limit" if limits.num_ctx_max is None else f"{limits.num_ctx_max:,} tokens",
             "INFERENCE_NUM_CTX_MAX", env),
        _row("keep_alive_max", "Longest time a model stays loaded",
             "No limit" if limits.keep_alive_max_s is None else _duration(limits.keep_alive_max_s),
             "INFERENCE_KEEP_ALIVE_MAX", env),
    ]
