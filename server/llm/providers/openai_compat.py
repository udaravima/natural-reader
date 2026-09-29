"""OpenAI-compatible adapter (spec §4.2): vLLM, OpenRouter, LiteLLM, and
Ollama's own /v1. `config.url` is the API base INCLUDING the version path
(ruling R10), e.g. https://openrouter.example.com/api/v1."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from ..types import (Capabilities, CallSettings, Chunk, Finish, Message,
                     ProviderError, ProviderUnavailable, ReasoningDelta, TextDelta, ToolCall,
                     ToolCallReady, ToolSpec, Usage)
from .base import (FeatureMemory, ProviderConfig, TTLCache, auth_headers, is_feature_rejection,
                   iter_lines, json_object, open_stream, parse_arguments, provider_error,
                   safe_error_message, settle_dropped)

logger = logging.getLogger(__name__)

_EFFORT = {"on": "medium", "low": "low", "medium": "medium", "high": "high"}   # 'off' -> omitted
_AUDIO_FORMAT = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
                 "audio/mpeg": "mp3", "audio/mp3": "mp3"}


def _wire_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role}
    if m.attachments and m.role == "user":
        parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
        for a in m.attachments:
            if a.kind == "image":
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:{a.mime};base64,{a.base64}"}})
            elif a.mime in _AUDIO_FORMAT:
                parts.append({"type": "input_audio",
                              "input_audio": {"data": a.base64, "format": _AUDIO_FORMAT[a.mime]}})
            else:
                logger.warning("Audio type %s has no OpenAI input_audio format; not sent", a.mime)
        out["content"] = parts
    else:
        out["content"] = m.content
    if m.tool_calls:
        out["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                             for c in m.tool_calls]
    if m.role == "tool":
        out["tool_call_id"] = m.tool_call_id
    return out


class OpenAICompatProvider:
    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self._client = client
        self._features = FeatureMemory()
        self._models = TTLCache()

    async def _entries(self) -> dict[str, dict]:
        cached = self._models.get("models")
        if cached is not None:
            return cached
        try:
            resp = await self._client.get(f"{self.config.url}/models", headers=auth_headers(self.config))
        except httpx.HTTPError as e:
            raise ProviderUnavailable(type(e).__name__) from e
        if resp.status_code != 200:
            raise provider_error(resp.status_code, resp.content)
        data = json_object(resp).get("data")
        entries = {e["id"]: e for e in (data if isinstance(data, list) else [])
                   if isinstance(e, dict) and e.get("id")}
        self._models.put("models", entries)
        return entries

    async def list_models(self) -> list[str]:
        return list((await self._entries()).keys())

    async def capabilities(self, model: str) -> Capabilities:
        return self._features.apply(model, await self._listed_capabilities(model))

    async def _listed_capabilities(self, model: str) -> Capabilities:
        try:
            entry = (await self._entries()).get(model) or {}
        except (ProviderUnavailable, ProviderError):
            return Capabilities()
        params = entry.get("supported_parameters")
        modalities = (entry.get("architecture") or {}).get("input_modalities")
        window = entry.get("context_length") or entry.get("max_model_len")
        has_params, has_modalities = isinstance(params, list), isinstance(modalities, list)
        return Capabilities(
            tools=("tools" in params) if has_params else None,
            thinking=("reasoning" in params or "reasoning_effort" in params) if has_params else None,
            vision=("image" in modalities) if has_modalities else None,
            audio=("audio" in modalities) if has_modalities else None,
            context_window=window if isinstance(window, int) else None)

    async def stream_chat(self, model: str, messages: list[Message], tools: list[ToolSpec],
                          settings: CallSettings) -> AsyncIterator[Chunk]:
        caps = await self.capabilities(model)
        effort = None
        if caps.thinking is not False and not self._features.rejected(model, "thinking"):
            effort = _EFFORT.get(settings.think)
        use_tools = bool(tools) and caps.tools is not False and not self._features.rejected(model, "tools")
        dropped: list[str] = []
        while True:
            body = self._body(model, messages, tools if use_tools else [], settings, effort)
            request = self._client.build_request("POST", f"{self.config.url}/chat/completions",
                                                 json=body, headers=auth_headers(self.config))
            try:
                resp = await open_stream(self._client, request)
                break
            except ProviderError as err:
                if not is_feature_rejection(err):
                    raise
                if effort is not None:
                    effort, feature = None, "thinking"
                elif use_tools:
                    use_tools, feature = False, "tools"
                else:
                    raise
                dropped.append(feature)
        for chunk in settle_dropped(self._features, model, self.config.name, dropped):
            yield chunk
        async for chunk in self._read(resp):
            yield chunk

    @staticmethod
    def _body(model: str, messages: list[Message], tools: list[ToolSpec],
              settings: CallSettings, effort: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "stream": True,
                                "stream_options": {"include_usage": True},
                                "messages": [_wire_message(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools]
        if effort:
            body["reasoning_effort"] = effort
        if settings.num_predict is not None:
            body["max_tokens"] = settings.num_predict
        if settings.temperature is not None:
            body["temperature"] = settings.temperature
        # num_ctx and keep_alive have no OpenAI equivalent: ignored (spec §4.2).
        return body

    @staticmethod
    async def _read(resp: httpx.Response) -> AsyncIterator[Chunk]:
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage: Usage | None = None
        saw_done = False
        try:
            async for line in iter_lines(resp):
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    saw_done = True
                    break
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                if payload.get("error"):
                    raw = json.dumps(payload)
                    logger.warning("Provider stream error: %s", safe_error_message(raw))
                    code = payload["error"].get("code") if isinstance(payload["error"], dict) else None
                    raise provider_error(code if isinstance(code, int) and code >= 400 else 500, raw)
                if payload.get("usage"):
                    u = payload["usage"]
                    usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
                for choice in payload.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield ReasoningDelta(reasoning)
                    if delta.get("content"):
                        yield TextDelta(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "args": ""})
                        fn = tc.get("function") or {}
                        slot["id"] = slot["id"] or tc.get("id") or ""
                        slot["name"] = slot["name"] or fn.get("name") or ""
                        slot["args"] += fn.get("arguments") or ""
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        finally:
            await resp.aclose()
        if not saw_done and finish is None:
            raise ProviderUnavailable("stream ended before the reply finished")
        for n, (_, slot) in enumerate(sorted(calls.items()), start=1):
            yield ToolCallReady(ToolCall(id=slot["id"] or f"call_{n}", name=slot["name"],
                                         arguments=parse_arguments(slot["args"])))
        if usage is not None:
            yield usage
        yield Finish("tool_calls" if calls else ("length" if finish == "length" else "stop"))
