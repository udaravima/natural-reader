"""Native Ollama adapter (spec §4.2): POST /api/chat (never /api/generate,
which skips the model's chat template), GET /api/tags, POST /api/show."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from ..types import (Capabilities, CallSettings, Chunk, FeatureDropped, Finish, Message,
                     ProviderError, ProviderUnavailable, ReasoningDelta, TextDelta, ToolCall,
                     ToolCallReady, ToolSpec, Usage)
from .base import (FeatureMemory, ProviderConfig, TTLCache, auth_headers, is_feature_rejection,
                   iter_lines, open_stream, parse_arguments, safe_error_message)

logger = logging.getLogger(__name__)

# 'off'/'on' send the boolean (byte-identical to the browser's old requests);
# the levels send their string.
_THINK_WIRE: dict[str, Any] = {"off": False, "on": True}
_DURATIONS = ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")


def _wire_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.attachments:
        # Ollama's message has ONE binary field. Audio rides in `images` too,
        # today's convention (spec §4.2); see ruling R7.
        out["images"] = [a.base64 for a in m.attachments]
    if m.tool_calls:
        out["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}}
                             for c in m.tool_calls]
    if m.role == "tool" and m.name:
        out["tool_name"] = m.name
    return out


class OllamaProvider:
    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self._client = client
        self._features = FeatureMemory()
        self._caps = TTLCache()

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(f"{self.config.url}/api/tags",
                                          headers=auth_headers(self.config))
        except httpx.HTTPError as e:
            raise ProviderUnavailable(type(e).__name__) from e
        if resp.status_code != 200:
            raise ProviderError(resp.status_code, safe_error_message(resp.content))
        return [m["name"] for m in resp.json().get("models", [])
                if isinstance(m, dict) and m.get("name")]

    async def capabilities(self, model: str) -> Capabilities:
        cached = self._caps.get(model)
        if cached is not None:
            return cached
        try:
            resp = await self._client.post(f"{self.config.url}/api/show", json={"model": model},
                                           headers=auth_headers(self.config))
        except httpx.HTTPError:
            return Capabilities()   # unknown, and not cached: the next call probes again
        if resp.status_code != 200:
            return Capabilities()
        body = resp.json()
        window = next((v for k, v in (body.get("model_info") or {}).items()
                       if k.endswith(".context_length") and isinstance(v, int)), None)
        listed = body.get("capabilities")
        if isinstance(listed, list):
            caps = Capabilities(tools="tools" in listed, thinking="thinking" in listed,
                                vision="vision" in listed, audio="audio" in listed,
                                context_window=window)
        else:   # an Ollama too old to list capabilities
            caps = Capabilities(context_window=window)
        self._caps.put(model, caps)
        return caps

    async def stream_chat(self, model: str, messages: list[Message], tools: list[ToolSpec],
                          settings: CallSettings) -> AsyncIterator[Chunk]:
        caps = await self.capabilities(model)
        think = self._think_value(model, settings, caps)
        use_tools = bool(tools) and caps.tools is not False and not self._features.rejected(model, "tools")
        dropped: list[str] = []
        while True:
            body = self._body(model, messages, tools if use_tools else [], settings, think)
            request = self._client.build_request("POST", f"{self.config.url}/api/chat", json=body,
                                                 headers=auth_headers(self.config))
            try:
                resp = await open_stream(self._client, request)
                break
            except ProviderError as err:
                if not is_feature_rejection(err):
                    raise
                # Today's order (the browser's retry chain): a think LEVEL first,
                # keeping tools, then tools.
                if isinstance(think, str):
                    think, feature = True, "think_level"
                elif use_tools:
                    use_tools, feature = False, "tools"
                else:
                    raise
                dropped.append(feature)
        # Yield FeatureDropped for every feature we tried, but remember only the last one
        # whose removal made the request succeed (ruling R12, Review Focus 1).
        for feature in dropped:
            yield FeatureDropped(feature)
        if dropped:
            last_feature = dropped[-1]
            self._features.remember(model, last_feature)
            logger.debug("Ollama model %s rejected %s; retried without it", model, last_feature)
        async for chunk in self._read(resp):
            yield chunk

    def _think_value(self, model: str, settings: CallSettings, caps: Capabilities) -> Any:
        if caps.thinking is False:
            return None   # omit the field: a model without thinking may reject it
        value = _THINK_WIRE.get(settings.think, settings.think)
        if isinstance(value, str) and self._features.rejected(model, "think_level"):
            return True
        return value

    @staticmethod
    def _body(model: str, messages: list[Message], tools: list[ToolSpec],
              settings: CallSettings, think: Any) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "stream": True,
                                "messages": [_wire_message(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools]
        if think is not None:
            body["think"] = think
        if settings.keep_alive is not None:
            body["keep_alive"] = settings.keep_alive
        options = {k: v for k, v in (("num_ctx", settings.num_ctx),
                                     ("num_predict", settings.num_predict),
                                     ("temperature", settings.temperature)) if v is not None}
        if options:
            body["options"] = options
        return body

    @staticmethod
    async def _read(resp: httpx.Response) -> AsyncIterator[Chunk]:
        n_calls = 0
        try:
            async for line in iter_lines(resp):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if payload.get("error"):
                    message = safe_error_message(json.dumps(payload))
                    logger.warning("Ollama stream error: %s", message)
                    raise ProviderError(500, message)
                msg = payload.get("message") or {}
                if msg.get("thinking"):
                    yield ReasoningDelta(msg["thinking"])
                if msg.get("content"):
                    yield TextDelta(msg["content"])
                for tc in msg.get("tool_calls") or []:
                    fn = (tc or {}).get("function") or {}
                    n_calls += 1
                    yield ToolCallReady(ToolCall(id=tc.get("id") or f"call_{n_calls}",
                                                 name=str(fn.get("name") or ""),
                                                 arguments=parse_arguments(fn.get("arguments"))))
                if payload.get("done"):
                    if "eval_count" in payload:
                        yield Usage(int(payload.get("prompt_eval_count") or 0),
                                    int(payload["eval_count"] or 0),
                                    detail={k: payload[k] for k in _DURATIONS if payload.get(k) is not None})
                    if n_calls:
                        yield Finish("tool_calls")
                    else:
                        yield Finish("length" if payload.get("done_reason") == "length" else "stop")
                    return
            raise ProviderUnavailable("stream ended before the reply finished")
        finally:
            await resp.aclose()
