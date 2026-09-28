"""What every adapter shares: the provider config record, HTTP error mapping,
tool-argument parsing, and the per-model memory of rejected features (spec §4.2)."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

import httpx

from ..types import (Capabilities, CallSettings, Chunk, FeatureDropped, Message, ProviderError,
                     ProviderTimeout, ProviderUnavailable, ToolSpec)

logger = logging.getLogger(__name__)

CAPABILITY_TTL_S = 300.0   # re-probe a model's capabilities after 5 minutes


@dataclass(frozen=True)
class ProviderConfig:
    name: str                               # lower-case; the model-id prefix
    kind: str                               # "ollama" | "openai"
    url: str                                # base URL, no trailing slash
    api_key: str = ""
    models: tuple[str, ...] | None = None   # allow-list; None = everything it lists

    def __repr__(self) -> str:   # never print the key
        return (f"ProviderConfig(name={self.name!r}, kind={self.kind!r}, "
                f"url={self.url!r}, models={self.models!r})")


class Provider(Protocol):
    config: ProviderConfig

    async def list_models(self) -> list[str]: ...

    async def capabilities(self, model: str) -> Capabilities: ...

    def stream_chat(self, model: str, messages: list[Message], tools: list[ToolSpec],
                    settings: CallSettings) -> AsyncIterator[Chunk]: ...


def auth_headers(cfg: ProviderConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}


_SECRET_PATTERNS = [
    # OpenAI/Anthropic-style API keys (sk-..., sk-ant-..., sk-proj-...).
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-[REDACTED]"),
    # An Authorization header value, if a provider ever echoes the request it received.
    (re.compile(r"Bearer\s+\S+"), "Bearer [REDACTED]"),
    # URL userinfo (scheme://user:pass@host) — a base URL that embeds credentials.
    (re.compile(r"://[^@/\s]+:[^@/\s]+@"), "://***@"),
]


def _redact_secrets(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def safe_error_message(raw: bytes | str) -> str:
    """The provider's error text with any embedded
    secret-shaped substring redacted — a provider that echoes back the request
    it received (or a misconfigured upstream that leaks its own key) must
    never let that key reach an SSE event or the chat event log. Redacted
    first, then trimmed to 300 chars, so a secret straddling the cut is still
    caught."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    try:
        body = json.loads(text)
        err = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
        text = str(err)
    except ValueError:
        pass
    return _redact_secrets(text.strip())[:300]


NOT_JSON_MESSAGE = "The provider sent a response that isn't JSON."


def json_object(resp: httpx.Response) -> dict[str, Any]:
    """A 200 body as a JSON object. Anything else (a proxy's HTML login or
    error page, say) is a ProviderError with a fixed message, never a
    ValueError: escaping the adapter, that would become an internal_error
    instead of a provider error (final review M3). The body is never quoted."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ProviderError(resp.status_code, NOT_JSON_MESSAGE)
    return body


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool-call arguments as a dict. Invalid JSON becomes {}: the tool then
    reports what's missing to the model, which can recover (Review Focus 1)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            logger.warning("Tool call arguments were not valid JSON (%d chars)", len(raw))
    return {}


class FeatureMemory:
    """Features a provider rejected for a model, for the process lifetime.
    Recorded only after the retry WITHOUT the feature succeeded (ruling R12)."""

    def __init__(self) -> None:
        self._rejected: dict[str, set[str]] = {}

    def rejected(self, model: str, feature: str) -> bool:
        return feature in self._rejected.get(model, set())

    def remember(self, model: str, feature: str) -> None:
        self._rejected.setdefault(model, set()).add(feature)


class TTLCache:
    def __init__(self, ttl_s: float = CAPABILITY_TTL_S) -> None:
        self._ttl = ttl_s
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        hit = self._items.get(key)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> None:
        self._items[key] = (time.monotonic(), value)


async def open_stream(client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
    """Send `request` streaming. Returns a 2xx response the caller must close;
    a non-2xx is read, closed and raised as ProviderError."""
    try:
        resp = await client.send(request, stream=True)
    except httpx.ReadTimeout as e:
        raise ProviderTimeout() from e
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        raise ProviderUnavailable(str(e) or type(e).__name__) from e
    except httpx.HTTPError as e:
        raise ProviderUnavailable(type(e).__name__) from e
    if resp.status_code >= 400:
        raw = await resp.aread()
        await resp.aclose()
        message = safe_error_message(raw)
        logger.warning("Provider answered HTTP %s: %s", resp.status_code, message)
        raise ProviderError(resp.status_code, message)
    return resp


async def iter_lines(resp: httpx.Response) -> AsyncIterator[str]:
    """Lines of a streamed body. A read that waits longer than the client's
    read timeout (INFERENCE_TIMEOUT_S, idle) becomes ProviderTimeout."""
    try:
        async for line in resp.aiter_lines():
            yield line
    except httpx.ReadTimeout as e:
        raise ProviderTimeout() from e
    except httpx.HTTPError as e:
        raise ProviderUnavailable(type(e).__name__) from e


def is_feature_rejection(err: ProviderError) -> bool:
    """A 4xx that may mean "this model can't take that feature". Auth, missing
    model and rate limits are never feature rejections."""
    return 400 <= err.status < 500 and err.status not in (401, 403, 404, 408, 429)


def settle_dropped(features: FeatureMemory, model: str, provider: str, dropped: list[str]) -> list[FeatureDropped]:
    """After a successful retry: every dropped feature is reported, but only the last one
    — the one whose removal made the request succeed — is remembered (ruling R12).

    Returns the FeatureDropped chunks to yield; also records memory and logs."""
    chunks = [FeatureDropped(feature) for feature in dropped]
    if dropped:
        last_feature = dropped[-1]
        features.remember(model, last_feature)
        logger.debug("Model %s on %s rejected %s; retried without it",
                     model, provider, last_feature)
    return chunks
