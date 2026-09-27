"""Which providers exist, and which one serves a model id (spec §4.3, §4.4, §4.6).

Model ids carry the provider name: `local:qwen2.5:7b`. The router splits at the
FIRST colon, because model names contain colons themselves. A name whose prefix
is not a provider (a pre-C1 session's plain `qwen2.5:7b`) goes to the first
provider of kind `ollama`. Trap: a provider named like a model family (`llama3`)
would capture `llama3:8b`; pick provider names that aren't model names.

With INFERENCE_PROVIDERS unset, there is exactly one provider, `ollama`, built
from today's OLLAMA_URL + INFERENCE_MODELS through model_router, so those
variables keep a single parser.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

import httpx

from ..services import model_router
from .providers.base import Provider, ProviderConfig
from .providers.ollama import OllamaProvider
from .providers.openai_compat import OpenAICompatProvider
from .types import Capabilities, CallSettings, Chunk, Message, TextDelta, ToolSpec

logger = logging.getLogger(__name__)

KINDS = {"ollama": OllamaProvider, "openai": OpenAICompatProvider}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def load_provider_configs(env: Mapping[str, str]) -> list[ProviderConfig]:
    raw = (env.get("INFERENCE_PROVIDERS") or "").strip()
    if not raw:
        cfg = model_router.load_inference_config(env)
        return [ProviderConfig(name="ollama", kind="ollama", url=cfg.ollama_url,
                               models=cfg.allowed_models)]
    out: list[ProviderConfig] = []
    for name in (n.strip().lower() for n in raw.split(",")):
        if not name:
            continue
        if not _NAME_RE.match(name) or any(c.name == name for c in out):
            logger.warning("Skipping inference provider %r: invalid or duplicate name", name)
            continue
        key = name.upper().replace("-", "_")
        kind = (env.get(f"INFERENCE_{key}_KIND") or "").strip().lower()
        url = (env.get(f"INFERENCE_{key}_URL") or "").strip().rstrip("/")
        if kind not in KINDS or not url:
            logger.warning("Skipping inference provider %r: INFERENCE_%s_KIND must be ollama or "
                           "openai, and INFERENCE_%s_URL is required", name, key, key)
            continue
        models_raw = (env.get(f"INFERENCE_{key}_MODELS") or "").strip()
        models = tuple(m.strip() for m in models_raw.split(",") if m.strip()) if models_raw else None
        out.append(ProviderConfig(name=name, kind=kind, url=url, models=models,
                                  api_key=(env.get(f"INFERENCE_{key}_API_KEY") or "").strip()))
    return out


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: str
    kind: str
    name: str
    capabilities: Capabilities

    def to_json(self) -> dict:
        return {"id": self.id, "provider": self.provider, "kind": self.kind,
                "name": self.name, "capabilities": self.capabilities.to_json()}


class UnknownModel(Exception):
    pass


class Router:
    def __init__(self, configs: list[ProviderConfig], client: httpx.AsyncClient) -> None:
        self._client = client
        self.providers: dict[str, Provider] = {c.name: KINDS[c.kind](c, client) for c in configs}

    def has_providers(self) -> bool:
        return bool(self.providers)

    def resolve(self, model_id: str) -> tuple[Provider, str]:
        prefix, sep, rest = model_id.partition(":")
        if sep and rest and prefix in self.providers:
            return self.providers[prefix], rest
        for provider in self.providers.values():
            if provider.config.kind == "ollama":
                return provider, model_id
        raise UnknownModel(model_id)

    def canonical_id(self, model_id: str) -> str:
        provider, name = self.resolve(model_id)
        return f"{provider.config.name}:{name}"

    def is_allowed(self, model_id: str) -> bool:
        try:
            provider, name = self.resolve(model_id)
        except UnknownModel:
            return False
        return provider.config.models is None or name in provider.config.models

    async def capabilities(self, model_id: str) -> Capabilities:
        provider, name = self.resolve(model_id)
        return await provider.capabilities(name)

    async def list_models(self) -> tuple[list[ModelInfo], list[str]]:
        async def one(p: Provider) -> list[ModelInfo] | None:
            try:
                names = await p.list_models()
                if p.config.models is not None:
                    names = [n for n in names if n in p.config.models]
                caps = await asyncio.gather(*(p.capabilities(n) for n in names))
                return [ModelInfo(f"{p.config.name}:{n}", p.config.name, p.config.kind, n, c)
                        for n, c in zip(names, caps)]
            except Exception as e:  # noqa: BLE001 — one bad provider must not hide the others
                logger.warning("Provider %s could not list models: %s", p.config.name, type(e).__name__)
                return None

        providers = list(self.providers.values())
        results = await asyncio.gather(*(one(p) for p in providers))
        models: list[ModelInfo] = []
        failed: list[str] = []
        for p, r in zip(providers, results):
            if r is None:
                failed.append(p.config.name)
            else:
                models.extend(r)
        return models, failed

    def stream_chat(self, model_id: str, messages: list[Message], tools: list[ToolSpec],
                    settings: CallSettings) -> AsyncIterator[Chunk]:
        provider, name = self.resolve(model_id)
        return provider.stream_chat(name, messages, tools, settings)

    async def complete(self, model_id: str, messages: list[Message],
                       settings: CallSettings = CallSettings()) -> str:
        parts: list[str] = []
        async for chunk in self.stream_chat(model_id, messages, [], settings):
            if isinstance(chunk, TextDelta):
                parts.append(chunk.text)
        return "".join(parts)

    async def aclose(self) -> None:
        await self._client.aclose()


_router: Router | None = None


async def start_router(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """App startup. `transport` lets tests inject an httpx.MockTransport."""
    global _router
    if _router is not None:
        return
    timeout_s = model_router.get_config().timeout_s
    # read= is httpx's IDLE timeout (time between chunks): a slow but
    # streaming reply is never cut off; a silent provider is (spec §4.4).
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(
        connect=5.0, read=timeout_s, write=30.0, pool=5.0))
    _router = Router(load_provider_configs(os.environ), client)
    names = ", ".join(f"{p.config.name} ({p.config.kind})" for p in _router.providers.values())
    logger.info("Inference providers: %s", names or "none")


async def stop_router() -> None:
    global _router
    if _router is not None:
        await _router.aclose()
        _router = None


def get_router() -> Router:
    if _router is None:
        raise RuntimeError("Inference router not started — call start_router() first")
    return _router
